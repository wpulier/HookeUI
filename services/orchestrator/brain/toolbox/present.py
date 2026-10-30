"""One set of verbs for anything on screen (contract 10; Amendment 2, points 2-5; Amendment 4, items 4-6):
`present` shows, hides, fades, colours, fills, narrows to a selection, opens, closes, moves, resizes or
brings forward a presentation (orchestrator/screen.py), and is done only once the studio's report shows
it. `look_at_screen` looks at the composed workspace when the report leaves a doubt. Nothing here is
specific to one kind of data: a kind that does not take a verb says so, with what it takes.

`act` is the verbs' one implementation, so anything else that changes what is on screen (the studio's own
layer controls, M1.5c) goes through it."""
from __future__ import annotations

import asyncio

from ..registry import Registry, Tool, ToolContext, ToolFailed, schema
from ...screen import REPORT_NEXT
from ...workspace_observation import ObservationError

COLOURS = {"pink": "#f979e8", "yellow": "#ffdc64", "cyan": "#38c3f7", "green": "#22c55e", "white": "#ffffff",
           "orange": "#ffa500", "red": "#e74c3c", "blue": "#1e90ff", "magenta": "#ff1493"}
LIVE_FLOOR = 0.15  # the live view dims to this at most: it is the microscope, never hidden (Amendment 4.5)
MAGNIFIER_MIN_W = 160  # the studio's narrowest magnifier (magnifier.ts resizeTo)
# How long a window change waits for a studio in the background to be in view again, within its request's life
# (ticket.MAX_TICKET_S): after it, the change was sent and applied but is said not confirmed, never "could not".
BACKGROUND_WAIT_S = 300.0
# A window change with no report since it was sent asks the studio for one this often while it waits (`screen-ask`).
ASK_REPORT_S = 0.5
# A report that disagrees with the app about a window an action touches is asked for again once, waiting at most this
# for the studio's next report, before the action's checks run on it (`_native_present`).
CAUGHT_UP_S = 1.0


def _no_report_since(result, actual, fence) -> bool:
    """The comparison found no report newer than the command (REPORT_STALE): none taken from the studio since."""
    return (result.get("status") == "unobserved" and (result.get("error") or {}).get("code") == "REPORT_STALE"
            and actual is not None and type(actual.get("seq")) is int and actual["seq"] <= fence["afterReportSeq"])
# A look at the screen that did not happen says only that, and that it says nothing about what the user sees (live
# 2026-10-28: a capture past its deadline became "I can't see your screen ... try refreshing", then "quit and reopen the
# app"); the windows the studio reports still say what is shown.
LOOK_NEXT = "ask again in a moment"


def _unlooked(error) -> str:
    """A capture refused by the report path, in words that claim nothing about the user's screen."""
    return (f"no picture of the workspace was taken: the studio's latest report could not be used for one ({error.code}); "
            "this says nothing about what the user sees")
OPACITY_WORDS = {"faint": 0.25, "fade": 0.35, "faded": 0.35, "dim": 0.35, "dimmed": 0.35, "half": 0.5, "full": 1.0,
                 "solid": 1.0}


def opacity_of(value) -> float:
    """An opacity from 0 to 1: a number, a percent ("40%"), or a word (faint, faded, half, full). Above 1
    without "%" is refused, never read as a percent (M1.5b review item 3)."""
    text = str(value if value is not None else "").strip().lower()
    if text in OPACITY_WORDS:
        return OPACITY_WORDS[text]
    percent = text.endswith("%")
    try:
        v = float(text.rstrip("%").strip())
    except ValueError:
        raise ToolFailed(f"{value!r} is not an opacity", next="say a number from 0 to 1, a percent, or faded, half or full") from None
    if percent:
        v /= 100
    elif v > 1:
        raise ToolFailed(f"{value!r} is more than full opacity", next="say a number from 0 to 1, or a percent like 40%")
    return max(0.0, min(1.0, v))


SELECTORS = ("members", "by", "top", "order", "where", "outliers", "rest")
SELECTION_ARGS = {"population": frozenset(SELECTORS),
                  "tracks": frozenset(k for k in SELECTORS if k != "outliers")}
ARGS = ("what", "do", "value", "column", "rect", "over", "revision") + SELECTORS  # column: the colour verb's, as value
TAKEN_BY = {"value": ("opacity", "colour"), "column": ("colour",), "rect": ("move", "resize"),
            "over": ("move", "resize")}  # each argument and the verbs that use it
FOR = {"value": "fading or colouring", "column": "colouring them", "rect": "moving or resizing",
       "over": "putting something over the view, which is moving or resizing it"}
# A key that is one of present's own words given as if it were an argument ("opacity": 0.5): said as its value.
AS_VALUE = ("opacity", "colour", "color", "fill", "level")


def unused_args(args: dict, kind: str, verb: str) -> tuple[str, str] | None:
    """(why, next step) when `args` holds something this verb on this kind does not use, else None: never dropped
    without a word (going live, G3: "fade the largest outline" faded every outline and said so, its by, top and
    order unused). Conservative: refused, nothing changed, and the words say what was not used and, where it can,
    which argument to use. A field left at its default (top 0, an order with nothing to sort by) is nothing asked;
    the colour verb's `by` is the column it colours by ("colour them by size")."""
    if "revision" in args and (kind != "population" or verb not in ("show", "open")):
        return "revision selects a retained population view", "open the population's original or current revision"

    def given(k: str) -> bool:
        v = args.get(k)
        if v is None or v is False or (isinstance(v, (str, list, dict, tuple)) and not v):
            return False
        return not (k == "top" and not isinstance(v, bool) and isinstance(v, (int, float)) and v == 0)

    def shown(k: str) -> str:
        v = args[k]
        text = ", ".join(map(str, v)) if isinstance(v, (list, tuple)) else (
            ", ".join(f"{a} {b}" for a, b in v.items()) if isinstance(v, dict) else str(v))
        return f"{k} {text}"[:60]

    selection = [k for k in SELECTORS if given(k)]
    if verb == "colour":
        selection = [k for k in selection if k != "by"]  # what it colours by, as value or column
    if "order" in selection and (not given("by") or verb == "colour"):
        selection.remove("order")  # an order with nothing to sort by: a default, not a choice
    consumed = SELECTION_ARGS.get(kind, ()) if verb == "emphasise" else ()
    unused = [k for k in selection if k not in consumed]
    unknown = [k for k in args if k not in ARGS]
    misplaced = [k for k, verbs in TAKEN_BY.items() if given(k) and verb not in verbs]
    if not unknown and not unused and not misplaced:
        return None
    from ...screen import NAMES, verb_words

    name = NAMES.get(kind, kind)
    parts = []
    if unused:
        rest = ("outlier selection is not implemented for motion tracks" if kind == "tracks" and verb == "emphasise"
                else "keeping some and fading the rest is the verb emphasise" if kind in SELECTION_ARGS
                else "keeping some and fading the rest is for the cells' layer")
        parts.append(f"{name} can't be {verb_words(verb)} for only some ({'; '.join(shown(k) for k in unused)}): {rest}")
    for k in unknown:
        hint = f": give it as value ({shown(k).split(' ', 1)[1]})" if k.lower() in AS_VALUE else ""
        parts.append(f"{shown(k)} is not something a change on screen takes{hint}")
    for k in misplaced:
        parts.append(f"{shown(k) if k != 'over' else 'over'} is for {FOR[k]}, not {verb_words(verb)}")
    if unused and kind == "population":
        step = "say which cells to keep and fade the rest, or say it for all of them"
    elif unused:
        step = "say it for all of them, or which cells to keep and fade the rest"
    elif any(k.lower() in AS_VALUE for k in unknown):
        step = "say it again with the amount or colour as the value"
    else:
        step = "say what to change, and how"
    return ("; ".join(parts) + ", so nothing was changed", step)


def _plain_column(column: str) -> str:
    from ...screen import COLUMN_WORDS

    return COLUMN_WORDS.get(column, column)


def category_style(population, column: str, gid: str | None) -> tuple[dict, str]:
    """The layer coloured by a category column (K1): a member's look group, or its your label. Each category
    its colour in the legend's order, with its count; a member without one is drawn grey. The group the column
    is read from goes with the style (`columnGroup`), so a correction that makes it stale takes it off
    (Layer.forget_stale_style). The words name the picture model."""
    from collections import Counter

    from ...enrichment import model_label
    from ...population import PLAIN_COLUMNS

    base, _, model = column.partition("@")
    group = population.columns.get(gid) or {}
    live = population.live()
    if base == "group":
        order = list(group.get("sizes") or {})
        categories = [{"id": g, "n": group["sizes"][g]} for g in order]
        label = f"look groups ({model_label(model)})"
        words = f"coloured by look group ({model_label(model)}, {len(order)} groups), with a legend"
    else:
        counts = Counter(str(v) for v in (population.value(m.id, column) for m in live) if v is not None)
        order = sorted(counts, key=lambda c: (-counts[c], c))
        categories = [{"id": c, "n": counts[c], "label": c} for c in order]
        plain = PLAIN_COLUMNS.get(base, base)
        label = f"{plain} ({model_label(model)})" if model else plain
        words = f"coloured by {label} ({len(order)} {'name' if len(order) == 1 else 'names'}), with a legend"
    if not categories:
        raise ToolFailed(f"no cell of {population.title} has a {PLAIN_COLUMNS.get(base, base)} to colour by",
                         next="colour them by look group, or by size")
    values = {}
    for m in live:
        v = population.value(m.id, column)
        if v is not None and str(v) in order:
            values[m.id] = order.index(str(v))
    return ({"colour": "category", "column": base, "columnGroup": gid, "columnExecution": group.get("execution"),
             "legend": {"label": label, "categories": categories}, "values": values}, words)


def _original_screen(screen, ctx, target=None):
    """Existing accepted request ownership, never a newer peer's screen."""
    if ctx is None or ctx.client is None and ctx.request_snapshot is None:
        return {}
    admission = getattr(ctx, "admission", None)
    scope = {"client": ctx.client, "snapshot": ctx.request_snapshot,
             "request_id": admission.request_id if admission is not None else ctx.request_id}
    try:
        screen.request_report(scope["snapshot"], scope["client"], request_id=scope["request_id"], target=target)
    except ObservationError as exc:
        raise ToolFailed(str(exc), next=REPORT_NEXT) from None
    return scope


async def _original_layer_ready(services, screen, ctx, scope, pid, kind, *, shown_here=False):
    """Observe an owned layer before changing its style; no peer/latest fallback. `shown_here`: this act has just
    shown it, so the studio is waited for to draw it (live 2026-10-11: Made today's "show cells" was refused at once,
    the last report still having only the layer shown before)."""
    if not scope:
        return
    from ..registry import CURRENT_RUN
    from ... import ticket as tickets
    from ...apply import blocked
    from ...motiontracks import track_layer_of
    from ...population import layer_of

    try:
        report = screen.request_report(**scope, target=pid)
    except ObservationError as exc:
        raise ToolFailed(str(exc), next=REPORT_NEXT) from None
    row = next((row for row in report["items"] if row["id"] == pid), None)
    request = tickets.current()
    identifier = pid.split(":", 1)[1]
    run = CURRENT_RUN.get()
    occurrence = run.occurrence if run is not None else None
    steps = (*ctx.prior_steps, ctx.steps)
    produced = any(prior.ok is True and prior.occurrence is not None
        and occurrence is not None and prior.occurrence.request_id == occurrence.request_id
        and identifier in step.produced.get(number, ())
        for step in steps for number, prior in step.runs.items())
    # A missing old target does not become a future target. Only an actual
    # successful producer from this same original request can wait for its root.
    if not screen._structural_item(row) and not produced and not shown_here:
        raise ToolFailed("original studio has not represented this structural target",
                         next=REPORT_NEXT)
    layer = track_layer_of(services.hub) if kind == "tracks" else layer_of(services.hub)

    def guard():
        if request is None or tickets.current() is not request or request.closed or request.expired():
            return "the original layer request is no longer current"
        reason = blocked(services.hub, request, "motion" if kind == "tracks" else "cell")
        if reason:
            return reason
        if layer.shown != identifier:
            return "the original produced layer is no longer shown"
        return None

    try:
        await screen.request_item_ready(**scope, target=pid, guard=guard)
    except ObservationError as exc:
        raise ToolFailed(str(exc), next=REPORT_NEXT) from None


