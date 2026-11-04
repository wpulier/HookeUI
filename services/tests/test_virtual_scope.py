"""The virtual microscope of the scenario harness (sim/virtual_scope.py) and the camera bridge playing its view as the
camera (`POST /sim/stage`, simulated_camera.py): the optics (the picture moves opposite to the slide), hands that move
smoothly, the truth only the harness reads, and its stream through the bridge's own FFmpeg command."""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from sim.virtual_scope import STEPS_PER_Z, VirtualScope, slide_from_frame

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "experiments" / "iphone-camera-feed" / "scripts"))
from simulated_camera import SimulatedCamera, stage_url  # noqa: E402

FFMPEG = shutil.which("ffmpeg")


def textured(seed: int = 3, size: int = 900) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = cv2.GaussianBlur(rng.uniform(0, 255, (size, size)).astype(np.float32), (0, 0), 6)
    base = cv2.normalize(base, None, 40, 220, cv2.NORM_MINMAX)
    return cv2.cvtColor(base.astype(np.uint8), cv2.COLOR_GRAY2BGR)


def test_a_slide_is_made_of_a_frames_own_picture_with_room_to_move():
    frame = np.zeros((720, 1280, 3), np.uint8)
    frame[300:420, 600:800] = 200
    slide, origin = slide_from_frame(frame, (700.0, 360.0), 300.0, pad=2)
    side = 2 * (int(300 / math.sqrt(2)) - 4)
    assert slide.shape[:2] == (5 * side, 5 * side)
    # the frame's own pixel lands where `origin` says
    assert slide[int(360 + origin[1]), int(700 + origin[0])].tolist() == [200, 200, 200]


def test_the_picture_moves_opposite_to_the_slide_and_the_truth_follows():
    scope = VirtualScope(textured(), size=(640, 480), disc=(320.0, 240.0, 200.0), zoom=1.5, noise=0)
    point = (500.0, 420.0)
    before = scope.where(*point)
    assert scope.slide_point(*before) == pytest.approx(point)
    ends = scope.hand(dx=40.0)  # the hands move the slide right
    assert scope.moving()
    time.sleep(max(0.0, ends - time.monotonic()) + 0.02)
    after = scope.where(*point)
    assert after[0] == pytest.approx(before[0] - 40.0 * 1.5) and after[1] == pytest.approx(before[1])
    assert not scope.moving()
    scope.hand(dz=0.5, z_speed=100.0)
    time.sleep(0.05)
    assert scope.truth()["z"] == pytest.approx(0.5) and scope.truth()["blur"] == pytest.approx(0.8)


def test_a_turned_phone_turns_the_picture():
    scope = VirtualScope(textured(), size=(640, 480), disc=(320.0, 240.0, 200.0), zoom=1.0, turn=1, noise=0)
    x, y, _ = scope.position()
    assert scope.where(x + 10, y) == pytest.approx((320.0, 250.0))  # right on the slide shows down in the picture
    assert scope.slide_point(320.0, 250.0) == pytest.approx((x + 10, y))


def test_stop_holds_the_slide_where_it_is_and_the_stage_keeps_to_its_limits():
    scope = VirtualScope(textured(), size=(640, 480), disc=(320.0, 240.0, 200.0), limits=(400, 400, 500, 500), start=(450, 450))
    scope.hand(dx=1000.0, speed=200.0)
    time.sleep(0.1)
    scope.halt()
    x, _, _ = scope.position()
    time.sleep(0.1)
    assert scope.position()[0] == pytest.approx(x) and 450 < x <= 500
    scope.hand(dx=-10_000.0, speed=1e6)
    time.sleep(0.08)  # a move takes at least 0.05 s
    assert scope.position()[0] == pytest.approx(400.0)


def test_only_a_stream_on_this_mac_can_stand_in_for_the_camera():
    assert stage_url("http://127.0.0.1:18990/stream.mjpeg")
    for bad in ("https://127.0.0.1:1/x", "http://example.com:80/x", "file:///etc/passwd", "rtsp://127.0.0.1:1/x",
                "http://127.0.0.1/stream.mjpeg", "nonsense"):
        with pytest.raises(ValueError):
            stage_url(bad)
    with pytest.raises(ValueError):
        SimulatedCamera("ignored", "Phone", stage="http://10.0.0.2:8080/stream.mjpeg")


