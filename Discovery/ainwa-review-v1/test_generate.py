#!/usr/bin/env python3
"""Critical tests for generate.py (AINWA-005/006).

Coverage:
  - build_candidates: approved URL in source is http/https only
  - build_candidates: javascript: source URL is dropped
  - build_candidates: public_summary is a list of 3 items
  - build_candidates: editorial_notes is a non-empty string
  - build_candidates: editorial_notes is absent from advisory field
  - build_candidates: already-approved URL is excluded by prompt builder
  - build_candidates: unknown item_id from Claude is skipped (no crash)
  - build_candidates: candidate count capped at MAX_CANDIDATES
  - build_candidates: advisory skipped status when keys absent
  - advisory: grok failure is non-blocking (exception → skipped)
  - advisory: gemini failure is non-blocking (exception → skipped)
  - main: approved-queue.json is never written
  - main: exits 0 when advisory keys absent (no API keys for advisors)
  - main: exits 1 when ANTHROPIC_API_KEY absent and not dry-run
  - main: dry-run returns 0 and writes no files
  - is_http_url: scheme validation

Run:
    python3 test_generate.py
    python3 -m pytest test_generate.py -v
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent))
import generate


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _item(item_id="raw-SRC-001-aabbccdd", source_name="Example News",
          source_role="Original Reporting", source_citation_allowed="yes",
          source_reliability="high", age_hours=2.0,
          item_title="Example AI story title",
          canonical_url="https://example.com/story",
          fetched_at="2026-08-13T19:53:22Z") -> dict:
    return {
        "item_id": item_id,
        "source_name": source_name,
        "source_role": source_role,
        "source_citation_allowed": source_citation_allowed,
        "source_reliability": source_reliability,
        "age_hours": age_hours,
        "item_title": item_title,
        "canonical_url": canonical_url,
        "fetched_at": fetched_at,
    }


def _selection(item_id=1,
               headline="EXAMPLE AI HEADLINE",
               public_summary=None,
               editorial_notes="Source quality: high. No dedup concerns.",
               category="Models",
               priority="High",
               top_story=False,
               developing=False) -> dict:
    if public_summary is None:
        public_summary = [
            "OpenAI announced a new model.",
            "The release marks a significant capability jump.",
            "Enterprise customers will gain access first.",
        ]
    return {
        "item_id": item_id,
        "headline": headline,
        "public_summary": public_summary,
        "editorial_notes": editorial_notes,
        "category": category,
        "priority": priority,
        "top_story": top_story,
        "developing": developing,
    }


def _make_item_lookup(*items):
    return {i + 1: items[i] for i in range(len(items))}


def _no_advisory():
    return {}, {}, False, False



# ---------------------------------------------------------------------------
# build_candidates schema tests
# ---------------------------------------------------------------------------

class TestBuildCandidatesSchema(unittest.TestCase):

    def test_public_summary_is_list_of_3(self):
        item = _item()
        sel = _selection()
        candidates = generate.build_candidates(
            [sel], _make_item_lookup(item), {}, {}, False, False
        )
        self.assertEqual(len(candidates), 1)
        ps = candidates[0]["proposal"]["public_summary"]
        self.assertIsInstance(ps, list)
        self.assertEqual(len(ps), 3)

    def test_editorial_notes_is_string(self):
        item = _item()
        sel = _selection()
        candidates = generate.build_candidates(
            [sel], _make_item_lookup(item), {}, {}, False, False
        )
        en = candidates[0]["proposal"]["editorial_notes"]
        self.assertIsInstance(en, str)
        self.assertTrue(len(en) > 0)

    def test_editorial_notes_not_in_public_summary(self):
        # editorial_notes must not appear anywhere inside public_summary bullets
        item = _item()
        sel = _selection(editorial_notes="INTERNAL: Source quality marginal, verify independently.")
        candidates = generate.build_candidates(
            [sel], _make_item_lookup(item), {}, {}, False, False
        )
        ps = candidates[0]["proposal"]["public_summary"]
        for bullet in ps:
            self.assertNotIn("INTERNAL", bullet)
            self.assertNotIn("Source quality marginal", bullet)

    def test_editorial_notes_absent_from_advisory(self):
        # advisory field must not contain editorial_notes content
        item = _item()
        sel = _selection(editorial_notes="Reviewer-only: dedup with SRC-005.")
        candidates = generate.build_candidates(
            [sel], _make_item_lookup(item), {}, {}, False, False
        )
        advisory_str = json.dumps(candidates[0]["advisory"])
        self.assertNotIn("Reviewer-only", advisory_str)

    def test_source_url_http_preserved(self):
        item = _item(canonical_url="https://example.com/story")
        candidates = generate.build_candidates(
            [_selection()], _make_item_lookup(item), {}, {}, False, False
        )
        self.assertEqual(candidates[0]["source"]["url"], "https://example.com/story")

    def test_source_url_javascript_dropped(self):
        item = _item(canonical_url="javascript:alert(1)")
        candidates = generate.build_candidates(
            [_selection()], _make_item_lookup(item), {}, {}, False, False
        )
        self.assertEqual(candidates[0]["source"]["url"], "")

    def test_source_url_data_uri_dropped(self):
        item = _item(canonical_url="data:text/html,<script>x</script>")
        candidates = generate.build_candidates(
            [_selection()], _make_item_lookup(item), {}, {}, False, False
        )
        self.assertEqual(candidates[0]["source"]["url"], "")

    def test_unknown_item_id_skipped(self):
        sel = _selection(item_id="raw-SRC-999-unknown")
        candidates = generate.build_candidates(
            [sel], {}, {}, {}, False, False  # empty lookup
        )
        self.assertEqual(candidates, [])

    def test_candidate_count_capped_at_max(self):
        items = [_item(item_id=f"raw-SRC-001-{i:08x}", canonical_url=f"https://example.com/{i}")
                 for i in range(20)]
        selections = [_selection(item_id=i+1) for i in range(len(items))]
        candidates = generate.build_candidates(
            selections, _make_item_lookup(*items), {}, {}, False, False
        )
        self.assertLessEqual(len(candidates), generate.MAX_CANDIDATES)

    def test_rank_assigned_sequentially(self):
        items = [_item(item_id=f"raw-SRC-001-{i:08x}", canonical_url=f"https://example.com/{i}")
                 for i in range(3)]
        selections = [_selection(item_id=i+1) for i in range(len(items))]
        candidates = generate.build_candidates(
            selections, _make_item_lookup(*items), {}, {}, False, False
        )
        ranks = [c["rank"] for c in candidates]
        self.assertEqual(ranks, [1, 2, 3])

    def test_status_is_review(self):
        item = _item()
        candidates = generate.build_candidates(
            [_selection()], _make_item_lookup(item), {}, {}, False, False
        )
        self.assertEqual(candidates[0]["status"], "review")

    def test_malformed_public_summary_coerced_to_list(self):
        # If Claude returns public_summary as a non-list, it becomes []
        item = _item()
        sel = _selection(public_summary="not a list")
        candidates = generate.build_candidates(
            [sel], _make_item_lookup(item), {}, {}, False, False
        )
        self.assertIsInstance(candidates[0]["proposal"]["public_summary"], list)


# ---------------------------------------------------------------------------
# Advisory tests
# ---------------------------------------------------------------------------

class TestAdvisory(unittest.TestCase):

    def test_advisory_skipped_when_no_keys(self):
        item = _item()
        candidates = generate.build_candidates(
            [_selection()], _make_item_lookup(item), {}, {}, False, False
        )
        adv = candidates[0]["advisory"]
        self.assertEqual(adv["grok"]["status"], "skipped")
        self.assertEqual(adv["gemini"]["status"], "skipped")

    def test_grok_result_stored_when_available(self):
        item = _item()
        grok_results = {"1": {"status": "ok", "assessment": "agree", "notes": "Looks good."}}
        candidates = generate.build_candidates(
            [_selection()], _make_item_lookup(item), grok_results, {}, True, False
        )
        self.assertEqual(candidates[0]["advisory"]["grok"]["status"], "ok")
        self.assertEqual(candidates[0]["advisory"]["grok"]["assessment"], "agree")

    def test_gemini_result_stored_when_available(self):
        item = _item()
        gemini_results = {"1": {"status": "ok", "assessment": "flag", "notes": "Check source."}}
        candidates = generate.build_candidates(
            [_selection()], _make_item_lookup(item), {}, gemini_results, False, True
        )
        self.assertEqual(candidates[0]["advisory"]["gemini"]["status"], "ok")
        self.assertEqual(candidates[0]["advisory"]["gemini"]["assessment"], "flag")

    def test_grok_exception_is_non_blocking(self):
        """Grok failure must not prevent candidate file from being written."""
        item = _item()
        CLAUDE_RESPONSE = json.dumps([_selection()])

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "filtered-discovery.json").write_text(json.dumps({"items": [item]}))
            (d / "approved-queue.json").write_text(json.dumps({"stories": []}))

            env = {"ANTHROPIC_API_KEY": "test-key", "GROK_API_KEY": "grok-key"}

            with patch.dict(os.environ, env, clear=False), \
                 patch("generate.call_claude", return_value=CLAUDE_RESPONSE), \
                 patch("generate.call_grok", side_effect=Exception("grok down")):
                rc = generate.main(["--data-dir", tmpdir])

            # Read inside the with-block before tmpdir is cleaned up
            result = json.loads((d / "candidate-queue.json").read_text())

        self.assertEqual(rc, 0)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["advisory"]["grok"]["status"], "skipped")

    def test_gemini_exception_is_non_blocking(self):
        item = _item()
        CLAUDE_RESPONSE = json.dumps([_selection()])

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "filtered-discovery.json").write_text(json.dumps({"items": [item]}))
            (d / "approved-queue.json").write_text(json.dumps({"stories": []}))

            env = {"ANTHROPIC_API_KEY": "test-key", "GEMINI_API_KEY": "gemini-key"}

            with patch.dict(os.environ, env, clear=False), \
                 patch("generate.call_claude", return_value=CLAUDE_RESPONSE), \
                 patch("generate.call_gemini", side_effect=Exception("gemini down")):
                rc = generate.main(["--data-dir", tmpdir])

            # Read inside the with-block before tmpdir is cleaned up
            result = json.loads((d / "candidate-queue.json").read_text())

        self.assertEqual(rc, 0)
        self.assertEqual(result["candidates"][0]["advisory"]["gemini"]["status"], "skipped")


# ---------------------------------------------------------------------------
# main() integration tests
# ---------------------------------------------------------------------------

class TestMain(unittest.TestCase):

    def _run_main(self, items, approved_stories, claude_response, extra_args=None,
                  extra_env=None) -> tuple[int, Path]:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "filtered-discovery.json").write_text(json.dumps({"items": items}))
            (d / "approved-queue.json").write_text(json.dumps({"stories": approved_stories}))

            env = {"ANTHROPIC_API_KEY": "test-key"}
            if extra_env:
                env.update(extra_env)

            argv = ["--data-dir", tmpdir, "--skip-advisory"] + (extra_args or [])

            with patch.dict(os.environ, env, clear=False), \
                 patch("generate.call_claude", return_value=claude_response):
                rc = generate.main(argv)

            candidates_path = d / "candidate-queue.json"
            approved_path = d / "approved-queue.json"

            # Return copies so they survive tmpdir cleanup
            result_candidates = candidates_path.read_text() if candidates_path.exists() else None
            result_approved = approved_path.read_text()

        return rc, result_candidates, result_approved

    def test_approved_queue_never_written(self):
        """approved-queue.json must be identical before and after generate.py runs."""
        item = _item()
        original_approved = json.dumps({"stories": []})
        CLAUDE_RESPONSE = json.dumps([_selection()])

        rc, _, approved_after = self._run_main([item], [], CLAUDE_RESPONSE)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(approved_after), {"stories": []})

    def test_approved_url_not_in_candidates(self):
        """A story already in approved-queue must not appear as a new candidate."""
        approved_url = "https://example.com/story"
        item = _item(canonical_url=approved_url)
        approved_stories = [{"source": {"url": approved_url}, "approved": {"locked": True}}]
        # Claude selection prompt would exclude item, so return empty selections
        CLAUDE_RESPONSE = json.dumps([])

        rc, candidates_text, _ = self._run_main([item], approved_stories, CLAUDE_RESPONSE)
        self.assertEqual(rc, 0)
        result = json.loads(candidates_text)
        candidate_urls = [c["source"]["url"] for c in result["candidates"]]
        self.assertNotIn(approved_url, candidate_urls)

    def test_exits_1_without_anthropic_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "filtered-discovery.json").write_text(json.dumps({"items": []}))
            (d / "approved-queue.json").write_text(json.dumps({"stories": []}))
            env_without_key = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
            with patch.dict(os.environ, env_without_key, clear=True):
                rc = generate.main(["--data-dir", tmpdir])
        self.assertEqual(rc, 1)

    def test_dry_run_writes_no_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "filtered-discovery.json").write_text(json.dumps({"items": [_item()]}))
            (d / "approved-queue.json").write_text(json.dumps({"stories": []}))
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=False):
                rc = generate.main(["--data-dir", tmpdir, "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertFalse((d / "candidate-queue.json").exists())

    def test_candidates_written_with_correct_schema(self):
        item = _item()
        CLAUDE_RESPONSE = json.dumps([_selection()])
        rc, candidates_text, _ = self._run_main([item], [], CLAUDE_RESPONSE)
        self.assertEqual(rc, 0)
        result = json.loads(candidates_text)
        self.assertIn("candidates", result)
        self.assertEqual(len(result["candidates"]), 1)
        c = result["candidates"][0]
        self.assertEqual(c["status"], "review")
        self.assertIsInstance(c["proposal"]["public_summary"], list)
        self.assertIsInstance(c["proposal"]["editorial_notes"], str)
        self.assertIn("advisory", c)

    def test_skip_advisory_flag_skips_both_advisors(self):
        item = _item()
        CLAUDE_RESPONSE = json.dumps([_selection()])
        # Even with keys set, --skip-advisory must skip both
        rc, candidates_text, _ = self._run_main(
            [item], [], CLAUDE_RESPONSE,
            extra_env={"GROK_API_KEY": "grok-key", "GEMINI_API_KEY": "gemini-key"}
        )
        self.assertEqual(rc, 0)
        result = json.loads(candidates_text)
        adv = result["candidates"][0]["advisory"]
        self.assertEqual(adv["grok"]["status"], "skipped")
        self.assertEqual(adv["gemini"]["status"], "skipped")

    def test_claude_markdown_fence_stripped(self):
        """Claude sometimes wraps JSON in ```json ... ``` — must be handled."""
        item = _item()
        wrapped = "```json\n" + json.dumps([_selection()]) + "\n```"
        rc, candidates_text, _ = self._run_main([item], [], wrapped)
        self.assertEqual(rc, 0)
        result = json.loads(candidates_text)
        self.assertEqual(len(result["candidates"]), 1)


# ---------------------------------------------------------------------------
# is_http_url unit tests
# ---------------------------------------------------------------------------

class TestIsHttpUrl(unittest.TestCase):

    def test_https_accepted(self):
        self.assertTrue(generate.is_http_url("https://example.com/path"))

    def test_http_accepted(self):
        self.assertTrue(generate.is_http_url("http://example.com/"))

    def test_javascript_rejected(self):
        self.assertFalse(generate.is_http_url("javascript:alert(1)"))

    def test_data_uri_rejected(self):
        self.assertFalse(generate.is_http_url("data:text/html,x"))

    def test_empty_rejected(self):
        self.assertFalse(generate.is_http_url(""))

    def test_none_rejected(self):
        self.assertFalse(generate.is_http_url(None))

    def test_no_netloc_rejected(self):
        self.assertFalse(generate.is_http_url("https://"))


# ---------------------------------------------------------------------------
# New behavior: brief_headline, anchor cap, carryover merge
# ---------------------------------------------------------------------------

class TestBriefHeadline(unittest.TestCase):

    def test_brief_headline_set_from_selection(self):
        item = _item()
        sel = dict(_selection())
        sel["brief_headline"] = "BRIEF HEADLINE FROM CLAUDE"
        sel.pop("headline", None)
        candidates = generate.build_candidates(
            [sel], _make_item_lookup(item), {}, {}, False, False
        )
        self.assertEqual(candidates[0]["proposal"]["brief_headline"], "BRIEF HEADLINE FROM CLAUDE")

    def test_headline_alias_matches_brief_headline(self):
        item = _item()
        sel = dict(_selection())
        sel["brief_headline"] = "BRIEF HEADLINE"
        sel.pop("headline", None)
        candidates = generate.build_candidates(
            [sel], _make_item_lookup(item), {}, {}, False, False
        )
        self.assertEqual(
            candidates[0]["proposal"]["headline"],
            candidates[0]["proposal"]["brief_headline"],
        )

    def test_brief_headline_falls_back_to_headline_key(self):
        item = _item()
        sel = dict(_selection(headline="LEGACY HEADLINE"))
        # No brief_headline key — should fall back to headline
        candidates = generate.build_candidates(
            [sel], _make_item_lookup(item), {}, {}, False, False
        )
        self.assertEqual(candidates[0]["proposal"]["brief_headline"], "LEGACY HEADLINE")

    def test_source_resolution_resolved_for_original_reporting(self):
        item = _item(source_role="Original Reporting")
        candidates = generate.build_candidates(
            [_selection()], _make_item_lookup(item), {}, {}, False, False
        )
        self.assertEqual(candidates[0]["source_resolution"], "resolved")

    def test_source_resolution_unresolved_for_discovery_only(self):
        item = _item(source_role="Discovery Only")
        candidates = generate.build_candidates(
            [_selection()], _make_item_lookup(item), {}, {}, False, False
        )
        self.assertEqual(candidates[0]["source_resolution"], "unresolved")


class TestAnchorCapWalk(unittest.TestCase):

    def _anchor_item(self, src_name, item_id):
        return {
            "item_id": item_id,
            "source_name": src_name,
            "source_role": "Original Reporting",
            "source_citation_allowed": "yes",
            "source_reliability": "high",
            "age_hours": 1.0,
            "item_title": f"{src_name} story",
            "canonical_url": f"https://example.com/{item_id}",
            "fetched_at": "2026-08-13T10:00:00Z",
        }

    def test_first_anchor_accepted_duplicate_skipped(self):
        tc1 = self._anchor_item("TechCrunch", "raw-SRC-018-aaaa0001")
        tc2 = self._anchor_item("TechCrunch", "raw-SRC-018-aaaa0002")
        lookup = {1: tc1, 2: tc2}
        ranked = [{"item_id": 1}, {"item_id": 2}]
        result = generate._apply_anchor_cap_walk(ranked, lookup)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["item_id"], 1)

    def test_different_anchor_sources_both_accepted(self):
        tc = self._anchor_item("TechCrunch", "raw-SRC-018-tc000001")
        bc = self._anchor_item("BleepingComputer", "raw-SRC-024-bc000001")
        lookup = {1: tc, 2: bc}
        ranked = [{"item_id": 1}, {"item_id": 2}]
        result = generate._apply_anchor_cap_walk(ranked, lookup)
        self.assertEqual(len(result), 2)

    def test_non_anchor_passes_through(self):
        non_anchor1 = self._anchor_item("CNBC", "raw-SRC-017-cn000001")
        non_anchor2 = self._anchor_item("CNBC", "raw-SRC-017-cn000002")
        lookup = {1: non_anchor1, 2: non_anchor2}
        ranked = [{"item_id": 1}, {"item_id": 2}]
        result = generate._apply_anchor_cap_walk(ranked, lookup)
        self.assertEqual(len(result), 2)

    def test_walk_stops_at_max_candidates(self):
        items = [self._anchor_item("CNBC", f"raw-SRC-017-{i:08x}") for i in range(20)]
        lookup = {i + 1: items[i] for i in range(len(items))}
        ranked = [{"item_id": i + 1} for i in range(20)]
        result = generate._apply_anchor_cap_walk(ranked, lookup)
        self.assertEqual(len(result), generate.MAX_CANDIDATES)

    def test_post_skip_stories_fill_remaining_slots(self):
        tc1 = self._anchor_item("TechCrunch", "raw-SRC-018-tc000001")
        tc2 = self._anchor_item("TechCrunch", "raw-SRC-018-tc000002")
        cnbc = self._anchor_item("CNBC", "raw-SRC-017-cn000001")
        lookup = {1: tc1, 2: tc2, 3: cnbc}
        # tc2 is a duplicate anchor — it gets skipped; cnbc should still be accepted
        ranked = [{"item_id": 1}, {"item_id": 2}, {"item_id": 3}]
        result = generate._apply_anchor_cap_walk(ranked, lookup)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1]["item_id"], 3)

    def test_no_filler_added(self):
        tc1 = self._anchor_item("TechCrunch", "raw-SRC-018-fill0001")
        tc2 = self._anchor_item("TechCrunch", "raw-SRC-018-fill0002")
        lookup = {1: tc1, 2: tc2}
        ranked = [{"item_id": 1}, {"item_id": 2}]
        result = generate._apply_anchor_cap_walk(ranked, lookup)
        self.assertEqual(len(result), 1)


class TestCarryoverMerge(unittest.TestCase):

    def _candidate(self, status, hours_old, item_id="raw-SRC-001-carrytest"):
        from datetime import datetime, timezone, timedelta
        disc = (datetime.now(timezone.utc) - timedelta(hours=hours_old)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "id": item_id,
            "status": status,
            "rank": 1,
            "original_headline": "Test",
            "source_resolution": "resolved",
            "source": {"name": "Test", "url": "https://example.com/carrytest", "role": "Original Reporting", "reliability": "high", "paywall": False},
            "proposal": {"brief_headline": "TEST", "headline": "TEST", "public_summary": [], "editorial_notes": "", "category": "Models", "priority": "High", "top_story": False, "developing": False},
            "advisory": {},
            "discovered_at": disc,
        }

    def test_unresolved_within_3_days_kept(self):
        from datetime import datetime, timezone, timedelta
        cutoff_ts = datetime.now(timezone.utc) - timedelta(hours=72)
        c = self._candidate("review", hours_old=24)
        result = generate._carryover_candidates([c], cutoff_ts)
        self.assertEqual(len(result), 1)

    def test_exactly_72h_old_kept(self):
        # Candidate discovered exactly at the cutoff boundary must be kept (>=).
        # Use a fixed reference with second precision to avoid sub-second timing races.
        from datetime import datetime, timezone
        ref = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)
        c = self._candidate("review", hours_old=0)
        c["discovered_at"] = ref.strftime("%Y-%m-%dT%H:%M:%SZ")
        result = generate._carryover_candidates([c], ref)
        self.assertEqual(len(result), 1)

    def test_older_than_72h_dropped(self):
        from datetime import datetime, timezone, timedelta
        cutoff_ts = datetime.now(timezone.utc) - timedelta(hours=72)
        c = self._candidate("review", hours_old=96)
        result = generate._carryover_candidates([c], cutoff_ts)
        self.assertEqual(len(result), 0)

    def test_approved_candidate_not_carried_over(self):
        from datetime import datetime, timezone, timedelta
        cutoff_ts = datetime.now(timezone.utc) - timedelta(hours=72)
        c = self._candidate("approved", hours_old=24)
        result = generate._carryover_candidates([c], cutoff_ts)
        self.assertEqual(len(result), 0)

    def test_rejected_candidate_not_carried_over(self):
        from datetime import datetime, timezone, timedelta
        cutoff_ts = datetime.now(timezone.utc) - timedelta(hours=72)
        c = self._candidate("rejected", hours_old=24)
        result = generate._carryover_candidates([c], cutoff_ts)
        self.assertEqual(len(result), 0)

    def test_snoozed_within_72h_kept(self):
        from datetime import datetime, timezone, timedelta
        cutoff_ts = datetime.now(timezone.utc) - timedelta(hours=72)
        c = self._candidate("snoozed", hours_old=48)
        result = generate._carryover_candidates([c], cutoff_ts)
        self.assertEqual(len(result), 1)

    def test_carryover_appended_after_new_candidates_in_output(self):
        """New candidates appear before carryover in the written file."""
        new_item = _item(item_id="raw-SRC-001-new00001", canonical_url="https://example.com/new")
        carryover_item = dict(self._candidate("review", hours_old=24, item_id="raw-SRC-001-carry01"))
        CLAUDE_RESPONSE = json.dumps([_selection(item_id=1)])

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "filtered-discovery.json").write_text(json.dumps({"items": [new_item]}))
            (d / "approved-queue.json").write_text(json.dumps({"stories": []}))
            (d / "candidate-queue.json").write_text(json.dumps({"candidates": [carryover_item]}))

            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=False), \
                 patch("generate.call_claude", return_value=CLAUDE_RESPONSE):
                rc = generate.main(["--data-dir", tmpdir, "--skip-advisory"])

            result = json.loads((d / "candidate-queue.json").read_text())

        self.assertEqual(rc, 0)
        ids = [c["id"] for c in result["candidates"]]
        self.assertEqual(ids[0], new_item["item_id"])
        self.assertEqual(ids[-1], carryover_item["id"])



# ---------------------------------------------------------------------------
# Prompt section layout tests
# ---------------------------------------------------------------------------

class TestPromptSections(unittest.TestCase):
    """build_selection_prompt presents candidates in labeled source sections."""

    def _anchor_item(self, src, url_suffix, **kwargs):
        return _item(source_name=src, canonical_url=f"https://example.com/{url_suffix}", **kwargs)

    def test_anchor_items_appear_in_their_named_section(self):
        tc = self._anchor_item("TechCrunch", "tc1", item_title="TC Story")
        cnbc = self._anchor_item("CNBC", "cnbc1", item_title="CNBC Story")
        eligible, prompt = generate.build_selection_prompt([tc, cnbc], excluded_urls=set())
        tc_idx = next(i + 1 for i, it in enumerate(eligible) if it is tc)
        tc_section_start = prompt.index('=== TechCrunch [ANCHOR] ===')
        non_anchor_start = prompt.index('=== Non-anchor candidates ===')
        tc_line_pos = prompt.index(f"{tc_idx}. [TechCrunch")
        self.assertGreater(tc_line_pos, tc_section_start)
        self.assertLess(tc_line_pos, non_anchor_start)

    def test_non_anchor_items_appear_in_non_anchor_section(self):
        tc = self._anchor_item("TechCrunch", "tc1")
        cnbc = self._anchor_item("CNBC", "cnbc1", item_title="CNBC Story")
        eligible, prompt = generate.build_selection_prompt([tc, cnbc], excluded_urls=set())
        cnbc_idx = next(i + 1 for i, it in enumerate(eligible) if it is cnbc)
        non_anchor_start = prompt.index('=== Non-anchor candidates ===')
        cnbc_line_pos = prompt.index(f"{cnbc_idx}. [CNBC")
        self.assertGreater(cnbc_line_pos, non_anchor_start)

    def test_anchor_item_index_not_in_non_anchor_section(self):
        tc = self._anchor_item("TechCrunch", "tc1")
        eligible, prompt = generate.build_selection_prompt([tc], excluded_urls=set())
        tc_idx = next(i + 1 for i, it in enumerate(eligible) if it is tc)
        non_anchor_start = prompt.index('=== Non-anchor candidates ===')
        non_anchor_text = prompt[non_anchor_start:]
        self.assertNotIn(f"{tc_idx}. [TechCrunch", non_anchor_text)

    def test_empty_anchor_section_shows_no_candidates_placeholder(self):
        cnbc = self._anchor_item("CNBC", "cnbc1")
        _, prompt = generate.build_selection_prompt([cnbc], excluded_urls=set())
        reuters_start = prompt.index('=== Reuters [ANCHOR] ===')
        the_info_start = prompt.index('=== The Information [ANCHOR] ===')
        reuters_block = prompt[reuters_start:the_info_start]
        self.assertIn("no eligible candidates this run", reuters_block)

    def test_eligible_list_is_reordered_anchor_first_then_non_anchor(self):
        # Input order: CNBC, TechCrunch, BleepingComputer
        # Expected reorder: TechCrunch, BleepingComputer, CNBC
        cnbc = self._anchor_item("CNBC", "cnbc1")
        tc = self._anchor_item("TechCrunch", "tc1")
        bc = self._anchor_item("BleepingComputer", "bc1")
        eligible, _ = generate.build_selection_prompt([cnbc, tc, bc], excluded_urls=set())
        sources = [it.get("source_name") for it in eligible]
        self.assertEqual(sources.index("TechCrunch"), 0)
        self.assertEqual(sources.index("BleepingComputer"), 1)
        self.assertEqual(sources.index("CNBC"), 2)

    def test_global_indices_match_eligible_list_positions(self):
        tc = self._anchor_item("TechCrunch", "tc1")
        cnbc = self._anchor_item("CNBC", "cnbc1")
        eligible, prompt = generate.build_selection_prompt([cnbc, tc], excluded_urls=set())
        # Reorder: tc (idx 1), cnbc (idx 2)
        self.assertIs(eligible[0], tc)
        self.assertIs(eligible[1], cnbc)
        self.assertIn("1. [TechCrunch", prompt)
        self.assertIn("2. [CNBC", prompt)


class TestIndexSelection(unittest.TestCase):
    """Claude's integer item_id maps deterministically to exact source records."""

    def test_valid_index_maps_to_correct_item(self):
        item_a = _item(item_id="raw-SRC-001-aaaa", canonical_url="https://example.com/a")
        item_b = _item(item_id="raw-SRC-002-bbbb", canonical_url="https://example.com/b",
                       source_name="Other News")
        sel_b = dict(_selection(item_id=2))  # select index 2 (item_b)
        lookup = {1: item_a, 2: item_b}
        candidates = generate.build_candidates(
            [sel_b], lookup, {}, {}, False, False
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["id"], "raw-SRC-002-bbbb")

    def test_original_headline_is_verbatim_item_title(self):
        verbatim_title = "Exact title from the feed — punctuation preserved!"
        item = _item(item_id="raw-SRC-001-prov", item_title=verbatim_title)
        lookup = {1: item}
        candidates = generate.build_candidates(
            [_selection(item_id=1)], lookup, {}, {}, False, False
        )
        self.assertEqual(candidates[0]["original_headline"], verbatim_title)

    def test_candidate_id_is_feed_item_id_not_sequence_number(self):
        item = _item(item_id="raw-SRC-046-fedid1")
        lookup = {5: item}  # placed at index 5
        sel = dict(_selection(item_id=5))
        candidates = generate.build_candidates(
            [sel], lookup, {}, {}, False, False
        )
        self.assertEqual(candidates[0]["id"], "raw-SRC-046-fedid1")
        self.assertNotEqual(candidates[0]["id"], 5)

    def test_non_integer_item_id_skipped(self):
        item = _item(item_id="raw-SRC-001-xxxx")
        lookup = {1: item}
        sel = {"item_id": "raw-SRC-001-xxxx", "brief_headline": "BAD", "public_summary": [],
               "editorial_notes": "", "category": "Models", "priority": "High",
               "top_story": False, "developing": False}
        candidates = generate.build_candidates(
            [sel], lookup, {}, {}, False, False
        )
        self.assertEqual(candidates, [])

    def test_out_of_range_index_skipped(self):
        item = _item(item_id="raw-SRC-001-range")
        lookup = {1: item}
        sel = dict(_selection(item_id=999))  # 999 not in lookup
        candidates = generate.build_candidates(
            [sel], lookup, {}, {}, False, False
        )
        self.assertEqual(candidates, [])


