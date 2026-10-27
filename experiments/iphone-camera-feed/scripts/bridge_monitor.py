"""The camera bridge's watch over its capture, a mixin of iphone_bridge_server.py's CameraBridge: the reader that
takes each JPEG from the capture's output into the frame cache, and the monitor that, once a second, keeps a capture
running while the phone is there, restarts a stalled one, heals a phone that is listed but silent, resets Continuity
Camera now and then while it is missing, and heals a frozen picture. The live state, and the functions and timings a
test may replace, are the main module's, reached through `main` (bridge_common.ModuleView)."""
from __future__ import annotations

import subprocess
import threading
import time
import zlib

from bridge_common import jpeg_size, wall_timestamp, with_stderr


class MonitorMixin:
    """The capture's reader and monitor, run by CameraBridge (which starts and stops the captures they watch)."""

    def _reader_loop(self, process: subprocess.Popen[bytes]) -> None:
        assert process.stdout is not None
        buffer = bytearray()
        try:
            while not self.stop_event.is_set():
                chunk = process.stdout.read(64 * 1024)
                if not chunk:
                    break
                buffer.extend(chunk)
                if not self._extract_frames(buffer, process):
                    break
        except Exception as exc:
            with self.condition:
                if self.process is process:
                    self.last_error = f"reader error: {exc}"
                self.condition.notify_all()
        finally:
            with self.condition:
                self.condition.notify_all()

    def _extract_frames(self, buffer: bytearray, process: subprocess.Popen[bytes] | None = None) -> bool:
        """Move complete JPEGs from the buffer into the frame cache. Returns False once
        `process` is no longer the current capture: its frames are not served."""
        while True:
            start = buffer.find(b"\xff\xd8")
            if start < 0:
                if len(buffer) > 1024 * 1024:
                    del buffer[:-2]
                return True
            end = buffer.find(b"\xff\xd9", start + 2)
            if end < 0:
                if start > 0:
                    del buffer[:start]
                return True

            frame = bytes(buffer[start : end + 2])
            del buffer[: end + 2]
            with self.condition:
                if process is not None and self.process is not process:
                    return False
                camera = self.main.SIMULATED_CAMERA
                if camera is not None:
                    # An old reader must not seed the new capture's paused frame,
                    # even when its own frame would subsequently be discarded.
                    frame = camera.pass_frame(frame)
                self.latest_frame = frame
                signature = (len(frame), zlib.crc32(frame))
                if signature != self.frame_signature:
                    self.frame_signature, self.same_since = signature, None
                elif self.same_since is None:
                    self.same_since = self.last_frame_at or time.monotonic()
                self.frame_size = jpeg_size(frame) or self.frame_size
                self.frame_count += 1
                self.capture_frames += 1
                self.total_bytes += len(frame)
                self.last_frame_at = time.monotonic()
                self.last_frame_wall_at = time.time()
                self.frame_times.append(self.last_frame_at)
                seq, taken_at = self.frame_count, self.last_frame_wall_at
                if self.capture_frames == 1:
                    self.last_error = ""
                    self.silent_heals = 0
                    print(
                        f"First frame from FFmpeg PID {process.pid if process else '?'} "
                        f"{self.last_frame_at - self.last_start_at:.2f} s after the request",
                        flush=True,
                    )
                self.condition.notify_all()
            # Where this frame came from, for the recording's sidecar (only the bridge knows).
            facts = camera.frame_facts(seq) if camera is not None else {"src": "camera"}
            self._write_recording_frame(frame, seq=seq, taken_at=taken_at, facts=facts)

    def _monitor_loop(self) -> None:
        while not self.stop_event.wait(1.0):
            try:
                self._monitor_tick()
            except Exception as exc:  # the monitor must outlive any one failure
                print(f"Bridge monitor error: {type(exc).__name__}: {exc}", flush=True)

    def _monitor_tick(self) -> None:
        """Once a second: keep a capture running while the phone is there, give a new
        capture `first_frame_timeout` for its first frame and a flowing one `stall_timeout`
        between frames, and while the phone is missing or the capture keeps failing,
        rescan every `rescan_interval` (with a rare, rate-limited Continuity reset)."""
        if self.no_camera():
            return  # no camera chosen: nothing to keep running, scan for or heal
        now = time.monotonic()
        with self.condition:
            if self.manual_ops or self.stop_event.is_set():
                return
            frozen = (self.main.SIMULATED_CAMERA is None and self.main.STREAM_SOURCE is None and self.same_since is not None
                      and now - self.same_since >= self.main.FROZEN_HEAL_S and now - self.last_freeze_heal >= self.main.FROZEN_HEAL_GAP_S)
            if frozen:
                self.last_freeze_heal = now
        if frozen:
            self.event(f"The iPhone's picture froze (identical frames for {now - (self.same_since or now):.0f} s): resetting the camera")
            threading.Thread(target=self.reset_camera, daemon=True, name="freeze-heal").start()
            return
        with self.condition:
            if self.manual_ops or self.stop_event.is_set():
                return
            idle_phone = self.main.SIMULATED_CAMERA is None and self.main.STREAM_SOURCE is None and not (
                self.process and self.process.poll() is None and self.capture_frames)
        if idle_phone:
            self.refresh_facts()
        with self.condition:
            if self.manual_ops or self.stop_event.is_set():
                return
            epoch = self.manual_epoch
            process = self.process
            running = bool(process and process.poll() is None)
            device_present = self._device_present_locked()
            streaming = self._streaming()
            problem = ""
            if running and self.capture_frames == 0:
                # A stream's listener waits for the phone's app as long as it takes: restarting it
                # would close the port the app is calling.
                if not streaming and now - self.last_start_at > self.args.first_frame_timeout:
                    problem = f"no first frame within {self.args.first_frame_timeout:g} s of the capture request"
                    heal_due = self._next_heal_at_locked()
                    if heal_due is not None and now < heal_due:
                        return  # the capture keeps asking; the next heal is not due yet
            elif running:
                if now - self.last_frame_at > self.args.stall_timeout:
                    problem = "camera stream stalled"
            elif process is not None:
                problem = "FFmpeg exited"
                # A listener that ended without a frame (the port taken, a bad URL or passphrase) is
                # started again every rescan_interval, not every second.
                if streaming and self.capture_frames == 0 and now - self.last_start_at < self.args.rescan_interval:
                    return
            else:
                problem = "no capture running"
            if device_present and not problem:
                return
            if problem in {"no capture running"} and device_present:
                scan_due = False  # a plain (re)start; nothing has failed
            else:
                scan_due = now - self.last_scan_at >= self.args.rescan_interval

        if scan_due:
            snapshot = self.main.make_device_snapshot(self.args.target_device)
            with self.condition:
                if not self._epoch_current_locked(epoch):
                    return
                self.last_scan_at = time.monotonic()
                self._apply_snapshot_locked(snapshot)
                device_present = bool(snapshot.get("ok"))

        if not device_present:
            self._while_missing(epoch)
            return
        if not problem:
            return  # the rescan found the phone and the capture is fine
        if problem.startswith("no first frame") and not streaming:
            self._heal_silent_phone(epoch, process, problem)
            return

        message = problem
        if problem == "FFmpeg exited" and process is not None:
            message = f"FFmpeg exited with code {process.returncode}"
        with self.condition:
            if not self._epoch_current_locked(epoch):
                return
            if problem != "no capture running":
                error = with_stderr(message, self.capture_stderr)
                if streaming:
                    error = (f"The stream stopped ({error}); listening for it again" if self.capture_frames
                             else f"The stream could not be read ({error})")
                self.last_error = error
        print(f"Restarting bridge: {message}", flush=True)
        self._launch(problem, replace=True, epoch=epoch, expected=process)

    def _heal_silent_phone(self, epoch: int, process: object, problem: str) -> None:
        """The phone is listed but has sent nothing for `first_frame_timeout`. Asking again as is was what the
        bridge did before 2026-10-11, and the phone never answered (19 requests in 4 minutes); resetting
        Continuity Camera brought it back at once. So: stop the capture, reset Continuity, wait for the phone
        to be listed again, ask again. Heals back off (30 s doubling to 5 min) and start over at the first
        frame; between them the capture is left asking, never replaced."""
        with self.lifecycle:
            with self.condition:
                if not self._epoch_current_locked(epoch) or self.process is not process:
                    return
                self.silent_heals += 1
                heal = self.silent_heals
                waited = time.monotonic() - self.last_start_at
            self._stop_capture("phone listed but silent")
        if self.main.native_capture_ready():
            # The app wakes the phone itself; a Continuity reset only ended sessions (2026-10-11). Ask again.
            self.event(f"{self.args.target_device} is listed but sent no frame in {waited:.0f} s: asking again "
                       f"(heal {heal} of this episode)")
            with self.condition:
                self.last_heal = {"at": wall_timestamp(time.time()), "heal": heal, "waitedSeconds": round(waited, 1),
                                  "reset": False, "reason": problem}
                self.last_error = f"The iPhone was listed but sent nothing for {waited:.0f} s: the bridge asked again"
            self._launch("asking the iPhone again", replace=False, epoch=epoch)
            return
        print(
            f"{self.args.target_device} is listed but sent no frame in {waited:.0f} s: resetting Continuity "
            f"Camera and asking again (heal {heal} of this episode)",
            flush=True,
        )
        reset = self.reset_continuity_agent_if_allowed()
        with self.condition:
            self.last_heal = {"at": wall_timestamp(time.time()), "heal": heal, "waitedSeconds": round(waited, 1),
                              "reset": bool(reset.get("attempted")), "reason": problem}
            self.last_error = (f"The iPhone was listed but sent nothing for {waited:.0f} s: Continuity Camera was "
                               "reset and the bridge asked again")
        if reset.get("attempted"):
            time.sleep(self.main.CONTINUITY_SETTLE_S)
            snapshot = self.wait_for_target_device(self.args.device_wait_timeout)
            if not snapshot.get("ok"):
                return  # the monitor's missing-phone path takes over
        self._launch("asking again after a Continuity reset", replace=False, epoch=epoch)

    def _while_missing(self, epoch: int) -> None:
        """The phone is not in the device list: no capture, and now and then a Continuity
        reset, which is what brings back a phone macOS stopped advertising. Automatic resets
        wait until the phone has been gone `auto_reset_after`, keep `auto_reset_min_interval`
        from any earlier reset (manual or automatic), and back off to once per 5 minutes."""
        with self.condition:
            had_capture = self.process is not None
        if had_capture:
            with self.lifecycle:
                with self.condition:
                    if not self._epoch_current_locked(epoch):
                        return
                self._stop_capture("device missing")
        now = time.monotonic()
        with self.condition:
            if not self._epoch_current_locked(epoch):
                return
            self.last_error = self._missing_message()
            missing_for = now - self.missing_since if self.missing_since else 0.0
            gap = min(300.0, self.args.auto_reset_min_interval * (2 ** max(0, self.auto_reset_count - 1)))
            since_reset = now - self.last_agent_reset_at if self.last_agent_reset_at else None
            due = missing_for >= self.args.auto_reset_after and (since_reset is None or since_reset >= gap)
            if due:
                self.auto_reset_count += 1
        if due:
            print(
                f"{self.args.target_device} missing for {missing_for:.0f} s: resetting Continuity Camera "
                f"(automatic reset {self.auto_reset_count} of this outage)",
                flush=True,
            )
            self.reset_continuity_agent_if_allowed()
