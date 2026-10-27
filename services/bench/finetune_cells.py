"""Fine-tune Cellpose-SAM on your corrected cells (session knowledge K4, contract 5 of
docs/design/2026-10-07-session-knowledge.md): an explicit job outside the app, never a live feature, and
nothing ever trains from clicks. `make finetune-cells`.

1. **The training set** is the M3.4 export of corrected populations (revision 1 and on;
   bench/export_corrections.py): for each, the green channel of its view inside the crop the finder was given
   (as the finder sees it), and a label map of its members as they stand now: the model's own masks with your
   corrections applied (cells you removed are gone, cells you merged are one). Cells you did not correct are
   taken as the model made them, and cells it missed stay background: no correction adds a cell. model.json
   says so. A corrected population whose masks are missing or fail their checksum is left out, and said.
2. **Held-out days:** a session is a day (AGENTS.md), so whole days are split: the newest held out (`--hold-out`,
   at least one, or the days named), never trained on, so the replay bench can judge the result on them. Runs of
   the app within a day are one session. A split is refused when a held-out view is also on the training side (the
   same JPEG, or the same bridge frame `(boot, seq)`). A fine-tune needs corrected cells from at least two days.
3. **Training** is cellpose's own (`noncommercial/cellpose_cells.train`: cellpose.train.train_seg), reached only
   through the licence router, so the commercial edition refuses it; the held-out days are never given to it. It
   waits for the app (bench/live.py) unless told to run beside it, and checks there is room for the weights first.
   The weights become the next version of "cellpose-sam-tuned" (orchestrator/finetuned.py), never over an earlier
   one, with what it trained on and held out (by view, never by a path that can go), the split's basis, its
   settings, losses, checksum, the base network it started from and its licence tier (non-commercial).
4. **Use and recommendation:** usable at once as CELLS "cellpose-sam-tuned"; recommended only if it wins on its
   held-out days in the replay bench (make replay-bench, docs/bench/replay.md), and the user decides.

  cd services && MICROMIND_EDITION=research nice -n 10 .venv/bin/python -m bench.finetune_cells [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bench import live  # noqa: E402
from bench.export_corrections import segmentation_rows  # noqa: E402
from orchestrator import finetuned, licences  # noqa: E402
from orchestrator.paths import REPO, Paths  # noqa: E402

NAME = "cellpose-sam-tuned"  # the CELLS implementation its weights are for (operations.CELLS, options.CELLS)
BASE = "cellpose-sam"
BASIS = "day"  # what a held-out session is (AGENTS.md: a session is a day)
SPARE = 64 * 1024 * 1024  # room kept beyond the weights the job writes
LABELS_ARE = ("the model's own masks with your corrections applied (cells you removed are gone, cells you merged are one); "
              "cells you did not correct are taken as the model made them, and cells it missed are background, since no "
              "correction adds a cell")
RECOMMENDATION = "recommended only if it wins on its held-out days in the replay bench (docs/bench/replay.md); the user decides"


class Refused(Exception):
    """The split or the job cannot go ahead; the message says why. Nothing was written."""


@dataclass
class Example:
    population: str
    day: str
    session: str
    revision: int
    image: np.ndarray
    labels: np.ndarray
    members: int
    removed: int
    merged: int
    made_at: float
    sha256: str  # the view's JPEG, as saved
    boot: str | None
    seq: int | None
    crop: list = field(default_factory=list)

    def row(self) -> dict:
        """What it trained on or held out, by the view itself (its JPEG's SHA-256 and the bridge's frame) and its
        evidence's day and id: never a path, which a deleted bundle would take away."""
        return {"population": self.population, "day": self.day, "session": self.session, "revision": self.revision,
                "members": self.members, "removed": self.removed, "merged": self.merged, "captureSha256": self.sha256,
                "boot": self.boot, "seq": self.seq, "crop": self.crop, "size": list(self.image.shape)}


