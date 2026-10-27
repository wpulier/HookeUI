"""The last minute of the view, a frame every half second (for "what just happened?").

The hub keeps about 4 s of frames; this keeps references to one frame's JPEG every EVERY_S for
KEEP_S, with what the hub measured about it (the slide's offset, its epoch, whether it was
steady, its sharpness and eyepiece). Nothing is decoded or copied: a listener on Hub.on_frame,
as cheap as the coach's.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable

EVERY_S = 0.5
KEEP_S = 60.0


@dataclass
class ClipFrame:
    id: int
    jpeg: bytes
    wall_at: float
    dx: float
    dy: float
    epoch: int
    steady: bool
    focus: float
    fov: object


class ClipBuffer:
    def __init__(self, get_frame: Callable[[int], object]) -> None:
        self.get_frame = get_frame  # FrameSource.get: the frame's JPEG by id
        self.frames: deque[ClipFrame] = deque()

    def frame(self, info) -> None:
        """Every published frame (Hub.on_frame): keeps one every EVERY_S."""
        if self.frames and info.wall_at - self.frames[-1].wall_at < EVERY_S:
            return
        frame = self.get_frame(info.id)
        if frame is None:
            return
        self.frames.append(ClipFrame(info.id, frame.jpeg, info.wall_at, info.dx, info.dy, info.epoch,
                                     bool(getattr(info, "steady", True)), float(getattr(info, "focus", 0.0) or 0.0),
                                     getattr(info, "fov", None)))
        while self.frames and info.wall_at - self.frames[0].wall_at > KEEP_S:
            self.frames.popleft()

    def window(self, seconds: float, most: int = 8) -> list[ClipFrame]:
        """Up to `most` frames spread evenly over the last `seconds`, oldest first, the newest
        always included; of the frames near each moment, the sharpest."""
        if not self.frames:
            return []
        newest = self.frames[-1].wall_at
        inside = [f for f in self.frames if newest - f.wall_at <= seconds]
        if len(inside) <= most:
            return inside
        span = inside[-1].wall_at - inside[0].wall_at
        picked: list[ClipFrame] = []
        for i in range(most):
            at = inside[0].wall_at + span * i / (most - 1)
            near = [f for f in inside if abs(f.wall_at - at) <= span / (2 * (most - 1))] or \
                [min(inside, key=lambda f: abs(f.wall_at - at))]
            best = max(near, key=lambda f: f.focus) if i < most - 1 else inside[-1]
            if not picked or best.id != picked[-1].id:
                picked.append(best)
        return picked
