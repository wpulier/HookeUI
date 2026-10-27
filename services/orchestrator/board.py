"""The Board: what is on screen now, as one state, and what "that" means on it.

`Board.now()` is a frozen `BoardState`:
- the outlines (box where they are now, share of the view, status, who made them);
- cells;
- the running look's things while it is current;
- the magnifier, the result viewer and the selection;
- the latest changes (added, removed, selected, cleared), with when.

`Board.resolve(ref, ctx)` finds the outline a request means: an id, "target 2", "the second one", "the one
I clicked", "that" / "it" / "this one", or a label. "That" and "the one I clicked" mean what was pointed at
when the user made the request, not something selected or clicked while it was being answered (the partner's
Amendment 1, C2): `Board.when_asked()` saves it once, when the request is made (`WhenAsked`: typed, as the turn
starts; spoken, when the words' final transcript came), and it rides on the request's `ToolContext`. The
Board writes nothing: `Scene` stays the only place outlines are written. Every tool finds its
object here (brain/toolbox/common.resolve_object), so "that" means the same thing to all of
them.

"That" (or no object named) is, in order:
1. what this request outlined;
2. what this request's own steps pointed at (select, magnify, dissect: `ctx.pointed`);
3. the selection when the request was made (gone since: said, never something else instead);
4. what the user clicked in the CLICK_RECENT_S before the request;
5. what the magnifier was open on;
6. the newest thing the app had outlined by then;
7. the newest outline by then (none: said, never one made after the request).
Without a `WhenAsked` (a tool run directly, tests), the screen now.
"""
from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from .brain.registry import ToolContext, ToolFailed

CLICK_RECENT_S = 10.0  # a click this recent is what "that" means, after the selection
RECENT = 30  # changes kept

DEICTIC = re.compile(r"(?:(?:that|this|it)(?: one)?|the (?:highlight|outline|thing|one)|here|there)", re.I)
CLICKED = re.compile(r"(?:the one |what |the thing )?(?:i|i've|i have) (?:just )?clicked(?: on)?|my click", re.I)
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4,
            "5th": 5, "last": -1}
ORDINAL = re.compile(r"(?:the )?(" + "|".join(ORDINALS) + r")(?: one| outline| highlight)?", re.I)
TARGET = re.compile(r"(?:target\s*)?(\d+)", re.I)
ID = re.compile(r"[opc]\d+")
PICKABLE = ("highlight", "part")


@dataclass(frozen=True)
class Item:
    id: str
    kind: str  # "highlight" | "part"
    label: str
    box: tuple[float, float, float, float]  # frame pixels now
    share: float  # of the view's area
    status: str
    source: str  # "click", "ai:<model>", "ai:sight", "cells"...
    confidence: float | None
    in_view: bool
    parent: str | None
    created: float
    taught: dict | None = None  # the closest label the user taught (taught.py)
    moved: str | None = None  # how far it went on its own while followed, in words (trail.py); None if it hardly moved
    made: str | None = None  # how it came to be, in words: who asked for it and what drew it (how_made)


def how_made(o) -> str | None:
    """How an outline came to be, each model in order, from its own record (known.chain: found by, drawn by,
    checked by, named by, followed by), so "which model?" and "how do you know?" are one account. Live
    2026-10-11: asked "which model did you use?", the answer invented "our built-in vision model"; Gemini 3.8 Flash
    had found it and EdgeTAM drawn it, and nothing had told the brain either."""
    from .known import chain

    return ", ".join(chain(o)) or None


@dataclass(frozen=True)
class Seen:
    id: str
    name: str
    box: tuple[float, float, float, float]


@dataclass(frozen=True)
class Change:
    version: int
    op: str  # "add" | "remove" | "select" | "clear"
    ids: tuple[str, ...]
    at: float


@dataclass(frozen=True)
class WhenAsked:
    """What the user was pointing at when they made a request (the partner's Amendment 1, C2), saved once, when
    it is made. The selection, the cells layer's selected cell and the magnifier change in place, so they are
    kept here; clicks keep their own time (an outline's `created`), so the newest click by `at` is read from the
    board."""
    at: float  # wall clock
    selected: str | None = None  # the outline selected
    selected_at: float = 0.0
    cell: tuple[str, str] | None = None  # (population, member) selected on the cells layer
    cell_at: float = 0.0
    magnified: str | None = None  # what the magnifier was open on


