"""What only this Mac can say about the iPhone through Continuity Camera: AVFoundation's device list (FFmpeg's), Desk
View, a USB cable, and the reset of Continuity Camera's agent. Test footage and a stream stand in for these at the
main module's touch points (iphone_bridge_server.py make_device_snapshot, reset_continuity_capture_agent)."""
from __future__ import annotations

import subprocess
import time

from bridge_common import ffmpeg_path, wall_timestamp


def run_device_list() -> str:
    result = subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
        capture_output=True,
        text=True,
        check=False,
    )
    return f"{result.stdout}\n{result.stderr}"


def parse_avfoundation_devices(raw: str) -> dict[str, list[dict[str, str | int]]]:
    section = None
    devices: dict[str, list[dict[str, str | int]]] = {"video": [], "audio": []}

    for line in raw.splitlines():
        if "AVFoundation video devices:" in line:
            section = "video"
            continue
        if "AVFoundation audio devices:" in line:
            section = "audio"
            continue
        if section is None:
            continue

        marker = "] ["
        if marker not in line:
            continue

        try:
            _, rest = line.split(marker, 1)
            index_text, name = rest.split("] ", 1)
            devices[section].append({"index": int(index_text), "name": name.strip()})
        except ValueError:
            continue

    return devices


def desk_view_running() -> bool:
    """Desk View (Continuity Camera's top-down view) holds the iPhone's camera while it is open; clicking
    the iPhone in the menu bar's camera menu opens it."""
    return subprocess.run(["pgrep", "-x", "Desk View"], capture_output=True, check=False).returncode == 0


def iphone_on_usb() -> bool | None:
    """Whether an iPhone is plugged into this Mac by USB (None when that can't be read)."""
    try:
        result = subprocess.run(["ioreg", "-p", "IOUSB", "-w0"], capture_output=True, text=True, check=False, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return "iPhone" in result.stdout


def snapshot_from_listing(target_device: str, raw: str) -> dict[str, object]:
    """Whether `target_device` is in a device listing (FFmpeg's, or test footage's stand-in for it)."""
    devices = parse_avfoundation_devices(raw)
    names = [str(device["name"]) for device in devices["video"]]
    ok = target_device in names
    return {
        "ok": ok,
        "targetDevice": target_device,
        "videoDevices": devices["video"],
        "audioDevices": devices["audio"],
        "checkedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "raw": raw,
    }


def reset_capture_agent() -> dict[str, object]:
    """Reset Continuity Camera's agent (macOS starts it again)."""
    started_at = time.time()
    result = subprocess.run(
        ["killall", "ContinuityCaptureAgent"],
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "attempted": True,
        "ok": result.returncode in {0, 1},
        "returnCode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "at": wall_timestamp(started_at),
    }
