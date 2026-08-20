#!/usr/bin/env python3
"""AINWA-005/006 Candidate Generation v1

Pipeline position:
  filtered-discovery.json → preselect (≤60) → Claude selection (max 30) → diversity walk → candidate-queue.json (max 20)

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
import fcntl
import json
import os
import sys
import urllib.request
import urllib.error
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
_RUNTIME = os.environ.get("AINWA_DATA_DIR")
DATA_DIR = Path(_RUNTIME).expanduser().resolve() / "state" if _RUNTIME else ROOT / "data"
FILTERED_FILE = DATA_DIR / "filtered-discovery.json"
APPROVED_FILE = DATA_DIR / "approved-queue.json"
CANDIDATES_FILE = DATA_DIR / "candidate-queue.json"

MAX_CANDIDATES = 20          # hard ceiling on final shortlist (target 15–20)
RANKED_RESPONSE_LIMIT = 30  # max stories Claude may return; diversity walk caps at MAX_CANDIDATES
CLAUDE_TIMEOUT_SECONDS = 180  # socket timeout for Claude API calls; covers larger ranked-list responses
CLAUDE_MAX_OUTPUT_TOKENS = 16000  # headroom for up to 30 enriched story objects
CATEGORIES = ("Models", "Research", "Security", "Governance", "Business", "Infrastructure", "Applications")

# Preselection: deterministic source-aware shortlist sent to Claude.
PRESELECT_MAX = 60     # global ceiling on items sent to Claude
PRESELECT_TARGET = 50  # soft target; per-source caps fill up to this naturally

# Per-source preselection ceilings by role (applied before global cap).
SOURCE_CAP_ORIGINAL_REPORTING = 8
SOURCE_CAP_PRIMARY_SOURCE = 6
SOURCE_CAP_MIXED = 4
SOURCE_CAP_DISCOVERY_ONLY = 3

# Diversity walk: post-Claude per-source caps for final shortlist.
DIVERSITY_PER_SOURCE_MAX = 3   # any source except Discovery Only
DIVERSITY_DISCOVERY_ONLY_MAX = 1  # Discovery Only sources contribute at most 1 slot

CARRYOVER_DAYS = 3  # unresolved candidates from prior runs survive this many days
LAST_CLAUDE_USAGE: dict = {}


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
            "max_tokens": CLAUDE_MAX_OUTPUT_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=CLAUDE_TIMEOUT_SECONDS,
    )
    global LAST_CLAUDE_USAGE
    LAST_CLAUDE_USAGE = dict(resp.get("usage") or {})
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
# Deterministic scoring and preselection
# ---------------------------------------------------------------------------

def _score_item(item: dict) -> float:
    """Deterministic editorial score in [0.0, 1.0] for preselection ordering."""
    role = item.get("source_role", "")
    role_score = {
        "Original Reporting": 1.00,
        "Primary Source": 0.90,
        "Mixed": 0.55,
        "Discovery Only": 0.25,
    }.get(role, 0.20)

    tier = item.get("source_tier")
    tier_score = {1: 1.00, 2: 0.70, 3: 0.30, 4: 0.10}.get(tier, 0.30)

    access = item.get("source_access", "unknown")
    access_score = {"free": 1.00, "unknown": 0.80, "mixed": 0.60, "paywalled": 0.40}.get(access, 0.80)

    age_hours = item.get("age_hours")
    try:
        recency_score = max(0.0, 1.0 - float(age_hours) / 72.0)
    except (TypeError, ValueError):
        recency_score = 0.0

    citation = item.get("source_citation_allowed", "")
    citation_score = {"yes": 1.00, "conditional": 0.60}.get(citation, 0.00)

    raw = (
        0.30 * role_score
        + 0.20 * tier_score
        + 0.15 * access_score
        + 0.25 * recency_score
        + 0.10 * citation_score
    )
    return min(1.0, raw)


def _source_role_cap(source_role: str) -> int:
    """Return the preselection ceiling for a source based on its editorial role."""
    return {
        "Original Reporting": SOURCE_CAP_ORIGINAL_REPORTING,
        "Primary Source": SOURCE_CAP_PRIMARY_SOURCE,
        "Mixed": SOURCE_CAP_MIXED,
        "Discovery Only": SOURCE_CAP_DISCOVERY_ONLY,
    }.get(source_role, SOURCE_CAP_PRIMARY_SOURCE)


def preselect_candidates(items: list[dict]) -> list[dict]:
    """Return a diverse shortlist of at most PRESELECT_MAX items for Claude.

    Phase 1: score all items; sort descending (tie-break: item_id lexicographic).
    Phase 2: per-source ceiling by role — each source contributes at most
             _source_role_cap(role) items.
    Phase 3: global ceiling — keep at most PRESELECT_MAX items total.
    """
    scored = sorted(
        items,
        key=lambda i: (-_score_item(i), i.get("item_id") or ""),
    )

    per_source: dict[str, int] = {}
    selected: list[dict] = []
    for item in scored:
        src = item.get("source_name", "")
        role = item.get("source_role", "")
        cap = _source_role_cap(role)
        count = per_source.get(src, 0)
        if count >= cap:
            continue
        per_source[src] = count + 1
        selected.append(item)
        if len(selected) >= PRESELECT_MAX:
            break

    return selected


# ---------------------------------------------------------------------------
# Diversity walk (deterministic post-Claude reducer)
# ---------------------------------------------------------------------------

def _diversity_walk(
    ranked: list[dict],
    item_lookup: dict[int, dict],
) -> list[dict]:
    """Walk Claude's ranked list, enforcing source diversity and MAX_CANDIDATES.

    Accepts items in Claude's priority order. Skips any item from a source that
    has already reached its diversity cap (DIVERSITY_DISCOVERY_ONLY_MAX for
    Discovery Only sources, DIVERSITY_PER_SOURCE_MAX for all others). Stops when
    MAX_CANDIDATES is reached or the list is exhausted. Never pads with fillers.
    item_lookup is keyed by 1-based sequence integer.
    """
    accepted: list[dict] = []
    source_counts: dict[str, int] = {}
    for sel in ranked:
        if len(accepted) >= MAX_CANDIDATES:
            break
        try:
            seq = int(sel.get("item_id") or 0)
        except (TypeError, ValueError):
            seq = 0
        item = item_lookup.get(seq, {})
        src = item.get("source_name", "")
        role = item.get("source_role", "")
        cap = (
            DIVERSITY_DISCOVERY_ONLY_MAX
            if role == "Discovery Only"
            else DIVERSITY_PER_SOURCE_MAX
        )
        count = source_counts.get(src, 0)
        if count >= cap:
            print(
                f"[AINWA] Diversity walk: skipping {src!r} (index {seq}), "
                f"cap {cap} reached.",
                file=sys.stderr,
            )
            continue
        source_counts[src] = count + 1
        accepted.append(sel)
    return accepted



# ---------------------------------------------------------------------------
# Flat ranked-array response parser
# ---------------------------------------------------------------------------

def _parse_claude_response(
    raw: str,
    item_lookup: dict[int, dict],
) -> list[dict]:
    """Parse Claude's flat ranked-array response into a validated list.

    Expected format: a JSON array of up to RANKED_RESPONSE_LIMIT story objects,
    each with an integer item_id (1-based prompt sequence index), in priority order.

    Returns validated story dicts with integer item_id fields preserving Claude's
    ranking order. Non-integer or out-of-range item_ids fail closed (dropped).
    Duplicate indices are deduplicated (first occurrence kept).
    """
    data = json.loads(_strip_code_fence(raw))
    if not isinstance(data, list):
        raise ValueError("Claude response must be a JSON array")

    result: list[dict] = []
    seen_seqs: set[int] = set()
    for i, story in enumerate(data):
        if not isinstance(story, dict):
            print(
                f"[AINWA] WARNING: ranked_stories[{i}] is not an object; skipping.",
                file=sys.stderr,
            )
            continue
        try:
            seq = int(story.get("item_id") or 0)
        except (TypeError, ValueError):
            print(
                f"[AINWA] WARNING: non-integer item_id at position {i}; skipping.",
                file=sys.stderr,
            )
            continue
        if seq not in item_lookup:
            print(
                f"[AINWA] WARNING: out-of-range index {seq} at position {i}; skipping.",
                file=sys.stderr,
            )
            continue
        if seq in seen_seqs:
            print(
                f"[AINWA] WARNING: duplicate index {seq} at position {i}; skipping.",
                file=sys.stderr,
            )
            continue
        seen_seqs.add(seq)
        story = dict(story)
        story["item_id"] = seq
        result.append(story)
    return result


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
        # Human-entered stories must never age out merely because a sourcing run
        # overlaps the review session. They remain until the editor explicitly
        # publishes, rejects, or archives them.
        if c.get("intake_method") == "manual":
            kept.append(c)
            continue
        raw_ts = c.get("queued_at") or c.get("discovered_at") or ""
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
    """Build the Claude selection prompt and return (preselected_items, prompt_text).

    Eligible items are scored deterministically and narrowed to PRESELECT_MAX
    via per-source role ceilings before being sent to Claude. Claude receives a
    flat numbered list in preselection score order and returns a flat ranked array.
    Post-processing (_diversity_walk) enforces per-source diversity caps and
    the MAX_CANDIDATES ceiling deterministically.
    """
    eligible = [i for i in items if i.get("canonical_url") not in excluded_urls]
    categories_str = ", ".join(CATEGORIES)

    preselected = preselect_candidates(eligible)

    # 1-based index over the preselected list (same order as item_lookup in main()).
    item_idx: dict[int, int] = {id(item): seq for seq, item in enumerate(preselected, 1)}

    def _line(item: dict) -> str:
        age = item.get("age_hours")
        age_str = f"{age:.1f}h" if isinstance(age, (int, float)) else "?h"
        return (
            f"{item_idx[id(item)]}. "
            f"[{item.get('source_name', '')} / {item.get('source_role', '')} / "
            f"cite:{item.get('source_citation_allowed', '?')} / {age_str}] "
            f"{item.get('item_title', '').strip()}"
        )

    candidates_text = "\n".join(_line(it) for it in preselected) if preselected else "(no eligible candidates this run)"

    prompt = f"""You are ANWU, the AInetWatch editorial AI. Select the best AI-industry stories \
