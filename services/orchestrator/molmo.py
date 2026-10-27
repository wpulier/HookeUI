"""Pointing "molmo" (options.POINTING): Molmo 2 4B, on this Mac, points at what the user asked about,
and each point is outlined as a click is (the click segmenter, tools.outline_at).

The look is unchanged: the selected vision model answers and names its targets ("box"). Beside it,
Molmo is asked to point at what the user's words are about (`query_for`), in its own GPU worker
process (gpu_worker/molmo.py), so nothing else waits for it. The vision model decides whether
anything is pointed at, and what it is called (a look with no targets outlines nothing, whatever
Molmo pointed at); Molmo decides where. Each point takes the label of the target whose box holds
it, or else of the nearest one (`assign`); whether each point fell inside one of the model's boxes
is logged as data ("molmo-point"). Molmo found nothing (or is not loaded yet, or failed): the
model's boxes are outlined, as "box" (logged "molmo-none").

Molmo reads a picture in ~2.5 s and its question in ~0.2 s, so after the slide settles the view is
read ahead (`MolmoReader`, as pointing "masks" masks it): a question on a view already read waits
only for its words and the points. Its points are in the pixels of the frame it read and move with
the slide to the look's frame (as outlines do). Numbers: docs/bench/finding.md, "Molmo 2".
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .coords import Crop, gemini_box_to_frame

SETTLE_S = 0.6          # steady this long before reading the view (as Sight and "masks")
GAP_S = 1.0             # at most one read per this many seconds
MOVED_FRACTION = 0.35   # of the eyepiece radius: a read from further back no longer covers the view
LOAD_TIMEOUT_S = 300.0  # the first use loads ~19 GB of weights from disk
TIMEOUT_S = 30.0
POINT_BOX = 24          # px: the box a point stands for when it is matched against outlines on screen

QUERY = 'Point to what this request is about: "{question}"'


def query_for(question: str, how: str = "about") -> str:
    """What Molmo is asked, from the user's words. "about" (the default; grounding bench): the
    request quoted; "question": the words as they are."""
    words = " ".join(str(question or "").split())[:300]
    return words if how == "question" else QUERY.format(question=words.replace('"', "'"))


def _inside(point, box) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def assign(points: list, targets: list[dict], crop: Crop) -> list[dict]:
    """Molmo's points (frame px) as the look's targets: each with the label and confidence of the
    model's target whose box holds it (the smallest), else of the nearest one, and "point". A point
    inside a target's box keeps that box too ("box_2d"), so it is matched against outlines on screen
    as the model's box would be. No targets from the model: nothing is pointed at."""
    boxes = []
    for t in targets:
        box = t.get("box_2d") if isinstance(t, dict) else None
        try:
            frame_box = gemini_box_to_frame([float(v) for v in box], crop) if isinstance(box, list) and len(box) == 4 else None
        except (TypeError, ValueError):
            frame_box = None
        boxes.append((t, frame_box))
    named = [(t, b) for t, b in boxes if isinstance(t, dict)]
    if not named:
        return []
    out = []
    for x, y in points:
        holding = [(t, b) for t, b in named if b is not None and _inside((x, y), b)]
        if holding:
            target, box = min(holding, key=lambda tb: (tb[1][2] - tb[1][0]) * (tb[1][3] - tb[1][1]))
        else:
            with_box = [(t, b) for t, b in named if b is not None]
            target, box = (min(with_box, key=lambda tb: np.hypot((tb[1][0] + tb[1][2]) / 2 - x, (tb[1][1] + tb[1][3]) / 2 - y))
                           if with_box else named[0])
            box = None
        entry = {"label": target.get("label") or "", "confidence": target.get("confidence"), "point": [float(x), float(y)],
                 "inBox": bool(holding)}
        if box is not None:
            entry["box_2d"] = target.get("box_2d")
        out.append(entry)
    return out


def point_box(point) -> tuple[float, float, float, float]:
    x, y = point
    return x - POINT_BOX / 2, y - POINT_BOX / 2, x + POINT_BOX / 2, y + POINT_BOX / 2


@dataclass
class Read:
    """A view Molmo has read, in the pixels of frame `frame_id`."""
    frame_id: int
    epoch: int
    dx: float
    dy: float
    radius: float
    crop: Crop
    ms: float = 0.0
    at: float = field(default_factory=time.monotonic)
    jpeg: bytes = b""  # the bytes it read, so pointing on it never looks the frame up again (ADR 0010)
    hold: object = None  # its capture held (bytes only) while it is the current read: counted (invariant 3)

    def current(self, info) -> bool:
        """Still covers what is in view: same motion epoch, and the slide has not moved far."""
        if info is None or info.epoch != self.epoch:
            return False
        return float(np.hypot(info.dx - self.dx, info.dy - self.dy)) < MOVED_FRACTION * max(self.radius, 1.0)


async def molmo_client(hub):
    """The Molmo worker (started on first use, the model loaded once), or None without a GPU."""
    gpu = getattr(hub, "gpu_molmo", None)
    if gpu is None:
        return None
    gpu = await gpu.ensure_started()
    if not getattr(gpu, "molmo_loaded", False):
        loaded = await gpu.call("molmo_load", timeout=LOAD_TIMEOUT_S)
        gpu.molmo_loaded = True
        gpu.molmo_load = {k: loaded.get(k) for k in ("loadMs", "warmMs", "memoryGb")}
    return gpu


