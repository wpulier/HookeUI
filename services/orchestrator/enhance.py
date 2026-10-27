"""A clearer picture from real frames ("I can't get the focus any finer"): averaged, the sharpest,
or focus-stacked. Nothing is invented: every output pixel is a camera pixel, an average of camera
pixels at one place on the slide, or the camera pixel from the frame where that place was sharpest.
Each result says in words how it was made, and that is what the user is shown.

Frames are aligned to the newest one by the hub's slide offset (FrameInfo.dx, dy, full-resolution
pixels): a point p of a frame is at p + (offset_newest - offset_frame) in the newest frame, the
same rule the studio draws outlines with. Only frames of one motion epoch can be aligned this way.
The result covers only the part of the newest frame every frame saw (`origin` is its top-left in
the newest frame's coordinates).

Pure functions on decoded images, and which frames go into them (`live_plan`, `held_plan`), shared by the
`enhance` tool (brain/toolbox/seeing.py) and a researcher's hands (research/hands.py).
A frame's image may also be given as a loader (a function returning it): then it is decoded when
it is used and let go after, so only one frame is resident at a time whatever the number of frames
(invariant 3: a focus stack of 30 full-size frames held at once was ~500 MB at 1080p). The focus
stack then decodes each frame twice (choosing, then copying); the result is the same.
Design: docs/design/2026-10-06-capability-menu.md, "Seeing better".
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable

import cv2
import numpy as np

STACK_SIGMA = 3.0  # px: how far the Laplacian energy is smoothed before the sharpest frame is chosen
STACK_MEDIAN = 7  # px: the choice map is median-filtered so single pixels do not flicker between frames


@dataclass
class Enhanced:
    image: np.ndarray  # uint8, gray or BGR as given
    used: int  # how many real frames went into it
    words: str  # how it was made, for the user
    origin: tuple[int, int] = (0, 0)  # its top-left in the newest frame's coordinates


def _span(seconds: float) -> str:
    return f"{seconds:.1f} s" if seconds < 10 else f"{round(seconds)} s"


def overlap(shifts: list[tuple[float, float]], width: int, height: int) -> tuple[int, int, int, int]:
    """The part of the newest frame (x0, y0, x1, y1) that every shifted frame covers."""
    xs, ys = [s[0] for s in shifts], [s[1] for s in shifts]
    x0, y0 = max(0, math.ceil(max(xs))), max(0, math.ceil(max(ys)))
    x1, y1 = min(width, math.floor(width + min(xs))), min(height, math.floor(height + min(ys)))
    return x0, y0, max(x0, x1), max(y0, y1)


SAME_SCALE = 1e-4  # a disc within this share of the newest one's is taken as the same scale


def _image(item) -> np.ndarray:
    """A frame's image, given or loaded now."""
    return item() if callable(item) else item


def _aligned(frames: list[tuple[np.ndarray, float, float]], discs: list[tuple[float, float, float]] | None = None):
    """Each frame moved into the newest frame's coordinates, and the overlap of them all. With
    `discs` ((cx, cy, radius) per frame, radius 0 when not measured), a frame whose disc differs in
    size from the newest one's is first scaled to it, about the discs' centres."""
    if not frames:
        raise ValueError("no frames")
    newest = frames[-1]
    h, w = _image(newest[0]).shape[:2]
    shifts = [(newest[1] - dx, newest[2] - dy) for _, dx, dy in frames]
    scales = [(1.0, 0.0, 0.0)] * len(frames)
    if discs is not None and len(discs) == len(frames) and discs[-1][2] > 0:
        rx, ry, rr = discs[-1]
        scales = [(rr / r, cx, cy) if r > 0 and abs(rr / r - 1.0) > SAME_SCALE else (1.0, 0.0, 0.0) for cx, cy, r in discs]
    # A scaled frame covers less of the view by (s - 1) of its size at the far edge; the overlap
    # allows for it as a shift of that much.
    margins = [(abs(s - 1.0) * w, abs(s - 1.0) * h) for s, _, _ in scales]
    rect = overlap([(sx + mx, sy + my) for (sx, sy), (mx, my) in zip(shifts, margins)]
                   + [(sx - mx, sy - my) for (sx, sy), (mx, my) in zip(shifts, margins)], w, h)

    def moved(i: int) -> np.ndarray:
        image, (sx, sy) = _image(frames[i][0]), shifts[i]
        s, cx, cy = scales[i]
        if s == 1.0:
            if abs(sx) < 1e-3 and abs(sy) < 1e-3:
                return image
            m = np.float32([[1, 0, sx], [0, 1, sy]])
        else:  # this frame's disc centre onto the newest one's, at the ratio of their radii, then the slide's move
            rx, ry = discs[-1][0], discs[-1][1]
            m = np.float32([[s, 0, rx - s * cx + sx], [0, s, ry - s * cy + sy]])
        return cv2.warpAffine(image, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)

    return moved, rect


