"""Every distinct thing in the view as a mask, before anyone asks (pointing option "masks").

Segmenter-first Set-of-Mark (Yang et al. 2023): the outline model is prompted with a grid of
points over the eyepiece disc, plus the detected spots (candidates.py) so small dots are not
missed between grid points; each prompt gives three masks with the model's own quality score.
Masks that score well, are stable (the same mask at logit -1 and +1), lie inside the disc and
are neither specks nor half the field are kept, overlapping ones de-duplicated (the better one
stays), at most MAX_MASKS. The vision model then picks numbers; the masks are the outlines.

Pure numpy here (the worker does the model calls), so it is tested without a GPU.
"""
from __future__ import annotations

import numpy as np

GRID = 16             # grid points per side over the disc's square (about 200 inside the disc)
DISC_KEEP = 0.92      # of the disc radius: prompts and masks stay off the bright rim
MIN_SCORE = 0.65      # the model's predicted IoU
MIN_STABILITY = 0.70  # mask at logit +1 over mask at logit -1 (a faint stoma's is ~0.76)
MIN_AREA_PX = 8       # at the working size (WORK_SIDE; ~20 frame px²): smaller is noise
MAX_FRAC = 0.20       # of the disc: larger is "the whole field", not a thing
MIN_INSIDE = 0.85     # of a mask's area inside the disc (a rim artefact otherwise)
NMS_IOU = 0.6
MAX_MASKS = 40        # numbers on one picture before it stops being readable
WORK_SIDE = 512       # masks are judged at this size (the crop's longest side), then traced at it


def disc_points(width: int, height: int, disc: tuple[float, float, float] | None, n: int = GRID,
                keep: float = DISC_KEEP) -> list[list[float]]:
    """An n x n grid over the disc's square (or the whole image), the points inside the disc."""
    if disc is None:
        xs = (np.arange(n) + 0.5) * width / n
        ys = (np.arange(n) + 0.5) * height / n
        return [[float(x), float(y)] for y in ys for x in xs]
    cx, cy, r = disc
    step = 2 * r / n
    pts = []
    for j in range(n):
        for i in range(n):
            x, y = cx - r + (i + 0.5) * step, cy - r + (j + 0.5) * step
            if np.hypot(x - cx, y - cy) <= r * keep and 0 <= x < width and 0 <= y < height:
                pts.append([float(x), float(y)])
    return pts


def disc_mask(shape: tuple[int, int], disc: tuple[float, float, float] | None, keep: float = DISC_KEEP) -> np.ndarray:
    h, w = shape
    if disc is None:
        return np.ones((h, w), bool)
    cx, cy, r = disc
    yy, xx = np.mgrid[0:h, 0:w]
    return (xx - cx) ** 2 + (yy - cy) ** 2 <= (r * keep) ** 2


def iou(a: np.ndarray, b: np.ndarray) -> float:
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / union) if union else 0.0


def passes(area: int, inside: int, valid_area: int, score: float, stability: float) -> bool:
    """One mask's numbers against the thresholds (cheap, before any mask is compared)."""
    return (score >= MIN_SCORE and stability >= MIN_STABILITY and area >= MIN_AREA_PX
            and inside >= MIN_INSIDE * area and area <= MAX_FRAC * valid_area)


def select(candidates: list[dict], cap: int = MAX_MASKS) -> list[dict]:
    """De-duplicate passing masks ({"mask", "score", "stability"}), best first, at most `cap`;
    returned in reading order (top to bottom in bands, left to right), so numbers are easy to find."""
    kept: list[dict] = []
    for c in sorted(candidates, key=lambda c: -(c["score"] * c["stability"])):
        if all(iou(c["mask"], k["mask"]) <= NMS_IOU for k in kept):
            kept.append(c)
        if len(kept) >= cap:
            break
    for k in kept:
        ys, xs = np.nonzero(k["mask"])
        k["cx"], k["cy"] = float(xs.mean()), float(ys.mean())
    band = max(1, (max((k["mask"].shape[0] for k in kept), default=1)) // 8)
    return sorted(kept, key=lambda k: (int(k["cy"] // band), k["cx"]))


def label_point(mask: np.ndarray, others: list[np.ndarray] = ()) -> tuple[float, float, float]:
    """Where a region's number goes: the point deepest inside it (distance transform), away from
    smaller regions inside it (a cell's number never sits on its nucleus). (x, y, how far that
    point is from the region's edge), in mask pixels."""
    import cv2

    free = mask.copy()
    for o in others:
        if o.sum() < mask.sum():
            free &= ~o
    if not free.any():
        free = mask
    dist = cv2.distanceTransform(np.pad(free.astype(np.uint8), 1), cv2.DIST_L2, 3)[1:-1, 1:-1]
    y, x = np.unravel_index(int(dist.argmax()), dist.shape)
    return float(x), float(y), float(dist[y, x])
