"""Measurements of a member from the specialist's own mask (contract 8).

docs/design/2026-10-06-captures-operations-evidence.md. Measurements use each member's original
instance mask, never a derived label map or the polygons drawn for display. Every value is in pixels
of the capture (a mask kept at another scale is multiplied back); evidence.py adds micrometres only by
the capture's scale_id.

- area: the mask's pixel count. Holes are not area; they are reported as `holeArea`. A mask in
  several pieces sums them and says `components`.
- perimeter: the outer boundaries of every piece, by Kulpa's corrected chain length (each axis step
  0.948, each diagonal step 1.340: an unbiased estimate of a digitised curve's length; counting
  boundary pixels underestimates it, summing steps as 1 and sqrt(2) overestimates it by about 5%).
- centroid: from the mask's moments.
- Feret max: the largest distance between vertices of the convex hull of the mask's pixel corners;
  Feret min: the smallest caliper width over the hull's edge directions (rotating calipers).
- elongation: Feret max / Feret min. circularity: 4 pi area / perimeter^2, at most 1 as in ImageJ: the pixel
  count reaches half a pixel past the boundary the perimeter runs along, so a small round mask read above 1
  (discs of radius 3-14 px: 1.01-1.09; live 2026-10-11, cells at 1.15). Flagged "small" under 50 px of area,
  where pixels make shape unreliable. solidity: area / hull area.
- intensity: the mean grey of the capture under the mask (comparable only within one capture and
  imaging setup).
- edge-cut: the mask touches the edge of what was segmented, or lies beyond 0.97 of the eyepiece
  disc's radius: part of the thing may be outside the view.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

KULPA_EVEN = 0.948
KULPA_ODD = 1.340
SMALL_AREA = 50.0
EDGE_DISC = 0.97


@dataclass
class Mask:
    """One member's mask: `bits` (bool) in its bounding box at (`x0`, `y0`) in capture pixels; `scale`
    capture pixels per mask pixel (1 when kept at full size)."""
    bits: np.ndarray
    x0: int
    y0: int
    scale: float = 1.0

    @property
    def box(self) -> tuple[float, float, float, float]:
        h, w = self.bits.shape
        return (self.x0, self.y0, self.x0 + w * self.scale, self.y0 + h * self.scale)


def kulpa_perimeter(bits: np.ndarray) -> float:
    """The length of the outer boundaries of every piece of `bits`, in mask pixels."""
    padded = np.pad(bits.astype(np.uint8), 1)
    contours, _ = cv2.findContours(padded, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    total = 0.0
    for contour in contours:
        pts = contour[:, 0, :].astype(np.int32)
        if len(pts) < 2:
            continue  # a one-pixel piece has no boundary steps (and is flagged small)
        steps = np.diff(np.vstack([pts, pts[:1]]), axis=0)
        diagonal = (steps[:, 0] != 0) & (steps[:, 1] != 0)
        total += KULPA_EVEN * float((~diagonal).sum()) + KULPA_ODD * float(diagonal.sum())
    return total


def _corners(bits: np.ndarray) -> np.ndarray:
    """The corners of the boundary pixels (each pixel is a unit square)."""
    padded = np.pad(bits.astype(np.uint8), 1)
    contours, _ = cv2.findContours(padded, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return np.zeros((0, 2), np.float64)
    pts = np.vstack([c[:, 0, :] for c in contours]).astype(np.float64) - 1.0  # undo the padding
    return np.vstack([pts, pts + [1, 0], pts + [0, 1], pts + [1, 1]])


def feret(points: np.ndarray) -> tuple[float, float, np.ndarray]:
    """(Feret max, Feret min, hull) of a point set: the largest distance between hull vertices, and
    the smallest width over the hull's edge directions."""
    if len(points) < 2:
        return 0.0, 0.0, points
    hull = cv2.convexHull(points.astype(np.float32))[:, 0, :].astype(np.float64)
    if len(hull) < 2:
        return 0.0, 0.0, hull
    diffs = hull[:, None, :] - hull[None, :, :]
    longest = float(np.sqrt((diffs ** 2).sum(-1)).max())
    widest_min = np.inf
    for i in range(len(hull)):
        a, b = hull[i], hull[(i + 1) % len(hull)]
        edge = b - a
        norm = float(np.hypot(*edge))
        if norm == 0:
            continue
        normal = np.array([-edge[1], edge[0]]) / norm
        widths = (hull - a) @ normal
        widest_min = min(widest_min, float(widths.max() - widths.min()))
    return longest, (0.0 if widest_min is np.inf else widest_min), hull


