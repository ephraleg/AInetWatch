import json
import tempfile
import unittest
from pathlib import Path

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

    def test_server_preserves_security_and_reuses_publish_script(self):
        server = (Path(__file__).parent / "server.py").read_text()
        self.assertIn('"/api/control/publish"', server)
        self.assertIn('"X-AINWA-CSRF-Token"', server)
        self.assertIn("MAX_BODY_BYTES", server)
        self.assertIn('ROOT / "publish.sh"', server)
        self.assertIn("PUBLISH_LOCK.acquire(blocking=False)", server)

    def test_launcher_loads_runtime_path_and_cloudflare_keychain_credentials(self):
        launcher = (Path(__file__).parent / "AINWA.command").read_text()
        self.assertIn('$HOME/DevOps/AINWAdata', launcher)
        self.assertIn('AINWA_CLOUDFLARE_API_TOKEN', launcher)
        self.assertIn('AINWA_CLOUDFLARE_ACCOUNT_ID', launcher)
        self.assertNotIn('echo "$CLOUDFLARE', launcher)


if __name__ == "__main__":
    unittest.main()
