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
import json
import os
import secrets
import sys
import threading
import webbrowser
from datetime import datetime, timezone
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
        "headline": proposal.get("headline") or candidate.get("ainetwatch_headline") or candidate.get("proposed_headline") or "",
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

    approved = {
        "id": candidate.get("id"),
        "source": source,
        "approved": {
            "headline": edits.get("headline", proposal["headline"]),
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
    if not candidate_id or action not in {"approve", "edit_approve", "reject", "snooze"}:
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

        if action in {"approve", "edit_approve"}:
            try:
                apayload = read_json_checked(APPROVED_FILE, {"version": 1, "stories": []})
            except QueueFileError as e:
                return 500, {"error": f"approved-queue.json is malformed and was not modified: {e.detail}"}
            stories = approved_list(apayload)
            if any(str(s.get("id")) == candidate_id for s in stories):
                return 409, {"error": "candidate is already approved"}
            approved = make_approved(candidate, edits if action == "edit_approve" else {})
            stories.append(approved)
            if isinstance(apayload, dict):
                apayload["version"] = apayload.get("version", 1)
                apayload["updated_at"] = now_iso()
                apayload["stories"] = stories
            else:
                apayload = {"version": 1, "updated_at": now_iso(), "stories": stories}
            write_json(APPROVED_FILE, apayload)
            candidate["status"] = "approved"
            candidate["human_decision"] = {
                "action": "edit_approve" if action == "edit_approve" else "approve",
                "at": now_iso(),
            }
            candidate["approved"] = approved["approved"]

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

                active = [c for c in candidates if c.get("status", "review") in {"review", "discovered", "candidate"}]
                active.sort(key=get_priority, reverse=True)
                active = active[:12]
                self._json(200, {
                    "candidates": active,
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
        if path != "/api/review":
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
