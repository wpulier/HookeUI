"""The voice's state as the studio shows it (W3, "alive and available"; the live-partner spec 4.8, horizontal 2: what is
going on, shown).

Will, live, 29 Oct: after the voice said "the video search is still running", "it just paused. No indication anywhere
the LIVE model was thinking or working"; and "if it's thinking, if Hooke brain is doing something while it talks, that's
gotta be clear".

The server is the truth of it, as it is of chat's `thinking`: `VoicePresence` reads the session's own phase
(`RealtimeSession.phase`) and Hooke's own work in flight (this Talk session's hand-offs still working, voice/handoffs.py,
and the open Project's think jobs, voice/jobs.py), and sends the studio, over the voice socket, whenever it changes:

    {"type": "voice-state", "state": ..., "n": <work in flight>, "working": [{"key", "label", "since"}], "seq", "at",
     "turn"}

  - **listening**: the talk key is held;
  - **heard**: the released turn was taken, its answer not yet asked for (the commit's short tail);
  - **thinking**: an answer is being made with nothing playing: from the released turn to its first words, and whenever
    the voice waits on a tool of its own (a closer look, a mark), or puts a round's outputs in and asks to go on;
  - **speaking**: its words are audible;
  - **working**: none of those, and `n` pieces of Hooke's own work in flight (each one's `key` is the studio's for the
    same work: `handoff:<id>` for a hand-off's row, `job:<id>` for a think job); a hand-off's wait is Hooke working, never
    the voice thinking, so its row and the state say the same;
  - **idle**: live and ready, nothing in flight; **off**: Talk ended.

Moving into a state is sent at once. Moving out of an active one (heard, thinking, speaking, listening) into idle or
working is sent once it has held SETTLE_S, so the step from one response to the next of a tool round never flashes idle
between them. A tick (TICK_S) reads it again for what changes without a moment of its own (a job starting or ending).
Each change is logged (`partner-state`) for the journeys' "Alive" line (bench/voice_journeys/standard.py). The studio's
older guesses (a 20 s timer on Talk's working dot, a status that waited on the next event) are not needed beside it.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any, Awaitable, Callable

SETTLE_S = 0.35  # leaving an active state is said once it has held this long (no flash of idle between responses)
TICK_S = 0.25  # read again this often for what changes without a moment of its own
LABEL_CHARS = 80
STATES = ("listening", "heard", "thinking", "speaking", "working", "idle", "off")
ACTIVE = frozenset({"listening", "heard", "thinking", "speaking"})
JOB_WORKING = frozenset({"queued", "running", "cancelling"})


def _label(text: str) -> str:
    words = " ".join(str(text or "").split())
    return words if len(words) <= LABEL_CHARS else words[: LABEL_CHARS - 1].rsplit(" ", 1)[0] + "…"


class VoicePresence:
    """One Talk session's state on screen. `session`: its RealtimeSession (`phase()`, `n`); `handoffs`: its
    voice/handoffs.py Handoffs; `jobs`: the hub's think jobs (`of(project)`); `project`: the open Project now."""

    def __init__(self, session, *, handoffs=None, jobs=None, project: Callable[[], str | None] = lambda: None,
                 send: Callable[[dict], Awaitable[None]] | None = None, spawn: Callable[[Awaitable], Any] | None = None,
                 log: Callable[[dict], None] | None = None, wall: Callable[[], float] = time.time) -> None:
        self.session, self.handoffs, self.jobs, self.project = session, handoffs, jobs, project
        self.send, self.spawn, self.log, self.wall = send, spawn, log or (lambda record: None), wall
        self.seq = 0
        self.sent: tuple | None = None  # (state, keys) last sent
        self.last: dict | None = None  # the last message sent
        self._settle: asyncio.TimerHandle | None = None
        self._tick: asyncio.Task | None = None
        self.closed = False

    # --- what is so now ------------------------------------------------------------------------------------------
    def working(self) -> list[dict]:
        """Hooke's own work in flight: this session's hand-offs still working, then the Project's think jobs."""
        out: list[dict] = []
        for handoff in list(getattr(self.handoffs, "open", {}).values()):
            if getattr(handoff, "state", "") == "working":
                out.append({"key": f"handoff:{handoff.id}", "label": _label(handoff.label or handoff.request),
                            "since": round(float(handoff.started), 3)})
        if self.jobs is not None:
            keys = {w["key"] for w in out}
            with contextlib.suppress(Exception):
                for job in self.jobs.of(self.project()):
                    if job.state not in JOB_WORKING:
                        continue
                    # A hand-off kept as a job too (claude/p25-w1's HandoffJob, kind "hooke", the hand-off's own id) has
                    # its row's key, so it is counted once: by its row while it is open, by its job while a late step
                    # of it still runs after the brain answered.
                    key = f"handoff:{job.id}" if getattr(job, "kind", "think") == "hooke" else f"job:{job.id}"
                    if key in keys:
                        continue
                    keys.add(key)
                    out.append({"key": key, "label": _label(getattr(job, "label", "") or job.request),
                                "since": round(float(job.started), 3)})
        return sorted(out, key=lambda w: (w["since"], w["key"]))

    def now(self) -> tuple[str, list[dict]]:
        phase = "off" if self.closed else self.session.phase()
        if phase == "off":
            return "off", []
        working = self.working()
        if phase == "quiet":
            return ("working" if working else "idle"), working
        return phase, working

    # --- saying it -------------------------------------------------------------------------------------------------
    def changed(self) -> None:
        """Read it again; send it when it changed (leaving an active state once it has held SETTLE_S)."""
        if self.sent is not None and self.sent[0] == "off":
            return
        state, working = self.now()
        key = (state, tuple(w["key"] for w in working))
        if key == self.sent:
            self._cancel_settle()
            return
        if (self.sent is not None and self.sent[0] in ACTIVE and state not in ACTIVE and state != "off"):
            if self._settle is None:
                with contextlib.suppress(RuntimeError):  # no running loop (a bare test): said at once instead
                    self._settle = asyncio.get_running_loop().call_later(SETTLE_S, self._settled)
                    return
            else:
                return
        self._cancel_settle()
        self._say(state, working)

    def _settled(self) -> None:
        self._settle = None
        state, working = self.now()
        if (state, tuple(w["key"] for w in working)) != self.sent:
            self._say(state, working)

    def _cancel_settle(self) -> None:
        if self._settle is not None:
            self._settle.cancel()
            self._settle = None

    def _say(self, state: str, working: list[dict]) -> None:
        self.seq += 1
        self.sent = (state, tuple(w["key"] for w in working))
        message = {"type": "voice-state", "state": state, "n": len(working), "working": working, "seq": self.seq,
                   "at": round(self.wall(), 3), "turn": int(getattr(self.session, "n", 0) or 0)}
        self.last = message
        with contextlib.suppress(Exception):
            self.log({"kind": "partner-state", "state": state, "n": len(working), "seq": self.seq, "t": message["at"],
                      "turn": message["turn"]})
        if self.send is not None and self.spawn is not None:
            with contextlib.suppress(Exception):
                self.spawn(self.send(message))

    # --- its life ---------------------------------------------------------------------------------------------------
    def start(self) -> None:
        """Said once now (live, idle), then read again on every moment the session pokes and every TICK_S."""
        self.changed()
        if self._tick is None and not self.closed:
            with contextlib.suppress(RuntimeError):
                self._tick = asyncio.get_running_loop().create_task(self._ticking())

    async def _ticking(self) -> None:
        while not self.closed:
            await asyncio.sleep(TICK_S)
            with contextlib.suppress(Exception):
                self.changed()

    def close(self) -> None:
        """Talk ended: off, said once."""
        if self.closed:
            return
        self.closed = True
        self._cancel_settle()
        if self._tick is not None:
            self._tick.cancel()
        if self.sent is not None and self.sent[0] != "off":
            self._say("off", [])
