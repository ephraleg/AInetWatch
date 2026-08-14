#!/usr/bin/env python3
"""AINWA Discovery Filter v1

Reads data/raw-discovery.json (produced by ingest.py) and writes
data/filtered-discovery.json with stale items removed, URLs canonicalized,
and within-run duplicates eliminated.

What this does:
  1. Rejects items older than --stale-hours (default 48) relative to the
     run_id timestamp in the input file.  Items with an unparseable date are
     kept (safe default).
  2. Canonicalizes item_url: lowercase scheme/host, strip trailing slash from
     path, strip common tracking parameters, strip fragment.  Result stored
     as canonical_url alongside the unchanged original.
  3. Drops items whose canonical_url was already seen (keeps first occurrence).
  4. Drops items whose title fingerprint (first 10 words, lowercased, punct
     stripped) matches an earlier item.  Tie-breaking: "Original Reporting"
     beats "Discovery Only"; then earliest item_published wins.

Does NOT: call Claude/Grok/Gemini, fetch article pages, write
candidate-queue.json, or deploy anything.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"

# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

_DATE_FORMATS = (
    "%a, %d %b %Y %H:%M:%S %z",    # RFC 2822 with numeric offset
    "%a, %d %b %Y %H:%M:%S GMT",   # RFC 2822 with literal "GMT"
    "%Y-%m-%dT%H:%M:%SZ",          # ISO 8601 UTC
    "%Y-%m-%dT%H:%M:%S%z",         # ISO 8601 with offset
)


def _parse_pub_date(s: str | None) -> datetime | None:
    """Parse item_published in any of the four feed date formats.

    Returns a timezone-aware datetime, or None if the string is absent or
    does not match any known format.
    """
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    return None


def _parse_run_id(run_id: str) -> datetime:
    dt = datetime.strptime(run_id, "%Y-%m-%dT%H:%M:%SZ")
    return dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# URL canonicalization
# ---------------------------------------------------------------------------

_TRACKING_PARAMS: frozenset[str] = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "ref", "referrer", "source", "via",
    "mc_cid", "mc_eid",
    "fbclid", "gclid", "dclid", "msclkid", "twclid", "li_fat_id",
    "_hsenc", "_hsmi", "mkt_tok", "yclid", "icid",
})


def canonical_url(url: str) -> str:
    """Return a normalized URL suitable for deduplication.

    Changes: lowercase scheme and host, strip trailing slash from path, remove
    tracking query parameters (sorted remainder kept), drop fragment.
    The original item_url is never modified.
    """
    url = url.strip()
    p = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(p.query, keep_blank_values=False)
    qs_clean = {k: v for k, v in qs.items() if k.lower() not in _TRACKING_PARAMS}
    query = urllib.parse.urlencode(sorted(qs_clean.items()), doseq=True)
    return urllib.parse.urlunparse((
        p.scheme.lower(),
        p.netloc.lower(),
        p.path.rstrip("/"),
        "",       # params — stripped
        query,
        "",       # fragment — stripped
    ))


# ---------------------------------------------------------------------------
# Title fingerprinting
# ---------------------------------------------------------------------------

_ROLE_ORDER: dict[str, int] = {
    "Original Reporting": 0,
    "Discovery Only": 1,
}

_DISTANT_FUTURE = datetime.max.replace(tzinfo=timezone.utc)


def _title_fingerprint(title: str | None, words: int = 10) -> str:
    if not title:
        return ""
    t = re.sub(r"[^\w\s]", " ", title.lower())
    return " ".join(t.split()[:words])


# ---------------------------------------------------------------------------
# Filter stages
# ---------------------------------------------------------------------------

def _reject_stale(
    items: list[dict],
    run_ts: datetime,
    cutoff_hours: float,
) -> tuple[list[dict], int]:
    """Drop items older than cutoff_hours relative to run_ts.

    Items with an unparseable date are kept.  Each surviving item gets two
    new fields: canonical_url and age_hours (None when date is unparseable).
    """
    kept: list[dict] = []
    dropped = 0
    for item in items:
        pub_dt = _parse_pub_date(item.get("item_published"))
        if pub_dt is not None:
            age = (run_ts - pub_dt).total_seconds() / 3600
            if age > cutoff_hours:
                dropped += 1
                continue
            age_hours: float | None = round(age, 2)
        else:
            age_hours = None
        annotated = dict(item)
        annotated["canonical_url"] = canonical_url(item.get("item_url", ""))
        annotated["age_hours"] = age_hours
        kept.append(annotated)
    return kept, dropped


def _dedup_canonical_url(items: list[dict]) -> tuple[list[dict], int]:
    """Keep the first item for each canonical_url; drop subsequent duplicates."""
    seen: set[str] = set()
    kept: list[dict] = []
    dropped = 0
    for item in items:
        canon = item.get("canonical_url", "")
        if canon in seen:
            dropped += 1
        else:
            seen.add(canon)
            kept.append(item)
    return kept, dropped


def _dedup_title(items: list[dict]) -> tuple[list[dict], int]:
    """Drop title-fingerprint duplicates, keeping the best item per group.

    Tie-break order: Original Reporting > Discovery Only > other roles; then
    earliest item_published; then original list position (stable).
    """
    groups: dict[str, list[tuple[int, dict]]] = {}
    for idx, item in enumerate(items):
        fp = _title_fingerprint(item.get("item_title"))
        groups.setdefault(fp, []).append((idx, item))

    winners: set[str] = set()
    for fp, group in groups.items():
        if len(group) == 1:
            winners.add(group[0][1]["item_id"])
        else:
            def _sort_key(pair: tuple[int, dict]) -> tuple[int, datetime, int]:
                _, item = pair
                role_rank = _ROLE_ORDER.get(item.get("source_role", ""), 99)
                pub_dt = _parse_pub_date(item.get("item_published")) or _DISTANT_FUTURE
                return (role_rank, pub_dt, pair[0])
            group.sort(key=_sort_key)
            winners.add(group[0][1]["item_id"])

    kept = [item for item in items if item["item_id"] in winners]
    dropped = len(items) - len(kept)
    return kept, dropped


# ---------------------------------------------------------------------------
# Main filter pass
# ---------------------------------------------------------------------------

def filter_items(data: dict, stale_cutoff_hours: float = 48.0) -> dict:
    """Apply all filter stages to a raw-discovery dict.

    Returns a new dict suitable for writing to filtered-discovery.json.
    The input dict is not modified.
    """
    run_id: str = data["run_id"]
    run_ts = _parse_run_id(run_id)
    items: list[dict] = data.get("items", [])

    after_stale, n_stale = _reject_stale(items, run_ts, stale_cutoff_hours)
    after_url, n_url = _dedup_canonical_url(after_stale)
    after_title, n_title = _dedup_title(after_url)

    return {
        "version": 1,
        "source_run_id": run_id,
        "filtered_at": _now_iso(),
        "input_count": len(items),
        "output_count": len(after_title),
        "stale_cutoff_hours": stale_cutoff_hours,
        "dropped": {
            "stale": n_stale,
            "url_dedup": n_url,
            "title_dedup": n_title,
        },
        "items": after_title,
    }


# ---------------------------------------------------------------------------
# Timestamp helper
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="AINWA Discovery Filter v1")
    parser.add_argument(
        "--input",
        default=str(DATA_DIR / "raw-discovery.json"),
        help="Path to raw-discovery.json (default: data/raw-discovery.json)",
    )
    parser.add_argument(
        "--output",
        default=str(DATA_DIR / "filtered-discovery.json"),
        help="Output path (default: data/filtered-discovery.json)",
    )
    parser.add_argument(
        "--stale-hours",
        type=float,
        default=48.0,
        metavar="N",
        help="Reject items published more than N hours before the run_id timestamp (default: 48)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    cutoff = args.stale_hours

    print(f"[AINWA-filter] input:  {input_path}")
    print(f"[AINWA-filter] output: {output_path}")
    print(f"[AINWA-filter] stale cutoff: {cutoff}h")

    if not input_path.exists():
        print(f"[ERROR] Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    with input_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    result = filter_items(data, stale_cutoff_hours=cutoff)

    d = result["dropped"]
    print(
        f"[AINWA-filter] {result['input_count']} in → "
        f"{d['stale']} stale, {d['url_dedup']} url-dup, {d['title_dedup']} title-dup → "
        f"{result['output_count']} out"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(".json.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, output_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    print(f"[AINWA-filter] wrote {output_path}")


if __name__ == "__main__":
    main()
