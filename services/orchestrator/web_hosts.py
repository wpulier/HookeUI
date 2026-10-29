"""The few web hosts Hooke shows inside the app (docs/architecture/on-screen.md "Web pages and videos").

A web page or video from the web opens in a sandboxed window of its own (apps/studio/src/web-window.ts): scripts run in
the frame's own origin, never the app's, it can never navigate the app, and its site and link are always shown. Only
these hosts are framed; every other page is said with its link instead. The studio keeps the same list (web-window.ts
WEB_HOSTS), and both check it: the server before it sends a window (presentations.SourceRef, web_window.py), the
studio before it frames one.

- YouTube, framed only as its privacy-enhanced embed (`www.youtube-nocookie.com/embed/<id>`), from any YouTube link;
- Vimeo, as its player (`player.vimeo.com/video/<id>`);
- Wikipedia and Wikimedia Commons pages.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}")
VIMEO_ID = re.compile(r"\d{1,12}")
YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtube-nocookie.com",
                           "youtube-nocookie.com", "music.youtube.com"})
PAGE_HOSTS = ("wikipedia.org", "commons.wikimedia.org")  # *.wikipedia.org and Commons


def _page_host(host: str) -> bool:
    return host == "commons.wikimedia.org" or (host.endswith(".wikipedia.org") and host.count(".") == 2)


def embed(url: str) -> dict | None:
    """The framed form of a link Hooke shows inside the app, or None when its host is not one it frames.

    Returns {kind: "video" | "web-page", url: what the frame loads, page: the link to show, site: its site, id}."""
    if not isinstance(url, str) or len(url) > 2048:
        return None
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    try:
        host, port = (parsed.hostname or "").lower(), parsed.port
    except ValueError:
        return None
    if parsed.scheme not in ("https", "http") or parsed.username or parsed.password or port not in (None, 443):
        return None
    if host in YOUTUBE_HOSTS:
        video = None
        if host == "youtu.be":
            video = parsed.path.strip("/").split("/")[0]
        elif parsed.path == "/watch":
            video = (parse_qs(parsed.query).get("v") or [""])[0]
        else:
            parts = parsed.path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] in ("embed", "shorts", "live", "v"):
                video = parts[1]
        if not video or not YOUTUBE_ID.fullmatch(video):
            return None
        return {"kind": "video", "url": f"https://www.youtube-nocookie.com/embed/{video}",
                "page": f"https://www.youtube.com/watch?v={video}", "site": "youtube.com", "id": video}
    if host in ("vimeo.com", "www.vimeo.com", "player.vimeo.com"):
        parts = parsed.path.strip("/").split("/")
        video = parts[1] if host == "player.vimeo.com" and len(parts) >= 2 and parts[0] == "video" else parts[0]
        if not VIMEO_ID.fullmatch(video or ""):
            return None
        return {"kind": "video", "url": f"https://player.vimeo.com/video/{video}",
                "page": f"https://vimeo.com/{video}", "site": "vimeo.com", "id": video}
    if _page_host(host) and parsed.scheme == "https" and parsed.path.startswith("/wiki/"):
        page = f"https://{host}{parsed.path}"
        return {"kind": "web-page", "url": page, "page": page, "site": host, "id": parsed.path[len("/wiki/"):][:200]}
    return None


def framed(url: str, kind: str) -> bool:
    """Whether `url` is exactly a frame address Hooke loads for `kind` (an embed `embed` made, never a page that only
    shares a host): the server's check of a web source (presentations.SourceRef) and of a web window."""
    shown = embed(url)
    return shown is not None and shown["kind"] == kind and shown["url"] == url
