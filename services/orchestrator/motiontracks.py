"""Motion tracks as evidence (M3.2 of docs/design/2026-10-06-captures-operations-evidence.md; contracts 1,
3, 8 and 10; Amendment 2, point 6; ADR 0010).

What moves, measured, is kept as any evidence a request made is:
- `track_motion` runs the "motion" operation (operations.MOTION: one implementation, an algorithm,
  movement.py) through the runner, so it has an Execution (origin, policy, attempts, timings). What it
  measured becomes a tracks item (`m1`): every track in slide coordinates (a point's frame pixels minus
  its frame's slide offset, as outlines are kept: drawn at `p + offset_now`) with its times and frames
  (each frame's `(boot, seq)` where the bridge said them), the motion epoch, the frames' span, the measured
  summary (tracks counted, speeds with their unit, the threshold, a shared drift, the slide's own speed),
  the scale it used and where it came from, the producer with its version and configuration, and the
  last frame with the tracks drawn on it by the app (its captured view).
- A followed outline's trail (trail.py) is kept the same way when the user asks where it went: its
  segments as the follower verified them, each break as it was (a gap, a loss, a correction, another
  epoch). Nothing joins two segments.
- Saved as populations are: atomically (`.tmp`, fsync, rename), the manifest last with a schema version
  and checksums, in `<paths.evidence>/<day>/m<n>/`, filed in the day's journal and read back at start;
  "saved" only once the journal files it, after the rename. Until then its view, picture and points are held in the unsaved-evidence
  allowance (contract 1); once saved its pixels and points leave memory and are read again when needed.
- `TrackLayer` shows one item over the live view, drawn only while its place is verified (contract 3):
  the same motion epoch (or a verified rejoin), the same geometry and objective, and its own view's
  pixels around the tracks matching where the slide's move puts them (a region NCC of at least 0.8, the
  population-wide check's number). Otherwise it is shown on its captured view, labelled as that.

A reuse is never said to be a fresh run: a second request that shared the measuring job uses the item
the job made, and says so.

An explicitly requested current-camera continuation is separate from that saved
measurement. Its image-coordinate trails include camera/stage motion and travel
with the exact producing JPEG; they never use the historical template matcher.
Visible presentations own one bounded spot/link producer and held latest sample.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import os
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from . import spaces
from .brain.registry import ToolFailed

SCHEMA = "tracks/1"
POINT_FIELDS = ("x", "y", "t", "frame")  # each point: slide px, wall time (s), this run's frame id (-1 unknown)
MAX_WIRE_POINTS = 60  # per track sent to the studio (older places thinned, each segment's ends kept)
MAX_WIRE_TRACKS = 400  # tracks sent to the studio (the fastest first); the rest are kept, said in the wire
# The live check (contract 3). A region of the item's own view around its tracks, at half resolution,
# must match the picture now best at the place the slide's move puts it, at least TRACKS_SCORE (the
# population-wide check's NCC), within AT_PLACE_PX of it (the slide's offset can be a few px off after a pan:
# finding 39); its peak's offset is drawn as the correction.
CHECK_EVERY_S = 0.3
CHECK_VALID_S = 2.0  # a check holds this long and this far, only for a pass that could not check at all
CHECK_TRAVEL_PX = 16.0
TRACKS_SCORE = 0.8
AT_PLACE_PX = 4.0
SCALE = 0.5
SEARCH = 6  # half-resolution px searched around the predicted place
PAD_PX = 32  # full px of the view around the tracks in the region checked
MIN_REGION_PX = 96  # the region is at least this wide and high (full px), centred on the tracks
MIN_TEXTURE = 2.0  # a region flatter than this (grey levels, std) cannot be checked by its pixels
MIN_MASKED_PX = 400  # half-resolution pixels of the region inside the eyepiece, then and now, a check needs
EDGE = "they lie near the edge of the eyepiece, where their place can't be checked"
DRAWN = "verified in place"  # what the studio reports of tracks it draws on the live view (main.ts screenItems)
DRAWN_WAIT_S = 1.5  # how long "drawn" waits for the studio's report before their own view is shown instead
COLUMNS = {"speed": "net", "net": "net", "net speed": "net", "fast": "net", "fastest": "net", "path": "path",
           "path speed": "path", "speed along the path": "path", "straightness": "straightness", "straight": "straightness",
           "straighter": "straightness"}
COLUMN_WORDS = {"net": "net speed", "path": "speed along its path", "straightness": "straightness"}
DEFAULT_STYLE = {"opacity": 1.0, "colour": "column", "column": "net"}


def hhmm(at: float | None) -> str:
    return time.strftime("%H:%M", time.localtime(at or time.time()))


def hhmmss(at: float | None) -> str:
    return time.strftime("%H:%M:%S", time.localtime(at or time.time()))


@dataclass
class Tracks:
    """One kept measurement of motion: a table of tracks (rows), each a list of segments of points."""
    id: str
    how: str  # "measured" (movement.py) | "followed" (the follower's trail)
    operation: str
    implementation: str
    made_by: dict
    capture_id: int
    view: dict  # the capture's facts (population.view_facts): epoch, dx, dy, disc, width, height, at, seq, boot, run
    scale_id: dict | None
    span: dict  # {"from", "to", "seconds", "frames", "fps"}
    summary: dict
    scale: dict  # {"umPerPx", "basis": measured | estimated | None, "objective"}
    rows: list  # per track: {"id", "values", "segments": [{"epoch", "start", "end", "n"}]}
    points: list | None  # per track, per segment: (n, 4) float64 POINT_FIELDS; None while it is on disk only
    frames: list = field(default_factory=list)  # [[frame, boot, seq, t, dx, dy]] the frames it was measured on
    producer: dict = field(default_factory=dict)
    transformations: dict = field(default_factory=dict)
    ticket: str | None = None
    thread: str | None = None
    day: str = ""  # the calendar date folder it is filed under (evidence/<day>/m2)
    made_at: float = field(default_factory=time.time)
    status: str = "saving"  # "saving" | "saved" | "failed" | "unsaved"
    status_why: str = ""
    path: Path | None = None
    capture: Any = None  # the Capture while its pixels are resident (dropped once saved)
    picture: bytes | None = None  # the captured view: the last frame with the tracks drawn (dropped once saved)
    hold: Any = None  # the capture hold (and reservation) while unsaved
    object_id: str | None = None  # a followed outline's id (how "followed")
    label: str = ""  # what that outline was called when its trail was kept
    # The day's generation its conversation belongs to (day/store.py): after "start today afresh" a new t1 is
    # not the old one, so an item of the old one is never listed as the new one's (the partner's review, S2).
    generation: int | None = None
    # The session it was made in (ADR 0017: the request's, else the one open); read back, its journal's. "" only for a
    # stand-in made with no session store.
    session: str = ""

    @property
    def made_for(self) -> str | None:
        """Who it was measured for when not for the user's own requests: a researcher's own reading
        (research/hands.py). Never "the tracks" a request means unless it is shown or named, as a population
        made for someone else (Population.made_for)."""
        made = self.made_by or {}
        # Only a background run is someone else's (Population.made_for reads it the same way): a "for" on the
        # user's own request's run would otherwise hide it from "the motion" they asked for.
        return str(made["for"]) if made.get("origin") == "background" and made.get("for") else None

    @property
    def title(self) -> str:
        """Its plain title (Amendment 4, item 1): "Motion · 18:04, 12 s" ("Motion, for Researcher 2 · …")."""
        what = "Motion" if self.how == "measured" else "Trail"
        what += f", for {self.made_for}" if self.made_for else ""
        times = self.measurement_times()
        when = hhmm(times[0]) if times is not None else "time unknown"
        return f"{what} · {when}, {max(1, round(float(self.span.get('seconds') or 0.0)))} s"

    @property
    def unit(self) -> str:
        return str(self.summary.get("unit") or "px/s")

    def measurement_times(self) -> tuple[float, float] | None:
        start, end = self.span.get("from"), self.span.get("to")
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                   and math.isfinite(value) and value > 0 for value in (start, end)):
            return None
        return (float(start), float(end)) if end >= start else None

    def interval(self) -> str:
        times = self.measurement_times()
        return f"{hhmmss(times[0])}-{hhmmss(times[1])}" if times else "measurement interval unknown"

    def measurement_basis(self, now: float) -> str:
        """Age of retained evidence, independent of where its presentation is drawn."""
        times = self.measurement_times()
        if times is None:
            timing = "measurement interval unknown; age unknown"
        else:
            age = now - times[1]
            timing = (f"measurement ended {age:.1f} s ago (wall-clock age)" if age >= 0
                      else "age unknown (measurement end is ahead of this clock)")
        identity = ("; spot tracks do not establish organism identity or continuity with a particular outlined object"
                    if self.how == "measured" else "")
        return f"saved measurement unchanged; {timing}" + identity

    def scale_words(self) -> str:
        basis = self.scale.get("basis")
        if not self.scale.get("umPerPx"):
            return f"in pixels: {self.scale.get('why') or 'no scale was set'}"
        return "µm by the measured scale" if basis == "measured" else "µm by the scale estimated from the objective"

    def words(self) -> str:
        """What it holds, in words (the index's summary, the brain's line)."""
        s = self.summary
        seconds = float(self.span.get("seconds") or 0.0)
        if self.how == "followed":
            what = f"the trail of {self.label}" if self.label else "a followed outline's trail"
            return f"{what}: {s.get('words') or 'its places'} ({self.interval()}; {self.scale_words()})"
        from .movement import speed_statistics_words

        # Old manifests without producer means remain readable; never reconstruct
        # them from a displayed subset or borrow a newer measurement's summary.
        averages = speed_statistics_words(s["speedStatistics"]) + " " if s.get("speedStatistics") else ""
        if not s.get("counted"):
            return averages + f"nothing could be followed for 1 s or more in {seconds:.1f} s ({self.interval()})"
        out = (f"{s.get('moving', 0)} of {s['counted']} small things moved at {s.get('threshold', 0):g} {self.unit} or more net"
               + (f" (median {s['netMedian']:.0f} {self.unit})" if s.get("netMedian") is not None else "")
               + f", over {seconds:.1f} s ({self.interval()})")
        if float(s.get("slideSpeed") or 0.0) >= float(s.get("threshold") or 0.0) > 0:
            out += f"; the view as a whole moved {float(s['slideSpeed']):.0f} {self.unit}"
        if s.get("drift"):
            out += "; the moving ones drifted together"
        return averages + out + f"; {self.scale_words()}"

    def plain(self) -> str:
        """Its row in the studio in plain words."""
        s = self.summary
        if self.how == "followed":
            return f"where {self.label or 'it'} went"
        return f"{s.get('moving', 0)} of {s.get('counted', 0)} moving" if s.get("counted") else "nothing followed"

    def line(self, placement: str = "") -> str:
        state = {"saving": "saving", "saved": "saved", "failed": f"not saved ({self.status_why})",
                 "unsaved": f"not saved ({self.status_why})"}[self.status]
        return f"{self.title} ({self.id}): {self.words()}" + (f"; {placement}" if placement else "") + f"; {state}"

    def row(self, tid: str) -> dict | None:
        return next((r for r in self.rows if r["id"] == tid), None)


# --- measuring (the operation's adapter calls this, in a thread) ----------------------------------------
def _gray(jpeg: bytes) -> np.ndarray:
    gray = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ToolFailed("a camera frame could not be read", next="ask again in a moment")
    return gray


def measure_clip(hub, clip: list, um_per_px: float | None, scale_words: str = "", draw: bool = True,
                 cls: str = "analysis") -> dict:
    """What moves in `clip` ([(frame, facts)], oldest first): movement.py's tracks and measurements, the
    words the model is given, and (`draw`) the last frame with the tracks drawn. Frames are decoded when
    the tracker reaches them, one resident at a time, inside a reservation (invariant 3) in allowance `cls`
    (a researcher's own run: "research", never the user's work's)."""
    from .capture import reserving
    from .movement import ClipFrame, follow, measure, trails, words

    frames, fov, last = [], None, None
    for frame, info in clip:
        frames.append(ClipFrame(float(info.wall_at), lambda j=frame.jpeg: _gray(j), float(info.dx), float(info.dy),
                                frame=int(getattr(info, "id", -1))))
        fov, last = getattr(info, "fov", None) or fov, (frame, info)
    if last is None:
        raise ToolFailed("there were no frames to measure", next="hold the slide still for a few seconds, then ask again")
    size = last[1]
    # What it decodes, counted: grey frames one at a time, and the colour of the last only when it draws
    # (the watcher's checks never draw; the partner's review of M3.2, note 5).
    with reserving(hub, getattr(size, "width", 0) or 0, getattr(size, "height", 0) or 0, "outline" if draw else "hold",
                   "following what moves", cls=cls):
        m = measure(frames, follow(frames, fov), um_per_px)
        picture = None
        if draw and m.counted:
            bgr = cv2.imdecode(np.frombuffer(last[0].jpeg, np.uint8), cv2.IMREAD_COLOR)
            if bgr is not None:
                ok, jpeg = cv2.imencode(".jpg", trails(bgr, m, (frames[-1].dx - frames[0].dx, frames[-1].dy - frames[0].dy)),
                                        [cv2.IMWRITE_JPEG_QUALITY, 88])
                picture = jpeg.tobytes() if ok else None
    return {"movement": m, "words": words(m, scale_words), "picture": picture, "tracks": len(m.tracks)}


def _frames_table(clip: list) -> list:
    return [[int(getattr(info, "id", -1)), getattr(info, "boot", None) or None, getattr(info, "seq", None),
             round(float(info.wall_at), 3), round(float(info.dx), 2), round(float(info.dy), 2)] for _frame, info in clip]


def _scale_of(reading) -> dict:
    if reading is None or not getattr(reading, "um_per_px", None):
        return {"umPerPx": None, "basis": None, "objective": getattr(reading, "objective", None) if reading else None}
    return {"umPerPx": float(reading.um_per_px), "basis": "measured" if getattr(reading, "measured", False) else "estimated",
            "objective": getattr(reading, "objective", None)}


def _arrays_bytes(points) -> int:
    return sum(int(a.nbytes) for segments in points or [] for a in segments)


def from_movement(hub, store: "TracksStore", capture, clip: list, done, reading, seconds: float, hold,
                  made_for: str | None = None, thread: str | None = None) -> Tracks:
    """The tracks item of one motion measurement (`done`: the runner's Result), its pixels and points held
    in the unsaved allowance (`hold`, grown first: refused cleanly, with the reason, when it does not fit).
    `made_for`: a researcher's own reading (research/hands.py), filed in its conversation (`thread`)."""
    from . import ticket as tickets
    from .movement import GAP, MAX_STEP_PX, MIN_TRACK_S, SMOOTH, WORK_WIDTH
    from .operations import OPERATIONS
    from .population import _day, _generation, _session, _thread, view_facts

    m = done.output["movement"]
    picture = done.output.get("picture")
    first = clip[0][1]
    x0, y0 = float(first.dx), float(first.dy)  # movement's points are in the first frame's coordinates
    epoch = getattr(clip[-1][1], "epoch", None)
    rows, points = [], []
    for n, (track, values) in enumerate(zip(m.tracks, m.counted), start=1):
        frames = list(track.frames) if len(track.frames) == len(track.points) else [None] * len(track.points)
        arr = np.array([[p[0] - x0, p[1] - y0, t, -1 if f is None else f]
                        for p, t, f in zip(track.points, track.times, frames)], np.float64).reshape(-1, 4)
        rows.append({"id": f"t{n}", "values": {"net": round(float(values["net"]), 4), "path": round(float(values["path"]), 4),
                                               "straightness": round(float(values["straightness"]), 4),
                                               "direction": round(float(values["direction"]), 2),
                                               "moving": bool(values["net"] >= m.threshold), "points": int(len(arr))},
                     "segments": [{"epoch": epoch, "start": "first", "end": "end", "n": int(len(arr))}]})
        points.append([arr])
    hold.reservation.grow(len(picture or b"") + _arrays_bytes(points), "the motion measurement's picture and points")
    moving = [r["values"]["net"] for r in rows if r["values"]["moving"]]
    summary = {"counted": len(rows), "moving": len(moving), "unit": m.unit, "threshold": round(float(m.threshold), 4),
               "slideSpeed": round(float(m.slide_speed), 4), "drift": m.drift,
               "netMedian": round(float(np.median(moving)), 4) if moving else None}
    from .movement import speed_statistics

    summary["speedStatistics"] = speed_statistics(m)
    impl = OPERATIONS["motion"].impl(done.execution.executed or "spots")
    request = tickets.current()
    session = _session(request, hub)
    made_by = {**done.execution.made_by(), **({"for": made_for} if made_for else {})}
    item = Tracks(store.new_id(session), "measured", "motion", done.execution.executed or "spots", made_by,
                  capture.id, view_facts(capture), getattr(capture, "scale", None),
                  {"from": round(float(clip[0][1].wall_at), 3), "to": round(float(clip[-1][1].wall_at), 3),
                   "seconds": round(float(m.seconds), 3), "frames": int(m.frames), "fps": round(float(m.fps), 3)},
                  summary, _scale_of(reading), rows, points, frames=_frames_table(clip),
                  producer={"implementation": impl.id if impl else "spots", "version": impl.version if impl else "",
                            "configuration": {"workWidth": WORK_WIDTH, "smooth": SMOOTH, "maxStepPx": MAX_STEP_PX, "gap": GAP,
                                              "minTrackS": MIN_TRACK_S, "threshold": round(float(m.threshold), 4),
                                              "unit": m.unit, "secondsAsked": round(float(seconds), 2)}},
                  transformations={"input": "the camera's JPEGs as received, each decoded once, grey, at "
                                            f"{WORK_WIDTH} px wide",
                                   "coordinates": "slide px: a point's frame px minus its frame's slide offset (the hub's dx, dy)",
                                   "linking": f"nearest place within {MAX_STEP_PX:g} px a frame (at {WORK_WIDTH} px wide), "
                                              f"a track may miss {GAP} frames; tracks shorter than {MIN_TRACK_S:g} s dropped",
                                   "speeds": f"from each path smoothed over {SMOOTH} frames",
                                   "picture": "the last frame, each track drawn by the app (blue slow, red fast)"},
                  ticket=getattr(request, "id", None), thread=thread if made_for else _thread(request, hub), day=_day(hub),
                  capture=capture, picture=picture, hold=hold, generation=_generation(request, hub), session=session)
    hold.reservation.settle(len(capture.jpeg or b"") + len(picture or b"") + _arrays_bytes(points))
    return item


def from_kind(hub, store: "TracksStore", capture, clip: list, done, reading, hold) -> Tracks:
    """A new kind of job's tracks (kinds.py, C6) kept as a tracks item: each point in slide coordinates (its frame's px
    minus that frame's slide offset), at that frame's own time; a track broken wherever the motion epoch changed, never
    joined across; its speeds, straightness and direction measured here from its places (px/s, µm/s with a scale), not
    taken from the kind. Its picture: the last frame given, the tracks drawn by the app."""
    import cv2

    from . import ticket as tickets
    from .population import _day, _generation, _session, _thread, view_facts

    out = done.output
    by_id = {int(info.id): info for _frame, info in clip}
    used = [by_id[i] for i in out["frames"]]
    # Its picture (one decode and its JPEG) and its points' arrays, admitted before they are made (Codex's diagnosis of
    # 621d0721), settled to what is kept once they are.
    n_points = sum(len(t["points"]) for t in out["tracks"])
    hold.reservation.grow(2 * 3 * int(capture.width) * int(capture.height) + 64 * n_points + 4096,
                          "the tracks' picture and points")
    um = float(reading.um_per_px) if reading is not None and getattr(reading, "um_per_px", None) else None
    unit = "µm/s" if um else "px/s"
    k = um or 1.0
    rows, points = [], []
    for n, track in enumerate(out["tracks"], start=1):
        places = [(x - float(used[i].dx), y - float(used[i].dy), float(used[i].wall_at), int(used[i].id), used[i].epoch)
                  for i, x, y in track["points"]]
        segments, current = [], []
        for place in places:
            if current and current[-1][4] != place[4]:
                segments.append(current)
                current = []
            current.append(place)
        segments.append(current)
        first = segments[0]  # speeds only within one view: a place in one is never compared with one in another
        seconds = first[-1][2] - first[0][2]
        path = sum(math.dist(a[:2], b[:2]) for a, b in zip(first, first[1:])) * k
        net = math.dist(first[0][:2], first[-1][:2]) * k
        values = {"net": round(net / seconds, 4) if seconds > 0 else 0.0, "path": round(path / seconds, 4) if seconds > 0 else 0.0,
                  "straightness": round(net / path, 4) if path > 0 else 0.0,
                  "direction": round(math.degrees(math.atan2(-(first[-1][1] - first[0][1]), first[-1][0] - first[0][0])), 2),
                  "moving": None, "points": len(places)}
        rows.append({"id": f"t{n}", "values": values,
                     "segments": [{"epoch": seg[0][4], "start": "first" if j == 0 else "another-view", "end": "end",
                                   "n": len(seg)} for j, seg in enumerate(segments)]})
        points.append([np.array([[p[0], p[1], p[2], p[3]] for p in seg], np.float64).reshape(-1, 4) for seg in segments])
    picture = None
    last_frame, last = clip[-1] if not used else next((f, i) for f, i in clip if int(i.id) == int(used[-1].id))
    bgr = cv2.imdecode(np.frombuffer(last_frame.jpeg, np.uint8), cv2.IMREAD_COLOR)
    if bgr is not None:
        for row_points in points:
            for seg in row_points:
                pts = np.array([[p[0] + float(last.dx), p[1] + float(last.dy)] for p in seg], np.int32)
                if len(pts) > 1:
                    cv2.polylines(bgr, [pts], False, (60, 200, 255), 2)
        ok, jpeg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 88])
        picture = jpeg.tobytes() if ok else None
    request = tickets.current()
    session = _session(request, hub)
    producer = out.get("producer") or {}
    item = Tracks(store.new_id(session), "measured", done.execution.operation, done.execution.executed or "",
                  done.execution.made_by(), capture.id, view_facts(capture), getattr(capture, "scale", None),
                  {"from": round(float(used[0].wall_at), 3), "to": round(float(used[-1].wall_at), 3),
                   "seconds": round(float(used[-1].wall_at - used[0].wall_at), 3), "frames": len(used),
                   "fps": round((len(used) - 1) / max(1e-6, float(used[-1].wall_at - used[0].wall_at)), 3)},
                  {"counted": len(rows), "moving": None, "unit": unit, "threshold": None, "netMedian": None},
                  _scale_of(reading), rows, points, frames=_frames_table([(None, i) for i in used]),
                  producer={**producer, "configuration": {"framesGiven": len(used)}},
                  transformations={"input": f"{len(used)} of the camera's frames, evenly spread, each decoded once, "
                                            "given to the kind's own code",
                                   "coordinates": "slide px: a point's frame px minus its frame's slide offset (the hub's dx, dy)",
                                   "linking": "the kind's own (its makers' code)", "speeds": "measured by the app from its places",
                                   "picture": "the last frame given, each track drawn by the app"},
                  ticket=getattr(request, "id", None), thread=_thread(request, hub), day=_day(hub), capture=capture,
                  picture=picture, hold=hold, generation=_generation(request, hub), session=session)
    hold.reservation.settle(len(capture.jpeg or b"") + len(picture or b"") + _arrays_bytes(points))
    return item


