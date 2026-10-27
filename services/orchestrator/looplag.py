"""How late the event loop runs, measured from outside it, with what it was doing when it was late.

Live 2026-10-11 02:38-02:39: 8 frames reached the studio in 80 s and one frame's publish took 46.8 s, while
micro-sam loaded on the analysis worker and the machine's load reached 142 on 14 cores. Nothing said where
the loop's time went. Everything live (frames, clicks, the follower, the voice) runs on that one loop, so a
stall of it is a freeze of the app.

A daemon thread posts a tick to the loop every TICK_S and waits for it to run. A tick that has not run after
LIMIT_MS is a stall: the loop thread's stack is taken then, while it is still stuck (sys._current_frames), and
again every STACK_EVERY_S while it lasts (at most STACKS), so the record says what the loop was doing, not what
it did afterwards. When the loop runs again the stall is logged as `loop-lag` (how late, the stacks, the
machine's readings at the time). The count and the worst are on /api/health (`loopLag`) for the sim harness
(scenario `model-load`). About 20 thread wake-ups a second; nothing runs on the loop but setting an event.

What its records found first used on the loop is imported at start, on a thread of its own (`warm_imports`,
WARM_IMPORTS), before anyone asks for it.

Log lines leave the loop too (`LINES`): sim 2026-10-20 (L1a's report, run 3) stalled the loop 228 ms on one line appended
to the interaction log (`agent.log`), and the loaded 28/36 run 478 and 224 ms on `agent.log` and `dialogue._export`. On
the loop a line is handed to one writer thread and the loop goes on; anywhere else it is written before `append` returns,
as before. Either way lines reach each file in the order they were handed in, whole, and `flush` (App.stop, exit) writes
every line still waiting. `MICROMIND_LOOP_WORK=on-loop` keeps today's way (every line written where it is logged), so
the two can be measured side by side.
"""
from __future__ import annotations

import asyncio
import atexit
import importlib
import os
import sys
import threading
import time
import traceback
from collections import deque
from pathlib import Path

TICK_S = 0.05
LIMIT_MS = 200.0
STACK_EVERY_S = 1.0
STACKS = 3
STACK_CHARS = 4000


# Modules whose first import stalled the loop (live 2026-10-11, `loop-lag` stacks), imported at start off it:
# - scipy.ndimage, which brings scipy.special: the first cell measurement (evidence.masks_from_labels), 240 ms at
#   11:40:25 and 243 ms at 16:20:13;
# - openai: the first voice session (12:20:35, 335 ms), and its live types at the first after a restart (`client.live`,
#   16:40:52, 239 ms).
# - google.genai and its types: the first held look (`sight.look` -> `vision.GeminiVision`), 1050 ms in the loaded 28/36
#   sim run of 2026-10-19 and 1439 ms in L1a's run 1; and the first function-calling turn (`gemini_declarations`).
WARM_IMPORTS = ("scipy.ndimage", "openai", "openai.resources.live", "google.genai", "google.genai.types")


def off_loop() -> bool:
    """Whether work L1b moved off the event loop runs off it (the default), or where it is asked for, as before
    (`MICROMIND_LOOP_WORK=on-loop`), so the two can be measured on the same build."""
    return os.environ.get("MICROMIND_LOOP_WORK", "off-loop") != "on-loop"


