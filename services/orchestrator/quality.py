"""Is this frame good enough to name things in? (roadmap W1-1; Google's AR microscope shows
nothing on out-of-focus frames, EIMS ranks "no target, over-exposed, out of focus".)

Measured once per frame on the eyepiece view at quarter size, next to the focus score:
  blurry   sharpness under BLUR_FRACTION of its recent peak (sharpness depends on the
           specimen, so a frame is compared with what this slide looked like in focus)
  empty    almost no texture or contrast at all (no slide, a blank field, the light off)
  dark     the view is mostly black (the light is too low)
  bright   the view is washed out
  glare    a large part of the view is saturated

Calibrated on the June 14 footage: focus 200-340 in focus, about half at a 2 px blur, a fifth
at 4 px, a twentieth at 8 px; a blank disc 7 with contrast 1.4; a darkened view 9-64 mean.
"""
from __future__ import annotations

import cv2
import numpy as np


BLUR_FRACTION = 0.35  # sharpness below this share of the recent peak is blurry
PEAK_DECAY = 0.997  # per frame: the peak halves in ~8 s at 30 fps, so a new specimen soon sets its own
JUMP_KEEP = 0.5  # share of the peak kept when the slide jumps (a new motion epoch)
MIN_PEAK = 20.0  # below this the peak says nothing about focus (a texture-less specimen)
EMPTY_FOCUS, EMPTY_STD = 15.0, 4.0
DARK_MEAN, BRIGHT_MEAN = 30.0, 225.0
GLARE_FRACTION = 0.25  # share of pixels at 250 or more
# Below this mean difference (grey levels, 64 px thumbnail) two pictures count as the same; how long
# the picture has been the same (`still`) is measured and logged only. A still picture is the normal
# state of a microscope: it is never called frozen, dimmed or refused (2026-10-06: the iPhone on a
# still slide, heavily denoised, differs by only 0.0024-0.0066 a frame, and a "frozen" flag at 0.004
# blanked the live view; a camera that stops sending is caught by frames no longer arriving).
STILL_DIFF = 0.0005
UNNAMEABLE = ("empty", "blurry", "dark", "bright")  # with the QUALITY gate (an option), these are not named

WORDS = {"blurry": "out of focus",
         "empty": "empty (nothing on the slide here)", "dark": "too dark",
         "bright": "washed out (too bright)", "glare": "partly washed out by glare"}


def _centre(gray: np.ndarray, fov) -> np.ndarray:
    """The eyepiece view inside 0.7 of its radius (the black surround and the vignetted rim
    left out)."""
    if fov is not None:
        r = int(fov.radius * 0.7)
        x0, y0 = max(0, int(fov.cx) - r), max(0, int(fov.cy) - r)
        gray = gray[y0 : int(fov.cy) + r, x0 : int(fov.cx) + r]
    return gray