# ---------------------------------------------------------------------------
# Flat response contract tests
# ---------------------------------------------------------------------------

class TestFlatResponseContract(unittest.TestCase):
    """_parse_claude_response parses Claude's flat ranked JSON array."""

    def test_valid_index_maps_to_correct_item(self):
        item_a = _item(item_id="raw-SRC-001-aaaa", canonical_url="https://example.com/a")
        item_b = _item(item_id="raw-SRC-002-bbbb", canonical_url="https://example.com/b")
        lookup = {1: item_a, 2: item_b}
        raw = json.dumps([dict(_selection(item_id=2))])
        result = generate._parse_claude_response(raw, lookup)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["item_id"], 2)

    def test_non_integer_item_id_fails_closed(self):
        lookup = {1: _item()}
        raw = json.dumps([{"item_id": "not-an-int", "brief_headline": "X"}])
        result = generate._parse_claude_response(raw, lookup)
        self.assertEqual(result, [])

    def test_out_of_range_index_fails_closed(self):
        lookup = {1: _item()}
        raw = json.dumps([dict(_selection(item_id=999))])
        result = generate._parse_claude_response(raw, lookup)
        self.assertEqual(result, [])

    def test_duplicate_index_deduplicated_first_kept(self):
        lookup = {1: _item()}
        sel = dict(_selection(item_id=1))
        raw = json.dumps([sel, sel])
        result = generate._parse_claude_response(raw, lookup)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["item_id"], 1)

    def test_non_list_response_raises_value_error(self):
        lookup = {1: _item()}
        raw = json.dumps({"item_id": 1})
        with self.assertRaises(ValueError):
            generate._parse_claude_response(raw, lookup)