def clip_scale(hub, clip: list) -> tuple[Any, str]:
    """The scale of a clip's own interval, read with its frames, before anything is run on them (Codex's diagnosis of
    621d0721): the reading for its eyepiece disc, only when every frame is of one view (its motion epoch) and the
    objective was set before its first frame; otherwise none (pixels), with why. A reading taken after the kind's
    code ran is never put on frames from before it."""
    scale = getattr(hub, "scale", None)
    if scale is None or not clip:
        return None, "no scale was set"
    infos = [info for _frame, info in clip]
    if len({getattr(i, "epoch", None) for i in infos}) != 1:
        return None, "its frames span more than one view of the slide"
    fov = getattr(infos[-1], "fov", None)
    reading = scale.reading(fov.radius) if fov is not None else None
    if reading is None or not getattr(reading, "um_per_px", None):
        return None, "no scale was set"
    try:
        set_at = (scale.facts().get("objective") or {}).get("at") if hasattr(scale, "facts") else None
    except Exception:
        set_at = None
    if isinstance(set_at, (int, float)) and set_at > float(infos[0].wall_at):
        return None, "the objective was set after its first frame, so its places are in pixels"
    return reading, ""


def trail_scale(hub, trail, reading, view_epoch) -> tuple[Any, str]:
    """The scale a kept trail's distances and speeds may be given in: the reading now only when every one of its
    segments is of the view now (its epoch) and the objective was set before it began; otherwise none (pixels),
    with why. A segment of an earlier view may have been seen under another objective or zoom: today's reading is
    never put on it (the partner's review of M3.2, note 3)."""
    if reading is None or not getattr(reading, "um_per_px", None):
        return None, "no scale was set"
    if any(seg.points and seg.epoch != view_epoch for seg in trail.segments):
        return None, "part of it is from an earlier view of the slide, where the scale now may not hold"
    first = min((p[2] for seg in trail.segments for p in seg.points), default=None)
    scale = getattr(hub, "scale", None)
    try:
        set_at = (scale.facts().get("objective") or {}).get("at") if scale is not None and hasattr(scale, "facts") else None
    except Exception:
        set_at = None
    if isinstance(set_at, (int, float)) and first is not None and set_at > first:
        return None, "the objective was set after it began, so its earlier places are in pixels"
    return reading, ""


