"""The camera bridge's capture lifecycle, with a fake capture (no camera).

The bridge is a standalone stdlib script (experiments/iphone-camera-feed/scripts), loaded
here by path. Each fake capture is a small Python process standing in for FFmpeg.
Recording durability tests use real FFmpeg with explicitly generated image inputs.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.waiting import HANG_S, until

SCRIPTS = Path(__file__).resolve().parents[2] / "experiments/iphone-camera-feed/scripts"

# Fake FFmpegs. Each carries a marker so the test can count live ones.
SILENT = "import time,sys; sys.argv; time.sleep(60)  # {marker}"
IGNORES_SIGTERM = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)  # {marker}"
FRAMES = (
    "import sys,time\n"
    "while True:\n"
    "    sys.stdout.buffer.write(b'\\xff\\xd8' + b'x'*100 + b'\\xff\\xd9'); sys.stdout.buffer.flush(); time.sleep(1/30)\n"
    "# {marker}"
)
FAILS = "import sys; sys.stderr.write('[avfoundation] Could not lock device for configuration\\n'); sys.exit(1)  # {marker}"


@pytest.fixture()
def bridge_module(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(f"bridge_under_test_{time.monotonic_ns()}", SCRIPTS / "iphone_bridge_server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    present = {"ok": True}
    resets: list[float] = []
    monkeypatch.setattr(module, "make_device_snapshot", lambda target: {"ok": present["ok"], "targetDevice": target})
    monkeypatch.setattr(module, "reset_continuity_capture_agent", lambda: resets.append(time.monotonic()) or {"attempted": True, "ok": True})
    module._test_present = present
    module._test_resets = resets
    monkeypatch.setattr(module, "CONTINUITY_SETTLE_S", 0.0)
    # The FFmpeg capture path unless a test asks for the capture app (test_camera_link.py).
    monkeypatch.setattr(module, "native_capture_ready", lambda: False)
    # The Mac's own facts (Desk View, USB) are read by the tests that need them.
    monkeypatch.setattr(module, "desk_view_running", lambda: False)
    monkeypatch.setattr(module, "iphone_on_usb", lambda: None)
    return module


def make_bridge(module, tmp_path, script: str, **overrides):
    monkey_argv = ["bridge", "--footage-dir", str(tmp_path)]
    old_argv = sys.argv
    sys.argv = monkey_argv
    try:
        args = module.parse_args()
    finally:
        sys.argv = old_argv
    for key, value in overrides.items():
        setattr(args, key, value)
    bridge = module.CameraBridge(args)
    bridge._ensure_monitor_thread_locked = lambda: None  # the tests tick the monitor themselves
    marker = f"fake-ffmpeg-{time.monotonic_ns()}"
    bridge.ffmpeg_command = lambda: [sys.executable, "-c", script.format(marker=marker)]
    bridge._test_marker = marker
    return bridge


def live_fakes(marker: str) -> int:
    out = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout.split()
    return len(out)


@pytest.fixture()
def cleanup():
    bridges = []
    yield bridges
    for bridge in bridges:
        bridge.stop()


def test_wait_for_frame_waits_when_no_capture_runs(bridge_module, tmp_path, cleanup, wall_clock):
    bridge = make_bridge(bridge_module, tmp_path, SILENT)
    cleanup.append(bridge)
    t0 = time.monotonic()
    assert bridge.wait_for_frame(0, timeout=0.5) == (0, None)
    assert time.monotonic() - t0 >= 0.45  # no busy return: a stream subscriber must not spin
    t0 = time.monotonic()
    assert bridge.wait_for_frame(0, timeout=0.5, return_if_stopped=True) == (0, None)
    wall_clock.within(time.monotonic() - t0, 0.1, "the reconnect")  # a reconnect still learns at once that nothing runs


def test_retired_reader_cannot_seed_the_picture_held_after_a_paused_seek(bridge_module, tmp_path, monkeypatch):
    from tests.test_sim_faults import camera
    from types import SimpleNamespace

    bridge = make_bridge(bridge_module, tmp_path, SILENT)
    cam = camera()
    cam.fps, cam.total_frames = 30.0, 900
    cam.video, cam._capture_starts = tmp_path / 'recording.mp4', []
    cam.paused_frame = 10
    old, current = SimpleNamespace(pid=101), SimpleNamespace(pid=102)
    bridge.process = current
    monkeypatch.setattr(bridge_module, 'SIMULATED_CAMERA', cam)
    cam.seek(600)
    previous = b'\xff\xd8previous view\xff\xd9'
    sought = b'\xff\xd8requested view\xff\xd9'
    assert not bridge._extract_frames(bytearray(previous), old)
    assert cam._held is None
    assert bridge._extract_frames(bytearray(sought), current)
    assert bridge.latest_frame == sought and cam._held == sought
    assert cam.playback()['seconds'] == 20.0


def test_health_answers_while_a_stubborn_capture_is_stopped(bridge_module, tmp_path, cleanup):
    bridge = make_bridge(bridge_module, tmp_path, IGNORES_SIGTERM)
    cleanup.append(bridge)
    assert bridge._launch("test", replace=False)
    time.sleep(0.3)  # let it install its SIGTERM handler
    stopper = threading.Thread(target=bridge._stop_capture, args=("test stop",))
    stopper.start()
    time.sleep(0.2)
    slowest = 0.0
    while stopper.is_alive():
        t0 = time.monotonic()
        health = bridge.health()
        slowest = max(slowest, time.monotonic() - t0)
        time.sleep(0.05)
    stopper.join()
    assert slowest < 0.2, f"health blocked {slowest:.2f} s while FFmpeg was being stopped"
    assert health["running"] is False
    assert bridge.health()["lastCaptureExit"]["reason"] == "test stop"
    assert live_fakes(bridge._test_marker) == 0


def test_concurrent_restarts_never_run_two_captures(bridge_module, tmp_path, cleanup):
    bridge = make_bridge(bridge_module, tmp_path, IGNORES_SIGTERM)
    cleanup.append(bridge)
    most = [0]
    done = threading.Event()

    def watch():
        while not done.is_set():
            most[0] = max(most[0], live_fakes(bridge._test_marker))
            time.sleep(0.02)

    watcher = threading.Thread(target=watch)
    watcher.start()
    threads = [threading.Thread(target=bridge._launch, args=(f"r{i}", True)) for i in range(3)]
    for thread in threads:
        thread.start()
        time.sleep(0.4)
    for thread in threads:
        thread.join()
    time.sleep(0.3)
    done.set()
    watcher.join()
    assert most[0] == 1
    assert bridge.health()["running"]


def test_new_capture_gets_first_frame_timeout_not_stall_timeout(bridge_module, tmp_path, cleanup):
    bridge = make_bridge(bridge_module, tmp_path, SILENT, first_frame_timeout=1.5, stall_timeout=0.5)
    cleanup.append(bridge)
    bridge._launch("test", replace=False)
    pid = bridge.health()["pid"]
    time.sleep(0.8)
    bridge._monitor_tick()  # past the stall timeout, inside the first-frame allowance
    assert bridge.health()["pid"] == pid and bridge.health()["restartCount"] == 1
    time.sleep(0.9)
    bridge._monitor_tick()
    health = bridge.health()
    assert health["restartCount"] == 2 and health["pid"] != pid
    # A listed phone that sent nothing is healed: Continuity Camera reset, then asked again.
    assert len(bridge_module._test_resets) == 1
    assert "sent nothing" in health["lastError"] and health["lastHeal"]["heal"] == 1


def test_flowing_capture_that_stalls_is_restarted_after_stall_timeout(bridge_module, tmp_path, cleanup):
    bridge = make_bridge(bridge_module, tmp_path, FRAMES, stall_timeout=0.5)
    cleanup.append(bridge)
    bridge._launch("test", replace=False)
    _, frame = bridge.wait_for_frame(0, timeout=3)
    assert frame
    assert bridge.health()["state"] == "live"
    pid = bridge.health()["pid"]
    import os
    import signal

    os.kill(pid, signal.SIGSTOP)  # alive but silent, like a frozen phone
    try:
        time.sleep(0.8)
        bridge._monitor_tick()
        assert bridge.health()["pid"] != pid
        assert bridge.health()["restartCount"] == 2
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_ffmpeg_stderr_reaches_health(bridge_module, tmp_path, cleanup):
    bridge = make_bridge(bridge_module, tmp_path, FAILS, rescan_interval=0.0)
    cleanup.append(bridge)
    bridge._launch("test", replace=False)
    time.sleep(0.6)
    assert "Could not lock device" in " ".join(bridge.health()["ffmpegStderr"])
    bridge._monitor_tick()  # notices the exit, records it, starts again
    health = bridge.health()
    assert health["lastError"].startswith("FFmpeg exited with code 1")
    assert "Could not lock device" in health["lastError"]
    assert health["lastCaptureExit"]["returnCode"] == 1
    assert "Could not lock device" in " ".join(health["lastCaptureExit"]["stderr"])


def test_missing_phone_is_rescanned_and_reset_rarely(bridge_module, tmp_path, cleanup):
    present, resets = bridge_module._test_present, bridge_module._test_resets
    bridge = make_bridge(
        bridge_module, tmp_path, FRAMES, rescan_interval=0.0, auto_reset_after=0.3, auto_reset_min_interval=30.0
    )
    cleanup.append(bridge)
    present["ok"] = False
    bridge.update_device_snapshot({"ok": False})
    for _ in range(8):
        bridge._monitor_tick()
        time.sleep(0.1)
    assert bridge.health()["state"] == "missing"
    assert len(resets) == 1  # one automatic reset, then at least 30 s apart
    present["ok"] = True  # the phone is plugged back in
    bridge._monitor_tick()
    _, frame = bridge.wait_for_frame(0, timeout=3)
    assert frame and bridge.health()["state"] == "live"  # back without pressing Reconnect
    assert bridge.health()["lastError"] == ""


def test_no_automatic_reset_soon_after_a_manual_one(bridge_module, tmp_path, cleanup):
    present, resets = bridge_module._test_present, bridge_module._test_resets
    bridge = make_bridge(bridge_module, tmp_path, FRAMES, rescan_interval=0.0, auto_reset_after=0.0,
                         device_wait_timeout=0.1)
    cleanup.append(bridge)
    present["ok"] = False
    result = bridge.restart("manual", wait_timeout=0.1)
    assert result["action"] == "device_missing" and len(resets) == 1
    for _ in range(3):
        bridge._monitor_tick()
    assert len(resets) == 1


def test_reconnect_reports_the_selected_missing_camera(bridge_module, tmp_path, cleanup):
    bridge = make_bridge(bridge_module, tmp_path, SILENT, target_device="Lab iPhone Camera")
    cleanup.append(bridge)
    bridge_module._test_present["ok"] = False

    result = bridge.restart("manual", wait_timeout=0.1, reset_missing_device=False)

    assert result["action"] == "device_missing"
    assert result["device"]["targetDevice"] == "Lab iPhone Camera"
    assert "Lab iPhone Camera" in result["message"]
    assert not result["request"]["sent"]


def serve(module, bridge, port_holder):
    module.IPhoneBridgeHandler.bridge_args = bridge.args
    module.IPhoneBridgeHandler.camera_bridge = bridge
    module.IPhoneBridgeHandler.device_snapshot = {}
    module.IPhoneBridgeHandler.log_message = lambda *a, **k: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.IPhoneBridgeHandler)
    port_holder.append(server.server_address[1])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_stream_without_frames_ends_and_does_not_restart_a_connecting_capture(bridge_module, tmp_path, cleanup,
                                                                               wall_clock):
    bridge = make_bridge(bridge_module, tmp_path, SILENT, stream_idle_timeout=1.0)
    cleanup.append(bridge)
    bridge._launch("connecting", replace=False)
    pid = bridge.health()["pid"]
    ports: list[int] = []
    server = serve(bridge_module, bridge, ports)
    try:
        threads_before = threading.active_count()
        t0 = time.monotonic()
        with urllib.request.urlopen(f"http://127.0.0.1:{ports[0]}/iphone.mjpeg", timeout=10) as response:
            assert b"\xff\xd8" not in response.read()  # ended cleanly, no frames (only the opening boundary)
        took = time.monotonic() - t0
        assert took >= 0.9
        wall_clock.within(took, 4.0, "the stream")
        health = bridge.health()
        assert health["pid"] == pid and health["restartCount"] == 1  # the connecting capture was left alone
        time.sleep(0.2)
        assert threading.active_count() <= threads_before + 1
    finally:
        server.shutdown()


def test_stream_starts_a_capture_when_none_runs(bridge_module, tmp_path, cleanup):
    bridge = make_bridge(bridge_module, tmp_path, FRAMES, stream_idle_timeout=1.0)
    cleanup.append(bridge)
    ports: list[int] = []
    server = serve(bridge_module, bridge, ports)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{ports[0]}/iphone.mjpeg", timeout=10) as response:
            data = response.read(4096)
        assert b"\xff\xd8" in data
        assert bridge.health()["restartCount"] == 1
    finally:
        server.shutdown()


def test_feed_probe_updates_device_state(bridge_module, tmp_path, cleanup):
    present = bridge_module._test_present
    bridge = make_bridge(bridge_module, tmp_path, SILENT)
    cleanup.append(bridge)
    bridge.update_device_snapshot({"ok": True})
    present["ok"] = False
    state = bridge_module.feed_state(bridge.args, bridge, probe_camera=True)
    assert state["cameraVisible"] is False
    assert state["bridge"]["devicePresent"] is False
    assert bridge.health()["devicePresent"] is False
    present["ok"] = True
    state = bridge_module.feed_state(bridge.args, bridge, probe_camera=True)
    assert state["cameraVisible"] is True and bridge.health()["devicePresent"] is True


# The camera recording. Fake recorders stand in for the recording FFmpeg: each reads the
# frames to the end of its stdin, then writes "frames=N" to its file (the last argument).
JPEG = b"\xff\xd8" + b"x" * 100 + b"\xff\xd9"
RECORDER = (
    "import signal,sys,time\n"
    "path = sys.argv[-1]\n"
    "signal.signal(signal.SIGTERM, lambda *a: open(path + '.signals', 'a').write('TERM\\n'))\n"
    "data = sys.stdin.buffer.read()\n"
    "time.sleep({finish_s})  # writing out the file\n"
    "open(path, 'w').write('frames=%d\\n' % data.count(b'\\xff\\xd9'))\n"
)
DIES_AFTER_A_FEW_FRAMES = (
    "import sys\n"
    "sys.stdin.buffer.read(200)\n"
    "open(sys.argv[-1], 'w').write('cut off\\n'); sys.exit(1)\n"
)
REMUX = "import sys; data = open(sys.argv[1], 'rb').read(); open(sys.argv[2], 'wb').write(data + b'index at the front\\n')"


def make_recording_bridge(module, tmp_path, recorder: str):
    bridge = make_bridge(module, tmp_path, SILENT, recording_dir=str(tmp_path / "recordings"))
    bridge.recording_command = lambda path: [sys.executable, "-c", recorder, str(path)]
    bridge.remux_command = lambda source, target: [sys.executable, "-c", REMUX, str(source), str(target)]
    return bridge


def wait_until_saved(bridge) -> dict:
    def saved() -> dict | None:
        status = bridge.recording_status()
        return status if status["state"] == "stopped" else None

    return until(saved, every=0.1, what="the recording to be finished")


def test_a_recorder_that_takes_over_10_s_to_finish_still_saves_a_complete_file(bridge_module, tmp_path, cleanup,
                                                                               wall_clock):
    # 2026-10-06: stop waited 8 s, then SIGTERM, then SIGKILL; FFmpeg was still writing out a
    # 38-minute recording and the MP4 had no index. Now it is never cut off, however long.
    bridge = make_recording_bridge(bridge_module, tmp_path, RECORDER.format(finish_s=10.5))
    cleanup.append(bridge)
    path = Path(bridge.start_recording("demo")["path"])
    for _ in range(3000):
        bridge._write_recording_frame(JPEG)

    t0 = time.monotonic()
    stopped = bridge.stop_recording("demo")
    wall_clock.within(time.monotonic() - t0, 1.0, "stop")  # stop must not wait for the file
    assert stopped["stopped"] and stopped["state"] == "finishing" and stopped["path"] == str(path)
    t0 = time.monotonic()
    bridge._write_recording_frame(JPEG)  # the frame reader goes on at once
    wall_clock.within(time.monotonic() - t0, 0.1, "the frame reader")

    time.sleep(10.0)
    status = bridge.recording_status()
    assert status["state"] == "finishing" and status["running"] is False
    assert [entry["path"] for entry in status["finishing"]] == [str(path)]
    assert status["finishing"][0]["frames"] == 3000

    status = wait_until_saved(bridge)
    assert path.read_text() == "frames=3000\nindex at the front\n"
    assert not Path(str(path) + ".signals").exists(), "the recorder was signalled while finishing"
    saved = status["lastSaved"]
    assert saved["ok"] and saved["index"] == "front" and saved["frames"] == 3000 and saved["finishSeconds"] >= 10


def test_a_stop_ends_only_the_recording_its_owner_started(bridge_module, tmp_path, cleanup):
    # 2026-10-06: the studio's Stop ended the camera recording `make demo-start` had started.
    bridge = make_recording_bridge(bridge_module, tmp_path, RECORDER.format(finish_s=0))
    cleanup.append(bridge)
    ports: list[int] = []
    server = serve(bridge_module, bridge, ports)

    def post(path: str) -> dict:
        request = urllib.request.Request(f"http://127.0.0.1:{ports[0]}{path}", method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())

    try:
        demo = post("/api/record/start?owner=demo")
        assert demo["ok"] and not demo["alreadyRecording"]
        studio = post("/api/record/start?owner=studio")
        assert studio["alreadyRecording"] and studio["path"] == demo["path"]
        assert studio["owners"] == ["demo", "studio"]
        for _ in range(10):
            bridge._write_recording_frame(JPEG)

        studio_stop = post("/api/record/stop?owner=studio")
        assert studio_stop["stopped"] is False and studio_stop["stillRecordingFor"] == ["demo"]
        assert studio_stop["state"] == "recording" and studio_stop["path"] == demo["path"]
        assert post("/api/record/stop?owner=studio")["stopped"] is False  # twice changes nothing
        assert post("/api/record/stop")["stopped"] is False  # nor does a caller that did not start it
        for _ in range(10):
            bridge._write_recording_frame(JPEG)
        bridge._drain_recording_queue()  # the recorder writes on its own thread
        assert bridge.recording_status()["frames"] == 20

        demo_stop = post("/api/record/stop?owner=demo")
        assert demo_stop["stopped"] and demo_stop["path"] == demo["path"]
        wait_until_saved(bridge)
        assert Path(demo["path"]).read_text() == "frames=20\nindex at the front\n"
    finally:
        server.shutdown()


def test_a_forced_stop_ends_the_recording_for_every_owner(bridge_module, tmp_path, cleanup):
    bridge = make_recording_bridge(bridge_module, tmp_path, RECORDER.format(finish_s=0))
    cleanup.append(bridge)
    bridge.start_recording("demo")
    bridge.start_recording("studio")
    stopped = bridge.stop_recording(force=True)
    assert stopped["stopped"] and stopped["owners"] == []
    wait_until_saved(bridge)
    # A new recording is a new file, with the new caller as its only owner.
    again = bridge.start_recording("studio")
    assert not again["alreadyRecording"] and again["owners"] == ["studio"] and again["path"] != stopped["path"]


def test_a_recorder_that_dies_is_finished_and_a_new_one_can_start(bridge_module, tmp_path, cleanup):
    bridge = make_recording_bridge(bridge_module, tmp_path, DIES_AFTER_A_FEW_FRAMES)
    cleanup.append(bridge)
    first = Path(bridge.start_recording("demo")["path"])

    def died() -> bool:
        if not bridge.recording_status()["running"]:
            return True
        bridge._write_recording_frame(JPEG * 100)  # big enough to reach the recorder at once
        return False

    until(died, every=0.05)
    status = wait_until_saved(bridge)
    assert status["owners"] == [] and "exited 1" in status["error"]
    assert status["lastSaved"]["path"] == str(first) and status["lastSaved"]["ok"] is False
    assert first.read_text() == "cut off\nindex at the front\n"  # what it wrote is kept
    assert bridge.start_recording("studio")["alreadyRecording"] is False


def _top_level_boxes(path: Path) -> list[str]:
    if not path.exists():
        return []
    boxes, data, at = [], path.read_bytes(), 0
    while at + 8 <= len(data):
        size = int.from_bytes(data[at : at + 4], "big")
        boxes.append(data[at + 4 : at + 8].decode("latin-1"))
        if size == 1:
            size = int.from_bytes(data[at + 8 : at + 16], "big")
        if size < 8:
            break
        at += size
    return boxes


def _frames_in(path: Path) -> int:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=False,
    )
    return int(result.stdout.strip() or 0)


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")
@pytest.mark.parametrize("changing", [False, True], ids=["held", "changing"])
@pytest.mark.parametrize("count", [45, 195], ids=["startup", "steady"])
def test_real_recording_persists_recent_frames_before_the_encoder_is_killed(
    bridge_module, tmp_path, cleanup, changing, count,
):
    # Generated input, actual bridge intake/writer and encoder. A burst of high-bitrate
    # frames fills FFmpeg's output buffer and used to hide the held-view recording bug.
    fps = 30  # stop midway through a fragment, not conveniently on its boundary
    generated = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30",
         "-frames:v", str(count if changing else 1), "-q:v", "2", "-f", "mjpeg", "-"],
        capture_output=True, check=True, timeout=10,
    ).stdout
    frames = [part + b"\xff\xd9" for part in generated.split(b"\xff\xd9")[:-1]]
    bridge = make_bridge(bridge_module, tmp_path, SILENT, fps=fps,
                         recording_dir=str(tmp_path / "recordings"))
    cleanup.append(bridge)
    path = Path(bridge.start_recording("dataset")["path"])
    process = bridge.recording_process
    started = time.monotonic()
    live = tmp_path / "at-five-seconds.mp4"
    try:
        for i in range(count):
            bridge._extract_frames(bytearray(frames[i % len(frames)]))
            time.sleep(max(0, started + (i + 1) / fps - time.monotonic()))
            if i + 1 == 5 * fps:
                live.write_bytes(path.read_bytes() if path.exists() else b"")
        # The kill comes once the recorder's thread has handed the encoder every frame (or dropped one as behind,
        # which the sidecar check below fails on). Frames still in the bridge's queue when the encoder dies never
        # reached it and can't be saved, and a loaded machine (CI's 3-vCPU runner) leaves 1-5 there: what this
        # test proves is that what the encoder was given survives its kill, not how far behind the writer runs.
        until(lambda: bridge.recording_frames + bridge.recording_dropped >= count,
              what="the recorder to hand the encoder every frame")
    finally:
        # Interrupt this test's encoder only, even if feeding or the assertion fails.
        process.kill()
        process.wait(timeout=5)
    crashed = tmp_path / "interrupted.mp4"
    crashed.write_bytes(path.read_bytes() if path.exists() else b"")
    bridge.stop_recording("dataset")
    saved = wait_until_saved(bridge)
    assert not saved["lastSaved"]["ok"] and "exited" in saved["error"]

    def decoded(video):
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
             "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0 and not result.stderr, result.stderr
        return int(result.stdout.strip())

    # Read video bytes independently, before graceful remux; sidecar/input progress
    # alone cannot certify that a recording can survive a crash.
    if count >= 5 * fps:
        assert decoded(live) > 0, "the video must be readable within the Data light's five seconds"
    recovered = decoded(crashed)
    assert count - fps <= recovered <= count, "more than the last second of video was lost"
    sidecar = read_sidecar(path)
    assert [line["i"] for line in sidecar if "i" in line] == list(range(count))
    assert [line["seq"] for line in sidecar if "i" in line] == list(range(1, count + 1))


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")
@pytest.mark.parametrize("changing", [False, True], ids=["held", "changing"])
def test_the_real_recorder_saves_an_mp4_with_its_index_first_and_survives_being_killed(
    bridge_module, tmp_path, cleanup, changing, monkeypatch,
):
    import bridge_recording  # the recorder's own module (scripts/, beside the bridge)

    # It counts every frame of the file: a recording's end waits 0.5 s for its queued frames, and a loaded Mac's ffmpeg
    # took longer (7 of 90 kept at load 75).
    drain = bridge_recording.RecordingMixin._drain_recording_queue
    monkeypatch.setattr(bridge_recording.RecordingMixin, "_drain_recording_queue", lambda self, timeout=0.5: drain(self, HANG_S))
    jpegs = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30",
         "-frames:v", "90" if changing else "1", "-q:v", "2", "-f", "mjpeg", "-"],
        capture_output=True, check=True, timeout=10,
    ).stdout
    if not changing:
        jpegs *= 90
    bridge = make_bridge(bridge_module, tmp_path, SILENT, recording_dir=str(tmp_path / "recordings"))
    cleanup.append(bridge)

    # Stopped normally: a plain MP4, every frame, the index (moov) before the frames (mdat).
    path = Path(bridge.start_recording("demo")["path"])
    bridge._extract_frames(bytearray(jpegs))
    bridge.stop_recording("demo")
    status = wait_until_saved(bridge)
    assert status["lastSaved"]["ok"] and status["lastSaved"]["index"] == "front"
    boxes = _top_level_boxes(path)
    assert boxes.index("moov") < boxes.index("mdat") and "moof" not in boxes
    assert _frames_in(path) == 90
    # Its sidecar has a line for every frame in the file, and says how many the file holds.
    sidecar = read_sidecar(path)
    assert len([line for line in sidecar if "i" in line]) == 90
    assert sidecar[-1]["type"] == "saved" and sidecar[-1]["fileFrames"] == 90 and sidecar[-1]["framesSent"] == 90

    # Killed while recording: the fragments written so far still play.
    killed = Path(bridge.start_recording("demo")["path"])
    deadline = time.monotonic() + 30
    while _top_level_boxes(killed).count("moof") < 2 and time.monotonic() < deadline:
        bridge._extract_frames(bytearray(jpegs))  # 3 s of frames: the encoder holds some back
        time.sleep(0.2)
    assert _top_level_boxes(killed).count("moof") >= 2, "the recorder wrote no fragments"
    bridge.recording_process.kill()
    bridge.recording_process.wait()
    bridge._write_recording_frame(jpegs[:1000])  # the frame reader notices it has gone
    status = wait_until_saved(bridge)
    assert status["lastSaved"]["path"] == str(killed) and status["lastSaved"]["ok"] is False
    assert _frames_in(killed) >= 30


# The recording's sidecar (`<name>.frames.jsonl`): the per-frame clock and where each frame came
# from, written by the bridge (docs/datasets.md).
def read_sidecar(video: Path) -> list[dict]:
    path = video.with_name(video.stem + ".frames.jsonl")
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_every_recorded_frame_has_a_sidecar_line_with_its_index_number_and_time(bridge_module, tmp_path, cleanup):
    bridge = make_recording_bridge(bridge_module, tmp_path, RECORDER.format(finish_s=0))
    cleanup.append(bridge)
    t0 = time.time()
    started = bridge.start_recording("dataset")
    video = Path(started["path"])
    assert started["sidecar"] == str(video.with_name(video.stem + ".frames.jsonl"))
    bridge._extract_frames(bytearray(JPEG * 5))
    bridge.recording_event("feed", source="footage", footage="june.mp4")
    bridge._extract_frames(bytearray(JPEG * 3))
    bridge.stop_recording("dataset")
    status = wait_until_saved(bridge)
    assert status["lastSaved"]["sidecar"] == started["sidecar"]

    lines = read_sidecar(video)
    header, frames = lines[0], [line for line in lines if "i" in line]
    assert header["type"] == "header" and header["video"] == video.name and header["owner"] == "dataset"
    assert header["bootId"] == bridge.health()["bootId"] and header["requested"]["fps"] == bridge.args.fps
    assert [f["i"] for f in frames] == list(range(8))
    assert [f["seq"] for f in frames] == list(range(1, 9))  # the bridge's own frame numbers
    assert all(f["src"] == "camera" for f in frames)
    assert all(t0 <= f["bridgeAt"] <= time.time() for f in frames)
    assert [f["bridgeAt"] for f in frames] == sorted(f["bridgeAt"] for f in frames)
    event = next(line for line in lines if line.get("type") == "event")
    assert event["event"] == "feed" and event["afterFrame"] == 4 and event["footage"] == "june.mp4"
    end = next(line for line in lines if line.get("type") == "end")
    assert end["frames"] == 8 and end["reason"] == "stopped"
    saved = lines[-1]
    assert saved["type"] == "saved" and saved["framesSent"] == 8  # fileFrames: None, a fake recorder's file


def test_footage_frames_are_marked_by_the_bridge_in_the_sidecar(bridge_module, tmp_path, cleanup):
    class Footage:  # stands in for SimulatedCamera: the bridge asks it what each frame was
        def pass_frame(self, frame):
            return frame

        def frame_facts(self, seq):
            return {"src": "footage", "footage": "june.mp4", "footageFrameEstimate": 100 + seq}

    bridge = make_recording_bridge(bridge_module, tmp_path, RECORDER.format(finish_s=0))
    cleanup.append(bridge)
    video = Path(bridge.start_recording("dataset")["path"])
    bridge._extract_frames(bytearray(JPEG * 2))
    bridge_module.SIMULATED_CAMERA = Footage()
    try:
        bridge._extract_frames(bytearray(JPEG * 2))
    finally:
        bridge_module.SIMULATED_CAMERA = None
    bridge.stop_recording("dataset")
    wait_until_saved(bridge)
    frames = [line for line in read_sidecar(video) if "i" in line]
    assert [f["src"] for f in frames] == ["camera", "camera", "footage", "footage"]
    assert frames[2]["footage"] == "june.mp4" and frames[2]["footageFrameEstimate"] == 103


def test_a_sidecar_that_cannot_be_written_never_stops_the_video(bridge_module, tmp_path, cleanup):
    bridge = make_recording_bridge(bridge_module, tmp_path, RECORDER.format(finish_s=0))
    cleanup.append(bridge)
    real_open = open

    def failing_open(path, *args, **kwargs):
        if str(path).endswith(".frames.jsonl"):
            raise PermissionError("read-only")
        return real_open(path, *args, **kwargs)

    import bridge_recording  # the recorder's own module (scripts/, beside the bridge), where its sidecar is opened

    bridge_recording.open = failing_open
    try:
        started = bridge.start_recording("dataset")
    finally:
        del bridge_recording.open
    assert started["ok"] and "read-only" in started["sidecarError"]
    bridge._extract_frames(bytearray(JPEG * 4))
    bridge._drain_recording_queue()  # the recorder writes on its own thread
    assert bridge.recording_status()["frames"] == 4
    bridge.stop_recording("dataset")
    wait_until_saved(bridge)
    assert Path(started["path"]).read_text() == "frames=4\nindex at the front\n"


def test_each_streamed_frame_carries_its_bridge_number(bridge_module, tmp_path, cleanup):
    bridge = make_bridge(bridge_module, tmp_path, FRAMES, stream_idle_timeout=1.0)
    cleanup.append(bridge)
    ports: list[int] = []
    server = serve(bridge_module, bridge, ports)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{ports[0]}/iphone.mjpeg", timeout=10) as response:
            boot = response.headers.get("X-Bridge-Boot")
            data = response.read(2048)
        numbers = [int(part.split(b"\r\n", 1)[0]) for part in data.split(b"X-Frame-Seq: ")[1:]]
        assert numbers and numbers == sorted(numbers) and numbers[0] >= 1
        assert boot and boot == bridge.health()["bootId"]
    finally:
        server.shutdown()


# A session's own recording played into the feed (POST /feed?source=recording): the camera video of one of Record's
# datasets in the app's datasets folder (--datasets-dir). Downstream it is footage like any other.
def _mp4(path: Path, seconds: int = 4) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=30", "-t", str(seconds),
                    "-pix_fmt", "yuv420p", "-y", str(path)], check=True)
    return path


def _dataset(root: Path, did: str, name: str = "", started: float | None = None, parts: int = 1) -> Path:
    folder = root / did
    for n in range(1, parts + 1):
        _mp4(folder / "video" / f"part-{n}.mp4")
    (folder / "dataset.json").write_text(json.dumps({"id": did, "name": name, "startedAtEpoch": started}))
    return folder


def _still(bridge, module, monkeypatch):
    """The switch without a capture: what it starts is the simulated camera's own (tested in test_sim_faults)."""
    restarts: list[str] = []
    monkeypatch.setattr(bridge, "restart", lambda reason, **kw: restarts.append(reason) or {"ok": True})
    monkeypatch.setattr(bridge, "_stop_capture", lambda why: None)
    monkeypatch.setattr(module, "SIMULATED_CAMERA", None)
    return restarts


needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")


@needs_ffmpeg
def test_a_session_recording_plays_held_at_its_place_and_is_said_to_be_footage(bridge_module, tmp_path, monkeypatch,
                                                                              cleanup, wall_clock):
    datasets = tmp_path / "datasets"
    started = time.mktime((2026, 10, 12, 1, 44, 11, 0, 0, -1))
    _dataset(datasets, "ds-20261012-014411", started=started)
    _dataset(datasets, "ds-20261012-020000", name="Sperm motility")
    bridge = make_bridge(bridge_module, tmp_path, SILENT, datasets_dir=str(datasets))
    cleanup.append(bridge)
    restarts = _still(bridge, bridge_module, monkeypatch)

    state = bridge_module.switch_feed(bridge.args, bridge, None, recording=("ds-20261012-014411", "1"), paused=True, at=2.0)
    camera = bridge_module.SIMULATED_CAMERA
    assert state["source"] == "footage" and state["footage"] == "recording:ds-20261012-014411/1"
    assert state["footageTitle"] == "Recording · Oct 12, 1:44 am"  # no name: when it began
    assert state["playback"] == {"seconds": 2.0, "duration": 4.0, "paused": True, "still": False}
    # Held from its first frame: the capture starts at the place asked, and the clock never runs on.
    assert camera.paused_frame == 60 and "2.000" in camera.capture_command("ffmpeg", bridge.args)
    time.sleep(0.2)
    assert camera.playback()["seconds"] == 2.0
    assert restarts == ["switched to the session recording recording:ds-20261012-014411/1"]

    # Named: its name. Played, not held: it plays on from where it was asked to start.
    state = bridge_module.switch_feed(bridge.args, bridge, None, recording=("ds-20261012-020000", 1), at=1.0)
    assert state["footageTitle"] == "Sperm motility" and state["playback"]["paused"] is False
    assert state["playback"]["seconds"] >= 1.0
    wall_clock.within(state["playback"]["seconds"], 1.5, "the playhead")
    # And back to the camera.
    state = bridge_module.switch_feed(bridge.args, bridge, None)
    assert state["source"] == "camera" and state["footage"] is None and bridge_module.SIMULATED_CAMERA is None


