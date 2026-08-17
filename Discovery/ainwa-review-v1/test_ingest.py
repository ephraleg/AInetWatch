#!/usr/bin/env python3
"""Focused security and robustness tests for ingest.py.

Coverage:
  - Valid RSS 2.0 parsing
  - Valid Atom 1.0 parsing
  - Non-http(s) scheme rejection
  - Private/loopback IP blocking
  - Redirect-to-private-address rejection
  - Oversized response rejection
  - Malformed XML rejection
  - Exact-URL deduplication within a run

Run:
    python3 test_ingest.py
    python3 -m pytest test_ingest.py -v   (if pytest is available)
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent))
import ingest

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

_SOURCE = {
    "id": "SRC-TEST",
    "name": "Test Source",
    "domain": "example.com",
    "priority": "high",
    "tier": 1,
    "access": "free",
    "role": "Original Reporting",
    "citation_allowed": "yes",
    "reliability": "high",
}

_RSS_BYTES = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Test RSS Feed</title>
    <item>
      <title>Article One</title>
      <link>https://example.com/article-1</link>
      <pubDate>Wed, 13 Aug 2026 10:00:00 +0000</pubDate>
      <description>&lt;p&gt;HTML description.&lt;/p&gt;</description>
    </item>
    <item>
      <title>Article Two</title>
      <link>https://example.com/article-2</link>
      <description>Plain text description.</description>
    </item>
    <item>
      <title>No-Link Item - must be skipped</title>
    </item>
  </channel>
</rss>"""

_ATOM_BYTES = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Test Atom Feed</title>
  <entry>
    <title>Atom Article One</title>
    <link rel="alternate" href="https://example.com/atom-1"/>
    <published>2026-08-13T10:00:00Z</published>
    <summary type="html">&lt;p&gt;HTML summary.&lt;/p&gt;</summary>
  </entry>
  <entry>
    <title>Atom Article Two</title>
    <link href="https://example.com/atom-2"/>
    <content type="text">Plain text content.</content>
  </entry>
  <entry>
    <title>No-Link Entry - must be skipped</title>
    <summary>No href attribute anywhere.</summary>
  </entry>
