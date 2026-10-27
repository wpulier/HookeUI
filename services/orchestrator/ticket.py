"""Tickets: one request's claim to change what is on screen.

docs/design/2026-10-06-captures-operations-evidence.md, contract 2; ADR 0010.

A request (a turn, a click, a command) gets a Ticket when it starts. The ticket notes the
generation, at that moment, of everything the request may change: outlines the user made and
outlines the app made, cells, dissected parts, the magnifier, the tracking session, the session. A
clear or close of a kind, a newer request replacing the app's outlines, a cancel, a tracking reset
or another session opened (a new one or one switched to) bumps a generation; a ticket issued before the bump is
then superseded for that scope. `apply.apply` checks the ticket immediately before any result changes the screen, so a
result that arrives after the user cleared, cancelled or moved on keeps its evidence and changes
nothing.

The ticket also holds the request's captures (capture.py) and its explicit model choices
(contract 7), and closes when its last piece of work is done: then its captures are let go.
"""
from __future__ import annotations

import asyncio
import contextvars
import itertools
import time
from contextlib import contextmanager
from typing import Any

SCOPES = ("outline:user", "outline:app", "cell", "part", "magnifier", "track", "motion", "session")
# Old names of scopes, for code written before the rename: "day" is the session's scope.
ALIASES = {"day": "session"}
# Scene kinds and the scopes their clear bumps ("highlight" is both makers' outlines).
KIND_SCOPES = {"highlight": ("outline:user", "outline:app"), "cell": ("cell",), "part": ("part",)}
# A ticket that is never closed lets its captures go after this long (logged), so a bug that
# forgets to close one cannot hold memory for the rest of the day.
MAX_TICKET_S = 600.0

CURRENT: contextvars.ContextVar["Ticket | None"] = contextvars.ContextVar("ticket", default=None)
# Told when a request closes with a choice nothing in it used (contract 7): (ticket, [choice]) -> None.
UNUSED_CHOICE: list = []
_ids = itertools.count(1)


class Generations:
    """A counter per scope; a bump supersedes every ticket issued before it for that scope."""

    def __init__(self) -> None:
        self.values = {scope: 0 for scope in SCOPES}
        self.why: dict[str, str] = {}

    def bump(self, *scopes: str, why: str = "", by: "Ticket | None" = None) -> None:
        """Bump `scopes`. The ticket doing it (`by`) is not superseded by its own bump."""
        for scope in scopes:
            scope = ALIASES.get(scope, scope)
            self.values[scope] = self.values.get(scope, 0) + 1
            if why:
                self.why[scope] = why
            if by is not None and scope in by.issued:
                by.issued[scope] = self.values[scope]

    def bump_kind(self, kind: str | None, why: str = "", by: "Ticket | None" = None) -> None:
        """What a clear of scene kind `kind` (None: everything) supersedes."""
        scopes = [s for k, ss in KIND_SCOPES.items() if kind is None or k == kind for s in ss]
        if kind is not None and kind not in KIND_SCOPES:
            scopes = []
        self.bump(*scopes, why=why or (f"the {kind} outlines were cleared" if kind else "the outlines were cleared"), by=by)

    def value(self, scope: str) -> int:
        return self.values.get(ALIASES.get(scope, scope), 0)