@needs_ffmpeg
def test_a_session_recording_is_refused_in_words_unless_it_is_a_part_inside_the_datasets_folder(bridge_module, tmp_path, monkeypatch, cleanup):
    datasets = tmp_path / "datasets"
    _dataset(datasets, "ds-20261012-014411")
    outside = _mp4(tmp_path / "elsewhere" / "secret.mp4")
    (datasets / "ds-20261012-020000" / "video").mkdir(parents=True)
    (datasets / "ds-20261012-020000" / "video" / "part-1.mp4").symlink_to(outside)
    bridge = make_bridge(bridge_module, tmp_path, SILENT, datasets_dir=str(datasets))
    cleanup.append(bridge)
    restarts = _still(bridge, bridge_module, monkeypatch)
    for dataset, part, words in (("ds-20261012-999999", 1, "No dataset called"), ("../elsewhere", 1, "No dataset called"),
                                 ("ds-20261012-014411", 2, "has no recording 2"), ("ds-20261012-014411", "x", "part is"),
                                 ("ds-20261012-020000", 1, "not in the datasets folder")):
        with pytest.raises(ValueError, match=words):
            bridge_module.switch_feed(bridge.args, bridge, None, recording=(dataset, part))
    assert restarts == [] and bridge_module.SIMULATED_CAMERA is None  # nothing was touched
    bridge.args.datasets_dir = None
    with pytest.raises(ValueError, match="without --datasets-dir"):
        bridge_module.switch_feed(bridge.args, bridge, None, recording=("ds-20261012-014411", 1))

    # Over HTTP: a 400 with the words, and an unknown source or a bad place too.
    bridge.args.datasets_dir = str(datasets)
    ports: list[int] = []
    server = serve(bridge_module, bridge, ports)
    try:
        for query, words in (("source=recording&dataset=ds-20261012-999999&part=1", "No dataset called"),
                             ("source=recording&dataset=ds-20261012-014411&part=1&at=nan", "at is a number"),
                             ("source=tape", "source is camera, footage, recording or asset")):
            request = urllib.request.Request(f"http://127.0.0.1:{ports[0]}/feed?{query}", method="POST")
            with pytest.raises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(request, timeout=10)
            assert refused.value.code == 400 and words in json.loads(refused.value.read())["error"]
        request = urllib.request.Request(f"http://127.0.0.1:{ports[0]}/feed?source=recording&dataset=ds-20261012-014411"
                                         "&part=1&paused=1&at=1.5", method="POST")
        with urllib.request.urlopen(request, timeout=10) as response:
            played = json.loads(response.read())
        assert played["footage"] == "recording:ds-20261012-014411/1" and played["playback"]["paused"] is True
        assert played["playback"]["seconds"] == 1.5
    finally:
        server.shutdown()


