"""Eyepiece tours: any big picture of a slide, or any microscope video, as footage that looks
like a phone held to an eyepiece, with the truth about every frame.

A tour of a still pans a round eyepiece view across the picture the way a hand moves a slide:
it drifts from one thing to the next, rests on it, now and then whip-pans, and blurs while it
moves fast. A video is played inside the eyepiece as it is. Either way the result is an MP4 for
`footage/` (the studio's camera menu and the sim harness play it through the bridge, exactly as
the phone) and a `.truth.json` beside it: for every frame, where the view is on the slide, how
fast it moves, and the box of every labelled thing in the eyepiece, in frame pixels.

    python -m bench.corpus.tour --still slide.jpg --objects objects.json --out footage/blood-tour.mp4
    python -m bench.corpus.tour --video paramecium.webm --specimen paramecium --out footage/paramecium.mp4

Objects are [{"label": "...", "box": [x0, y0, x1, y1]}] in the picture's pixels (bench/corpus/
adapters.py reads them from datasets). Everything is seeded: the same inputs give the same tour.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

WIDTH, HEIGHT, FPS = 1280, 720, 30


@dataclass
class Look:
    """How the phone sees the eyepiece."""
    width: int = WIDTH
    height: int = HEIGHT
    fps: int = FPS
    disc: float = 0.46  # eyepiece radius as a share of the frame height
    zoom: float = 1.0  # frame pixels per picture pixel
    vignette: float = 0.35  # darkening towards the eyepiece's edge, 0-1
    jpeg: int = 70  # the phone's compression
    noise: float = 3.0  # sensor noise (grey levels)
    blur_per_px: float = 0.25  # motion blur length per px/frame of slide speed


@dataclass
class Tour:
    """A hand moving a slide: rest on a thing, drift to the next, sometimes whip-pan."""
    seconds: float = 40.0
    rest: tuple[float, float] = (1.5, 4.0)  # seconds resting on a thing
    drift_px_s: float = 220.0  # view speed while drifting, picture pixels per second
    whip_every: int = 5  # a whip-pan (fast, blurred, continuity lost) every this many moves
    whip_px_s: float = 2500.0
    seed: int = 7
    path: list = field(default_factory=list)  # [(t, x, y)] the view centre over time, picture pixels


def disc_mask(look: Look) -> np.ndarray:
    """1 inside the eyepiece, 0 outside, with a soft edge and the eyepiece's vignetting."""
    yy, xx = np.mgrid[0:look.height, 0:look.width].astype(np.float32)
    d = np.hypot(xx - look.width / 2, yy - look.height / 2) / (look.disc * look.height)
    inside = np.clip((1.0 - d) * 40.0, 0.0, 1.0)  # soft rim, a few px
    vignette = 1.0 - look.vignette * np.clip(d, 0, 1) ** 2
    return (inside * vignette).astype(np.float32)


def plan_path(size: tuple[int, int], objects: list[dict], tour: Tour, look: Look) -> list[tuple[float, float, float]]:
    """The view centre over time: resting on things (in a seeded order) and moving between them."""
    rng = np.random.default_rng(tour.seed)
    w, h = size
    view_r = look.disc * look.height / look.zoom  # eyepiece radius in picture pixels
    lo, hi = np.array([view_r, view_r]), np.array([max(view_r, w - view_r), max(view_r, h - view_r)])
    stops = [np.clip(np.array([(o["box"][0] + o["box"][2]) / 2, (o["box"][1] + o["box"][3]) / 2])
                     + rng.normal(0, view_r * 0.25, 2), lo, hi) for o in objects]
    if not stops:
        stops = [rng.uniform(lo, hi) for _ in range(8)]
    rng.shuffle(stops)
    t, pos, path, moves = 0.0, np.array(stops[0], float), [], 0
    path.append((t, *pos))
    i = 1
    while t < tour.seconds:
        t += float(rng.uniform(*tour.rest))
        path.append((t, *pos))  # resting
        target = np.array(stops[i % len(stops)], float)
        i += 1
        moves += 1
        speed = tour.whip_px_s if tour.whip_every and moves % tour.whip_every == 0 else tour.drift_px_s
        t += float(np.linalg.norm(target - pos)) / speed
        pos = target
        path.append((t, *pos))
    return path


