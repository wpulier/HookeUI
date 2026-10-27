"""What the camera bridge says about the camera, a mixin of iphone_bridge_server.py's CameraBridge: its health, why
there is no picture (the diagnosis), the checklist's facts, how evenly frames come and the console's source checks,
the Phone check, and the camera's story. Built only from what the bridge read; the live state is the main module's,
reached through `main` (bridge_common.ModuleView)."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from bridge_common import wall_timestamp
from bridge_sources import SOURCE_TITLES, STREAM_KINDS, source_choices


# Steadiness: frame arrival times kept for the last CADENCE_FRAMES frames (20 s at 30 fps), summarized over
# CADENCE_WINDOW_S for health and the console. A gap over GAP_MS is a frame visibly late (three frame times at 30
# fps would be 100 ms); "even" is the live feed at its best: 30 fps (Continuity measured 30, footage 30) with
# p95 gaps under 50 ms (footage through this bridge read by the orchestrator's way: 72 ms with its reader's own
# bunching, 33 ms between frames).
CADENCE_FRAMES = 600
CADENCE_WINDOW_S = 5.0
GAP_MS = 100.0
EVEN_FPS = 27.0
EVEN_P95_MS = 50.0
# The media worker's pace when it follows the machine (--media-pace follow, footage_media.Worker): its own frame rate
# over PACE_WINDOW_S, against PACE_FPS_SHARE of the rate it is set to make (28.5 at 30 fps: a steady 30 reads
# 29.5-30.1 over a few seconds).
PACE_WINDOW_S = 3.0
PACE_FPS_SHARE = 0.95


def cadence_summary(times: list[float], now: float, window: float) -> dict[str, object]:
    """Frame rate and how evenly the frames came over the last `window` seconds."""
    recent = [t for t in times if now - t <= window]
    if len(recent) < 2:
        return {"fps": 0.0, "p50Ms": None, "p95Ms": None, "maxGapMs": None, "gaps": 0, "even": False, "windowS": window}
    gaps = sorted(1000.0 * (b - a) for a, b in zip(recent, recent[1:]))
    span = max(now - recent[0], recent[-1] - recent[0])
    fps = round((len(recent) - 1) / span, 1) if span > 0 else 0.0
    p95 = gaps[min(len(gaps) - 1, int(round(0.95 * (len(gaps) - 1))))]
    summary = {
        "fps": fps,
        "p50Ms": round(gaps[len(gaps) // 2], 1),
        "p95Ms": round(p95, 1),
        "maxGapMs": round(gaps[-1], 1),
        "gaps": sum(1 for gap in gaps if gap > GAP_MS),
        "windowS": window,
    }
    summary["even"] = fps >= EVEN_FPS and p95 <= EVEN_P95_MS
    return summary


def unsafe_quality_list(app_version: object) -> list[str]:
    try:
        from phone_control import unsafe_qualities

        return unsafe_qualities(str(app_version) if app_version else None)
    except Exception:
        return []


def proven_versions() -> list[str]:
    """The Blackmagic Camera versions the Phone check proved (blackmagic-api.json)."""
    try:
        from phone_lab import load_manifest

        return sorted((load_manifest().get("versions") or {}).keys())
    except Exception:
        return []


class StatusMixin:
    """The camera's own story, told by CameraBridge."""

    def own_fps(self, window: float = PACE_WINDOW_S) -> float | None:
        """The frame rate this bridge made over the last `window` seconds (since its capture started, when that is
        sooner), or None while no capture runs (no live feed to keep) or one started under a second ago."""
        with self.condition:
            if not (self.process and self.process.poll() is None):
                return None
            now = time.monotonic()
            span = min(window, now - self.last_start_at) if self.last_start_at else window
            if span < 1.0:
                return None
            recent = 0
            for at in reversed(self.frame_times):
                if now - at > span:
                    break
                recent += 1
        return recent / span

    def media_pace(self, requested: bool) -> int:
        """How many media jobs (thumbnails, lengths) may run at once now: with `--media-pace follow` (the default),
        at most two prefetch jobs while the bridge makes its frame rate; explicit requests may use the full pool.
        MEDIA_JOBS_MAX without capture, and one, as `one` always allows,
        while it does not. Never none: a camera that runs a little under --fps (Continuity, Blackmagic or a stream at
        24-25 fps against 30) would otherwise never measure a length or prefetch a thumbnail (review of #372)."""
        from footage_media import MEDIA_JOBS_MAX

        if getattr(self.args, "media_pace", "follow") != "follow":
            return 1
        with self.condition:
            capturing = bool(self.process and self.process.poll() is None)
        if not capturing:
            return min(MEDIA_JOBS_MAX, os.cpu_count() or 1)
        fps = self.own_fps()
        # Reserve capacity from speculative work. Explicit thumbnail requests must
        # not wait behind a pool occupied by slow background probes; the worker
        # already promotes requested jobs ahead of prefetch. Slow capture still
        # constrains both, and an offline library can use the full pool.
        if fps is not None and fps >= PACE_FPS_SHARE * self.args.fps:
            return min(MEDIA_JOBS_MAX if requested else 2, os.cpu_count() or 1)
        return 1

    def health(self) -> dict[str, object]:
        # Read before the bridge's lock: the recorder's lock may be held while it writes, and the frames (which need
        # the bridge's lock) must never wait on the recording (an audit finding, 2026-10-12).
        recording = self.recording_status()
        with self.condition:
            now = time.monotonic()
            running = bool(self.process and self.process.poll() is None)
            last_frame_age = None if not self.last_frame_at else now - self.last_frame_at
            last_start_age = now - self.last_start_at if self.last_start_at else None
            last_request_age = now - self.last_request_at if self.last_request_at else None
            device_present = self._device_present_locked()
            state, state_label = self._state_locked(
                running=running,
                device_present=device_present,
                last_frame_age=last_frame_age,
                last_start_age=last_start_age,
            )
            return {
                "ok": state == "live",
                "state": state,
                "stateLabel": state_label,
                "running": running,
                "devicePresent": device_present,
                "targetDevice": self.args.target_device,
                # Where the live picture comes from: the AVFoundation device, or a network stream
                # (its URL without path, query or secrets). Test footage replaces either without
                # showing here, as everywhere downstream.
                "source": {**self.main.STREAM_SOURCE.describe(), "link": getattr(self.args, "link", None)} if self.main.STREAM_SOURCE else {"kind": "device", "name": self.args.target_device},
                # The two live sources the console offers (Continuity Camera, a phone app's stream) and
                # which is on; test footage plays over either.
                "sourceKind": self.main.live_source_kind(self.args),
                "capture": ("footage" if self.main.SIMULATED_CAMERA else "stream" if self.main.STREAM_SOURCE else
                            "app" if self.main.native_capture_ready() else "ffmpeg"),
                # How evenly frames arrive (the last CADENCE_WINDOW_S): the live feed's standard is 30 fps, evenly.
                "cadence": cadence_summary(list(self.frame_times), now, CADENCE_WINDOW_S),
                "trial": self.trial_running,
                "trialCount": len(self.trials),
                "sourceChoices": source_choices(self.args, self.main.live_source_kind(self.args),
                                                 self.phone_link.snapshot() if self.phone_link else None),
                # Blackmagic Camera as the phone link last read it (cached; never read on this path).
                "phone": self.phone_link.snapshot() if self.phone_link else None,
                # Why there is no picture and what to do, in plain words (None while live).
                "diagnosis": self._diagnosis_locked(state, now, running, device_present),
                "lastHeal": self.last_heal,
                "bootId": self.boot_id,
                "pid": self.process.pid if running and self.process else None,
                "frameCount": self.frame_count,
                "latestFrameBytes": len(self.latest_frame) if self.latest_frame else 0,
                "totalBytes": self.total_bytes,
                "restartCount": self.restart_count,
                "scanCount": self.scan_count,
                "continuityResets": self.continuity_resets,
                "requestCount": self.request_count,
                "uptimeSeconds": round(now - self.started_at, 2),
                "lastStartAgeSeconds": round(last_start_age, 2) if last_start_age is not None else None,
                "lastStartAt": wall_timestamp(self.last_start_wall_at),
                "lastRequestAgeSeconds": round(last_request_age, 2) if last_request_age is not None else None,
                "lastRequestAt": wall_timestamp(self.last_request_wall_at),
                "lastRequestReason": self.last_request_reason,
                "lastFrameAgeSeconds": round(last_frame_age, 2) if last_frame_age is not None else None,
                "lastFrameAt": wall_timestamp(self.last_frame_wall_at),
                # Why there is no picture, kept until frames flow again (FFmpeg's own words
                # when it failed: the last line of its stderr).
                "lastError": self.last_error,
                # The last lines FFmpeg wrote for the current capture, and how the previous
                # one ended (reason, exit code, its last stderr lines).
                "ffmpegStderr": list(self.capture_stderr),
                "lastCaptureExit": self.last_capture_exit,
                "lastContinuityReset": self.last_agent_reset,
                "recording": recording,
                "settings": {
                    "size": self.args.size,
                    "fps": self.args.fps,
                    "pixelFormat": self.args.pixel_format,
                    "jpegQuality": self.args.jpeg_quality,
                    "stallTimeout": self.args.stall_timeout,
                    "firstFrameTimeout": self.args.first_frame_timeout,
                },
            }

    def _state_locked(
        self,
        *,
        running: bool,
        device_present: bool,
        last_frame_age: float | None,
        last_start_age: float | None,
    ) -> tuple[str, str]:
        if self.no_camera():
            return "no_camera", "No camera in use: pictures and videos only"
        if not device_present:
            return "missing", f"{self.args.target_device} is not visible to AVFoundation"
        if running and self.latest_frame is not None and last_frame_age is not None:
            if last_frame_age < self.args.stall_timeout:
                return "live", "Fresh iPhone frames are arriving"
            return "stale", "No fresh iPhone frames are arriving"
        if running and self.latest_frame is None:
            if last_start_age is not None and last_start_age < self.args.stall_timeout:
                return "requesting", self.main.STREAM_SOURCE.waiting_message() if self._streaming() else "Capture request sent to iPhone"
            return "waiting", self.main.STREAM_SOURCE.waiting_message() if self._streaming() else "Waiting for iPhone frames after request"
        return "down", "FFmpeg capture is not running"

    def _diagnosis_locked(self, state: str, now: float, running: bool, device_present: bool) -> dict[str, object] | None:
        """What is wrong and what to do about it, for the console and the studio's notice. Built only from
        what the bridge knows (its state, its last heal, the facts the monitor read); never a guess."""
        if state in ("live", "no_camera") or self.main.SIMULATED_CAMERA is not None:
            return None  # no camera chosen is the user's choice, never a fault
        if self.main.STREAM_SOURCE is not None:
            failure = self.main.STREAM_SOURCE.runtime_failure(self.capture_stderr)
            if failure:
                return failure
        if self.main.live_source_kind(self.args) == "other":
            return self._other_diagnosis_locked(device_present)
        if self.main.STREAM_SOURCE is not None:
            return self._stream_diagnosis_locked()
        facts = self.facts
        steps: list[str] = []
        if facts.get("deskView"):
            steps.append("Quit Desk View: it is holding the iPhone's camera.")
        phone = self.phone_link.snapshot() if self.phone_link else {}
        if phone.get("reachable"):
            steps.append("Close Blackmagic Camera and lock the iPhone: Continuity Camera only starts on a locked phone.")
        if "camera access is denied" in (self.last_error or "") or any("camera access is denied" in line for line in self.capture_stderr):
            return {"code": "camera-permission", "text": "MicroscopeMind Camera isn't allowed to use the camera.",
                    "steps": ["Open System Settings › Privacy & Security › Camera.",
                              "Turn on MicroscopeMind Camera, then press Reconnect."]}
        if not device_present:
            steps += [
                "Wake the iPhone and unlock it once, then lock it and keep it near this Mac.",
                "Check that Wi-Fi and Bluetooth are on, on the same Apple ID as this Mac.",
            ]
            if facts.get("usb") is not True:
                steps.append("Or plug the iPhone into this Mac by USB and tap Trust.")
            steps.append("The bridge resets Continuity Camera by itself while the iPhone is missing.")
            return {"code": "phone-missing", "text": "This Mac doesn't see the iPhone's camera.", "steps": steps,
                    "deskView": bool(facts.get("deskView")), "usb": facts.get("usb")}
        waited = now - self.last_start_at if self.last_start_at else 0.0
        if self.capture_frames == 0 and (not running or waited >= self.args.silent_after):
            steps += [
                "Wake the iPhone, then lock it again (Continuity Camera starts on a locked phone).",
                "Check the iPhone's screen for a Continuity Camera notice: tap Resume if it is paused.",
            ]
            if facts.get("usb") is not True:
                steps.append("Plug the iPhone into this Mac by USB: wired is the steadiest link.")
            heal_at = self._next_heal_at_locked()
            if heal_at is not None:
                steps.append("The bridge asks the iPhone again by itself." if self.main.native_capture_ready()
                             else "The bridge resets Continuity Camera and asks again by itself.")
            steps.append("Or switch to Blackmagic over USB or Wi-Fi in the camera console.")
            return {
                "code": "phone-silent",
                # Words that stay the same while it lasts (a notice is rebuilt only when they change); the
                # numbers are fields of their own.
                "text": "The iPhone is listed but hasn't sent its picture.",
                "askedSecondsAgo": round(waited),
                "nextHealInSeconds": max(0, round(heal_at - now)) if heal_at is not None else None,
                "steps": steps,
                "note": "macOS shows the camera as used by the app that started the studio (for example Claude "
                        "or Terminal): that is this bridge asking.",
                "deskView": bool(facts.get("deskView")),
                "usb": facts.get("usb"),
                "heals": self.silent_heals,
            }
        if state == "stale":
            return {"code": "phone-stalled", "text": "The iPhone's picture stopped. The bridge is asking again.",
                    "steps": steps}
        return {"code": "connecting", "text": "Asking the iPhone for its picture.", "steps": steps}

    def _other_diagnosis_locked(self, device_present: bool) -> dict[str, object]:
        """Another camera without a picture: one this Mac sees by name, or a stream address."""
        stream = self.main.STREAM_SOURCE
        if stream is not None:
            listening = "mode=listener" in stream.url or "listen=1" in stream.url
            return {"code": "other-stream-waiting", "text": "Waiting for the other camera's stream.",
                    "steps": [f"Send the picture from the other device to {stream.connect_to}." if listening else
                              f"Check the address ({stream.redacted}), and that the camera is on and on this Mac's network."]}
        name = getattr(self.args, "other_device", None) or "the camera"
        if "camera access is denied" in (self.last_error or "") or any("camera access is denied" in line for line in self.capture_stderr):
            return {"code": "camera-permission", "text": "MicroscopeMind Camera isn't allowed to use the camera.",
                    "steps": ["Open System Settings › Privacy & Security › Camera.",
                              "Turn on MicroscopeMind Camera, then press Reconnect."]}
        if not device_present:
            return {"code": "other-missing", "text": f"This Mac doesn't see {name}.",
                    "steps": [f"Plug {name} into this Mac, or check its cable, then press Reconnect.",
                              "Or choose another camera under Other camera."]}
        return {"code": "other-silent", "text": f"{name} is connected but hasn't sent its picture.",
                "steps": ["Unplug it and plug it back in, then press Reconnect."]}

    def _stream_diagnosis_locked(self) -> dict[str, object]:
        """Blackmagic without a picture: what the phone link knows, and the one thing to do."""
        phone = self.phone_link.snapshot() if self.phone_link else {"reachable": False, "addresses": {}}
        if phone.get("discoveryIssue") and not phone.get("reachable"):
            return {"code": "camera-discovery", "text": "Hooke could not find cameras on this network.",
                    "steps": [phone["discoveryIssue"]]}
        addresses = phone.get("addresses") or {}
        link = phone.get("link") or ("usb" if addresses.get("usb") else "wifi")
        stopped = bool(self.frame_count and self.capture_frames == 0 and self.last_frame_wall_at)
        if not phone.get("reachable"):
            steps = ["Open Blackmagic Camera on the iPhone and keep it open.",
                     "In its Settings › Network Access, turn on HTTP Server.",
                     "In its Settings › Remote Camera Control, turn Remote Camera Control off."]
            if link == "wifi":
                steps.append("Keep the iPhone on the same Wi-Fi as this Mac.")
            return {"code": "phone-app-unreachable", "text": "Blackmagic Camera isn't reachable.", "steps": steps,
                    "settingsFile": "/api/blackmagic.xml"}
        if phone.get("needsSettings"):
            return {"code": "phone-needs-settings",
                    "text": "Blackmagic Camera needs this Mac's settings file.",
                    "steps": ["Keep Blackmagic Camera open on the iPhone.",
                              "On the camera page, scan the code with the phone's camera and download the file (or AirDrop it).",
                              "In Blackmagic Camera: Settings › Live Stream › Platform › Import Stream Settings, then pick it.",
                              "Choose MicroscopeMind (ObservationUI's saved platform name) and Standard quality. The bridge does the rest."],
                    "settingsFile": "/api/blackmagic.xml"}
        if phone.get("error"):
            return {"code": "phone-stream-failed", "text": "The phone didn't start its stream.", "detail": phone["error"],
                    "steps": ["In Blackmagic Camera's Settings, turn Remote Camera Control off.",
                              "If it still won't start, import the settings file in its Settings › Live Stream › Platform."],
                    "settingsFile": "/api/blackmagic.xml"}
        if phone.get("streaming"):
            return {"code": "stream-not-arriving",
                    "text": "The phone's stream stopped. Asking it again." if stopped else "The phone is streaming; waiting for its picture.",
                    "steps": [f"It streams to {phone.get('server') or 'a server'}; the bridge restarts it if nothing arrives."]}
        return {"code": "stream-starting", "text": "Starting the phone's stream.", "steps": []}

    def cadence_trace(self, window: float = CADENCE_WINDOW_S) -> dict[str, object]:
        """The console's frame-rhythm trace: when each frame of the last `window` seconds arrived (ms before now)."""
        with self.condition:
            now = time.monotonic()
            times = list(self.frame_times)
        return {"windowS": window, "ago": [round(1000.0 * (now - t)) for t in times if now - t <= window],
                "summary": cadence_summary(times, now, window)}

    def start_trial(self, seconds: float = 60.0) -> dict[str, object]:
        """A source check: `seconds` of the live feed measured as the app receives it (frame rate, gaps, restarts),
        with the source and, for Blackmagic, whether focus and exposure were held. One at a time; kept with the
        others so sources can be compared back to back."""
        seconds = max(10.0, min(300.0, float(seconds)))
        with self.condition:
            if self.trial_running:
                return dict(self.trial_running)
            kind = self.main.live_source_kind(self.args)
            phone = self.phone_link.snapshot() if self.phone_link else {}
            self.trial_running = {"id": f"t{len(self.trials) + 1}", "source": kind, "title": SOURCE_TITLES[kind],
                                  "seconds": seconds, "startedAt": wall_timestamp(time.time()),
                                  "locked": (phone.get("autofocus") is False and phone.get("autoExposure") == "Off")
                                  if kind in STREAM_KINDS and phone.get("reachable") else None}
            trial = dict(self.trial_running)
        threading.Thread(target=self._run_trial, args=(trial,), daemon=True, name="source-trial").start()
        return trial

    def _run_trial(self, trial: dict[str, object]) -> None:
        start = time.monotonic()
        restarts = self.restart_count
        arrivals: list[float] = []
        seen = 0.0
        while not self.stop_event.is_set() and time.monotonic() - start < float(trial["seconds"]):
            time.sleep(0.5)
            with self.condition:
                fresh = [t for t in self.frame_times if t > seen and t >= start]
            if fresh:
                arrivals.extend(fresh)
                seen = fresh[-1]
        end = time.monotonic()
        result = {**trial, **cadence_summary(arrivals, end, end - start), "frames": len(arrivals),
                  "restarts": self.restart_count - restarts, "endedAt": wall_timestamp(time.time()),
                  "stoppedEarly": self.stop_event.is_set()}
        result.pop("windowS", None)
        with self.condition:
            self.trials.append(result)
            self.trial_running = None
        if self.args.trials_dir:
            try:
                folder = Path(self.args.trials_dir)
                folder.mkdir(parents=True, exist_ok=True)
                with (folder / "trials.jsonl").open("a") as handle:
                    handle.write(json.dumps(result) + "\n")
            except OSError as exc:
                print(f"Could not keep the source check: {exc}", flush=True)
        print(f"Source check {result['id']} ({result['title']}): {result['fps']} fps, p95 gap {result['p95Ms']} ms, "
              f"{result['gaps']} gaps over {GAP_MS:g} ms", flush=True)

    def event(self, text: str) -> None:
        """A line of the camera's story: printed to the log and kept for the checklist's report."""
        self.events.append({"at": time.strftime("%H:%M:%S"), "text": text})
        print(text, flush=True)

    def doctor_facts(self) -> dict[str, object]:
        """What the checklist (camera_doctor.checklist) is built from: only what the bridge read."""
        phone = self.phone_link.snapshot() if self.phone_link else {}
        with self.condition:
            now = time.monotonic()
            running = bool(self.process and self.process.poll() is None)
            denied = "camera access is denied" in (self.last_error or "") or any(
                "camera access is denied" in line for line in self.capture_stderr)
            try:
                target = tuple(int(n) for n in str(self.args.size).lower().split("x"))
            except ValueError:
                target = None
            facts = {
                "source": self.main.live_source_kind(self.args),
                "footage": self.main.SIMULATED_CAMERA is not None,
                "running": running,
                "streamRuntimeFailure": (self.main.STREAM_SOURCE.runtime_failure(self.capture_stderr)
                                         if self.main.STREAM_SOURCE is not None else None),
                "devicePresent": self._device_present_locked(),
                "captureFrames": self.capture_frames,
                "lastFrameAge": None if not self.last_frame_at else round(now - self.last_frame_at, 2),
                "askedFor": round(now - self.last_start_at, 1) if self.last_start_at else None,
                "fps": cadence_summary(list(self.frame_times), now, CADENCE_WINDOW_S).get("fps"),
                "frameSize": list(self.frame_size) if self.frame_size else None,
                "targetSize": list(target) if target and len(target) == 2 else None,
                "frozenFor": round(now - self.same_since, 1) if self.same_since is not None else 0.0,
                "permissionDenied": denied,
                "deskView": bool(self.facts.get("deskView")),
                "usbCable": self.facts.get("usb"),
                "iphoneNearby": self.main.iphone_nearby(self.last_device_snapshot, self.args.target_device),
                "blackmagicOpen": bool(phone.get("reachable")),
                "readers": self.readers,
                "phone": phone,
                "appCrashedAt": phone.get("appCrashedAt"),
                "link": phone.get("link"),
                "unsafeQualities": unsafe_quality_list(phone.get("app")),
                "otherDevice": getattr(self.args, "other_device", None),
                "otherStream": self.main.STREAM_SOURCE.redacted if self.main.STREAM_SOURCE and getattr(self.args, "other_url", None) else None,
                "otherConnectTo": self.main.STREAM_SOURCE.connect_to if self.main.STREAM_SOURCE and getattr(self.args, "other_url", None) else None,
                "otherListens": bool(self.main.STREAM_SOURCE and getattr(self.args, "other_url", None)
                                     and ("mode=listener" in self.args.other_url or "listen=1" in self.args.other_url)),
                "provenVersions": proven_versions(),
            }
        try:
            facts["loadPerCore"] = round(os.getloadavg()[0] / (os.cpu_count() or 1), 2)
        except OSError:
            pass
        return facts

    def doctor(self) -> dict[str, object]:
        from camera_doctor import checklist, report

        result = checklist(self.doctor_facts())
        events = list(self.events)
        return {**result, "setUp": self.blackmagic_set_up(result), "events": events[-20:], "report": report(result, events)}

    def blackmagic_set_up(self, result: dict) -> bool:
        """Whether the Blackmagic Camera app has been set up on this Mac (its settings imported and chosen, seen once),
        kept beside the source choice. Then the app being closed is only "open it", never the first-time walkthrough
        (live 2026-10-13: closing the app brought the setup back, and reopening it said "set up" again)."""
        if getattr(self, "_set_up", None) is None:
            marker = self._set_up_marker()
            self._set_up = bool(marker and marker.exists())
        if not self._set_up and result.get("source") == "blackmagic":
            ok = {item["id"]: item["ok"] for item in result.get("items", [])}
            if result.get("live") or (ok.get("settings") and ok.get("quality")):
                self._set_up = True
                marker = self._set_up_marker()
                if marker:
                    try:
                        marker.parent.mkdir(parents=True, exist_ok=True)
                        marker.write_text(wall_timestamp(time.time()) or "set up")
                    except OSError:
                        pass
        return self._set_up

    def _set_up_marker(self):
        source_file = getattr(self.args, "source_file", None)
        return Path(source_file).expanduser().with_name("blackmagic-set-up") if source_file else None

    def phone_check(self, start: bool = False) -> dict[str, object]:
        """The Phone check (phone_lab.py): what Blackmagic Camera's API really allows, learned once with the app open.
        Its state for the camera page; `start` begins a run (one at a time). Never on a test bridge (--no-phone)."""
        if not self.phone_allowed():
            return {"running": False, "finished": False, "step": "", "summary": [],
                    "error": "This camera bridge doesn't control a phone (a test bridge)."}
        if start and (self.lab is None or not self.lab.state.get("running")):
            from phone_control import Phone, app_crash_times, find_phone
            from phone_lab import PhoneCheck

            def phone():
                # The address the bridge already reaches first (Bonjour can miss a phone that is there), then a search.
                known = self.phone_link.phone if self.phone_link else None
                found = known or find_phone(prefer="usb" if self.main.stream_addresses(self.args).get("usb") else "wifi")
                return Phone(found.base, found.name, via=found.via, unchecked=True) if found else None

            self.lab = PhoneCheck(phone, app_crash_times)
            link = self.ensure_phone_link()
            if link:
                link.paused = True
            self.event("Phone check started")
            self.lab.start()
            threading.Thread(target=self._after_phone_check, daemon=True).start()
        if self.lab is None:
            return {"running": False, "finished": False, "step": "", "summary": []}
        return dict(self.lab.state)

    def _after_phone_check(self) -> None:
        lab = self.lab
        if lab and lab.thread:
            lab.thread.join()
        if self.phone_link:
            self.phone_link.paused = False
            self.phone_link.source_changed()  # aim and start the stream again, as the source wants
        self.event("Phone check finished" + (f": {lab.state.get('error')}" if lab and lab.state.get("error") else ""))