</feed>"""


# ---------------------------------------------------------------------------
# 1. Valid RSS parsing
# ---------------------------------------------------------------------------

class TestRSSParsing(unittest.TestCase):
    def _parse(self):
        return ingest.parse_feed(_RSS_BYTES, _SOURCE, "https://example.com/feed", "2026-08-13T10:00:00Z")

    def test_item_count_excludes_no_link(self):
        items = self._parse()
        self.assertEqual(len(items), 2, "Item without <link> must be skipped")

    def test_item_url(self):
        items = self._parse()
        self.assertEqual(items[0]["item_url"], "https://example.com/article-1")

    def test_item_title(self):
        items = self._parse()
        self.assertEqual(items[0]["item_title"], "Article One")

    def test_item_published(self):
        items = self._parse()
        self.assertEqual(items[0]["item_published"], "Wed, 13 Aug 2026 10:00:00 +0000")

    def test_html_description_format(self):
        items = self._parse()
        self.assertEqual(items[0]["feed_description_format"], "html")

    def test_text_description_format(self):
        items = self._parse()
        self.assertEqual(items[1]["feed_description_format"], "text")

    def test_source_fields_propagated(self):
        items = self._parse()
        self.assertEqual(items[0]["source_id"], "SRC-TEST")
        self.assertEqual(items[0]["source_name"], "Test Source")
        self.assertEqual(items[0]["fetch_method"], "rss")

    def test_deterministic_item_id(self):
        items = self._parse()
        expected = ingest._make_item_id("SRC-TEST", "https://example.com/article-1")
        self.assertEqual(items[0]["item_id"], expected)

    def test_item_id_stable_across_calls(self):
        items1 = self._parse()
        items2 = self._parse()
        self.assertEqual(items1[0]["item_id"], items2[0]["item_id"])


# ---------------------------------------------------------------------------
# 2. Valid Atom parsing
# ---------------------------------------------------------------------------

class TestAtomParsing(unittest.TestCase):
    def _parse(self):
        return ingest.parse_feed(_ATOM_BYTES, _SOURCE, "https://example.com/feed", "2026-08-13T10:00:00Z")

    def test_item_count_excludes_no_link(self):
        items = self._parse()
        self.assertEqual(len(items), 2, "Atom entry without href must be skipped")

    def test_item_url(self):
        items = self._parse()
        self.assertEqual(items[0]["item_url"], "https://example.com/atom-1")

    def test_item_title(self):
        items = self._parse()
        self.assertEqual(items[0]["item_title"], "Atom Article One")

    def test_item_published(self):
        items = self._parse()
        self.assertEqual(items[0]["item_published"], "2026-08-13T10:00:00Z")

    def test_html_description_format(self):
        items = self._parse()
        self.assertEqual(items[0]["feed_description_format"], "html")

    def test_text_description_format(self):
        items = self._parse()
        self.assertEqual(items[1]["feed_description_format"], "text")

    def test_fetch_method_atom(self):
        items = self._parse()
        self.assertEqual(items[0]["fetch_method"], "atom")


# ---------------------------------------------------------------------------
# 3. Non-http(s) scheme rejection
# ---------------------------------------------------------------------------

class TestSchemeRejection(unittest.TestCase):
    def _check(self, url):
        with self.assertRaises(ValueError, msg=f"Should reject scheme in {url!r}"):
            ingest.validate_url(url)

    def test_rejects_ftp(self):
        self._check("ftp://example.com/feed")

    def test_rejects_file(self):
        self._check("file:///etc/passwd")

    def test_rejects_javascript(self):
        self._check("javascript:alert(1)")

    def test_rejects_data_uri(self):
        self._check("data:text/html,<script>alert(1)</script>")

    def test_rejects_empty_string(self):
        with self.assertRaises(ValueError):
            ingest.validate_url("")

    def test_accepts_https(self):
        # Must not raise
        ingest.validate_url("https://example.com/feed")

    def test_accepts_http(self):
        ingest.validate_url("http://example.com/feed")


# ---------------------------------------------------------------------------
# 4. Private / loopback IP blocking (is_blocked_ip)
# ---------------------------------------------------------------------------

class TestPrivateIPBlocking(unittest.TestCase):
    def _blocked(self, ip):
        self.assertTrue(ingest.is_blocked_ip(ip), f"Expected {ip!r} to be blocked")

    def _allowed(self, ip):
        self.assertFalse(ingest.is_blocked_ip(ip), f"Expected {ip!r} to be allowed")

    def test_loopback_v4(self):
        self._blocked("127.0.0.1")

    def test_loopback_v4_other(self):
        self._blocked("127.0.0.2")

    def test_loopback_v6(self):
        self._blocked("::1")

    def test_rfc1918_10(self):
        self._blocked("10.0.0.1")
        self._blocked("10.255.255.255")

    def test_rfc1918_172(self):
        self._blocked("172.16.0.1")
        self._blocked("172.31.255.255")

    def test_rfc1918_192(self):
        self._blocked("192.168.1.1")
        self._blocked("192.168.0.0")

    def test_link_local_metadata(self):
        # AWS/GCP/Azure instance metadata endpoint
        self._blocked("169.254.169.254")
        self._blocked("169.254.0.1")

    def test_multicast_v4(self):
        self._blocked("224.0.0.1")
        self._blocked("239.255.255.255")

    def test_ipv4_mapped_v6_private(self):
        # ::ffff:127.0.0.1 is IPv4-mapped loopback
        self._blocked("::ffff:7f00:1")   # ::ffff:127.0.0.1

    def test_unique_local_v6(self):
        self._blocked("fc00::1")
        self._blocked("fd00::1")

    def test_link_local_v6(self):
        self._blocked("fe80::1")

    def test_public_ipv4_allowed(self):
        self._allowed("8.8.8.8")
        self._allowed("1.1.1.1")
        self._allowed("93.184.216.34")  # example.com

    def test_public_ipv6_allowed(self):
        self._allowed("2001:4860:4860::8888")  # Google DNS


# ---------------------------------------------------------------------------
# 5. resolve_and_check blocks private IPs via DNS
# ---------------------------------------------------------------------------

class TestResolveAndCheck(unittest.TestCase):
    def _mock_dns(self, ip):
        return [(None, None, None, None, (ip, 0))]

    def test_blocks_loopback_via_dns(self):
        with patch("socket.getaddrinfo", return_value=self._mock_dns("127.0.0.1")):
            with self.assertRaises(ValueError):
                ingest.resolve_and_check("localhost")

    def test_blocks_private_via_dns(self):
        with patch("socket.getaddrinfo", return_value=self._mock_dns("10.0.0.1")):
            with self.assertRaises(ValueError):
                ingest.resolve_and_check("internal.corp")

    def test_blocks_metadata_via_dns(self):
        with patch("socket.getaddrinfo", return_value=self._mock_dns("169.254.169.254")):
            with self.assertRaises(ValueError):
                ingest.resolve_and_check("metadata.internal")

    def test_dns_failure_raises(self):
        import socket as _socket
        with patch("socket.getaddrinfo", side_effect=_socket.gaierror("NXDOMAIN")):
            with self.assertRaises(ValueError):
                ingest.resolve_and_check("does-not-exist.invalid")


# ---------------------------------------------------------------------------
# 6. Redirect-to-private-address rejection
# ---------------------------------------------------------------------------

class TestRedirectToPrivate(unittest.TestCase):
    def _make_handler(self):
        return ingest.SSRFRedirectHandler()

    def test_redirect_to_loopback_blocked(self):
        handler = self._make_handler()
        with patch("ingest.resolve_and_check", side_effect=ValueError("loopback")):
            with self.assertRaises(ValueError):
                handler.redirect_request(
                    MagicMock(), MagicMock(), 302, "Found", {},
                    "http://127.0.0.1/secret",
                )

    def test_redirect_to_private_ip_blocked(self):
        handler = self._make_handler()
        with patch("ingest.resolve_and_check", side_effect=ValueError("private")):
            with self.assertRaises(ValueError):
                handler.redirect_request(
                    MagicMock(), MagicMock(), 301, "Moved", {},
                    "https://10.0.0.1/data",
                )

    def test_redirect_to_ftp_blocked(self):
        handler = self._make_handler()
        # ftp:// should be rejected before DNS resolution
        with self.assertRaises(ValueError):
            handler.redirect_request(
                MagicMock(), MagicMock(), 302, "Found", {},
                "ftp://example.com/file",
            )

    def test_redirect_to_valid_host_allowed(self):
        handler = self._make_handler()
        # Patch resolve_and_check to pass (public IP).  super() may raise
        # HTTPError or TypeError when called with MagicMock args — that is
        # acceptable because our SSRF validation already passed by then.
        with patch("ingest.resolve_and_check"):
            try:
                handler.redirect_request(
                    MagicMock(spec=ingest.urllib.request.Request),
                    MagicMock(), 302, "Found", {},
                    "https://example.com/redirected-feed",
                )
            except (TypeError, AttributeError, ingest.urllib.error.HTTPError):
                pass  # expected when super() receives MagicMock args


# ---------------------------------------------------------------------------
# 7. Oversized response rejection
# ---------------------------------------------------------------------------

class TestOversizedResponse(unittest.TestCase):
    def _make_mock_response(self, size: int):
        data = b"x" * size
        resp = MagicMock()
        resp.read.return_value = data
        resp.headers.get.return_value = "application/rss+xml"
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def _patch_fetch(self, size: int):
        resp = self._make_mock_response(size)
        opener = MagicMock()
        opener.open.return_value = resp
        return patch("ingest.urllib.request.build_opener", return_value=opener)

    def test_rejects_response_one_byte_over_limit(self):
        over = ingest.MAX_RESPONSE_BYTES + 1
        with self._patch_fetch(over):
            with patch("ingest.resolve_and_check"):
                with self.assertRaises(ValueError, msg="One byte over limit must be rejected"):
                    ingest.safe_fetch("https://example.com/big-feed")

    def test_accepts_response_exactly_at_limit(self):
        exact = ingest.MAX_RESPONSE_BYTES
        with self._patch_fetch(exact):
            with patch("ingest.resolve_and_check"):
                data, _ = ingest.safe_fetch("https://example.com/feed")
                self.assertEqual(len(data), exact)


# ---------------------------------------------------------------------------
# 8. Malformed XML rejection
# ---------------------------------------------------------------------------

class TestMalformedXML(unittest.TestCase):
    def test_rejects_plain_text(self):
        with self.assertRaises(ValueError):
            ingest.parse_feed(b"this is not xml", _SOURCE, "https://x.com", "2026-01-01T00:00:00Z")

    def test_rejects_truncated_xml(self):
        with self.assertRaises(ValueError):
            ingest.parse_feed(
                b"<?xml version='1.0'?><rss><channel><item>",
                _SOURCE, "https://x.com", "2026-01-01T00:00:00Z",
            )

    def test_html_page_returns_no_items(self):
        # A well-formed-XML HTML page won't raise ParseError, but it has no
        # RSS/Atom structure so parse_feed returns an empty list rather than
        # an error. This is the correct behavior: content is untrusted but not
        # invalid XML, so we surface zero items and the run continues.
        items = ingest.parse_feed(
            b"<html><body>Not a feed</body></html>",
            _SOURCE, "https://x.com", "2026-01-01T00:00:00Z",
        )
        self.assertEqual(items, [])

    def test_empty_bytes(self):
        with self.assertRaises(ValueError):
            ingest.parse_feed(b"", _SOURCE, "https://x.com", "2026-01-01T00:00:00Z")


# ---------------------------------------------------------------------------
# 9. Exact-URL deduplication within a run
# ---------------------------------------------------------------------------

class TestExactURLDedup(unittest.TestCase):
    _DUPE_RSS = b"""\
