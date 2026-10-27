"""The simulated camera's picture faults (W0-4): degradations become FFmpeg filters, a content
freeze keeps frames arriving with the same picture, and bad parameters are refused."""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "experiments" / "iphone-camera-feed" / "scripts"))
from simulated_camera import DEGRADATIONS, SimulatedCamera, degrade_filters  # noqa: E402


def camera() -> SimulatedCamera:
    cam = SimulatedCamera.__new__(SimulatedCamera)  # no recording needed for these
    import threading

    cam.lock, cam.faults = threading.RLock(), []
    cam.degradation, cam.repeat_until, cam._repeated = {}, 0.0, None
    cam.paused_frame, cam._held = None, None  # playing (the player's pause holds a picture instead)
    return cam


def test_no_degradation_is_the_live_chain():
    assert camera().filter_chain(30, "1280x720") == "fps=30,scale=1280:720"


def test_degradations_in_camera_order():
    filters = degrade_filters({"noise": 10, "blur": 3, "zoom": 2, "vignette": 0.5, "exposure": -0.4, "motion": 4})
    assert [f.split("=")[0] for f in filters] == ["crop", "gblur", "tmix", "vignette", "eq", "noise"]
    assert "gblur=sigma=3" in filters and "eq=brightness=-0.200" in filters


def test_degrade_validates_and_clears():
    cam = camera()
    assert cam.degrade(blur="2.5") == {"blur": 2.5}
    assert "gblur=sigma=2.5" in cam.filter_chain(30, "1280x720")
    with pytest.raises(ValueError):
        cam.degrade(blur=99)
    with pytest.raises(ValueError):
        cam.degrade(sharpen=1)
    assert cam.degrade() == {}
    assert cam.filter_chain(30, "1280x720") == "fps=30,scale=1280:720"
    assert set(DEGRADATIONS) == {"zoom", "blur", "motion", "vignette", "exposure", "noise", "swimmer"}
    cam.degrade(swimmer=120)
    assert "overlay=x=" in cam.filter_chain(30, "1280x720") and cam.filter_chain(30, "1280x720").endswith("scale=1280:720")


def test_repeat_sends_the_same_picture_while_frames_keep_coming():
    cam = camera()
    assert cam.pass_frame(b"a") == b"a"
    cam.repeat(0.2)
    assert [cam.pass_frame(f) for f in (b"b", b"c", b"d")] == [b"b", b"b", b"b"]
    time.sleep(0.25)
    assert cam.pass_frame(b"e") == b"e"



def test_pause_holds_one_picture_while_frames_keep_coming_and_play_goes_on():
    """The studio's player: paused, every frame is the one that arrived first (a slide at rest, the
    app stays live); played, frames pass again."""
    cam = camera()
    cam.fps, cam.total_frames, cam.target_device = 30.0, 900, "phone"
    cam._world_origin = time.monotonic() - 10.0  # 10 s in
    cam.pause()
    assert cam.paused_frame == 300 and cam.world_frame() == 300
    assert [cam.pass_frame(f) for f in (b"p", b"q", b"r")] == [b"p", b"p", b"p"]
    cam.seek(600)  # moved while paused: the next frame is held there
    assert cam.world_frame() == 600 and cam.pass_frame(b"s") == b"s" and cam.pass_frame(b"t") == b"s"
    cam.resume()
    assert cam.paused_frame is None and cam.pass_frame(b"u") == b"u" and 599 <= cam.world_frame() <= 602
