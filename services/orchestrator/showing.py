"""Show me: a researcher's finding shown on screen from its own evidence (the partner §2.3; ADR 0015;
the UX document §2, "The Show me experience").

A showing is the owner of the changes its beats make. The brain chooses the beats from the one verb
set (`present`: open an index entry, dim the live view, emphasise members, ...), during the request
that started the showing, and each counts only once the studio's report shows it (`act`). For every
property a beat changed, the showing keeps what it was before the showing's first beat on it and what
the showing set it to last; for the cells' layer, the whole style its first beat replaced (a colouring
by a measurement is its legend and values too).

Stopping it (the user's Escape or Stop on its bar, "stop" by voice or chat, a new showing, or a new
day) puts back a property only while it is still the showing's: still owned by it (screen.Screen.owners)
and still showing what the showing set. What the user, or a later request, changed meanwhile stays as
they left it, and is said so. A panel it closed can't be reopened as it was: said, never claimed.
After a stop, the request that started it can make no more beats, and a beat the studio confirms after
the stop is put back at once. Stopping touches nothing else: research and recording go on.
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field

POPULATION_STYLE = ("opacity", "fill", "colour", "column", "value", "emphasis")
COLOURING = ("legend", "values", "column", "value")  # what goes with a colour (put back with it)
NAMES = {"live": "the live view", "outlines": "the app's outlines", "magnifier": "the magnifier",
         "viewer": "the result viewer", "slidemap": "the slide map", "card": "the details card",
         "made-today": "the list of what was made today", "sidebar": "the sidebar", "sessions": "the sessions list"}
TOP_LEVEL = ("opacity", "visible", "open", "rect", "shows", "source")  # reported on the item itself; the rest in its style
PANELS = ("magnifier", "viewer", "slidemap")  # a panel's open, what it shows and where it is go together
CLOSE_ONLY = ("card", "made-today")  # panels the verbs can only close: one the showing closed is said, never reopened
# Another session opened (app._clear_view; ADR 0017) stops a showing with this reason.
SESSION_CHANGE = "another session was opened"
DAY_CLOSES = ("magnifier", "viewer")  # what another session's opening closes (app._clear_view); the slide map it leaves
DAY_CLEARS = ("card",)  # what it empties: a details card's outline is gone, so nothing to reopen
PROP_WORDS = {"opacity": "fading", "visible": "showing", "open": "opening", "shows": "what it shows", "source": "picture",
              "rect": "place and size", "emphasis": "kept and faded cells", "colour": "colours", "column": "colouring",
              "value": "colour", "fill": "fill", "front": "place in front"}
RECT_SLACK_PX = 6  # as act's own check: a place or size within this is the one it set


def name_of(pid: str) -> str:
    return "the cell outlines" if pid.startswith("layer:") else NAMES.get(pid, pid)


def whose(pid: str) -> str:
    """The possessive: "the live view's", "the cell outlines'"."""
    name = name_of(pid)
    return name + ("'" if name.endswith("s") else "'s")


def about(pid: str, prop: str) -> str:
    """A property in plain words: "the live view's brightness", "the cell outlines' kept and faded cells"."""
    word = "brightness" if (pid, prop) == ("live", "opacity") else PROP_WORDS.get(prop, prop)
    return f"{whose(pid)} {word}"


@dataclass
class Showing:
    id: str
    rid: str
    about: str  # whose finding, in words ("Researcher 2's finding")
    request: str | None  # the `by` of the request that started it: its beats are the showing's
    started_at: float = field(default_factory=time.time)
    # (presentation, property) -> {"first_was": before the showing's first beat on it, "set": what it set last}
    touched: dict[tuple[str, str], dict] = field(default_factory=dict)
    first_style: dict[str, dict] = field(default_factory=dict)  # a cells' layer -> its style before the first beat
    active: bool = True

    @property
    def owner(self) -> str:
        return f"showing:{self.id}"

    def wire(self) -> dict:
        return {"type": "showing", "id": self.id, "rid": self.rid, "about": self.about, "active": self.active}


class Stopped(Exception):
    """A beat of a showing that was stopped: it is not made."""


class Showings:
    def __init__(self, broadcast=lambda payload: None) -> None:
        self.broadcast = broadcast
        self.current: Showing | None = None
        self.stopped: dict[str, Showing] = {}  # request -> the showing it started that was stopped (its later beats refused)
        self._ids = itertools.count(1)

    def start(self, rid: str, about: str, request: str | None) -> Showing:
        self.current = Showing(f"sh{next(self._ids)}", rid, about, request)
        if request is not None:
            self.stopped.pop(request, None)
        self.broadcast(self.current.wire())
        return self.current

    def owner_for(self, by: str | None) -> str | None:
        """The showing's owner when `by` is the request that started the active showing, else `by`.
        Stopped raised for a beat of a stopped showing's request: it was interrupted, so it makes no more."""
        if by is not None and by in self.stopped:
            raise Stopped(f"{self.stopped[by].about} was stopped")
        showing = self.current
        if showing is not None and showing.active and by is not None and by == showing.request:
            return showing.owner
        return by

    def before_layer(self, pid: str, style: dict, by: str | None) -> None:
        """A beat about to change a cells' layer's style: the style it had before the showing's first beat."""
        showing = self.current
        if showing is not None and showing.active and by == showing.owner:
            showing.first_style.setdefault(pid, dict(style))

    def noted(self, change, screen) -> str:
        """A change the studio confirmed. "late": a beat of a stopped showing (stopping or stopped), to be put
        back; "owned": the active showing's (each property it changed is kept, with what it was before the
        showing's first beat); "" otherwise."""
        if any(s.owner == change.by for s in self.stopped.values()):
            return "late"
        showing = self.current  # a stopped one was caught above, so the active showing's owner is its own
        if showing is not None and change.by == showing.owner:
            for prop, value in change.set.items():
                record = (screen.owners.get(change.target) or {}).get(prop) or {}
                if record.get("by") != change.by or record.get("at") is None:
                    continue  # unchanged by this beat (already so): never the showing's
                entry = showing.touched.setdefault((change.target, prop), {"first_was": record.get("was")})
                shown = _shown_now(screen, change.target, prop) if prop == "rect" else _UNKNOWN
                # A place and size as the studio shows them once confirmed: a move keeps its width whatever
                # the move's rect said, so what it set is what is shown (review of #162, round 4).
                entry["set"] = shown if isinstance(shown, list) and len(shown) == 4 else value
                entry["verb"] = change.verb  # what its own check compared (a move: the place; a resize: the width)
                entry.setdefault("verbs", set()).add(change.verb)
            return "owned"
        return ""

    def ended(self) -> None:
        if self.current is not None:
            self.current.active = False
            self.broadcast(self.current.wire())
        self.current = None


