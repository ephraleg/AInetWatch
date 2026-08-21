#!/usr/bin/env python3
"""AINWA-009 Static Homepage Builder

Reads data/approved-queue.json and writes:
  index.html   — the 60 most recently approved stories (by approved_at desc)
  archive.html — stories 61 and beyond

Homepage retention rule (locked — see docs/ANWU-HOMEPAGE-ARCHITECTURE.md §5b):
  Selection is by approved_at descending only.
  priority/top_story/developing affect rendering prominence, not which 50 are selected.

Security gates (all checked before any output is written):
  - approved.locked must be True for every story — hard exit if not
  - all story text is html.escaped
  - source URLs must be http(s); anything else renders as plain text with no href
  - editorial_notes is never read or rendered

Does NOT: call any AI API, write approved-queue.json, or deploy anything.

Run:
  python3 build.py
  python3 build.py --dry-run
  python3 build.py --data-dir /tmp/d --output-dir /tmp/out --template /tmp/t.html
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent.parent
# The website is always built from the cumulative Published Stories list.
# AINWA_DATA_DIR contains workflow state and must never replace this source.
DATA_DIR = ROOT / "data"
TEMPLATE_FILE = REPO_ROOT / "index-live-content.html"
OUTPUT_DIR = REPO_ROOT

HOMEPAGE_CAP = 60


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def is_http_url(value) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def esc(text) -> str:
    return html.escape(str(text or ""), quote=True)


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Story loading and validation
# ---------------------------------------------------------------------------

def load_stories(data_dir: Path) -> tuple[list[dict], list[dict]]:
    """Load approved and archived stories from approved-queue.json.

    Returns (approved_eligible, archive_only):
      approved_eligible — status "approved" (or absent); feed the homepage pool.
      archive_only      — status "archived"; go directly to archive.html.

    Hard-exits if any story's approved.locked is not True.
    Public content is always read from the approved block for both types.
    """
    data = read_json(data_dir / "approved-queue.json")
    stories = data.get("stories", []) if isinstance(data, dict) else []

    # Gate: every story must be locked before any output is written.
    for story in stories:
        if story.get("approved", {}).get("locked") is not True:
            sid = story.get("id", "<unknown>")
            print(f"[AINWA] BUILD ERROR: story {sid!r} is not locked. Refusing to build.", file=sys.stderr)
            sys.exit(1)

    approved_eligible: list[dict] = []
    archive_only: list[dict] = []
    unknown: list[str] = []

    for s in stories:
        status = s.get("status", "approved")
        if status == "approved":
            approved_eligible.append(s)
        elif status == "archived":
            archive_only.append(s)
        else:
            unknown.append(str(s.get("id", "<unknown>")))

    if unknown:
        print(f"[AINWA] WARNING: {len(unknown)} record(s) with unrecognised status excluded: {unknown[:5]}", file=sys.stderr)

    return approved_eligible, archive_only


def sort_by_approved_at(stories: list[dict]) -> list[dict]:
    """Sort descending by approved_at. Missing/invalid dates sort last."""
    return sorted(
        stories,
        key=lambda s: s.get("approved", {}).get("approved_at") or "",
        reverse=True,
    )


def _priority_rank(story: dict) -> int:
    val = str(story.get("approved", {}).get("priority") or "").lower()
    return {"high": 0, "medium": 1, "low": 2}.get(val, 3)


# ---------------------------------------------------------------------------
# Template chrome loading
# ---------------------------------------------------------------------------

def load_chrome(template_path: Path) -> tuple[str, str]:
    """Split template into (head_chrome, tail_chrome) at <main></main>."""
    text = template_path.read_text(encoding="utf-8")
    HEAD_MARKER = '<main class="wrap">'
    TAIL_MARKER = "</main>"
    head_end = text.index(HEAD_MARKER) + len(HEAD_MARKER)
    # Use rindex so we get the last </main> (the one that closes the page main, not any inside)
    tail_start = text.rindex(TAIL_MARKER)
    return text[:head_end], text[tail_start:]


# ---------------------------------------------------------------------------
# HTML renderers
# ---------------------------------------------------------------------------

def _render_tooltip(story: dict, tip_prefix: str) -> str:
    sid = story.get("id", "")
    approved = story.get("approved", {})
    # Read only public_summary — editorial_notes is never read here.
    bullets = approved.get("public_summary") or []
    if not isinstance(bullets, list):
        bullets = []
    category = esc(approved.get("category") or "")
    social_tags = approved.get("social_tags") or []
    if isinstance(social_tags, str):
        social_tags = social_tags.split()
    source_name = esc((story.get("source") or {}).get("name") or "")
    raw_date = str(story.get("published_at") or story.get("discovered_at") or "").strip()
    try:
        story_date = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).strftime("%m/%d/%y")
    except ValueError:
        try:
            story_date = parsedate_to_datetime(raw_date).strftime("%m/%d/%y")
        except (TypeError, ValueError):
            story_date = raw_date
    tip_id = f"tip-{esc(tip_prefix)}-{esc(sid)}"
    bullet_html = "".join(f"<li>{esc(b)}</li>" for b in bullets[:5])
    tag_html = f'<span class="tag">{category}</span>' if category else ""
    hashtag_html = "".join(f'<span class="hashtag">{esc(tag)}</span>' for tag in social_tags[:8])
    tooltip_heading = " ".join(part for part in (source_name, esc(story_date)) if part)
    return (
        f'<input type="checkbox" id="{tip_id}" class="tip-toggle">\n'
        f'<label for="{tip_id}" class="tip-indicator" aria-label="Show summary">Summary ⓘ</label>\n'
        f'<div class="tooltip">\n'
        f'  <div class="tooltip-head"><strong>{tooltip_heading}</strong></div>\n'
        f'  <ul>{bullet_html}</ul>\n'
        f'  <div class="tags">{tag_html}{hashtag_html}</div>\n'
        f'</div>'
    )


def _render_link(story: dict) -> str:
    approved = story.get("approved", {})
    source = story.get("source", {})
    # Prefer brief_headline; fall back to headline for backward compat.
    headline = esc(approved.get("brief_headline") or approved.get("headline") or "")
    url = source.get("url") or ""
    if is_http_url(url):
        return (
            f'<a class="headline" href="{esc(url)}" '
            f'target="_blank" rel="noopener noreferrer">{headline}</a>'
        )
    # Non-http(s) URL: render headline as unlinked text. No href written.
    return f'<span class="headline">{headline}</span>'


def render_top_story(story: dict) -> str:
    sid = esc(story.get("id") or "")
    approved = story.get("approved", {})
    dev_tag = '<span class="developing-tag">Developing</span>\n          ' if approved.get("developing") else ""
    return (
        f'\n    <section class="top-story" aria-labelledby="top-story-heading">\n'
        f'      <h2 id="top-story-heading" class="visually-hidden">Top Story</h2>\n'
        f'      <div class="top-story-body" data-story-id="{sid}" data-top-story="true">\n'
        f'        <div class="story-text">\n'
        f'          {dev_tag}{_render_link(story)}\n'
        f'          {_render_tooltip(story, "top")}\n'
        f'        </div>\n'
        f'      </div>\n'
        f'    </section>\n'
    )


def render_developing_strip(story: dict) -> str:
    sid = esc(story.get("id") or "")
    return (
        f'\n    <section class="developing-strip-section" aria-labelledby="developing-heading">\n'
        f'      <h2 id="developing-heading" class="visually-hidden">Developing</h2>\n'
        f'      <div class="developing-strip" data-story-id="{sid}" data-developing="true">\n'
        f'        <span class="developing-tag">Developing</span>\n'
        f'        {_render_link(story)}\n'
        f'        {_render_tooltip(story, "dev")}\n'
        f'      </div>\n'
        f'    </section>\n'
    )


def render_wire_story(story: dict, col_idx: int) -> str:
    sid = esc(story.get("id") or "")
    return (
        f'          <div class="story" data-story-id="{sid}">\n'
        f'            <div class="story-body"><div class="story-text">\n'
        f'              {_render_link(story)}\n'
        f'              {_render_tooltip(story, f"c{col_idx}")}\n'
        f'            </div></div>\n'
        f'          </div>\n'
    )


def render_main_content(stories: list[dict]) -> str:
    """Generate the <main> body for index.html from the 50-story homepage set."""
    if not stories:
        return '\n    <p style="padding:20px 0">No approved stories yet.</p>\n'

    top = next((s for s in stories if s.get("approved", {}).get("top_story")), None)
    developing = next(
        (s for s in stories if s.get("approved", {}).get("developing") and s is not top),
        None,
    )

    used_ids = {id(top), id(developing)} - {id(None)}
    wire = [s for s in stories if id(s) not in used_ids]
    # Sort wire by priority; stable sort preserves approved_at order within same priority.
    wire.sort(key=_priority_rank)

    cols: list[list[dict]] = [[], [], []]
    for i, story in enumerate(wire):
        cols[i % 3].append(story)

    parts: list[str] = []
    if top:
        parts.append(render_top_story(top))
    if developing:
        parts.append(render_developing_strip(developing))

    parts.append('\n    <section aria-labelledby="wire-heading">')
    parts.append('      <h2 id="wire-heading" class="visually-hidden">Headlines</h2>')
    parts.append('      <div class="wire-grid">')
    for col_idx, col_stories in enumerate(cols):
        parts.append('        <div class="wire-col">')
        for story in col_stories:
            parts.append(render_wire_story(story, col_idx + 1))
        parts.append('        </div>')
    parts.append('      </div>')
    parts.append('    </section>\n')
    return "\n".join(parts)


def render_archive_content(stories: list[dict]) -> str:
    """Generate the <main> body for archive.html."""
    if not stories:
        return '\n    <p style="padding:20px 0">No archive stories yet.</p>\n'

    parts = [
        '\n    <section aria-labelledby="archive-heading">',
        '      <h2 id="archive-heading" '
        'style="padding:14px 0 8px;border-bottom:1px solid var(--rule)">Archive</h2>',
        '      <div class="wire-grid">',
        '        <div class="wire-col" style="grid-column:1/-1;border:none;padding:0">',
    ]
    for story in stories:
        parts.append(render_wire_story(story, 0))
    parts.extend(['        </div>', '      </div>', '    </section>\n'])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Build entry point
# ---------------------------------------------------------------------------

def build(data_dir: Path, output_dir: Path, template_file: Path, dry_run: bool = False) -> None:
    approved_eligible, archive_only = load_stories(data_dir)
    head_chrome, tail_chrome = load_chrome(template_file)

    # Homepage pool: approved records only, newest first.
    sorted_eligible = sort_by_approved_at(approved_eligible)
    homepage = sorted_eligible[:HOMEPAGE_CAP]
    overflow = sorted_eligible[HOMEPAGE_CAP:]

    # Archive: homepage overflow + straight-to-archive records, sorted by approved_at desc.
    archive = sort_by_approved_at(overflow + archive_only)

    homepage_html = head_chrome + render_main_content(homepage) + tail_chrome
    archive_html = head_chrome + render_archive_content(archive) + tail_chrome

    if dry_run:
        print(
            f"[AINWA] DRY RUN: would write {len(homepage)} stories to index.html, "
            f"{len(archive)} to archive.html ({len(overflow)} overflow + {len(archive_only)} archived)",
            file=sys.stderr,
        )
        return

    write_file(output_dir / "index.html", homepage_html)
    write_file(output_dir / "archive.html", archive_html)
    print(
        f"[AINWA] Built index.html ({len(homepage)} stories) "
        f"and archive.html ({len(archive)} stories: {len(overflow)} overflow + {len(archive_only)} archived).",
        file=sys.stderr,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="AINWA-009 Static Homepage Builder")
    parser.add_argument("--data-dir", default=None, help="Override data directory")
    parser.add_argument("--output-dir", default=None, help="Override output directory")
    parser.add_argument("--template", default=None, help="Override template HTML file")
    parser.add_argument("--dry-run", action="store_true", help="Report counts; write no files")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir).resolve() if args.data_dir else DATA_DIR
    output_dir = Path(args.output_dir).resolve() if args.output_dir else OUTPUT_DIR
    template_file = Path(args.template).resolve() if args.template else TEMPLATE_FILE

    if not template_file.exists():
        print(f"[AINWA] Template not found: {template_file}", file=sys.stderr)
        return 1

    build(data_dir, output_dir, template_file, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