async def act(services, args: dict, by: str | None = None, ctx=None) -> str:
    """One verb on one thing on screen (or an index entry), done only once the studio's report shows it.
    `by`: who asked (a request, "brain", or "user" from the studio's own controls)."""
    from ... import ticket as tickets
    from ...apply import apply
    from ...evidence import evidence_of
    from ...population import SHOWN_COLOUR, column_of, layer_of, select, value as member_value
    from ...screen import NAMES, VERBS, UnclearReference, resolve, screen_of, verb_words, why_not

    hub = services.hub
    screen = screen_of(hub)
    scope = _original_screen(screen, ctx)  # before lookup, shared state mutation or any causal dispatch

    def current_report():
        return screen.request_report(**scope) if scope else screen.latest

    def shown(pid):
        return next((item for item in (current_report() or {}).get("items", []) if item["id"] == pid), None)

    verb = str(args.get("do") or "").strip().lower()
    if verb not in VERBS:
        raise ToolFailed(f"{verb!r} is not something done to what is on screen", next="say one of: " + ", ".join(VERBS))
    what = str(args.get("what") or "")
    from ...screen import UNNAMED_WINDOW, frontmost

    if verb in ("close", "hide") and " ".join(what.lower().split()) in UNNAMED_WINDOW:
        # "Close that" (or "hide that"): the window in front, as every closing tool takes it (screen.frontmost).
        front = frontmost((current_report() or {}).get("items"))
        if front is not None and native_window(services, front["id"]):
            return await close_window(services, ctx, str(front["id"]), by=by)
        if front is not None:
            what, verb = str(front["id"]), "close"
    from ...resources import ENTRY_ID, resources_of

    # An index entry it may name, read off the event loop (the index reads its stores; K3 review SF1); words never are one.
    named = None
    if ENTRY_ID.fullmatch(what.strip()):
        index = resources_of(hub)
        lookup_day = index.session()  # the session open now (ADR 0017)
        named = await asyncio.to_thread(index.get, what.strip(), lookup_day)
        if named is not None and named.state == "unavailable":
            raise ToolFailed(named.summary, next="restore the retained data, then reopen the session")
    try:
        pid, kind = resolve(hub, what, ctx, entry=named)
    except UnclearReference as exc:
        raise ToolFailed(str(exc), next="say which one") from None
    except ValueError as exc:
        raise ToolFailed(str(exc), next="ask what is on screen") from None
    if kind == "entry" and verb in ("close", "hide"):
        return await close_entry(services, ctx, pid.split(":", 1)[1], named, current_report(), by=by)
    _original_screen(screen, ctx, pid)  # a replaced original root must fail before shared state changes
    from ...data_reader import READER_SURFACES

    if pid in READER_SURFACES:  # the Data reader (ticket 66, D6): its × closes it; it does not move
        if verb in ("close", "hide"):
            shown_now = next((i for i in (current_report() or {}).get("items", []) if i.get("id") == pid), None)
            if shown_now is None or not shown_now.get("open"):
                raise ToolFailed("the reader is not open, so there is nothing of it to close", next="ask what is on screen")
            return await reader_close(services, ctx, shown_now)
        raise ToolFailed(f"the reader can't be {verb_words(verb)}: it sits on the right of the Tab",
                         next="close it, or open something else in it")
    asked = verb
    if kind in ("population", "tracks") and verb in ("open", "close"):
        # Cell outlines and motion tracks are layers: shown on the picture, and closed by hiding them ("close the
        # tracks"; M3.2), done once the report shows it (flow 7). Opened where the studio has the Data reader, they
        # open there as their Data row does (ticket 66, D6; _layer_in_reader); elsewhere, by showing them (Made
        # today's Open; M1.5c review item 2).
        verb = "show" if verb == "open" else "hide"
    refused = why_not(kind, verb) or unused_args(args, kind, verb)
    if refused:
        raise ToolFailed(refused[0], next=refused[1])
    if kind in ("population", "tracks") and asked in ("open", "close") and args.get("revision") is None:
        routed = await _layer_in_reader(services, ctx, screen, pid, asked, named, current_report())
        if routed is not None:
            return routed
    if kind in ("population", "tracks") and verb not in ("show", "hide"):
        await _original_layer_ready(services, screen, ctx, scope, pid, kind)
    if current_report() is None:
        raise ToolFailed("no studio is showing the view now, so nothing on screen can change",
                         next=REPORT_NEXT)
    if by is None:
        request = tickets.current()
        by = f"request:{request.id}" if request is not None else "brain"
    from ...showing import Stopped, late_words, put_back_late, showings_of

    try:
        by = showings_of(hub).owner_for(by)  # a beat of the request that started a showing is the showing's (showing.py)
    except Stopped as exc:
        raise ToolFailed(f"{exc}: nothing more of it is shown", next="ask to show it again") from None
    name = NAMES.get(kind, kind)
    values: dict = {}
    words = verb_words(verb)
    undo = None  # what puts back what the server changed, when the studio never shows the change
    confirmed = None  # what the server keeps once the studio shows the change (the layer's last shown style)

    def item(report, key=pid):
        return next((i for i in report.get("items", []) if i.get("id") == key), None)

    def check(report) -> bool:
        return True

    if kind == "entry":
        return await open_entry(services, pid.split(":", 1)[1], screen, by, ctx=ctx, entry=named, verb=verb)
    kept = None  # the motion tracks acted on (kind "tracks")
    sent_kind = kind  # the kind the change goes to the studio as
    if kind == "tracks":
        from ...motiontracks import COLUMN_WORDS as TRACK_WORDS, colour_style, column_of as track_column, \
            select as select_tracks, track_layer_of, tracks_of

        tid = pid.split(":", 1)[1]
        kept = tracks_of(hub).get(tid)
        if kept is None:
            raise ToolFailed(f"{tid} is not motion kept today", next="ask what data there is")
        name = kept.title
        tlayer = track_layer_of(hub)
        if verb == "show":
            import asyncio as _asyncio

            wire = await _asyncio.to_thread(tracks_of(hub).wire, kept)  # its points may be read from disk: off the loop
            shown_after = screen.reported_at  # "drawn" is confirmed by a report after this (S1)
            done = await apply(hub, lambda _: tlayer.show(kept, wire), scope="motion")
            if not done.ok:
                raise ToolFailed(f"not shown: {done.reason}", next="ask again")
            await _original_layer_ready(services, screen, ctx, scope, pid, kind, shown_here=True)
            values = {"visible": True}

            def check(r):
                return bool((item(r) or {}).get("visible"))
        elif verb == "hide":
            done = await apply(hub, lambda _: tlayer.hide(), scope="motion")
            if not done.ok:
                raise ToolFailed(f"not hidden: {done.reason}", next="ask again")
            values = {"visible": False}

            def check(r):
                return not (item(r) or {}).get("visible")
        else:
            if tlayer.shown != tid:
                raise ToolFailed(f"{name} is not shown now", next="show it first")
            was = dict(tlayer.style)
            showings_of(hub).before_layer(pid, was, by)
            style = dict(tlayer.style)
            if verb == "opacity":
                style["opacity"] = opacity_of(args.get("value"))
                words = f"at {round(style['opacity'] * 100)}% opacity"
            elif verb == "colour":
                choice = str(args.get("value") or args.get("column") or args.get("by") or "speed").strip().lower()
                if choice in ("id", "each", "by track", "distinct", "own", "own colours"):
                    style.update(colour="id")
                    for key in ("legend", "values", "value", "column"):
                        style.pop(key, None)
                    words = "coloured each in its own colour"
                elif choice in COLOURS:
                    style.update(colour="uniform", value=COLOURS[choice])
                    for key in ("legend", "values", "column"):
                        style.pop(key, None)
                    words = f"coloured {choice}"
                else:
                    column = track_column(choice)
                    if column is None:
                        raise ToolFailed(f"tracks have no {choice} measured", next="colour them by net speed, path speed, "
                                                                                 "straightness or one colour")
                    style.update(colour_style(kept, column))
                    style.pop("value", None)
                    words = f"coloured by {TRACK_WORDS[column]}, with a legend"
            elif verb == "emphasise":
                chosen, how = select_tracks(kept, ids=args.get("members"), where=args.get("where"), by=args.get("by"),
                                            order=str(args.get("order") or "desc"), top=args.get("top"))
                if not chosen:
                    raise ToolFailed(f"none of {kept.title} fits that", next="ask for the fastest or the straightest ones instead")
                rest = "hide" if str(args.get("rest") or "").lower().startswith("hid") else "fade"
                style["emphasis"] = {"members": [r["id"] for r in chosen], "rest": rest}
                words = f"{len(chosen)} kept" + (f" ({how})" if how else "") + f", the rest {'hidden' if rest == 'hide' else 'faded'}"
            done = await apply(hub, lambda _: setattr(tlayer, "style", style), scope="motion")
            if not done.ok:
                raise ToolFailed(f"not changed: {done.reason}", next="ask again")
            tlayer.broadcast(tlayer.wire())
            changed = [k for k in ("opacity", "colour", "column", "value", "emphasis") if style.get(k) != was.get(k)]
            values = {k: style.get(k) for k in changed}

            def undo():
                if tlayer.style is style and tlayer.shown == tid:  # only while still this change's (re-check N4)
                    tlayer.style = dict(tlayer.confirmed_style)
                    tlayer.broadcast(tlayer.wire())

            def confirmed(style=style):
                if tlayer.style is style:
                    tlayer.confirmed_style = dict(style)

            def check(r, style=style):
                shown = item(r) or {}
                got = shown.get("style") or {}
                if verb == "opacity":
                    return abs(float(shown.get("opacity", -1)) - style["opacity"]) < 0.02
                if verb == "colour":
                    return (got.get("colour") == style["colour"] and got.get("column") == style.get("column")
                            and got.get("value") == style.get("value"))
                return set((got.get("emphasis") or {}).get("members") or []) == set(style["emphasis"]["members"]) \
                    and (got.get("emphasis") or {}).get("rest") == style["emphasis"]["rest"]
    elif kind == "population":
        layer = layer_of(hub)
        eid = pid.split(":", 1)[1]
        population = evidence_of(hub).get(eid)
        if population is None:
            raise ToolFailed(f"{eid} is not a population kept today", next="ask what data there is")
        name = population.title
        if verb == "show":
            if "revision" in args and (type(args["revision"]) is not int or args["revision"] not in (0, population.revision)):
                raise ToolFailed("that population revision is not available", next="open its original or current revision")
            if args.get("revision") == 0 and population.original is None:
                raise ToolFailed("the original measurements were not retained for this older corrected result",
                                 next="inspect its current revision and recorded correction history")
            await asyncio.to_thread(evidence_of(hub).require_retained, population)
            done = await apply(hub, lambda _: layer.show(population), scope="cell")
            if not done.ok:
                raise ToolFailed(f"not shown: {done.reason}", next="ask again")
            await _original_layer_ready(services, screen, ctx, scope, pid, kind, shown_here=True)
            retained_words, retained_url = await _on_their_view(services, layer, population, revision=args.get("revision"))
            from ...screen import magnifier_arrangement
            retained_shown = magnifier_arrangement(retained_url, None, [retained_url]) if retained_url else lambda r: False
            values = {"visible": True}

            def check(r):
                # A configured live layer cannot acknowledge a retained-image presentation.
                return retained_shown(r) if retained_url else bool((item(r) or {}).get("visible"))
        elif verb == "hide":
            done = await apply(hub, lambda _: layer.hide(), scope="cell")
            if not done.ok:
                raise ToolFailed(f"not hidden: {done.reason}", next="ask again")
            values = {"visible": False}

            def check(r):
                return not (item(r) or {}).get("visible")
        else:
            if layer.shown != eid:
                raise ToolFailed(f"{population.title} is not shown now", next="show it first")
            was = dict(layer.style)
            showings_of(hub).before_layer(pid, was, by)  # a showing's first beat on it: the style to put back
            style = dict(layer.style)
            if verb == "opacity":
                style["opacity"] = opacity_of(args.get("value"))
                words = f"at {round(style['opacity'] * 100)}% opacity"
            elif verb in ("fill", "edge"):
                style["fill"] = verb == "fill"
            elif verb == "colour":
                # What it colours by, as value, column or by ("colour them by size"; the review of G3, SF3).
                choice = str(args.get("value") or args.get("column") or args.get("by") or "id").strip().lower()
                if choice in ("id", "each", "by member", "distinct", "own", "own colours"):
                    style.update(colour="id")
                    for key in ("legend", "values", "value", "column", "columnGroup", "columnExecution"):
                        style.pop(key, None)
                    words = "coloured each in its own colour"
                elif choice in COLOURS:
                    style.update(colour="uniform", value=COLOURS[choice])
                    for key in ("legend", "values", "column", "columnGroup", "columnExecution"):
                        style.pop(key, None)
                    words = f"coloured {choice}"
                else:
                    word = choice.replace("by ", "")
                    # Any column the cells have, its case kept; a look group or your label from the one grouping
                    # every reader uses; refused, with the reason, when it was made before a correction (K1 review).
                    column = column_of(word, population, services.hub)
                    gid, _group = population.column_group(column) if column else (None, None)
                    if population.column_type(column) == "category":
                        made, words = category_style(population, column, gid)
                        style.update(made)
                        style.pop("value", None)
                    else:
                        numbers = {m.id: member_value(population, m, column) for m in population.live()}
                        numbers = {k: float(v) for k, v in numbers.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
                        if not numbers:
                            raise ToolFailed(f"{population.title} has no {word} measured",
                                             next="colour them by size, elongation or roundness, or measure them first")
                        low, high = min(numbers.values()), max(numbers.values())
                        span = (high - low) or 1.0
                        from ...population import UNITS

                        style.update(colour="column", column=column, columnGroup=gid,
                                     columnExecution=(_group or {}).get("execution"),
                                     legend={"label": _plain_column(column), "min": round(low, 3), "max": round(high, 3),
                                             "unit": UNITS.get(column, "")},
                                     values={k: round((v - low) / span, 3) for k, v in numbers.items()})
                        style.pop("value", None)
                        words = f"coloured by {_plain_column(column)}, with a legend"
            elif verb == "emphasise":
                notes: list[str] = []
                chosen, _how = select(population, ids=args.get("members"), where=args.get("where"), by=args.get("by"),
                                      order=str(args.get("order") or "desc"), top=args.get("top"), outliers=args.get("outliers"),
                                      hub=services.hub, notes=notes)
                if not chosen:
                    raise ToolFailed(f"none of {population.title} fits that", next="ask for the largest or the most elongated ones instead")
                rest = "hide" if str(args.get("rest") or "").lower().startswith("hid") else "fade"
                style["emphasis"] = {"members": [m.id for m in chosen], "rest": rest}
                # Why these: what the selection actually used (re-check N8: "keep c4 and c6" was said "(largest)").
                reasons = []
                if args.get("members"):
                    reasons.append("the ones named")
                if args.get("where"):
                    if any(column_of(k) != SHOWN_COLOUR for k in args["where"]):
                        reasons.append("in the ranges given")
                    reasons += [h for h in (_how or "").split(", ") if h.startswith("drawn ")]  # "drawn red" (G3)
                if args.get("outliers") and "outliers of" in (_how or ""):  # only when select used them (5 or more)
                    reasons.append(f"far from the usual {_plain_column(column_of(args.get('outliers')))}")
                if args.get("by"):
                    order = "smallest" if "smallest" in (_how or "") else "largest"  # the order select used
                    reasons.append(f"{order} by {_plain_column(column_of(args.get('by')))}")
                why_these = f" ({', '.join(reasons)})" if reasons else ""
                words = f"{len(chosen)} kept{why_these}, the rest {'hidden' if rest == 'hide' else 'faded'}" + "".join(
                    f"; {n}" for n in notes)
            done = await apply(hub, lambda _: setattr(layer, "style", style), scope="cell")
            if not done.ok:
                raise ToolFailed(f"not changed: {done.reason}", next="ask again")
            layer.broadcast(layer.wire())
            changed = [k for k in ("opacity", "fill", "colour", "column", "value", "emphasis") if style.get(k) != was.get(k)]
            values = {k: style.get(k) for k in changed}

            def undo():
                # Only while the style is still this change's and the same population is shown: a later
                # change, or another population shown since, is never overwritten; and back to what the
                # studio last showed, not to another change that never showed (M1.5b re-check N4).
                if layer.style is style and layer.shown == eid:
                    layer.style = dict(layer.confirmed_style)
                    layer.broadcast(layer.wire())

            def confirmed(style=style):
                if layer.style is style:
                    layer.confirmed_style = dict(style)

            def check(r, style=style):
                shown = item(r) or {}
                got = shown.get("style") or {}
                if verb == "opacity":
                    return abs(float(shown.get("opacity", -1)) - style["opacity"]) < 0.02
                if verb in ("fill", "edge"):
                    return got.get("fill") == style["fill"]
                if verb == "colour":
                    return (got.get("colour") == style["colour"] and got.get("column") == style.get("column")
                            and got.get("value") == style.get("value"))
                return set((got.get("emphasis") or {}).get("members") or []) == set(style["emphasis"]["members"]) \
                    and (got.get("emphasis") or {}).get("rest") == style["emphasis"]["rest"]
    elif kind == "live":
        level = opacity_of(args.get("value"))
        if level < LIVE_FLOOR:
            raise ToolFailed(f"the live view is never hidden: it dims to {round(LIVE_FLOOR * 100)}% at most",
                             next="say a value from 0.15 to 1")
        values = {"opacity": level}
        words = f"at {round(level * 100)}% opacity"

        def check(r):
            return abs(float((item(r) or {}).get("opacity", -1)) - values["opacity"]) < 0.02
    elif kind == "outlines":
        if verb in ("show", "hide"):
            values = {"visible": verb == "show"}

            def check(r):
                return bool((item(r) or {}).get("visible")) == values["visible"]
        elif verb == "opacity":
            values = {"opacity": opacity_of(args.get("value"))}
            words = f"at {round(values['opacity'] * 100)}% opacity"

            def check(r):
                return abs(float((item(r) or {}).get("opacity", -1)) - values["opacity"]) < 0.02
        else:  # colour, fill, edge: the outlines' own settings, as set_style sets them (kept until changed)
            from ...options import OUTLINE_COLORS
            from .display import outline_style

            if verb == "colour":
                colour = str(args.get("value") or "").strip().lower()
                if colour not in OUTLINE_COLORS:
                    raise ToolFailed(f"outlines can be {', '.join(OUTLINE_COLORS)}, not {colour or 'that'}", next="say one of those colours")
                done_words = await outline_style(services, color=colour)
            else:
                done_words = await outline_style(services, style="classic" if verb == "fill" else "edge")
            return "; ".join(done_words)
    elif kind in ("magnifier", "viewer", "slidemap", "card", "made-today", "sidebar", "sessions", "document"):
        shown_now = shown(pid) or {}
        if verb in ("move", "resize", "front") and not shown_now.get("open"):
            raise ToolFailed(f"{name} is not open", next="open it first")
        if kind in ("sidebar", "sessions") and verb in ("close", "hide"):
            values = {"open": False} if verb == "close" else {"visible": False}

            def check(r):
                got = item(r) or {}
                return not got.get("open") or got.get("visible") is False
        elif kind in ("sidebar", "sessions") and verb == "open":
            values = {"open": True}

            def check(r):
                got = item(r) or {}
                return bool(got.get("open")) and got.get("visible") is not False
        elif verb == "close":
            if kind == "magnifier" and services.magnifier is not None:
                services.magnifier.close()
            elif kind == "viewer" and services.presenter is not None:
                (services.presenter.for_client(ctx.client) if ctx is not None and ctx.client is not None
                 else services.presenter).close()
            elif kind == "slidemap" and services.slidemap is not None:
                services.slidemap.set_open(False)
                hub.broadcast(services.slidemap.wire())
            values = {"open": False}

            def check(r):
                return not (item(r) or {}).get("open")
        elif verb == "open":
            if kind == "slidemap" and services.slidemap is not None:
                services.slidemap.set_open(True)
                hub.broadcast(services.slidemap.wire())
            elif kind == "magnifier":
                selected = getattr(hub.scene, "selected", None)
                if services.magnifier is None or not selected:
                    raise ToolFailed("the magnifier opens on something", next="say what to magnify")
                services.magnifier.zoom(selected)
            elif kind == "viewer":
                raise ToolFailed("the result viewer opens on a researcher's work", next="say which work to show")
            values = {"open": True}

            def check(r):
                return bool((item(r) or {}).get("open"))
        elif verb in ("move", "resize"):
            rect = args.get("rect")
            # Over the view, or over what it magnifies, only because the user asked for it there (the model's call):
            # kept there until it is moved again. A place nobody asked for keeps clear of it (going live, G3).
            over = asked_over(args)
            live_rect, now_rect = (shown("live") or {}).get("rect"), shown_now.get("rect")
            if rect is None and over and verb == "move" and _rect(live_rect) and _rect(now_rect):
                # "Over the live view", with no place given: centred on it, as wide as it is now.
                rect = [live_rect[0] + (live_rect[2] - now_rect[2]) / 2, live_rect[1] + (live_rect[3] - now_rect[3]) / 2,
                        now_rect[2], now_rect[3]]
            if not _rect(rect):
                raise ToolFailed(f"where to {verb} it is a rect [x, y, width, height] in screen px",
                                 next="say where, for example top left, or how big")
            values = {"rect": [round(float(v)) for v in rect], **({"over": True} if over else {})}
            want = values["rect"]
            if kind == "magnifier" and verb == "resize" and want[2] < MAGNIFIER_MIN_W:
                raise ToolFailed(f"the magnifier is at least {MAGNIFIER_MIN_W} px wide", next=f"say a width of {MAGNIFIER_MIN_W} px or more")
            parts = (0, 1) if verb == "move" else (2, 3) if kind == "document" else (2,)  # a move is where it is; a resize its width (its height follows)
            was_rect = shown_now.get("rect")
            if kind == "magnifier" and isinstance(was_rect, list) and len(was_rect) == 4:
                def undo():
                    # Not where or how big it was asked to be: back to how it was before, its own floating
                    # place included (re-check: a put-back as a move pinned it there).
                    screen.send(pid, kind, verb, {"restore": True}, by, **scope)

            def check(r):
                got = (item(r) or {}).get("rect") or [0, 0, 0, 0]
                return all(abs(got[k] - want[k]) <= 6 for k in parts)
        elif verb == "front":
            values = {"front": True}

            def check(r):
                return (item(r) or {}).get("z", -1) >= max((i.get("z") or 0) for i in r.get("items", []))
    try:
        change = screen.send(pid, sent_kind, verb, values, by, **scope)
        applied = await screen.confirm(change, check)
    except BaseException as exc:
        if undo is not None:
            undo()  # only this still-owned style/target; never a later change
        if isinstance(exc, ObservationError):
            raise ToolFailed(str(exc), next=REPORT_NEXT) from None
        raise
    if applied:
        if showings_of(hub).noted(change, screen) == "late":  # confirmed after its showing was stopped: put back
            put, left = await put_back_late(services, change)
            raise ToolFailed(late_words(put, left), next="ask to show it again")
    else:
        if undo is not None:
            undo()  # the change did not show: what the server changed is put back, as the screen still is
        if kind == "magnifier" and verb in ("move", "resize") and change.id in screen.applied and not values.get("over"):
            # A place nobody asked to have over what it magnifies keeps clear of it: never a refusal of the user's
            # own request for it there (that is `over`; going live, G3).
            raise ToolFailed(f"the magnifier did not go {'there' if verb == 'move' else 'that wide'}: unless asked, it keeps "
                             "clear of what it magnifies", next="say if you want it over it anyway, or another place or size")
        shown_words = "narrowed to that selection" if verb == "emphasise" else words  # never "is not 3 kept (…)"
        raise ToolFailed(f"{name} {'are' if kind == 'outlines' else 'is'} not {shown_words} on screen: the studio has not shown it",
                         next="look at the studio, or ask again")
    if confirmed is not None:
        confirmed()
    if kind == "population" and verb == "show":
        return f"{name} {words}" + retained_words
    if kind == "tracks" and verb == "show":
        return f"{name} {words}" + await _tracks_where(services, kept, shown_after, ctx=ctx)
    if kind == "tracks" and verb == "opacity":
        from ...motiontracks import drawn_live

        reported = shown(pid) or {}
        if not drawn_live(reported):
            # The studio acknowledges the configured layer style even while no
            # live paths are drawn. Its captured fallback is a separate JPEG.
            return (f"{name} live-layer style {words}; not drawn on the live view now "
                    f"({reported.get('placement') or 'placement not reported'}); captured image unchanged by this style setting")
    if values.get("over"):
        # Where it went, as asked, and what it covers there, as the studio reports it.
        shown_after = shown(pid) or {}
        obj = hub.scene.objects.get(shown_after.get("shows") or "") if shown_after.get("shows") else None
        what = f"the {obj.label}" if obj is not None and obj.label else "what it magnifies"
        return (f"put {name} {'over the view' if verb == 'move' else 'that size'}, as you asked"
                + (f"; it covers {what}" if shown_after.get("covers") else ""))
    return f"{name} {words}"


async def _tracks_where(services, kept, after: float, *, ctx=None) -> str:
    """Motion tracks just shown: drawn over the live view only once their place is verified (contract 3) and
    the studio reports them drawn there (S1); otherwise on the view they were measured on, in the magnifier,
    labelled as that, and said."""
    from ...motiontracks import drawn_or_their_view, on_their_view, track_layer_of

    if ctx is not None and (ctx.client is not None or ctx.request_snapshot is not None):
        from ...screen import screen_of
        from ...motiontracks import drawn_live
        screen = screen_of(services.hub)
        scope = _original_screen(screen, ctx)
        report = screen.request_report(**scope)
        item = next((i for i in report.get('items', []) if i.get('id') == f'tracks:{kept.id}'), {})
        if drawn_live(item):
            return '; drawn over the requesting studio live view where their place is checked'
        result = await _native_present(services, ctx, {'do': 'open',
            'source': {'day': kept.day, 'id': kept.id, 'version': None, 'picture': 0},
            'title': f'{kept.title}: captured motion'})
        return '; ' + result + '; measured on the retained capture, not the current live view'
    layer = track_layer_of(services.hub)
    if not layer.placement:
        await layer.check()  # the first check of their place, now
    if layer.shown != kept.id:
        return ""
    if layer.current_camera():
        return '; ' + layer.current_camera_words()
    if layer.verified():
        said = await drawn_or_their_view(services, kept, after)
        return "; " + ("drawn over the live view where their place is checked" if said.startswith("drawn") else said)
    return "; " + await on_their_view(services, kept, str(layer.placement.get("why") or ""))


def _rect(rect) -> bool:
    return isinstance(rect, list) and len(rect) == 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in rect)


