"""A camera recording uses libx264 where FFmpeg has it, else VideoToolbox (the Mac app's LGPL FFmpeg)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "experiments/iphone-camera-feed/scripts"))

import bridge_common  # noqa: E402

from orchestrator import recordings  # noqa: E402

GPL = " V....D libx264              libx264 H.264 / AVC\n V....D h264_videotoolbox    VideoToolbox H.264 Encoder\n"
LGPL = " V....D h264_videotoolbox    VideoToolbox H.264 Encoder (codec h264)\n V....D mjpeg  MJPEG\n"


def listing(text):
    return lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=text, stderr="")


def test_bridge_prefers_libx264_and_falls_back_to_videotoolbox(monkeypatch):
    monkeypatch.setattr(bridge_common, "ffmpeg_path", lambda: "ffmpeg")
    for text, codec in ((GPL, "libx264"), (LGPL, "h264_videotoolbox"), ("", "libx264")):
        monkeypatch.setattr(bridge_common, "_H264", None)
        monkeypatch.setattr(bridge_common.subprocess, "run", listing(text))
        encoder = bridge_common.h264_encoder()
        assert encoder["codec"] == codec and encoder["args"][:2] == ["-c:v", codec]


def test_studio_recording_remux_falls_back_to_videotoolbox(monkeypatch):
    monkeypatch.setattr(recordings.subprocess, "run", listing(GPL))
    assert recordings._h264()[:2] == ["-c:v", "libx264"]
    monkeypatch.setattr(recordings.subprocess, "run", listing(LGPL))
    assert recordings._h264() == ["-c:v", "h264_videotoolbox", "-q:v", "65"]
