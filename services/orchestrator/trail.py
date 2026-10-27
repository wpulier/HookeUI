"""A followed object's trail: where it went on its own, from the follower's verified places.

docs/design/2026-10-07-the-partner.md §2.1; the UX document §1, "Motion the user can trust": a
trajectory is drawn from actual tracked coordinates and timestamps, older segments fade, the interval
and units are shown (pixels without a scale), slide motion is taken out, and lost, uncertain and
reacquired spans are honest: never joined, never filled.

Each step of the object follower (objtrack.py, about 10 a second) says per followed outline what
it found, as one of these states:
- "seen": its place is known. EdgeTAM found it where the slide's motion puts it, or where its own
  looks confirm it moved to, on a calm, clear view the motion tracker registered.
- "unsure": found, but its place is not known (a smeared, refocusing or unregistered view, or its
  looks did not agree).
- "missed": EdgeTAM did not find it this step.
- "lost": EdgeTAM lost it (dropped after misses), or its outline is being re-found;
- "stopped": no longer followed because newer outlines are (the trail just ends; nothing was lost).

A place is kept in slide coordinates: the outline's centre minus its anchor's slide offset, the
coordinates outlines are stored in (contract/overlay.json; the studio draws a point at
`p + offset_now`), stamped with its frame's time and id. The slide's own motion is taken out, so a
thing that stays put on a moving slide has a trail of one point. A trail is segments of "seen"
places within one motion epoch, broken at every other state, every change of epoch and every
second nothing observed it (a failed step, following switched off). Nothing joins two segments.

Only the follower's own moves are the object's motion. A correction is not: the hub's template
re-anchoring and drift correction nudging an outline (a few px after a pan), or the follower's own
small move while the slide travelled (objtrack.MAX_DRIFT_CORRECTION_PX, SLIDE_TRAVEL_PX). A
correction ends the open segment ("corrected") and the next starts where the outline is now: the
places before it stay where they were measured, and no path is counted across it (review of #155,
T1 and its re-checks). It is not a gap: the place stays known. The cost: a slow swimmer's small
moves during a pan are taken as corrections, so it has no trail there.

Kept in memory for WINDOW_S, for followed outlines only; a removed outline's trail goes with it.
It is derived only from writes the follower made through `apply` and places it verified. Drawn live
under its outline, it belongs to the outlines' presentation. When the user asks where a followed
thing went, `track_motion(of=...)` keeps its trail as a tracks item (motiontracks.py, M3.2): an index
entry and a "tracks" presentation, its segments' breaks as they were, saved and read back.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from . import spaces

WINDOW_S = 60.0  # how much of a trail is kept, drawn and spoken of
MIN_STEP_PX = 1.0  # a place this close to the last kept one is kept again only after KEEP_EVERY_S
KEEP_EVERY_S = 0.5
STALE_S = 1.0  # an open segment nothing observed for this long (a few follower steps) ends in a gap
MAX_SEGMENTS = 40
MAX_WIRE_POINTS = 240  # per trail sent to the studio (older places thinned, each segment's ends kept)
# A trail says it went somewhere only past both: this much path (its outline's centre wanders a few
# px from step to step as EdgeTAM redraws it, objtrack.MOVED_PX) and this far from where it started,
# farther than the hub's largest drift correction (hub.MAX_DRIFT_CORRECTION_PX), so corrections back
# and forth never add up to motion.
SHOWN_PX = 12.0
REACH_PX = 10.0
PUSH_EVERY_S = 0.25  # the studio is sent trails at most this often
STATES = ("seen", "unsure", "missed", "lost", "stopped")


@dataclass
class Segment:
    epoch: int
    start: str  # "first", "after-gap" (unsure, missed or unobserved), "reacquired" (lost), "after-correction"
    points: list[list[float]] = field(default_factory=list)  # [x, y, t, frame]: slide px, the frame's wall time and id
    end: str = "open"  # "open" (still going), "gap", "lost", "stopped" or "corrected"

    def wire(self, keep: int | None = None) -> dict:
        points = self.points
        if keep is not None and len(points) > keep >= 2:
            step = (len(points) - 1) / (keep - 1)
            points = [points[round(i * step)] for i in range(keep)]
        return {"epoch": self.epoch, "start": self.start, "end": self.end,
                "points": [[round(p[0], 1), round(p[1], 1), round(p[2], 2)] for p in points]}


@dataclass
class Trail:
    object_id: str
    segments: list[Segment] = field(default_factory=list)
    after: str = "first"  # how the next segment starts
    observed_at: float | None = None  # when a step last said anything about it
    corrections: list = field(default_factory=list)  # when each correction was taken out (never its motion), for its words

    def path_px(self) -> float:
        """Along the drawn segments only: never across a break."""
        return sum(math.dist(a[:2], b[:2]) for s in self.segments for a, b in zip(s.points, s.points[1:]))

    def reach_px(self) -> float:
        """How far it got from its first kept place, within that place's epoch, with each correction's jump
        taken out (as its path never counts one): only its own motion, and what a gap leaves unknown."""
        if not self.segments:
            return 0.0
        first = self.segments[0]
        x0, y0 = first.points[0][:2]
        shift = [0.0, 0.0]
        prev = None
        reach = 0.0
        for s in self.segments:
            if s.epoch != first.epoch or not s.points:
                continue
            if prev is not None and s.start == "after-correction":
                shift[0] += prev[0] - s.points[0][0]
                shift[1] += prev[1] - s.points[0][1]
            for p in s.points:
                reach = max(reach, math.dist((x0, y0), (p[0] + shift[0], p[1] + shift[1])))
            prev = s.points[-1][:2]
        return reach

    def seconds(self) -> float:
        """From its first kept place to its last, breaks included."""
        times = [p[2] for s in self.segments for p in s.points]
        return max(times) - min(times) if times else 0.0

    def known_seconds(self) -> float:
        """How long its place was known: the kept segments' own spans, never a break."""
        return sum(s.points[-1][2] - s.points[0][2] for s in self.segments if s.points)

    def breaks(self) -> tuple[int, int]:
        """Gaps (unsure, missed, unobserved or no longer followed) and losses after kept segments."""
        ended = [s.end for s in self.segments]
        return sum(e in ("gap", "stopped") for e in ended), sum(e == "lost" for e in ended)


