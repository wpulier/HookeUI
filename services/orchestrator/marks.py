"""Set-of-Mark rendering: draw numbered marks on candidates so a vision model can answer
"which number is the thing the user means?" instead of producing pixel coordinates."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .candidates import Candidate
from .coords import Crop
from .drawing import outline
from .fov import FieldOfView


@dataclass
class MarkedImage:
    jpeg: bytes
    crop: Crop  # region of the full frame the image shows (before resizing)
    marks: dict[int, Candidate]  # mark number -> candidate
    scale: float  # image pixels per full-frame pixel


def fov_crop(fov: FieldOfView | None, width: int, height: int, pad: float = 1.02) -> Crop:
    if fov is None:
        return Crop.full(width, height)
    r = fov.radius * pad
    x0, y0 = max(0, int(fov.cx - r)), max(0, int(fov.cy - r))
    x1, y1 = min(width, int(fov.cx + r)), min(height, int(fov.cy + r))
    return Crop(x0, y0, x1 - x0, y1 - y0)


def render_marks(
    image_bgr: np.ndarray,
    candidates: list[Candidate],
    crop: Crop,
    max_side: int = 1024,
    jpeg_quality: int = 88,
) -> MarkedImage:
    view = image_bgr[crop.y : crop.y + crop.height, crop.x : crop.x + crop.width]
    scale = min(1.0, max_side / max(crop.width, crop.height))
    if scale < 1.0:
        view = cv2.resize(view, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    else:
        view = view.copy()

    marks: dict[int, Candidate] = {}
    font = cv2.FONT_HERSHEY_SIMPLEX
    for number, cand in enumerate(candidates, start=1):
        x = (cand.x - crop.x) * scale
        y = (cand.y - crop.y) * scale
        if not (0 <= x < view.shape[1] and 0 <= y < view.shape[0]):
            continue
        marks[number] = cand
        # Thin ring well outside the object so the mark never hides what it points at.
        r = max(cand.radius * 2.2 * scale, 10)
        center = (int(round(x)), int(round(y)))
        cv2.circle(view, center, int(r) + 1, (0, 0, 0), 1, cv2.LINE_AA)
        cv2.circle(view, center, int(r), (255, 255, 255), 1, cv2.LINE_AA)
        label = str(number)
        (tw, th), _ = cv2.getTextSize(label, font, 0.5, 1)
        tx, ty = int(x + r + 2), int(y - r + th)
        cv2.rectangle(view, (tx - 2, ty - th - 2), (tx + tw + 2, ty + 3), (0, 0, 0), -1)
        cv2.putText(view, label, (tx, ty), font, 0.5, (255, 255, 255), 1, cv2.LINE_AA)

    ok, encoded = cv2.imencode(".jpg", view, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
    if not ok:
        raise RuntimeError("could not encode marked image")
    return MarkedImage(encoded.tobytes(), crop, marks, scale)


def encode_crop(image_bgr: np.ndarray, crop: Crop, max_side: int = 1024, jpeg_quality: int = 88) -> tuple[bytes, float]:
    view = image_bgr[crop.y : crop.y + crop.height, crop.x : crop.x + crop.width]
    scale = min(1.0, max_side / max(crop.width, crop.height))
    if scale < 1.0:
        view = cv2.resize(view, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", view, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
    if not ok:
        raise RuntimeError("could not encode crop")
    return encoded.tobytes(), scale


def render_regions(image_bgr: np.ndarray, crop: Crop, regions: list[list[list[list[float]]]], max_side: int = 768,
                   jpeg_quality: int = 90, labels: list[tuple[float, float]] | None = None,
                   thickness: int = 2) -> tuple[bytes, bytes, float]:
    """A close-up (`crop`) and the same close-up with numbered region outlines (1-based),
    for the dissect prompt and pointing "masks". Regions are polygons in frame pixels; each number
    sits at the point deepest inside its region, or at `labels[i]` (frame pixels) when given.
    Returns (clean, marked, scale)."""
    clean, scale = encode_crop(image_bgr, crop, max_side=max_side, jpeg_quality=jpeg_quality)
    view = cv2.imdecode(np.frombuffer(clean, np.uint8), cv2.IMREAD_COLOR)
    colors = [(255, 79, 163), (71, 181, 255), (120, 230, 120), (255, 210, 90), (200, 140, 255), (90, 220, 220),
              (255, 140, 90), (230, 230, 230)]
    for i, polygons in enumerate(regions):
        color = colors[i % len(colors)][::-1]  # RGB -> BGR
        pts_all = []
        for poly in polygons:
            pts = np.array([[(x - crop.x) * scale, (y - crop.y) * scale] for x, y in poly], np.int32)
            outline(view, [pts], color, thickness)  # on a dark casing: seen on any stain (drawing.py)
            pts_all.append(pts)
        if pts_all:
            allp = np.vstack(pts_all)
            m = np.zeros(view.shape[:2], np.uint8)
            cv2.fillPoly(m, pts_all, 1)
            dist = cv2.distanceTransform(np.pad(m, 1), cv2.DIST_L2, 3)[1:-1, 1:-1]
            y, x = np.unravel_index(int(dist.argmax()), dist.shape) if dist.max() > 0 else (int(allp[:, 1].mean()), int(allp[:, 0].mean()))
            if labels is not None and i < len(labels):
                x, y = int(round((labels[i][0] - crop.x) * scale)), int(round((labels[i][1] - crop.y) * scale))
            label = str(i + 1)
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.rectangle(view, (x - tw // 2 - 3, y - th // 2 - 4), (x + tw // 2 + 3, y + th // 2 + 4), (43, 26, 19), -1)
            cv2.putText(view, label, (x - tw // 2, y + th // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)
    ok, marked = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
    return clean, marked.tobytes(), scale


LABEL_FONT = 0.6  # render_regions' numbers (cv2 font scale, thickness 2)


def mask_labels(regions: list[dict], crop: Crop, max_side: int = 1024) -> list[tuple[float, float]]:
    """Where each automatic mask's number goes (pointing "masks"), in frame pixels: inside the
    region when its number fits there (gpu_worker/automask.label_point), otherwise just beside it,
    so a small thing is never hidden under its own number."""
    scale = min(1.0, max_side / max(crop.width, crop.height))
    out = []
    for n, region in enumerate(regions, start=1):
        (tw, th), _ = cv2.getTextSize(str(n), cv2.FONT_HERSHEY_SIMPLEX, LABEL_FONT, 2)
        lx, ly = region["label"]
        if float(region.get("inner") or 0.0) * scale >= max(tw, th) / 2 + 3:
            out.append((float(lx), float(ly)))
            continue
        pts = np.array([p for poly in region["polygons"] for p in poly], float)
        x0, y0, x1, y1 = pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max()
        half = (tw / 2 + 5) / scale
        x = x1 + half if (x1 + 2 * half - crop.x) * scale < crop.width * scale else x0 - half
        out.append((float(x), float((y0 + y1) / 2)))
    return out


def render_masks(image_bgr: np.ndarray, crop: Crop, regions: list[dict], max_side: int = 1024,
                 jpeg_quality: int = 88) -> tuple[bytes, bytes, float]:
    """The view and the same view with every automatic mask outlined and numbered (1-based),
    for pointing "masks" (orchestrator/masks.py). Returns (clean, marked, scale)."""
    return render_regions(image_bgr, crop, [r["polygons"] for r in regions], max_side=max_side,
                          jpeg_quality=jpeg_quality, labels=mask_labels(regions, crop, max_side), thickness=1)
