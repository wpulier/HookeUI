"""The slide map (roadmap W5-4): the steady views seen so far, pasted where they are on the slide,
and where an outline is from the view now ("where was the stoma?").

- The map: every frame carries the slide's cumulative offset (motion.py): content at p in frame F
  is at p - offset[F] on the slide. A steady frame is decoded at a quarter of its size (the JPEG
  decoder scales for free) and its eyepiece disc is pasted at -offset * SCALE. Nothing is blended
  or invented: every map pixel is a camera pixel from a steady view, the newest on top. Frames
  flagged blurry are not pasted (better input, as best_frame prefers sharp frames).
- Per motion epoch: offsets of two epochs cannot be compared, so each epoch has its own map. A
  view that rejoins an earlier epoch (motion.py's bank of places seen before) goes on with that
  epoch's map. At most EPOCHS_KEPT maps are kept, each at most MAX_SIDE map px a side (the side
  farthest from the view is cut off when it would grow past that); the oldest goes first.
- Cheap on the frame path: the listener only compares numbers and hands a frame's JPEG (already
  in memory) to its own thread, and only when the slide has moved MOVE_PX since the last paste or
  REFRESH_S have passed; frames that arrive while a paste is running are skipped.
- Where a thing is (`place`): its outline's centre, carried by the offsets from its anchor frame
  to now, against the eyepiece's centre, in fields of view (the eyepiece's diameter). Only in the
  same epoch; otherwise it says it cannot be told. Arithmetic, not a judgment: the model says it.

The studio's panel (apps/studio/src/slidemap.ts) shows the current map as a JPEG
(/api/slidemap.jpg) with the view and the outlines drawn over it from the frame headers and the
scene it already has; the panel's open state is here so the brain can open it ("show me the map").
"""
from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np

from . import spaces
from .contract import IN_VIEW_RADIUS

SCALE = 0.25  # map px per camera px (cv2.IMREAD_REDUCED_COLOR_4 decodes at exactly this)
MAX_SIDE = 1536  # map px a side of one epoch's map (6144 camera px: about 6 fields of a 1000 px disc)
EPOCHS_KEPT = 4  # maps kept (at most 4 x 1536^2 x 4 bytes = 38 MB)
MOVE_PX = 48.0  # camera px the slide moves before another steady frame is pasted
REFRESH_S = 3.0  # a still view is pasted again this often (light or focus changed, something moved in)
DISC_SHARE = 0.94  # of the eyepiece radius pasted: its rim is dark and soft
GROW_PX = 256  # map px added beyond what a paste needs, so the map is not reallocated every paste
SEND_EVERY_S = 1.0  # while the panel is open, a newer map is announced at most this often
JPEG_QUALITY = 82


@dataclass
class Place:
    """Where a thing is from the view now (camera px and fields of view)."""

    dx: float  # its centre minus the eyepiece's centre, camera px (+x right, +y down in the picture)
    dy: float
    fields: float  # distance in fields of view (the eyepiece's diameter)
    in_view: bool


