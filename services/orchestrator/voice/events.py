"""The live partner's events: one bus for everything the voice says or is told unasked (part 2.5 spec, section 4.5).

Every event has a persistent id, a kind, a route and a delivery state:
  - **route**, from its kind (`KINDS`): "interrupt" (said as soon as no key is held, the voice's own unprompted words
    stopped for it: only a failure the user must act on now), "idle" (said in a pause) or "context" (a silent system
    item, never answered). A publisher may pass its own route: the watcher's kinds take theirs from view_watch.DELIVERY,
    and any note can be made silent;
  - **state:** pending, then playing, then heard (or interrupted). Heard is the end of playback of the response that
    carried it (output_audio_buffer.stopped), never its start. A result that is cut off (the buffer cleared, the
    response cancelled or truncated, a barge-in) stays owed: it comes back once at the next pause, with what of it was
    heard, and a second cut retires it into context. Progress, and anything else that is not owed, is dropped when cut;
  - **text** is facts from the app, never the user's words and never words to read out: whenever it reaches the model
    it is wrapped as information (`untrusted.block`).

Each kind (`Kind`) says its route, whether it is owed, whether it is progress (its own channel: said only in a pause, at
least PROGRESS_SPACING_S apart for one job, dropped when cut), how its events coalesce (one per thing it is about, or one
per kind so that a burst is one line) and its rate limit.

The bus holds state and rules only; when to speak is its consumer's (`RealtimeSession`, voice/openai_realtime.py),
through the session's own gate. A bus can be handed to a new session, so what is owed outlives the session that was
to say it (`adopt`)."""
from __future__ import annotations

import contextlib
import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from ..untrusted import block

ROUTES = ("interrupt", "idle", "context")
WAITING = ("pending", "interrupted")  # due to be said (an interrupted one is a result still owed)
OVER = ("heard", "told", "retired", "dropped")
SAME_EVENT_S = 120.0  # the same thing told again this soon after it was said is not said again (its facts are told)
KEPT_S = 600.0  # an event that is over is forgotten this long after
INTERRUPTIONS = 2  # a result cut off this many times is retired into context, never said a third time
PROGRESS_SPACING_S = 8.0  # progress about one job is said at most this often
PROGRESS_KEPT_S = 10.0  # progress not said within this long is stale, and dropped
STALE_S = 60.0  # a waiting event that is not owed is dropped after this long (the view it was about has moved on)


@dataclass(frozen=True)
class Kind:
    """One kind's delivery rule."""
    route: str  # its route, unless the publisher gives one
    owed: bool = False  # said until heard (results); progress and context never are
    progress: bool = False  # the low-priority channel
    one_per: str = "ref"  # "ref": one event per thing it is about; "kind": a waiting one of the kind takes in the next
    newest: bool = False  # folded in, the newer facts replace the waiting one's (a view, a job's progress); else added
    once: bool = True  # the same thing (kind and ref, or the same text) is said once in SAME_EVENT_S
    kept_s: float | None = STALE_S  # a waiting one not said by then is dropped; None: kept until heard (owed)
    window_s: float = 0.0  # context: notes this close to the first are one item
    urgent_ok: bool = False  # `urgent=True` makes it interrupting


RESULT = Kind("idle", owed=True, kept_s=None)
WATCHED = Kind("idle", one_per="kind", newest=True, once=False)  # view_watch.py: its publisher gives the route
PROGRESS = Kind("idle", progress=True, newest=True, once=False)