from the list below for today's wire.

Selection criteria (in order of importance):
1. Importance and novelty to the AI industry
2. Timeliness (prefer lower age)
3. Source quality: Original Reporting > Primary Source > Discovery Only; cite:yes > cite:conditional > cite:no
4. Reader relevance for an AI-industry professional audience
5. Non-duplication (prefer stories covering distinct events)

Response rules:
- Each integer index maps to exactly one feed record.
- Rank up to {RANKED_RESPONSE_LIMIT} stories in priority order. Post-processing will enforce \
source diversity (at most {DIVERSITY_PER_SOURCE_MAX} per source; at most \
{DIVERSITY_DISCOVERY_ONLY_MAX} per Discovery Only source) and cap the shortlist at \
{MAX_CANDIDATES}.
- You may rank multiple stories from the same source — post-processing will keep only the \
top-ranked ones within each source's diversity cap.
- Do not pad with weak stories merely to reach {RANKED_RESPONSE_LIMIT}. Quality over count.

Story fields:
- item_id: the integer index from the candidate list (e.g. 3)
- brief_headline: AInetWatch-style in ALL CAPS, ≤12 words, punchy and factual
- public_summary: exactly 3 reader-facing bullets — (1) what happened, (2) why it matters, \
(3) what changes or who is affected. No internal language. Published verbatim if approved.
- editorial_notes: 1-3 sentences for the human reviewer only — source quality, verification \
concerns, corroboration or duplication context. Never published.
- category: exactly one of: {categories_str}
- priority: High | Medium | Low
- top_story: true for the single most important story only, false for all others
- developing: true only if the story is clearly unresolved or actively breaking

