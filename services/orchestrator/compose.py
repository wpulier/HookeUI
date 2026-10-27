"""Several ways to get one thing, tried in turn, at once, or a second only when the first is slow: the first whose
result is accepted wins, and the rest are called off (R5 of docs/design/2026-10-11-model-runtime.md).

Each way is a `Try`: a name and a coroutine factory whose coroutine does the work and judges it, returning the result
when it is accepted and None when it is not (a photo that failed the pixel check, an outline the check said does not
fit). Acceptance is the try's own: a fact the app measures, or a check model's verdict, never this module's.

- **In turn** (`first(..., in_turn=True)`, a cascade): each try runs to its end before the next starts.
- **At once** (a race): every try with no `after_s` starts now.
- **Hedged:** a try with `after_s` starts that long after the first one began, and only when the try it `hedges` is
  still running then (the same search sent again beside a slow one) or, naming none, when nothing has been accepted;
  `when`, if given, must also say yes then (the web search itself still out, not the check of the photo it found).

Every try leaves a record: when it started and ended (seconds from the start), and how it ended: "accepted", "not
accepted", "failed" (with the error), "called off" (another won first), or "not started".
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class Try:
    name: str
    run: Callable[[], Awaitable[Any]]  # returns the accepted result, or None when not accepted
    after_s: float | None = None  # hedged: start this long after the first try began
    hedges: str | None = None  # hedged: start only when this try is still running then
    when: Callable[[], bool] | None = None  # hedged: and only when this says yes then
    record: dict = field(default_factory=dict)


async def first(tries: list[Try], *, in_turn: bool = False) -> tuple[Any, list[dict]]:
    """The first accepted result (None when no try's was), and every try's record."""
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    for t in tries:
        t.record = {"name": t.name, "outcome": "not started"}

    def began(t: Try) -> None:
        t.record["startS"] = round(loop.time() - t0, 3)

    def ended(t: Try, outcome: str, error: BaseException | None = None) -> None:
        t.record.update(outcome=outcome, endS=round(loop.time() - t0, 3))
        if error is not None:
            t.record["error"] = f"{type(error).__name__}: {error}"[:200]

    if in_turn:
        for t in tries:
            began(t)
            try:
                result = await t.run()
            except asyncio.CancelledError:
                ended(t, "called off")
                raise
            except Exception as exc:
                ended(t, "failed", exc)
                continue
            if result is not None:
                ended(t, "accepted")
                return result, [t.record for t in tries]
            ended(t, "not accepted")
        return None, [t.record for t in tries]

    running: dict[asyncio.Task, Try] = {}
    waiting = sorted((t for t in tries if t.after_s is not None), key=lambda t: t.after_s)

    def start(t: Try) -> None:
        began(t)
        running[asyncio.ensure_future(t.run())] = t

    for t in tries:
        if t.after_s is None:
            start(t)
    try:
        while running or waiting:
            due = waiting[0].after_s - (loop.time() - t0) if waiting else None
            if not running or (due is not None and due <= 0):
                t = waiting.pop(0)
                hedged = next((r for r in running.values() if r.name == t.hedges), None) if t.hedges else None
                if (t.hedges is None or hedged is not None) and (t.when is None or t.when()):
                    start(t)
                else:
                    t.record["outcome"] = "not started"  # what it hedged had already ended
                continue
            done, _pending = await asyncio.wait(running, timeout=due, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                t = running.pop(task)
                error = task.exception()
                if error is not None:
                    ended(t, "failed", error)
                elif task.result() is not None:
                    ended(t, "accepted")
                    return task.result(), [t.record for t in tries]
                else:
                    ended(t, "not accepted")
        return None, [t.record for t in tries]
    finally:
        for task, t in running.items():
            task.cancel()
            if "endS" not in t.record:
                ended(t, "called off")
        if running:
            await asyncio.gather(*running, return_exceptions=True)
