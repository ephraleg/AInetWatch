"""AINWA-011 (#89) Minimum critical production-readiness checks.

Does NOT duplicate tests already covered by:
  test_build.py  — unlocked records, unapproved exclusion, HTML escaping,
                   unsafe URLs, 60-story cap
  test_server.py — editorial_notes/advisory absent from approved record

Checks added here:
  1. editorial_notes and advisory sentinel values absent from built HTML
  2. publish.sh preflight exits 0; --deploy without credentials exits non-zero;
     dummy sentinel token never appears in stdout/stderr
  3. publish.sh deploys the Worker via wrangler.jsonc, whose assets
     directory is dist (never .); allowlist excludes JSON,
     Python, docs, test, Git, and secret file types
  4. Production-path files contain no /Users/q/AInetWatch reference
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
PUBLISH_SH = SCRIPT_DIR / "publish.sh"
BUILD_PY = SCRIPT_DIR / "build.py"

sys.path.insert(0, str(SCRIPT_DIR))
import build


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _locked_story(sid, *, headline="Headline", url="https://example.com/s",
                  approved_at="2026-08-01T12:00:00Z", **extra_approved):
    return {
        "id": sid,
        "source": {"name": "Source", "url": url, "role": "Original Reporting",
                   "reliability": "High", "paywall": False},
        "approved": {
            "headline": headline,
            "public_summary": ["Point one.", "Point two.", "Point three."],
            "category": "Models", "priority": "Medium",
            "top_story": False, "developing": False, "paywall": False,
            "approved_at": approved_at, "approved_by": "human", "locked": True,
            **extra_approved,
        },
        "original_headline": headline,
        "discovered_at": approved_at,
        "language": {"source_language": "en", "localizations": {}},
    }


def _write_queue(data_dir: Path, stories: list) -> None:
    (data_dir / "approved-queue.json").write_text(
        json.dumps({"version": 1, "updated_at": "2026-08-01T12:00:00Z",
                    "stories": stories}),
        encoding="utf-8",
    )


MINIMAL_TEMPLATE = (
    '<!DOCTYPE html><html><body>'
    '<main class="wrap"><p>PLACEHOLDER</p></main>'
    '</body></html>\n'
)


# ---------------------------------------------------------------------------
# 1. editorial_notes and advisory sentinel values never in built HTML
# ---------------------------------------------------------------------------

class TestSentinelContentAbsentFromHtml(unittest.TestCase):
    """editorial_notes and advisory must never surface in generated HTML
    even when present at the story level (defence-in-depth over schema
    enforcement in server.py)."""

    def test_editorial_notes_and_advisory_absent_from_built_html(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d) / "data"
            data_dir.mkdir()
            out_dir = Path(d) / "out"
            out_dir.mkdir()
            tpl = Path(d) / "template.html"
            tpl.write_text(MINIMAL_TEMPLATE, encoding="utf-8")

            editorial_sentinel = "EDITORIAL_SENTINEL_MUST_NOT_PUBLISH"
            advisory_sentinel  = "ADVISORY_SENTINEL_MUST_NOT_PUBLISH"

            story = _locked_story("s1")
            # Inject sentinels at story level — the paths a leaky pipeline
            # could introduce them even if server.py never writes them to
            # approved records.
            story["editorial_notes"] = editorial_sentinel
            story["advisory"] = {
                "grok":   {"verdict": advisory_sentinel},
                "gemini": {"verdict": advisory_sentinel},
            }

            _write_queue(data_dir, [story])
            build.build(data_dir, out_dir, tpl)

            for filename in ("index.html", "archive.html"):
                html = (out_dir / filename).read_text(encoding="utf-8")
                self.assertNotIn(editorial_sentinel, html,
                                 msg=f"editorial_notes sentinel found in {filename}")
                self.assertNotIn(advisory_sentinel, html,
                                 msg=f"advisory sentinel found in {filename}")
                self.assertNotIn("editorial", html.lower(),
                                 msg=f"'editorial' string found in {filename}")


# ---------------------------------------------------------------------------
# 2. publish.sh gate behaviour
# ---------------------------------------------------------------------------

class TestPublishShGates(unittest.TestCase):
    """Preflight success, credential-absent --deploy failure, token not printed."""

    def _run(self, args=(), env_extra=None, timeout=30):
        env = {k: v for k, v in os.environ.items()
               if k not in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID")}
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            ["bash", str(PUBLISH_SH)] + list(args),
            capture_output=True, text=True, timeout=timeout,
            env=env,
        )

    def test_preflight_exits_zero(self):
        result = self._run()
        self.assertEqual(result.returncode, 0,
                         msg=f"preflight stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        combined = result.stdout + result.stderr
        self.assertIn("PREFLIGHT OK", combined)

    def test_deploy_without_token_fails_closed(self):
        result = self._run(args=["--deploy"])
        self.assertNotEqual(result.returncode, 0)
        combined = result.stdout + result.stderr
        self.assertIn("CLOUDFLARE_API_TOKEN", combined)

    def test_deploy_without_account_id_fails_closed(self):
        result = self._run(
            args=["--deploy"],
            env_extra={"CLOUDFLARE_API_TOKEN": "dummy-token-value"},
        )
        self.assertNotEqual(result.returncode, 0)
        combined = result.stdout + result.stderr
        self.assertIn("CLOUDFLARE_ACCOUNT_ID", combined)

    def test_token_value_never_in_output(self):
        sentinel_token = "SECRET_SENTINEL_TOKEN_XK29QV"
        result = self._run(
            args=["--deploy"],
            env_extra={"CLOUDFLARE_API_TOKEN": sentinel_token},
        )
        # Aborts because ACCOUNT_ID is absent — that's expected.
        self.assertNotEqual(result.returncode, 0)
        combined = result.stdout + result.stderr
        self.assertNotIn(sentinel_token, combined,
                         msg="API token sentinel value appeared in script output")


# ---------------------------------------------------------------------------
# 3. publish.sh static analysis — deploy target and allowlist
# ---------------------------------------------------------------------------

class TestPublishShStaticAnalysis(unittest.TestCase):
    """publish.sh must deploy the Worker via wrangler.jsonc, whose assets
    directory must be dist/ — never the repo root — and its allowlist must
    exclude sensitive or non-public file types."""

    _SCRIPT_TEXT = PUBLISH_SH.read_text(encoding="utf-8")
    _WRANGLER_JSONC_TEXT = (SCRIPT_DIR / "wrangler.jsonc").read_text(encoding="utf-8")

    # File extensions / names that must never be in the deploy allowlist.
    _FORBIDDEN_PATTERNS = (
        ".json", ".py", ".md", ".sh", ".txt", ".yml", ".yaml",
        ".env", ".git", ".pem", ".key", ".log",
    )

    def test_deploy_uses_wrangler_deploy_with_config(self):
        # The Worker deploy command must reference wrangler.jsonc.
        self.assertIn("wrangler deploy --config wrangler.jsonc", self._SCRIPT_TEXT)

    def test_deploy_never_targets_repo_root_or_bypasses_dist(self):
        # Neither the old Pages-deploy-of-root form nor a Worker-deploy-of-root
        # form may appear, and the config it deploys with must scope assets
        # to dist/, never the repo root.
        self.assertNotIn("wrangler pages deploy .", self._SCRIPT_TEXT)
        self.assertNotIn("wrangler deploy .", self._SCRIPT_TEXT)
        self.assertNotIn('"directory": "."', self._WRANGLER_JSONC_TEXT)
        self.assertIn('"directory": "dist"', self._WRANGLER_JSONC_TEXT)

    def test_allowlist_excludes_forbidden_file_types(self):
        # Extract the ALLOWLIST block from the script.
        in_block = False
        allowlist_entries = []
        for line in self._SCRIPT_TEXT.splitlines():
            stripped = line.strip()
            if stripped.startswith("ALLOWLIST=("):
                in_block = True
                continue
            if in_block:
                if stripped == ")":
                    break
                entry = stripped.strip("'\"")
                if entry:
                    allowlist_entries.append(entry)

        self.assertTrue(allowlist_entries,
                        msg="Could not parse ALLOWLIST from publish.sh")

        for entry in allowlist_entries:
            for forbidden in self._FORBIDDEN_PATTERNS:
                self.assertFalse(
                    entry.lower().endswith(forbidden) or entry == forbidden,
                    msg=f"Forbidden entry '{entry}' (matches '{forbidden}') in publish.sh ALLOWLIST",
                )


# ---------------------------------------------------------------------------
# 4. Production-path files contain no /Users/q/AInetWatch reference
# ---------------------------------------------------------------------------

class TestProductionPathIsolation(unittest.TestCase):
    """No production-path file may reference /Users/q/AInetWatch.
    Test files are excluded — they may document the constraint."""

    _PRODUCTION_FILES = [
        p for p in SCRIPT_DIR.iterdir()
        if p.suffix in (".py", ".sh") and not p.name.startswith("test_")
    ]

    def test_no_ainetwatch_path_in_production_files(self):
        self.assertTrue(self._PRODUCTION_FILES,
                        msg="No production files found to check")
        violations = []
        for path in self._PRODUCTION_FILES:
            text = path.read_text(encoding="utf-8")
            if "/Users/q/AInetWatch" in text:
                violations.append(path.name)
        self.assertEqual(violations, [],
                         msg=f"Found /Users/q/AInetWatch in: {violations}")


if __name__ == "__main__":
    unittest.main()
