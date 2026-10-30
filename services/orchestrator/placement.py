"""Where Hooke puts what it shows: one placement vocabulary for `present`, `tabs` and the `show_*` tools
(docs/architecture/on-screen.md "Placing things"; the live partner spec 4.8, horizontal 2).

Will, 29 Oct: "HOW we display in various ways the data produced by the models is critical and not an afterthought at
all." Every tool that puts something on screen takes the same `placement` word, and each placement is done only when
the studio's report shows it (ADR 0006): the words here are the brain's, the checks below read the report.

- `new-tab`: a Tab of its own, a canvas with no specimen (a run's outlines go there on their capture, with their table).
- `beside`: side by side in this Tab, tiled with what is there, the live view included (the Tab's own Arrange).
- `overlay`: drawn on the live specimen (outlines and layers only).
- `window`: a floating window, in front.
- `replace`: in place of the window in front (or the one named), which closes.
- `close`: closed; its Data is kept.
- `only`: the only thing open in its Tab: every other window closed and the specimen hidden (Main's floats small).

With a Tab named, each puts the thing in that Tab (`put`), wherever it is now: open on that Tab already, open in another
Tab (moved there with `instead`, the model's choice from the user's words; otherwise opened there too, and said so), or
saved but not open (a picture, a researcher's picture or a table opened there), then placed as asked, keeping the window
it put there.
"""
from __future__ import annotations

import re

from .untrusted import inline

PLACEMENTS = ("new-tab", "beside", "overlay", "window", "replace", "close", "only")
# Every name the screen and the brain's own words give a layer drawn on the specimen: a population (`e3`, `layer:e3`,
# `population:e3`, the marks' and front()'s overlay ids) or motion tracks (`m1`, `tracks:m1`). will-display, 29 Oct:
# "show the outlines as an overlay" named `layer:e1` and was refused as a window.
LAYER = re.compile(r"(?:(?:layer|population|tracks):)?([em]\d+)")


def layer_of(what: str) -> str | None:
    """The population (eN) or motion item (mN) `what` names as a layer, or None when it names no layer."""
    found = LAYER.fullmatch(" ".join(str(what or "").split()).lower())
    return found.group(1) if found else None

# The brain-facing words (kept short: they go into the tool descriptions).
WORDS = {
    "new-tab": "a Tab of its own (no specimen)",
    "beside": "side by side in this Tab with what is there, the live view included",
    "overlay": "drawn on the live specimen (outlines and layers only)",
    "window": "a floating window, in front",
    "replace": "in place of the window in front, which closes",
    "close": "closed (its Data is kept)",
    "only": "the only thing open in its Tab (the rest closed, the specimen hidden; Main's floats small)",
}
DESCRIPTION = "where it goes: " + "; ".join(f"{p}: {WORDS[p]}" for p in PLACEMENTS)
SCHEMA = {"type": "string", "enum": list(PLACEMENTS), "description": DESCRIPTION}

# Where each kind of output goes when no placement is asked (Will's defaults, 29 Oct): a run's outlines on the
# specimen with their table beside, a reference image beside, a document in a Tab of its own, a video or web page in a
# window.
DEFAULTS = {"outlines": "overlay", "tracks": "overlay", "table": "beside", "reference": "beside", "image": "beside",
            "graph": "beside", "document": "new-tab", "video": "window", "web-page": "window"}


def default(kind: str) -> str:
    """The placement an output of `kind` gets when none is asked."""
    return DEFAULTS.get(kind, "window")


# An index entry's kind (resources.py) as the kind of thing it is on a Tab (workspace-model.ts ITEM_KINDS).
ENTRY_KINDS = {"population": "outlines", "tracks": "tracks", "dataset-table": "table", "result-table": "table",
               "analysis": "table", "graph": "graph", "research": "document", "snapshot": "image", "clip": "video",
               "recording": "video", "job": "document"}


def kind_of_entry(kind: str, *, web: dict | None = None, media: str | None = None) -> str:
    """What an index entry is on screen, by its kind (and a result's media: a reference photo, a web page or video, a
    document written from the Project's records)."""
    if kind == "result":
        if media in ("web-page", "video", "document"):
            return media
        return "reference" if web or media == "reference" else "image"
    return ENTRY_KINDS.get(kind, "document")


