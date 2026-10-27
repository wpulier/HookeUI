"""What moves on the slide, besides the slide: small things found in each frame, linked into
tracks, measured (docs/design/2026-10-06-capability-menu.md, "Motion"; tool `track_motion`).

Measurements, not verdicts: how many things were followed, how far and how fast each went,
how straight, and whether they share one direction (a drift of the liquid). Whether that means
"alive", "dying" or "flowing" is the model's reading of the numbers.

How (option MOTION "blobs"):
- Each frame is shrunk to WORK_WIDTH and its small dark or bright spots are found: the frame
  minus a blurred copy of itself, above a few times the frame's own noise, inside the eyepiece.
- Their centres are moved into the first frame's coordinates by the slide's own offset (the
  hub's dx, dy), so moving the slide is not counted as the things moving.
- Centres are linked frame to frame by the nearest match within a step limit (Hungarian
  assignment), allowing a short gap.
- Speeds come from the path smoothed over SMOOTH frames (the centre of a small spot jitters by
  a pixel, which at 30 frames a second would read as speed).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import cv2
import numpy as np

WORK_WIDTH = 640  # frames are measured at this width
SMOOTH = 5  # frames in the moving average of a path
MAX_STEP_PX = 5.0  # the farthest a thing may go between two frames, at WORK_WIDTH (~250 px/s full size at 30 fps)
GAP = 2  # frames a track may miss and still continue
MIN_TRACK_S = 1.0  # a track must be followed this long to count
MOVING_UM_S = 5.0  # net speed that counts as moving, with a scale
MOVING_PX_S = 8.0  # the same without a scale, in full-size pixels a second


@dataclass
class ClipFrame:
    t: float  # seconds (wall clock)
    image: "np.ndarray | Callable[[], np.ndarray]"  # grayscale, full size; or a loader, decoded when used (one at a time)
    dx: float  # the slide's cumulative offset (full-size px)
    dy: float
    frame: int | None = None  # the frame's id in this run (kept with each place a track is seen at)


@dataclass
class Track:
    times: list[float] = field(default_factory=list)
    points: list[tuple[float, float]] = field(default_factory=list)  # full-size px, in the first frame's coordinates
    missed: int = 0
    frames: list = field(default_factory=list)  # the frame each point was seen on (ClipFrame.frame), beside `times`

    @property
    def duration(self) -> float:
        return self.times[-1] - self.times[0] if len(self.times) > 1 else 0.0


@dataclass
class Movement:
    seconds: float
    frames: int
    fps: float
    tracks: list[Track]
    counted: list[dict]  # per track: path, net speed (units/s), straightness, direction
    unit: str  # "µm/s" or "px/s"
    threshold: float
    slide_speed: float  # how fast the whole view moved (units/s): the slide or the liquid as a whole
    drift: dict | None  # the movers' shared direction, when they share one

    @property
    def moving(self) -> list[dict]:
        return [c for c in self.counted if c["net"] >= self.threshold]


def _spots(gray_small: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Centres (x, y) of the small spots in a shrunk frame."""
    img = cv2.GaussianBlur(gray_small.astype(np.float32), (0, 0), 1.0)
    background = cv2.GaussianBlur(img, (0, 0), 6.0)
    diff = np.abs(img - background)
    inside = diff[mask > 0]
    if inside.size == 0:
        return np.zeros((0, 2), np.float32)
    noise = float(np.median(np.abs(inside - np.median(inside)))) * 1.4826 + 1e-3
    binary = ((diff > max(4.0 * noise, 3.0)) & (mask > 0)).astype(np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, _, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)
    keep = [i for i in range(1, n) if 3 <= stats[i, cv2.CC_STAT_AREA] <= 400]
    return centroids[keep].astype(np.float32) if keep else np.zeros((0, 2), np.float32)


def _disc_mask(shape, fov, scale: float) -> np.ndarray:
    h, w = shape
    mask = np.zeros((h, w), np.uint8)
    if fov is None:
        mask[int(h * 0.05):int(h * 0.95), int(w * 0.05):int(w * 0.95)] = 1
        return mask
    cv2.circle(mask, (int(fov.cx * scale), int(fov.cy * scale)), max(1, int(fov.radius * scale * 0.9)), 1, -1)
    return mask


