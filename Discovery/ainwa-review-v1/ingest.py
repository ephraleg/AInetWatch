#!/usr/bin/env python3
"""AINWA Discovery Ingestion v1

Reads enabled Tier 1 and Tier 2 sources from ainwa-discovery.yml, fetches their
RSS/Atom feeds, and writes normalized raw discovery items to
data/raw-discovery.json.

Security model
--------------
- Only feed_url values from the registry are fetched.  Item URLs found inside
  feeds are stored as strings but never fetched.
- All fetched content is treated as untrusted.
- SSRF protections: scheme enforcement, DNS pre-resolution, per-hop redirect
  validation, hard block of private/loopback/link-local/multicast/metadata IPs.
- Response-size limit: if the limit is exceeded the entire response is rejected;
  no partial/truncated XML is ever parsed.
- Timeouts on every fetch.

Does NOT: call Claude/Grok/Gemini, write to candidate-queue.json, deduplicate
across runs, fetch article pages, or publish anything.

External dependency: PyYAML (pyyaml) — the only non-stdlib import.
Run:  pip install pyyaml
Then: python3 ingest.py
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    print("[AINWA] PyYAML is required: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TIMEOUT_SECONDS = 15
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB — hard rejection if exceeded
MAX_REDIRECTS = 5
USER_AGENT = "AInetWatch-Ingest/1.0"

# ---------------------------------------------------------------------------
# SSRF protection — blocked IP ranges
# ---------------------------------------------------------------------------
_BLOCKED_NETS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    # IPv4
    ipaddress.ip_network("0.0.0.0/8"),          # "This" network
    ipaddress.ip_network("10.0.0.0/8"),          # RFC 1918 private
    ipaddress.ip_network("100.64.0.0/10"),       # Carrier-grade NAT (RFC 6598)
    ipaddress.ip_network("127.0.0.0/8"),         # Loopback
    ipaddress.ip_network("169.254.0.0/16"),      # Link-local / cloud metadata
    ipaddress.ip_network("172.16.0.0/12"),       # RFC 1918 private
    ipaddress.ip_network("192.0.0.0/24"),        # IETF protocol assignments
    ipaddress.ip_network("192.0.2.0/24"),        # TEST-NET-1 (documentation)
    ipaddress.ip_network("192.168.0.0/16"),      # RFC 1918 private
    ipaddress.ip_network("198.18.0.0/15"),       # Benchmarking (RFC 2544)
    ipaddress.ip_network("198.51.100.0/24"),     # TEST-NET-2 (documentation)
    ipaddress.ip_network("203.0.113.0/24"),      # TEST-NET-3 (documentation)
    ipaddress.ip_network("224.0.0.0/4"),         # Multicast
    ipaddress.ip_network("240.0.0.0/4"),         # Reserved
    ipaddress.ip_network("255.255.255.255/32"),  # Broadcast
    # IPv6
    ipaddress.ip_network("::1/128"),             # Loopback
    ipaddress.ip_network("::/128"),              # Unspecified
    ipaddress.ip_network("fc00::/7"),            # Unique local (RFC 4193)
    ipaddress.ip_network("fe80::/10"),           # Link-local
    ipaddress.ip_network("ff00::/8"),            # Multicast
    ipaddress.ip_network("::ffff:0:0/96"),       # IPv4-mapped (belt-and-suspenders)
]


def is_blocked_ip(addr_str: str) -> bool:
    """Return True if the address is in a blocked range."""
    try:
        addr = ipaddress.ip_address(addr_str)
    except ValueError:
        return True  # unparseable → block
    # Unwrap IPv4-mapped IPv6 before range checks
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    for net in _BLOCKED_NETS:
        try:
            if addr in net:
                return True
        except TypeError:
            pass  # mixed-version comparison — skip this network entry
    return False


def resolve_and_check(hostname: str) -> None:
    """Resolve hostname; raise ValueError if any returned IP is blocked."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValueError(f"DNS resolution failed for {hostname!r}: {exc}") from exc
    for _family, _type, _proto, _canon, sockaddr in infos:
        ip = sockaddr[0]
        if is_blocked_ip(ip):
            raise ValueError(
                f"Host {hostname!r} resolves to blocked address {ip!r}"
            )


def validate_url(url: str) -> urllib.parse.ParseResult:
    """Parse and validate URL: must be http/https with a non-empty hostname.

    Raises ValueError on scheme violations or missing hostname.
    Does NOT resolve DNS — call resolve_and_check() separately.
    """
    if not isinstance(url, str) or not url:
        raise ValueError("URL must be a non-empty string")
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError as exc:
        raise ValueError(f"Malformed URL {url!r}: {exc}") from exc
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"Disallowed scheme {parsed.scheme!r} in {url!r}; only http/https allowed"
        )
    if not parsed.hostname:
        raise ValueError(f"URL has no hostname: {url!r}")
    return parsed


# ---------------------------------------------------------------------------
# SSRF-blocking redirect handler
# ---------------------------------------------------------------------------

class SSRFRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validate scheme and IP on every redirect hop."""

    max_redirections = MAX_REDIRECTS

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp,
        code: int,
        msg: str,
        headers,
        newurl: str,
    ) -> urllib.request.Request:
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"Redirect to disallowed scheme {parsed.scheme!r}: {newurl!r}"
            )
        if not parsed.hostname:
            raise ValueError(f"Redirect to URL with no hostname: {newurl!r}")
        resolve_and_check(parsed.hostname)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# ---------------------------------------------------------------------------
# Safe HTTP fetch
# ---------------------------------------------------------------------------

def safe_fetch(
    url: str,
    *,
    max_bytes: int = MAX_RESPONSE_BYTES,
    timeout: int = TIMEOUT_SECONDS,
) -> tuple[bytes, str]:
    """Fetch url with SSRF protections and a hard response-size limit.

    Returns (body_bytes, content_type_string).

    Raises ValueError  — security violation or oversized response.
    Raises urllib.error.URLError / OSError — network failure.
    """
    parsed = validate_url(url)
    resolve_and_check(parsed.hostname)

    opener = urllib.request.build_opener(SSRFRedirectHandler())
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    with opener.open(req, timeout=timeout) as resp:
        content_type: str = resp.headers.get("Content-Type", "")
        # Read one byte past the limit to detect over-limit responses.
        data = resp.read(max_bytes + 1)

    if len(data) > max_bytes:
        raise ValueError(
            f"Response size {len(data):,} bytes exceeds {max_bytes:,}-byte limit; "
            "rejected (partial XML is not parsed)"
        )
    return data, content_type


# ---------------------------------------------------------------------------
# Feed parsing — RSS 2.0 and Atom 1.0
# ---------------------------------------------------------------------------

_ATOM_NS = "http://www.w3.org/2005/Atom"
_CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"


def _guess_desc_format(text: str | None) -> str | None:
    """'html' if text looks like HTML, 'text' for plain, None if absent/empty."""
    if not text or not text.strip():
        return None
    t = text.strip()
    if (
        t.startswith("<")
        or "</" in t
        or "<br" in t.lower()
        or "&#" in t
        or "&lt;" in t
        or "&amp;" in t
    ):
        return "html"
    return "text"


def _make_item_id(source_id: str, item_url: str) -> str:
    """Deterministic, stable item ID: raw-{source_id}-{sha1(url)[:8]}."""
    h = hashlib.sha1(item_url.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    return f"raw-{source_id}-{h}"


def _source_fields(source: dict, feed_url: str, fetched_at: str, method: str) -> dict:
    return {
        "source_id": source["id"],
        "source_name": source["name"],
        "source_domain": source["domain"],
        "source_priority": source["priority"],
        "source_role": source.get("role", ""),
        "source_tier": source.get("tier"),
        "source_access": source.get("access", "unknown"),
        "source_citation_allowed": str(source.get("citation_allowed", "no")),
        "source_reliability": source.get("reliability", ""),
        "fetch_method": method,
        "feed_url": feed_url,
        "fetched_at": fetched_at,
    }


def _parse_rss(
    root: ET.Element,
    source: dict,
    feed_url: str,
    fetched_at: str,
) -> list[dict]:
    items: list[dict] = []
    channel = root.find("channel") if root.tag == "rss" else root
    if channel is None:
        return items
    for el in channel.findall("item"):
        link = (el.findtext("link") or "").strip()
        if not link:
            continue
        title = (el.findtext("title") or "").strip() or None
        pub = (el.findtext("pubDate") or "").strip() or None
        # Prefer content:encoded; fall back to description
        content = el.findtext(f"{{{_CONTENT_NS}}}encoded") or ""
        desc = el.findtext("description") or ""
        raw = (content or desc).strip() or None
        items.append({
            "item_id": _make_item_id(source["id"], link),
            **_source_fields(source, feed_url, fetched_at, "rss"),
            "item_url": link,
            "item_title": title,
            "item_published": pub,
            "feed_description": raw,
            "feed_description_format": _guess_desc_format(raw),
        })
    return items


def _parse_atom(
    root: ET.Element,
    source: dict,
    feed_url: str,
    fetched_at: str,
) -> list[dict]:
    ns = _ATOM_NS
    items: list[dict] = []
    for entry in root.findall(f"{{{ns}}}entry"):
        # Prefer rel="alternate"; fall back to first link with an href
        link = ""
        for link_el in entry.findall(f"{{{ns}}}link"):
            href = link_el.get("href", "")
            if not href:
                continue
            if link_el.get("rel", "alternate") == "alternate":
                link = href
                break
            if not link:
                link = href
        if not link:
            continue

        title_el = entry.find(f"{{{ns}}}title")
        title = (title_el.text or "").strip() or None if title_el is not None else None

        pub_el = entry.find(f"{{{ns}}}published")
        if pub_el is None:
            pub_el = entry.find(f"{{{ns}}}updated")
        pub = (pub_el.text or "").strip() or None if pub_el is not None else None

        raw: str | None = None
        desc_format: str | None = None
        for tag in (f"{{{ns}}}content", f"{{{ns}}}summary"):
            el = entry.find(tag)
            if el is not None and el.text:
                raw = el.text.strip() or None
                atom_type = el.get("type", "text")
                if raw:
                    desc_format = "html" if atom_type in ("html", "xhtml") else "text"
                break

        items.append({
            "item_id": _make_item_id(source["id"], link),
            **_source_fields(source, feed_url, fetched_at, "atom"),
            "item_url": link,
            "item_title": title,
            "item_published": pub,
            "feed_description": raw,
            "feed_description_format": desc_format,
        })
    return items


def parse_feed(
    data: bytes,
    source: dict,
    feed_url: str,
    fetched_at: str,
) -> list[dict]:
    """Parse RSS 2.0 or Atom 1.0 bytes into raw item dicts.

    Raises ValueError on XML parse error.
    Items without a URL are silently skipped.
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"XML parse error: {exc}") from exc

    if root.tag == f"{{{_ATOM_NS}}}feed":
        return _parse_atom(root, source, feed_url, fetched_at)
    return _parse_rss(root, source, feed_url, fetched_at)