def centre_at(path, t: float) -> tuple[float, float]:
    """The view centre at time t, eased between waypoints (a hand speeds up and slows down)."""
    for (t0, x0, y0), (t1, x1, y1) in zip(path, path[1:]):
        if t0 <= t <= t1:
            u = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            u = u * u * (3 - 2 * u)
            return x0 + (x1 - x0) * u, y0 + (y1 - y0) * u
    return path[-1][1], path[-1][2]


def objects_in_view(objects: list[dict], cx: float, cy: float, look: Look) -> list[dict]:
    """Labelled things whose centre is inside the eyepiece, as frame-pixel boxes."""
    out, r = [], look.disc * look.height
    for o in objects:
        x0, y0, x1, y1 = o["box"]
        fx0, fy0 = (x0 - cx) * look.zoom + look.width / 2, (y0 - cy) * look.zoom + look.height / 2
        fx1, fy1 = (x1 - cx) * look.zoom + look.width / 2, (y1 - cy) * look.zoom + look.height / 2
        if math.hypot((fx0 + fx1) / 2 - look.width / 2, (fy0 + fy1) / 2 - look.height / 2) <= r * 0.95:
            out.append({"label": o["label"], "box": [round(fx0, 1), round(fy0, 1), round(fx1, 1), round(fy1, 1)]})
    return out


def phone(frame: np.ndarray, mask: np.ndarray, look: Look, rng, speed_px: float, angle: float) -> np.ndarray:
    """What the phone records: motion blur along the pan, the eyepiece, noise, compression."""
    if speed_px * look.blur_per_px >= 1.5:
        n = int(min(61, speed_px * look.blur_per_px)) | 1
        kernel = np.zeros((n, n), np.float32)
        c = n // 2
        dx, dy = math.cos(angle) * c, math.sin(angle) * c
        cv2.line(kernel, (int(round(c - dx)), int(round(c - dy))), (int(round(c + dx)), int(round(c + dy))), 1.0, 1)
        frame = cv2.filter2D(frame, -1, kernel / max(kernel.sum(), 1.0))
    out = frame.astype(np.float32) * mask[..., None]
    if look.noise:
        out += rng.normal(0, look.noise, out.shape).astype(np.float32)
    out = np.clip(out, 0, 255).astype(np.uint8)
    ok, jpeg = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, look.jpeg])
    return cv2.imdecode(jpeg, cv2.IMREAD_COLOR) if ok else out


