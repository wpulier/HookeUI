"""The datasets on disk, as the Datasets tab lists them: where they are, what each holds, renaming.

Datasets are for programs and agents (docs/datasets.md); the tab only says where they are and what
is in them. A rename is appended to the dataset's own events and its summary rewritten, never a
change to what was recorded.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import manifest
from .manifest import folder_bytes

ID = re.compile(r"^ds-\d{8}-\d{6}(-\d+)?$")
NAME_CHARS = 120


def find(root: Path, dataset_id: str) -> Path | None:
    """A dataset's folder by its id; None for anything that is not one (no paths from a request)."""
    if not ID.match(dataset_id or ""):
        return None
    folder = Path(root) / dataset_id
    return folder if (folder / manifest.NAME).is_file() else None


def thumb(folder: Path, summary: dict) -> Path | None:
    """The picture the Datasets tab shows for a dataset, from its own pictures: of the views the running
    look kept (whole eyepiece views) from the middle half of its window, the most detailed (the largest
    file: an empty field compresses to little); then any picture from then; then the minute before the
    start; then a snapshot. None when it holds no picture."""
    pictures = Path(folder) / manifest.PICTURES
    if not pictures.is_dir():
        return None
    start, stop = summary.get("startedAtEpoch"), summary.get("stoppedAtEpoch") or time.time()
    middle = (start + (stop - start) / 4, stop - (stop - start) / 4) if isinstance(start, (int, float)) else None

    def inside(p: Path) -> bool:
        return middle is None or middle[0] <= p.stat().st_mtime <= middle[1]

    for pattern, timed in (("crops/**/*-sight-*.jpg", True), ("crops/**/*.jpg", True), ("crops/**/*.jpg", False),
                           ("preroll/*.jpg", False), ("snapshots/*.png", False)):
        found = [p for p in pictures.glob(pattern) if p.is_file() and (not timed or inside(p))]
        if found:
            return max(found, key=lambda p: p.stat().st_size)
    return None


def row(folder: Path, summary: dict, sizes: bool = True) -> dict:
    parts = summary.get("video", {}).get("parts", [])
    files = {}
    if sizes:
        for name in (manifest.EVENTS, manifest.FRAMES, manifest.STATES):
            path = folder / name
            files[name] = path.stat().st_size if path.exists() else 0
    return {
        "id": summary.get("id") or folder.name, "name": summary.get("name") or "", "status": summary.get("status"),
        "startedAt": summary.get("startedAt"), "startedAtEpoch": summary.get("startedAtEpoch"),
        "stoppedAt": summary.get("stoppedAt"), "stoppedAtEpoch": summary.get("stoppedAtEpoch"),
        "endedBy": summary.get("endedBy"), "reconstructed": bool(summary.get("reconstructed")),
        "runs": len(summary.get("runs", [])), "gaps": summary.get("gaps", []),
        # The window as the tab's ribbon draws it: when the app collected, and the gaps between.
        "runSpans": [[r.get("startedAtEpoch"), r.get("endedAtEpoch")] for r in summary.get("runs", [])],
        "thumb": sizes and thumb(folder, summary) is not None,
        "parts": [{"n": p.get("n"), "videoKept": p.get("videoKept"), "sidecarKept": p.get("sidecarKept"),
                   "hasSidecar": bool(p.get("sidecar")), "joined": p.get("joined"),
                   "bytes": (folder / p["video"]).stat().st_size if sizes and (folder / p["video"]).exists() else 0}
                  for p in parts],
        "counts": summary.get("counts", {}), "rig": summary.get("rig", {}),
        "enteredAfter": [{"field": f.get("field"), "value": f.get("value")} for f in summary.get("rigEnteredAfter") or []],
        "sample": {k: (v or {}).get("value") for k, v in (summary.get("sample") or {}).items()},
        "screens": len(summary.get("screen") or []), "research": len((summary.get("reconstruction") or {}).get("researchers") or []),
        # The pictures themselves, not their index (pictures/preroll.jsonl): the same number the writer counts.
        "pictures": sum(1 for p in (folder / manifest.PICTURES).rglob("*") if p.is_file() and p.suffix != ".jsonl")
                    if sizes and (folder / manifest.PICTURES).is_dir() else 0,
        "files": files, "bytes": folder_bytes(folder) if sizes else 0, "path": str(folder),
        # After Record stopped: whether it was checked as saved whole (checks.py), and if not, why.
        "endCheck": ({k: (summary.get("endCheck") or {}).get(k) for k in ("ok", "problems", "notes")}
                     if summary.get("endCheck") else None),
    }


def listing(root: Path) -> dict:
    return {"root": str(root), "datasets": [row(folder, summary) for folder, summary in manifest.all_in(Path(root))]}


def rename_closed(folder: Path, name: str, clock=time.time) -> dict:
    """Rename a dataset that is not collecting: its summary rewritten, the rename appended to its events."""
    summary = manifest.read(folder) or {}
    name = clean_name(name)
    summary["name"] = name
    manifest.write(folder, summary)
    with (folder / manifest.EVENTS).open("a", encoding="utf-8") as f:
        f.write(json.dumps({"t": round(clock(), 4), "run": len(summary.get("runs", [])), "stream": "dataset",
                            "record": {"event": "renamed", "name": name}}, ensure_ascii=False, separators=(",", ":")) + "\n")
    return summary


TRASH = ".trash"


def trash(root: Path, dataset_id: str, clock=time.time) -> Path:
    """The user deletes a dataset (the Datasets tab): it is moved to datasets/.trash/, whole, and
    leaves the list; emptying the trash is the user's (in the Finder). Never one being collected."""
    folder = find(root, dataset_id)
    if folder is None:
        raise ValueError("no such dataset")
    summary = manifest.read(folder) or {}
    if summary.get("status") == "collecting":
        raise ValueError("it is being collected: switch Data off first")
    target = Path(root) / TRASH / f"{folder.name}-trashed-{time.strftime('%Y%m%d-%H%M%S', time.localtime(clock()))}"
    target.parent.mkdir(parents=True, exist_ok=True)
    folder.rename(target)
    return target


def clean_name(name: str) -> str:
    return " ".join(str(name or "").split())[:NAME_CHARS]
