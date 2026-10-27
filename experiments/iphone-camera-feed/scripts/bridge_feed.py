"""What the live feed may play besides the camera and test footage: a session's own recording (one of Record's
datasets) and any recording or picture under a media root, each checked before it plays. The switch itself stays in
iphone_bridge_server.py (switch_feed): it replaces the main module's SIMULATED_CAMERA."""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from bridge_recording import sidecar_path


class SwitchSteps:
    """How long each step of a feed switch, seek or play took, in milliseconds and in the order taken (L1c): its answer
    says them, so a slow switch says where its time went. `mark(name)` ends the step `name` (the time since the last
    mark), `said()` is every step with the total."""

    def __init__(self) -> None:
        self.started = self.last = time.perf_counter()
        self.ms: dict[str, float] = {}

    def mark(self, name: str) -> None:
        now = time.perf_counter()
        self.ms[name] = round(self.ms.get(name, 0.0) + (now - self.last) * 1000, 1)
        self.last = now

    def said(self) -> dict[str, float]:
        return {**self.ms, "total": round((time.perf_counter() - self.started) * 1000, 1)}

    def words(self) -> str:
        return ", ".join(f"{name} {ms:g} ms" for name, ms in self.said().items())


# A session's recording (POST /feed?source=recording): the camera video of one of Record's datasets, in the app's
# datasets folder (--datasets-dir). Only an id the app gives a dataset, and only a part file inside that folder.
DATASET_ID = re.compile(r"^ds-\d{8}-\d{6}(-\d+)?$")


def _when_words(epoch: float) -> str:
    """"Oct 12, 1:44 am", as the studio's recordings list says it (the orchestrator's datasets/recordings.py)."""
    text = time.strftime("%b %-d, %-I:%M %p", time.localtime(epoch))
    return text[:-2] + text[-2:].lower()


def dataset_recording(args: argparse.Namespace, dataset: str, part: object) -> tuple[Path, str]:
    """A session's recording to play: `<datasets-dir>/<dataset>/video/part-<n>.mp4`, and its title (the dataset's
    name, else "Recording · Oct 12, 1:44 am", the part's start). ValueError, in words, for an unknown dataset or
    part, or a path that leads outside the datasets folder."""
    folder = getattr(args, "datasets_dir", None)
    if not folder:
        raise ValueError("This bridge was started without --datasets-dir, so it plays no session's recording")
    if not DATASET_ID.match(str(dataset or "")):
        raise ValueError(f"No dataset called {dataset!r}")
    try:
        n = int(str(part))
    except ValueError:
        n = 0
    if n < 1:
        raise ValueError("part is a recording's number, from 1")
    root = Path(folder).expanduser().resolve()
    video = (root / str(dataset) / "video" / f"part-{n}.mp4").resolve()
    if root not in video.parents:
        raise ValueError(f"Dataset {dataset}'s recording {n} is not in the datasets folder")
    if not video.is_file():
        raise ValueError(f"Dataset {dataset} has no recording {n}" if (root / str(dataset)).is_dir()
                         else f"No dataset called {dataset!r}")
    try:
        summary = json.loads((root / str(dataset) / "dataset.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        summary = {}
    summary = summary if isinstance(summary, dict) else {}
    name = " ".join(str(summary.get("name") or "").split())
    return video, name or f"Recording · {_when_words(recording_started(video, summary, n) or video.stat().st_mtime)}"


def recording_started(video: Path, summary: dict, n: int) -> float | None:
    """When a dataset's recording part began: its sidecar's start, else when the dataset noticed it, else the
    dataset's own start (the same order as the orchestrator's datasets/recordings.py)."""
    try:
        with sidecar_path(video).open("rb") as handle:
            header = json.loads(handle.readline() or b"{}")
        if isinstance(header, dict) and isinstance(header.get("startedAtEpoch"), (int, float)):
            return float(header["startedAtEpoch"])
    except (OSError, ValueError):
        pass
    for entry in (summary.get("video") or {}).get("parts") or []:
        if isinstance(entry, dict) and entry.get("n") == n and isinstance(entry.get("noticedAtEpoch"), (int, float)):
            return float(entry["noticedAtEpoch"])
    started = summary.get("startedAtEpoch")
    return float(started) if isinstance(started, (int, float)) else None


# Any recording or picture of the user's (POST /feed?source=asset): a file inside one of the bridge's media roots, and
# nothing outside them, a link that leads outside included. The orchestrator names the file (its library knows where
# each asset is); the bridge only checks it may play it.
def media_roots(args: argparse.Namespace) -> list[Path]:
    """The folders the bridge may play any file from: every --media-root, then configured datasets, footage and recordings."""
    roots: list[Path] = []
    for folder in [*(getattr(args, "media_root", None) or []), getattr(args, "datasets_dir", None),
                   getattr(args, "footage_dir", None), getattr(args, "recording_dir", None)]:
        if folder:
            root = Path(folder).expanduser().resolve()
            if root not in roots:
                roots.append(root)
    return roots


def asset_file(args: argparse.Namespace, path: str, title: str = "") -> tuple[Path, str]:
    """An asset to play: the file at `path` (absolute, its links followed to where it really is) and its title (the
    one given, else the file's name). ValueError, in words, for a path outside every media root, a file that is
    neither a recording nor a picture, or one that isn't there. A path outside the roots is refused before anything
    else is said about it, so the feed never tells whether a file exists elsewhere."""
    from simulated_camera import STILL_SUFFIXES, VIDEO_SUFFIXES

    text = str(path or "")
    if not text or not Path(text).is_absolute():
        raise ValueError("path is the file's absolute path")
    file = Path(text).resolve()
    if not any(root in file.parents for root in media_roots(args)):
        raise ValueError(f"{text} is not in a folder this bridge plays from (its --media-root folders, the datasets "
                         "folder, the footage folder and the configured recordings folder)")
    if file.suffix.lower() not in VIDEO_SUFFIXES | STILL_SUFFIXES:
        raise ValueError(f"Only recordings ({', '.join(sorted(VIDEO_SUFFIXES))}) and pictures "
                         f"({', '.join(sorted(STILL_SUFFIXES))}) can be played")
    if not file.is_file():
        raise ValueError(f"There is no file at {text}")
    return file, " ".join(str(title or "").split()) or Path(text).name