def test_the_bridge_reads_a_stage_as_a_live_stream_with_nothing_to_pause_or_seek():
    camera = SimulatedCamera("ignored", "Phone", still_fps=30.0, stage="http://127.0.0.1:18990/stream.mjpeg")
    args = SimpleNamespace(fps=30, size="1280x720", jpeg_quality=5)
    command = camera.capture_command("ffmpeg", args)
    i = command.index("-i")
    assert command[i - 4:i + 2] == ["-f", "mpjpeg", "-use_wallclock_as_timestamps", "1", "-i",
                                    "http://127.0.0.1:18990/stream.mjpeg"]
    assert "-re" not in command and "-ss" not in command
    camera.pause()
    camera.seek(100)
    assert camera.playback() == {"seconds": 0.0, "duration": 0.0, "paused": False, "still": False, "live": True}
    assert camera.status()["stage"] == "http://127.0.0.1:18990/stream.mjpeg"


@pytest.mark.skipif(FFMPEG is None, reason="FFmpeg is not installed")
def test_the_stage_reaches_the_bridges_ffmpeg_and_moving_the_slide_moves_the_picture():
    scope = VirtualScope(textured(), size=(640, 480), disc=(320.0, 240.0, 200.0), zoom=1.0, fps=20, noise=0)
    scope.serve()
    try:
        camera = SimulatedCamera("ignored", "Phone", still_fps=20.0, stage=f"{scope.url}/stream.mjpeg")
        command = camera.capture_command(FFMPEG, SimpleNamespace(fps=10, size="640x480", jpeg_quality=3))
        frames: list[bytes] = []
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        def read() -> None:
            buf = b""
            while proc.poll() is None:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                buf += chunk
                while True:
                    a, b = buf.find(b"\xff\xd8"), buf.find(b"\xff\xd9")
                    if a < 0 or b < 0 or b < a:
                        break
                    frames.append(buf[a:b + 2])
                    buf = buf[b + 2:]

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        deadline = time.monotonic() + 8
        while len(frames) < 4 and time.monotonic() < deadline:
            time.sleep(0.05)
        first = cv2.imdecode(np.frombuffer(frames[-1], np.uint8), cv2.IMREAD_GRAYSCALE)
        ends = scope.hand(dx=30.0, speed=1e5)
        time.sleep(max(0.0, ends - time.monotonic()) + 0.6)
        n = len(frames)
        while len(frames) < n + 2 and time.monotonic() < deadline + 4:
            time.sleep(0.05)
        second = cv2.imdecode(np.frombuffer(frames[-1], np.uint8), cv2.IMREAD_GRAYSCALE)
        proc.terminate()
        proc.wait(5)
        assert first.shape == (480, 640)
        a, b = (f[140:340, 220:420].astype(np.float32) for f in (first, second))  # inside the eyepiece disc
        (shift_x, shift_y), _ = cv2.phaseCorrelate(a, b, cv2.createHanningWindow((200, 200), cv2.CV_32F))
        assert shift_x == pytest.approx(-30.0, abs=3.0) and abs(shift_y) < 3.0  # the picture moved left
    finally:
        scope.close()


def test_the_openflexure_like_api_moves_reports_and_focuses():
    scope = VirtualScope(textured(), size=(320, 240), disc=(160.0, 120.0, 100.0), z=0.8, fps=10)
    scope.serve()
    try:
        def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(scope.url + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())

        status, start = call("GET", "/api/v2/instrument/state/stage/position")
        assert status == 200 and start["z"] == int(0.8 * STEPS_PER_Z)
        status, action = call("POST", "/api/v2/actions/stage/move", {"x": 120, "y": -60, "z": 0})
        assert status == 201 and action["status"] in ("running", "completed")
        deadline = time.monotonic() + 3
        while action["status"] != "completed" and time.monotonic() < deadline:
            time.sleep(0.02)
            _, action = call("GET", action["href"])
        assert action["status"] == "completed"
        assert action["output"]["x"] == start["x"] + 120 and action["output"]["y"] == start["y"] - 60
        _, focus = call("POST", "/api/v2/extensions/org.openflexure.autofocus/fast_autofocus", {})
        time.sleep(0.4)
        _, focus = call("GET", focus["href"])
        assert focus["status"] == "completed" and abs(scope.truth()["z"]) < 1e-6
        with urllib.request.urlopen(scope.url + "/api/v2/streams/snapshot", timeout=5) as r:
            assert r.headers["Content-Type"] == "image/jpeg" and r.read()[:2] == b"\xff\xd8"
    finally:
        scope.close()
