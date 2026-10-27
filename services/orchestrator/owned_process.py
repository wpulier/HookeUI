"""Finite IO for our own child processes, including their descendants and callers waiting for a worker.

Every child supervised here MUST be created with start_new_session=True. Only that owned process group is stopped, with
the child's own descendants (read from the process table by parent, never matched by name): one that left the group with
setsid is still the child's.
A scope carries a caller's cancellation/deadline through synchronous proof helpers; it never outlives the request.
"""
from __future__ import annotations

import asyncio
import contextvars
import os
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable

REAP_S = .5
POLL_S = .02


class Stopped(RuntimeError):
    pass


@dataclass(frozen=True)
class Bound:
    deadline: float = float('inf')
    stop: Callable[[], bool] = lambda: False

    def check(self):
        if self.stop():
            raise Stopped('cancelled')
        if time.monotonic() >= self.deadline:
            raise Stopped('timed out')


_bound = contextvars.ContextVar('owned-child-bound', default=Bound())


@contextmanager
def scope(seconds: float | None = None, stop: Callable[[], bool] = lambda: False):
    """Carry the original bound; yield it for cooperative checks between parent-side IO chunks."""
    parent = _bound.get()
    deadline = min(parent.deadline, time.monotonic() + seconds) if seconds is not None else parent.deadline
    bound = Bound(deadline, lambda: parent.stop() or stop())
    token = _bound.set(bound)
    try:
        bound.check()
        yield bound
        bound.check()
    finally:
        _bound.reset(token)


PS_S = 2.0  # one read of the process table; a stuck `ps` never holds a Stop


def _table() -> dict[int, tuple[int, str]]:
    """pid -> (parent pid, start time) for every process now, read once. Empty if it can't be read: the group is still
    stopped."""
    try:
        out = subprocess.run(['ps', '-A', '-o', 'pid=,ppid=,lstart='], capture_output=True, text=True,
                             timeout=PS_S).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    table = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2].strip())
    return table


def descendants(pid: int) -> dict[int, str]:
    """The live descendants of `pid` (pid -> start time), by parent pid, read while the tree is intact. Only these are
    ever signalled: a child that called setsid (C7 case 7e, 2026-10-14) left the owned group but is still a descendant,
    and once its parent is gone it belongs to launchd and can no longer be told apart from anyone else's."""
    table = _table()
    children: dict[int, list[int]] = {}
    for child, (parent, _) in table.items():
        children.setdefault(parent, []).append(child)
    found, todo = {}, list(children.get(pid, ()))
    while todo:
        child = todo.pop()
        if child in found or child == pid:
            continue
        found[child] = table[child][1]
        todo.extend(children.get(child, ()))
    return found


def _signal_same(pids: dict[int, str], sig, *, check: bool):
    """Signal each of these processes, and with `check` only while it is still the same process (its start time
    unchanged), so a pid reused since is never signalled."""
    now = _table() if check else None
    for pid, started in pids.items():
        if now is not None and now.get(pid, (None, None))[1] != started:
            continue
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass


def terminate(process):
    """Terminate, kill if necessary, and reap a child, its own process group and its descendants that left the group.
    No global process searches: only the child's own tree, read before anything is signalled."""
    # Read while the child is alive: once it exits its children are launchd's. An exited child has none left to find.
    tree = descendants(process.pid) if process.poll() is None else {}

    def signal_group(sig):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            # Darwin can report EPERM for a group whose last child has just exited but is not reaped yet.
            # A live child still has to be stopped; never swallow an actual refusal to signal that child.
            if process.poll() is None:
                process.send_signal(sig)

    signal_group(signal.SIGTERM)
    _signal_same(tree, signal.SIGTERM, check=False)
    try:
        process.wait(timeout=REAP_S)
    except subprocess.TimeoutExpired:
        pass
    finally:
        # A child may have exited while a descendant still holds its stdout open.
        signal_group(signal.SIGKILL)
        if tree:
            _signal_same(tree, signal.SIGKILL, check=True)
        process.wait(timeout=REAP_S)


@contextmanager
def locked(lock):
    """A cancelled waiter cannot kill the different request currently using a shared worker."""
    bound = _bound.get()
    while True:
        bound.check()
        if lock.acquire(timeout=POLL_S):
            break
    try:
        bound.check()
        yield
    finally:
        lock.release()


@contextmanager
def watching(process, seconds: float, what: str):
    """Bound blocking read/write/communicate, even a child silent before its first protocol byte."""
    parent = _bound.get()
    bound = Bound(min(parent.deadline, time.monotonic() + seconds), parent.stop)
    done = threading.Event()
    failure = []

    def watch():
        while not done.is_set():
            try:
                bound.check()
            except Stopped as exc:
                failure.append(Stopped(f'{what} {exc}'))
                terminate(process)
                return
            done.wait(POLL_S)

    thread = threading.Thread(target=watch, name='owned-child-deadline', daemon=True)
    thread.start()
    try:
        yield
    finally:
        done.set()
        thread.join()
        if failure:
            raise failure[0]


KEEP_BYTES = 64 * 1024  # what is kept of a child's own output (its last words), whatever it writes


def drained(process, keep: int = KEEP_BYTES) -> str:
    """Everything a child writes, read to its end so it never blocks on a full pipe, but only its last `keep` bytes kept:
    code nobody reviewed can't fill the app's memory by printing (Codex's parent-read boundary). Under `watching`, its
    deadline or cancellation ends the child and so the read."""
    tail = bytearray()
    stream = process.stdout
    while True:
        chunk = stream.read(65536)
        if not chunk:
            break
        tail += chunk if isinstance(chunk, bytes) else chunk.encode()
        if len(tail) > keep:
            del tail[:len(tail) - keep]
    process.wait()
    return tail.decode("utf-8", "replace")


async def off_loop(function, *args, **kwargs):
    """Run synchronous owned-child work off the loop; cancellation waits for its finite child cleanup."""
    stop = threading.Event()
    with scope(stop=stop.is_set):
        task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            stop.set()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue  # stop may cancel an already-cancelled request while its child is being reaped
                except Exception:
                    break
            if not task.cancelled():
                task.exception()  # consume the stopped child's failure even if it finished before the await
            raise