def average(frames: list[tuple[np.ndarray, float, float]], span_s: float | None = None) -> Enhanced:
    """The mean of the frames, aligned to the newest by the slide offset (one epoch, while steady:
    the caller picks them). Noise falls by about the square root of the number of frames."""
    moved, (x0, y0, x1, y1) = _aligned(frames)
    if x1 <= x0 or y1 <= y0:
        raise ValueError("the frames do not overlap")
    # One float accumulator, added to in place: each frame is cast as it is added (the same float32 sums as adding
    # its float copy), so no float copy of a frame and no second sum is ever held beside it (contract 1's peak).
    total = None
    for i in range(len(frames)):
        part = moved(i)[y0:y1, x0:x1]
        if total is None:
            total = part.astype(np.float32)
        else:
            np.add(total, part, out=total)
        del part
    total /= len(frames)
    total += 0.5
    np.clip(total, 0, 255, out=total)
    image = total.astype(np.uint8)
    del total
    over = f" over {_span(span_s)}" if span_s else ""
    return Enhanced(image, len(frames), f"averaged {len(frames)} real frames{over}, aligned by the slide's movement",
                    (x0, y0))


def sharpest(image: np.ndarray, considered: int, span_s: float) -> Enhanced:
    """One real frame, unaltered: the sharpest of `considered` (the hub chose it, Hub.best_frame)."""
    return Enhanced(image, 1, f"the sharpest of {considered} real frames from the last {_span(span_s)}, unaltered")


def energy(gray: np.ndarray, sigma: float = STACK_SIGMA) -> np.ndarray:
    """Local sharpness: the absolute Laplacian, Gaussian-smoothed."""
    lap = np.abs(cv2.Laplacian(gray.astype(np.float32), cv2.CV_32F, ksize=3))
    return cv2.GaussianBlur(lap, (0, 0), sigma)


def focus_stack(frames: list[tuple[np.ndarray, float, float]], ago_s: float | None = None,
                discs: list[tuple[float, float, float]] | None = None) -> Enhanced:
    """Each place taken from the frame where it is sharpest: aligned by the slide offset (and, with
    `discs`, the phone's refocusing scaled out), the frame with the highest local Laplacian energy
    is chosen per pixel, the choice map is median-filtered, and the pixels are copied from the
    chosen frames (no blending)."""
    if len(frames) > 255:
        raise ValueError("at most 255 frames")
    moved, (x0, y0, x1, y1) = _aligned(frames, discs)
    if x1 <= x0 or y1 <= y0:
        raise ValueError("the frames do not overlap")
    # A frame's colour is let go once its grey is made, and the picture is filled in place (no index arrays of the
    # places a frame won, as a boolean index makes: 16 bytes a place), so its peak no longer depends on how much of
    # the picture one frame wins (contract 1). The pixels are the same as copying with a boolean index.
    best, choice, out = None, None, None
    for i in range(len(frames)):  # choosing: one frame resident at a time
        part = moved(i)[y0:y1, x0:x1]
        gray = part if part.ndim == 2 else cv2.cvtColor(part, cv2.COLOR_BGR2GRAY)
        if out is None:
            out = np.empty_like(part)
        del part  # only its grey is scored
        e = energy(gray)
        del gray
        if best is None:
            best, choice = e, np.zeros(e.shape, np.uint8)
        else:
            better = e > best
            best = np.where(better, e, best)
            choice[better] = i
            del better
        del e
    del best
    choice = cv2.medianBlur(choice, STACK_MEDIAN)
    for i in range(len(frames)):  # copying each place from its frame, decoded again when it was given as a loader
        mask = choice == i
        if mask.any():
            frame = moved(i)[y0:y1, x0:x1]
            np.copyto(out, frame, where=mask if out.ndim == 2 else mask[..., None])
            del frame
        del mask
    used = len(np.unique(choice))
    when = f", {_span(ago_s)} ago" if ago_s is not None and ago_s >= 2 else ""
    return Enhanced(out, len(frames),
                    f"focus-stacked {len(frames)} real frames from the last turn of the focus knob{when} "
                    f"(each place taken from the frame where it was sharpest; {used} of them contributed)", (x0, y0))


