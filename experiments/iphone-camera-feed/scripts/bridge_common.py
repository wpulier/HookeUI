"""What the camera bridge's modules share (iphone_bridge_server.py and the modules beside it): where things are, the
time in words, FFmpeg and its stderr, and ModuleView, the window through which the modules beside the main one reach
its live state."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections import deque
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = ROOT_DIR.parents[1]
# The camera page, built from the studio's code (apps/studio/src/camera, vite.camera.config.ts). The bridge serves
# only it and its own routes: nothing else in this folder (recordings, the manifest) is reachable over HTTP.
CAMERA_PAGE_DIR = REPO_DIR / "apps" / "studio" / "dist-camera"
CAMERA_PAGE_MISSING = (b"<!doctype html><meta charset=utf-8><title>Camera</title><body style='font:15px -apple-system,sans-serif;"
                       b"background:#1c1e20;color:#edeeef;padding:32px'><h1 style='font-size:20px'>The camera page isn't built</h1>"
                       b"<p>Start ObservationUI with <code>make start</code> (it builds the page), then reload.</p>")
# The test assets store, shared by every checkout (services/orchestrator/assets.py, assets/README.md).
ASSETS_FOOTAGE = Path(os.environ.get("MICROMIND_ASSETS") or Path.home() / "MicroscopeMind").expanduser() / "footage"
# Its recordings' titles, specimens and optics: the repository's manifest (public: it lists none of anyone's own), and
# the store's private one (untracked: its owner's recordings), read over it (footage_media.Library).
ASSETS_MANIFEST = REPO_DIR / "assets" / "manifest.json"
PRIVATE_MANIFEST = ASSETS_FOOTAGE.parent / "manifest.json"
CORPUS_MANIFEST = REPO_DIR / "services" / "bench" / "corpus" / "corpus.json"
# FFmpeg's stderr: the last lines of each capture are kept for /api/health, and the first
# ones are logged (an FFmpeg that repeats a warning every frame must not flood the log).
STDERR_TAIL_LINES = 8
STDERR_LOG_LINES = 40


class ModuleView:
    """A module's names as they are at each lookup. The bridge's live state (SIMULATED_CAMERA, STREAM_SOURCE) and the
    functions a test may replace (make_device_snapshot, native_capture_ready...) are the main module's
    (iphone_bridge_server.py); the routes and the bridge's mixins, in modules of their own, reach them through its
    view (`main`), so they see what it holds now. A test loads the main module afresh and replaces names on it, and
    the modules beside it, imported once, see that too."""

    def __init__(self, namespace: dict) -> None:
        self._namespace = namespace

    def __getattr__(self, name: str) -> object:
        try:
            return self._namespace[name]
        except KeyError:
            raise AttributeError(name) from None


def wall_timestamp(epoch_seconds: float) -> str | None:
    if not epoch_seconds:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(epoch_seconds))


def ffmpeg_path() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("ffmpeg is required. Install with: brew install ffmpeg")
    return path


# The H.264 encoder of a camera recording: libx264 where this FFmpeg has it (a GPL build such as Homebrew's), else
# Apple's VideoToolbox, which an LGPL build has (the Mac app's, packaging/macos). Asked of FFmpeg once.
_H264 = None


def h264_encoder() -> dict:
    """The recording's encoder: its settings as the sidecar says them, and its FFmpeg arguments (`args`)."""
    global _H264
    if _H264 is None:
        try:
            listed = subprocess.run([ffmpeg_path(), "-hide_banner", "-encoders"], capture_output=True, text=True,
                                    timeout=10).stdout
        except (OSError, RuntimeError, subprocess.SubprocessError):
            listed = ""
        if " libx264 " in listed or " h264_videotoolbox " not in listed:
            _H264 = {"codec": "libx264", "crf": 20, "preset": "veryfast", "tune": "zerolatency",
                     "args": ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-crf", "20"]}
        else:
            _H264 = {"codec": "h264_videotoolbox", "quality": 65, "realtime": True,
                     "args": ["-c:v", "h264_videotoolbox", "-realtime", "1", "-q:v", "65"]}
    return _H264


def drain_stderr(process: subprocess.Popen[bytes], tail: deque, label: str, redact=None) -> None:
    """Read an FFmpeg's stderr to the end: keep its last lines, log the first ones (through
    `redact`, when its input URL holds a secret)."""
    if process.stderr is None:
        return
    logged = 0
    try:
        for raw in iter(process.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if redact is not None:
                line = redact(line)
            if not line:
                continue
            tail.append(line)
            if logged < STDERR_LOG_LINES:
                logged += 1
                print(f"[{label} {process.pid}] {line}", flush=True)
                if logged == STDERR_LOG_LINES:
                    print(f"[{label} {process.pid}] (further output not logged; last lines in /api/health)", flush=True)
    except (OSError, ValueError):
        pass


def with_stderr(message: str, tail: deque) -> str:
    """A failure message with FFmpeg's last stderr line, when it said anything."""
    last = tail[-1] if tail else ""
    return f"{message}: {last}" if last else message


def jpeg_size(frame: bytes) -> tuple[int, int] | None:
    """A JPEG's width and height from its frame header (SOF0/SOF1/SOF2), without decoding it."""
    index = 2
    while index + 9 < len(frame) and index < 65536:
        if frame[index] != 0xFF:
            return None
        marker = frame[index + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            return (frame[index + 7] << 8 | frame[index + 8], frame[index + 5] << 8 | frame[index + 6])
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        index += 2 + (frame[index + 2] << 8 | frame[index + 3])
    return None
