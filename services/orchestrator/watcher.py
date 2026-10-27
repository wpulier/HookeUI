"""Watching for something to happen, and saying so when it does ("tell me when they stop
moving", "let me know if something swims in", "tell me when the cell divides").

The model chooses what to watch for and how it is measured (tool `watch`); code only measures
and reports:
- "motion": the share of followed things moving (movement.py) every few seconds; it is told when
  the share goes below or above the value the model set, two checks in a row;
- "appears": the hub notices something new in a still view (quality.StillChange "content");
- "look": a yes/no question the vision model answers about the view now, every so often; it
  fires on the model's yes.

What fired, with the measurement, is said at once (narrator, "interrupt"), on the delegation that
asked, and shown in the chat otherwise; so is the end of a watch that ran out of time. At most
MAX_ACTIVE watches; each check is logged ("watch").
"""
from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

MAX_ACTIVE = 3
LONGEST_S = 15 * 60.0
DEFAULT_S = 5 * 60.0
MOTION_EVERY_S = 5.0
LOOK_EVERY_S = 15.0


@dataclass
class Watch:
    id: str
    kind: str  # motion | appears | look
    what: str  # what the user is waiting for, in their words
    below: float | None = None  # motion: fire when the moving share is below this (0-1)
    above: float | None = None  # ...or above it
    question: str = ""  # look: the yes/no question
    until: float = 0.0  # monotonic deadline
    started: float = field(default_factory=time.monotonic)
    checks: int = 0
    last: str = ""  # the latest measurement, in words
    task: asyncio.Task | None = None
    origin: dict = field(default_factory=dict)  # delegation_id, asked: where it is said
    event: str = ""  # terminal outcome, set once by the watcher
    seconds: int = 0  # elapsed on the same clock that set the deadline
    measurement: dict = field(default_factory=dict)

    def facts(self) -> dict:
        facts = {"kind": self.kind, "what": self.what, "checks": self.checks, "last": self.last,
                 "seconds": self.seconds, "fired": self.event == "fired"}
        if self.measurement:
            facts["measurement"] = dict(self.measurement)
        if self.kind == "look" and self.last:
            facts["observation"] = {"text": self.last, "source": "model"}
        return facts