# --- what the studio's report shows ------------------------------------------------------------------------------------
def _legacy(item: dict) -> dict:
    return ((item.get("view") or {}).get("legacy") or {})


def strip(wire: dict | None) -> dict:
    """The Tab strip's workspace facts of a report: the Project, the Tab in front and every open Tab."""
    item = next((i for i in (wire or {}).get("items") or () if i.get("id") == "workspace-tabs"), None)
    return _legacy(item or {}).get("workspace") or {}


def tab_of(wire: dict | None, tab: str) -> dict | None:
    """An open Tab of the report, by its id or exact title."""
    return next((t for t in strip(wire).get("tabs") or () if tab in (t.get("id"), t.get("title"))), None)


def windows_on(wire: dict | None, tab: str) -> list[dict]:
    """The windows open and shown on Tab `tab` (its id), as the report shows them: each document window that says it is
    on that Tab, and, when that Tab is in front, every other window open and shown (native windows, the magnifier),
    which the report places on the Tab in front."""
    from .screen import closable, laid_out

    front = strip(wire).get("tab")
    out = []
    for item in (wire or {}).get("items") or ():
        if item.get("id") == "workspace-tabs" or not closable(item) or not item.get("open", True):
            continue
        document = _legacy(item).get("document")
        on = document.get("tab") if document else front
        if on == tab and (item.get("visible") or laid_out(item)):
            out.append(item)
    return out


def identities(item: dict) -> set[str]:
    """Every name a window goes by in a report: its id, its placement, the Data item it shows."""
    names = {str(item.get("id") or "")}
    document = _legacy(item).get("document") or {}
    if document.get("placement"):
        names |= {str(document["placement"]), f"document:{document['placement']}"}
    if (document.get("resource") or {}).get("id"):
        names.add(str(document["resource"]["id"]))
    return names - {""}


def only_shown(wire: dict | None, tab: str, keep: list[str], *, home: bool) -> tuple[bool, str]:
    """Whether the report shows only `keep` on Tab `tab`, and the specimen hidden (on Main, floating), with what it
    shows otherwise in plain words."""
    shown = windows_on(wire, tab)
    wanted = set(keep)
    others = [w for w in shown if not identities(w) & wanted]
    kept = [w for w in shown if identities(w) & wanted]
    specimen = ((tab_of(wire, tab) or {}).get("specimen") or {}).get("state")
    want = "float" if home else "hidden"
    problems = []
    if keep and not kept:
        problems.append("what was to stay is not shown there")
    if others:
        names = ", ".join(inline(str(w.get("title") or _legacy(w).get("title") or w.get("id")), 60) for w in others[:4])
        problems.append(f"still open there: {names}")
    if specimen is not None and specimen != want:
        problems.append(f"the live view still {'fills it' if specimen == 'fill' else specimen}")
    return (not problems, "; ".join(problems))


def _overlap(a, b) -> float:
    """How much of the smaller of rectangles a and b the other covers (0-1)."""
    if not (isinstance(a, list) and isinstance(b, list) and len(a) == len(b) == 4):
        return 0.0
    width = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    smaller = min(a[2] * a[3], b[2] * b[3])
    return (width * height) / smaller if smaller > 0 else 0.0


def beside_shown(wire: dict | None, tab: str, thing: list[str]) -> tuple[bool, str]:
    """Whether the report shows `thing` on Tab `tab` side by side with what is there: its window shown, and no window
    the Tab's Arrange tiles there (documents and native windows, and the live view when it floats) covering a quarter
    of it, or it of them. A panel Arrange does not tile (the magnifier over the live view) does not count."""
    shown = [w for w in windows_on(wire, tab) if str(w.get("id") or "").startswith(("document:", "w-"))]
    wanted = set(thing)
    mine = [w for w in shown if identities(w) & wanted]
    if not mine:
        return False, "it is not shown there"
    others = [w.get("rect") for w in shown if not identities(w) & wanted]
    specimen = (tab_of(wire, tab) or {}).get("specimen") or {}
    if specimen.get("state") == "float" and specimen.get("rect"):
        others.append(specimen["rect"])
    elif specimen.get("state") == "fill":
        return False, "the live view fills the Tab behind it"
    for a in [w.get("rect") for w in mine]:
        for b in others:
            if _overlap(a, b) > 0.25:
                return False, "its windows still overlap"
    return True, ""


