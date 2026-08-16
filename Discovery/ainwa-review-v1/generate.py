#!/usr/bin/env python3
"""AINWA-005/006 Candidate Generation v1

Pipeline position:
  filtered-discovery.json → Claude selection (max 17) → Grok/Gemini advisory → candidate-queue.json

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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
FILTERED_FILE = DATA_DIR / "filtered-discovery.json"
APPROVED_FILE = DATA_DIR / "approved-queue.json"
CANDIDATES_FILE = DATA_DIR / "candidate-queue.json"

MAX_CANDIDATES = 17
CATEGORIES = ("Models", "Research", "Security", "Governance", "Business", "Infrastructure", "Applications")

# Sources that may contribute at most one story to the shortlist.
# Reuters and The Information are configured-but-inactive (no feed_url yet);
# they are included here so the cap applies automatically once they become active.
ANCHOR_SOURCES: frozenset[str] = frozenset({
    "Reuters",
    "The Information",
    "TechCrunch",
    "BleepingComputer",
    "arXiv",
})

CARRYOVER_DAYS = 3  # unresolved candidates from prior runs survive this many days


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
# Anchor cap enforcement (hard, post-Claude)
# ---------------------------------------------------------------------------

def _enforce_anchor_cap(selections: list[dict], item_lookup: dict[int, dict]) -> list[dict]:
    """Deterministically drop extra stories from the same anchor source.

    Claude is asked to respect the one-per-anchor rule in its prompt, but this
    function hard-enforces it regardless. Extra anchor stories are dropped
    without replacement — no weak filler is added.

    item_lookup is keyed by 1-based sequence integer (matching prompt indices).
    """
    anchor_seen: set[str] = set()
    result: list[dict] = []
    for sel in selections:
        try:
            seq = int(sel.get("item_id") or 0)
        except (TypeError, ValueError):
            seq = 0
        src_name = item_lookup.get(seq, {}).get("source_name", "")
        if src_name in ANCHOR_SOURCES:
            if src_name in anchor_seen:
                print(
                    f"[AINWA] Anchor cap: dropping extra {src_name!r} selection (index {seq}).",
                    file=sys.stderr,
                )
                continue
            anchor_seen.add(src_name)
        result.append(sel)
    return result



# ---------------------------------------------------------------------------
# Structured response parser
# ---------------------------------------------------------------------------

def _parse_claude_response(
    raw: str,
    item_lookup: dict[int, dict],
) -> list[dict]:
    """Parse the structured anchor-slot + non-anchor response into a flat ordered list.

    Expected format:
      {
        "anchor_slots": {"TechCrunch": <story>|null, ...},  # one key per anchor source
        "non_anchor_stories": [<story>, ...],
        "ranking": [<item_id int>, ...]  # all selected ids in priority order
      }

    Returns story dicts with integer item_id fields, ordered by ranking.
    Non-integer or out-of-range item_ids are silently dropped (fail closed).
    """
    data = json.loads(_strip_code_fence(raw))
    if not isinstance(data, dict):
        raise ValueError("Claude response must be a JSON object, not a list or other type")

    all_selections: dict[int, dict] = {}

    for src_name, story in (data.get("anchor_slots") or {}).items():
        if story is None:
            continue
        if not isinstance(story, dict):
            print(
                f"[AINWA] WARNING: anchor slot {src_name!r} is not an object; skipping.",
                file=sys.stderr,
            )
            continue
        try:
            seq = int(story.get("item_id") or 0)
        except (TypeError, ValueError):
            print(
                f"[AINWA] WARNING: non-integer item_id in anchor slot {src_name!r}; skipping.",
                file=sys.stderr,
            )
            continue
        if seq not in item_lookup:
            print(
                f"[AINWA] WARNING: out-of-range index {seq} in anchor slot {src_name!r}; skipping.",
                file=sys.stderr,
            )
            continue
        story = dict(story)
        story["item_id"] = seq
        all_selections[seq] = story

    for i, story in enumerate(data.get("non_anchor_stories") or []):
        if not isinstance(story, dict):
            print(
                f"[AINWA] WARNING: non_anchor_stories[{i}] is not an object; skipping.",
                file=sys.stderr,
            )
            continue
        try:
            seq = int(story.get("item_id") or 0)
        except (TypeError, ValueError):
            print(
                f"[AINWA] WARNING: non-integer item_id in non_anchor_stories[{i}]; skipping.",
                file=sys.stderr,
            )
            continue
        if seq not in item_lookup:
            print(
                f"[AINWA] WARNING: out-of-range index {seq} in non_anchor_stories[{i}]; skipping.",
                file=sys.stderr,
            )
            continue
        if seq in all_selections:
            print(
                f"[AINWA] WARNING: duplicate item_id {seq} in non_anchor_stories[{i}]; skipping.",
                file=sys.stderr,
            )
            continue
        story = dict(story)
        story["item_id"] = seq
        all_selections[seq] = story

    ordered: list[dict] = []
    seen: set[int] = set()
    for val in (data.get("ranking") or []):
        try:
            seq = int(val)
        except (TypeError, ValueError):
            continue
        if seq in all_selections and seq not in seen:
            ordered.append(all_selections[seq])
            seen.add(seq)

    for seq, story in all_selections.items():
        if seq not in seen:
            ordered.append(story)

    return ordered[:MAX_CANDIDATES]


# ---------------------------------------------------------------------------
# Carryover candidate extraction
# ---------------------------------------------------------------------------

def _carryover_candidates(existing: list[dict], cutoff_ts: datetime) -> list[dict]:
    """Return prior unresolved candidates within the rolling 72-hour carryover window.

    A candidate qualifies if:
      - status is 'review' or 'snoozed' (not approved / rejected / archived)
      - discovered_at timestamp >= cutoff_ts (true rolling window, not calendar-date)
    """
    terminal = {"approved", "rejected", "archived"}
    kept: list[dict] = []
    for c in existing:
        if c.get("status") in terminal:
            continue
        raw_ts = c.get("discovered_at") or ""
        try:
            discovered = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if discovered >= cutoff_ts:
            kept.append(c)
    return kept


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_selection_prompt(
    items: list[dict], excluded_urls: set[str]
) -> tuple[list[dict], str]:
    """Build the Claude selection prompt and return (eligible_items, prompt_text).

    All candidates are visible to Claude as a numbered list. 1-based sequence indices
    are the only stable identifiers Claude is given — raw feed IDs are not shown,
    preventing hallucination. Claude returns a structured JSON object with one named
    anchor slot per anchor source (or null) plus a separate non-anchor list and an
    explicit ranking. _enforce_anchor_cap remains as a hard post-selection backstop.
    """
    eligible = [i for i in items if i.get("canonical_url") not in excluded_urls]
    categories_str = ", ".join(CATEGORIES)

    lines = []
    for idx, item in enumerate(eligible, 1):
        age = item.get("age_hours")
        age_str = f"{age:.1f}h" if isinstance(age, (int, float)) else "?h"
        anchor_tag = " [ANCHOR]" if item.get("source_name", "") in ANCHOR_SOURCES else ""
        lines.append(
            f"{idx}. "
            f"[{item.get('source_name', '')} / {item.get('source_role', '')} / "
            f"cite:{item.get('source_citation_allowed', '?')} / {age_str}]{anchor_tag} "
            f"{item.get('item_title', '').strip()}"
        )

    anchor_slots_example = ",\n".join(
        f'    "{src}": null' for src in sorted(ANCHOR_SOURCES)
    )

    prompt = f"""You are ANWU, the AInetWatch editorial AI. Select the best AI-industry stories \
