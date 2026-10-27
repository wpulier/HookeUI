"""Propose "things you might point at" inside the eyepiece disc.

These are cheap, class-agnostic regions (dots, blobs, outlined shapes) found a few times a
second. They exist so that a question like "what's the little dot in the corner?" can be
answered by choosing among numbered candidates instead of asking a language model for
pixel coordinates. A candidate is only a region that stands out from its surroundings;
what it is gets decided later by a vision model.

Each candidate keeps a stable id while the slide moves: positions are compared in
specimen coordinates (frame position minus the motion offset).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .fov import FieldOfView


# Fraction of the eyepiece radius searched for spots. The bright halo and vignetting at the
# rim are fixed to the camera, not the specimen, so they must never become candidates.
RIM_KEEP_OUT = 0.88


@dataclass
class Candidate:
    id: int
    x: float  # centre, full-frame pixels, in the frame it was last seen
    y: float
    radius: float
    contrast: float  # signed: negative = darker than surroundings
    score: float
    frame_id: int
    sx: float = 0.0  # specimen coordinates (motion-compensated)
    sy: float = 0.0
    hits: int = 1

    def box(self) -> tuple[float, float, float, float]:
        r = max(self.radius * 1.4, 6.0)
        return self.x - r, self.y - r, self.x + r, self.y + r


@dataclass
class CandidateTracker:
    analysis_width: int = 640
    max_candidates: int = 40
    match_px: float = 18.0
    min_radius_px: float = 3.0
    _next_id: int = 1
    current: list[Candidate] = field(default_factory=list)

    def update(
        self, frame_id: int, gray: np.ndarray, fov: FieldOfView | None, offset: tuple[float, float]
    ) -> list[Candidate]:
        found = propose(gray, fov, self.analysis_width, self.max_candidates, self.min_radius_px)
        ox, oy = offset
        matched: list[Candidate] = []
        previous = list(self.current)
        for cand in found:
            sx, sy = cand.x - ox, cand.y - oy
            best, best_d = None, self.match_px
            for prev in previous:
                d = float(np.hypot(prev.sx - sx, prev.sy - sy))
                if d < best_d and np.sign(prev.contrast) == np.sign(cand.contrast):
                    best, best_d = prev, d
            if best is not None:
                previous.remove(best)
                cand.id, cand.hits = best.id, best.hits + 1
            else:
                cand.id = self._next_id
                self._next_id += 1
            cand.frame_id, cand.sx, cand.sy = frame_id, sx, sy
            matched.append(cand)
        self.current = matched
        return matched


def propose(
    gray: np.ndarray,
    fov: FieldOfView | None,
    analysis_width: int = 640,
    max_candidates: int = 40,
    min_radius_px: float = 3.0,
) -> list[Candidate]:
    """Multi-scale difference-of-Gaussians blob detection inside the disc."""
    height, width = gray.shape[:2]
    scale = analysis_width / width
    small = cv2.resize(gray, (analysis_width, int(round(height * scale))), interpolation=cv2.INTER_AREA).astype(np.float32)

    if fov is not None:
        valid = np.zeros(small.shape, np.uint8)
        cv2.circle(valid, (int(fov.cx * scale), int(fov.cy * scale)), int(fov.radius * scale * RIM_KEEP_OUT), 255, -1)
    else:
        valid = np.full(small.shape, 255, np.uint8)
    inside = small[valid > 0]
    # Normalize by the disc's own statistics; the black surround would flatten everything.
    small = (small - float(np.median(inside))) / (float(inside.std()) + 1e-6)

    sigmas = [1.5, 2.5, 4.0, 6.5, 10.0]
    found: list[tuple[float, float, float, float]] = []  # x, y, sigma, response (small px)
    blurred = [cv2.GaussianBlur(small, (0, 0), s) for s in sigmas]
    for i, sigma in enumerate(sigmas[:-1]):
        dog = blurred[i] - blurred[i + 1]  # positive = brighter than surroundings
        for sign in (1.0, -1.0):
            response = dog * sign
            local_max = response == cv2.dilate(response, np.ones((5, 5), np.uint8))
            values = response[valid > 0]
            noise = float(np.median(np.abs(values - np.median(values)))) * 1.4826 + 1e-6
            threshold = max(5.0 * noise, float(np.percentile(values, 99.0)))
            ys, xs = np.nonzero(local_max & (response > threshold) & (valid > 0))
            for x, y in zip(xs, ys):
                found.append((float(x), float(y), sigma, float(dog[y, x])))

    # Strongest first, suppress overlaps across scales. Dark spots (debris, dust, dense
    # bodies) are rarer than bright ones in transmitted light but are what people ask
    # about, so up to a third of the slots are reserved for them.
    found.sort(key=lambda f: abs(f[3]) * f[2] ** 0.5, reverse=True)
    kept: list[tuple[float, float, float, float]] = []
    for f in found:
        r = f[2] * 1.414
        if all(np.hypot(f[0] - k[0], f[1] - k[1]) > max(r, k[2] * 1.414) for k in kept):
            kept.append(f)
    dark = [k for k in kept if k[3] < 0][: max_candidates // 3]
    bright = [k for k in kept if k[3] >= 0][: max_candidates - len(dark)]
    kept = sorted(dark + bright, key=lambda f: abs(f[3]) * f[2] ** 0.5, reverse=True)

    candidates = []
    for x, y, sigma, resp in kept:
        radius = sigma * 1.414 / scale
        if radius < min_radius_px:
            continue
        candidates.append(
            Candidate(
                id=0,
                x=x / scale,
                y=y / scale,
                radius=radius,
                contrast=resp,
                score=abs(resp) * sigma**0.5,
                frame_id=-1,
            )
        )
    return candidates


def describe_position(c: Candidate, fov: FieldOfView | None, width: int, height: int) -> str:
    """Plain-language position ("upper right, near the edge") for deterministic matching
    against what the user said and for the language model's context."""
    cx, cy, r = (fov.cx, fov.cy, fov.radius) if fov else (width / 2, height / 2, min(width, height) / 2)
    nx, ny = (c.x - cx) / r, (c.y - cy) / r
    vertical = "upper" if ny < -0.33 else "lower" if ny > 0.33 else ""
    horizontal = "left" if nx < -0.33 else "right" if nx > 0.33 else ""
    where = " ".join(p for p in (vertical, horizontal) if p) or "centre"
    if np.hypot(nx, ny) > 0.7:
        where += ", near the edge"
    size = "tiny" if c.radius < 6 else "small" if c.radius < 14 else "large"
    tone = "dark" if c.contrast < 0 else "bright"
    return f"{size} {tone} spot, {where}"