def follow(frames: list[ClipFrame], fov=None) -> list[Track]:
    """Spots in every frame, linked into tracks (full-size px, in the first frame's coordinates)."""
    if len(frames) < 2:
        return []
    def image(f: ClipFrame) -> np.ndarray:
        return f.image() if callable(f.image) else f.image

    h, w = image(frames[0]).shape[:2]
    scale = min(1.0, WORK_WIDTH / w)
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    mask = _disc_mask((size[1], size[0]), fov, scale)
    x0, y0 = frames[0].dx, frames[0].dy
    live: list[Track] = []
    done: list[Track] = []
    step = MAX_STEP_PX / scale  # in full-size px
    for f in frames:
        full = image(f)
        small = cv2.resize(full, size, interpolation=cv2.INTER_AREA) if scale < 1 else full
        del full
        found = _spots(small, mask) / scale
        # Into the first frame's coordinates: a slide point at p then is at p + (offset now - offset then) now.
        found = found - np.array([f.dx - x0, f.dy - y0], np.float32) if len(found) else found
        live, ended = _link(live, found, f.t, f.frame, step)
        done.extend(ended)
    return [t for t in done + live if t.duration >= MIN_TRACK_S]


def _link(live: list[Track], found: np.ndarray, at: float, frame: int | None, step: float):
    """The shared spot assignment, without choosing a coordinate basis."""
    from scipy.optimize import linear_sum_assignment

    used = set()
    if live and len(found):
        heads = np.array([t.points[-1] for t in live], np.float32)
        cost = np.linalg.norm(heads[:, None, :] - found[None, :, :], axis=2)
        limit = step * (1 + np.array([t.missed for t in live]))[:, None]
        cost = np.where(cost <= limit, cost, 1e6)
        rows, cols = linear_sum_assignment(cost)
        for r, c in zip(rows, cols):
            if cost[r, c] < 1e6:
                live[r].times.append(at)
                live[r].points.append((float(found[c, 0]), float(found[c, 1])))
                live[r].frames.append(frame)
                live[r].missed = -1
                used.add(c)
    still, ended = [], []
    for track in live:
        track.missed += 1
        (still if track.missed <= GAP else ended).append(track)
    return still + [Track([at], [(float(p[0]), float(p[1]))], frames=[frame])
                    for i, p in enumerate(found) if i not in used], ended


class CameraTracks:
    """Bounded rolling image-coordinate observations; never stage-relative speeds.

    Used only on the hub's existing ordered analysis worker, with its already
    decoded grey image. Gaps or epochs break tails; no biological identity is
    inferred by these nearest-spot associations.
    """
    MAX_TRACKS = 400
    MAX_POINTS = 60
    WINDOW_S = 4.0

    def __init__(self):
        self.live: list[Track] = []
        self.ids = {}
        self.next_id = 0
        self.last = None

    def update(self, gray, info):
        if self.last is not None and (info.epoch != self.last.epoch or info.boot != self.last.boot
                                     or not 0 < info.wall_at - self.last.wall_at <= .25):
            self.live, self.ids = [], {}
        self.last = info
        h, w = gray.shape
        scale = min(1.0, WORK_WIDTH / w)
        size = (max(1, round(w * scale)), max(1, round(h * scale)))
        small = cv2.resize(gray, size, interpolation=cv2.INTER_AREA) if scale < 1 else gray
        found = _spots(small, _disc_mask(small.shape, info.fov, scale)) / scale
        omitted = max(0, len(found) - self.MAX_TRACKS)
        self.live, _ = _link(self.live, found[:self.MAX_TRACKS], info.wall_at, info.id, MAX_STEP_PX / scale)
        # Bound association inputs and Python history independently of elapsed
        # time or a client's redraw frequency. Lost heads are never current.
        self.live = self.live[-self.MAX_TRACKS:]
        rows, ids = [], {}
        for track in self.live:
            retained = [i for i, at in enumerate(track.times) if info.wall_at - at <= self.WINDOW_S][-self.MAX_POINTS:]
            track.times = [track.times[i] for i in retained]
            track.points = [track.points[i] for i in retained]
            track.frames = [track.frames[i] for i in retained]
            if not track.points:
                continue
            key = id(track)
            tid = self.ids.get(key)
            if tid is None:
                self.next_id += 1
                tid = f'live-t{self.next_id}'
            ids[key] = tid
            if track.missed:
                continue
            moved = track.duration >= MIN_TRACK_S and math.dist(track.points[0], track.points[-1]) > 1
            rows.append({'id': tid, 'moving': moved, 'net': None, 'path': None, 'straightness': None,
                         'segments': [{'start': 'first', 'end': 'open',
                                       'points': [[round(x, 2), round(y, 2), at]
                                                  for (x, y), at in zip(track.points, track.times)]}]})
        self.ids = ids
        return rows, omitted


