import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import generate
import server
from control_state import ControlState


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

    def test_launcher_loads_runtime_path_and_cloudflare_keychain_credentials(self):
        launcher = (Path(__file__).parent / "AINWA.command").read_text()
        self.assertIn('$HOME/DevOps/AINWAdata', launcher)
        self.assertIn('AINWA_CLOUDFLARE_API_TOKEN', launcher)
        self.assertIn('AINWA_CLOUDFLARE_ACCOUNT_ID', launcher)
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


class PublishQueueTransactionTests(unittest.TestCase):
    def _setup(self, root):
        state = ControlState(root / "runtime")
        state.ensure()
        queued = story("new-story")
        queued["proposal"].update({"brief_headline": "New", "public_summary": ["One", "Two"], "category": "Business"})
        state.add(queued, "publish_queue")
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


if __name__ == "__main__":
    unittest.main()