# ---------------------------------------------------------------------------
# Exact-URL deduplication (within one run only)
# ---------------------------------------------------------------------------

def dedup_by_url(items: list[dict]) -> tuple[list[dict], int]:
    """Remove items with a duplicate item_url.  Returns (kept, dropped_count)."""
    seen: set[str] = set()
    kept: list[dict] = []
    dropped = 0
    for item in items:
        url = item.get("item_url", "")
        if url in seen:
            dropped += 1
        else:
            seen.add(url)
            kept.append(item)
    return kept, dropped


# ---------------------------------------------------------------------------
# Registry loading
# ---------------------------------------------------------------------------

def load_registry(path: Path) -> list[dict]:
    """Return all enabled Tier 1 and Tier 2 sources from ainwa-discovery.yml."""
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return [
        s
        for s in data.get("sources", [])
        if s.get("enabled") is True
        and s.get("tier") in (1, 2)
    ]


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
# Main ingestion run
# ---------------------------------------------------------------------------

def run_ingestion(registry_path: Path, output_path: Path) -> dict:
    """Fetch all configured High-priority feeds and write raw-discovery.json.

    Returns the output dict (also written to disk atomically).
    """
    run_id = _now_iso()
    sources = load_registry(registry_path)

    all_items: list[dict] = []
    errors: list[dict] = []
    skipped: list[dict] = []
    seen_urls: set[str] = set()  # cross-source exact-URL dedup within this run

    print(f"[AINWA] run_id={run_id}")
    print(f"[AINWA] {len(sources)} enabled Tier 1/2 sources in registry")

    for src in sources:
        src_id = src.get("id", "?")
        src_name = src.get("name", "?")
        feed_url = src.get("feed_url")

        if not feed_url:
            reason = "no feed_url — deferred to deterministic page-check path"
            print(f"[SKIP]  {src_id} {src_name}: {reason}")
            skipped.append({
                "source_id": src_id,
                "source_name": src_name,
                "reason": reason,
            })
            continue

        fetched_at = _now_iso()
        print(f"[FETCH] {src_id} {src_name}")
        print(f"        {feed_url}")

        try:
            validate_url(feed_url)  # scheme/hostname check before any network call
            data, content_type = safe_fetch(feed_url)
            items = parse_feed(data, src, feed_url, fetched_at)

            # Dedup against URLs already seen this run (cross-source)
            new_items: list[dict] = []
            run_dupes = 0
            for item in items:
                url = item["item_url"]
                if url in seen_urls:
                    run_dupes += 1
                else:
                    seen_urls.add(url)
                    new_items.append(item)

            all_items.extend(new_items)
            dup_note = (
                f"  ({run_dupes} exact-URL dup{'s' if run_dupes != 1 else ''} dropped)"
                if run_dupes
                else ""
            )
            print(f"        {len(new_items)} items{dup_note}")

        except Exception as exc:
            msg = str(exc)
            print(f"[ERROR] {src_id} {src_name}: {msg}", file=sys.stderr)
            errors.append({
                "source_id": src_id,
                "source_name": src_name,
                "feed_url": feed_url,
                "error": msg,
                "at": fetched_at,
            })

    output = {
        "version": 1,
        "run_id": run_id,
        "tier_filter": "1,2",
        "item_count": len(all_items),
        "error_count": len(errors),
        "skipped_count": len(skipped),
        "items": all_items,
        "errors": errors,
        "skipped": skipped,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(".json.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(output, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, output_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    print(
        f"\n[AINWA] {len(all_items)} items, "
        f"{len(errors)} error{'s' if len(errors) != 1 else ''}, "
        f"{len(skipped)} skipped  →  {output_path}"
    )
    return output


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="AINWA Discovery Ingestion v1")
    parser.add_argument(
        "--registry",
        default=str(ROOT / "ainwa-discovery.yml"),
        help="Path to ainwa-discovery.yml (default: same directory as this script)",
    )
    parser.add_argument(
        "--output",
        default=str(DATA_DIR / "raw-discovery.json"),
        help="Output path for raw-discovery.json",
    )
    args = parser.parse_args()
    run_ingestion(Path(args.registry), Path(args.output))


if __name__ == "__main__":
    main()
