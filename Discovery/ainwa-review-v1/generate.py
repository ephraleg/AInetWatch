#!/usr/bin/env python3
"""AINWA-005/006 Candidate Generation v1

Pipeline position:
  filtered-discovery.json → Claude selection (max 12) → Grok/Gemini advisory → candidate-queue.json

Does NOT write approved-queue.json or trigger any approval action.

API keys (from environment variables):
  ANTHROPIC_API_KEY  — required; exits with error if absent
  GROK_API_KEY       — optional; advisory skipped if absent or on error
  GEMINI_API_KEY     — optional; advisory skipped if absent or on error

Run:
  python3 generate.py
  python3 generate.py --dry-run        # print prompt only, no API calls
  python3 generate.py --skip-advisory  # skip Grok/Gemini even if keys are set
  python3 generate.py --data-dir /tmp/test-data
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
FILTERED_FILE = DATA_DIR / "filtered-discovery.json"
APPROVED_FILE = DATA_DIR / "approved-queue.json"
CANDIDATES_FILE = DATA_DIR / "candidate-queue.json"

MAX_CANDIDATES = 12
CATEGORIES = ("Models", "Research", "Security", "Governance", "Business", "Infrastructure", "Applications")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def is_http_url(value) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def read_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def _http_post(url: str, headers: dict, body: dict, timeout: int = 120) -> dict:
    raw = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=raw, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _strip_code_fence(text: str) -> str:
    """Remove optional markdown code fences Claude sometimes wraps around JSON."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0]
    return text.strip()


# ---------------------------------------------------------------------------
# API callers
# ---------------------------------------------------------------------------

def call_claude(prompt: str, api_key: str, model: str = "claude-sonnet-4-6") -> str:
    """Return the first text content block from the Claude Messages API."""
    resp = _http_post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        body={
            "model": model,
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    return resp["content"][0]["text"]


def call_grok(shortlist_json: str, api_key: str) -> list[dict]:
    """Return per-candidate advisory assessments from Grok."""
    prompt = _advisory_prompt(shortlist_json)
    resp = _http_post(
        "https://api.x.ai/v1/chat/completions",
        headers={"authorization": f"Bearer {api_key}", "content-type": "application/json"},
        body={
            "model": "grok-3-fast",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2048,
            "temperature": 0,
        },
    )
    return json.loads(_strip_code_fence(resp["choices"][0]["message"]["content"]))


def call_gemini(shortlist_json: str, api_key: str) -> list[dict]:
    """Return per-candidate advisory assessments from Gemini."""
    prompt = _advisory_prompt(shortlist_json)
    resp = _http_post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}",
        headers={"content-type": "application/json"},
        body={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 2048},
        },
    )
    return json.loads(_strip_code_fence(resp["candidates"][0]["content"]["parts"][0]["text"]))


def _advisory_prompt(shortlist_json: str) -> str:
    return f"""You are an editorial advisor for AInetWatch, an AI industry news wire.

Review the shortlist of up to {MAX_CANDIDATES} AI news candidates below and return a JSON array
with one object per candidate:

{{"item_id": "...", "assessment": "agree" | "flag" | "disagree", "notes": "1-2 sentences"}}

Return ONLY the JSON array, no other text.

Candidates:
{shortlist_json}"""


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_selection_prompt(items: list[dict], excluded_urls: set[str]) -> str:
    eligible = [i for i in items if i.get("canonical_url") not in excluded_urls]
    categories_str = ", ".join(CATEGORIES)

    lines = []
    for idx, item in enumerate(eligible, 1):
        age = item.get("age_hours")
        age_str = f"{age:.1f}h" if isinstance(age, (int, float)) else "?h"
        lines.append(
            f"{idx}. [{item.get('item_id', '')}] "
            f"[{item.get('source_name', '')} / {item.get('source_role', '')} / "
            f"cite:{item.get('source_citation_allowed', '?')} / {age_str}] "
            f"{item.get('item_title', '').strip()}"
        )

    return f"""You are ANWU, the AInetWatch editorial AI. Select and rank the {MAX_CANDIDATES} most \
important AI-industry stories from the list below for today's wire.

Selection criteria (in order of importance):
1. Importance and novelty to the AI industry
2. Timeliness (prefer lower age)
3. Source quality: Original Reporting > Primary Source > Discovery Only; cite:yes > cite:conditional > cite:no
4. Reader relevance for an AI-industry professional audience
5. Non-duplication (prefer stories covering distinct events)

For each selected story produce exactly these fields:
- headline: AInetWatch-style in ALL CAPS, ≤12 words, punchy and factual
- public_summary: exactly 3 reader-facing bullets — (1) what happened, (2) why it matters, \
(3) what changes or who is affected. No internal language. Published verbatim if approved.
- editorial_notes: 1-3 sentences for the human reviewer only — source quality, verification \
concerns, corroboration or duplication context. Never published.
- category: exactly one of: {categories_str}
- priority: High | Medium | Low
- top_story: true for the single most important story only, false for all others
- developing: true only if the story is clearly unresolved or actively breaking

Return ONLY a JSON array of up to {MAX_CANDIDATES} objects, no other text:
[
  {{
    "item_id": "raw-SRC-XXX-XXXXXXXX",
    "headline": "ALL CAPS HEADLINE HERE",
    "public_summary": ["What happened.", "Why it matters.", "What changes or who is affected."],
    "editorial_notes": "Source quality and verification notes for the reviewer.",
    "category": "Models",
    "priority": "High",
    "top_story": false,
    "developing": false
  }}
]

Candidates ({len(eligible)} items after excluding already-approved stories):
{chr(10).join(lines)}"""


