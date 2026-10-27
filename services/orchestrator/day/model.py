"""What a session is made of: threads of turns, subjects, preferences, a digest."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

Role = Literal["user", "assistant", "note"]
Channel = Literal["chat", "voice", "system"]


@dataclass(frozen=True)
class Asked:
    """The conversation a request was made in, fixed when the request starts (CONTEXT.md):
    everything that answers it, however late, belongs there. `generation` is the session store's load
    it was made in, so an answer that arrives after midnight or a fresh start never lands in
    a conversation that only has the same id."""
    thread: str
    generation: int
    day: str | None = field(default=None, compare=False)  # origin metadata, never routing/authority
    stale: bool = field(default=False, compare=False)  # a saved origin already outside its writing load
    # The session it was asked in (day/sid.py), set by SessionStore.here(); neither journaled nor compared yet
    # (one session per day: `day` still says the same).
    session: str | None = field(default=None, compare=False)

    @property
    def routing_generation(self) -> int:
        """Ownership for current-load guards, separate from the original numeric facts.

        Loads start at 1. A known-stale origin uses 0 even when its original
        generation happens to equal a later load's token.
        """
        return 0 if self.stale else self.generation


@dataclass
class Turn:
    id: int  # unique within the session and never reused, also across restarts
    role: Role
    channel: Channel
    text: str
    at: float = field(default_factory=time.time)
    frame_id: int | None = None
    refs: dict = field(default_factory=dict)  # objectIds, researcher id, delegation id...
    thread: str = ""  # "t3"
    asked: Asked | None = field(default=None, compare=False)  # immutable origin held by this turn's load


@dataclass
class Thread:
    id: str  # "t3"
    n: int
    title: str = ""
    auto_title: bool = True  # named from its first request until the user names it
    started: float = field(default_factory=time.time)
    last: float = field(default_factory=time.time)
    subjects: list[dict] = field(default_factory=list)  # [{"name", "named_by", "at"}]: what was in view, in order
    summary: str = ""  # older turns, folded in the background (day/memory.py)
    summary_upto: int = 0  # the last turn id the summary covers
    turns: list[Turn] = field(default_factory=list)
    research: list[str] = field(default_factory=list)  # researcher ids started in it
    lines: list[dict] = field(default_factory=list)  # the chat as shown (dialogue.py), latest LINES_KEPT

    @property
    def subject(self) -> str:
        return self.subjects[-1]["name"] if self.subjects else ""

    def wire(self, sid: str) -> dict:
        users = sum(1 for t in self.turns if t.role == "user")
        # Untitled until its first request names it; numbered so two new ones are told apart. Its key names its session.
        return {"id": self.id, "key": f"{sid}/{self.id}", "title": self.title or f"Conversation {self.n}", "started": self.started,
                "last": self.last, "subjects": [s["name"] for s in self.subjects], "requests": users,
                "research": list(self.research)}