@dataclass(frozen=True)
class BoardState:
    version: int
    frame_id: int | None
    epoch: int | None
    items: tuple[Item, ...]
    selected: str | None
    magnifier: dict  # {"open", "object", "level"}
    presenter: dict  # {"open", "rid"}
    seen: tuple[Seen, ...]  # the running look's things, while it describes the view
    recent: tuple[Change, ...]
    at: float
    evidence: tuple[str, ...] = ()  # evidence lines (population.evidence_lines): shown, running, this conversation's
    shown: tuple[str, ...] = ()  # what the studio reports it shows, one line per thing (screen.Screen.lines; contract 10)

    def item(self, object_id: str | None) -> Item | None:
        return next((i for i in self.items if i.id == object_id), None)

    @property
    def outlines(self) -> tuple[Item, ...]:
        return tuple(i for i in self.items if i.kind in PICKABLE)


class Board:
    def __init__(self, hub, magnifier=None, presenter=None, sight=None, clock: Callable[[], float] = time.time,
                 listen: bool = True, log: Callable[[dict], None] | None = None) -> None:
        self.hub, self.magnifier, self.presenter, self.sight = hub, magnifier, presenter, sight
        self.clock = clock
        self.log = log  # the interaction log: when code, not the model, picks what "that" means
        self.changes: deque[Change] = deque(maxlen=RECENT)
        if listen:  # the app's one board keeps the changes; a stand-in for one lookup does not
            hub.scene.on_change.append(self._changed)

    def _changed(self, op: str, ids: tuple[str, ...]) -> None:
        self.changes.append(Change(self.hub.scene.version, op, ids, self.clock()))

    def when_asked(self) -> WhenAsked:
        """What is pointed at now, saved for a request being made now (C2)."""
        scene, mag = self.hub.scene, self.magnifier
        layer = getattr(self.hub, "layer", None)  # the cells layer, if there is one (population.layer_of)
        cell = (layer.shown, layer.selected) if layer is not None and layer.shown and layer.selected else None
        return WhenAsked(self.clock(), scene.selected, getattr(scene, "selected_at", 0.0), cell,
                         getattr(layer, "selected_at", 0.0) if cell else 0.0,
                         getattr(mag, "displayed_object", getattr(mag, "object_id", None)) if mag is not None and mag.open else None)

    # --- the state ---------------------------------------------------------------------------
    def now(self) -> BoardState:
        from .agent import _box_in
        from .brain.toolbox.common import in_view

        hub, info = self.hub, self.hub.latest_info
        area = float((getattr(info, "width", 0) or 1) * (getattr(info, "height", 0) or 1))
        items = []
        trails = getattr(hub, "trails", None)
        reading = hub.scale_reading() if trails is not None and getattr(trails, "trails", None) and hasattr(hub, "scale_reading") else None
        for o in hub.scene.objects.values():
            if o.trial is not None:
                continue  # a blind A/B/C arm is not on the board until its vote
            box = _box_in(o, info) if info is not None else o.bbox
            items.append(Item(o.id, o.kind, o.label, box, (box[2] - box[0]) * (box[3] - box[1]) / area, o.status, o.source,
                              o.confidence, in_view(o, info), o.parent, o.created_at, o.taught,
                              trails.words(o.id, reading) if trails is not None else None, how_made(o)))
        mag = self.magnifier
        view = getattr(self.sight, "view", None) if self.sight is not None else None
        seen = ()
        if view is not None and view.current(info):
            seen = tuple(Seen(t.id, t.name, b) for t in view.things if (b := view.box_now(t, info)) is not None)
        return BoardState(
            version=hub.scene.version,
            frame_id=getattr(info, "id", None),
            epoch=getattr(info, "epoch", None),
            items=tuple(items),
            selected=hub.scene.selected,
            magnifier={"open": bool(mag and mag.open), "object": getattr(mag, "displayed_object", getattr(mag, "object_id", None)) if mag and mag.open else None,
                       "level": getattr(mag, "level", None) if mag and mag.open else None},
            presenter={"open": bool(self.presenter and self.presenter.open),
                       "rid": getattr(self.presenter, "rid", None) if self.presenter and self.presenter.open else None},
            seen=seen,
            recent=tuple(self.changes),
            at=self.clock(),
            evidence=_evidence(hub),
            shown=_shown(hub),
        )

    # --- what a request means ----------------------------------------------------------------
    def resolve(self, ref: Any, ctx: ToolContext):
        """The outline (scene.SceneObject) `ref` means for this request, now. Raises ToolFailed
        saying why when there is none."""
        scene = self.hub.scene
        from .brain.registry import step_output, typed_step_output

        if isinstance(ref, dict) and set(ref) == {"step", "output"}:
            ref = typed_step_output(ctx, ref, kind="outline")["id"]
        text = str(ref or "").strip()
        if text.startswith("@"):
            text = step_output(ctx, text)
        if not text:
            return self._that(self.now(), ctx, None)
        if text in scene.objects:
            return scene.objects[text]
        if ctx.aliases.get(text) in scene.objects:  # re-outlined by this answer
            return scene.objects[ctx.aliases[text]]
        if ID.fullmatch(text):
            # An explicit identity is not a deictic request. Only its exact
            # request-owned alias above may replace it, never another selection.
            raise ToolFailed(f"I can't find {text!r} on screen", next="read its retained source, or name another outline")
        m = TARGET.fullmatch(text)
        listed = getattr(ctx, "targets", None) or ctx.objects  # the look's own numbers when it pointed (M2.1 re-check SF2)
        if m and listed:
            i = int(m.group(1)) - 1
            if 0 <= i < len(listed) and listed[i] in scene.objects:
                return scene.objects[listed[i]]
        if m:
            # The step names this answer's own target, and the answer outlined fewer (live 2026-10-10 20:33:
            # zoom on "target 1" with nothing outlined). Said as what to do, so the request's recovery can.
            made = len(listed or [])
            raise ToolFailed(f"this answer outlined {'nothing' if not made else made}, so there is no {text!r} yet",
                             next="find it first (find with what= the thing), then use its outline")
        state = self.now()
        m = ORDINAL.fullmatch(text)
        if m:
            return self._ordinal(state, ctx, ORDINALS[m.group(1).lower()], text)
        if CLICKED.fullmatch(text):
            clicked = self._clicked(state, recent_only=False, until=_until(state, ctx))
            if clicked is None:
                raise ToolFailed("you haven't clicked on anything I can find", next="click the thing you mean")
            return scene.objects[clicked.id]
        if DEICTIC.fullmatch(text):
            return self._that(state, ctx, text)
        key = text.lower()
        named = [i for i in state.outlines if i.label and (i.label.lower() == key or key in i.label.lower())]
        if named:
            return scene.objects[max(named, key=lambda i: i.created).id]
        raise ToolFailed(f"I can't find {text!r} on screen", next="click it, or ask me to find it")

    def _that(self, state: BoardState, ctx: ToolContext, said: str | None):
        scene = self.hub.scene
        for oid in ctx.objects:
            if oid in scene.objects:
                return scene.objects[oid]
        pointed = getattr(ctx, "pointed", None)  # this request's own step selected or magnified it
        if pointed in scene.objects:
            return scene.objects[pointed]
        asked = getattr(ctx, "when_asked", None)
        picked = self._guess(state, said, asked, ctx.aliases)
        from .brain.intent import log_override

        now = picked
        if asked is not None:
            try:
                now = self._guess(state, said, None, ctx.aliases)
            except ToolFailed:
                now = None  # nothing would be meant now: for the log only, never a failure here
        log_override(self.log, "board-that", said=said or "", picked=picked.id, label=picked.label,
                     question=ctx.question,  # the model named no object: code chose one
                     **({"asOf": "the request", "nowWouldBe": now.id if now is not None else None}
                        if now is not picked else {}))
        return picked

    def _guess(self, state: BoardState, said: str | None, asked: WhenAsked | None = None, aliases: dict | None = None):
        """What "that" most likely means when the model named nothing (logged as an override), as of when the
        request was made (`asked`; None: now)."""
        scene = self.hub.scene
        selected = asked.selected if asked is not None else state.selected
        magnified = asked.magnified if asked is not None else state.magnifier["object"]
        until = asked.at if asked is not None else state.at
        pointed = pointed_last(scene, asked, state.at)
        if pointed is not None and pointed != selected and pointed in scene.objects:
            return scene.objects[pointed]  # a click newer than the selection (#179 re-check SF2)
        if selected is not None:
            if selected in scene.objects:
                return scene.objects[selected]
            if (aliases or {}).get(selected) in scene.objects:  # re-outlined by this answer
                return scene.objects[aliases[selected]]
            if asked is not None:
                # What was meant has gone since: said, never something else acted on in its place.
                raise ToolFailed("the outline you had selected when you asked is no longer on screen",
                                 next="click it again")
        clicked = self._clicked(state, recent_only=True, until=until)
        if clicked is not None:
            return scene.objects[clicked.id]
        if magnified in scene.objects:
            return scene.objects[magnified]
        before = [i for i in state.outlines if i.created <= until]
        made = [i for i in before if i.source.startswith("ai")]
        pick = max(made or before, key=lambda i: i.created, default=None)
        if pick is not None:
            return scene.objects[pick.id]
        if asked is not None and state.outlines:
            # Outlined only since the request: not what it was about.
            raise ToolFailed(f"nothing was outlined when you asked{' for ' + repr(said) + ' to mean' if said else ''}",
                             next="click it, or ask me to find it")
        if said:
            raise ToolFailed(f"nothing is outlined for {said!r} to mean; ask me to find it first",
                             next=None)  # no next: the message says what to do
        raise ToolFailed("nothing is outlined yet; ask me to find it first", next=None)  # no next: the message says what to do

    def _clicked(self, state: BoardState, recent_only: bool, until: float) -> Item | None:
        """The newest outline the user's click made by `until` (when the request was made, or now)."""
        clicks = [i for i in state.outlines if i.source == "click" and i.created <= until
                  and (not recent_only or until - i.created <= CLICK_RECENT_S)]
        return max(clicks, key=lambda i: i.created, default=None)

    def _ordinal(self, state: BoardState, ctx: ToolContext, n: int, said: str):
        """"The second one": of what this request outlined, else of the app's latest group of
        outlines, in the order they were made."""
        scene = self.hub.scene
        among = [oid for oid in ctx.objects if oid in scene.objects]
        if not among:
            made = sorted((i for i in state.outlines if i.source.startswith("ai")), key=lambda i: i.created)
            among = [i.id for i in made]
        if not among:
            raise ToolFailed(f"nothing is outlined for {said!r} to mean", next="click it, or ask me to find it")
        i = n - 1 if n > 0 else len(among) - 1
        if not 0 <= i < len(among):
            raise ToolFailed(f"there {'is' if len(among) == 1 else 'are'} only {len(among)} outlined",
                             next="say which one, or click it")
        return scene.objects[among[i]]