def on_loop() -> bool:
    """Whether the caller is on a running event loop (the app's), so file work it asks for would stall it."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class Lines:
    """Lines appended to files by one writer thread, in the order they were handed in.

    `append` on the event loop queues the line and returns; anywhere else (a worker thread, a test, a bench) it writes
    every line waiting, its own last, before it returns, so a caller off the loop finds its line in the file as before.
    One lock (`_io`) is held while lines are taken from the queue and written, so two writers never interleave or
    reorder them. A line that could not be written is counted (`failed`, `error`): the caller's work goes on, as
    `dialogue._export` always did."""

    def __init__(self) -> None:
        self._queue: deque[tuple[Path, str]] = deque()
        self._ready = threading.Condition()
        self._io = threading.Lock()
        self._thread: threading.Thread | None = None
        self.handed = 0  # lines handed in since the start
        self.written = 0
        self.failed = 0
        self.error = ""  # the last write's error, if one failed

    def append(self, path: Path | str, line: str) -> None:
        """`line` (without its newline) to the end of `path`, its folder made if it is missing."""
        text = line if line.endswith("\n") else line + "\n"
        with self._ready:
            self._queue.append((Path(path), text))
            self.handed += 1
            if on_loop() and off_loop():
                self._start()
                self._ready.notify()
                return
        self.flush()

    def pending(self) -> int:
        with self._ready:
            return len(self._queue)

    def flush(self) -> None:
        """Every line handed in so far is in its file when this returns (App.stop, exit, a reader in this process)."""
        with self._io:
            while True:
                with self._ready:
                    batch = list(self._queue)
                    self._queue.clear()
                if not batch:
                    return
                self._write(batch)

    def wire(self) -> dict:
        return {"handed": self.handed, "written": self.written, "pending": self.pending(), "failed": self.failed,
                **({"error": self.error} if self.error else {})}

    def _start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="log-lines", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            with self._ready:
                while not self._queue:
                    self._ready.wait()
            self.flush()

    def _write(self, batch: list[tuple[Path, str]]) -> None:
        start = 0
        while start < len(batch):  # consecutive lines for one file go in one open
            path = batch[start][0]
            end = start
            while end < len(batch) and batch[end][0] == path:
                end += 1
            text = "".join(text for _, text in batch[start:end])
            try:
                try:
                    f = path.open("a")
                except FileNotFoundError:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    f = path.open("a")
                with f:
                    f.write(text)
                self.written += end - start
            except (OSError, ValueError) as exc:
                self.failed += end - start
                self.error = f"{path.name}: {type(exc).__name__}: {exc}"[:300]
            start = end


LINES = Lines()  # every log line of this process (agent.log, dialogue._export)
atexit.register(LINES.flush)  # a line handed in before exit is never lost


def warm_imports(modules: tuple[str, ...] = WARM_IMPORTS) -> threading.Thread:
    """Import `modules` on a daemon thread, off the event loop (App.start). It runs at the normal priority: a thread
    holding the interpreter's lock must never wait behind others while the loop waits for it. One that can't be
    imported here is skipped (where it is used, it fails in its own words)."""
    def run() -> None:
        for name in modules:
            try:
                importlib.import_module(name)
            except Exception:
                pass

    thread = threading.Thread(target=run, name="warm-imports", daemon=True)
    thread.start()
    return thread


def stack_of(thread_id: int) -> str:
    """The thread's stack now, innermost last, cut to its last STACK_CHARS characters."""
    frame = sys._current_frames().get(thread_id)
    if frame is None:
        return ""
    text = "".join(traceback.format_stack(frame, limit=30))
    return text[-STACK_CHARS:]


class LoopLag:
    def __init__(self, log=None, readings=None) -> None:
        self.log = log  # (record) -> None, called on the loop once it runs again
        self.readings = readings  # () -> dict: the machine's latest readings (machine.Machine.snapshot)
        self.count = 0  # stalls past LIMIT_MS since the start
        self.worst_ms = 0.0
        self.last: dict | None = None  # the newest stall: when, how long, the first frame of its stack
        self._loop = None
        self._loop_thread = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, loop) -> None:
        """Called on the loop: it is the thread to watch."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._loop = loop
        self._loop_thread = threading.get_ident()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="loop-lag", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def wire(self) -> dict:
        return {"count": self.count, "worstMs": round(self.worst_ms), "limitMs": LIMIT_MS, "last": self.last,
                "lines": LINES.wire()}

    def _run(self) -> None:
        while not self._stop.wait(TICK_S):
            ran = threading.Event()
            when: list[float] = []

            def tick() -> None:
                when.append(time.monotonic())
                ran.set()

            posted = time.monotonic()
            try:
                self._loop.call_soon_threadsafe(tick)
            except RuntimeError:
                return  # the loop is closed
            if ran.wait(LIMIT_MS / 1000):
                continue
            at = time.time() - LIMIT_MS / 1000
            stacks = [stack_of(self._loop_thread)]
            while not ran.wait(STACK_EVERY_S):
                if self._stop.is_set():
                    return
                if len(stacks) < STACKS:
                    stacks.append(stack_of(self._loop_thread))
            ms = (when[0] - posted) * 1000 if when else (time.monotonic() - posted) * 1000
            self._stalled(ms, at, stacks)

    def _stalled(self, ms: float, at: float, stacks: list[str]) -> None:
        self.count += 1
        self.worst_ms = max(self.worst_ms, ms)
        lines = [line for line in (stacks[0] if stacks else "").strip().splitlines() if line.strip()]
        self.last = {"at": round(at, 3), "ms": round(ms), "where": lines[-2].strip() if len(lines) >= 2 else ""}
        readings = {}
        if self.readings is not None:
            try:
                readings = self.readings()
            except Exception:
                readings = {}
        record = {"kind": "loop-lag", "ms": round(ms), "since": round(at, 3), "limitMs": LIMIT_MS,
                  "stacks": stacks, "machine": readings}
        if self.log is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._logged, record)
        except RuntimeError:
            pass

    def _logged(self, record: dict) -> None:
        try:
            self.log(record)
        except Exception:
            pass  # the monitor never takes the app down
