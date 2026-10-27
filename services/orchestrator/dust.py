"""Dust on the optics: spots that stay put in the eyepiece while the slide moves (roadmap W4-3).

Dust and smudges on the phone's lens or in the eyepiece sit still in the eyepiece disc while the
slide slides under them; the specimen moves. So the median of many eyepiece-aligned views taken at
different places on the slide is the flat field (the light, the vignetting, and whatever is on the
optics), and what is left of it after its smooth part (the light) is removed is the fixed residue:
dark spots there are dust. Each spot is then checked in every view (darker than the ring around it)
and kept only when most views show it.

This is a measurement, not a verdict (docs/design/2026-10-06-facts-not-verdicts.md): the models
are told where such spots are ("likely dust on the optics", `line`), and they decide; option DUST
"filter" (code drops the app's outlines that lie on a spot) is a non-default option that has to beat
being told.

Cheap on the frame path: `frame` keeps a reference to the camera's JPEG (nothing decoded) when the
slide has moved far enough from every view kept; the estimate runs in a thread every few seconds.
"""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np

from .coords import Crop, frame_box_to_gemini
from .fov import FieldOfView

SIZE = 320  # px: each view is the eyepiece disc mapped onto a square this big (about 2 camera px a px)
MIN_MOVE = 0.2  # of the disc's radius: a new view is kept only this far from every view kept (same epoch)
MIN_GAP_S = 0.25  # at most four views a second are kept
MAX_VIEWS = 45  # views in the median (the oldest go first)
MIN_VIEWS = 9  # fewer than this says nothing about what stays put
MAX_AGE_S = 600.0  # a view older than this goes (dust is wiped, the phone is moved)
RADIUS_CHANGE = 0.1  # the disc changed size by more than this: the old views no longer line up
DISC_MOVE = 0.08  # of the radius: the disc's centre moved this far (the phone shifted): start afresh
RIM = 0.88  # of the radius: the vignetted rim is left out
MIN_DEPTH = 0.05  # a spot is at least this much darker than the flat field around it
NOISE_K = 5.0  # ... and at least this many robust standard deviations of the residue
MIN_AREA = 3  # px of the SIZE square
MAX_SPOT = 0.2  # of the radius: anything bigger is the light, not a speck
PERSIST = 0.75  # a spot must be darker than its ring in at least this share of the views (random texture: about half)
MAX_SPOTS = 8
REFRESH_S = 3.0


@dataclass(frozen=True)
class Spot:
    u: float  # centre, in disc radii from the disc's centre (x right)
    v: float  # (y down)
    r: float  # radius, in disc radii
    depth: float  # how much darker than the flat field around it (0.12: 12%)
    seen: int  # views in which it was darker than the ring around it
    views: int  # views looked at

    def frame_box(self, fov) -> tuple[float, float, float, float]:
        """Its box in frame pixels for the eyepiece disc `fov`."""
        cx, cy, R = fov.cx + self.u * fov.radius, fov.cy + self.v * fov.radius, max(self.r * fov.radius, 1.0)
        return cx - R, cy - R, cx + R, cy + R

    def wire(self) -> dict:
        return {"u": round(self.u, 3), "v": round(self.v, 3), "r": round(self.r, 3), "depth": round(self.depth, 3),
                "seen": self.seen, "views": self.views}


