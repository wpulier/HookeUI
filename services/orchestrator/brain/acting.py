"""Who carries out a request's acting steps: Hooke's tools directly, or, for a request the voice handed over, the
voice's Hands (voice/hands.py: a typed intent, the partner cursor under its lease, a check at dispatch and a receipt
from Hooke's own state), so a hand-off acts in Hooke as visibly and as truthfully as the voice's own calls (the live
partner spec, 4.2: "every acting tool goes through Hands").

The hand-off sets `acting` around its brain turn (voice/partner_tools.py `_do_in_hooke`); the step runner
(brain/actions.py) reads `current()` when it starts each step. Contextvars are copied when a task is made, so steps that
finish late are still carried out by the same hand-off. Typed chat sets nothing and runs as before.
"""
from __future__ import annotations

import contextlib
from contextvars import ContextVar
from typing import Any, Awaitable, Callable, Protocol


class Acting(Protocol):
    def intent(self, tool: str, args: dict) -> Any:
        """The step's typed intent, None for a look (its result is its answer and it runs directly)."""

    async def step(self, run: Any, args: dict, ctx: Any, start: Callable[[], None],
                   skip: Callable[[str], None]) -> dict:
        """Carry out one step: `start` starts the registry's run (it sets run.task); `skip` ends the step unrun with a
        reason (a takeover before it ran, a question back to the user). Returns the step's receipt."""


ACTING: ContextVar[Acting | None] = ContextVar("acting", default=None)


def current() -> Acting | None:
    return ACTING.get()


@contextlib.contextmanager
def acting(bridge: Acting | None):
    token = ACTING.set(bridge)
    try:
        yield bridge
    finally:
        ACTING.reset(token)