(up to {MAX_CANDIDATES} total) from the list below for today's wire.

Selection criteria (in order of importance):
1. Importance and novelty to the AI industry
2. Timeliness (prefer lower age)
3. Source quality: Original Reporting > Primary Source > Discovery Only; cite:yes > cite:conditional > cite:no
4. Reader relevance for an AI-industry professional audience
5. Non-duplication (prefer stories covering distinct events)

Anchor source rules (items marked [ANCHOR]):
- Reuters, The Information, TechCrunch, BleepingComputer, arXiv (cs.AI / cs.LG) are anchor sources.
- The response schema gives you exactly ONE named slot per anchor source. Fill it with the best \
qualifying story object, or set it to null. Do NOT place anchor-source stories in non_anchor_stories.
- An unfilled anchor slot is correct — do not force a weak story to meet a quota.
- Techmeme is Discovery Only and is never an anchor.
- Do not fill weak stories merely to reach {MAX_CANDIDATES}. Quality over count.

Story fields (same for anchor slots and non-anchor stories):
- item_id: the sequence number from the left of the candidate list (integer, e.g. 3)
- brief_headline: AInetWatch-style in ALL CAPS, ≤12 words, punchy and factual
- public_summary: exactly 3 reader-facing bullets — (1) what happened, (2) why it matters, \
(3) what changes or who is affected. No internal language. Published verbatim if approved.
- editorial_notes: 1-3 sentences for the human reviewer only — source quality, verification \
concerns, corroboration or duplication context. Never published.
- category: exactly one of: {categories_str}
- priority: High | Medium | Low
- top_story: true for the single most important story only, false for all others
- developing: true only if the story is clearly unresolved or actively breaking

