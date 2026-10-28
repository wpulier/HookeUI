#!/usr/bin/env python3
"""The camera bridge: the only thing that opens the iPhone (AVFoundation through FFmpeg or the capture app, or
Blackmagic Camera's stream), serving its newest frame to the app and the console. The entry point, the capture's
lifecycle (CameraBridge) and the live state every module beside it acts on (SIMULATED_CAMERA, STREAM_SOURCE) are
here. Beside it: the capture's reader and monitor (bridge_monitor.py), the recorder (bridge_recording.py), what the
bridge says about the camera (bridge_status.py), the HTTP routes (bridge_routes.py), the live sources
(bridge_sources.py), the Mac's Continuity facts (continuity.py), what the feed may play (bridge_feed.py), the
Blackmagic settings server (settings_server.py) and what they share (bridge_common.py)."""
from __future__ import annotations

import argparse
import contextlib
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path

# The modules beside this one (the script's own folder is on sys.path). Their names are imported here so that what
# this module always offered stays importable from it.
from bridge_common import (  # noqa: F401
    ASSETS_FOOTAGE, ASSETS_MANIFEST, CAMERA_PAGE_DIR, CORPUS_MANIFEST, PRIVATE_MANIFEST, REPO_DIR, ROOT_DIR, STDERR_LOG_LINES,
    STDERR_TAIL_LINES, ModuleView,
    drain_stderr, ffmpeg_path, jpeg_size, wall_timestamp, with_stderr,
)
from bridge_feed import DATASET_ID, SwitchSteps, asset_file, dataset_recording, media_roots, recording_started  # noqa: F401
from bridge_monitor import MonitorMixin
from bridge_recording import (  # noqa: F401
    DEFAULT_RECORDING_OWNER, RECORDING_BEHIND_S, SHUTDOWN_RECORDING_WAIT_S, SIDECAR_FLUSH_S, SIDECAR_SCHEMA,
    SIDECAR_SUFFIX, RecordingMixin, count_video_frames, recording_owner, sidecar_path,
)
from bridge_routes import LOCAL_ORIGIN, BridgeHandler, BridgeServer  # noqa: F401
from bridge_sources import (  # noqa: F401
    SOURCE_ALIASES, SOURCE_DEVICE_NAMES, SOURCE_KINDS, SOURCE_TITLES, STREAM_KINDS, read_source_choice,
    save_source_choice, source_choices, source_kind,
)
from bridge_status import (  # noqa: F401
    CADENCE_FRAMES, CADENCE_WINDOW_S, EVEN_FPS, EVEN_P95_MS, GAP_MS, StatusMixin, cadence_summary, proven_versions,
    unsafe_quality_list,
)
from continuity import (  # noqa: F401
    desk_view_running, iphone_on_usb, parse_avfoundation_devices, reset_capture_agent, run_device_list,
    snapshot_from_listing,
)
from settings_server import SETTINGS_PORT, serve_settings, settings_url, settings_xml  # noqa: F401

# Test footage: a SimulatedCamera that stands in for the phone at the three points where
# the bridge touches the device (listing, capture command, Continuity reset). Set by
# --simulate at startup, or switched at runtime through /feed (the studio's camera menu: test
# footage, a session's own recording from the app's datasets, --datasets-dir, or any recording or
# picture of the user's under a media root, --media-root). Everything else in this file runs
# identically, and nothing downstream can tell.
SIMULATED_CAMERA = None
# The phone's picture from a network stream instead of an AVFoundation device (--source-url, a
# stream_source.StreamSource): an app on the phone streams SRT to the bridge, which listens. It
# replaces the same three touch points, and test footage still replaces it while it plays.
STREAM_SOURCE = None
# This module's names as they are now, for the modules beside it (bridge_common.ModuleView): they act on this live
# state, and see a name a test replaced here.
MAIN = ModuleView(globals())
_ANY = object()  # CameraBridge._launch: no expectation about the current process

# A frozen iPhone picture (byte-identical frames: Continuity repeated one picture at 30 fps on demo night, 2026-10-12,
# while health said live) is healed by itself after this long, at most once a minute. Only the iPhone: its sensor noise
# makes every real frame differ, while a still scene through Blackmagic's H.264 can repeat exactly (the checklist
# names it there, and Reset camera is one click).
FROZEN_HEAL_S = 3.0
FROZEN_HEAL_GAP_S = 60.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="The camera bridge: the chosen live source (the iPhone, or Blackmagic Camera's stream) as one feed for the app."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--target-device", default="iPhoneWild Camera")
    parser.add_argument(
        "--source-url",
        metavar="URL",
        help="Read the picture from a network stream instead of the AVFoundation device, e.g. "
        "'srt://0.0.0.0:9710?mode=listener&latency=120000' (the phone's app calls, the bridge listens; "
        "SRT latency is in microseconds). The environment variable MICROMIND_CAMERA_SOURCE does the same "
        "and keeps a passphrase off the command line",
    )
    parser.add_argument(
        "--source-file",
        default=None,
        help="Where the console's choice of live source (Continuity Camera or a stream) is kept across "
        "restarts (a small private JSON file); without it the choice lasts until the bridge stops",
    )
    parser.add_argument(
        "--default-source",
        default="continuity",
        choices=["continuity", "blackmagic", "none"],
        help="The live source when none was chosen yet (make start: none, No camera, so a first run starts with pictures "
             "and videos and searches for no camera, 2026-10-15; a saved choice or a stream URL wins)",
    )
    parser.add_argument("--size", default="1280x720")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--pixel-format", default="nv12")
    parser.add_argument("--jpeg-quality", type=int, default=4)
    parser.add_argument("--stall-timeout", type=float, default=5.0,
                        help="Restart a capture whose frames stop for this long")
    parser.add_argument("--first-frame-timeout", type=float, default=20.0,
                        help="Time a new capture gets for its first frame before the bridge heals Continuity Camera "
                        "(first frames take 1-4 s, once 14.3 s; at 15 s a slow phone lost the race)")
    parser.add_argument("--no-phone", action="store_true",
                        help="Never look for or control Blackmagic Camera (test and simulation bridges; implied by --simulate)")
    parser.add_argument("--page-dir", default=str(CAMERA_PAGE_DIR),
                        help="The built camera page this bridge serves at /camera/ (apps/studio's npm run build makes it)")
    parser.add_argument("--trials-dir", default=None,
                        help="Where the console's source checks (back-to-back trials) are kept; without it, in memory only")
    parser.add_argument("--silent-after", type=float, default=6.0,
                        help="A capture without its first frame this long is said to be waiting on a silent phone")
    parser.add_argument("--rescan-interval", type=float, default=5.0,
                        help="While the phone is missing or the capture is failing, rescan devices this often")
    parser.add_argument("--auto-reset-after", type=float, default=10.0,
                        help="Device missing this long before the bridge resets Continuity Camera by itself")
    parser.add_argument("--auto-reset-min-interval", type=float, default=30.0,
                        help="Minimum seconds between automatic Continuity resets (backs off to 5 min)")
    parser.add_argument("--stream-idle-timeout", type=float, default=10.0,
                        help="End an /iphone.mjpeg response after this long without a frame; the client reconnects")
    parser.add_argument("--device-wait-timeout", type=float, default=14.0)
    parser.add_argument("--continuity-reset-cooldown", type=float, default=8.0)
    parser.add_argument("--recording-dir", default=None)
    parser.add_argument(
        "--simulate",
        metavar="VIDEO",
        help="Simulate the iPhone with this recording; downstream services see a normal live bridge",
    )
    parser.add_argument("--simulate-start-frame", type=int, default=0)
    parser.add_argument(
        "--footage-dir",
        default=str(ASSETS_FOOTAGE),
        help="Recordings the studio can play into the live feed instead of the camera (default: the "
        "assets store's footage/, assets/README.md)",
    )
    parser.add_argument(
        "--datasets-dir",
        default=None,
        help="The app's datasets (what Record collects; `python -m orchestrator.paths datasets`): the studio can play a "
        "session's own recording into the live feed (POST /feed?source=recording). Without it, none can be played",
    )
    parser.add_argument(
        "--media-root",
        action="append",
        default=[],
        metavar="FOLDER",
        help="A folder whose recordings and pictures may be played into the live feed (POST /feed?source=asset), such as "
        "the app's snapshots and results; repeatable. The datasets, footage and configured recordings folders count as media roots too",
    )
    # Options beside the earlier way (L1c, docs/research/2026-10-20-live-view-regression-bisect.md), each measured against
    # it on a quiet Mac by a bridge-only bench (3 interleaved runs, 2026-10-20 07:15, load 4-7): the defaults are the ways
    # that won. An environment variable sets each for a whole launch.
    parser.add_argument(
        "--media-pace", choices=("one", "follow"), default=os.environ.get("MICROMIND_MEDIA_PACE") or "follow",
        help="Thumbnails and lengths: as many as the machine allows (follow, the default: up to "
        "footage_media.MEDIA_JOBS_MAX while the bridge keeps its frame rate, one at a time when it does not; 60 thumbnails' "
        "p95 wait 0.01 s against 2.1 s, the feed 30.1 fps either way), or one at a time (one, the earlier way)",
    )
    parser.add_argument(
        "--upload-priority", choices=("normal", "background"), default=os.environ.get("MICROMIND_UPLOAD_PRIORITY") or "normal",
        help="An added recording's copy: on its request thread (normal, the default), or on a thread of its own at "
        "background priority (background). The upload never slowed the feed (30.05 against 30.04 fps) and took 331 ms "
        "in the background against 263, so normal stays",
    )
    parser.add_argument(
        "--probe-cache", choices=("off", "on"), default=os.environ.get("MICROMIND_PROBE_CACHE") or "on",
        help="A played file's rate and frame count: read once for each file as it is (on, the default: kept by its "
        "path, inode, size and modification time; a switch's probe 0.1 ms against 76), or by FFprobe on every switch "
        "(off)",
    )
    parser.add_argument(
        "--file-stop", choices=("grace", "kill"), default=os.environ.get("MICROMIND_FILE_STOP") or "kill",
        help="A recording played as the camera, stopped for a switch or a seek: killed at once (kill, the default: a file "
        "being read needs no grace; the stop 59 ms against 264, a switch 248 ms against 467), or asked to stop, then "
        "killed after 0.2 s (grace, the earlier way)",
    )
    return parser.parse_args()