Return ONLY a JSON array of up to {RANKED_RESPONSE_LIMIT} story objects, ranked \
highest-priority first, no other text:
[
  {{
    "item_id": 42,
    "brief_headline": "ALL CAPS HEADLINE HERE",
    "public_summary": ["What happened.", "Why it matters.", "What changes or who is affected."],
    "editorial_notes": "Source quality and verification notes for the reviewer.",
    "category": "Models",
    "priority": "High",
    "top_story": false,
    "developing": false
  }}
]

Candidates:

{candidates_text}"""

    return preselected, prompt


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
            "queued_at": now_iso(),
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
        f"[AINWA] {len(eligible_items)} items in prompt after exclusions and preselection "
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

    if _RUNTIME and LAST_CLAUDE_USAGE:
        from control_state import ControlState
        ControlState(Path(_RUNTIME).expanduser().resolve()).record_usage(
            "candidate_selection", "anthropic", args.model,
            LAST_CLAUDE_USAGE.get("input_tokens", 0),
            LAST_CLAUDE_USAGE.get("output_tokens", 0),
            0.0,
        )

    try:
        selections = _parse_claude_response(raw_response, item_lookup)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"[AINWA] Failed to parse Claude response: {exc}", file=sys.stderr)
        print(f"[AINWA] Raw:\n{raw_response}", file=sys.stderr)
        return 1

    print(f"[AINWA] Claude ranked {len(selections)} candidates.", file=sys.stderr)

    selections = _diversity_walk(selections, item_lookup)
    print(f"[AINWA] After diversity walk: {len(selections)} candidates.", file=sys.stderr)

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

    # Re-read the queue inside an exclusive file lock to incorporate any reviewer
    # changes (manual adds, review actions) that occurred during the API-call window.
    with _queue_lock(candidates_file):
        current_payload = read_json(candidates_file, {"candidates": []})
        current_candidates = (
            current_payload.get("candidates", [])
            if isinstance(current_payload, dict) else []
        )
        fresh_carryover = _carryover_candidates(current_candidates, cutoff_ts)
        merged_candidates = new_candidates + fresh_carryover
        output = {
            "version": 1,
            "generated_at": now_iso(),
            "model": args.model,
            "input_count": len(items),
            "excluded_approved": len(approved_urls),
            "carryover_count": len(fresh_carryover),
            "candidates": merged_candidates,
        }
        write_json(candidates_file, output)
    print(
        f"[AINWA] Wrote {len(new_candidates)} new + {len(fresh_carryover)} carryover "
        f"= {len(merged_candidates)} total candidates to {candidates_file}.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
