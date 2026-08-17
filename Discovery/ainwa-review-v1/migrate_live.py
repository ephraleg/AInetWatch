#!/usr/bin/env python3
"""migrate_live.py — One-time import of live homepage stories into AINWA approved queue.

Parses index-live-content.html, extracts up to IMPORT_CAP stories, and writes
them into Discovery/ainwa-review-v1/data/approved-queue.json in the exact
schema consumed by build.py.

Usage:
    python3 migrate_live.py --dry-run     # report only, write nothing (safe default)
    python3 migrate_live.py               # write (refuses if queue already non-empty)
    python3 migrate_live.py --force       # overwrite non-empty queue (backup created first)

Safety:
    - Never modifies candidate, rejected, or archive queues
    - --force creates a .bak backup before any write
    - No publish, deploy, or external API calls
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent.parent
DEFAULT_SOURCE_HTML = REPO_ROOT / "index-live-content.html"
DEFAULT_APPROVED_FILE = ROOT / "data" / "approved-queue.json"

IMPORT_CAP = 49

# Base timestamp from live-content HTML comment:
# "LAST UPDATED 13-AUG-2026 07:43 EDT" — EDT = UTC-4, so 11:43 UTC.
# Each successive story gets a 1-minute-earlier timestamp so build.py's
# approved_at-descending sort preserves the original document order.
BASE_TS = datetime(2026, 8, 13, 11, 43, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# HTML extraction
# ---------------------------------------------------------------------------

def _ts(rank: int) -> str:
    return (BASE_TS - timedelta(minutes=rank)).strftime("%Y-%m-%dT%H:%M:%SZ")


def extract_stories(html_text: str) -> list[dict]:
    """Return one dict per story found in the live-content HTML, in document order."""
    story_id_re = re.compile(r'data-story-id="([^"]+)"')
    matches = list(story_id_re.finditer(html_text))

    stories: list[dict] = []
    for i, m in enumerate(matches):
        story_id = m.group(1)
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(html_text)
        block = html_text[start:end]

        # top_story / developing flags live on the same opening tag as data-story-id
        opener = block[:300]
        top_story = 'data-top-story="true"' in opener
        developing = 'data-developing="true"' in opener

        # Headline + URL — class="headline" always precedes href in this file
        hl_m = re.search(
            r'<a[^>]+class="headline"[^>]+href="([^"]*)"[^>]*>\s*(.*?)\s*</a>',
            block, re.DOTALL,
        )
        url = hl_m.group(1).strip() if hl_m else ""
        headline = re.sub(r"\s+", " ", hl_m.group(2)).strip() if hl_m else ""

        # Source label
        src_m = re.search(r'<span[^>]+class="source"[^>]*>([^<]+)</span>', block)
        source = src_m.group(1).strip() if src_m else ""

        # Tooltip bullets
        bullets = [
            b.strip()
            for b in re.findall(r"<li>(.*?)</li>", block, re.DOTALL)
            if b.strip()
        ]

        # Category tags (build.py renders only one; we keep all for completeness in
        # the approved record but the first becomes the canonical category string)
        tags = [
            t.strip()
            for t in re.findall(r'class="tag">([^<]+)</span>', block)
            if t.strip()
        ]

        stories.append({
            "id": story_id,
            "top_story": top_story,
            "developing": developing,
            "headline": headline,
            "url": url,
            "source": source,
            "bullets": bullets,
            "tags": tags,
        })

    return stories


# ---------------------------------------------------------------------------
# Schema conversion
# ---------------------------------------------------------------------------

def to_approved_record(story: dict, rank: int) -> dict:
    """Convert an extracted story into the exact approved-queue.json schema."""
    # build.py renders only the first category tag; the second is stored in
    # _migration_tags (internal, never read by build.py or server.py).
    category = story["tags"][0] if story["tags"] else ""
    ts = _ts(rank)
    return {
        "id": f"migrate-{story['id']}",
        "intake_method": "migration",
        "_migration_source_id": story["id"],
        "_migration_tags": story["tags"],       # internal — never rendered publicly
        "source": {
            "name": story["source"],
            "url": story["url"],
            "paywall": False,
        },
        "approved": {
            "brief_headline": story["headline"],
            "headline": story["headline"],
            "public_summary": story["bullets"],
            "category": category,
            "priority": "Medium",               # not present in source HTML
            "top_story": story["top_story"],
            "developing": story["developing"],
            "paywall": False,                   # not present in source HTML
            "approved_at": ts,
            "approved_by": "migration",
            "locked": True,
        },
        "original_headline": story["headline"],  # source has editorial headlines only
        "discovered_at": ts,
        "language": {"source_language": "en", "localizations": {}},
    }


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def read_approved(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "updated_at": None, "stories": []}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def backup_and_write(path: Path, payload: dict) -> Path:
    bak = path.with_suffix(".json.bak")
    if path.exists():
        shutil.copy2(path, bak)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    return bak


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

FIELDS_NOT_PRESERVED = [
    "priority — not in source HTML; defaults to 'Medium'",
    "paywall — not in source HTML; defaults to false",
    "original_headline — source has editorial headlines only; set equal to brief_headline",
    "source.role / source.reliability — not in source HTML; omitted",
    "second category tag — source has 2 tags/story; build.py renders one; "
    "first tag → approved.category, both stored in internal _migration_tags",
]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate live homepage stories into AINWA approved queue"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Report only; write no files")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite a non-empty approved queue (backup created first)")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE_HTML),
                        help="Override source HTML path")
    parser.add_argument("--approved-file", default=str(DEFAULT_APPROVED_FILE),
                        help="Override approved-queue.json path")
    args = parser.parse_args(argv)

    source_path = Path(args.source)
    approved_path = Path(args.approved_file)

    # --- Source validation ---
    if not source_path.exists():
        print(f"[migrate] ERROR: source HTML not found: {source_path}", file=sys.stderr)
        return 1

    html_text = source_path.read_text(encoding="utf-8")
    raw_stories = extract_stories(html_text)

    to_import = raw_stories[:IMPORT_CAP]
    skipped = raw_stories[IMPORT_CAP:]
    records = [to_approved_record(s, rank) for rank, s in enumerate(to_import)]

    # --- Current queue state ---
    current = read_approved(approved_path)
    current_count = len(current.get("stories", []))

    # --- Report ---
    print()
    print("=== AINWA Live Content Migration ===")
    print(f"Source:               {source_path}")
    print(f"Target:               {approved_path}")
    print(f"Stories available:    {len(raw_stories)}")
    print(f"Import cap:           {IMPORT_CAP}")
    print(f"Would import:         {len(to_import)}")
    print(f"Would skip (over cap):{len(skipped)}")
    print(f"Current queue:        {current_count} stories")
    if args.force:
        print(f"Resulting count:      {len(records)} (replace — --force)")
    else:
        print(f"Resulting count:      {current_count + len(records)} (append)")

    print()
    print("Fields that cannot be fully preserved:")
    for note in FIELDS_NOT_PRESERVED:
        print(f"  - {note}")

    print()
    print("Files a real run would change:")
    print(f"  WRITE:  {approved_path}")
    print(f"  BACKUP: {approved_path.with_suffix('.json.bak')}  (created before write)")

    if skipped:
        print()
        print(f"Stories over cap (would be skipped):")
        for s in skipped:
            print(f"  {s['id']}: {s['headline'][:70]}")

    if args.dry_run:
        print()
        print("[DRY RUN] No files written.")
        return 0

    # --- Safety gate ---
    if current_count > 0 and not args.force:
        print(
            f"\n[migrate] REFUSED: approved-queue.json already has {current_count} stories.\n"
            f"          Use --force to overwrite (a backup will be created first).",
            file=sys.stderr,
        )
        return 2

    # --- Write ---
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if args.force:
        new_stories = records
    else:
        new_stories = current.get("stories", []) + records

    new_payload = {"version": 1, "updated_at": now, "stories": new_stories}
    bak = backup_and_write(approved_path, new_payload)

    print()
    if approved_path.with_suffix(".json.bak").exists():
        print(f"[migrate] Backup:  {bak}")
    print(f"[migrate] Wrote {len(records)} records → {approved_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
