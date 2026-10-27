"""Named comparisons (M2 of docs/design/2026-10-06-captures-operations-evidence.md): the models the user
names do the same work on the same view, each through the runner with the policy asked for (raw by
default, ADR 0011), and the app draws what each made side by side on that view's own pixels.

- Not the blind arena: the user asked by name, so names are shown; votes are the arena's alone.
- A result reused from an earlier run (the runner's shared jobs, or a population already kept for that
  view and model) is said to be reused, with when it was made, and its time is not counted as a run.
- What each made is measured by app code on the same view: an outline's area and the overlap between
  them (IoU); for every cell, the counts and how many cells each found that the other found too.
"""
from __future__ import annotations

import time

import cv2
import numpy as np

from .drawing import outline

COLOURS_BGR = [(249, 121, 232), (34, 197, 94), (56, 195, 247)]  # pink, green, amber: one per model
MATCH_IOU = 0.5  # two cells' boxes this alike are the same cell, for a comparison's counts


def polygon_area(polygons) -> float:
    """The area an outline covers, a ring's hole taken off (scale.filled_area)."""
    from .scale import filled_area

    return float(filled_area(polygons))


def mask_of(polygons, shape: tuple[int, int], origin: tuple[int, int] = (0, 0)) -> np.ndarray:
    out = np.zeros(shape, np.uint8)
    for p in polygons or []:
        pts = np.round(np.asarray(p, float) - origin).astype(np.int32).reshape(-1, 1, 2)
        cv2.fillPoly(out, [pts], 1)
    return out.astype(bool)


def overlap(a, b) -> float | None:
    """IoU of two outlines (lists of polygons in one view's px), drawn on the box around both."""
    pts = [np.asarray(p, float) for p in [*(a or []), *(b or [])]]
    if not a or not b or not pts:
        return None
    allp = np.concatenate(pts)
    x0, y0 = np.floor(allp.min(axis=0)).astype(int)
    x1, y1 = np.ceil(allp.max(axis=0)).astype(int)
    shape = (int(y1 - y0 + 2), int(x1 - x0 + 2))
    ma, mb = mask_of(a, shape, (x0, y0)), mask_of(b, shape, (x0, y0))
    union = int((ma | mb).sum())
    return round(int((ma & mb).sum()) / union, 3) if union else None


def box_iou(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def matched(a_boxes: list, b_boxes: list) -> int:
    """How many of `a_boxes` have a box in `b_boxes` at least MATCH_IOU alike (each used once)."""
    free = list(b_boxes)
    n = 0
    for box in a_boxes:
        best = max(range(len(free)), key=lambda i: box_iou(box, free[i]), default=None)
        if best is not None and box_iou(box, free[best]) >= MATCH_IOU:
            free.pop(best)
            n += 1
    return n


def when(at: float | None) -> str:
    return time.strftime("%H:%M:%S", time.localtime(at)) if at else "earlier"


def caption_strip(text: str, width: int, colour) -> np.ndarray:
    """The caption under a panel, whole: smaller type, then a second line, so the area is never cut off."""
    font, room = cv2.FONT_HERSHEY_SIMPLEX, max(20, width - 12)
    for scale in (0.5, 0.42, 0.36):
        if cv2.getTextSize(text, font, scale, 1)[0][0] <= room:
            strip = np.full((28, width, 3), 24, np.uint8)
            cv2.putText(strip, text, (6, 19), font, scale, colour, 1, cv2.LINE_AA)
            return strip
    head, _, tail = text.rpartition(" · ")
    lines = [head, tail] if head else [text[: len(text) // 2], text[len(text) // 2:]]
    strip = np.full((44, width, 3), 24, np.uint8)
    for n, line in enumerate(lines):
        cv2.putText(strip, line, (6, 17 + 18 * n), font, 0.36, colour, 1, cv2.LINE_AA)
    return strip


def picture(image: np.ndarray, arms: list[dict], box, height: int = 300) -> bytes | None:
    """One panel per model: the view's own pixels around `box` (x0, y0, x1, y1), with what that model
    made drawn on it and its name under it; panels side by side. A JPEG, or None."""
    h, w = image.shape[:2]
    x0, y0, x1, y1 = box
    pad = max(24.0, 0.3 * max(x1 - x0, y1 - y0))
    cx0, cy0 = int(max(0, x0 - pad)), int(max(0, y0 - pad))
    cx1, cy1 = int(min(w, x1 + pad)), int(min(h, y1 + pad))
    if cx1 <= cx0 or cy1 <= cy0:
        return None
    panels = []
    for n, arm in enumerate(arms):
        crop = image[cy0:cy1, cx0:cx1].copy()
        scale = height / crop.shape[0]
        crop = cv2.resize(crop, (max(1, int(crop.shape[1] * scale)), height), interpolation=cv2.INTER_CUBIC)
        colour = COLOURS_BGR[n % len(COLOURS_BGR)]
        for poly in arm.get("polygons") or []:
            pts = np.round((np.asarray(poly, float) - (cx0, cy0)) * scale).astype(np.int32).reshape(-1, 1, 2)
            outline(crop, [pts], colour, 2)  # on a dark casing: seen on any stain (drawing.py)
        panels.append(np.vstack([crop, caption_strip(str(arm.get("caption") or ""), crop.shape[1], colour)]))
    gap = np.full((panels[0].shape[0], 8, 3), 0, np.uint8)
    row = panels[0]
    for p in panels[1:]:
        row = np.hstack([row, gap, p])
    ok, jpeg = cv2.imencode(".jpg", row, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return jpeg.tobytes() if ok else None