# --- which frames, made into the picture (the `enhance` tool, and a researcher's hands: research/hands.py) ------
HOWS = ("average", "sharpest", "stack")
AVERAGE_WINDOW_S = 1.5  # the still frames of about the last second and a half are averaged...
AVERAGE_MIN = 4  # ...at least this many (fewer: the slide has not been still long enough)...
AVERAGE_MAX = 30  # ...at most this many, evenly spread
SHARPEST_WINDOW_S = 3.0
STACK_MAX = 30  # frames of the sweep stacked, evenly spread
HOLD_STILL = "the slide is moving: hold the slide still for a second, then ask again"
NO_SWEEP = ("there is no turn of the focus knob to stack yet: turn the fine focus slowly through the specimen "
            "first, then ask again")


# What making a picture holds at its peak (contract 1: reserved before anything is allocated, so the peak is inside
# the reservation; finding 90 of docs/design/2026-10-06-captures-operations-evidence.md). Every way decodes whole
# frames, one at a time, and a frame being decoded holds DECODE_PX bytes a pixel of the frame at once: OpenCV's own
# buffer and the array it returns (3 + 3, measured with malloc's own counts: tracemalloc sees only the array). Beside
# that, PER_PX bytes a pixel of the frames it works on (the eyepiece's box when cut to it), measured with tracemalloc
# over the real code at 1280x720 and 1920x1080, the user's tool and a researcher's hands alike:
# - "average": its float accumulator (12); a frame's cut and aligned copy fit beside it in a decode's room;
# - "stack": the sharpness so far, the choice map and the picture being filled in (4 + 1 + 3) and, scoring a frame,
#   its grey and two float sharpness maps (1 + 4 + 4), or its sharpness and the new sharpness so far (4 + 4 + 1):
#   17 at most, 12 more than a decode's room;
# - "sharpest": its JPEG (1); the frame and its cut copy fit in a decode's room;
# - "held-sharpest": the sharpest so far (3) and, scoring a frame, its grey, float Laplacian and that's variance's
#   copy (1 + 4 + 4).
# MARGIN covers what neither count states: OpenCV's filters' and the JPEG encoder's own working rows (at most 0.9
# bytes a pixel, measured) and small objects. tests/test_enhance.py holds every way to its reservation.
PER_PX = {"average": 12, "stack": 12, "sharpest": 1, "held-sharpest": 12}
DECODE_PX = 6
MARGIN = 1.1


@dataclass
class Plan:
    """What an enhanced picture is made from, before it is made: `work` makes it (off the event loop) from frames
    whose newest was at `newest` ((dx, dy, epoch)) with the eyepiece `fov` of then; `label` says how for the
    magnifier ("{n}" the frames used), `size` is the frames' (width, height); `info` the newest frame's facts (its
    source, for provenance). `used`: the frames it is made from (the one chosen, once `work` has chosen it); `per_px`:
    the bytes a pixel of them it holds at its peak (PER_PX); `crop`: the box of each frame it works on (frame px),
    when cut to the eyepiece first. `need` is what to reserve for it."""
    work: Callable[[], Enhanced]
    newest: tuple
    fov: object
    label: str
    size: tuple
    info: object = None
    used: list = field(default_factory=list)
    per_px: float = 0.0
    crop: tuple | None = None

    @property
    def pixels(self) -> int:
        """The pixels of each frame it works on."""
        if self.crop is not None:
            return (self.crop[2] - self.crop[0]) * (self.crop[3] - self.crop[1])
        return int(self.size[0]) * int(self.size[1])

    @property
    def need(self) -> int:
        """The bytes it holds at its peak, its margin included: what both the user's tool and a researcher's hands
        reserve before making it. Each frame is decoded whole, also when only the eyepiece's box is worked on."""
        whole = int(self.size[0]) * int(self.size[1])
        return int((self.pixels * self.per_px + whole * DECODE_PX) * MARGIN)


def disc_box(fov, width: int, height: int) -> tuple | None:
    """The eyepiece disc with a little room (frame px), inside the frame; None without one."""
    if fov is None:
        return None
    pad = fov.radius * 1.02
    box = (max(0, int(fov.cx - pad)), max(0, int(fov.cy - pad)), min(int(width), int(fov.cx + pad) + 1),
           min(int(height), int(fov.cy + pad) + 1))
    return box if box[2] - box[0] >= 8 and box[3] - box[1] >= 8 else None


