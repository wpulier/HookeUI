"""The camera recording and its sidecars (docs/datasets.md), a mixin of the camera bridge (iphone_bridge_server.py's
CameraBridge): one recorder at a time takes the camera's frames from a queue on its own thread, every caller that asked
for it is an owner, and each recording's sidecar is its clock. The bridge sets up the recorder's state in its own
__init__ and gives the mixin `main`, a view of its live state (bridge_common.ModuleView)."""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from bridge_common import ROOT_DIR, STDERR_TAIL_LINES, drain_stderr, ffmpeg_path, h264_encoder, wall_timestamp, with_stderr


# The camera recording's owners: whoever starts it names itself (`?owner=studio`, `demo`), and
# a stop ends only that owner's part. Callers that do not say are "manual".
DEFAULT_RECORDING_OWNER = "manual"
# On shutdown, how long the bridge waits for a recording's file to finish before it exits
# anyway (the file then completes without it). Short: `make stop` escalates after a few seconds.
SHUTDOWN_RECORDING_WAIT_S = 5.0
# How far behind the recorder may fall. The live picture never waits on the recording: the recorder takes its frames
# from a buffer on its own thread, and only frames that have waited longer than this are dropped (counted, and seen
# in the sidecar as missing seqs). Demo night, 2026-10-12: the encoder at 145% CPU.
RECORDING_BEHIND_S = 3.0
# Every recording gets a sidecar beside it, `<name>.frames.jsonl`: a header, one line per frame the
# recorder took (its index in the video, the bridge's frame number, when the bridge took it, where it
# came from) and event lines (a feed switch, a seek, a capture restart). The video's own timestamps
# are a steady `--fps`, not times: the sidecar is the clock. Written by the bridge, because only the
# bridge knows whether a frame came from the camera or from test footage (docs/datasets.md).
SIDECAR_SUFFIX = ".frames.jsonl"
SIDECAR_SCHEMA = 1
# Flushed at every line (a small write a frame): the recorder is its own process and finishes the frames
# it was given even when the bridge dies, so a bridge killed outright must lose at most the frame in
# flight, not a second of lines (2026-10-06 sim: a killed bridge left 28 frames without a line).
SIDECAR_FLUSH_S = 0.0


def sidecar_path(video: Path) -> Path:
    """The per-frame sidecar of a recording: `X.mp4` -> `X.frames.jsonl`."""
    return video.with_name(video.stem + SIDECAR_SUFFIX)


