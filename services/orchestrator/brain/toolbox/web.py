"""A video or web page from the web on screen (`show_web`; orchestrator/web_window.py, video_search.py).

"Find a YouTube video about stomata and open it": the video is found by the grounded web search, checked (its real
title from the video site, judged against what was asked), kept in Data as this Project's own entry, and opened in a
sandboxed window, by default floating in front (placement.py: a video or web page opens as a window). A link to an
allowed site (YouTube, Vimeo, Wikipedia) opens the same way; any other site is said with its link."""
from __future__ import annotations

from ..registry import Registry, Tool, ToolContext, ToolFailed, schema
from .resources import require_current


def where_kept(services, ctx, shown: dict, title: str, values: dict) -> str:
    """Keeps the video or page in Data as this Project's own entry (a result of the run, its source the web, never this
    slide), and returns its id."""
    from ..registry import CURRENT_RUN, attach

    kept = {"media": shown["kind"], "web": {"site": shown["site"], "page": shown["page"], "title": title[:200]},
            "url": shown["url"], **values}
    run = CURRENT_RUN.get()
    results = services.hub.resources.results
    if run is not None and run.tool == "show_web":  # its own run's result, kept once (resources.from_run)
        attach(values=kept)
        entry = results.from_run(run, ctx, services.hub, produced=True)
    else:  # another tool showing what it read (retrieve_page show): kept as show_web's would be
        asked = getattr(ctx, "asked", None)
        entry = results.add("show_web", {"url": shown["page"]}, f"{title[:120]} ({shown['site']})", values=kept,
                            request=getattr(ctx, "question", "") or "", session=getattr(asked, "session", None) or None)
    if entry is None:
        raise ToolFailed("it was found but could not be kept in Data for display", next=None)  # no next: the limitation is stated
    return str(entry["id"])


async def open_web(services, ctx, *, shown: dict, title: str, entry: str, placement: str | None, tab: str | None) -> str:
    """Opens the kept video or page on screen as asked (web_window.show), in the studio that asked."""
    from ...data_reader import reader_studio
    from ...placement import default
    from ...screen import screen_of
    from ...web_window import show

    day = services.day
    studio = reader_studio(screen_of(services.hub), ctx, day.sid) if day is not None else None
    if studio is None or getattr(services, "workspace_tabs", None) is None:
        raise ToolFailed(f"this studio has no Project workspace to show it in; it is kept in Data as {entry}; its link: "
                         f"{shown['page']}", next=None)  # no next: where it is and its link are said

    def guard():
        require_current(ctx, day)

    return await show(services, ctx, studio, shown=shown, title=title, entry=entry,
                      placement=placement or default(shown["kind"]), tab=tab, guard=guard)


def add_web_tools(registry: Registry, services) -> None:
    async def show_web(ctx: ToolContext, args: dict) -> str:
        if getattr(services, "day", None) is None or getattr(services.hub, "resources", None) is None:
            raise ToolFailed("no Project is open to keep it in", next="open a Project")
        from ...placement import PLACEMENTS
        from ...untrusted import inline
        from ...video_search import NotFound, find
        from ...web_hosts import embed

        placement = args.get("placement")
        if placement is not None and placement not in PLACEMENTS:
            raise ToolFailed(f"{placement!r} is not a place for it", next="say one of: " + ", ".join(PLACEMENTS))
        tab = " ".join(str(args.get("tab") or "").split())[:80] or None
        video, url = inline(args.get("video"), 600), str(args.get("url") or "").strip()
        if not video and not url:
            raise ToolFailed("say which video to find, or give the page's link", next=None)  # the message says what to give
        if url:
            shown = embed(url)
            if shown is None:
                raise ToolFailed("that site is not one Hooke shows inside the app (it shows YouTube, Vimeo and "
                                 f"Wikipedia); its link: {inline(url, 300)}", next=None)  # no next: the link is said
            title = inline(args.get("title"), 200) or shown["site"]
            if shown["kind"] == "video":
                from ...video_search import oembed

                real = await oembed(shown["page"], shown["site"])
                if real is None:
                    raise ToolFailed(f"{shown['site']} does not know that video (removed, private or a wrong link)",
                                     next=None)  # no next: what happened is said
                title = real["title"]
            require_current(ctx, services.day)
            entry = where_kept(services, ctx, shown, title, {})
            return await open_web(services, ctx, shown=shown, title=title, entry=entry, placement=placement, tab=tab)
        try:
            found = await (getattr(services, "find_video", None) or find)(video)
        except NotFound as exc:
            raise ToolFailed(f"no video of {video} was opened: {exc}", next="try other words for it if useful") from None
        require_current(ctx, services.day)
        entry = where_kept(services, ctx, found.shown(), found.title,
                           {"subject": video, "channel": found.channel, "checked": found.why or "its title matches"})
        said = await open_web(services, ctx, shown=found.shown(), title=found.title, entry=entry, placement=placement,
                              tab=tab)
        by = f" by {inline(found.channel, 80)}" if found.channel else ""
        return (f"{said} Found by a web search for {video}; the video site calls it \"{inline(found.title, 160)}\"{by}; "
                f"checked against what was asked: {inline(found.why or 'its title matches', 200)}.")

    registry.add(Tool(
        "show_web", "Find a video on the web (YouTube or Vimeo) about something and open it, or open a web page or "
        "video by its link (YouTube, Vimeo, Wikipedia; other sites are said with their link): \"find a YouTube video "
        "about stomata and open it\". The video found is checked (its real title, judged against what was asked) and "
        "kept in Data. It opens in its own sandboxed window with its source and link shown; placement: window (the "
        "default, in front), beside, new-tab, only or replace. Done once the screen shows it.",
        schema({"video": {"type": "string", "maxLength": 600, "description": "what the video should be about"},
                "url": {"type": "string", "maxLength": 2048, "description": "a YouTube, Vimeo or Wikipedia link"},
                "title": {"type": "string", "maxLength": 200, "description": "with url: a short title for the window"},
                "placement": {"type": "string", "enum": ["window", "beside", "new-tab", "only", "replace"],
                              "description": "where it goes (default window)"},
                "tab": {"type": "string", "maxLength": 80, "description": "a Tab by title or id (made when none has it)"}}),
        show_web, kind="effect", inline_ms=1500, family="display"))
