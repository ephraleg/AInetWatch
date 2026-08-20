import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import generate
import server
from control_state import ControlState
from server import validate_generated_publication


def story(sid, priority="High"):
    return {"id": sid, "original_headline": sid, "source": {"name": "Source", "url": f"https://example.com/{sid}"}, "proposal": {"priority": priority}}


class ControlStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = ControlState(Path(self.tmp.name))
        self.state.ensure()

    def tearDown(self):
        self.tmp.cleanup()

    def test_story_can_exist_in_only_one_location(self):
        self.state.add(story("one"), "scanned")
        self.state.move("one", "candidates")
        snap = self.state.snapshot()
        self.assertEqual([], snap["scanned"])
        self.assertEqual(["one"], [s["id"] for s in snap["candidates"]])

    def test_clear_standby_returns_to_candidates(self):
        self.state.add(story("snoozed"), "standby")
        result = self.state.clear("standby")
        self.assertEqual("returned_to_candidates", result["result"])
        self.assertEqual("snoozed", self.state.snapshot()["candidates"][0]["id"])

    def test_clear_scanned_records_exact_history(self):
        self.state.add(story("old"), "scanned")
        self.state.clear("scanned")
        history = json.loads(self.state.files["history"].read_text())
        self.assertIn("https://example.com/old", history["exact"])

    def test_publish_queue_reedit_stays_in_queue(self):
        self.state.add(story("queued"), "publish_queue")
        self.state.update_story("queued", {"headline": "Edited"})
        location, updated = self.state.find("queued")
        self.assertEqual("publish_queue", location)
        self.assertEqual("Edited", updated["proposal"]["headline"])

    def test_publish_selection_persists_and_clear_retains_stories(self):
        self.state.add(story("one"), "publish_queue")
        self.state.add(story("two"), "publish_queue")
        self.state.set_publish_selection(["two"])
        reloaded = ControlState(Path(self.tmp.name)).snapshot()
        self.assertEqual(1, reloaded["publish_approved_count"])
        self.assertFalse(reloaded["publish_queue"][0]["publish_approved"])
        self.assertTrue(reloaded["publish_queue"][1]["publish_approved"])
        self.state.clear_publish_selection()
        cleared = self.state.snapshot()
        self.assertEqual(0, cleared["publish_approved_count"])
        self.assertEqual(2, len(cleared["publish_queue"]))

    def test_usage_totals_need_no_model_call(self):
        self.state.record_usage("tooltip", "anthropic", "model", 100, 20, .01)
        self.assertEqual({"input_tokens": 100, "output_tokens": 20, "estimated_cost": .01}, self.state.usage_today())


class StaticUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).parent / "index.html").read_text()

    def test_four_column_ratios_and_controls(self):
        self.assertIn("grid-template-columns:18fr 18fr 18fr 46fr", self.html)
        for text in ("Scanned", "Candidates", "Standby", "Editorial", "Lazy Update", "Publish Queue", "Generate Tooltip"):
            self.assertIn(text, self.html)

    def test_clear_controls_and_publish_checkboxes(self):
        for text in ("Clear Scanned", "Clear Candidates", "Clear Standby"):
            self.assertIn(text, self.html)
        self.assertIn('class="pub"', self.html)
        self.assertIn("Load Last Scan", self.html)
        self.assertIn("watchOperation", self.html)
        self.assertIn("Currently published:", self.html)
        self.assertIn("After publishing:", self.html)
        self.assertIn("Generate Tooltip and Headline", self.html)
        self.assertIn('id="eOriginal"', self.html)
        self.assertIn('id="eDate"', self.html)
        self.assertIn("4–8 words", self.html)
        for text in ("Open Queue", "Approve Queue", "Clear Queue", "Restart Server", "Kill Server"):
            self.assertIn(text, self.html)
        self.assertIn("publishApproved()", self.html)
        self.assertIn("publish_approved_count", self.html)
        self.assertIn("o.csrf_token!==oldToken", self.html)
        self.assertIn("Building and deploying AInetWatch.com. Please wait.", self.html)
        self.assertIn("Publish failed", self.html)
        self.assertIn('class="footer-status"', self.html)
        self.assertIn('aria-live="polite"', self.html)

    def test_server_preserves_security_and_reuses_publish_script(self):
        server = (Path(__file__).parent / "server.py").read_text()
        self.assertIn('"/api/control/publish"', server)
        self.assertIn('"/api/control/recover-scan"', server)
        self.assertIn('"X-AINWA-CSRF-Token"', server)
        self.assertIn("MAX_BODY_BYTES", server)
        self.assertIn('ROOT / "publish.sh"', server)
        self.assertIn("PUBLISH_LOCK.acquire(blocking=False)", server)
        self.assertIn("published_count", server)
        self.assertIn("original_approved_payload", server)
        self.assertIn('"/api/control/approve-queue"', server)
        self.assertIn('"/api/control/clear-queue"', server)
        self.assertIn('"/api/control/restart-server"', server)
        self.assertIn('"/api/control/kill-server"', server)
        self.assertIn("RESTART_REQUESTED", server)

    def test_launcher_loads_runtime_path_and_cloudflare_keychain_credentials(self):
        launcher = (Path(__file__).parent / "AINWA.command").read_text()
        self.assertIn('$HOME/DevOps/AINWAdata', launcher)
        self.assertIn('AINWA_CLOUDFLARE_API_TOKEN', launcher)
        self.assertIn('AINWA_CLOUDFLARE_ACCOUNT_ID', launcher)
        self.assertIn('export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"', launcher)
        self.assertNotIn('echo "$CLOUDFLARE', launcher)
        self.assertNotIn('python3 "$AINWA_DIR/ingest.py"', launcher)
        self.assertNotIn("SOURCING_PID_FILE", launcher)


class SourceRecoveryTests(unittest.TestCase):
    def test_recovery_populates_60_without_candidates_or_duplicates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state = ControlState(root)
            state.ensure()
            state.add(story("candidate"), "candidates")
            items = [{
                "item_id": "candidate" if i == 0 else f"scan-{i}",
                "canonical_url": "https://example.com/candidate" if i == 0 else f"https://news.example/{i}",
                "item_url": "https://example.com/candidate" if i == 0 else f"https://news.example/{i}",
                "item_title": f"Story {i}", "source_name": f"News {i}",
                "source_priority": "high", "source_role": "Original Reporting",
                "source_reliability": "high", "age_hours": float(i),
            } for i in range(80)]
            (root / "state" / "filtered-discovery.json").write_text(json.dumps({"items": items}))
            handler = object.__new__(server.Handler)
            with patch.object(server, "CONTROL", state), patch.object(server, "DATA_DIR", root / "state"):
                result = handler._populate_scanned_from_filtered()
            snapshot = state.snapshot()
            self.assertEqual(60, result["scanned_added"])
            self.assertEqual(60, len(snapshot["scanned"]))
            self.assertNotIn("candidate", [item["id"] for item in snapshot["scanned"]])
            self.assertEqual(1, len(snapshot["candidates"]))

    def test_claude_usage_is_captured_from_provider_response(self):
        response = {"content": [{"text": "[]"}], "usage": {"input_tokens": 321, "output_tokens": 45}}
        with patch.object(generate, "_http_post", return_value=response):
            self.assertEqual("[]", generate.call_claude("prompt", "key"))
        self.assertEqual({"input_tokens": 321, "output_tokens": 45}, generate.LAST_CLAUDE_USAGE)


