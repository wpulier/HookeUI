"""How fast and how complete is a look at the view (orchestrator/sight.py)?

For each saved June 14 frame in questions.json, the SIGHT prompt is sent to each model
(twice). Measured: time, things returned, and recall: the share of hand-labelled targets on
that frame (the things people asked about) that fall inside some returned box (grown by the
question's tolerance). High recall means a later "highlight that" can use the look's box
instead of a new vision call.

  cd services && .venv/bin/python -m bench.sight_bench [--models gemini-3.8-flash,gemini-3.5-flash-lite]

Writes results/sight-bench.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import numpy as np

from bench.grounding import prepare
from orchestrator.coords import gemini_box_to_frame
from orchestrator.keys import load_keys
from orchestrator.options import VISION
from orchestrator.sight import SIGHT
from orchestrator.vision import make_vision

BENCH = Path(__file__).resolve().parent


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="gemini-3.8-flash,gemini-3.5-flash-lite")
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    load_keys()
    questions = json.loads((BENCH / "questions.json").read_text())["questions"]
    frames: dict[str, list] = {}
    for q in questions:
        frames.setdefault(q["frame"], []).append(q)
    prepared = {f: prepare(qs[0]) for f, qs in frames.items()}
    summary, rows = {}, []
    for model in args.models.split(","):
        vision = make_vision(VISION[model].spec)
        found = total = 0
        ms, counts = [], []
        for _ in range(args.repeats):
            for f, qs in frames.items():
                result = await vision.ask([prepared[f]["clean"]], SIGHT.format(context="", same="", same_key=""), max_output_tokens=900)
                data = result.data or {}
                boxes = []
                for t in data.get("things") or []:
                    if isinstance(t, dict) and isinstance(t.get("box_2d"), list) and len(t["box_2d"]) == 4:
                        boxes.append(gemini_box_to_frame([float(v) for v in t["box_2d"]], prepared[f]["crop"]))
                ms.append(result.latency_ms)
                counts.append(len(boxes))
                for q in qs:
                    tol = q["tolerance"]
                    hit = any(any(b[0] - tol <= x <= b[2] + tol and b[1] - tol <= y <= b[3] + tol for x, y in q["targets"]) for b in boxes)
                    found += hit
                    total += 1
                rows.append({"model": model, "frame": f, "ms": round(result.latency_ms), "things": [t.get("name") for t in data.get("things") or [] if isinstance(t, dict)],
                             "summary": data.get("summary"), "error": result.error})
                print(model, f, round(result.latency_ms), len(boxes), (data.get("summary") or "")[:80], flush=True)
        summary[model] = {"recall": f"{found}/{total}", "p50Ms": round(float(np.median(ms))), "p90Ms": round(float(np.percentile(ms, 90))),
                          "things": round(float(np.mean(counts)), 1)}
    print(json.dumps(summary, indent=2))
    (BENCH / "results" / "sight-bench.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
