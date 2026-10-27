"""The camera link made robust after 2026-10-11, and the second way in.

On 2026-10-11 the iPhone stayed listed by macOS but sent nothing: the bridge asked 19 times in 4 minutes,
the console and the studio added reconnects and device scans of their own, and only a Continuity Camera
reset by hand brought the picture back. Here, with fake FFmpegs (test_bridge.py's fixture):
- a listed but silent phone is healed (Continuity reset, then asked again), with backoff;
- Reconnect heals it too, and reconnects asked at once share one;
- probes never scan devices while a capture runs;
- the bridge says why there is no picture, in plain words.

And the phone app stream (Blackmagic Camera's custom SRT server) as a live source the console can choose:
its URL checks, its secrets kept out of everything shown, the choice kept, and a real SRT stream pushed
into a real bridge listener (skipped without FFmpeg's SRT support).
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.test_bridge import FRAMES, SCRIPTS, SILENT, bridge_module, cleanup, make_bridge, serve  # noqa: F401

SECRET = "pass-phrase-123"


# --- Continuity: a phone that is listed but silent ------------------------------------------------------

def test_a_listed_but_silent_phone_is_healed_with_backoff_until_it_answers(bridge_module, tmp_path, cleanup):
    resets = bridge_module._test_resets
    bridge = make_bridge(bridge_module, tmp_path, SILENT, first_frame_timeout=0.4, auto_reset_min_interval=1.0,
                         continuity_reset_cooldown=0.0)
    cleanup.append(bridge)
    bridge._launch("test", replace=False)
    first = bridge.health()["pid"]
    time.sleep(0.5)
    bridge._monitor_tick()
    assert len(resets) == 1 and bridge.health()["pid"] != first  # heal 1: reset, asked again
    second = bridge.health()["pid"]

    time.sleep(0.5)  # past the new capture's first-frame wait, inside the 1 s gap after the reset
    bridge._monitor_tick()
    assert len(resets) == 1 and bridge.health()["pid"] == second  # left asking, never replaced as is

    time.sleep(0.6)
    bridge._monitor_tick()
    assert len(resets) == 2 and bridge.health()["lastHeal"]["heal"] == 2  # heal 2, after the gap
    time.sleep(1.2)
    bridge._monitor_tick()
    assert len(resets) == 2  # the gap doubled (2 s)

    marker = bridge._test_marker
    bridge.ffmpeg_command = lambda: [sys.executable, "-c", FRAMES.format(marker=marker)]  # the phone answers
    time.sleep(1.0)
    bridge._monitor_tick()
    assert len(resets) == 3
    _, frame = bridge.wait_for_frame(bridge.frame_count, timeout=3)
    assert frame and bridge.health()["state"] == "live"
    assert bridge.silent_heals == 0 and bridge.health()["diagnosis"] is None and bridge.health()["lastError"] == ""


def test_reconnect_resets_a_silent_phone_but_not_one_still_connecting(bridge_module, tmp_path, cleanup):
    resets = bridge_module._test_resets
    bridge = make_bridge(bridge_module, tmp_path, SILENT, silent_after=0.3, continuity_reset_cooldown=0.0,
                         device_wait_timeout=0.1)
    cleanup.append(bridge)
    bridge._launch("test", replace=False)
    result = bridge.reconnect(wait_timeout=0.1)  # asked a moment ago: still connecting
    assert resets == [] and result["continuityReset"] is None
    time.sleep(0.4)
    result = bridge.reconnect(wait_timeout=0.1)  # silent for longer than silent_after
    assert len(resets) == 1 and result["continuityReset"]["attempted"]
    assert result["request"]["sent"]


def test_reconnects_asked_at_once_share_one_request(bridge_module, tmp_path, cleanup):
    bridge = make_bridge(bridge_module, tmp_path, SILENT)
    cleanup.append(bridge)
    results: list[dict] = []
    threads = [threading.Thread(target=lambda: results.append(bridge.reconnect(wait_timeout=0.8))) for _ in range(3)]
    for thread in threads:
        thread.start()
        time.sleep(0.05)
    for thread in threads:
        thread.join()
    assert bridge.health()["requestCount"] == 1  # one capture asked for, not three replacing each other
    assert sum(1 for result in results if result.get("joined")) == 2


def test_probes_do_not_scan_devices_while_a_capture_runs(bridge_module, tmp_path, cleanup, monkeypatch):
    scans: list[str] = []
    monkeypatch.setattr(bridge_module, "make_device_snapshot", lambda target: scans.append(target) or {"ok": True, "targetDevice": target})
    bridge = make_bridge(bridge_module, tmp_path, SILENT)
    cleanup.append(bridge)
    state = bridge_module.feed_state(bridge.args, bridge, probe_camera=True)  # nothing runs: a real scan
    assert len(scans) == 1 and state["cameraVisible"] is True
    bridge._launch("test", replace=False)
    for _ in range(3):
        state = bridge_module.feed_state(bridge.args, bridge, probe_camera=True)
    assert len(scans) == 1 and state["cameraVisible"] is True  # the phone was left alone


def test_the_bridge_says_why_there_is_no_picture(bridge_module, tmp_path, cleanup):
    present = bridge_module._test_present
    bridge = make_bridge(bridge_module, tmp_path, SILENT, silent_after=0.2)
    cleanup.append(bridge)
    bridge.facts = {"deskView": True, "usb": False, "checkedAt": time.monotonic()}
    bridge._launch("test", replace=False)
    time.sleep(0.3)
    diagnosis = bridge.health()["diagnosis"]
    assert diagnosis["code"] == "phone-silent"
    assert diagnosis["text"] == "The iPhone is listed but hasn't sent its picture."
    assert diagnosis["askedSecondsAgo"] >= 0 and diagnosis["nextHealInSeconds"] is not None
    assert diagnosis["steps"][0].startswith("Quit Desk View")
    assert any("USB" in step for step in diagnosis["steps"])
    assert any("resets Continuity Camera" in step for step in diagnosis["steps"])
    assert "Claude" in diagnosis["note"]

    present["ok"] = False
    bridge.update_device_snapshot({"ok": False})
    assert bridge.health()["diagnosis"]["code"] == "phone-missing"


def test_the_monitor_reads_the_macs_facts_only_while_there_is_no_picture(bridge_module, tmp_path, cleanup, monkeypatch):
    reads: list[int] = []
    monkeypatch.setattr(bridge_module, "desk_view_running", lambda: reads.append(1) or True)
    bridge = make_bridge(bridge_module, tmp_path, FRAMES)
    cleanup.append(bridge)
    bridge._monitor_tick()  # no capture yet: read
    assert reads == [1] and bridge.facts["deskView"] is True
    bridge._monitor_tick()  # within FACTS_TTL_S: not read again
    assert reads == [1]
    bridge._launch("test", replace=False)
    bridge.wait_for_frame(0, timeout=3)
    bridge.facts["checkedAt"] = 0.0
    bridge._monitor_tick()  # live: never read
    assert reads == [1]


# --- The phone app stream as a live source ----------------------------------------------------------------

@pytest.fixture()
def stream_module(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import importlib

    import stream_source

    return importlib.reload(stream_source)


def test_a_stream_url_is_checked_and_its_secrets_never_shown(stream_module):
    url = f"srt://0.0.0.0:9710?mode=listener&latency=120000&passphrase={SECRET}&streamid=cam1-key"
    source = stream_module.StreamSource(url)
    assert source.label == "SRT stream :9710"
    assert source.redacted == "srt://0.0.0.0:9710"
    described = json.dumps(source.describe())
    assert SECRET not in described and "cam1-key" not in described
    assert source.describe()["secured"] is True
    assert source.describe()["connectTo"].startswith("srt://") and source.describe()["connectTo"].endswith(":9710")
    said = source.redact(f"[srt @ 0x1] Connection to {url} failed: wrong passphrase {SECRET}")
    assert SECRET not in said and "cam1-key" not in said and "srt://0.0.0.0:9710" in said
    for bad in ("file:///etc/passwd", "pipe:0", "concat:a|b", "http://example.com/x.ts", "9710"):
        with pytest.raises(ValueError):
            stream_module.StreamSource(bad)


def test_a_stream_capture_reads_the_url_into_the_cameras_frames(stream_module):
    from types import SimpleNamespace

    args = SimpleNamespace(size="1280x720", fps=30, jpeg_quality=4)
    command = stream_module.StreamSource("srt://0.0.0.0:9710?mode=listener").capture_command("ffmpeg", args)
    assert command[1] == "-nostdin"  # `make stop` finds the bridge's FFmpegs by it
    assert command[command.index("-i") + 1] == "srt://0.0.0.0:9710?mode=listener"
    assert "force_original_aspect_ratio=decrease" in command[command.index("-vf") + 1]  # fitted, never stretched
    assert "fps=" not in command[command.index("-vf") + 1] and command[command.index("-fps_mode") + 1] == "passthrough"
    assert "-hwaccel" not in command  # VideoToolbox dropped most frames in this pipeline (2026-10-11)
    assert command[-3:] == ["-f", "image2pipe", "pipe:1"] or command[-1] == "pipe:1"
    rtmp = stream_module.StreamSource("rtmp://0.0.0.0:1935/live/my-key?listen=1").capture_command("ffmpeg", args)
    assert rtmp[rtmp.index("-i") - 2:rtmp.index("-i")] == ["-listen", "1"]
    assert "listen=1" not in rtmp[rtmp.index("-i") + 1]


def stream_args(bridge, tmp_path):
    bridge.args.source_file = str(tmp_path / "camera-source.json")
    bridge.args.device_name = bridge.args.target_device
    return bridge.args


def test_the_console_chooses_the_live_source_and_it_is_kept(bridge_module, tmp_path, cleanup):
    bridge = make_bridge(bridge_module, tmp_path, SILENT, first_frame_timeout=0.2)
    cleanup.append(bridge)
    args = stream_args(bridge, tmp_path)
    url = f"srt://127.0.0.1:19710?mode=listener&passphrase={SECRET}"
    state = bridge_module.switch_source(args, bridge, "wifi", url)
    health = bridge.health()
    assert state["ok"] and state["changed"]
    assert health["source"]["kind"] == "stream" and health["source"]["link"] == "blackmagic"  # "wifi" is its old name
    assert health["targetDevice"] == "Blackmagic Camera" and health["sourceKind"] == "blackmagic"
    assert [choice["active"] for choice in health["sourceChoices"]] == [False, True, False, False]  # an old pinned link shows as Blackmagic
    assert health["diagnosis"]["code"] == "phone-app-unreachable"  # no phone in the tests
    assert SECRET not in json.dumps(health)
    saved = Path(args.source_file)
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600  # it can hold a passphrase
    assert json.loads(saved.read_text())["kind"] == "blackmagic"
    assert bridge_module.initial_live_source(args) == ("blackmagic", url)

    time.sleep(0.3)
    pid = health["pid"]
    bridge._monitor_tick()  # a listener waiting for the phone's app is never restarted or "healed"
    assert bridge.health()["pid"] == pid and bridge_module._test_resets == []

    bridge_module.switch_source(args, bridge, "continuity")
    health = bridge.health()
    assert health["targetDevice"] == "iPhoneWild Camera" and health["source"]["kind"] == "device"
    assert bridge_module.initial_live_source(args)[0] == "continuity"
    with pytest.raises(ValueError):
        bridge_module.switch_source(args, bridge, "wifi", "file:///etc/passwd")
    assert bridge.health()["source"]["kind"] == "device" and bridge.health()["running"]


def test_a_launch_url_wins_over_the_saved_choice(bridge_module, tmp_path, cleanup, monkeypatch):
    bridge = make_bridge(bridge_module, tmp_path, SILENT)
    cleanup.append(bridge)
    args = stream_args(bridge, tmp_path)
    bridge_module.save_source_choice(args, "continuity")
    monkeypatch.setenv("MICROMIND_CAMERA_SOURCE", "srt://0.0.0.0:9800?mode=listener")
    assert bridge_module.initial_live_source(args) == ("blackmagic", "srt://0.0.0.0:9800?mode=listener")


def test_ffmpegs_words_about_a_stream_are_redacted(bridge_module, tmp_path, cleanup):
    url = f"srt://127.0.0.1:19711?mode=listener&passphrase={SECRET}"
    bridge = make_bridge(bridge_module, tmp_path, SILENT)
    cleanup.append(bridge)
    bridge_module.use_live_source(stream_args(bridge, tmp_path), "wifi", url)
    said = f"import sys; sys.stderr.write('[srt] {url}: wrong passphrase {SECRET}\\n'); sys.exit(1)"
    bridge.ffmpeg_command = lambda: [sys.executable, "-c", said]
    bridge._launch("test", replace=False)
    time.sleep(0.6)
    assert SECRET not in json.dumps(bridge.health())


def test_the_source_switch_needs_the_consoles_header(bridge_module, tmp_path, cleanup):
    bridge = make_bridge(bridge_module, tmp_path, SILENT)
    cleanup.append(bridge)
    stream_args(bridge, tmp_path)
    ports: list[int] = []
    server = serve(bridge_module, bridge, ports)

    def post(headers: dict) -> tuple[int, dict]:
        request = urllib.request.Request(f"http://127.0.0.1:{ports[0]}/api/source?kind=wifi", method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    try:
        assert post({})[0] == 403
        assert bridge.health()["source"]["kind"] == "device"
        status, body = post({"X-MicroscopeMind": "camera-source"})
        assert status == 200 and body["ok"] and bridge.health()["source"]["kind"] == "stream"
        with urllib.request.urlopen(f"http://127.0.0.1:{ports[0]}/api/source", timeout=5) as response:
            assert [choice["kind"] for choice in json.loads(response.read())["choices"]] == ["continuity", "blackmagic", "other", "none"]
    finally:
        server.shutdown()


def test_the_bridge_runs_on_the_macs_own_python():
    """studio.sh starts the bridge with the system python3 (3.9 on this Mac), while these tests run on 3.13."""
    system = Path("/usr/bin/python3")
    if not system.exists():
        pytest.skip("no system Python")
    for name in ("iphone_bridge_server.py", "stream_source.py"):
        result = subprocess.run([str(system), "-c", f"import ast,sys; ast.parse(open({str(SCRIPTS / name)!r}).read())"],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        compiled = subprocess.run([str(system), "-m", "py_compile", str(SCRIPTS / name)], capture_output=True, text=True,
                                  env={**os.environ, "PYTHONPYCACHEPREFIX": str(Path("/tmp") / f"mm-pyc-{os.getpid()}")})
        assert compiled.returncode == 0, compiled.stderr


# --- A real SRT stream into a real bridge listener ----------------------------------------------------------

def ffmpeg_has_srt() -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    protocols = subprocess.run([ffmpeg, "-hide_banner", "-protocols"], capture_output=True, text=True).stdout
    return any(line.strip() == "srt" for line in protocols.splitlines())


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def push_stream(port: int) -> subprocess.Popen:
    """What the phone's app does: an H.264 picture over SRT, calling the bridge's listener."""
    return subprocess.Popen(
        [shutil.which("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-re",
         "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30", "-c:v", "libx264", "-preset", "ultrafast",
         "-tune", "zerolatency", "-g", "15", "-f", "mpegts", f"srt://127.0.0.1:{port}?mode=caller&latency=50000"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


@pytest.mark.skipif(not ffmpeg_has_srt(), reason="FFmpeg without SRT")
def test_a_real_srt_stream_reaches_the_bridge_and_comes_back_after_a_drop(bridge_module, tmp_path, cleanup):
    port = free_udp_port()
    bridge = make_bridge(bridge_module, tmp_path, SILENT, stall_timeout=2.0, rescan_interval=0.5)
    cleanup.append(bridge)
    bridge_module.use_live_source(stream_args(bridge, tmp_path), "wifi", f"srt://127.0.0.1:{port}?mode=listener&latency=50000")
    del bridge.ffmpeg_command  # the real capture command: FFmpeg listening for the stream
    bridge._launch("test", replace=False)
    time.sleep(0.5)
    pusher = push_stream(port)
    try:
        _, frame = bridge.wait_for_frame(0, timeout=15)
        assert frame and frame.startswith(b"\xff\xd8")
        assert bridge.health()["state"] == "live" and bridge.health()["source"]["kind"] == "stream"

        pusher.kill()  # the phone stops streaming
        pusher.wait()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and bridge.health()["state"] == "live":
            time.sleep(0.2)
        assert bridge.health()["state"] != "live"
        for _ in range(12):  # the monitor listens again (as many ticks as it takes)
            bridge._monitor_tick()
            time.sleep(0.25)
        assert bridge.health()["running"]
        counted = bridge.frame_count
        pusher = push_stream(port)  # and it calls back
        _, frame = bridge.wait_for_frame(counted, timeout=20)
        assert frame, bridge.health()
        assert bridge_module._test_resets == []  # never a Continuity reset for a stream
    finally:
        pusher.kill()


# --- Steadiness: the app's standard is 30 fps, evenly, whatever the source ----------------------------------

def test_steadiness_tells_an_even_feed_from_a_bunched_one(bridge_module):
    even = [i / 30 for i in range(151)]  # 5 s at 30 fps
    summary = bridge_module.cadence_summary(even, now=5.0, window=5.0)
    assert summary["even"] and 29.5 <= summary["fps"] <= 30.5 and abs(summary["p95Ms"] - 33.3) < 1 and summary["gaps"] == 0
    # The phone over Wi-Fi (2026-10-11): frames handed over in batches, about 23 fps, p95 gaps near 200 ms.
    bunched = [batch * 0.2 + i * 0.004 for batch in range(25) for i in range(5)]
    summary = bridge_module.cadence_summary(bunched, now=5.0, window=5.0)
    assert not summary["even"] and summary["p95Ms"] > 150 and summary["gaps"] >= 20
    assert bridge_module.cadence_summary([], now=1.0, window=5.0)["fps"] == 0.0


def test_the_console_reads_the_rhythm_and_runs_a_source_check(bridge_module, tmp_path, cleanup):
    bridge = make_bridge(bridge_module, tmp_path, FRAMES, trials_dir=str(tmp_path / "trials"))
    cleanup.append(bridge)
    bridge._launch("test", replace=False)
    bridge.wait_for_frame(0, timeout=3)
    time.sleep(1.2)
    trace = bridge.cadence_trace()
    assert len(trace["ago"]) >= 20 and all(0 <= ago <= 5000 for ago in trace["ago"])
    assert bridge.health()["cadence"]["fps"] > 20
    trial = {"id": "t1", "source": "continuity", "title": "iPhone", "seconds": 1.5, "startedAt": "now", "locked": None}
    bridge.trial_running = dict(trial)
    bridge._run_trial(trial)
    result = bridge.trials[-1]
    assert result["frames"] > 30 and result["fps"] > 20 and result["restarts"] == 0 and bridge.trial_running is None
    kept = (tmp_path / "trials" / "trials.jsonl").read_text().splitlines()
    assert json.loads(kept[-1])["id"] == "t1"
    assert bridge.health()["trialCount"] == 1



# --- The capture app: macOS wakes the iPhone only for an app ----------------------------------------------------

def test_the_iphone_is_captured_by_the_bridges_app_and_a_silent_phone_is_asked_again_without_a_reset(
        bridge_module, tmp_path, cleanup, monkeypatch):
    resets = bridge_module._test_resets
    monkeypatch.setattr(bridge_module, "native_capture_ready", lambda: True)
    bridge = make_bridge(bridge_module, tmp_path, SILENT, first_frame_timeout=0.3, silent_after=0.1,
                         continuity_reset_cooldown=0.0, device_wait_timeout=0.1)
    cleanup.append(bridge)
    command = bridge_module.CameraBridge.ffmpeg_command(bridge)  # the real command, not the test's fake
    assert command[0].endswith("capture-app/run.sh") and command[1:4] == ["iPhoneWild Camera", "1280x720", "30"]
    assert bridge.health()["capture"] == "app"
    bridge._launch("test", replace=False)
    first = bridge.health()["pid"]
    time.sleep(0.4)
    bridge._monitor_tick()
    assert bridge.health()["pid"] != first and resets == []  # asked again; Continuity not reset
    assert bridge.health()["lastHeal"]["reset"] is False
    time.sleep(0.2)
    bridge.reconnect(wait_timeout=0.1)
    assert resets == []  # Reconnect asks again too, without a reset
    steps = bridge.health()["diagnosis"]["steps"]
    assert "The bridge asks the iPhone again by itself." in steps


def test_the_app_without_camera_permission_says_how_to_allow_it(bridge_module, tmp_path, cleanup, monkeypatch):
    monkeypatch.setattr(bridge_module, "native_capture_ready", lambda: True)
    denied = ("import sys; sys.stderr.write('[capture] camera access is denied for MicroscopeMind Camera: allow it in "
              "System Settings > Privacy & Security > Camera\\n'); sys.exit(4)  # {marker}")
    bridge = make_bridge(bridge_module, tmp_path, denied)
    cleanup.append(bridge)
    bridge._launch("test", replace=False)
    time.sleep(0.6)
    diagnosis = bridge.health()["diagnosis"]
    assert diagnosis["code"] == "camera-permission" and "Privacy & Security" in diagnosis["steps"][0]


def test_the_capture_app_falls_back_to_ffmpeg_when_not_built(bridge_module, tmp_path, cleanup, monkeypatch):
    monkeypatch.setattr(bridge_module, "CAPTURE_APP_BINARY", tmp_path / "missing")
    monkeypatch.setattr(bridge_module, "native_capture_ready", bridge_module.native_capture_ready.__wrapped__
                        if hasattr(bridge_module.native_capture_ready, "__wrapped__") else
                        (lambda: (tmp_path / "missing").exists()))
    bridge = make_bridge(bridge_module, tmp_path, SILENT)
    cleanup.append(bridge)
    command = bridge_module.CameraBridge.ffmpeg_command(bridge)
    assert "avfoundation" in command and bridge.health()["capture"] == "ffmpeg"


# --- Other camera: any camera this Mac sees, or a stream address (2026-10-13) ----------------------------

LISTING_WITH_A_MICROSCOPE = """[AVFoundation indev @ 0x1] AVFoundation video devices:
[AVFoundation indev @ 0x1] [0] FaceTime HD Camera
[AVFoundation indev @ 0x1] [1] USB Microscope
[AVFoundation indev @ 0x1] [2] iPhoneWild Camera
[AVFoundation indev @ 0x1] [3] Capture screen 0
[AVFoundation indev @ 0x1] AVFoundation audio devices:
[AVFoundation indev @ 0x1] [0] MacBook Pro Microphone
"""


def test_another_camera_is_captured_by_name_and_kept(bridge_module, tmp_path, cleanup, monkeypatch):
    """A USB microscope camera (or a webcam, a capture card): chosen by name from the cameras this Mac sees, captured
    by the same capture app as the iPhone, kept across restarts, and never a reason to reset Continuity Camera."""
    monkeypatch.setattr(bridge_module, "native_capture_ready", lambda: True)
    monkeypatch.setattr(bridge_module, "run_device_list", lambda: LISTING_WITH_A_MICROSCOPE)
    bridge = make_bridge(bridge_module, tmp_path, SILENT, first_frame_timeout=0.2)
    cleanup.append(bridge)
    args = stream_args(bridge, tmp_path)
    cameras = bridge_module.camera_list(args)
    assert [c["name"] for c in cameras] == ["FaceTime HD Camera", "USB Microscope", "iPhoneWild Camera"]  # no screens
    assert [c["iphone"] for c in cameras] == [False, False, True]

    with pytest.raises(ValueError, match="Choose a camera"):
        bridge_module.switch_source(args, bridge, "other")  # nothing chosen yet: said, nothing switched
    bridge_module.switch_source(args, bridge, "other", device="USB Microscope")
    health = bridge.health()
    assert health["sourceKind"] == "other" and health["targetDevice"] == "USB Microscope"
    command = bridge_module.CameraBridge.ffmpeg_command(bridge)
    assert "USB Microscope" in command  # the capture app, by name
    assert bridge.reset_continuity_agent_if_allowed()["attempted"] is False  # the iPhone's agent is left alone
    saved = json.loads(Path(args.source_file).read_text())
    assert saved["kind"] == "other" and saved["otherDevice"] == "USB Microscope"

    bridge_module.switch_source(args, bridge, "continuity")  # back to the iPhone: its own name again
    assert bridge.health()["targetDevice"] == "iPhoneWild Camera"
    assert bridge_module.camera_list(args)[1]["chosen"] is True  # the other camera is remembered
    bridge_module.switch_source(args, bridge, "other")  # and Other goes back to it
    assert bridge.health()["targetDevice"] == "USB Microscope"

    fresh = make_bridge(bridge_module, tmp_path, SILENT)
    cleanup.append(fresh)
    fresh.args.source_file = args.source_file
    assert bridge_module.initial_live_source(fresh.args) == ("other", None) and fresh.args.other_device == "USB Microscope"


def test_another_camera_by_its_stream_address(bridge_module, tmp_path, cleanup):
    """A network camera (RTSP, over TCP so a lossy Wi-Fi never tears the picture) or anything sending SRT or RTMP."""
    bridge = make_bridge(bridge_module, tmp_path, SILENT, first_frame_timeout=0.2)
    cleanup.append(bridge)
    args = stream_args(bridge, tmp_path)
    with pytest.raises(ValueError, match="srt://, rtmp:// or rtsp://"):
        bridge_module.switch_source(args, bridge, "other", url="file:///etc/passwd")
    url = f"rtsp://admin:{SECRET}@192.168.50.20:554/stream1"
    bridge_module.switch_source(args, bridge, "other", url=url)
    health = bridge.health()
    assert health["sourceKind"] == "other" and SECRET not in json.dumps(health)
    command = bridge_module.CameraBridge.ffmpeg_command(bridge)
    assert command[command.index("-rtsp_transport") + 1] == "tcp" and url in command
    doctor = bridge.doctor()
    chosen = next(item for item in doctor["items"] if item["id"] == "other-chosen")
    assert chosen["ok"] and SECRET not in json.dumps(doctor)


def test_the_other_cameras_checklist_says_what_to_do(bridge_module):
    import camera_doctor

    nothing = camera_doctor.checklist({"source": "other"})
    assert nothing["first"] == "other-chosen" and "Other camera" in nothing["items"][0]["fix"]
    unplugged = camera_doctor.checklist({"source": "other", "otherDevice": "USB Microscope", "devicePresent": False})
    first = next(item for item in unplugged["items"] if item["id"] == unplugged["first"])
    assert first["id"] == "other-connected" and "Plug USB Microscope" in first["fix"] and first["action"] == "reconnect"
    live = camera_doctor.checklist({"source": "other", "otherDevice": "USB Microscope", "devicePresent": True, "captureFrames": 90,
                                    "lastFrameAge": 0.03, "fps": 30.0, "frameSize": [1280, 720], "targetSize": [1280, 720],
                                    "frozenFor": 0.0})
    assert live["live"] and live["first"] is None


# --- No camera: pictures and videos only (Will, 2026-10-15) -----------------------------------------------

def test_no_camera_captures_scans_and_resets_nothing_and_is_kept(bridge_module, tmp_path, cleanup, monkeypatch):
    """Chosen when the session uses only pictures and videos: no capture, no device scan, no Continuity reset, no
    diagnosis (it is the user's choice, never a fault); kept across restarts; a camera chosen again goes back to work."""
    scans: list[str] = []
    real_snapshot = bridge_module.make_device_snapshot
    monkeypatch.setattr(bridge_module, "make_device_snapshot", lambda target: scans.append(target) or real_snapshot(target))
    bridge = make_bridge(bridge_module, tmp_path, SILENT, first_frame_timeout=0.2, rescan_interval=0.0)
    cleanup.append(bridge)
    args = stream_args(bridge, tmp_path)
    bridge_module.switch_source(args, bridge, "continuity")
    assert bridge.health()["running"]

    state = bridge_module.switch_source(args, bridge, "none")
    health = bridge.health()
    assert state["ok"] and state["switch"]["action"] == "no_camera"
    assert health["state"] == "no_camera" and health["stateLabel"] == "No camera in use: pictures and videos only"
    assert health["running"] is False and health["diagnosis"] is None and health["sourceKind"] == "none"
    assert [c["active"] for c in health["sourceChoices"]] == [False, False, False, True]
    assert json.loads(Path(args.source_file).read_text())["kind"] == "none"

    scans.clear()
    launches = health["restartCount"]
    for _ in range(5):
        bridge._monitor_tick()
        bridge_module.feed_state(args, bridge, probe_camera=True)
    bridge.ensure_capture("a viewer")
    health = bridge.health()
    assert scans == [] and bridge_module._test_resets == []
    assert health["restartCount"] == launches and health["continuityResets"] == 0 and not health["running"]
    assert bridge.reset_continuity_agent_if_allowed()["attempted"] is False
    assert bridge.doctor()["mode"] == "off" and bridge.doctor()["headline"] == "No camera"

    # Kept for the next start, by its old names too.
    assert bridge_module.initial_live_source(args) == ("none", None)
    bridge_module.save_source_choice(args, "off")
    assert bridge_module.initial_live_source(args)[0] == "none"

    # A picture or video played over it is a feed like any other.
    monkeypatch.setattr(bridge_module, "SIMULATED_CAMERA", object())
    assert bridge.no_camera() is False
    monkeypatch.setattr(bridge_module, "SIMULATED_CAMERA", None)

    bridge_module.switch_source(args, bridge, "continuity")
    assert bridge.health()["running"] and bridge.health()["sourceKind"] == "continuity"


def test_a_first_run_starts_on_the_default_source_and_a_saved_choice_wins(bridge_module, tmp_path, cleanup):
    bridge = make_bridge(bridge_module, tmp_path, SILENT)
    cleanup.append(bridge)
    args = stream_args(bridge, tmp_path)
    args.default_source = "none"  # studio.sh on a first run
    assert bridge_module.initial_live_source(args) == ("none", None)
    bridge_module.save_source_choice(args, "blackmagic")
    assert bridge_module.initial_live_source(args)[0] == "blackmagic"


def test_make_start_begins_a_first_run_on_no_camera():
    studio = (Path(__file__).resolve().parents[2] / "scripts" / "studio.sh").read_text()
    assert "--default-source none" in studio


def test_channel_listener_rehomes_only_the_previous_default(bridge_module, tmp_path, cleanup, monkeypatch):
    import stream_source
    bridge = make_bridge(bridge_module, tmp_path, SILENT)
    cleanup.append(bridge)
    args = stream_args(bridge, tmp_path)
    monkeypatch.setenv("MICROMIND_BLACKMAGIC_PORT", "9810")
    previous = "srt://0.0.0.0:9710?mode=listener&latency=20000"
    new = "srt://0.0.0.0:9810?mode=listener&latency=20000"
    assert stream_source.default_url() == new
    Path(args.source_file).write_text(json.dumps({"kind": "none", "url": previous}))
    assert bridge_module.initial_live_source(args) == ("none", None)
    assert args.stream_url == new  # choosing No camera still starts no listener
    Path(args.source_file).write_text(json.dumps({"kind": "blackmagic", "url": previous}))
    assert bridge_module.initial_live_source(args) == ("blackmagic", new)
    custom = "srt://0.0.0.0:9910?mode=listener&latency=50000"
    Path(args.source_file).write_text(json.dumps({"kind": "blackmagic", "url": custom}))
    assert bridge_module.initial_live_source(args) == ("blackmagic", custom)
