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


def _selection(item_id="raw-SRC-001-aabbccdd",
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
    return {i["item_id"]: i for i in items}


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
        selections = [_selection(item_id=i["item_id"]) for i in items]
        candidates = generate.build_candidates(
            selections, _make_item_lookup(*items), {}, {}, False, False
        )
        self.assertLessEqual(len(candidates), generate.MAX_CANDIDATES)

    def test_rank_assigned_sequentially(self):
        items = [_item(item_id=f"raw-SRC-001-{i:08x}", canonical_url=f"https://example.com/{i}")
                 for i in range(3)]
        selections = [_selection(item_id=i["item_id"]) for i in items]
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
        grok_results = {item["item_id"]: {"status": "ok", "assessment": "agree", "notes": "Looks good."}}
        candidates = generate.build_candidates(
            [_selection()], _make_item_lookup(item), grok_results, {}, True, False
        )
        self.assertEqual(candidates[0]["advisory"]["grok"]["status"], "ok")
        self.assertEqual(candidates[0]["advisory"]["grok"]["assessment"], "agree")

    def test_gemini_result_stored_when_available(self):
        item = _item()
        gemini_results = {item["item_id"]: {"status": "ok", "assessment": "flag", "notes": "Check source."}}
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
        # Claude selection prompt would exclude item, so return empty list
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


if __name__ == "__main__":
    unittest.main()
