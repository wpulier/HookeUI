"""Did an image model keep the microscope photo as it was?

Annotations are thin lines, arrows and light label boxes; redrawing changes the picture's
broad structure. So both pictures are blurred (which removes thin lines), light or very dark
annotation pixels are masked out, and the 99th percentile of the remaining difference is the
score. Calibrated on spike S4 (docs/bench/spike-s4-annotate.md): faithful gpt-image-2.5
outputs score 7.8-15.3; Gemini's invented pore 21.1, its softened crop 28.2, and a
researcher annotation that drew idealised guard cells 54.
"""
from __future__ import annotations

import cv2
import numpy as np

ALTERED_ABOVE = 16.0
SIDE = 1024


def square_pad(jpeg: bytes, side: int = SIDE) -> tuple[bytes, tuple[int, int, int, int]]:
    """The photo centred on a dark square (no stretching); returns the PNG and where the
    photo sits in it (x, y, w, h)."""
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    h, w = image.shape[:2]
    scale = side / max(h, w)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
    canvas = np.full((side, side, 3), 24, np.uint8)
    x, y = (side - nw) // 2, (side - nh) // 2
    canvas[y : y + nh, x : x + nw] = resized
    return cv2.imencode(".png", canvas)[1].tobytes(), (x, y, nw, nh)


def pad_to(jpeg: bytes, width: int, height: int) -> bytes:
    """The photo centred on a dark canvas of the given size (never stretched), as PNG."""
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    h, w = image.shape[:2]
    scale = min(width / w, height / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
    canvas = np.full((height, width, 3), 24, np.uint8)
    x, y = (width - nw) // 2, (height - nh) // 2
    canvas[y : y + nh, x : x + nw] = resized
    return cv2.imencode(".png", canvas)[1].tobytes()


def alteration(before_png: bytes, after_png: bytes, region: tuple[int, int, int, int]) -> float:
    """99th-percentile difference of the blurred photo region, annotation pixels excluded."""
    before = cv2.imdecode(np.frombuffer(before_png, np.uint8), cv2.IMREAD_COLOR)
    after = cv2.imdecode(np.frombuffer(after_png, np.uint8), cv2.IMREAD_COLOR)
    if after.shape[:2] != before.shape[:2]:
        after = cv2.resize(after, (before.shape[1], before.shape[0]), interpolation=cv2.INTER_AREA)
    x, y, w, h = region
    before, after = before[y : y + h, x : x + w], after[y : y + h, x : x + w]
    g0 = cv2.cvtColor(before, cv2.COLOR_BGR2GRAY).astype(np.float32)
    g1 = cv2.cvtColor(after, cv2.COLOR_BGR2GRAY).astype(np.float32)
    hsv = cv2.cvtColor(after, cv2.COLOR_BGR2HSV)
    ink = ((hsv[..., 2] > 215) & (hsv[..., 1] < 70)) | (hsv[..., 2] < 40)
    ink = cv2.dilate(ink.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    sigma = max(1.0, w / 128)
    diff = np.abs(cv2.GaussianBlur(g0, (0, 0), sigma) - cv2.GaussianBlur(g1, (0, 0), sigma))[~ink]
    return float(np.percentile(diff, 99)) if diff.size else 0.0
