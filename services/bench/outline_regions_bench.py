"""Round 2 of the outline fit (docs/bench/outline-fit.md, 2026-10-11): tissue regions only, outlines drawn so they stand
out from the stain, judged on the scale Will labels with.

Round 1 found the models differ only on tissue regions, where its judge was least reliable, and that a pink outline
vanished on magenta stains and a ring could not be shown. This round:

  targets  on the sharpest frames of the stained recordings, the app's running look (sight_prompt, Gemini 3.8 Flash)
           with the bench's own addendum: each thing's kind (an object, a region of tissue, or many separate small
           things), for a region that is a band around another part that part's box (a ring), and one sentence on
           how it looks in the picture. Only regions are kept.
  arms     each outline model draws the region's box: EdgeTAM (the default), SAM 2.1 tiny, micro-sam (light
           microscopy), SAM 3.1 on this Mac (its tracker). For a ring also "<model> as a ring": the model's outline
           of the ring's box minus its outline of the box of what the ring surrounds (EdgeTAM, SAM 3.1).
  judge    GPT-6.1 Sol (another family than the look and the outline check), on Will's scale: nailed, kinda, missed.
           It is shown the view with the outline in the colour that stands out most from the stain (drawing.py) on a
           dark casing and the target box dashed, a close-up with the outline, the thing's name and the look's own
           sentence on how it looks.
  review   a sample for Will (review.json), the judge's rating hidden; scored by bench/outline_fit_review.py --round 2.

  cd services && .venv/bin/python -m bench.outline_regions_bench [--frames 6] [--things 4] [--most 48]

Writes results/outline-regions/: targets.json, rows.jsonl, summary.json, review.json and pictures/. The outline
models run here (MPS), one at a time, at low priority; the vision calls go to the cloud (about $5).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
from pathlib import Path

import cv2
import numpy as np

from bench.outline_fit_bench import SOURCES, box_of, sharpest_frames
from gpu_worker.worker import mask_to_polygons
from orchestrator.agent import close_region, enlarged
from orchestrator.assets import store
from orchestrator.coords import Crop, gemini_box_to_frame
from orchestrator.drawing import contrast_colour, outline, points
from orchestrator.keys import load_keys
from orchestrator.marks import encode_crop
from orchestrator.options import VISION
from orchestrator.scale import filled_area
from orchestrator.sight import sight_prompt
from orchestrator.vision import make_vision

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "outline-regions"
STAINED = ("stained stem", "stained section", "dicot stem (demo)")
BOX_ARMS = ("edgetam", "sam2.1-tiny", "micro-sam-lm", "sam3.1-local")
RING_ARMS = ("edgetam", "sam3.1-local")  # "<model> as a ring"
RATINGS = ("nailed", "kinda", "missed")

KINDS = """

For this benchmark, also give each thing in "things":
- "kind": "object" (one distinct thing: a cell, a stoma, a vessel), "region" (a stretch of tissue: cortex, pith, a
  band of xylem, a vascular bundle) or "many" (several separate small things of one kind: scattered chloroplasts);
- "surrounds": for a region that is a ring or band around another part (the cortex around the vascular cylinder), that
  part's "box_2d" [ymin, xmin, ymax, xmax] on the same 0-1000 scale; otherwise null;