def centre(obj) -> tuple[float, float] | None:
    """An outline's centre in slide coordinates (its anchor frame's pixels minus that frame's offset)."""
    pts = [p for poly in obj.polygons for p in poly]
    if not pts:
        return None
    x = sum(p[0] for p in pts) / len(pts) - obj.anchor_offset[0]
    y = sum(p[1] for p in pts) / len(pts) - obj.anchor_offset[1]
    return x, y


class Trails:
    def __init__(self, clock=None) -> None:
        self.clock = clock if clock is not None else (lambda: time.time())  # read at each call, never bound at import
        self.trails: dict[str, Trail] = {}
        self.version = 0
        self.sent_version = 0
        self.sent_at = 0.0
        self.stale_breaks = 0  # segments ended because nothing observed them (a loaded machine shows here: health)

    def observe(self, obj, state: str, epoch: int | None, *, wrote: bool = False, at: float | None = None,
                frame: int | None = None) -> None:
        """One follower step's finding about `obj` (its outline as it is after the step's writes):
        `wrote` when the follower itself moved it this step, `at` and `frame` the step's frame."""
        assert state in STATES, state
        now = self.clock() if at is None else at
        trail = self.trails.setdefault(obj.id, Trail(obj.id))
        last = trail.segments[-1] if trail.segments else None
        open_ = last is not None and last.end == "open"
        if open_ and trail.observed_at is not None and now - trail.observed_at > STALE_S:
            last.end, open_ = "gap", False  # nothing observed it in between: never joined across that
            self.version += 1
            self.stale_breaks += 1
        trail.observed_at = now
        if state == "seen" and epoch is not None and obj.anchor_epoch == epoch and (place := centre(obj)) is not None:
            if open_ and last.epoch == epoch and not wrote and math.dist(last.points[-1][:2], place) >= MIN_STEP_PX:
                # Moved, but not as the object's own motion: a correction. The places before stay where they
                # were measured; the next segment starts where the outline is now, never joined to them. A
                # further correction before any own motion moves that one place (a slow pan is many corrections:
                # never a segment each, which would push the measured ones out).
                trail.corrections.append(now)
                still = all(math.dist(p[:2], last.points[0][:2]) < MIN_STEP_PX for p in last.points)
                if last.start == "after-correction" and still:
                    # Nothing of its own since the last correction (only the same place kept again): one place.
                    last.points[:] = [[place[0], place[1], now, frame]]
                else:
                    last.end = "corrected"
                    trail.segments.append(Segment(epoch, "after-correction", [[place[0], place[1], now, frame]]))
                self.version += 1
            elif open_ and last.epoch == epoch:
                x, y, t = last.points[-1][:3]
                if math.dist((x, y), place) >= MIN_STEP_PX or now - t >= KEEP_EVERY_S:
                    last.points.append([place[0], place[1], now, frame])
                    self.version += 1
            else:
                if open_:
                    last.end = "gap"  # another epoch: its places are not comparable with these
                trail.segments.append(Segment(epoch, trail.after, [[place[0], place[1], now, frame]]))
                trail.after = "after-gap"
                self.version += 1
        else:
            self._end(trail, state)
        self._prune(trail, self.clock() if at is None else now)

    def _end(self, trail: Trail, state: str) -> None:
        ended = {"lost": "lost", "stopped": "stopped"}.get(state, "gap")
        last = trail.segments[-1] if trail.segments else None
        if last is not None and last.end == "open":
            last.end = ended
            self.version += 1
        elif ended == "lost" and last is not None and last.end == "gap" and trail.after != "reacquired":
            last.end = "lost"  # the misses closed it; the drop that followed says it was lost
            self.version += 1
        if ended == "lost":
            trail.after = "reacquired"  # remembered over later gaps: the next segment is found again

    def end(self, object_id: str, state: str) -> None:
        """Following stopped without a step's finding (a reset after a failed step: "gap"; following
        switched off: "stopped"): its open segment ends, so a later place is never joined to it."""
        trail = self.trails.get(object_id)
        if trail is not None:
            self._end(trail, state)

    def _prune(self, trail: Trail, now: float) -> None:
        for s in trail.segments:
            s.points = [p for p in s.points if now - p[2] <= WINDOW_S]
        trail.corrections = [t for t in trail.corrections if now - t <= WINDOW_S]
        kept = [s for s in trail.segments if s.points]
        if len(kept) != len(trail.segments):
            self.version += 1
        trail.segments = kept[-MAX_SEGMENTS:]

    def keep_only(self, ids) -> None:
        """Trails of outlines that are gone go with them."""
        gone = [oid for oid in self.trails if oid not in ids]
        for oid in gone:
            del self.trails[oid]
        if gone:
            self.version += 1

    def words(self, object_id: str, reading=None) -> str | None:
        """How far it went on its own in the last WINDOW_S, in plain words, or None when it did not go
        anywhere. `reading` is the scale (scale.Reading) or None: µm then say where the scale came from."""
        trail = self.trails.get(object_id)
        if trail is None:
            return None
        self._prune(trail, self.clock())
        path = trail.path_px()
        if path < SHOWN_PX or trail.reach_px() < REACH_PX:
            return None
        span, known = max(1, round(trail.seconds())), max(1, round(trail.known_seconds()))
        if reading is not None and getattr(reading, "um_per_px", None):
            from .scale import round_um

            um = round_um(path * reading.um_per_px)
            far = f"{um} µm" if reading.measured else f"about {um} µm (scale estimated from the objective)"
        else:
            far = f"{round(path)} px (no scale yet)"
        gaps, lost = trail.breaks()
        first = min((p[2] for seg in trail.segments for p in seg.points), default=None)
        n = sum(1 for t in trail.corrections if first is not None and t >= first)  # only within what its words cover
        corrected = f"; {n} re-registration{'s' if n != 1 else ''} of its outline left out" if n else ""
        if not gaps and not lost:
            return f"{far} over the slide in {span} s" + (f" ({corrected[2:]})" if corrected else "")
        # The path is only where its place was known: said as such, never as all it went (bench: a
        # swimmer's kept path was a fifth of its true one, the pans between being gaps).
        notes = ([f"{gaps} gap{'s' if gaps != 1 else ''}"] if gaps else []) + ([f"lost {lost}x"] if lost else [])
        if known >= span:  # rounded alike: "in the 1 s of the last 1 s" says nothing
            return f"{far} over the slide, its motion measured over about {known} s ({', '.join(notes)}{corrected})"
        return f"{far} over the slide in the {known} s of the last {span} s its motion was measured ({', '.join(notes)}{corrected})"

    def wire(self, reading=None) -> dict:
        now = self.clock()
        for trail in list(self.trails.values()):
            self._prune(trail, now)  # also those no longer observed: their old places age out
        out = []
        for trail in self.trails.values():
            total = sum(len(s.points) for s in trail.segments)
            if not total:
                continue
            share = MAX_WIRE_POINTS / total if total > MAX_WIRE_POINTS else None
            out.append({"id": trail.object_id,
                        "segments": [s.wire(max(2, int(len(s.points) * share)) if share else None) for s in trail.segments],
                        "words": self.words(trail.object_id, reading)})
        return {"type": "trails", "space": spaces.of_wire("trails"), "offset": [0, 0], "trails": out, "windowS": WINDOW_S}

    def due(self, now: float | None = None) -> bool:
        """Changed since last sent, and not sent in the last PUSH_EVERY_S."""
        now = time.monotonic() if now is None else now
        return self.version != self.sent_version and now - self.sent_at >= PUSH_EVERY_S

    def sent(self, now: float | None = None) -> None:
        self.sent_version = self.version
        self.sent_at = time.monotonic() if now is None else now