# --- making room beside a window ---------------------------------------------------------------------------------------
# A new window opened beside another is fitted before its first paint (present._opening_basis): 240 to 360 px wide at
# its picture's aspect, top-aligned with the window it goes beside, within the visible area. When there is no such room
# on either side, Hooke makes it, as a person would: it moves that window over, and narrows it only when moving is not
# enough (will-workspace, 30 Oct: "make room or select the original window" was handed to the user).
BESIDE_GAP = 12
BESIDE_WIDTH = (240, 360)  # the new window's narrowest and widest initial fit (present._opening_basis)
NARROWEST = 240  # a window is never narrowed below this to make room


def _fits_beside(x: float, y: float, width: float, area, size, gap: float) -> bool:
    """Whether a new window of picture `size` fits on the right of a window at x, y, `width` wide (_opening_basis)."""
    room = area[0] + area[2] - (x + width) - gap
    available = area[1] + area[3] - y
    wide = min(BESIDE_WIDTH[1], room, max(0.0, available - 44) * size[0] / size[1])
    return wide >= BESIDE_WIDTH[0] and max(180 + 44, wide * size[1] / size[0] + 44) <= available and y >= area[1]


def room_beside(anchor, area, size, gap: float = BESIDE_GAP) -> tuple[float, float, float] | None:
    """Where window `anchor` (x, y, width, height) goes, and how wide, so a new window of picture `size` fits on its
    right inside `area`: its own place first, then moved left (and up, when too low), and only then narrowed. None when
    the area cannot hold both side by side (a true impossibility, said as such)."""
    if not (isinstance(anchor, (list, tuple)) and isinstance(area, (list, tuple)) and len(anchor) == len(area) == 4
            and isinstance(size, (list, tuple)) and len(size) == 2 and size[0] > 0 and size[1] > 0):
        return None
    x0, y0, width0 = anchor[0], anchor[1], anchor[2]
    widths = [width0] + [w for w in (area[2] - gap - BESIDE_WIDTH[1], area[2] - gap - BESIDE_WIDTH[0])
                         if NARROWEST <= w < width0]
    for width in widths:
        wanted = min(BESIDE_WIDTH[1], area[2] - width - gap)
        x = max(area[0], min(x0, area[0] + area[2] - width - gap - wanted))
        for y in dict.fromkeys((max(area[1], y0), area[1])):
            if _fits_beside(x, y, width, area, size, gap):
                return x, y, width
    return None


async def make_room_beside(services, ctx, anchor_id: str, size) -> str | None:
    """Moves (and, only when moving is not enough, narrows) window `anchor_id` so a new window of picture `size` fits
    beside it, each change the window's own move or resize, confirmed on screen (present). Returns what it did, in
    words; None when the visible area cannot hold both (nothing is changed then)."""
    from .brain.toolbox.present import _native_settled
    from .screen import screen_of

    report = screen_of(services.hub).completion_report(ctx.client)
    row = next((r for r in (report or {}).get("items") or () if r.get("id") == anchor_id), None)
    area = ((report or {}).get("viewport") or {}).get("presentationArea")
    rect = (row or {}).get("rect")
    placed = room_beside(rect, area, size)
    if placed is None:
        return None
    x, y, width = placed
    narrowed, moved = width != rect[2], (x, y) != (rect[0], rect[1])
    if narrowed:
        await _native_settled(services, ctx, {"do": "resize", "instance": {"instanceId": anchor_id},
                                              "size": {"width": width, "height": rect[3]}})
    if moved:
        await _native_settled(services, ctx, {"do": "move", "instance": {"instanceId": anchor_id},
                                              "position": {"x": x, "y": y}})
    title = inline(str((row or {}).get("title") or "the window it goes beside"), 80)
    if narrowed and moved:
        return f"narrowed {title} and moved it over so the new window fits beside it"
    if narrowed or moved:
        return f"{'narrowed' if narrowed else 'moved'} {title}{'' if narrowed else ' over'} so the new window fits beside it"
    return ""