def showings_of(hub) -> Showings:
    found = getattr(hub, "showings", None)
    if not isinstance(found, Showings):
        found = Showings(getattr(hub, "broadcast", None) or (lambda payload: None))
        try:
            hub.showings = found
        except AttributeError:
            pass
    return found


def _shown_now(screen, pid: str, prop: str):
    """What the studio reports for a property now, or a marker that it reports nothing about it."""
    item = screen.item(pid)
    if item is None:
        return _UNKNOWN
    if prop in TOP_LEVEL or prop not in POPULATION_STYLE:
        if prop == "opacity" and "opacity" not in item:
            return (item.get("style") or {}).get("opacity", _UNKNOWN)
        return item.get(prop, _UNKNOWN)
    return (item.get("style") or {}).get(prop, _UNKNOWN)


_UNKNOWN = object()


def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(float(a) - float(b)) < 0.02
    if isinstance(a, dict) and isinstance(b, dict) and "members" in a and "members" in b:
        return set(a.get("members") or []) == set(b.get("members") or []) and a.get("rest") == b.get("rest")
    return a == b


def _still_as_set(screen, pid: str, prop: str, entry: dict) -> bool:
    """Whether the studio still shows what the showing set, compared as the beat's own check compared it."""
    if pid == "magnifier" and prop == "shows":
        return True  # the studio reports the outline it follows here; a picture's own mark is its source
    now = _shown_now(screen, pid, prop)
    if now is _UNKNOWN:
        return True
    want = entry.get("set")
    if prop == "rect" and isinstance(now, list) and isinstance(want, list) and len(now) == len(want) == 4:
        verbs = entry.get("verbs") or {entry.get("verb")}
        parts = tuple(sorted(({2} if "resize" in verbs else set()) | ({0, 1} if verbs - {"resize"} else set())))
        return all(abs(float(now[k]) - float(want[k])) <= RECT_SLACK_PX for k in parts)
    return _same(now, want)


