"""Pointing "masks": segmenter-first Set-of-Mark (options.POINTING).

After the slide settles, the outline model masks every distinct thing in the eyepiece disc on the
heavy GPU worker (gpu_worker/automask.py: a point grid plus the detected spots), off the click
path. The masks are kept with the frame they were made on, so they follow the slide like outlines
do (p + offset_now - offset_then) and are dropped when continuity is lost or the slide has moved
far. When the user asks, the vision model is shown the view and the same view with every mask
outlined and numbered (marks.render_masks), and names numbers; a chosen number becomes an outline
straight from its mask, with no second segmentation (Agent.point). A thing no number fits is
still boxed by the model and outlined from its box, as with "box". With no masks ready, the look
is a plain "box" look (logged).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .coords import Crop
from . import execution

SETTLE_S = 0.6          # steady this long before masking (as Sight)
GAP_S = 1.0             # at most one masking per this many seconds
MOVED_FRACTION = 0.35   # of the eyepiece radius: masks from further back no longer cover the view
REFRESH_S = 60.0        # mask again after this long even without a change
TIMEOUT_S = 30.0


def shifted(region: dict, dx: float, dy: float) -> dict:
    """A region moved by (dx, dy) frame pixels."""
    return {**region, "polygons": [[[x + dx, y + dy] for x, y in poly] for poly in region["polygons"]],
            "label": [region["label"][0] + dx, region["label"][1] + dy]}


def region_box(region: dict) -> tuple[float, float, float, float]:
    pts = np.array([p for poly in region["polygons"] for p in poly], float)
    return float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())


def agrees(mask_box, model_box) -> bool:
    """The numbered mask the model chose is the thing it also boxed: they overlap well, or each
    one's centre lies inside the other. (The model is asked for both; a number that is only near
    its box is not taken: grounding bench, the model named a nearby outline for unmasked specks.)"""
    ix = max(0.0, min(mask_box[2], model_box[2]) - max(mask_box[0], model_box[0]))
    iy = max(0.0, min(mask_box[3], model_box[3]) - max(mask_box[1], model_box[1]))
    area = lambda b: max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area(mask_box) + area(model_box) - ix * iy
    if union > 0 and ix * iy / union >= 0.3:
        return True
    centre = lambda b: ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    within = lambda p, b: b[0] <= p[0] <= b[2] and b[1] <= p[1] <= b[3]
    return within(centre(mask_box), model_box) and within(centre(model_box), mask_box)


@dataclass
class MaskSet:
    """Every mask made on one settled frame, in that frame's pixels."""
    frame_id: int
    epoch: int
    dx: float
    dy: float
    radius: float
    regions: list[dict]
    ms: float = 0.0
    at: float = field(default_factory=time.monotonic)

    def current(self, info) -> bool:
        """Still covers what is in view: same motion epoch, and the slide has not moved far."""
        if info is None or info.epoch != self.epoch:
            return False
        return float(np.hypot(info.dx - self.dx, info.dy - self.dy)) < MOVED_FRACTION * max(self.radius, 1.0)

    def now(self, info) -> list[dict] | None:
        """The regions where they are in frame `info` (those still inside the disc), or None when
        the set no longer covers the view."""
        if not self.current(info):
            return None
        sx, sy = info.dx - self.dx, info.dy - self.dy
        fov = getattr(info, "fov", None)
        out = [shifted(r, sx, sy) for r in self.regions]
        if fov is not None:
            out = [r for r in out if fov.contains(r["label"][0], r["label"][1], margin=4)]
        return out