def _loader(jpeg: bytes, box: tuple | None):
    """A frame's decode, cut to `box` (the same box in every frame, so their offsets still align them). The cut is
    a copy, so the whole frame's decode is let go at once rather than kept alive behind it."""
    if box is None:
        return lambda: decode(jpeg)
    x0, y0, x1, y1 = box
    return lambda: decode(jpeg)[y0:y1, x0:x1].copy()


def decode(jpeg: bytes) -> np.ndarray:
    from .brain.registry import ToolFailed

    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ToolFailed("a camera frame could not be read", next="ask again in a moment")
    return image


def spread(items: list, most: int) -> list:
    """At most `most` of the items, evenly spread, the newest always kept."""
    if len(items) <= most:
        return list(items)
    picks = np.linspace(0, len(items) - 1, most).round().astype(int)
    return [items[i] for i in sorted(set(picks.tolist()))]


def still_frames(hub) -> list:
    """The frames the slide has been still for, newest last: one epoch, steady, within
    AVERAGE_WINDOW_S of the newest, and still in the camera ring."""
    latest = hub.latest_info
    out = []
    for info in reversed(list(hub.infos.values())):
        if info.epoch != latest.epoch or not info.steady or latest.wall_at - info.wall_at > AVERAGE_WINDOW_S:
            break
        if hub.source.get(info.id) is not None:
            out.append(info)
    return out[::-1]


def live_plan(hub, how: str, sweep=None, crop: bool = False) -> Plan:
    """The live view's frames for an enhanced picture made `how`. Raises ToolFailed saying what to do when
    there is none to make (the slide moving, no turn of the focus knob yet). `crop`: work on the eyepiece disc
    alone (a researcher's picture: less memory); a focus stack is never cut (it scales frames by their discs)."""
    from .brain.registry import ToolFailed

    latest = hub.latest_info
    if latest is None or hub.source.get(latest.id) is None:
        from .frames import no_picture
        why, next_step = no_picture(hub, "there is no live picture to work from",
                                     "check the camera is connected and the view is live, then ask again")
        raise ToolFailed(why, next=next_step)
    size = (latest.width, latest.height)
    if how == "average":
        if not latest.steady:
            raise ToolFailed(HOLD_STILL, next=None)  # no next: the message says what to do
        infos = still_frames(hub)
        if len(infos) < AVERAGE_MIN:
            raise ToolFailed("the slide has not been still long enough: hold it still for a second, then ask again",
                             next=None)  # no next: the message says what to do
        infos = spread(infos, AVERAGE_MAX)
        jpegs = [(hub.source.get(i.id).jpeg, i.dx, i.dy) for i in infos]
        span = infos[-1].wall_at - infos[0].wall_at
        box = disc_box(infos[-1].fov, *size) if crop else None

        def work() -> Enhanced:
            # Each frame decoded as it is added, one resident at a time.
            return average([(_loader(j, box), dx, dy) for j, dx, dy in jpegs], span_s=span)
        return Plan(work, (infos[-1].dx, infos[-1].dy, infos[-1].epoch), infos[-1].fov, "Averaged: {n} real frames",
                    size, infos[-1], list(infos), PER_PX["average"], box)
    if how == "sharpest":
        best = hub.best_frame(window_s=SHARPEST_WINDOW_S)
        if best is None:
            raise ToolFailed("there is no recent frame to choose from", next="wait for the view to come back, then try again")
        considered = sum(1 for fid, i in list(hub.infos.items()) if i.epoch == best.epoch
                         and latest.wall_at - i.wall_at <= SHARPEST_WINDOW_S and hub.source.get(fid) is not None)
        jpeg = hub.source.get(best.id).jpeg
        box = disc_box(best.fov, *size) if crop else None

        def work() -> Enhanced:
            return sharpest(_loader(jpeg, box)(), max(1, considered), SHARPEST_WINDOW_S)
        return Plan(work, (best.dx, best.dy, best.epoch), best.fov, f"Sharpest: 1 real frame of {max(1, considered)}",
                    size, best, [best], PER_PX["sharpest"], box)
    swept = sweep.latest() if sweep is not None else None
    if swept is None:
        raise ToolFailed(NO_SWEEP, next=None)  # no next: the message says what to do
    frames = spread(swept.frames, STACK_MAX)
    last = frames[-1]
    known = hub.infos.get(last.id)
    ago = None if swept.ended_at is None else max(0.0, time.time() - swept.ended_at)

    def work() -> Enhanced:
        return focus_stack([(lambda j=f.jpeg: decode(j), f.dx, f.dy) for f in frames], ago_s=ago,
                           discs=[f.disc for f in frames])
    return Plan(work, (last.dx, last.dy, last.epoch), known.fov if known is not None else latest.fov,
                "Focus stack: {n} real frames", size, known, [known or last], PER_PX["stack"])


