"""Fetching reference pictures from the web, safely.

URLs come from web search results and model output, so they are untrusted:
- http(s) only; every address the host resolves to must be public (no loopback, private,
  link-local, multicast or reserved ranges), checked again on each redirect (at most 3);
- 8 s timeout, 8 MB cap, the response must be an image;
- the picture is decoded and re-encoded as JPEG (at most 2048 px), so nothing but pixels
  is kept.
Wikimedia Commons file pages go through the Commons API first, which gives the image URL
and its licence and author for the credit.

Pages' text (fetch_text) is fetched the same way, for checking a researcher's points against
the pages it cites (claims.py): HTML or plain text only, reduced to words. That text is only
searched by code for a point's key terms; it is never given to a model.
"""
from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import ipaddress
import re
import socket
import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser

import cv2
import httpx
import numpy as np

from .provenance import is_ai, source_type

MAX_BYTES = 8 * 1024 * 1024
MAX_REDIRECTS = 3
TIMEOUT_S = 8.0
MAX_SIDE = 2048
USER_AGENT = "ObservationUI/0.1 (research assistant; developer preview)"


class FetchRefused(Exception):
    """Why a fetch was refused or failed. `transient`: a reason that may pass (a timeout, a lost connection, HTTP
    408, 429 or 5xx), so the address may be worth one more try later; any other (another HTTP 4xx, not an image,
    a non-public address...) fails again however often it is asked."""

    def __init__(self, message: str = "", *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


def _status(code: int) -> FetchRefused:
    return FetchRefused(f"HTTP {code}", transient=code in (408, 429) or code >= 500)


def _failed(exc: Exception) -> FetchRefused:
    """A network failure, said by its kind: a timeout or a lost connection may pass (httpx's transport errors)."""
    return FetchRefused(type(exc).__name__, transient=isinstance(exc, (httpx.TransportError, TimeoutError)))


@dataclass
class FetchedImage:
    jpeg: bytes
    width: int
    height: int
    url: str            # where the pixels came from
    page: str           # the page a person would open for the credit
    title: str = ""
    licence: str = ""
    author: str = ""

    @property
    def sha1(self) -> str:
        return hashlib.sha1(self.jpeg).hexdigest()

    def credit(self) -> str:
        bits = [b for b in (self.author, self.licence) if b]
        host = urllib.parse.urlparse(self.page).hostname or ""
        return ", ".join(bits + [host]) if bits else host


def _public(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise FetchRefused(f"cannot resolve {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise FetchRefused(f"{host} resolves to a non-public address")


def check_url(url: str) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise FetchRefused("only http(s) URLs")
    if parsed.username or parsed.password:
        raise FetchRefused("no credentials in URLs")
    _public(parsed.hostname)
    return parsed


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text or "")).strip()[:120]


async def _get(client: httpx.AsyncClient, url: str, accept_image: bool) -> tuple[httpx.Response, bytes, str]:
    """GET with manual redirects (each re-checked) and a size cap."""
    for _ in range(MAX_REDIRECTS + 1):
        await asyncio.get_running_loop().run_in_executor(None, check_url, url)
        async with client.stream("GET", url) as response:
            if response.is_redirect:
                url = urllib.parse.urljoin(url, response.headers.get("location", ""))
                continue
            if response.status_code >= 400:
                raise _status(response.status_code)
            kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if accept_image and not kind.startswith("image/"):
                raise FetchRefused(f"not an image ({kind or 'no type'})")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise FetchRefused("larger than 8 MB")
            return response, bytes(body), url
    raise FetchRefused("too many redirects")


def _reencode(data: bytes) -> tuple[bytes, int, int]:
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise FetchRefused("not a picture that can be decoded")
    h, w = image.shape[:2]
    scale = min(1.0, MAX_SIDE / max(h, w))
    if scale < 1.0:
        image = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise FetchRefused("could not re-encode")
    return jpeg.tobytes(), image.shape[1], image.shape[0]


async def _commons(client: httpx.AsyncClient, url: str) -> dict | None:
    """A Commons file page -> {image url, page, title, licence, author}, or None."""
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname not in ("commons.wikimedia.org", "commons.m.wikimedia.org") or "/wiki/" not in parsed.path:
        return None
    title = urllib.parse.unquote(parsed.path.split("/wiki/", 1)[1])
    if not title.lower().startswith("file:"):
        return None
    response, body, _ = await _get(client, "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query", "titles": title, "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": 1600,
        "format": "json"}), accept_image=False)
    pages = (httpx.Response(200, content=body).json().get("query") or {}).get("pages") or {}
    page = next(iter(pages.values()), {})
    info = (page.get("imageinfo") or [None])[0]
    if "missing" in page or not info:
        raise FetchRefused("no such file on Wikimedia Commons")
    meta = info.get("extmetadata") or {}
    return {"url": info.get("thumburl") or info["url"], "page": info.get("descriptionurl") or url,
            "title": title[5:].rsplit(".", 1)[0].replace("_", " "),
            "licence": _strip_html((meta.get("LicenseShortName") or {}).get("value", "")),
            "author": _strip_html((meta.get("Artist") or {}).get("value", ""))}