@dataclass
class RegionMarks:
    """What a "masks" look showed the model: number -> region, in the pixels of frame `frame_id`
    (whose motion offset and epoch are kept, so a number is outlined on a later frame too)."""
    jpeg: bytes
    crop: Crop
    regions: dict[int, dict]
    frame_id: int
    epoch: int
    dx: float
    dy: float
    made_on: int = -1  # the frame the masks were made on
    made_ms: float = 0.0  # how long making them took (the background run's time)

    def polygons_in(self, number: int, info) -> list | None:
        """The region's polygons in frame `info`, or None if continuity was lost since."""
        region = self.regions.get(number)
        if region is None or info is None or info.epoch != self.epoch:
            return None
        return shifted(region, info.dx - self.dx, info.dy - self.dy)["polygons"]

    def box(self, number: int) -> tuple[float, float, float, float] | None:
        region = self.regions.get(number)
        return region_box(region) if region is not None else None


class Masker:
    """Keeps a MaskSet for the settled view while pointing is "masks" and someone is there."""

    def __init__(self, hub, log: Callable[[dict], None] | None = None, wanted: Callable[[], bool] | None = None,
                 also: Callable[[], bool] | None = None) -> None:
        self.hub = hub
        self.log = log or (lambda record: None)
        self.wanted = wanted or (lambda: bool(hub.clients))
        self.also = also or (lambda: False)  # masks wanted besides the setting (a blind comparison's "masks" arm)
        self.current: MaskSet | None = None
        self.recheck = False  # something new appeared in a still view: mask again
        self._task: asyncio.Task | None = None
        self._unsteady_at = 0.0
        self._last = 0.0
        self._busy = False

    def enabled(self) -> bool:
        return getattr(self.hub.settings, "pointing", "") == "masks" or bool(self.also())

    def for_view(self, info) -> list[dict] | None:
        """The regions to number for a look at frame `info`, or None (none ready, or stale)."""
        return self.current.now(info) if self.current is not None else None

    def still_changed(self, kind: str, info) -> None:
        """Hub: something new appeared while the slide stayed still: its mask is needed too."""
        if kind == "content":
            self.recheck = True

    def due(self, info, now: float) -> bool:
        if info is None or not info.steady:
            self._unsteady_at = now
            return False
        if now - self._unsteady_at < SETTLE_S or now - self._last < GAP_S:
            return False
        if self.current is None or not self.current.current(info) or self.recheck:
            return True
        return now - self._last > REFRESH_S

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(0.25)
            if not self.enabled() or self._busy or not self.wanted() or not self.hub.source.is_fresh():
                continue
            if self.due(self.hub.latest_info, time.monotonic()):
                self._busy = True
                try:
                    await self.run()
                except Exception as exc:
                    self.log({"kind": "automask", "error": f"{type(exc).__name__}: {exc}"[:200]})
                finally:
                    self._busy = False

    @execution.traced("automask", request="masks")
    async def run(self) -> MaskSet | None:
        """Mask the settled view once (on the heavy GPU worker, so clicks never wait for it)."""
        hub = self.hub
        info = hub.best_frame(SETTLE_S) if hasattr(hub, "best_frame") else hub.latest_info
        frame = hub.source.get(info.id) if info is not None else None
        if frame is None:
            return None
        self._last = time.monotonic()
        self.recheck = False
        gpu = hub.gpu_heavy if hub.gpu_heavy is not None else hub.gpu
        if gpu is None:
            return None
        if hasattr(gpu, "ensure_started"):
            gpu = await gpu.ensure_started()
        fov = info.fov
        spots = [[c.x, c.y] for c in hub.candidates_in(info.id)]
        result = await gpu.call("automask", timeout=TIMEOUT_S, frame_id=info.id, jpeg=frame.jpeg, points=spots,
                                disc=[fov.cx, fov.cy, fov.radius] if fov is not None else None)
        radius = fov.radius if fov is not None else min(info.width, info.height) / 2
        self.current = MaskSet(info.id, info.epoch, info.dx, info.dy, float(radius), list(result.get("regions") or []),
                               ms=float(result.get("ms") or 0.0))
        self.log({"kind": "automask", "frameId": info.id, "epoch": info.epoch, "regions": len(self.current.regions),
                  "prompts": result.get("prompts"), "candidates": result.get("candidates"), "ms": result.get("ms")})
        return self.current