- "looks": one short sentence on how it looks in this picture (its colour, texture and where its edges are), for
  someone who does not know the biology."""

JUDGE = """You are judging an outline drawn on a microscope picture. Image 1 is the whole view: the outline is drawn in
{colour} on a dark edge, and the dashed white rectangle is the box it was asked to fill. Image 2 is a close-up of the
same outline.
The outline is meant to be: {name}. How it looks in this picture: {looks}{ring}
Rate how well the outline follows that thing's own edges:
- "nailed": it follows the thing's edges closely;
- "kinda": it gets the thing, but loosely: it cuts some of it off or takes in some of what is around it;
- "missed": it is much too big, much too small, or around something else.
Reply with JSON only: {{"rating": "nailed" | "kinda" | "missed", "why": "one short sentence"}}"""
RING = (" It is a band around another part (the dotted grey rectangle marks that part): the outline should follow the"
        " band and leave the part inside it out.")


def filled(polygons, shape) -> np.ndarray:
    """The outline as a mask, filled even-odd as it is drawn (a ring's hole left out)."""
    m = np.zeros(shape[:2], np.uint8)
    if polygons:
        cv2.fillPoly(m, [np.round(np.asarray(p)).astype(np.int32) for p in polygons], 1)
    return m


async def regions(looker, image, things: int) -> list[dict]:
    """The look's regions on a frame, each with its box, what it surrounds (a ring) and how it looks, in frame px."""
    h, w = image.shape[:2]
    crop = Crop(0, 0, w, h)
    jpeg = encode_crop(image, crop, max_side=768)[0]
    result = await looker.ask([jpeg], sight_prompt(None, None) + KINDS, max_output_tokens=1400)
    out = []
    for t in (result.data or {}).get("things") or []:
        box = t.get("box_2d") if isinstance(t, dict) else None
        if not (isinstance(box, list) and len(box) == 4) or t.get("kind") != "region":
            continue
        inner = t.get("surrounds")
        out.append({"label": str(t.get("name") or "region")[:40], "looks": str(t.get("looks") or "")[:200],
                    "box": [float(v) for v in gemini_box_to_frame([float(v) for v in box], crop)],
                    "surrounds": ([float(v) for v in gemini_box_to_frame([float(v) for v in inner], crop)]
                                  if isinstance(inner, list) and len(inner) == 4 else None)})
    return out[:things]


def picture(image, polygons, target) -> tuple[bytes, bytes, str]:
    """The judge's (and Will's) two pictures and the outline's colour: the view with the outline on a dark casing in the
    colour that stands out from the stain, the target box dashed (what a ring surrounds dotted grey), and a close-up."""
    name, colour = contrast_colour(image, polygons)
    marked = image.copy()
    dashed(marked, target["box"], (255, 255, 255), 14)
    if target.get("surrounds"):
        dashed(marked, target["surrounds"], (170, 170, 170), 6)
    outline(marked, points(polygons), colour, 3)
    h, w = image.shape[:2]
    view, _ = encode_crop(marked, Crop(0, 0, w, h), max_side=1024)
    return view, enlarged(marked, close_region(marked, target["box"])), name


def dashed(image, box, colour, dash: int) -> None:
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    for (ax, ay), (bx, by) in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        length = max(abs(bx - ax), abs(by - ay))
        for s in range(0, length, dash * 2):
            e = min(length, s + dash)
            cv2.line(image, (ax + (bx - ax) * s // max(length, 1), ay + (by - ay) * s // max(length, 1)),
                     (ax + (bx - ax) * e // max(length, 1), ay + (by - ay) * e // max(length, 1)), colour, 2, cv2.LINE_AA)


async def judged(judge, limit, image, polygons, target) -> dict:
    view, close, colour = picture(image, polygons, target)
    prompt = JUDGE.format(colour=colour, name=target["label"], looks=target["looks"] or "not said",
                          ring=RING if target.get("surrounds") else "")
    async with limit:
        reply = await judge.ask([view, close], prompt, max_output_tokens=600)
    data = reply.data or {}
    rating = data.get("rating") if data.get("rating") in RATINGS else "unclear"
    return {"rating": rating, "why": str(data.get("why") or "")[:200], "colour": colour, "view": view, "close": close}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=int, default=6, help="frames per stained recording")
    parser.add_argument("--things", type=int, default=4, help="regions per frame")
    parser.add_argument("--most", type=int, default=48, help="regions in all")
    parser.add_argument("--looker", default="gemini-3.8-flash", help="the running look's model (the app's)")
    parser.add_argument("--judge", default="gpt-6.1-sol", help="another family than the app's models")
    parser.add_argument("--review", type=int, default=40)
    args = parser.parse_args()
    os.nice(10)
    load_keys()
    from gpu_worker.worker import Models

    looker, judge = (make_vision(VISION[m].spec) for m in (args.looker, args.judge))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "pictures").mkdir(exist_ok=True)

    # 1. The look's regions on the sharpest frames of the stained recordings.
    frames, targets = {}, []
    for source in STAINED:
        path = SOURCES[source]
        full = path if os.path.isabs(path) else str(store().root / "footage" / path)
        if not os.path.exists(full):
            print(f"skip {source}: {full} missing", flush=True)
            continue
        for n, image in enumerate(sharpest_frames(full, args.frames)):
            frames[f"{source}-{n}"] = image
            found = await regions(looker, image, args.things)
            for t in found:
                targets.append({"source": source, "frame": f"{source}-{n}", **t})
            print(f"{source} frame {n}: {[(t['label'], bool(t['surrounds'])) for t in found]}", flush=True)
    random.Random(20261012).shuffle(targets)
    targets = targets[:args.most]
    for i, t in enumerate(targets):
        t["target"] = i
    (OUT / "targets.json").write_text(json.dumps(targets, indent=1))

    # 2. Outlines, one model at a time; a ring is its box's outline minus that of what it surrounds.
    models = Models()
    outlines = []  # (target, arm, polygons, ms)
    for model in BOX_ARMS:
        for t in targets:
            image = frames[t["frame"]]
            jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
            got = models.segment({"frame_id": hash(t["frame"]) & 0xFFFF, "jpeg": jpeg, "box": t["box"], "model": model})
            outlines.append((t, model, got.get("polygons") or [], got.get("ms")))
            if model in RING_ARMS and t.get("surrounds"):
                inner = models.segment({"frame_id": hash(t["frame"]) & 0xFFFF, "jpeg": jpeg, "box": t["surrounds"],
                                        "model": model})
                band = filled(got.get("polygons") or [], image.shape) & (1 - filled(inner.get("polygons") or [], image.shape))
                outlines.append((t, f"{model} as a ring", mask_to_polygons(band.astype(bool)),
                                 (got.get("ms") or 0) + (inner.get("ms") or 0)))
        try:
            models.unload(model)
        except Exception:
            pass

    # 3. Every outline judged by the other family's model, on Will's scale.
    limit = asyncio.Semaphore(6)

    async def row(i, t, arm, polygons, ms):
        r = {"id": i, "target": t["target"], "source": t["source"], "frame": t["frame"], "label": t["label"],
             "looks": t["looks"], "ring": bool(t.get("surrounds")), "arm": arm, "box": [round(v, 1) for v in t["box"]],
             "surrounds": t.get("surrounds"), "modelMs": ms}
        if not polygons:
            return {**r, "rating": "none", "why": "no outline"}
        verdict = await judged(judge, limit, frames[t["frame"]], polygons, t)
        (OUT / "pictures" / f"{i}.jpg").write_bytes(verdict.pop("view"))
        (OUT / "pictures" / f"{i}-close.jpg").write_bytes(verdict.pop("close"))
        return {**r, **verdict, "areaPx": round(filled_area(polygons)), "box2": [round(v, 1) for v in box_of(polygons)],
                "polygons": [[[round(x, 1), round(y, 1)] for x, y in p] for p in polygons]}

    rows = await asyncio.gather(*(row(i, *o) for i, o in enumerate(outlines)))
    with open(OUT / "rows.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    # 4. Ratings per arm (all regions, and rings apart); the review sample for Will, the judge hidden.
    arms = list(dict.fromkeys(r["arm"] for r in rows))

    def tally(subset):
        return {arm: {k: sum(1 for r in subset if r["arm"] == arm and r["rating"] == k) for k in (*RATINGS, "unclear", "none")}
                for arm in arms if any(r["arm"] == arm for r in subset)}

    summary = {"judge": args.judge, "looker": args.looker, "targets": len(targets),
               "rings": sum(1 for t in targets if t.get("surrounds")), "all": tally(rows),
               "ringsOnly": tally([r for r in rows if r["ring"]])}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    pool = [r for r in rows if r["rating"] in RATINGS]
    random.Random(20261012).shuffle(pool)
    per_arm = max(1, args.review // max(1, len(arms)))
    sample = []
    for arm in arms:  # each arm alike, across the judge's ratings
        mine = sorted((r for r in pool if r["arm"] == arm), key=lambda r: RATINGS.index(r["rating"]))
        step = max(1, len(mine) // per_arm)
        sample += mine[::step][:per_arm]
    sample = sorted(sample, key=lambda r: r["id"])[:args.review]
    (OUT / "review.json").write_text(json.dumps([{"id": r["id"], "label": r["label"], "looks": r["looks"], "ring": r["ring"],
                                                 "source": r["source"], "picture": f"pictures/{r['id']}.jpg",
                                                 "close": f"pictures/{r['id']}-close.jpg"} for r in sample], indent=1))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
