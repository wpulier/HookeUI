#!/usr/bin/env python3
"""The next release's build number (CFBundleVersion, what Sparkle compares): higher than every build any channel's feed
has published, and never lower than the commit count build.sh used before channels (packaging/macos/README.md
"Channels"). A commit count alone is no order across branches; the feeds are the release sequence, and releases are
serialized (one release workflow at a time), so two releases never get the same number.

    release_number.py --commit-count 3412 --feed URL_OR_FILE [--feed ...]

A feed that answers 404 is a channel with no release yet. Any other failure stops: a number that might be lower than a
published one would never be offered. Standard library only.
"""
from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

SPARKLE = "{http://www.andymatuschak.org/xml-namespaces/sparkle}"


def published(feed: bytes) -> list[int]:
    """Every build number a feed offers."""
    root = ET.fromstring(feed)
    builds = []
    for item in root.iter("item"):
        value = item.findtext(SPARKLE + "version")
        if value is None:
            enclosure = item.find("enclosure")
            value = enclosure.get(SPARKLE + "version") if enclosure is not None else None
        if value is None or not value.strip().isdigit():
            raise ValueError(f"an item without a numeric sparkle:version: {value!r}")
        builds.append(int(value))
    return builds


def read(source: str, timeout: float = 30) -> bytes | None:
    """A feed's bytes, or None when it has never been published (404)."""
    if "://" not in source:
        path = Path(source)
        return path.read_bytes() if path.exists() else None
    try:
        with urllib.request.urlopen(urllib.request.Request(source, headers={"User-Agent": "hookeui-release"}),
                                    timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def next_build(commit_count: int, feeds: list[bytes | None]) -> int:
    highest = max((b for feed in feeds if feed is not None for b in published(feed)), default=0)
    return max(commit_count, highest + 1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--commit-count", type=int, required=True)
    ap.add_argument("--feed", action="append", default=[], help="every channel's feed (URL or file)")
    a = ap.parse_args(argv)
    if not a.feed:
        ap.error("give every channel's feed: the number must exceed all of them")
    feeds = []
    for source in a.feed:
        try:
            feed = read(source)
            if feed is not None:
                published(feed)
        except (OSError, ValueError, ET.ParseError) as e:
            print(f"release_number.py: could not read {source}: {e}. No number given.", file=sys.stderr)
            return 1
        print(f"{source}: {'no release yet' if feed is None else max(published(feed), default='empty')}",
              file=sys.stderr)
        feeds.append(feed)
    print(next_build(a.commit_count, feeds))
    return 0


if __name__ == "__main__":
    sys.exit(main())