class Ticket:
    """One request: what it may still change, the captures it holds and its explicit choices."""

    def __init__(self, generations: Generations, kind: str, asked: Any = None, choices: dict | None = None,
                 store=None, on_close=None, channel: str = "") -> None:
        self.id = f"r{next(_ids)}"
        from .brain.admission import RequestAdmission
        from .capture import RUN

        self.admission = RequestAdmission(f"{RUN}/{self.id}") if kind in {"turn", "hands", "command"} else None
        if kind == "hands":
            self.admission.reserve_final()  # early voice looks share room for the eventual outcome
        self.workspace_origin: dict | None = None
        self.workspace_store = None
        # The workspace run whose window waits on its outlines' check on the Stage's still (population.placed_on_stage):
        # its run, so the action's reply says where its result is (`placed`).
        self.on_specimen: str | None = None
        self.kind = kind  # "click" | "turn" | "hands" | "command" | "follower" | ...
        self.channel = channel  # "chat" | "voice" | "studio" | ""
        self.asked = asked
        self.generations = generations
        self.issued = dict(generations.values)
        self.choices: dict = dict(choices or {})  # operation -> {"implementation", "policy", "origin", "used"} (`use`)
        self.overrides: dict = {}  # option group -> option, the same choices as settings reads see them (options.overriding)
        # Every choice the request made, in order, with the step that made it (Slice 5: a choice per step; `choices`
        # holds the one in force, which the steps after a `use` see until the next `use` of that operation).
        self.choice_log: list[dict] = []
        # The tickets that share these choices (a spoken request's hands and its hand-off): an unused
        # choice is said only once the last of them is done (scaffolding review, M1.4 item 1).
        self._sharing = {"open": 1}
        self.cancelled: str | None = None
        self.created = time.monotonic()
        self.store = store  # capture.Captures, for holds
        self.holds: list = []
        self.tasks: set[asyncio.Task] = set()
        self.sealed = False
        self.closed = False
        self.on_close = on_close
        # One request is one record (brain/speech.py for spoken ones): what was heard, its own work by role,
        # the delegations matched to it, and the tickets that are the same request (hands and its turn).
        self.heard: Any = None  # brain/speech.Heard: the words, their span on the voice's clock, what was pointed at
        self.intent: str | None = None  # the fast pass's judgment of the words, once made
        self.relation = "new"  # "correction": these words correct or replace the request before (the model's judgment)
        self.superseded_by: "Ticket | None" = None  # the request that corrected it, or joined its unfinished words
        self.work: dict[str, Any] = {}  # "hands", "look", "turn" tasks; "origin": hands' held camera and context
        self.delegations: list[str] = []  # the voice's delegations matched to this request
        self.members: list["Ticket"] = [self]

    # --- what it may still change ---------------------------------------------------------
    def superseded(self, scope: str) -> str | None:
        """Why this ticket may no longer change `scope`, or None."""
        if self.cancelled:
            return f"the request was called off ({self.cancelled})"
        for s in (ALIASES.get(scope, scope), "session"):
            if s in self.issued and self.generations.value(s) != self.issued[s]:
                if s == "session":
                    return "another session was opened since the request was made"
                return "superseded: " + (self.generations.why.get(s) or f"{s} changed since the request was made")
        return None

    def held(self, frame_id) -> Any:
        """The Capture of `frame_id` this request holds, or None: no lookup in the frame ring."""
        return next((h.capture for h in self.holds if h.capture.id == frame_id and not h.released), None)

    def cancel(self, why: str = "cancelled") -> None:
        self.cancelled = why

    def cancel_request(self, why: str) -> None:
        """Call off what this request has not finished, in every ticket that is the same request (its
        hands and its turn). What it already did stays: cancelling changes only later effects."""
        for ticket in self.members:
            ticket.cancel(why)

    def renew(self, *scopes: str) -> None:
        """This request itself changed `scopes` (it replaced the app's outlines): its later
        results there still count."""
        for scope in scopes:
            scope = ALIASES.get(scope, scope)
            self.issued[scope] = self.generations.value(scope)

    # --- what it holds ----------------------------------------------------------------------
    def hold(self, capture, cls: str = "analysis", kind: str = "hold"):
        """Keep `capture` (and its reservation) until this ticket closes; returns the capture."""
        from .capture import Capture

        if self.store is None or not isinstance(capture, Capture) or self.closed:
            return capture  # nothing held to keep (a test's stand-in view has no bytes)
        if any(h.capture is capture and h.kind == kind for h in self.holds):
            return capture  # already held for this kind of work (another kind reserves its own decodes)
        self.holds.append(self.store.hold(capture, cls, kind, f"{self.kind} {self.id}"))
        return capture

    def track(self, task: asyncio.Task | None) -> None:
        """A piece of this request's work: the ticket stays open until it is done."""
        if task is None or self.closed:
            return
        self.tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task) -> None:
        self.tasks.discard(task)
        if self.sealed and not self.tasks:
            self.close()

    def seal(self) -> None:
        """No more work will be added: close once what is running is done."""
        self.sealed = True
        if not self.tasks:
            self.close()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for hold in self.holds:
            hold.release()
        self.holds.clear()
        if self.on_close is not None:
            try:
                self.on_close(self)
            except Exception:
                pass
        self._let_choices_go()

    # --- choices shared by the tickets of one request ---------------------------------------------
    def adopt_choices(self, other: "Ticket | None") -> None:
        """This ticket is the same request as `other` (the hand-off of words hands already acted on):
        it shares `other`'s choices, so what one chose covers what the other does, and an unused choice
        is said once, when the last of them is done."""
        if other is None or other is self:
            return
        for op, c in self.choices.items():
            other.choices.setdefault(op, c)
        other.overrides.update({k: v for k, v in self.overrides.items() if k not in other.overrides})
        if other.admission is not None:
            self.admission = other.admission
        other.choice_log.extend(c for c in self.choice_log if c not in other.choice_log)
        self.choices, self.overrides, self.choice_log = other.choices, other.overrides, other.choice_log
        self._sharing = other._sharing
        self._sharing["open"] += 1
        if self not in other.members:
            other.members.append(self)
        self.members = other.members
        if other.cancelled and not self.cancelled:
            self.cancelled = other.cancelled  # the same request, already called off
        if self.asked is None:
            self.asked = other.asked

    def keep_choices(self, seconds: float) -> None:
        """Hold the choices open for `seconds` after this ticket closes: a hand-off of the same words
        may still adopt them (hands, then the look the request needs)."""
        if not self.choices:
            return
        self._sharing["open"] += 1
        try:
            asyncio.get_running_loop().call_later(seconds, self._let_choices_go)
        except RuntimeError:
            self._sharing["open"] -= 1

    def _let_choices_go(self) -> None:
        self._sharing["open"] -= 1
        if self._sharing["open"] > 0:
            return
        unused = self.unused_choices()
        for listener in list(UNUSED_CHOICE):
            if unused:
                try:
                    listener(self, unused)
                except Exception:
                    pass

    def unused_choices(self) -> list[dict]:
        """The choices made for this request (`use`) that nothing in it used (contract 7: said)."""
        return [{"operation": op, **c} for op, c in self.choices.items()
                if isinstance(c, dict) and not c.get("used") and not c.get("refused")]

    def expired(self) -> bool:
        return not self.closed and time.monotonic() - self.created > MAX_TICKET_S