def _changed_by(screen, showing, pid: str, props) -> str | None:
    """Who changed any of these since: "you", "a later request", "" (changed, by nobody reported), or None
    when all are still the showing's, as it set them."""
    for prop, entry in props:
        record = (screen.owners.get(pid) or {}).get(prop) or {}
        if record.get("by") != showing.owner:
            return "you" if record.get("by") == "user" else "a later request"
    for prop, entry in props:
        if not _still_as_set(screen, pid, prop, entry):
            return ""
    return None


async def stop(services, why: str = "stopped") -> str:
    """Ends the active showing and puts back only what is still its own and still as it set it. Words for
    the user: what was put back, what stays as someone changed it since, and what could not be put back."""
    from .screen import screen_of

    hub = services.hub
    showings = showings_of(hub)
    showing = showings.current
    if showing is None or not showing.active:
        return "nothing is being shown"
    showing.active = False
    if showing.request is not None:
        showings.stopped[showing.request] = showing  # its request's later beats are refused
    showings.broadcast(showing.wire())
    screen = screen_of(hub)
    put, kept, left = [], [], []
    layers: dict[str, list[tuple[str, object]]] = {}
    panels: dict[str, list[tuple[str, dict]]] = {}
    for (pid, prop), entry in list(showing.touched.items()):
        if pid in PANELS:
            panels.setdefault(pid, []).append((prop, entry))
            continue
        if why == SESSION_CHANGE and pid in DAY_CLEARS:
            continue  # its outline went with the session left: nothing to reopen
        who = _changed_by(screen, showing, pid, [(prop, entry)])
        if who is not None:
            kept.append(f"{about(pid, prop)} (changed {'by ' + who + ' ' if who else ''}since)")
            continue
        if pid.startswith("layer:"):
            layers.setdefault(pid, []).append((prop, entry.get("first_was")))
            continue
        try:
            said = await _put_back(services, pid, prop, entry.get("first_was"), entry.get("verb"))
        except Exception as exc:  # one that fails never stops the others; it is said
            left.append(f"{about(pid, prop)} ({exc})")
            continue
        if said:
            (put if not said.startswith("!") else left).append(said.lstrip("!"))
    for pid, props in panels.items():
        if why == SESSION_CHANGE and pid in DAY_CLOSES:
            continue  # the session's opening closed it: nothing of it to put back (never the left one's work reopened)
        # A panel's opening, what it shows and where it is go together: changed in any of them since, it is
        # left as it is now, whole (a viewer the user closed is never reopened, nor work they opened closed).
        who = _changed_by(screen, showing, pid, props)
        if who is None:  # anything else of it changed since the showing began (closed with its x, say): theirs
            other = next((r for prop, r in (screen.owners.get(pid) or {}).items() if prop not in dict(props)
                          and r.get("by") not in (showing.owner, "showing:restore")
                          and (r.get("at") or 0) >= showing.started_at - 0.002), None)  # owner times are rounded to ms
            if other is not None:
                who = "you" if other.get("by") == "user" else "a later request"
        if who is not None:
            kept.append(f"{name_of(pid)} (changed {'by ' + who + ' ' if who else ''}since)")
            continue
        try:
            said = await _put_back_panel(services, pid, dict(props))
        except Exception as exc:
            left.append(f"{name_of(pid)} ({exc})")
            continue
        for item in said:
            (put if not item.startswith("!") else left).append(item.lstrip("!"))
    for pid, props in layers.items():
        try:
            said = await _put_back_layer(services, pid, props, showing.first_style.get(pid))
        except Exception as exc:
            left.append(f"the cell outlines ({exc})")
            continue
        if said:
            (put if not said.startswith("!") else left).append(said.lstrip("!"))
    if showings.current is showing:
        showings.current = None
    log = getattr(services, "log", None)
    if log is not None:
        log({"kind": "showing-stop", "showing": showing.id, "rid": showing.rid, "why": why, "put": put, "kept": kept, "left": left})
    parts = [f"stopped showing {showing.about}"]
    if put:
        parts.append("Put back: " + "; ".join(put))
    if kept:
        parts.append("Left as changed since: " + "; ".join(kept))
    if left:
        parts.append("Not put back: " + "; ".join(left))
    if not (put or kept or left):
        parts.append("Nothing of it needed putting back")
    return ". ".join(parts) + "."


