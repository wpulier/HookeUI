"""The Data light (docs/datasets.md "The Data light"): whether the dataset Record is collecting is really
being saved, checked once a second from the dataset's own files, and, after Record stops, whether it was
saved whole. Record is the switch; this only looks, and never starts, stops or trims anything.

While it records, five things are checked, each from what is in the dataset's folder rather than from
what the app meant to write:
  video    the camera's video, linked into the dataset, has grown within VIDEO_GROWING_S
  times    its frame times (the bridge's sidecar, linked beside it) have grown within GROWING_S
  frames   the app's facts about each frame (frames.jsonl) have grown within GROWING_S, and the frames
           carry the bridge's frame number, so they can be joined to the video
  records  the records file is there and nothing failed to be written in the last ERROR_S
  disk     there is room left
Growing means seen growing: a file must get bigger between two looks, so the light cannot pass on files
that merely exist. It says "checking" from a start or a resume until every check has passed (or FIRST_S
has gone by: then a failing check is a problem), "ok" while every one passes, and "problem" with a plain
sentence for each root cause. Each change is written into the dataset (`light` events), so later analysis
knows when saving was degraded.

After a stop it says "saving" until the bridge has finished the camera video and every file is in place,
then "saved" (or "problem"); the result is written into the dataset's summary as `endCheck`.
"""
from __future__ import annotations

from pathlib import Path

from . import manifest

OWNER = "dataset"  # the bridge's recording owner for a dataset (collector.py)
FIRST_S = 10.0  # from a start or a resume: "checking", not yet a problem, for this long
GROWING_S = 3.0  # the frame times and the frame facts are written at least every second
VIDEO_GROWING_S = 5.0  # the camera video grows a fragment (about a second) at a time
ERROR_S = 30.0  # something that could not be written makes the light amber this long
END_WAIT_S = 120.0  # after a stop: how long to wait for the bridge to finish the camera video
SAVED_SHOW_S = 10.0  # "saved" stays on the light this long
BACKLOG = 25_000  # items waiting for the disk: the writer is falling behind (half of writer.QUEUE_LIMIT)


class Growth:
    """When a file was last seen to grow: only a size larger than the one seen before counts."""

    def __init__(self) -> None:
        self.size: int | None = None
        self.at: float | None = None

    def see(self, path: Path, now: float) -> bool:
        """Look at the file; True when it exists."""
        try:
            size = path.stat().st_size
        except OSError:
            return False
        if self.size is not None and size > self.size:
            self.at = now
        self.size = size if self.size is None else max(size, self.size)
        return True

    def grew_within(self, seconds: float, now: float) -> bool:
        return self.at is not None and now - self.at <= seconds


def item(name: str, ok: bool, problem: str = "") -> dict:
    return {"name": name, "ok": ok, "problem": "" if ok else problem}