class Canvas:
    """One epoch's map: pixels and which of them were pasted, with the map px of [0, 0]."""

    def __init__(self) -> None:
        self.ox = self.oy = 0  # map coords (slide px x SCALE) of pixel [0, 0]
        self.img = np.zeros((0, 0, 3), np.uint8)
        self.mask = np.zeros((0, 0), np.uint8)
        self.used_at = 0.0
        self.last: tuple[float, float, float] | None = None  # (dx, dy, time) of the last paste

    @property
    def size(self) -> tuple[int, int]:
        return self.img.shape[1], self.img.shape[0]

    def _span(self, lo: int, hi: int, have_lo: int, have_hi: int) -> tuple[int, int]:
        """The range one axis keeps to hold [lo, hi): grown with a margin, at most MAX_SIDE,
        cutting off the side away from the new paste."""
        if have_hi > have_lo and have_lo <= lo and hi <= have_hi:
            return have_lo, have_hi
        a = min(lo - GROW_PX, have_lo) if have_hi > have_lo else lo - GROW_PX
        b = max(hi + GROW_PX, have_hi) if have_hi > have_lo else hi + GROW_PX
        if b - a > MAX_SIDE:
            if (lo + hi) / 2 >= (have_lo + have_hi) / 2:  # pasting toward the high side: keep it
                b = max(hi, min(b, hi + GROW_PX))
                a = b - MAX_SIDE
            else:
                a = min(lo, max(a, lo - GROW_PX))
                b = a + MAX_SIDE
        return a, b

    def paste(self, small: np.ndarray, mask: np.ndarray, x: int, y: int) -> None:
        """Paste `small` where `mask` is set, its top-left at map (x, y)."""
        h, w = small.shape[:2]
        width, height = self.size
        x0, x1 = self._span(x, x + w, self.ox, self.ox + width)
        y0, y1 = self._span(y, y + h, self.oy, self.oy + height)
        if (x0, y0, x1 - x0, y1 - y0) != (self.ox, self.oy, width, height):
            img = np.zeros((y1 - y0, x1 - x0, 3), np.uint8)
            used = np.zeros((y1 - y0, x1 - x0), np.uint8)
            # What the old map and the new range share is kept.
            ax, ay = max(x0, self.ox), max(y0, self.oy)
            bx, by = min(x1, self.ox + width), min(y1, self.oy + height)
            if bx > ax and by > ay:
                img[ay - y0:by - y0, ax - x0:bx - x0] = self.img[ay - self.oy:by - self.oy, ax - self.ox:bx - self.ox]
                used[ay - y0:by - y0, ax - x0:bx - x0] = self.mask[ay - self.oy:by - self.oy, ax - self.ox:bx - self.ox]
            self.img, self.mask, self.ox, self.oy = img, used, x0, y0
        # The part of the paste inside the map (a paste can be partly cut off at MAX_SIDE).
        ax, ay = max(x, self.ox), max(y, self.oy)
        bx, by = min(x + w, self.ox + self.size[0]), min(y + h, self.oy + self.size[1])
        if bx <= ax or by <= ay:
            return
        src = small[ay - y:by - y, ax - x:bx - x]
        keep = mask[ay - y:by - y, ax - x:bx - x] > 0
        dst = self.img[ay - self.oy:by - self.oy, ax - self.ox:bx - self.ox]
        dst[keep] = src[keep]
        self.mask[ay - self.oy:by - self.oy, ax - self.ox:bx - self.ox][keep] = 255

    def painted(self) -> tuple[int, int, int, int] | None:
        """The pasted part, as (x0, y0, x1, y1) array px, or None when nothing was pasted."""
        if not self.mask.size:
            return None
        rows, cols = np.flatnonzero(self.mask.any(axis=1)), np.flatnonzero(self.mask.any(axis=0))
        if not len(rows):
            return None
        return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