def examples(evidence: list[Path], picture, skipped: list | None = None) -> list[Example]:
    """Every corrected population (revision 1 and on) under the evidence folders, as a training example:
    `picture(jpeg, crop)` is the finder's own input (the adapter's), the labels its members as they stand now. A
    population whose masks or view cannot be used is added to `skipped` with why."""
    from bench.bundle import session_of
    from orchestrator.evidence import _full_res
    from orchestrator.measure import Mask

    skipped = skipped if skipped is not None else []
    found: dict[tuple[str, str], Example] = {}
    for root in evidence:
        for row in segmentation_rows(Path(root)):
            where = f"{row.get('day')}/{row.get('population')}"
            if not row.get("masks") or not row.get("capture"):
                why = row.get("masksProblem") and f"its masks {row['masksProblem']}" or \
                    ("its view is missing" if not row.get("capture") else "its masks are missing")
                if (where, why) not in skipped:
                    skipped.append((where, why))
                continue
            folder = Path(row["masks"]).parent
            key = (row.get("day") or "", str(folder))
            if key in found:
                continue
            manifest = json.loads((folder / "manifest.json").read_text())
            view = manifest.get("view") or {}
            source = manifest.get("source") or {}
            crop = ((manifest.get("producer") or {}).get("configuration") or {}).get("crop") or manifest.get("region") \
                or [0, 0, int(view.get("width") or 0), int(view.get("height") or 0)]
            x, y, w, h = (int(v) for v in crop)
            labels = np.zeros((h, w), np.uint32 if len(manifest.get("members") or []) > 65535 else np.uint16)
            live_ids = [m["id"] for m in manifest.get("members") or [] if m.get("status") not in ("deleted", "merged")]
            with np.load(folder / "masks.npz") as data:
                n = 0
                for mid in live_ids:
                    if f"{mid}.bits" not in data.files:
                        continue
                    mh, mw, x0, y0, sc = data[f"{mid}.meta"]
                    bits = np.unpackbits(data[f"{mid}.bits"])[: int(mh) * int(mw)].reshape(int(mh), int(mw)).astype(bool)
                    full = _full_res(Mask(bits, int(x0), int(y0), float(sc)))
                    ox, oy = int(x0) - x, int(y0) - y  # into the crop's own pixels
                    ys = slice(max(0, oy), min(h, oy + full.shape[0]))
                    xs = slice(max(0, ox), min(w, ox + full.shape[1]))
                    part = full[ys.start - oy:ys.stop - oy, xs.start - ox:xs.stop - ox]
                    if part.any():
                        n += 1
                        labels[ys, xs][part] = n
            jpeg = (folder / "capture.jpg").read_bytes()
            statuses = [m.get("status") for m in manifest.get("members") or []]
            boot = source.get("boot") or view.get("boot") or None
            seq = source.get("seq") if source.get("seq") is not None else view.get("seq")
            found[key] = Example(manifest.get("id"), manifest.get("day") or "", session_of(manifest),
                                 int(manifest.get("revision") or 0), picture(jpeg, [x, y, w, h]), labels, n,
                                 statuses.count("deleted"), statuses.count("merged"), float(manifest.get("madeAt") or 0.0),
                                 hashlib.sha256(jpeg).hexdigest(), boot, int(seq) if seq is not None else None, [x, y, w, h])
    return sorted(found.values(), key=lambda e: (e.session, e.made_at))


def split(found: list[Example], hold_out: int = 1, days: list[str] | None = None) -> tuple[list[Example], list[Example]]:
    """(trained on, held out) by day, never one day split across both: the newest `hold_out` days held out, or the
    days named. Refused when a named day has no corrected cells, or when a held-out view is also on the training
    side (the same JPEG, or the same bridge frame)."""
    order = sorted({e.session for e in found})
    if days:
        unknown = [d for d in days if d not in order]
        if unknown:
            raise Refused(f"no corrected cells on {', '.join(unknown)} to hold out; the days with corrected cells are "
                          f"{', '.join(order) or 'none'}")
        held = set(days)
    else:
        held = set(order[-hold_out:] if hold_out > 0 else [])
    train, test = [e for e in found if e.session not in held], [e for e in found if e.session in held]
    for e in test:
        twin = next((t for t in train if t.sha256 == e.sha256 or (e.boot and e.seq is not None and (t.boot, t.seq) ==
                                                                     (e.boot, e.seq))), None)
        if twin is not None:
            what = "the same picture" if twin.sha256 == e.sha256 else f"the same bridge frame ({e.boot}, {e.seq})"
            raise Refused(f"the held-out {e.session} {e.population} is {what} as {twin.session} {twin.population} on the "
                          "training side, so it would be judged on what it trained on")
    return train, test


def room(paths: Paths, adapter, net) -> str:
    """Why there is no room for the weights the job writes ("" when there is): the trainer's file and the version
    kept, each the size of the network, beside the models folder."""
    if net is None:
        size = adapter.base_weights(digest=False).get("bytes") or 1_300_000_000  # its file, or Cellpose-SAM's 1.2 GB
    else:
        size = sum(p.numel() * p.element_size() for p in net.parameters())
    need = 2 * int(size) + SPARE
    place = Path(paths.models)
    while not place.exists() and place != place.parent:
        place = place.parent
    free = shutil.disk_usage(place).free
    if free < need:
        return (f"fine-tuning writes about {need / 1e9:.1f} GB (the trainer's file and the version kept) and only "
                f"{free / 1e9:.1f} GB is free at {place}")
    return ""


