#!/usr/bin/env python3
"""AINWA Review Console v1 — zero-dependency local review server.

Run:
    python3 server.py
Then open:
    http://127.0.0.1:8765

Files are intentionally plain JSON so Claude/other pipeline components can read/write them.

Security note: this console is designed to run unauthenticated on 127.0.0.1 only.
See the "Security hardening (v1.1)" section below for what that does and does not protect
against, and docs/hardening-2026-08-13.md-equivalent notes in README.md.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import secrets
import sys
import threading
import webbrowser
from contextlib import contextmanager
from datetime import datetime, date, timedelta, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent

# Data directory is configurable (--data-dir) so tests can run against an isolated
# temp directory instead of the real prototype data in ./data.
DATA_DIR = ROOT / "data"
CANDIDATES_FILE = DATA_DIR / "candidate-queue.json"
APPROVED_FILE = DATA_DIR / "approved-queue.json"
LOG_FILE = DATA_DIR / "review-log.json"
INDEX_FILE = ROOT / "index.html"

LOCK = threading.Lock()

MAX_BODY_BYTES = 1_000_000  # 1 MB — POST body size limit (finding #4)

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

# ---------------------------------------------------------------------------
# Security hardening (v1.1) — set once in main() before the server starts.
# ---------------------------------------------------------------------------
CSRF_TOKEN: str | None = None
ALLOWED_ORIGINS: set[str] = set()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class QueueFileError(Exception):
    """Raised when a required JSON data file exists but cannot be parsed.

    Deliberately distinct from "file does not exist yet" (which is normal on
    first run). Callers must surface this to the client rather than silently
    treating the file as empty (finding #6) — and must never overwrite the
    broken file.
    """

    def __init__(self, path: Path, detail: str):
        self.path = path
        self.detail = detail
        super().__init__(f"{path.name}: {detail}")


def read_json_checked(path: Path, default):
    """Read JSON, returning `default` only if the file doesn't exist.

    If the file exists but is not valid JSON, log to stderr and raise
    QueueFileError instead of silently returning `default` — a malformed
    file must never look identical to an empty queue.
    """
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[AINWA ERROR] Failed to parse {path}: {e}", file=sys.stderr)
        raise QueueFileError(path, str(e)) from e


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


@contextmanager
def _queue_lock(path: Path):
    """Advisory exclusive file lock for cross-process candidate-queue serialization.

    Uses a sidecar .lock file independent of os.replace() on the queue itself.
    fcntl.flock is advisory — both sides must call _queue_lock() for this to work.
    """
    lock_path = path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = lock_path.open("w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        fd.close()


def ensure_files() -> None:
    # Only creates files that don't exist yet — never touches an existing
    # (even malformed) file. That's what makes "preserve the previous data;
    # do not overwrite malformed files automatically" (finding #6) safe.
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CANDIDATES_FILE.exists():
        write_json(CANDIDATES_FILE, {"version": 1, "generated_at": None, "candidates": []})
    if not APPROVED_FILE.exists():
        write_json(APPROVED_FILE, {"version": 1, "updated_at": None, "stories": []})
    if not LOG_FILE.exists():
        write_json(LOG_FILE, {"version": 1, "events": []})


def candidate_list(payload) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("candidates", []) or []
    return []


def approved_list(payload) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("stories", []) or []
    return []


def duplicate_ids(candidates: list[dict]) -> list[str]:
    """Return candidate IDs that appear more than once (finding #7)."""
    counts: dict[str, int] = {}
    for c in candidates:
        cid = str(c.get("id"))
        counts[cid] = counts.get(cid, 0) + 1
    return sorted(cid for cid, n in counts.items() if n > 1)


def is_http_url(value) -> bool:
    """True only for well-formed http:// or https:// URLs (finding #1).

    Deliberately conservative: anything else (javascript:, data:, vbscript:,
    empty, non-string, missing netloc) is rejected. Used both server-side
    (so unsafe URLs are never persisted into approved records) and mirrored
    client-side in index.html (defense in depth) before rendering as a link.
    """
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def normalize_language(raw) -> dict:
    """Multilingual compatibility hook (v1: English-only, structural only).

    Always returns a well-formed `{"source_language": ..., "localizations": {...}}`
    dict, never raises. Malformed input is not propagated into approved records —
    it's logged to stderr and dropped/reset, so a bad `language` field on a
    candidate (e.g. from a future buggy discovery script) can never block or
    corrupt the human's English approval decision. This is deliberately lightweight
    (no schema library) per the instruction to keep this a small compatibility
    change, not a new framework.

    `localizations` entries are for future use only. v1 never creates them —
    ANWU discovery/proposal generation is English-only, and there is no
    translation workflow yet. If a candidate happens to already carry valid
    localization entries (future-proofing), they are preserved as-is on the
    approved record; AINWA's rule is that translations must derive from
    approved English content, never regenerate/reinterpret it — this function
    only ever passes such data through, it never invents or edits it.
    """
    default = {"source_language": "en", "localizations": {}}
    if raw is None:
        return default
    if not isinstance(raw, dict):
        print(f"[AINWA] WARNING: malformed 'language' field (expected object, got {type(raw).__name__}); using default.", file=sys.stderr)
        return default

    source_language = raw.get("source_language")
    if not isinstance(source_language, str) or not source_language:
        print("[AINWA] WARNING: malformed 'language.source_language' (expected non-empty string); defaulting to 'en'.", file=sys.stderr)
        source_language = "en"

    raw_localizations = raw.get("localizations", {})
    localizations: dict = {}
    if isinstance(raw_localizations, dict):
        for lang, entry in raw_localizations.items():
            if not isinstance(lang, str) or not lang:
                print(f"[AINWA] WARNING: dropping localization with invalid language code {lang!r}.", file=sys.stderr)
                continue
            if not isinstance(entry, dict):
                print(f"[AINWA] WARNING: dropping malformed localization entry for '{lang}' (expected object, got {type(entry).__name__}).", file=sys.stderr)
                continue
            status = entry.get("status")
            headline = entry.get("headline")
            public_summary = entry.get("public_summary")
            approved_at = entry.get("approved_at")
            localizations[lang] = {
                "status": status if isinstance(status, str) and status else "pending",
                "headline": headline if headline is None or isinstance(headline, str) else None,
                "public_summary": public_summary if public_summary is None or isinstance(public_summary, str) else None,
                "approved_at": approved_at if approved_at is None or isinstance(approved_at, str) else None,
            }
    elif raw_localizations not in (None, {}):
        print("[AINWA] WARNING: malformed 'language.localizations' (expected object); defaulting to empty.", file=sys.stderr)

    return {"source_language": source_language, "localizations": localizations}


def get_priority(candidate: dict) -> float:
    for key in ("rank", "priority_score", "priority"):
        value = candidate.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            m = {"critical": 100, "high": 80, "medium": 50, "low": 20}
            if value.lower() in m:
                return float(m[value.lower()])
    return 0.0


def normalized_source(candidate: dict) -> dict:
    source = candidate.get("source") or candidate.get("primary_source") or {}
    if not isinstance(source, dict):
        source = {}
    raw_url = source.get("url") or candidate.get("url") or ""
    return {
        "name": source.get("name") or candidate.get("source_name") or "",
        # Server-side sanitization (finding #1): a non-http(s) URL is dropped
        # here, before it ever reaches an approved record or an API response.
        "url": raw_url if is_http_url(raw_url) else "",
        "role": source.get("role") or source.get("source_type") or candidate.get("source_role") or "",
        "reliability": source.get("reliability") or candidate.get("reliability") or "",
        "paywall": bool(source.get("paywall") or candidate.get("paywall") or candidate.get("paywall_status") in ("paywall", "paywalled", True)),
    }


def normalized_proposal(candidate: dict) -> dict:
    proposal = candidate.get("proposal") if isinstance(candidate.get("proposal"), dict) else {}
    # public_summary is the new canonical name (AINWA-006). Fall back to the
    # legacy "summary" key so old-format candidates still render correctly.
    raw_summary = proposal.get("public_summary") or proposal.get("summary") or candidate.get("summary") or []
    if isinstance(raw_summary, str):
        raw_summary = [raw_summary]
    return {
        "brief_headline": proposal.get("brief_headline") or proposal.get("headline") or candidate.get("ainetwatch_headline") or candidate.get("proposed_headline") or "",
        "headline": proposal.get("brief_headline") or proposal.get("headline") or candidate.get("ainetwatch_headline") or candidate.get("proposed_headline") or "",
        "public_summary": raw_summary or [],
        # editorial_notes is reviewer-only and must never enter an approved record.
        "editorial_notes": str(proposal.get("editorial_notes") or candidate.get("editorial_notes") or ""),
        "category": proposal.get("category") or candidate.get("category") or "",
        "priority": proposal.get("priority") or candidate.get("priority") or candidate.get("priority_score") or "",
        "top_story": bool(proposal.get("top_story", candidate.get("top_story", False))),
        "developing": bool(proposal.get("developing", candidate.get("developing", False))),
        "why_selected": proposal.get("why_selected") or candidate.get("claude_rationale") or candidate.get("selection_rationale") or "",
        "duplicate_note": proposal.get("duplicate_note") or candidate.get("duplicate_note") or candidate.get("corroboration") or "",
    }


def make_approved(candidate: dict, edits: dict) -> dict:
    # Caller guarantees `edits` is already a dict (validated in apply_action,
    # finding #5) — this function no longer does `edits or {}` fallback.
    source = normalized_source(candidate)
    proposal = normalized_proposal(candidate)
    # Accept new "public_summary" key from the console; fall back to legacy
    # "summary" key so old edit payloads still work during transition.
    public_summary = edits.get("public_summary") or edits.get("summary") or proposal["public_summary"]
    if isinstance(public_summary, str):
        public_summary = [line.strip() for line in public_summary.splitlines() if line.strip()]

    # brief_headline is the new canonical name; headline is kept as an alias.
    # Prefer edits.brief_headline > edits.headline > proposal.brief_headline > proposal.headline.
    brief_headline = (
        edits.get("brief_headline")
        or edits.get("headline")
        or proposal.get("brief_headline")
        or proposal["headline"]
    )

    approved = {
        "id": candidate.get("id"),
        "source": source,
        "approved": {
            "brief_headline": brief_headline,
            "headline": brief_headline,  # backward-compat alias
            # editorial_notes must never enter the approved record — it is
            # reviewer-only context and is explicitly omitted here.
            "public_summary": public_summary,
            "category": edits.get("category", proposal["category"]),
            "priority": edits.get("priority", proposal["priority"]),
            "top_story": bool(edits.get("top_story", proposal["top_story"])),
            "developing": bool(edits.get("developing", proposal["developing"])),
            "paywall": bool(edits.get("paywall", source["paywall"])),
            "approved_at": now_iso(),
            "approved_by": "human",
            "locked": True,
        },
        "original_headline": candidate.get("original_headline") or candidate.get("headline") or "",
        "discovered_at": candidate.get("discovered_at"),
        # Multilingual compatibility hook (structural only — see normalize_language()).
        # English is authoritative; localizations stay empty in v1 and can only ever
        # be added here, never generated by this function.
        "language": normalize_language(candidate.get("language")),
    }
    return approved


def make_archived(candidate: dict) -> dict:
    """Build an archive record.

    Uses the same approved object as a normal approval (brief_headline,
    public_summary, locked: true, approved_by: human). Adds status: "archived"
    and archived_at at the top level so build.py routes it to archive.html only.
    build.py always reads public content from the approved block.
    """
    ts = now_iso()
    base = make_approved(candidate, {})
    base["status"] = "archived"
    base["archived_at"] = ts
    # approved_at is set inside make_approved → same instant
    return base


def make_manual_candidate(url: str, source_name: str, original_headline: str, proposal: dict | None = None) -> dict:
    ts = now_iso()
    p = proposal or {}
    brief_headline = str(p.get("brief_headline") or original_headline)
    public_summary = p.get("public_summary") or []
    if isinstance(public_summary, str):
        public_summary = [ln.strip() for ln in public_summary.splitlines() if ln.strip()]
    return {
        "id": f"manual-{secrets.token_hex(8)}",
        "intake_method": "manual",
        "status": "review",
        "queued_at": ts,
        "discovered_at": ts,
        "original_headline": original_headline,
        "source": {
            "name": source_name,
            "url": url,
            "paywall": bool(p.get("paywall", False)),
        },
        "proposal": {
            "brief_headline": brief_headline,
            "headline": brief_headline,
            "public_summary": public_summary,
            "category": str(p.get("category") or ""),
            "priority": str(p.get("priority") or ""),
            "top_story": bool(p.get("top_story", False)),
            "developing": bool(p.get("developing", False)),
            "editorial_notes": str(p.get("editorial_notes") or ""),
            "why_selected": "",
            "duplicate_note": "",
        },
    }


def _canonical_url(url: str) -> str:
    p = urlparse(url)
    return p._replace(scheme=p.scheme.lower(), netloc=p.netloc.lower(), fragment="").geturl().rstrip("/")


def apply_manual(body: dict) -> tuple[int, dict]:
    url = str(body.get("url") or "").strip()
    source_name = str(body.get("source_name") or "").strip()
    original_headline = str(body.get("original_headline") or "").strip()

    if not is_http_url(url):
        return 400, {"error": "url must be a valid http or https URL"}
    if not source_name:
        return 400, {"error": "source_name is required"}
    if not original_headline:
        return 400, {"error": "original_headline is required"}

    canonical = _canonical_url(url)

    with LOCK:
        with _queue_lock(CANDIDATES_FILE):
            try:
                cpayload = read_json_checked(CANDIDATES_FILE, {"version": 1, "candidates": []})
            except QueueFileError as e:
                return 500, {"error": f"candidate-queue.json is malformed and was not modified: {e.detail}"}
            candidates = candidate_list(cpayload)

            try:
                apayload = read_json_checked(APPROVED_FILE, {"version": 1, "stories": []})
            except QueueFileError as e:
                return 500, {"error": f"approved-queue.json is malformed and was not modified: {e.detail}"}
            stories = approved_list(apayload)

            for c in candidates:
                existing = (c.get("source") or {}).get("url") or c.get("url") or ""
                if is_http_url(existing) and _canonical_url(existing) == canonical:
                    return 409, {"error": "a candidate with that URL is already in the queue"}
            for s in stories:
                existing = (s.get("source") or {}).get("url") or s.get("url") or ""
                if is_http_url(existing) and _canonical_url(existing) == canonical:
                    return 409, {"error": "a story with that URL has already been approved"}

            proposal_overrides = {
                "brief_headline": str(body.get("brief_headline") or "").strip(),
                "public_summary": body.get("public_summary") or [],
                "category": str(body.get("category") or "").strip(),
                "priority": str(body.get("priority") or "").strip(),
                "top_story": bool(body.get("top_story", False)),
                "developing": bool(body.get("developing", False)),
                "editorial_notes": str(body.get("editorial_notes") or "").strip(),
                "paywall": bool(body.get("paywall", False)),
            }
            candidate = make_manual_candidate(url, source_name, original_headline, proposal_overrides)
            candidates.append(candidate)
            if isinstance(cpayload, dict):
                cpayload["candidates"] = candidates
                cpayload["updated_at"] = now_iso()
            else:
                cpayload = {"version": 1, "updated_at": now_iso(), "candidates": candidates}
            write_json(CANDIDATES_FILE, cpayload)

            append_log({
                "candidate_id": candidate["id"],
                "action": "manual_add",
                "url": url,
                "source_name": source_name,
                "at": now_iso(),
            })

    return 200, {"ok": True, "id": candidate["id"]}


def append_log(event: dict) -> None:
    try:
        payload = read_json_checked(LOG_FILE, {"version": 1, "events": []})
    except QueueFileError as e:
        # Do NOT silently overwrite a corrupted log with a fresh one — the
        # review action itself already succeeded by the time we log it, so
        # we report the logging failure to stderr and leave the broken file
        # untouched rather than destroying whatever is in it (finding #6).
        print(f"[AINWA ERROR] Could not append to review log ({e}); leaving {LOG_FILE} untouched.", file=sys.stderr)
        return
    if not isinstance(payload, dict):
        payload = {"version": 1, "events": []}
    payload.setdefault("events", []).append(event)
    write_json(LOG_FILE, payload)


def apply_action(body: dict) -> tuple[int, dict]:
    candidate_id = str(body.get("id") or "").strip()
    action = str(body.get("action") or "").strip()
    if not candidate_id or action not in {"approve", "edit_approve", "reject", "snooze", "archive"}:
        return 400, {"error": "id and a valid action are required"}

    # Finding #5: validate `edits` shape before touching any file, so a bad
    # payload returns a clean 400 instead of an unhandled exception deep
    # inside make_approved().
    edits: dict = {}
    if action == "edit_approve":
        raw_edits = body.get("edits")
        if not isinstance(raw_edits, dict):
            return 400, {"error": "edits must be a JSON object"}
        edits = raw_edits

    with LOCK:
        with _queue_lock(CANDIDATES_FILE):
            try:
                cpayload = read_json_checked(CANDIDATES_FILE, {"version": 1, "candidates": []})
            except QueueFileError as e:
                return 500, {"error": f"candidate-queue.json is malformed and was not modified: {e.detail}"}

            candidates = candidate_list(cpayload)

            # Finding #7: refuse to act at all while duplicate IDs exist anywhere
            # in the queue — acting on an ambiguous ID could silently affect the
            # wrong record once the data is corrected.
            dupes = duplicate_ids(candidates)
            if dupes:
                return 409, {
                    "error": "Duplicate candidate IDs detected in candidate-queue.json: "
                             + ", ".join(dupes)
                             + ". Fix the file before reviewing.",
                    "duplicate_ids": dupes,
                }

            candidate = next((c for c in candidates if str(c.get("id")) == candidate_id), None)
            if not candidate:
                return 404, {"error": f"candidate {candidate_id} not found"}

            if action in {"approve", "edit_approve", "archive"}:
                try:
                    apayload = read_json_checked(APPROVED_FILE, {"version": 1, "stories": []})
                except QueueFileError as e:
                    return 500, {"error": f"approved-queue.json is malformed and was not modified: {e.detail}"}
                stories = approved_list(apayload)
                if any(str(s.get("id")) == candidate_id for s in stories):
                    return 409, {"error": "candidate is already approved or archived"}
                if action == "archive":
                    record = make_archived(candidate)
                else:
                    record = make_approved(candidate, edits if action == "edit_approve" else {})
                stories.append(record)
                if isinstance(apayload, dict):
                    apayload["version"] = apayload.get("version", 1)
                    apayload["updated_at"] = now_iso()
                    apayload["stories"] = stories
                else:
                    apayload = {"version": 1, "updated_at": now_iso(), "stories": stories}
                write_json(APPROVED_FILE, apayload)
                candidate["status"] = "archived" if action == "archive" else "approved"
                candidate["human_decision"] = {
                    "action": action,
                    "at": now_iso(),
                }
                candidate["approved"] = record["approved"]

            elif action == "reject":
                candidate["status"] = "rejected"
                candidate["human_decision"] = {
                    "action": "reject",
                    "reason": body.get("reason") or "",
                    "at": now_iso(),
                }

            elif action == "snooze":
                candidate["status"] = "snoozed"
                candidate["human_decision"] = {
                    "action": "snooze",
                    "until": body.get("until") or "",
                    "reason": body.get("reason") or "",
                    "at": now_iso(),
                }

            if isinstance(cpayload, dict):
                cpayload["candidates"] = candidates
                cpayload["updated_at"] = now_iso()
            else:
                cpayload = {"version": 1, "updated_at": now_iso(), "candidates": candidates}
            write_json(CANDIDATES_FILE, cpayload)

            append_log({
                "candidate_id": candidate_id,
                "action": action,
                "reason": body.get("reason") or "",
                "at": now_iso(),
            })

    return 200, {"ok": True, "id": candidate_id, "action": action}


class Handler(BaseHTTPRequestHandler):
    server_version = "AINWAReview/1.1"

    def _json(self, status: int, data) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/state":
            with LOCK:
                try:
                    cpayload = read_json_checked(CANDIDATES_FILE, {"version": 1, "candidates": []})
                    apayload = read_json_checked(APPROVED_FILE, {"version": 1, "stories": []})
                except QueueFileError as e:
                    # Finding #6: never report a malformed file as an empty
                    # queue. The frontend must show this, not "queue is clear".
                    self._json(500, {"error": f"{e.path.name} is malformed and could not be loaded: {e.detail}"})
                    return

                candidates = candidate_list(cpayload)

                dupes = duplicate_ids(candidates)
                if dupes:
                    # Finding #7: surface clearly, and the frontend won't
                    # render the queue/actions while this is the response.
                    self._json(409, {
                        "error": "Duplicate candidate IDs detected in candidate-queue.json: "
                                 + ", ".join(dupes)
                                 + ". Fix the file before reviewing.",
                        "duplicate_ids": dupes,
                    })
                    return

                # 3-day rolling queue: show candidates discovered within the last
                # 3 calendar days (UTC). Older candidates age out of the active view.
                # Snoozed candidates remain visible and actionable; snoozed band
                # takes precedence over carryover band.
                today_utc = datetime.now(timezone.utc).date()
                cutoff = today_utc - timedelta(days=3)

                active_statuses = {"review", "discovered", "candidate", "snoozed"}
                terminal_statuses = {"approved", "rejected", "archived"}

                banded: list[dict] = []
                for c in candidates:
                    status = c.get("status", "review")
                    if status in terminal_statuses:
                        continue
                    raw_ts = c.get("queued_at") or c.get("discovered_at") or ""
                    try:
                        disc_date = date.fromisoformat(raw_ts[:10])
                    except (ValueError, TypeError):
                        disc_date = today_utc
                    if disc_date < cutoff:
                        continue  # aged out (older than 3 days; boundary day is included)

                    if status == "snoozed":
                        band = "snoozed"
                    elif disc_date == today_utc:
                        band = "current"
                    else:
                        band = "carryover"

                    entry = dict(c)
                    entry["_band"] = band
                    banded.append(entry)

                # Order: current (rank asc), carryover (discovered_at desc, rank asc),
                # snoozed last (discovered_at desc).
                def _rank(c):
                    r = c.get("rank")
                    return float(r) if isinstance(r, (int, float)) else 999.0

                current_g = sorted(
                    [c for c in banded if c.get("_band") == "current"],
                    key=_rank,
                )
                def _queue_ts(c):
                    return c.get("queued_at") or c.get("discovered_at") or ""

                carryover_g = sorted(
                    [c for c in banded if c.get("_band") == "carryover"],
                    key=lambda c: (_queue_ts(c), _rank(c)),
                    reverse=True,
                )
                snoozed_g = sorted(
                    [c for c in banded if c.get("_band") == "snoozed"],
                    key=_queue_ts,
                    reverse=True,
                )
                banded = current_g + carryover_g + snoozed_g

                self._json(200, {
                    "candidates": banded,
                    "approved_count": len(approved_list(apayload)),
                    "queue_total": len(candidates),
                    "generated_at": cpayload.get("generated_at") if isinstance(cpayload, dict) else None,
                    "csrf_token": CSRF_TOKEN,
                })
            return

        if path in {"/", "/index.html"}:
            try:
                raw = INDEX_FILE.read_bytes()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
            return

        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in {"/api/review", "/api/manual"}:
            self.send_error(404)
            return

        # --- Finding #2: CSRF / local write protection, layered checks ---

        # 1) Content-Type must be exactly application/json (ignoring an
        #    optional charset parameter). This alone defeats the classic
        #    "text/plain simple request skips CORS preflight" CSRF trick,
        #    since a cross-origin page can freely set Content-Type but the
        #    browser still won't let it lie about it being a simple request
        #    once we require a non-simple value... in practice the real stop
        #    is layered with the token check below; this check just refuses
        #    to process anything that isn't declared as JSON.
        content_type = self.headers.get("Content-Type", "")
        if content_type.split(";")[0].strip().lower() != "application/json":
            self._json(415, {"error": "Content-Type must be application/json"})
            return

        # 2) Origin, when the browser sends one, must be the console's own
        #    local origin. Non-browser clients (curl, test scripts) that omit
        #    Origin entirely are not blocked here — the CSRF token (below) is
        #    what actually gates them.
        origin = self.headers.get("Origin")
        if origin is not None and origin not in ALLOWED_ORIGINS:
            self._json(403, {"error": "Origin not allowed"})
            return

        # 3) Per-server-start CSRF token, required in a custom header. A
        #    cross-origin "simple request" (e.g. text/plain, or any request
        #    from a page that hasn't fetched /api/state on this origin) has
        #    no way to know this value.
        token = self.headers.get("X-AINWA-CSRF-Token")
        if not token or not secrets.compare_digest(token, CSRF_TOKEN or ""):
            self._json(403, {"error": "missing or invalid CSRF token"})
            return

        # 4) Body size limit (finding #4) — checked before reading the body.
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"error": "invalid Content-Length"})
            return
        if length < 0:
            self._json(400, {"error": "invalid Content-Length"})
            return
        if length > MAX_BODY_BYTES:
            self._json(413, {"error": f"request body exceeds {MAX_BODY_BYTES} bytes"})
            return

        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"error": "invalid JSON"})
            return
        if not isinstance(body, dict):
            self._json(400, {"error": "request body must be a JSON object"})
            return

        if path == "/api/manual":
            status, result = apply_manual(body)
        else:
            status, result = apply_action(body)
        self._json(status, result)

    def log_message(self, fmt, *args):
        print("[AINWA]", fmt % args)