def live_source_kind(args: argparse.Namespace) -> str:
    kind = getattr(args, "live_kind", None)
    if kind:
        return kind
    return getattr(args, "link", None) or "continuity" if STREAM_SOURCE is not None else "continuity"


def use_live_source(args: argparse.Namespace, kind: str, url: str | None = None, device: str | None = None) -> None:
    """Which live source the bridge captures: the iPhone through Continuity Camera (the AVFoundation device
    `args.device_name`), the stream Blackmagic Camera sends (`args.link` "blackmagic"), or another camera: one this
    Mac sees, by name (`args.other_device`, captured as the iPhone is), or a stream address (`args.other_url`). A stream
    replaces the device under its own name, which never carries the URL's secrets. Test footage replaces any."""
    global STREAM_SOURCE
    from stream_source import default_url, StreamSource

    kind = source_kind(kind)
    device_name = getattr(args, "device_name", None) or args.target_device
    args.device_name = device_name
    args.live_kind = kind
    if kind == "other":
        if device:
            args.other_device, args.other_url = device, None
        elif url:
            StreamSource(url)  # raises on a bad URL
            args.other_device, args.other_url = None, url
        if getattr(args, "other_url", None):
            STREAM_SOURCE = StreamSource(args.other_url)
            args.link = None
            args.target_device = "Other camera (stream)"
        elif getattr(args, "other_device", None):
            STREAM_SOURCE = None
            args.link = None
            args.target_device = args.other_device
        else:
            args.live_kind = None
            raise ValueError("Choose a camera or a stream address for Other camera (on the camera page)")
        return
    if kind == "none":
        # No camera: nothing to capture. The device name is kept, so choosing the iPhone again goes back to it.
        STREAM_SOURCE = None
        args.link = None
        args.target_device = device_name
        return
    if kind in STREAM_KINDS:
        wanted = url or getattr(args, "stream_url", None) or default_url()
        if STREAM_SOURCE is None or STREAM_SOURCE.url != wanted:
            STREAM_SOURCE = StreamSource(wanted)  # raises on a bad URL
        args.stream_url = STREAM_SOURCE.url
        args.link = kind
        args.target_device = SOURCE_DEVICE_NAMES[kind]
    else:
        STREAM_SOURCE = None
        args.link = None
        args.target_device = device_name


def initial_live_source(args: argparse.Namespace) -> tuple[str, str | None]:
    """The source to start with: a stream URL given at launch (MICROMIND_CAMERA_SOURCE, --source-url), else
    the console's last choice (--source-file), else --default-source (studio.sh: No camera on a first run, so a new
    user starts with pictures and videos, not a camera search), else Continuity Camera."""
    url = os.environ.get("MICROMIND_CAMERA_SOURCE", "").strip() or args.source_url
    if url:
        return "blackmagic", url
    saved = read_source_choice(args)
    # Early Beta builds persisted Stable's uncustomized default. Move only that
    # default to this channel; explicit/custom stream settings remain untouched.
    from stream_source import DEFAULT_URL, default_url
    if saved.get("url") == DEFAULT_URL:
        saved["url"] = default_url()
    kind = SOURCE_ALIASES.get(str(saved.get("kind")), saved.get("kind"))
    args.other_device = saved.get("otherDevice") or getattr(args, "other_device", None)
    args.other_url = saved.get("otherUrl") or getattr(args, "other_url", None)
    if kind == "other" and not (args.other_device or args.other_url):
        kind = None  # an Other camera with nothing chosen: start as if never chosen
    if kind in SOURCE_KINDS:
        if saved.get("url"):
            args.stream_url = saved["url"]  # Blackmagic's listener, kept whichever source is on
        return kind, (None if kind in ("other", "none") else saved.get("url")) or None
    return getattr(args, "default_source", None) or "continuity", None


def stream_addresses(args: argparse.Namespace) -> dict[str, str | None]:
    """Where the phone can reach this Mac's listener: over Wi-Fi (the LAN address) and over USB (the Personal
    Hotspot link's address, when the cable link is up)."""
    from phone_control import usb_address
    from stream_source import default_url, StreamSource, lan_address

    try:
        port = (STREAM_SOURCE or StreamSource(getattr(args, "stream_url", None) or default_url())).port
    except ValueError:
        port = None
    wifi, usb = lan_address(), usb_address()
    return {"wifi": f"srt://{wifi}:{port}" if wifi and port else None,
            "usb": f"srt://{usb}:{port}" if usb and port else None}


# What only the Mac can tell about a silent phone, checked off the request path (the monitor, at most
# every FACTS_TTL_S while there is no picture): health() only reads the cached answer.
FACTS_TTL_S = 5.0
# The iPhone through Continuity Camera is captured by the bridge's own small app (capture-app/: "MicroscopeMind
# Camera", built by capture-app/build.sh, started through LaunchServices by capture-app/run.sh). macOS wakes the
# iPhone only for an app with its own identity: a bare FFmpeg capture is an anonymous client that gets frames only
# while something else keeps the phone awake (2026-10-11: FFmpeg 0 frames in 40 s; this app 30.1 fps, first frame
# in 1.5 s, side by side). FFmpeg stays the fallback when the app isn't built (MICROMIND_CAPTURE=ffmpeg forces it).
CAPTURE_APP_DIR = ROOT_DIR / "capture-app"
CAPTURE_APP_BINARY = CAPTURE_APP_DIR / "build" / "MicroscopeMind Camera.app" / "Contents" / "MacOS" / "capture"
# The hardware JPEG encoder's quality: 0.45 gave 54 KB frames at 1280x720 (FFmpeg's -q:v 4 about 45 KB); 0.6 gave 107 KB.
CAPTURE_APP_QUALITY = 0.45


def native_capture_ready() -> bool:
    return CAPTURE_APP_BINARY.exists() and os.environ.get("MICROMIND_CAPTURE", "") != "ffmpeg"


# After `killall ContinuityCaptureAgent` macOS starts the agent again; the phone is listed again within a few
# seconds (6 s on 2026-10-11). Asking before the agent is back only makes FFmpeg fail once more.
CONTINUITY_SETTLE_S = 1.5