class MolmoPointer:
    """Reads the settled view ahead while "molmo" pointing is in use, and points on request."""

    def __init__(self, hub, log: Callable[[dict], None] | None = None, wanted: Callable[[], bool] | None = None,
                 also: Callable[[], bool] | None = None, client=molmo_client) -> None:
        self.hub = hub
        self.log = log or (lambda record: None)
        self.wanted = wanted or (lambda: bool(hub.clients))
        self.also = also or (lambda: False)  # wanted besides the setting (a blind comparison's "molmo" arm)
        self.client = client
        self.current: Read | None = None
        self.recheck = False
        self._task: asyncio.Task | None = None
        self._unsteady_at = 0.0
        self._last = 0.0
        self._busy = False
        self._lock = asyncio.Lock()  # one request at a time: the worker keeps one picture read

    def enabled(self) -> bool:
        return getattr(self.hub.settings, "pointing", "") == "molmo" or bool(self.also())

    def still_changed(self, kind: str, info) -> None:
        if kind == "content":
            self.recheck = True

    def due(self, info, now: float) -> bool:
        if info is None or not info.steady:
            self._unsteady_at = now
            return False
        if now - self._unsteady_at < SETTLE_S or now - self._last < GAP_S:
            return False
        return self.current is None or not self.current.current(info) or self.recheck

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(0.25)
            if not self.enabled() and self.current is not None and not self._busy:
                self.forget()  # pointing switched away from Molmo: its read-ahead lets its bytes go
            if not self.enabled() or self._busy or not self.wanted() or not self.hub.source.is_fresh():
                continue
            if self.due(self.hub.latest_info, time.monotonic()):
                self._busy = True
                try:
                    await self.read()
                except Exception as exc:
                    self.log({"kind": "molmo-read", "error": f"{type(exc).__name__}: {exc}"[:200]})
                finally:
                    self._busy = False

    def forget(self) -> None:
        """Let the current read-ahead go (its held bytes with it)."""
        if self.current is not None and self.current.hold is not None:
            self.current.hold.release()
        self.current = None

    @staticmethod
    def _crop_of(info, width: int, height: int) -> Crop:
        from .marks import fov_crop

        return fov_crop(info.fov, width, height)

    async def read(self) -> Read | None:
        """Read the settled view once, so a request on it waits only for its own words."""
        from .capture import Refused, captures

        hub = self.hub
        info = hub.best_frame(SETTLE_S) if hasattr(hub, "best_frame") else hub.latest_info
        store = captures(hub)
        frame = store.get(info.id) if info is not None else None
        if frame is None or not frame.jpeg:
            return None
        try:
            # The bytes are kept to point on later: held, so they count against the bound (invariant 3).
            hold = store.hold(frame, "analysis", "bytes", "Molmo's read-ahead")
        except Refused as exc:
            self.log({"kind": "molmo-read", "frameId": info.id, "skipped": str(exc)})
            return None
        self._last = time.monotonic()
        self.recheck = False
        try:
            gpu = await self.client(hub)
            if gpu is None:
                hold.release()
                return None
            crop = self._crop_of(info, info.width, info.height)
            async with self._lock:
                result = await gpu.call("molmo_read", timeout=TIMEOUT_S, frame_id=info.id, jpeg=frame.jpeg,
                                        crop=[crop.x, crop.y, crop.width, crop.height])
        except BaseException:
            hold.release()
            raise
        radius = info.fov.radius if info.fov is not None else min(info.width, info.height) / 2
        if self.current is not None and self.current.hold is not None:
            self.current.hold.release()  # the read it replaces lets its bytes go
        self.current = Read(info.id, info.epoch, info.dx, info.dy, float(radius), crop, ms=float(result.get("ms") or 0.0),
                            jpeg=frame.jpeg, hold=hold)
        self.log({"kind": "molmo-read", "frameId": info.id, "epoch": info.epoch, "ms": result.get("ms")})
        return self.current

    async def point(self, question: str, view) -> dict:
        """Molmo's points for the user's words on `view` (a snapshot), in the view frame's pixels:
        on the view read ahead when it still covers this one (moved with the slide), else on the
        view itself. {"points", "query", "text", "ms", "read" (prompt tokens read), "ahead", "frameId"}."""
        info, image, crop = view
        gpu = await self.client(self.hub)
        if gpu is None:
            raise RuntimeError("no GPU worker for Molmo")
        query = query_for(question)
        ahead = self.current if self.current is not None and self.current.current(info) else None
        t0 = time.perf_counter()
        async with self._lock:
            if ahead is not None and ahead.jpeg:
                result = await gpu.call("molmo_point", timeout=TIMEOUT_S, frame_id=ahead.frame_id, jpeg=ahead.jpeg, query=query,
                                        crop=[ahead.crop.x, ahead.crop.y, ahead.crop.width, ahead.crop.height])
                sx, sy = info.dx - ahead.dx, info.dy - ahead.dy  # from the frame read to this one
            else:
                import cv2

                ok, jpeg = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
                result = await gpu.call("molmo_point", timeout=TIMEOUT_S, frame_id=info.id, jpeg=jpeg.tobytes(), query=query,
                                        crop=[crop.x, crop.y, crop.width, crop.height])
                sx = sy = 0.0
                ahead = None
        points = [[x + sx, y + sy] for x, y in result.get("points") or []]
        return {"points": points, "query": query, "text": result.get("text", ""), "read": result.get("read"),
                "ms": (time.perf_counter() - t0) * 1000, "workerMs": result.get("ms"), "ahead": ahead is not None,
                "frameId": ahead.frame_id if ahead is not None else info.id}
