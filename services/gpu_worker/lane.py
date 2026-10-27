"""Clicks ahead of background inference on the other workers (MICROMIND_CLICK_LANE; L1b part 2, item 3 of
docs/research/2026-10-20-live-view-regression-bisect.md).

Each worker's queue already puts a click first (inbox.py), but the workers share one GPU and the Mac's cores. A
click's EdgeTAM outline on the click worker took 55 ms alone and 94-549 ms beside a Cellpose-SAM run on the analysis
worker and the follower's steps (sim `cells-clicks`, 2026-10-19/20). The lane lets a background network on another
worker wait, between its chunks, while a click is being answered:

- **Marked** by the worker a click is sent to: from when its reader takes the request until its reply is sent
  (worker.serve). Each worker has its own slot (by role) in a small file all of one app's workers map, named by the
  app's process (their parent): its clicks being answered and when that last changed. Only its owner writes a slot,
  so a worker never waits for its own click, and the slot of a worker that went away goes stale (STALE_S).
- **Waited for** at a chunk's start (`wait`): before each tile of a Cellpose network ("cells"), and before a
  follower's step ("all"); at most WAIT_S each time, and never for a mark older than STALE_S (a worker that went away
  mid-click).

"off" is how it was. Which mode is the default is measured (bench/click_lane_bench.py, docs/bench/click-lane.md).
"""
from __future__ import annotations

import contextlib
import mmap
import os
import struct
import tempfile
import threading
import time
from pathlib import Path

MODES = ("off", "cells", "all")  # nothing waits; Cellpose's tiles wait; Cellpose's tiles and the follower's steps
DEFAULT = "off"
WAIT_S = 0.3  # the most one chunk waits for clicks (a click's own p95 bound)
STALE_S = 2.0  # a mark this old is not waited for
POLL_S = 0.004
_SLOT = "<qd"  # clicks being answered there, when that last changed (time.time: one clock for every process)
_SLOT_SIZE = struct.calcsize(_SLOT)
SLOTS = {"main": 0, "follow": 1, "heavy": 2, "molmo": 3}  # a worker of no role (a bench's own) takes the last
_N = 8
_SIZE = _SLOT_SIZE * _N


def mode(environ=os.environ) -> str:
    value = (environ.get("MICROMIND_CLICK_LANE") or "").strip().lower()
    return value if value in MODES else DEFAULT


def path(parent: int | None = None) -> Path:
    """The file one app's workers share: named by the app's process, their parent."""
    return Path(tempfile.gettempdir()) / f"micromind-click-lane-{parent if parent is not None else os.getppid()}"


class Lane:
    def __init__(self, where: Path, how: str = "all", role: str | None = None) -> None:
        self.how = how
        self.slot = SLOTS.get(role or "", _N - 1)
        self.own = 0  # this worker's clicks being answered
        self.waited_s = 0.0  # how long this worker's chunks waited, in all
        self._lock = threading.Lock()  # marked from the reader thread, cleared from the serving one
        fd = os.open(where, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.fstat(fd).st_size < _SIZE:
                os.ftruncate(fd, _SIZE)
            self._map = mmap.mmap(fd, _SIZE)
        finally:
            os.close(fd)
        self._write()  # a worker started again begins with none (its slot may hold what an earlier one left)

    def _write(self) -> None:
        struct.pack_into(_SLOT, self._map, self.slot * _SLOT_SIZE, self.own, time.time())

    def enter(self) -> None:
        """A click is being answered here (taken from the pipe)."""
        with self._lock:
            self.own += 1
            self._write()

    def leave(self) -> None:
        """Its reply is sent."""
        with self._lock:
            self.own = max(0, self.own - 1)
            self._write()

    def others(self, now: float | None = None) -> int:
        """Clicks the other workers are answering now (a stale slot counts none)."""
        now = time.time() if now is None else now
        total = 0
        for n in range(_N):
            if n == self.slot:
                continue
            count, at = struct.unpack_from(_SLOT, self._map, n * _SLOT_SIZE)
            if count > 0 and now - at <= STALE_S:
                total += count
        return total

    def wait(self, limit_s: float = WAIT_S) -> float:
        """Before a background chunk: wait while another worker answers a click, at most `limit_s`. Seconds waited."""
        if not self.others():
            return 0.0
        t0 = time.monotonic()
        while self.others() and time.monotonic() - t0 < limit_s:
            time.sleep(POLL_S)
        waited = time.monotonic() - t0
        self.waited_s += waited
        return waited

    def hook(self, network) -> bool:
        """Wait for clicks before each forward of a torch network (Cellpose's, run one tile at a time)."""
        register = getattr(network, "register_forward_pre_hook", None)
        if register is None:
            return False
        def before(_module, _args) -> None:
            self.wait()

        register(before)
        return True


def tidy(folder: Path | None = None) -> list[Path]:
    """The files of apps no longer running, removed (the click worker does it as it starts): what was removed."""
    folder = Path(tempfile.gettempdir()) if folder is None else folder
    removed = []
    for old in folder.glob("micromind-click-lane-*"):
        try:
            pid = int(old.name.rsplit("-", 1)[1])
            os.kill(pid, 0)
        except ValueError:
            continue
        except ProcessLookupError:
            with contextlib.suppress(OSError):
                old.unlink()
                removed.append(old)
        except OSError:
            continue  # alive, another user's
    return removed


def opened(role: str | None = None, environ=os.environ) -> Lane | None:
    """This worker's lane (None when MICROMIND_CLICK_LANE is "off", or its file cannot be made: as before)."""
    how = mode(environ)
    if how == "off":
        return None
    if role == "main":
        tidy()
    try:
        return Lane(path(), how, role)
    except OSError:
        return None