def _smoothed(points: np.ndarray) -> np.ndarray:
    if len(points) < SMOOTH:
        return points
    kernel = np.ones(SMOOTH) / SMOOTH
    return np.stack([np.convolve(points[:, i], kernel, mode="valid") for i in range(2)], axis=1)


def measure(frames: list[ClipFrame], tracks: list[Track], um_per_px: float | None) -> Movement:
    k = um_per_px or 1.0
    unit = "µm/s" if um_per_px else "px/s"
    threshold = MOVING_UM_S if um_per_px else MOVING_PX_S
    counted = []
    for t in tracks:
        p = _smoothed(np.array(t.points, np.float32))
        seconds = t.duration * (len(p) - 1) / max(1, len(t.points) - 1) or t.duration
        path = float(np.sum(np.linalg.norm(np.diff(p, axis=0), axis=1))) if len(p) > 1 else 0.0
        net_vec = p[-1] - p[0]
        net = float(np.linalg.norm(net_vec))
        counted.append({"path": path * k / seconds if seconds else 0.0, "net": net * k / seconds if seconds else 0.0,
                        "straightness": net / path if path > 0 else 0.0,
                        "direction": float(np.degrees(np.arctan2(-net_vec[1], net_vec[0]))),
                        "start": t.points[0], "end": t.points[-1]})
    seconds = frames[-1].t - frames[0].t if len(frames) > 1 else 0.0
    slide = (np.hypot(frames[-1].dx - frames[0].dx, frames[-1].dy - frames[0].dy) * k / seconds) if seconds else 0.0
    movement = Movement(seconds, len(frames), (len(frames) - 1) / seconds if seconds else 0.0, tracks, counted, unit,
                        threshold, float(slide), None)
    movers = movement.moving
    if len(movers) >= 5:
        angles = np.radians([c["direction"] for c in movers])
        mean = np.array([np.cos(angles).mean(), np.sin(angles).mean()])
        spread = float(np.linalg.norm(mean))  # 1: all one way; near 0: every which way
        if spread >= 0.6:
            movement.drift = {"direction": float(np.degrees(np.arctan2(mean[1], mean[0]))), "share": spread}
    return movement


def speed_statistics(m: Movement) -> dict:
    """Rates across the complete measured population, before row/display rounding.

    Each eligible track contributes once; neither its duration nor point count is
    a weight. The moving subset uses the measurement's original net threshold.
    """
    def means(rows: list[dict]) -> dict:
        count = len(rows)
        return {"count": count,
                "netMean": round(math.fsum(float(r["net"]) for r in rows) / count, 4) if count else None,
                "pathMean": round(math.fsum(float(r["path"]) for r in rows) / count, 4) if count else None}

    return {"method": "arithmetic mean of per-track rates", "unit": m.unit,
            "basis": f"slide-compensated paths smoothed over {SMOOTH} frames; tracks followed for at least "
                     f"{MIN_TRACK_S:g} s; each track counted once",
            "allCounted": means(m.counted), "moving": means(m.moving)}