def pointed_last(scene, asked: WhenAsked | None, now: float) -> str | None:
    """What the user pointed at last when they asked (`asked`; None: as of `now`): the newer of the outline selected
    and a click just before (CLICK_RECENT_S), each with its time (C2). A click on empty space outlines without
    selecting, so a selection made earlier is not what a click just before the request meant (#179 re-check SF2).
    The one rule "that" (Board._guess) and research's object (start_researcher) both read."""
    selected = asked.selected if asked is not None else scene.selected
    selected_at = asked.selected_at if asked is not None else float(getattr(scene, "selected_at", 0.0) or 0.0)
    until = asked.at if asked is not None else now
    clicks = [o for o in scene.objects.values() if o.source == "click" and getattr(o, "trial", None) is None
              and o.kind in PICKABLE and o.created_at <= until and until - o.created_at <= CLICK_RECENT_S]
    newest = max(clicks, key=lambda o: o.created_at, default=None)
    if newest is not None and newest.id != selected and newest.created_at > selected_at:
        return newest.id
    return selected


def _until(state: BoardState, ctx: ToolContext) -> float:
    """When "that" is read as of: when the request was made, else now."""
    asked = getattr(ctx, "when_asked", None)
    return asked.at if asked is not None else state.at


def _shown(hub) -> tuple[str, ...]:
    """What the studio last reported it shows (contract 10: the screen, not only the board)."""
    screen = getattr(hub, "screen", None)
    try:
        return tuple(screen.lines()) if screen is not None else ()
    except Exception:
        return ()


def _evidence(hub) -> tuple[str, ...]:
    """The evidence lines of the conversation going on now (population.evidence_lines)."""
    from .population import evidence_lines

    day = getattr(hub, "execution_day", None)
    try:
        return evidence_lines(hub, getattr(day, "active_id", None))
    except Exception:
        return ()