class SlideMap:
    def __init__(self, get_frame: Callable[[int], object], send: Callable[[dict], None] | None = None,
                 clock: Callable[[], float] = time.monotonic, background: bool = True) -> None:
        self.get_frame = get_frame  # FrameSource.get: the frame's JPEG by id
        self.send = send or (lambda payload: None)
        self.clock = clock
        self.maps: OrderedDict[int, Canvas] = OrderedDict()
        self.version = 0  # every paste
        self.open = False  # the studio's panel (the brain opens it too)
        self.epoch: int | None = None  # the newest frame's epoch
        self.pasted = 0
        self.paste_ms: list[float] = []  # the last pastes' cost, off the frame path (measured, reported)
        self._lock = threading.Lock()
        self._busy = False
        self._sent = (-1, None, 0.0)  # (version, epoch, when) last announced
        self._pool = ThreadPoolExecutor(1, thread_name_prefix="slidemap") if background else None

    # --- fed by the hub ---------------------------------------------------------------------
    def frame(self, info) -> None:
        """Every published frame (Hub.on_frame): a few comparisons; a paste goes to its thread."""
        self.epoch = info.epoch
        if self.open:
            self._announce()
        fov = getattr(info, "fov", None)
        if not info.steady or fov is None or self._busy or "blurry" in (getattr(info, "quality", ()) or ()):
            return
        now = self.clock()
        canvas = self.maps.get(info.epoch)
        last = canvas.last if canvas is not None else None
        if last is not None and math.hypot(info.dx - last[0], info.dy - last[1]) < MOVE_PX and now - last[2] < REFRESH_S:
            return
        frame = self.get_frame(info.id)
        if frame is None:
            return
        job = (frame.jpeg, info.epoch, info.dx, info.dy, (fov.cx, fov.cy, fov.radius), now)
        if self._pool is None:
            self.paste(*job)
            return
        self._busy = True
        self._pool.submit(self._paste_quietly, job)

    def _paste_quietly(self, job) -> None:
        try:
            self.paste(*job)
        except Exception:
            pass
        finally:
            self._busy = False

    def paste(self, jpeg: bytes, epoch: int, dx: float, dy: float, disc: tuple[float, float, float], now: float) -> bool:
        """Paste one frame's eyepiece disc into its epoch's map. True when something was pasted."""
        t0 = time.perf_counter()
        small = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_REDUCED_COLOR_4)
        if small is None:
            return False
        cx, cy, r = (v * SCALE for v in disc)
        r *= DISC_SHARE
        h, w = small.shape[:2]
        x0, y0 = max(0, int(cx - r)), max(0, int(cy - r))
        x1, y1 = min(w, int(math.ceil(cx + r)) + 1), min(h, int(math.ceil(cy + r)) + 1)
        if x1 - x0 < 4 or y1 - y0 < 4:
            return False
        crop = small[y0:y1, x0:x1]
        mask = np.zeros(crop.shape[:2], np.uint8)
        cv2.circle(mask, (int(round(cx - x0)), int(round(cy - y0))), max(1, int(r)), 255, -1)
        # Content at frame px p is at slide px p - offset: map px p * SCALE - offset * SCALE.
        mx, my = int(round(x0 - dx * SCALE)), int(round(y0 - dy * SCALE))
        with self._lock:
            canvas = self.maps.get(epoch)
            if canvas is None:
                canvas = self.maps[epoch] = Canvas()
                while len(self.maps) > EPOCHS_KEPT:
                    self.maps.popitem(last=False)
            self.maps.move_to_end(epoch)
            canvas.paste(crop, mask, mx, my)
            canvas.used_at, canvas.last = now, (dx, dy, now)
            self.version += 1
            self.pasted += 1
        self.paste_ms = (self.paste_ms + [(time.perf_counter() - t0) * 1000])[-200:]
        return True

    def clear(self) -> None:
        """A new day: the maps of the old one go."""
        with self._lock:
            self.maps.clear()
            self.version += 1
        self._announce(force=True)

    # --- the panel --------------------------------------------------------------------------
    def set_open(self, open_: bool) -> None:
        self.open = bool(open_)
        self._announce(force=True)

    def wire(self) -> dict:
        """The panel's state and the current epoch's map: where it sits on the slide (map px of
        its top-left: slide px x SCALE) and how big it is, so the studio can draw the view and
        the outlines over it."""
        epoch = self.epoch if self.epoch is not None else (next(reversed(self.maps)) if self.maps else None)
        with self._lock:
            canvas = self.maps.get(epoch) if epoch is not None else None
            box = canvas.painted() if canvas is not None else None
            out = {"type": "slidemap", "space": spaces.of_wire("slidemap"), "open": self.open, "epoch": epoch, "scale": SCALE, "maps": len(self.maps),
                   "version": self.version, "url": None, "origin": None, "size": None}
            if box is not None:
                x, y, w, h = canvas.ox + box[0], canvas.oy + box[1], box[2] - box[0], box[3] - box[1]
                # The picture is cut to exactly this rectangle, so it always fits what the studio
                # was told, even when a paste came in between.
                out.update(url=f"/api/slidemap.jpg?epoch={epoch}&x={x}&y={y}&w={w}&h={h}&v={self.version}",
                           origin=[x, y], size=[w, h])
        return out

    def _announce(self, force: bool = False) -> None:
        version, epoch, when = self._sent
        now = self.clock()
        if not force and (self.version, self.epoch) == (version, epoch):
            return
        if not force and self.epoch == epoch and now - when < SEND_EVERY_S:
            return
        self._sent = (self.version, self.epoch, now)
        self.send(self.wire())

    def jpeg(self, epoch: int | None = None, rect: tuple[int, int, int, int] | None = None) -> bytes | None:
        """An epoch's map (the current one by default) as a JPEG: its pasted part, or exactly the
        map px rectangle (x, y, w, h) given (black where nothing is), as wire() names it."""
        epoch = self.epoch if epoch is None else epoch
        with self._lock:
            canvas = self.maps.get(epoch) if epoch is not None else None
            box = canvas.painted() if canvas is not None else None
            if box is None:
                return None
            if rect is None:
                img = canvas.img[box[1]:box[3], box[0]:box[2]].copy()
            else:
                x, y, w, h = (int(v) for v in rect)
                w, h = max(1, min(w, MAX_SIDE)), max(1, min(h, MAX_SIDE))
                img = np.zeros((h, w, 3), np.uint8)
                cw, ch = canvas.size
                ax, ay = max(x, canvas.ox), max(y, canvas.oy)
                bx, by = min(x + w, canvas.ox + cw), min(y + h, canvas.oy + ch)
                if bx > ax and by > ay:
                    img[ay - y:by - y, ax - x:bx - x] = canvas.img[ay - canvas.oy:by - canvas.oy, ax - canvas.ox:bx - canvas.ox]
        ok, out = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        return out.tobytes() if ok else None


