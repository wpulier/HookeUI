"""Outlines drawn into a picture so they can be seen on any stain.

Live 2026-10-11 (the outline-fit review, docs/bench/outline-fit.md): a pink outline on a magenta-stained section all
but vanished, for the user and for the models shown it (naming, the outline check, the review's judge). Every outline
the app draws into a picture now has a dark casing under its line, so a line of the stain's own colour still stands
out by its edges; its colour stays what the prompts and the studio say ("outlined in magenta").

`contrast_colour` picks, from a small palette, the colour farthest from the stain along an outline (CIELAB distance to
the median colour of a band around it): for pictures whose words can name the colour (the outline-fit bench's judge
and review pictures), never for the app's own prompts, which name magenta.
"""
from __future__ import annotations

import cv2
import numpy as np

CASING_BGR = (12, 10, 9)  # near-black, under every line
# Colours that read as outlines, by name (BGR), the studio's palette (apps/studio/src/view.ts OUTLINE_COLORS).
PALETTE = {"magenta": (163, 79, 255), "yellow": (74, 240, 255), "green": (143, 255, 89), "cyan": (255, 227, 63),
           "white": (255, 255, 255)}


def points(polygons, offset=(0.0, 0.0), scale: float = 1.0) -> list[np.ndarray]:
    """Polygons in frame px as OpenCV's integer point arrays, moved by -`offset` and scaled."""
    ox, oy = offset
    return [np.round((np.asarray(p, float) - (ox, oy)) * scale).astype(np.int32).reshape(-1, 1, 2)
            for p in polygons or [] if len(p) >= 2]


def outline(image: np.ndarray, pts: list[np.ndarray], colour, width: int, casing: bool = True) -> None:
    """Draw closed polylines (holes included: a ring is drawn as its two edges) on `image` in place: a dark casing two
    pixels wider each side first (a picture is compressed and resized before a model sees it), then the line."""
    if not pts:
        return
    if casing:
        cv2.polylines(image, pts, True, CASING_BGR, width + 4, cv2.LINE_AA)
    cv2.polylines(image, pts, True, tuple(int(c) for c in colour), width, cv2.LINE_AA)


def contrast_colour(image: np.ndarray, polygons, band: int = 6) -> tuple[str, tuple[int, int, int]]:
    """The palette colour farthest from what lies along the outline (the median colour of a band `band` px either side
    of its line), as (name, BGR)."""
    h, w = image.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    pts = points(polygons)
    if pts:
        cv2.polylines(mask, pts, True, 1, band * 2 + 1)
    under = image[mask.astype(bool)] if mask.any() else image.reshape(-1, 3)
    lab = cv2.cvtColor(np.median(under, axis=0).astype(np.uint8).reshape(1, 1, 3), cv2.COLOR_BGR2LAB).astype(float)[0, 0]

    def distance(bgr) -> float:
        other = cv2.cvtColor(np.array(bgr, np.uint8).reshape(1, 1, 3), cv2.COLOR_BGR2LAB).astype(float)[0, 0]
        return float(np.linalg.norm(lab - other))

    name = max(PALETTE, key=lambda n: distance(PALETTE[n]))
    return name, PALETTE[name]
