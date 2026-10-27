"""Running maintenance beside the live app: one low-priority worker process at a time, started from a thread.

`Maintainer` is the app's: a thread (never the event loop) that, a while after the start and then every TICK_S, starts
an attempt (`python -m orchestrator.maintenance attempt KEY`, prepare.py) for each entry whose policy is review or auto
and whose check is due (discover.due), one at a time, and on a person's Check or Retry at once. The process runs at the
lowest priority the system has (thumbnails.background: `taskpolicy -b`; the attempt's own thread too), in its own process
group, so Stop, a revoked policy or quitting ends it with everything it started (owned_process.terminate). While it runs,
its attempt file is watched and `changed` is called when it moves on, so every studio sees the step.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from typing import Callable, Mapping

from . import discover, policy, prepare

FIRST_AFTER_S = 120.0  # after the start, behind the catalog's own first look (catalog_news.FIRST_AFTER_S)
TICK_S = 600.0  # how often due checks are looked for
WATCH_S = 0.5  # how often a running attempt's file is looked at
ATTEMPT_S = 3 * 3600  # the longest one attempt may take (downloads and a build included) before it is stopped


def command(key: str, again: bool = False) -> list[str]:
    from ..thumbnails import background

    return background([sys.executable, "-m", "orchestrator.maintenance", "attempt", key, *(["--again"] if again else [])])


class Maintainer:
    def __init__(self, changed: Callable[[], None] = lambda: None, environ: Mapping[str, str] = os.environ,
                 allowed: Callable[[], str] = lambda: "", first_after: float = FIRST_AFTER_S, every: float = TICK_S,
                 command: Callable[[str, bool], list[str]] = command):
        """`allowed` returns "" when unattended maintenance may run now, else why not (the day's local-only mode).
        `command`: the attempt's process (the lowest priority; a test of the flow may run it at its own)."""
        # The environment as it was when the app was made: a later change of the process's (a test's) is not this app's.
        self.changed, self.environ, self.allowed, self.command = changed, dict(environ), allowed, command
        self.first_after, self.every = first_after, every
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._asked: list[str] = []
        self._process: subprocess.Popen | None = None
        self._key = ""
        self._stopping = False
        self._thread: threading.Thread | None = None
        self._resumed: dict[str, float] = {}  # when each unfinished apply was last resumed: once a minute at most
        self._tried: dict[str, float] = {}  # when each entry's attempt last started: one that died early waits RETRY_S

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="maintenance", daemon=True)
            self._thread.start()

    def running(self) -> str:
        with self._lock:
            return self._key if self._process is not None and self._process.poll() is None else ""

    def now(self, key: str) -> None:
        """A person's Check or Retry: this entry's attempt next, whatever its schedule."""
        with self._lock:
            if key not in self._asked:  # also while one runs: it runs again after (its lock keeps it to one at a time)
                self._asked.append(key)
        self.start()
        self._wake.set()

    def cancel(self, key: str) -> bool:
        """Stop this entry's attempt (queued or running). True when one was stopped."""
        from .. import owned_process

        with self._lock:
            queued = key in self._asked
            if queued:
                self._asked.remove(key)
            process = self._process if self._key == key else None
        if process is not None:
            owned_process.terminate(process)
        if process is not None or queued:
            row = prepare.last(key, self.environ)
            if row.get("state") in prepare.RUNNING:
                from . import store
                if row["state"] == "applying":  # the pointer may have moved: its first check must still run (_resume)
                    row.update(pid=0)
                    with self._lock:
                        if key not in self._asked:
                            self._asked.append(key)
                    self._wake.set()
                else:
                    row.update(state="stopped", says="Stopped: you pressed Stop", pid=0, ended=store.now())
                store.write(prepare.path(key, self.environ), row)
            self.changed()
        return process is not None or queued

    def stop(self) -> None:
        """Quitting: no new attempt starts, and the running one ends with everything it started."""
        from .. import owned_process

        with self._lock:
            self._stopping = True
            process = self._process
        self._wake.set()
        if process is not None:
            owned_process.terminate(process)

    def _next(self) -> tuple[str, bool]:
        """(the next entry, whether a person asked for it)."""
        with self._lock:
            if self._asked:
                return self._asked.pop(0), True
        resumed = [k for k in prepare.unfinished(self.environ) if time.monotonic() - self._resumed.get(k, -1e9) >= 60]
        if resumed:  # stopped while applying, under any policy: its first check runs now, not at the next due check
            self._resumed[resumed[0]] = time.monotonic()
            return resumed[0], True
        from .. import catalog_news

        if not catalog_news.automatic(self.environ):  # MICROMIND_CATALOG_AUTO=0: no check online on its own
            return "", False
        now = time.monotonic()
        due = [k for k in policy.keys(self.environ) if discover.due(k, self.environ)
               and now - self._tried.get(k, -1e9) >= discover.RETRY_S]
        # The longest-waiting first, so 10-30 entries take turns (MT4).
        due.sort(key=lambda k: float(discover.last(k, self.environ).get("next", 0)))
        return (due[0], False) if due else ("", False)

    def runnable(self, key: str) -> bool:
        """Whether the entry's attempt may start now: unless the day is local only; a resumed first check after
        applying needs no network, so it runs then too (round 3)."""
        return not self.allowed() or key in prepare.unfinished(self.environ)

    def _loop(self) -> None:
        from ..thumbnails import background_priority

        background_priority()
        self._wake.wait(self.first_after)
        while not self._stopping:
            self._wake.clear()
            key, asked = self._next()
            if key and self.runnable(key):
                self._attempt(key, asked)
                continue  # the next due entry at once; the wait is for when none is
            self._wake.wait(self.every)

    def _attempt(self, key: str, again: bool = False) -> None:
        from .. import owned_process
        from ..paths import SERVICES

        with self._lock:
            if self._stopping:
                return
            self._key = key
            self._tried[key] = time.monotonic()
            self._process = process = subprocess.Popen(
                self.command(key, again), cwd=str(SERVICES), env=dict(self.environ), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        path = prepare.path(key, self.environ)
        seen = None
        try:
            waited = 0.0
            while process.poll() is None and waited < ATTEMPT_S:
                try:
                    stamp = path.stat().st_mtime_ns
                except OSError:
                    stamp = None
                if stamp != seen:
                    seen = stamp
                    self.changed()
                try:
                    process.wait(WATCH_S)
                except subprocess.TimeoutExpired:
                    waited += WATCH_S
        finally:
            owned_process.terminate(process)
            with self._lock:
                self._process, self._key = None, ""
            self.changed()

