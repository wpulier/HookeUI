"""No camera is the user's choice (Will, 2026-10-15): the orchestrator says so and every model hears it as a choice,
never as a camera to fix ("otherwise the agent will tell users their camera is broken")."""
from __future__ import annotations

from orchestrator.frames import NO_CAMERA_LABEL, NO_CAMERA_WHY, FrameSource, no_picture


def source_with(health: dict | None) -> FrameSource:
    source = FrameSource("http://bridge.invalid", lambda frame: None)
    source.bridge_health = health
    return source


def test_the_feed_says_no_camera_in_use_not_a_connection_problem():
    source = source_with({"ok": False, "state": "no_camera"})
    assert source.no_camera() and source.state() == ("no_camera", NO_CAMERA_LABEL)
    assert "not live" not in NO_CAMERA_LABEL and "offline" not in NO_CAMERA_LABEL
    # Any other camera without a picture is still not live: only the choice is calm.
    assert source_with({"ok": False, "state": "missing"}).state()[0] == "bridge_not_live"


def test_a_tool_with_no_picture_says_no_camera_is_in_use():
    class Hub:
        source = source_with({"ok": False, "state": "no_camera"})

    why, next = no_picture(Hub(), "there is no live picture to take", "check the camera is connected")
    assert why == NO_CAMERA_WHY and "connected" not in next and "picture or video" in next
    Hub.source = source_with({"ok": False, "state": "missing"})
    assert no_picture(Hub(), "there is no live picture to take", "check the camera is connected") == (
        "there is no live picture to take", "check the camera is connected")


def test_every_check_the_camera_message_goes_through_no_picture():
    """A message telling the user to check the camera is said only where No camera can't be the reason."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "orchestrator"
    direct = [f"{path.relative_to(root)}" for path in root.rglob("*.py")
              if 'next="check the camera is' in path.read_text()]
    assert direct == []
