"""A dataset collected while the user records (docs/datasets.md, ADR 0009; Record is the switch).

Record is the only edge. On: a folder `ds-YYYYMMDD-HHMMSS/` is made and everything that
happens goes into it as it happens: every logged record, the day's journal events, votes, scale
changes, the scene when it changes, what the hub measured about each frame, the pictures that were
kept or shown, and the camera's video with the bridge's per-frame sidecar (hard-linked, so the
dataset keeps them whoever deletes the originals). Off: the summary is finished. Nothing a model or
a rule judges ever removes anything from it.

The state lives in the dataset itself, not in the day: midnight and "start today afresh" never touch
it, and after any restart (a crash, `make start`, the bridge restarting) the same dataset carries on,
with the gap written down and a new video part. Only the user's Stop ends it.

The Data light beside Record (checks.py) is this module's check that all of it is really being saved,
and, after a stop, that it was saved whole.

The taps only put an item on the writer's queue (writer.py); the video is reconciled once a second
against the bridge's health (its recording's owners), so asking the bridge, retrying after a failure
and linking a new part all happen here, off the frame path. Which frames were the camera's and which
were not is the bridge's to say, in its sidecar; this module never reads it.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import os
import shutil
import time
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from . import manifest, store
from .checks import Light
from .manifest import folder_bytes
from .writer import QUEUE_LIMIT, Writer

OWNER = "dataset"  # the bridge's recording owner (POST /api/record/start?owner=dataset)
RETRY_S = 10.0  # after the bridge refused or failed to record, ask again this much later
SETTLE_S = 3.0  # the bridge's health (read once a second) shows a request within this
WIRE_EVERY_S = 5.0  # the studio's Data light: size and state, this often while on
SUMMARY_EVERY_S = 30.0  # dataset.json rewritten with the counts this often while on
DISK_EVERY_S = 30.0
DISK_LOW_BYTES = 2 * 1024 ** 3  # under this free on the datasets' disk the Data light warns


def dataset_id(epoch: float) -> str:
    return "ds-" + time.strftime("%Y%m%d-%H%M%S", time.localtime(epoch))


async def post_bridge(bridge_url: str, path: str) -> dict:
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.post(f"{bridge_url.rstrip('/')}{path}")
        response.raise_for_status()
        return response.json()


class DatasetCollector:
    def __init__(self, root: Path, *, bridge_health: Callable[[], dict | None], bridge_url: str = "",
                 broadcast: Callable[[dict], None] = lambda message: None,
                 snapshot: Callable[[], list] | None = None,
                 scene: Callable[[], tuple[int, dict]] | None = None,
                 preroll: Callable[[], list] | None = None,
                 sources: Callable[[], dict[str, Path]] | None = None,
                 about: Callable[[], dict] | None = None,
                 rig: Callable[[], dict] | None = None,
                 log: Callable[[dict], None] | None = None,
                 post: Callable[[str], Awaitable[dict]] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.root = Path(root)
        self.bridge_health = bridge_health  # the bridge's last /api/health (FrameSource polls it each second)
        self.broadcast = broadcast
        self.snapshot = snapshot  # App.snapshot: what a studio is sent, kept at each start and resume
        self.scene = scene  # () -> (version, scene wire)
        self.preroll = preroll  # () -> the last minute's ClipFrames (clip.py), kept at the start
        self.sources = sources  # () -> {name: path} of the logs a dataset can be cut back out of
        self.about = about  # () -> the app's build and edition
        self.rig = rig  # () -> the microscope facts now, each with its source (rig.at_start)
        self.log = log
        self.post = post or (lambda path: post_bridge(bridge_url, path))
        self.clock = clock
        self.folder: Path | None = None
        self.summary: dict = {}
        self.writer: Writer | None = None
        self.closing: list[Writer] = []  # writers finishing after a stop
        # Stopped datasets whose last video part could not be linked (another disk): copied once the
        # bridge has finished the file. (folder, summary, its writer)
        self.unkept: list[tuple[Path, dict, Writer]] = []
        self.run = 0
        self.dropped = 0
        self._dropped_noted = 0
        self.video_state = "off"  # off | starting | recording | waiting | error
        self.video_error = ""
        self.resumed_after: float | None = None
        self.disk_free: int | None = None
        self.bytes = 0
        self._retry_at = 0.0
        self._requested_at = -SETTLE_S
        self._last_boot: str | None = None
        self._scene_version: int | None = None
        self._wire_at = 0.0
        self._summary_at = 0.0
        self._disk_at = -DISK_EVERY_S
        self._last_wire: dict | None = None
        self._kick: asyncio.Event | None = None
        self.light = Light()  # the Data light: is it really being saved (checks.py)
        self.last_frame: tuple[int | None, str | None] = (None, None)  # the newest frame's (seq, boot)
        self.screens = 0  # the studio's own recordings kept in the dataset being collected

    # --- what the studio sees -------------------------------------------------------------------
    @property
    def on(self) -> bool:
        return self.writer is not None

    def wire(self) -> dict:
        parts = self.summary.get("video", {}).get("parts", []) if self.on else []
        return {"type": "dataset", "on": self.on, "id": self.summary.get("id") if self.on else None,
                "name": self.summary.get("name", "") if self.on else "",
                "startedAt": self.summary.get("startedAtEpoch") if self.on else None,
                "video": {"state": self.video_state, "parts": len(parts), "error": self.video_error},
                "bytes": self.bytes if self.on else 0, "dropped": self.dropped if self.on else 0,
                "resumedAfter": self.resumed_after if self.on else None,
                "diskLow": self.disk_low, "diskFree": self.disk_free,
                "error": (self.writer.error if self.writer is not None else ""),
                "check": self.light.wire(self.on, self.clock())}

    @property
    def disk_low(self) -> bool:
        return self.disk_free is not None and self.disk_free < DISK_LOW_BYTES

    def _publish(self, force: bool = False) -> None:
        wire = self.wire()
        if force or wire != self._last_wire:
            self._last_wire = wire
            self._wire_at = self.clock()
            self.broadcast(wire)

    # --- the switch --------------------------------------------------------------------------
    def start(self) -> dict:
        """The user pressed Record: a new dataset, collecting from now."""
        if self.on:
            return self.wire()
        now = self.clock()
        self.root.mkdir(parents=True, exist_ok=True)
        base = dataset_id(now)
        folder, n = self.root / base, 2
        while folder.exists():
            folder, n = self.root / f"{base}-{n}", n + 1
        folder.mkdir()
        self.folder, self.run = folder, 1
        self.dropped = self._dropped_noted = 0
        self.resumed_after = None
        self.summary = {
            "schema": manifest.SCHEMA, "kind": "microscopemind-dataset", "id": folder.name, "name": "",
            "status": "collecting", "startedAt": manifest.iso(now), "startedAtEpoch": round(now, 3),
            "stoppedAt": None, "stoppedAtEpoch": None, "endedBy": None, "reconstructed": False,
            "edges": "Started and stopped only by the user's Record (its Stop); nothing inside was removed.",
            "format": "docs/datasets.md",
            "runs": [{"n": 1, "startedAt": manifest.iso(now), "startedAtEpoch": round(now, 3), "endedAtEpoch": None}],
            "gaps": [], "app": self._about(), "video": {"parts": []}, "sourceLogs": [], "rig": self._rig(),
            "rigEnteredAfter": [],
            "counts": {},
        }
        self.writer = Writer(folder)
        self.screens = 0
        self.light.started(folder.name, now)
        self._dataset_event("started")
        self._keep_state("start")
        self._keep_preroll()
        self._note_sources("start")
        self._save_summary()
        self.video_state, self.video_error, self._retry_at = "starting", "", 0.0
        self._log({"kind": "dataset", "event": "start", "id": folder.name})
        self._publish(force=True)
        self._wake()
        return self.wire()

    def stop(self) -> dict:
        """The user stopped Record: the dataset is complete (the Data light checks it was saved whole)."""
        if not self.on:
            return self.wire()
        now = self.clock()
        self._dataset_event("stopped", by="user")
        self._note_sources("stop")
        self.summary.update(status="complete", stoppedAt=manifest.iso(now), stoppedAtEpoch=round(now, 3), endedBy="user")
        self.summary["counts"] = self._counts()  # the writer puts the exact numbers in as it writes it
        if self.summary.get("runs"):
            self.summary["runs"][-1]["endedAtEpoch"] = round(now, 3)
        self._save_summary()
        writer, self.writer = self.writer, None
        writer.close(timeout=0)  # writes everything put so far, then stops; nothing here waits for it
        self.closing = [w for w in self.closing if not w.closed] + [writer]
        if any(not (part.get("videoKept") and part.get("sidecarKept")) for part in self.summary["video"]["parts"]):
            self.unkept.append((self.folder, self.summary, writer))
        self._log({"kind": "dataset", "event": "stop", "id": self.summary.get("id")})
        self.light.stopped(self.folder, self.summary, writer, self.video_error, now)
        self.video_state = "off"
        self.video_error = ""
        self._publish(force=True)
        self._wake()  # the bridge is told this dataset no longer records (the next tick)
        return self.wire()

    def rename(self, dataset_id: str, name: str) -> bool:
        """The user named a dataset (the Datasets tab): appended to its events, its summary rewritten."""
        if self.on and self.summary.get("id") == dataset_id:
            self.summary["name"] = store.clean_name(name)
            self.record("dataset", {"event": "renamed", "name": self.summary["name"]})
            self._save_summary()
            self._publish(force=True)
            return True
        folder = store.find(self.root, dataset_id)
        if folder is None:
            return False
        store.rename_closed(folder, name, self.clock)
        return True

    def rig_fact(self, field: str, fact: dict) -> None:
        """A microscope fact the user entered while this collects: appended, marked as entered after
        the start, never written into what the dataset started with."""
        if not self.on:
            return
        entry = {"field": field, **fact, "enteredAfter": True}
        self.record("dataset", {"event": "rig-fact", **entry})
        self.summary.setdefault("rigEnteredAfter", []).append(entry)
        self._save_summary()

    def context_line(self) -> str:
        """What the brain and the voice are told while it is on (read-only: only the user's Record switches it)."""
        if not self.on:
            return ""
        started = time.localtime(self.summary.get("startedAtEpoch") or self.clock())
        when = f"{(started.tm_hour % 12) or 12}:{started.tm_min:02d}:{started.tm_sec:02d} {'am' if started.tm_hour < 12 else 'pm'}"
        return (f"A dataset is being collected (the user is recording, since {when}): what is seen and said now is kept "
                "for later analysis. Only the user starts or stops recording, in the studio.")

    def resume(self) -> None:
        """At the app's start: a dataset still collecting (the app stopped or crashed while the dot
        was on) carries on, with the time it was down written down as a gap."""
        open_ones = [(folder, summary) for folder, summary in manifest.all_in(self.root)
                     if summary.get("status") == "collecting"]
        if not open_ones:
            return
        (folder, summary), others = open_ones[0], open_ones[1:]
        now = self.clock()
        for other_folder, other in others:  # cannot happen through the switch; never left dangling
            other.update(status="complete", endedBy="found-open-at-start")
            manifest.write(other_folder, other)
        last = max([summary.get("startedAtEpoch") or now] +
                   [(folder / name).stat().st_mtime for name in (manifest.EVENTS, manifest.FRAMES)
                    if (folder / name).exists()])
        self.folder, self.summary = folder, summary
        runs = summary.setdefault("runs", [])
        if runs and runs[-1].get("endedAtEpoch") is None:
            runs[-1]["endedAtEpoch"] = round(last, 3)
        self.run = len(runs) + 1
        runs.append({"n": self.run, "startedAt": manifest.iso(now), "startedAtEpoch": round(now, 3), "endedAtEpoch": None})
        gap = {"fromEpoch": round(last, 3), "toEpoch": round(now, 3), "from": manifest.iso(last), "to": manifest.iso(now),
               "seconds": round(now - last, 1),
               "what": "the app was not running: no records or frame facts; a video part may have gone on if the "
                       "camera bridge kept recording (its sidecar says)"}
        summary.setdefault("gaps", []).append(gap)
        summary["app"] = self._about()
        self.resumed_after = gap["seconds"]
        self.dropped = self._dropped_noted = 0
        self.writer = Writer(folder, counts=summary.get("counts"))
        self.screens = len(summary.get("screen") or [])
        self.light.started(folder.name, now)
        self._dataset_event("resumed", gap=gap)
        self._dataset_event("rig", why="resume", facts=self._rig())  # what was measured may differ now
        self._keep_state("resume")
        self._note_sources("resume")
        self._save_summary()
        self.video_state, self.video_error, self._retry_at = "starting", "", 0.0
        self._log({"kind": "dataset", "event": "resume", "id": folder.name, "gapSeconds": gap["seconds"]})
        self._publish(force=True)

    def close(self, timeout: float = 5.0) -> None:
        """The app is stopping: everything is written, and the dataset stays on (resume)."""
        if self.writer is not None:
            self._dataset_event("app-stopping")
            self.summary["counts"] = self._counts()
            self._save_summary()
            self.writer.close(timeout)
            self.writer = None
        for writer in self.closing:
            writer.close(timeout)

    # --- the taps (any thread; each only puts an item on the queue) -----------------------------------
    def record(self, stream: str, data: dict) -> None:
        writer = self.writer
        if writer is not None:
            writer.put(("event", {"t": round(self.clock(), 4), "run": self.run, "stream": stream, "record": data}))

    def interaction(self, entry: dict) -> None:
        self.record("interaction", entry)

    def vote(self, entry: dict) -> None:
        self.record("vote", entry)

    def journal(self, event: dict) -> None:
        self.record("journal", event)
        if event.get("type") == "session-opened" and self.on:
            # Another session (a new one, or one switched to; ADR 0017): the dataset goes on, with the state the app is
            # in now and the session's own journal among its sources.
            self._keep_state("session-opened")
            self._note_sources("session-opened")
            self._save_summary()

    def frame(self, info) -> None:
        writer = self.writer
        if writer is None:
            return
        self.last_frame = (info.seq, info.boot)
        if writer.waiting() > QUEUE_LIMIT:  # the disk is not keeping up: frame facts go first, never records
            self.dropped += 1
            return
        writer.put(("frame", self.run, info))

    def keep_log_picture(self, path: Path, relative: str) -> None:
        """A picture a model was shown (Agent.keep_image): at pictures/<its path under the logs folder>."""
        writer = self.writer
        if writer is not None:
            writer.put(("file", str(path), f"{manifest.PICTURES}/{relative}"))

    def keep_file(self, path: Path, kind: str) -> None:
        """A file a tool saved (a snapshot and its description): at pictures/<kind>/<name>."""
        writer = self.writer
        if writer is not None:
            name = f"{manifest.PICTURES}/{kind}/{Path(path).name}"
            writer.put(("file", str(path), name))
            self.record("dataset", {"event": "file", "kind": kind, "file": name, "source": str(path)})

    def keep_screen(self, path: Path, started_at: float, finished: bool) -> None:
        """The studio's own recording (the view with its outlines, the chat and the voice; recordings.py):
        hard-linked in when it starts, so the dataset holds it as it grows, and copied in when it is
        finished if it is on another disk. At video/screen-N.<ext>, as a rebuilt dataset has it."""
        if self.writer is None:
            return
        source = str(path)
        screens = self.summary.setdefault("screen", [])
        entry = next((s for s in screens if s.get("source") == source), None)
        if entry is None:
            if finished:
                return  # it started before this dataset: not one of its own
            self.screens = len(screens) + 1
            entry = {"file": f"{manifest.VIDEO}/screen-{self.screens}{Path(path).suffix}", "source": source,
                     "startEpoch": round(started_at, 3), "start": manifest.iso(started_at), "finished": False,
                     "what": "the studio's own screen recording (the view with its outlines, the chat and the voice), "
                             "recorded in the browser; its times are the browser's, from its start"}
            screens.append(entry)
        entry["finished"] = finished
        self.writer.put(("video", source, entry["file"], finished))
        channels = path.with_suffix('.channels.jsonl')
        if channels.is_file():
            entry['channelsFile'] = str(Path(entry['file']).with_suffix('.channels.jsonl'))
            self.writer.put(('video', str(channels), entry['channelsFile'], finished))
        self.record("dataset", {"event": "screen", **entry})
        self._save_summary()

    def keep_picture(self, jpeg: bytes, label: str) -> None:
        """A picture the app made from real frames and showed in the magnifier (kept only in memory)."""
        writer = self.writer
        if writer is not None:
            now = self.clock()
            name = f"{manifest.PICTURES}/magnifier/{int(now * 1000)}-{hashlib.sha1(jpeg).hexdigest()[:8]}.jpg"
            writer.put(("bytes", jpeg, name))
            self.record("dataset", {"event": "picture", "kind": "magnifier", "file": name, "label": label})

    # --- once a second ------------------------------------------------------------------------------
    async def run_forever(self) -> None:
        self._kick = asyncio.Event()
        while True:
            try:
                await asyncio.wait_for(self._kick.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            self._kick.clear()
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # the loop outlives any one failure
                print(f"Dataset tick: {type(exc).__name__}: {exc}", flush=True)

    async def tick(self) -> None:
        now = self.clock()
        await self._video(now)
        self._check(now, finish=False)
        await self._finish_check(now)
        if self.on:
            if self.scene is not None:
                version, wire = self.scene()
                if version != self._scene_version:
                    self._scene_version = version
                    self.record("scene", wire)
            if self.dropped != self._dropped_noted:
                self.record("dataset", {"event": "dropped", "frames": self.dropped - self._dropped_noted,
                                        "why": "the disk was not keeping up: per-frame facts only, never records"})
                self._dropped_noted = self.dropped
            if now - self._disk_at >= DISK_EVERY_S:
                self._disk_at = now
                try:
                    self.disk_free = shutil.disk_usage(self.root).free
                except OSError:
                    self.disk_free = None
            if now - self._summary_at >= SUMMARY_EVERY_S:
                self.summary["counts"] = self._counts()
                self._save_summary()
            if now - self._wire_at >= WIRE_EVERY_S:
                if self.folder is not None:
                    self.bytes = await asyncio.to_thread(folder_bytes, self.folder)
                self._publish(force=True)
                return
        self._publish()

    def _check(self, now: float, *, finish: bool = True) -> None:
        """The Data light: while on, whether everything is really being saved; after a stop, whether it
        was saved whole. Each change is written into the dataset, so analysis knows when saving faltered."""
        health = self.bridge_health()
        if self.on and self.folder is not None:
            before = self.light.state
            changed = self.light.check(now, folder=self.folder, summary=self.summary, health=health,
                                       video_state=self.video_state, video_error=self.video_error, writer=self.writer,
                                       last_frame=self.last_frame, disk_low=self.disk_low, disk_free=self.disk_free)
            if changed and self.light.state != "checking":  # the first seconds are not yet a verdict
                light = {"state": self.light.state, "problems": self.light.problems}
                self.record("dataset", {"event": "light", **light})
                if self.light.state != before:
                    self._log({"kind": "dataset", "event": "light", "id": self.summary.get("id"), **light})
        result = self.light.end(now, health) if finish else None
        self._log_end_check(result)

    async def _finish_check(self, now: float) -> None:
        """Scan/write the stopped dataset off-loop; commit its light only while that same stop still owns it."""
        owner = self.light.ending
        if owner is None:
            return
        inspected = copy.copy(self.light)
        summary = copy.deepcopy(owner["summary"])
        inspected.ending = {**owner, "summary": summary}
        result = await asyncio.to_thread(inspected.end, now, copy.deepcopy(self.bridge_health()))
        if result is not None and self.light.ending is owner:
            # start/stop may have handed the live light to another dataset while IO ran. Its pending check survives.
            owner["summary"].update({key: summary[key] for key in ("endCheck", "madeWith", "counts") if key in summary})
            self.light.ending, self.light.last = None, result
        self._log_end_check(result)

    def _log_end_check(self, result: dict | None) -> None:
        if result is not None:
            # The check's own time is ISO with its offset (dataset.json's endCheck); in the interaction log it is kept
            # as `checkedAt`, so the line's `at` stays the log's own format, which every reader parses.
            logged = {key: value for key, value in result.items() if key != "at"}
            self._log({"kind": "dataset", "event": "end-check", **logged, "checkedAt": result.get("at")})

    async def _video(self, now: float) -> None:
        """Keep the bridge recording for this dataset while it is on, and every part of the video
        linked into it; tell the bridge when it is off."""
        health = self.bridge_health() or {}
        recording = health.get("recording") or {}
        owners = recording.get("owners") or []
        boot = health.get("bootId")
        if boot and self._last_boot and boot != self._last_boot and self.on:
            self.record("dataset", {"event": "bridge-restarted", "boot": boot, "previous": self._last_boot})
        if boot:
            self._last_boot = boot
        settled = now - self._requested_at >= SETTLE_S
        for item in list(self.unkept):  # a stopped dataset's last part, finished on another disk
            folder, summary, writer = item
            if writer.closed and self._link_parts(folder, summary, recording, write=True):
                self.unkept.remove(item)
        if not self.on:
            if recording.get("running") and OWNER in owners and settled:
                self._requested_at = now
                try:
                    await self.post(f"/api/record/stop?owner={OWNER}")
                except Exception as exc:
                    print(f"Dataset: the bridge was not told to stop recording: {exc}", flush=True)
            return
        if recording.get("running") and OWNER in owners:
            self._part(recording, boot, joined=None)
            if self.video_state != "recording":
                self.video_state, self.video_error = "recording", ""
        elif not health:
            self.video_state = "waiting"
            self.video_error = "the camera bridge is not answering"
        elif settled and now >= self._retry_at:
            self._requested_at = now
            try:
                reply = await self.post(f"/api/record/start?owner={OWNER}")
            except Exception as exc:
                reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            if reply.get("ok"):
                self._part(reply, boot, joined=bool(reply.get("alreadyRecording")))
                self.video_state, self.video_error = "recording", ""
            else:
                error = str(reply.get("error") or "the camera bridge did not start recording")
                if error != self.video_error:
                    self.record("dataset", {"event": "video-error", "error": error})
                self.video_state, self.video_error = "error", error
                self._retry_at = now + RETRY_S
        if self.folder is not None:
            self._link_parts(self.folder, self.summary, recording)

    def _part(self, status: dict, boot: str | None, joined: bool | None) -> None:
        """A recording file this dataset is in: a new part the first time its path is seen."""
        source = status.get("path")
        parts = self.summary.setdefault("video", {}).setdefault("parts", [])
        if not source or any(part["source"] == source for part in parts):
            return
        n = len(parts) + 1
        part = {"n": n, "video": f"{manifest.VIDEO}/part-{n}.mp4", "sidecar": f"{manifest.VIDEO}/part-{n}.frames.jsonl",
                "source": source, "sourceSidecar": status.get("sidecar"), "boot": boot,
                "noticedAt": manifest.iso(self.clock()), "noticedAtEpoch": round(self.clock(), 3),
                # Joined a recording someone else had started (the studio's Record): the file holds more
                # than this dataset's window; the sidecar's times say which frames are inside it.
                "joined": joined, "owners": status.get("owners"), "videoKept": None, "sidecarKept": None,
                "copyWhenFinished": False}
        parts.append(part)
        self.record("dataset", {"event": "video-part", **part})
        self._save_summary()

    def _link_parts(self, folder: Path, summary: dict, recording: dict, write: bool = False) -> bool:
        """Hard-link each part's video and sidecar into the dataset as soon as they exist (the same file
        on disk, growing as the bridge writes it). Across disks a copy is made only once the bridge has
        finished the file: a file still being written is never copied half-way. True when every part
        is kept. `write`: a stopped dataset (its writer closed), its summary written here."""
        busy = {recording.get("path")} if recording.get("running") else set()
        busy |= {str(entry.get("path")) for entry in recording.get("finishing") or []}
        changed = False
        notes: list[dict] = []
        for part in summary.get("video", {}).get("parts", []):
            for key, source_key, kept_key in (("video", "source", "videoKept"), ("sidecar", "sourceSidecar", "sidecarKept")):
                source = part.get(source_key)
                if part.get(kept_key) or not source or not Path(source).exists():
                    continue
                target = folder / part[key]
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(source, target)
                    part[kept_key] = "linked"
                except FileExistsError:
                    part[kept_key] = "linked"
                except OSError:
                    part["copyWhenFinished"] = True
                    if part["source"] in busy:
                        continue
                    try:
                        shutil.copy2(source, target)
                        part[kept_key] = "copied"
                    except OSError as exc:
                        notes.append({"event": "video-not-kept", "part": part["n"], "error": str(exc)})
                        continue
                changed = True
                notes.append({"event": f"{key}-kept", "part": part["n"], "how": part[kept_key], "file": part[key]})
        if write:
            if changed:
                summary.setdefault("keptAfterStop", []).extend({**note, "atEpoch": round(self.clock(), 3)} for note in notes)
                manifest.write(folder, summary)
        else:
            for note in notes:
                self.record("dataset", note)
            if changed:
                self._save_summary()
        return all(part.get("videoKept") and part.get("sidecarKept") for part in summary.get("video", {}).get("parts", []))

    # --- helpers -----------------------------------------------------------------------------------
    def _wake(self) -> None:
        if self._kick is not None:
            self._kick.set()

    def _dataset_event(self, event: str, **details) -> None:
        self.record("dataset", {"event": event, **details})

    def _keep_state(self, why: str) -> None:
        if self.writer is None or self.snapshot is None:
            return
        try:
            state = self.snapshot()
        except Exception as exc:
            state = {"error": f"{type(exc).__name__}: {exc}"}
        self.writer.put(("line", manifest.STATES, {"t": round(self.clock(), 4), "run": self.run, "why": why,
                                                     "snapshot": state}))

    def _keep_preroll(self) -> None:
        """The minute before Record started (the clip buffer: a frame every half second), so the start
        errs earlier, never later."""
        if self.writer is None or self.preroll is None:
            return
        try:
            frames = list(self.preroll())
        except Exception:
            return
        for clip in frames:
            name = f"{manifest.PICTURES}/preroll/{int(clip.wall_at * 1000)}-f{clip.id}.jpg"
            self.writer.put(("bytes", clip.jpeg, name))
            fov = getattr(clip, "fov", None)
            self.writer.put(("line", f"{manifest.PICTURES}/preroll.jsonl", {
                "t": round(clip.wall_at, 4), "id": clip.id, "file": name, "dx": round(clip.dx, 2), "dy": round(clip.dy, 2),
                "epoch": clip.epoch, "steady": clip.steady, "focus": round(clip.focus, 2),
                "fov": None if fov is None else [round(fov.cx, 1), round(fov.cy, 1), round(fov.radius, 1)]}))
        self.record("dataset", {"event": "preroll", "frames": len(frames),
                                "what": "the last minute before the start, a frame every half second (pictures/preroll/)"})

    def _note_sources(self, when: str) -> None:
        """Where the logs this dataset copies from are, and how long each was: if a tap ever failed,
        the window can be cut back out of them (by time) while they are kept."""
        if self.sources is None:
            return
        files = {}
        for name, path in self.sources().items():
            try:
                files[name] = {"path": str(path), "bytes": Path(path).stat().st_size}
            except OSError:
                files[name] = {"path": str(path), "bytes": None}
        entry = {"when": when, "at": manifest.iso(self.clock()), "atEpoch": round(self.clock(), 3), "files": files}
        self.summary.setdefault("sourceLogs", []).append(entry)

    def _rig(self) -> dict:
        try:
            return self.rig() if self.rig is not None else {}
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    def _about(self) -> dict:
        try:
            return self.about() if self.about is not None else {}
        except Exception:
            return {}

    def counts(self) -> dict:
        """What the dataset being collected holds so far (its summary has them only every 30 s)."""
        return self._counts()

    def _counts(self) -> dict:
        writer = self.writer
        if writer is None:
            return self.summary.get("counts", {})
        return {"events": writer.events, "frames": writer.frames, "pictures": writer.pictures,
                "droppedFrames": self.dropped, "run": self.run}

    def _save_summary(self) -> None:
        self._summary_at = self.clock()
        if self.writer is not None:
            self.writer.put(("summary", copy.deepcopy(self.summary)))
        elif self.folder is not None:
            manifest.write(self.folder, self.summary)

    def _log(self, record: dict) -> None:
        if self.log is not None:
            try:
                self.log(record)
            except Exception:
                pass

