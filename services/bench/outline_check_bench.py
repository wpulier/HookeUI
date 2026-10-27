"""Does the outline check tell a fitting outline from a wrong one, and does "fix" recover it?
(orchestrator/outline_check.py; option OUTLINE_CHECK)

For each hand-labelled shape in questions.json (stomata and other ovals on the June 14 frames):
  good       EdgeTAM from the labelled point (the outline the app would draw)
  neighbour  EdgeTAM from a point 140 px away (something else)
  too_big    a disc five times the good outline's size, around it
  too_small  a disc a third of the good outline's size, at its centre
The check (the fast vision model) should say "yes" only for good. For the others, "fix" outlines
again with EdgeTAM from the box the model gives; it counts as recovered when the new outline
overlaps the good one (IoU >= 0.5).

Two of a kind (going live G4; with finding 69's going-live set): on frames with two hand-labelled
ovals, both outlined "stoma", each outline is checked as the app checks it with a namesake in view,
told "2 outlines in this view are named ... this one is meant to be the one at <its box>":
  pair       each good outline, with the line: "yes" is right ("wrong: a different stoma" was the live failure)
  pair-bare  the same without the line (the check as before G4), for the comparison
  pair-other one stoma's outline drawn on the other one (the outline model on the namesake), told the box it was
             meant for: "yes" is wrong; recovered when the fix overlaps the meant stoma (IoU >= 0.5)

  cd services && .venv/bin/python -m bench.outline_check_bench [--model gemini-3.5-flash] [--repeats 1]

Writes results/outline-check-<model>.json. No servers; EdgeTAM runs here (MPS).
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import cv2
import numpy as np

from gpu_worker.worker import Models
from orchestrator.agent import identify_images
from orchestrator.coords import Crop
from orchestrator.keys import load_keys
from orchestrator.options import VISION
from orchestrator.outline_check import check
from orchestrator.vision import make_vision

HERE = Path(__file__).resolve().parent
from orchestrator.assets import store  # noqa: E402  (the assets store: assets/README.md)
FRAMES = store().bench("frames")  # regenerated from the real recordings: python -m bench.extract_frames


def disc(cx: float, cy: float, r: float, n: int = 32) -> list[list[list[float]]]:
    a = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return [[[float(cx + r * np.cos(t)), float(cy + r * np.sin(t))] for t in a]]


def mask(polygons, shape) -> np.ndarray:
    m = np.zeros(shape[:2], np.uint8)
    for p in polygons:
        cv2.fillPoly(m, [np.round(np.array(p)).astype(np.int32)], 1)
    return m.astype(bool)


def iou(a, b) -> float:
    u = (a | b).sum()
    return float((a & b).sum() / u) if u else 0.0


def box_of(polygons):
    pts = np.concatenate([np.array(p, np.float32) for p in polygons])
    return float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gemini-3.5-flash")
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()
    load_keys()
    vision = make_vision(VISION[args.model].spec)
    models = Models()
    questions = [q for q in json.loads((HERE / "questions.json").read_text())["questions"] if q["kind"] == "shape"]
    rows = []
    for q in questions:
        image = cv2.imread(str(FRAMES / q["frame"]))
        h, w = image.shape[:2]
        jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
        base = {"frame_id": hash(q["frame"]) & 0xFFFF, "jpeg": jpeg}
        x, y = q["targets"][0]
        good = models.segment({**base, "points": [[x, y]], "model": "edgetam"})["polygons"]
        if not good:
            continue
        x0, y0, x1, y1 = box_of(good)
        size = max(x1 - x0, y1 - y0)
        nx, ny = (x + 140 if x + 140 < w - 20 else x - 140), y
        variants = {"good": good,
                    "neighbour": models.segment({**base, "points": [[nx, ny]], "model": "edgetam"})["polygons"],
                    "too_big": disc((x0 + x1) / 2, (y0 + y1) / 2, size * 2.5),
                    "too_small": disc((x0 + x1) / 2, (y0 + y1) / 2, max(4.0, size / 6))}
        good_mask = mask(good, image.shape)
        crop = Crop(0, 0, w, h)
        for _ in range(args.repeats):
            for name, polys in variants.items():
                if not polys:
                    continue
                view, close = identify_images(image, polys, crop, box_of(polys))
                fits, why, box, ms = await check(vision, view, close, "", q["question"])
                row = {"frame": q["frame"], "question": q["question"], "variant": name, "fits": fits, "why": why,
                       "ms": round(ms), "right": (fits == "yes") == (name == "good")}
                if name != "good" and fits not in ("yes", "unclear") and box is not None:
                    bx = (box[1] / 1000 * w, box[0] / 1000 * h, box[3] / 1000 * w, box[2] / 1000 * h)
                    again = models.segment({**base, "box": list(bx), "model": "edgetam"})["polygons"]
                    if again:
                        view2, close2 = identify_images(image, again, crop, box_of(again))
                        fits2, _, _, ms2 = await check(vision, view2, close2, "", q["question"])
                        overlap = iou(mask(again, image.shape), good_mask)
                        row.update(againFits=fits2, againIou=round(overlap, 2), againMs=round(ms2),
                                   recovered=fits2 == "yes" and overlap >= 0.5, keptWrong=fits2 == "yes" and overlap < 0.5)
                rows.append(row)
                print(f"{'ok ' if row['right'] else 'BAD'} {name:10} {fits:9} {row.get('againIou', ''):>5} {q['question'][:50]}", flush=True)
    by = {}
    for r in rows:
        ok, n = by.get(r["variant"], (0, 0))
        by[r["variant"]] = (ok + r["right"], n + 1)
    wrong = [r for r in rows if r["variant"] != "good"]
    summary = {"model": args.model, "right": f"{sum(r['right'] for r in rows)}/{len(rows)}",
               "byVariant": {k: f"{a}/{b}" for k, (a, b) in by.items()},
               "goodCalledWrong": f"{sum(1 for r in rows if r['variant'] == 'good' and r['fits'] != 'yes')}/{by.get('good', (0, 0))[1]}",
               "fixRecovered": f"{sum(1 for r in wrong if r.get('recovered'))}/{len(wrong)}",
               "fixKeptWrong": sum(1 for r in wrong if r.get("keptWrong")),
               "p50Ms": int(np.median([r["ms"] for r in rows])) if rows else None}
    summary["twoOfAKind"], pair_rows = await two_of_a_kind(vision, models, questions, args.repeats)
    print(json.dumps(summary, indent=2))
    (HERE / "results" / f"outline-check-{args.model}.json").write_text(json.dumps({"summary": summary, "rows": rows, "pairRows": pair_rows}, indent=2))


async def two_of_a_kind(vision, models, questions, repeats: int) -> tuple[dict, list]:
    """The G4 set: frames with two labelled ovals, both outlined "stoma"; each checked with the namesake line."""
    from orchestrator.outline_check import box_1000

    frames: dict[str, list] = {}
    for q in questions:
        frames.setdefault(q["frame"], []).append(q)
    rows = []
    for frame, qs in frames.items():
        if len(qs) < 2:
            continue
        image = cv2.imread(str(FRAMES / frame))
        h, w = image.shape[:2]
        jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
        base = {"frame_id": hash(frame) & 0xFFFF, "jpeg": jpeg}
        good = [models.segment({**base, "points": [list(q["targets"][0])], "model": "edgetam"})["polygons"] for q in qs[:2]]
        if not all(good):
            continue
        crop = Crop(0, 0, w, h)
        for _ in range(repeats):
            for k in (0, 1):
                meant, other = good[k], good[1 - k]
                several = (2, box_1000(meant, crop))
                view, close = identify_images(image, meant, crop, box_of(meant))
                for variant, line in (("pair", several), ("pair-bare", None)):
                    fits, why, _, ms = await check(vision, view, close, "stoma", "", line)
                    rows.append({"frame": frame, "variant": variant, "fits": fits, "why": why, "ms": round(ms), "right": fits == "yes"})
                view, close = identify_images(image, other, crop, box_of(other))
                fits, why, box, ms = await check(vision, view, close, "stoma", "", several)
                row = {"frame": frame, "variant": "pair-other", "fits": fits, "why": why, "ms": round(ms), "right": fits != "yes"}
                if fits not in ("yes", "unclear") and box is not None:
                    bx = (box[1] / 1000 * w, box[0] / 1000 * h, box[3] / 1000 * w, box[2] / 1000 * h)
                    again = models.segment({**base, "box": list(bx), "model": "edgetam"})["polygons"]
                    if again:
                        row["againIou"] = round(iou(mask(again, image.shape), mask(meant, image.shape)), 2)
                        row["recovered"] = row["againIou"] >= 0.5
                rows.append(row)
                print(f"{'ok ' if row['right'] else 'BAD'} pair-other {fits:9} {frame}", flush=True)
    out = {}
    for variant in ("pair", "pair-bare", "pair-other"):
        mine = [r for r in rows if r["variant"] == variant]
        out[variant] = f"{sum(r['right'] for r in mine)}/{len(mine)}"
    other = [r for r in rows if r["variant"] == "pair-other"]
    out["pairOtherRecovered"] = f"{sum(1 for r in other if r.get('recovered'))}/{len(other)}"
    return out, rows


if __name__ == "__main__":
    asyncio.run(main())