def generations(hub) -> Generations:
    """The hub's generations, kept on its scene (so a stand-in hub with a Scene has them)."""
    scene = getattr(hub, "scene", None)
    gens = getattr(scene, "generations", None)
    if not isinstance(gens, Generations):
        gens = Generations()
        try:
            scene.generations = gens
        except AttributeError:
            pass
    return gens


_open: "set[Ticket]" = set()


def issue(hub, kind: str, *, asked: Any = None, choices: dict | None = None, channel: str = "") -> Ticket:
    """A new ticket for a request starting now."""
    from .capture import captures

    _expire()
    ticket = Ticket(generations(hub), kind, asked, choices, store=captures(hub), on_close=_open.discard, channel=channel)
    _open.add(ticket)
    return ticket


def cancel_open(channel: str, before: float, why: str) -> list[str]:
    """Call off the open requests of `channel` that started before `before` (time.monotonic()):
    the fast pass judged the user's words a cancel ("never mind"). Their results keep their
    evidence and change nothing on screen. Returns the ids called off."""
    called = []
    for ticket in list(_open):
        if ticket.channel == channel and ticket.created < before and not ticket.cancelled and ticket.kind != "follower":
            ticket.cancel(why)
            called.append(ticket.id)
    return called


def _expire() -> None:
    for ticket in [t for t in _open if t.expired()]:
        ticket.close()


def working(hub) -> bool:
    """Whether this app still owns a request besides the caller; storage removal waits for those users."""
    from .capture import captures

    store, caller = captures(hub), current()
    return any(t is not caller and t.store is store and not t.closed for t in _open)


def open_tickets() -> int:
    return len(_open)


def current() -> Ticket | None:
    return CURRENT.get()


@contextmanager
def using(ticket: Ticket | None):
    """Run the block (and the tasks it starts) as `ticket`'s work, with its choices (contract 7)."""
    from .options import choices_of

    token = CURRENT.set(ticket)
    try:
        with choices_of(ticket.overrides if ticket is not None else None):
            yield ticket
    finally:
        CURRENT.reset(token)


def chosen(operation: str) -> dict | None:
    """The current request's choice for `operation` (`use`), as the work running now may see it: none
    in work started as not for the request (options.unrelated), nor outside a request."""
    from .options import overriding

    request = current()
    if request is None or overriding() is None:
        return None
    return request.choices.get(operation) or None


def reusable(operation: str, made_by: dict | None) -> bool:
    """Whether something already made may stand in for `operation` in the request running now: always,
    unless it chose an implementation (`use`) and this was made by another, which is then made again
    with the choice (ADR 0011: a choice is never silently not honoured; scaffolding review, M1.4 item 2).
    Something its choice made is the choice honoured (marked used)."""
    choice = chosen(operation)
    if not choice:
        return True
    if choice.get("refused"):
        return False  # a refused choice: nothing already made stands in, and nothing is made in its place
    if (made_by or {}).get("implementation") == choice.get("implementation"):
        choice["used"] = True
        return True
    return False


def detached():
    """A context for work that belongs to no request (a background loop): no ticket in it."""
    return using(None)