def finetune(paths: Paths, *, evidence: list[Path] | None = None, hold_out: int = 1, days: list[str] | None = None,
             epochs: int = 100, learning_rate: float = 1e-5, net=None, block: int | None = None, dry_run: bool = False,
             beside_live: bool = False, say=print) -> dict | None:
    """The job: the training set, the split, the training, the new version. Returns the version written (None for
    a dry run, or when there is nothing to train on or the split is refused: said why)."""
    try:
        adapter = licences.adapter(NAME)  # refused in the commercial edition (ADR 0005)
    except licences.NotInEdition as exc:
        raise SystemExit(f"fine-tuning Cellpose-SAM is not possible here: {exc}")
    skipped: list = []
    found = examples(evidence or [paths.evidence], adapter.picture, skipped)
    for where, why in skipped:
        say(f"Left out {where}: {why}.")
    if not found:
        say("No corrected cells yet (a population at revision 1 or later): nothing to fine-tune on. Correct some cells "
            "first (\"that's not a cell\", \"these two are one cell\"), then run this again. Nothing was written.")
        return None
    try:
        train, held = split(found, hold_out, days)
    except Refused as exc:
        say(f"Not fine-tuned: {exc}. Nothing was written.")
        return None
    say(f"{len(found)} corrected populations from {len({e.session for e in found})} days: training on {len(train)} "
        f"({', '.join(sorted({e.session for e in train})) or 'none'}), holding out {len(held)} "
        f"({', '.join(sorted({e.session for e in held})) or 'none'})")
    if not train or not held:
        say("Fine-tuning needs corrected cells from at least two days: one to train on and one held out for the replay "
            "bench to judge it on (runs of the app on one day are one session). Nothing was written.")
        return None
    if dry_run:
        say(json.dumps({"basis": BASIS, "trainOn": [e.row() for e in train], "heldOut": [e.row() for e in held]}, indent=1))
        return None
    for why in (live.refuse("Fine-tuning", beside_live), room(paths, adapter, net)):
        if why:
            say(f"Not fine-tuned: {why}. Nothing was written.")
            return None
    started = time.time()
    base = adapter.base_weights() if net is None else {"network": "a stand-in network the caller gave"}
    with tempfile.TemporaryDirectory(prefix="finetune-") as scratch:
        result = adapter.train([e.image for e in train], [e.labels for e in train], epochs=epochs,
                               learning_rate=learning_rate, out=Path(scratch), name=NAME, net=net, block=block)
        meta = {
            "base": {"implementation": BASE, "version": "cellpose cpsam", **base},
            "madeAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "madeAtEpoch": round(time.time(), 3),
            "madeBy": "make finetune-cells (bench/finetune_cells.py)", "build": _build(), "seconds": round(time.time() - started, 1),
            "besideLive": bool(beside_live and live.running()),
            "edition": licences.edition(), "tier": licences.tier((NAME,)), "licence": licences.COMPONENTS[NAME].licence,
            "split": {"basis": BASIS, "trainedOnDays": sorted({e.session for e in train}), "heldOutDays": sorted({e.session for e in held}),
                      "checked": "no held-out view is on the training side (its JPEG's SHA-256, its bridge frame)"},
            "trainedOn": [e.row() for e in train], "heldOut": sorted({e.session for e in held}),
            "heldOutPopulations": [e.row() for e in held],
            "settings": {"epochs": epochs, "learningRate": learning_rate, "block": result.get("block"),
                         "trainer": "cellpose.train.train_seg", "input": "the green channel of the crop the finder was given",
                         "heldOutGivenToTrainer": False},
            "losses": {"train": result.get("trainLosses")},
            "labels": LABELS_ARE,
            "recommendation": RECOMMENDATION,
        }
        version = finetuned.write(NAME, Path(result["file"]), meta, finetuned.root())  # the catalog's folder
    say(f"{NAME} {version['version']}: {version['folder']} ({version['bytes'] / 1e6:.1f} MB). Usable now as the cell finder "
        f"\"{NAME}\"; judge it on its held-out days with make replay-bench IMPL={NAME},{BASE}.")
    return version


def _build() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True,
                              timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fine-tune Cellpose-SAM on your corrected cells (an explicit job).")
    parser.add_argument("--evidence", action="append", default=[], help="an evidence folder (default: the app's)")
    parser.add_argument("--bundle", action="append", default=[], help="a session bundle: its evidence is used")
    parser.add_argument("--hold-out", type=int, default=1, help="the newest days held out (default 1)")
    parser.add_argument("--hold-out-day", action="append", default=[], help="a day to hold out (2026-10-07)")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--learning-rate", type=float, default=1e-5)
    parser.add_argument("--dry-run", action="store_true", help="say the training set and split; write nothing")
    parser.add_argument("--beside-live", action="store_true", help="run even while the app runs (it may slow its clicks)")
    args = parser.parse_args(argv)
    paths = Paths.from_env()
    roots = [Path(p).expanduser() for p in args.evidence] + [Path(b).expanduser() / "evidence" for b in args.bundle]
    version = finetune(paths, evidence=roots or None, hold_out=args.hold_out, days=args.hold_out_day or None,
                       epochs=args.epochs, learning_rate=args.learning_rate, dry_run=args.dry_run,
                       beside_live=args.beside_live)
    return 0 if version is not None or args.dry_run else 2


if __name__ == "__main__":
    raise SystemExit(main())