KINDS: dict[str, Kind] = {
    # results: owed until heard
    "researcher-done": RESULT,
    "researcher-failed": RESULT,
    "capability-done": RESULT,
    "tool-done": RESULT,
    "job-result": RESULT,
    "job-failed": Kind("idle", owed=True, kept_s=None, urgent_ok=True),  # urgent: a failure to act on now
    "job-question": RESULT,  # a job's question or permission request for the user, with its id
    "job-cancelled": RESULT,  # a job's own confirmation that it stopped: "cancelled" is said only from this
    "notice": RESULT,  # a notification the narrator was asked to say (brain/narrator.py)
    # progress: its own channel
    "researcher-progress": PROGRESS,
    "progress": PROGRESS,
    # silent context
    "capability-on": Kind("context"),
    "user-action": Kind("context", one_per="kind", once=False, window_s=1.2),
    "context": Kind("context"),
    # the watcher's: the route its publisher gives (these when it gives none); a burst of one kind is one line
    "slide-moved": WATCHED,
    "focus-drifted": WATCHED,
    "population-changed": WATCHED,
    "cell-divided": Kind("interrupt", one_per="kind", newest=True, once=False),
    "reconnect": Kind("idle", kept_s=30.0),
    # its first line as Talk connects (it talks first): in a pause, never owed, let go if the user speaks first or it
    # is not said soon (`withdraw`, RealtimeSession.talk_down)
    "opener": Kind("idle", kept_s=20.0),
}
OTHER = Kind("idle")  # a kind not registered: said once in a pause if it can be, never chased


@dataclass(eq=False)
class Event:
    id: str
    kind: str
    route: str
    text: str
    ref: str | None = None
    mark: str | None = None  # what is pointed at as it is said (its card, the frame)
    points: list[str] = field(default_factory=list)  # pointed at as its words start (think's result)
    owed: bool = False
    progress: bool = False
    ident: tuple = ()
    created: float = 0.0  # the bus's clock
    wall: float = 0.0  # when it happened (the bench times its line from here)
    state: str = "pending"
    since: float = 0.0  # when it last became due, or entered its state
    interrupted: int = 0  # times cut off before it was heard
    sent: int = 0  # responses that carried it
    merged: list[str] = field(default_factory=list)  # refs folded into it (a burst)
    cut: dict | None = None  # what of it was heard when it was last cut: {"words", "heardS", "ofS"}
    before: Callable[[], Awaitable[None]] | None = None  # drawn as it is said (its mark)
    still: Callable[[], bool] | None = None  # still true when due? (progress whose job has ended is not)
    data: dict = field(default_factory=dict)  # what its delivery needs (a job's task, facts, prepared words)
    asking: bool = False  # in a response asked for and not yet sent
    rid: str | None = None  # the response carrying it now
    _heard: list = field(default_factory=list, repr=False)

    @property
    def due(self) -> bool:
        return self.state in WAITING and not self.asking

    def when_heard(self, callback: Callable[[], None]) -> None:
        """`callback` once the event is heard to its end (at once if it was); never if it is cut off for good."""
        if self.state == "heard":
            with contextlib.suppress(Exception):
                callback()
        elif self.state not in OVER:
            self._heard.append(callback)

    def wrapped(self) -> str:
        """Its facts as the model reads them: information from the app, not the user's words."""
        return block(self.text, tag="hooke-event", source="app")

    def holds(self) -> bool:
        if self.still is None:
            return True
        try:
            return bool(self.still())
        except Exception:
            return True