def aligned(gray: np.ndarray, fov, size: int = SIZE) -> np.ndarray:
    """The eyepiece disc of `gray` mapped onto a size x size square (float32; outside the frame 0)."""
    r = fov.radius
    x0, y0 = fov.cx - r, fov.cy - r
    scale = size / (2 * r)
    # Downscale first (area), then place: no aliasing of fine specimen detail into the median.
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA).astype(np.float32)
    m = np.float32([[1, 0, -x0 * scale], [0, 1, -y0 * scale]])
    return cv2.warpAffine(small, m, (size, size), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def _radius_map(size: int) -> np.ndarray:
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    c = (size - 1) / 2
    return np.hypot(xx - c, yy - c) / (size / 2)


def _usable(flat: np.ndarray) -> np.ndarray:
    """Where the flat field can show a spot: inside RIM of the disc and away from any edge of the
    camera frame (a disc wider than the frame is cut off; outside the frame the views are 0)."""
    size = flat.shape[0]
    inside = _radius_map(size) < RIM
    lit = flat > 0.25 * float(np.median(flat[inside])) if inside.any() else inside
    margin = max(3, size // 20) | 1
    lit = cv2.erode(lit.astype(np.uint8), np.ones((margin, margin), np.uint8)).astype(bool)
    return inside & lit


def flat_field(views: list[np.ndarray]) -> np.ndarray:
    """The median of eyepiece-aligned views at different places on the slide."""
    return np.median(np.stack(views), axis=0).astype(np.float32)


def residue(flat: np.ndarray) -> np.ndarray:
    """The flat field over its smooth part (the light and the vignetting): 1 where nothing is on
    the optics, below 1 on a dark spot. Outside RIM it is 1."""
    inside = _usable(flat)
    filled = flat.copy()
    rmap = _radius_map(flat.shape[0])
    ring = flat[(rmap > RIM - 0.08) & inside]
    filled[~inside] = float(np.median(ring)) if ring.size else float(flat[inside].mean() if inside.any() else 1.0)
    k = max(3, (flat.shape[0] // 10) | 1)
    smooth = cv2.medianBlur(np.clip(filled, 0, 255).astype(np.uint8), k).astype(np.float32)
    smooth = cv2.GaussianBlur(smooth, (0, 0), k / 6)
    out = filled / np.maximum(smooth, 1.0)
    out[~inside] = 1.0
    return out


def find_spots(views: list[np.ndarray], flat: np.ndarray | None = None) -> list[Spot]:
    """Dark spots of the flat field's fixed residue that most of the views show too."""
    if len(views) < MIN_VIEWS:
        return []
    flat = flat_field(views) if flat is None else flat
    res = residue(flat)
    size = flat.shape[0]
    inside = _usable(flat)
    values = res[inside]
    mad = float(np.median(np.abs(values - np.median(values)))) * 1.4826
    depth = max(MIN_DEPTH, NOISE_K * mad)
    mask = ((res < 1.0 - depth) & inside).astype(np.uint8)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    half = size / 2
    spots = []
    for i in range(1, count):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < MIN_AREA:
            continue
        w, h = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
        radius_px = max(w, h) / 2
        if radius_px / half > MAX_SPOT:
            continue
        cx, cy = centroids[i]
        blob = labels == i
        darkest = float(1.0 - res[blob].min())
        seen = sum(1 for v in views if _darker_there(v, cx, cy, max(radius_px, 1.5), depth / 2))
        if seen < PERSIST * len(views):
            continue
        spots.append(Spot(float((cx - (size - 1) / 2) / half), float((cy - (size - 1) / 2) / half),
                          float(max(radius_px, 1.0) / half), darkest, seen, len(views)))
    spots.sort(key=lambda s: -s.depth)
    return spots[:MAX_SPOTS]


def _darker_there(view: np.ndarray, cx: float, cy: float, radius: float, depth: float) -> bool:
    """In one view, the spot's place is darker than the ring around it by at least `depth`."""
    size = view.shape[0]
    outer = radius * 3 + 2
    x0, y0 = max(0, int(cx - outer)), max(0, int(cy - outer))
    x1, y1 = min(size, int(cx + outer) + 1), min(size, int(cy + outer) + 1)
    patch = view[y0:y1, x0:x1]
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    d = np.hypot(xx - cx, yy - cy)
    core, ring = patch[d <= radius], patch[(d >= radius * 1.5 + 1) & (d <= outer)]
    if core.size == 0 or ring.size == 0:
        return False
    around = float(np.median(ring))
    return around > 1.0 and float(core.mean()) < (1.0 - depth) * around


def spots_on(spots: list[Spot], fov, box: tuple[float, float, float, float]) -> list[Spot]:
    """The spots `box` (frame pixels) is about: the spot's centre is inside it and the box is not
    much bigger than the spot (a cell that happens to have a speck over it is not the speck)."""
    out = []
    for spot in spots:
        sx0, sy0, sx1, sy1 = spot.frame_box(fov)
        cx, cy = (sx0 + sx1) / 2, (sy0 + sy1) / 2
        side = max(box[2] - box[0], box[3] - box[1])
        if box[0] <= cx <= box[2] and box[1] <= cy <= box[3] and side <= 3.0 * (sx1 - sx0) + 8:
            out.append(spot)
    return out


def line(spots: list[Spot], fov, crop: Crop, outlined: tuple[float, float, float, float] | None = None) -> str:
    """The fact as the models are told it, boxes on image 1 (the eyepiece crop `crop`); with
    `outlined` (naming a click), whether the outlined thing is where one of them is. "" without spots."""
    if not spots or fov is None:
        return ""
    views = max(s.views for s in spots)
    boxes = ", ".join(str(frame_box_to_gemini(s.frame_box(fov), crop)) for s in spots[:6])
    text = (f"Measured by the app: in {views} views at different places on the slide, {len(spots[:6])} dark "
            f"spot{'s' if len(spots[:6]) != 1 else ''} stayed at the same place in the eyepiece while the slide moved "
            f"under {'them' if len(spots[:6]) != 1 else 'it'}, so likely dust or smudges on the optics, not on the slide: "
            f"{boxes} ([ymin, xmin, ymax, xmax], 0-1000 of image 1).")
    if outlined is not None and spots_on(spots, fov, outlined):
        text += " The outlined thing is at the place of one of these spots."
    return text


class DustMap:
    """The running flat field and its dust spots (option DUST; App.build wires `frame` to the hub).

    Every view is aligned with one reference disc, the median of the kept views' discs: the disc
    fitter jitters by a few per cent from frame to frame (2026-06-14 footage: radius 386 or 403 px
    with the disc cut off by the frame), and dust aligned by each frame's own fit would smear. A
    disc that really moved (the phone shifted or zoomed) starts the views afresh."""

    def __init__(self, get_frame: Callable[[int], object], clock: Callable[[], float] = time.time,
                 decode: Callable[[bytes], np.ndarray] | None = None) -> None:
        self.get_frame = get_frame  # FrameSource.get: the frame's JPEG by id
        self.clock = clock
        self.decode = decode or (lambda jpeg: cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE))
        self.kept: list[dict] = []  # {"id", "jpeg", "fov", "dx", "dy", "epoch", "at"}
        self.grays: dict[int, tuple[np.ndarray, float]] = {}  # decoded views and their scale, owned by estimate()
        self._aligned: dict[int, np.ndarray] = {}  # views aligned with `_aligned_to`, owned by estimate()
        self._aligned_to: tuple[float, float, float] | None = None
        self.spots: list[Spot] = []
        self.ref = None  # the disc the spots are placed in (the median of the kept views' discs)
        self.views = 0  # views the spots were measured on
        self.estimated_at = 0.0
        self._changed = False
        self._generation = 0  # bumped by clear(): an estimate running meanwhile is dropped
        self._last_kept = float("-inf")
        self._task: asyncio.Task | None = None

    def reference(self):
        """The median disc of the kept views, or None."""
        fovs = [k["fov"] for k in self.kept]
        if not fovs:
            return None
        return FieldOfView(float(np.median([f.cx for f in fovs])), float(np.median([f.cy for f in fovs])),
                           float(np.median([f.radius for f in fovs])), 1.0)

    # --- on the frame path (cheap) -----------------------------------------------------
    def frame(self, info) -> None:
        """Every published frame (Hub.on_frame): keeps a reference to the frame when the slide has
        moved far enough from every view kept. Nothing is decoded here."""
        fov = info.fov
        if fov is None or fov.radius < 20:
            return
        now = info.wall_at
        if now - self._last_kept < MIN_GAP_S:
            return
        ref = self.ref or self.reference()
        if ref is not None and (abs(fov.radius - ref.radius) > RADIUS_CHANGE * ref.radius
                                or math.hypot(fov.cx - ref.cx, fov.cy - ref.cy) > DISC_MOVE * ref.radius):
            self.clear()  # the disc moved or changed size (the phone shifted or zoomed): start afresh
        self.kept = [k for k in self.kept if now - k["at"] <= MAX_AGE_S]
        far = MIN_MOVE * fov.radius
        for k in self.kept:
            if k["epoch"] == info.epoch and math.hypot(k["dx"] - info.dx, k["dy"] - info.dy) < far:
                return
        frame = self.get_frame(info.id)
        if frame is None:
            return
        self.kept.append({"id": info.id, "jpeg": frame.jpeg, "fov": fov, "dx": info.dx, "dy": info.dy,
                          "epoch": info.epoch, "at": now})
        if len(self.kept) > MAX_VIEWS:
            self.kept.pop(0)  # its decoded view goes at the next estimate (which owns `grays`)
        self._last_kept = now
        self._changed = True

    def clear(self) -> None:
        self.kept, self.grays, self.spots, self.views, self.ref = [], {}, [], 0, None
        self._aligned, self._aligned_to = {}, None
        self._changed = False
        self._generation += 1

    # --- off the frame path ----------------------------------------------------------
    def estimate(self) -> list[Spot]:
        """Decode the views not yet decoded, align them all with the reference disc, and measure the
        flat field and its spots (in a thread; 30-200 ms for 45 views). Works on its own copies, so the
        frame path never waits for it; a `clear` meanwhile discards its result."""
        generation, kept = self._generation, list(self.kept)
        if not kept:
            return []
        ids = {k["id"] for k in kept}
        grays = {i: g for i, g in self.grays.items() if i in ids}
        for k in kept:
            if k["id"] not in grays:
                gray = self.decode(k["jpeg"])
                if gray is not None:
                    # Kept at half size when the disc is still at least SIZE across there (memory).
                    half = k["fov"].radius >= SIZE
                    grays[k["id"]] = (cv2.resize(gray, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA) if half
                                      else gray, 0.5 if half else 1.0)
        fovs = [k["fov"] for k in kept]
        key = (float(np.median([f.cx for f in fovs])), float(np.median([f.cy for f in fovs])),
               float(np.median([f.radius for f in fovs])))
        cache, to = self._aligned, self._aligned_to
        if to is None or max(abs(a - b) for a, b in zip(key, to)) > 0.5:
            cache, to = {}, key  # the reference moved: align every view again
        ref = FieldOfView(*to, 1.0)  # within half a pixel: the views already aligned are kept
        cache = {i: v for i, v in cache.items() if i in ids}
        for k in kept:
            if k["id"] in grays and k["id"] not in cache:
                gray, scale = grays[k["id"]]
                cache[k["id"]] = aligned(gray, FieldOfView(ref.cx * scale, ref.cy * scale, ref.radius * scale, 1.0))
        views = [cache[k["id"]] for k in kept if k["id"] in cache]
        spots = find_spots(views) if len(views) >= MIN_VIEWS else []
        if generation != self._generation:
            return []  # cleared while measuring: these views no longer count
        self.grays, self._aligned, self._aligned_to = grays, cache, to
        self.spots, self.ref, self.views, self.estimated_at = spots, ref, len(views), self.clock()
        return spots

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(REFRESH_S)
            if not self._changed or len(self.kept) < MIN_VIEWS:
                continue
            self._changed = False
            try:
                await loop.run_in_executor(None, self.estimate)
            except Exception:
                pass

    def wire(self) -> dict:
        return {"spots": [s.wire() for s in self.spots], "views": self.views, "kept": len(self.kept),
                "disc": None if self.ref is None else [round(self.ref.cx, 1), round(self.ref.cy, 1), round(self.ref.radius, 1)]}