@needs_ffmpeg
def test_test_footage_also_starts_held_at_a_place(bridge_module, tmp_path, monkeypatch, cleanup):
    footage = tmp_path / "footage"
    _mp4(footage / "leaf.mp4")
    bridge = make_bridge(bridge_module, tmp_path, SILENT, footage_dir=str(footage))
    cleanup.append(bridge)
    _still(bridge, bridge_module, monkeypatch)
    state = bridge_module.switch_feed(bridge.args, bridge, "leaf.mp4", paused=True, at=3.0)
    assert state["footage"] == "leaf.mp4" and state["playback"] == {"seconds": 3.0, "duration": 4.0, "paused": True,
                                                                      "still": False}
    assert bridge_module.SIMULATED_CAMERA.paused_frame == 90


@needs_ffmpeg
def test_playing_a_session_recording_is_written_in_the_sidecar_of_what_records(bridge_module, tmp_path, monkeypatch, cleanup):
    datasets = tmp_path / "datasets"
    _dataset(datasets, "ds-20261012-014411", name="Sperm motility")
    bridge = make_recording_bridge(bridge_module, tmp_path, RECORDER.format(finish_s=0))
    bridge.args.datasets_dir = str(datasets)
    cleanup.append(bridge)
    _still(bridge, bridge_module, monkeypatch)
    video = Path(bridge.start_recording("dataset")["path"])
    bridge._extract_frames(bytearray(JPEG * 2))
    bridge_module.switch_feed(bridge.args, bridge, None, recording=("ds-20261012-014411", 1), paused=True, at=1.0)
    bridge._extract_frames(bytearray(JPEG * 2))
    bridge_module.switch_feed(bridge.args, bridge, None)
    bridge.stop_recording("dataset")
    wait_until_saved(bridge)
    lines = read_sidecar(video)
    feeds = [line for line in lines if line.get("event") == "feed"]
    assert [(f["source"], f["footage"]) for f in feeds] == [("recording", "recording:ds-20261012-014411/1"), ("camera", None)]
    assert feeds[0]["at"] == 1.0 and feeds[0]["paused"] is True and feeds[0]["afterFrame"] == 1
    frames = [line for line in lines if "i" in line]
    assert [f["src"] for f in frames] == ["camera", "camera", "footage", "footage"]
    assert frames[2]["footage"] == "recording:ds-20261012-014411/1" and frames[2]["held"] is True


