"""Rebuild a dataset afterwards from what was kept: for a session before Record collected datasets, or one
not recorded (make dataset-import; docs/datasets.md).

It reads the window FROM..TO (local times) out of the day journals, the interaction log, the pictures
those records name, the snapshots and the researchers' work started in it, and keeps a recording of the
camera (and, optionally, the studio's own screen recording) as its video, hard-linked. It is marked
`reconstructed`: its window was chosen by a person, and what was not captured then (the app's per-frame
facts, the bridge's per-frame clock) is said to be missing, never made up. Facts a person states now
(`--fact eyepiece=25|what they said, when`) go in as entered after, with that evidence.

  python -m orchestrator.datasets.importer --from 2026-01-01T10:00:00 --to 2026-01-01T10:15:00 \\
      --video /path/to/your-recording.mp4 --video-start 2026-01-01T10:02:00
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from ..day.prefs import FILE as PREFS_FILE, history
from ..paths import Paths
from . import manifest
from .check import problems
from .rig import FIELDS, clean_value
from .writer import link_or_copy

PICTURE_KEYS = ("image", "view", "close")  # interaction-record fields naming a picture under the logs folder
FIELD_NUMBER_GIVEN = ("field_number", "fieldNumber")


def epoch(text: str) -> float:
    return datetime.fromisoformat(text).timestamp()


def days_between(start: float, stop: float) -> list[str]:
    first, last = date.fromtimestamp(start), date.fromtimestamp(stop)
    return [(first + timedelta(days=n)).isoformat() for n in range((last - first).days + 1)]


def parse_fact(text: str) -> tuple[str, str, str]:
    """'eyepiece=25|said "I think I have a... 25X" at 01:36:51' -> (eyepiece, 25, the evidence)."""
    pair, _, evidence = text.partition("|")
    key, _, value = pair.partition("=")
    if not key.strip() or not value.strip():
        raise SystemExit(f"a fact is KEY=VALUE|evidence, not {text!r}")
    return key.strip(), value.strip(), evidence.strip()


def count_frames(video: Path) -> int | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    result = subprocess.run([ffprobe, "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
                             "stream=nb_read_packets", "-of", "csv=p=0", str(video)], capture_output=True, text=True)
    try:
        return int(result.stdout.strip().splitlines()[0])
    except (IndexError, ValueError):
        return None


class Gathered:
    """What a window of the day's logs holds, copied into a dataset folder: the records (`events`), the
    files read, the pictures linked in, the researchers' work and the scale's settings."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.read: dict[str, dict] = {}
        self.pictures = 0
        self.research: list[dict] = []
        self.scale_prefs: list[dict] = []
        self.field_number_given = False
        self.linked: list[tuple[str, str]] = []