def holding(wire: dict | None, kind: str) -> list[dict]:
    """The open Tabs holding a `kind` (the Tab strip's counts by kind)."""
    from .workspace_observation import tabs_holding

    return tabs_holding(wire or {}, kind)


async def _document(services, ctx, what: str, placement: str, tab: str | None) -> str | None:
    """A document saved in Data (documents.py) placed as asked: in a Tab of its own (new-tab, only) or in the reader
    beside, done once the report shows it. None when `what` is not a document."""
    import asyncio

    from . import documents
    from .brain.registry import ToolFailed
    from .brain.toolbox.resources import require_current
    from .data_reader import reader_studio
    from .resources import resources_of
    from .screen import screen_of

    index = resources_of(services.hub)
    entry = await asyncio.to_thread(index.get, what, index.session())
    if not documents.is_document(entry):
        return None
    day = services.day
    studio = reader_studio(screen_of(services.hub), ctx, day.sid) if day is not None else None
    if studio is None:
        raise ToolFailed(f"{what} is saved in Data under Write-ups; this studio has no Project workspace to show it in",
                         next=None)  # no next: where it is saved is said

    def guard():
        require_current(ctx, day)

    title = inline(tab or entry.shown or entry.title, 60)
    where = await documents.show(services, ctx, studio, entry, placement=placement, title=title, guard=guard)
    return f"the document {what} (saved in Data under Write-ups) is {where}"


# --- present's placement: one word for anything shown ------------------------------------------------------------------
async def place(services, ctx, registry, args: dict) -> str:
    """`present` with a placement (the one vocabulary): what it names put where it was asked, through the same tools
    that put each kind there, each done only when the studio's report shows it.

    - A layer (a population eN or motion item mN, by any name the screen gives it: `layer:e3`, `tracks:m1`): overlay
      draws it on the live specimen (present show); close hides it; new-tab, beside, only, replace and window put its
      outlines on their capture with its table (show_measurements' placements).
    - A document written from the Project's records (dN): in a Tab of its own, or in the reader beside (documents.py).
    - Anything else (a window on screen, a saved picture, a researcher's picture, a table): `put` in the Tab named, or
      the Tab in front, wherever it is now.
    """
    from .brain.registry import ToolFailed
    from .brain.toolbox.present import act

    what = args.get("what") if isinstance(args.get("what"), str) else ""
    what = what.strip()
    placement = str(args.get("placement") or "")
    tab = " ".join(str(args.get("tab") or "").split())[:80] or None
    instead = args.get("instead") is True
    layer = layer_of(what)
    if not placement and layer is not None and layer.startswith("e"):
        # A run's outlines with no place asked: drawn on the live specimen, with their table beside (DEFAULTS).
        drawn = await act(services, {"do": "show", "what": layer}, ctx=ctx)
        tool = registry.tools.get("show_measurements")
        if tool is None:
            return drawn
        try:
            table = await tool.handler(ctx, {"of": layer, "placement": "beside", **({"tab": tab} if tab else {})})
        except ToolFailed as exc:
            return f"{drawn}; its table was not placed beside: {exc}"
        return f"{drawn}; and {table}"
    placement = placement or "window"
    if placement not in PLACEMENTS:
        raise ToolFailed(f"{placement!r} is not a place for it", next="say one of: " + ", ".join(PLACEMENTS))
    if layer is not None:
        named = f"tracks:{layer}" if layer.startswith("m") else layer
        if placement == "overlay":
            return await act(services, {"do": "show", "what": named}, ctx=ctx)
        if placement == "close":
            return await act(services, {"do": "hide", "what": named}, ctx=ctx)
        tool = registry.tools.get("show_measurements")
        if tool is None:
            raise ToolFailed("its table cannot be opened here", next="present show draws its outlines on the specimen")
        return await tool.handler(ctx, {"of": layer, "placement": placement, **({"tab": tab} if tab else {}),
                                        **({"instead": True} if instead else {})})
    if not what:
        raise ToolFailed("name what to place: its id from the screen or Data", next="look at what is on screen",
                         argument=True)  # the call's own arguments did not fit: corrected by a later call (handoffs.corrected)
    if re.fullmatch(r"d\d+", what) and placement in ("new-tab", "only", "beside", "window"):
        said = await _document(services, ctx, what, placement, tab)
        if said is not None:
            return said
    if placement == "overlay":
        raise ToolFailed(f"{inline(what, 60)} is not outlines or tracks; only those are drawn on the live view",
                         next="put it beside the live view instead (placement beside)")
    if placement == "close":
        return await act(services, {"do": "close", "what": what}, ctx=ctx)
    return await put(services, ctx, what, placement=placement, tab=tab, instead=instead)


