"""One thread writes everything of a dataset to disk.

The taps (collector.py) only put an item on a queue, so the frame path and the event loop never
wait on the disk: a stalled disk would otherwise freeze the live view. Items are written in the
order they were put: records (events.jsonl), per-frame facts (frames.jsonl), pictures (hard links,
or copies across disks), summaries (dataset.json) and closing. Files are flushed once a second; only
the summary is synced (manifest.write). Under pressure the collector drops per-frame facts only,
never records (QUEUE_LIMIT).
"""
from __future__ import annotations

import errno
import json
import os
import queue
import shutil
import threading
import time
from pathlib import Path
from typing import Callable

from . import manifest

QUEUE_LIMIT = 50_000  # items waiting: past it the collector drops per-frame facts (and counts them)
FLUSH_S = 1.0


def frame_line(run: int, info) -> dict:
    """What the hub measured about one frame (hub.FrameInfo), compact. Joined to the video by
    (boot, seq): the bridge's run and frame number, as its recording sidecar gives them."""
    fov = info.fov
    stats = [round(float(v), 4) for v in (info.stats or ())]
    line = {"t": round(info.wall_at, 4), "run": run, "id": info.id, "seq": info.seq, "boot": info.boot or None,
            "w": info.width, "h": info.height, "dx": round(info.dx, 2), "dy": round(info.dy, 2), "epoch": info.epoch,
            "tracked": info.tracked, "steady": info.steady,
            "fov": None if fov is None else [round(fov.cx, 1), round(fov.cy, 1), round(fov.radius, 1)],
            "disc": round(info.disc, 3) if info.disc else None, "focus": round(info.focus, 2), "stats": stats,
            "peak": round(info.peak, 2), "q": list(info.quality),
            "changedAt": round(info.changed_at, 4) if info.changed_at else None}
    if info.change is not None:
        line["change"] = info.change
    return line


def link_or_copy(source: Path, target: Path) -> str:
    """Hard-link `source` into the dataset (instant on the same disk, and the dataset keeps the file
    whoever deletes the original); a copy across disks. Returns "linked", "copied" or "exists"."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return "exists"
    try:
        os.link(source, target)
        return "linked"
    except OSError as exc:
        if exc.errno not in (errno.EXDEV, errno.EPERM, errno.ENOTSUP, errno.EMLINK):
            raise
    shutil.copy2(source, target)
    return "copied"


class Writer:
    def __init__(self, folder: Path, on_error: Callable[[str], None] | None = None, counts: dict | None = None) -> None:
        self.folder = folder
        self.queue: queue.SimpleQueue = queue.SimpleQueue()
        self.on_error = on_error or (lambda text: None)
        counts = counts or {}  # a resumed dataset counts on from what it held
        self.events = int(counts.get("events") or 0)
        self.frames = int(counts.get("frames") or 0)
        self.pictures = int(counts.get("pictures") or 0)
        self.error = ""
        self.error_at = 0.0  # when something last could not be written (the Data light shows it a while)
        self._files: dict[str, object] = {}
        self._flushed = time.monotonic()
        self._thread = threading.Thread(target=self._run, name=f"dataset-{folder.name}", daemon=True)
        self._closing = False
        self._closed = threading.Event()
        self._thread.start()

    # --- what the collector puts on the queue (any thread) ----------------------------------
    def put(self, item: tuple) -> None:
        self.queue.put(item)

    def waiting(self) -> int:
        return self.queue.qsize()

    def close(self, timeout: float | None = 10.0) -> bool:
        """Write everything put so far, then stop. True when it finished within `timeout` (0: do not
        wait; it finishes on its own)."""
        if not self._closing:
            self._closing = True
            self.queue.put(("close",))
        return self._closed.wait(timeout)

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def drain(self, timeout: float = 10.0) -> bool:
        """Wait until everything put so far is written (tests, and the summary at stop)."""
        if self._closing:
            return self._closed.wait(timeout)
        done = threading.Event()
        self.queue.put(("call", done.set))
        return done.wait(timeout)

    # --- the thread -------------------------------------------------------------------------
    def _run(self) -> None:
        try:
            while True:
                try:
                    item = self.queue.get(timeout=FLUSH_S)
                except queue.Empty:
                    self._flush()
                    continue
                if item[0] == "close":
                    break
                try:
                    self._do(item)
                except Exception as exc:  # one bad item never stops the dataset
                    self._failed(f"{item[0]}: {type(exc).__name__}: {exc}")
                if time.monotonic() - self._flushed >= FLUSH_S:
                    self._flush()
        finally:
            self._flush()
            for handle in self._files.values():
                try:
                    handle.close()
                except OSError:
                    pass
            self._files.clear()
            self._closed.set()

    def _do(self, item: tuple) -> None:
        kind = item[0]
        if kind == "event":
            self._append(manifest.EVENTS, item[1])
            self.events += 1
        elif kind == "frame":
            _, run, info = item
            self._append(manifest.FRAMES, frame_line(run, info))
            self.frames += 1
        elif kind == "line":  # a line in another file of the dataset (start-state.jsonl, pictures/preroll.jsonl)
            _, name, line = item
            self._append(name, line)
        elif kind == "file":  # a finished file elsewhere, kept in the dataset
            _, source, name = item
            if link_or_copy(Path(source), self.folder / name) != "exists":
                self.pictures += 1
        elif kind == "video":  # a video still being recorded elsewhere (the studio's own): linked now, copied when finished
            _, source, name, finished = item
            target = self.folder / name
            if finished:
                link_or_copy(Path(source), target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    if not target.exists():
                        os.link(source, target)
                except OSError as exc:
                    if exc.errno not in (errno.EXDEV, errno.EPERM, errno.ENOTSUP, errno.EMLINK):
                        raise  # another disk: copied once it is finished
        elif kind == "bytes":  # a picture that exists only in memory
            _, data, name = item
            target = self.folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            self.pictures += 1
        elif kind == "summary":
            self._flush()
            # What is written so far, exactly: everything put before this summary has been written.
            item[1].setdefault("counts", {}).update(events=self.events, frames=self.frames, pictures=self.pictures)
            manifest.write(self.folder, item[1])
        elif kind == "call":
            self._flush()
            item[1]()

    def _append(self, name: str, line: dict) -> None:
        handle = self._files.get(name)
        if handle is None:
            path = self.folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = self._files[name] = path.open("a", encoding="utf-8")
        handle.write(json.dumps(line, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")

    def _flush(self) -> None:
        self._flushed = time.monotonic()
        for name, handle in list(self._files.items()):
            try:
                handle.flush()
            except (OSError, ValueError) as exc:
                self._failed(f"{name}: {exc}")

    def _failed(self, text: str) -> None:
        self.error_at = time.time()
        if text != self.error:
            self.error = text
            print(f"Dataset {self.folder.name}: {text}", flush=True)
            self.on_error(text)