def asked_over(args: dict) -> bool:
    """Whether the request asked for the panel over the view or over what it shows (`over`: the model's reading of
    the user's words; a place nobody asked for keeps clear)."""
    return args.get("over") is True or str(args.get("over") or "").strip().lower() in ("true", "yes")


async def _on_their_view(services, layer, population, *, revision=None) -> tuple[str, str | None]:
    """Cells shown while none of them is in place on the live view (the slide moved, the view changed): they
    are shown on the view they were made on, in the magnifier, real pixels, and that is said (contract 3's
    captured view; acceptance flow 5 on footage: "shown" with nothing drawn)."""
    import asyncio as _asyncio
    import time as _time

    from ...population import drawn_on_stage

    # Kept on an original image the Stage does not show: never drawn there, so not waited for. Found on the still the
    # Stage shows, it is drawn there once its first check matches the Stage's picture (S5a), as live cells are.
    kept = bool(population.view.get("sourceImage")) and not drawn_on_stage(services.hub, population)
    for _ in range(10):  # the layer's first check of their places (every 0.3 s)
        if (layer.placement or layer.shown != population.id or kept
                or getattr(services.hub, "latest_info", None) is None):
            break
        await _asyncio.sleep(0.15)
    verified, total = layer.verified_count()
    if layer.shown != population.id or verified and revision is None:
        return "", None
    why = next((p.get("why") for p in layer.placement.values() if p.get("why")), "")
    magnifier = getattr(services, "magnifier", None)
    if magnifier is None:
        if population.view.get("sourceImage"):
            raise ToolFailed("the retained image viewer is unavailable", next="open it from Data instead")
        return f"; none of the {total} is in place on the live view now" + (f" ({why})" if why else ""), None
    from ...population import captured_picture
    from ...screen import picture_shown
    from ...media import CAPTURE, RAW

    current_revision = population.revision
    members = ([m["id"] for m in population.original["members"]] if revision == 0 and population.original is not None
               else [m.id for m in population.live()])
    jpeg = await _asyncio.to_thread(captured_picture, services.hub, population, members)
    if layer.shown != population.id or population.revision != current_revision:
        raise ToolFailed("the population changed while its retained view was being opened", next="open its current revision again")
    if jpeg is None:
        if population.view.get("sourceImage"):
            raise ToolFailed("the retained specimen image could not be read", next="restore its saved image and retry")
        return f"; none of the {total} is in place on the live view now" + (f" ({why})" if why else ""), None
    when = _time.strftime("%H:%M", _time.localtime(population.view.get("at") or population.made_at))
    try:
        label = (f"{'Original' if revision == 0 else 'Current'} revision {revision} · {len(members)} members · "
                 if revision is not None else f"Current revision {current_revision} · {len(members)} members · ")
        url = await picture_shown(magnifier, jpeg, label + f"{population.title} on the view they were made on ({when}): real pixels, not the live view",
                            kind_of=RAW if population.view.get("sourceImage") else CAPTURE)
    except ToolFailed:
        raise ToolFailed("the retained picture was not shown in the magnifier", next="look at the studio, then open the saved result again") from None
    if revision is not None:
        return f"; retained revision {revision} shown with {len(members)} members; no analysis rerun", url
    return (f"; none of the {total} is in place on the live view now" + (f" ({why})" if why else "") +
            f", so they are shown on the view they were made on ({when}) in the magnifier"), url


