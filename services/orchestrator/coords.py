"""Every coordinate conversion in one place.

Conventions:
- Pixel coordinates are (x, y) in the full-resolution source frame, origin top-left.
- Normalized coordinates are (x, y) in [0, 1] relative to the full frame.
- Gemini returns `box_2d` as [y0, x0, y1, x1] and points as [y, x], both on a 0-1000
  scale relative to the image that was SENT (often a crop), not the full frame.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Crop:
    """A rectangle of the full frame that was cut out (and possibly resized) for a model."""

    x: int
    y: int
    width: int
    height: int

    @classmethod
    def full(cls, width: int, height: int) -> "Crop":
        return cls(0, 0, width, height)

    def to_frame(self, cx: float, cy: float) -> tuple[float, float]:
        """Crop-local pixel (in crop units, before any resize) to full-frame pixel."""
        return self.x + cx, self.y + cy


def gemini_point_to_frame(point_yx: list[float], crop: Crop) -> tuple[float, float]:
    y, x = point_yx
    return crop.to_frame(x / 1000.0 * crop.width, y / 1000.0 * crop.height)


def gemini_box_to_frame(box_2d: list[float], crop: Crop) -> tuple[float, float, float, float]:
    """[y0, x0, y1, x1] on 0-1000 of the crop -> (x0, y0, x1, y1) full-frame pixels."""
    y0, x0, y1, x1 = box_2d
    fx0, fy0 = crop.to_frame(x0 / 1000.0 * crop.width, y0 / 1000.0 * crop.height)
    fx1, fy1 = crop.to_frame(x1 / 1000.0 * crop.width, y1 / 1000.0 * crop.height)
    return min(fx0, fx1), min(fy0, fy1), max(fx0, fx1), max(fy0, fy1)


def frame_box_to_gemini(box: tuple[float, float, float, float], crop: Crop) -> list[int]:
    """(x0, y0, x1, y1) full-frame pixels -> [y0, x0, y1, x1] on 0-1000 of the crop."""
    x0, y0, x1, y1 = box
    return [
        round((y0 - crop.y) / crop.height * 1000),
        round((x0 - crop.x) / crop.width * 1000),
        round((y1 - crop.y) / crop.height * 1000),
        round((x1 - crop.x) / crop.width * 1000),
    ]


def to_normalized(x: float, y: float, width: int, height: int) -> tuple[float, float]:
    return x / width, y / height


def from_normalized(nx: float, ny: float, width: int, height: int) -> tuple[float, float]:
    return nx * width, ny * height
