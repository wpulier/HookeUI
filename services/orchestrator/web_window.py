"""A web page or a video from the web on screen (docs/architecture/on-screen.md "Web pages and videos").

`show_web` finds a video (a grounded web search, its result checked: video_search.py) or takes a link, keeps it in Data
as the Project's own entry, and opens it in a sandboxed window on a Tab (apps/studio/src/web-window.ts), placed with the
placement words (placement.py; by default a window, in front). Only an allowed host's framed address is ever sent
(web_hosts.py; presentations.SourceRef checks the same), and the studio checks it again before it frames it.

It is done only once the studio's screen report shows the window on that Tab (`document:<placement>`, representation
`web-page` or `video`), its frame loaded and nothing over it (ADR 0006); otherwise said with what the report shows and
where it is kept. Like the table window, it answers `web-window` at once with its placement; that answer is no
confirmation of its own.
"""
from __future__ import annotations

import asyncio
import json
import uuid

from .brain.registry import ToolFailed
from .data_reader import ASK_EVERY_S, ReaderStudio, _current
from .table_window import TableWindows, _strip, _tab, over
from .untrusted import inline

WEB_WAIT_S = 10.0  # the window shown with its frame loaded (a video player takes a moment), the studio in view
ANSWER_WAIT_S = 4.0


def web_windows(hub) -> TableWindows:
    """The `web-window` messages waiting for their studio's answer (the same one-answer broker the table window uses)."""
    if getattr(hub, "web_windows", None) is None:
        hub.web_windows = TableWindows()
    return hub.web_windows


def source_of(shown: dict, title: str) -> dict:
    """The web page or video as a source descriptor (presentations.SourceRef: only an allowed host's framed address)."""
    from .presentations import SourceRef

    wire = {"key": f"web:{shown['kind']}:{shown['id']}", "kind": shown["kind"], "media": "external-reference",
            "availability": "temporary", "identity": {"url": shown["url"], "site": shown["site"], "page": shown["page"]},
            "provenance": {"basis": "web", "web": {"page": shown["page"], "site": shown["site"], "title": title[:200]}}}
    return SourceRef.from_wire(wire).to_wire()


def window_in(report: dict | None, placement: str | None, resource: str, project: str, tab: str) -> dict | None:
    """The web window of the report showing Data item `resource` in that Project and Tab, open."""
    for item in (report or {}).get("items") or []:
        doc = item.get("document") or {}
        shows = doc.get("resource") or {}
        if (str(item.get("id") or "").startswith("document:") and item.get("open", True)
                and (placement is None or item["id"] == f"document:{placement}")
                and doc.get("representation") in ("web-page", "video") and doc.get("project") == project
                and doc.get("tab") == tab and shows.get("id") == resource):
            return item
    return None


async def show(services, ctx, studio: ReaderStudio, *, shown: dict, title: str, entry: str, placement: str,
               tab: str | None, guard) -> str:
    """Opens the web page or video `shown` (web_hosts.embed's) kept as Data item `entry`, placed as asked, and says
    what the report showed. Refuses, saying where it is kept and its link, when the report never shows it there."""
    from .placement import beside_shown, only_shown

    source_of(shown, title)  # the server's own check of the address, before anything is sent
    project = studio.project
    what = f"the {'video' if shown['kind'] == 'video' else 'page'} \"{inline(title, 120)}\" ({shown['site']})"
    kept = f"it is kept in Data as {entry}; its link: {shown['page']}"
    if placement == "overlay":
        raise ToolFailed("only outlines and layers are drawn on the live view; a video or page opens as a window",
                         next="open it as a window, beside the live view, or in a Tab of its own")
    made = False
    if placement == "new-tab":
        tab_id, tab_title, made = await _tab(services, ctx, tab or (title[:40] or shown["site"]), new=True)
    elif tab:
        tab_id, tab_title, made = await _tab(services, ctx, tab)
    else:
        strip = _strip(_current(studio, guard))
        tab_id, tab_title = strip.get("tab") or studio.tab, strip.get("tabTitle") or ""
    guard()
    where = f'Tab "{inline(tab_title, 80)}"' if tab_title else "the Tab in front"
    if placement == "replace":
        from .brain.toolbox.present import act
        from .workspace_observation import front

        before = front(_current(studio, guard)).get("window")
        if before:
            await act(services, {"do": "close", "what": before}, ctx=ctx)

    def refused(why: str):
        raise ToolFailed(f"{what} is not shown in {where}: {why}; {kept}", next="ask again to open it there")

    message = {"type": "web-window", "id": uuid.uuid4().hex, "session": project, "tab": tab_id,
               "workspacePin": studio.pin,
               "item": {"kind": shown["kind"], "url": shown["url"], "page": shown["page"], "site": shown["site"],
                        "title": title[:200], "resource": {"day": project, "id": entry, "version": None}}}
    answer = await web_windows(services.hub).ask(studio, message, ANSWER_WAIT_S)
    guard()
    if answer is None:
        refused("the studio did not answer")
    if not answer.get("ok"):
        refused(f"the studio said {inline(str(answer.get('error') or 'it could not open it'), 160)!r}")
    window = str(answer.get("placement") or "") or None
    if placement in ("beside", "only"):
        broker = getattr(services.hub, "workspace_tabs", None)
        args = ({"action": "arrange", "tab": tab_id} if placement == "beside"
                else {"action": "only", "tab": tab_id, "keep": [f"document:{window}"]})
        if broker is not None and hasattr(broker, "run"):
            await broker.run(ctx, args, verify=False)
        else:
            await services.workspace_tabs(ctx, args)
        guard()

    def done(report) -> tuple[bool, str]:
        item = window_in(report, window, entry, project, tab_id)
        if _strip(report).get("tab") != tab_id:
            return False, "another Tab came to the front"
        if item is None:
            return False, "its window is not open there"
        state = (item.get("document") or {}).get("state")
        if state == "failed":
            return False, "its page could not load there"
        if state != "rendered" or not item.get("visible"):
            return False, "its page was still loading"
        if placement == "beside":
            return beside_shown(report, tab_id, [item["id"]])
        if placement == "only":
            return only_shown(report, tab_id, [item["id"]], home=tab_id == "main")
        above = over(item, report.get("items") or [])
        if above is not None:
            return False, f"it is open there but behind {inline(str(above.get('title') or 'another window'), 80)}"
        return True, ""

    loop = asyncio.get_running_loop()
    tick = asked_at = loop.time()
    waited, why, report = 0.0, "", None
    while True:
        report = _current(studio, guard)
        ok, why = done(report)
        if ok:
            break
        now = loop.time()
        if report.get("visible") is not False:  # only the studio's time in view counts
            waited += now - tick
        tick = now
        if waited >= WEB_WAIT_S:
            refused(why)
        if now - asked_at >= ASK_EVERY_S:
            studio.client.offer(json.dumps({"type": "screen-ask"}))
            asked_at = now
        await asyncio.sleep(0.05)
    item = window_in(report, window, entry, project, tab_id)
    new = " (a new Tab)" if made else ""
    how = {"beside": ", beside what is open there, tiled side by side",
           "only": ", the only window there" + (" (the live view floats small in its corner)" if tab_id == "main"
                                                 else " (the live view hidden)"),
           }.get(placement, ", in front")
    return f"the studio shows {what} in a window in {where}{new}{how} ({item['id']}); {kept}."