# ---------------------------------------------------------------------------
# Candidate builder
# ---------------------------------------------------------------------------

def build_candidates(
    selections: list[dict],
    item_lookup: dict[str, dict],
    grok_results: dict[str, dict],
    gemini_results: dict[str, dict],
    grok_ran: bool,
    gemini_ran: bool,
) -> list[dict]:
    candidates = []
    for rank, sel in enumerate(selections[:MAX_CANDIDATES], 1):
        item_id = str(sel.get("item_id") or "")
        item = item_lookup.get(item_id)
        if not item:
            print(f"[AINWA] WARNING: Claude returned unknown item_id {item_id!r}; skipping.", file=sys.stderr)
            continue

        raw_url = item.get("canonical_url") or item.get("item_url") or ""
        source_url = raw_url if is_http_url(raw_url) else ""

        advisory = {
            "grok": grok_results.get(item_id, {
                "status": "skipped",
                "reason": "GROK_API_KEY not set" if not grok_ran else "not returned by advisor",
            }),
            "gemini": gemini_results.get(item_id, {
                "status": "skipped",
                "reason": "GEMINI_API_KEY not set" if not gemini_ran else "not returned by advisor",
            }),
        }

        candidates.append({
            "id": item_id,
            "status": "review",
            "rank": rank,
            "original_headline": item.get("item_title", ""),
            "source": {
                "name": item.get("source_name", ""),
                "url": source_url,
                "role": item.get("source_role", ""),
                "reliability": item.get("source_reliability", ""),
                "paywall": False,
            },
            "proposal": {
                "headline": str(sel.get("headline") or ""),
                "public_summary": sel.get("public_summary") if isinstance(sel.get("public_summary"), list) else [],
                "editorial_notes": str(sel.get("editorial_notes") or ""),
                "category": str(sel.get("category") or ""),
                "priority": str(sel.get("priority") or ""),
                "top_story": bool(sel.get("top_story", False)),
                "developing": bool(sel.get("developing", False)),
            },
            "advisory": advisory,
            "discovered_at": item.get("fetched_at") or now_iso(),
        })
    return candidates


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="AINWA-005 Candidate Generation")
    parser.add_argument("--dry-run", action="store_true", help="Print prompt; do not call any API")
    parser.add_argument("--skip-advisory", action="store_true", help="Skip Grok/Gemini advisory")
    parser.add_argument("--data-dir", default=None, help="Override data directory (for tests)")
    parser.add_argument("--model", default="claude-sonnet-4-6", help="Claude model to use")
    args = parser.parse_args(argv)

    # Resolve paths (allow override for tests)
    filtered_file = FILTERED_FILE
    approved_file = APPROVED_FILE
    candidates_file = CANDIDATES_FILE
    if args.data_dir:
        d = Path(args.data_dir).resolve()
        filtered_file = d / "filtered-discovery.json"
        approved_file = d / "approved-queue.json"
        candidates_file = d / "candidate-queue.json"

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not anthropic_key and not args.dry_run:
        print("[AINWA] ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        return 1

    # Load filtered items
    filtered = read_json(filtered_file, {"items": []})
    items = filtered.get("items", []) if isinstance(filtered, dict) else []

    # Load approved URLs to exclude
    approved = read_json(approved_file, {"stories": []})
    approved_urls: set[str] = set()
    for story in (approved.get("stories", []) if isinstance(approved, dict) else []):
        url = (story.get("source") or {}).get("url", "")
        if url:
            approved_urls.add(url)

    print(f"[AINWA] {len(items)} filtered items, {len(approved_urls)} approved URLs excluded.", file=sys.stderr)

    prompt = build_selection_prompt(items, approved_urls)
    item_lookup = {i["item_id"]: i for i in items if "item_id" in i}

    if args.dry_run:
        print(prompt)
        return 0

    # --- Claude selection ---
    print(f"[AINWA] Calling Claude ({args.model}) for candidate selection…", file=sys.stderr)
    try:
        raw_response = call_claude(prompt, anthropic_key, args.model)
    except Exception as exc:
        print(f"[AINWA] Claude API error: {exc}", file=sys.stderr)
        return 1

    try:
        selections = json.loads(_strip_code_fence(raw_response))
        if not isinstance(selections, list):
            raise ValueError("Expected a JSON array")
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"[AINWA] Failed to parse Claude response: {exc}", file=sys.stderr)
        print(f"[AINWA] Raw:\n{raw_response}", file=sys.stderr)
        return 1

    print(f"[AINWA] Claude selected {len(selections)} candidates.", file=sys.stderr)

    # --- Build shortlist JSON for advisors ---
    shortlist_json = json.dumps([
        {
            "item_id": s.get("item_id"),
            "headline": s.get("headline"),
            "public_summary": s.get("public_summary"),
            "source": item_lookup.get(str(s.get("item_id") or ""), {}).get("source_name"),
        }
        for s in selections
    ], indent=2)

    # --- Grok advisory ---
    grok_key = os.environ.get("GROK_API_KEY") if not args.skip_advisory else None
    grok_ran = False
    grok_results: dict[str, dict] = {}
    if grok_key:
        print("[AINWA] Calling Grok for advisory…", file=sys.stderr)
        try:
            for r in call_grok(shortlist_json, grok_key):
                grok_results[str(r.get("item_id") or "")] = {
                    "status": "ok",
                    "assessment": r.get("assessment"),
                    "notes": r.get("notes"),
                }
            grok_ran = True
        except Exception as exc:
            print(f"[AINWA] Grok advisory failed (non-blocking): {exc}", file=sys.stderr)
    else:
        reason = "--skip-advisory" if args.skip_advisory else "GROK_API_KEY not set"
        print(f"[AINWA] Grok advisory skipped: {reason}.", file=sys.stderr)

    # --- Gemini advisory ---
    gemini_key = os.environ.get("GEMINI_API_KEY") if not args.skip_advisory else None
    gemini_ran = False
    gemini_results: dict[str, dict] = {}
    if gemini_key:
        print("[AINWA] Calling Gemini for advisory…", file=sys.stderr)
        try:
            for r in call_gemini(shortlist_json, gemini_key):
                gemini_results[str(r.get("item_id") or "")] = {
                    "status": "ok",
                    "assessment": r.get("assessment"),
                    "notes": r.get("notes"),
                }
            gemini_ran = True
        except Exception as exc:
            print(f"[AINWA] Gemini advisory failed (non-blocking): {exc}", file=sys.stderr)
    else:
        reason = "--skip-advisory" if args.skip_advisory else "GEMINI_API_KEY not set"
        print(f"[AINWA] Gemini advisory skipped: {reason}.", file=sys.stderr)

    # --- Build and write candidates ---
    candidates = build_candidates(selections, item_lookup, grok_results, gemini_results, grok_ran, gemini_ran)

    output = {
        "version": 1,
        "generated_at": now_iso(),
        "model": args.model,
        "input_count": len(items),
        "excluded_approved": len(approved_urls),
        "candidates": candidates,
    }
    write_json(candidates_file, output)
    print(f"[AINWA] Wrote {len(candidates)} candidates to {candidates_file}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