def iphone_nearby(snapshot: dict[str, object] | None, target_device: str) -> bool | None:
    """Whether macOS finds the iPhone at all, from a device listing: Continuity lists the phone's microphone
    ("iPhoneWild Microphone") whenever it finds the phone, also while it doesn't offer its camera (live 2026-10-13:
    locked, Blackmagic closed, the microphone listed and no camera). None when it can't be told."""
    if not snapshot or "audioDevices" not in snapshot or not target_device.endswith(" Camera"):
        return None
    microphone = target_device[: -len(" Camera")] + " Microphone"
    return any(str(device.get("name")) == microphone for device in snapshot.get("audioDevices") or [])


def make_device_snapshot(target_device: str) -> dict[str, object]:
    if STREAM_SOURCE and not SIMULATED_CAMERA:
        return STREAM_SOURCE.device_snapshot(target_device)
    return snapshot_from_listing(target_device, SIMULATED_CAMERA.device_listing() if SIMULATED_CAMERA else run_device_list())


def camera_list(args: argparse.Namespace) -> list[dict[str, object]]:
    """The cameras this Mac sees (AVFoundation's video devices), for Other camera's picker: a USB microscope camera,
    a webcam, a capture card. The screens FFmpeg lists are not cameras. Asked only when the picker is open."""
    raw = SIMULATED_CAMERA.device_listing() if SIMULATED_CAMERA else run_device_list()
    iphone = getattr(args, "device_name", None) or args.target_device
    names = [str(d["name"]) for d in parse_avfoundation_devices(raw)["video"] if not str(d["name"]).startswith("Capture screen")]
    return [{"name": name, "iphone": name == iphone or "Desk View" in name, "chosen": name == getattr(args, "other_device", None)}
            for name in dict.fromkeys(names)]


def reset_continuity_capture_agent() -> dict[str, object]:
    if SIMULATED_CAMERA:
        return SIMULATED_CAMERA.reset_agent()
    if STREAM_SOURCE:
        return STREAM_SOURCE.reset_agent()
    return reset_capture_agent()