# --- putting a thing in a Tab, wherever it is now ------------------------------------------------------------------------
def window_id(item: dict) -> str:
    """The id a `tabs list` item goes by on screen: its native window's (w-…), else its document window's."""
    return str(item.get("window") or f"document:{item.get('placement')}")


def located(tabs, what: str) -> list[tuple[dict, dict]]:
    """Every (work Tab, item) of a `tabs list` that holds `what`: by its placement, its window or the Data item it
    shows. A researcher's or reader Tab keeps its own work and is never where a window is put."""
    names = {what, what.removeprefix("document:")}
    out = []
    for tab in tabs or ():
        if (tab.get("kind") or "work") != "work":
            continue
        for item in tab.get("items") or ():
            ids = {item.get("placement"), f"document:{item.get('placement')}", item.get("window"),
                   (item.get("resource") or {}).get("id")} - {None, "", "document:None"}
            if names & ids:
                out.append((tab, item))
    return out


async def _saved(services, what: str):
    """The Data item `what` names (a researcher's Tab id `research:rN` names its researcher's work), or a refusal that
    says it is neither open nor saved."""
    import asyncio

    from .brain.registry import ToolFailed
    from .resources import resources_of

    index = resources_of(services.hub)
    key = what.removeprefix("research:")
    entry = await asyncio.to_thread(index.get, key, index.session())
    if entry is None:
        raise ToolFailed(f"{inline(what, 60)} is not open on any Tab and not in this Project's Data",
                         next="name it by its id from the screen, the Tabs or Data", argument=True)
    return entry


def picture_source(services, entry) -> dict | None:
    """The exact source a picture-like Data item opens from in a window of its own (present open): a saved picture (a
    reference photo, a result's picture, a snapshot) by its id, or a researcher's picture by its exact retained output
    (`outputId`, as show_result opens it), never a copy. None when it has no picture to open."""
    from .resources import _research_picture_source, resources_of

    if entry.kind in ("result", "snapshot", "bookmark") and entry.pictures:
        return {"day": entry.day, "id": entry.id, "version": entry.version, "picture": 0}
    if entry.kind not in ("figure", "research"):
        return None
    manager = resources_of(services.hub).research
    rid = (entry.source or {}).get("researcher") if entry.kind == "figure" else entry.id
    session = manager.get(rid) if manager is not None and rid else None
    if session is None:
        return None
    images = [o for o in session.current_outputs() if o.get("kind") == "image" and o.get("id")]
    wanted = (entry.source or {}).get("outputId") if entry.kind == "figure" else None
    output = next((o for o in images if o["id"] == wanted), None) if wanted else (images[0] if images else None)
    if output is None:
        return None
    basis = _research_picture_source(session, output)
    return {"day": session.day, "id": session.rid, "version": basis["version"], "picture": images.index(output),
            "outputId": output["id"]}


async def open_picture(services, ctx, source: dict, title: str) -> str:
    """Opens `source` in a native window on the Tab in front (present open, confirmed by the studio) and returns the
    window's id, as its own receipt gives it."""
    from .brain.registry import CURRENT_RUN, ToolFailed
    from .brain.toolbox.present import _native_present

    run = CURRENT_RUN.get()
    before = len(run.outputs) if run is not None else 0
    await _native_present(services, ctx, {"do": "open", "source": source, "title": title[:120]})
    opened = [o.wire() for o in (run.outputs[before:] if run is not None else ())]
    window = next((o.get("instanceId") for o in reversed(opened) if o.get("kind") == "presentation"), None)
    if not window:
        raise ToolFailed("its window did not open", next="ask again to open it there")
    return str(window)


