#!/usr/bin/env python3
"""Unit tests for filter.py.

Coverage:
  - Stale date rejection (>cutoff hours)
  - Items within cutoff are kept
  - Items with unparseable dates are kept (safe default)
  - Items with absent date field are kept
  - GMT literal date format parsed correctly
  - All four date formats accepted
  - URL canonicalization: tracking params stripped
  - URL canonicalization: trailing slash removed
  - URL canonicalization: scheme/host lowercased
  - URL canonicalization: fragment stripped
  - URL canonicalization: clean URL unchanged
  - Canonical URL deduplication: exact canonical match drops second item
  - Canonical URL deduplication: tracking-param variants treated as same URL
  - Canonical URL deduplication: distinct URLs both kept
  - Title fingerprint deduplication: same fingerprint, prefer Original Reporting
  - Title fingerprint deduplication: same role → prefer earliest published
  - Title fingerprint deduplication: empty titles not collapsed
  - filter_items integration: counts and structure correct

Run:
    python3 test_filter.py
    python3 -m pytest test_filter.py -v   (if pytest is available)
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import filter as f  # noqa: E402 — local module named 'filter'


# ---------------------------------------------------------------------------
# Helpers for building minimal raw item dicts
# ---------------------------------------------------------------------------

def _item(
    item_id: str = "raw-SRC-001-aabbccdd",
    source_id: str = "SRC-001",
    source_role: str = "Original Reporting",
    item_url: str = "https://example.com/article",
    item_title: str = "Example Article Title Here For Testing",
    item_published: str | None = "Wed, 13 Aug 2026 10:00:00 +0000",
) -> dict:
    return {
        "item_id": item_id,
        "source_id": source_id,
        "source_name": "Test Source",
        "source_domain": "example.com",
        "source_priority": "high",
        "source_role": source_role,
        "source_citation_allowed": "yes",
        "source_reliability": "high",
        "fetch_method": "rss",
        "feed_url": "https://example.com/feed",
        "fetched_at": "2026-08-13T12:00:00Z",
        "item_url": item_url,
        "item_title": item_title,
        "item_published": item_published,
        "feed_description": "A test description.",
        "feed_description_format": "text",
    }


def _run_ts(iso: str = "2026-08-13T12:00:00Z") -> datetime:
    dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
    return dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 1. _parse_pub_date — date format coverage
# ---------------------------------------------------------------------------

class TestParsePubDate(unittest.TestCase):
    def test_rfc2822_numeric_offset(self):
        dt = f._parse_pub_date("Wed, 13 Aug 2026 10:00:00 +0000")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 8)
        self.assertEqual(dt.day, 13)

    def test_rfc2822_gmt_literal(self):
        dt = f._parse_pub_date("Thu, 13 Aug 2026 16:47:00 GMT")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_iso8601_utc_z(self):
        dt = f._parse_pub_date("2026-08-13T10:00:00Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_iso8601_with_offset(self):
        dt = f._parse_pub_date("2026-08-13T10:00:00+00:00")
        self.assertIsNotNone(dt)

    def test_negative_offset(self):
        dt = f._parse_pub_date("Thu, 13 Aug 2026 15:25:01 -0400")
        self.assertIsNotNone(dt)

    def test_unparseable_returns_none(self):
        self.assertIsNone(f._parse_pub_date("not a date"))

    def test_empty_returns_none(self):
        self.assertIsNone(f._parse_pub_date(""))

    def test_none_returns_none(self):
        self.assertIsNone(f._parse_pub_date(None))

    def test_result_is_timezone_aware(self):
        dt = f._parse_pub_date("Wed, 13 Aug 2026 10:00:00 +0000")
        self.assertIsNotNone(dt.tzinfo)


# ---------------------------------------------------------------------------
# 2. canonical_url — normalization
# ---------------------------------------------------------------------------

class TestCanonicalUrl(unittest.TestCase):
    def test_strips_utm_source(self):
        url = "https://example.com/article?utm_source=rss&utm_medium=feed"
        self.assertEqual(f.canonical_url(url), "https://example.com/article")

    def test_strips_all_tracking_params(self):
        url = "https://example.com/p?fbclid=abc&gclid=xyz&ref=twitter"
        self.assertEqual(f.canonical_url(url), "https://example.com/p")

    def test_keeps_non_tracking_params(self):
        url = "https://example.com/search?q=ai&page=2"
        result = f.canonical_url(url)
        self.assertIn("q=ai", result)
        self.assertIn("page=2", result)

    def test_strips_trailing_slash_from_path(self):
        url = "https://example.com/article/"
        self.assertEqual(f.canonical_url(url), "https://example.com/article")

    def test_root_path_slash_kept(self):
        # urlunparse always produces a "/" for a hostname with no path —
        # we only strip trailing slash, so "/" at root stays "/"
        url = "https://example.com/"
        result = f.canonical_url(url)
        self.assertEqual(result, "https://example.com")

    def test_lowercases_scheme(self):
        url = "HTTPS://example.com/article"
        self.assertTrue(f.canonical_url(url).startswith("https://"))

    def test_lowercases_host(self):
        url = "https://Example.COM/article"
        self.assertIn("example.com", f.canonical_url(url))

    def test_strips_fragment(self):
        url = "https://www.techmeme.com/260813/p43#a260813p43"
        result = f.canonical_url(url)
        self.assertNotIn("#", result)
        self.assertEqual(result, "https://www.techmeme.com/260813/p43")

    def test_clean_url_unchanged(self):
        url = "https://techcrunch.com/2026/08/13/some-article"
        self.assertEqual(f.canonical_url(url), url)

    def test_mixed_tracking_and_real_params(self):
        url = "https://example.com/p?id=42&utm_source=rss"
        result = f.canonical_url(url)
        self.assertIn("id=42", result)
        self.assertNotIn("utm_source", result)


# ---------------------------------------------------------------------------
# 3. _reject_stale — stale filtering
# ---------------------------------------------------------------------------

class TestRejectStale(unittest.TestCase):
    RUN_TS = _run_ts("2026-08-13T12:00:00Z")  # noon Aug 13

    def _run(self, items, cutoff=48.0):
        return f._reject_stale(items, self.RUN_TS, cutoff)

    def test_fresh_item_kept(self):
        # 2h old at run time
        item = _item(item_published="Wed, 13 Aug 2026 10:00:00 +0000")
        kept, dropped = self._run([item])
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, 0)

    def test_stale_item_dropped(self):
        # 72h old (3 days)
        item = _item(item_published="Sun, 10 Aug 2026 12:00:00 +0000")
        kept, dropped = self._run([item])
        self.assertEqual(len(kept), 0)
        self.assertEqual(dropped, 1)

    def test_exactly_at_cutoff_kept(self):
        # Exactly 48h old — boundary is exclusive (> not >=)
        item = _item(item_published="Mon, 11 Aug 2026 12:00:00 +0000")
        kept, dropped = self._run([item], cutoff=48.0)
        self.assertEqual(len(kept), 1)

    def test_one_second_over_cutoff_dropped(self):
        # 48h + 1s
        item = _item(item_published="Mon, 11 Aug 2026 11:59:59 +0000")
        kept, dropped = self._run([item], cutoff=48.0)
        self.assertEqual(dropped, 1)

    def test_unparseable_date_kept(self):
        item = _item(item_published="not a valid date at all")
        kept, dropped = self._run([item])
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, 0)

    def test_none_date_kept(self):
        item = _item(item_published=None)
        kept, dropped = self._run([item])
        self.assertEqual(len(kept), 1)

    def test_kept_item_has_canonical_url(self):
        item = _item(
            item_url="https://example.com/story?utm_source=rss",
            item_published="Wed, 13 Aug 2026 10:00:00 +0000",
        )
        kept, _ = self._run([item])
        self.assertIn("canonical_url", kept[0])
        self.assertNotIn("utm_source", kept[0]["canonical_url"])

    def test_kept_item_has_age_hours(self):
        item = _item(item_published="Wed, 13 Aug 2026 10:00:00 +0000")
        kept, _ = self._run([item])
        self.assertAlmostEqual(kept[0]["age_hours"], 2.0, places=1)

    def test_unparseable_date_age_hours_is_none(self):
        item = _item(item_published="not a date")
        kept, _ = self._run([item])
        self.assertIsNone(kept[0]["age_hours"])

    def test_original_fields_preserved(self):
        item = _item(item_published="Wed, 13 Aug 2026 10:00:00 +0000")
        kept, _ = self._run([item])
        self.assertEqual(kept[0]["item_url"], item["item_url"])
        self.assertEqual(kept[0]["item_published"], item["item_published"])

    def test_mixed_batch(self):
        fresh = _item(item_id="raw-SRC-001-aaa", item_published="Wed, 13 Aug 2026 10:00:00 +0000")
        stale = _item(item_id="raw-SRC-001-bbb", item_published="Sat, 08 Aug 2026 12:00:00 +0000")
        kept, dropped = self._run([fresh, stale])
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, 1)
        self.assertEqual(kept[0]["item_id"], "raw-SRC-001-aaa")


# ---------------------------------------------------------------------------
# 4. _dedup_canonical_url
# ---------------------------------------------------------------------------

class TestDedupCanonicalUrl(unittest.TestCase):
    def _annotated(self, item_id, canon):
        item = _item(item_id=item_id)
        item["canonical_url"] = canon
        item["age_hours"] = 1.0
        return item

    def test_distinct_urls_both_kept(self):
        a = self._annotated("raw-SRC-001-aaa", "https://example.com/a")
        b = self._annotated("raw-SRC-001-bbb", "https://example.com/b")
        kept, dropped = f._dedup_canonical_url([a, b])
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, 0)

    def test_same_canonical_drops_second(self):
        a = self._annotated("raw-SRC-001-aaa", "https://example.com/story")
        b = self._annotated("raw-SRC-001-bbb", "https://example.com/story")
        kept, dropped = f._dedup_canonical_url([a, b])
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, 1)
        self.assertEqual(kept[0]["item_id"], "raw-SRC-001-aaa")

    def test_tracking_param_variants_deduplicated(self):
        # Two items that differed only in utm params → same canonical_url
        a = self._annotated("raw-SRC-001-aaa", "https://example.com/p")
        b = self._annotated("raw-SRC-001-bbb", "https://example.com/p")
        kept, dropped = f._dedup_canonical_url([a, b])
        self.assertEqual(dropped, 1)

    def test_empty_input(self):
        kept, dropped = f._dedup_canonical_url([])
        self.assertEqual(kept, [])
        self.assertEqual(dropped, 0)


# ---------------------------------------------------------------------------
# 5. _dedup_title
# ---------------------------------------------------------------------------

class TestDedupTitle(unittest.TestCase):
    def _item_with_title(
        self,
        item_id: str,
        title: str,
        role: str = "Original Reporting",
        published: str = "Wed, 13 Aug 2026 10:00:00 +0000",
    ) -> dict:
        item = _item(item_id=item_id, source_role=role, item_title=title, item_published=published)
        item["canonical_url"] = f"https://example.com/{item_id}"
        item["age_hours"] = 2.0
        return item

    def test_distinct_titles_both_kept(self):
        a = self._item_with_title("raw-SRC-001-aaa", "Apple announces new iPhone model")
        b = self._item_with_title("raw-SRC-001-bbb", "Google launches new search feature today")
        kept, dropped = f._dedup_title([a, b])
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, 0)

    def test_same_fingerprint_drops_duplicate(self):
        title = "OpenAI releases GPT-5 model with improved reasoning"
        a = self._item_with_title("raw-SRC-001-aaa", title)
        b = self._item_with_title("raw-SRC-001-bbb", title)
        kept, dropped = f._dedup_title([a, b])
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, 1)

    def test_prefers_original_reporting_over_discovery_only(self):
        title = "Major AI breakthrough announced by researchers"
        discovery = self._item_with_title("raw-SRC-001-disc", title, role="Discovery Only")
        original = self._item_with_title("raw-SRC-001-orig", title, role="Original Reporting")
        # Put discovery first in input list to ensure role-based selection
        kept, dropped = f._dedup_title([discovery, original])
        self.assertEqual(kept[0]["item_id"], "raw-SRC-001-orig")
        self.assertEqual(dropped, 1)

    def test_same_role_prefers_earliest_published(self):
        title = "Big company acquires smaller startup for billions"
        early = self._item_with_title(
            "raw-SRC-001-early", title, published="Wed, 13 Aug 2026 06:00:00 +0000"
        )
        late = self._item_with_title(
            "raw-SRC-001-late", title, published="Wed, 13 Aug 2026 10:00:00 +0000"
        )
        # Both same role; 'late' comes first in list — early should win
        kept, dropped = f._dedup_title([late, early])
        self.assertEqual(kept[0]["item_id"], "raw-SRC-001-early")

    def test_punctuation_ignored_in_fingerprint(self):
        # Same words, different punctuation → same fingerprint → deduplicated
        a = self._item_with_title("raw-SRC-001-aaa", "Apple: new iPhone! announced today")
        b = self._item_with_title("raw-SRC-001-bbb", "Apple  new iPhone  announced today")
        kept, dropped = f._dedup_title([a, b])
        self.assertEqual(dropped, 1)

    def test_empty_title_items_not_collapsed(self):
        # Two items with empty/None titles each get fingerprint "" — they
        # collide. Only one is kept.  This is acceptable: empty-title items
        # are low quality and deduplication is conservative.
        a = self._item_with_title("raw-SRC-001-aaa", "")
        b = self._item_with_title("raw-SRC-001-bbb", "")
        kept, dropped = f._dedup_title([a, b])
        self.assertEqual(len(kept), 1)

    def test_preserves_original_order_of_winners(self):
        a = self._item_with_title("raw-SRC-001-aaa", "Alpha story about technology")
        b = self._item_with_title("raw-SRC-001-bbb", "Beta story about science today")
        c = self._item_with_title("raw-SRC-001-ccc", "Gamma story about politics news")
        kept, _ = f._dedup_title([a, b, c])
        self.assertEqual([i["item_id"] for i in kept],
                         ["raw-SRC-001-aaa", "raw-SRC-001-bbb", "raw-SRC-001-ccc"])

    def test_empty_input(self):
        kept, dropped = f._dedup_title([])
        self.assertEqual(kept, [])
        self.assertEqual(dropped, 0)


# ---------------------------------------------------------------------------
# 6. filter_items — integration
# ---------------------------------------------------------------------------

class TestFilterItems(unittest.TestCase):
    RUN_ID = "2026-08-13T12:00:00Z"

    def _raw(self, items: list[dict]) -> dict:
        return {
            "version": 1,
            "run_id": self.RUN_ID,
            "priority_filter": "high",
            "item_count": len(items),
            "error_count": 0,
            "skipped_count": 0,
            "items": items,
            "errors": [],
            "skipped": [],
        }

    def test_output_structure(self):
        raw = self._raw([_item()])
        result = f.filter_items(raw)
        for key in ("version", "source_run_id", "filtered_at", "input_count",
                    "output_count", "stale_cutoff_hours", "dropped", "items"):
            self.assertIn(key, result)

    def test_dropped_keys(self):
        raw = self._raw([_item()])
        result = f.filter_items(raw)
        self.assertIn("stale", result["dropped"])
        self.assertIn("url_dedup", result["dropped"])
        self.assertIn("title_dedup", result["dropped"])

    def test_source_run_id_matches_input(self):
        raw = self._raw([_item()])
        result = f.filter_items(raw)
        self.assertEqual(result["source_run_id"], self.RUN_ID)

    def test_stale_cutoff_hours_recorded(self):
        raw = self._raw([_item()])
        result = f.filter_items(raw, stale_cutoff_hours=24.0)
        self.assertEqual(result["stale_cutoff_hours"], 24.0)

    def test_all_fresh_no_drops(self):
        items = [
            _item(item_id="raw-SRC-001-aaa", item_title="First distinct headline alpha beta"),
            _item(item_id="raw-SRC-001-bbb", item_url="https://example.com/b",
                  item_title="Second distinct headline gamma delta"),
        ]
        result = f.filter_items(self._raw(items))
        self.assertEqual(result["input_count"], 2)
        self.assertEqual(result["output_count"], 2)
        self.assertEqual(result["dropped"]["stale"], 0)
        self.assertEqual(result["dropped"]["url_dedup"], 0)
        self.assertEqual(result["dropped"]["title_dedup"], 0)

    def test_stale_dropped_counted(self):
        fresh = _item(item_id="raw-SRC-001-aaa", item_published="Wed, 13 Aug 2026 10:00:00 +0000")
        stale = _item(item_id="raw-SRC-001-bbb", item_published="Mon, 01 Jan 2026 00:00:00 +0000")
        result = f.filter_items(self._raw([fresh, stale]))
        self.assertEqual(result["dropped"]["stale"], 1)
        self.assertEqual(result["output_count"], 1)

    def test_output_items_have_canonical_url(self):
        item = _item(item_url="https://example.com/p?utm_source=rss")
        result = f.filter_items(self._raw([item]))
        self.assertIn("canonical_url", result["items"][0])
        self.assertNotIn("utm_source", result["items"][0]["canonical_url"])

    def test_output_items_have_age_hours(self):
        result = f.filter_items(self._raw([_item()]))
        self.assertIn("age_hours", result["items"][0])

    def test_input_item_url_unchanged(self):
        original_url = "https://example.com/p?utm_source=rss"
        item = _item(item_url=original_url)
        result = f.filter_items(self._raw([item]))
        self.assertEqual(result["items"][0]["item_url"], original_url)

    def test_default_cutoff_is_48(self):
        import inspect
        sig = inspect.signature(f.filter_items)
        self.assertEqual(sig.parameters["stale_cutoff_hours"].default, 48.0)

    def test_empty_input(self):
        result = f.filter_items(self._raw([]))
        self.assertEqual(result["input_count"], 0)
        self.assertEqual(result["output_count"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
