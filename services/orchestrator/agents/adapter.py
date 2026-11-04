"""The one interface every outside agent's adapter has (ADR 0029).

An adapter drives one program the user installed and signed into (Codex, Claude Code, an ACP agent such as Grok
Build), or later a cloud service, and turns what that program says into the same few events:

- ``session``: the program's own conversation id (to continue it later) and the model it uses.
- ``turn``: a turn started (``state: working``) or ended (``state: completed | interrupted | failed``).
- ``text``: something the agent said (``final`` for its answer, else its commentary), and ``delta`` while it streams.
- ``tool``: something it ran or changed (a command, a file edit, a tool call), ``running`` then ``done``/``failed``.
- ``permission``: it asks to do something; only the user answers (``answer``). ``resolved`` when settled.
- ``usage``: what it reports spending (``costUsd`` as the program counts it, cumulative) and tokens.
- ``warning``: something the program said went wrong but didn't end it.
- ``exit``: the program ended.

The conductor (conductor.py) holds the transcript, the status and the safety rules; an adapter only translates.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable

from .transport import Transport

HOOKE_MARK = "[Hooke]"  # every message Hooke relays starts with this: Hooke's words, never the researcher's consent


@dataclass
class Spec:
    """What one launch starts with. Paths and env come from the conductor; nothing here is chosen by the agent."""

    cwd: str
    brief: str  # the first message: the assignment and how to reach Hooke (brief.py)
    instructions: str  # standing instructions (system or developer text): who Hooke is and the rules
    env: dict[str, str] = field(default_factory=dict)
    budget_usd: float | None = None  # where the program can hold itself to a spend (Claude Code --max-budget-usd)
    model: str | None = None
    resume: str | None = None  # the program's own conversation id, to continue it
    # Hooke's own `hooke` command: it reaches only Hooke's door, with this launch's own connection, so where a program
    # can be told so it runs outside the program's sandbox without an ask (Claude Code); nothing else does.
    hooke: str | None = None
    skill: str | None = None  # Hooke's skill file, which the brief points it to: readable without an ask where possible


@dataclass
class Permission:
    """A pending ask, as the user sees it."""

    id: str
    title: str  # "Run a command", "Change files", "Use Write"
    detail: str  # the command, the paths, the tool's input, as the program gave it
    reason: str = ""
    always: bool = False  # whether the program offers "allow for this session"


class Adapter:
    """Base: an event queue, and the five verbs every program supports."""

    program = ""  # codex | claude-code | grok | gemini | acp
    label = ""

    def __init__(self, transport_factory: Callable[[list[str], str, dict], Transport]):
        self._factory = transport_factory
        self.transport: Transport | None = None
        self._events: asyncio.Queue = asyncio.Queue()
        self.permissions: dict[str, Permission] = {}
        self.session: str | None = None
        self.busy = False
        self.ended = False

    # --- what the conductor reads ---------------------------------------------------------------------------------
    def emit(self, kind: str, **fields) -> None:
        if kind == "turn":
            self.busy = fields.get("state") == "working"
        self._events.put_nowait({"type": kind, **fields})

    async def events(self) -> AsyncIterator[dict]:
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    def _finish(self, code: int | None, why: str = "") -> None:
        if self.ended:
            return
        self.ended = True
        self.busy = False
        for pid in list(self.permissions):
            self.emit("resolved", id=pid, outcome="cancelled")
        self.permissions.clear()
        tail = self.transport.stderr_tail() if self.transport is not None else []
        self.emit("exit", code=code, why=why, stderr=tail[-8:])
        self._events.put_nowait(None)

    # --- the verbs ------------------------------------------------------------------------------------------------
    async def start(self, spec: Spec) -> None:
        """Start the program and give it the brief as its first turn."""
        raise NotImplementedError

    async def send(self, text: str) -> str:
        """Hooke's message to the agent (already marked as Hooke's). Steers a turn that is running, else starts the
        next turn. Returns ``steered`` or ``started``."""
        raise NotImplementedError

    async def interrupt(self) -> bool:
        """Stop the turn that is running, keeping the agent for the next message. False when none was running."""
        raise NotImplementedError

    async def answer(self, permission: str, allow: bool, *, always: bool = False) -> None:
        """The user's answer to one ask. Raises KeyError for an ask that isn't waiting."""
        raise NotImplementedError

    async def stop(self) -> None:
        """End the program and everything it started."""
        if self.transport is not None:
            await self.transport.close()
        self._finish(self.transport.returncode if self.transport else None, "stopped")


def marked(text: str) -> str:
    """Text Hooke relays, marked as Hooke's (ADR 0029): the agent's standing instructions say such a message is never
    the researcher's consent to anything."""
    text = (text or "").strip()
    return text if text.startswith(HOOKE_MARK) else f"{HOOKE_MARK} {text}"


def short(value, most: int = 600) -> str:
    text = value if isinstance(value, str) else repr(value)
    text = " ".join(text.split())
    return text if len(text) <= most else text[: most - 1] + "…"