def gather(paths: Paths, start: float, stop: float, folder: Path | None, *, votes: bool = False,
           research_status=None, link=link_or_copy) -> Gathered:
    """The window start..stop of the day journals, the interaction log (and, with `votes`, the vote log),
    with the pictures they name, the snapshots and the researchers' work started in it. With a `folder`
    the files are linked into it; without one nothing is written (a preview). `research_status(day, rid)`
    says how a researcher stands now (a snapshot marks one still working as pending). `link(source, target)`
    keeps a file in the folder (a dataset hard-links; a session bundle copies: bench/bundle.py)."""
    # Lines handed to the log writer thread on the event loop (looplag.LINES) are written before the logs are read, as
    # dialogue's reader does: a dataset gathered just after a tool logged (a bookmark, then Stop) keeps that line.
    # Callers are off the event loop (snapshot's preview and save run in a thread; the importer is a command).
    from ..looplag import LINES

    LINES.flush()
    got = Gathered()
    inside = lambda t: start <= t <= stop  # noqa: E731

    def keep(source: Path, target: Path) -> bool:
        if folder is None:
            return True
        return link(source, folder / target) != "exists"

    # The day journals (turns, lines, subjects, preferences, researchers linked).
    for day in days_between(start, stop):
        journal = paths.sessions / day / "journal.jsonl"
        if not journal.exists():
            continue
        got.read[f"journal {day}"] = {"path": str(journal), "bytes": journal.stat().st_size}
        for raw in journal.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            at = float(event.get("at") or 0)
            if event.get("type") == "preference" and event.get("key") == "scale" and at <= stop:
                got.scale_prefs.append(event)  # the one in force at the start, and the changes inside
            if inside(at):
                got.events.append({"t": round(at, 4), "run": 1, "stream": "journal", "record": event})
                if event.get("type") == "research-linked" and event.get("rid"):
                    got.linked.append((day, str(event["rid"])))

    # The user's preferences left the day journals for their own file (day/prefs.py): a scale set since is only
    # there. A copy seeded from a day journal is in force from when it was seeded but is no change of its own, so
    # it is never a record of the window, nor a scale setting when it repeats the one before it.
    when = lambda e: float(e.get("at") or 0)  # noqa: E731
    kept = history(paths.sessions)
    scale = sorted([*got.scale_prefs, *(e for e in kept if e.get("key") == "scale" and when(e) <= stop)], key=when)
    got.scale_prefs[:] = [e for i, e in enumerate(scale)
                          if not (e.get("seededFrom") and i and scale[i - 1].get("value") == e.get("value"))]
    records = [e for e in kept if inside(when(e)) and not e.get("seededFrom")]
    got.events += [{"t": round(when(e), 4), "run": 1, "stream": "journal", "record": e} for e in records]
    if records or {id(e) for e in kept} & {id(e) for e in got.scale_prefs}:
        prefs = paths.sessions / PREFS_FILE
        got.read["preferences"] = {"path": str(prefs), "bytes": prefs.stat().st_size}

    # The interaction log (its times are to the second), and the pictures its records name; then votes.
    logs = [("interaction", paths.interaction_log)] + ([("vote", paths.votes_log)] if votes else [])
    for stream, log in logs:
        if not log.exists():
            continue
        got.read["interactions" if stream == "interaction" else "votes"] = {"path": str(log), "bytes": log.stat().st_size}
        for raw in log.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(raw)
                at = time.mktime(time.strptime(entry.get("at", ""), "%Y-%m-%dT%H:%M:%S"))
            except (ValueError, TypeError, AttributeError):
                continue
            if not inside(at):
                continue
            got.events.append({"t": round(at, 4), "run": 1, "stream": stream, "record": entry})
            if stream != "interaction":
                continue
            got.field_number_given = got.field_number_given or any(key in raw for key in FIELD_NUMBER_GIVEN)
            named = [entry.get(key) for key in PICTURE_KEYS] + list(entry.get("closerImages") or [])
            for relative in [p for p in named if isinstance(p, str) and p]:
                source = log.parent / relative
                if source.is_file() and keep(source, Path(manifest.PICTURES) / relative):
                    got.pictures += 1

    # Snapshots taken in the window, with their descriptions.
    for day in days_between(start, stop):
        for meta in sorted((paths.snapshots / day).glob("*.json")) if (paths.snapshots / day).is_dir() else []:
            try:
                taken = float(json.loads(meta.read_text()).get("wallAt") or 0)
            except (OSError, ValueError):
                continue
            if inside(taken):
                for item in (meta, meta.with_suffix(".png")):
                    if item.exists() and keep(item, Path(manifest.PICTURES) / "snapshots" / item.name):
                        got.pictures += 1

    # Bookmarks kept in the window, with their pictures (a log line names its picture only relative to the
    # bookmarks folder: the M3.3 re-check).
    for day in days_between(start, stop):
        marks = paths.bookmarks / day
        for meta in sorted(marks.glob("b*.json")) if marks.is_dir() else []:
            try:
                kept_at = float(json.loads(meta.read_text()).get("at") or 0)
            except (OSError, ValueError):
                continue
            if inside(kept_at):
                for item in (meta, meta.with_suffix(".jpg")):
                    if item.exists() and keep(item, Path(manifest.PICTURES) / "bookmarks" / day / item.name) \
                            and item.suffix == ".jpg":
                        got.pictures += 1  # its record goes with it, but only the picture is counted as one

    # The researchers' work started in the window (their files, as they are now).
    for day, rid in got.linked:
        source = paths.research / day / rid
        if not source.is_dir():
            got.research.append({"rid": rid, "kept": False, "why": f"{source} is not there"})
            continue
        if folder is not None:
            for item in source.rglob("*"):
                if item.is_file():
                    link(item, folder / "research" / rid / item.relative_to(source))
        row = {"rid": rid, "kept": True, "folder": f"research/{rid}"}
        status = research_status(day, rid) if research_status else None
        if status is not None:
            row["status"] = status
        got.research.append(row)
    return got


