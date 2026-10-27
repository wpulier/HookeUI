"""What each model on this Mac really takes (R1 of docs/design/2026-10-11-model-runtime.md).

Each model loads alone in a fresh process through the worker's own code (gpu_worker.worker.Models), and the worker's
report (its footprint above the process before, its peak while loading, its seconds) is set beside what the process
holds when measured from outside, and the model's weights. The old way (resident size) is measured beside it, to show
what loads.json held before.

  cd services && .venv/bin/python -m bench.footprint_bench [--models edgetam,sam2.1-tiny,...] [--big]

--big adds Molmo 2 (about 11 GB). A model this Mac can't load (not installed, no key) is a row saying so.
Writes results/footprint/rows.jsonl. Loads compete with a running app: run it with the app idle, said in the rows.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "footprint"
MODELS = ("sam2.1-tiny", "edgetam", "edgetam-video", "dinov2-small", "bioclip-2", "medsiglip", "cellpose-sam",
          "micro-sam-lm", "sam3.1-local")


def _rss_mb(pid: int) -> float:
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return float(out or 0) / 1024


def _one(model: str, conn) -> None:
    """In a fresh process: the worker's Models, ready as a worker is (torch and the GPU context), then one load."""
    try:
        from gpu_worker.footprint import footprint_mb, peak_mb
        from gpu_worker.worker import Models

        models = Models()
        told = []
        models.notify = told.append
        models.torch.ones(4, device=models.device)  # the GPU context a worker holds before any model
        host = footprint_mb()
        rss_before = _rss_mb(os.getpid())
        t0 = time.perf_counter()
        models.load({"model": model})
        wall = time.perf_counter() - t0
        loaded = next((m for m in told if m.get("load") == "loaded"), {})
        weights = 0.0
        for holder in (getattr(models, "_point", {}).get(model, (None, None))[1],):
            if holder is not None:
                weights = sum(v.numel() * v.element_size() for v in list(holder.parameters()) + list(holder.buffers())) / 2**20
        conn.send({"model": model, "hostMb": round(host or 0), "worker": {k: loaded.get(k) for k in ("memoryMb", "peakMb", "seconds", "how", "pid")},
                   "outside": {"footprintMb": round((footprint_mb() or 0) - (host or 0)), "lifetimePeakMb": round((peak_mb() or 0) - (host or 0)),
                               "rssDeltaMb": round(_rss_mb(os.getpid()) - rss_before)},
                   **({"weightsMb": round(weights)} if weights else {}), "wallS": round(wall, 1)})
    except Exception as exc:
        conn.send({"model": model, "error": f"{type(exc).__name__}: {exc}"[:300]})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default=",".join(MODELS))
    parser.add_argument("--big", action="store_true", help="also Molmo 2 (about 11 GB)")
    args = parser.parse_args()
    models = [m for m in args.models.split(",") if m] + (["molmo2-4b"] if args.big else [])
    OUT.mkdir(parents=True, exist_ok=True)
    from orchestrator.keys import load_keys

    load_keys()
    app = subprocess.run(["lsof", "-nP", "-iTCP:8770", "-sTCP:LISTEN"], capture_output=True, text=True).stdout.strip()
    rows = []
    ctx = mp.get_context("spawn")
    for model in models:
        parent, child = ctx.Pipe()
        process = ctx.Process(target=_one, args=(model, child))
        process.start()
        row = parent.recv() if parent.poll(900) else {"model": model, "error": "no answer in 15 min"}
        process.join(30)
        row["appRunning"] = bool(app)
        rows.append(row)
        print(json.dumps(row), flush=True)
    with open(OUT / "rows.jsonl", "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main()