async def _open_here(services, ctx, entry, tab_id: str, *, title: str) -> str:
    """Opens saved Data item `entry` on Tab `tab_id`, which is in front: a population or a table as the Tab's own
    window (tabs add), a video or web page in its sandboxed window (web_window.py), a picture or a researcher's picture
    in a window of its own. Returns the window's id on screen."""
    from .brain.registry import ToolFailed
    from .workspace_tabs import ADD_PRESENTATIONS

    broker = services.workspace_tabs
    if entry.kind in ADD_PRESENTATIONS:
        reply = await broker(ctx, {"action": "add", "tab": tab_id, "resource": {"id": entry.id, "version": entry.version}})
        here = next((t for t in reply.get("tabs") or () if t.get("id") == tab_id), {})
        item = next((i for i in reversed(here.get("items") or ()) if (i.get("resource") or {}).get("id") == entry.id), None)
        if item is None:
            raise ToolFailed(f"{entry.id} was added to that Tab, but the studio does not list it there",
                             next="list the Tabs to see what each holds")
        return window_id(item)
    values = getattr(entry, "values", None) or {}
    if entry.kind == "result" and values.get("media") in ("video", "web-page"):
        from .data_reader import reader_studio
        from .screen import screen_of
        from .web_hosts import embed
        from .web_window import show

        page = (values.get("web") or {}).get("page") or values.get("url")
        shown = embed(str(page or ""))
        studio = reader_studio(screen_of(services.hub), ctx, services.day.sid) if services.day is not None else None
        if shown is None or studio is None:
            raise ToolFailed(f"{entry.id} cannot be opened in a window here; its link: {inline(str(page), 300)}", next=None)  # no next: its link is said
        from .brain.toolbox.resources import require_current

        await show(services, ctx, studio, shown=shown, title=title, entry=entry.id, placement="window", tab=None,
                   guard=lambda: require_current(ctx, services.day))
        listed = await broker(ctx, {"action": "list"})
        here = next((t for t in listed.get("tabs") or () if t.get("id") == tab_id), {})
        item = next((i for i in reversed(here.get("items") or ()) if (i.get("resource") or {}).get("id") == entry.id), None)
        if item is None:
            raise ToolFailed(f"{entry.id} opened, but the studio does not list it on that Tab",
                             next="list the Tabs to see what each holds")
        return window_id(item)
    source = picture_source(services, entry)
    if source is None:
        raise ToolFailed(f"{entry.id} is read in the reader, not opened as a window in a Tab",
                         next=f"present open {entry.id} shows it in the reader")
    return await open_picture(services, ctx, source, title)


def _report_wire(services, ctx) -> dict | None:
    from .screen import screen_of

    observed = screen_of(services.hub).observation(ctx.client) if getattr(ctx, "client", None) else None
    return observed.wire() if observed is not None else None