Return ONLY a JSON object with this exact structure, no other text:
{{
  "anchor_slots": {{
{anchor_slots_example}
  }},
  "non_anchor_stories": [
    {{
      "item_id": 3,
      "brief_headline": "ALL CAPS HEADLINE HERE",
      "public_summary": ["What happened.", "Why it matters.", "What changes or who is affected."],
      "editorial_notes": "Source quality and verification notes for the reviewer.",
      "category": "Models",
      "priority": "High",
      "top_story": false,
      "developing": false
    }}
  ],
  "ranking": [5, 3, 12]
}}

ranking must list the item_id of EVERY selected story (from anchor_slots AND non_anchor_stories) \
in priority order, highest first. Total ranking length must not exceed {MAX_CANDIDATES}.

Candidates ({len(eligible)} items after excluding already-approved and carryover):
{chr(10).join(lines)}"""

    return eligible, prompt


# ---------------------------------------------------------------------------
# Candidate builder
# ---------------------------------------------------------------------------

def build_candidates(
    selections: list[dict],
    item_lookup: dict[int, dict],
    grok_results: dict[str, dict],
    gemini_results: dict[str, dict],
    grok_ran: bool,
    gemini_ran: bool,
) -> list[dict]:
    """Build candidate records from Claude's selections.

    item_lookup is keyed by 1-based sequence integer — the same numbers Claude
    was shown in the prompt. sel["item_id"] must be that integer; anything
    non-integer or out-of-range is skipped (fail closed). The candidate's "id"
    field is set from the feed record's own item_id to preserve source provenance.
    """
    candidates = []
    for rank, sel in enumerate(selections[:MAX_CANDIDATES], 1):
        raw_seq = sel.get("item_id")
        try:
            seq = int(raw_seq)
        except (TypeError, ValueError):
            print(
                f"[AINWA] WARNING: Claude returned non-integer item_id {raw_seq!r}; skipping.",
                file=sys.stderr,
            )
            continue
        item = item_lookup.get(seq)
        if not item:
            print(
                f"[AINWA] WARNING: Claude returned out-of-range index {seq}; skipping.",
                file=sys.stderr,
            )
            continue

        # Candidate id comes from the feed record, not the selection index.
        # This preserves verbatim source provenance in the queue.
        feed_item_id = item.get("item_id", "")
        raw_url = item.get("canonical_url") or item.get("item_url") or ""
        source_url = raw_url if is_http_url(raw_url) else ""
        source_role = item.get("source_role", "")

        # Advisory results are keyed by str(seq) to match how they were stored.
        seq_key = str(seq)
        advisory = {
            "grok": grok_results.get(seq_key, {
                "status": "skipped",
                "reason": "GROK_API_KEY not set" if not grok_ran else "not returned by advisor",
            }),
            "gemini": gemini_results.get(seq_key, {
                "status": "skipped",
                "reason": "GEMINI_API_KEY not set" if not gemini_ran else "not returned by advisor",
            }),
        }

        # brief_headline is the AI-prepared editorial headline.
        # headline is kept as an alias for backward compatibility.
        brief_headline = str(sel.get("brief_headline") or sel.get("headline") or "")

        # source_resolution: "resolved" when the ingested source is itself the
        # original/primary source; "unresolved" for Discovery Only leads whose
        # underlying primary source has not yet been fetched. The original_headline
        # below is verbatim from the ingested record in both cases — for unresolved
        # leads it is the discovery-source headline, not the underlying article's.
        if source_role in ("Original Reporting", "Primary Source"):
            source_resolution = "resolved"
        else:
            source_resolution = "unresolved"

        candidates.append({
            "id": feed_item_id,
            "status": "review",
            "rank": rank,
            "original_headline": item.get("item_title", ""),
            "source_resolution": source_resolution,
            "source": {
                "name": item.get("source_name", ""),
                "url": source_url,
                "role": source_role,
                "reliability": item.get("source_reliability", ""),
                "paywall": False,
            },
            "proposal": {
                "brief_headline": brief_headline,
                "headline": brief_headline,  # backward-compat alias
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

    # Load approved/archived URLs to exclude
    approved = read_json(approved_file, {"stories": []})
    approved_urls: set[str] = set()
    for story in (approved.get("stories", []) if isinstance(approved, dict) else []):
        url = (story.get("source") or {}).get("url", "")
        if url:
            approved_urls.add(url)

    # Load existing candidates for carryover merge.
    # Prior unresolved candidates within CARRYOVER_DAYS are preserved and
    # their URLs are excluded from new selection to avoid re-picking.
    cutoff_ts = datetime.now(timezone.utc) - timedelta(hours=72)
    existing_candidates_payload = read_json(candidates_file, {"candidates": []})
    existing_candidates = (
        existing_candidates_payload.get("candidates", [])
        if isinstance(existing_candidates_payload, dict)
        else []
    )
    carryover = _carryover_candidates(existing_candidates, cutoff_ts)
    carryover_urls: set[str] = set()
    for c in carryover:
        url = (c.get("source") or {}).get("url", "")
        if url:
            carryover_urls.add(url)

    excluded_urls = approved_urls | carryover_urls
    print(
        f"[AINWA] {len(items)} filtered items, "
        f"{len(approved_urls)} approved/archived URLs excluded, "
        f"{len(carryover)} carryover candidates preserved.",
        file=sys.stderr,
    )

    eligible_items, prompt = build_selection_prompt(items, excluded_urls)
    # 1-based integer keys matching the sequence numbers shown in the prompt.
    item_lookup: dict[int, dict] = {i + 1: eligible_items[i] for i in range(len(eligible_items))}
    print(
        f"[AINWA] {len(eligible_items)} items in prompt after exclusions "
        f"(from {len(items)} filtered).",
        file=sys.stderr,
    )

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
        selections = _parse_claude_response(raw_response, item_lookup)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"[AINWA] Failed to parse Claude response: {exc}", file=sys.stderr)
        print(f"[AINWA] Raw:\n{raw_response}", file=sys.stderr)
        return 1

    print(f"[AINWA] Claude selected {len(selections)} candidates.", file=sys.stderr)

    # Hard-enforce anchor cap regardless of whether Claude respected the prompt rule.
    selections = _enforce_anchor_cap(selections, item_lookup)
    print(f"[AINWA] After anchor cap enforcement: {len(selections)} candidates.", file=sys.stderr)

    # --- Build shortlist JSON for advisors ---
    # item_id here is the integer sequence number; advisors key their response by it.
    shortlist_json = json.dumps([
        {
            "item_id": s.get("item_id"),
            "headline": s.get("brief_headline") or s.get("headline"),
            "public_summary": s.get("public_summary"),
            "source": item_lookup.get(int(s.get("item_id") or 0), {}).get("source_name"),
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
    new_candidates = build_candidates(selections, item_lookup, grok_results, gemini_results, grok_ran, gemini_ran)

    # Merge: today's new candidates first, then carryover (prior unresolved within window).
    # Carryover candidates retain their existing status / human_decision fields.
    merged_candidates = new_candidates + carryover

    output = {
        "version": 1,
        "generated_at": now_iso(),
        "model": args.model,
        "input_count": len(items),
        "excluded_approved": len(approved_urls),
        "carryover_count": len(carryover),
        "candidates": merged_candidates,
    }
    write_json(candidates_file, output)
    print(
        f"[AINWA] Wrote {len(new_candidates)} new + {len(carryover)} carryover "
        f"= {len(merged_candidates)} total candidates to {candidates_file}.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
