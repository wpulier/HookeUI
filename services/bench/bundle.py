"""A session bundle (session knowledge K4, contract 5 of docs/design/2026-10-07-session-knowledge.md): a
dataset (docs/datasets.md) plus that window's evidence, executions, outcomes, corrections and journal, in a
folder you name, for the replay bench (bench/replay_bench.py), the fine-tune job (bench/finetune_cells.py) and
any program or agent. `make bundle OUT= (DATASET= | FROM= TO= | DAY=)`.

- **Read-only on its sources.** It reads the day journals, the interaction and vote logs, saved evidence,
  researchers' folders and the datasets, and writes only into its own folder: built hidden beside it and
  renamed into place once whole, so a bundle that fails part way leaves nothing. It never writes inside the
  datasets folder, never over an existing folder, and deletes nothing.
- **What it holds** (the format: docs/datasets.md, "Session bundles"):
  - `dataset/`: the dataset itself when one is named (videos hard-linked, or copied across disks; the rest copied);
  - `records.jsonl`: the window's records as a dataset's events (`{t, run, stream, record}`: journal,
    interaction, vote), and the pictures they name under `pictures/`, the researchers' work under `research/`;
  - `executions.jsonl`, `votes.jsonl`: the window's execution and vote records, as logged;
  - `outcomes.json`: what those executions' outcomes are (orchestrator/outcomes.py, K3), per subject;
  - `corrections.jsonl`: the window's corrections (bench/export_corrections.py: segmented cells and relabels);
  - `evidence/<day>/<id>/`: the window's populations as saved (manifest, capture.jpg, masks, vectors), copied
    and checked against their checksums;
  - `captures/`: views of the window's other executions, taken from the dataset's camera video by the bridge's
    `(boot, seq)` (a frame of the H.264 recording, not the JPEG the app analysed: said so on each).
- **What it lacks is said in plain words** (`lacks` in bundle.json): no dataset or a reconstructed one, no video,
  no corrections (so the replay bench reports agreement only), views not kept, incomplete populations.

  python -m bench.bundle --out ~/bundles/leaf --from 2026-10-07T12:40 --to 2026-10-07T14:00
  python -m bench.bundle --out ~/bundles/sperm --dataset ds-20261006-012139
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.datasets import manifest as dataset_manifest  # noqa: E402
from orchestrator.datasets.importer import gather  # noqa: E402
from orchestrator.datasets.snapshot import _journaled_evidence, evidence_in  # noqa: E402
from orchestrator.datasets.writer import link_or_copy  # noqa: E402
from orchestrator.paths import REPO, Paths  # noqa: E402
from dataclasses import fields  # noqa: E402

SCHEMA = "bundle/1"
NAME = "bundle.json"
EXECUTION_KINDS = ("execution", "execution-use", "execution-output", "execution-revealed")
EVIDENCE_FILES = ("manifest.json", "capture.jpg", "masks.npz", "vectors.npz")
VIDEO_SUFFIXES = (".mp4", ".webm", ".mov", ".m4v")


# --- writing ---------------------------------------------------------------------------------------
def window_of(paths: Paths, *, dataset: str | None = None, start: str | None = None, stop: str | None = None,
              day: str | None = None) -> tuple[float, float, Path | None, dict | None]:
    """(start, stop, the dataset's folder, its summary): a dataset's own window, a day, or FROM..TO (local times).
    A dataset Record collected is bundled only once it is saved: after Stop the bridge still finishes its video,
    and the end check (`endCheck`, the Data light's "Saved") is written last."""
    if dataset:
        folder = Path(dataset).expanduser()
        if not folder.is_dir():
            folder = paths.datasets / dataset
        summary = dataset_manifest.read(folder)
        if summary is None:
            raise SystemExit(f"no dataset at {folder}")
        a, b = summary.get("startedAtEpoch"), summary.get("stoppedAtEpoch")
        if summary.get("status") == "collecting" or not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            raise SystemExit(f"{folder.name} is still collecting (Record is on): bundle it after Stop, once it is saved")
        if not summary.get("reconstructed") and summary.get("endedBy") not in ("snapshot", "reconstruction") \
                and not summary.get("endCheck"):
            raise SystemExit(f"{folder.name} is still being saved (Record stopped; its video and frame facts may not be "
                             "whole until the end check is written): bundle it once the Data light says Saved")
        return float(a), float(b), folder, summary
    if day:
        midnight = datetime.fromisoformat(day)
        following = (midnight + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight.timestamp(), following.timestamp() - 0.001, None, None  # 23 or 25 hours on a clock change
    if not (start and stop):
        raise SystemExit("name a dataset (--dataset), a day (--day) or a window (--from and --to)")
    a, b = datetime.fromisoformat(start).timestamp(), datetime.fromisoformat(stop).timestamp()
    if b <= a:
        raise SystemExit("--to must be after --from")
    return a, b, None, None


def write(paths: Paths, out: Path, *, dataset: str | None = None, start: str | None = None, stop: str | None = None,
          day: str | None = None, video: bool = True, clock=time.time) -> Path:
    """Write one bundle into `out` (a new folder). Returns it."""
    out = Path(out).expanduser().resolve()
    datasets = paths.datasets.expanduser().resolve()
    if out == datasets or datasets in out.parents:
        raise SystemExit(f"a bundle is never written inside the datasets folder ({datasets}); name another folder")
    homes = [paths.home, *(getattr(paths, f.name) for f in fields(Paths) if f.name not in ("home", "keys_file"))]
    for source in homes:  # the saves root and every save's own place (recordings, snapshots, models...)
        source = Path(source).expanduser().resolve()
        if source.suffix:  # a log file: its folder
            source = source.parent
        if out == source or source in out.parents:
            raise SystemExit(f"a bundle is never written inside the app's saves ({source}); name another folder")
    taken = (out.is_dir() and any(out.iterdir())) or (out.exists() and not out.is_dir())
    if taken:
        raise SystemExit(f"{out} already exists: a bundle is never written over anything")
    a, b, dataset_folder, summary = window_of(paths, dataset=dataset, start=start, stop=stop, day=day)
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = out.parent / f".{out.name}.bundling-{os.getpid()}"
    staging.mkdir()
    try:
        _write(paths, staging, out.name, a, b, dataset_folder, summary, video=video, clock=clock)
        if out.is_dir():
            out.rmdir()  # an empty folder the user made for it
        os.rename(staging, out)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return out


def _write(paths: Paths, folder: Path, bundle_id: str, start: float, stop: float, dataset_folder: Path | None,
           summary: dict | None, *, video: bool, clock) -> None:
    lacks: list[str] = []
    kept: dict[str, int] = {"linked": 0, "copied": 0}

    # The dataset itself, as it is.
    if dataset_folder is not None:
        for item in sorted(dataset_folder.rglob("*")):
            if not item.is_file():
                continue
            target = folder / "dataset" / item.relative_to(dataset_folder)
            if item.suffix.lower() in VIDEO_SUFFIXES:
                if not video:
                    continue
                how = link_or_copy(item, target)  # hard-linked: the same file, never written to
                kept["copied" if how == "copied" else "linked"] += 1
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
                kept["copied"] += 1
        if summary.get("reconstructed"):
            missing = (summary.get("reconstruction") or {}).get("notCaptured") or []
            lacks.append("The dataset was reconstructed afterwards from the logs, not collected by Record"
                         + (f"; it lacks: {'; '.join(str(m) for m in missing)}" if missing else "") + ".")
        if not video:
            lacks.append("Its videos were left out (--no-video), so no view can be taken from them.")
    else:
        lacks.append("No dataset: the window was not collected by Record, so it holds no per-frame facts and no "
                     "camera video of its own.")

    # The window's records (day journals, interaction and vote logs), the pictures they name, researchers' work:
    # copied, so nothing done to the bundle can reach the user's files (only a dataset's videos are linked).
    got = gather(paths, start, stop, folder, votes=True, link=copy)
    events = sorted(got.events, key=lambda e: e["t"])
    _lines(folder / "records.jsonl", events)
    executions = [e["record"] for e in events if e["stream"] == "interaction" and e["record"].get("kind") in EXECUTION_KINDS]
    _lines(folder / "executions.jsonl", executions)
    votes = [e["record"] for e in events if e["stream"] == "vote"]
    _lines(folder / "votes.jsonl", votes)

    # The window's populations, copied as saved and checked as the app checks what it reads back.
    populations, runs = [], {r["id"]: r for r in executions if r.get("kind") == "execution" and r.get("id")}
    for ev in evidence_in(paths, start, stop, _journaled_evidence(events)):
        row = {"id": ev["id"], "day": ev["day"], "status": ev["status"], "revision": ev["revision"], "members": ev["members"],
               "operation": ev["operation"], "implementation": ev["implementation"]}
        if ev["status"] != "whole":
            row["why"] = ev["why"]
            lacks.append(f"Population {ev['id']} is not saved whole ({ev['why']}); it is kept as it is, marked incomplete.")
        if ev["folder"] is not None:
            target = folder / "evidence" / ev["day"] / ev["id"]
            target.mkdir(parents=True, exist_ok=True)
            for name in EVIDENCE_FILES:
                if (ev["folder"] / name).is_file():
                    shutil.copy2(ev["folder"] / name, target / name)
            row["folder"] = f"evidence/{ev['day']}/{ev['id']}"
            try:
                made = json.loads((target / "manifest.json").read_text())
            except (OSError, ValueError):
                made = {}
            torn = [name for name, digest in (made.get("files") or {}).items()
                    if not (target / name).is_file() or hashlib.sha256((target / name).read_bytes()).hexdigest() != digest]
            if torn and row["status"] == "whole":  # a save landed while it was copied: the copy is checked, not the source
                row.update(status="incomplete", why=f"{', '.join(torn)} changed while it was copied; bundle it again")
                lacks.append(f"Population {ev['id']} is not saved whole ({row['why']}); it is kept as it is, marked incomplete.")
            execution = made.get("execution") or (made.get("madeBy") or {}).get("execution")
            view = made.get("view") or {}
            record = runs.get(execution) or {}
            row.update(execution=execution, subject=record.get("subject"), session=session_of(made),
                       madeAt=made.get("madeAt"), corrected=any((r.get("was") or r.get("added")) for r in made.get("revisions") or []),
                       capture={"id": made.get("capture"), "boot": view.get("boot"), "seq": view.get("seq"), "run": view.get("run"),
                                "file": f"{row['folder']}/capture.jpg" if (target / "capture.jpg").exists() else None},
                       prompt={"kind": "field", "crop": (made.get("producer") or {}).get("configuration", {}).get("crop")
                               or made.get("region"), "disc": view.get("disc")})
            if not record:
                row["subjectWhy"] = "its execution record is not in the window's interaction log"
        populations.append(row)

    # Corrections: of segmented cells (the bundle's own copies), and relabels of researchers started in the window.
    from bench.export_corrections import rows as label_rows, segmentation_rows

    corrections = segmentation_rows(folder / "evidence")
    for row in corrections:  # the bundle's own copies, by their place in it (the folder is renamed once whole)
        for key in ("capture", "masks"):
            if row.get(key):
                row[key] = str(Path(row[key]).relative_to(folder))
    linked = {rid for _, rid in got.linked}
    corrections += [{"kind": "label", **r} for r in label_rows(paths.research) if r.get("rid") in linked]
    _lines(folder / "corrections.jsonl", corrections)
    corrected = [p for p in populations if p.get("corrected")]

    # Outcomes of the window's executions (K3), from the bundle's own records.
    from orchestrator.operations import label_of
    from orchestrator.outcomes import Outcomes

    tallies = Outcomes([folder / "executions.jsonl"], folder / "evidence", folder / "votes.jsonl").tallies()
    outcomes = [{"subject": t.subject, "operation": t.operation, "implementation": t.implementation, "policy": t.policy,
                 "day": t.day, "runs": t.runs, "empty": t.empty, "failed": t.failed,
                 "msMedian": round(float(np.median(t.ms)), 1) if t.ms else None, "members": t.members, "deleted": t.deleted,
                 "merged": t.merged, "undone": t.undone, "won": t.won, "lost": t.lost, "executions": t.executions,
                 "words": t.words(f"{label_of(t.implementation)} ({t.policy})", time.strftime("%Y-%m-%d", time.localtime(stop)))}
                for t in tallies]
    (folder / "outcomes.json").write_text(json.dumps(outcomes, indent=1) + "\n")

    # Views of the other executions, from the dataset's camera video by (boot, seq).
    captures = _captures(folder, dataset_folder, summary, executions, populations, lacks, video=video)

    if not populations:
        lacks.append("No population in this window: nothing segmented every cell here, so the cell replay has nothing "
                     "to run on.")
    if not [c for c in corrections if c["kind"] == "segmentation"]:
        lacks.append("No corrections of segmented cells: the replay bench can report agreement with what was recorded, "
                     "not accuracy.")
    if not executions:
        lacks.append("No executions were logged in this window.")

    made_at = clock()
    by_operation: dict[str, int] = {}
    for r in executions:
        if r.get("kind") == "execution":
            by_operation[r.get("operation") or "?"] = by_operation.get(r.get("operation") or "?", 0) + 1
    description = {
        "schema": SCHEMA, "kind": "microscopemind-bundle", "id": bundle_id,
        "made": {"at": dataset_manifest.iso(made_at), "atEpoch": round(made_at, 3), "by": "make bundle", "build": _build()},
        "window": {"from": dataset_manifest.iso(start), "to": dataset_manifest.iso(stop), "fromEpoch": round(start, 3),
                   "toEpoch": round(stop, 3), "of": f"dataset {dataset_folder.name}" if dataset_folder else "a window"},
        "sources": {"dataset": str(dataset_folder) if dataset_folder else None, "evidence": str(paths.evidence),
                    "sessions": str(paths.sessions), "interactions": str(paths.interaction_log), "votes": str(paths.votes_log),
                    "research": str(paths.research), "read": got.read},
        "dataset": None if summary is None else {"id": summary.get("id"), "name": summary.get("name"),
                                                 "reconstructed": bool(summary.get("reconstructed")),
                                                 "endedBy": summary.get("endedBy"), "files": kept},
        "holds": {"records": len(events), "streams": _count(e["stream"] for e in events), "executions": by_operation,
                  "votes": len(votes), "populations": len(populations), "correctedPopulations": len(corrected),
                  "corrections": _count(c["kind"] for c in corrections), "captures": _count(c["source"] for c in captures),
                  "pictures": got.pictures, "researchers": len(got.research)},
        "populations": populations, "captures": captures,
        "lacks": lacks,
        "readOnly": "Every source was only read: nothing in them was written, moved or removed.",
        "format": "docs/datasets.md, Session bundles",
    }
    (folder / NAME).write_text(json.dumps(description, indent=1, default=str) + "\n")


def _captures(folder: Path, dataset_folder: Path | None, summary: dict | None, executions: list[dict],
              populations: list[dict], lacks: list[str], *, video: bool) -> list[dict]:
    """Where each execution's view is: its population's capture.jpg, a frame of the dataset's camera video by
    (boot, seq), or nowhere (said)."""
    out, missing = [], []
    by_execution = {p.get("execution"): p for p in populations if p.get("execution")}
    frames = _frames(dataset_folder)  # (run, frame id) -> (boot, seq)
    runs = (summary or {}).get("runs") or []
    parts = ((summary or {}).get("video") or {}).get("parts") or []
    for record in executions:
        if record.get("kind") != "execution" or record.get("capture") is None:
            continue
        entry = {"execution": record.get("id"), "operation": record.get("operation"), "capture": record.get("capture")}
        population = by_execution.get(record.get("id"))
        if population is not None and (population.get("capture") or {}).get("file"):
            out.append({**entry, "source": "evidence", "file": population["capture"]["file"],
                        "boot": population["capture"].get("boot"), "seq": population["capture"].get("seq")})
            continue
        run = _run_at(runs, record.get("startedAt"))
        found = frames.get((run, int(record["capture"]))) if run is not None else None
        if found is None or not video:
            missing.append(entry)
            continue
        boot, seq = found
        file = _from_video(dataset_folder, parts, boot, seq, folder / "captures" / f"r{run}-f{record['capture']}.jpg")
        if file is None:
            missing.append(entry)
            continue
        out.append({**entry, "source": "video", "file": str(file.relative_to(folder)), "boot": boot, "seq": seq,
                    "note": "a frame of the camera recording (H.264, re-encoded by the recorder), not the JPEG the app analysed"})
    if missing:
        if dataset_folder is None:
            why = "the window has no dataset, so no camera video to take them from, and only populations keep their view"
        elif not video:
            why = "the dataset's videos were left out"
        elif not frames:
            why = "the dataset has no per-frame facts naming each frame's bridge frame (boot, seq), so no video frame can be found"
        else:
            why = "no camera video frame was found for their bridge frame (boot, seq)"
        lacks.append(f"{len(missing)} execution{'s have' if len(missing) != 1 else ' has'} no view kept ("
                     + ", ".join(f"{m['execution']} {m['operation']}" for m in missing[:6])
                     + (", ..." if len(missing) > 6 else "") + f"): {why}.")
    return out


def _frames(dataset_folder: Path | None) -> dict[tuple[int, int], tuple[str, int]]:
    found: dict[tuple[int, int], tuple[str, int]] = {}
    path = dataset_folder / dataset_manifest.FRAMES if dataset_folder is not None else None
    if path is None or not path.is_file():
        return found
    for raw in path.read_text().splitlines():
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        if line.get("boot") and line.get("seq") is not None and line.get("id") is not None:
            found[(int(line.get("run") or 1), int(line["id"]))] = (str(line["boot"]), int(line["seq"]))
    return found


def _run_at(runs: list[dict], at) -> int | None:
    if not isinstance(at, (int, float)):
        return None
    for run in runs:
        a, b = run.get("startedAtEpoch"), run.get("endedAtEpoch")
        if isinstance(a, (int, float)) and at >= a and (not isinstance(b, (int, float)) or at <= b):
            return int(run.get("n") or 1)
    return None


def _from_video(dataset_folder: Path, parts: list[dict], boot: str, seq: int, target: Path) -> Path | None:
    """The frame the bridge numbered (boot, seq), from the part whose sidecar has it (docs/datasets.md: the
    sidecar's `i` is the frame's index in the video), written as a JPEG. None when no part has it."""
    import cv2

    for part in parts:
        if part.get("boot") != boot or not part.get("sidecar") or not part.get("video"):
            continue
        sidecar, movie = dataset_folder / part["sidecar"], dataset_folder / part["video"]
        if not sidecar.is_file() or not movie.is_file():
            continue
        index = None
        for raw in sidecar.read_text().splitlines():
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            if line.get("seq") == seq and "i" in line:
                index = int(line["i"])
                break
        if index is None:
            continue
        cap = cv2.VideoCapture(str(movie))  # a file on disk, never the phone (AGENTS.md)
        try:
            cap.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, image = cap.read()
        finally:
            cap.release()
        if not ok:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(target), image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        return target
    return None


def session_of(manifest: dict) -> str:
    """The session a population belongs to: its day (AGENTS.md: a session is a day). Runs of the app within a day are
    one session, so a fine-tune never holds out a view its own day trained on (the K4 review's B1)."""
    return str(manifest.get("day") or "?")


def copy(source: Path, target: Path) -> str:
    """Keep a file in the bundle as its own copy (never a hard link to the user's file)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return "exists"
    shutil.copy2(source, target)
    return "copied"


def _lines(path: Path, items) -> None:
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")


def _count(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return out


def _build() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True,
                              timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


# --- reading ---------------------------------------------------------------------------------------
@dataclass
class Recorded:
    """One population of a bundle, as the replay bench and the fine-tune need it."""
    bundle: Path
    row: dict
    manifest: dict
    folder: Path
    masks: dict = field(default_factory=dict)  # member id -> Mask (every mask ever saved: revision 0 and merges)

    @property
    def id(self) -> str:
        return self.row["id"]

    @property
    def subject(self) -> str:
        return self.row.get("subject") or "subject not recorded"

    @property
    def session(self) -> str:
        return self.row.get("session") or session_of(self.manifest)

    @property
    def day(self) -> str:
        return str(self.row.get("day") or self.manifest.get("day") or "")

    def sha256(self) -> str:
        return hashlib.sha256(self.jpeg()).hexdigest()

    def frame(self) -> tuple:
        """The bridge's frame the view was (boot, seq); (None, None) when the view does not say."""
        source = self.manifest.get("source") or {}
        view = self.manifest.get("view") or {}
        boot = source.get("boot") or view.get("boot") or None
        seq = source.get("seq") if source.get("seq") is not None else view.get("seq")
        return (boot, int(seq) if seq is not None else None)

    def jpeg(self) -> bytes:
        return (self.folder / "capture.jpg").read_bytes()

    def model_members(self) -> list[str]:
        """Revision 0: the members the model itself returned (none a correction added)."""
        added = {a for r in self.manifest.get("revisions") or [] for a in (r.get("added") or [])}
        return [m["id"] for m in self.manifest.get("members") or [] if m["id"] not in added]

    def current(self) -> dict[str, str]:
        """Each member's status now, after every revision."""
        return {m["id"]: m.get("status") or "model" for m in self.manifest.get("members") or []}


def load(folder: Path) -> dict:
    return json.loads((Path(folder) / NAME).read_text())


def recorded(folder: Path) -> list[Recorded]:
    """The bundle's whole populations, each with its masks read back (as evidence.Evidence reads them)."""
    from orchestrator.measure import Mask

    folder = Path(folder)
    out = []
    for row in load(folder).get("populations") or []:
        if row.get("status") != "whole" or not row.get("folder"):
            continue
        place = folder / row["folder"]
        made = json.loads((place / "manifest.json").read_text())
        masks = {}
        if (place / "masks.npz").exists():
            with np.load(place / "masks.npz") as data:
                for name in data.files:
                    if not name.endswith(".bits"):
                        continue
                    mid = name[: -len(".bits")]
                    h, w, x0, y0, sc = data[f"{mid}.meta"]
                    bits = np.unpackbits(data[name])[: int(h) * int(w)].reshape(int(h), int(w)).astype(bool)
                    masks[mid] = Mask(bits, int(x0), int(y0), float(sc))
        out.append(Recorded(folder, row, made, place, masks))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a session bundle (read-only on its sources).")
    parser.add_argument("--out", required=True, help="the new folder to write it into (never inside the datasets folder)")
    parser.add_argument("--dataset", help="a dataset id (in the datasets folder) or folder: its window")
    parser.add_argument("--from", dest="start", help="the window's start, local time (2026-10-07T12:40)")
    parser.add_argument("--to", dest="stop", help="the window's end, local time")
    parser.add_argument("--day", help="a whole day (2026-10-07)")
    parser.add_argument("--no-video", action="store_true", help="leave a dataset's videos out")
    args = parser.parse_args(argv)
    out = write(Paths.from_env(), Path(args.out), dataset=args.dataset, start=args.start, stop=args.stop, day=args.day,
                video=not args.no_video)
    description = load(out)
    holds = description["holds"]
    print(f"{out}: {holds['records']} records, {sum(holds['executions'].values())} executions, {holds['populations']} "
          f"populations ({holds['correctedPopulations']} corrected), {sum(holds['captures'].values())} views kept")
    for line in description["lacks"]:
        print(f"  lacks: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
