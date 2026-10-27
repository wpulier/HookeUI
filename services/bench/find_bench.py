"""How well does "highlight X" grab the right things, against labelled truth? (E1, first set)

BCCD blood smears (MIT; footage/corpus, `make corpus`): every white cell, red cell and platelet
boxed by hand. For each sampled picture and request, the brain's own answer prompt (json-actions,
the full tool list) is asked with the picture; the boxes it points at ("targets") are outlined
with EdgeTAM from each box, as the app does; each outline's box is matched to the truth
(IoU >= 0.5, one to one). Reported per model and request: precision, recall, and how often the
model reached for a tool (find, count...) as well as pointing.

  cd services && .venv/bin/python -m bench.find_bench [--models gemini-3.5-flash,gemini-3.8-flash] [--n 15]
  cd services && .venv/bin/python -m bench.find_bench --finder [--models gemini-3.5-flash] [--n 12]

--pointing box,masks: the look's pointing option; "masks" numbers EdgeTAM's automatic masks of the
picture (orchestrator/masks.py) and a chosen number's mask is the outline when it agrees with the
model's own box (else the box is outlined, as "box").

--pointing molmo: the look as "box", and beside it Molmo 2 4B (this Mac) pointing at what the request is
about (orchestrator/molmo.py); each point is outlined as a click (EdgeTAM), labelled from the model's
targets; the model gave no targets: nothing; Molmo gave no points: the model's boxes (as the app does).

--finder: the find tool's own ways instead (orchestrator/finder, "find every ..."): "look" (the vision
model boxes them) and "cells" (the cell finder outlines every cell, the model picks which; find by="cells");
"cells:<CELLS option>" picks from another cell finder ("cells:micro-sam-lm").
"points" (bench only, count by pointing): the model points at every one ("none" allowed) and each
point is an EdgeTAM click.

Notes: BCCD's red cells are not all boxed (a known gap of the set), so red-cell precision reads
low where the model found real but unlabelled cells; white cells (one or two a picture) and
platelets are the reliable cases. Pictures are full slides, not phone-through-eyepiece views.
Writes results/find-bench.json (or find-bench-finder.json) and a contact sheet.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
from pathlib import Path

import cv2
import numpy as np

from bench.corpus import adapters
from bench.corpus.build import unpack
from bench.corpus.fetch import RAW
from bench.tool_choice import registry
from gpu_worker.worker import Models
from orchestrator.brain.registry import parse_actions
from orchestrator.coords import Crop, gemini_box_to_frame
from orchestrator.keys import load_keys
from orchestrator.marks import encode_crop
from orchestrator.options import VISION
from orchestrator.prompts import ASK, ASK_MASKS, with_tools
from orchestrator.vision import make_vision

HERE = Path(__file__).resolve().parent
REQUESTS = [  # (what the user says, the truth labels it means)
    ("Highlight the white blood cell.", ("WBC",)),
    ("Point out the platelets.", ("Platelets",)),
    ("Highlight every red blood cell.", ("RBC",)),
]
FINDS = [("every platelet", ("Platelets",)), ("every red blood cell", ("RBC",)), ("every white blood cell", ("WBC",))]

POINT_ALL = """You are looking through a microscope together with the user. The image is the current view.
The user asked: "{question}"
Point at {what}: one point on the centre of each one you can see, as [y, x] on a 0-1000 scale of the
image. If there are none, give no points and say why in "note". Never point at what you cannot see.
Reply with JSON only: {{"points": [[y, x]], "note": ""}}"""


def pictures(n: int, seed: int = 7) -> list[tuple[Path, list[dict]]]:
    root = unpack(RAW / "bccd")
    jpgs = sorted(root.glob("**/JPEGImages/*.jpg"))
    out = []
    for p in jpgs:
        xml = next(iter(root.glob(f"**/Annotations/{p.stem}.xml")), None)
        if xml is not None:
            truth = adapters.voc(xml)
            if any(t["label"] == "WBC" for t in truth):
                out.append((p, truth))
    random.Random(seed).shuffle(out)
    return out[:n]


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def match(found: list, truth: list) -> int:
    """Found boxes matched one to one to truth boxes at IoU >= 0.5 (greedy, best first)."""
    pairs = sorted(((iou(f, t), i, j) for i, f in enumerate(found) for j, t in enumerate(truth)), reverse=True)
    used_f, used_t, hits = set(), set(), 0
    for score, i, j in pairs:
        if score < 0.5:
            break
        if i not in used_f and j not in used_t:
            used_f.add(i), used_t.add(j)
            hits += 1
    return hits


def mask_box(polygons):
    if not polygons:
        return None
    pts = np.concatenate([np.array(p, np.float32) for p in polygons])
    return float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())


def outline_boxes(models, k, frame_jpeg, boxes) -> list:
    out = []
    for b in boxes:
        seg = models.segment({"frame_id": k, "jpeg": frame_jpeg, "box": list(b), "model": "edgetam"})
        mb = mask_box(seg["polygons"])
        if mb is not None:
            out.append(mb)
    return out


def outline_points(models, k, frame_jpeg, points) -> list:
    """Each point outlined as a click is (one point prompt; the app's tools.outline_at)."""
    out = []
    for x, y in points:
        seg = models.segment({"frame_id": k, "jpeg": frame_jpeg, "points": [[float(x), float(y)]], "model": "edgetam"})
        mb = mask_box(seg["polygons"])
        if mb is not None:
            out.append(mb)
    return out


def point_hits(points, truth) -> int:
    """Points matched one to one to the truth boxes they lie in."""
    used, hits = set(), 0
    for x, y in points:
        for j, t in enumerate(truth):
            if j not in used and t[0] <= x <= t[2] and t[1] <= y <= t[3]:
                used.add(j)
                hits += 1
                break
    return hits


def mask_targets(targets: list, regions: list, crop) -> tuple[list, list, int]:
    """(boxes to outline with EdgeTAM, outlines taken straight from masks, how many by mask), as
    Agent.point does for pointing "masks"."""
    from orchestrator.masks import agrees, region_box

    boxes, direct = [], []
    for t in targets:
        if not isinstance(t, dict):
            continue
        has = isinstance(t.get("box_2d"), list) and len(t["box_2d"]) == 4
        box = gemini_box_to_frame([float(v) for v in t["box_2d"]], crop) if has else None
        mark = t.get("mark")
        if isinstance(mark, int) and 1 <= mark <= len(regions) and (box is None or agrees(region_box(regions[mark - 1]), box)):
            direct.append(region_box(regions[mark - 1]))
        elif box is not None:
            boxes.append(box)
    return boxes, direct, len(direct)


async def look_mode(args) -> None:
    from orchestrator.candidates import propose
    from orchestrator.marks import render_masks

    tools = registry().prompt_section()
    models = Models()
    pics = pictures(args.n)
    rows, tiles = [], []
    automasks: dict[int, dict] = {}
    for model in args.models.split(","):
        vision = make_vision(VISION[model].spec)
        for k, (path, truth) in enumerate(pics):
            image = cv2.imread(str(path))
            h, w = image.shape[:2]
            crop = Crop(0, 0, w, h)
            jpeg, _ = encode_crop(image, crop)
            frame_jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
            for pointing in args.pointing.split(","):
                if pointing == "masks" and k not in automasks:
                    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                    auto = models.automask({"frame_id": 1000 + k, "jpeg": frame_jpeg,
                                            "points": [[c.x, c.y] for c in propose(gray, None)]})
                    automasks[k] = {**auto, "marked": render_masks(image, crop, auto["regions"])[1]}
                for text, labels in REQUESTS:
                    want = [t["box"] for t in truth if t["label"] in labels]
                    template = ASK_MASKS if pointing == "masks" else ASK
                    prompt = with_tools(template, tools, "(nothing outlined)", "").format(context="", history="(none)", question=text)
                    images = [jpeg, automasks[k]["marked"]] if pointing == "masks" else [jpeg]
                    molmo, wall = None, None
                    if pointing == "molmo":
                        from orchestrator.molmo import query_for

                        t0 = time.perf_counter()
                        look = asyncio.create_task(vision.ask(images, prompt))
                        molmo = await asyncio.to_thread(models.molmo_point, {"frame_id": 5000 + k, "jpeg": frame_jpeg,
                                                                             "query": query_for(text)})
                        result = await look
                        wall = (time.perf_counter() - t0) * 1000
                    else:
                        result = await vision.ask(images, prompt)
                    data = result.data or {}
                    targets = data.get("targets") or []
                    point_outlines = None
                    if pointing == "molmo" and molmo["points"] and [t for t in targets if isinstance(t, dict)]:
                        from orchestrator.molmo import assign

                        assigned = assign(molmo["points"], targets, crop)
                        point_outlines = outline_points(models, k, frame_jpeg, [t["point"] for t in assigned])
                        boxes, direct, by_mask = [], [], 0
                    elif pointing == "masks":
                        boxes, direct, by_mask = mask_targets(targets, automasks[k]["regions"], crop)
                    else:
                        boxes = [gemini_box_to_frame([float(v) for v in t["box_2d"]], crop) for t in targets
                                 if isinstance(t, dict) and isinstance(t.get("box_2d"), list) and len(t["box_2d"]) == 4]
                        direct, by_mask = [], 0
                    outlines = point_outlines if point_outlines is not None else direct + outline_boxes(models, k, frame_jpeg, boxes)
                    used = [n for n, _ in parse_actions(data.get("actions"))]
                    pointed = len(molmo["points"]) if point_outlines is not None else len(boxes) + len(direct)
                    row = {"model": model, "pointing": pointing, "picture": path.name, "request": text, "truth": len(want),
                           "pointed": pointed, "byMask": by_mask,
                           "boxHits": point_hits(molmo["points"], want) if point_outlines is not None else match(boxes + direct, want),
                           "outlined": len(outlines), "outlineHits": match(outlines, want),
                           "tools": used, "ms": round(wall if wall is not None else result.latency_ms), "error": result.error,
                           "automaskMs": automasks[k]["ms"] if pointing == "masks" else None,
                           "visionMs": round(result.latency_ms), "molmoMs": molmo["ms"] if molmo else None,
                           "molmoPoints": len(molmo["points"]) if molmo else None, "molmoRead": molmo["read"] if molmo else None, "molmoText": molmo["text"] if molmo else None,
                           "usedMolmo": point_outlines is not None if molmo else None}
                    rows.append(row)
                    print(json.dumps(row), flush=True)
                    if model == args.models.split(",")[0] and k < 4 and labels == ("WBC",) and pointing == "box":
                        tile = image.copy()
                        for b in want:
                            cv2.rectangle(tile, tuple(map(int, b[:2])), tuple(map(int, b[2:])), (80, 255, 80), 2)
                        for b in outlines:
                            cv2.rectangle(tile, tuple(map(int, b[:2])), tuple(map(int, b[2:])), (255, 80, 255), 2)
                        tiles.append(cv2.resize(tile, (400, 300)))
    summary = {}
    for model, pointing in [(m, p) for m in args.models.split(",") for p in args.pointing.split(",")]:
        for text, _ in REQUESTS:
            rs = [r for r in rows if r["model"] == model and r["request"] == text and r["pointing"] == pointing]
            truth, pointed, outlined = sum(r["truth"] for r in rs), sum(r["pointed"] for r in rs), sum(r["outlined"] for r in rs)
            bh, oh = sum(r["boxHits"] for r in rs), sum(r["outlineHits"] for r in rs)
            summary[f"{model} | {pointing} | {text}"] = {"byMask": sum(r["byMask"] for r in rs),
                "recallBox": round(bh / truth, 2) if truth else None, "precisionBox": round(bh / pointed, 2) if pointed else None,
                "recallOutline": round(oh / truth, 2) if truth else None,
                "precisionOutline": round(oh / outlined, 2) if outlined else None,
                "usedATool": sum(1 for r in rs if r["tools"]), "p50Ms": int(np.median([r["ms"] for r in rs])) if rs else None,
                **({"molmoP50Ms": int(np.median([r["molmoMs"] for r in rs])), "usedMolmo": sum(bool(r["usedMolmo"]) for r in rs)}
                   if pointing == "molmo" and rs else {})}
    print(json.dumps(summary, indent=2))
    name = "find-bench.json" if args.pointing == "box" else f"find-bench-{args.pointing.replace(',', '-')}.json"
    (HERE / "results" / name).write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    if tiles:
        cv2.imwrite(str(HERE / "results" / "find-bench.jpg"), np.hstack(tiles))


async def finder_mode(args) -> None:
    """The find tool's look, one whole view against four enlarged quarters (FIND_ALL)."""
    from types import SimpleNamespace

    from orchestrator.agent import Agent
    from orchestrator.finder import PICK_CELLS, numbered, picked
    from orchestrator.prompts import FIND

    models = Models()
    pics = pictures(args.n)
    rows = []
    for model in args.models.split(","):
        vision = make_vision(VISION[model].spec)

        class Looker:
            hub = SimpleNamespace(settings=SimpleNamespace(vision=model))
            look_in = Agent.look_in

            def vision(self, option):
                return vision

        agent = Looker()
        for k, (path, truth) in enumerate(pics):
            image = cv2.imread(str(path))
            h, w = image.shape[:2]
            view = (SimpleNamespace(id=k, width=w, height=h), image, Crop(0, 0, w, h))
            frame_jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
            for what, labels in FINDS:
                want = [t["box"] for t in truth if t["label"] in labels]
                thing = what.replace("every ", "")
                ctx = SimpleNamespace(question=f"Find {what}.")
                for strategy in args.strategies.split(","):
                    if strategy == "points":
                        clean, _ = encode_crop(image, Crop(0, 0, w, h))
                        result = await vision.ask([clean], POINT_ALL.format(question=ctx.question, what=what))
                        pts = [p for p in (result.data or {}).get("points") or [] if isinstance(p, list) and len(p) == 2]
                        outlines = []
                        for py, px in pts[:60]:
                            x, y = float(px) / 1000 * w, float(py) / 1000 * h
                            seg = models.segment({"frame_id": k, "jpeg": frame_jpeg, "points": [[x, y]], "model": "edgetam"})
                            mb = mask_box(seg["polygons"])
                            if mb is not None:
                                outlines.append(mb)
                        row = {"model": model, "strategy": strategy, "picture": path.name, "find": what, "truth": len(want),
                               "found": len(outlines), "hits": match(outlines, want), "ms": round(result.latency_ms)}
                        rows.append(row)
                        print(json.dumps(row), flush=True)
                        continue
                    if strategy.split(":")[0] == "cells":  # "cells" (Cellpose-SAM) or "cells:<CELLS option>"
                        finder = strategy.split(":")[1] if ":" in strategy else "cellpose-sam"
                        polys = models.cells({"frame_id": k, "jpeg": frame_jpeg, "model": finder})["polygons"][:60]
                        clean, _ = encode_crop(image, Crop(0, 0, w, h))
                        result = await vision.ask([clean, numbered(image, polys, Crop(0, 0, w, h))],
                                                  PICK_CELLS.format(question=ctx.question, what=thing))
                        chosen = picked(result.data or {}, polys)
                        outlines = [b for b in (mask_box([p]) for p in chosen) if b]
                        row = {"model": model, "strategy": strategy, "picture": path.name, "find": what, "truth": len(want),
                               "found": len(outlines), "hits": match(outlines, want), "ms": round(result.latency_ms)}
                        rows.append(row)
                        print(json.dumps(row), flush=True)
                        continue
                    prompt = FIND.format(images=" The image is the current view through the eyepiece.", question=ctx.question,
                                         quantity="every", what=thing, where=" in the view", image="the image")
                    data, used = await agent.look_in(prompt, view, None)
                    outlines = outline_boxes(models, k, frame_jpeg, [tuple(t["box"]) for t in data.get("targets") or []])
                    row = {"model": model, "strategy": strategy, "picture": path.name, "find": what, "truth": len(want),
                           "found": len(outlines), "hits": match(outlines, want), "ms": round(used.get("latency_ms") or 0)}
                    rows.append(row)
                    print(json.dumps(row), flush=True)
    summary = {}
    for model in args.models.split(","):
        for what, _ in FINDS:
            for strategy in args.strategies.split(","):
                rs = [r for r in rows if r["model"] == model and r["find"] == what and r["strategy"] == strategy]
                t, f, hh = sum(r["truth"] for r in rs), sum(r["found"] for r in rs), sum(r["hits"] for r in rs)
                summary[f"{model} | {what} | {strategy}"] = {"recall": round(hh / t, 2) if t else None,
                                                              "precision": round(hh / f, 2) if f else None, "found": f, "truth": t}
    print(json.dumps(summary, indent=2))
    name = "find-bench-finder.json" if args.strategies == "look,cells" else \
        f"find-bench-finder-{args.strategies.replace(',', '-').replace(':', '_')}.json"
    (HERE / "results" / name).write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="gemini-3.5-flash,gemini-3.8-flash")
    parser.add_argument("--n", type=int, default=15)
    parser.add_argument("--finder", action="store_true", help="the find tool's ways (--strategies)")
    parser.add_argument("--pointing", default="box", help="the look's pointing: box, masks, molmo (comma-separated)")
    parser.add_argument("--strategies", default="look,cells", help="--finder: look, cells, points")
    args = parser.parse_args()
    load_keys()
    await (finder_mode(args) if args.finder else look_mode(args))


if __name__ == "__main__":
    asyncio.run(main())
