"""Sample native navigation over an accepted immutable OME-Zarr folder.

Use the ordinary selected-folder intake first, then name its ProjectStore, Project,
session and exact revision. Existing 4096-entry/64 GiB intake limits apply; raw
local/remote folders cannot substitute for accepted content. This bench does not
establish remote lazy intake, full H08 qualification, browser/GPU memory or a true
peak footprint. Missing samples refuse certification. Heavy slot required.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

GIB = 1 << 30


def physical_ram() -> int:
    try:
        return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")


def footprint_mb(pid: int) -> float | None:
    from gpu_worker.footprint import footprint_mb as measure
    return measure(pid)


def inspect_dataset(pyramid, expected_shape, expected_levels, budget):
    """Validate the opened dataset before starting a worker; never certify CLI intentions."""
    finest = pyramid.levels[0]
    if tuple(finest.shape) != tuple(expected_shape):
        raise ValueError('Opened dataset shape does not match the requested fixture')
    if len(pyramid.levels) != expected_levels or pyramid.axes != ('z', 'y', 'x') or finest.dtype.str != '<u2':
        raise ValueError('Opened dataset levels, axes or dtype differ from the fixture declaration')
    logical = math.prod(finest.shape) * finest.dtype.itemsize
    if budget <= 0 or logical < 4 * budget:
        raise ValueError('Opened dataset must be at least 4x a positive resident budget')
    return {'shape': list(finest.shape), 'dtype': finest.dtype.str,
            'levels': [list(level.shape) for level in pyramid.levels],
            'logicalBytes': logical, 'revision': pyramid.revision}


def summarize_footprints(samples, budget):
    valid = [value for value in samples if type(value) in (int, float) and math.isfinite(value) and value > 0]
    complete = bool(samples) and len(valid) == len(samples)
    maximum = max(valid) if valid else None
    return {'sampledMaximumMiB': maximum, 'complete': complete,
            'withinBudget': complete and maximum * (1 << 20) <= budget}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dataset", type=Path, help="ProjectStore containing the accepted source folder")
    ap.add_argument("--source-project", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--revision", required=True, type=int)
    ap.add_argument("--shape", type=int, nargs=3, default=(32, 8192, 8192))
    ap.add_argument("--levels", type=int, default=5)
    ap.add_argument("--budget-gib", type=float, default=1.0)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--remote", action="store_true", help="refuses: immutable remote lazy intake remains incomplete")
    args = ap.parse_args(argv)
    ram = physical_ram()
    budget = int(args.budget_gib * GIB)
    ceiling = min(8 * GIB, ram // 5)
    if budget > ceiling:
        sys.exit(f"The budget {args.budget_gib} GiB is over min(8 GiB, 20% of RAM) = {ceiling / GIB:.1f} GiB")
    if budget <= 0:
        sys.exit('The resident budget must be positive')
    if args.remote:
        sys.exit('Remote lazy intake has no accepted immutable source yet; H08 remote qualification remains incomplete')
    location = os.environ.get("NAPARI_HOST_PREPARATION")
    if not location:
        sys.exit("Needs NAPARI_HOST_PREPARATION (the prepared private bundle the actual-worker tests use)")
    prepared = json.loads(Path(location).read_text())
    for name, value in prepared["environment"].items():
        os.environ[name] = value

    from orchestrator.scientific_projects import ProjectStore
    from orchestrator import ngff_sources as ngff
    from orchestrator import scientific_host as parent

    store = ngff.SelectedStore(ProjectStore(args.dataset), args.source_project, args.session, args.revision)
    pyramid = ngff.open_multiscale(store)
    facts = inspect_dataset(pyramid, args.shape, args.levels, budget)
    logical = facts['logicalBytes']
    sources = {f"level{i}": s for i, s in enumerate(pyramid.sources())}
    z, y, x = pyramid.levels[0].shape
    sz, sy, sx = pyramid.levels[0].scale
    tz, ty, tx = pyramid.levels[0].translation
    metadata = pyramid.metadata()
    steps, footprints = [], []

    def step(name, worker, source=None):
        before, t = sum(r["bytes"] for r in store.audit), time.monotonic()
        if source:
            done, _ = worker.request({"op": "python-resources", "source": source})
            if done["console"]["error"]:
                raise RuntimeError(f"{name}: {done['console']['error']}")
        reply, pixels = worker.request({"op": "render", "width": 1024, "height": 768})
        stats, _ = worker.request({"op": "python-resources", "source":
                                   "import json\nprint(json.dumps(viewer.layers[0].data[0].stats))"})
        cache = json.loads(stats["console"]["stdout"].strip().splitlines()[-1])
        used = footprint_mb(worker.process.pid)
        footprints.append(used)
        steps.append({"step": name, "ms": round((time.monotonic() - t) * 1000), "entryReadMiB":
                      round((sum(r["bytes"] for r in store.audit) - before) / 2**20, 2),
                      "cacheResidentMiB": round(cache["residentBytes"] / 2**20, 1),
                      "workerFootprintMiB": round(used, 1) if used is not None and math.isfinite(used) else None, "distinctPixels": len(set(pixels[::97]))})

    with parent.review(prepared["key"], Path(prepared["folder"]).name, sources,
                       retained_layers=[{"input": "level0", "metadata": metadata, "levels": list(sources)}]) as worker:
        step("open", worker)
        for depth in range(0, z, max(1, z // 8)):
            step(f"depth {depth}", worker, f"viewer.dims.set_point(0, {tz + depth * sz})")
        cy, cx = ty + y * sy / 2, tx + x * sx / 2
        step("zoom to full resolution", worker, f"viewer.camera.center = (0, {cy}, {cx})\nviewer.camera.zoom = 4.0")
        for k in range(4):
            step(f"pan {k}", worker, f"viewer.camera.center = (0, {cy + 300 * k}, {cx + 300 * k})")
        level, _ = worker.request({"op": "python-resources", "source": "print(viewer.layers[0].data_level)"})
        shown_level = int(level["console"]["stdout"].strip().splitlines()[-1])
    total = sum(r["bytes"] for r in store.audit)
    measured = summarize_footprints(footprints, budget)
    report = {
        "format": "hooke-h08-large-data/2",
        "dataset": {**facts, 'geometry': metadata['geometry']},
        "budget": {"GiB": args.budget_gib, "ceilingGiB": round(ceiling / GIB, 2), "ramGiB": round(ram / GIB, 1),
                   "ratio": round(logical / budget, 1)},
        "workerFootprint": measured,
        "sourceEntryMiB": round(total / 2**20, 1), "sourceEntryShareOfLevel0": round(total / logical, 4),
        "requests": len(store.audit), "levelShownAfterZoom": shown_level, "steps": steps,
        "source": "accepted immutable selected folder",
        "gpu": "included in the worker's phys_footprint on macOS; no separate GPU counter",
        "notMeasured": ["edit, process, export and reopen of large lazy data (not implemented)",
                        "remote lazy intake",
                        "the browser's memory", "peak footprint between navigation samples", "physical retained-storage I/O including chunk verification"],
        "pass": measured["withinBudget"] and total < logical / 4,
        "fullH08Qualification": False,
    }
    text = json.dumps(report, indent=1)
    if args.out:
        args.out.write_text(text + "\n")
    print(text)
    sys.exit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
