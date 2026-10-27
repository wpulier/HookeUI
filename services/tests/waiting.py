"""Waiting in a test for what the code does on its own time (a thread, a task, a child process, a queue's worker).

`until(lambda: queue.wire()[0]["state"] in ("done", "failed"))` returns as soon as the state is reached, and fails with
what it waited for only past `HANG_S`. The limit catches a hang, nothing else: the 1-6 s polls these replaced ran out at
load 30-50 (other sessions' suites, orchestrators, ffmpeg) while the code was right, and the gate failed. How long
something takes is a `wall_clock` bound or a `timing` test (conftest.py), never a wait's limit. A loop that checks
something stays false for its whole window is not a wait: it keeps its own loop.

The clock and the sleeps are this module's own, taken at import: a test that moves `time.monotonic` (a proof's limit
passed) or stubs `time.sleep` still waits in real time."""
import inspect
from asyncio import sleep as _async_sleep
from time import monotonic as _monotonic, sleep as _sleep

HANG_S = 60.0


def until(predicate, seconds: float = HANG_S, every: float = 0.02, what: str | None = None):
    """`predicate()`'s first true value, polled every `every` s; an AssertionError past `seconds`."""
    deadline = _monotonic() + seconds
    while not (value := predicate()):
        if _monotonic() >= deadline:
            raise AssertionError(f"waited {seconds:g} s for {what or _said(predicate)}")
        _sleep(every)
    return value


async def until_async(predicate, seconds: float = HANG_S, every: float = 0.01, what: str | None = None):
    """`until` inside a test's event loop: the loop's own tasks run while it waits."""
    deadline = _monotonic() + seconds
    while not (value := predicate()):
        if _monotonic() >= deadline:
            raise AssertionError(f"waited {seconds:g} s for {what or _said(predicate)}")
        await _async_sleep(every)
    return value


def _said(predicate) -> str:
    """A lambda by its line of source; anything else (a bound `path.exists`, a local function) by its repr."""
    if getattr(predicate, "__name__", "") == "<lambda>":
        try:
            return inspect.getsource(predicate).strip()
        except (OSError, TypeError):
            pass
    return repr(predicate)
