"""Finding a video on the web for what was asked (`show_web`'s video; docs/architecture/on-screen.md "Web pages and
videos").

The search is the existing grounded web retrieval (research_worker.retrieval.lookup, Gemini 3.8 Flash with Google
Search), asked for video pages. Its result is checked before anything is shown: the model judges, the code checks.

- Each candidate is a video page on an allowed host (web_hosts.embed); anything else is dropped.
- Its real title and channel come from the video site itself (its oEmbed answer), never from the search's words, and a
  video the site does not answer for (removed, private, invented) is dropped.
- A model judges which one's real title and description match what was asked (retrieval.judge_videos); the code takes
  only a choice that is one of those candidates, and says so when none matches.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from .web_hosts import embed

OEMBED_S = 8.0
OEMBED = {"youtube.com": "https://www.youtube.com/oembed?format=json&url=",
          "vimeo.com": "https://vimeo.com/api/oembed.json?url="}


class NotFound(ValueError):
    """No video found, or none of those found is what was asked."""


@dataclass(frozen=True)
class FoundVideo:
    kind: str
    url: str
    page: str
    site: str
    id: str
    title: str
    channel: str
    description: str
    why: str  # the check's own words for why it is the one asked for

    def shown(self) -> dict:
        return {"kind": self.kind, "url": self.url, "page": self.page, "site": self.site, "id": self.id}


async def oembed(page: str, site: str) -> dict | None:
    """The video site's own title and channel for `page`, or None when it does not answer for it."""
    import httpx

    base = OEMBED.get(site)
    if base is None:
        return None
    try:
        async with httpx.AsyncClient(timeout=OEMBED_S, follow_redirects=False,
                                     headers={"User-Agent": "Hooke/1 (video check)"}) as client:
            response = await client.get(base + quote(page, safe=""))
        if response.status_code != 200 or len(response.content) > 64 * 1024:
            return None
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    title = data.get("title") if isinstance(data, dict) else None
    if not isinstance(title, str) or not title.strip():
        return None
    return {"title": " ".join(title.split())[:300], "channel": " ".join(str(data.get("author_name") or "").split())[:120]}


async def find(subject: str, *, lookup=None, judge=None, check=None, observe=None) -> FoundVideo:
    """The one video found for `subject` that the check says is what was asked, else NotFound with why."""
    from research_worker import retrieval

    lookup = lookup or retrieval.lookup
    judge = judge or retrieval.judge_videos
    check = check or oembed
    matches = await lookup(subject, videos=True, observe=observe)
    candidates, seen = [], set()
    for match in matches[:3]:
        shown = embed(str(match.get("page") or ""))
        if shown is None or shown["kind"] != "video" or shown["url"] in seen:
            continue
        seen.add(shown["url"])
        real = await check(shown["page"], shown["site"])
        if real is None:  # the video site does not know it: removed, private, or never there
            continue
        candidates.append({**shown, "title": real["title"], "channel": real["channel"],
                           "description": " ".join(str(match.get("description") or "").split())[:600]})
    if not candidates:
        raise NotFound("the search found no video page the video site confirms")
    verdict = await judge(subject, candidates)
    if not 0 <= verdict.choice < len(candidates):  # the code takes only one of the candidates it checked
        raise NotFound("none of the videos found is what was asked" + (f": {verdict.why}" if verdict.why else ""))
    chosen = candidates[verdict.choice]
    return FoundVideo(kind="video", url=chosen["url"], page=chosen["page"], site=chosen["site"], id=chosen["id"],
                      title=chosen["title"], channel=chosen["channel"], description=chosen["description"],
                      why=verdict.why)