class Light:
    def __init__(self) -> None:
        self.state = "off"  # while recording: checking | ok | problem
        self.id: str | None = None
        self.items: list[dict] = []
        self.problems: list[str] = []
        self.at: float | None = None  # when the state last changed
        self.last: dict | None = None  # the end check of the last dataset stopped
        self.ending: dict | None = None  # a stopped dataset whose end check is not done yet
        self._since = 0.0
        self._passed = False
        self._growth: dict[str, Growth] = {}

    # --- what the studio is sent ------------------------------------------------------------------
    def wire(self, on: bool, now: float) -> dict:
        if on:
            state, problems, items, at, ident = self.state, self.problems, self.items, self.at, self.id
        else:
            last = self.last
            items, ident, at = [], (last or {}).get("id"), (last or {}).get("atEpoch")
            problems = list((last or {}).get("problems") or [])
            if self.ending is not None:
                state, ident, at, problems = "saving", self.ending["id"], self.ending["at"], []
            elif last and not last.get("ok"):
                state = "problem"
            elif last and now - float(last.get("atEpoch") or 0) < SAVED_SHOW_S:
                state = "saved"
            else:
                state, problems = "off", []
        return {"state": state, "id": ident, "items": items, "problems": problems, "at": at, "last": self.last}

    # --- the edges ---------------------------------------------------------------------------------
    def started(self, dataset_id: str, now: float) -> None:
        """A start or a resume: checking again from nothing."""
        self.state, self.id, self.items, self.problems, self.at = "checking", dataset_id, [], [], now
        self._since, self._passed, self._growth = now, False, {}

    def stopped(self, folder: Path, summary: dict, writer, video_error: str, now: float) -> None:
        self.ending = {"id": summary.get("id"), "folder": folder, "summary": summary, "writer": writer,
                       "videoError": video_error, "at": now}
        self.state, self.items, self.problems = "off", [], []

    # --- while it records (once a second) ------------------------------------------------------------
    def check(self, now: float, *, folder: Path, summary: dict, health: dict | None, video_state: str,
              video_error: str, writer, last_frame: tuple, disk_low: bool, disk_free: int | None) -> bool:
        """Check the dataset being recorded; True when the light changed."""
        recording = (health or {}).get("recording") or {}
        mine = bool(recording.get("running")) and OWNER in (recording.get("owners") or [])
        parts = summary.get("video", {}).get("parts", [])
        part = next((p for p in parts if mine and p.get("source") == recording.get("path")), None)

        facts = self._grew(folder / manifest.FRAMES, GROWING_S, now)
        seq, boot = last_frame
        no_pictures = not facts
        items: list[dict] = []
        problems: list[str] = []
        if no_pictures:
            problems.append(f"No pictures from the camera in the last {GROWING_S:.0f} s, so the camera video, its "
                            "frame times and the frame facts are not growing. Records are still saved.")

        # The camera video.
        if not health:
            items.append(item("video", False, "The camera bridge is not answering, so the camera video is not being saved."))
        elif video_state == "error":
            items.append(item("video", False, f"The camera video is not being saved: {video_error}. Asking the bridge again."))
        elif not mine:
            items.append(item("video", False, "The camera bridge has not started recording the video yet."))
        elif part is None or not (part.get("videoKept") or part.get("copyWhenFinished")):
            items.append(item("video", False, "The camera video is not in the dataset yet."))
        elif part.get("videoKept") and not self._grew(folder / part["video"], VIDEO_GROWING_S, now):
            items.append(item("video", False, "" if no_pictures else
                              f"The camera video in the dataset has not grown in the last {VIDEO_GROWING_S:.0f} s."))
        else:
            items.append(item("video", True))
        video_blocked = bool(items[-1]["problem"])  # its own problem, not the camera's silence

        # Its frame times (the bridge's sidecar).
        if part is None or video_blocked:
            items.append(item("times", False, ""))  # waits for the video (its problem is said there)
        elif recording.get("sidecarError"):
            items.append(item("times", False, f"The frame times are not being written: {recording['sidecarError']}."))
        elif not (part.get("sidecarKept") or part.get("copyWhenFinished")):
            items.append(item("times", False, "The frame times are not in the dataset yet."))
        elif part.get("sidecarKept") and not self._grew(folder / part["sidecar"], GROWING_S, now):
            items.append(item("times", False, "" if no_pictures else
                              f"The frame times in the dataset have not grown in the last {GROWING_S:.0f} s."))
        else:
            items.append(item("times", True))

        # The frame facts, joinable to the video.
        if no_pictures:
            items.append(item("frames", False, ""))
        elif seq is None or not boot:
            items.append(item("frames", False, "The frames carry no number from the camera bridge, so they cannot "
                                               "be matched to the video."))
        else:
            items.append(item("frames", True))

        # The records.
        if not (folder / manifest.EVENTS).exists():
            items.append(item("records", False, "Nothing has been written into the dataset's folder."))
        elif writer is None or writer.closed:
            items.append(item("records", False, f"The dataset's writer stopped{': ' + writer.error if writer and writer.error else ''}."))
        elif writer.error and now - writer.error_at <= ERROR_S:
            items.append(item("records", False, f"Something could not be written: {writer.error}."))
        elif writer.waiting() > BACKLOG:
            items.append(item("records", False, "The disk is not keeping up: records are waiting to be written."))
        else:
            items.append(item("records", True))

        # Room left.
        free = f" ({disk_free / 1024 ** 3:.1f} GB free)" if disk_free is not None else ""
        items.append(item("disk", not disk_low, f"The disk is nearly full{free}."))

        problems += [i["problem"] for i in items if not i["ok"] and i["problem"]]
        if all(i["ok"] for i in items):
            state = "ok"
            self._passed = True
        elif self._passed or now - self._since >= FIRST_S:
            state = "problem"
        else:
            state = "checking"
        changed = (state, problems) != (self.state, self.problems)
        if state != self.state:
            self.at = now
        self.state, self.items, self.problems = state, items, problems
        return changed

    def _grew(self, path: Path, seconds: float, now: float) -> bool:
        growth = self._growth.setdefault(str(path), Growth())
        return growth.see(path, now) and growth.grew_within(seconds, now)

    # --- after a stop ---------------------------------------------------------------------------------
    def end(self, now: float, health: dict | None) -> dict | None:
        """The end check of the dataset just stopped, once the bridge has finished its video (or
        END_WAIT_S has gone by). Returns the result when it is done, and writes it into the dataset."""
        e = self.ending
        if e is None:
            return None
        waited = now - e["at"]
        writer = e["writer"]
        recording = (health or {}).get("recording") or {}
        owners = recording.get("owners") or []
        running = recording.get("path") if recording.get("running") else None
        finishing = {str(entry.get("path")) for entry in recording.get("finishing") or []}
        parts = e["summary"].get("video", {}).get("parts", [])
        unfinished = [p for p in parts if p.get("source") in finishing or (p.get("source") == running and OWNER in owners)]
        if waited < END_WAIT_S and (not writer.closed or unfinished or (parts and not health)):
            return None
        folder: Path = e["folder"]
        problems: list[str] = []
        notes: list[str] = []
        if not parts:
            reason = f": {e['videoError']}" if e.get("videoError") else ""
            problems.append(f"No camera video was recorded{reason}.")
        last = recording.get("lastSaved") or {}
        for p in parts:
            n = p["n"]
            video, sidecar = folder / p["video"], folder / p["sidecar"]
            if not (video.exists() and video.stat().st_size > 0):
                problems.append(f"Part {n} of the camera video is not in the dataset.")
            if not sidecar.exists():
                problems.append(f"Part {n}'s frame times are not in the dataset.")
            if p in unfinished:
                problems.append(f"The camera bridge had not finished part {n} of the video after {END_WAIT_S:.0f} s; "
                                "what was recorded is in the dataset and plays as it is.")
            elif not health:
                notes.append(f"The camera bridge was not answering, so part {n} was not seen to finish.")
            if last.get("path") == p.get("source") and last.get("ok") is False:
                problems.append(f"Part {n} of the camera video did not finish cleanly ({last.get('error') or 'no index'}); "
                                "what was recorded plays as it is.")
            if p.get("source") == running and OWNER not in owners and owners:
                notes.append(f"Part {n}'s file goes on recording for {' and '.join(owners)}; its frame times say which "
                             "frames are in this dataset.")
        if not (folder / manifest.EVENTS).exists():
            problems.append("Its records file is missing.")
        if not writer.closed:
            problems.append(f"Its files were still being written {END_WAIT_S:.0f} s after the stop.")
        elif writer.error:
            notes.append(f"Something could not be written while it recorded: {writer.error}.")
        summary = e["summary"]
        if writer.closed:
            summary.setdefault("counts", {}).update(events=writer.events, frames=writer.frames, pictures=writer.pictures)
        result = {"id": e["id"], "ok": not problems, "problems": problems, "notes": notes, "at": manifest.iso(now),
                  "atEpoch": round(now, 3), "waitedSeconds": round(waited, 1), "counts": dict(summary.get("counts") or {}),
                  "videoParts": len(parts)}
        summary["endCheck"] = {k: v for k, v in result.items() if k != "id"}
        try:  # what ran while it recorded (made_with.py): who opens it elsewhere is offered what made it
            from ..made_with import of_dataset
            from ..result_entries import carry_dataset

            summary["madeWith"] = carry_dataset(folder, of_dataset(folder))
        except Exception:  # noqa: BLE001 - its check is written whatever happens to this
            pass
        manifest.write(folder, summary)
        self.ending, self.last = None, result
        return result