# ---------------------------------------------------------------------------
# queued_at timestamp tests
# ---------------------------------------------------------------------------

class TestQueuedAt(unittest.TestCase):
    """queued_at is the candidate-creation timestamp; carryover uses it over discovered_at."""

    def test_build_candidates_sets_queued_at(self):
        candidates = generate.build_candidates(
            [_selection()], _make_item_lookup(_item()), {}, {}, False, False
        )
        self.assertIn("queued_at", candidates[0])
        # Must be a non-empty ISO string
        queued_at = candidates[0]["queued_at"]
        self.assertIsInstance(queued_at, str)
        self.assertTrue(queued_at.endswith("Z"))

    def test_build_candidates_queued_at_differs_from_old_fetched_at(self):
        # fetched_at is 2026-08-13 (article ingestion); queued_at must be today
        old_item = _item(fetched_at="2026-08-13T10:00:00Z")
        candidates = generate.build_candidates(
            [_selection()], _make_item_lookup(old_item), {}, {}, False, False
        )
        self.assertNotEqual(candidates[0]["queued_at"], "2026-08-13T10:00:00Z")
        # discovered_at still preserves the article's fetched_at
        self.assertEqual(candidates[0]["discovered_at"], "2026-08-13T10:00:00Z")

    def test_carryover_uses_queued_at_over_discovered_at(self):
        from datetime import datetime, timezone, timedelta
        cutoff_ts = datetime.now(timezone.utc) - timedelta(hours=72)
        # queued_at is recent; discovered_at is ancient — carryover should keep it
        c = {
            "id": "raw-SRC-001-qtest1",
            "status": "review",
            "queued_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "discovered_at": "2026-01-01T00:00:00Z",  # very old article timestamp
            "source": {"url": "https://example.com/q1"},
        }
        result = generate._carryover_candidates([c], cutoff_ts)
        self.assertEqual(len(result), 1)

    def test_carryover_ages_out_on_old_queued_at(self):
        from datetime import datetime, timezone, timedelta
        cutoff_ts = datetime.now(timezone.utc) - timedelta(hours=72)
        # queued_at is 5 days old — must age out even if discovered_at were recent
        c = {
            "id": "raw-SRC-001-qtest2",
            "status": "review",
            "queued_at": (datetime.now(timezone.utc) - timedelta(hours=120)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "discovered_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": {"url": "https://example.com/q2"},
        }
        result = generate._carryover_candidates([c], cutoff_ts)
        self.assertEqual(len(result), 0)

    def test_carryover_falls_back_to_discovered_at_when_no_queued_at(self):
        from datetime import datetime, timezone, timedelta
        cutoff_ts = datetime.now(timezone.utc) - timedelta(hours=72)
        # No queued_at field — backward compat: use discovered_at
        c = {
            "id": "raw-SRC-001-qtest3",
            "status": "review",
            "discovered_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": {"url": "https://example.com/q3"},
        }
        result = generate._carryover_candidates([c], cutoff_ts)
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