class Watcher:
    def __init__(self, measure_motion: Callable[[], Awaitable[tuple[float, str] | None]],
                 ask_look: Callable[[str], Awaitable[tuple[bool, str] | None]],
                 say: Callable[[Watch, str, bool], None], log: Callable[[dict], None] = lambda r: None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        self.measure_motion = measure_motion  # () -> (moving share 0-1, words) or None (nothing to follow)
        self.ask_look = ask_look  # question -> (yes, what the model saw) or None
        self.say = say  # (watch, text, fired)
        self.log = log
        self.sleep = sleep
        self.clock = clock
        self.active: dict[str, Watch] = {}
        self._ids = itertools.count(1)
        self._appeared: asyncio.Event | None = None

    # --- starting and stopping -----------------------------------------------------------------
    def start(self, kind: str, what: str, *, below: float | None = None, above: float | None = None,
              question: str = "", seconds: float = DEFAULT_S, origin: dict | None = None) -> Watch:
        if len(self.active) >= MAX_ACTIVE:
            raise ValueError(f"already watching for {len(self.active)} things; stop one first")
        if kind == "motion" and below is None and above is None:
            raise ValueError("watching motion needs below or above (a share of the things moving, 0 to 1)")
        if kind == "look" and not question:
            raise ValueError("watching with looks needs a yes/no question")
        if kind not in ("motion", "appears", "look"):
            raise ValueError(f"cannot watch {kind!r}: motion, appears or look")
        w = Watch(f"w{next(self._ids)}", kind, what, below, above, question,
                  self.clock() + max(10.0, min(LONGEST_S, seconds)), origin=origin or {})
        w.started = self.clock()
        self.active[w.id] = w
        w.task = asyncio.ensure_future(self._run(w))
        return w

    def stop(self, which: str | None = None) -> list[Watch]:
        """Stop one watch (by id, or words in what it watches for), or all of them."""
        stopped = [w for w in self.active.values()
                   if which in (None, "", "all") or which == w.id or which.lower() in w.what.lower()]
        for w in stopped:
            # Report even a task cancelled before it enters _run. Its cancellation
            # handler uses the same terminal guard, so the result is filed once.
            if w.task is not None and not w.task.done():
                w.task.cancel()
            self._finish(w, "cancelled", f"Stopped watching for {w.what}: cancelled by you.")
        return stopped

    def appeared(self, kind: str, info=None) -> None:
        """Hub.on_still_change: something new in a still view ("content"), or light or focus."""
        if kind == "content" and self._appeared is not None:
            self._appeared.set()

    def describe(self) -> str:
        if not self.active:
            return "not watching for anything"
        now = self.clock()
        return "; ".join(f"{w.id}: {w.what} ({w.kind}, {int(now - w.started)} s so far, {w.checks} checks"
                         f"{', last: ' + w.last if w.last else ''})" for w in self.active.values())

    # --- the watching itself -------------------------------------------------------------------
    async def _run(self, w: Watch) -> None:
        try:
            fired, text = await self._until(w)
        except asyncio.CancelledError:
            self._finish(w, "cancelled", f"Stopped watching for {w.what}: cancelled.")
            return
        except Exception as exc:  # a broken check ends the watch, and says so
            self._finish(w, "failed", f"Stopped watching for {w.what}: {type(exc).__name__}: {exc}"[:200])
            return
        self._finish(w, "fired" if fired else "timed-out", text)

    def _finish(self, w: Watch, event: str, text: str) -> None:
        if w.event:
            return
        w.event, w.seconds = event, round(max(0.0, self.clock() - w.started))
        fired = event == "fired"
        self.active.pop(w.id, None)
        self.log({"kind": "watch", "id": w.id, "watch": w.kind, "what": w.what, "fired": fired, "checks": w.checks,
                  "seconds": w.seconds, "last": w.last, "event": event})
        self.say(w, text, fired)

    async def _until(self, w: Watch) -> tuple[bool, str]:
        if w.kind == "appears":
            self._appeared = self._appeared or asyncio.Event()
            self._appeared.clear()
            try:
                await asyncio.wait_for(self._appeared.wait(), timeout=max(0.0, w.until - self.clock()))
                w.checks += 1
                return True, f"Something new appeared in the view ({w.what})."
            except asyncio.TimeoutError:
                return False, self._gave_up(w)
        streak = 0
        while self.clock() < w.until:
            w.checks += 1
            if w.kind == "motion":
                got = await self.measure_motion()
                if got is not None:
                    share, words = got
                    w.last = words
                    w.measurement = {"movingShare": share, "below": w.below, "above": w.above}
                    hit = (w.below is not None and share < w.below) or (w.above is not None and share > w.above)
                    streak = streak + 1 if hit else 0
                    self.log({"kind": "watch-check", "id": w.id, "share": round(share, 3), "hit": hit})
                    if streak >= 2:
                        return True, f"{w.what[:1].upper() + w.what[1:]}: {words}"
                await self.sleep(MOTION_EVERY_S)
            else:
                got = await self.ask_look(w.question)
                if got is not None:
                    yes, seen = got
                    w.last = seen
                    w.measurement = {"yes": yes}
                    self.log({"kind": "watch-check", "id": w.id, "yes": yes, "seen": seen[:120]})
                    if yes:
                        return True, f"{w.what[:1].upper() + w.what[1:]}: {seen}"
                await self.sleep(LOOK_EVERY_S)
        return False, self._gave_up(w)

    def _gave_up(self, w: Watch) -> str:
        minutes = (self.clock() - w.started) / 60
        return (f"Stopped watching for {w.what} after {minutes:.0f} min without it happening"
                f"{' (last: ' + w.last + ')' if w.last else ''}.")
