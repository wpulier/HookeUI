"""Finding the parts inside an outlined object (the brain's `dissect` tool).

The object is cropped from the frame and prompted with a grid of points inside its mask on
one image embedding (spike S3, docs/bench/spike-s3-dissect.md: part masks score only
0.25-0.6, so the threshold is 0.25). Candidate masks are clipped to the object, kept if
they cover 1-60% of it, and de-duplicated (mask IoU > 0.7, higher score wins).
Pure numpy here, so it is tested without a GPU.
"""
from __future__ import annotations

import cv2
import numpy as np

MIN_SCORE = 0.25
MIN_FRAC, MAX_FRAC = 0.01, 0.60
NMS_IOU = 0.7
MAX_REGIONS = 8
CROP_PAD = 0.15


def polygon_mask(polygons, shape) -> np.ndarray:
    mask = np.zeros(shape[:2], np.uint8)
    for poly in polygons:
        cv2.fillPoly(mask, [np.round(np.array(poly)).astype(np.int32)], 1)
    return mask.astype(bool)


def crop_box(mask: np.ndarray, pad: float = CROP_PAD) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask)
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    px, py = (x1 - x0) * pad, (y1 - y0) * pad
    h, w = mask.shape
    return int(max(0, x0 - px)), int(max(0, y0 - py)), int(min(w, x1 + px)), int(min(h, y1 + py))


def grid_points(mask: np.ndarray, n: int = 8) -> list[list[float]]:
    """Up to n x n points on a grid over the mask's box, keeping those inside the mask."""
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return []
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    pts = []
    for j in range(n):
        for i in range(n):
            px, py = x0 + (i + 0.5) * (x1 - x0) / n, y0 + (j + 0.5) * (y1 - y0) / n
            if mask[int(py), int(px)]:
                pts.append([float(px), float(py)])
    return pts


def iou(a: np.ndarray, b: np.ndarray) -> float:
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / union) if union else 0.0


def refine(candidates: list[tuple[np.ndarray, float]], obj: np.ndarray, cap: int = MAX_REGIONS) -> list[dict]:
    """Clip to the object, keep MIN_FRAC..MAX_FRAC of its area, de-duplicate by mask IoU."""
    area = obj.sum()
    if not area:
        return []
    kept_area = []
    for mask, score in candidates:
        clipped = mask & obj
        frac = clipped.sum() / area
        if MIN_FRAC <= frac <= MAX_FRAC and score >= MIN_SCORE:
            kept_area.append({"mask": clipped, "score": float(score), "frac": float(frac)})
    kept: list[dict] = []
    for region in sorted(kept_area, key=lambda r: -r["score"]):
        if all(iou(region["mask"], k["mask"]) <= NMS_IOU for k in kept):
            kept.append(region)
    return kept[:cap]