class PublicationGenerationStyleTests(unittest.TestCase):
    def test_approval_preserves_source_publication_date(self):
        candidate = story("dated")
        candidate["published_at"] = "2026-08-20T09:30:00Z"
        approved = server.make_approved(candidate, {})
        self.assertEqual("2026-08-20T09:30:00Z", approved["published_at"])

    def test_accepts_hook_headline_and_concise_phrase_bullets(self):
        generated = {
            "brief_headline": "Critics Challenge Zuckerberg’s AI Compute Vision",
            "public_summary": [
                "Zuckerberg’s manifesto outlines Meta’s expansive vision for AI",
                "Critics cite infrastructure constraints and difficult resource realities",
                "AI ambitions may exceed available computing power and energy",
            ],
        }
        self.assertIsNone(validate_generated_publication(generated))

    def test_rejects_verbose_or_filler_output(self):
        self.assertIn("4–8", validate_generated_publication({
            "brief_headline": "This headline contains far too many words for the required single line hook",
            "public_summary": ["Six word bullet fits this test now"] * 3,
        }))
        self.assertIn("prohibited", validate_generated_publication({
            "brief_headline": "Meta Vision Meets Compute Reality",
            "public_summary": [
                "The article explains Meta’s broad artificial intelligence vision",
                "Critics cite infrastructure constraints and difficult resource realities",
                "Available computing power may limit Meta’s ambitious roadmap",
            ],
        }))


class PublishQueueTransactionTests(unittest.TestCase):
    def _setup(self, root):
        state = ControlState(root / "runtime")
        state.ensure()
        queued = story("new-story")
        queued["proposal"].update({"brief_headline": "New", "public_summary": ["One", "Two"], "category": "Business"})
        state.add(queued, "publish_queue")
        state.set_publish_selection(["new-story"])
        approved_file = root / "published" / "approved-queue.json"
        approved_file.parent.mkdir()
        approved_file.write_text(json.dumps({"version": 1, "stories": [{"id": "existing"}]}))
        return state, approved_file

    def test_success_adds_to_master_then_removes_from_queue(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state, approved_file = self._setup(Path(tmpdir))
            handler = object.__new__(server.Handler)
            with patch.object(server, "CONTROL", state), patch.object(server, "APPROVED_FILE", approved_file), patch.dict(os.environ, {"AINWA_PUBLISH_COMMAND": "/usr/bin/true"}):
                status, result = handler._publish({"ids": ["new-story"]})
            self.assertEqual(200, status)
            self.assertEqual(2, len(json.loads(approved_file.read_text())["stories"]))
            self.assertEqual([], state.snapshot()["publish_queue"])
            self.assertEqual(1, result["published"])

    def test_failure_restores_master_and_keeps_publish_queue(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state, approved_file = self._setup(Path(tmpdir))
            original = json.loads(approved_file.read_text())
            handler = object.__new__(server.Handler)
            with patch.object(server, "CONTROL", state), patch.object(server, "APPROVED_FILE", approved_file), patch.dict(os.environ, {"AINWA_PUBLISH_COMMAND": "/usr/bin/false"}):
                status, _result = handler._publish({"ids": ["new-story"]})
            self.assertEqual(502, status)
            self.assertEqual(original, json.loads(approved_file.read_text()))
            self.assertEqual(["new-story"], [s["id"] for s in state.snapshot()["publish_queue"]])

    def test_publish_uses_saved_approval_not_request_ids(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state, approved_file = self._setup(root)
            other = story("not-approved")
            other["proposal"].update({"brief_headline": "Other", "public_summary": ["One", "Two"], "category": "Business"})
            state.add(other, "publish_queue")
            handler = object.__new__(server.Handler)
            with patch.object(server, "CONTROL", state), patch.object(server, "APPROVED_FILE", approved_file), patch.dict(os.environ, {"AINWA_PUBLISH_COMMAND": "/usr/bin/true"}):
                status, result = handler._publish({"ids": ["not-approved"]})
            self.assertEqual(200, status)
            self.assertEqual(1, result["published"])
            published_ids = [item["id"] for item in json.loads(approved_file.read_text())["stories"]]
            self.assertIn("new-story", published_ids)
            self.assertNotIn("not-approved", published_ids)


if __name__ == "__main__":
    unittest.main()
