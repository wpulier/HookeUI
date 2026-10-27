#!/usr/bin/env python3
"""The Sparkle appcast for one release: the update every installed HookeUI reads (SUFeedURL), with the dmg's EdDSA
signature, so an app only installs what this key signed.

    appcast.py --dmg HookeUI-0.1.1.dmg --version 0.1.1 --build 1234 --url https://.../HookeUI-0.1.1.dmg \
               --signature BASE64 --notes notes.md --out appcast.xml
"""
from __future__ import annotations

import argparse
import html
import os
import re
from email.utils import formatdate
from pathlib import Path


def _inline(text: str) -> str:
    """One line's words as HTML: escaped, then **bold** and `code` (the notes' only inline marks)."""
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)


def notes_html(text: str) -> str:
    """Release notes in Markdown as the small HTML Sparkle's update offer shows: "#" headings, "- " lists whose wrapped
    lines (indented) continue their item, paragraphs whose wrapped lines join, and **bold** and `code`. Before 0.4.0
    every wrapped line was a paragraph of its own and ** showed as typed (the 0.3.0 and first 0.4.0 offers)."""
    out: list[str] = []
    items: list[str] = []
    paragraph: list[str] = []

    def close_paragraph() -> None:
        if paragraph:
            out.append(f"<p>{_inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def close_list() -> None:
        if items:
            out.append("<ul>" + "".join(f"<li>{_inline(item)}</li>" for item in items) + "</ul>")
            items.clear()

    for raw in text.strip().splitlines():
        line = raw.strip()
        if not line:
            close_paragraph()
            close_list()
            continue
        if line.startswith(("- ", "* ")):
            close_paragraph()
            items.append(line[2:].strip())
            continue
        if items and raw[:1].isspace():  # a list item's wrapped line
            items[-1] += " " + line
            continue
        close_list()
        if line.startswith("#"):
            close_paragraph()
            out.append(f"<h3>{_inline(line.lstrip('#').strip())}</h3>")
            continue
        paragraph.append(line)
    close_paragraph()
    close_list()
    return "".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    for name in ("dmg", "version", "build", "url", "signature", "out"):
        ap.add_argument(f"--{name}", required=True)
    ap.add_argument("--notes", type=Path)
    ap.add_argument("--title", default="HookeUI", help="the app's name: HookeUI, HookeUI Beta (channels.json)")
    ap.add_argument("--min-os", default="14.0")
    a = ap.parse_args()
    if not re.fullmatch(r"\d+(\.\d+){1,2}", a.version) or not a.build.isdigit():
        raise SystemExit("version is like 0.1.1 and build is a number")
    title = html.escape(a.title)
    notes = notes_html(a.notes.read_text()) if a.notes and a.notes.is_file() else f"<p>{title} {html.escape(a.version)}</p>"
    feed = f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
  <channel>
    <title>{title}</title>
    <link>https://hookeui.org</link>
    <description>Updates to {title}</description>
    <language>en</language>
    <item>
      <title>{title} {html.escape(a.version)}</title>
      <pubDate>{formatdate(usegmt=True)}</pubDate>
      <sparkle:version>{a.build}</sparkle:version>
      <!-- Existing releases allowed silent installation. Require an interactive offer for every older build. -->
      <sparkle:minimumAutoupdateVersion>{a.build}</sparkle:minimumAutoupdateVersion>
      <sparkle:shortVersionString>{html.escape(a.version)}</sparkle:shortVersionString>
      <sparkle:minimumSystemVersion>{html.escape(a.min_os)}</sparkle:minimumSystemVersion>
      <description><![CDATA[{notes}]]></description>
      <enclosure url="{html.escape(a.url, quote=True)}" length="{os.path.getsize(a.dmg)}"
                 type="application/octet-stream" sparkle:edSignature="{html.escape(a.signature, quote=True)}"/>
    </item>
  </channel>
</rss>
"""
    Path(a.out).write_text(feed)
    print(f"{a.out}: {a.title} {a.version} ({a.build})")


if __name__ == "__main__":
    main()
