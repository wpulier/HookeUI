"""Does a dataset folder hold what docs/datasets.md says it does? One check for every dataset, collected
or reconstructed: `python -m orchestrator.datasets.check <folder>...` (make dataset-check) prints its
problems, none when it is sound."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import manifest

STATUSES = ("collecting", "complete")
SOURCES = ("user", "measured", "estimate", "bridge-config", "default", "unknown")
ORDER_SLACK_S = 1.0  # taps from other threads may land a little out of order


def problems(folder: Path, name: str | None = None) -> list[str]:
    """`name`: the id it will have, for a dataset still in its temporary folder (a snapshot is checked
    before it is renamed into place)."""
    folder = Path(folder)
    name = name or folder.name
    out: list[str] = []
    summary = manifest.read(folder)
    if summary is None:
        return [f"{folder}: no readable {manifest.NAME}"]
    if summary.get("schema") != manifest.SCHEMA:
        out.append(f"schema is {summary.get('schema')!r}, not {manifest.SCHEMA}")
    if summary.get("id") != name:
        out.append(f"id {summary.get('id')!r} is not the folder's name {name!r}")
    if summary.get("status") not in STATUSES:
        out.append(f"status {summary.get('status')!r} is not one of {STATUSES}")
    start, stop = summary.get("startedAtEpoch"), summary.get("stoppedAtEpoch")
    if not isinstance(start, (int, float)):
        out.append("no startedAtEpoch")
    if summary.get("status") == "complete" and not (isinstance(stop, (int, float)) and isinstance(start, (int, float)) and stop >= start):
        out.append("complete, but its stop is missing or before its start")
    for key, kind in (("runs", list), ("gaps", list), ("video", dict), ("counts", dict), ("rig", dict)):
        if not isinstance(summary.get(key), kind):
            out.append(f"{key} is not a {kind.__name__}")
    if not summary.get("runs"):
        out.append("no runs")
    if summary.get("reconstructed") and not isinstance(summary.get("reconstruction"), dict):
        out.append("reconstructed, but it does not say how (reconstruction)")
    for name, fact in (summary.get("rig") or {}).items():
        if isinstance(fact, dict) and "source" in fact and fact["source"] not in SOURCES:
            out.append(f"rig fact {name} has an unknown source {fact['source']!r}")
        elif isinstance(fact, dict) and "value" in fact and "source" not in fact:
            out.append(f"rig fact {name} does not say where it came from")

    events = _lines(folder / manifest.EVENTS, out)
    last = None
    for n, line in enumerate(events, 1):
        if not {"t", "run", "stream", "record"} <= set(line):
            out.append(f"{manifest.EVENTS}:{n} lacks t, run, stream or record")
            continue
        if last is not None and line["t"] < last - ORDER_SLACK_S:
            out.append(f"{manifest.EVENTS}:{n} is out of order by {last - line['t']:.1f} s")
        last = max(last or line["t"], line["t"])
    counted = (summary.get("counts") or {}).get("events")
    if isinstance(counted, int) and counted > len(events):
        out.append(f"counts say {counted} events, {manifest.EVENTS} has {len(events)}")
    for n, line in enumerate(_lines(folder / manifest.FRAMES, out), 1):
        if not {"t", "run", "id"} <= set(line):
            out.append(f"{manifest.FRAMES}:{n} lacks t, run or id")
            break

    for part in (summary.get("video") or {}).get("parts", []):
        video = folder / str(part.get("video"))
        if part.get("videoKept") and not (video.is_file() and video.stat().st_size > 0):
            out.append(f"part {part.get('n')}: {part.get('video')} is said to be kept but is missing or empty")
        if part.get("sidecar") and part.get("sidecarKept") and not (folder / part["sidecar"]).is_file():
            out.append(f"part {part.get('n')}: its sidecar {part['sidecar']} is missing")
        if not part.get("sidecar") and not isinstance(part.get("timing"), dict):
            out.append(f"part {part.get('n')} has no sidecar and does not say how its frames' times are known")
    out += _made_with(folder, summary)
    return out


def _made_with(folder: Path, summary: dict) -> list[str]:
    """`madeWith` (the capabilities behind it) is a list of entries; each one carried with it has its packed entry in
    `capabilities/<sha256>.observationui.zip`, of the size it names (result_entries.carry_dataset)."""
    if "madeWith" not in summary:
        return []
    made = summary["madeWith"]
    if not isinstance(made, list) or not all(isinstance(m, dict) for m in made):
        return ["madeWith is not a list of entries"]
    from ..result_entries import descriptor
    from ..share import STORED_SUFFIX as SUFFIX

    out = []
    for named in made:
        info = descriptor(named.get("portable"))
        if info is None:
            if named.get("portable") is not None:
                out.append(f"madeWith {named.get('key')!r}: its portable descriptor can't be read")
            continue  # named only: a reviewed entry anyone can get, nothing carried
        packed = folder / "capabilities" / (info["sha256"] + SUFFIX)
        if not packed.is_file() or packed.stat().st_size != info["bytes"]:
            out.append(f"madeWith {named.get('key')!r}: capabilities/{packed.name} is missing or not {info['bytes']} bytes")
    return out


def _lines(path: Path, out: list[str]) -> list[dict]:
    if not path.exists():
        return []
    lines = []
    with path.open(encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            if not raw.strip():
                continue
            try:
                lines.append(json.loads(raw))
            except ValueError:
                out.append(f"{path.name}:{n} is not JSON")
    return lines


def main(argv: list[str]) -> int:
    bad = 0
    for arg in argv:
        found = problems(Path(arg))
        print(f"{arg}: {'sound' if not found else f'{len(found)} problems'}")
        for problem in found:
            print(f"  - {problem}")
        bad += bool(found)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