def count_video_frames(video: Path, timeout_s: float = 120.0) -> int | None:
    """How many frames a video file holds (ffprobe counts its packets), or None when it cannot tell."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not video.exists():
        return None
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-count_packets", "-select_streams", "v:0",
             "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, check=False, timeout=timeout_s,
        )
        return int(result.stdout.strip().splitlines()[0]) if result.returncode == 0 and result.stdout.strip() else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def recording_owner(query: dict[str, list[str]]) -> str:
    """Who is starting or stopping the camera recording (`?owner=`): a short name."""
    raw = query.get("owner", [""])[0]
    owner = "".join(char for char in raw if char.isalnum() or char in {"-", "_"})[:40]
    return owner or DEFAULT_RECORDING_OWNER


class RecordingMixin:
    """The recorder's half of CameraBridge (its state is made in CameraBridge.__init__)."""

    def recording_command(self, path: Path) -> list[str]:
        """The recorder: the camera's JPEG frames on stdin, H.264 out. It writes fragmented
        MP4, a fragment a second, so the file is playable however the recorder ends (killed,
        the bridge stopped, a full disk) and loses at most the last second; once it has exited,
        `remux_command` rewrites it as a plain MP4 with its index at the front."""
        return [
            ffmpeg_path(),
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            "-f",
            "mjpeg",
            "-framerate",
            str(self.args.fps),
            # The format/rate are known; the default stream analysis otherwise
            # holds the first ~50 frames before producing any recoverable video.
            "-analyzeduration",
            "1",
            "-i",
            "pipe:0",
            "-an",
            # libx264's zerolatency (VideoToolbox's realtime): lookahead/B-frame queues would add loss on top of the
            # one-second fragment when the encoder dies. Encode each frame as it arrives.
            *h264_encoder()["args"],
            "-pix_fmt",
            "yuv420p",
            "-f",
            "mp4",
            "-frag_duration",
            "1000000",
            "-movflags",
            "+empty_moov+default_base_moof",
            # A held view compresses so well that fragments never fill AVIO's
            # buffer. Publish them now, not only when recording stops.
            "-flush_packets",
            "1",
            str(path),
        ]

    def remux_command(self, source: Path, target: Path) -> list[str]:
        # A copy, no re-encode: 0.7 s for a 38-minute recording (305 MB) on an idle disk.
        return [
            ffmpeg_path(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            str(target),
        ]

    def start_recording(self, owner: str = DEFAULT_RECORDING_OWNER) -> dict[str, object]:
        """Record the camera for `owner`. When a recording is already running, `owner` joins
        it ("alreadyRecording") and it keeps running until every owner has stopped it."""
        with self.recording_lock:
            process = self.recording_process
            if process and process.poll() is None:
                self.recording_owners.add(owner)
                return {"ok": True, "alreadyRecording": True, **self.recording_status_locked()}
            if process:  # it exited by itself since the last frame: finish its file first
                self._end_recording_locked(f"recording ffmpeg exited {process.returncode}")

            # Keep recordings made from test footage apart from real captures.
            recording_dir = Path(self.args.recording_dir or ROOT_DIR / "output" / ("sim-recordings" if self.main.SIMULATED_CAMERA else "recordings"))
            recording_dir.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            safe_device = re.sub("-+", "-", "".join(
                char if char.isalnum() or char in {"-", "_"} else "-"
                for char in self.args.target_device
            )).strip("-")  # "iPhoneWild-Camera", "SRT-stream-9710"
            path = recording_dir / f"{safe_device}-{timestamp}.mp4"
            taken = {str(entry["path"]) for entry in self.recording_finishing}
            suffix = 2
            while path.exists() or str(path) in taken:
                path = recording_dir / f"{safe_device}-{timestamp}-{suffix}.mp4"
                suffix += 1
            try:
                process = subprocess.Popen(
                    self.recording_command(path),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
            except Exception as exc:
                self.recording_error = f"recording ffmpeg did not start: {exc}"
                return {"ok": False, "alreadyRecording": False, **self.recording_status_locked()}
            self.recording_process = process
            self.recording_owners = {owner}
            self.recording_stderr = deque(maxlen=STDERR_TAIL_LINES)
            threading.Thread(
                target=drain_stderr,
                args=(process, self.recording_stderr, "recording ffmpeg"),
                daemon=True,
            ).start()
            self.recording_path = path
            self.recording_started_at = time.monotonic()
            self.recording_frames = 0
            self.recording_bytes = 0
            self.recording_dropped = 0
            self.recording_error = ""
            self._open_sidecar_locked(path, owner)
            print(f"Started recording to {path} for {owner}", flush=True)
            return {"ok": True, "alreadyRecording": False, **self.recording_status_locked()}

    def _open_sidecar_locked(self, video: Path, owner: str) -> None:
        """Start the recording's sidecar with its header. A sidecar that cannot be written is
        reported (`sidecarError`) and never stops the video."""
        self.recording_sidecar, self.recording_sidecar_path, self.recording_sidecar_error = None, sidecar_path(video), ""
        now = time.time()
        header = {
            "type": "header",
            "schema": SIDECAR_SCHEMA,
            "video": video.name,
            "bootId": self.boot_id,
            "owner": owner,
            "startedAt": wall_timestamp(now),
            "startedAtEpoch": round(now, 4),
            "targetDevice": self.args.target_device,
            "source": {**self.main.STREAM_SOURCE.describe(), "link": getattr(self.args, "link", None)} if self.main.STREAM_SOURCE else {"kind": "device", "name": self.args.target_device},
            # What the bridge asked the camera for, not what it measured.
            "requested": {"size": self.args.size, "fps": self.args.fps, "pixelFormat": self.args.pixel_format,
                          "jpegQuality": self.args.jpeg_quality},
            "encoder": {**{k: v for k, v in h264_encoder().items() if k != "args"}, "frameRate": self.args.fps},
            "fields": {
                "i": "the frame's index in the video (0-based)",
                "seq": "the bridge's frame number in this bootId (the X-Frame-Seq of /iphone.mjpeg)",
                "bridgeAt": "epoch seconds when the bridge took the frame from FFmpeg (not when the camera captured it)",
                "src": "camera or footage",
                "footageFrameEstimate": "for footage: the recording's frame index, estimated from the capture start",
            },
            "note": "The video's own timestamps are a steady frameRate, not times: use bridgeAt.",
        }
        try:
            self.recording_sidecar = open(self.recording_sidecar_path, "a", encoding="utf-8")
            self._sidecar_write_locked(header, flush=True)
        except OSError as exc:
            self._sidecar_failed_locked(exc)

    def _sidecar_write_locked(self, line: dict, flush: bool = False) -> None:
        sidecar = self.recording_sidecar
        if sidecar is None:
            return
        try:
            sidecar.write(json.dumps(line, separators=(",", ":")) + "\n")
            now = time.monotonic()
            if flush or now - self.recording_sidecar_flushed_at >= SIDECAR_FLUSH_S:
                sidecar.flush()
                self.recording_sidecar_flushed_at = now
        except (OSError, ValueError) as exc:
            self._sidecar_failed_locked(exc)

    def _sidecar_failed_locked(self, exc: BaseException) -> None:
        self.recording_sidecar_error = f"sidecar not written: {exc}"
        print(f"Recording sidecar {self.recording_sidecar_path}: {exc}", flush=True)
        sidecar, self.recording_sidecar = self.recording_sidecar, None
        if sidecar is not None:
            with contextlib.suppress(OSError, ValueError):
                sidecar.close()

    def recording_event(self, event: str, **details: object) -> None:
        """Note in the running recording's sidecar something that changes what its frames are
        (a feed switch, a seek, a capture restart). Nothing happens when nothing records. Queued in order with
        the frames, so a caller holding the bridge's lock never waits on the recorder."""
        if self.recording_process is None:
            return
        with self.recording_ready:
            self.recording_queue.append(("event", event, details, time.time()))
            self.recording_ready.notify()

    def _write_recording_event(self, event: str, details: dict, now: float) -> None:
        with self.recording_lock:
            if self.recording_process is None or self.recording_sidecar is None:
                return
            self._sidecar_write_locked({"type": "event", "event": event, "at": round(now, 4),
                                        "afterFrame": self.recording_frames - 1, **details}, flush=True)

    def stop_recording(self, owner: str = DEFAULT_RECORDING_OWNER, force: bool = False) -> dict[str, object]:
        """End `owner`'s part in the recording. The recorder stops only when no owner is left,
        or with `force` (2026-10-06: the studio's Stop ended the camera recording `make
        demo-start` had started, 8 minutes early). Returns at once: the recorder finishes its
        file on its own thread, and the status says "finishing" until the file is complete."""
        self._drain_recording_queue()
        with self.recording_lock:
            process = self.recording_process
            if not process:
                return {"ok": True, "wasRecording": False, "stopped": False, **self.recording_status_locked()}
            if not force and process.poll() is None:
                if owner not in self.recording_owners:
                    return {
                        "ok": True,
                        "wasRecording": True,
                        "stopped": False,
                        "note": f"{owner} did not start this recording; it goes on for {', '.join(sorted(self.recording_owners))}",
                        **self.recording_status_locked(),
                    }
                self.recording_owners.discard(owner)
                if self.recording_owners:
                    print(f"Recording {self.recording_path} goes on for {', '.join(sorted(self.recording_owners))} ({owner} stopped)", flush=True)
                    return {
                        "ok": True,
                        "wasRecording": True,
                        "stopped": False,
                        "stillRecordingFor": sorted(self.recording_owners),
                        **self.recording_status_locked(),
                    }
            exited = process.poll()
            entry = self._end_recording_locked(f"recording ffmpeg exited {exited}" if exited is not None else "")
            print(
                f"Stopping recording {entry['path']} ({entry['frames']} frames) for {owner}"
                f"{' (forced)' if force else ''}; its file is being finished",
                flush=True,
            )
            return {"ok": exited in {0, None}, "wasRecording": True, "stopped": True, **self.recording_status_locked()}

    def _end_recording_locked(self, error: str = "") -> dict[str, object]:
        """Take the recorder off the frames and hand it to a thread that lets it finish its
        file. Under `recording_lock`, and waits for nothing: the camera's frame reader also
        takes this lock, and must never wait for a file to close (the monitor would take that
        as a stalled camera and restart it)."""
        process = self.recording_process
        assert process is not None
        now = time.monotonic()
        entry: dict[str, object] = {
            "path": str(self.recording_path),
            "sidecar": str(self.recording_sidecar_path) if self.recording_sidecar_path else None,
            "pid": process.pid,
            "frames": self.recording_frames,
            "bytes": self.recording_bytes,
            "dropped": self.recording_dropped,
            "durationSeconds": round(now - self.recording_started_at, 2) if self.recording_started_at else 0.0,
            "stage": "encoder",
            "_endedAt": now,
        }
        self.recording_process = None
        self.recording_owners = set()
        if error:
            self.recording_error = with_stderr(error, self.recording_stderr)
        self._sidecar_write_locked({"type": "end", "at": round(time.time(), 4), "frames": self.recording_frames,
                                    "reason": self.recording_error if error else "stopped"}, flush=True)
        sidecar, self.recording_sidecar = self.recording_sidecar, None
        if sidecar is not None:
            with contextlib.suppress(OSError, ValueError):
                sidecar.close()
        self.recording_finishing.append(entry)
        thread = threading.Thread(
            target=self._finish_recording,
            args=(process, entry, self.recording_stderr),
            name=f"finish-recording-{process.pid}",
            daemon=True,
        )
        self.recording_finishers = [t for t in self.recording_finishers if t.is_alive()] + [thread]
        thread.start()
        return entry

    def _finish_recording(self, process: subprocess.Popen[bytes], entry: dict[str, object], stderr_tail: deque) -> None:
        """Let an ended recorder write the rest of its file, however long that takes: it is
        never terminated or killed (2026-10-06: a limit of 8 s, then SIGTERM and SIGKILL,
        killed FFmpeg while it wrote out a 38-minute recording on a busy disk, and the MP4 had
        no index). Then give the file its index at the front."""
        path = Path(str(entry["path"]))
        if process.stdin:
            try:
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        returncode = process.wait()
        time.sleep(0.05)  # let the stderr reader take FFmpeg's last words
        error = "" if returncode == 0 else with_stderr(f"recording ffmpeg exited {returncode}", stderr_tail)
        with self.recording_lock:
            entry["stage"] = "index"
        index, remux_note = "none", ""
        if path.exists() and path.stat().st_size:
            index, remux_note = self._remux_faststart(path)
        self._sidecar_trailer(entry, path, returncode, index)
        with self.recording_lock:
            if entry in self.recording_finishing:
                self.recording_finishing.remove(entry)
            finished_in = time.monotonic() - float(entry["_endedAt"])  # type: ignore[arg-type]
            self.recording_last_saved = {
                "path": str(path),
                "sidecar": entry.get("sidecar"),
                "ok": returncode == 0 and index != "none",
                "frames": entry["frames"],
                "durationSeconds": entry["durationSeconds"],
                "bytes": path.stat().st_size if path.exists() else 0,
                # "front": a plain MP4, index first; "fragmented": playable as it was recorded
                "index": index,
                "finishSeconds": round(finished_in, 2),
                "error": "; ".join(part for part in (error, remux_note) if part),
            }
        print(
            f"Saved recording {path} ({entry['frames']} frames, index {index}) "
            f"{finished_in:.1f} s after it was stopped{': ' + error if error else ''}{'; ' + remux_note if remux_note else ''}",
            flush=True,
        )

    def _remux_faststart(self, path: Path) -> tuple[str, str]:
        """Rewrite a finished fragmented recording as a plain MP4 with its index at the front,
        in place. On any failure the fragmented file is kept as it is (it plays)."""
        temp = path.with_name(f"{path.stem}.faststart.tmp{path.suffix}")
        try:
            result = subprocess.run(
                self.remux_command(path, temp),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
            )
            last = result.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] if result.stderr else []
            # A copy keeps every frame: much smaller means the remux lost some.
            if result.returncode != 0 or not temp.exists() or temp.stat().st_size < 0.95 * path.stat().st_size:
                temp.unlink(missing_ok=True)
                return "fragmented", f"kept the fragmented file (remux exited {result.returncode}{': ' + last[0] if last else ''})"
            os.replace(temp, path)
            return "front", ""
        except Exception as exc:
            with contextlib.suppress(OSError):
                temp.unlink(missing_ok=True)
            return "fragmented", f"kept the fragmented file (remux failed: {exc})"

    def _sidecar_trailer(self, entry: dict[str, object], video: Path, returncode: int, index: str) -> None:
        """After the file is complete: how many frames it really holds, so a reader can check
        the sidecar against it (an encoder cut off loses its last frames)."""
        sidecar = entry.get("sidecar")
        if not sidecar or not Path(str(sidecar)).exists():
            return
        trailer = {"type": "saved", "at": round(time.time(), 4), "ok": returncode == 0 and index != "none",
                   "index": index, "framesSent": entry["frames"], "fileFrames": count_video_frames(video)}
        try:
            with open(str(sidecar), "a", encoding="utf-8") as handle:
                handle.write(json.dumps(trailer, separators=(",", ":")) + "\n")
        except OSError as exc:
            print(f"Recording sidecar {sidecar}: trailer not written: {exc}", flush=True)

    def recording_status(self) -> dict[str, object]:
        with self.recording_lock:
            return self.recording_status_locked()

    def recording_status_locked(self) -> dict[str, object]:
        process = self.recording_process
        if process and process.poll() is not None:  # it exited by itself: its file is finished now
            self._end_recording_locked(f"recording ffmpeg exited {process.returncode}")
            process = None
        running = process is not None
        now = time.monotonic()
        duration = now - self.recording_started_at if running and self.recording_started_at else 0.0
        finishing = [
            {**{k: v for k, v in entry.items() if not k.startswith("_")}, "finishingSeconds": round(now - float(entry["_endedAt"]), 1)}  # type: ignore[arg-type]
            for entry in self.recording_finishing
        ]
        return {
            # "recording": taking frames; "finishing": stopped, a file is still being written
            # (see `finishing`); "stopped": every file is complete.
            "state": "recording" if running else "finishing" if finishing else "stopped",
            "running": running,
            "owners": sorted(self.recording_owners) if running else [],
            "path": str(self.recording_path) if self.recording_path else None,
            "sidecar": str(self.recording_sidecar_path) if self.recording_sidecar_path else None,
            "durationSeconds": round(duration, 2),
            "frames": self.recording_frames,
            "bytes": self.recording_bytes,
            "error": self.recording_error,
            "sidecarError": self.recording_sidecar_error,
            "finishing": finishing,
            "lastSaved": self.recording_last_saved,
        }

    def _write_recording_frame(self, frame: bytes, seq: int | None = None, taken_at: float | None = None,
                               facts: dict[str, object] | None = None) -> None:
        """Hand the recorder a frame (the reader's thread: never waits). A frame that has waited past
        RECORDING_BEHIND_S is dropped and counted; the live picture is never held back by the recording."""
        if self.recording_process is None:
            return
        now = time.monotonic()
        with self.recording_ready:
            self.recording_queue.append(("frame", now, frame, seq, taken_at, facts))
            dropped = 0
            while self.recording_queue and self.recording_queue[0][0] == "frame" and now - self.recording_queue[0][1] > RECORDING_BEHIND_S:
                self.recording_queue.popleft()
                dropped += 1
            if dropped:
                self.recording_dropped += dropped
                if not self.recording_behind:
                    self.recording_behind = True
                    self.recording_queue.append(("event", "recording-behind",
                                                 {"behindS": RECORDING_BEHIND_S, "note": "frames older than this dropped"}, time.time()))
            elif self.recording_behind:
                oldest = next((item[1] for item in self.recording_queue if item[0] == "frame"), now)
                if now - oldest < RECORDING_BEHIND_S / 3:
                    self.recording_behind = False
            self.recording_ready.notify()

    def _recording_writer_loop(self) -> None:
        """The recorder's own thread: writes queued frames and events to the recording, in order."""
        while True:  # past the bridge's stop it finishes what is queued, then ends
            with self.recording_ready:
                while not self.recording_queue and not self.stop_event.is_set():
                    self.recording_ready.wait(0.5)
                if not self.recording_queue:
                    return
                item = self.recording_queue.popleft()
                self.recording_ready.notify_all()  # a drain waiting for an empty queue
            if item[0] == "frame":
                self._record_frame_now(*item[2:])
            else:
                self._write_recording_event(item[1], item[2], item[3])

    def _drain_recording_queue(self, timeout: float = 0.5) -> None:
        """Let the recorder write what is waiting before a recording is ended (its last frames are kept)."""
        deadline = time.monotonic() + timeout
        with self.recording_ready:
            while self.recording_queue and time.monotonic() < deadline:
                self.recording_ready.wait(max(0.01, deadline - time.monotonic()))

    def _record_frame_now(self, frame: bytes, seq: int | None = None, taken_at: float | None = None,
                          facts: dict[str, object] | None = None) -> None:
        """Give the recorder a frame, and note it in the sidecar: its index in the video, the
        bridge's frame number `seq`, when the bridge took it, and where it came from."""
        with self.recording_lock:
            process = self.recording_process
            if not process or not process.stdin:
                return
            if process.poll() is not None:
                self._end_recording_locked(f"recording ffmpeg exited {process.returncode}")
                return
            try:
                process.stdin.write(frame)
            except (OSError, ValueError) as exc:
                self._end_recording_locked(f"recording pipe closed: {exc}")
                return
            index = self.recording_frames
            self.recording_frames += 1
            self.recording_bytes += len(frame)
            if self.recording_sidecar is not None:
                line: dict[str, object] = {"i": index, "seq": seq,
                                           "bridgeAt": round(taken_at if taken_at is not None else time.time(), 4)}
                line.update(facts or {"src": "unknown"})
                self._sidecar_write_locked(line)

    def _finish_recordings_for_shutdown(self, wait_s: float = SHUTDOWN_RECORDING_WAIT_S) -> None:
        """End the recording and give its file a moment to finish. Whatever is still being
        written after that completes on its own: FFmpeg keeps writing after the bridge exits
        (`make stop` matches only capture FFmpegs) and the fragmented file plays as it is."""
        self._drain_recording_queue()
        with self.recording_lock:
            if self.recording_process:
                self._end_recording_locked()
            finishers = list(self.recording_finishers)
        deadline = time.monotonic() + wait_s
        for thread in finishers:
            thread.join(max(0.0, deadline - time.monotonic()))
        with self.recording_lock:
            for entry in self.recording_finishing:
                print(f"Recording {entry['path']} is still being finished by FFmpeg PID {entry['pid']}; it completes on its own", flush=True)
