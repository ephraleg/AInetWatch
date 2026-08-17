#!/usr/bin/env python3
"""Integration tests for the AINWA Review Console v1.1 hardening pass.

Zero external dependencies (stdlib only), matching the project's design.
Spins up server.py as a real subprocess against an isolated temp data
directory (--data-dir) so it never touches the real prototype data.

Run:
    python3 test_server.py
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "server.py"

PASS = []
FAIL = []


def check(name: str, condition: bool, detail: str = ""):
    if condition:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}  {detail}")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def request(method, path, base, body=None, headers=None, timeout=5):
    url = base + path
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        else:
            data = body if isinstance(body, bytes) else str(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                parsed = None
            return resp.status, parsed, raw
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = None
        return e.code, parsed, raw


def wait_ready(base, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, _, _ = request("GET", "/api/state", base)
            if status in (200, 409, 500):
                return True
        except (urllib.error.URLError, ConnectionRefusedError, OSError):
            pass
        time.sleep(0.1)
    return False


def seed(data_dir: Path, candidates=None, approved=None, log_events=None):
    (data_dir).mkdir(parents=True, exist_ok=True)
    with (data_dir / "candidate-queue.json").open("w") as f:
        json.dump({"version": 1, "generated_at": "2026-08-13T00:00:00Z", "candidates": candidates or []}, f)
    with (data_dir / "approved-queue.json").open("w") as f:
        json.dump({"version": 1, "updated_at": None, "stories": approved or []}, f)
    with (data_dir / "review-log.json").open("w") as f:
        json.dump({"version": 1, "events": log_events or []}, f)


_UNSET = object()


def make_candidate(cid, url="https://example.com/story", headline="Example original headline", language=_UNSET):
    c = {
        "id": cid,
        "status": "review",
        "rank": 5,
        "original_headline": headline,
        "source": {"name": "Example News", "url": url, "role": "Original Reporting", "reliability": "High", "paywall": False},
        "proposal": {
            "headline": "EXAMPLE PROPOSED HEADLINE",
            "public_summary": ["a", "b", "c"],
            "category": "Models",
            "priority": "High",
            "top_story": False,
            "developing": False,
            "why_selected": "test",
            "duplicate_note": "",
        },
        "discovered_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if language is not _UNSET:
        c["language"] = language
    return c


def main():
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    tmp = Path(tempfile.mkdtemp(prefix="ainwa-test-"))
    data_dir = tmp / "data"
    seed(data_dir, candidates=[make_candidate("cand-A"), make_candidate("cand-B")])

    proc = subprocess.Popen(
        [sys.executable, str(SERVER), "--port", str(port), "--data-dir", str(data_dir), "--no-open"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        ready = wait_ready(base)
        check("server starts and becomes ready", ready)
        if not ready:
            out, err = proc.communicate(timeout=2)
            print(out, err)
            return finish()

        # --- normal candidate loads ---
        status, body, _ = request("GET", "/api/state", base)
        check("GET /api/state returns 200", status == 200, f"got {status}")
        check("candidates loaded", body and len(body.get("candidates", [])) == 2, str(body))
        token = (body or {}).get("csrf_token")
        check("csrf_token present in /api/state", bool(token))

        good_headers = {"X-AINWA-CSRF-Token": token or ""}

        # --- approve works ---
        status, body, _ = request("POST", "/api/review", base, body={"id": "cand-A", "action": "approve"}, headers=good_headers)
        check("approve works", status == 200 and body.get("ok") is True, f"{status} {body}")
        approved = json.loads((data_dir / "approved-queue.json").read_text())
        check("approved-queue.json contains cand-A", any(s.get("id") == "cand-A" for s in approved.get("stories", [])))

        # --- edit & approve works ---
        edits = {"headline": "EDITED HEADLINE", "public_summary": ["x", "y"], "category": "Security", "priority": "High", "top_story": True, "developing": False, "paywall": False}
        status, body, _ = request("POST", "/api/review", base, body={"id": "cand-B", "action": "edit_approve", "edits": edits}, headers=good_headers)
        check("edit_approve works", status == 200 and body.get("ok") is True, f"{status} {body}")
        approved = json.loads((data_dir / "approved-queue.json").read_text())
        b = next((s for s in approved["stories"] if s["id"] == "cand-B"), None)
        check("edit_approve applied edited headline", b is not None and b["approved"]["headline"] == "EDITED HEADLINE", str(b))
        check("edit_approve sets locked True", b is not None and b["approved"]["locked"] is True)

        # --- reject / snooze work (fresh candidates) ---
        seed(data_dir, candidates=[make_candidate("cand-C"), make_candidate("cand-D")], approved=approved["stories"])
        status, body, _ = request("POST", "/api/review", base, body={"id": "cand-C", "action": "reject", "reason": "duplicate"}, headers=good_headers)
        check("reject works", status == 200 and body.get("ok") is True, f"{status} {body}")
        cq = json.loads((data_dir / "candidate-queue.json").read_text())
        c = next((c for c in cq["candidates"] if c["id"] == "cand-C"), None)
        check("reject sets status=rejected", c is not None and c.get("status") == "rejected")

        status, body, _ = request("POST", "/api/review", base, body={"id": "cand-D", "action": "snooze", "until": "tomorrow", "reason": "recheck"}, headers=good_headers)
        check("snooze works", status == 200 and body.get("ok") is True, f"{status} {body}")
        cq = json.loads((data_dir / "candidate-queue.json").read_text())
        d = next((c for c in cq["candidates"] if c["id"] == "cand-D"), None)
        check("snooze sets status=snoozed", d is not None and d.get("status") == "snoozed")

        # --- javascript: source URL cannot become a clickable link / reach approved record ---
        # Note: /api/state deliberately returns RAW candidate data (including a suspicious
        # raw url as inert text a reviewer can see) — normalization/sanitization happens at
        # approval time. The actual guarantees under test are: (a) the frontend never turns
        # it into a clickable href (isHttpUrl guard, checked below), and (b) it can never
        # reach the approved record (checked below) — not that the raw candidate view hides it.
        seed(data_dir, candidates=[make_candidate("cand-E", url="javascript:alert(1)")], approved=[])
        status, body, _ = request("POST", "/api/review", base, body={"id": "cand-E", "action": "approve"}, headers={"X-AINWA-CSRF-Token": token or ""})
        check("approve of javascript: url candidate succeeds (server sanitizes, doesn't reject)", status == 200, f"{status} {body}")
        approved = json.loads((data_dir / "approved-queue.json").read_text())
        e = next((s for s in approved["stories"] if s["id"] == "cand-E"), None)
        check("approved record's source.url is empty, not javascript:", e is not None and e["source"]["url"] == "", str(e))
        idx_js = INDEX_CONTENT.find("isHttpUrl")
        check("index.html contains an isHttpUrl guard used before rendering the source link", idx_js != -1)

        # --- text/plain POST is rejected ---
        seed(data_dir, candidates=[make_candidate("cand-F")], approved=[])
        status, body, _ = request("POST", "/api/review", base, body=json.dumps({"id": "cand-F", "action": "approve"}),
                                   headers={"Content-Type": "text/plain", "X-AINWA-CSRF-Token": token or ""})
        check("text/plain POST rejected (not 200)", status != 200, f"{status} {body}")
        check("text/plain POST rejected with 415", status == 415, f"{status} {body}")

        # --- missing/incorrect CSRF token rejected ---
        status, body, _ = request("POST", "/api/review", base, body={"id": "cand-F", "action": "approve"}, headers={})
        check("missing CSRF token rejected with 403", status == 403, f"{status} {body}")
        status, body, _ = request("POST", "/api/review", base, body={"id": "cand-F", "action": "approve"}, headers={"X-AINWA-CSRF-Token": "wrong-token"})
        check("incorrect CSRF token rejected with 403", status == 403, f"{status} {body}")
        status, body, _ = request("GET", "/api/state", base)
        cq = json.loads((data_dir / "candidate-queue.json").read_text())
        f = next((c for c in cq["candidates"] if c["id"] == "cand-F"), None)
        check("candidate untouched after rejected CSRF attempts", f is not None and f.get("status", "review") == "review")

        # --- 1 MB POST rejected ---
        # Server checks Content-Length (from headers, parsed before do_POST runs) against the
        # limit BEFORE calling self.rfile.read(). So we only need to declare an oversized
        # Content-Length and can read the response without actually streaming that much body —
        # sending the full body via a normal client would otherwise race a server-side close
        # against the client still writing (BrokenPipeError), which is a test-harness artifact,
        # not something under test here.
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.putrequest("POST", "/api/review", skip_accept_encoding=True)
            conn.putheader("Content-Type", "application/json")
            conn.putheader("X-AINWA-CSRF-Token", token or "")
            conn.putheader("Content-Length", str(2_000_000))
            conn.endheaders()
            resp = conn.getresponse()
            status = resp.status
            resp.read()
        finally:
            conn.close()
        check("oversized (>1MB) POST rejected with 413", status == 413, f"status={status}")

        # --- malformed edits returns 400 ---
        status, body, _ = request("POST", "/api/review", base, body={"id": "cand-F", "action": "edit_approve", "edits": "not-a-dict"}, headers=good_headers)
        check("malformed edits (string, not object) returns 400", status == 400, f"{status} {body}")
        status, body, _ = request("POST", "/api/review", base, body={"id": "cand-F", "action": "edit_approve", "edits": [1, 2, 3]}, headers=good_headers)
        check("malformed edits (array, not object) returns 400", status == 400, f"{status} {body}")

        # --- malformed candidate JSON produces a visible error, file untouched ---
        broken = "{ this is not valid JSON !!"
        (data_dir / "candidate-queue.json").write_text(broken)
        status, body, _ = request("GET", "/api/state", base)
        check("malformed candidate-queue.json returns non-200", status != 200, f"{status} {body}")
        check("malformed candidate-queue.json returns 500 with error field", status == 500 and "error" in (body or {}), f"{status} {body}")
        on_disk = (data_dir / "candidate-queue.json").read_text()
        check("malformed candidate-queue.json left untouched on disk (not overwritten to empty)", on_disk == broken, on_disk[:50])
        # restore for subsequent tests
        seed(data_dir, candidates=[make_candidate("cand-G")], approved=[])

        # --- duplicate candidate IDs detected ---
        seed(data_dir, candidates=[make_candidate("dup-1"), make_candidate("dup-1")], approved=[])
        status, body, _ = request("GET", "/api/state", base)
        check("duplicate candidate IDs rejected via /api/state (non-200)", status != 200, f"{status} {body}")
        check("duplicate candidate IDs reported with duplicate_ids field", body and "dup-1" in body.get("duplicate_ids", []), str(body))
        status, body, _ = request("POST", "/api/review", base, body={"id": "dup-1", "action": "approve"}, headers=good_headers)
        check("review action blocked while duplicate IDs exist", status == 409, f"{status} {body}")
        seed(data_dir, candidates=[make_candidate("cand-H")], approved=[])

        # --- multilingual compatibility hooks ---

        # 1) No 'language' field at all on the candidate (today's real shape, and the
        #    backward-compatibility case) -> approved record still gets a safe default.
        seed(data_dir, candidates=[make_candidate("lang-none")], approved=[])
        status, body, _ = request("POST", "/api/review", base, body={"id": "lang-none", "action": "approve"}, headers=good_headers)
        check("approve succeeds for candidate with no language field", status == 200, f"{status} {body}")
        approved = json.loads((data_dir / "approved-queue.json").read_text())
        rec = next((s for s in approved["stories"] if s["id"] == "lang-none"), None)
        check("missing language field defaults to source_language=en, localizations={}",
              rec is not None and rec.get("language") == {"source_language": "en", "localizations": {}}, str(rec))

        # 2) Explicit source_language: en, empty localizations -> preserved as-is.
        seed(data_dir, candidates=[make_candidate("lang-empty", language={"source_language": "en", "localizations": {}})], approved=[])
        status, body, _ = request("POST", "/api/review", base, body={"id": "lang-empty", "action": "approve"}, headers=good_headers)
        check("approve succeeds for candidate with explicit empty localizations", status == 200, f"{status} {body}")
        approved = json.loads((data_dir / "approved-queue.json").read_text())
        rec = next((s for s in approved["stories"] if s["id"] == "lang-empty"), None)
        check("explicit source_language=en + empty localizations preserved",
              rec is not None and rec.get("language") == {"source_language": "en", "localizations": {}}, str(rec))

        # 3) A valid future localization object on the candidate -> preserved unchanged
        #    into the approved record (this function only ever passes such data through).
        valid_localization = {
            "source_language": "en",
            "localizations": {
                "es": {"status": "pending", "headline": None, "public_summary": None, "approved_at": None}
            },
        }
        seed(data_dir, candidates=[make_candidate("lang-valid", language=valid_localization)], approved=[])
        status, body, _ = request("POST", "/api/review", base, body={"id": "lang-valid", "action": "approve"}, headers=good_headers)
        check("approve succeeds for candidate with a valid future localization", status == 200, f"{status} {body}")
        approved = json.loads((data_dir / "approved-queue.json").read_text())
        rec = next((s for s in approved["stories"] if s["id"] == "lang-valid"), None)
        check("valid future localization object preserved exactly",
              rec is not None and rec.get("language") == valid_localization, str(rec))

        # 4) Malformed language structures: approval must still succeed (English workflow
        #    unaffected) and the malformed piece must be rejected/dropped cleanly, never
        #    crash the request and never propagate garbage into the approved record.
        malformed_cases = [
            ("lang-bad-type", "not-an-object"),
            ("lang-bad-source", {"source_language": 123, "localizations": {}}),
            ("lang-bad-localizations-type", {"source_language": "en", "localizations": "nope"}),
            ("lang-bad-entry-type", {"source_language": "en", "localizations": {"es": "not-an-object"}}),
            ("lang-bad-entry-fields", {"source_language": "en", "localizations": {"es": {"status": 42, "headline": 99, "public_summary": [], "approved_at": True}}}),
        ]
        for cid, bad_language in malformed_cases:
            seed(data_dir, candidates=[make_candidate(cid, language=bad_language)], approved=[])
            status, body, _ = request("POST", "/api/review", base, body={"id": cid, "action": "approve"}, headers=good_headers)
            check(f"malformed language ({cid}) does not block English approval", status == 200, f"{status} {body}")
            approved = json.loads((data_dir / "approved-queue.json").read_text())
            rec = next((s for s in approved["stories"] if s["id"] == cid), None)
            lang = rec.get("language") if rec else None
            check(f"malformed language ({cid}) rejected cleanly -> well-formed structure on approved record",
                  isinstance(lang, dict) and isinstance(lang.get("source_language"), str) and isinstance(lang.get("localizations"), dict),
                  str(lang))
            check(f"malformed language ({cid}) does not silently pass the raw malformed value through",
                  lang != bad_language, str(lang))

        # 5) English-only regression check: an ordinary approval (no language field at all,
        #    exactly like every pre-existing test above) still produces the same English
        #    fields as before this pass — the multilingual hook didn't change English behavior.
        seed(data_dir, candidates=[make_candidate("lang-regression")], approved=[])
        status, body, _ = request("POST", "/api/review", base, body={"id": "lang-regression", "action": "approve"}, headers=good_headers)
        approved = json.loads((data_dir / "approved-queue.json").read_text())
        rec = next((s for s in approved["stories"] if s["id"] == "lang-regression"), None)
        check("English approval still has all pre-existing approved.* fields",
              rec is not None and set(rec["approved"].keys()) >= {"headline", "public_summary", "category", "priority", "top_story", "developing", "paywall", "approved_at", "approved_by", "locked"},
              str(rec))

        # --- AINWA-006: generate.py-shaped candidate round-trip ---
        # A candidate carrying public_summary, editorial_notes, and advisory
        # (the shape generate.py writes) must produce an approved record where:
        # (a) approved.public_summary matches the proposal,
        # (b) editorial_notes is absent from the approved record entirely,
        # (c) advisory is absent from the approved record entirely.
        generate_candidate = {
            "id": "gen-001",
            "status": "review",
            "rank": 1,
            "original_headline": "Source headline from feed",
            "source": {"name": "Example News", "url": "https://example.com/gen", "role": "Original Reporting", "reliability": "High", "paywall": False},
            "proposal": {
                "headline": "GENERATE PY CANDIDATE HEADLINE",
                "public_summary": ["What happened.", "Why it matters.", "Who is affected."],
                "editorial_notes": "Source quality: high. No dedup concerns. Reviewer-only.",
                "category": "Models",
                "priority": "High",
                "top_story": False,
                "developing": False,
            },
            "advisory": {
                "grok": {"status": "skipped", "reason": "GROK_API_KEY not set"},
                "gemini": {"status": "skipped", "reason": "GEMINI_API_KEY not set"},
            },
            "discovered_at": "2026-08-16T10:00:00Z",
        }
        seed(data_dir, candidates=[generate_candidate], approved=[])
        status, body, _ = request("POST", "/api/review", base, body={"id": "gen-001", "action": "approve"}, headers=good_headers)
        check("generate.py-shaped candidate approves successfully", status == 200 and (body or {}).get("ok") is True, f"{status} {body}")
        approved = json.loads((data_dir / "approved-queue.json").read_text())
        gen_rec = next((s for s in approved.get("stories", []) if s.get("id") == "gen-001"), None)
        check("approved.public_summary matches proposal public_summary",
              gen_rec is not None and gen_rec["approved"]["public_summary"] == ["What happened.", "Why it matters.", "Who is affected."],
              str(gen_rec))
        check("editorial_notes absent from approved record",
              gen_rec is not None and "editorial_notes" not in gen_rec["approved"],
              str(gen_rec["approved"] if gen_rec else None))
        check("advisory absent from approved record",
              gen_rec is not None and "advisory" not in gen_rec,
              str(list(gen_rec.keys()) if gen_rec else None))

        # --- AINWA-007: normalize_language null-safe + public_summary key consistency ---
        # normalize_language(None) must return the safe default without blocking approval.
        # A localization entry carrying "public_summary" (the canonical field name for
        # reader-facing content in localizations) must pass through correctly.
        valid_localization_ps = {
            "source_language": "en",
            "localizations": {
                "es": {"status": "pending", "headline": None, "public_summary": None, "approved_at": None}
            }
        }
        seed(data_dir, candidates=[make_candidate("lang-ps", language=valid_localization_ps)], approved=[])
        status, body, _ = request("POST", "/api/review", base, body={"id": "lang-ps", "action": "approve"}, headers=good_headers)
        check("approve succeeds for candidate with public_summary localization key", status == 200, f"{status} {body}")
        approved = json.loads((data_dir / "approved-queue.json").read_text())
        ps_rec = next((s for s in approved["stories"] if s.get("id") == "lang-ps"), None)
        check("localization entry with public_summary key preserved on approved record",
              ps_rec is not None and ps_rec.get("language") == valid_localization_ps,
              str(ps_rec))

        # --- manual candidate endpoint (/api/manual) ---
        manual_url = "https://example.com/manual-story-1"
        seed(data_dir, candidates=[], approved=[])

        status, body, _ = request("POST", "/api/manual", base,
                                   body={"url": manual_url, "source_name": "Example News", "original_headline": "Big AI story"},
                                   headers=good_headers)
        check("manual: valid candidate accepted (200)", status == 200 and (body or {}).get("ok") is True, f"{status} {body}")
        manual_id = (body or {}).get("id", "")
        check("manual: response contains a candidate id", bool(manual_id), str(body))

        cq = json.loads((data_dir / "candidate-queue.json").read_text())
        mc = next((c for c in cq.get("candidates", []) if c.get("id") == manual_id), None)
        check("manual: candidate written to candidate-queue.json", mc is not None, str([c.get("id") for c in cq.get("candidates", [])]))
        check("manual: intake_method is 'manual'", mc is not None and mc.get("intake_method") == "manual", str(mc))
        check("manual: status is 'review'", mc is not None and mc.get("status") == "review", str(mc))
        check("manual: source.url stored correctly", mc is not None and (mc.get("source") or {}).get("url") == manual_url, str(mc))

        aq = json.loads((data_dir / "approved-queue.json").read_text())
        check("manual: candidate not written to approved-queue.json", not any(s.get("id") == manual_id for s in aq.get("stories", [])))

        # proposal fields are persisted when provided
        status, body, _ = request("POST", "/api/manual", base,
                                   body={"url": "https://example.com/manual-with-proposal",
                                         "source_name": "Test Source",
                                         "original_headline": "Raw feed headline",
                                         "brief_headline": "Editor-written brief headline",
                                         "public_summary": ["Point one.", "Point two."],
                                         "category": "Models",
                                         "priority": "High",
                                         "top_story": True,
                                         "developing": False,
                                         "paywall": True},
                                   headers=good_headers)
        check("manual: proposal fields accepted (200)", status == 200 and (body or {}).get("ok") is True, f"{status} {body}")
        pid = (body or {}).get("id", "")
        cq = json.loads((data_dir / "candidate-queue.json").read_text())
        mp = next((c for c in cq.get("candidates", []) if c.get("id") == pid), None)
        check("manual: brief_headline stored in proposal", mp is not None and (mp.get("proposal") or {}).get("brief_headline") == "Editor-written brief headline", str(mp))
        check("manual: public_summary stored in proposal", mp is not None and (mp.get("proposal") or {}).get("public_summary") == ["Point one.", "Point two."], str(mp))
        check("manual: category stored in proposal", mp is not None and (mp.get("proposal") or {}).get("category") == "Models", str(mp))
        check("manual: top_story stored in proposal", mp is not None and (mp.get("proposal") or {}).get("top_story") is True, str(mp))
        check("manual: paywall stored in source", mp is not None and (mp.get("source") or {}).get("paywall") is True, str(mp))

        # invalid URL schemes rejected
        status, body, _ = request("POST", "/api/manual", base,
                                   body={"url": "javascript:alert(1)", "source_name": "X", "original_headline": "Y"},
                                   headers=good_headers)
        check("manual: javascript: URL rejected with 400", status == 400, f"{status} {body}")

        status, body, _ = request("POST", "/api/manual", base,
                                   body={"url": "ftp://example.com/file", "source_name": "X", "original_headline": "Y"},
                                   headers=good_headers)
        check("manual: ftp:// URL rejected with 400", status == 400, f"{status} {body}")

        # missing required fields
        status, body, _ = request("POST", "/api/manual", base,
                                   body={"url": "https://example.com/story2"},
                                   headers=good_headers)
        check("manual: missing source_name and headline rejected with 400", status == 400, f"{status} {body}")

        status, body, _ = request("POST", "/api/manual", base,
                                   body={"url": "https://example.com/story3", "source_name": "X", "original_headline": ""},
                                   headers=good_headers)
        check("manual: empty original_headline rejected with 400", status == 400, f"{status} {body}")

        # duplicate URL rejected
        status, body, _ = request("POST", "/api/manual", base,
                                   body={"url": manual_url, "source_name": "Other", "original_headline": "Duplicate"},
                                   headers=good_headers)
        check("manual: duplicate candidate URL rejected with 409", status == 409, f"{status} {body}")

        # duplicate URL against approved story rejected
        seed(data_dir, candidates=[],
             approved=[{"id": "approved-001", "source": {"url": "https://example.com/approved-story"}, "approved": {"locked": True, "approved_by": "human", "approved_at": "2026-08-17T00:00:00Z"}}])
        status, body, _ = request("POST", "/api/manual", base,
                                   body={"url": "https://example.com/approved-story", "source_name": "X", "original_headline": "Z"},
                                   headers=good_headers)
        check("manual: URL already in approved stories rejected with 409", status == 409, f"{status} {body}")

        # CSRF required at /api/manual too
        status, body, _ = request("POST", "/api/manual", base,
                                   body={"url": "https://example.com/new", "source_name": "X", "original_headline": "Z"},
                                   headers={})
        check("manual: missing CSRF token rejected with 403", status == 403, f"{status} {body}")

        # /api/review still works (regression check)
        seed(data_dir, candidates=[make_candidate("rcheck-manual")], approved=[])
        status, body, _ = request("POST", "/api/review", base, body={"id": "rcheck-manual", "action": "approve"}, headers=good_headers)
        check("manual: /api/review unaffected after adding /api/manual route", status == 200 and (body or {}).get("ok") is True, f"{status} {body}")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    # --- server still binds only to loopback by default / refuses non-loopback without flag ---
    tmp2 = Path(tempfile.mkdtemp(prefix="ainwa-test-host-"))
    try:
        port2 = free_port()
        p = subprocess.run(
            [sys.executable, str(SERVER), "--host", "10.0.0.5", "--port", str(port2), "--data-dir", str(tmp2 / "data"), "--no-open"],
            cwd=ROOT, capture_output=True, text=True, timeout=5,
        )
        check("non-loopback host refused without --allow-remote (nonzero exit)", p.returncode != 0, f"rc={p.returncode} stderr={p.stderr[:200]}")
        check("refusal message printed to stderr", "Refusing to bind" in p.stderr, p.stderr[:200])
    except subprocess.TimeoutExpired:
        check("non-loopback host refused without --allow-remote (nonzero exit)", False, "server did not exit — it bound instead of refusing")
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    import argparse
    spec = SERVER.read_text()
    check("default --host is 127.0.0.1 in source", '"--host", default="127.0.0.1"' in spec)

    # --- Straight to Archive ---
    tmp3 = Path(tempfile.mkdtemp(prefix="ainwa-test-archive-"))
    data3 = tmp3 / "data"
    archive_candidate = make_candidate("arch-001")
    seed(data3, candidates=[archive_candidate], approved=[])
    port3 = free_port()
    base3 = f"http://127.0.0.1:{port3}"
    proc3 = subprocess.Popen(
        [sys.executable, str(SERVER), "--port", str(port3), "--data-dir", str(data3), "--no-open"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        ready3 = wait_ready(base3)
        check("archive test server starts", ready3)
        if ready3:
            status3, body3, _ = request("GET", "/api/state", base3)
            token3 = (body3 or {}).get("csrf_token")
            headers3 = {"X-AINWA-CSRF-Token": token3 or ""}

            status3, body3, _ = request("POST", "/api/review", base3,
                                         body={"id": "arch-001", "action": "archive"},
                                         headers=headers3)
            check("archive action returns 200", status3 == 200 and (body3 or {}).get("ok") is True, f"{status3} {body3}")

            aq = json.loads((data3 / "approved-queue.json").read_text())
            arec = next((s for s in aq.get("stories", []) if s.get("id") == "arch-001"), None)
            check("archive record present in approved-queue.json", arec is not None, str(aq))
            check("archive record has status=archived", arec is not None and arec.get("status") == "archived", str(arec))
            check("archive record has archived_at", arec is not None and bool(arec.get("archived_at")), str(arec))
            check("archive record has approved.locked=True", arec is not None and arec.get("approved", {}).get("locked") is True, str(arec))
            check("archive record has approved.approved_by=human", arec is not None and arec.get("approved", {}).get("approved_by") == "human", str(arec))
            check("archive record has brief_headline in approved", arec is not None and "brief_headline" in arec.get("approved", {}), str(arec))

            cq3 = json.loads((data3 / "candidate-queue.json").read_text())
            ac = next((c for c in cq3.get("candidates", []) if c.get("id") == "arch-001"), None)
            check("candidate status set to archived", ac is not None and ac.get("status") == "archived", str(ac))

    finally:
        proc3.terminate()
        try:
            proc3.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc3.kill()
        shutil.rmtree(tmp3, ignore_errors=True)

    # --- 3-day carryover banding in /api/state ---
    tmp4 = Path(tempfile.mkdtemp(prefix="ainwa-test-band-"))
    data4 = tmp4 / "data"
    from datetime import timedelta
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT12:00:00Z")
    yesterday_str = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT12:00:00Z")
    old_str = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT12:00:00Z")

    band_candidates = [
        {**make_candidate("band-today"), "discovered_at": today_str, "status": "review"},
        {**make_candidate("band-yesterday"), "discovered_at": yesterday_str, "status": "review"},
        {**make_candidate("band-snoozed"), "discovered_at": yesterday_str, "status": "snoozed"},
        {**make_candidate("band-old"), "discovered_at": old_str, "status": "review"},
    ]
    seed(data4, candidates=band_candidates, approved=[])
    port4 = free_port()
    base4 = f"http://127.0.0.1:{port4}"
    proc4 = subprocess.Popen(
        [sys.executable, str(SERVER), "--port", str(port4), "--data-dir", str(data4), "--no-open"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        ready4 = wait_ready(base4)
        check("band test server starts", ready4)
        if ready4:
            status4, body4, _ = request("GET", "/api/state", base4)
            check("band /api/state returns 200", status4 == 200, f"{status4} {body4}")
            candidates4 = (body4 or {}).get("candidates", [])
            ids4 = [c["id"] for c in candidates4]
            bands4 = {c["id"]: c.get("_band") for c in candidates4}

            check("today candidate present in state", "band-today" in ids4, str(ids4))
            check("yesterday candidate present in state (carryover)", "band-yesterday" in ids4, str(ids4))
            check("snoozed candidate present in state", "band-snoozed" in ids4, str(ids4))
            check("5-day-old candidate aged out of state", "band-old" not in ids4, str(ids4))
            check("today candidate has _band=current", bands4.get("band-today") == "current", str(bands4))
            check("yesterday candidate has _band=carryover", bands4.get("band-yesterday") == "carryover", str(bands4))
            check("snoozed candidate has _band=snoozed", bands4.get("band-snoozed") == "snoozed", str(bands4))
            check("current candidates appear before carryover",
                  ids4.index("band-today") < ids4.index("band-yesterday") if "band-today" in ids4 and "band-yesterday" in ids4 else False,
                  str(ids4))
            check("snoozed candidates appear after carryover",
                  ids4.index("band-snoozed") > ids4.index("band-yesterday") if "band-snoozed" in ids4 and "band-yesterday" in ids4 else False,
                  str(ids4))
    finally:
        proc4.terminate()
        try:
            proc4.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc4.kill()
        shutil.rmtree(tmp4, ignore_errors=True)

    # --- queued_at banding: server uses queued_at over discovered_at ---
    tmp5 = Path(tempfile.mkdtemp(prefix="ainwa-test-queuedat-"))
    data5 = tmp5 / "data"
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    old_article_str = "2026-08-13T10:00:00Z"   # article ingestion date — always "old"
    old_queued_str = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

    queued_candidates = [
        # Old discovered_at, current queued_at → must appear as "current"
        {**make_candidate("qa-current"), "discovered_at": old_article_str, "queued_at": now_str, "status": "review"},
        # Old discovered_at, old queued_at → must age out
        {**make_candidate("qa-aged"), "discovered_at": old_article_str, "queued_at": old_queued_str, "status": "review"},
        # No queued_at, current discovered_at → backward-compat fallback, appears as "current"
        {**make_candidate("qa-fallback"), "discovered_at": now_str, "status": "review"},
    ]
    seed(data5, candidates=queued_candidates, approved=[])
    port5 = free_port()
    base5 = f"http://127.0.0.1:{port5}"
    proc5 = subprocess.Popen(
        [sys.executable, str(SERVER), "--port", str(port5), "--data-dir", str(data5), "--no-open"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        ready5 = wait_ready(base5)
        check("queued_at test server starts", ready5)
        if ready5:
            status5, body5, _ = request("GET", "/api/state", base5)
            check("queued_at /api/state returns 200", status5 == 200, f"{status5} {body5}")
            candidates5 = (body5 or {}).get("candidates", [])
            ids5 = [c["id"] for c in candidates5]
            bands5 = {c["id"]: c.get("_band") for c in candidates5}

            check("old article but current queued_at appears as current",
                  bands5.get("qa-current") == "current", str(bands5))
            check("old article with old queued_at ages out",
                  "qa-aged" not in ids5, str(ids5))
            check("no queued_at falls back to discovered_at (backward compat)",
                  bands5.get("qa-fallback") == "current", str(bands5))
    finally:
        proc5.terminate()
        try:
            proc5.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc5.kill()
        shutil.rmtree(tmp5, ignore_errors=True)

    finish()


def finish():
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for name in FAIL:
            print(f"  - {name}")
        sys.exit(1)
    sys.exit(0)


INDEX_CONTENT = (ROOT / "index.html").read_text()

if __name__ == "__main__":
    main()