def by_view(trail) -> list[dict]:
    """A trail's places by view (motion epoch, in the order it was first seen in each): its path along that view's
    segments, its reach from its own first place in that view with each correction's jump taken out (as
    trail.reach_px does for the first view only), how far its place jumped across that view's breaks (a gap or a
    loss: `acrossBreakPx`, never a correction's) and how many there were (`gaps`), how many places were kept there
    and over how long its place was known there. A place in one view is never compared with one in another."""
    views: dict = {}
    for seg in trail.segments:
        if not seg.points:
            continue
        v = views.setdefault(seg.epoch, {"epoch": seg.epoch, "pathPx": 0.0, "reachPx": 0.0, "acrossBreakPx": 0.0, "gaps": 0,
                                          "points": 0, "knownS": 0.0, "first": seg.points[0][:2], "shift": [0.0, 0.0],
                                          "prev": None})
        if v["prev"] is not None:
            if seg.start == "after-correction":  # re-registered, not moved: taken out
                v["shift"][0] += v["prev"][0] - seg.points[0][0]
                v["shift"][1] += v["prev"][1] - seg.points[0][1]
            else:  # found again after a gap or a loss: where it was then is not where it was before, unobserved
                v["acrossBreakPx"] += math.dist(v["prev"], seg.points[0][:2])
                v["gaps"] += 1
        v["pathPx"] += sum(math.dist(a[:2], b[:2]) for a, b in zip(seg.points, seg.points[1:]))
        v["points"] += len(seg.points)
        v["knownS"] += seg.points[-1][2] - seg.points[0][2]
        x0, y0 = v["first"]
        for q in seg.points:
            v["reachPx"] = max(v["reachPx"], math.dist((x0, y0), (q[0] + v["shift"][0], q[1] + v["shift"][1])))
        v["prev"] = seg.points[-1][:2]
        last = seg.points[-1]  # where it ended in this view, corrections taken out as for its reach
        v["endPx"] = math.dist((x0, y0), (last[0] + v["shift"][0], last[1] + v["shift"][1]))
    return [{"epoch": v["epoch"], "pathPx": round(v["pathPx"], 2), "reachPx": round(v["reachPx"], 2),
             "endPx": round(v["endPx"], 2), "acrossBreakPx": round(v["acrossBreakPx"], 2), "gaps": v["gaps"],
             "points": v["points"], "knownS": round(v["knownS"], 3)}
            for v in views.values()]


def px_words(px: float) -> str:
    """A distance in px as said: tenths under 20 px (never rounded up to a gate's own number: 11.9 px is not
    "about 12 px, under the 12 px"), whole px above."""
    if px < 20:
        return f"{px:.1f}".rstrip("0").rstrip(".") + " px"
    return f"{round(px)} px"


def went_somewhere(views: list[dict]) -> bool:
    """trail.py's gate, view by view: it went somewhere when, in at least one view, it has SHOWN_PX of path and got
    REACH_PX from where it was first seen in that view (the partner's re-check of M3.2, B1: the first view's reach
    alone said a thing that only swam in a later view stayed put)."""
    from .trail import REACH_PX, SHOWN_PX

    return any(v["pathPx"] >= SHOWN_PX and v["reachPx"] >= REACH_PX for v in views)


ORDINALS = ("first", "second", "third", "fourth", "fifth")


def trail_values(trail, segments: list, k: float) -> dict:
    """A kept trail's measurements, never across a break (the partner's review of M3.2, B1 and B2):
    - `path`: its path along the stretches its place was known, over the time it was known;
    - `net`: its own displacement within those stretches (each stretch's own end-to-end move, summed; never
      the jump across a gap, a loss, a correction or another view), over that same time, so it is at most
      `path`;
    - `straightness`: that displacement over the path, only for a trail with no break (none, "not measured
      across breaks", otherwise);
    - `moving`: whether it went somewhere by trail.py's own gate in any one of its views (at least SHOWN_PX of path
      and REACH_PX from where it was first seen in that view; `went_somewhere`), never assumed."""
    gaps, lost = trail.breaks()
    epochs = {seg.epoch for seg in trail.segments if seg.points}
    broken = bool(gaps or lost or len(epochs) > 1)
    known = trail.known_seconds()
    path = trail.path_px()
    own = np.sum([a[-1, :2] - a[0, :2] for a in segments], axis=0) if segments else np.zeros(2)
    displacement = float(np.hypot(*own))
    return {"net": round(displacement * k / known, 4) if known > 0 else None,
            "path": round(path * k / known, 4) if known > 0 else None,
            "straightness": None if broken or path <= 0 else round(min(1.0, displacement / path), 4),
            "moving": went_somewhere(by_view(trail)),
            "netBasis": "its own motion within the stretches its place was known; never across a break",
            "points": int(sum(len(a) for a in segments))}


def from_trail(hub, store: "TracksStore", capture, obj, trail, reading, execution, hold) -> Tracks:
    """A followed outline's trail kept as a tracks item: its segments as the follower verified them, each
    break as it was; the picture is the view now with its same-epoch segments drawn (`capture` held)."""
    from . import ticket as tickets
    from .population import _day, _generation, _session, _thread, view_facts
    from .trail import KEEP_EVERY_S, MIN_STEP_PX, REACH_PX, SHOWN_PX, STALE_S, WINDOW_S

    segments, metas = [], []
    for seg in trail.segments:
        if not seg.points:
            continue
        arr = np.array([[p[0], p[1], p[2], -1 if p[3] is None else p[3]] for p in seg.points], np.float64).reshape(-1, 4)
        segments.append(arr)
        metas.append({"epoch": seg.epoch, "start": seg.start, "end": seg.end, "n": int(len(arr))})
    if not segments:
        raise ToolFailed("following has kept none of its places yet", next="keep it followed for a few seconds, then ask again")
    view = view_facts(capture)
    reading, why_px = trail_scale(hub, trail, reading, view.get("epoch"))
    k = float(getattr(reading, "um_per_px", 0.0) or 0.0) or 1.0
    unit = "µm/s" if reading is not None else "px/s"
    known = trail.known_seconds()
    path, views = trail.path_px(), by_view(trail)
    reach = max((v["reachPx"] for v in views), default=0.0)  # the farthest it got from its first place in any one view
    gaps, lost = trail.breaks()
    values = trail_values(trail, segments, k)
    times = [float(a[:, 2].min()) for a in segments] + [float(a[:, 2].max()) for a in segments]
    infos = getattr(hub, "infos", None) or {}
    seen = sorted({int(f) for a in segments for f in a[:, 3] if f >= 0})
    frames = []
    for fid in seen:  # (boot, seq) only where the frame's facts are still kept (30 s): "where known"
        info = infos.get(fid) if hasattr(infos, "get") else None
        frames.append([fid, getattr(info, "boot", None) or None, getattr(info, "seq", None),
                       round(float(getattr(info, "wall_at", 0.0) or 0.0), 3) or None,
                       round(float(getattr(info, "dx", 0.0)), 2) if info is not None else None,
                       round(float(getattr(info, "dy", 0.0)), 2) if info is not None else None])
    words = trail_words(trail, reading)
    request = tickets.current()
    session = _session(request, hub)
    scale = _scale_of(reading)
    if why_px:
        scale["why"] = why_px
    item = Tracks(store.new_id(session), "followed", "track", "edgetam-video",
                  {**execution.made_by(), "how": "followed", "object": obj.id}, capture.id, view,
                  getattr(capture, "scale", None),
                  {"from": round(min(times), 3), "to": round(max(times), 3), "seconds": round(max(times) - min(times), 3),
                   "frames": len(seen), "fps": None},
                  {"unit": unit, "pathPx": round(path, 2), "reachPx": round(reach, 2), "knownSeconds": round(known, 3),
                   "gaps": gaps, "lost": lost, "corrections": len(trail.corrections), "wentSomewhere": values["moving"],
                   "views": views,
                   "words": words},
                  scale, [{"id": "t1", "values": values, "segments": metas}], [segments], frames=frames,
                  producer={"implementation": "edgetam-video", "version": "yonigozlan/EdgeTAM-hf",
                            "configuration": {"keptFor": WINDOW_S, "minStepPx": MIN_STEP_PX, "keepEveryS": KEEP_EVERY_S,
                                              "staleS": STALE_S, "shownPx": SHOWN_PX, "reachPx": REACH_PX}},
                  transformations={"input": "the follower's verified places of the outline (trail.py), as they were kept",
                                   "coordinates": "slide px: the outline's centre minus its anchor's slide offset",
                                   "breaks": "kept as they were: a gap, a loss, a correction or another epoch; never joined. "
                                             "A segment that starts \"reacquired\" was found again by the outline's look "
                                             "after it was lost (a re-find by appearance, not a proof it is the same "
                                             "thing). The last segment's \"open\" end means it was still being followed "
                                             "when it was kept",
                                   "speeds": "net and path speeds within the stretches its place was known, over the time "
                                             "it was known; never across a break; straightness only without a break",
                                   "picture": "the view when it was kept, its same-epoch segments drawn by the app"},
                  ticket=getattr(request, "id", None), thread=_thread(request, hub), day=_day(hub), capture=capture,
                  hold=hold, object_id=obj.id, label=str(getattr(obj, "label", "") or ""),
                  generation=_generation(request, hub), session=session)
    return item


def view_facts_words(v: dict, near, there: str = "") -> str:
    """One view of a kept trail that went nowhere by trail.py's gate, in its measured facts (the partner's re-checks
    of M3.2, round 6: each category chosen by a threshold met a new edge, so the numbers are said instead): how far its
    place got from where it was first seen, how much path it was seen making, how far its place shifted across the
    breaks in following it (unobserved), and, only when both its place change and its seen path are under trail.py's
    bars, that they are. One place kept: whether it moved can't be told."""
    from .trail import REACH_PX, SHOWN_PX

    if v["points"] <= 1:
        return f"it was seen once{there}; whether it moved can't be told"
    reach, path, gaps, across = v["reachPx"], v["pathPx"], v.get("gaps", 0), v.get("acrossBreakPx", 0.0)
    end = v.get("endPx", reach)
    if reach > 0:
        # Its reach is the farthest it got, not where it ended (the partner's round-6 re-check, P28: 100, 400, 104).
        got = f"it got as far as about {near(reach)} from where it was first seen{there}"
        if reach - end >= REACH_PX:
            got += f", and ended about {near(end)} from it"
        words = [got]
        words.append(f"{near(path)} of path was seen" if path > 0 else "none of it was seen as path")
    else:
        words = [f"its place did not change from where it was first seen{there}"]
        if path > 0:
            words.append(f"{near(path)} of path was seen")
    if gaps:
        shifted = (f"its place shifted {near(across)}{' in all' if gaps > 1 else ''} across {gaps} "
                   f"break{'s' if gaps != 1 else ''} in following it, unobserved") if across > 0 else \
            f"it was found again where it was after {gaps} break{'s' if gaps != 1 else ''} in following it"
        words.append(shifted)
    sentence = "; ".join(words)
    if reach < REACH_PX and path < SHOWN_PX:
        sentence += (f" (both under the {REACH_PX:g} px of place change and {SHOWN_PX:g} px of path a trail needs before "
                     "its outline's redraws can be told from motion)")
    return sentence


