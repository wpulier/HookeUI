"""Instant first guess for "what's the little dark dot near the top?" (pointing: snap-first).

The words of a question are matched against the detected candidate spots (position in the
eyepiece, size, dark or bright) with no model call, so an amber "looking" ring can appear
within milliseconds. The vision model's box follows a second or two later and replaces it.
The ring is a guess about where to look, never a name: it is drawn as "Looking…".
"""
from __future__ import annotations

import re

import numpy as np

from .candidates import Candidate

WORDS = {
    "top": re.compile(r"\b(top|upper|above|up)\b", re.I),
    "bottom": re.compile(r"\b(bottom|lower|below|down)\b", re.I),
    "left": re.compile(r"\bleft\b", re.I),
    "right": re.compile(r"\bright\b", re.I),
    "middle": re.compile(r"\b(middle|centre|center|central)\b", re.I),
    "edge": re.compile(r"\b(corner|edge|side|rim)\b", re.I),
    "small": re.compile(r"\b(little|tiny|small|speck|specks|dot|dots|spot|spots|point|smudge|smudges|mark|marks)\b", re.I),
    "large": re.compile(r"\b(big|large|huge)\b", re.I),
    "dark": re.compile(r"\b(dark|black|darker)\b", re.I),
    "bright": re.compile(r"\b(bright|white|pale|light|shiny|glowing)\b", re.I),
}


def cues(text: str) -> set[str]:
    return {name for name, pattern in WORDS.items() if pattern.search(text or "")}


def pick_spot(text: str, candidates: list[Candidate], fov) -> Candidate | None:
    """The detected spot that best fits the words, or None when guessing would mislead.

    Candidates are small blobs, so a guess is only made when the question is about
    something small (dot, speck, spot...) and says where or how dark it is. Larger shapes
    ("the oval on the right") are not candidates: on the labelled questions, guessing for
    them was wrong 6 times in 8 (services/bench/first_guess.py), so they wait for the model."""
    found = cues(text)
    if "small" not in found or not (found - {"small"}) or not candidates:
        return None
    if fov is not None:
        cx, cy, r = fov.cx, fov.cy, fov.radius
    else:
        xs, ys = [c.x for c in candidates], [c.y for c in candidates]
        cx, cy, r = float(np.mean(xs)), float(np.mean(ys)), max(1.0, float(np.ptp(xs) + np.ptp(ys)) / 2)
    radii = np.array([c.radius for c in candidates])
    small, large = float(np.percentile(radii, 30)), float(np.percentile(radii, 70))
    best, best_score = None, -np.inf
    for c in candidates:
        u, v = (c.x - cx) / r, (c.y - cy) / r
        score = 0.0
        score += -2 * v if "top" in found else 0
        score += 2 * v if "bottom" in found else 0
        score += -2 * u if "left" in found else 0
        score += 2 * u if "right" in found else 0
        score += -2 * (u * u + v * v) if "middle" in found else 0
        score += 1.5 * (u * u + v * v) if "edge" in found else 0
        score += (1.0 if c.radius <= small else -0.5) if "small" in found else 0
        score += (1.0 if c.radius >= large else -0.5) if "large" in found else 0
        score += (1.5 if c.contrast < 0 else -1.5) if "dark" in found else 0
        score += (1.5 if c.contrast > 0 else -1.5) if "bright" in found else 0
        score += 0.2 * min(c.score, 2.0)  # a clearer spot wins ties
        if score > best_score:
            best, best_score = c, score
    return best


def ring(c: Candidate, scale: float = 1.6, points: int = 28) -> list[list[float]]:
    r = max(c.radius * scale, 8.0)
    return [[c.x + r * float(np.cos(a)), c.y + r * float(np.sin(a))] for a in np.linspace(0, 2 * np.pi, points, endpoint=False)]
