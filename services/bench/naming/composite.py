"""Put a labelled microscope picture where the app would see it: inside an eyepiece circle on a
1280x720 frame, with the iPhone's vignetting, noise and JPEG quality (bench/naming).

Labelled sets are clean crops; our pictures are a phone looking down an eyepiece: a bright disc
in a black surround, darker towards the edge, a little noisy, re-encoded. The thing to name is
centred (these sets are one object per picture, centred), so its outline is a circle around the
middle unless the set gives a box.
"""
from __future__ import annotations

import cv2
import numpy as np

from orchestrator.fov import FieldOfView

WIDTH, HEIGHT = 1280, 720
RADIUS = 330  # the eyepiece circle on the June 14 and 2026-10-04 footage is ~650 px across
CENTRE = (WIDTH / 2, HEIGHT / 2)


def eyepiece(picture_bgr: np.ndarray | None, seed: int = 0, vignette: float = 0.45, noise: float = 3.0,
             jpeg_quality: int = 75, magnification: float = 1.0) -> np.ndarray:
    """The frame the app would get. `picture_bgr` None: a grey disc (nothing to see). With
    `magnification` below 1 (a lower objective), the picture's short side covers only that share of
    the disc, centred, and the rest of the disc is the picture mirrored around it, so the thing is
    small in the view."""
    rng = np.random.default_rng(seed)
    frame = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
    side = 2 * RADIUS
    if picture_bgr is None:
        disc = np.full((side, side, 3), 150, np.uint8)
    else:
        h, w = picture_bgr.shape[:2]
        scale = side * magnification / min(h, w)  # cover the disc, as the field of view covers the eyepiece
        target = (max(1, round(w * scale)), max(1, round(h * scale)))
        resized = cv2.resize(picture_bgr, (max(side, target[0]) if magnification >= 1 else target[0],
                                           max(side, target[1]) if magnification >= 1 else target[1]),
                             interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
        if resized.shape[0] < side or resized.shape[1] < side:
            pad_y, pad_x = max(0, side - resized.shape[0]), max(0, side - resized.shape[1])
            resized = np.pad(resized, ((pad_y // 2 + 1, pad_y - pad_y // 2 + 1), (pad_x // 2 + 1, pad_x - pad_x // 2 + 1),
                                       (0, 0)), mode="symmetric")
        y0, x0 = (resized.shape[0] - side) // 2, (resized.shape[1] - side) // 2
        disc = resized[y0 : y0 + side, x0 : x0 + side]
    yy, xx = np.mgrid[0:side, 0:side].astype(np.float32)
    r = np.hypot(xx - RADIUS + 0.5, yy - RADIUS + 0.5) / RADIUS
    shade = (1.0 - vignette * np.clip(r, 0, 1) ** 2)[..., None]
    lit = disc.astype(np.float32) * shade + rng.normal(0, noise, disc.shape)
    lit[r > 1.0] = 0
    cx, cy = int(CENTRE[0] - RADIUS), int(CENTRE[1] - RADIUS)
    frame[cy : cy + side, cx : cx + side] = np.clip(lit, 0, 255).astype(np.uint8)
    ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
    return cv2.imdecode(jpeg, cv2.IMREAD_COLOR)


def fov() -> FieldOfView:
    return FieldOfView(cx=CENTRE[0], cy=CENTRE[1], radius=float(RADIUS), confidence=1.0)


def object_outline(picture_shape: tuple[int, int] | None, bbox: list[float] | None = None,
                   fraction: float = 0.22, magnification: float = 1.0) -> tuple[list[list[list[float]]], tuple[float, float, float, float]]:
    """The outline the app would have drawn around the centred object: the set's box mapped into
    the frame when it has one, else a circle `fraction` of the disc across (times `magnification`,
    as in `eyepiece`). Returns (polygons, bbox)."""
    fraction *= magnification
    if bbox and picture_shape:
        h, w = picture_shape
        scale = 2 * RADIUS * magnification / min(h, w)
        ox, oy = CENTRE[0] - w * scale / 2, CENTRE[1] - h * scale / 2
        x0, y0, x1, y1 = (ox + bbox[0] * scale, oy + bbox[1] * scale, ox + bbox[2] * scale, oy + bbox[3] * scale)
        polygon = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
        return [polygon], (x0, y0, x1, y1)
    radius = fraction * RADIUS * 2 / 2
    points = [[CENTRE[0] + radius * np.cos(a), CENTRE[1] + radius * np.sin(a)] for a in np.linspace(0, 2 * np.pi, 32, endpoint=False)]
    return [[[float(x), float(y)] for x, y in points]], (CENTRE[0] - radius, CENTRE[1] - radius, CENTRE[0] + radius, CENTRE[1] + radius)