def is_loopback(host: str) -> bool:
    return host in LOOPBACK_HOSTS


def main():
    global DATA_DIR, CANDIDATES_FILE, APPROVED_FILE, LOG_FILE, CSRF_TOKEN, ALLOWED_ORIGINS

    parser = argparse.ArgumentParser(description="AINWA Review Console v1")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically")
    parser.add_argument("--data-dir", default=None, help="Override the data directory (mainly for tests)")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Explicitly allow binding to a non-loopback host. There is still no authentication — "
             "anyone who can reach the host/port has full read/write control of the review queue.",
    )
    args = parser.parse_args()

    # Finding #3: host guardrail.
    if not is_loopback(args.host) and not args.allow_remote:
        print(
            f"[AINWA] Refusing to bind to non-loopback host '{args.host}' without --allow-remote.\n"
            f"        This server has NO authentication; binding it to a non-loopback address\n"
            f"        exposes full read/write control of the review queue to your network.\n"
            f"        Pass --allow-remote to do this deliberately.",
            file=sys.stderr,
        )
        sys.exit(2)

    if not is_loopback(args.host) and args.allow_remote:
        print(
            "=" * 78 + "\n"
            "  SECURITY WARNING: AINWA Review Console is binding to a NON-LOOPBACK host.\n"
            f"  Host: {args.host}  Port: {args.port}\n"
            "  This server has NO AUTHENTICATION. Anyone who can reach this address can\n"
            "  read every candidate and approve/reject/edit/snooze stories.\n"
            "  Only do this on a trusted, isolated network, and only temporarily.\n"
            + "=" * 78,
            file=sys.stderr,
        )

    if args.data_dir:
        DATA_DIR = Path(args.data_dir).resolve()
        CANDIDATES_FILE = DATA_DIR / "candidate-queue.json"
        APPROVED_FILE = DATA_DIR / "approved-queue.json"
        LOG_FILE = DATA_DIR / "review-log.json"

    CSRF_TOKEN = secrets.token_hex(32)
    ALLOWED_ORIGINS = {
        f"http://{args.host}:{args.port}",
        f"http://127.0.0.1:{args.port}",
        f"http://localhost:{args.port}",
    }

    ensure_files()
    addr = (args.host, args.port)
    server = ThreadingHTTPServer(addr, Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"AINWA Review Console running at {url}")
    print(f"Candidate queue: {CANDIDATES_FILE}")
    print(f"Approved queue:  {APPROVED_FILE}")
    print("Press Control-C to stop.")
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