class Bus:
    def __init__(self, *, kinds: dict[str, Kind] | None = None, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time) -> None:
        self.kinds = dict(KINDS if kinds is None else kinds)
        self.clock, self.wall = clock, wall
        self.events: dict[str, Event] = {}  # by id, oldest first
        self.owner: object | None = None  # the one session that says them
        self._progress_at: dict[str, float] = {}  # job -> when progress about it was last sent
        self._progress_lines: dict[str, int] = {}  # job -> progress lines sent about it

    def kind(self, name: str) -> Kind:
        return self.kinds.get(name, OTHER)

    # --- publishing ------------------------------------------------------------------------------------------------
    def publish(self, kind: str, text: str, *, ref: str | None = None, route: str | None = None, urgent: bool = False,
                mark: str | None = None, points=(), before: Callable[[], Awaitable[None]] | None = None,
                still: Callable[[], bool] | None = None, data: dict | None = None) -> tuple[Event, str]:
        """An event, with how it was taken: "new"; "merged" (folded into one still waiting: a result's later facts are
        added to it, a view's or progress's newer facts replace its own); or "duplicate" (the same thing was said
        lately: not said again)."""
        k = self.kind(kind)
        now = self.clock()
        route = "interrupt" if urgent and k.urgent_ok else (route if route in ROUTES else k.route)
        ident = (kind, ref) if ref else (kind, hashlib.sha256(text.encode()).hexdigest()[:16])
        same = self._latest(lambda e: e.ident == ident)
        if same is not None and same.due:
            self._fold(same, k, now, text, ref, mark, before, data)
            return same, "merged"
        if same is not None and k.once and now - same.created < SAME_EVENT_S:
            return same, "duplicate"
        if k.one_per == "kind":
            waiting = self._latest(lambda e: e.kind == kind and e.route == route and e.state == "pending"
                                   and not e.asking)
            if waiting is not None:
                self._fold(waiting, k, now, text, ref, mark, before, data)
                return waiting, "merged"
        event = Event(f"ev-{uuid.uuid4().hex[:12]}", kind, route, text, ref, mark, [str(p) for p in points][:4],
                      owed=k.owed and route != "context", progress=k.progress, ident=ident, created=now,
                      wall=self.wall(), since=now, before=before, still=still, data=dict(data or {}))
        self.events[event.id] = event
        if event.owed and ref:  # its result is out: progress about it no longer holds
            for e in self.events.values():
                if e.progress and e.ref == ref and e.due:
                    self._end(e, "dropped")
        return event, "new"

    @staticmethod
    def _fold(event: Event, k: Kind, now: float, text: str, ref, mark, before, data) -> None:
        """Newer facts into a waiting event; it waits for anything more as if just published (said together)."""
        event.since = max(event.since, now)
        if k.newest:
            if event.ref and ref and ref != event.ref:
                event.merged.append(event.ref)
            event.text, event.ref = text, ref or event.ref
            event.mark, event.before = mark or event.mark, before or event.before
        elif text not in event.text:
            event.text += "\n" + text
            event.mark, event.before = event.mark or mark, event.before or before
        if data:
            event.data.update(data)

    def _latest(self, match) -> Event | None:
        return next((e for e in reversed(self.events.values()) if match(e)), None)

    # --- what is due -----------------------------------------------------------------------------------------------
    def due(self) -> list[Event]:
        """What may be said, results first (owed before not owed, then oldest first): waiting events that are neither
        context nor progress."""
        found = [e for e in self.events.values() if e.due and e.route != "context" and not e.progress]
        return sorted(found, key=lambda e: (not e.owed, e.created))

    def progress_due(self) -> Event | None:
        """The oldest waiting progress whose job's last line is PROGRESS_SPACING_S old (or that has none)."""
        now = self.clock()
        for e in self.events.values():
            if e.progress and e.due and now - self._progress_at.get(e.ref or "", -1e9) >= PROGRESS_SPACING_S:
                return e
        return None

    def context_due(self) -> list[Event]:
        """Silent notes ready to be told: waiting context events whose window has passed."""
        now = self.clock()
        return [e for e in self.events.values() if e.route == "context" and e.due
                and now - e.created >= self.kind(e.kind).window_s]

    def result_for(self, ref: str) -> Event | None:
        """The result told about this job, if any (its progress then no longer holds)."""
        return self._latest(lambda e: e.ref == ref and e.owed)

    def progress_waiting(self, ref: str) -> bool:
        return any(e.progress and e.ref == ref and (e.due or e.asking) for e in self.events.values())

    def progress_lines(self, ref: str) -> int:
        return self._progress_lines.get(ref, 0)

    def playing(self) -> list[Event]:
        return [e for e in self.events.values() if e.state == "playing"]

    def carried(self, rid: str) -> list[Event]:
        return [e for e in self.events.values() if e.state == "playing" and e.rid == rid]

    def prune(self) -> list[tuple[Event, str]]:
        """Waiting events no longer worth saying (not owed and stale, or progress whose job has ended) are dropped, and
        events long over forgotten. Returns what was dropped, and why."""
        now, out = self.clock(), []
        for e in list(self.events.values()):
            if e.state in OVER:
                if now - e.since > KEPT_S:
                    del self.events[e.id]
                continue
            if not e.due or e.owed:
                continue
            kept = PROGRESS_KEPT_S if e.progress else self.kind(e.kind).kept_s
            if kept is not None and now - e.created > kept:
                self._end(e, "dropped")
                out.append((e, "stale"))
            elif e.progress and (not e.holds() or (e.ref and self.result_for(e.ref))):
                self._end(e, "dropped")
                out.append((e, "its job has ended"))
        return out

    # --- its delivery ----------------------------------------------------------------------------------------------
    def asked(self, events) -> None:
        for e in events:
            e.asking = True

    def released(self, events) -> None:
        """A response asked for them and was not sent (the user started talking): due again as they were."""
        for e in events:
            e.asking = False

    def sent(self, events) -> None:
        now = self.clock()
        for e in events:
            e.asking, e.state, e.since, e.rid = False, "playing", now, None
            e.sent += 1
            if e.progress and e.ref:
                self._progress_at[e.ref] = now
                self._progress_lines[e.ref] = self._progress_lines.get(e.ref, 0) + 1

    def bind(self, rid: str, ids) -> list[Event]:
        """The response that carries them (its id from response.created)."""
        out = []
        for i in ids:
            e = self.events.get(i)
            if e is not None and e.state == "playing" and e.rid is None:
                e.rid = rid
                out.append(e)
        return out

    def heard(self, event: Event) -> None:
        if event.state != "playing":
            return
        self._end(event, "heard")
        callbacks, event._heard = event._heard, []
        for callback in callbacks:
            with contextlib.suppress(Exception):
                callback()

    def cut(self, event: Event, *, words: str = "", heard_s: float | None = None, of_s: float | None = None) -> str:
        """Cut off before it was heard to its end. A result stays owed (due again at the next pause, with what of it
        was heard) until its second cut retires it; anything else is dropped. Returns its new state."""
        if event.state != "playing":
            return event.state
        event.rid = None
        if not event.owed:
            self._end(event, "dropped")
            return event.state
        event.interrupted += 1
        event.cut = {"words": words, "heardS": heard_s, "ofS": of_s}
        if event.interrupted >= INTERRUPTIONS:
            self._end(event, "retired")
        else:
            event.state, event.since = "interrupted", self.clock()
            event.route = "idle" if event.route == "interrupt" else event.route  # it comes back in a pause
        return event.state

    def told(self, event: Event) -> None:
        self._end(event, "told")

    def withdraw(self, kind: str) -> list[Event]:
        """Waiting events of a kind that is never owed, let go unsaid (the user spoke first). Returns them."""
        out = [e for e in self.events.values() if e.kind == kind and e.state in WAITING and not e.owed]
        for e in out:
            self._end(e, "dropped")
        return out

    def _end(self, event: Event, state: str) -> None:
        event.state, event.since, event.asking, event.rid = state, self.clock(), False, None
        if state != "heard":
            event._heard = []

    def adopt(self, owner) -> list[Event]:
        """`owner` says these events from now on. What another session was saying when it ended was not heard to its
        end: cut (a result comes back once). Returns what was cut."""
        cut = []
        if self.owner is not None and self.owner is not owner:
            for e in self.playing():
                self.cut(e)
                cut.append(e)
            for e in self.events.values():
                e.asking = False
        self.owner = owner
        return cut


def bus_of(keeper) -> Bus:
    """The one bus kept on `keeper` (the brain), made on first use. Each Talk session is handed it, so what is owed is
    still owed when Talk is pressed again, and a result that lands after a session ended is said by the next."""
    found = getattr(keeper, "partner_bus", None)
    if not isinstance(found, Bus):
        found = Bus()
        with contextlib.suppress(AttributeError):
            keeper.partner_bus = found
    return found