def speed_statistics_words(statistics: dict) -> str:
    """The same compact factual projection for immediate and retained readers."""
    unit = statistics["unit"]
    out = []
    for key, label in (("allCounted", "All {count} counted {tracks}"), ("moving", "{count} moving {tracks}")):
        rates = statistics[key]
        count = rates["count"]
        population = label.format(count=count, tracks="track" if count == 1 else "tracks")
        if count:
            out.append(f"{population}: arithmetic mean net speed {rates['netMean']:g} {unit}; "
                       f"path speed {rates['pathMean']:g} {unit}.")
        else:
            out.append(f"{population}: arithmetic mean unavailable (no tracks).")
    return " ".join(out)


def _compass(degrees: float) -> str:
    names = ["right", "up and right", "up", "up and left", "left", "down and left", "down", "down and right"]
    return names[int(((degrees % 360) + 22.5) // 45) % 8]


def words(m: Movement, scale_words: str = "") -> str:
    """The measurements, as the model is given them."""
    averages = speed_statistics_words(speed_statistics(m))
    if not m.counted:
        return (f"Watched {m.seconds:.1f} s ({m.frames} frames): nothing was followed for {MIN_TRACK_S:.0f} s or more "
                f"(no small moving or still spots could be tracked). {averages}")
    movers = m.moving
    n, k = len(m.counted), len(movers)
    out = [f"Watched {m.seconds:.1f} s ({m.frames} frames, {m.fps:.0f} a second){', ' + scale_words if scale_words else ''}.",
           averages,
           f"Followed {n} small things for at least {MIN_TRACK_S:.0f} s; {k} of them ({100 * k / n:.0f}%) moved at "
           f"{m.threshold:g} {m.unit} or more net."]
    if movers:
        net = np.array([c["net"] for c in movers])
        path = np.array([c["path"] for c in movers])
        straight = np.array([c["straightness"] for c in movers])
        out.append(f"Those moving: net speed median {np.median(net):.0f} {m.unit} (fastest tenth {np.percentile(net, 90):.0f}); "
                   f"speed along their path median {np.median(path):.0f} {m.unit}; straightness (net / path) "
                   f"median {np.median(straight):.2f}.")
    still = [c for c in m.counted if c["net"] < m.threshold]
    if still:
        out.append(f"The others: net speed median {np.median([c['net'] for c in still]):.1f} {m.unit} "
                   f"(path speed {np.median([c['path'] for c in still]):.0f}: jiggling in place reads as path, not net).")
    out.append(f"The whole view moved {m.slide_speed:.0f} {m.unit} (the slide, or all the liquid together)."
               if m.slide_speed >= m.threshold else "The view as a whole held still.")
    if m.drift:
        out.append(f"The moving ones mostly went {_compass(m.drift['direction'])} together (agreement "
                   f"{m.drift['share']:.2f} of 1): a shared drift of the liquid, or a current.")
    elif len(movers) >= 5:
        out.append("The moving ones went every which way (no shared drift).")
    return " ".join(out)


def trails(bgr: np.ndarray, m: Movement, offset: tuple[float, float]) -> np.ndarray:
    """The last frame with each track drawn on it, coloured by net speed (blue slow, red fast).
    `offset`: the last frame's slide offset minus the first's, to draw first-frame coordinates on it."""
    out = bgr.copy()
    if not m.counted:
        return out
    top = max(max(c["net"] for c in m.counted), m.threshold * 2)
    for track, c in zip(m.tracks, m.counted):
        heat = min(1.0, c["net"] / top)
        color = (int(255 * (1 - heat)), int(80 + 100 * (1 - abs(heat - 0.5) * 2)), int(255 * heat))
        pts = (np.array(track.points, np.float32) + np.array(offset, np.float32)).astype(np.int32)
        cv2.polylines(out, [pts.reshape(-1, 1, 2)], False, color, 2 if c["net"] >= m.threshold else 1, cv2.LINE_AA)
        if c["net"] >= m.threshold:
            cv2.circle(out, tuple(int(v) for v in pts[-1]), 4, color, -1, cv2.LINE_AA)
    return out