def held_plan(frames: list, how: str, crop: bool = False) -> Plan:
    """A held clip's frames (held.HeldFrame) for an enhanced picture: "average" averages its still frames of the
    newest frame's view (one epoch, aligned by the slide's offset); "sharpest" is its sharpest frame, unaltered.
    A clip is not a turn of the focus knob, so it is never stacked. Raises ToolFailed saying why. `used` names the
    frames it is made from (the one chosen, once `work` has run); `crop`: the eyepiece disc alone."""
    from .brain.registry import ToolFailed

    kept = [f for f in frames if getattr(f, "info", None) is not None]
    if not kept:
        raise ToolFailed("this clip's frames were kept without their facts", next=None)  # no next: nothing to do differently
    newest = kept[-1]
    size = (newest.info.width, newest.info.height)
    if how == "stack":
        raise ToolFailed("a held clip is not a turn of the focus knob: it can be averaged or its sharpest frame taken",
                         next="ask for average or sharpest")
    if how == "average":
        still = [f for f in kept if f.epoch == newest.epoch and f.steady]
        if len(still) < AVERAGE_MIN:
            raise ToolFailed(f"the clip has {len(still)} still frames of one view; averaging takes at least {AVERAGE_MIN}",
                             next="take its sharpest frame instead")
        still = spread(still, AVERAGE_MAX)
        span = still[-1].at - still[0].at
        box = disc_box(getattr(still[-1].info, "fov", None), *size) if crop else None

        def work() -> Enhanced:
            return average([(_loader(f.jpeg, box), f.dx, f.dy) for f in still], span_s=span)
        return Plan(work, (still[-1].dx, still[-1].dy, still[-1].epoch), getattr(still[-1].info, "fov", None),
                    "Averaged: {n} real frames of the held clip", size, still[-1].info, list(still),
                    PER_PX["average"], box)
    box = disc_box(getattr(newest.info, "fov", None), *size) if crop else None
    plan = Plan(None, (newest.dx, newest.dy, newest.epoch), getattr(newest.info, "fov", None),
                f"Sharpest: 1 real frame of the held clip's {len(kept)}", size, newest.info, [],
                PER_PX["held-sharpest"], box)

    def work() -> Enhanced:
        # Each frame decoded and scored one at a time; only the sharpest so far is kept. The one chosen is named.
        best, score, chosen = None, -1.0, None
        for f in kept:
            image = _loader(f.jpeg, box)()
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            s = float(cv2.Laplacian(gray, cv2.CV_32F).var())
            if s > score:
                best, score, chosen = image, s, f
            del image, gray
        plan.used[:] = [chosen]
        plan.info = chosen.info
        return Enhanced(best, 1, f"the sharpest of the held clip's {len(kept)} frames, unaltered")
    plan.work = work
    return plan


def disc_cut(result: Enhanced, fov) -> tuple[int, int, int, int]:
    """Where to cut a result (its own coordinates) to the eyepiece disc with a little room, or all of it."""
    oh, ow = result.image.shape[:2]
    if fov is None:
        return (0, 0, ow, oh)
    ox, oy = result.origin
    pad = fov.radius * 1.02
    x0, y0 = max(0, int(fov.cx - pad) - ox), max(0, int(fov.cy - pad) - oy)
    x1, y1 = min(ow, int(fov.cx + pad) - ox), min(oh, int(fov.cy + pad) - oy)
    return (x0, y0, x1, y1) if x1 - x0 >= 8 and y1 - y0 >= 8 else (0, 0, ow, oh)


def jpeg_of(result: Enhanced, cut: tuple[int, int, int, int]) -> bytes:
    from .brain.registry import ToolFailed

    x0, y0, x1, y1 = cut
    ok, encoded = cv2.imencode(".jpg", result.image[y0:y1, x0:x1], [cv2.IMWRITE_JPEG_QUALITY, 94])
    if not ok:
        raise ToolFailed("the picture could not be encoded", next="ask again in a moment")
    return encoded.tobytes()
