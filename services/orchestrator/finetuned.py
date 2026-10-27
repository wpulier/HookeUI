"""Fine-tuned models (session knowledge K4, contract 5 of docs/design/2026-10-07-session-knowledge.md;
ADR 0013): weights an explicit fine-tune job made from the user's corrected cells (bench/finetune_cells.py),
kept so the app can use them as an implementation of their operation (CELLS "cellpose-sam-tuned").

- **One folder per version** in the catalog entry's own folder, `<catalog>/models/<name>/v<n>/` (beside a downloaded
  model's revisions; paths.finetuned): the weights file and `model.json` (what it
  was made from: the base model, the corrected populations it trained on, the sessions held out, the
  settings, the losses, the weights' checksum and its licence tier). Written into a hidden folder and
  renamed into place only once whole, the description last; a version is never written over, and nothing
  here deletes one (an asset: the user's, kept until they delete it; docs/saves.md).
- **The current version** is the newest whole one. An implementation that needs one (`Impl.weights`,
  `Option.weights`) is "not available here" until it exists, and says how to make one.
- **Nothing trains from clicks automatically**, and the app never recommends a version because it exists:
  only a win on held-out sessions in the replay bench (docs/bench/replay.md) can back a recommendation,
  and the user decides.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

SCHEMA = "finetuned/1"
META = "model.json"
WEIGHTS = "weights"  # the trainer's own file (cellpose: the network's state dict)
HOW = "make finetune-cells"


def root(paths=None) -> Path:
    """Where fine-tuned versions are kept: the catalog's models folder, each in its entry's own folder (the catalog
    audit's G3: they were under the assets' models/, apart from everything else the catalog holds; paths.finetuned)."""
    from . import paths as where

    return where.finetuned()


def adopt_old(old: Path, name: str, where: Path | None = None) -> list[str]:
    """Versions made before G3, in the assets' models/ folder (`old`), cloned into the catalog's (APFS clones: no
    duplicate), each under its own number and only when that number is free there: never written over, and the old
    folder kept. Returns the versions adopted. `make catalog-adopt` runs it."""
    from .downloads import clone

    base = (where if where is not None else root()) / name
    adopted = []
    for v in versions(name, old):
        dest = base / v["version"]
        if not v["whole"] or dest.exists():
            continue
        base.mkdir(parents=True, exist_ok=True)
        staging = base / f".{v['version']}.adopting-{os.getpid()}"
        staging.mkdir()
        try:
            for part in (WEIGHTS, META):
                clone(Path(v["folder"]) / part, staging / part)
            os.rename(staging, dest)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        adopted.append(v["version"])
    return adopted


def _number(folder: Path) -> int | None:
    name = folder.name
    return int(name[1:]) if name.startswith("v") and name[1:].isdigit() else None


def versions(name: str, where: Path | None = None) -> list[dict]:
    """Every version of `name`, oldest first: its description with `folder`, `weights` (paths) and `whole`
    (its description is there, and its weights file has the size it says; a version that is not whole is
    listed, never used)."""
    base = (where if where is not None else root()) / name
    found = []
    for folder in sorted((p for p in base.iterdir() if p.is_dir()) if base.is_dir() else [], key=lambda p: _number(p) or -1):
        n = _number(folder)
        if n is None:
            continue  # a hidden folder still being written, or anything else
        entry = {"name": name, "version": f"v{n}", "n": n, "folder": str(folder), "weights": str(folder / WEIGHTS),
                 "whole": False}
        try:
            meta = json.loads((folder / META).read_text())
            entry.update({k: v for k, v in meta.items() if k not in ("folder", "weights", "whole", "n")})
            entry["whole"] = (folder / WEIGHTS).is_file() and (folder / WEIGHTS).stat().st_size == meta.get("bytes")
        except (OSError, ValueError):
            entry["why"] = "its description (model.json) is missing or unreadable"
        if not entry["whole"] and "why" not in entry:
            entry["why"] = "its weights file is missing or not the size its description gives"
        found.append(entry)
    return found


def current(name: str, where: Path | None = None) -> dict | None:
    """The newest whole version of `name`, or None."""
    whole = [v for v in versions(name, where) if v["whole"]]
    return whole[-1] if whole else None


def missing(name: str, where: Path | None = None) -> str:
    """Why `name` cannot be used here ("" when a whole version exists)."""
    if not name or current(name, where) is not None:
        return ""
    return f"no fine-tuned weights here yet ({HOW} makes them from your corrected cells)"


def verify(entry: dict) -> bool:
    """The weights file's SHA-256 is the one its description gives (read in full: for a bench, not a click)."""
    digest = hashlib.sha256()
    with open(entry["weights"], "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest() == entry.get("sha256")


def write(name: str, weights_file: Path, meta: dict, where: Path | None = None) -> dict:
    """Keep `weights_file` as the next version of `name`, with `meta` as its description. Moved (or copied
    across disks) into a hidden folder, its checksum taken, its description written last, then renamed into
    place: a version that is there is whole, and an existing one is never written over."""
    base = (where if where is not None else root()) / name
    base.mkdir(parents=True, exist_ok=True)
    taken = [v["n"] for v in versions(name, base.parent)]
    n = max(taken, default=0) + 1
    while (base / f"v{n}").exists():
        n += 1
    staging = base / f".v{n}.saving-{os.getpid()}"
    staging.mkdir()
    try:
        target = staging / WEIGHTS
        try:
            os.replace(weights_file, target)
        except OSError:
            shutil.copy2(weights_file, target)
        digest = hashlib.sha256()
        with open(target, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                digest.update(block)
        description = {**meta, "schema": SCHEMA, "name": name, "version": f"v{n}", "bytes": target.stat().st_size,
                       "sha256": digest.hexdigest()}
        with open(staging / META, "w") as f:
            f.write(json.dumps(description, indent=1, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())
        final = base / f"v{n}"
        if final.exists():  # another job took the number meanwhile: never written over
            raise FileExistsError(f"{final} was made while this one was written; run it again")
        os.rename(staging, final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return next(v for v in versions(name, base.parent) if v["n"] == n)