def trail_words(trail, reading) -> str:
    """How far a followed thing went, in words. When no view passes trail.py's own gate (`went_somewhere`), each view
    is said in its measured facts (`view_facts_words`: its place change, the path it was seen making, its shift across
    breaks, and the gate's bars only when both are under them), never a category chosen by a threshold; a trail of one
    place: whether it moved can't be told. Otherwise its path, only over the time its place was known when there was a
    break, and a loss said as found again by its look (ADR 0012)."""
    path, views = trail.path_px(), by_view(trail)
    um = getattr(reading, "um_per_px", None) if reading is not None else None
    if not went_somewhere(views):  # no view passes trail.py's gate: each view's measured facts, said as they are
        def near(px: float) -> str:
            words = px_words(px)
            if um:
                from .scale import round_um

                words += f" ({'' if reading.measured else 'about '}{round_um(px * um)} µm)"
            return words

        if not views or (len(views) == 1 and views[0]["points"] <= 1):
            return "seen once; whether it moved can't be told"
        if len(views) == 1:
            return view_facts_words(views[0], near, "")
        parts = [f"in its {ORDINALS[i] if i < len(ORDINALS) else f'{i + 1}th'} view {view_facts_words(v, near, ' there')}"
                 for i, v in enumerate(views)]
        return f"in each of its {len(views)} views: " + "; ".join(parts)
    span, known = max(1, round(trail.seconds())), max(1, round(trail.known_seconds()))
    if um:
        from .scale import round_um

        far = f"{round_um(path * um)} µm" if reading.measured else f"about {round_um(path * um)} µm"
    else:
        far = f"{round(path)} px"
    gaps, lost = trail.breaks()
    found_again = any(seg.start == "reacquired" for seg in trail.segments if seg.points)
    notes = ([f"{gaps} gap{'s' if gaps != 1 else ''}"] if gaps else []) + \
        ([f"lost {lost}x" + (", found again by its look" if found_again else "")] if lost else [])
    if not notes:
        return f"{far} over the slide in {span} s"
    return f"{far} over the slide while its place was known (about {known} of {span} s; {', '.join(notes)})"


def draw_followed(bgr: np.ndarray, item: Tracks) -> np.ndarray:
    """The view with a kept trail's segments of its own epoch drawn: each segment alone (never joined), older
    parts fainter, a ring where it was found again and a cross where it was lost."""
    out = bgr.copy()
    epoch, dx, dy = item.view.get("epoch"), float(item.view.get("dx") or 0.0), float(item.view.get("dy") or 0.0)
    t0, t1 = float(item.span.get("from") or 0.0), float(item.span.get("to") or 0.0)
    for row, segments in zip(item.rows, item.points or []):
        for meta, arr in zip(row["segments"], segments):
            if meta["epoch"] != epoch or not len(arr):
                continue
            pts = np.round(arr[:, :2] + (dx, dy)).astype(np.int32)
            for a, b, t in zip(pts, pts[1:], arr[1:, 2]):
                age = (t - t0) / (t1 - t0) if t1 > t0 else 1.0
                colour = tuple(int(c * (0.35 + 0.65 * age)) for c in (249, 121, 232))  # the outlines' pink, older fainter
                cv2.line(out, tuple(int(v) for v in a), tuple(int(v) for v in b), colour, 2, cv2.LINE_AA)
            if meta["start"] == "reacquired":
                cv2.circle(out, tuple(int(v) for v in pts[0]), 5, (255, 255, 255), 1, cv2.LINE_AA)
            if meta["end"] == "lost":
                x, y = (int(v) for v in pts[-1])
                cv2.line(out, (x - 5, y - 5), (x + 5, y + 5), (255, 255, 255), 1, cv2.LINE_AA)
                cv2.line(out, (x + 5, y - 5), (x - 5, y + 5), (255, 255, 255), 1, cv2.LINE_AA)
    return out