async def put_back_late(services, change) -> tuple[list[str], list[str]]:
    """A beat of a stopped showing the studio confirmed after the stop: put back at once. (put, not put back)."""
    from .screen import screen_of

    screen = screen_of(services.hub)
    showing = next((s for s in showings_of(services.hub).stopped.values() if s.owner == change.by), None)
    put, left = [], []
    props = {}
    for prop in change.set:
        record = (screen.owners.get(change.target) or {}).get(prop) or {}
        if record.get("by") != change.by:
            continue
        # Back to what was there before the showing when it is known (it was put back at the stop), else
        # to what this beat replaced.
        was = (showing.touched.get((change.target, prop)) or {}).get("first_was", record.get("was")) if showing else record.get("was")
        props[prop] = {"first_was": was, "set": change.set[prop], "verb": change.verb}
    if not props:
        return put, left
    if change.target in PANELS:
        said = await _put_back_panel(services, change.target, props)
    elif change.target.startswith("layer:"):
        said = [await _put_back_layer(services, change.target, [(k, v["first_was"]) for k, v in props.items()],
                                      showing.first_style.get(change.target) if showing else None)]
    else:
        said = [await _put_back(services, change.target, k, v["first_was"], v["verb"]) for k, v in props.items()]
    for item in said:
        if item:
            (put if not item.startswith("!") else left).append(item.lstrip("!"))
    return put, left


def late_words(put: list[str], left: list[str]) -> str:
    """What a beat confirmed after its showing was stopped says: what was put back, and what could not be."""
    parts = ["the showing was stopped"]
    if put:
        parts.append("so this was put back (" + "; ".join(put) + ")")
    if left:
        parts.append("and this could not be put back (" + "; ".join(left) + ")")
    return ", ".join(parts) if len(parts) > 1 else "the showing was stopped; this beat changed nothing that is still its own"


async def _put_back(services, pid: str, prop: str, was, verb: str | None = None) -> str:
    """One property back to what it was, through the verbs (done once shown); "" when nothing to do, a
    "!" first when it could not be put back."""
    from .brain.registry import ToolFailed
    from .brain.toolbox.present import act

    by = "showing:restore"
    name = name_of(pid)
    try:
        if pid == "live" and prop == "opacity":
            await act(services, {"what": "live view", "do": "opacity", "value": was if was is not None else 1.0}, by=by)
            return f"{name} at full brightness" if was in (None, 1, 1.0) else f"{name} as it was"
        if pid == "outlines" and prop == "visible":
            await act(services, {"what": "outlines", "do": "show" if was in (None, True) else "hide"}, by=by)
            return f"{name} {'shown' if was in (None, True) else 'hidden'} again"
        if pid == "outlines" and prop == "opacity":
            await act(services, {"what": "outlines", "do": "opacity", "value": was if was is not None else 1.0}, by=by)
            return f"{whose(pid)} opacity as it was"
        if prop == "open" and pid in CLOSE_ONLY:
            return f"!{name} (it was open before; open it again from where it was)" if was else ""
        if prop == "open" and pid in PANELS:
            if was:
                return f"!{name} (it was open before; reopen it from where it was)"
            await act(services, {"what": NAMES[pid], "do": "close"}, by=by)
            return f"{name} closed again"
        if prop == "rect" and isinstance(was, list) and len(was) == 4:
            # Back where it was, even over what it shows: that place was somebody's own (a user's drag over its object
            # is never refused on the way back; the review of G3, N5).
            await act(services, {"what": NAMES.get(pid, pid), "do": "resize" if verb == "resize" else "move", "rect": was,
                                 "over": True}, by=by)
            return f"{name} {'as big as' if verb == 'resize' else 'where'} it was"
    except ToolFailed as exc:
        return f"!{about(pid, prop)}: {exc}"
    return ""


