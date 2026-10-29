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
"""
from __future__ import annotations

from .untrusted import inline

PLACEMENTS = ("new-tab", "beside", "overlay", "window", "replace", "close", "only")

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
    """What an index entry is on screen, by its kind (and a result's media: a reference photo, a web page or video)."""
    if kind == "result":
        if media in ("web-page", "video"):
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
    """Whether the report shows `thing` on Tab `tab` side by side with what is there: its window shown, and neither it
    nor any other window there (the live view included, when it floats) covering a quarter of another."""
    shown = windows_on(wire, tab)
    wanted = set(thing)
    mine = [w for w in shown if identities(w) & wanted]
    if not mine:
        return False, "it is not shown there"
    rects = [w.get("rect") for w in shown]
    specimen = (tab_of(wire, tab) or {}).get("specimen") or {}
    if specimen.get("state") == "float" and specimen.get("rect"):
        rects.append(specimen["rect"])
    elif specimen.get("state") == "fill":
        return False, "the live view fills the Tab behind it"
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            if _overlap(a, b) > 0.25:
                return False, "its windows still overlap"
    return True, ""


def holding(wire: dict | None, kind: str) -> list[dict]:
    """The open Tabs holding a `kind` (the Tab strip's counts by kind)."""
    from .workspace_observation import tabs_holding

    return tabs_holding(wire or {}, kind)


# --- present's placement: one word for anything shown ------------------------------------------------------------------
async def place(services, ctx, registry, args: dict) -> str:
    """`present` with a placement (the one vocabulary): what it names put where it was asked, through the same tools
    that put each kind there, each done only when the studio's report shows it.

    - A population (eN) or motion item (mN): overlay draws it on the live specimen (present show); new-tab, beside,
      only, replace and window put its outlines on their capture with its table (show_measurements' placements).
    - A window on screen (w-…, document:…): beside arranges its Tab; only keeps it alone there; window brings it to the
      front; close closes it.
    """
    import re

    from .brain.registry import ToolFailed
    from .brain.toolbox.present import act

    placement = str(args.get("placement") or "")
    if placement not in PLACEMENTS:
        raise ToolFailed(f"{placement!r} is not a place for it", next="say one of: " + ", ".join(PLACEMENTS))
    what = args.get("what") if isinstance(args.get("what"), str) else ""
    what = what.strip()
    tab = " ".join(str(args.get("tab") or "").split())[:80] or None
    if re.fullmatch(r"[em]\d+", what):
        if placement == "overlay":
            return await act(services, {"do": "show", "what": what}, ctx=ctx)
        if placement == "close":
            return await act(services, {"do": "hide", "what": what}, ctx=ctx)
        tool = registry.tools.get("show_measurements")
        if tool is None:
            raise ToolFailed("its table cannot be opened here", next="present show draws its outlines on the specimen")
        return await tool.handler(ctx, {"of": what, "placement": placement, **({"tab": tab} if tab else {})})
    if not what:
        raise ToolFailed("name what to place: its id from the screen or Data", next="look at what is on screen")
    if placement == "overlay":
        raise ToolFailed("only outlines and layers are drawn on the specimen; this is a window",
                         next="put it beside the live view instead (placement beside)")
    if placement == "close":
        return await act(services, {"do": "close", "what": what}, ctx=ctx)
    if placement == "window":
        return await act(services, {"do": "front", "what": what}, ctx=ctx)
    if placement in ("beside", "only", "new-tab", "replace"):
        broker = getattr(services, "workspace_tabs", None)
        if broker is None:
            raise ToolFailed("this studio has no Tabs to place it in", next="open the Project's workspace")
        from .screen import screen_of

        observed = screen_of(services.hub).observation(ctx.client) if getattr(ctx, "client", None) else None
        wire = observed.wire() if observed is not None else None
        front_tab = strip(wire).get("tab")
        if not front_tab:
            raise ToolFailed("the studio has not said which Tab is in front", next="look at the screen")
        if placement == "only":
            reply = await broker(ctx, {"action": "only", "tab": front_tab, "keep": [what]})
            home = front_tab == "main"
            rest = "the live view floats small in its corner" if home else "the live view is hidden there"
            return f"{what} is now the only window in the Tab in front; {rest} ({reply.get('shown')})"
        if placement == "beside":
            reply = await broker(ctx, {"action": "arrange", "tab": front_tab})
            return f"{what} is side by side with what is open in the Tab in front, the live view included ({reply.get('shown')})"
        raise ToolFailed(f"{placement} is not installed for an open window yet",
                         next="use beside, only, window or close for an open window")
    raise ToolFailed(f"{placement} is not installed for this", next="say one of: " + ", ".join(PLACEMENTS))
