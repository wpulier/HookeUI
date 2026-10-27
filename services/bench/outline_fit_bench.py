"""Which outline fits what is meant, per specimen (W4 of the platform plan, 2026-10-11).

Live 2026-10-10/11: of 78 outline checks, 33 said the outline did not fit or could not tell, and the check's redraw
fixed 6 of 18 bad fits; onion cells clicked one after another came out 1,230 to 7,258 px². This measures, on real
frames of the specimens that failed, what fits:

  targets   the running look's own call (orchestrator/sight.sight_prompt, the app's vision model) names and boxes the
            notable things on each frame, as the app's pointing does
  box arms  each outline model draws each box: EdgeTAM, SAM 2.1 tiny, micro-sam (light microscopy), SAM 3.1 (this
            Mac); and the box itself as the outline (a region with no edge of its own)
  click     a click at the box's centre with EdgeTAM: the mask kept by score (today), the smallest close to it
            (prefer_small), and the first the app's own check (OUTLINE_CHECK's model) says fits, in score order: the
            repair W4 proposes, choosing among the click's own candidates instead of drawing again from a box

Every outline is judged by a model of another family than the app's (GPT-6.1 Sol judges Gemini's pointing and the
outline models), with the app's check prompt and images (outline_check.check, agent.identify_images). A sample is
written for Will's own labels (results/outline-fit/review.json), so the judge is measured against him before its
verdicts decide a default.

  cd services && .venv/bin/python -m bench.outline_fit_bench [--frames 4] [--things 3] [--judge gpt-6.1-sol]

Writes results/outline-fit/rows.jsonl, summary.json and the review sample. Runs the outline models here (MPS), one
model at a time, at low priority; the vision calls go to the cloud (about $5 for the default run).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import time
from pathlib import Path

import cv2
import numpy as np

from orchestrator.agent import identify_images
from orchestrator.assets import store
from orchestrator.coords import Crop, gemini_box_to_frame
from orchestrator.keys import load_keys
from orchestrator.marks import encode_crop
from orchestrator.options import VISION
from orchestrator.outline_check import check
from orchestrator.sight import sight_prompt
from orchestrator.vision import make_vision

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "outline-fit"
DEMO = Path.home() / "MicroscopeMind/profiles/companion-20261010/datasets/ds-20261011-163535/video/part-1.mp4"
SOURCES = {  # what failed live, from recordings in the assets store (and the demo's own dataset)
    "leaf epidermis": "real/2026-06-14-leaf-epidermis-3m43s.mp4",
    "stained stem": "real/2026-10-04-stained-plant-stem-section-42s.mp4",
    "stained section": "real/2026-10-06-stained-plant-section-18s.mp4",
    "sperm wet mount": "real/2026-10-06-demo-sperm-wet-mount-37m57s.mp4",
    "dicot stem (demo)": str(DEMO),
}
BOX_ARMS = ("edgetam", "sam2.1-tiny", "micro-sam-lm", "sam3.1-local")
CLICK_ARMS = ("click: by score", "click: smallest close", "click: check picks")


def sharpest_frames(path: str, k: int, tries: int = 24) -> list[np.ndarray]:
    """k of the sharpest frames among `tries` spread through the recording (a bench's sampling, not the app's rule)."""
    video = cv2.VideoCapture(path)
    count = int(video.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    scored = []
    for i in np.linspace(0.05, 0.95, tries):
        video.set(cv2.CAP_PROP_POS_FRAMES, int(i * count))
        ok, frame = video.read()
        if ok:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            scored.append((float(cv2.Laplacian(gray, cv2.CV_64F).var()), int(i * count), frame))
    video.release()
    scored.sort(key=lambda s: -s[0])
    picked, used = [], []
    for _, at, frame in scored:  # not two frames from the same moment
        if all(abs(at - u) > count / (tries * 2) for u in used):
            picked.append(frame)
            used.append(at)
        if len(picked) == k:
            break
    return picked


def box_of(polygons):
    pts = np.concatenate([np.array(p, np.float32) for p in polygons])
    return float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())


def area(polygons, shape) -> int:
    m = np.zeros(shape[:2], np.uint8)
    for p in polygons:
        cv2.fillPoly(m, [np.round(np.array(p)).astype(np.int32)], 1)
    return int(m.sum())


def box_outline(box):
    x0, y0, x1, y1 = box
    return [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]]


async def targets(vision, image) -> list[dict]:
    """The running look's things with their boxes, in frame px (the whole frame as its crop)."""
    h, w = image.shape[:2]
    crop = Crop(0, 0, w, h)
    jpeg = encode_crop(image, crop, max_side=768)[0]
    result = await vision.ask([jpeg], sight_prompt(None, None), max_output_tokens=900)
    out = []
    for t in (result.data or {}).get("things") or []:
        box = t.get("box_2d") if isinstance(t, dict) else None
        if isinstance(box, list) and len(box) == 4:  # as the running look places them (sight.py)
            out.append({"label": str(t.get("name") or "thing")[:40],
                        "box": tuple(float(v) for v in gemini_box_to_frame([float(v) for v in box], crop))})
    return out


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=int, default=4, help="frames per recording")
    parser.add_argument("--things", type=int, default=3, help="targets per frame")
    parser.add_argument("--looker", default="gemini-3.8-flash", help="the running look's model (the app's)")
    parser.add_argument("--checker", default="gemini-3.5-flash", help="the outline check's model (the app's)")
    parser.add_argument("--judge", default="gpt-6.1-sol", help="another family than the app's models")
    parser.add_argument("--review", type=int, default=40)
    parser.add_argument("--reuse", action="store_true", help="the targets of the saved run (rows.jsonl), not new looks")
    parser.add_argument("--arms", default=",".join(BOX_ARMS), help="the box arms to run")
    parser.add_argument("--tag", default="", help="write rows-<tag>.jsonl and summary-<tag>.json: these arms only")
    args = parser.parse_args()
    arms = tuple(a for a in args.arms.split(",") if a)
    os.nice(10)
    load_keys()
    from gpu_worker.worker import Models

    looker, checker, judge = (make_vision(VISION[m].spec) for m in (args.looker, args.checker, args.judge))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "pictures").mkdir(exist_ok=True)

    # 1. Frames and the look's targets on each (or the saved run's targets, on the same frames).
    cases = []
    saved = []
    if args.reuse:
        for r in map(json.loads, (OUT / "rows.jsonl").open()):
            if r["arm"] == BOX_ARMS[0]:
                saved.append(r)
    for source, path in SOURCES.items():
        full = path if os.path.isabs(path) else str(store().root / "footage" / path)
        if not os.path.exists(full):
            print(f"skip {source}: {full} missing", flush=True)
            continue
        for n, image in enumerate(sharpest_frames(full, args.frames)):
            if args.reuse:
                things = [{"label": r["label"], "box": tuple(r["box"]), "target": r["id"]} for r in saved
                          if r["frame"] == f"{source}-{n}"]
            else:
                things = (await targets(looker, image))[:args.things]
            for t in things:
                cases.append({"source": source, "frame": f"{source}-{n}", "image": image, **t})
            print(f"{source} frame {n}: {[t['label'] for t in things]}", flush=True)

    # 2. Outlines, one model at a time (low memory beside a live app), each kept with its case.
    models = Models()
    outlines = []  # (case, arm, polygons, model ms)
    for arm in arms:
        for c in cases:
            jpeg = cv2.imencode(".jpg", c["image"], [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
            req = {"frame_id": hash(c["frame"]) & 0xFFFF, "jpeg": jpeg, "box": list(c["box"]), "model": arm}
            try:
                got = models.segment(req)
            except Exception as exc:  # an arm that can't run here is said, never filled in
                print(f"{arm} failed: {type(exc).__name__}: {exc}", flush=True)
                break
            outlines.append((c, arm, got.get("polygons") or [], got.get("ms")))
        try:
            models.unload(arm)
        except Exception:
            pass
    if args.tag:  # these arms only, judged as the full run's are, beside its files
        limit = asyncio.Semaphore(6)
        rows = []

        async def judged_row(i, c, arm, polygons, model_ms):
            r = {"id": i, "target": c.get("target"), "source": c["source"], "frame": c["frame"], "label": c["label"],
                 "arm": arm, "box": [round(v, 1) for v in c["box"]], "modelMs": model_ms}
            if not polygons:
                return {**r, "fits": "none", "why": "no outline"}
            h, w = c["image"].shape[:2]
            view, close = identify_images(c["image"], polygons, Crop(0, 0, w, h), box_of(polygons))
            async with limit:
                fits, why, _, ms = await check(judge, view, close, c["label"], f"Outline the {c['label']}")
            return {**r, "fits": fits, "why": why, "judgeMs": round(ms), "areaPx": area(polygons, c["image"].shape)}

        rows = await asyncio.gather(*(judged_row(i, *o) for i, o in enumerate(outlines)))
        with open(OUT / f"rows-{args.tag}.jsonl", "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        summary = {"judge": args.judge, "targets": len(cases), "arms": {
            arm: {"fits": f"{sum(r['fits'] == 'yes' for r in rows if r['arm'] == arm)}/{sum(1 for r in rows if r['arm'] == arm)}",
                  "none": sum(r['fits'] == 'none' for r in rows if r['arm'] == arm)} for arm in arms}}
        (OUT / f"summary-{args.tag}.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary, indent=2))
        return
    for c in cases:
        outlines.append((c, "the box itself", box_outline(c["box"]), 0.0))
    click_cands = []
    for c in cases:
        x0, y0, x1, y1 = c["box"]
        jpeg = cv2.imencode(".jpg", c["image"], [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
        got = models.segment({"frame_id": hash(c["frame"]) & 0xFFFF, "jpeg": jpeg, "points": [[(x0 + x1) / 2, (y0 + y1) / 2]],
                              "model": "edgetam", "candidates": True})
        click_cands.append((c, got.get("candidates") or []))

    # 3. The click arms: by score (today), smallest close, and the first the app's check says fits.
    limit = asyncio.Semaphore(6)

    async def judged(c, polygons, model):
        h, w = c["image"].shape[:2]
        crop = Crop(0, 0, w, h)
        view, close = identify_images(c["image"], polygons, crop, box_of(polygons))
        async with limit:
            fits, why, _, ms = await check(model, view, close, c["label"], f"Outline the {c['label']}")
        return fits, why, ms, (view, close)

    for c, cands in click_cands:
        if not cands:
            for arm in CLICK_ARMS:
                outlines.append((c, arm, [], None))
            continue
        best = cands[0]
        close = [k for k in cands if k["score"] >= best["score"] - 0.08]
        outlines.append((c, CLICK_ARMS[0], best["polygons"], None))
        outlines.append((c, CLICK_ARMS[1], min(close, key=lambda k: k["area"])["polygons"], None))
        picked = best
        for k in cands:  # in score order, the first the app's check says fits
            if (await judged(c, k["polygons"], checker))[0] == "yes":
                picked = k
                break
        outlines.append((c, CLICK_ARMS[2], picked["polygons"], None))

    # 4. Every outline judged by the other family's model.
    async def row(i, c, arm, polygons, model_ms):
        r = {"id": i, "source": c["source"], "frame": c["frame"], "label": c["label"], "arm": arm,
             "box": [round(v, 1) for v in c["box"]], "modelMs": model_ms}
        if not polygons:
            return {**r, "fits": "none", "why": "no outline"}
        fits, why, ms, (view, close) = await judged(c, polygons, judge)
        for name, jpeg in ((f"{i}.jpg", view), (f"{i}-close.jpg", close)):  # what the judge saw, for the review page
            (OUT / "pictures" / name).write_bytes(jpeg)
        return {**r, "fits": fits, "why": why, "judgeMs": round(ms), "areaPx": area(polygons, c["image"].shape),
                "polygons": [[[round(x, 1), round(y, 1)] for x, y in p] for p in polygons]}

    rows = await asyncio.gather(*(row(i, *o) for i, o in enumerate(outlines)))
    with open(OUT / "rows.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    # 5. Fit rate per recording and arm; the review sample (verdicts hidden on the page).
    summary = {"judge": args.judge, "looker": args.looker, "checker": args.checker, "targets": len(cases), "bySource": {}}
    for source in sorted({r["source"] for r in rows}):
        mine = [r for r in rows if r["source"] == source]
        summary["bySource"][source] = {arm: f"{sum(r['fits'] == 'yes' for r in mine if r['arm'] == arm)}/"
                                            f"{sum(1 for r in mine if r['arm'] == arm)}" for arm in (*BOX_ARMS, 'the box itself', *CLICK_ARMS)}
    summary["all"] = {arm: f"{sum(r['fits'] == 'yes' for r in rows if r['arm'] == arm)}/{sum(1 for r in rows if r['arm'] == arm)}"
                      for arm in (*BOX_ARMS, 'the box itself', *CLICK_ARMS)}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    pool = [r for r in rows if r["fits"] in ("yes", "too_big", "too_small", "wrong")]
    random.Random(20261011).shuffle(pool)
    yes = [r for r in pool if r["fits"] == "yes"][:args.review // 2]
    no = [r for r in pool if r["fits"] != "yes"][:args.review - len(yes)]
    sample = sorted(yes + no, key=lambda r: r["id"])
    (OUT / "review.json").write_text(json.dumps([{"id": r["id"], "label": r["label"], "source": r["source"],
                                                  "picture": f"pictures/{r['id']}.jpg", "close": f"pictures/{r['id']}-close.jpg", "judge": r["fits"]} for r in sample], indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