async def put(services, ctx, what: str, *, placement: str, tab: str | None, instead: bool) -> str:
    """`what` put in the Tab named (made when no Tab has that title), or in a new Tab (new-tab), or the Tab in front,
    and placed there as asked (only, beside, window, replace), wherever it is now:
    - open on that Tab already: placed there;
    - open in another Tab: with `instead` (the model's, from the user's words: "instead", "move it") moved there, the
      person's own Move; otherwise opened there too, and the answer says where else it is still open;
    - not open (saved in Data, or a researcher's picture): opened there from its saved work.
    The window it put there is the one kept (only) or arranged (beside), by its id on screen, never the Data id it was
    asked by. Done only once the studio's report shows it (the tabs broker checks only and beside)."""
    from .brain.registry import ToolFailed
    from .brain.toolbox.present import act
    from .table_window import _tab

    broker = getattr(services, "workspace_tabs", None)
    if broker is None:
        raise ToolFailed("this studio has no Tabs to place it in", next="open the Project's workspace")
    listed = await broker(ctx, {"action": "list"})
    tabs = [t for t in listed.get("tabs") or () if isinstance(t, dict)]
    found = located(tabs, what)
    entry = None
    if not found:
        wire = _report_wire(services, ctx)
        on_screen = any(i.get("id") == what for i in (wire or {}).get("items") or ())
        if on_screen:  # a panel the Tabs do not list (the magnifier, the reader): on the Tab in front, as before
            front_tab = strip(wire).get("tab")
            if placement == "window":
                return await act(services, {"do": "front", "what": what}, ctx=ctx)
            if placement in ("only", "beside") and front_tab:
                reply = await broker(ctx, {"action": "only", "tab": front_tab, "keep": [what]} if placement == "only"
                                     else {"action": "arrange", "tab": front_tab})
                return f"{what} is {'the only window' if placement == 'only' else 'side by side with what is open'} " \
                       f"in the Tab in front ({reply.get('shown')})"
        entry = await _saved(services, what)
    # The app's own words for what a researcher or the web made (its `title`), never theirs (`shown`, untrusted).
    title = str((found[0][1].get("title") if found else (
        entry.title if getattr(entry, "untrusted", False) else (getattr(entry, "shown", "") or entry.title))) or what)
    if placement == "new-tab":  # a new Tab is asked for: made, and told apart from one already called that
        tab_id, tab_title, made = await _tab(services, ctx, tab or title, new=True)
    elif tab:
        tab_id, tab_title, made = await _tab(services, ctx, tab)
    else:
        front = next((t for t in tabs if t.get("selected")), None)
        if front is None:
            raise ToolFailed("the studio has not said which Tab is in front", next="list the Tabs, then ask again")
        tab_id, tab_title, made = str(front["id"]), str(front.get("title") or ""), False
    where = f'Tab "{inline(tab_title, 80)}"' + (" (a new Tab)" if made else "")
    here = [item for t, item in found if t.get("id") == tab_id]
    elsewhere = [(t, item) for t, item in found if t.get("id") != tab_id]
    if placement == "replace":  # in place of the window in front there, which closes first
        from .workspace_observation import front as front_of

        before = front_of(_report_wire(services, ctx) or {}).get("window")
        mine = {window_id(i) for i in here}
        if before and before not in mine:
            await act(services, {"do": "close", "what": before}, ctx=ctx)
    how, also = "", []
    if here:
        keep = window_id(here[0])
    elif elsewhere and instead:
        source_tab, item = elsewhere[0]
        await broker(ctx, {"action": "move", "tab": tab_id, "from": str(source_tab["id"]), "placement": str(item["placement"])})
        keep = window_id(item)
        how = f'; moved there from Tab "{inline(str(source_tab.get("title") or ""), 80)}", where it is no longer open'
        also = [str(t.get("title") or "") for t, _ in elsewhere[1:]]
    else:
        if entry is None:
            shows = (elsewhere[0][1].get("resource") or {}).get("id")
            if not shows:
                raise ToolFailed(f"{inline(what, 60)} is open in another Tab and keeps no saved work to open here",
                                 next="say instead to move it here")
            entry = await _saved(services, str(shows))
        keep = await _open_here(services, ctx, entry, tab_id, title=title)
        how = f"; opened there from its saved work ({entry.id})"
        also = [str(t.get("title") or "") for t, _ in elsewhere]
    if placement in ("only", "new-tab"):
        reply = await broker(ctx, {"action": "only", "tab": tab_id, "keep": [keep]})
        rest = "the live view floats small in its corner" if tab_id == "main" else "the live view is hidden there"
        said = f"{inline(title, 80)} is now the only window in {where}, in front; {rest} ({keep})"
        shown = (reply.get("shown") or {}).get("windows")
        if shown is not None and not any(k in shown for k in (keep, f"document:{keep}")):
            said += f"; the studio lists there: {', '.join(map(str, shown[:4]))}"
    elif placement == "beside":
        await broker(ctx, {"action": "arrange", "tab": tab_id})
        said = f"{inline(title, 80)} is side by side with what is open in {where}, the live view included ({keep})"
    else:  # window, replace: in front there
        if not how.startswith("; opened"):
            await act(services, {"do": "front", "what": keep}, ctx=ctx)
        said = f"{inline(title, 80)} is open in a window in {where}, in front ({keep})"
    said += how
    if also:
        names = ", ".join(f'"{inline(t, 60)}"' for t in also[:3])
        said += (f"; it is also still open in Tab {names} (instead moves it from there, rather than opening it here "
                 "too)")
    return said