# Any recording or picture of the user's played into the feed (POST /feed?source=asset): a file inside one of the
# bridge's media roots (--media-root, the datasets and footage folders). A picture is one held frame at the bridge's
# rate, so downstream it is a slide at rest. Downstream all of it is footage like any other.
def _picture(path: Path, size: str = "400x300") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}", "-frames:v", "1", "-y", str(path)],
                   check=True)
    return path


def _post(port: int, query: str) -> tuple[int, dict]:
    request = urllib.request.Request(f"http://127.0.0.1:{port}/feed?{query}", method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as refused:
        return refused.code, json.loads(refused.read())


@needs_ffmpeg
def test_an_asset_recording_under_a_media_root_plays_held_at_its_place(bridge_module, tmp_path, monkeypatch, cleanup,
                                                                       wall_clock):
    snapshots, results = tmp_path / "snapshots", tmp_path / "results"
    video = _mp4(results / "2026-10-12" / "r1" / "sperm sample.mp4")
    bridge = make_bridge(bridge_module, tmp_path / "footage", SILENT, media_root=[str(snapshots), str(results)])
    cleanup.append(bridge)
    restarts = _still(bridge, bridge_module, monkeypatch)

    state = bridge_module.switch_feed(bridge.args, bridge, None, asset=(str(video), "  Sperm   sample "), paused=True, at=2.0)
    camera = bridge_module.SIMULATED_CAMERA
    assert state["source"] == "footage" and state["footage"] == f"asset:{video}"  # the path as asked
    assert state["footageTitle"] == "Sperm sample"
    assert state["playback"] == {"seconds": 2.0, "duration": 4.0, "paused": True, "still": False}
    assert camera.paused_frame == 60 and "2.000" in camera.capture_command("ffmpeg", bridge.args)
    assert restarts == [f"switched to the file {video}"]

    # Untitled: the file's name. The datasets and footage folders are media roots too.
    bridge.args.datasets_dir = str(tmp_path / "datasets")
    for played in (_mp4(tmp_path / "datasets" / "ds-1" / "video" / "part-1.mp4"), _mp4(tmp_path / "footage" / "added" / "leaf.mp4")):
        state = bridge_module.switch_feed(bridge.args, bridge, None, asset=(str(played), ""), at=1.0)
        assert state["footage"] == f"asset:{played}" and state["footageTitle"] == played.name
        assert state["playback"]["paused"] is False and state["playback"]["seconds"] >= 1.0
        wall_clock.within(state["playback"]["seconds"], 1.5, "the playhead")

    # Over HTTP, one call with its place and hold.
    ports: list[int] = []
    server = serve(bridge_module, bridge, ports)
    try:
        status, played = _post(ports[0], urllib.parse.urlencode({"source": "asset", "path": str(video), "title": "Sperm sample",
                                                                  "paused": "1", "at": "1.5"}))
        assert status == 200 and played["footage"] == f"asset:{video}" and played["footageTitle"] == "Sperm sample"
        assert played["playback"] == {"seconds": 1.5, "duration": 4.0, "paused": True, "still": False}
    finally:
        server.shutdown()


@needs_ffmpeg
def test_a_picture_plays_as_a_still_whose_frames_keep_arriving(bridge_module, tmp_path, monkeypatch, cleanup):
    snapshots = tmp_path / "snapshots"
    picture = _picture(snapshots / "2026-10-12" / "s1.png")  # 4:3, so the frame letterboxes it
    bridge = make_bridge(bridge_module, tmp_path / "footage", SILENT, media_root=[str(snapshots)], stream_idle_timeout=2.0)
    del bridge.ffmpeg_command  # the real command: the picture's own capture (never the camera: footage replaces it)
    cleanup.append(bridge)
    monkeypatch.setattr(bridge_module, "SIMULATED_CAMERA", None)

    state = bridge_module.switch_feed(bridge.args, bridge, None, asset=(str(picture), "Blood smear"), paused=True, at=3.0)
    camera = bridge_module.SIMULATED_CAMERA
    assert camera.still and state["switch"]["action"] == "live"
    assert state["source"] == "footage" and state["footage"] == f"asset:{picture}" and state["footageTitle"] == "Blood smear"
    assert state["playback"] == {"seconds": 0.0, "duration": 0.0, "paused": True, "still": True}
    command = camera.capture_command("ffmpeg", bridge.args)
    assert command[command.index("-loop"):command.index("-i") + 2] == ["-loop", "1", "-framerate", "30", "-i", str(picture)]
    assert "-re" in command and "-ss" not in command and "-stream_loop" not in command
    assert command[command.index("-vf") + 1].startswith("fps=30,") and ",pad=1280:720:" in command[command.index("-vf") + 1]
    assert (camera.fps, camera.total_frames, camera.paused_frame) == (30.0, 1, None)

    # Downstream: frames arrive at the bridge's rate on /iphone.mjpeg, each the same picture at the bridge's size.
    ports: list[int] = []
    server = serve(bridge_module, bridge, ports)
    try:
        data, t0 = b"", time.monotonic()
        with urllib.request.urlopen(f"http://127.0.0.1:{ports[0]}/iphone.mjpeg", timeout=10) as response:
            while time.monotonic() - t0 < 2.0:
                data += response.read1(1 << 16)
        frames = [part.split(b"\r\n\r\n", 1)[1].split(b"\r\n--iphonebridge")[0] for part in data.split(b"X-Frame-Seq: ")[1:-1]]
        assert len(frames) >= 30, f"{len(frames)} frames in 2 s"  # 60 at 30 fps; a loaded machine still well past half
        assert len(set(frames)) == 1 and bridge_module.jpeg_size(frames[0]) == (1280, 720)
        assert bridge.health()["ok"]

        # Pause, play and seek are harmless on a picture: ok, and the capture is never restarted for them.
        starts = bridge.restart_count
        for path in ("/feed/pause?paused=1", "/feed/pause?paused=0", "/feed/seek?seconds=5"):
            request = urllib.request.Request(f"http://127.0.0.1:{ports[0]}{path}", method="POST")
            with urllib.request.urlopen(request, timeout=10) as response:
                reply = json.loads(response.read())
            assert reply["ok"] and reply["playback"]["still"] is True, (path, reply)
        assert bridge.restart_count == starts
        count = bridge.frame_count
        time.sleep(0.5)
        assert bridge.frame_count - count >= 5  # still arriving
    finally:
        server.shutdown()
        bridge._stop_capture("test over")


@needs_ffmpeg
def test_an_asset_is_refused_in_words_unless_it_is_a_recording_or_picture_inside_a_media_root(bridge_module, tmp_path, monkeypatch, cleanup):
    root = tmp_path / "results"
    outside = _mp4(tmp_path / "elsewhere" / "secret.mp4")
    (root / "notes.txt").parent.mkdir(parents=True)
    (root / "notes.txt").write_text("not a picture")
    (root / "fake.png").write_text("not a picture either")
    (root / "link.mp4").symlink_to(outside)
    bridge = make_bridge(bridge_module, tmp_path / "footage", SILENT, media_root=[str(root)])
    cleanup.append(bridge)
    restarts = _still(bridge, bridge_module, monkeypatch)
    for path, words in ((outside, "is not in a folder this bridge plays from"),
                        (tmp_path / "elsewhere" / "missing.mp4", "is not in a folder this bridge plays from"),
                        (root / "link.mp4", "is not in a folder this bridge plays from"),  # a link that leads outside
                        (root / ".." / "elsewhere" / "secret.mp4", "is not in a folder this bridge plays from"),
                        (root / "notes.txt", "Only recordings (.m4v, .mkv, .mov, .mp4, .webm) and pictures"),
                        (root / "missing.mp4", "There is no file at"),
                        (root / "fake.png", "fake.png does not open as a picture"),
                        ("results/notes.mp4", "path is the file's absolute path"), ("", "path is the file's absolute path")):
        with pytest.raises(ValueError, match=re.escape(words)):
            bridge_module.switch_feed(bridge.args, bridge, None, asset=(str(path), ""))
    assert restarts == [] and bridge_module.SIMULATED_CAMERA is None  # nothing was touched

    # Over HTTP: a 400 with the words.
    ports: list[int] = []
    server = serve(bridge_module, bridge, ports)
    try:
        for path, words in ((outside, "is not in a folder"), (root / "missing.mp4", "There is no file at"),
                            (root / "notes.txt", "Only recordings")):
            status, reply = _post(ports[0], urllib.parse.urlencode({"source": "asset", "path": str(path)}))
            assert status == 400 and words in reply["error"] and reply["ok"] is False
        status, reply = _post(ports[0], "source=asset")
        assert status == 400 and "absolute path" in reply["error"]
    finally:
        server.shutdown()
    assert restarts == []


@needs_ffmpeg
def test_playing_an_asset_is_written_in_the_sidecar_of_what_records(bridge_module, tmp_path, monkeypatch, cleanup):
    root = tmp_path / "results"
    video, picture = _mp4(root / "clip.mp4"), _picture(root / "clearer.png")
    bridge = make_recording_bridge(bridge_module, tmp_path, RECORDER.format(finish_s=0))
    bridge.args.media_root = [str(root)]
    cleanup.append(bridge)
    _still(bridge, bridge_module, monkeypatch)
    recorded = Path(bridge.start_recording("dataset")["path"])
    bridge._extract_frames(bytearray(JPEG * 2))
    bridge_module.switch_feed(bridge.args, bridge, None, asset=(str(video), "Clip"), paused=True, at=1.0)
    bridge._extract_frames(bytearray(JPEG * 2))
    bridge_module.switch_feed(bridge.args, bridge, None, asset=(str(picture), ""), paused=True, at=1.0)
    bridge._extract_frames(bytearray(JPEG * 2))
    bridge_module.switch_feed(bridge.args, bridge, None)
    bridge.stop_recording("dataset")
    wait_until_saved(bridge)
    lines = read_sidecar(recorded)
    feeds = [line for line in lines if line.get("event") == "feed"]
    assert [(f["source"], f["footage"]) for f in feeds] == [("asset", f"asset:{video}"), ("asset", f"asset:{picture}"),
                                                            ("camera", None)]
    assert feeds[0]["at"] == 1.0 and feeds[0]["paused"] is True and feeds[0]["afterFrame"] == 1
    # A picture has no place and nothing to pause: the event says it is a still instead (its `at` is the event's time).
    assert feeds[1]["still"] is True and feeds[1]["at"] > 1e9 and "paused" not in feeds[1]
    frames = [line for line in lines if "i" in line]
    assert [f["src"] for f in frames] == ["camera", "camera", "footage", "footage", "footage", "footage"]
    assert frames[2]["footage"] == f"asset:{video}" and frames[2]["held"] is True and "still" not in frames[2]
    assert frames[4]["footage"] == f"asset:{picture}" and frames[4]["still"] is True and frames[4]["footageFrameEstimate"] == 0


@needs_ffmpeg
@pytest.mark.timing  # its pictures are probed and made small by ffprobe and ffmpeg at background priority (taskpolicy -b)
def test_an_uploaded_picture_is_listed_as_a_still_with_its_thumbnail(bridge_module, tmp_path, cleanup):
    footage = tmp_path / "footage"
    _mp4(footage / "added" / "clip.mp4")
    bridge = make_bridge(bridge_module, footage, SILENT)
    cleanup.append(bridge)
    data = _picture(tmp_path / "phone" / "IMG_0042.PNG").read_bytes()
    ports: list[int] = []
    server = serve(bridge_module, bridge, ports)
    studio = {"Origin": "http://127.0.0.1:8770", "X-MicroscopeMind": "add-footage"}

    def upload(name: str, body: bytes) -> tuple[int, dict]:
        query = urllib.parse.urlencode({"filename": name, "specimen": "onion epidermis", "objective": "40"})
        request = urllib.request.Request(f"http://127.0.0.1:{ports[0]}/assets/upload?{query}", data=body, method="POST",
                                         headers={**studio, "Content-Type": "application/octet-stream"})
        try:
            with urllib.request.urlopen(request, timeout=HANG_S) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as refused:
            return refused.code, json.loads(refused.read())

    try:
        status, reply = upload("IMG_0042.PNG", data)
        item = reply["item"]
        assert status == 200 and item["kind"] == "still" and item["group"] == "Added here" and item["seconds"] == 0.0
        assert item["name"].endswith("-onion-epidermis-picture.png") and item["title"].endswith("(picture)")
        side = json.loads((footage / (item["name"] + ".json")).read_text())
        assert side["kind"] == "still" and (side["width"], side["height"]) == (400, 300) and side["optics"] == {"objective": 40.0}
        status, reply = upload("notes.png", b"not a picture")
        assert status == 400 and "does not open as a picture" in reply["error"]
        status, reply = upload("notes.txt", b"words")
        assert status == 400 and "pictures (.bmp, .jpeg, .jpg, .png, .tif, .tiff, .webp)" in reply["error"]

        with urllib.request.urlopen(f"http://127.0.0.1:{ports[0]}/assets/library", timeout=HANG_S) as response:
            listed = {i["name"]: i for i in json.loads(response.read())["items"]}
        assert listed[item["name"]]["kind"] == "still" and listed["added/clip.mp4"]["kind"] == "recording"
        name = urllib.parse.quote(item["name"])

        def thumbnail() -> tuple[str, bytes] | None:  # made at background priority: a 503 is "not yet", as the studio retries
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{ports[0]}/assets/thumb?name={name}", timeout=30) as response:
                    return response.headers["Content-Type"], response.read()
            except urllib.error.HTTPError as busy:
                if busy.code != 503:
                    raise
                return None

        kind, thumb = until(thumbnail)
        assert kind == "image/jpeg" and bridge_module.jpeg_size(thumb)[0] == 240
    finally:
        server.shutdown()

@pytest.mark.parametrize("action", ["reconnect", "reset_camera"])
def test_isolated_workspace_cannot_reset_shared_camera(bridge_module, tmp_path, monkeypatch, action):
    bridge = make_bridge(bridge_module, tmp_path, SILENT, no_phone=True)
    monkeypatch.setenv("MICROMIND_WORKSPACE_STUDY", "1")
    monkeypatch.setattr(bridge, "restart", lambda *a, **k: pytest.fail("preview must not restart hardware"))
    monkeypatch.setattr(bridge, "_stop_capture", lambda *a, **k: pytest.fail("preview must not stop hardware"))
    result = getattr(bridge, action)()
    assert not result["ok"]
    assert "isolated preview" in result["error"]
    assert bridge_module._test_resets == []


def test_isolated_workspace_cannot_change_camera_source(bridge_module, tmp_path, monkeypatch):
    bridge = make_bridge(bridge_module, tmp_path, SILENT, no_phone=True)
    monkeypatch.setenv("MICROMIND_WORKSPACE_STUDY", "1")
    monkeypatch.setattr(bridge, "_stop_capture", lambda *a, **k: pytest.fail("preview must not stop hardware"))
    with pytest.raises(ValueError, match="isolated preview"):
        bridge_module.switch_source(bridge.args, bridge, "continuity")


# --- L1c options: the media worker's pace, a file reader stopped at once, a switch's steps timed ----------------------
def _frames_at(bridge, fps: float, seconds: float) -> None:
    """The bridge's own frame times as if it had made `fps` for the last `seconds`."""
    now = time.monotonic()
    bridge.frame_times.clear()
    bridge.frame_times.extend(now - seconds + n / fps for n in range(int(seconds * fps)))


def test_the_media_pace_is_one_job_unless_it_follows_the_machine(bridge_module, tmp_path, cleanup):
    bridge = make_bridge(bridge_module, tmp_path, SILENT)
    cleanup.append(bridge)
    assert bridge.args.media_pace == "follow"  # the default since L1c's bench
    bridge.args.media_pace = "one"  # the earlier way
    assert bridge.media_pace(False) == 1 and bridge.media_pace(True) == 1
    bridge.args.media_pace = "follow"
    full = min(8, os.cpu_count() or 1)
    assert bridge.media_pace(False) == full  # no capture runs: no live feed to keep
    assert bridge._launch("test", replace=False)
    bridge.last_start_at = time.monotonic() - 10
    _frames_at(bridge, 30.0, 4.0)
    assert bridge.own_fps() == pytest.approx(30.0, abs=0.5) and bridge.media_pace(False) == min(2, os.cpu_count() or 1)
    assert bridge.media_pace(True) == full  # requested pictures can pass slow speculative probes
    _frames_at(bridge, 20.0, 4.0)  # the bridge lost its frame rate: one at a time, as the earlier way
    assert bridge.media_pace(True) == 1 and bridge.media_pace(False) == 1
    # A real camera a little under --fps (Continuity, Blackmagic or a stream at 24-25 fps against 30): background work
    # still goes on, one job at a time, never none (review of #372).
    for fps in (24.0, 25.0):
        _frames_at(bridge, fps, 4.0)
        assert bridge.own_fps() < 0.95 * bridge.args.fps
        assert bridge.media_pace(False) == 1 and bridge.media_pace(True) == 1
    bridge.last_start_at = time.monotonic() - 0.5  # a capture just started (a switch): not a rate to judge yet
    assert bridge.own_fps() is None and bridge.media_pace(False) == 1


def test_the_media_pace_is_chosen_at_launch_and_kept_by_the_library(bridge_module, tmp_path, monkeypatch):
    assert bridge_module.MEDIA_PACE == "follow"
    monkeypatch.setattr(bridge_module, "_LIBRARIES", {})
    monkeypatch.setattr(bridge_module, "MEDIA_PACE", "one")
    assert bridge_module.library_for(tmp_path / "a").worker.pace is None
    monkeypatch.setattr(bridge_module, "MEDIA_PACE", "follow")
    assert bridge_module.library_for(tmp_path / "b").worker.pace is bridge_module.media_pace
    assert bridge_module.media_pace(False) == 1  # before the bridge serves: one at a time


@pytest.mark.parametrize("how, under", [("grace", 2.5), ("kill", 0.2)])
def test_a_file_played_as_the_camera_is_stopped_at_once_when_asked(bridge_module, tmp_path, monkeypatch, cleanup,
                                                                     wall_clock, how, under):
    """--file-stop (L1c): FFmpeg reading a file whose reader stopped reading never honours SIGTERM, so `grace` waits
    0.2 s before SIGKILL on every switch and seek; `kill` stops it at once. The phone's capture keeps its grace."""
    from types import SimpleNamespace

    bridge = make_bridge(bridge_module, tmp_path, IGNORES_SIGTERM, file_stop=how)
    cleanup.append(bridge)
    monkeypatch.setattr(bridge_module, "SIMULATED_CAMERA", SimpleNamespace(still=False))
    assert bridge._launch("test", replace=False)
    time.sleep(0.3)  # let it install its SIGTERM handler
    t0 = time.monotonic()
    bridge._stop_capture("switching feed")
    wall_clock.within(time.monotonic() - t0, under, f"stopping a file reader ({how})")
    assert live_fakes(bridge._test_marker) == 0


@needs_ffmpeg
def test_a_switch_says_how_long_each_of_its_steps_took(bridge_module, tmp_path, monkeypatch, cleanup, capsys):
    footage = tmp_path / "footage"
    _mp4(footage / "leaf.mp4")
    bridge = make_bridge(bridge_module, tmp_path, FRAMES, footage_dir=str(footage))
    cleanup.append(bridge)
    monkeypatch.setattr(bridge_module, "SIMULATED_CAMERA", None)
    state = bridge_module.switch_feed(bridge.args, bridge, "leaf.mp4")
    steps = state["steps"]
    assert list(steps) == ["find", "probe", "note", "stop", "snapshot", "launch", "firstFrame", "state",
                           "total"]
    assert all(ms >= 0 for ms in steps.values())
    assert steps["total"] == pytest.approx(sum(ms for name, ms in steps.items() if name != "total"), abs=1.0)
    assert state["switch"]["action"] == "live"
    assert "Feed switched to test footage leaf.mp4: find " in capsys.readouterr().out
    seek = bridge_module.seek_feed(bridge.args, bridge, 1.0)
    assert list(seek["steps"]) == ["stop", "note", "snapshot", "launch", "firstFrame", "state", "total"]
    bridge_module.switch_feed(bridge.args, bridge, None)


@needs_ffmpeg
def test_a_played_files_probe_is_kept_by_the_file_as_it_is(bridge_module, tmp_path, monkeypatch):
    """--probe-cache on (L1c): FFprobe counts a recording once; the same file plays again without it, and a changed
    or failing file is read again."""
    import simulated_camera

    video = _mp4(tmp_path / "leaf.mp4", seconds=1)
    calls: list[Path] = []
    real = simulated_camera._probe
    monkeypatch.setattr(simulated_camera, "_probe", lambda path, fps=30.0: calls.append(path) or real(path, fps))
    monkeypatch.setattr(simulated_camera, "_PROBED", {})
    first = simulated_camera.SimulatedCamera(str(video), "iPhoneWild Camera", probe=simulated_camera.probe_kept)
    again = simulated_camera.SimulatedCamera(str(video), "iPhoneWild Camera", probe=simulated_camera.probe_kept)
    assert (first.fps, first.total_frames) == (again.fps, again.total_frames) == (30.0, 30) and len(calls) == 1
    replaced = _mp4(tmp_path / "other.mp4", seconds=1)
    os.utime(replaced, ns=(video.stat().st_atime_ns, video.stat().st_mtime_ns))
    replaced.replace(video)  # another file at the same path, same size and time: a new inode, read again
    assert simulated_camera.SimulatedCamera(str(video), "x", probe=simulated_camera.probe_kept).total_frames == 30
    assert len(calls) == 2
    calls.clear()
    _mp4(video, seconds=2)  # the file changed: read again
    assert simulated_camera.SimulatedCamera(str(video), "x", probe=simulated_camera.probe_kept).total_frames == 60
    assert len(calls) == 1
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    for _ in range(2):
        with pytest.raises(ValueError):
            simulated_camera.SimulatedCamera(str(broken), "x", probe=simulated_camera.probe_kept)
    assert len(calls) == 3  # a file that fails is never kept
    assert simulated_camera.SimulatedCamera(str(video), "x").total_frames == 60 and len(calls) == 4  # off: every time
    args = SimpleNamespace(probe_cache="on")
    assert bridge_module.file_probe(args) is simulated_camera.probe_kept
    assert bridge_module.file_probe(SimpleNamespace(probe_cache="off")) is None


def test_a_file_reader_that_never_exits_after_kill_does_not_hold_later_switches(bridge_module, tmp_path, monkeypatch,
                                                                                  cleanup, capsys):
    """--file-stop kill (review of #372): the wait after SIGKILL is bounded (2 s) and said, as the phone's is, so a reader
    stuck in uninterruptible I/O never holds `lifecycle`, and with it every later switch, for good."""
    waits: list[object] = []

    class Stuck:
        pid, returncode = 4242, None

        def poll(self):
            return None

        def kill(self):
            pass

        def wait(self, timeout=None):
            waits.append(timeout)
            raise subprocess.TimeoutExpired("ffmpeg", timeout)

    bridge = make_bridge(bridge_module, tmp_path, SILENT, file_stop="kill")
    cleanup.append(bridge)
    monkeypatch.setattr(bridge_module, "SIMULATED_CAMERA", SimpleNamespace(still=False))
    bridge.process = Stuck()
    bridge._stop_capture("switching feed")
    assert waits == [2] and bridge.process is None
    assert "FFmpeg PID 4242 did not exit after SIGKILL" in capsys.readouterr().out
    assert bridge.lifecycle.acquire(blocking=False)  # free for the next switch
    bridge.lifecycle.release()


def test_configured_recording_root_allows_owned_movies_without_widening_confinement(bridge_module, tmp_path):
    root = tmp_path / 'recordings'
    root.mkdir()
    args = SimpleNamespace(media_root=[], datasets_dir=None, footage_dir=None, recording_dir=str(root))
    assert bridge_module.media_roots(args) == [root.resolve()]
    for suffix in ('.mp4', '.webm'):
        movie = root / f'annotated{suffix}'
        movie.write_bytes(b'synthetic path fixture; not codec evidence')
        assert bridge_module.asset_file(args, str(movie))[0] == movie.resolve()
    outside = tmp_path / 'other.mp4'
    outside.write_bytes(b'outside recording root')
    (root / 'escape.mp4').symlink_to(outside)
    for path in (outside, root / '..' / 'other.mp4', root / 'escape.mp4'):
        with pytest.raises(ValueError, match='is not in a folder this bridge plays from'):
            bridge_module.asset_file(args, str(path))
    args.recording_dir = None
    with pytest.raises(ValueError, match='is not in a folder this bridge plays from'):
        bridge_module.asset_file(args, str(root / 'annotated.mp4'))


@pytest.mark.parametrize('duration,container,frames,expected', [
    ('47.368333', None, 1421, 47.368333),
    ('N/A', '47.368333', 1421, 47.368333),
    ('4', None, 120, 4),
    (None, None, 120, 4),
    ('nan', '-1', 120, 4),
    ('inf', '0', 120, 4),
])
def test_probe_uses_declared_timeline_for_variable_rate_media(bridge_module, tmp_path, monkeypatch,
                                                            duration, container, frames, expected):
    import simulated_camera
    video = tmp_path / 'timeline.mp4'
    video.write_bytes(b'synthetic probe metadata fixture')
    rate = '50/1' if frames == 1421 else '30/1'
    reply = {'streams': [{'r_frame_rate': rate, 'nb_read_packets': str(frames), 'duration': duration}],
             'format': {'duration': container}}
    monkeypatch.setattr(simulated_camera.shutil, 'which', lambda _: '/fixture/ffprobe')
    monkeypatch.setattr(simulated_camera.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=json.dumps(reply)))
    camera = simulated_camera.SimulatedCamera(str(video), 'fixture')
    assert camera.total_frames == frames
    assert camera.total_frames / camera.fps == pytest.approx(expected)
    if frames == 120:
        assert camera.fps == 30  # Existing constant-rate and missing/invalid-duration fallback.
    camera.paused_frame = 0
    camera.seek(int((expected - 1) * camera.fps))
    assert abs(camera.playback()['seconds'] - (expected - 1)) < 1 / camera.fps + .01