class Writer:
    """Frames to an H.264 MP4 through FFmpeg."""

    def __init__(self, out: Path, look: Look) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg is required (brew install ffmpeg)")
        out.parent.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(
            [ffmpeg, "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{look.width}x{look.height}",
             "-r", str(look.fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
             str(out)], stdin=subprocess.PIPE)

    def write(self, frame: np.ndarray) -> None:
        self.proc.stdin.write(frame.tobytes())

    def close(self) -> None:
        self.proc.stdin.close()
        if self.proc.wait() != 0:
            raise RuntimeError("ffmpeg failed")


def tour_still(image: np.ndarray, objects: list[dict], out: Path, *, specimen: str = "", source: dict | None = None,
               look: Look | None = None, tour: Tour | None = None) -> dict:
    """Pan an eyepiece across a still picture. Returns the truth (also written beside `out`)."""
    look, tour = look or Look(), tour or Tour()
    h, w = image.shape[:2]
    path = tour.path or plan_path((w, h), objects, tour, look)
    mask, rng = disc_mask(look), np.random.default_rng(tour.seed)
    n = int(tour.seconds * look.fps)
    frames, writer, prev = [], Writer(out, look), None
    for i in range(n):
        t = i / look.fps
        cx, cy = centre_at(path, t)
        speed = 0.0 if prev is None else math.hypot(cx - prev[0], cy - prev[1]) * look.zoom
        angle = 0.0 if prev is None else math.atan2(cy - prev[1], cx - prev[0])
        m = np.float32([[look.zoom, 0, look.width / 2 - cx * look.zoom], [0, look.zoom, look.height / 2 - cy * look.zoom]])
        view = cv2.warpAffine(image, m, (look.width, look.height), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
        writer.write(phone(view, mask, look, rng, speed, angle))
        frames.append({"t": round(t, 3), "centre": [round(cx, 1), round(cy, 1)], "speed": round(speed, 1),
                       "objects": objects_in_view(objects, cx, cy, look)})
        prev = (cx, cy)
    writer.close()
    return _truth(out, "still", specimen, source, look, frames)


def tour_video(video: Path, out: Path, *, specimen: str = "", source: dict | None = None, look: Look | None = None,
               seconds: float | None = None, seed: int = 7) -> dict:
    """Play a microscope video inside the eyepiece (filling the eyepiece, centred)."""
    look = look or Look()
    cap = cv2.VideoCapture(str(video))  # a file on disk, never the phone (AGENTS.md)
    fps = cap.get(cv2.CAP_PROP_FPS) or look.fps
    mask, rng = disc_mask(look), np.random.default_rng(seed)
    writer, frames, i = Writer(out, look), [], 0
    limit = int((seconds or 1e9) * look.fps)
    src_i, frame = -1, None  # the source frame shown, and its picture
    while i < limit:
        want = int(i * fps / look.fps)  # resample to the look's frame rate
        while src_i < want:
            ok, got = cap.read()
            if not ok:
                break
            frame, src_i = got, src_i + 1
        if frame is None or src_i < want:
            break  # the video has ended
        fh, fw = frame.shape[:2]
        side = 2 * look.disc * look.height
        scale = side / min(fh, fw)
        view = np.zeros((look.height, look.width, 3), np.uint8)
        resized = cv2.resize(frame, (int(fw * scale), int(fh * scale)), interpolation=cv2.INTER_AREA)
        rh, rw = resized.shape[:2]
        x0, y0 = (look.width - rw) // 2, (look.height - rh) // 2
        sx0, sy0 = max(0, -x0), max(0, -y0)
        dx0, dy0 = max(0, x0), max(0, y0)
        cw, ch = min(rw - sx0, look.width - dx0), min(rh - sy0, look.height - dy0)
        view[dy0:dy0 + ch, dx0:dx0 + cw] = resized[sy0:sy0 + ch, sx0:sx0 + cw]
        writer.write(phone(view, mask, look, rng, 0.0, 0.0))
        frames.append({"t": round(i / look.fps, 3), "source_frame": src_i, "objects": []})
        i += 1
    cap.release()
    writer.close()
    return _truth(out, "video", specimen, source, look, frames)


def _truth(out: Path, kind: str, specimen: str, source: dict | None, look: Look, frames: list[dict]) -> dict:
    truth = {"kind": kind, "specimen": specimen, "source": source or {}, "fps": look.fps,
             "size": [look.width, look.height], "disc": {"cx": look.width / 2, "cy": look.height / 2,
                                                         "r": look.disc * look.height},
             "frames": frames}
    out.with_suffix(".truth.json").write_text(json.dumps(truth, separators=(",", ":")))
    return truth


def main() -> None:
    parser = argparse.ArgumentParser(description="Render an eyepiece tour of a still, or a video in the eyepiece.")
    parser.add_argument("--still")
    parser.add_argument("--objects", help="JSON list of {label, box} in the still's pixels")
    parser.add_argument("--video")
    parser.add_argument("--specimen", default="")
    parser.add_argument("--out", required=True)
    parser.add_argument("--seconds", type=float, default=40.0)
    parser.add_argument("--zoom", type=float, default=1.0, help="frame pixels per picture pixel")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    out = Path(args.out)
    if args.still:
        image = cv2.imread(args.still, cv2.IMREAD_COLOR)
        objects = json.loads(Path(args.objects).read_text()) if args.objects else []
        tour_still(image, objects, out, specimen=args.specimen, look=Look(zoom=args.zoom),
                   tour=Tour(seconds=args.seconds, seed=args.seed))
    elif args.video:
        tour_video(Path(args.video), out, specimen=args.specimen, seconds=args.seconds, seed=args.seed)
    else:
        parser.error("--still or --video")
    print(f"wrote {out} and {out.with_suffix('.truth.json')}")


if __name__ == "__main__":
    main()
