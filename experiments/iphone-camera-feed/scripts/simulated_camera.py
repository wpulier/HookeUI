"""A simulated iPhone camera for the bridge (`iphone_bridge_server.py --simulate VIDEO`).

The bridge touches the physical device in exactly three places: the AVFoundation device
listing, the FFmpeg capture command, and the ContinuityCaptureAgent reset. In simulation
only those three are replaced; everything else in the bridge (the FFmpeg process, MJPEG
parsing, frame cache, stall monitor, reconnect state machine, recording and every HTTP
endpoint) runs unchanged, so downstream services receive frames exactly as they would
from the phone and cannot tell the difference.

- Frames come from a recording, encoded by FFmpeg with the same MJPEG settings as live.
- The specimen keeps "existing" while the camera is disconnected: the recording advances
  on a wall clock, and every capture start seeks to the current world position.
- Faults reproduce the real symptoms: `freeze` pauses FFmpeg (alive but silent), `crash`
  kills it, `unplug` removes the device from the listing so FFmpeg cannot open it, and a
  ContinuityCaptureAgent reset brings an unplugged device back after a delay, which is the
  recovery the bridge's hard reconnect relies on.
- `repeat` keeps frames arriving but all of them the same picture (the 2026-10-04 symptom: the
  phone's Camera app open, a frozen blurred picture still arriving as "live").
- `degrade` changes the picture the way a real eyepiece does: defocus blur, vignetting,
  exposure, motion blur, sensor noise, or a zoom step standing in for an objective change.
  It is applied by FFmpeg, so it restarts the capture at the current world position (a short
  stall, like `seek`); the parameters are reported on /sim/status.
- A picture plays too (a snapshot, a clearer picture, a photo the user added): one frame looped at
  the bridge's frame rate, read in real time like a recording, so frames keep arriving and
  downstream it is a slide at rest. It has no length, no place and nothing to pause.

Ground truth for test harnesses (which recording frame is on screen) is exposed only on
/sim/* endpoints; /api/health and /iphone.mjpeg are identical to live operation.

Standard library only: the bridge runs on the system Python.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

OTHER_VIDEO_DEVICES = ["FaceTime HD Camera"]
AUDIO_DEVICES = ["MacBook Pro Microphone"]
# What the bridge can play in place of the camera: recordings, and pictures, which play as one held frame. The footage
# library (footage_media.py) and the bridge's asset source read these, so the three agree on what a file is.
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}
STILL_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def is_still(path: Path | str) -> bool:
    """Whether a file plays as a picture (one held frame) rather than as a recording."""
    return Path(path).suffix.lower() in STILL_SUFFIXES


def _probe(video: Path, still_fps: float = 30.0) -> tuple[float, int]:
    """A recording's playback timeline rate and packet count. A picture has no rate of its own: it plays at the bridge's
    (`still_fps`), and it is one frame. ValueError, in words, for a file that does not open as either."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe is required for camera simulation (brew install ffmpeg)")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=r_frame_rate,nb_read_packets,width,height,duration:format=duration", "-of", "json", str(video)],
        capture_output=True, text=True, check=False,
    )
    try:
        metadata = json.loads(result.stdout)
        stream = metadata["streams"][0]
        if is_still(video):
            # ffprobe names a stream even for a file that only claims to be a picture; a real one has a size.
            if int(stream["width"]) > 0 and int(stream["height"]) > 0:
                return float(still_fps), 1
            raise ValueError("no picture")
        num, den = stream["r_frame_rate"].split("/")
        fps, frames = float(num) / float(den), int(stream["nb_read_packets"])
    except (ValueError, KeyError, IndexError, TypeError, ZeroDivisionError):
        raise ValueError(f"{video.name} does not open as a {'picture' if is_still(video) else 'video'}") from None
    if frames < 1 or not math.isfinite(fps) or fps <= 0:
        raise ValueError(f"{video.name} has no frames to play")
    # Browser/canvas recordings can be variable-rate: r_frame_rate is a nominal rate,
    # not packets / elapsed seconds. Keep the native duration for seek/pause/resume.
    # These ticks do not establish exact native frame indices for variable-rate media.
    for declared in (stream.get("duration"), (metadata.get("format") or {}).get("duration")):
        try:
            duration = float(declared)
            rate = frames / duration if math.isfinite(duration) and duration > 0 else 0
        except (ValueError, TypeError, OverflowError):
            continue
        if math.isfinite(rate) and rate > 0:
            if not math.isclose(rate, fps, rel_tol=1e-4):
                fps = rate
            break
    return fps, frames