async def open_entry(services, eid: str, screen, by: str, *, ctx=None, entry=None, verb: str = "open") -> str:
    """An index entry opened as the presentation its kind is shown in (screen.PRESENTED_AS): in a studio with the Data
    reader, the kinds Data lists open there, on the Tab the person is on (data_reader.py; ticket 66, D6), and cell
    outlines and motion tracks too when opened (`verb` open; shown, they are drawn on the picture)."""
    import asyncio as _asyncio
    import copy

    from ...data_reader import reader_studio
    from ...resources import resources_of
    from ...screen import READER, presented
    from ...ticket import current
    from .resources import require_current

    hub = services.hub
    index = resources_of(hub)
    day = entry.day if entry is not None else index.session()  # its session
    request = current()
    day_owner = getattr(services, "day", None)
    generation = getattr(day_owner, "generation", None)
    scope = _original_screen(screen, ctx)

    def original_owner():
        if current() is not request or request is not None and request.closed:
            raise ToolFailed("the original reopen request is no longer active", next="ask to open the resource again")
        require_current(ctx, day_owner)
        if index.session() != day or day_owner is not None and not day_owner.current(generation):
            raise ToolFailed("the session changed while the saved resource was being opened", next="ask again in the current session")
        _original_screen(screen, ctx)

    if entry is None:
        entry = await _asyncio.to_thread(index.get, eid, day)  # the index reads its stores, never on the loop
    original_owner()
    if entry is None:
        raise ToolFailed(f"nothing today is called {eid}", next="ask what data there is")
    entry = copy.deepcopy(entry)
    studio = reader_studio(screen, ctx, index.session()) if screen is not None else None
    shown_as, why = presented(entry.kind, studio is not None)
    if shown_as is None:
        raise ToolFailed(f"{entry.title} is not shown on screen: {why}", next="ask to read it instead")
    if studio is not None and (shown_as == READER or verb == "open" and shown_as in ("population", "tracks")):
        return await _open_in_reader(index, entry, studio, original_owner)
    if shown_as == "tracks":  # motion tracks open as their layer, live where verified (M3.2)
        return await act(services, {"what": f"tracks:{entry.id}", "do": "show"}, by=by, ctx=ctx)
    chronology = await _asyncio.to_thread(index.chronology, entry)
    original_owner()
    if shown_as == "viewer" and scope:
        return await _native_present(services, ctx, {'do': 'open',
            'source': {'day': entry.day, 'id': entry.id, 'version': entry.version, 'picture': None},
            'title': entry.title})
    if shown_as == "viewer":
        if services.presenter is None:
            raise ToolFailed("there is no result viewer here", next="ask to read it instead")
        try:
            services.presenter.show(entry.id)
        except KeyError as exc:
            raise ToolFailed(f"{entry.title} has nothing to show yet ({exc})", next="ask to read it instead") from None
        target = "viewer"

        def check(r):
            return any(i.get("id") == "viewer" and i.get("open") and i.get("shows") == entry.id for i in r.get("items", []))
    elif shown_as == "magnifier":
        found = await _asyncio.to_thread(index.picture, entry.id, 0, entry.day)
        original_owner()
        await _asyncio.to_thread(index.require_original, entry)
        original_owner()
        if not found or services.magnifier is None:
            raise ToolFailed(f"{entry.title} has no picture to show", next="ask to read it instead")
        data, media = found
        if media != "image/jpeg":
            import cv2
            import numpy as np

            decoded = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            data = cv2.imencode(".jpg", decoded, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes() if decoded is not None else b""
        if not data:
            raise ToolFailed(f"{entry.title}'s picture could not be read", next="ask to read it instead")
        from ...screen import magnifier_arrangement

        from ...media import CAPTURE, RAW, REFERENCE, line as media_line

        web = (entry.made_by or {}).get("web") if entry.media == REFERENCE else None
        if web is not None:  # a reference photo from the web: said and kept as one, never as the camera's (kind (f))
            url = services.magnifier.show_picture(data, f"{entry.title}: {media_line(REFERENCE)}", kind_of=REFERENCE,
                                                  credit=str(web.get("credit") or ""))
        elif entry.media == RAW:
            url = services.magnifier.show_picture(data, f"{entry.title}: retained original image", kind_of=RAW)
        else:
            what = {"bookmark": "the camera's view when it was kept, made smaller",
                    # Correction 5: shown in brainreg's own output space, as it registered it, never over the raw slices.
                    "job": "three slices of the volume as brainreg downsampled and reoriented it, the registered atlas's "
                           "boundaries drawn, in that space",
                    "volume": "the middle slice of the volume as you added it"}.get(entry.kind, "a picture the app made")
            url = services.magnifier.show_picture(data, f"{entry.title}: {what}, not the live view", kind_of=CAPTURE)
        target = "magnifier"
        services.magnifier.identify_sources([services.magnifier.picture_now()], {"id": entry.id, "day": entry.day, "title": entry.title})
        check = magnifier_arrangement(url, None, [url])
    else:  # a population: its layer
        from ...apply import apply
        from ...evidence import evidence_of
        from ...population import layer_of

        population = evidence_of(hub).get(entry.id)
        done = await apply(hub, lambda _: layer_of(hub).show(population), scope="cell")
        if not done.ok:
            raise ToolFailed(f"not shown: {done.reason}", next="ask again")
        target = f"layer:{entry.id}"

        def check(r):
            return any(i.get("id") == target and i.get("visible") for i in r.get("items", []))
    values = {"open": True, "shows": entry.id}
    if target == "magnifier":
        values["source"] = url  # what the studio reports for a picture (its `shows` is the outline it follows)
    original_owner()
    change = screen.send(target, "entry", "open", values, by, **scope)
    if not await screen.confirm(change, check):
        raise ToolFailed(f"{entry.title} is not open on screen: the studio has not shown it", next="look at the studio, or ask again")
    from ...showing import late_words, put_back_late, showings_of

    if showings_of(hub).noted(change, screen) == "late":  # confirmed after its showing was stopped: put back
        put, left = await put_back_late(services, change)
        raise ToolFailed(late_words(put, left), next="ask to show it again")
    original_owner()
    await _asyncio.to_thread(index.require_original, entry)
    original_owner()
    from ..registry import CURRENT_RUN
    run = CURRENT_RUN.get()
    if run is not None:
        run.resource = {"id": entry.id, "day": entry.day, "title": entry.title, "operation": "reopened",
                        "chronology": chronology, "byteAssociation": "not independently verified by this reopen"}
    return f"Opened existing {entry.title} ({entry.id}, {entry.day}); its original artifact was retrieved, not made again"


async def _open_in_reader(index, entry, studio, original_owner) -> str:
    """`entry` in the Data reader on the Tab the person is on, done once the studio's report shows it there
    (data_reader.py), with the same receipt as any reopen."""
    import asyncio as _asyncio

    from ...data_reader import open_in_reader, words
    from ..registry import CURRENT_RUN

    chronology = await _asyncio.to_thread(index.chronology, entry)
    original_owner()
    state, _item, visible = await open_in_reader(studio, entry, original_owner)
    await _asyncio.to_thread(index.require_original, entry)
    original_owner()
    run = CURRENT_RUN.get()
    if run is not None:
        run.resource = {"id": entry.id, "day": entry.day, "title": entry.title, "operation": "reopened",
                        "chronology": chronology, "byteAssociation": "not independently verified by this reopen"}
    return words(entry, state, visible)


async def _layer_in_reader(services, ctx, screen, pid: str, verb: str, named, report) -> str | None:
    """Cell outlines or motion tracks opened (or closed) where the studio has the Data reader: open puts their index
    entry in the reader, as their Data row does, and close closes the reader while it shows them. None where that does
    not apply (no reader, no entry, nothing of them in the reader): they are shown or hidden as layers."""
    from ...data_reader import reader_showing, reader_studio
    from ...resources import resources_of

    layer = pid.split(":", 1)[1]
    if verb == "close":
        shown_now = reader_showing(report, layer)
        return await reader_close(services, ctx, shown_now) if shown_now is not None else None
    index = resources_of(services.hub)
    if reader_studio(screen, ctx, index.session()) is None:
        return None
    entry = named if named is not None and named.id == layer else await asyncio.to_thread(index.get, layer, index.session())
    if entry is None:
        return None
    return await open_entry(services, entry.id, screen, "brain", ctx=ctx, entry=entry, verb="open")


async def reader_close(services, ctx, item: dict) -> str:
    """Closes the Data reader showing `item` (its surface in the studio's report), done once no reader shows it."""
    from ...data_reader import close_in_reader, reader_studio
    from ...resources import resources_of
    from ...screen import screen_of
    from .resources import require_current

    screen = screen_of(services.hub)
    studio = reader_studio(screen, ctx, resources_of(services.hub).session())
    if studio is None:
        raise ToolFailed("the reader is not in the studio that asked", next="close it with its ×")

    def guard():
        require_current(ctx, getattr(services, "day", None))
        _original_screen(screen, ctx)

    await close_in_reader(studio, item, guard)
    return "closed the reader; what it showed stays in Data"


async def reshow_outlines(services, ctx=None) -> str:
    """Restore outlines on their requesting browser; unknown receipt is not hidden. Drawn on the live view, they are
    brought into sight first: Main to the front when the Tab in front hides the specimen (placement.live_in_sight)."""
    from ...placement import live_in_sight

    in_sight = await live_in_sight(services.hub, ctx) if ctx is not None else ""
    return in_sight + await _outlines_shown(services, ctx)


async def _outlines_shown(services, ctx=None) -> str:
    from ...screen import laid_out, screen_of

    screen = screen_of(services.hub)
    uncertain = "; outline visibility is not confirmed in the originating studio"

    def observed():
        scope = _original_screen(screen, ctx, "outlines")
        report = screen.request_report(**scope, target="outlines") if scope else screen.latest
        row = next((item for item in (report or {}).get("items", []) if item.get("id") == "outlines"), None)
        return report, row

    try:
        before, item = observed()
    except (ToolFailed, ObservationError):
        return uncertain
    if item is None:
        return uncertain if ctx is not None and ctx.client is not None else ""
    if item.get("visible", True) or laid_out(item):
        # Shown, or shown in a studio in the background (another tab or app in front), which paints nothing and
        # so reports nothing visible: the outlines are on there, and show once it is in view. Nothing to change.
        return ""
    try:
        await act(services, {"what": "outlines", "do": "show"}, ctx=ctx)
    except ToolFailed:
        # A timeout/retirement says nothing about pixels. A later current report
        # can establish visibility, but does not manufacture the missing ACK.
        try:
            after, item = observed()
        except (ToolFailed, ObservationError):
            return uncertain
        if item is not None and after is not None and after["at"] > (before or {}).get("at", 0):
            if item.get("visible") is True:
                return ""
            if item.get("visible") is False:
                return "; the originating studio reports the outlines are hidden"
        return uncertain
    return "; the outlines were hidden, shown again"


def _window_ref(ctx, value):
    from ..registry import typed_step_output
    if isinstance(value, dict) and set(value) == {"step", "output"}:
        return typed_step_output(ctx, value, kind="presentation")["instanceId"]
    if isinstance(value, dict) and set(value) == {"instanceId"}:
        value = value["instanceId"]
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ToolFailed("name an exact window or an earlier presentation output",
                         next="name the window you want to change")
    return value


def _sight_capture(ctx, value):
    """Resolve only the original detached observation, never a live outline."""
    import re
    from ...sight import SightSelection
    from ...presentations import PresentationError
    from ...ticket import current

    if (set(value) != {'sight'} or not isinstance(value['sight'], str)
            or not re.fullmatch(r's[1-8]', value['sight'])):
        raise PresentationError('invalid_action', 'open needs one exact original running-look ID')
    selected = ctx.sight_selection
    if not isinstance(selected, SightSelection) or selected.view is None:
        raise PresentationError('source_unavailable', 'this request has no original running look')
    if selected.error:
        raise PresentationError('source_unavailable', selected.error)
    view, capture = selected.view, selected.capture
    thing = next((item for item in view.things if item.id == value['sight']), None)
    if thing is None:
        raise PresentationError('source_unavailable', 'that thing was not in the original running look')
    ticket = current()
    if (capture is None or capture.id != view.frame_id or ticket is None
            or ticket.held(capture.id) is not capture):
        raise PresentationError('source_unavailable', 'the original running-look picture is no longer held')
    return capture, thing.box, {'sight': {'id': thing.id, 'at': view.at},
                                'epoch': view.epoch, 'offset': [view.dx, view.dy]}


def _native_ref(services, value, ctx=None):
    """Freeze an explicit producer, including an old research output's own ID."""
    from ...presentations import ResourceRef, SourceRef, PresentationError
    if isinstance(value, dict) and ("step" in value or "output" in value):
        from ..registry import typed_step_output
        value = typed_step_output(ctx, value, kind="source")["source"]
    if isinstance(value, dict) and "key" in value:
        return SourceRef.from_wire(value)
    if isinstance(value, dict) and set(value) == {"motion", "live"} and value["live"] is True:
        from ...motiontracks import track_layer_of
        return SourceRef.from_wire(track_layer_of(services.hub).continuation_source(value["motion"]))
    ref = ResourceRef.from_wire(value)
    if set(value) - {"day", "id", "version", "picture", "outputId"}:
        raise PresentationError("invalid_action", "source has undeclared address fields")
    if "outputId" not in value:
        return ref
    from ...resources import resources_of, _research_picture_source
    from ... import media
    index = resources_of(services.hub)
    oid = value["outputId"]
    if not isinstance(oid, str) or not oid or len(oid) > 256:
        raise PresentationError("source_identity_mismatch", "research output needs its exact bounded ID")
    manager = index.research
    session = manager.get(ref.id) if manager is not None else None
    if session is None or session.day != ref.day:
        session = next((s for s in manager.read_day(ref.day, kept_only=False) if s.rid == ref.id), None) if manager else None
    output = session.output(oid) if session is not None else None
    if output is not None and output.get('kind') == 'presentation' and ref.picture is None:
        from ...presentation_delivery import written_source
        return written_source(session, ref, oid)[0]
    if output is None or output.get("id") != oid or output.get("kind") != "image":
        raise PresentationError("source_unavailable", "that exact research image is unavailable")
    basis = _research_picture_source(session, output)
    if ref.version != basis["version"]:
        raise PresentationError("source_identity_mismatch", "research output revision differs")
    return SourceRef.from_wire({"key": f"{ref.day}/{ref.id}/{oid}/v{ref.version}",
        "kind": "saved-image", "media": media.of_output(output), "availability": "retained",
        "identity": {"resource": dict(value)}, "provenance": basis})


def _window_action(ctx, args):
    from ...presentations import PresentationAction, PresentationError
    op = args.get("do")
    op = "arrange" if op == "beside" else op
    allowed = {"open": {"source", "title", "relation"}, "duplicate": set(), "split": set(), "close": set(),
               "front": set(), "zoom": {"zoom"}, "pan": {"center"}, "move": {"position"},
               "resize": {"size"}, "arrange": {"targets", "relation"},
               "opacity": {"value"}, "hide": set(), "restore": set(), "group": {"targets"}, "ungroup": set()}
    if op not in allowed:
        raise PresentationError("capability_unsupported", "this window does not support that operation")
    if set(args) - (allowed[op] | {"do", "what", "instance"}):
        raise PresentationError("invalid_action", "window operation has unused arguments")
    if "what" in args and "instance" in args:
        raise PresentationError("invalid_action", "window operation has competing target references")
    if op in ("arrange", "group") and ("what" in args or "instance" in args):
        raise PresentationError("invalid_action", "arrange names its windows only in targets")
    reference = args.get("instance", args.get("what"))
    iid = _window_ref(ctx, reference) if reference is not None else None
    if op not in ("open", "arrange", "group") and iid is None:
        raise PresentationError("invalid_action", "this operation needs an exact window reference")
    values = {}
    if op == "open":
        if "title" in args:
            values["title"] = args["title"]
        if 'relation' in args:
            relation = args['relation']
            if not isinstance(relation, dict) or 'reference' not in relation:
                raise PresentationError('invalid_action', 'initial placement needs an exact relation')
            values['relation'] = {**relation, 'reference': _window_ref(ctx, relation['reference'])}
    elif op == "zoom":
        values = args.get("zoom")
        if not isinstance(values, dict):
            raise PresentationError("invalid_action", "zoom needs level, factor or fit")
    elif op == "opacity":
        raw = args.get("value")
        try:
            values = {"value": float(str(raw).rstrip('%')) / (100 if str(raw).endswith('%') else 1)}
        except (TypeError, ValueError):
            raise PresentationError("invalid_action", "opacity needs a fraction or percentage")
    elif op == "group":
        targets = args.get("targets")
        if not isinstance(targets, list):
            raise PresentationError("invalid_action", "group needs window targets")
        values = {"targets": [_window_ref(ctx, v) for v in targets]}
    elif op == "pan":
        values = {"center": args.get("center")}
    elif op in ("move", "resize"):
        key, fields = ("position", ("x", "y")) if op == "move" else ("size", ("width", "height"))
        point = args.get(key)
        if not isinstance(point, dict) or set(point) != set(fields):
            raise PresentationError("invalid_action", f"{key} needs {fields[0]} and {fields[1]}")
        values = {key: [point[f] for f in fields]}
    elif op == "arrange":
        targets, relation = args.get("targets"), args.get("relation")
        if not isinstance(targets, list) or not 1 <= len(targets) <= 8 or not isinstance(relation, dict):
            raise PresentationError("invalid_action", "arrange needs bounded targets and a relation")
        relation = dict(relation)
        if "reference" in relation:
            relation["reference"] = _window_ref(ctx, relation["reference"])
        values = {"targets": [_window_ref(ctx, v) for v in targets], "relation": relation}
    return PresentationAction(op, instance=iid, values=values)


def _window_expectations(message, presentation_area=None, before_report=None, arrangement=None):
    """Use the existing closed completion vocabulary; no model or extra judge."""
    from ...completion import tab_window

    expected = []
    targets, op = message["targets"], message["op"]
    def add(predicate, ids, values, phase="drawn", tolerance=None):
        expected.append({"id": f"{message['commandId']}:{len(expected)}", "predicate": predicate,
                         "targets": ids, "expected": values, "essential": True,
                         "requiredPhase": phase, "tolerance": tolerance})
    if op == "close":
        add("closed", targets, {"value": True})
        return expected
    if op == "hide" or op == "opacity" and message["instances"][0]["view"]["opacity"] == 0:
        value = {"hidden": True} if op == "hide" else {"opacity": 0.0}
        add("view_state", targets, value, phase="committed")
        add("visible", targets, {"value": False}, phase="committed")
        add("open", targets, {"value": True}, phase="committed")
        return expected
    if op in ("group", "ungroup"):
        # Membership is observed on each original registered window even when
        # responsive layout tucks one of them. It does not claim a new media draw.
        # Exact source/payload and causal property-owner fences still apply.
        for state in message["instances"]:
            add("source_exact", [state["id"]], {"sourceRef": state["sourceRef"]}, phase="committed")
            add("view_state", [state["id"]], {"group": state["view"]["group"]}, phase="committed")
        add("open", targets, {"value": True}, phase="committed")
        return expected
    for state in message["instances"]:
        add("source_exact", [state["id"]], {"sourceRef": state["sourceRef"]})
    add("media_ready", targets, {"decoded": True, "drawn": True})
    add("visible", targets, {"value": not (op == "opacity" and message["instances"][0]["view"]["opacity"] == 0)})
    if op in ("opacity", "restore"):
        key = {"opacity": "opacity", "restore": "hidden"}[op]
        for state in message["instances"]:
            add("view_state", [state["id"]], {key: state["view"][key]})
    if op in ("open", "duplicate", "split"):
        for state in message["instances"]:
            controls = ["close", "move", "resize"]
            if state["sourceRef"]["kind"] in ("live", "saved-image", "composite"):
                controls += ["zoom", "pan"]
            add("control", [state["id"]], {"each": controls, "independent": True})
    if op == "split":
        add("independent_windows", targets, {"count": len(targets), "distinctRoots": True})
        add("content_size", targets, {"minWidth": 240, "minHeight": 180})
    fit_size = False
    if op == "zoom":
        add("zoom", targets, {"value": message["instances"][0]["view"]["zoom"]})
        for state in message["instances"]:
            fence = message["fence"]["targets"][state["id"]]
            changed = {key for key, value in fence["committedPropertyVersions"].items()
                       if value != fence["basePropertyVersions"].get(key)}
            if "pan" in changed:
                add("pan", [state["id"]], {"center": state["view"]["pan"]})
            fit_size = fit_size or "geometry" in changed
    if op == "pan":
        add("pan", targets, {"center": message["instances"][0]["view"]["pan"]})
    if op == "front":
        scope = (before_report or {}).get("foreground")
        own_roots = {row.get("rootId") for row in (before_report or {}).get("items", []) if row["id"] in targets}
        if (scope and scope.get("scope") == "workspace-windows"
                and own_roots & {root.get("rootId") for root in scope.get("roots") or ()}):
            add("front", targets, {"scope": scope["scope"], "hostId": scope["hostId"],
                "above": [row["rootId"] for row in scope["roots"] if row["rootId"] not in own_roots]})
        elif scope and scope.get("scope") == "workspace-windows":
            # The window lives inside one of the host's roots, not as a root of its own: in the Project workspace
            # every native window is mounted in the study artifacts beside the document windows (main.ts
            # onPresentation), so the stage's foreground never lists it and could never prove it in front (will-leaf,
            # 30 Oct: front on the open reference photo timed out, and the user was told to ask again). Its Tab's own
            # order proves it: in front is above every window shown on that Tab (workspace_observation.front, the
            # window highest on the Tab), by the z the studio measured for each.
            add("front", targets, {"scope": "tab-windows", "above": [
                row["id"] for row in before_report.get("items", [])
                if row["id"] not in targets and tab_window(row) and row.get("open") is True
                and row.get("visible") is True and row.get("connected") is True
                and type(row.get("z")) in (int, float)]})
        else:
            above = [row['id'] for row in (before_report or {}).get('items', [])
                     if row['id'] not in targets and row.get('open') and row.get('visible') and row.get('connected')]
            add("front", targets, {"above": above})
    if (op in ("move", "resize") or fit_size) and len(message["instances"]) == 1:
        state = message["instances"][0]
        geometry = state["geometryIntent"]
        size = geometry["size"]
        if op == "move" and size is None and before_report is not None:
            import math
            original = next((row for row in before_report["items"] if row["id"] == state["id"]), None)
            target = message["fence"]["targets"][state["id"]]
            rect = original.get("rect") if original is not None else None
            if (original is not None and original.get("open") is True
                    and original.get("visible") is True and original.get("connected") is True
                    and original.get("lifetimeGeneration") == state["lifetimeGeneration"]
                    and original.get("sourceGeneration") == state["sourceGeneration"]
                    and original.get("sourceRef") == state["sourceRef"]
                    and original.get("sourceToken") == target["sourceToken"]
                    and original.get("propertyVersions", {}).get("rect") == target["basePropertyVersions"].get("geometry")
                    and isinstance(rect, (list, tuple)) and len(rect) == 4
                    and all(type(v) in (int, float) and math.isfinite(v) for v in rect)
                    and rect[2] > 0 and rect[3] > 0):
                size = rect[2:]  # original accepted dimensions, never the later draw ACK
        if size is not None and presentation_area is not None:
            # The renderer fits viewport-CSS intent to this issuing report's
            # measured host/viewport intersection. Never infer it from the ACK.
            left, top, width, height = presentation_area
            w, h = max(1, min(size[0], width)), max(1, min(size[1], height))
            x, y = geometry["position"]
            if fit_size and before_report:
                original = next((row for row in before_report["items"] if row["id"] == state["id"]), None)
                if original and original.get("rect"):
                    x, y = original["rect"][:2]
            rect = [max(left, min(x, left + width - w)),
                    max(top, min(y, top + height - h)), w, h]
            add("rect", targets, {"rect": rect}, tolerance=2)
    if op in ("arrange", "open") and arrangement is not None:
        add("rect", targets, {"rect": arrangement}, tolerance=2)
    # Unknown-size geometry still cannot establish the requested rectangle.
    return expected


def _rectangle(value) -> bool:
    import math
    return (isinstance(value, (list, tuple)) and len(value) == 4
            and all(type(v) in (int, float) and math.isfinite(v) for v in value)
            and value[2] > 0 and value[3] > 0)


def _not_shown_where(row, report) -> str:
    """Where a window open but laid out nowhere is, from the report: on another Tab than the one in front (its
    arrangement's Tab and the Tab strip's titles), or plainly not shown there."""
    from ...untrusted import inline
    from ...workspace_observation import front

    tab = (((row or {}).get("view") or {}).get("arrangement") or {}).get("tab")
    now = front(report)
    strip = next((i for i in report.get("items") or () if i.get("id") == "workspace-tabs"), None)
    tabs = ((((strip or {}).get("view") or {}).get("legacy") or {}).get("workspace") or {}).get("tabs") or ()
    titles = {t.get("id"): t.get("title") for t in tabs if isinstance(t, dict)}
    if tab and now.get("tab") and tab != now["tab"]:
        there = f'the "{inline(titles[tab], 80)}" Tab' if titles.get(tab) else "another Tab"
        here = f'the "{inline(now["tabTitle"], 80)}" Tab' if now.get("tabTitle") else "another Tab"
        return f"it is on {there}, and {here} is in front"
    return "it is not shown on the Tab in front"


def _placement_issue(state, row, report):
    """Why the report's `row` cannot place anything against the Core's window `state` now, or None when it can.
    One truth for what is on screen: the Core says what the window is, the report where it is.

    - "disagrees": the report and the Core differ about the window (no row, another lifetime or source, an older
      version or another owner of one of its properties): the report may simply be behind, so Hooke asks the studio
      for a fresh one and looks again before it says so (`_native_present`).
    - "gone": the Core has it closed.
    - "hidden": the same window, open in both, laid out nowhere the person sees (on a Tab not in front, hidden):
      nothing can be placed against it, and nobody changed it.

    Each comes with the check that failed, in words. A studio in the background paints nothing, so no window in it
    reports visible: one laid out shown there (`screen.laid_out`) still anchors, as `completion` counts it shown."""
    from ...screen import laid_out

    if not state.open:
        return "gone", "it was closed"
    if row is None:
        return "disagrees", "the studio's report does not show it"
    if (row.get("lifetimeGeneration") != state.lifetime_generation
            or row.get("sourceGeneration") != state.source_generation):
        return "disagrees", "the studio's report shows another opening of it"
    if row.get("sourceRef") != state.source.to_wire():
        return "disagrees", "the studio's report shows another source in it"
    for k, v in state.property_versions.items():
        name = "rect" if k == "geometry" else k
        if row.get("propertyVersions", {}).get(name) != v:
            return "disagrees", f"the studio's report has another version of its {name} than the app"
        if row.get("propertyOwners", {}).get(name) != state.owners.get(k):
            return "disagrees", f"the studio's report has another owner of its {name} than the app"
    if row.get("open") is not True:
        return "disagrees", "the studio's report shows it closed"
    shown = row.get("visible") is True or (report.get("visible") is False and laid_out(row))
    if row.get("connected") is not True or not shown or not _rectangle(row.get("rect")):
        return "hidden", _not_shown_where(row, report)
    return None


def _placement_observation(states, report, ids):
    """Exact original CSS geometry, shared by initial and later placement. A window that cannot anchor raises with
    `placement` set to why (`_placement_issue`) and its reason in words."""
    import copy
    from ...presentations import PresentationError
    area = report["viewport"].get("presentationArea")
    if not _rectangle(area):
        raise PresentationError("layout_unavailable", "beside needs the original measured visible presentation area",
                                next="show the workspace and ask again")
    rows = {row["id"]: row for row in report["items"]}
    observed = {}
    for iid in ids:
        state, row = states[iid], rows.get(iid)
        issue = _placement_issue(state, row, report)
        if issue is not None:
            kind, why = issue
            error = PresentationError("target_hidden" if kind == "hidden" else "target_revised",
                                      f"window {iid} cannot be placed against: {why}",
                                      next="show that window, then arrange again" if kind == "hidden" else None)
            error.placement, error.why = kind, why
            raise error
        # Placement depends on the original window, source and owned geometry,
        # not the next camera sample or paint receipt. A live anchor redraws while
        # the new source is being prepared without moving or changing owners.
        observed[iid] = {key: copy.deepcopy(row.get(key)) for key in (
            "id", "rootId", "workspaceId", "rect", "open", "visible", "connected",
            "sourceRef", "sourceToken", "sourceGeneration", "lifetimeGeneration",
            "propertyVersions", "propertyOwners")}
    return copy.deepcopy(area), observed


def _opening_basis(relation, states, report, size):
    """Fit only the new child beside its original parent, before first paint."""
    import math
    from ...presentations import PresentationError
    if (set(relation) - {'kind','reference','gapPx','align','side'} or relation.get('kind') != 'beside'
            or relation.get('align','top') != 'top'):
        raise PresentationError('invalid_action', 'initial placement supports an exact top-aligned beside relation')
    area, observed = _placement_observation(states, report, (relation['reference'],))
    anchor = observed[relation['reference']]
    gap = relation.get('gapPx',12)
    if type(gap) not in (int,float) or not math.isfinite(gap) or gap < 0:
        raise PresentationError('invalid_action','initial placement needs a nonnegative finite gap')
    if not isinstance(size,(list,tuple)) or len(size) != 2 or any(type(n) not in (int,float) or n <= 0 or not math.isfinite(n) for n in size):
        raise PresentationError('source_unavailable','initial placement needs original image dimensions')
    left, top, width, height = anchor['rect']
    sides = [relation['side']] if 'side' in relation else ['right','left']
    for side in sides:
        if side not in ('right','left'):
            raise PresentationError('invalid_action','initial placement side must be left or right')
        room = area[0]+area[2]-left-width-gap if side == 'right' else left-area[0]-gap
        available_height = area[1]+area[3]-top
        child_width = min(360,room,max(0,available_height-44)*size[0]/size[1])
        child_height = max(180+44,child_width*size[1]/size[0]+44)
        if child_width < 240 or child_height > available_height or top < area[1]:
            continue
        x = left+width+gap if side == 'right' else left-gap-child_width
        rect = [x,top,child_width,child_height]
        normalized = {'kind':'beside','reference':relation['reference'],'gapPx':gap,'align':'top','side':side}
        return rect, area, anchor, normalized
    raise PresentationError('constraint_conflict','There is not enough space beside this report for its captured view.',
                            next='move or resize the report, or open the capture from Research')


def _arrangement_basis(action, states, report):
    """Resolve beside from original observed CSS geometry, without fitting or resizing."""
    import copy
    import math
    from ...presentations import PresentationError

    relation, targets = action.values["relation"], action.values["targets"]
    if set(relation) - {"kind", "reference", "gapPx", "align", "side"}:
        raise PresentationError("invalid_action", "arrangement has undeclared arguments")
    if (len(targets) != 1 or relation.get("kind") != "beside"
            or relation.get("align", "top") != "top"
            or relation.get("reference") is None or relation["reference"] in targets):
        raise PresentationError("capability_unsupported", "installed beside needs one target and a distinct reference with top alignment",
                                next="choose one window and a distinct reference with top alignment")
    gap = relation.get("gapPx", 12)
    sides = [relation["side"]] if "side" in relation else ["right", "left"]
    if (type(gap) not in (int, float) or not math.isfinite(gap) or gap < 0
            or any(side not in ("left", "right") for side in sides)):
        raise PresentationError("invalid_action", "beside needs a nonnegative finite gap and left or right side")
    area, observed = _placement_observation(states, report, (targets[0], relation["reference"]))
    target, anchor = observed[targets[0]]["rect"], observed[relation["reference"]]["rect"]
    for side in sides:
        x = anchor[0] + anchor[2] + gap if side == "right" else anchor[0] - gap - target[2]
        rect = [x, anchor[1], target[2], target[3]]
        if (not all(math.isfinite(v) for v in rect) or x < area[0] or rect[1] < area[1]
                or x + rect[2] > area[0] + area[2] or rect[1] + rect[3] > area[1] + area[3]):
            continue
        normalized = {**relation, "side": side, "gapPx": gap, "align": "top"}
        return rect, copy.deepcopy(area), observed[relation["reference"]], normalized
    raise PresentationError("constraint_conflict",
        f"the beside placement cannot fit target rect {target} beside reference rect {anchor} "
        f"on {' or '.join(sides)} with gap {gap} in presentation area {area}; their current sizes are unchanged",
        next="resize a window or move the reference, then arrange again")



async def close_entry(services, ctx, entry_id: str, entry, report, by: str | None = None) -> str:
    """Closing an index entry closes what shows it: the Data reader showing it (ticket 66, D6), a native window whose
    source is that entry, else the panel its kind opened in before the reader (screen.WITHOUT_READER) while that panel is
    open; nothing showing it is said (live 2026-10-11: "pull up the snapshot from earlier", then "close that", was
    refused "entry can't be closed" and the snapshot stayed)."""
    import json as _json

    from ...data_reader import reader_showing
    from ...screen import presented

    items = (report or {}).get("items") or []
    title = getattr(entry, "title", None) or entry_id
    in_reader = reader_showing(report, entry_id)  # the Data reader showing it (ticket 66, D6)
    if in_reader is not None:
        return await reader_close(services, ctx, in_reader)
    quoted = _json.dumps(entry_id)
    for item in sorted(items, key=lambda i: -(i.get("z") or 0)):
        source = item.get("sourceRef") or item.get("source")
        if (str(item.get("id", "")).startswith("w-") and item.get("open", True) and source is not None
                and quoted in _json.dumps(source, default=str)):
            return await close_window(services, ctx, str(item["id"]), by=by)
    panel = presented(str(getattr(entry, "kind", "") or ""), reader=False)[0]  # the panel it opened in before the reader
    shown = next((i for i in items if i.get("id") == panel and i.get("open")), None)
    # Only while it shows this entry, never whatever else is in that panel: the viewer says what it shows; the
    # magnifier's picture keeps the entry it was opened from (Magnifier.identify_sources).
    magnifier = getattr(services, "magnifier", None)
    picture = magnifier.picture_now() if panel == "magnifier" and magnifier is not None else None
    showing = shown is not None and (shown.get("shows") == entry_id if panel == "viewer"
                                     else ((picture or {}).get("resource") or {}).get("id") == entry_id)
    if panel in ("magnifier", "viewer") and showing:
        return await act(services, {"what": panel, "do": "close"}, by=by, ctx=ctx)
    raise ToolFailed(f"{title} is not open on screen, so there is nothing of it to close",
                     next="say which window to close, or ask what is on screen")


def native_window(services, item_id) -> bool:
    """Whether `item_id` is a native workspace window this app can command (a "w-" instance), as `present` routes it."""
    return (str(item_id or "").startswith("w-") and getattr(services, "presentations", None) is not None
            and getattr(services, "prepared_sources", None) is not None
            and callable(getattr(services, "workspace_context", None)))


async def close_window(services, ctx, item_id: str, by: str | None = None) -> str:
    """Close one window from the studio's report the way `present` would: a native window by its instance (its own
    lifetime), anything else as a legacy panel. "Close that" through close_magnifier or close_result reached only the
    legacy path, which never matches a native window (live 2026-10-11: TARGET_GONE, the zoom window left open)."""
    if native_window(services, item_id):
        return await _native_present(services, ctx, {"do": "close", "instance": {"instanceId": str(item_id)}})
    return await act(services, {"what": str(item_id), "do": "close"}, by=by, ctx=ctx)


# A change that ends the same however often it is sent (an absolute position, size, centre or order), and the failures
# that are only the screen not showing it in time: Hooke sends it once more itself, never the user (spec 4.8,
# horizontal 3; will-leaf, 30 Oct: "ask again in a moment" was spoken after front timed out).
SAME_TWICE = ("front", "move", "resize", "pan", "hide", "restore", "opacity")
NOT_SHOWN_YET = ("CONFIRM_TIMEOUT", "TOPOLOGY_MISMATCH", "VERIFICATION_UNAVAILABLE", "DISPLAY_UNVERIFIED")


async def _its_tab_in_front(services, ctx, reference) -> None:
    """Front on a window laid out on a Tab not in front (a Project reopened with it on another Tab): its Tab is
    brought forward first, the person's own Tab click, so the window can come to the front where they look. A window
    the report does not place on a Tab, or one already on the Tab in front, is left to front as it is."""
    from ...placement import strip
    from ...screen import screen_of

    broker = getattr(services, "workspace_tabs", None)
    if broker is None:
        return
    try:
        iid = _window_ref(ctx, reference)
    except ToolFailed:
        return  # the action itself says what was wrong with its window reference
    screen = screen_of(services.hub)
    observed = screen.observation(ctx.client) if getattr(ctx, "client", None) is not None else None
    wire = observed.wire() if observed is not None else None
    row = next((i for i in (wire or {}).get("items") or () if i.get("id") == iid), None)
    tab = (((row or {}).get("view") or {}).get("arrangement") or {}).get("tab")
    front = strip(wire).get("tab")
    if not tab or not front or tab == front:
        return
    await broker(ctx, {"action": "switch", "tab": str(tab)})
    if callable(getattr(screen, "refresh", None)):
        await screen.refresh(ctx.client, timeout=CAUGHT_UP_S, newer=True)


async def _native_settled(services, ctx, args):
    """A native window change, sent once more by Hooke when the screen did not show it the first time (SAME_TWICE,
    NOT_SHOWN_YET); a second miss is said as what the screen shows, with nothing for the user to repeat."""
    from ...screen import screen_of
    from ..registry import CURRENT_RUN

    if args.get("do") in ("front", "restore"):
        await _its_tab_in_front(services, ctx, args.get("instance", args.get("what")))
    try:
        return await _native_present(services, ctx, args)
    except ToolFailed as first:
        run = CURRENT_RUN.get()
        failure = (((run.outcome or {}).get("failure") or {}) if run is not None else {})
        if args.get("do") not in SAME_TWICE or failure.get("code") not in NOT_SHOWN_YET:
            raise
        screen = screen_of(services.hub)
        if callable(getattr(screen, "refresh", None)):
            await screen.refresh(ctx.client, timeout=CAUGHT_UP_S, newer=True)
        try:
            return await _native_present(services, ctx, args)
        except ToolFailed as second:
            if args.get("do") not in SAME_TWICE:
                raise
            raise ToolFailed(f"{second}; it was sent twice and the screen still does not show it",
                             next=None) from first  # no next: Hooke already tried again; nothing is the user's to repeat


async def _native_present(services, ctx, args):
    import copy
    from dataclasses import replace
    from ...apply import apply, WORKSPACE, blocked
    from ... import ticket as tickets
    from ...completion import compare
    from ...presentations import PresentationAction, PresentationError, PresentationObservation
    from ...presentation_wire import command, completion_fence, command_facet
    from ...screen import CONFIRM_S, screen_of
    from ...workspace_observation import ObservationError
    from ..registry import CURRENT_RUN, record_output
    run = CURRENT_RUN.get()
    if run is None or run.occurrence is None:
        raise ToolFailed("no original operation owns this workspace request", next=None)  # no next: workspace authority cannot be assigned without its original occurrence
    occurrence = run.occurrence
    original_ticket = tickets.current()
    leases, prepared, effect = [], None, None
    receipts, initial_children = [], {}
    unseen = {"background": False, "unreported": False}  # applied in the background / no report since it was sent
    arrangement = None
    children_receipt, cleanup = None, None
    core, store, screen = services.presentations, services.prepared_sources, screen_of(services.hub)
    goal = run._requested_goal
    origin_client = ctx.client  # the actual accepted origin, never a later context's client
    if goal is not None and goal.report is None:
        def current_report():
            try:
                observed = screen.observation(origin_client)
                if (not screen._current_observation(observed)
                        or screen._observation_clock() - observed.received_at < 0):
                    return None
                return screen.completion_report(origin_client)
            except ObservationError:
                return None
        goal.report = current_report

    def bind_goal(facet):
        if facet is None:
            return
        row = facet["row"]
        expected, unresolved = goal.expected(run, row["presentationIds"], publishes=facet["publishes"])
        if facet["compensation"]:
            expected, unresolved = [], False
        row["goal"]["expected"] = expected
        row["goal"]["confirmation"] = None
        if expected and not unresolved and facet["comparedAt"] is not None:
            proof = compare(expected, facet["report"], facet["fence"], now=facet["comparedAt"])
            # Cancellation/guard failures override both comparisons; no desired
            # condition can revive a command that lost its original authority.
            if facet["authorityError"] is not None:
                proof = {**proof, "status": "failed", "error": dict(facet["authorityError"])}
            row["goal"]["confirmation"] = proof

    def failure(code, reason, next_step=None):
        if run._facets and run._facets[-1]["row"]["status"] == "pending":
            run._facets[-1]["row"].update(status="failed", error={"code": code.upper(), "reason": reason})
        run.outcome = {"requestId": occurrence.request_id, "actionId": occurrence.action_id,
            "commandId": effect.command_id if effect else None, "status": "failed",
            "failure": {"code": code.upper(), "reason": reason}, "receipts": list(receipts)}
        raise ToolFailed(reason, next=next_step or "check the original window or source, then ask again")

    beside_note = []  # why an open asked for beside its anchor was opened where the person is instead

    def unplaced(opening, why):
        """An open asked for beside a window laid out nowhere the person sees (on a Tab not in front, hidden): nobody
        changed that window, it is just not there to be beside. One part that cannot be done never takes the whole
        action down (live 2026-10-29 18:54: the photo was kept and never shown), so the new window opens where the
        studio opens any (on the Tab in front), its anchor left out of the action, and the receipt says why."""
        reference = (opening.values.get('relation') or opening.values.get('placement', {}).get('relation') or {}).get('reference')
        before.pop(reference, None)
        beside_note.append(f"not beside window {reference}: {why}")
        return replace(opening, values={k: v for k, v in opening.values.items() if k not in ('relation', 'placement')})
    try:
        pctx = services.workspace_context(ctx.client, request_id=occurrence.request_id, owner=occurrence.action_id)
        pin = ctx.request_snapshot
        if pin is not None:
            client = pin.observation.client
            if (client.workspace_id != pctx.workspace_id or client.client_id != pctx.client_id
                    or client.page_epoch != pctx.client_epoch or client.connection_epoch != pctx.connection_epoch
                    or pin.observation.app_run_id != core.app_run):
                failure("client_mismatch", "the request pin belongs to another accepted workspace")
        action = _window_action(ctx, args)
        ids = action.values.get("targets", ()) if action.op in ("arrange", "group") else ((action.instance,) if action.instance else ())
        before = {iid: core.get(pctx.workspace_id, iid) for iid in ids}
        if action.op == "close" and action.instance and not before[action.instance].open:
            # Closed already by an earlier step of this same request (its `open` was last written by that step's
            # occurrence): this close is done, not failed (live 2026-10-11 16:17:34: close_magnifier closed the live
            # magnification, the completion round's present close of it failed on its released token, and the answer
            # said it could not be closed). A window closed by anything else is left to the paths below.
            mine = {prior.occurrence.action_id for steps in (*ctx.prior_steps, ctx.steps) for prior in steps.runs.values()
                    if prior is not run and prior.ok is True and prior.occurrence is not None
                    and prior.occurrence.request_id == occurrence.request_id}
            if before[action.instance].owners.get("open") in mine:
                return f"{action.instance} was already closed by an earlier step of this request: nothing more to close"
        if action.op in ("move", "ungroup") and action.instance:
            group = before[action.instance].view.group
            if group:
                before.update({s.id: s for s in core.list(pctx.workspace_id) if s.open and s.view.group == group})
        if action.op in ("arrange", "open") and action.values.get("relation", {}).get("reference"):
            anchor = action.values["relation"]["reference"]
            before.setdefault(anchor, core.get(pctx.workspace_id, anchor))
        # One truth for what is on screen: when the studio's last report is not current, or disagrees with the app
        # about a window this action touches, the report may only be behind. Hooke asks the studio for one now and
        # looks again, once, before any check below says the window changed (an internal conflict is Hooke's to
        # resolve, never the user's to ask again).
        try:
            observed = screen.observation(ctx.client)
            latest = screen.completion_report(ctx.client)
        except ObservationError:
            observed = latest = None
        if latest is not None and (not screen._current_observation(observed) or any(
                (_placement_issue(state, next((r for r in latest["items"] if r["id"] == iid), None), latest)
                 or ("",))[0] == "disagrees" for iid, state in before.items())):
            if callable(getattr(screen, "refresh", None)):
                await screen.refresh(ctx.client, timeout=CAUGHT_UP_S, newer=True)
        baseline = screen.completion_report(ctx.client)
        if baseline is None:
            failure("observation_incomplete", "the accepted workspace has no current observation")
        if (action.op in ("move", "resize")
                and "presentationArea" in baseline["viewport"]
                and baseline["viewport"]["presentationArea"] is None):
            failure("layout_unavailable", "the native presentation host has no visible viewport intersection")
        deliveries = {}
        # Existing windows borrow their exact accepted token. No disk reread or
        # latest-resource lookup may replace their already retained original.
        rows = {row["id"]: row for row in baseline["items"]}
        for iid, state in before.items():
            row = rows.get(iid)
            if (row is None or row.get("lifetimeGeneration") != state.lifetime_generation
                    or row.get("sourceGeneration") != state.source_generation):
                failure("target_revised", "that exact window is no longer observed on this connection")
            lease = store.borrow(row.get("sourceToken"), pctx, closing=action.op == "close")
            leases.append(lease)
            if not state.source.same_identity(lease.source):
                failure("source_mismatch", "the observed token belongs to another source")
            deliveries[iid] = lease
        if pin is not None:
            pinned = {row["id"]: row for row in pin.observation.wire()["items"]}
            number = run.step[0] if run.step else 0
            candidates = [prior for steps in ctx.prior_steps for prior in steps.runs.values()]
            candidates.extend(prior for n, prior in ctx.steps.runs.items() if n < number)
            earlier = [prior for prior in candidates if prior.ok is True and prior.occurrence is not None
                       and prior.occurrence.request_id == occurrence.request_id]
            if run.outputs and (run.outcome or {}).get("status") == "confirmed":
                earlier.append(run)
            # A step of this request still running (a window waiting for the studio to be in view) changed it as
            # this request, not as the user or another request (live 2026-10-11: a second zoom on the same stoma
            # was refused "that window changed" while the first waited in the background).
            running = [prior for prior in candidates if prior.ok is None and prior.occurrence is not None
                       and prior.occurrence.request_id == occurrence.request_id]
            # This action's own first attempt changed it too, when Hooke sends it once more (_native_settled).
            owners = {prior.occurrence.action_id for prior in earlier + running} | {occurrence.action_id}
            for iid, state in before.items():
                row = pinned.get(iid)
                # Same-request successful outputs, including earlier rounds,
                # supersede its Send pin; no unrelated user edit does.
                if row is None:
                    made = [output.wire() for prior in earlier for output in prior.outputs
                            if output.get("kind") == "presentation" and output.get("instanceId") == iid
                            and output.get("workspaceId") == pctx.workspace_id]
                    row = made[-1] if made else None
                if (row is None
                        or (row.get("lifetimeGeneration") != state.lifetime_generation and state.owners.get("open") not in owners)
                        or (row.get("sourceGeneration") != state.source_generation and state.owners.get("source") not in owners)
                        or any(row.get("propertyVersions", {}).get(prop) != version and state.owners.get(prop) not in owners
                               for prop, version in state.property_versions.items())):
                    failure("target_revised", "that window changed after this request was sent")
        if action.op == "arrange":
            arrangement = _arrangement_basis(action, before, baseline)
            action = replace(action, values={**action.values, "relation": arrangement[3]})
        child_deliveries = []
        if action.op == "open":
            value = args.get("source")
            if isinstance(value, dict) and 'sight' in value:
                capture, rect, provenance = _sight_capture(ctx, value)
                lease = await store.prepare_capture(capture, rect, provenance, pctx)
            else:
                ref = _native_ref(services, value, ctx)
                lease = await store.prepare(ref, pctx)
            leases.append(lease)
            child_deliveries.append(lease)
            action = replace(action, source=lease.source)
            if 'relation' in action.values:
                try:
                    arrangement = _opening_basis(action.values['relation'], before, baseline, lease.to_wire().get('originalSize'))
                except PresentationError as exc:
                    if getattr(exc, "placement", None) != "hidden":
                        raise
                    action = unplaced(action, getattr(exc, "why", exc.reason))
                else:
                    values = {k:v for k,v in action.values.items() if k != 'relation'}
                    values['placement'] = {'position':arrangement[0][:2], 'size':arrangement[0][2:], 'relation':arrangement[3]}
                    action = replace(action, values=values)
        elif action.op in ("duplicate", "split"):
            parent = before[action.instance]
            refs = parent.source.components if action.op == "split" else (parent.source,)
            if action.op == "split" and len(refs) < 2:
                failure("component_unavailable", "separate original sources were not retained")
            for ref in refs:
                lease = deliveries[action.instance].retain() if action.op == "duplicate" else await store.prepare(ref, pctx)
                leases.append(lease)
                child_deliveries.append(lease)
        # Preparation may await. Refuse target changes across it while allowing
        # another independent window to be edited normally.
        if action.op == "open" and ctx.prepared_source_check is not None:
            await ctx.prepared_source_check()
        if action.op == 'open' and arrangement is not None:
            current_report = screen.completion_report(ctx.client)
            try:
                current = _placement_observation(before,current_report,(arrangement[2]['id'],)) if current_report else None
            except PresentationError as exc:
                if getattr(exc, "placement", None) != "hidden":
                    raise
                current, arrangement = "unplaced", None
                action = unplaced(action, getattr(exc, "why", exc.reason))
            if current != "unplaced" and (current is None or current[0] != arrangement[1]
                                          or current[1][arrangement[2]['id']] != arrangement[2]):
                failure('target_revised','the original report or visible area changed while preparing its captured view')
        for iid, original in before.items():
            now = core.get(pctx.workspace_id, iid)
            if (now.lifetime_generation != original.lifetime_generation
                    or now.source_generation != original.source_generation
                    or now.property_versions != original.property_versions):
                failure("target_revised", "that window changed while its source was being prepared")
        prepared = core.admit(action, pctx)
        applied = await apply(services.hub, lambda _place: core.commit(prepared), scope="session", destination=WORKSPACE)
        if not applied.ok:
            failure("request_canceled", applied.reason)
        effect = applied.value
        if child_deliveries:
            deliveries.update(zip(effect.targets, child_deliveries))
        if action.op == "split":
            initial_children = {iid: core.get(pctx.workspace_id, iid) for iid in effect.targets}

        def compensation_guard(committed):
            if original_ticket is None or tickets.current() is not original_ticket:
                raise PresentationError("request_canceled", "the original request Ticket is no longer current")
            core.compensation_guard(committed, pctx)

        async def deliver(committed, op, original, *, compensation=False, required=False):
            if compensation:
                compensation_guard(committed)
            states = tuple(core.get(pctx.workspace_id, iid) for iid in committed.targets)
            report = screen.completion_report(ctx.client)
            if report is None:
                failure("observation_incomplete", "the originating workspace observation is unavailable")
            area = copy.deepcopy(report["viewport"].get("presentationArea"))
            geometry_op = op in ("move", "resize", "arrange") or op == 'open' and arrangement is not None
            if geometry_op and "presentationArea" in report["viewport"] and area is None:
                failure("layout_unavailable", "the native presentation host has no visible viewport intersection")
            issued = screen._observation_clock()
            message = command(committed, op=op, states=states, deliveries=deliveries, before=original,
                occurrence=occurrence, after_report_seq=report["seq"], issued_mono=issued,
                deadline_mono=issued + CONFIRM_S, before_report=report)
            fence = completion_fence(message, screen, report)
            expected = _window_expectations(message, area, report,
                                            arrangement=arrangement[0] if arrangement is not None else None)
            facet = None
            if goal is not None:
                facet = {"row": command_facet(message, expected, step=run.step[0], round=run.step[1].round,
                                              goal_id=goal.id),
                         "fence": fence, "report": None, "comparedAt": None, "authorityError": None,
                         "publishes": op in ("open", "duplicate", "split"), "compensation": compensation}
                run._facets.append(facet)
                bind_goal(facet)
            if compensation:
                compensation_guard(committed)
            services.hub.broadcast(message)
            # The deadline counts only time the studio is in view: in the background it paints nothing, and paints
            # what it was given once shown again, so that wait is the user's, never a failure (completion.compare).
            background_s, background_since, waited_since = 0.0, None, None
            asked_at = screen._observation_clock()
            while True:
                error = None
                if compensation:
                    try:
                        compensation_guard(committed)
                    except PresentationError as exc:
                        error = {"code": exc.code.upper(), "reason": exc.reason}
                try:
                    actual = screen.completion_report(ctx.client)
                except ObservationError as exc:
                    if not compensation:
                        raise
                    actual = None
                    error = error or {"code": exc.code, "reason": str(exc)}
                compared_at = screen._observation_clock()
                held = background_s + (compared_at - background_since if background_since is not None else 0.0)
                effective = {**fence, "deadlineMono": fence["deadlineMono"] + held}
                result = compare(expected, actual, effective, now=compared_at)
                in_background = (result["status"] == "pending" and (result.get("error") or {}).get("code") == "STUDIO_IN_BACKGROUND"
                                 # or no report since the command, from a studio whose last report says it is in the background
                                 or (result["status"] == "unobserved" and actual is not None and actual.get("visible") is False
                                     and type(actual.get("seq")) is int and actual["seq"] <= fence["afterReportSeq"]))
                if in_background and background_since is None:
                    background_since = compared_at
                    waited_since = waited_since if waited_since is not None else compared_at
                    run.report_progress("waiting for the studio to be in view: it is applied there, and shows as soon as the "
                                        "studio is in view again")
                elif not in_background and background_since is not None:
                    background_s += compared_at - background_since
                    background_since = None
                    run.report_progress(None)
                if in_background and compared_at - waited_since >= BACKGROUND_WAIT_S:
                    result = {**result, "status": "unobserved", "error": {
                        "code": "STUDIO_IN_BACKGROUND", "boundary": "observe",
                        "reason": "the studio stayed in the background, so it is not confirmed on screen yet"}}
                    run.report_progress(None)
                if (geometry_op and area is not None and actual is not None
                        and actual.get("viewport", {}).get("presentationArea") != area):
                    error = error or {"code": "LAYOUT_CHANGED", "boundary": "draw",
                        "reason": "the native presentation area changed after the original command was issued",
                        "retryable": False, "next": "ask again using the current workspace layout"}
                if arrangement is not None and actual is not None:
                    anchor = arrangement[2]
                    current = next((row for row in actual["items"] if row["id"] == anchor["id"]), None)
                    fixed = core.get(pctx.workspace_id, anchor["id"])
                    original_anchor = original[anchor["id"]]
                    if (area != arrangement[1] or current is None
                            or not all(current.get(k) is True for k in ("open", "connected"))
                            # in the background no item reports visible (nothing is painted); that is no change
                            or (current.get("visible") is not True and actual.get("visible") is not False)
                            or any(current.get(k) != anchor.get(k) for k in (
                                "rect", "sourceRef", "sourceToken", "sourceGeneration", "lifetimeGeneration",
                                "propertyVersions", "propertyOwners"))
                            or fixed.lifetime_generation != original_anchor.lifetime_generation
                            or fixed.source_generation != original_anchor.source_generation
                            or fixed.property_versions != original_anchor.property_versions
                            or fixed.owners != original_anchor.owners):
                        error = error or {"code": "TARGET_REVISED", "reason": "the original beside reference or measured area changed",
                                          "next": "check the two windows and arrange again"}
                if facet is not None:
                    facet.update(report=actual, comparedAt=compared_at, fence=effective)
                if not compensation:
                    if original_ticket is None or tickets.current() is not original_ticket:
                        reason = "the original request Ticket is no longer current"
                    else:
                        reason = core.check_request(pctx) or blocked(services.hub, original_ticket, "day")
                    if reason:
                        error = {"code": "REQUEST_CANCELED", "reason": reason}
                if error:
                    result = {**result, "status": "failed", "error": error}
                    if facet is not None:
                        facet["authorityError"] = error
                    break
                if result["status"] in ("confirmed", "failed", "timed_out") or (
                        result["status"] == "unobserved" and (result.get("error") or {}).get("code") == "STUDIO_IN_BACKGROUND"):
                    break
                if not in_background and screen._observation_clock() >= effective["deadlineMono"]:
                    break
                if (not in_background and _no_report_since(result, actual, fence)
                        and screen._observation_clock() - asked_at >= ASK_REPORT_S):
                    # No report since the command: Hooke asks the studio for one (a studio reports on a change and
                    # every few seconds), rather than waiting the deadline out and failing it as "stale".
                    asked_at = screen._observation_clock()
                    services.hub.broadcast({"type": "screen-ask"})
                await asyncio.sleep(0.25 if in_background else 0.025)
            receipts.append(result)
            if facet is not None:
                facet["row"].update(status=result["status"], confirmation=copy.deepcopy(result),
                                    error=copy.deepcopy(result.get("error")))
                bind_goal(facet)
            run.outcome = {**result, "receipts": list(receipts)}
            if result.get("error"):
                run.outcome["failure"] = dict(result["error"])
            if result["status"] == "unobserved" and (result.get("error") or {}).get("code") == "STUDIO_IN_BACKGROUND":
                unseen["background"] = True
                return False  # sent and applied by the studio, not a failure: it shows once the studio is in view
            if not compensation and not required and _no_report_since(result, actual, fence):
                # Sent, and the studio took no report Hooke could read since, though it was asked: not confirmed,
                # said as that, never a failure with "ask again" (an internal check is not the user's to repeat).
                unseen["unreported"] = True
                plain = {"code": "STUDIO_UNREPORTED", "boundary": "observe", "retryable": False, "next": None,
                         "reason": "the studio has not reported what it shows since the change was sent"}
                receipts[-1] = {**result, "error": plain}
                run.outcome = {**receipts[-1], "receipts": list(receipts), "failure": plain}
                if facet is not None:
                    facet["row"].update(status="unobserved", confirmation=copy.deepcopy(receipts[-1]), error=dict(plain))
                return False
            if result["status"] != "confirmed":
                if _no_report_since(result, actual, fence):
                    # A step the whole action needs (a split's retirement of its original): not confirmed is not done.
                    raise ToolFailed(f"the {op} was sent, but not confirmed on screen: the studio has not reported "
                                     "what it shows since",
                                     next=None)  # no next: it was asked for a report already; nothing is the user's to do
                raise ToolFailed((result.get("error") or {}).get("reason") or "the display could not be verified",
                                 next=(result.get("error") or {}).get("next") or REPORT_NEXT)
            raw = screen.observation(ctx.client).wire()
            acks = {r["presentationId"]: r for r in raw["commandResults"] if r["commandId"] == committed.command_id}
            observations = []
            for row in raw["items"]:
                if row["id"] not in committed.targets or row["id"] not in acks:
                    continue
                load = row.get("load") or {}
                observations.append(PresentationObservation(row["id"], committed.command_id, row["sourceKey"],
                    row["sourceGeneration"], row["lifetimeGeneration"], row["revision"],
                    "closed" if not row["open"] else load.get("state", "unknown"), row["visible"],
                    row.get("rootId", ""), load.get("drawToken") or "", tuple(row["rect"]),
                    tuple(row["contentRect"]), tuple(load.get("naturalSize") or ()), row["propertyVersions"],
                    connected=row["connected"], report_sequence=raw["seq"], transaction_id=committed.command_id))
            if compensation:
                # A removed root has no geometry to copy. Only its actual
                # accepted causal absence result supplies a closed observation.
                present = {row["id"] for row in raw["items"]}
                for iid in committed.targets:
                    ack = acks.get(iid, {})
                    if iid not in present and ack.get("phase") == "closed" and ack.get("absent") is True:
                        observations.append(PresentationObservation(iid, committed.command_id, ack.get("sourceKey", ""),
                            ack.get("sourceGeneration"), ack.get("lifetimeGeneration"), ack.get("committedRevision"),
                            "closed", False, "", "", (), (), (), ack.get("propertyVersions") or {}, connected=False,
                            report_sequence=ack.get("reportSeq"), transaction_id=committed.command_id, absent=True))
                compensation_guard(committed)
            observed = core.observe(pctx, observations)
            if observed.rejected or (compensation and set(observed.accepted) != set(committed.targets)):
                error = {"code": "DISPLAY_UNVERIFIED", "reason": "the core rejected the observed compensation close"
                         if compensation else "the core rejected the original command's observed targets"}
                receipts[-1] = {**result, "status": "failed", "error": error}
                if facet is not None:
                    facet["authorityError"] = error
                    facet["row"].update(status="failed", confirmation=copy.deepcopy(receipts[-1]), error=dict(error))
                    bind_goal(facet)
                run.outcome = {**receipts[-1], "failure": error, "receipts": list(receipts)}
                raise ToolFailed(error["reason"], next=error.get("next") or REPORT_NEXT)
            if geometry_op and not any(row["predicate"] == "rect" for row in expected):
                receipts[-1] = {**result, "status": "unobserved", "failure": {
                    "code": "DISPLAY_UNVERIFIED", "reason": "this operation's exact observed predicate is not installed"}}
                run.outcome = {**receipts[-1], "receipts": list(receipts)}
                if facet is not None:
                    facet["row"].update(status="unobserved", confirmation=copy.deepcopy(receipts[-1]),
                                        error=dict(receipts[-1]["failure"]))
                return False
            return True

        async def clean_provisional():
            """The original split's conditional undo, with actual causal close evidence."""
            failed = run.outcome
            closed, preserved, errors = [], [], []
            try:
                for iid in initial_children:
                    try:
                        # No await between the real before snapshot and guarded
                        # core rollback. This grants no forward-work authority.
                        if original_ticket is None or tickets.current() is not original_ticket:
                            raise PresentationError("request_canceled", "the original request Ticket is no longer current")
                        before_close = {iid: core.get(pctx.workspace_id, iid)}
                        undone = core.rollback(effect, pctx, native_child=iid)
                        preserved.extend(undone.preserved)
                        errors.extend({"instanceId": target, "code": code.upper(),
                                       "reason": "provisional child changed before compensation"}
                                      for target, code in undone.errors)
                        for close in undone.closes:
                            await deliver(close, "close", before_close, compensation=True)
                            closed.extend(close.targets)
                    except (PresentationError, ObservationError, ToolFailed) as exc:
                        errors.append({"instanceId": iid, "code": getattr(exc, "code", "CLEANUP_UNVERIFIED").upper(),
                                       "reason": str(exc)})
            finally:
                run.outcome = {**failed, "receipts": list(receipts),
                               "cleanup": {"closed": closed, "preserved": preserved, "failures": errors}}

        cleanup = clean_provisional
        confirmed = await deliver(effect, action.op, before)
        if action.op == "split":
            children_receipt = receipts[-1]
        if action.op in ("open", "duplicate", "split"):
            for iid in effect.targets:
                state = core.get(pctx.workspace_id, iid)
                record_output(ctx, {"kind": "presentation", "instanceId": iid, "workspaceId": pctx.workspace_id,
                                   "source": state.source.to_wire(), "sourceToken": deliveries[iid].token,
                                   "lifetimeGeneration": state.lifetime_generation,
                                   "sourceGeneration": state.source_generation,
                                   "propertyVersions": dict(state.property_versions)})
            # Publication is the producer boundary. Reuse this command's own
            # captured report and fence, before a parent close or later round.
            if goal is not None:
                bind_goal(run._facets[-1])
        if action.op == "split":
            parent_before = {effect.parent_id: before[effect.parent_id]}
            retired = await apply(services.hub, lambda _place: core.retire_group(effect, pctx), scope="session", destination=WORKSPACE)
            if not retired.ok:
                failure("request_canceled", retired.reason)
            await deliver(retired.value.retirement, "close", parent_before, required=True)
            run.outcome = {**children_receipt, "retirement": receipts[-1], "receipts": list(receipts)}
        note = "".join(f"; {line}" for line in beside_note)
        if not confirmed and unseen["background"]:
            return ("the window change was sent to the studio, which stayed in the background: it shows once the "
                    "studio is in view again, not confirmed on screen yet" + note)
        if not confirmed and unseen["unreported"]:
            return ("sent, but not confirmed on screen: the studio has not reported what it shows since it was sent "
                    "(not known whether it happened; not done and not failed)" + note)
        if not confirmed:
            return "the window change was sent; its display is unverified" + note
        # This is also the receipt carried into conversation/voice reconciliation.
        # A generic success loses which retained source actually changed, letting
        # an older assistant claim rename an unrelated image as the original: each
        # window says its source's identity, in words a person and a model read
        # (the structured source is in the run's outputs).
        from ...untrusted import inline

        changed = []
        for iid in effect.targets:
            state = core.get(pctx.workspace_id, iid)
            source = state.source.to_wire()
            web = source['provenance'].get('web') or {}
            address = source['identity'].get('resource') or {}
            shown = source['identity'].get('object') or source['identity'].get('objectId') or address.get('id')
            line = f"{source['kind']} {source['media']}" + (f" {shown}" if shown else "")
            if web.get('title'):
                line += f" \"{inline(web['title'], 200)}\"" + (f" from {inline(web['site'], 200)}" if web.get('site') else "")
            if web.get('page'):
                line += f" ({inline(web['page'], 1000)})"
            changed.append(f"{line} in window {iid}")
        return f"shown on screen ({action.op}, confirmed by the studio): " + "; ".join(changed) + note
    except PresentationError as exc:
        failure({"components_unavailable": "component_unavailable"}.get(exc.code, exc.code), exc.reason,
                next_step=exc.next)
    except ObservationError as exc:
        failure(exc.code, str(exc))
    except ToolFailed:
        if run.outcome is None:
            run.outcome = {"requestId": occurrence.request_id, "actionId": occurrence.action_id,
                "commandId": effect.command_id if effect else None, "status": "failed",
                "failure": {"code": "INVALID_ACTION", "reason": "the requested window reference is unavailable"}}
        raise
    finally:
        if cleanup is not None and initial_children and children_receipt is None and run.outcome is not None:
            await cleanup()
        if prepared is not None and effect is None:
            core.cancel(prepared, reason="workspace operation did not commit")
        for lease in leases:
            lease.release()


def add_present_tools(registry: Registry, services) -> None:
    import json
    from ...placement import DESCRIPTION as PLACEMENT_WORDS, SCHEMA as PLACEMENT_SCHEMA
    from ...screen import TAKES, VERBS

    hub = services.hub
    native = (getattr(services, "presentations", None) is not None
              and getattr(services, "prepared_sources", None) is not None
              and callable(getattr(services, "workspace_context", None)))
    native_verbs = ("open", "close", "duplicate", "split", "zoom", "pan", "move",
                    "resize", "front", "arrange", "beside", "opacity", "hide", "restore", "group", "ungroup")
    operations = list(VERBS) + ["place"]  # place: put `what` where `placement` says (placement.py)
    targets = {kind: list(verbs) for kind, verbs in TAKES.items()}
    for kind in ("population", "tracks"):
        targets[kind] += ["open", "close"]  # the existing show/hide aliases
    examples = [
        {"do": "show", "what": "tracks:m1"},
        {"do": "opacity", "what": "tracks:m1", "value": "50%"},
        {"do": "open", "what": "d1"},
        {"do": "resize", "what": "magnifier", "rect": [900, 20, 240, 160]},
    ]
    properties = {
        "what": {"type": "string", "description": "exact document:placement ID from the screen, legacy layer/panel, or retained resource ID; tracks:mN is a layer, magnifier is one captured-image panel"},
        "do": {"type": "string", "description": "operation for this target; value is an argument, never an operation", "enum": operations},
        "value": {"type": "string", "description": "opacity: 0-1 or percent (50%); colour: a colour, own colours, or measurement; never used by open/show"},
        "column": {"type": "string", "description": "colour: measured column, as an alternative to value"},
        "by": {"type": "string", "description": "emphasise: measurement; colour: measured column"},
        "top": {"type": "integer", "description": "emphasise: how many"},
        "order": {"type": "string", "description": "measurement order", "enum": ["desc", "asc"]},
        "members": {"type": "array", "items": {"type": "string"}, "description": "emphasise: these"},
        "where": {"type": "object", "description": "emphasise: {measurement: [min, max]}"},
        "outliers": {"type": "string", "description": "population emphasise only: outliers of a measurement; not supported for tracks"},
        "rest": {"type": "string", "description": "emphasise: fade or hide the rest", "enum": ["fade", "hide"]},
        "rect": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4,
                 "description": "document or legacy magnifier move/resize: [x,y,width,height] workspace CSS pixels, width at least 160; native windows use their own layout fields"},
        "over": {"type": "boolean", "description": "legacy magnifier move/resize: asked for over the view or its object"},
        "revision": {"type": "integer", "description": "population open/show only: 0 for retained original, or its current revision; no recomputation"},
        "placement": {**PLACEMENT_SCHEMA, "description": "do=place only: " + PLACEMENT_WORDS
                      + ". Outlines (eN, layer:eN): overlay draws them on the live view; new-tab gives them a Tab with "
                      "their capture and table"},
        "tab": {"type": "string", "maxLength": 80,
                "description": "with placement: the Tab to put it in, by title or id, made when none has that title "
                               "(default: in front); it is opened there from Data when not open there"},
        "instead": {"type": "boolean",
                    "description": "with placement: move it here from the Tab it is open in, when the user wants it "
                                   "here rather than there; otherwise it opens here too"},
    }
    description = ("Present layers, document windows and the magnifier" + (" or independent native windows" if native else "") + ". "
                   "do=place with placement puts a thing where it is asked (overlay on the live view, beside, new-tab, "
                   "window, replace, close, only), done once the screen shows it; with tab it puts it in that Tab "
                   "(a window, a saved picture, a researcher's picture, a table), wherever it is now. "
                   "Choose an operation supported by the exact target. Document windows (notes, masks, tables) close without removing Data. Outlines are named or segmented specimen shapes; "
                   "they are distinct from population masks and motion trajectory layers. A prior step N that made motion tracks or a population "
                   "can be addressed with what='@N' for layer show/hide/opacity; never predict its new ID. Reopen saved work by its "
                   "exact ID from resources, without rerunning its maker: open shows it in the reader; show draws a layer on the picture. Open a saved member (c17) for "
                   "its original clean crop without making a live outline. Historical motion tracks need verified field placement; "
                   "current-camera trails draw only on their exact paired camera frame and include camera/stage motion. "
                   "For moving motion into a corner, open its existing source in a native window, then resize/move "
                   "and fade that window; hide its tracks layer if transferring it off the camera. The measurement is "
                   "unchanged. Restore the overlay with show on tracks:mN. Changing layer opacity alone does not move it.")
    argument_contract = ("Target operations (installed support, not evidence of availability or completion): "
                         "Examples below are syntax placeholders; use original IDs from context/receipts. "
                         "Open/show does not accept value; opacity uses do=opacity and value. ")
    if native:
        operations.extend(verb for verb in native_verbs if verb not in operations)
        targets["native_instance"] = list(native_verbs)
        window_ref = {"type": "object", "description": "window address: {instanceId} returned by an open, or {step,output} of a presentation created earlier in this same action list; never a source/resource address",
                      "oneOf": [
                          {"properties": {"instanceId": {"type": "string"}}, "required": ["instanceId"], "additionalProperties": False},
                          {"properties": {"step": {"type": "integer", "minimum": 1}, "output": {"type": "integer", "minimum": 0}},
                           "required": ["step", "output"], "additionalProperties": False}]}
        properties["what"] = {"anyOf": [{"type": "string"}, window_ref],
                              "description": "legacy target/resource ID, or native {instanceId}/{step,output}; a legacy tracks:mN is not an independent window"}
        properties.update({
            "source": {"type": "object", "description": "native open only: {motion:exact mN,live:true} reopens an already available current-camera continuation without measuring again;  earlier successful producer {step,output} (retained source output), original {sight:'s2'}, exact SourceRef, or {day,id,version,picture}; motion mN picture 0 with version:null opens its original captured image with app-drawn tracks in an independent still window, not a live overlay; written research uses picture:null; use show_result for existing researcher work without constructing sources; old research images also require outputId"},
            "instance": window_ref,
            "targets": {"type": "array", "minItems": 1, "maxItems": 8, "items": window_ref,
                        "description": "native group: two or more distinct windows to link; arrange/beside: one window to move; for installed beside use one target and a distinct relation.reference anchor; no what/instance alongside targets"},
            "relation": {"type": "object", "description": "native open or arrange/beside, top alignment: open with source+relation fits only the new image window to its original source aspect and available space before its first paint; the reference stays fixed. For arrange, one target moves beside a distinct reference. Keeps current measured sizes and the reference fixed; resize explicitly before arranging when needed. For both, explicit side left or right is honored; omit side to try right then left within the observed presentation area. Refuses if the requested side or neither automatic side fits. Other relation kinds/alignments are not installed",
                         "properties": {"kind": {"type": "string", "enum": ["beside", "above", "below", "row", "column"]},
                                        "reference": window_ref, "gapPx": {"type": "number", "minimum": 0},
                                        "align": {"type": "string", "enum": ["top", "center", "bottom"]},
                                        "side": {"type": "string", "enum": ["left", "right"],
                                                 "description": "omit to choose a fitting side; an explicit side never falls back"}},
                         "required": ["kind"], "additionalProperties": False},
            "position": {**schema({"x": {"type": "number"}, "y": {"type": "number"}}, ["x", "y"]),
                         "description": "native move only: {x,y} viewport CSS pixels (the reported DOM rectangle space), fitted within the accepted viewport.presentationArea; never rect", "additionalProperties": False},
            "size": {"type": "object", "description": "native resize only: {width,height} CSS pixels; never rect",
                     "properties": {"width": {"type": "number"}, "height": {"type": "number"}},
                     "required": ["width", "height"], "additionalProperties": False},
            "zoom": {"type": "object", "description": "native zoom only: exactly one of {factor}, {level}, {fit:true}",
                     "properties": {"factor": {"type": "number"}, "level": {"type": "number"}, "fit": {"type": "boolean", "enum": [True]}},
                     "oneOf": [{"required": [key]} for key in ("factor", "level", "fit")], "additionalProperties": False},
            "center": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2,
                       "description": "native pan only: normalized [x,y]"},
            "title": {"type": "string", "description": "native open only: short window title, not a caption or source provenance replacement"},
        })
        examples.extend([
            {"do": "open", "source": {"step": 1, "output": 0}},
            {"do": "open", "source": {"step": 1, "output": 0},
             "relation": {"kind": "beside", "reference": {"instanceId": "w-00000000-0000-4000-8000-000000000002"}}},
            {"do": "open", "source": {"day": "2026-10-09", "id": "d1", "version": None, "picture": 0}},
            {"do": "open", "source": {"sight": "s2"}},
            {"do": "resize", "instance": {"step": 1, "output": 0}, "size": {"width": 240, "height": 160}},
            {"do": "move", "instance": {"instanceId": "w-00000000-0000-4000-8000-000000000001"},
             "position": {"x": 20, "y": 20}},
            {"do": "arrange", "targets": [{"instanceId": "w-00000000-0000-4000-8000-000000000001"}],
             "relation": {"kind": "beside", "reference": {"instanceId": "w-00000000-0000-4000-8000-000000000002"},
                          "gapPx": 12, "align": "top"}},
        ])
        description += (" Native open uses the original qualified source; source={sight:'s2'} opens that original "
                        "running-look region as a captured still, without an outline or another look. Two opens without instance make "
                        "independent windows with content-dependent decoded size; resize explicitly for chosen dimensions. "
                        "For initial beside placement use open source+relation: only the new image window fits beside the exact "
                        "original-browser reference before its first paint; the anchor remains fixed. "
                        "Duplicate/split use actual retained originals. Native window commands "
                        "support opacity (value fraction/percent), hide/restore without losing content, and group/ungroup. Moving one grouped window moves its companions. Confirmation "
                        "requires the originating studio's actual drawing. Beside requires observed geometry. Exact zoom/pan/front may remain "
                        "unverified even when supported; capability support does not prove fulfillment.")
        argument_contract += ("Native open: source, optional title/instance/relation (instance replaces its source rather than creating a window). "
                              "For a new image window, relation={kind:'beside',reference:{instanceId:the exact observed window ID}} "
                              "fits the new window before first paint, leaving the anchor fixed. An explicit side is strict; omitted side tries right then left. "
                              "A successful open returns outputs[0] with kind=presentation and instanceId: this identifies the opened window, including an existing instance whose source was replaced. "
                              "The source identifies its content, not its window; several windows can show the same source. "
                              "In a single action list, if action 1 opens a source, action 2 can resize it with "
                              "instance={step:1,output:0}. In a later model round, use instance={instanceId:the exact returned instanceId}. "
                              "Step/output numbers address only this action list, not previous rounds. "
                              "Native open does not open the legacy magnifier. "
                              "Native duplicate/split/close/front: instance only. "
                              "Native zoom: instance+zoom; pan: instance+center; move: instance+position; resize: instance+size. "
                              "Native arrange/beside: targets+relation (no what, instance, rect or value). ")
        argument_contract += ("For TWO windows of the same source, use a second open with the same qualified source and relation for initial fitting. "
                              "To keep existing sizes instead, create both windows before arranging (requires enough room for both): "
                              "action 1 present(do='open',source=the qualified source,title=a short title); "
                              "action 2 present(do='duplicate',instance={step:1,output:0}); "
                              "action 3 present(do='arrange',targets=[{step:2,output:0}],"
                              "relation={kind:'beside',reference:{step:1,output:0},gapPx:12,align:'top'}). "
                              "The second window must be created, not just mentioned. "
                              "instanceId must be an actual returned/observed window ID; 'viewer' is not a window ID. ")
    params = schema(properties, ["do"])
    params["description"] = argument_contract + json.dumps(targets, ensure_ascii=False, separators=(",", ":"))
    params["examples"] = examples

    async def present(ctx: ToolContext, args: dict) -> str:
        if args.get("do") == "place" or args.get("placement") is not None:  # the one placement vocabulary (placement.py)
            from ...placement import place

            return await place(services, ctx, registry, args)
        if ("source" in args or "instance" in args or "targets" in args
                or isinstance(args.get("what"), dict) or str(args.get("what", "")).startswith("w-")):
            if (getattr(services, "presentations", None) is None
                    or getattr(services, "prepared_sources", None) is None
                    or not callable(getattr(services, "workspace_context", None))):
                raise ToolFailed("native workspace presentations are unavailable",
                                 next="ask for an installed display capability")
            return await _native_settled(services, ctx, args)
        return await act(services, args, ctx=ctx)

    registry.add(Tool("present", description, params, present, inline_ms=1500, family="display", verbs=VERBS))

    async def look_at_screen(ctx: ToolContext, args: dict) -> str:
        """Acquire workspace pixels for the next admitted answer, with no inner model call."""
        import hashlib
        import io
        import weakref
        from PIL import Image
        from ...capture import captures
        from ...inspection import PictureInput
        from ...screen import screen_of
        from ..registry import CURRENT_RUN
        from ..admission import MAX_CALLS

        screen = screen_of(hub)
        scoped = ctx.client is not None or ctx.request_snapshot is not None
        if not scoped and not screen.showing():
            raise ToolFailed("no studio is showing the view now, so no picture of the screen was taken; this says "
                             "nothing about what the user sees", next=LOOK_NEXT)
        if ctx.admission is None or ctx.admission.used >= MAX_CALLS:
            raise ToolFailed("no answer capacity remains to inspect workspace pixels", next="ask again in a new request")
        ctx.admission.reserve_final()  # acquisition must leave an admitted interpretation opportunity
        run = CURRENT_RUN.get()
        if run is None:
            raise ToolFailed("workspace inspection has no request occurrence", next=None)  # no next: an unowned acquisition cannot borrow another request's authority
        if scoped:
            from ...workspace_observation import ObservationError
            from .resources import require_current

            if ctx.client is None or ctx.request_snapshot is None:
                raise ToolFailed("workspace capture needs the originating accepted client and request snapshot",
                                 next="send the request from the connected studio")
            require_current(ctx, getattr(services, "day", None))
            key = (ctx.admission.request_id, ctx.request_snapshot.snapshot_id, ctx.client)
            # The existing shared quota retains only a weak reference to the original
            # producer. Pixels and their lease remain owned by that run and Screen.
            for reserved in ctx.inspection_slots:
                if not isinstance(reserved, tuple) or reserved[:3] != key:
                    continue
                original = reserved[3]()
                if original is None:
                    raise ToolFailed("the original workspace input is no longer held", next="ask again in a new request")
                if original.task is not None and not original.task.done():
                    await asyncio.shield(original.task)
                require_current(ctx, getattr(services, "day", None))
                if original.ok is not True or len(original.images) != 1:
                    raise ToolFailed("the original workspace acquisition did not supply an input", next="ask again")
                try:
                    image = await screen.look(snapshot=ctx.request_snapshot, client=ctx.client,
                                              request_id=ctx.admission.request_id, targets=(), memory=captures(hub).memory)
                except ObservationError as error:
                    raise ToolFailed(_unlooked(error), next=LOOK_NEXT) from None
                require_current(ctx, getattr(services, "day", None))
                if image is not original.images[0]:
                    raise ToolFailed("the reused workspace input differs from its original producer", next=None)  # no next: a different input cannot replace the original acquisition
                return "Reused the original workspace input for interpretation; no additional picture was acquired or inspected."
            if len(ctx.inspection_slots) >= 2:
                raise ToolFailed("this request already has two picture inputs", next="ask about this workspace in a new request")
            reserved = (*key, weakref.ref(run))
            ctx.inspection_slots.append(reserved)  # reserve before the acquisition yields
            attached = False
            try:
                image = await screen.look(snapshot=ctx.request_snapshot, client=ctx.client,
                                          request_id=ctx.admission.request_id, targets=(), memory=captures(hub).memory)
                require_current(ctx, getattr(services, "day", None))
                if image is None:
                    raise ToolFailed("the picture of the workspace did not arrive in time, so it was not looked at; this "
                                     "says nothing about what the user sees", next=LOOK_NEXT)
                _keep_screen_picture(services, image.jpeg)
                run.images.append(image)
                attached = True
            except ObservationError as error:
                raise ToolFailed(_unlooked(error), next=LOOK_NEXT) from None
            finally:
                if not attached:
                    for index, slot in enumerate(ctx.inspection_slots):
                        if slot is reserved:
                            del ctx.inspection_slots[index]
                            break
            return "Acquired the originating workspace pixels for interpretation; they have not yet been visually inspected."
        if len(ctx.inspection_slots) >= 2:
            raise ToolFailed("this request already has two picture inputs", next="ask about this workspace in a new request")
        limit = 6 * 1024 * 1024
        lease = captures(hub).memory.reserve("analysis", limit * 3, "workspace answer input")
        try:
            jpeg = await screen.look()
            if not jpeg:
                raise ToolFailed("the picture of the workspace did not arrive in time, so it was not looked at; this "
                                 "says nothing about what the user sees", next=LOOK_NEXT)
            if len(jpeg) > limit:
                raise ToolFailed("the workspace picture exceeds its size limit",
                                 next="use a smaller studio viewport, then ask again")
            try:
                with Image.open(io.BytesIO(jpeg)) as image:
                    width, height = image.size
                    if image.format != "JPEG" or width * height > 4_000_000:
                        raise ToolFailed("the workspace picture exceeds its dimensions or is not a JPEG",
                                         next="use a smaller studio viewport and ask to capture it again")
            except (OSError, ValueError, Image.DecompressionBombError):
                raise ToolFailed("the workspace picture could not be read", next="ask to capture the workspace again") from None
            facts = {"channel": "workspace", "fidelity": "approximate-composition",
                     "timing": "acquired now, not retrospectively at Send", "inputSize": [width, height],
                     "inputSha256": hashlib.sha256(jpeg).hexdigest(),
                     "basis": "Studio composed workspace; controls and occlusion may be missing; not specimen evidence"}
            _keep_screen_picture(services, jpeg)
            lease.settle(len(jpeg) * 3)
            run.images.append(PictureInput(jpeg, facts, lease))
            ctx.inspection_slots.append(None)
            return "Acquired a composed workspace picture for interpretation; it has not yet been visually inspected."
        except BaseException:
            lease.release()
            raise

    registry.add(Tool("look_at_screen", "Look at the user's screen when what the studio reports is unclear; never the specimen.",
                      schema({"question": {"type": "string", "description": "what to check"}}),
                      look_at_screen, kind="query", inline_ms=6000, family="display"))


def _keep_screen_picture(services, jpeg: bytes) -> None:
    """Kept as a performance-log picture only, in its own folder (logs/screens/<day>/, never logs/crops, which
    holds what the specimen models were shown and what a reconstructed dataset copies; M1.5b review S7)."""
    import time
    from pathlib import Path

    log = getattr(services, "log", None)
    history = getattr(services, "history_log", None)
    if history is None:
        return
    folder = Path(history).parent / "screens" / time.strftime("%Y-%m-%d")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        now = time.time()
        path = folder / f"{time.strftime('%H%M%S', time.localtime(now))}-{int(now * 1000) % 1000:03d}-screen.jpg"
        path.write_bytes(jpeg)
        if log is not None:
            log({"kind": "screen-look", "screenPicture": str(path.relative_to(Path(history).parent))})
    except OSError:
        pass
