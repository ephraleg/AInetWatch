"""Critical tests for build.py (AINWA-009).

Security gates tested here:
  - Unlocked record → hard exit (no output written)
  - Non-approved record excluded from output
  - HTML injection in headline/source escaped
  - javascript: (non-http) URL blocked — no href written
  - 60 approved stories → 50 newest on homepage, 10 in archive
  - editorial_notes never appears anywhere in built output
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Allow running from repo root or from the module directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build


MINIMAL_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head><title>AInetWatch</title></head>
<body>
<main class="wrap">
<p>PLACEHOLDER</p>
</main>
</body>
</html>
"""


def _ts(offset_hours: int = 0) -> str:
    """Return an ISO 8601 UTC timestamp offset from a fixed base."""
    base = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
    return (base + timedelta(hours=offset_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _story(
    sid: str,
    *,
    locked: bool = True,
    status: str | None = None,
    approved_at: str | None = None,
    headline: str = "Test Headline",
    url: str = "https://example.com/story",
    source_name: str = "Example News",
    public_summary: list | None = None,
    editorial_notes: str | None = None,
    top_story: bool = False,
    developing: bool = False,
    priority: str = "Medium",
) -> dict:
    """Build a minimal valid approved-queue story record."""
    record: dict = {
        "id": sid,
        "source": {"name": source_name, "url": url, "role": "Original Reporting",
                   "reliability": "High", "paywall": False},
        "approved": {
            "headline": headline,
            "public_summary": public_summary or ["Point one.", "Point two.", "Point three."],
            "category": "Models",
            "priority": priority,
            "top_story": top_story,
            "developing": developing,
            "paywall": False,
            "approved_at": approved_at or _ts(),
            "approved_by": "human",
            "locked": locked,
        },
        "original_headline": "Original headline",
        "discovered_at": _ts(),
        "language": {"source_language": "en", "localizations": {}},
    }
    if status is not None:
        record["status"] = status
    if editorial_notes is not None:
        # editorial_notes must never be written into approved records in production,
        # but we add it here at the story level to verify build.py never emits it.
        record["editorial_notes"] = editorial_notes
    return record


def _write_queue(data_dir: Path, stories: list[dict]) -> None:
    queue = {"version": 1, "updated_at": _ts(), "stories": stories}
    (data_dir / "approved-queue.json").write_text(
        json.dumps(queue, indent=2), encoding="utf-8"
    )


def _write_template(output_dir: Path) -> Path:
    tpl = output_dir / "template.html"
    tpl.write_text(MINIMAL_TEMPLATE, encoding="utf-8")
    return tpl


class TestUnlockedBlocksBuild(unittest.TestCase):
    """Unlocked record must cause a hard exit — no output written."""

    def test_unlocked_story_exits_before_writing(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d) / "data"
            data_dir.mkdir()
            out_dir = Path(d) / "out"
            out_dir.mkdir()

            stories = [
                _story("s1", locked=True),
                _story("s2", locked=False),  # this one is not locked
            ]
            _write_queue(data_dir, stories)
            tpl = _write_template(Path(d))

            with self.assertRaises(SystemExit) as cm:
                build.build(data_dir, out_dir, tpl)

            self.assertNotEqual(cm.exception.code, 0)
            # No output files must exist.
            self.assertFalse((out_dir / "index.html").exists())
            self.assertFalse((out_dir / "archive.html").exists())

    def test_all_locked_builds_successfully(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d) / "data"
            data_dir.mkdir()
            out_dir = Path(d) / "out"
            out_dir.mkdir()

            _write_queue(data_dir, [_story("s1"), _story("s2")])
            tpl = _write_template(Path(d))

            build.build(data_dir, out_dir, tpl)

            self.assertTrue((out_dir / "index.html").exists())
            self.assertTrue((out_dir / "archive.html").exists())


class TestUnapprovedExcluded(unittest.TestCase):
    """Records with a non-approved top-level status must not appear in output."""

    def test_rejected_story_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d) / "data"
            data_dir.mkdir()
            out_dir = Path(d) / "out"
            out_dir.mkdir()

            stories = [
                _story("good", headline="Approved Story"),
                _story("bad", status="rejected", headline="Rejected Story"),
            ]
            _write_queue(data_dir, stories)
            tpl = _write_template(Path(d))

            build.build(data_dir, out_dir, tpl)

            index_html = (out_dir / "index.html").read_text(encoding="utf-8")
            self.assertIn("Approved Story", index_html)
            self.assertNotIn("Rejected Story", index_html)

    def test_snoozed_story_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d) / "data"
            data_dir.mkdir()
            out_dir = Path(d) / "out"
            out_dir.mkdir()

            stories = [
                _story("keep", headline="Kept Story"),
                _story("snooze", status="snoozed", headline="Snoozed Story"),
            ]
            _write_queue(data_dir, stories)
            tpl = _write_template(Path(d))

            build.build(data_dir, out_dir, tpl)

            index_html = (out_dir / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("Snoozed Story", index_html)


class TestHtmlEscaping(unittest.TestCase):
    """All story text must be HTML-escaped before output."""

    def test_headline_injection_escaped(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d) / "data"
            data_dir.mkdir()
            out_dir = Path(d) / "out"
            out_dir.mkdir()

            malicious = '<script>alert("xss")</script>'
            _write_queue(data_dir, [_story("s1", headline=malicious)])
            tpl = _write_template(Path(d))

            build.build(data_dir, out_dir, tpl)

            index_html = (out_dir / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("<script>", index_html)
            self.assertIn("&lt;script&gt;", index_html)

    def test_source_name_injection_escaped(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d) / "data"
            data_dir.mkdir()
            out_dir = Path(d) / "out"
            out_dir.mkdir()

            malicious = '"><img src=x onerror=alert(1)>'
            _write_queue(data_dir, [_story("s1", source_name=malicious)])
            tpl = _write_template(Path(d))

            build.build(data_dir, out_dir, tpl)

            index_html = (out_dir / "index.html").read_text(encoding="utf-8")
            # The injected tag must not appear unescaped.
            # html.escape keeps "onerror=alert" as literal text inside the escaped entity —
            # that's safe. What must NOT appear is the raw unescaped <img ... onerror=...> tag.
            self.assertNotIn('<img src=x onerror=alert', index_html)
            self.assertIn("&lt;img src=x", index_html)

    def test_summary_bullet_injection_escaped(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d) / "data"
            data_dir.mkdir()
            out_dir = Path(d) / "out"
            out_dir.mkdir()

            malicious_summary = ['<b onmouseover=alert(1)>hover me</b>']
            _write_queue(data_dir, [_story("s1", public_summary=malicious_summary)])
            tpl = _write_template(Path(d))

            build.build(data_dir, out_dir, tpl)

            index_html = (out_dir / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("<b ", index_html)
            self.assertIn("&lt;b ", index_html)


class TestUrlBlocking(unittest.TestCase):
    """Non-http(s) URLs must not produce an href attribute."""

    def _build_and_read(self, url: str) -> str:
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d) / "data"
            data_dir.mkdir()
            out_dir = Path(d) / "out"
            out_dir.mkdir()

            _write_queue(data_dir, [_story("s1", url=url)])
            tpl = _write_template(Path(d))
            build.build(data_dir, out_dir, tpl)
            return (out_dir / "index.html").read_text(encoding="utf-8")

    def test_javascript_url_blocked(self):
        html = self._build_and_read("javascript:alert(1)")
        self.assertNotIn('href=', html)

    def test_data_url_blocked(self):
        html = self._build_and_read("data:text/html,<h1>hi</h1>")
        self.assertNotIn('href=', html)

    def test_file_url_blocked(self):
        html = self._build_and_read("file:///etc/passwd")
        self.assertNotIn('href=', html)

    def test_http_url_allowed(self):
        html = self._build_and_read("http://example.com/story")
        self.assertIn('href="http://example.com/story"', html)

    def test_https_url_allowed(self):
        html = self._build_and_read("https://example.com/story")
        self.assertIn('href="https://example.com/story"', html)


class TestHomepageCap(unittest.TestCase):
    """60 approved stories → 50 most recent on homepage, 10 in archive."""

    def _make_60_stories(self) -> list[dict]:
        """Return 60 stories. Story IDs encode their recency: s59 is newest."""
        stories = []
        for i in range(60):
            stories.append(_story(
                f"s{i:02d}",
                headline=f"Story {i:02d}",
                approved_at=_ts(i),  # later i → later timestamp → more recent
            ))
        return stories

    def test_fifty_newest_on_homepage(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d) / "data"
            data_dir.mkdir()
            out_dir = Path(d) / "out"
            out_dir.mkdir()

            _write_queue(data_dir, self._make_60_stories())
            tpl = _write_template(Path(d))

            build.build(data_dir, out_dir, tpl)

            index_html = (out_dir / "index.html").read_text(encoding="utf-8")
            archive_html = (out_dir / "archive.html").read_text(encoding="utf-8")

            # Stories s10–s59 (the 50 most recent) must be on homepage.
            for i in range(10, 60):
                self.assertIn(f"Story {i:02d}", index_html,
                              msg=f"Story {i:02d} should be on homepage (index.html)")

            # Stories s00–s09 (the 10 oldest) must be in archive.
            for i in range(0, 10):
                self.assertIn(f"Story {i:02d}", archive_html,
                              msg=f"Story {i:02d} should be in archive (archive.html)")

    def test_oldest_not_on_homepage(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d) / "data"
            data_dir.mkdir()
            out_dir = Path(d) / "out"
            out_dir.mkdir()

            _write_queue(data_dir, self._make_60_stories())
            tpl = _write_template(Path(d))

            build.build(data_dir, out_dir, tpl)

            index_html = (out_dir / "index.html").read_text(encoding="utf-8")

            # The 10 oldest stories must NOT appear on the homepage.
            for i in range(0, 10):
                self.assertNotIn(f"Story {i:02d}", index_html,
                                 msg=f"Story {i:02d} should NOT be on homepage")

    def test_archive_has_exactly_ten(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d) / "data"
            data_dir.mkdir()
            out_dir = Path(d) / "out"
            out_dir.mkdir()

            _write_queue(data_dir, self._make_60_stories())
            tpl = _write_template(Path(d))

            build.build(data_dir, out_dir, tpl)

            archive_html = (out_dir / "archive.html").read_text(encoding="utf-8")
            # Each story appears exactly once. Count occurrences of data-story-id.
            self.assertEqual(archive_html.count('data-story-id="s'), 10)

    def test_priority_does_not_override_recency(self):
        """A high-priority old story must not displace a newer story from the homepage."""
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d) / "data"
            data_dir.mkdir()
            out_dir = Path(d) / "out"
            out_dir.mkdir()

            stories = self._make_60_stories()
            # s00 is the oldest story — mark it high priority.
            stories[0]["approved"]["priority"] = "High"
            _write_queue(data_dir, stories)
            tpl = _write_template(Path(d))

            build.build(data_dir, out_dir, tpl)

            index_html = (out_dir / "index.html").read_text(encoding="utf-8")
            archive_html = (out_dir / "archive.html").read_text(encoding="utf-8")

            # s00 is old — it must be in archive regardless of priority.
            self.assertNotIn("Story 00", index_html)
            self.assertIn("Story 00", archive_html)


class TestEditorialNotesNeverInOutput(unittest.TestCase):
    """editorial_notes must never appear in index.html or archive.html."""

    def test_editorial_notes_absent_from_index(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d) / "data"
            data_dir.mkdir()
            out_dir = Path(d) / "out"
            out_dir.mkdir()

            secret = "EDITORIAL_SECRET_DO_NOT_PUBLISH"
            _write_queue(data_dir, [_story("s1", editorial_notes=secret)])
            tpl = _write_template(Path(d))

            build.build(data_dir, out_dir, tpl)

            index_html = (out_dir / "index.html").read_text(encoding="utf-8")
            self.assertNotIn(secret, index_html)
            self.assertNotIn("editorial", index_html.lower())

    def test_editorial_notes_absent_from_archive(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d) / "data"
            data_dir.mkdir()
            out_dir = Path(d) / "out"
            out_dir.mkdir()

            secret = "INTERNAL_NOTE_ARCHIVE_CHECK"
            # Create 51 stories so one lands in archive; last one has editorial_notes.
            stories = [
                _story(f"s{i:02d}", approved_at=_ts(i))
                for i in range(50)
            ]
            stories.append(_story("s50", approved_at=_ts(-1), editorial_notes=secret))
            _write_queue(data_dir, stories)
            tpl = _write_template(Path(d))

            build.build(data_dir, out_dir, tpl)

            archive_html = (out_dir / "archive.html").read_text(encoding="utf-8")
            self.assertNotIn(secret, archive_html)
            self.assertNotIn("editorial", archive_html.lower())


class TestDryRun(unittest.TestCase):
    """--dry-run must not write any files."""

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d) / "data"
            data_dir.mkdir()
            out_dir = Path(d) / "out"
            out_dir.mkdir()

            _write_queue(data_dir, [_story("s1")])
            tpl = _write_template(Path(d))

            build.build(data_dir, out_dir, tpl, dry_run=True)

            self.assertFalse((out_dir / "index.html").exists())
            self.assertFalse((out_dir / "archive.html").exists())


class TestEmptyQueue(unittest.TestCase):
    """Empty approved-queue must produce valid (empty-state) HTML without crashing."""

    def test_empty_queue_builds(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d) / "data"
            data_dir.mkdir()
            out_dir = Path(d) / "out"
            out_dir.mkdir()

            _write_queue(data_dir, [])
            tpl = _write_template(Path(d))

            build.build(data_dir, out_dir, tpl)

            index_html = (out_dir / "index.html").read_text(encoding="utf-8")
            archive_html = (out_dir / "archive.html").read_text(encoding="utf-8")
            self.assertIn("<main", index_html)
            self.assertIn("<main", archive_html)


if __name__ == "__main__":
    unittest.main()