async def fetch_image(url: str, client: httpx.AsyncClient | None = None) -> FetchedImage:
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False, headers={"User-Agent": USER_AGENT})
    try:
        async with asyncio.timeout(TIMEOUT_S * 2):
            meta = await _commons(client, url)
            image_url = meta["url"] if meta else url
            _, body, final = await _get(client, image_url, accept_image=True)
            # Read what the file says made it before re-encoding drops it: a picture made by an
            # AI model is never a reference for what a real thing looks like (provenance.py).
            made_by = source_type(body)
            if is_ai(made_by):
                raise FetchRefused(f"the picture says it was made by an AI model ({made_by}); not used as a reference")
            jpeg, w, h = _reencode(body)
            if meta:
                return FetchedImage(jpeg, w, h, final, meta["page"], meta["title"], meta["licence"], meta["author"])
            return FetchedImage(jpeg, w, h, final, url)
    except (httpx.HTTPError, TimeoutError) as exc:
        raise _failed(exc) from exc
    finally:
        if own:
            await client.aclose()


async def commons_search(query: str, limit: int = 3, client: httpx.AsyncClient | None = None) -> list[str]:
    """File pages on Wikimedia Commons matching a query; a failed search raises FetchRefused."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False, headers={"User-Agent": USER_AGENT})
    try:
        _, body, _ = await _get(client, "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode({
            "action": "query", "list": "search", "srnamespace": 6, "srsearch": f"{query} filetype:bitmap",
            "srlimit": limit * 2, "format": "json"}), accept_image=False)
        data = httpx.Response(200, content=body).json()
        query_result = data.get("query") if isinstance(data, dict) else None
        hits = query_result.get("search") if isinstance(query_result, dict) else None
        if not isinstance(hits, list) or any(not isinstance(hit, dict) or not isinstance(hit.get("title"), str)
                                             for hit in hits):
            raise FetchRefused("Commons returned no usable search response")
        titles = [h["title"] for h in hits if h.get("title", "").lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
        return ["https://commons.wikimedia.org/wiki/" + urllib.parse.quote(t.replace(" ", "_")) for t in titles[:limit]]
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise FetchRefused(f"Commons search failed ({type(exc).__name__})") from exc
    finally:
        if own:
            await client.aclose()


TEXT_TYPES = ("text/html", "text/plain", "application/xhtml+xml")
MAX_TEXT_CHARS = 2_000_000


async def page_images(url: str, client: httpx.AsyncClient | None = None) -> list[str]:
    """Bounded image candidates actually present on a public source page.

    No script execution or recursive crawling. These are candidates, not verified
    photographs; the caller must inspect their downloaded pixels for relevance.
    Each subsequent image fetch independently checks its address and redirects.
    """
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False,
                                       headers={"User-Agent": USER_AGENT})
    try:
        async with asyncio.timeout(TIMEOUT_S * 2):
            response, body, final = await _get(client, url, accept_image=False)
        kind = response.headers.get('content-type', '').split(';')[0].strip().lower()
        if kind.startswith('image/'):
            return [final]
        if kind not in TEXT_TYPES:
            raise FetchRefused('source page is not readable HTML')
        preferred, inline = [], []

        class Images(HTMLParser):
            def handle_starttag(self, tag, attrs):
                attributes = dict(attrs)
                if tag == 'meta' and (attributes.get('property') or attributes.get('name') or '').lower() in (
                        'og:image', 'og:image:url', 'twitter:image', 'twitter:image:src'):
                    preferred.append(attributes.get('content'))
                elif tag == 'img' and len(inline) < 100:
                    inline.append(attributes.get('data-src') or attributes.get('src'))

        Images().feed(body.decode(response.encoding or 'utf-8', errors='replace'))
        result = []
        for candidate in preferred + inline:
            if not candidate:
                continue
            absolute = urllib.parse.urljoin(final, candidate)
            if urllib.parse.urlparse(absolute).scheme in ('http', 'https') and absolute not in result:
                result.append(absolute)
            if len(result) == 3:
                break
        return result
    except (httpx.HTTPError, TimeoutError) as exc:
        raise _failed(exc) from exc
    finally:
        if own:
            await client.aclose()


def page_words(body: str) -> str:
    """A web page reduced to its words: scripts, styles and tags gone, entities decoded."""
    text = re.sub(r"(?is)<(script|style|noscript|template)\b.*?</\1\s*>", " ", body or "")
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(html_lib.unescape(text).split())[:MAX_TEXT_CHARS]


async def fetch_text(url: str, client: httpx.AsyncClient | None = None) -> str:
    """The words of a web page (public addresses, re-checked redirects, 8 MB cap, as for pictures).
    Raises FetchRefused for anything that is not a readable page (a PDF, an image, an error)."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False, headers={"User-Agent": USER_AGENT})
    try:
        async with asyncio.timeout(TIMEOUT_S * 2):
            response, body, _ = await _get(client, url, accept_image=False)
        kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if kind not in TEXT_TYPES:
            raise FetchRefused(f"not a text page ({kind or 'no type'})")
        return page_words(body.decode(response.encoding or "utf-8", errors="replace"))
    except (httpx.HTTPError, TimeoutError) as exc:
        raise _failed(exc) from exc
    finally:
        if own:
            await client.aclose()