# --- the store ---------------------------------------------------------------------------------------
class TracksStore:
    """The session's tracks items: the one writer of them, their saving and their reading back. `items` are those of
    the session open now (as evidence.Evidence's populations): another session's are read back when it is opened
    (`reload`), and one made for another session is filed there, never listed here."""

    def __init__(self, hub, root: Path | None, day=None, log: Callable[[dict], None] | None = None,
                 keep: Callable[[Path, str], None] | None = None) -> None:
        self.hub = hub
        self.root = Path(root) if root is not None else None
        from .project_artifact_folder import ArtifactFolder
        self._folders = ArtifactFolder(self.root, 'evidence') if self.root is not None else None
        self._retained_manifests = {}
        self._recovered_optional = {}
        self.day = day  # day.SessionStore: each saved item a journal event, read back at start
        self.log = log or (lambda record: None)
        self.keep = keep  # Services.keep: mirror into a dataset being recorded
        self.items: OrderedDict[str, Tracks] = OrderedDict()
        self.by_execution: dict[str, str] = {}  # the execution that made it -> its item (a shared job's one item)
        self.making: dict[str, asyncio.Future] = {}  # an execution's item being made now (the others wait for it)
        self.listeners: list[Callable[[Tracks], None]] = []
        self._given = 0  # the highest number given or read back in this run (never lower: day/ids.py)
        self._floors: dict[tuple, int] = {}  # (session, date) -> the floor read once a run
        self._away: dict[tuple[str, str], Tracks] = {}  # (session, id) -> another session's still being saved
        self._lock = threading.RLock()  # the writer slot and the status, decided together
        self._writing: set[str] = set()
        self._dirty: set[str] = set()
        self.problems: list[str] = []
        if self.day is not None:
            self.read_back()
            listeners = getattr(self.day, "listeners", None)
            if isinstance(listeners, list):  # another session opened: its tracks are the ones listed
                listeners.append(self._session_event)

    def new_id(self, session: str | None = None) -> str:
        """"m1", "m2"...: new for the session it is made in (`session`, else the one open) on any of its days and for
        today's folder (day/ids.py), and never one whose folder is on disk."""
        from .day.ids import floor, owner

        date = getattr(self.day, "date", None) or time.strftime("%Y-%m-%d")
        root = self.root_for(session)
        folder = root / date if root is not None else None
        store = owner(self.day, session)
        key = (getattr(store, "sid", None), date)
        while True:
            with self._lock:
                if key not in self._floors:
                    self._floors[key] = floor(store, "m", date)
                self._given = max(self._floors[key], self._given) + 1
                tid = f"m{self._given}"
            if tid not in self.items and (folder is None or not (folder / tid).exists()):
                return tid

    def root_for(self, session=None, *, write=False):
        return self._folders.root_for(self.day, session or self._session(), write=write) if self._folders else None

    def _date(self) -> str:
        return getattr(self.day, 'date', None) or time.strftime('%Y-%m-%d')

    def _session(self) -> str:
        return str(getattr(self.day, "sid", "") or "")

    def _here(self, item: Tracks) -> bool:
        """Whether an item belongs to the session open now (one made with no session store always does)."""
        return not item.session or not self._session() or item.session == self._session()

    def admit(self) -> None:
        """Before measuring: refused (with the reason) past the evidence disk budget, while populations' saves
        fail (evidence.Evidence.admit), or while this store's own saves fail."""
        from .evidence import EvidenceRefused, evidence_of

        evidence_of(self.hub).admit()
        failing = [i for i in self.items.values() if i.status == "failed"]
        if len(failing) >= 3:
            raise EvidenceRefused(f"saving motion tracks has failed {len(failing)} times ({failing[-1].status_why}), so new "
                                  "measurements wait until it works", next="check the disk has room, then ask again")

    def add(self, item: Tracks) -> Tracks:
        with self._lock:
            if self._here(item):
                self.items[item.id] = item
                execution = (item.made_by or {}).get("execution")
                if execution:
                    self.by_execution[execution] = item.id
            else:  # made for another session (asked there, finished after a switch): filed there only
                self._away[(item.session, item.id)] = item
        record = getattr(self.day, "record_tracks_made", None)
        if record is not None:
            try:
                record(item.id, day=item.day or None, session=item.session or None)
            except Exception:
                pass
        self._changed(item)
        return item

    def for_execution(self, execution_id: str | None) -> Tracks | None:
        """The item a measuring job already made (its other requests reuse it: invariant 5)."""
        return self.items.get(self.by_execution.get(execution_id or "", ""))

    def get(self, tid: str | None) -> Tracks | None:
        return self.items.get(tid) if tid else None

    def latest(self) -> Tracks | None:
        """The newest of the user's own (a researcher's is reached by its id or once shown: made_for)."""
        return next((t for t in reversed(self.items.values()) if not t.made_for), None)

    def _changed(self, item: Tracks) -> None:
        if not self._here(item):
            return  # another session's: nothing on this session's screen is about it
        for listener in list(self.listeners):
            try:
                listener(item)
            except Exception:
                pass

    # --- what is read of an item ------------------------------------------------------------------
    def points(self, item: Tracks) -> list:
        """Its points, from memory or (once saved) from tracks.npz; [] when they cannot be read."""
        if item.points is not None:
            return item.points
        if item.path is None or not (item.path / "tracks.npz").is_file():
            return []
        data = np.load(item.path / "tracks.npz")
        return [[np.asarray(data[f"{row['id']}.s{j}"], np.float64).reshape(-1, 4) if f"{row['id']}.s{j}" in data
                 else np.zeros((0, 4)) for j in range(len(row["segments"]))] for row in item.rows]

    def picture(self, item: Tracks, max_bytes: int | None = None) -> bytes | None:
        from .resources import read_picture_bytes
        if item.picture:
            return item.picture
        if item.path is not None and (item.path / "tracks.jpg").is_file():
            return read_picture_bytes(item.path / "tracks.jpg", max_bytes)
        return None

    def capture_jpeg(self, item: Tracks) -> bytes | None:
        if item.capture is not None and getattr(item.capture, "jpeg", None):
            return item.capture.jpeg
        if item.path is not None and (item.path / "capture.jpg").is_file():
            return (item.path / "capture.jpg").read_bytes()
        return None

    def wire(self, item: Tracks) -> dict:
        """The studio's data for an item: each track's segments of the item's own epoch in its view's pixels
        (`p = slide + the view's offset`, as a population's members are sent), thinned, with its values."""
        epoch, dx, dy = item.view.get("epoch"), float(item.view.get("dx") or 0.0), float(item.view.get("dy") or 0.0)
        points = self.points(item)
        order = sorted(range(len(item.rows)), key=lambda i: -float(item.rows[i]["values"].get("net") or 0.0))
        tracks = []
        for i in order[:MAX_WIRE_TRACKS]:
            row = item.rows[i]
            segs = []
            for meta, arr in zip(row["segments"], points[i] if i < len(points) else []):
                if meta["epoch"] != epoch or not len(arr):
                    continue
                keep = arr
                if len(arr) > MAX_WIRE_POINTS:
                    idx = np.round(np.linspace(0, len(arr) - 1, MAX_WIRE_POINTS)).astype(int)
                    keep = arr[idx]
                segs.append({"start": meta["start"], "end": meta["end"],
                             "points": [[round(float(p[0]) + dx, 1), round(float(p[1]) + dy, 1), round(float(p[2]), 2)] for p in keep]})
            tracks.append({"id": row["id"], "moving": bool(row["values"].get("moving")), "net": row["values"].get("net"),
                           "path": row["values"].get("path"), "straightness": row["values"].get("straightness"), "segments": segs})
        return {"type": "motion-tracks", "space": spaces.of_wire("motion-tracks"), "id": item.id, "title": item.title, "how": item.how, "capture": item.capture_id,
                "epoch": epoch, "offset": [dx, dy], "from": item.span.get("from"), "to": item.span.get("to"),
                "seconds": item.span.get("seconds"), "interval": item.interval(), "unit": item.unit,
                "umPerPx": item.scale.get("umPerPx"), "scale": item.scale.get("basis"),
                "threshold": item.summary.get("threshold"), "count": len(item.rows),
                "left": max(0, len(item.rows) - MAX_WIRE_TRACKS), "tracks": tracks}

    # --- saving -----------------------------------------------------------------------------------
    def schedule_save(self, item: Tracks) -> None:
        if self.root is None:
            item.status, item.status_why = "unsaved", "no evidence folder in this run"
            return
        with self._lock:
            item.status = "saving"
            if item.id in self._writing:
                self._dirty.add(item.id)
                return
            self._writing.add(item.id)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        threading.Thread(target=self._save, args=(item, loop), daemon=True, name=f"tracks-{item.id}").start()

    def _done_writing(self, item: Tracks) -> bool:
        with self._lock:
            self._writing.discard(item.id)
            again = item.id in self._dirty
            self._dirty.discard(item.id)
        if again:
            self.schedule_save(item)
        return again

    def _on_loop(self, loop, fn, *args) -> None:
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(fn, *args)
        else:
            fn(*args)

    def _save(self, item: Tracks, loop=None) -> None:
        from .evidence import _atomic

        try:
            from .project_evidence import destination, sync_publication, verify_previous, remember
            folder, portable = destination(self, item)
            if portable:
                verify_previous(self, item)
            files = {}
            jpeg = self.capture_jpeg(item)
            if jpeg:
                files["capture.jpg"] = jpeg
            picture = self.picture(item)
            if picture:
                files["tracks.jpg"] = picture
            files["tracks.npz"] = self._points_bytes(item)
            sums = {}
            for name, data in files.items():
                _atomic(folder / name, data)
                sums[name] = hashlib.sha256(data).hexdigest()
            _atomic(folder / "manifest.json", json.dumps(self.manifest(item, sums), default=str).encode())  # the manifest last
            if portable:
                verify_previous(self, item)
                sync_publication(self, item, folder)
                remember(self, folder)
        except Exception as exc:
            self._on_loop(loop, self._failed, item, f"{type(exc).__name__}: {exc}"[:200])
            return
        self._on_loop(loop, self._saved, item, folder)

    def _points_bytes(self, item: Tracks) -> bytes:
        arrays = {}
        for row, segments in zip(item.rows, self.points(item)):
            for j, arr in enumerate(segments):
                arrays[f"{row['id']}.s{j}"] = np.asarray(arr, np.float64)
        buffer = io.BytesIO()
        np.savez_compressed(buffer, **arrays)
        return buffer.getvalue()

    def manifest(self, item: Tracks, sums: dict) -> dict:
        made = item.made_by or {}
        return {"schema": SCHEMA, "kind": "tracks", "id": item.id, "title": item.title, "how": item.how,
                "operation": item.operation, "implementation": item.implementation, "madeBy": item.made_by,
                # Interpreting it needs none of the optional logs (Amendment 4, item 3).
                "execution": made.get("execution"), "policy": made.get("policy"), "origin": made.get("origin"),
                "producer": item.producer, "transformations": item.transformations,
                "source": {"capture": item.capture_id, "boot": item.view.get("boot"), "seq": item.view.get("seq")},
                "capturedAt": item.view.get("at"), "capture": item.capture_id, "view": item.view, "scaleId": item.scale_id,
                "scale": item.scale, "epoch": item.view.get("epoch"), "span": item.span, "frames": item.frames,
                "summary": item.summary, "pointFields": list(POINT_FIELDS), "tracks": item.rows,
                "object": item.object_id, "label": item.label, "ticket": item.ticket, "thread": item.thread,
                "day": item.day, "session": item.session, "madeAt": item.made_at, "files": sums}

    def _saved(self, item: Tracks, folder: Path) -> None:
        item.path = folder
        with self._lock:
            if item.id in self._dirty and self._done_writing(item):
                return  # asked again while it was written: saved again, said once that is done
        # It is saved once its session's journal files it: a restart reads it back from there. Until then the writer
        # slot stays taken (a save asked for meanwhile waits for it) and the status says "saving", so whoever waits for
        # "saved" (a restart's read_back, the studio) finds it filed. A filing that did not finish is said as failed,
        # never as saved, and keeps its pixels and points (a restart would not find it).
        why = "its session's journal did not file it"
        try:
            record = getattr(self.day, "record_tracks", None)
            if record is not None:
                from .population import filed_thread

                # Filed in its session under its conversation; one from before sessions only while that is still the
                # one loaded (S2): else filed under the session.
                from .project_evidence import reference
                path, identity = reference(self, item, folder)
                record(item.id, path, filed_thread(self.hub, item.thread, item.session, item.generation),
                       day=item.day or None, session=item.session or None, **identity)
            why = ""
        except Exception as exc:
            why = f"its session's journal did not file it ({type(exc).__name__}: {exc})"[:200]
        finally:
            with self._lock:
                again = self._done_writing(item)
                if not again:
                    if why:
                        item.status, item.status_why = "failed", why
                    else:
                        item.status, item.status_why = "saved", ""
                        # Saved: its pixels and points leave memory (read again from its folder when needed); the
                        # table stays.
                        item.capture, item.picture, item.points = None, None, None
                        if item.hold is not None:
                            item.hold.release()
                            item.hold = None
        if again:
            return  # asked again while it was filed: saved again, said once that is done
        if why:
            self.log({"kind": "tracks-save-failed", "id": item.id, "error": why})
            self._changed(item)
            return
        if self.keep is not None:
            # A dataset being recorded keeps the whole item: its points are its core (the partner's review, S4).
            for name in ("manifest.json", "tracks.npz", "tracks.jpg", "capture.jpg"):
                if (folder / name).exists():
                    try:
                        self.keep(folder / name, f"tracks-{item.day}-{item.id}-{name}")
                    except Exception:
                        pass
        self.log({"kind": "tracks-saved", "id": item.id, "tracks": len(item.rows), "path": str(folder)})
        self._changed(item)

    def _failed(self, item: Tracks, why: str) -> None:
        with self._lock:
            if self._done_writing(item):
                return  # the next save may succeed: its outcome is the one said
            item.status, item.status_why = "failed", why
        self.log({"kind": "tracks-save-failed", "id": item.id, "error": why})
        self._changed(item)

    # --- reading back ------------------------------------------------------------------------------
    def _session_event(self, event: dict) -> None:
        if event.get("type") == "session-opened":
            self.reload()

    def reload(self) -> list[str]:
        """Another session was opened (ADR 0017): the items listed are now its own, read back from its journal (every
        date it touched) as history, never drawn live (the app hides the layer). One still being saved finishes and
        is filed in its own session."""
        with self._lock:
            saving = {(t.session, t.id): t for t in self.items.values() if t.session and t.status == "saving"}
            self._away = {k: t for k, t in {**self._away, **saving}.items() if t.status == "saving"}
            self.items = OrderedDict()
            self.by_execution = {}
        loaded = self.read_back()
        with self._lock:  # its own still being saved: listed again until their save is filed
            for (sid, tid), item in list(self._away.items()):
                if sid == self._session():
                    self.items.setdefault(tid, item)
                    del self._away[(sid, tid)]
        return loaded

    def read_back(self) -> list[str]:
        """The session's saved items, from its journal (ADR 0001), whatever date folder each is in: each manifest
        checked against its files; a partial one is reported "incomplete, not loaded"."""
        loaded, problems = [], []
        for tid, entry in (getattr(self.day, "tracks", {}) or {}).items():
            try:
                from .project_evidence import resolve
                folder = resolve(self, self._session(), entry)
                manifest = json.loads((folder / "manifest.json").read_text())
                if manifest.get("schema") != SCHEMA:
                    raise ValueError(f"schema {manifest.get('schema')}")
                for name, digest in (manifest.get("files") or {}).items():
                    if hashlib.sha256((folder / name).read_bytes()).hexdigest() != digest:
                        raise ValueError(f"{name} does not match its checksum")
                if entry.get('manifestSha256'):
                    from .project_folder_copy import _stamp
                    self._retained_manifests[folder] = (entry['manifestSha256'], _stamp((folder / 'manifest.json').lstat()))
            except Exception as exc:
                problems.append(f"{tid}: incomplete, not loaded ({type(exc).__name__}: {exc})"[:200])
                continue
            item = Tracks(manifest["id"], manifest.get("how") or "measured", manifest.get("operation") or "motion",
                          manifest.get("implementation") or "", manifest.get("madeBy") or {}, manifest.get("capture"),
                          manifest.get("view") or {}, manifest.get("scaleId"), manifest.get("span") or {},
                          manifest.get("summary") or {}, manifest.get("scale") or {}, manifest.get("tracks") or [], None,
                          frames=manifest.get("frames") or [], producer=manifest.get("producer") or {},
                          transformations=manifest.get("transformations") or {}, ticket=manifest.get("ticket"),
                          # The conversation the journal filed it under (ADR 0001: the journal is the source of truth;
                          # one made before "start today afresh" and saved after is under the day, whatever its
                          # manifest says: the partner's re-check of M3.2, S1).
                          thread=entry.get("thread", manifest.get("thread")), day=manifest.get("day") or "",
                          made_at=manifest.get("madeAt") or 0.0,
                          status="saved", path=folder, object_id=manifest.get("object"), label=manifest.get("label") or "",
                          generation=getattr(self.day, "generation", None),  # filed in this journal: as loaded
                          session=self._session())
            self.items[item.id] = item
            if (item.made_by or {}).get("execution"):
                self.by_execution[item.made_by["execution"]] = item.id
            n = int(item.id[1:]) if item.id[1:].isdigit() else 0
            self._given = max(self._given, n)
            loaded.append(item.id)
        if loaded or problems:
            self.log({"kind": "tracks-read-back", "loaded": loaded, "problems": problems})
        self.problems = problems
        return loaded


def tracks_of(hub) -> TracksStore:
    """The hub's tracks store (made on first use, without saving, for a test's stand-in hub)."""
    store = getattr(hub, "tracks_store", None)
    if not isinstance(store, TracksStore):
        store = TracksStore(hub, None)
        try:
            hub.tracks_store = store
        except AttributeError:
            pass
    return store


# --- keeping (the tool's two ways) -------------------------------------------------------------------
class MotionFrames(list):
    """One forward acquisition's original Captures, held in the existing research allowance.

    Hands owns one lease; a real CPU reader retains another before dispatch. Cancellation of
    Hands cannot release that reader's original JPEGs. This is a temporary input, not a store.
    """

    def __init__(self, captures):
        super().__init__()
        self.captures, self.holds = captures, []
        self._owners, self._lock = 1, threading.Lock()

    def add(self, capture):
        hold = self.captures.hold(capture, "research", "bytes", "upcoming motion frames")
        self.holds.append(hold)
        self.append((capture, capture))  # original bytes and immutable original facts stay paired

    def retain(self):
        with self._lock:
            if self._owners == 0:
                raise RuntimeError("the motion input has been released")
            self._owners += 1
        return self

    def release(self):
        with self._lock:
            if self._owners == 0:
                return
            self._owners -= 1
            if self._owners:
                return
            holds, self.holds = self.holds, []
            self.clear()
        for hold in holds:
            hold.release()


async def reading_motion_frames(clip, work):
    """The existing CPU executor reads with an independent forward-input lease, if any."""
    window = clip.retain() if isinstance(clip, MotionFrames) else None
    lock, started, abandoned = threading.Lock(), False, False

    def read():
        nonlocal started
        with lock:
            if abandoned:
                return None  # caller cancelled before the queued CPU reader began
            started = True
        try:
            return work()
        finally:
            if window is not None:
                window.release()

    try:
        return await asyncio.to_thread(read)
    except BaseException:
        with lock:
            if not started and not abandoned:
                abandoned = True
                if window is not None:
                    window.release()
        raise


