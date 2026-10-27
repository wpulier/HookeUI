"""The frames of the last turn of the focus knob, kept for a focus stack (enhance.focus_stack).

A sweep is what the coach calls one (coach.py, the same thresholds): the slide is still and its
sharpness changes by more than SWEEP_CHANGE within SWEEP_WINDOW_S; it is over once the
sharpness has not changed for SWEEP_IDLE_S, or the slide moves. While it lasts, references to
the frames' JPEG bytes (the ones the camera ring already holds, nothing decoded or copied) are
kept with their slide offset and sharpness, about KEEP_FPS a second, at most MAX_FRAMES (a long
sweep is thinned evenly, so it still spans the whole turn). The second before the sweep was
noticed is kept too: the turn started there.

A sweep is kept until the next one starts, or for KEEP_S after it ended.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from .coach import SWEEP_IDLE_S, SWEEP_WINDOW_S, is_sweep

KEEP_FPS = 15.0
MAX_FRAMES = 60
KEEP_S = 120.0
MIN_FRAMES = 3  # fewer than this is not a sweep worth stacking


@dataclass
class SweepFrame:
    id: int
    jpeg: bytes
    dx: float
    dy: float
    focus: float
    epoch: int
    wall_at: float
    # The eyepiece disc (centre and radius, FrameInfo.disc; radius 0 when not measured): the phone's
    # own refocusing changes its size, and the stack scales such frames back (enhance.focus_stack).
    disc: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass
class Sweep:
    frames: list[SweepFrame] = field(default_factory=list)
    started_at: float = 0.0
    ended_at: float | None = None  # None while the knob is still turning

    @property
    def span_s(self) -> float:
        return self.frames[-1].wall_at - self.frames[0].wall_at if self.frames else 0.0


class SweepBuffer:
    def __init__(self, get_frame: Callable[[int], object], clock: Callable[[], float] = time.time) -> None:
        self.get_frame = get_frame  # FrameSource.get: the frame's JPEG by id
        self.clock = clock
        self.recent: deque[tuple[float, float]] = deque()  # (time, focus), as the coach keeps them
        self.preroll: deque[SweepFrame] = deque()  # kept frames of the last SWEEP_WINDOW_S
        self.current: Sweep | None = None  # the knob is turning
        self.last: Sweep | None = None  # the latest finished sweep
        self.last_change = 0.0
        self.interval = 1.0 / KEEP_FPS

    def frame(self, info) -> None:
        """Every published frame (Hub.on_frame): cheap, nothing decoded."""
        now = info.wall_at
        if not info.steady or info.focus <= 0 or (self.preroll and self.preroll[-1].epoch != info.epoch):
            self._end(now)
            self.recent.clear()
            self.preroll.clear()
            if not info.steady or info.focus <= 0:
                return
        self.recent.append((now, info.focus))
        while self.recent and now - self.recent[0][0] > SWEEP_WINDOW_S:
            self.recent.popleft()
        changing = is_sweep([f for _, f in self.recent])
        kept = self._keep(info, now)
        if changing:
            self.last_change = now
            if self.current is None:  # the turn started within the last window: keep that too
                self.current = Sweep(list(self.preroll), self.preroll[0].wall_at if self.preroll else now)
                self.interval = 1.0 / KEEP_FPS
                kept = None
        if self.current is not None:
            if now - self.last_change > SWEEP_IDLE_S:
                self._end(now)
            elif kept is not None:
                self._add(kept)

    def _keep(self, info, now: float) -> SweepFrame | None:
        """The frame, if it is due (about KEEP_FPS a second), also in the pre-roll."""
        latest = self.current.frames[-1].wall_at if self.current is not None and self.current.frames else \
            (self.preroll[-1].wall_at if self.preroll else None)
        if latest is not None and now - latest < self.interval * 0.9:
            return None
        frame = self.get_frame(info.id)
        if frame is None:
            return None
        fov, radius = getattr(info, "fov", None), getattr(info, "disc", 0.0) or 0.0
        disc = (float(fov.cx), float(fov.cy), float(radius)) if fov is not None and radius > 0 else (0.0, 0.0, 0.0)
        kept = SweepFrame(info.id, frame.jpeg, info.dx, info.dy, info.focus, info.epoch, now, disc)
        self.preroll.append(kept)
        while self.preroll and now - self.preroll[0].wall_at > SWEEP_WINDOW_S:
            self.preroll.popleft()
        return kept

    def _add(self, kept: SweepFrame) -> None:
        frames = self.current.frames
        frames.append(kept)
        if len(frames) > MAX_FRAMES:  # a long turn: every other frame, half as often from now on
            thinned = frames[::2]
            if thinned[-1] is not frames[-1]:
                thinned.append(frames[-1])
            self.current.frames = thinned
            self.interval *= 2

    def _end(self, now: float) -> None:
        if self.current is None:
            return
        self.current.ended_at = now
        if len(self.current.frames) >= MIN_FRAMES:
            self.last = self.current
        self.current = None

    def latest(self) -> Sweep | None:
        """The sweep to stack: the one going on now if it has enough frames, else the last one,
        if it ended less than KEEP_S ago."""
        if self.current is not None and len(self.current.frames) >= MIN_FRAMES:
            return self.current
        if self.last is not None and self.clock() - (self.last.ended_at or 0.0) <= KEEP_S:
            return self.last
        return None