# A file's rate and frame count, kept by the file it was read from (its path, inode, size and modification time) and
# the rate a picture plays at: the option --probe-cache on (L1c). FFprobe counts every packet of a recording, the
# largest step of a switch on a loaded disk (1.8-5.8 s on 2026-10-20), and playing a recording again needs no new
# count. A file that fails is not kept, so it is read again next time.
_PROBED: dict[tuple[str, int, int, int, float], tuple[float, int]] = {}
_PROBED_LOCK = threading.Lock()


def probe_kept(video: Path, still_fps: float = 30.0) -> tuple[float, int]:
    """`_probe`, read once for each file as it is now and kept."""
    stat = video.stat()
    key = (str(video), stat.st_ino, stat.st_size, stat.st_mtime_ns, float(still_fps))
    with _PROBED_LOCK:
        known = _PROBED.get(key)
    if known is None:
        known = _probe(video, still_fps)
        with _PROBED_LOCK:
            _PROBED[key] = known
    return known


# What `degrade` can do, with the allowed range of each parameter.
DEGRADATIONS = {
    "zoom": (1.0, 4.0),         # x: centre crop enlarged back to full size (an objective change)
    "blur": (0.0, 20.0),        # Gaussian sigma in source pixels (defocus)
    "motion": (1.0, 10.0),      # frames averaged together (motion blur during a pan)
    "vignette": (0.0, 1.0),     # 0 none ... 1 strong darkening towards the edge
    "exposure": (-1.0, 1.0),    # brightness shift: -1 dark ... 1 bright
    "noise": (0.0, 60.0),       # sensor noise strength (seeded, so it repeats)
    "swimmer": (0.0, 400.0),    # px/s: a dark blob crossing a still view (something swimming in)
}


def degrade_filters(degradation: dict[str, float]) -> list[str]:
    """FFmpeg filters for a degradation, in the order a camera adds them: optics (zoom, blur),
    motion, the eyepiece's vignetting, exposure, then sensor noise."""
    filters = []
    zoom = degradation.get("zoom", 1.0)
    if zoom > 1.0:
        filters.append(f"crop=iw/{zoom:g}:ih/{zoom:g},scale=iw*{zoom:g}:ih*{zoom:g}")
    if degradation.get("blur", 0) > 0:
        filters.append(f"gblur=sigma={degradation['blur']:g}")
    if degradation.get("motion", 1) > 1:
        filters.append(f"tmix=frames={int(degradation['motion'])}")
    if degradation.get("vignette", 0) > 0:
        filters.append(f"vignette=angle={0.2 + 1.2 * degradation['vignette']:.3f}")
    if degradation.get("exposure", 0) != 0:
        filters.append(f"eq=brightness={0.5 * degradation['exposure']:.3f}")
    if degradation.get("noise", 0) > 0:
        filters.append(f"noise=alls={int(degradation['noise'])}:allf=t:all_seed=7")
    return filters


