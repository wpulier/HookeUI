"""Find the microscope eyepiece's circular field of view in a camera frame.

Shooting through an eyepiece leaves a bright disc surrounded by black. Everything
outside the disc is meaningless to analysis and, worse, stays fixed while the slide
moves, which would bias motion estimates toward zero.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class FieldOfView:
    cx: float
    cy: float
    radius: float
    confidence: float  # fraction of the fitted disc that is actually lit

    def inscribed_square(self, shrink: float = 0.92) -> tuple[int, int, int, int]:
        """Largest axis-aligned square inside the disc, as (x, y, w, h) pixels."""
        half = self.radius * shrink / np.sqrt(2.0)
        return (
            int(round(self.cx - half)),
            int(round(self.cy - half)),
            int(round(2 * half)),
            int(round(2 * half)),
        )

    def mask(self, width: int, height: int, erode_px: int = 0) -> np.ndarray:
        mask = np.zeros((height, width), dtype=np.uint8)
        cv2.circle(
            mask,
            (int(round(self.cx)), int(round(self.cy))),
            max(1, int(round(self.radius - erode_px))),
            255,
            thickness=-1,
        )
        return mask

    def contains(self, x: float, y: float, margin: float = 0.0) -> bool:
        return (x - self.cx) ** 2 + (y - self.cy) ** 2 <= (self.radius - margin) ** 2


MIN_DISC_BRIGHTNESS = 45.0  # mean grey level inside the disc (0-255)
MIN_DISC_CONTRAST = 30.0  # inside minus outside


def detect_fov(gray: np.ndarray, dark_threshold: int | None = None) -> FieldOfView | None:
    """Fit the lit disc. Returns None when the frame has no eyepiece-like disc
    (for example a plain camera view with no microscope)."""
    height, width = gray.shape[:2]
    scale = 320.0 / max(width, height)
    small = cv2.resize(gray, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (5, 5), 0)

    if dark_threshold is None:
        # Otsu splits black surround from lit disc; clamp so dark specimens don't vanish.
        otsu, _ = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        dark_threshold = int(np.clip(otsu * 0.5, 12, 60))
    _, lit = cv2.threshold(small, dark_threshold, 255, cv2.THRESH_BINARY)
    lit = cv2.morphologyEx(lit, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))

    contours, _ = cv2.findContours(lit, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(largest)
    if area < 0.05 * small.size:
        return None

    lit_fraction = float(np.count_nonzero(lit) / lit.size)
    if lit_fraction > 0.97:
        return None

    hull = cv2.convexHull(largest)
    # Fit only the rim: points touching the frame border belong to the image edge, not the
    # eyepiece, and would shrink the circle when the disc is larger than the frame.
    points = hull.reshape(-1, 2).astype(np.float64)
    h_small, w_small = small.shape[:2]
    on_border = (
        (points[:, 0] <= 1) | (points[:, 1] <= 1) | (points[:, 0] >= w_small - 2) | (points[:, 1] >= h_small - 2)
    )
    if int((~on_border).sum()) < 5:
        return None
    fitted = _fit_circle(_rim_samples(points, on_border))
    if fitted is None:
        return None
    cx, cy, radius = fitted

    # Confidence: how much of the fitted disc (within the frame) is lit.
    disc = np.zeros_like(lit)
    cv2.circle(disc, (int(round(cx)), int(round(cy))), int(round(radius)), 255, thickness=-1)
    disc_pixels = np.count_nonzero(disc)
    if disc_pixels == 0:
        return None
    agreement = float(np.count_nonzero(cv2.bitwise_and(disc, lit)) / disc_pixels)
    spill = float(np.count_nonzero(cv2.bitwise_and(cv2.bitwise_not(disc), lit)) / max(np.count_nonzero(lit), 1))
    if agreement < 0.8 or spill > 0.15:
        return None
    # A real eyepiece is a clearly lit disc in a black surround. A dark room, a covered lens or
    # a dim blob is not one (it would zoom the view into darkness).
    inside = float(cv2.mean(small, mask=disc)[0])
    outside_mask = cv2.bitwise_not(disc)
    outside = float(cv2.mean(small, mask=outside_mask)[0]) if np.count_nonzero(outside_mask) else 0.0
    if inside < MIN_DISC_BRIGHTNESS or inside - outside < MIN_DISC_CONTRAST:
        return None

    return FieldOfView(cx=cx / scale, cy=cy / scale, radius=radius / scale, confidence=round(agreement, 3))


def _rim_samples(points: np.ndarray, on_border: np.ndarray) -> np.ndarray:
    """Rim vertices plus evenly spaced samples along hull edges between two rim
    vertices. Hull vertices are sparse on smooth arcs, and edges that touch the frame
    border are skipped because they trace the image edge, not the eyepiece."""
    samples = [points[~on_border]]
    count = len(points)
    for i in range(count):
        j = (i + 1) % count
        if on_border[i] or on_border[j]:
            continue
        a, b = points[i], points[j]
        steps = int(np.linalg.norm(b - a) // 4)
        if steps > 1:
            t = np.linspace(0, 1, steps, endpoint=False)[1:, None]
            samples.append(a + (b - a) * t)
    return np.vstack(samples)


def _fit_circle(points: np.ndarray) -> tuple[float, float, float] | None:
    """Algebraic (Kasa) least-squares circle fit."""
    x, y = points[:, 0], points[:, 1]
    a = np.column_stack([x, y, np.ones_like(x)])
    b = x * x + y * y
    try:
        sol, *_ = np.linalg.lstsq(a, b, rcond=None)
    except np.linalg.LinAlgError:
        return None
    cx, cy = sol[0] / 2.0, sol[1] / 2.0
    r_sq = sol[2] + cx * cx + cy * cy
    if r_sq <= 0:
        return None
    return float(cx), float(cy), float(np.sqrt(r_sq))


# --- the disc's size to a fraction of a pixel ------------------------------------------------
# The eyepiece's field stop sits where the objective forms its image, so turning the focus knob
# moves the specimen's image but never the disc: its edge and size stay put. The phone's own lens
# is different: when it refocuses ("hunts"), everything it sees grows or shrinks a little (focus
# breathing), the disc too. So a disc that changed size says the phone's camera changed (its lens,
# or the phone moved on the eyepiece), not the knob. Measured on the real recordings (2026-06-14
# and the 2026-10-06 demo, 38 min): with the view steady and sharpness steady, the radius held to
# 0.006% (median of 1 s windows; about 0.02 px); the user turning the knob through focus changed
# it by at most 0.05%; the phone hunting on a blank field changed it by 1-2.4%, back and forth with
# the sharpness, before it settled.
EDGE_RAYS = 180  # rays from the centre, every 2 degrees
EDGE_REACH = 30.0  # px either side of the tracked rim that each ray samples
EDGE_STEP = 0.5  # px between samples
EDGE_MIN_STEP = 30.0  # grey levels between inside and outside for a ray to count
EDGE_MIN_RAYS = 30  # rays with an edge (60 degrees of rim) for a measurement
_OFFSETS = np.arange(-EDGE_REACH, EDGE_REACH + EDGE_STEP / 2, EDGE_STEP)
_ANGLES = np.linspace(0.0, 2.0 * np.pi, EDGE_RAYS, endpoint=False)


def edge_radius(gray: np.ndarray, fov: FieldOfView | None) -> float | None:
    """The disc's radius to a fraction of a pixel (about a millisecond): where each ray from the
    tracked centre crosses half-way between the lit inside and the dark outside, and a circle fitted
    to those points. None without a disc or with too little of its rim in the frame."""
    if fov is None:
        return None
    height, width = gray.shape[:2]
    cos, sin = np.cos(_ANGLES), np.sin(_ANGLES)
    reach = fov.radius + _OFFSETS[None, :]
    xs = fov.cx + reach * cos[:, None]
    ys = fov.cy + reach * sin[:, None]
    inside_frame = (xs.min(1) >= 2) & (ys.min(1) >= 2) & (xs.max(1) <= width - 3) & (ys.max(1) <= height - 3)
    if int(inside_frame.sum()) < EDGE_MIN_RAYS:
        return None
    xs, ys = xs[inside_frame].astype(np.float32), ys[inside_frame].astype(np.float32)
    profiles = cv2.remap(gray.astype(np.float32), xs, ys, cv2.INTER_LINEAR)
    inside = np.median(profiles[:, :10], axis=1)
    outside = np.median(profiles[:, -10:], axis=1)
    step = inside - outside
    level = profiles - outside[:, None] - 0.5 * step[:, None]  # above zero: brighter than half-way
    falls = (level[:, :-1] >= 0) & (level[:, 1:] < 0)
    falls &= (step >= EDGE_MIN_STEP)[:, None]
    middle = len(_OFFSETS) // 2
    distance = np.where(falls, np.abs(np.arange(falls.shape[1]) - middle)[None, :], np.iinfo(np.int32).max)
    j = distance.argmin(axis=1)
    rows = np.nonzero(falls[np.arange(len(j)), j])[0]
    if len(rows) < EDGE_MIN_RAYS:
        return None
    j = j[rows]
    a, b = level[rows, j], level[rows, j + 1]
    at = _OFFSETS[j] + EDGE_STEP * a / (a - b)  # where the profile crosses half-way
    angles = _ANGLES[inside_frame][rows]
    px = fov.cx + (fov.radius + at) * np.cos(angles)
    py = fov.cy + (fov.radius + at) * np.sin(angles)
    fitted = _fit_circle(np.column_stack([px, py]))
    return None if fitted is None else fitted[2]


ENTER_AGREEMENT = 4  # of the recent looks, before the view zooms to an eyepiece


class FovTracker:
    """Keep a stable field of view across frames.

    Per-frame fits jitter by a few percent (exposure, specimen brightness near the rim).
    The tracked value only changes when recent detections agree on a different disc,
    which happens when the phone shifts on the eyepiece or magnification changes.
    """

    def __init__(self, every_n_frames: int = 10, window: int = 7, change_fraction: float = 0.04) -> None:
        self.every_n_frames = every_n_frames
        self.change_fraction = change_fraction
        self.recent: list[FieldOfView | None] = []
        self.window = window
        self.current: FieldOfView | None = None
        self._count = 0

    def update(self, gray: np.ndarray) -> FieldOfView | None:
        self._count += 1
        if self.current is not None and self._count % self.every_n_frames:
            return self.current
        self.recent.append(detect_fov(gray))
        self.recent = self.recent[-self.window :]
        found = [f for f in self.recent if f is not None]
        if self.current is None and len(found) < min(ENTER_AGREEMENT, self.window):
            # Not on an eyepiece yet: wait until most recent looks agree on one, so a single
            # false find never zooms the view in.
            return None
        if len(found) * 2 <= len(self.recent):
            # Mostly no disc: the camera is not on an eyepiece (or the view is dark).
            if len(self.recent) >= 3:
                self.current = None
            return self.current
        median = FieldOfView(
            cx=float(np.median([f.cx for f in found])),
            cy=float(np.median([f.cy for f in found])),
            radius=float(np.median([f.radius for f in found])),
            confidence=float(np.median([f.confidence for f in found])),
        )
        if self.current is None or self._differs(median, self.current):
            self.current = median
        return self.current

    def _differs(self, a: FieldOfView, b: FieldOfView) -> bool:
        tolerance = self.change_fraction * b.radius
        return (
            abs(a.cx - b.cx) > tolerance
            or abs(a.cy - b.cy) > tolerance
            or abs(a.radius - b.radius) > tolerance
        )