async def _put_back_panel(services, pid: str, props: dict) -> list[str]:
    """A panel the showing changed, still as it left it: closed again if it opened it; a panel it closed is
    said, not reopened; else what it shows (only while open) and where it is, put back."""
    from .brain.registry import ToolFailed
    from .screen import magnifier_source, on_screen, open_state, screen_of

    name = name_of(pid)
    opened = props.get("open")
    if opened is not None and opened.get("first_was"):
        return [f"!{name} (it was open before; reopen it from where it was)"]
    if opened is not None:
        return [await _put_back(services, pid, "open", opened.get("first_was"))]
    out = []
    screen = screen_of(services.hub)
    is_open = _open_now(services, pid)  # the server's own state: the studio's last report may not have caught up
    try:
        if pid == "viewer" and "shows" in props and is_open:
            was = props["shows"].get("first_was")
            if isinstance(was, str) and was and services.presenter is not None:
                after = screen.reported_at
                services.presenter.show(was)
                await on_screen(services.hub, open_state("viewer", True, was), after, "the earlier work is not open again",
                                "open it from Researchers")
                out.append("the result viewer on what it showed before")
        if pid == "magnifier" and "source" in props and is_open:
            was, follows = props["source"].get("first_was"), (props.get("shows") or {}).get("first_was")
            if was == "live" and follows and services.magnifier is not None and follows in services.hub.scene.objects:
                after = screen.reported_at
                services.magnifier.zoom(follows)
                await on_screen(services.hub, magnifier_source("live", follows), after, "the magnifier is not live again",
                                "click the thing to magnify it")
                out.append("the magnifier back on the live view")
            else:
                out.append("!the magnifier's picture (close it, or click the thing to magnify it)")
    except ToolFailed as exc:
        out.append(f"!{name}: {exc}")
    if "rect" in props:
        rect = props["rect"]
        verbs = rect.get("verbs") or {rect.get("verb")}
        if "resize" in verbs:  # its size, then its place: each as its own verb puts it back
            out.append(await _put_back(services, pid, "rect", rect.get("first_was"), "resize"))
        if verbs - {"resize"}:
            out.append(await _put_back(services, pid, "rect", rect.get("first_was"), "move"))
    return [o for o in out if o]


def _open_now(services, pid: str) -> bool:
    """Whether a panel is open, from the server's own state (the presenter, the magnifier, the slide map)."""
    holder = {"viewer": getattr(services, "presenter", None), "magnifier": getattr(services, "magnifier", None),
              "slidemap": getattr(services, "slidemap", None)}.get(pid)
    value = getattr(holder, "open", None) if holder is not None else None
    if isinstance(value, bool):
        return value
    from .screen import screen_of

    return bool((screen_of(services.hub).item(pid) or {}).get("open"))


async def _put_back_layer(services, pid: str, props: list[tuple[str, object]], first_style: dict | None) -> str:
    """A cells' layer's properties still the showing's, back in one change, done once shown: from the style
    its first beat replaced when kept (a colouring with its legend and values), else from each was."""
    from .apply import apply
    from .population import layer_of
    from .screen import screen_of

    hub = services.hub
    layer = layer_of(hub)
    eid = pid.split(":", 1)[1]
    owned = dict(props)
    screen = screen_of(hub)
    if "open" in owned or "visible" in owned:
        before = owned.get("visible", owned.get("open"))
        if not before and layer.shown == eid:
            done = await apply(hub, lambda _: layer.hide(), scope="cell")
            if not done.ok:
                return f"!the cell outlines: {done.reason}"
            after = screen.reported_at
            hidden = await screen.until(lambda r: not any(i.get("id") == pid and i.get("visible") for i in r.get("items", [])), after)
            return "the cell outlines hidden again" if hidden else "!the cell outlines: the studio has not shown them hidden"
    style_props = {k: v for k, v in owned.items() if k in POPULATION_STYLE}
    if not style_props or layer.shown != eid:
        return ""
    style = dict(layer.style)
    for key, was in style_props.items():
        value = first_style.get(key) if first_style is not None else was
        if value is None:
            style.pop(key, None)
        else:
            style[key] = value
    if "colour" in style_props:  # a colouring is its legend and values too: as they were, or none
        for key in COLOURING:
            if first_style is not None and key in first_style:
                style[key] = first_style[key]
            elif first_style is not None or style.get("colour") != "column":
                style.pop(key, None)
    after = screen.reported_at
    done = await apply(hub, lambda _: setattr(layer, "style", style), scope="cell")
    if not done.ok:
        return f"!the cell outlines' style: {done.reason}"
    layer.broadcast(layer.wire())

    def check(report) -> bool:
        item = next((i for i in report.get("items", []) if i.get("id") == pid), None)
        if item is None:
            return False
        got = item.get("style") or {}
        for key in style_props:
            want = style.get(key)
            have = item.get("opacity", got.get("opacity")) if key == "opacity" else got.get(key)
            if key == "opacity" and want is None:
                want = 1.0
            if not _same(have, want):
                return False
        return True

    shown = await screen.until(check, after)
    return "the cell outlines as they were" if shown else "!the cell outlines' style: the studio has not shown it put back"