async def keep_measured(hub, clip: list, reading, scale_words: str, seconds: float, draw: bool = True,
                        origin: str = "brain-default", made_for: str | None = None, thread: str | None = None,
                        check=None):
    """Measure what moves in `clip` through the runner (the "motion" operation) and keep it as a tracks
    item. Returns (item, reused, result): `reused` when the measuring job was shared with another request,
    whose item is used (never presented as a fresh run). Refused, with the reason, before it runs past the
    disk budget or when unsaved evidence fills its allowance."""
    from .capture import captures
    from .operations import Prompt, Unavailable, runner

    store = tracks_of(hub)
    store.admit()
    frame, info = clip[-1]
    cap = captures(hub)
    capture = cap.adopt(info, frame.jpeg)  # one Capture per frame: the bytes read with these facts
    # Refused (with its reason) when full. A researcher's own run holds in its own allowance, never the user's.
    hold = cap.hold(capture, "research" if made_for else "unsaved", "bytes", "motion tracks being kept")
    try:
        try:
            done = await runner(hub).run("motion", capture, Prompt("clip", clip=clip if isinstance(clip, MotionFrames) else tuple(clip)), origin=origin,
                                         params={"umPerPx": round(float(reading.um_per_px), 6) if reading is not None and
                                                 getattr(reading, "um_per_px", None) else None,
                                                 "scaleWords": scale_words, "draw": bool(draw),
                                                 **({"cls": "research"} if made_for else {})}, made_for=made_for)
        except Unavailable as exc:
            raise ToolFailed(f"motion can't be measured here ({exc})",
                             next=getattr(exc, "next", None)) from exc  # next: passed on (what would make it run)
        eid = done.execution.id
        while True:
            if check is not None:
                check()
            found = store.for_execution(eid)
            if found is not None:  # the job's one item, kept by the request it was shared with
                hold.release()
                return found, True, done
            if eid not in store.making:
                break
            # Another request sharing the job is keeping it now: wait for it, holding this one's room meanwhile, so
            # that if it could not (cancelled, failed) this one keeps the measurement itself (the partner's
            # re-check of M3.2, note 1: the second request failed with no item when the first was cancelled).
            await asyncio.shield(store.making[eid])
        making = asyncio.get_running_loop().create_future()
        store.making[eid] = making
        try:
            # Its rows and points are made off the event loop (14 ms for 400 tracks: the partner's review, note 4).
            item = await reading_motion_frames(clip, lambda: from_movement(hub, store, capture, clip, done, reading,
                                                                          seconds, hold, made_for, thread))
            if check is not None:
                check()
            runner(hub).produced(done.execution, item.id)
            store.add(item)
        finally:
            store.making.pop(eid, None)
            if not making.done():
                making.set_result(store.for_execution(eid))
    except BaseException:
        hold.release()
        raise
    store.schedule_save(item)
    return item, bool(done.use.reused), done


async def keep_followed(hub, obj, reading) -> Tracks:
    """A followed outline's trail kept as a tracks item (the follower ran in the background; a request uses
    it now, so it becomes evidence: contract 8), with the view now as its captured view."""
    from .capture import captures, decoding
    from .operations import runner

    trails = getattr(hub, "trails", None)
    trail = trails.trails.get(obj.id) if trails is not None else None
    if trail is None or not any(s.points for s in trail.segments):
        raise ToolFailed(f"{obj.label or obj.id} has no trail: it is not being followed, or has not been for long",
                         next="ask to follow it, then ask again in a few seconds")
    store = tracks_of(hub)
    store.admit()
    info = getattr(hub, "latest_info", None)
    cap = captures(hub)
    capture = cap.get(info.id) if info is not None else None  # the view now, taken before anything is awaited
    if capture is None:
        from .frames import no_picture
        why, next_step = no_picture(hub, "there is no picture from the camera now",
                                     "check the camera is live, then ask again")
        raise ToolFailed(why, next=next_step)
    hold = cap.hold(capture, "unsaved", "bytes", "a trail being kept")
    try:
        execution = runner(hub).background("track", "edgetam-video", capture.id)
        item = from_trail(hub, store, capture, obj, trail, reading, execution, hold)

        def draw() -> bytes | None:
            with decoding(hub, capture, "look", owner="a kept trail's picture") as held:
                bgr = held.bgr()
                if bgr is None:
                    return None
                ok, jpeg = cv2.imencode(".jpg", draw_followed(bgr, item), [cv2.IMWRITE_JPEG_QUALITY, 88])
            return jpeg.tobytes() if ok else None

        picture = await asyncio.to_thread(draw)
        hold.reservation.grow(len(picture or b"") + _arrays_bytes(item.points), "a kept trail's picture and points")
        item.picture = picture
        hold.reservation.settle(len(capture.jpeg or b"") + len(picture or b"") + _arrays_bytes(item.points))
        runner(hub).produced(execution, item.id)
    except BaseException:
        hold.release()
        raise
    store.add(item)
    store.schedule_save(item)
    return item


# --- selecting and colouring tracks (the verbs) ------------------------------------------------------
def column_of(name: str | None) -> str | None:
    if not name:
        return None
    key = " ".join(str(name).strip().lower().replace("by ", "").split())
    return COLUMNS.get(key, key if key in COLUMN_WORDS else None)


def select(item: Tracks, *, ids=None, where: dict | None = None, by: str | None = None, order: str = "desc",
           top: int | None = None, moving: bool | None = None) -> tuple[list[dict], str]:
    """Tracks by id, by measured ranges (`where`: {column: [min, max]}), the moving ones, sorted by a column,
    the first `top`; and the words for how they were chosen."""
    rows = list(item.rows)
    how = []
    if ids:
        wanted = {str(i) for i in ids}
        rows = [r for r in rows if r["id"] in wanted]
        how.append("the ones named")
    if moving is not None:
        rows = [r for r in rows if bool(r["values"].get("moving")) == moving]
        how.append("moving" if moving else "not moving")
    for name, bounds in (where or {}).items():
        column = column_of(name)
        if column is None:
            raise ToolFailed(f"tracks have no {name}", next="say net speed, path speed or straightness")
        lo, hi = (list(bounds) + [None, None])[:2] if isinstance(bounds, (list, tuple)) else (bounds, None)
        rows = [r for r in rows if isinstance(r["values"].get(column), (int, float))
                and (lo is None or r["values"][column] >= float(lo)) and (hi is None or r["values"][column] <= float(hi))]
        how.append(f"{COLUMN_WORDS[column]} in the range given")
    column = column_of(by)
    if by and column is None:
        raise ToolFailed(f"tracks have no {by}", next="say net speed, path speed or straightness")
    if column:
        ascending = str(order or "").strip().lower() in ("asc", "ascending", "slowest", "smallest", "least", "lowest")
        rows = sorted(rows, key=lambda r: float(r["values"].get(column) or 0.0), reverse=not ascending)
        how.append(f"{'lowest' if ascending else 'highest'} {COLUMN_WORDS[column]}")
    if top:
        rows = rows[: int(top)]
        how.append(f"first {int(top)}")
    return rows, ", ".join(how)


def colour_style(item: Tracks, column: str) -> dict:
    """Colour by a measured column: each track's place in the range (0-1) and the legend."""
    numbers = {r["id"]: float(r["values"][column]) for r in item.rows if isinstance(r["values"].get(column), (int, float))}
    if not numbers:
        raise ToolFailed(f"{item.title} has no {COLUMN_WORDS.get(column, column)} measured",
                         next="colour them by net speed or one colour")
    low, high = min(numbers.values()), max(numbers.values())
    span = (high - low) or 1.0
    unit = item.unit if column in ("net", "path") else ""
    return {"colour": "column", "column": column, "values": {k: round((v - low) / span, 3) for k, v in numbers.items()},
            "legend": {"label": COLUMN_WORDS[column], "min": round(low, 3), "max": round(high, 3), "unit": unit}}


# --- the layer over the live view (contract 3) -------------------------------------------------------
class LiveMotionSnapshot:
    """One consumer's held original pixels and detached same-sample observations."""
    def __init__(self, hold, wire):
        self.hold, self.capture = hold, hold.capture
        self.wire = json.loads(json.dumps(wire))

    def release(self):
        self.hold.release()


class LiveMotionLease:
    """A native presentation's visibility claim, not a new source authority."""
    def __init__(self, layer, work, owner):
        self.layer, self.work, self.owner = layer, work, owner
        self.released = False

    def current(self) -> bool:
        """Validate original visibility ownership without acquiring a newer sample."""
        return (not self.released and self.layer._live is self.work
                and self.work.valid() and self.owner in self.work.owners)

    def release(self):
        if not self.released:
            self.released = True
            self.layer._release_live(self.work, self.owner)


class _LiveMotion:
    """One layer-owned producer incarnation, read by the ordered analysis worker."""
    WORK_BYTES = 12 * 1024 * 1024  # 640px scratch, 400x400 assignment and bounded Python tails

    def __init__(self, hub, item):
        from .capture import RUN
        from .ticket import generations
        self.hub, self.item = hub, item
        self.generation = uuid.uuid4().hex
        self.day_generation = generations(hub).value('session')
        address = item.session or item.day  # the index's address of an item: (session, id)
        self.source = {'key': f'live-motion/{RUN}/{address}/{item.id}/{self.generation}',
                       'kind': 'live', 'media': 'labelled', 'availability': 'temporary', 'digest': None,
                       'identity': {'run': RUN, 'resource': {'day': address, 'id': item.id, 'version': None, 'picture': 0},
                                    'producerGeneration': self.generation},
                       'provenance': {'basis': 'current-camera', 'coordinates': 'camera pixels; includes camera/stage motion',
                                      'measuredView': item.view, 'measuredSpan': item.span, 'producer': item.producer}}
        # Detach all source facts from saved-item dictionaries once at creation.
        self.source = json.loads(json.dumps(self.source))
        self.owners = set()
        self.latest = None
        self.reservation = None
        self.tracker = None
        self.basis = None
        self.retired = False
        self.jobs = 0
        self.activation = 0
        self.lock = threading.Lock()

    def valid(self):
        from .ticket import generations
        return not self.retired and generations(self.hub).value('session') == self.day_generation

    def start(self):
        from .capture import captures
        from .movement import CameraTracks
        with self.lock:
            if self.reservation is None:
                self.reservation = captures(self.hub).memory.reserve('analysis', self.WORK_BYTES,
                                                                  'current-camera spot/link state')
            if not self.owners:
                self.tracker, self.basis = CameraTracks(), None
                self.activation += 1

    def stop(self, *, retire=False):
        with self.lock:
            self.retired = self.retired or retire
            self.owners.clear()
            self.activation += 1
            if self.latest is not None:
                self.latest.release()
                self.latest = None
            if not self.jobs and self.reservation is not None:
                self.reservation.release()
                self.reservation, self.tracker, self.basis = None, None, None

    def analyse(self, gray, info, activation):
        """Optional bounded work on already-decoded pixels; never waits for another worker."""
        with self.lock:
            if (not self.valid() or not self.owners or self.tracker is None
                    or activation != self.activation):
                return None
            self.jobs += 1
            tracker = self.tracker
        try:
            basis = (info.width, info.height, info.optics_json, info.boot)
            if self.basis is not None and basis != self.basis:
                self.stop(retire=True)
                return None
            self.basis = basis
            rows, omitted = tracker.update(gray, info)
            return self, activation, rows, omitted
        finally:
            with self.lock:
                self.jobs -= 1
                if not self.owners and not self.jobs and self.reservation is not None:
                    self.reservation.release()
                    self.reservation, self.tracker, self.basis = None, None, None