class CameraBridge(MonitorMixin, StatusMixin, RecordingMixin):
    """The capture of the chosen source: started, stopped and asked again here; its reader and monitor
    (bridge_monitor.MonitorMixin), what it says about the camera (bridge_status.StatusMixin) and its recorder
    (bridge_recording.RecordingMixin) are mixins of their own."""

    main = MAIN  # this module's live state, for the mixins

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.condition = threading.Condition()
        # Serializes every capture stop and start (and the wait for an old FFmpeg to exit),
        # so two captures of the phone can never run at once. health() never takes it.
        self.lifecycle = threading.RLock()
        # A reconnect or feed switch in progress: the monitor stands back while it runs, and
        # an epoch change tells the monitor its earlier look at the state is out of date.
        self.manual_ops = 0
        self.manual_epoch = 0
        self.recording_lock = threading.Lock()
        self.process: subprocess.Popen[bytes] | None = None
        self.reader_thread: threading.Thread | None = None
        self.monitor_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.latest_frame: bytes | None = None
        self.frame_count = 0
        self.total_bytes = 0
        self.restart_count = 0
        self.request_count = 0
        self.scan_count = 0  # device scans applied (the sim's no-camera scenario: none while no camera is chosen)
        self.continuity_resets = 0
        self.started_at = time.monotonic()
        self.last_start_at = 0.0
        self.last_start_wall_at = 0.0
        self.last_request_at = 0.0
        self.last_request_wall_at = 0.0
        self.last_request_reason = ""
        self.last_frame_at = 0.0
        self.last_frame_wall_at = 0.0
        self.last_error = ""
        self.capture_frames = 0  # frames from the current capture
        self.capture_base_count = 0  # frame_count when the current capture started
        self.capture_stderr: deque = deque(maxlen=STDERR_TAIL_LINES)
        self.last_capture_exit: dict[str, object] | None = None
        self.last_scan_at = 0.0
        self.missing_since = 0.0
        self.auto_reset_count = 0  # automatic Continuity resets in this missing episode
        self.nearby_said: bool | None = None  # what the camera's events last said about the iPhone being found
        self.last_agent_reset_at = 0.0
        self.last_agent_reset: dict[str, object] | None = None
        # A phone that is listed but sends nothing (2026-10-11: 19 requests in 4 minutes, not one frame,
        # until Continuity Camera was reset by hand). Each heal resets Continuity and asks again; they
        # back off like the missing phone's resets and start over at the first frame.
        self.silent_heals = 0
        self.last_heal: dict[str, object] | None = None
        # Reconnects asked for while one runs wait for it and share its answer (the console and the studio
        # once launched two captures in the same second, and each replaced the other's).
        self.reconnect_lock = threading.Lock()
        self.last_reconnect: dict[str, object] | None = None
        # Facts about the Mac for the diagnosis, read by the monitor (desk_view_running, iphone_on_usb).
        self.facts: dict[str, object] = {"deskView": False, "usb": None, "checkedAt": 0.0}
        self.phone_link = None  # phone_control.PhoneLink, started by main (ensure_phone_link)
        # The camera's own story for the checklist and its Copy report: source switches, captures, heals, resets,
        # freezes, the phone link's actions (wall time, plain words).
        self.events: deque = deque(maxlen=50)
        # Facts about the picture itself, read as each frame arrives: its size (from the JPEG header, no decode)
        # and since when it has been byte-identical (a frozen picture: Continuity repeated one on 2026-10-12 while
        # health said live).
        self.frame_size: tuple[int, int] | None = None
        self.frame_signature: tuple[int, int] | None = None
        self.same_since: float | None = None
        self.readers = 0  # clients of /iphone.mjpeg right now (the app, the console)
        self.last_freeze_heal = float("-inf")
        self.lab = None  # the Phone check (phone_lab.PhoneCheck), while it runs and after
        # When each frame arrived (monotonic), for the steadiness the console draws and the trials measure: the
        # app's standard is the live feed's own (30 fps, evenly), whatever the source.
        self.frame_times: deque = deque(maxlen=CADENCE_FRAMES)
        self.trials: list[dict[str, object]] = []
        self.trial_running: dict[str, object] | None = None
        self.last_device_snapshot: dict[str, object] | None = None
        # The camera recording: one recorder at a time takes frames; every caller that asked
        # for it is an owner, and it ends when the last owner stops it. A recorder that has
        # ended goes to `recording_finishing` until its file is complete (never killed).
        self.recording_process: subprocess.Popen[bytes] | None = None
        self.recording_owners: set[str] = set()
        self.recording_path: Path | None = None
        self.recording_started_at = 0.0
        self.recording_frames = 0
        self.recording_bytes = 0
        self.recording_error = ""
        self.recording_stderr: deque = deque(maxlen=STDERR_TAIL_LINES)
        # What the recorder still has to write (frames and sidecar events, in order), and how many frames it dropped.
        self.recording_queue: deque = deque()
        self.recording_ready = threading.Condition()
        self.recording_dropped = 0
        self.recording_behind = False
        threading.Thread(target=self._recording_writer_loop, daemon=True, name="recording-writer").start()
        self.recording_finishing: list[dict[str, object]] = []
        self.recording_last_saved: dict[str, object] | None = None
        self.recording_finishers: list[threading.Thread] = []
        # This run of the bridge: frame numbers (`frame_count`) start again at each run, so a
        # reader keys them by bootId and seq.
        self.boot_id = uuid.uuid4().hex[:12]
        self.recording_sidecar = None  # the open sidecar of the running recording
        self.recording_sidecar_path: Path | None = None
        self.recording_sidecar_error = ""
        self.recording_sidecar_flushed_at = 0.0

    def ffmpeg_command(self) -> list[str]:
        if SIMULATED_CAMERA:
            return SIMULATED_CAMERA.capture_command(ffmpeg_path(), self.args)
        if STREAM_SOURCE:
            return STREAM_SOURCE.capture_command(ffmpeg_path(), self.args)
        if native_capture_ready():
            return [str(CAPTURE_APP_DIR / "run.sh"), self.args.target_device, self.args.size, str(self.args.fps),
                    str(CAPTURE_APP_QUALITY)]
        return [
            ffmpeg_path(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "avfoundation",
            "-pixel_format",
            self.args.pixel_format,
            "-framerate",
            str(self.args.fps),
            "-video_size",
            self.args.size,
            "-i",
            f"{self.args.target_device}:none",
            "-an",
            "-vf",
            f"fps={self.args.fps}",
            "-q:v",
            str(self.args.jpeg_quality),
            "-vcodec",
            "mjpeg",
            "-f",
            "image2pipe",
            "pipe:1",
        ]

    def start(self) -> None:
        self._launch("initial server start", replace=False)

    def ensure_monitor(self) -> None:
        """The monitor runs from startup, also while the phone is missing, so a phone that
        reappears is found without pressing Reconnect."""
        with self.condition:
            self._ensure_monitor_thread_locked()

    def no_camera(self) -> bool:
        """No camera chosen and nothing playing: nothing is captured, scanned or reset (Will, 2026-10-15)."""
        return SIMULATED_CAMERA is None and live_source_kind(self.args) == "none"

    def ensure_capture(self, reason: str) -> None:
        """Start a capture if none is running and the device is there. Never restarts a
        running capture: a new viewer must not kill a capture that is still connecting."""
        with self.condition:
            if self.manual_ops or self.stop_event.is_set() or self.no_camera():
                return
            if self.process and self.process.poll() is None:
                return
            if not self._device_present_locked():
                return
        self._launch(reason, replace=False)

    @contextlib.contextmanager
    def manual_operation(self):
        with self.condition:
            self.manual_ops += 1
            self.manual_epoch += 1
        try:
            yield
        finally:
            with self.condition:
                self.manual_ops -= 1

    def _missing_message(self) -> str:
        return f"{self.args.target_device} is not visible to AVFoundation"

    def _device_present_locked(self) -> bool:
        if self.last_device_snapshot is None:
            return True
        return bool(self.last_device_snapshot.get("ok"))

    def update_device_snapshot(self, snapshot: dict[str, object]) -> None:
        with self.condition:
            self._apply_snapshot_locked(snapshot)

    def probe_device(self) -> dict[str, object]:
        """A device scan for the console's and the studio's probes, which ask every few seconds while there
        is no picture. A scan is another FFmpeg opening AVFoundation: while a capture of the phone runs,
        and above all while it waits for the phone to answer, it competed for the phone (2026-10-11:
        "Invalid deviceControlPID"). So while a capture runs the answer is the last scan's; the monitor
        and Reconnect still scan when something has failed. With no camera chosen nothing is scanned."""
        if self.no_camera():
            return {"ok": False, "noCamera": True, "targetDevice": self.args.target_device, "videoDevices": []}
        with self.condition:
            running = bool(self.process and self.process.poll() is None)
            snapshot = self.last_device_snapshot
            if running and snapshot is not None:
                return {**snapshot, "cached": True}
        snapshot = make_device_snapshot(self.args.target_device)
        self.update_device_snapshot(snapshot)
        return snapshot

    def refresh_facts(self, force: bool = False) -> dict[str, object]:
        """What the Mac can tell about why the phone is silent (Desk View open, plugged in by USB or not),
        at most every FACTS_TTL_S, never on the request path."""
        now = time.monotonic()
        with self.condition:
            if not force and now - float(self.facts.get("checkedAt") or 0.0) < FACTS_TTL_S:
                return dict(self.facts)
        facts = {"deskView": desk_view_running(), "usb": iphone_on_usb(), "checkedAt": now}
        with self.condition:
            self.facts = facts
        return dict(facts)

    def _apply_snapshot_locked(self, snapshot: dict[str, object]) -> None:
        self.scan_count += 1
        self.last_device_snapshot = snapshot
        if live_source_kind(self.args) == "continuity" and not snapshot.get("ok"):
            # Where it stops, said once each time it changes: the camera's events tell anyone helping (and us).
            nearby = iphone_nearby(snapshot, self.args.target_device)
            if nearby is not None and nearby != self.nearby_said:
                self.nearby_said = nearby
                self.event("macOS finds the iPhone, but doesn't offer its camera" if nearby
                           else "macOS doesn't find the iPhone (nothing of it is listed)")
        elif snapshot.get("ok"):
            self.nearby_said = None
        if not snapshot.get("ok"):
            self.last_error = self._missing_message()
            if not self.missing_since:
                self.missing_since = time.monotonic()
                print(f"{self._missing_message()}; rescanning every {self.args.rescan_interval:g} s", flush=True)
        else:
            if self.last_error == self._missing_message():
                self.last_error = ""
            if self.missing_since:
                print(f"{self.args.target_device} is visible again", flush=True)
            self.missing_since = 0.0
            self.auto_reset_count = 0
        self.condition.notify_all()

    def reconnect(self, reason: str = "manual iPhone reconnect requested", wait_timeout: float = 8.0) -> dict[str, object]:
        """The user's Reconnect (console, studio, `make camera-reconnect`). One at a time: a reconnect asked
        for while another runs waits for it and returns its answer, instead of replacing the capture it just
        started. A phone that is listed but silent gets a Continuity reset first, as a missing one does."""
        if os.environ.get("MICROMIND_WORKSPACE_STUDY") == "1":
            return {"ok": False, "error": "Camera recovery is unavailable in this isolated preview. The production camera is unchanged."}
        if not self.reconnect_lock.acquire(blocking=False):
            with self.reconnect_lock:
                pass  # the reconnect in flight has finished
            joined = dict(self.last_reconnect or {})
            joined["joined"] = True
            joined["bridge"] = self.health()
            joined["ok"] = bool(joined["bridge"]["ok"])
            return joined
        try:
            result = self.restart(reason, wait_timeout=wait_timeout, reset_silent_device=True)
            self.last_reconnect = result
            return result
        finally:
            self.reconnect_lock.release()

    def restart(
        self,
        reason: str,
        wait_timeout: float = 8.0,
        reset_missing_device: bool = True,
        reset_silent_device: bool = False,
        steps: SwitchSteps | None = None,
    ) -> dict[str, object]:
        with self.manual_operation():
            return self._restart(reason, wait_timeout, reset_missing_device, reset_silent_device, steps)

    def _phone_silent_locked(self, now: float) -> bool:
        """The phone was asked and has sent nothing for `silent_after`, or the last capture ended without a
        frame. Only the phone through Continuity: a stream's listener waiting is not the phone failing."""
        if SIMULATED_CAMERA is None and STREAM_SOURCE is not None:
            return False
        if self.capture_frames:
            return False
        if not self.last_start_at:
            return False
        running = bool(self.process and self.process.poll() is None)
        return not running or now - self.last_start_at >= self.args.silent_after

    def _restart(
        self,
        reason: str,
        wait_timeout: float,
        reset_missing_device: bool,
        reset_silent_device: bool = False,
        steps: SwitchSteps | None = None,
    ) -> dict[str, object]:
        steps = steps or SwitchSteps()  # a switch's steps (snapshot, launch, firstFrame), timed here
        if self.no_camera():
            self._stop_capture("no camera chosen")
            return self._restart_result("no_camera", "No camera is in use: this session uses pictures and videos.",
                                        {"ok": False, "targetDevice": self.args.target_device, "videoDevices": []})
        snapshot = make_device_snapshot(self.args.target_device)
        self.update_device_snapshot(snapshot)
        steps.mark("snapshot")
        continuity_reset: dict[str, object] | None = None

        if snapshot["ok"] and reset_silent_device and not native_capture_ready():
            with self.condition:
                silent = self._phone_silent_locked(time.monotonic())
            if silent:
                # Listed but not sending (2026-10-11): asking again changes nothing; a Continuity reset did.
                self._stop_capture("phone listed but silent")
                continuity_reset = self.reset_continuity_agent_if_allowed()
                if continuity_reset.get("attempted"):
                    self.event("Reconnect: the iPhone was listed but sent nothing; Continuity Camera reset")
                    time.sleep(CONTINUITY_SETTLE_S)
                    snapshot = self.wait_for_target_device(self.args.device_wait_timeout)

        if not snapshot["ok"]:
            self._stop_capture("device missing")
            with self.condition:
                self.last_error = self._missing_message()
                self.condition.notify_all()

            if reset_missing_device:
                continuity_reset = self.reset_continuity_agent_if_allowed()
                snapshot = self.wait_for_target_device(self.args.device_wait_timeout)

        if not snapshot["ok"]:
            return self._restart_result(
                "device_missing",
                f"{self.args.target_device} is still not visible to FFmpeg/AVFoundation, so no phone request could be sent.",
                snapshot,
                continuity_reset=continuity_reset,
            )

        started = self._launch(reason, replace=True)
        steps.mark("launch")
        with self.condition:
            # Frames counted after this point come from the new capture only (frames of the
            # old one are discarded once it is swapped out).
            baseline_count = self.capture_base_count

        if not started:
            return self._restart_result(
                "start_failed",
                "FFmpeg could not start the iPhone capture request.",
                snapshot,
                continuity_reset=continuity_reset,
            )

        _, frame = self.wait_for_frame(baseline_count, timeout=wait_timeout, return_if_stopped=True)
        steps.mark("firstFrame")
        health = self.health()
        if frame and health["ok"]:
            return self._restart_result(
                "live",
                "Fresh iPhone frames received. The bridge is live.",
                snapshot,
                continuity_reset=continuity_reset,
            )

        if health["running"]:
            return self._restart_result(
                "request_sent_waiting",
                f"{STREAM_SOURCE.waiting_message()}. Start streaming from the phone's app to this Mac."
                if self._streaming()
                else "The iPhone capture request was started, but no fresh frames arrived before the timeout. Lock and mount the iPhone, accept any prompt, then request again.",
                snapshot,
                continuity_reset=continuity_reset,
            )

        return self._restart_result(
            "capture_exited",
            "The iPhone capture request started but FFmpeg exited before frames arrived.",
            snapshot,
            continuity_reset=continuity_reset,
        )

    def reset_continuity_agent_if_allowed(self) -> dict[str, object]:
        if live_source_kind(self.args) in ("other", "none"):  # Continuity Camera is the iPhone's: never reset for
            return {"attempted": False, "ok": False, "reason": "not Continuity Camera"}  # another camera, or none
        now = time.monotonic()
        elapsed = now - self.last_agent_reset_at if self.last_agent_reset_at else None
        if elapsed is not None and elapsed < self.args.continuity_reset_cooldown:
            return {
                "attempted": False,
                "ok": False,
                "reason": "cooldown",
                "cooldownRemainingSeconds": round(self.args.continuity_reset_cooldown - elapsed, 2),
                "lastReset": self.last_agent_reset,
            }

        reset = reset_continuity_capture_agent()
        with self.condition:
            self.continuity_resets += 1
            self.last_agent_reset_at = now
            self.last_agent_reset = reset
            self.last_error = "Continuity Camera agent reset; waiting for iPhone camera to reappear"
            self.condition.notify_all()
        return reset

    def wait_for_target_device(self, timeout: float) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        snapshot = make_device_snapshot(self.args.target_device)
        self.update_device_snapshot(snapshot)
        while not snapshot["ok"] and time.monotonic() < deadline:
            time.sleep(1)
            snapshot = make_device_snapshot(self.args.target_device)
            self.update_device_snapshot(snapshot)
        return snapshot

    def _restart_result(
        self,
        action: str,
        message: str,
        snapshot: dict[str, object],
        continuity_reset: dict[str, object] | None = None,
    ) -> dict[str, object]:
        health = self.health()
        return {
            "ok": bool(health["ok"]),
            "action": action,
            "message": message,
            "device": {
                "present": bool(snapshot.get("ok")),
                "targetDevice": snapshot.get("targetDevice"),
                "videoDevices": snapshot.get("videoDevices", []),
                "checkedAt": snapshot.get("checkedAt"),
            },
            "request": {
                "sent": health["running"] or action in {"live", "request_sent_waiting", "capture_exited"},
                "acknowledgedByFreshFrames": bool(health["ok"]),
                "lastRequestAt": health["lastRequestAt"],
                "lastRequestReason": health["lastRequestReason"],
            },
            "continuityReset": continuity_reset,
            "bridge": health,
        }

    def stop(self) -> None:
        self.stop_event.set()
        if self.phone_link:
            self.phone_link.stop()
        self._finish_recordings_for_shutdown()
        self._stop_capture("bridge stopping")
        with self.condition:
            self.condition.notify_all()

    def photo_folder(self) -> Path:
        return Path(self.args.recording_dir or ROOT_DIR / "output" / ("sim-recordings" if SIMULATED_CAMERA else "recordings"))

    def reveal_photo(self, name: str) -> dict[str, object]:
        """Show a photo the Photo button saved in the Finder ("Where did it go?", 2026-10-13). Only a file of the photo
        folder, by its name: nothing else on the Mac can be opened from here."""
        folder = self.photo_folder().resolve()
        target = (folder / name).resolve()
        if target.parent != folder or not target.is_file() or target.suffix.lower() != ".jpg":
            return {"ok": False, "error": "That photo isn't in the photo folder"}
        subprocess.run(["open", "-R", str(target)], check=False, timeout=5)
        return {"ok": True, "path": str(target)}

    def take_photo(self) -> dict[str, object]:
        """The Photo button: the newest camera frame, exactly as it arrived (no re-encoding), saved beside the
        recordings as `<device>-<time>.jpg`. Blackmagic Camera's API has no still-photo route (the Phone check,
        2026-10-12), so the photo is the Mac's, at the stream's size; a recording on the phone is full quality."""
        frame = self.latest_snapshot()
        if frame is None:
            return {"ok": False, "error": "There's no live picture to take a photo of."}
        folder = self.photo_folder()
        folder.mkdir(parents=True, exist_ok=True)
        device = re.sub("-+", "-", "".join(c if c.isalnum() or c in "-_" else "-" for c in self.args.target_device)).strip("-")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path, n = folder / f"{device}-{stamp}.jpg", 2
        while path.exists():
            path, n = folder / f"{device}-{stamp}-{n}.jpg", n + 1
        path.write_bytes(frame)
        size = jpeg_size(frame)
        self.event(f"Photo saved: {path.name}")
        return {"ok": True, "path": str(path), "name": path.name, "bytes": len(frame),
                "width": size[0] if size else None, "height": size[1] if size else None}

    def reset_camera(self) -> dict[str, object]:
        """The user's Reset camera: what fixed demo night, on whichever source is chosen. The iPhone: Continuity
        Camera's agent is reset (the user asked, so no cooldown), then the capture asked again. Blackmagic: the
        phone's stream is stopped and started once, and the listener started afresh."""
        if os.environ.get("MICROMIND_WORKSPACE_STUDY") == "1":
            return {"ok": False, "error": "Camera recovery is unavailable in this isolated preview. The production camera is unchanged."}
        kind = live_source_kind(self.args)
        if kind in STREAM_KINDS:
            self.event("Reset camera: a fresh listener, then the phone's stream again")
            # The listener first, so the phone's restarted stream calls the fresh one (the other way round, at 09:43
            # on 2026-10-12, the phone called a listener that was then replaced, and never came back).
            self._launch("reset camera", replace=True)
            restarted = self.phone_link and self.phone_link.restart_stream()
            if not restarted:
                return {"ok": False, "error": "The Mac receiver restarted, but the phone stream did not. "
                        + (self.phone_link.error if self.phone_link else "Camera control is unavailable."),
                        "bridge": self.health()}
            return {"ok": True, "message": "The phone's stream and the listener were restarted.", "bridge": self.health()}
        if kind == "other":
            self.event("Reset camera: a fresh capture of the other camera")
            with self.manual_operation():
                self._stop_capture("reset camera")
                with self.condition:
                    self.same_since = None
            return self.reconnect("reset camera")
        self.event("Reset camera: resetting Continuity Camera, then asking the iPhone again")
        with self.manual_operation():
            self._stop_capture("reset camera")
            reset = reset_continuity_capture_agent()
            with self.condition:
                self.last_agent_reset_at = time.monotonic()
                self.last_agent_reset = reset
                self.same_since = None
            time.sleep(CONTINUITY_SETTLE_S)
            self.wait_for_target_device(self.args.device_wait_timeout)
        return self.reconnect("reset camera")

    def frames_flowing(self) -> bool:
        """Fresh frames from the chosen source (the phone link asks little of the phone while they flow)."""
        with self.condition:
            return bool(self.latest_frame and self.last_frame_at and time.monotonic() - self.last_frame_at < self.args.stall_timeout)

    def phone_allowed(self) -> bool:
        """Test and simulation bridges never reach for a real phone (it belongs to the live bridge)."""
        return not (getattr(self.args, "no_phone", False) or getattr(self.args, "simulate", None))

    def ensure_phone_link(self):
        """The bridge's link to Blackmagic Camera (phone_control.PhoneLink), started with the bridge. None on a
        bridge that may not touch a phone."""
        if not self.phone_allowed():
            return None
        if self.phone_link is None:
            from phone_control import PhoneLink

            self.phone_link = PhoneLink(lambda: live_source_kind(self.args), lambda: stream_addresses(self.args),
                                        self.frames_flowing, log=self.event,
                                        restart_listener=lambda: self._launch("listening again for the phone's stream", replace=True))
            self.phone_link.start()
        return self.phone_link

    def _next_heal_at_locked(self) -> float | None:
        """When the monitor will next reset Continuity Camera for a silent phone (monotonic seconds)."""
        if not self.last_start_at:
            return None
        first = self.last_start_at + self.args.first_frame_timeout
        if not self.last_agent_reset_at:
            return first
        return max(first, self.last_agent_reset_at + self._heal_gap_locked())

    def _heal_gap_locked(self) -> float:
        return min(300.0, self.args.auto_reset_min_interval * (2 ** max(0, self.silent_heals - 1)))

    def _streaming(self) -> bool:
        """Whether the live capture reads a network stream (not the device, not test footage)."""
        return STREAM_SOURCE is not None and SIMULATED_CAMERA is None

    def wait_for_frame(
        self, last_seen_count: int, timeout: float = 2.0, return_if_stopped: bool = False
    ) -> tuple[int, bytes | None]:
        """The next frame after `last_seen_count`, waiting up to `timeout`. While no capture
        runs it still waits (a stream subscriber must not spin); `return_if_stopped` returns
        at once instead, for a reconnect that wants to know its capture already exited."""
        deadline = time.monotonic() + timeout
        with self.condition:
            while not self.stop_event.is_set():
                if self.frame_count > last_seen_count and self.latest_frame:
                    return self.frame_count, self.latest_frame
                if return_if_stopped and not (self.process and self.process.poll() is None):
                    return self.frame_count, None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self.frame_count, None
                self.condition.wait(timeout=remaining)
        return self.frame_count, None

    def wait_for_frame_at(self, last_seen_count: int, timeout: float = 2.0) -> tuple[int, bytes | None, float]:
        """wait_for_frame, with when the bridge took that frame (wall seconds): the X-Frame-At every reader measures
        its delay from."""
        count, frame = self.wait_for_frame(last_seen_count, timeout)
        with self.condition:
            at = self.last_frame_wall_at if count == self.frame_count else time.time()
        return count, frame, at

    def latest_snapshot(self) -> bytes | None:
        with self.condition:
            if not self.latest_frame or not self.last_frame_at:
                return None
            if time.monotonic() - self.last_frame_at >= self.args.stall_timeout:
                return None
            return self.latest_frame

    def _clear_frame_cache_locked(self) -> None:
        self.latest_frame = None
        self.last_frame_at = 0.0
        self.last_frame_wall_at = 0.0
        self.condition.notify_all()

    def _ensure_monitor_thread_locked(self) -> None:
        if not self.monitor_thread or not self.monitor_thread.is_alive():
            self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self.monitor_thread.start()

    def _launch(self, reason: str, replace: bool, epoch: int | None = None, expected: object = _ANY) -> bool:
        """Start a capture. With `replace`, a running one is stopped first (and waited for);
        without it, a running capture is kept. Every capture start and stop happens under
        `lifecycle`, after the previous FFmpeg has exited, so two captures of the phone can
        never run at once. The monitor passes its `epoch` and the process it judged
        (`expected`): the launch is dropped if a reconnect or feed switch began since it
        looked, or another caller already replaced that process."""
        with self.lifecycle:
            with self.condition:
                if self.stop_event.is_set() or not self._epoch_current_locked(epoch) or self.no_camera():
                    return False
                if expected is not _ANY and self.process is not expected:
                    return False
                if not replace and self.process and self.process.poll() is None:
                    return True
            self._stop_capture(reason)
            with self.condition:
                if self.stop_event.is_set() or not self._epoch_current_locked(epoch):
                    return False
                started = self._start_locked(reason)
                self._ensure_monitor_thread_locked()
                return started

    def _epoch_current_locked(self, epoch: int | None) -> bool:
        return epoch is None or (self.manual_ops == 0 and self.manual_epoch == epoch)

    def _start_locked(self, reason: str) -> bool:
        # Only called by _launch, under `lifecycle`, after _stop_capture.
        assert self.process is None or self.process.poll() is not None, "a capture is still running"
        try:
            process = subprocess.Popen(
                self.ffmpeg_command(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except Exception as exc:
            self.process = None
            self.last_error = f"FFmpeg start failed: {exc}"
            self.condition.notify_all()
            return False

        self.process = process
        now = time.monotonic()
        wall_now = time.time()
        self.last_start_at = now
        self.last_start_wall_at = wall_now
        self.last_request_at = now
        self.last_request_wall_at = wall_now
        self.last_request_reason = reason
        self.restart_count += 1
        self.request_count += 1
        self.capture_frames = 0
        self.capture_base_count = self.frame_count
        # lastError is kept until this capture delivers a frame: it says why the last one failed.
        # A stream's listener with nothing to report says it is waiting for the phone to stream.
        if self._streaming() and not self.last_error:
            self.last_error = STREAM_SOURCE.waiting_message()
        self.capture_stderr = deque(maxlen=STDERR_TAIL_LINES)
        # FFmpeg's errors quote its input URL, which can carry a passphrase or a stream key.
        redact = STREAM_SOURCE.redact if STREAM_SOURCE else None
        threading.Thread(target=drain_stderr, args=(process, self.capture_stderr, "ffmpeg", redact), daemon=True).start()
        self.reader_thread = threading.Thread(target=self._reader_loop, args=(process,), daemon=True)
        self.reader_thread.start()
        self.condition.notify_all()
        self.recording_event("capture-start", reason=reason, nextSeq=self.frame_count + 1)
        self.same_since = None
        self.event(f"Started the capture (PID {process.pid}) for {self.args.target_device}: {reason}")
        return True

    def _stop_capture(self, reason: str) -> None:
        """Stop the capture and wait for FFmpeg to exit. The process handle is swapped out
        under the condition (health() sees "down" at once and frames of the old capture are
        discarded); the wait itself happens outside it, so health() never blocks on an
        FFmpeg stuck in AVFoundation. `lifecycle` stays held until the old FFmpeg is gone."""
        with self.lifecycle:
            with self.condition:
                process = self.process
                stderr_tail = self.capture_stderr
                self.process = None
                self._clear_frame_cache_locked()
            if not process:
                return
            exited_by_itself = process.poll() is not None
            if not exited_by_itself and SIMULATED_CAMERA and getattr(self.args, "file_stop", "kill") == "kill":
                # A file being read needs no grace, and asked to stop it never stops by itself: its reader has
                # stopped reading, so it waits on a full pipe and every switch waited the full 0.2 s (L1c, 2026-10-20).
                process.kill()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    print(f"FFmpeg PID {process.pid} did not exit after SIGKILL", flush=True)
            elif not exited_by_itself:
                process.terminate()
                try:
                    # A recording played as the camera is only a file being read: it is stopped at
                    # once (FFmpeg reading with -re ignored SIGTERM, and every switch or seek waited
                    # the full 2 s before SIGKILL). The phone keeps its grace, for AVFoundation.
                    process.wait(timeout=0.2 if SIMULATED_CAMERA else 2)
                except subprocess.TimeoutExpired:
                    # AVFoundation can hold FFmpeg past SIGTERM while the phone is not answering.
                    process.kill()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        print(f"FFmpeg PID {process.pid} did not exit after SIGKILL", flush=True)
            time.sleep(0.05)  # let the stderr reader take FFmpeg's last words
            exit_info = {
                "at": wall_timestamp(time.time()),
                "pid": process.pid,
                "reason": "FFmpeg exited by itself" if exited_by_itself else reason,
                "returnCode": process.returncode,
                "stderr": list(stderr_tail),
            }
            with self.condition:
                self.last_capture_exit = exit_info
            if exited_by_itself:
                self.event(f"The capture (PID {process.pid}) exited with code {process.returncode}")


_LIBRARIES: dict = {}  # footage folder -> footage_media.Library (cached listing, thumbnails, uploads)
MEDIA_PACE = "follow"  # --media-pace, set by main() before any library is made


def library_for(folder: Path):
    """The footage folder's library: scanned at most every few seconds, lengths and thumbnails made by
    one background-priority worker (footage_media.py), so the live feed never waits on media work."""
    from footage_media import Library, Worker

    key = str(folder)
    if key not in _LIBRARIES:
        _LIBRARIES[key] = Library(folder, (ASSETS_MANIFEST, PRIVATE_MANIFEST), CORPUS_MANIFEST,
                                  Worker(pace=media_pace) if MEDIA_PACE == "follow" else None)
    return _LIBRARIES[key]


def media_pace(requested: bool) -> int:
    """How many media jobs may run at once now (CameraBridge.media_pace): one until the bridge is serving."""
    bridge = getattr(IPhoneBridgeHandler, "camera_bridge", None)
    return bridge.media_pace(requested) if bridge is not None else 1


def footage_library(folder: Path) -> list[dict[str, object]]:
    """Every recording under the footage folder, grouped for the camera menu and the Assets view: the
    real microscope recordings (the manifests: the store's private one over assets/manifest.json), recordings added
    from the studio, the corpus
    tours by specimen, then anything else; each with a title, its length when known and whether it
    has a per-frame truth beside it (benches only; the app never reads it)."""
    if not folder.is_dir():
        return []
    return library_for(folder).items()


_REAL_CAMERA_SEEN: dict[str, tuple[float, bool]] = {}
REAL_CAMERA_TTL_S = 15.0


def real_camera_visible(target_device: str) -> bool:
    """Whether the phone itself is visible to AVFoundation, even while footage plays. A scan is an FFmpeg
    opening AVFoundation, and the studio asks every few seconds while its camera menu is open: the answer is
    kept for REAL_CAMERA_TTL_S so footage on one bridge never crowds a phone another capture is asking."""
    now = time.monotonic()
    seen = _REAL_CAMERA_SEEN.get(target_device)
    if seen and now - seen[0] < REAL_CAMERA_TTL_S:
        return seen[1]
    devices = parse_avfoundation_devices(run_device_list())
    visible = target_device in [str(device["name"]) for device in devices["video"]]
    _REAL_CAMERA_SEEN[target_device] = (now, visible)
    return visible


def feed_state(args: argparse.Namespace, bridge: "CameraBridge", probe_camera: bool = False) -> dict[str, object]:
    """Where the feed comes from, small enough to poll every few seconds: the library itself is
    GET /assets/library, fetched only when its version changes and someone looks at it."""
    folder = Path(args.footage_dir)
    library = library_for(folder) if folder.is_dir() else None
    state: dict[str, object] = {
        "source": "footage" if SIMULATED_CAMERA else "camera",
        "footage": getattr(SIMULATED_CAMERA, "footage_name", None),
        "footageTitle": getattr(SIMULATED_CAMERA, "footage_title", None),
        "folder": str(folder),
        "libraryVersion": library.version() if library else "",
        # Where the recording is (for the studio's playhead), when one plays.
        "playback": SIMULATED_CAMERA.playback() if SIMULATED_CAMERA else None,
        "libraryCount": len(library.items()) if library else 0,
        "targetDevice": args.target_device,
        "bridge": bridge.health(),
    }
    if probe_camera:
        if bridge.no_camera():
            state["cameraVisible"] = False  # no camera chosen: nothing is looked for
        elif SIMULATED_CAMERA:
            # The phone itself, while footage plays; the bridge's device state is the footage's.
            # A stream has no device to look for (its listener is always there), so no scan.
            state["cameraVisible"] = True if STREAM_SOURCE else real_camera_visible(getattr(args, "device_name", None) or args.target_device)
        else:
            snapshot = bridge.probe_device()  # devicePresent follows the probe at once (no scan while capturing)
            state["cameraVisible"] = bool(snapshot["ok"])
            state["bridge"] = bridge.health()
    return state


def switch_source(args: argparse.Namespace, bridge: "CameraBridge", kind: str, url: str | None = None,
                  device: str | None = None) -> dict[str, object]:
    """The console's choice of live source: the iPhone (Continuity Camera), Blackmagic over Wi-Fi or Blackmagic
    over USB. Kept in --source-file for the next start. Between Wi-Fi and USB the capture keeps running (both
    streams come into the same listener): only the phone is told where to stream, by the phone link. While test
    footage plays it keeps playing: the choice is what the camera menu goes back to. Downstream it looks exactly
    like the camera reconnecting, or, between the two links, like nothing at all."""
    from stream_source import StreamSource

    if os.environ.get("MICROMIND_WORKSPACE_STUDY") == "1":
        raise ValueError("Camera source changes are unavailable in this isolated preview. Use its configured private feed.")
    kind = source_kind(kind)
    if kind in STREAM_KINDS and url:
        StreamSource(url)  # a bad URL is refused before the running capture is touched
    if kind == "other":
        if url:
            StreamSource(url)
        if not (device or url or getattr(args, "other_device", None) or getattr(args, "other_url", None)):
            raise ValueError("Choose a camera or a stream address for Other camera (on the camera page)")
    previous_kind, previous_url = live_source_kind(args), getattr(args, "stream_url", None)
    same_listener = previous_kind in STREAM_KINDS and kind in STREAM_KINDS and (not url or url == previous_url)
    result = None
    with bridge.manual_operation():
        playing_footage = SIMULATED_CAMERA is not None
        if not playing_footage and not same_listener:
            bridge._stop_capture("switching live source")
        use_live_source(args, kind, url if kind != "other" or not device else None, device)
        save_source_choice(args, kind)
        bridge.recording_event("source", source=kind)
        what = (f" ({args.other_device})" if args.other_device else " (a stream)") if kind == "other" else ""
        if kind == "none":
            what = ": pictures and videos only"
        bridge.event(f"Live source: {SOURCE_TITLES[kind]}{what}")
        if not playing_footage and not same_listener:
            with bridge.condition:
                bridge.silent_heals = 0
                bridge.last_error = ""
            result = bridge.restart(f"switched to {SOURCE_TITLES[kind]}", wait_timeout=1.0, reset_missing_device=False)
    if bridge.phone_link:
        bridge.phone_link.source_changed()
    changed = (previous_kind, previous_url) != (kind, getattr(args, "stream_url", None))
    return {**feed_state(args, bridge), "ok": True, "changed": changed, "switch": result, "kept": same_listener}


def file_probe(args: argparse.Namespace):
    """How a played file's rate and frames are read: kept per file with --probe-cache on (the default), else read every
    time."""
    from simulated_camera import probe_kept

    return probe_kept if getattr(args, "probe_cache", "on") == "on" else None


def switch_feed(args: argparse.Namespace, bridge: "CameraBridge", footage: str | None,
                recording: tuple[str, object] | None = None, paused: bool = False,
                at: float | None = None, asset: tuple[str, str] | None = None) -> dict[str, object]:
    """Play a test recording, a session's own recording (`recording`: a dataset's id and part) or any recording or
    picture under a media root (`asset`: its absolute path and title) into the live feed, or go back to the camera.
    `at`: where in the recording to start (seconds); `paused`: held there from its first frame, so a session opened on
    its recording never shows it playing. A picture is one held frame, so neither applies to it. Downstream this
    looks exactly like the camera reconnecting."""
    global SIMULATED_CAMERA
    steps = SwitchSteps()
    folder = Path(args.footage_dir)
    camera = None
    name = None
    if recording is not None:
        video, title = dataset_recording(args, *recording)
        name = f"recording:{recording[0]}/{int(str(recording[1]))}"
    elif asset is not None:
        video, title = asset_file(args, *asset)
        name = f"asset:{asset[0]}"  # the path as asked, so the orchestrator knows its own asset in /feed
    elif footage:
        titles = {str(item["name"]): str(item["title"]) for item in footage_library(folder)}
        path = (folder / footage).resolve()
        if footage not in titles or folder.resolve() not in path.parents and not (folder / footage).is_symlink():
            raise ValueError(f"No recording called {footage!r} in {folder}")
        video, title, name = folder / footage, titles[footage], footage
    steps.mark("find")  # which file, checked (a footage name through the library's listing)
    if name is not None:
        from simulated_camera import SimulatedCamera

        # A file that does not open (a picture that isn't one, a video with no frames) is refused before the running
        # capture is touched.
        camera = SimulatedCamera(str(video), args.target_device, still_fps=args.fps, probe=file_probe(args))
        camera.footage_name = name
        camera.footage_title = title
        camera.bridge = bridge
        if paused:
            camera.pause()  # before the capture starts: its first frame is the one held
        if at is not None:
            camera.seek(int(max(0.0, at) * camera.fps) % max(1, camera.total_frames))
    source = "recording" if recording is not None else "asset" if asset is not None else "footage" if name else "camera"
    if camera is not None and camera.still:
        held: dict[str, object] = {"still": True}  # one held frame: no place in it, nothing to pause
    else:
        held = {**({"at": round(max(0.0, at), 3)} if camera is not None and at is not None else {}),
                **({"paused": True} if camera is not None and paused else {})}
    steps.mark("probe")  # the file opened and probed (its rate and frames), before the running capture is touched
    bridge.recording_event("feed", source=source, footage=name, **held)
    steps.mark("note")
    with bridge.manual_operation():
        bridge._stop_capture("switching feed")
        steps.mark("stop")
        SIMULATED_CAMERA = camera
        said = {"recording": f"the session recording {name}", "asset": f"the file {asset[0] if asset else ''}",
                "footage": f"test footage {name}"}.get(source, "the camera")
        # A file's first frames come at once; the camera's may never (an app not open, a phone asleep), and its state is
        # what /feed and the health say as it comes: the switch answers within a second instead of waiting for it (live
        # 2026-10-12: going live with Blackmagic Camera closed held the studio's session switch 6 s).
        result = bridge.restart(f"switched to {said}", wait_timeout=6.0 if camera is not None else 1.0,
                                reset_missing_device=False, steps=steps)
    return said_steps(f"switched to {said}", {**feed_state(args, bridge), "switch": result}, steps)


def said_steps(what: str, state: dict[str, object], steps: SwitchSteps) -> dict[str, object]:
    """A switch's answer with how long each of its steps took (`steps`), also said in the bridge's log."""
    steps.mark("state")
    print(f"Feed {what}: {steps.words()}", flush=True)
    return {**state, "steps": steps.said()}


def pause_feed(args: argparse.Namespace, bridge: "CameraBridge", paused: bool) -> dict[str, object]:
    """The studio's play/pause. Paused, the picture holds and frames keep coming (a slide at rest,
    never a dead feed); played, the recording goes on from the held frame."""
    camera = SIMULATED_CAMERA
    if not camera:
        return {**feed_state(args, bridge), "ok": False, "error": "No recording is playing"}
    if camera.still:
        return {**feed_state(args, bridge), "ok": True}  # a picture is one held frame: nothing to pause or play
    if paused:
        camera.pause()
        bridge.recording_event("footage-paused", footageFrame=camera.paused_frame)
        return {**feed_state(args, bridge), "ok": True}
    if camera.paused_frame is None:
        return {**feed_state(args, bridge), "ok": True}
    steps = SwitchSteps()
    camera.resume()
    bridge.recording_event("footage-played")
    steps.mark("note")
    result = bridge.restart("test footage played on", wait_timeout=6.0, reset_missing_device=False, steps=steps)
    return said_steps("played on", {**feed_state(args, bridge), "ok": True, "switch": result}, steps)


def seek_feed(args: argparse.Namespace, bridge: "CameraBridge", seconds: float) -> dict[str, object]:
    """The studio's playhead: the recording jumps to `seconds` and plays on from there. Downstream it
    looks like the slide jumping (a new view), exactly as a seek in the sim harness does."""
    camera = SIMULATED_CAMERA
    if not camera:
        return {**feed_state(args, bridge), "ok": False, "error": "No recording is playing"}
    if camera.still:
        # A picture has one frame: a seek moves nothing, and restarting the capture would only stall the view.
        return {**feed_state(args, bridge), "ok": True}
    steps = SwitchSteps()
    with bridge.manual_operation():
        # Retire the old reader before clearing the held picture. Otherwise an
        # in-flight old frame can become the picture held at the new playhead.
        bridge._stop_capture("seeking test footage")
        steps.mark("stop")
        camera.seek(int(max(0.0, seconds) * camera.fps) % camera.total_frames)
        bridge.recording_event("footage-seek", seconds=round(seconds, 3))
        steps.mark("note")
        result = bridge.restart(f"test footage moved to {seconds:.1f} s", wait_timeout=6.0, reset_missing_device=False,
                                steps=steps)
    return said_steps(f"moved to {seconds:.1f} s", {**feed_state(args, bridge), "ok": True, "switch": result}, steps)


def settings_file(args: argparse.Namespace) -> bytes:
    """The Blackmagic settings file for this Mac's listener (settings_server.py)."""
    from stream_source import default_url, StreamSource

    port = (STREAM_SOURCE or StreamSource(getattr(args, "stream_url", None) or default_url())).port or 9710
    return settings_xml(stream_addresses(args), port)


def start_settings_server(args: argparse.Namespace, port: int | None = None):
    """The server the phone fetches the settings file from (settings_server.py), the file made fresh for each request."""
    return serve_settings(lambda: settings_file(args), port)


class IPhoneBridgeHandler(BridgeHandler):
    """The bridge's routes (bridge_routes.py), acting on this module's state."""

    main = MAIN


def main() -> int:
    global SIMULATED_CAMERA, MEDIA_PACE
    args = parse_args()
    MEDIA_PACE = args.media_pace
    try:
        # Before the footage: it is named after the source it stands in for.
        use_live_source(args, *initial_live_source(args))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.simulate:
        from simulated_camera import SimulatedCamera

        SIMULATED_CAMERA = SimulatedCamera(args.simulate, args.target_device, start_frame=args.simulate_start_frame,
                                           still_fps=args.fps, probe=file_probe(args))
        SIMULATED_CAMERA.footage_name = Path(args.simulate).name
        # Started on a recording from the footage folder: named as the camera menu names it.
        played = Path(args.simulate).resolve()
        for item in footage_library(Path(args.footage_dir)):
            if (Path(args.footage_dir) / str(item["name"])).resolve() == played:
                SIMULATED_CAMERA.footage_name, SIMULATED_CAMERA.footage_title = str(item["name"]), str(item["title"])
                break
        print(
            f"SIMULATED CAMERA: '{args.target_device}' is played from {SIMULATED_CAMERA.video.name} "
            f"({SIMULATED_CAMERA.total_frames} frames). Faults: POST /sim/freeze|crash|unplug|plug|seek|repeat|degrade; "
            "ground truth: GET /sim/status",
            flush=True,
        )
    bridge = CameraBridge(args)
    if SIMULATED_CAMERA:
        SIMULATED_CAMERA.bridge = bridge
    no_camera = bridge.no_camera()
    # No camera chosen: nothing is scanned at start either.
    snapshot = ({"ok": False, "noCamera": True, "targetDevice": args.target_device, "videoDevices": []} if no_camera
                else make_device_snapshot(args.target_device))
    if not no_camera:
        bridge.update_device_snapshot(snapshot)
    bridge.ensure_monitor()
    if no_camera:
        print("No camera chosen: the bridge plays only the pictures and videos the studio opens.", flush=True)
    elif snapshot["ok"]:
        bridge.start()
    else:
        print("Target iPhone device is not visible yet; starting console in recovery mode.", file=sys.stderr)
        print(f"Expected AVFoundation device: {args.target_device}", file=sys.stderr)
        print("Open the page, lock and mount the iPhone near the Mac, or plug it in and approve Trust/Allow.", file=sys.stderr)

    IPhoneBridgeHandler.bridge_args = args
    IPhoneBridgeHandler.device_snapshot = snapshot
    IPhoneBridgeHandler.camera_bridge = bridge
    bridge.ensure_phone_link()  # finds and drives Blackmagic Camera when a Blackmagic source is chosen (not on test bridges)
    if bridge.phone_allowed():
        start_settings_server(args)  # the phone fetches the Blackmagic settings file from here (the camera page's QR code)

    if STREAM_SOURCE:
        print(f"Source: {STREAM_SOURCE.label}, read from {STREAM_SOURCE.redacted} (no AVFoundation device is opened); "
              f"the phone's app streams to {STREAM_SOURCE.connect_to}")
    else:
        print(f"Target iPhone device: {args.target_device}")
    print(f"Serving robust iPhone bridge at: http://{args.host}:{args.port}/camera/")
    print("The server keeps FFmpeg open and auto-restarts if the iPhone stream stalls.")
    print("The page receives /iphone.mjpeg from the bridge; browser camera permission is not used.")
    print("Stop with Ctrl-C.")

    server = BridgeServer((args.host, args.port), IPhoneBridgeHandler)

    def stop_on_terminate(*_: object) -> None:
        # `make stop` sends SIGTERM: shut down like Ctrl-C, so FFmpeg is stopped too.
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_on_terminate)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        bridge.stop()
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
