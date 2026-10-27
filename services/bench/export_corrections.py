"""Corrections as labelled data (`make export-corrections`), from today's and earlier days' folders:
- **Names** ("kind": "label"): every suggested relabel with the picture of the thing and what happened to
  it. An accepted correction is a picture with a name the user agreed to: ground truth on our own
  specimens, for the naming bench. A dismissed one keeps the old name as the user's choice.
- **Segmentations** ("kind": "segmentation", M3): every correction the user made to segmented cells
  (`correct_cells`, M2: removed as not a cell, merged into one, an undo), with the population's view, its
  masks file (revision 0, the model's own, and every mask a correction added), the execution it corrects,
  and what each touched member was before. Ground truth for the cell finders on our own specimens.
Appends to the kept corrections log (Paths.corrections_log, beside the votes; one row per correction,
new ones only) and prints a summary.

    uv run python -m bench.export_corrections [--all]   # --all: also proposals not yet decided
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.paths import Paths  # noqa: E402


def rows(research: Path, include_open: bool = False) -> list[dict]:
    found = []
    for path in sorted(research.glob("**/session.json")):
        try:
            session = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        folder = path.parent
        for c in session.get("corrections") or []:
            status = c.get("status")
            if status == "proposed" and not include_open:
                continue
            crop = research / c["crop"] if c.get("crop") else None
            if crop is None and (folder / "inputs" / "crop.jpg").exists():
                crop = folder / "inputs" / "crop.jpg"  # older proposals: the researcher's snapshot
            found.append({
                "id": c.get("id"), "day": session.get("day") or next((part[:10] for part in reversed(path.parts) if part[:4].isdigit() and part[4:5] == "-"), None), "rid": session.get("rid"), "status": status,
                "from": c.get("label"), "to": c.get("to_label"), "suggestedBy": c.get("from"),
                "reason": c.get("reason"), "confidence": c.get("confidence"),
                "specimen": session.get("specimen") if isinstance(session.get("specimen"), str) else None,
                "crop": str(crop) if crop is not None and crop.exists() else None,
                "archived": "archive" in path.parts,
            })
    return found


def segmentation_rows(evidence: Path) -> list[dict]:
    """Every revision of every saved population that changed members (`was` or `added`): one row each, in
    order. Each member says what it was before the revision, what the revision made it (`became`) and what
    it is now (`current`, after any later revision); a revision a later undo reversed says which (`undoneBy`).
    The user's own words are `userWords`; the model's paraphrase, when it gave one, is `modelWhy` (M3.4 review B1).
    Each row keeps what leads back to its view: the manifest, the frame (`source`: boot and seq), the producer
    and its version, the region and the scale."""
    import hashlib

    found = []
    for path in sorted(evidence.glob("*/*/manifest.json")):
        try:
            manifest = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        folder = path.parent
        members = {m["id"]: m for m in manifest.get("members") or []}
        revisions = manifest.get("revisions") or []
        by_r = {rev.get("r"): rev for rev in revisions}
        undone_by = {rev["undoes"]: rev.get("r") for rev in revisions if rev.get("undoes") is not None}
        masks = folder / "masks.npz"
        masks_ok = masks.exists() and (not (manifest.get("files") or {}).get("masks.npz")
                                       or hashlib.sha256(masks.read_bytes()).hexdigest() == manifest["files"]["masks.npz"])
        day = manifest.get("day") or folder.parent.name
        for revision in revisions:
            if not (revision.get("was") or revision.get("added")):
                continue
            undone = by_r.get(revision.get("undoes")) if revision.get("undoes") is not None else None

            def became(mid: str) -> str:
                if undone is not None:  # an undo puts back what the undone revision found
                    return (undone.get("was") or {}).get(mid) or "deleted"  # one it added goes again
                return "merged" if revision.get("added") else "deleted"

            change = str(revision.get("change") or "")
            found.append({
                "kind": "segmentation", "id": f"{day}/{manifest.get('id')}-r{revision.get('r')}", "day": day,
                "session": manifest.get("session") or day,  # the session it was made in (ADR 0017; before: its date's)
                "population": manifest.get("id"), "revision": revision.get("r"), "change": change,
                "userWords": revision.get("asked") or None,  # what the user said, as said
                "modelWhy": change.split(": ", 1)[1] if ": " in change else None,  # the model's own paraphrase
                "by": revision.get("by"), "undoes": revision.get("undoes"), "undoneBy": undone_by.get(revision.get("r")),
                "members": {mid: {"was": status, "became": became(mid), "current": (members.get(mid) or {}).get("status"),
                                  "box": (members.get(mid) or {}).get("box")}
                            for mid, status in (revision.get("was") or {}).items()},
                "added": [{"id": mid, "box": (members.get(mid) or {}).get("box"),
                           "current": (members.get(mid) or {}).get("status"),
                           "mergedFrom": ((members.get(mid) or {}).get("extras") or {}).get("mergedFrom")}
                          for mid in revision.get("added") or []],
                "execution": revision.get("execution") or manifest.get("execution"),
                "implementation": manifest.get("implementation"), "operation": manifest.get("operation"),
                "producer": manifest.get("producer"), "source": manifest.get("source"),
                "region": manifest.get("region"), "scaleId": manifest.get("scaleId"), "manifest": str(path),
                "capture": str(folder / "capture.jpg") if (folder / "capture.jpg").exists() else None,
                "masks": str(masks) if masks_ok else None,
                **({} if masks_ok or not masks.exists() else {"masksProblem": "does not match its checksum"}),
            })
    return found


def append_new(out: Path, rows: list[dict]) -> int:
    """Append each row that differs from the latest line for its id (or has none yet): kept judgments are never
    rewritten, and an asset deleted later never takes a row away (M3.4 review B2). A row that changed since
    (undone later, its cell corrected again and back, its picture gone) is appended again, so the latest line
    for a day's id is always how it stood at the latest export (the re-check: compared with every line ever
    written, a row that went A, B and back to A was never appended again). Returns how many were added."""
    latest: dict[tuple, str] = {}
    if out.exists():
        for line in out.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            latest[(r.get("kind"), r.get("day"), r.get("id"))] = json.dumps(r, sort_keys=True)
    # By day too: a name correction's id (<rid>-cN) repeats on another day (the re-check's note).
    new = [r for r in rows if latest.get((r.get("kind"), r.get("day"), r.get("id"))) != json.dumps(r, sort_keys=True)]
    if new:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a") as f:
            for r in new:
                f.write(json.dumps(r) + "\n")
    return len(new)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="also proposals not yet decided")
    args = parser.parse_args()
    paths = Paths.from_env()
    found = [{"kind": "label", **r} for r in rows(paths.research, include_open=args.all)]
    found += segmentation_rows(paths.evidence)
    out = paths.corrections_log  # beside the votes: kept for good, appended, never rebuilt
    added = append_new(out, found)
    labels = [r for r in found if r["kind"] == "label"]
    cells = [r for r in found if r["kind"] == "segmentation"]
    by_status: dict[str, int] = {}
    for r in labels:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    with_picture = sum(1 for r in labels if r["crop"])
    print(f"{len(labels)} name corrections ({', '.join(f'{n} {s}' for s, n in sorted(by_status.items())) or 'none'}), "
          f"{with_picture} with a picture; {len(cells)} corrections of segmented cells, "
          f"{sum(1 for r in cells if r['masks'])} with their masks; {added} new -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