def build(paths: Paths, start: float, stop: float, *, video: Path | None = None, video_start: float | None = None,
          video_fps: float = 30.0, screen: Path | None = None, screen_start: float | None = None, name: str = "",
          facts: list[str] = (), sample: list[str] = (), clock=time.time) -> Path:
    if stop <= start:
        raise SystemExit("--to must be after --from")
    root = paths.datasets
    root.mkdir(parents=True, exist_ok=True)
    folder = root / f"ds-{time.strftime('%Y%m%d-%H%M%S', time.localtime(start))}"
    if folder.exists():
        raise SystemExit(f"{folder} already exists: a dataset is never written over")
    folder.mkdir()
    inside = lambda t: start <= t <= stop  # noqa: E731
    got = gather(paths, start, stop, folder)
    events, read, pictures, research = got.events, got.read, got.pictures, got.research
    scale_prefs, field_number_given = got.scale_prefs, got.field_number_given

    events.sort(key=lambda e: e["t"])
    now = clock()
    events.append({"t": round(now, 4), "run": 1, "stream": "dataset",
                   "record": {"event": "reconstructed", "from": manifest.iso(start), "to": manifest.iso(stop)}})
    with (folder / manifest.EVENTS).open("w", encoding="utf-8") as f:
        for line in events:
            f.write(json.dumps(line, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")

    # The camera's recording, kept as part 1. Recorded before sidecars: its frames' times are an estimate.
    parts: list[dict] = []
    if video is not None:
        frames = count_frames(video)
        kept = link_or_copy(video, folder / manifest.VIDEO / "part-1.mp4")
        parts.append({"n": 1, "video": f"{manifest.VIDEO}/part-1.mp4", "sidecar": None, "source": str(video),
                      "sourceSidecar": None, "boot": None, "joined": None, "videoKept": "linked" if kept == "exists" else kept,
                      "sidecarKept": None,
                      "timing": {"source": "estimate", "startEpoch": video_start, "start": manifest.iso(video_start),
                                 "frameRate": video_fps, "frames": frames,
                                 "fileSeconds": round(frames / video_fps, 2) if frames else None,
                                 "how": "No per-frame clock was kept then. Frame i was taken about start + i / frameRate; "
                                        "where the file is shorter than the time it covers, some frames never arrived, "
                                        "so later frames are off by up to the difference."}})
    screens: list[dict] = []
    if screen is not None:
        link_or_copy(screen, folder / manifest.VIDEO / "screen-1.mp4")
        screens.append({"file": f"{manifest.VIDEO}/screen-1.mp4", "source": str(screen), "startEpoch": screen_start,
                        "start": manifest.iso(screen_start),
                        "what": "the studio's own screen recording (the view with its outlines, the chat and the voice), "
                                "not the camera"})

    # What it started with: the objective and field number as the scale had them at the start (the
    # last setting before it), nothing guessed; settings changed inside the window are listed as changes.
    rig: dict[str, dict] = {field: {"value": None, "source": "unknown"} for field in FIELDS}
    rig["objective"] = {"value": None, "source": "unknown", "note": "no objective was set before the start"}
    at_start = [e for e in scale_prefs if e["at"] <= start]
    field_number = (at_start[-1]["value"] if at_start else (scale_prefs[0]["value"] if scale_prefs else {})).get("fieldNumber")
    if at_start and at_start[-1]["value"].get("objective"):
        rig["objective"] = {"value": at_start[-1]["value"]["objective"], "source": "user", "via": None,
                            "at": at_start[-1]["at"], "note": "who set it was not kept then"}
    if field_number is not None:
        rig["fieldNumber"] = ({"value": field_number, "source": "default",
                               "evidence": "no field number was given in the window (none in its conversation or tool calls); "
                                           "this is the app's default"}
                              if not field_number_given else
                              {"value": field_number, "source": "unknown",
                               "evidence": "a field number was mentioned in the window; which one the scale used is not known"})
    rig_changes = [{"at": manifest.iso(e["at"]), "atEpoch": round(e["at"], 3), "objective": e["value"].get("objective"),
                    "fieldNumber": e["value"].get("fieldNumber"), "source": "user", "via": None}
                   for e in scale_prefs if inside(e["at"])]
    entered_after = []
    for text in facts:
        key, value, evidence = parse_fact(text)
        value = clean_value(key, value) if key in FIELDS else value
        entered_after.append({"field": key, "value": value, "source": "user", "via": "reconstruction",
                              "evidence": evidence, "at": round(now, 3), "enteredAfter": True})
    sample_facts = {}
    for text in sample:
        key, value, evidence = parse_fact(text)
        sample_facts[key] = {"value": value, "source": "user", "via": "reconstruction", "evidence": evidence,
                             "at": round(now, 3), "enteredAfter": True}

    from .. import capabilities

    summary = {
        "schema": manifest.SCHEMA, "kind": "microscopemind-dataset", "id": folder.name, "name": name,
        "status": "complete", "startedAt": manifest.iso(start), "startedAtEpoch": round(start, 3),
        "stoppedAt": manifest.iso(stop), "stoppedAtEpoch": round(stop, 3), "endedBy": "reconstruction",
        "reconstructed": True,
        "edges": "Reconstructed afterwards: its window was chosen by a person, not switched on and off.",
        "format": "docs/datasets.md",
        "reconstruction": {"at": manifest.iso(now), "importerBuild": capabilities.build(), "read": read,
                           "notCaptured": ["frames.jsonl: the app did not keep per-frame facts then",
                                           "the camera bridge kept no per-frame clock (sidecar) then",
                                           "votes: the vote log has no times to place them in the window",
                                           "the session's own app build was not kept"],
                           "researchers": research},
        "runs": [{"n": 1, "startedAt": manifest.iso(start), "startedAtEpoch": round(start, 3), "endedAtEpoch": round(stop, 3)}],
        "gaps": [], "app": {"build": None, "source": "unknown"}, "video": {"parts": parts}, "screen": screens,
        "sourceLogs": [{"when": "reconstruction", "at": manifest.iso(now), "atEpoch": round(now, 3), "files": read}],
        "rig": rig, "rigChanges": rig_changes, "rigEnteredAfter": entered_after, "sample": sample_facts,
        "counts": {"events": len(events), "frames": 0, "pictures": pictures},
    }
    manifest.write(folder, summary)
    found = problems(folder)
    if found:
        raise SystemExit(f"{folder} does not pass the dataset check:\n  - " + "\n  - ".join(found))
    return folder


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="start", required=True, help="local time, e.g. 2026-10-06T01:21:39")
    parser.add_argument("--to", dest="stop", required=True)
    parser.add_argument("--video", help="a recording of the camera (kept as part 1)")
    parser.add_argument("--video-start", help="when its first frame was taken (local time)")
    parser.add_argument("--video-fps", type=float, default=30.0, help="the steady rate its frames were written at")
    parser.add_argument("--screen", help="the studio's own screen recording, if any")
    parser.add_argument("--screen-start")
    parser.add_argument("--name", default="")
    parser.add_argument("--fact", action="append", default=[], help="KEY=VALUE|evidence: a microscope fact stated now")
    parser.add_argument("--sample", action="append", default=[], help="KEY=VALUE|evidence: a fact about the sample")
    args = parser.parse_args(argv)
    if args.video and not args.video_start:
        parser.error("--video needs --video-start")
    folder = build(Paths.from_env(), epoch(args.start), epoch(args.stop),
                   video=Path(args.video).resolve() if args.video else None,
                   video_start=epoch(args.video_start) if args.video_start else None, video_fps=args.video_fps,
                   screen=Path(args.screen).resolve() if args.screen else None,
                   screen_start=epoch(args.screen_start) if args.screen_start else None,
                   name=args.name, facts=args.fact, sample=args.sample)
    summary = manifest.read(folder)
    print(f"{folder}: {summary['counts']['events']} records, {summary['counts']['pictures']} pictures, "
          f"{len(summary['video']['parts'])} video part(s), {len(summary['reconstruction']['researchers'])} researchers; "
          "passes the dataset check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
