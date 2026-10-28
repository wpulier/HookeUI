"""Who an outside agent's work is recorded as (ADR 0026): `agent:<connection id>`, shown by the agent's own label.

The door sets ACTOR for the work it starts; `commands.run_tool` records it instead of "you", so the operations runner,
evidence and Data's "Made by" say the agent did it. Labels are kept here by connection id (connections.py fills it),
so a result made by a connection that was later disconnected still says who made it.
"""
from __future__ import annotations

from contextvars import ContextVar

ACTOR: ContextVar[str | None] = ContextVar("door_actor", default=None)
_LABELS: dict[str, str] = {}


def current() -> str | None:
    return ACTOR.get()


def remember(connection_id: str, label: str) -> None:
    _LABELS[connection_id] = label


def is_agent(actor) -> bool:
    return isinstance(actor, str) and actor.startswith("agent:ac_")


def maker(actor: str) -> dict:
    """Data's maker for an agent's actor: its label as it named itself, never a verified identity."""
    cid = actor[len("agent:"):]
    return {"kind": "agent", "id": cid, "name": _LABELS.get(cid) or "An agent"}
