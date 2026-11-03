"""The limits the user sets for the microscope's motors and light (services/scope_bridge/AGENTS.md).

Every action is checked against them before anything moves, in the bridge itself, whoever asked: a move that would leave
the stage's box, a single step larger than the user allows, an exposure outside its range. Light and large
acquisitions are never refused by a limit; they wait for the user's yes (`bridge.py`).

Stored as JSON (`limits.json` in the bridge's folder), changed only by the user's own words (the orchestrator's
`scope_limits` tool quotes them) or by hand. Standard library only: the bridge's own environment and the app's tests read
the same file.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

AXES = ("x", "y", "z")


@dataclass
class Limits:
    # The stage's box, absolute stage coordinates in µm (None: not set; then nothing on that axis may move).
    x: tuple[float, float] | None = None
    y: tuple[float, float] | None = None
    z: tuple[float, float] | None = None
    step_xy: float = 500.0  # the most one move may travel across the slide, µm
    step_z: float = 25.0  # the most one move may travel in focus, µm
    exposure_ms: tuple[float, float] = (1.0, 1000.0)
    # An acquisition above either of these waits for the user's yes (it takes long or makes many pictures).
    frames: int = 100
    seconds: float = 300.0
    notes: list[str] = field(default_factory=list)  # the user's words when they were set, newest last

    @classmethod
    def around(cls, x: float, y: float, z: float, *, xy: float = 2000.0, dz: float = 100.0) -> "Limits":
        """Conservative first limits: a box around where the stage is now (the user widens them)."""
        return cls(x=(x - xy, x + xy), y=(y - xy, y + xy), z=(z - dz, z + dz),
                   notes=[f"first limits: {xy:g} µm each way across and {dz:g} µm in focus from where the stage was"])

    def box(self, axis: str) -> tuple[float, float] | None:
        return getattr(self, axis)

    def inside(self, axis: str, value: float, tol: float = 1e-6) -> bool:
        box = self.box(axis)
        return box is not None and finite(value) and box[0] - tol <= value <= box[1] + tol

    def wire(self) -> dict:
        out = asdict(self)
        for k in ("x", "y", "z", "exposure_ms"):
            if out[k] is not None:
                out[k] = list(out[k])
        return out

    @classmethod
    def from_wire(cls, data: dict) -> "Limits":
        """Limits from JSON, every value checked (a bad file is an error, never a looser limit)."""
        if not isinstance(data, dict):
            raise ValueError("limits are an object")
        out = cls()
        for axis in AXES:
            value = data.get(axis)
            if value is None:
                setattr(out, axis, None)
                continue
            lo, hi = pair(value, f"{axis} limits")
            setattr(out, axis, (lo, hi))
        for key in ("step_xy", "step_z", "seconds"):
            if key in data:
                v = number(data[key], key)
                if v <= 0:
                    raise ValueError(f"{key} is more than 0")
                setattr(out, key, v)
        if "frames" in data:
            v = number(data["frames"], "frames")
            if v < 1 or v != int(v):
                raise ValueError("frames is a whole number, at least 1")
            out.frames = int(v)
        if "exposure_ms" in data:
            lo, hi = pair(data["exposure_ms"], "exposure_ms")
            if lo <= 0:
                raise ValueError("the shortest exposure is more than 0 ms")
            out.exposure_ms = (lo, hi)
        notes = data.get("notes") or []
        out.notes = [str(n)[:300] for n in notes][-20:] if isinstance(notes, list) else []
        return out


def finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def number(v, what: str) -> float:
    if not finite(v):
        raise ValueError(f"{what} is a number")
    return float(v)


def pair(v, what: str) -> tuple[float, float]:
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise ValueError(f"{what} are [lowest, highest]")
    lo, hi = number(v[0], what), number(v[1], what)
    if lo > hi:
        raise ValueError(f"{what}: the lowest is above the highest")
    return lo, hi


class LimitsFile:
    """The user's limits, kept beside the bridge's receipts."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> Limits | None:
        try:
            return Limits.from_wire(json.loads(self.path.read_text()))
        except FileNotFoundError:
            return None

    def save(self, limits: Limits) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(limits.wire(), indent=1))
        tmp.replace(self.path)