class TrackLayer:
    """The tracks item shown over the live view, its style, and whether its place is verified now. The
    studio draws its tracks live only while verified, in the item's epoch."""

    def __init__(self, hub, broadcast=None) -> None:
        self.hub = hub
        self.broadcast = broadcast or getattr(hub, "broadcast", lambda payload: None)
        self.shown: str | None = None
        self.style: dict = dict(DEFAULT_STYLE)
        self.confirmed_style: dict = dict(self.style)
        self.style_for: str | None = None
        self.placement: dict = {}  # {"state", "at", "dx", "dy", "why", "score", "frame", "correction"}
        self.view: tuple | None = None  # (epoch, dx, dy) of the shown item's view now (a rejoin may carry it)
        self.correction: tuple[float, float] = (0.0, 0.0)
        self._template: tuple | None = None
        self._template_of: str | None = None
        self._task: asyncio.Task | None = None
        self._sent: tuple = ()
        self.item_wire: dict | None = None  # the shown item's studio data, kept for a studio that reconnects
        self._live: _LiveMotion | None = None
        self._overlay_live: LiveMotionLease | None = None

    def live_source(self, item: Tracks) -> dict:
        """Issue a dormant same-run producer source; showing/opening owns its work."""
        from .capture import RUN
        if item.view.get('run') != RUN:
            raise ToolFailed('an earlier run cannot produce current-camera motion', next=None)  # no next: retained history cannot authorize a new current-camera producer
        if self._live is None or self._live.item is not item or not self._live.valid():
            if self._live is not None:
                self._live.stop(retire=True)
            self._live = _LiveMotion(self.hub, item)
        return json.loads(json.dumps(self._live.source))

    def continuation_source(self, item_id: str) -> dict:
        """Read an existing producer; presentation never silently starts new analysis."""
        if not isinstance(item_id, str) or self._live is None or self._live.item.id != item_id or not self._live.valid():
            raise ToolFailed('that motion has no available current-camera continuation; its retained measurement is still available', next=None)  # no next: the concrete limitation is already stated; no automatic substitute action
        return json.loads(json.dumps(self._live.source))

    def _live_for(self, source):
        wire = source.to_wire() if hasattr(source, 'to_wire') else source
        work = self._live
        if work is None or not work.valid() or wire != work.source:
            raise ToolFailed('this original current-camera producer is no longer available', next=None)  # no next: the original producer incarnation cannot be reused
        return work

    def acquire_live(self, source, owner: str) -> LiveMotionLease:
        """PreparedSources calls after original admission; release on rollback/last close.

        `owner` is an existing delivery/visibility token, never a client assertion.
        Preparing may take a provisional claim; it must release on every failed
        prepare/commit path and on actual retirement. It makes no draw receipt.
        """
        if not isinstance(owner, str) or not owner or len(owner) > 256:
            raise ToolFailed('current-camera display needs a bounded original owner', next=None)  # no next: invalid server ownership identifier
        work = self._live_for(source)
        if owner in work.owners:
            raise ToolFailed('that current-camera visibility owner is already held', next=None)  # no next: the existing original owner must not be acquired twice
        work.start()
        work.owners.add(owner)
        return LiveMotionLease(self, work, owner)

    def _release_live(self, work, owner):
        work.owners.discard(owner)
        if not work.owners:
            work.stop()

    def acquire_snapshot(self, source, owner: str) -> LiveMotionSnapshot | None:
        """Hold the exact current producer sample before an HTTP reader awaits.

        None means no sample yet, not a captured-image substitute. A returned
        consumer copy survives a newer sample and owns its independent release.
        """
        from .capture import captures
        if not isinstance(owner, str) or not owner or len(owner) > 256:
            raise ToolFailed('current-camera sample needs a bounded original reader', next=None)  # no next: invalid server reader identifier
        work = self._live_for(source)
        if not work.owners or work.latest is None:
            return None
        # Current image-track IDs have no finite measured ranks. Qualify the
        # detached sample, leaving the measured layer's configuration intact.
        style = {k: v for k, v in self.style.items() if k not in ('column', 'values', 'legend', 'emphasis')}
        if style.get('colour') == 'column':
            style['colour'] = 'id'
        wire = {**work.latest.wire, 'style': style}
        return LiveMotionSnapshot(captures(self.hub).hold(work.latest.capture, 'analysis', 'look', owner), wire)

    def analysis_work(self):
        """Freeze an existing incarnation at frame intake, before executor await."""
        work = self._live
        if work is not None and (not work.valid() or not getattr(self.hub, 'clients', ())):
            work.stop(retire=True)
            return None
        if work is not None and work.valid() and work.owners and getattr(self.hub, 'clients', ()):
            return work, work.activation
        return None

    def retire_live(self):
        """The existing Hub shutdown retires producer ownership and its sample."""
        if self._live is not None:
            self._live.stop(retire=True)
        self._overlay_live = None

    def publish_live(self, result, capture, sample):
        """Commit a worker result only to its original incarnation and same frame."""
        from .capture import captures
        if result is None:
            return None
        work, activation, rows, omitted = result
        if (work is not self._live or not work.valid() or activation != work.activation
                or not work.owners or not getattr(self.hub, 'clients', ())):
            return None
        wire = {'type': 'motion-tracks', 'space': spaces.of_wire('motion-tracks'), 'id': work.item.id,
                'basis': 'camera-pixels', 'sample': sample, 'title': work.item.title, 'how': 'measured',
                'capture': capture.id, 'epoch': capture.epoch, 'offset': [0, 0],
                'from': min((p[2] for row in rows for seg in row['segments'] for p in seg['points']), default=capture.wall_at),
                'to': capture.wall_at, 'seconds': 0, 'interval': hhmmss(capture.wall_at),
                'unit': 'px/s', 'umPerPx': None, 'scale': None, 'threshold': None,
                'count': len(rows), 'left': omitted, 'tracks': rows}
        wire['seconds'] = wire['to'] - wire['from']
        # Retain one producer sample for the native join. Hold replacement before
        # releasing the old one; HTTP consumers hold separate original copies.
        latest = LiveMotionSnapshot(captures(self.hub).hold(capture, 'analysis', 'bytes', 'current-camera motion sample'), wire)
        if work.latest is not None:
            work.latest.release()
        work.latest = latest
        if self._overlay_live is not None and self.shown == work.item.id:
            self.item_wire = wire
            self.placement = {'state': 'verified', 'frame': capture.id, 'at': time.time()}
            self.view, self.correction = (capture.epoch, capture.dx, capture.dy), (0, 0)
            self.broadcast(wire)
            self.broadcast(self.wire())
            return wire
        return None  # native-only owners do not replace the direct layer's finite data

    def item(self) -> Tracks | None:
        return tracks_of(self.hub).get(self.shown)

    def verified(self) -> bool:
        return self.placement.get("state") == "verified"

    def show(self, item: Tracks, wire: dict | None = None, *, current_camera: bool | None = None) -> None:
        """Show `item`; `wire`, its studio data made beforehand off the event loop (its points may be read from
        disk), else made here."""
        if self._overlay_live is not None:
            self._overlay_live.release()
            self._overlay_live = None
        live = current_camera is not False and self._live is not None and self._live.item is item
        if live and not self._live.valid():
            raise ToolFailed('the original current-camera producer ended; request a new continuation', next=None)  # no next: the message requests a new continuation
        if live:
            self._overlay_live = self.acquire_live(self._live.source, 'motion-overlay')
        if self.style_for != item.id:
            # Another item: per-track values and a selection were about the old one's tracks; opacity and one
            # colour are the look of the layer and stay.
            style = {k: v for k, v in self.style.items() if k not in ("emphasis", "values", "legend")}
            if style.get("colour") == "column":
                style.update(colour_style(item, style.get("column") or "net") if item.rows else {"colour": "column"})
            self.style, self.confirmed_style, self.style_for = style, dict(style), item.id
        if live and self.style.get('colour') == 'column':
            self.style = {**self.style, 'colour': 'id'}
            self.confirmed_style = dict(self.style)
        if live:
            # Finite measured-member IDs/columns are not current image-track
            # identities. Preserve opacity/uniform colour, never borrow ranks.
            self.style = {k: v for k, v in self.style.items() if k not in ('values', 'legend', 'emphasis', 'column')}
            self.confirmed_style = dict(self.style)
        self.shown, self.placement, self._sent = item.id, {}, ()
        self._template, self._template_of = None, None
        self.correction, self.view = (0.0, 0.0), None
        self.item_wire = None if live else wire if wire is not None else tracks_of(self.hub).wire(item)
        if self.item_wire is not None:
            self.broadcast(self.item_wire)
        self.broadcast(self.wire())
        self.start()

    def hide(self) -> None:
        if self._overlay_live is not None:
            self._overlay_live.release()
            self._overlay_live = None
        if self.shown is not None:
            self.shown = None
            self.placement = {}
            self.broadcast(self.wire())

    def placement_words(self) -> str:
        if self._overlay_live is not None:
            return 'current-camera trails; image-coordinate motion includes camera/stage motion'
        if self.verified():
            return "shown over the live view, verified in place"
        why = self.placement.get("why") or "not checked yet"
        return f"on the layer, not drawn on the live view now ({why}); kept on the view it was measured on"

    def current_camera(self) -> bool:
        return self._overlay_live is not None

    def current_camera_words(self) -> str:
        if self._overlay_live is not None and not self._overlay_live.work.valid():
            return 'the original current-camera continuation ended; request a new continuation'
        return 'current-camera continuation enabled; samples draw only on their exact camera frames'

    def wire(self) -> dict:
        view = self.view
        wire = {"type": "tracks-layer", "space": spaces.of_wire("tracks-layer"), "tracks": self.shown, "style": dict(self.style), "verified": self.verified(),
                "why": "" if self.verified() else str(self.placement.get("why") or ""),
                "correction": [round(self.correction[0], 1), round(self.correction[1], 1)],
                "view": {"epoch": view[0], "offset": [round(view[1], 2), round(view[2], 2)]} if view is not None else None}

        screen = getattr(self.hub, "screen", None)
        last_change = screen.last_change(f"tracks:{self.shown}") if screen is not None and self.shown else None
        if last_change is not None:
            wire["lastChange"] = last_change
        return wire

    def start(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._task is None or self._task.done():
            self._task = loop.create_task(self._loop())

    async def _loop(self) -> None:
        while self.shown is not None:
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass  # a failed check leaves it unverified; the next one tries again
            await asyncio.sleep(CHECK_EVERY_S)

    async def check(self) -> None:
        """One pass: the shown item's own view, around its tracks, matched against the picture now where the
        slide's move puts it. Checked and not there: unverified at once (never "moved"). Not checkable this
        pass (out of the picture): an earlier check holds only for CHECK_VALID_S and CHECK_TRAVEL_PX."""
        if self._overlay_live is not None:
            if not self._overlay_live.work.valid():
                self.placement = {'state': 'unverified', 'why': 'the original current-camera producer ended'}
                self.broadcast(self.wire())
            return  # current samples are paired with their JPEG, never matched to a historical template
        from .apply import objective_now, preconditions
        from .capture import RUN, Refused, captures
        from .population import _ViewOf

        item, now = self.item(), getattr(self.hub, "latest_info", None)
        if item is None or now is None:
            return
        store = captures(self.hub)
        latest = store.get(now.id)  # the view now, taken before anything is awaited (ADR 0010)
        view = _ViewOf(item, getattr(self.hub, "epoch_links", None), now.epoch)
        self.view = (view.epoch, view.dx, view.dy)
        why = preconditions(view, now, objective_now(self.hub))
        if not why and item.view.get("run") != RUN:  # none recorded: can't tell, so not this run's (ADR 0012)
            # Epochs and offsets start again with every start: one from before a restart is not this run's.
            why = "they were measured before the app restarted, so where they are on the slide now is not known"
        if not why and not getattr(now, "tracked", True):
            why = "the slide's motion was not registered on the view now"
        got = None  # (verified, score, correction) when checked this pass
        why_unchecked = ""
        if not why and latest is not None and latest.jpeg:
            if self._template_of != item.id:
                template = await asyncio.to_thread(self._make_template, item, view)
                if self.shown != item.id:
                    return
                self._template, self._template_of = template, item.id
            if isinstance(self._template, str):
                why = self._template  # why their place can't be checked by their view (near the edge, too flat...)
            else:
                try:
                    with store.hold(latest, "analysis", "hold", "checking the shown tracks"):  # its decode is counted
                        gray = await asyncio.to_thread(lambda: _half(latest.gray()))
                except Refused as exc:
                    gray, why = None, f"their place could not be checked now ({exc})"
                if gray is not None:
                    shift = (float(now.dx) - view.dx, float(now.dy) - view.dy)
                    fov = getattr(now, "fov", None)
                    disc_now = (fov.cx, fov.cy, fov.radius) if fov is not None else None
                    got = await asyncio.to_thread(_match, gray, self._template, shift, disc_now)
                    if self.shown != item.id:
                        return
                    if isinstance(got, str):
                        why_unchecked, got = got, None  # not checkable this pass: said, and the window decides
        t = time.time()
        old = self.placement
        if got is not None and got[0]:
            self.correction = got[2]
            self.placement = {"state": "verified", "at": t, "dx": float(now.dx), "dy": float(now.dy), "score": round(got[1], 3),
                              "frame": getattr(now, "id", None), "correction": got[2], "residual": got[2]}
        elif got is not None:
            self.correction = (0.0, 0.0)
            self.placement = {"state": "unverified", "at": t, "dx": float(now.dx), "dy": float(now.dy),
                              "score": round(got[1], 3), "frame": getattr(now, "id", None), "residual": got[2],
                              "why": "its view's pixels did not match where the slide's move puts them"}
        else:
            lapsed = bool(why) or old.get("state") != "verified" or t - float(old.get("at") or 0.0) > CHECK_VALID_S or \
                math.hypot(float(now.dx) - float(old.get("dx") or 0.0), float(now.dy) - float(old.get("dy") or 0.0)) > CHECK_TRAVEL_PX
            if lapsed:
                self.correction = (0.0, 0.0)
                self.placement = {"state": "unverified", "at": t, "dx": float(now.dx), "dy": float(now.dy), "frame": None,
                                  "why": why or why_unchecked or "their place could not be checked on the view now"}
        if got is not None:
            # Retain the actual pair and raw residual even when rejected. A failed
            # match means unverified, not proof that the specimen/stage moved.
            try:
                tracks_of(self.hub).log({"kind": "tracks-placement", "id": item.id, "captureFrame": item.capture_id,
                                        "frame": getattr(now, "id", None), "epoch": now.epoch, "viewEpoch": view.epoch,
                                        "checkSeq": now.seq, "checkBoot": now.boot, "checkAt": now.wall_at,
                                        "checkTracked": now.tracked,
                                        "checkGeometry": {"width": now.width, "height": now.height, "discPx": now.disc},
                                        "offset": [float(now.dx), float(now.dy)], "viewOffset": [view.dx, view.dy],
                                        "state": self.placement["state"], "score": self.placement["score"],
                                        "residual": list(got[2]), "why": self.placement.get("why", "")})
            except Exception:
                pass  # optional diagnostics must never prevent publishing unverified state
        current = (self.verified(), str(self.placement.get("why") or ""), (round(self.correction[0]), round(self.correction[1])),
                   (self.view[0], round(self.view[1], 1), round(self.view[2], 1)) if self.view else None)
        if current != self._sent:
            self._sent = current
            self.broadcast(self.wire())

    def _make_template(self, item: Tracks, view) -> tuple | str:
        """The item's own view around its tracks (capture px, padded, at least MIN_REGION_PX), at half resolution,
        with a mask of the part inside 95% of the eyepiece (the disc itself, not the square inscribed in it, so
        tracks near its edge are checked too): (pixels, (x, y) of its corner at half resolution, mask), or why
        their place can't be checked by it."""
        from .capture import reserving

        jpeg = tracks_of(self.hub).capture_jpeg(item)
        if jpeg is None:
            return "the view they were measured on can't be read"
        epoch, dx, dy = item.view.get("epoch"), float(item.view.get("dx") or 0.0), float(item.view.get("dy") or 0.0)
        pts = [arr[:, :2] for row, segs in zip(item.rows, tracks_of(self.hub).points(item))
               for meta, arr in zip(row["segments"], segs) if meta["epoch"] == epoch and len(arr)]
        if not pts:
            return "none of them is of the view they were kept with"
        allp = np.concatenate(pts) + (dx, dy)
        x0, y0 = allp.min(axis=0) - PAD_PX
        x1, y1 = allp.max(axis=0) + PAD_PX
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        half_w, half_h = max((x1 - x0) / 2, MIN_REGION_PX / 2), max((y1 - y0) / 2, MIN_REGION_PX / 2)
        w, h = int(item.view.get("width") or 0), int(item.view.get("height") or 0)
        bx0, by0, bx1, by1 = 0.0, 0.0, float(w), float(h)
        disc = item.view.get("disc")
        if disc:  # within the eyepiece's bounding box; the mask below keeps the disc itself
            bx0, by0 = max(bx0, disc[0] - disc[2]), max(by0, disc[1] - disc[2])
            bx1, by1 = min(bx1, disc[0] + disc[2]), min(by1, disc[1] + disc[2])
        ax0, ay0 = max(bx0, cx - half_w), max(by0, cy - half_h)
        ax1, ay1 = min(bx1, cx + half_w), min(by1, cy + half_h)
        with reserving(self.hub, w, h, "hold", "the shown tracks' template"):  # the capture's decode, counted
            gray = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
            if gray is None:
                return "the view they were measured on can't be read"
            small = _half(gray)
        hx0, hy0 = int(math.ceil(ax0 * SCALE)), int(math.ceil(ay0 * SCALE))
        hx1, hy1 = int(ax1 * SCALE), int(ay1 * SCALE)
        crop = small[hy0:hy1, hx0:hx1].copy() if hx1 > hx0 and hy1 > hy0 else np.zeros((0, 0), np.uint8)
        mask = _inside(crop.shape, (hx0, hy0), disc)
        if crop.size == 0 or int(mask.sum()) < MIN_MASKED_PX:
            return EDGE
        if float(crop[mask].std()) < MIN_TEXTURE:
            return "their view has too little detail around them to check their place by"
        return crop, (hx0, hy0), mask


def _half(gray):
    if gray is None:
        return None
    return cv2.resize(gray, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_AREA)


def _inside(shape, corner, disc) -> np.ndarray:
    """Which pixels of a half-resolution region with its top left at `corner` lie inside 95% of the eyepiece
    `disc` ((cx, cy, r) in full px; None: the whole region)."""
    hh, ww = shape[:2]
    if not disc or hh == 0 or ww == 0:
        return np.ones((hh, ww), bool)
    ys, xs = np.mgrid[0:hh, 0:ww]
    return np.hypot((xs + corner[0]) / SCALE - disc[0], (ys + corner[1]) / SCALE - disc[1]) <= float(disc[2]) * 0.95


def _match(gray: np.ndarray, template: tuple, shift: tuple[float, float], disc_now=None):
    """(verified, score, raw residual in full px) for the region at the place the slide's move puts it, matched by
    its pixels inside the eyepiece both then and now (a masked NCC); or why it can't be checked now (that place is
    out of the picture, or too little of it is inside the eyepiece now)."""
    from .population import _subpixel

    crop, (ox, oy), mask = template
    th, tw = crop.shape[:2]
    h, w = gray.shape[:2]
    fx, fy = ox + shift[0] * SCALE, oy + shift[1] * SCALE
    px, py = int(round(fx)), int(round(fy))
    x0, y0 = px - SEARCH, py - SEARCH
    if x0 < 0 or y0 < 0 or x0 + tw + 2 * SEARCH > w or y0 + th + 2 * SEARCH > h:
        return "the slide has carried them out of the picture"
    use = mask & _inside(crop.shape, (px, py), disc_now)  # inside the eyepiece where they were and where they are now
    if int(use.sum()) < MIN_MASKED_PX:
        return EDGE
    window = gray[y0:y0 + th + 2 * SEARCH, x0:x0 + tw + 2 * SEARCH]
    if use.all():
        res = cv2.matchTemplate(window, crop, cv2.TM_CCOEFF_NORMED)
    else:
        res = cv2.matchTemplate(window, crop, cv2.TM_CCOEFF_NORMED, mask=use.astype(np.uint8) * 255)
    res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
    _, score, _, loc = cv2.minMaxLoc(res)
    sx, sy = _subpixel(res, loc)
    off = ((x0 + sx - fx) / SCALE, (y0 + sy - fy) / SCALE)
    ok = bool(score >= TRACKS_SCORE and math.hypot(*off) <= AT_PLACE_PX)
    return ok, float(score), (float(off[0]), float(off[1]))


def track_layer_of(hub) -> TrackLayer:
    found = getattr(hub, "track_layer", None)
    if not isinstance(found, TrackLayer):
        found = TrackLayer(hub)
        try:
            hub.track_layer = found
        except AttributeError:
            pass
    return found


# --- showing (the tool's and the verbs' one way) -----------------------------------------------------
def drawn_live(row: dict) -> bool:
    """The studio's actual draw receipt, for either registered or paired-frame trails."""
    placement = row.get("placement") or ""
    return bool(row.get("visible") and (placement == DRAWN or placement.startswith("current-camera trails on frame ")))


async def show(services, item: Tracks, *, current_camera: bool | None = None) -> str:
    """Show `item` over the live view (through apply: a superseded request shows nothing), checked in place
    first; done only once the studio's report shows it (flow 7). Not verified: shown on its captured view,
    in the magnifier, labelled as that. Words for the answer."""
    from .apply import apply
    from .screen import on_screen, screen_of

    hub = services.hub
    layer = track_layer_of(hub)
    wire = await asyncio.to_thread(tracks_of(hub).wire, item)  # its points may be read from disk: off the loop
    after = screen_of(hub).reported_at
    done = await apply(hub, lambda _: layer.show(item, wire, current_camera=current_camera), scope="motion")
    if not done.ok:
        return f"kept, not shown ({done.reason})"
    if layer.current_camera():
        from .screen import Screen
        screen = getattr(hub, "screen", None)
        target = f"tracks:{item.id}"
        if isinstance(screen, Screen) and screen.showing():
            drew = await screen.until(lambda report: any(row.get("id") == target and drawn_live(row)
                for row in report.get("items", [])), after, DRAWN_WAIT_S)
            if not drew:
                return "current-camera continuation enabled, but the studio has not yet confirmed drawing a paired frame"
            return ("the studio confirmed current-camera motion trails drawn over the live camera view; "
                    "they continue on matching new camera frames. The saved measurement remains unchanged")
        return layer.current_camera_words()
    await layer.check()  # the first check of their place, on the view now
    if layer.shown != item.id:
        return "kept; something else was shown since"
    if layer.verified():
        return await drawn_or_their_view(services, item, after)
    return await on_their_view(services, item, str(layer.placement.get("why") or ""))


async def drawn_or_their_view(services, item: Tracks, after: float) -> str:
    """Verified in place: "drawn over the live view" only once the studio reports them drawn there (its
    placement DRAWN, not merely the layer visible: the partner's review of M3.2, S1); otherwise their captured
    view, and why."""
    from .screen import Screen

    target = f"tracks:{item.id}"

    def drawn(report) -> bool:
        return any(i.get("id") == target and i.get("visible") and i.get("placement") == DRAWN for i in report.get("items", []))

    screen = getattr(services.hub, "screen", None)
    # A studio drawing them reports it within a frame or two (it reports every 200 ms at most, and at once when
    # asked), so their own view is shown after DRAWN_WAIT_S, not the 4 s a change waits (the re-check's note 2).
    if isinstance(screen, Screen) and screen.showing() and not await screen.until(drawn, after, DRAWN_WAIT_S):
        return await on_their_view(services, item, "the studio has not drawn them on the live view")
    return "drawn over the live view where their place is checked (older parts fainter, breaks never joined)"


async def on_their_view(services, item: Tracks, why: str) -> str:
    """The tracks on the view they were measured on (contract 2's captured view), in the magnifier,
    labelled as not the live view."""
    from .screen import picture_shown

    hub = services.hub
    magnifier = getattr(services, "magnifier", None)
    picture = await asyncio.to_thread(tracks_of(hub).picture, item)
    reason = f" ({why})" if why else ""
    if magnifier is None or picture is None:
        return f"not drawn on the live view now{reason}"
    label = (f"{item.title}: drawn by the app on the view it was measured on ({hhmmss(item.view.get('at'))}), "
             "not the live view")
    try:
        await picture_shown(magnifier, picture, label)
    except ToolFailed as exc:
        return f"not drawn on the live view now{reason}; its own view: {exc.said()}"
    return f"shown on the view it was measured on, in the magnifier, not the live view{reason}"


def lines(hub, thread: str | None = None) -> list[tuple[float, str]]:
    """The tracks the brain sees on its board (contract 9): the one shown over the live view, and this
    conversation's, each with when it was made (newest first is the caller's)."""
    from .population import current_generation

    store = getattr(hub, "tracks_store", None)
    if not isinstance(store, TracksStore):
        return []
    layer = getattr(hub, "track_layer", None)
    shown = layer.item() if isinstance(layer, TrackLayer) else None
    out = []
    now = time.time()
    for item in store.items.values():
        basis = item.measurement_basis(now)
        if shown is not None and item.id == shown.id:
            line = item.line(layer.placement_words()) + "; " + basis
            if layer.current_camera():
                line += ("; current trails do not update these counts or speeds; "
                         "current image-coordinate spot links are separate from saved measured track identities")
            if layer._live is not None and layer._live.item is item and layer._live.valid():
                line += f'; independent moving window source={{"motion":"{item.id}","live":true}}; saved picture source={{"day":"{item.session or item.day}","id":"{item.id}","version":null,"picture":0}}'
            out.append((float("inf"), line))
        elif item.made_for:
            continue  # measured for a researcher's own reading: never on the user's board unless shown (#186's rule)
        elif thread is None or (item.thread == thread and current_generation(hub, item.generation)):
            out.append((float(item.made_at), item.line("not shown") + "; " + basis))
    return out
