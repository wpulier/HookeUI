"""The user's own Stop of a run in progress (the Actions panel's Stop: the studio command `action-cancel`), told apart
from a waiter that went away, and what that Stop really did (`Stop.report`).

A cancellation alone says only that someone no longer waits: the operations runner then keeps contract 4 (a job stops
only if its work has not begun and nobody else needs it). The user's Stop is said explicitly, never inferred from a
cancellation: the run is started inside `stoppable()`, whose holder every task it starts inherits (contextvars are
copied when a task is made), and `action-cancel` sets `requested` on that holder before it cancels the run's task. Code
that reads `requested()` while handling the CancelledError knows the user pressed Stop; any other cancellation, a
disconnect included, reads False.

What a Stop did is said from what the run recorded on its holder, never assumed (L7b, ticket 62 row 12; F078, F095):
- **stopped:** work it ended, each by how it ended ("stopped", "completed" before the Stop reached it, "failed: …");
- **kept:** what had finished and stays (a step done before the Stop, a choice it made, partial files kept and marked);
- **lost:** what was begun and is discarded (a result computed but not used, unsaved work);
- **carries on:** work it asked for that another request still needs, so it was not ended (contract 4).
"""
from __future__ import annotations

import contextlib
from contextvars import ContextVar


class Stop:
    """One stoppable run's holder: `requested` is set only by the user's Stop, just before its task is cancelled.
    `task` is the tool run it waits on now (commands.run_tool sets it as each starts)."""

    __slots__ = ("requested", "task", "late", "outcomes", "listeners", "kept", "lost", "carries_on", "ended_work",
                 "steps")

    def __init__(self) -> None:
        self.requested = False
        self.task = None
        # The steps a whole request started under this holder (brain/actions.py ActionRunner: its sequence and each
        # step's run), for a stop of that request (the voice's stop_work on a hand-off): `stop_steps` cancels them.
        self.steps: set = set()
        # Work it stopped that had not ended when the Stop was answered (operations.Runner._stopped): while any is
        # here the Stop is "stopping", never "stopped". How each ended is kept, and told to `listeners` once all have.
        self.late: set = set()
        self.outcomes: list[str] = []
        self.listeners: list = []
        self.kept: list[str] = []  # finished before the Stop and kept ("the model choice Cellpose-SAM")
        self.lost: list[str] = []  # begun and discarded ("the outlines it was computing")
        self.carries_on: list[str] = []  # asked for by this run, still needed by another request: not ended
        self.ended_work: list[str] = []  # what each outcome was of, in the same order as `outcomes` when known

    def ended(self, task, outcome: str, what: str = "") -> None:
        """Late work ended (`outcome`: "stopped", "completed" or "failed: …"); once none is left, listeners are told."""
        self.late.discard(task)
        self.work(what, outcome)
        if not self.late:
            for listener in list(self.listeners):
                try:
                    listener(list(self.outcomes))
                except Exception:  # what is told of a Stop never breaks the loop
                    pass

    def stop(self) -> None:
        """The user pressed Stop: the tool run going now is cancelled, and none starts after it (run_tool). A run that had
        already ended is left as it ended: what its run says (stopped or done) is the run's own answer, never this."""
        self.requested = True
        if self.task is not None and not self.task.done():
            self.task.cancel()

    def started(self, task) -> None:
        """A step of the request this holder stops (its steps' sequence, or one step's run), until it ends."""
        if task is None or task.done():
            return
        self.steps.add(task)
        task.add_done_callback(self.steps.discard)

    def stop_steps(self) -> list:
        """The user stopped the whole request: every step it started that has not ended is cancelled, and none starts
        after (its sequence is one of them). The tasks cancelled, for the caller to wait on: stopped only once they end."""
        self.requested = True
        cancelled = [task for task in list(self.steps) if not task.done()]
        for task in cancelled:
            task.cancel()
        return cancelled

    # --- what the run tells its holder, as it goes ---------------------------------------------------------------
    def did(self, what: str) -> None:
        """A step of the run finished before any Stop: whatever it changed stays (said as kept if a Stop follows)."""
        if what and what not in self.kept:
            self.kept.append(what)

    def discarded(self, what: str) -> None:
        """Work of the run that the Stop throws away (begun, never shown or saved)."""
        if what and what not in self.lost:
            self.lost.append(what)

    def shared(self, what: str) -> None:
        """Work this run asked for that another request still needs: the Stop dropped only this run's wait for it."""
        if what and what not in self.carries_on:
            self.carries_on.append(what)

    def work(self, what: str, outcome: str) -> None:
        """One piece of work the Stop ended, and how (`Runner._stopped`'s outcome), with what it was."""
        self.ended_work.append(what)
        self.outcomes.append(outcome)

    # --- what is said ----------------------------------------------------------------------------------------------
    def report(self, title: str) -> dict:
        """What this Stop did, from what the run recorded: {"outcome", "stopped", "kept", "lost", "carriesOn", "words"}.

        `outcome` is "stopping" while work it ended has not ended yet, "failed" if any did not stop cleanly, "stopped"
        when everything it ended stopped, "completed" when the work had finished before the Stop reached it, and
        "nothing" when the Stop ended no work at all (none was running, or all of it is still needed elsewhere). Nothing
        is said as stopped that the run did not record as stopped."""
        if self.late:
            outcome = "stopping"
        elif any(o.startswith("failed") for o in self.outcomes):
            outcome = "failed"
        elif not self.outcomes:
            outcome = "nothing"
        elif all(o == "stopped" for o in self.outcomes):
            outcome = "stopped"
        else:
            outcome = "completed"
        stopped = [f"{what or 'its work'}: {how}" for what, how in zip(self._names(), self.outcomes)]
        lost = list(self.lost)
        if outcome == "completed" and not lost:
            lost.append("the result it finished just as you pressed Stop (not used)")
        words = [{"stopping": f"Stopping “{title}”: its work hasn't ended yet (you pressed Stop).",
                  "failed": f"“{title}” did not stop cleanly after you pressed Stop.",
                  "stopped": f"Stopped “{title}” (you pressed Stop).",
                  "completed": f"“{title}” had already finished its work when you pressed Stop; nothing new was applied.",
                  "nothing": f"You pressed Stop on “{title}”: none of its work was ended (none was running)."}[outcome]]
        if outcome == "nothing" and self.carries_on:
            words[0] = f"Stopped waiting for “{title}” (you pressed Stop); its work was not ended."
        if self.carries_on:
            words.append("Still running because another request needs it: " + "; ".join(self.carries_on) + ".")
        # Only what was recorded, or what the runner guarantees (nothing after a Stop is applied): never assumed.
        words.append("Kept: " + "; ".join(self.kept) + "." if self.kept else "Kept: none recorded.")
        if lost:
            words.append("Discarded: " + "; ".join(lost) + ".")
        elif outcome == "stopped":
            words.append("Nothing of its unfinished work was applied.")
        elif outcome == "stopping":
            words.append("Nothing it finishes from now on is applied.")
        elif outcome == "nothing":
            words.append("Discarded: nothing.")
        else:
            words.append("Discarded: none recorded.")
        return {"outcome": outcome, "stopped": stopped, "kept": list(self.kept), "lost": lost,
                "carriesOn": list(self.carries_on), "words": " ".join(words)}

    def _names(self) -> list[str]:
        return self.ended_work + [""] * (len(self.outcomes) - len(self.ended_work))