def view_stats(gray: np.ndarray, fov) -> tuple[float, float, float, float]:
    """(focus, mean, std, saturated fraction) of the eyepiece view, at quarter size."""
    gray = _centre(gray, fov)
    if gray.size == 0:
        return 0.0, 0.0, 0.0, 0.0
    small = cv2.resize(gray, (max(1, gray.shape[1] // 4), max(1, gray.shape[0] // 4)), interpolation=cv2.INTER_AREA)
    return (float(cv2.Laplacian(small, cv2.CV_32F).var()), float(small.mean()), float(small.std()),
            float((small >= 250).mean()))


def thumbnail(gray: np.ndarray, fov) -> np.ndarray | None:
    """A 64 px grey thumbnail of the eyepiece view, for telling a live picture from a frozen one."""
    gray = _centre(gray, fov)
    if gray.size == 0:
        return None
    return cv2.resize(gray, (64, 64), interpolation=cv2.INTER_AREA).astype(np.float32)


class Content:
    """When the picture last changed at all (a frozen feed keeps arriving unchanged)."""

    def __init__(self) -> None:
        self.previous: np.ndarray | None = None
        self.changed_at: float | None = None

    def update(self, thumb: np.ndarray | None, wall_at: float) -> float:
        """The wall time the picture last changed, after this frame."""
        if thumb is None or self.previous is None or self.changed_at is None or thumb.shape != self.previous.shape:
            self.changed_at = wall_at
        elif float(np.abs(thumb - self.previous).mean()) >= STILL_DIFF:
            self.changed_at = wall_at
        self.previous = thumb
        return self.changed_at


class Quality:
    """The running peak of sharpness, per motion epoch, and each frame's flags."""

    def __init__(self) -> None:
        self.peak = 0.0
        self.epoch: int | None = None

    def flags(self, focus: float, mean: float, std: float, saturated: float, epoch: int) -> tuple[str, ...]:
        if epoch != self.epoch:
            # The slide jumped: maybe another specimen, so the old peak counts half (a duller
            # specimen is not called blurry), but a jump straight into a blur is still caught.
            self.epoch, self.peak = epoch, max(focus, JUMP_KEEP * self.peak)
        self.peak = max(focus, self.peak * PEAK_DECAY)
        found = []
        if mean < DARK_MEAN:
            found.append("dark")
        elif mean > BRIGHT_MEAN:
            found.append("bright")
        if focus < EMPTY_FOCUS and std < EMPTY_STD:
            found.append("empty")
        elif self.peak >= MIN_PEAK and focus < BLUR_FRACTION * self.peak:
            found.append("blurry")
        if saturated > GLARE_FRACTION and "bright" not in found:
            found.append("glare")
        return tuple(found)


def measured(info) -> str:
    """What the app measured on a picture, as numbers for a model to weigh (QUALITY "tell"): never
    a verdict. The model looks at the picture and decides whether it can tell what is there."""
    stats = getattr(info, "stats", ()) or ()
    if len(stats) < 4:
        return ""
    focus, mean, std, saturated = stats
    peak = getattr(info, "peak", 0.0) or 0.0
    sharp = (f"sharpness {round(100 * focus / peak)}% of the sharpest this slide has looked recently"
             if peak >= MIN_PEAK else "sharpness not comparable yet (too little texture seen)")
    return (f"Measured by the app on this picture (numbers, not a judgment): {sharp}; brightness "
            f"{mean:.0f} of 255; contrast {std:.0f}; {100 * saturated:.0f}% of the view at full white.")


def unnameable(flags) -> tuple[str, ...]:
    return tuple(f for f in flags or () if f in UNNAMEABLE)


def describe(flags) -> str:
    """'out of focus and too dark', for the person."""
    words = [WORDS[f] for f in flags if f in WORDS]
    return " and ".join(words)


# --- something changed in a still view (roadmap W3-1) ---------------------------------------
# Sight looked again only when the slide moved, the labels changed, or after a minute, so
# something swimming in, or a turn of the focus knob, went unseen. While the slide is still,
# the view is compared with a reference taken a moment ago, shifted by the slide's own small
# motion, at 128 px: pixels that changed by far more than the frame-to-frame noise. Something
# new is one compact blob of them; a refocus, a vibration or a breath of the stage changes
# pixels all over. Calibrated on the June 14 footage (a still stretch, then the hand-held
# microscope's focus breathing) and a 36 px blob crossing it: the blob's changed pixels were
# one component of 72-80; breathing gave scattered specks (largest 1-13) or 700-1,500 pixels
# in many blobs.
CHANGE_EVERY = 5  # frames between checks (6 a second at 30 fps)
BLOB_PIXELS = 20  # the largest connected group of changed pixels (of 128x128) to count as something new
BLOB_SHARE = 0.5  # ...and it holds at least this share of all changed pixels
WIDE_PIXELS = 400  # this many changed pixels across the view: the view changed (focus, vibration), not one thing
LIGHT_SHIFT = 10.0  # mean grey levels: the light changed, not the content
FOCUS_RATIO = 1.6  # sharpness changed more than this either way: a refocus
REFERENCE_S = 5.0  # with nothing new, the reference is renewed this often (slow drift, light)
MARGIN = 4  # thumbnail border the shift fills in


class StillChange:
    """While the slide is still: did something new appear in the view? `update` returns
    "content" (something appeared, moved or left), "light", "focus" or None."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.reference: np.ndarray | None = None
        self.ref_offset = (0.0, 0.0)
        self.ref_focus = 0.0
        self.ref_at = 0.0
        self.previous: np.ndarray | None = None
        self.epoch: int | None = None
        self.count = 0

    def update(self, gray: np.ndarray, fov, steady: bool, epoch: int, offset: tuple[float, float],
               focus: float, wall_at: float) -> str | None:
        if fov is None or not steady or epoch != self.epoch:
            self.reset()
            self.epoch = epoch
            return None
        self.count += 1
        if self.count % CHANGE_EVERY:
            return None
        r = int(fov.radius * 0.7)
        cx, cy = int(fov.cx), int(fov.cy)
        crop = gray[max(0, cy - r): cy + r, max(0, cx - r): cx + r]
        if crop.shape[0] < 32 or crop.shape[1] < 32:
            return None
        now = cv2.resize(crop, (128, 128), interpolation=cv2.INTER_AREA).astype(np.float32)
        if self.reference is None or self.reference.shape != crop.shape or wall_at - self.ref_at > REFERENCE_S:
            self._renew(crop, offset, focus, wall_at, now)
            return None
        # Shift the reference by the slide's motion since it was taken (content at p moves to
        # p + offset change), then compare without the overall brightness.
        dx, dy = offset[0] - self.ref_offset[0], offset[1] - self.ref_offset[1]
        shifted = cv2.warpAffine(self.reference, np.float32([[1, 0, dx], [0, 1, dy]]), (crop.shape[1], crop.shape[0]),
                                 borderMode=cv2.BORDER_REPLICATE)
        then = cv2.resize(shifted, (128, 128), interpolation=cv2.INTER_AREA).astype(np.float32)
        noise = float(np.abs(now - self.previous).std()) if self.previous is not None else 1.0
        self.previous = now
        diff = ((now - now.mean()) - (then - then.mean()))[MARGIN:-MARGIN, MARGIN:-MARGIN]
        mask = (np.abs(diff) > max(6 * noise, 12.0)).astype(np.uint8)
        changed = int(mask.sum())
        largest = 0
        if changed:
            count, _labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
            largest = int(stats[1:, cv2.CC_STAT_AREA].max()) if count > 1 else 0
        light = abs(float(now.mean()) - float(then.mean()))
        ratio = (focus + 1.0) / (self.ref_focus + 1.0)
        kind = None
        if ratio > FOCUS_RATIO or ratio < 1 / FOCUS_RATIO or changed >= WIDE_PIXELS:
            kind = "focus"
        elif largest >= BLOB_PIXELS and largest >= BLOB_SHARE * changed:
            kind = "content"
        elif light > LIGHT_SHIFT:
            kind = "light"
        if kind is not None:
            self._renew(crop, offset, focus, wall_at, now)
        return kind

    def _renew(self, crop, offset, focus, wall_at, thumb) -> None:
        self.reference, self.ref_offset, self.ref_focus, self.ref_at = crop.astype(np.float32), offset, focus, wall_at
        self.previous = thumb
