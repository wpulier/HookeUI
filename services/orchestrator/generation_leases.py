"""Process-local ownership of immutable runtime directories during admitted work."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
import threading
from typing import Callable, Iterator


class Busy(RuntimeError):
    pass


class Missing(RuntimeError):
    pass


_lock = threading.RLock()
_owners: dict[Path, int] = defaultdict(int)
_retire: dict[Path, list[Callable[[], None]]] = defaultdict(list)
_withdrawing: set[Path] = set()


@contextmanager
def _hold(folder: Path, *, require_present: bool) -> Iterator[None]:
    key = folder.resolve()
    with _lock:
        if any(key == root or key.is_relative_to(root) for root in _withdrawing):
            raise Busy("This runtime is being removed; wait for removal before preparing it again")
        if require_present and not key.is_dir():
            raise Missing("This runtime is no longer available; prepare or restore its exact version before running")
        _owners[key] += 1
    try:
        yield
    finally:
        callbacks = []
        with _lock:
            _owners[key] -= 1
            if not _owners[key]:
                del _owners[key]
                callbacks = _retire.pop(key, [])
        for callback in callbacks:
            callback()


@contextmanager
def use(folder: Path) -> Iterator[None]:
    with _hold(folder, require_present=True):
        yield


@contextmanager
def prepare(folder: Path) -> Iterator[None]:
    """Own a runtime before its first directory exists, through preparation's cleanup."""
    with _hold(folder, require_present=False):
        yield


def holding(folder: Path) -> int:
    """How many admitted pieces of work in this process hold `folder` now (0: none). Durable holds are
    orchestrator/maintenance/pins.py's."""
    key = folder.resolve()
    with _lock:
        return sum(n for path, n in _owners.items() if path == key or path.is_relative_to(key))


def when_idle(folder: Path, callback: Callable[[], None]) -> None:
    key = folder.resolve()
    with _lock:
        if _owners.get(key):
            _retire[key].append(callback)
            return
    callback()


async def release(owner) -> None:
    """Release on a thread, even after admission expired; cancellation waits for cleanup."""
    task = asyncio.create_task(asyncio.to_thread(owner.__exit__, None, None, None))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError


@contextmanager
def withdraw(folder: Path) -> Iterator[None]:
    key = folder.resolve()
    with _lock:
        if any(path == key or path.is_relative_to(key) for path in _owners):
            raise Busy('This runtime still has running work; let it finish or stop that work before removing it')
        if any(path == key or path.is_relative_to(key) or key.is_relative_to(path) for path in _withdrawing):
            raise Busy('This runtime is already being removed')
        _withdrawing.add(key)
    try:
        yield
    finally:
        with _lock:
            _withdrawing.remove(key)