STOP: ContextVar[Stop | None] = ContextVar("user_stop", default=None)


def requested() -> bool:
    """Whether the cancellation being handled now is the user's Stop of the run this code belongs to."""
    stop = STOP.get()
    return stop is not None and stop.requested


def current() -> Stop | None:
    """The stoppable run's holder this code belongs to, or None (nothing records outside one)."""
    return STOP.get()


class AlreadyRunning(Exception):
    """The same run (same Project, action, settings and input) is going now: this one is not started (F094)."""

    def __init__(self, run: str | None, title: str, seconds: float):
        self.run, self.title, self.seconds = run, title, seconds
        super().__init__(f"“{title}” is already running on this input with these settings (started {round(seconds)} s "
                         "ago, perhaps in another window): it was not started twice; the running one's result appears in the view and "
                         "Activity when it ends")


_RUNNING: dict[str, tuple[str | None, str, float]] = {}


def identity(*parts) -> str:
    """What makes two runs the same work, from their Project, action, settings and input (any JSON-able parts)."""
    import hashlib
    import json

    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


@contextlib.contextmanager
def once(key: str, title: str, run: str | None = None):
    """Hold `key` for the body; the same key again while held raises AlreadyRunning before anything starts. Two windows
    (or one reloaded mid-run, whose Run button forgot the run) pressing Run on the same input ran it twice (L7b)."""
    import time

    held = _RUNNING.get(key)
    if held is not None:
        raise AlreadyRunning(held[0], held[1], time.monotonic() - held[2])
    _RUNNING[key] = (run, title, time.monotonic())
    try:
        yield
    finally:
        _RUNNING.pop(key, None)


@contextlib.contextmanager
def stoppable():
    """Run the body (and every task it starts) as one run the user can stop; yields its holder."""
    stop = Stop()
    token = STOP.set(stop)
    try:
        yield stop
    finally:
        STOP.reset(token)