# --- where a thing is -------------------------------------------------------------------------
def place(obj, info) -> Place | None:
    """Where an outline's centre is from the eyepiece's centre now, or None when it cannot be told
    (unregistered current motion, another motion epoch, or no eyepiece seen)."""
    fov = getattr(info, "fov", None)
    if (info is None or getattr(info, "tracked", None) is False or fov is None
            or fov.radius <= 0 or obj.anchor_epoch != info.epoch):
        return None
    cx, cy = obj.center
    dx = cx + info.dx - obj.anchor_offset[0] - fov.cx
    dy = cy + info.dy - obj.anchor_offset[1] - fov.cy
    distance = math.hypot(dx, dy)
    return Place(dx, dy, distance / (2 * fov.radius), distance <= fov.radius * IN_VIEW_RADIUS)


DIRECTIONS = ["to the right of", "up and to the right of", "above", "up and to the left of", "to the left of",
              "down and to the left of", "below", "down and to the right of"]
PARTS = ["right", "upper right", "top", "upper left", "left", "lower left", "bottom", "lower right"]


def _sector(dx: float, dy: float) -> int:
    angle = math.degrees(math.atan2(-dy, dx)) % 360  # 0 right, 90 up (in the picture +y is down)
    return int(((angle + 22.5) % 360) // 45)


def direction(dx: float, dy: float) -> str:
    """"up and to the left of", "above", ... in the picture (followed by what it is from)."""
    return DIRECTIONS[_sector(dx, dy)]


def place_words(p: Place, um_per_px: float | None = None) -> str:
    """How far and which way, as a person would say it: "about 1.2 fields up and to the left of
    the view (about 850 µm)", or where in the view it is."""
    from .scale import round_um

    away = math.hypot(p.dx, p.dy)
    if p.in_view:
        if p.fields < 0.1:
            return "in view now, near the centre"
        return f"in view now, toward the {PARTS[_sector(p.dx, p.dy)]} of the view"
    far = f"{p.fields:.1f}" if p.fields < 10 else f"{p.fields:.0f}"
    unit = "field" if far == "1.0" else "fields"
    size = f" (about {round_um(away * um_per_px)} µm)" if um_per_px else ""
    return f"about {far} {unit} {direction(p.dx, p.dy)} the view{size}"