<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item><title>A</title><link>https://example.com/story</link></item>
  <item><title>A again</title><link>https://example.com/story</link></item>
  <item><title>B</title><link>https://example.com/other</link></item>
</channel></rss>"""

    def test_dedup_by_url_drops_exact_duplicate(self):
        items = ingest.parse_feed(self._DUPE_RSS, _SOURCE, "https://example.com/feed", "2026-08-13T00:00:00Z")
        # parse_feed returns all 3 (it does not dedup)
        self.assertEqual(len(items), 3)

        kept, dropped = ingest.dedup_by_url(items)
        self.assertEqual(dropped, 1)
        self.assertEqual(len(kept), 2)

    def test_dedup_preserves_first_occurrence(self):
        items = ingest.parse_feed(self._DUPE_RSS, _SOURCE, "https://example.com/feed", "2026-08-13T00:00:00Z")
        kept, _ = ingest.dedup_by_url(items)
        self.assertEqual(kept[0]["item_title"], "A")

    def test_dedup_keeps_distinct_urls(self):
        items = ingest.parse_feed(self._DUPE_RSS, _SOURCE, "https://example.com/feed", "2026-08-13T00:00:00Z")
        kept, _ = ingest.dedup_by_url(items)
        urls = {i["item_url"] for i in kept}
        self.assertIn("https://example.com/story", urls)
        self.assertIn("https://example.com/other", urls)

    def test_dedup_no_duplicates_unchanged(self):
        items = ingest.parse_feed(_RSS_BYTES, _SOURCE, "https://example.com/feed", "2026-08-13T00:00:00Z")
        kept, dropped = ingest.dedup_by_url(items)
        self.assertEqual(dropped, 0)
        self.assertEqual(len(kept), len(items))


# ---------------------------------------------------------------------------
# 10. Registry loading — tier-based filtering
# ---------------------------------------------------------------------------

def _make_registry_yaml(sources: list[dict]) -> str:
    import yaml as _yaml
    return _yaml.dump({"sources": sources})


class TestRegistryLoading(unittest.TestCase):

    def _write_registry(self, tmpdir, sources):
        import yaml as _yaml
        p = Path(tmpdir) / "ainwa-discovery.yml"
        p.write_text(_yaml.dump({"sources": sources}), encoding="utf-8")
        return p

    def _make_src(self, **overrides):
        base = {
            "id": "SRC-T01", "name": "Test", "domain": "test.com",
            "priority": "high", "tier": 1, "access": "free",
            "role": "Original Reporting", "citation_allowed": "yes",
            "reliability": "high", "enabled": True, "feed_url": "https://test.com/feed",
        }
        base.update(overrides)
        return base

    def test_tier1_sources_loaded(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = self._write_registry(d, [self._make_src(tier=1)])
            result = ingest.load_registry(p)
        self.assertEqual(len(result), 1)

    def test_tier2_sources_loaded(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = self._write_registry(d, [self._make_src(tier=2)])
            result = ingest.load_registry(p)
        self.assertEqual(len(result), 1)

    def test_tier3_sources_not_loaded(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = self._write_registry(d, [self._make_src(tier=3)])
            result = ingest.load_registry(p)
        self.assertEqual(result, [])

    def test_tier4_sources_not_loaded(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = self._write_registry(d, [self._make_src(tier=4)])
            result = ingest.load_registry(p)
        self.assertEqual(result, [])

    def test_disabled_source_not_loaded(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = self._write_registry(d, [self._make_src(tier=1, enabled=False)])
            result = ingest.load_registry(p)
        self.assertEqual(result, [])

    def test_source_without_tier_not_loaded(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            src = self._make_src()
            src.pop("tier")
            p = self._write_registry(d, [src])
            result = ingest.load_registry(p)
        self.assertEqual(result, [])

    def test_tier1_and_tier3_only_tier1_loaded(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = self._write_registry(d, [
                self._make_src(id="SRC-T01", tier=1),
                self._make_src(id="SRC-T03", tier=3),
            ])
            result = ingest.load_registry(p)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "SRC-T01")


# ---------------------------------------------------------------------------
# 11. Source fields — tier and access propagation
# ---------------------------------------------------------------------------

class TestSourceFields(unittest.TestCase):

    def test_source_tier_propagated(self):
        fields = ingest._source_fields(
            _SOURCE, "https://test.com/feed", "2026-08-17T00:00:00Z", "rss"
        )
        self.assertEqual(fields["source_tier"], 1)

    def test_source_access_propagated(self):
        fields = ingest._source_fields(
            _SOURCE, "https://test.com/feed", "2026-08-17T00:00:00Z", "rss"
        )
        self.assertEqual(fields["source_access"], "free")

    def test_source_tier_none_when_absent(self):
        src = dict(_SOURCE)
        src.pop("tier", None)
        fields = ingest._source_fields(src, "https://test.com/feed", "2026-08-17T00:00:00Z", "rss")
        self.assertIsNone(fields["source_tier"])

    def test_source_access_unknown_when_absent(self):
        src = dict(_SOURCE)
        src.pop("access", None)
        fields = ingest._source_fields(src, "https://test.com/feed", "2026-08-17T00:00:00Z", "rss")
        self.assertEqual(fields["source_access"], "unknown")


if __name__ == "__main__":
    unittest.main(verbosity=2)