def measure(mask: Mask, gray: np.ndarray | None = None, disc: tuple[float, float, float] | None = None,
            segmented: tuple[int, int, int, int] | None = None) -> dict:
    """Every measurement of one member, in capture pixels (see the module's definitions).
    `gray`: the capture's grey picture (for intensity); `disc`: (cx, cy, r) of the eyepiece;
    `segmented`: (x, y, w, h) of the region the specialist segmented (for edge-cut)."""
    bits = np.asarray(mask.bits, bool)
    s = float(mask.scale)
    area_px = float(bits.sum())
    if area_px == 0:
        return {"areaPx": 0.0, "empty": True}
    count, labels = cv2.connectedComponents(bits.astype(np.uint8), connectivity=8)
    padded = np.pad(bits.astype(np.uint8), 1)
    contours, _ = cv2.findContours(padded.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros_like(padded)
    cv2.drawContours(filled, contours, -1, 1, thickness=cv2.FILLED)
    hole_px = float(filled.sum()) - area_px
    perimeter = kulpa_perimeter(bits) * s
    moments = cv2.moments(bits.astype(np.uint8), binaryImage=True)
    cx = mask.x0 + (moments["m10"] / moments["m00"] + 0.5) * s
    cy = mask.y0 + (moments["m01"] / moments["m00"] + 0.5) * s
    longest, shortest, hull = feret(_corners(bits))
    hull_area = float(cv2.contourArea(hull.astype(np.float32))) if len(hull) >= 3 else area_px
    area = area_px * s * s
    out = {
        "areaPx": round(area, 2),
        "holeAreaPx": round(max(0.0, hole_px) * s * s, 2),
        "components": int(count - 1),
        "perimeterPx": round(perimeter, 2),
        "centroid": [round(cx, 2), round(cy, 2)],
        "feretMaxPx": round(longest * s, 2),
        "feretMinPx": round(shortest * s, 2),
        "elongation": round(longest / shortest, 3) if shortest > 0 else None,
        "circularity": round(min(1.0, 4 * np.pi * area / (perimeter ** 2)), 3) if perimeter > 0 else None,
        "solidity": round(area_px / hull_area, 3) if hull_area > 0 else None,
        "small": area < SMALL_AREA,
    }
    if gray is not None:
        out["intensity"] = _intensity(mask, gray)
    out["edgeCut"] = _edge_cut(mask, disc, segmented)
    return out


def _intensity(mask: Mask, gray: np.ndarray) -> float | None:
    bits = np.asarray(mask.bits, bool)
    if mask.scale != 1.0:
        h, w = bits.shape
        bits = cv2.resize(bits.astype(np.uint8), (int(round(w * mask.scale)), int(round(h * mask.scale))),
                          interpolation=cv2.INTER_NEAREST).astype(bool)
    y0, x0 = int(mask.y0), int(mask.x0)
    region = gray[max(0, y0):y0 + bits.shape[0], max(0, x0):x0 + bits.shape[1]]
    bits = bits[max(0, -y0):max(0, -y0) + region.shape[0], max(0, -x0):max(0, -x0) + region.shape[1]]
    values = region[bits[:region.shape[0], :region.shape[1]]]
    return round(float(values.mean()), 2) if values.size else None


def _edge_cut(mask: Mask, disc, segmented) -> bool:
    x0, y0, x1, y1 = mask.box
    if segmented is not None:
        sx, sy, sw, sh = segmented
        if x0 <= sx or y0 <= sy or x1 >= sx + sw or y1 >= sy + sh:
            return True
    if disc is not None:
        cx, cy, r = disc
        ys, xs = np.nonzero(np.asarray(mask.bits, bool))
        far = np.hypot(mask.x0 + (xs + 0.5) * mask.scale - cx, mask.y0 + (ys + 0.5) * mask.scale - cy).max()
        return bool(far > EDGE_DISC * r)
    return False


def coverage(masks: list[Mask], disc: tuple[float, float, float] | None, size: tuple[int, int]) -> float | None:
    """The union of the masks over the eyepiece disc's area (overlaps count once), or over the
    picture when there is no disc."""
    w, h = size
    union = np.zeros((h, w), bool)
    for m in masks:
        bits = np.asarray(m.bits, bool)
        if m.scale != 1.0:
            bh, bw = bits.shape
            bits = cv2.resize(bits.astype(np.uint8), (int(round(bw * m.scale)), int(round(bh * m.scale))),
                              interpolation=cv2.INTER_NEAREST).astype(bool)
        y0, x0 = int(m.y0), int(m.x0)
        ys, xs = slice(max(0, y0), min(h, y0 + bits.shape[0])), slice(max(0, x0), min(w, x0 + bits.shape[1]))
        part = bits[ys.start - y0:ys.stop - y0, xs.start - x0:xs.stop - x0]
        union[ys, xs] |= part
    if disc is not None:
        cx, cy, r = disc
        yy, xx = np.ogrid[:h, :w]
        inside = (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r
        total = float(inside.sum())
        return round(float((union & inside).sum()) / total, 4) if total else None
    return round(float(union.sum()) / float(w * h), 4)