class SimulatedCamera:
    # A picture rather than a recording (set from the file's suffix; a class default for cameras made without one).
    still = False

    def __init__(self, video: str, target_device: str, start_frame: int = 0, reset_recovery_seconds: float = 3.0,
                 still_fps: float = 30.0, probe=None) -> None:
        self.video = Path(video).resolve()
        if not self.video.exists():
            raise SystemExit(f"Simulation recording not found: {self.video}")
        self.target_device = target_device
        self.still = is_still(self.video)
        # A picture plays at the bridge's own rate (`still_fps`, its --fps): one frame, so it is always that frame.
        # `probe`: how its rate and frames are read (probe_kept with --probe-cache on), else read now.
        self.fps, self.total_frames = (probe or _probe)(self.video, still_fps)
        self.reset_recovery_seconds = reset_recovery_seconds
        self.lock = threading.RLock()
        self.bridge = None  # set by the bridge once constructed
        self.plugged = True
        self.recover_on_reset = True
        self.frozen_until = 0.0
        self.degradation: dict[str, float] = {}
        self.repeat_until = 0.0
        self._repeated: bytes | None = None
        self.faults: list[dict] = []
        self._world_origin = time.monotonic() - start_frame / self.fps
        # Paused (the studio's player): the picture held, its frame kept, while frames keep arriving
        # (a still slide, never a dead feed); None while playing.
        self.paused_frame: int | None = None
        self._held: bytes | None = None
        self._capture_starts: list[dict] = []

    # --- the three device touch points ----------------------------------------------
    def device_listing(self) -> str:
        """Same text format as `ffmpeg -f avfoundation -list_devices true -i ""`."""
        with self.lock:
            video = list(OTHER_VIDEO_DEVICES) + ([self.target_device] if self.plugged else [])
        lines = ["[AVFoundation indev @ 0x0] AVFoundation video devices:"]
        lines += [f"[AVFoundation indev @ 0x0] [{i}] {name}" for i, name in enumerate(video)]
        lines.append("[AVFoundation indev @ 0x0] AVFoundation audio devices:")
        lines += [f"[AVFoundation indev @ 0x0] [{i}] {name}" for i, name in enumerate(AUDIO_DEVICES)]
        return "\n".join(lines) + "\n"

    def capture_command(self, ffmpeg: str, args) -> list[str]:
        """Replaces the avfoundation input with the recording; output settings match live."""
        with self.lock:
            plugged = self.plugged
        if not plugged:
            # Like opening a camera that macOS no longer advertises: FFmpeg fails at once.
            return [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "quiet", "-i", "/nonexistent/simulated-unplugged-camera"]
        world_frame = self.world_frame()
        frame_count = self.bridge.frame_count if self.bridge is not None else 0
        with self.lock:
            self._capture_starts.append(
                {"bridgeFrameCount": frame_count, "sourceFrame": world_frame, "at": time.time()}
            )
            self._capture_starts = self._capture_starts[-50:]
        if self.still:
            # One frame looped at the bridge's rate and read in real time (-re), as a recording is: frames arrive as
            # the camera's do, all the same picture (a slide at rest). It has one frame, so no place to start from.
            source = ["-re", "-loop", "1", "-framerate", f"{self.fps:g}", "-i", str(self.video)]
        else:
            source = ["-re", "-stream_loop", "-1", "-ss", f"{world_frame / self.fps:.3f}", "-i", str(self.video)]
        return [
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "warning", *source,
            "-an", "-vf", self.filter_chain(args.fps, args.size),
            "-q:v", str(args.jpeg_quality), "-vcodec", "mjpeg", "-f", "image2pipe", "pipe:1",
        ]

    def filter_chain(self, fps, size: str) -> str:
        with self.lock:
            degradation = dict(self.degradation)
        chain = ",".join([f"fps={fps}", *degrade_filters(degradation)])
        speed = degradation.get("swimmer", 0)
        if speed > 0:
            # A dark blob crossing the middle of the view (it wraps around), on a still slide.
            chain += (f"[base];color=c=0x1c241c:s=36x36:r={fps}[blob];"
                      f"[base][blob]overlay=x='mod(100+{speed:g}*t,W)':y='H/2-18':eval=frame:shortest=1")
        if self.still:
            # A picture keeps its shape: fitted inside the frame, the rest black like the eyepiece's surround. A snapshot
            # of a close-up or a clearer picture of the disc is rarely 16:9, and stretched, round cells would measure oval.
            width, height = size.lower().split("x")
            return chain + (f",scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2")
        return chain + f",scale={size.replace('x', ':')}"

    def reset_agent(self) -> dict:
        """Stands in for `killall ContinuityCaptureAgent`: an unplugged simulated phone is
        re-advertised after a short delay, as the real agent reset does."""
        started = time.time()
        with self.lock:
            recover = self.recover_on_reset and not self.plugged
        if recover:
            threading.Timer(self.reset_recovery_seconds, self.plug).start()
        self._log("continuity_reset", recovers=recover)
        return {
            "attempted": True,
            "ok": True,
            "returnCode": 0,
            "stdout": "",
            "stderr": "",
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started)),
        }

    # --- world clock and ground truth ------------------------------------------------
    def world_frame(self) -> int:
        if self.paused_frame is not None:
            return self.paused_frame
        return int((time.monotonic() - self._world_origin) * self.fps) % self.total_frames

    def playback(self) -> dict:
        """Where the recording is now, for the studio's player (it loops), and whether it is paused. A picture says so
        (`still`): no length, no place, always held."""
        if self.still:
            return {"seconds": 0.0, "duration": 0.0, "paused": True, "still": True}
        return {"seconds": round(self.world_frame() / self.fps, 2), "duration": round(self.total_frames / self.fps, 2),
                "paused": self.paused_frame is not None, "still": False}

    def pause(self) -> None:
        """Holds the picture (the next frame that arrives, then always that one) and stops the
        recording's clock. Frames keep arriving: downstream it is a slide at rest. A picture is held already."""
        if self.still:
            return
        with self.lock:
            if self.paused_frame is None:
                self.paused_frame = self.world_frame()
                self._held = None
        self._log("pause", frame=self.paused_frame)

    def resume(self) -> None:
        """Plays on from the held frame (the caller restarts the capture there). A picture has nothing to play."""
        if self.still:
            return
        with self.lock:
            frame, self.paused_frame, self._held = self.paused_frame, None, None
            if frame is not None:
                self._world_origin = time.monotonic() - frame / self.fps
        self._log("resume", frame=frame)

    def seek(self, frame: int) -> None:
        """Move the specimen: the next capture start (and the current one, restarted by the
        caller) shows this recording frame. Paused, it holds that frame instead. A picture has one frame: nothing moves."""
        if self.still:
            return
        with self.lock:
            self._world_origin = time.monotonic() - max(0, frame) / self.fps
            if self.paused_frame is not None:
                self.paused_frame, self._held = max(0, frame) % self.total_frames, None
        self._log("seek", frame=frame)

    def source_frame_for(self, bridge_frame_count: int) -> int | None:
        """Recording frame index shown in bridge frame number `bridge_frame_count` (1-based,
        as counted by the bridge), if it came from a known capture start."""
        with self.lock:
            starts = [s for s in self._capture_starts if s["bridgeFrameCount"] < bridge_frame_count]
        if not starts:
            return None
        start = starts[-1]
        return (start["sourceFrame"] + bridge_frame_count - start["bridgeFrameCount"] - 1) % self.total_frames

    def frame_facts(self, bridge_frame_count: int) -> dict:
        """What the bridge's frame `bridge_frame_count` was, for a recording's sidecar: test
        footage, which recording, its frame (an estimate from the capture start), and whether the
        picture was held (paused), repeated (/sim/repeat) or degraded (/sim/degrade)."""
        facts: dict = {"src": "footage", "footage": getattr(self, "footage_name", None) or self.video.name,
                       "footageFrameEstimate": self.source_frame_for(bridge_frame_count)}
        with self.lock:
            if self.still:
                facts["still"] = True  # a picture: every frame is its one frame
                facts["footageFrameEstimate"] = 0
            if self.paused_frame is not None:
                facts["held"] = True
                facts["footageFrameEstimate"] = self.paused_frame
            if time.monotonic() < self.repeat_until:
                facts["repeated"] = True
            if self.degradation:
                facts["degraded"] = dict(self.degradation)
        return facts

    # --- faults ------------------------------------------------------------------------
    def unplug(self, recover_on_reset: bool = True) -> None:
        with self.lock:
            self.plugged = False
            self.recover_on_reset = recover_on_reset
        self._kill_capture(signal.SIGKILL)
        self._log("unplug", recoverOnReset=recover_on_reset)

    def plug(self) -> None:
        with self.lock:
            self.plugged = True
        self._log("plug")

    def crash(self) -> None:
        self._kill_capture(signal.SIGKILL)
        self._log("crash")

    def freeze(self, seconds: float) -> None:
        process = self._capture_process()
        if process is None:
            return
        os.kill(process.pid, signal.SIGSTOP)
        with self.lock:
            self.frozen_until = time.monotonic() + seconds

        def thaw(pid: int = process.pid) -> None:
            try:
                os.kill(pid, signal.SIGCONT)
            except ProcessLookupError:
                pass  # the bridge already replaced the stalled capture

        threading.Timer(seconds, thaw).start()
        self._log("freeze", seconds=seconds, pid=process.pid)

    def degrade(self, **params: float) -> dict[str, float]:
        """Set the picture degradation (no parameters: a clean picture again). The caller
        restarts the capture so FFmpeg applies it. Raises ValueError for an unknown or
        out-of-range parameter."""
        clean = {}
        for name, value in params.items():
            if name not in DEGRADATIONS:
                raise ValueError(f"unknown degradation {name!r} (one of {', '.join(DEGRADATIONS)})")
            low, high = DEGRADATIONS[name]
            value = float(value)
            if not low <= value <= high:
                raise ValueError(f"{name} must be between {low} and {high}")
            clean[name] = value
        with self.lock:
            self.degradation = clean
        self._log("degrade", **clean)
        return clean

    def repeat(self, seconds: float) -> None:
        """For `seconds`, every frame the bridge receives is replaced by the one that arrived
        first: frames keep arriving, the picture never changes."""
        with self.lock:
            self.repeat_until = time.monotonic() + seconds
            self._repeated = None
        self._log("repeat", seconds=seconds)

    def pass_frame(self, frame: bytes) -> bytes:
        """Called by the bridge for every frame it caches (the content freeze above, and a pause)."""
        with self.lock:
            if self.paused_frame is not None:
                if self._held is None:
                    self._held = frame
                return self._held
            if time.monotonic() >= self.repeat_until:
                self._repeated = None
                return frame
            if self._repeated is None:
                self._repeated = frame
            return self._repeated

    def _capture_process(self):
        bridge = self.bridge
        process = getattr(bridge, "process", None)
        if process is None or process.poll() is not None:
            return None
        return process

    def _kill_capture(self, sig: int) -> None:
        process = self._capture_process()
        if process is not None:
            try:
                os.kill(process.pid, sig)
            except ProcessLookupError:
                pass

    def _log(self, kind: str, **details) -> None:
        with self.lock:
            self.faults.append({"event": kind, "at": time.time(), **details})
            self.faults = self.faults[-100:]

    def status(self) -> dict:
        bridge = self.bridge
        latest_count = bridge.frame_count if bridge is not None else 0
        latest_source = self.source_frame_for(latest_count) if latest_count else None
        with self.lock:
            return {
                "simulated": True,
                "video": str(self.video),
                "still": self.still,
                "fps": self.fps,
                "totalFrames": self.total_frames,
                "plugged": self.plugged,
                "recoverOnReset": self.recover_on_reset,
                "frozen": time.monotonic() < self.frozen_until,
                "repeating": time.monotonic() < self.repeat_until,
                "degradation": dict(self.degradation),
                "worldFrame": int((time.monotonic() - self._world_origin) * self.fps) % self.total_frames,
                "latestBridgeFrame": latest_count,
                "captureStarts": list(self._capture_starts[-5:]),
                "events": list(self.faults[-20:]),
                "latestSourceFrame": latest_source,
            }
