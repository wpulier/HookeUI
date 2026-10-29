"""Benchmark "what's that?" grounding: latency, correct-object rate and the guesses, per
cloud model and strategy, on the hand-labelled questions in bench/questions.json.

Strategies:
  marks  clean view + numbered-candidate view; the model picks a number or falls back to a box
  box    clean view only; the model returns a bounding box
  masks  clean view + the view with EdgeTAM's automatic masks outlined and numbered (pointing
         "masks", orchestrator/masks.py); the model picks a number or falls back to a box
  molmo  the box look as "box", and beside it Molmo 2 4B on this Mac pointing at what the question
         is about (pointing "molmo", orchestrator/molmo.py); the points are what the app outlines
         (no points: the model's box, as the app does). --molmo-query: "about" (the app's), "question"
         (the words as they are), "label" (after the look: "Point to the <its guess>"). --molmo-ahead:
         Molmo has read the view before the question (the app reads the settled view ahead)

--outline also scores what the app would draw: for a box, EdgeTAM's outline from it (as the app
does); for a mask, the mask itself. "outlineHit": a target point lies inside the outline.

Usage (keys from ~/Desktop/micromind.txt):
  python -m bench.grounding --models gemini:gemini-3.8-flash,gemini:gemini-3.5-flash-lite \
      --strategies marks,box --repeats 2
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import cv2
import numpy as np

from orchestrator.candidates import propose
from orchestrator.coords import gemini_box_to_frame
from orchestrator.fov import detect_fov
from orchestrator.keys import load_keys
from orchestrator.marks import encode_crop, fov_crop, render_marks, render_masks
from orchestrator.masks import agrees, region_box
from orchestrator.prompts import LOCATE_WITH_BOX, LOCATE_WITH_MARKS, LOCATE_WITH_MASKS
from orchestrator.vision import make_vision

BENCH = Path(__file__).resolve().parent
from orchestrator.assets import store  # noqa: E402  (the assets store: assets/README.md)
FRAMES = store().bench("frames")  # regenerated from the real recordings: python -m bench.extract_frames


def score(center, box, targets, tolerance) -> bool:
    for tx, ty in targets:
        if center and np.hypot(center[0] - tx, center[1] - ty) <= tolerance:
            return True
        if box and box[0] - 5 <= tx <= box[2] + 5 and box[1] - 5 <= ty <= box[3] + 5:
            # A box containing the target only counts if it is not absurdly large.
            if (box[2] - box[0]) * (box[3] - box[1]) <= (6 * tolerance) ** 2:
                return True
    return False


MAX_SIDE = 1024
OUTLINE = False
MOLMO_QUERY = "about"
MOLMO_AHEAD = False
_MODELS = None


def models():
    """EdgeTAM on this Mac (gpu_worker.worker.Models), loaded on first use."""
    global _MODELS
    if _MODELS is None:
        from gpu_worker.worker import Models

        _MODELS = Models()
    return _MODELS


def has_box(data: dict) -> bool:
    return isinstance(data.get("box_2d"), list) and len(data["box_2d"]) == 4


def inside(polygons, targets) -> bool:
    return any(cv2.pointPolygonTest(np.array(poly, np.float32), (float(tx), float(ty)), False) >= 0
               for poly in polygons for tx, ty in targets)


def prepare(item: dict, masks: bool = False) -> dict:
    image = cv2.imread(str(FRAMES / item["frame"]))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    fov = detect_fov(gray)
    crop = fov_crop(fov, image.shape[1], image.shape[0])
    candidates = propose(gray, fov)
    marked = render_marks(image, candidates, crop, max_side=MAX_SIDE)
    clean, scale = encode_crop(image, crop, max_side=MAX_SIDE)
    jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
    out = {"clean": clean, "marked": marked, "crop": crop, "jpeg": jpeg, "id": abs(hash(item["frame"])) % 100000}
    if masks:
        # As the app does after the slide settles: EdgeTAM masks the disc from a grid plus the spots.
        auto = models().automask({"frame_id": out["id"], "jpeg": jpeg, "disc": [fov.cx, fov.cy, fov.radius] if fov else None,
                                  "points": [[c.x, c.y] for c in candidates]})
        regions = auto["regions"]
        _, masked, _ = render_masks(image, crop, regions, max_side=MAX_SIDE)
        out.update(regions=regions, masked=masked, automaskMs=auto["ms"])
        (BENCH / "results" / "pointing").mkdir(parents=True, exist_ok=True)
        (BENCH / "results" / "pointing" / f"masks-{item['frame']}").write_bytes(masked)
    return out


def molmo_points(item: dict, prepared: dict, query: str) -> dict:
    """Molmo's points for `query` on the eyepiece crop (frame px), as the worker op does."""
    crop = prepared["crop"]
    return models().molmo_point({"frame_id": prepared["id"], "jpeg": prepared["jpeg"], "query": query,
                                 "crop": [crop.x, crop.y, crop.width, crop.height]})


def on_target(point, item) -> bool:
    return any(np.hypot(point[0] - tx, point[1] - ty) <= item["tolerance"] for tx, ty in item["targets"])


async def run_one(vision, strategy: str, item: dict, prepared: dict) -> dict:
    crop = prepared["crop"]
    molmo = None
    if strategy == "molmo":
        from orchestrator.molmo import query_for

        prompt = LOCATE_WITH_BOX.format(question=item["question"], context="")
        if MOLMO_AHEAD:
            models().molmo().read((prepared["id"], (crop.x, crop.y, crop.width, crop.height)),
                                  cv2.cvtColor(cv2.imdecode(np.frombuffer(prepared["jpeg"], np.uint8), cv2.IMREAD_COLOR)
                                               [crop.y:crop.y + crop.height, crop.x:crop.x + crop.width], cv2.COLOR_BGR2RGB))
        else:
            prepared["id"] += 100000  # a fresh read of the picture every time (as on a new view)
        t0 = time.perf_counter()
        if MOLMO_QUERY == "label":
            result = await vision.ask([prepared["clean"]], prompt)
            guess = str((result.data or {}).get("guess") or "thing")
            molmo = await asyncio.to_thread(molmo_points, item, prepared, f"Point to the {guess}.")
        else:
            look = asyncio.create_task(vision.ask([prepared["clean"]], prompt))
            molmo = await asyncio.to_thread(molmo_points, item, prepared, query_for(item["question"], MOLMO_QUERY))
            result = await look
        wall = (time.perf_counter() - t0) * 1000
    elif strategy == "marks":
        prompt = LOCATE_WITH_MARKS.format(question=item["question"], context="")
        result = await vision.ask([prepared["clean"], prepared["marked"].jpeg], prompt)
    elif strategy == "masks":
        prompt = LOCATE_WITH_MASKS.format(question=item["question"], context="")
        result = await vision.ask([prepared["clean"], prepared["masked"]], prompt)
    else:
        prompt = LOCATE_WITH_BOX.format(question=item["question"], context="")
        result = await vision.ask([prepared["clean"]], prompt)

    center = box = None
    used = None
    data = result.data or {}
    mark = data.get("mark")
    polygons, outline_ms = None, None
    if strategy == "marks" and isinstance(mark, int) and mark in prepared["marked"].marks:
        cand = prepared["marked"].marks[mark]
        center, used = (cand.x, cand.y), "mark"
    elif strategy == "masks" and isinstance(mark, int) and 1 <= mark <= len(prepared["regions"]) and (
            not has_box(data) or agrees(region_box(prepared["regions"][mark - 1]), gemini_box_to_frame(data["box_2d"], crop))):
        # As Agent.point: the chosen mask is the outline, when it is the thing the model also boxed.
        region = prepared["regions"][mark - 1]
        box = region_box(region)
        center, used, polygons, outline_ms = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), "mask", region["polygons"], 0.0
    elif strategy == "molmo" and molmo["points"] and has_box(data):
        # As Agent.point: each point is a click; scored on the first, all points reported.
        center, used = tuple(molmo["points"][0]), "molmo"
    elif has_box(data):
        box = gemini_box_to_frame(data["box_2d"], crop)
        center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        used = "box (mask disagreed)" if strategy == "masks" and isinstance(mark, int) else \
            "box (molmo none)" if strategy == "molmo" else "box"
    if OUTLINE and used == "molmo":
        polygons, outline_ms = [], 0.0
        for px, py in molmo["points"]:  # each point as a click (tools.outline_at)
            seg = models().segment({"frame_id": prepared["id"], "jpeg": prepared["jpeg"], "points": [[px, py]], "model": "edgetam"})
            polygons += seg["polygons"][:1]
            outline_ms += seg["ms"]
    if OUTLINE and used and used.startswith("box"):
        # What the app draws from a box: EdgeTAM's outline (tools.outline_at with box=).
        seg = models().segment({"frame_id": prepared["id"], "jpeg": prepared["jpeg"], "box": [float(v) for v in box],
                                "model": "edgetam"})
        polygons, outline_ms = seg["polygons"], seg["ms"]
    return {
        "question": item["question"],
        "frame": item["frame"],
        "kind": item["kind"],
        "strategy": strategy,
        "model": result.model,
        "latencyMs": round(wall if strategy == "molmo" else result.latency_ms),
        "visionMs": round(result.latency_ms),
        "molmoMs": molmo["ms"] if molmo else None,
        "molmoRead": molmo["read"] if molmo else None,
        "molmoQuery": MOLMO_QUERY if molmo else None,
        "molmoText": molmo["text"] if molmo else None,
        "points": [[round(x), round(y)] for x, y in molmo["points"]] if molmo else None,
        "molmoCorrect": bool(molmo["points"]) and on_target(molmo["points"][0], item) if molmo else None,
        "pointsOnTarget": sum(on_target(p, item) for p in molmo["points"]) if molmo else None,
        "pointInBox": (bool(molmo["points"]) and has_box(data) and all(
            b[0] <= p[0] <= b[2] and b[1] <= p[1] <= b[3] for b in [gemini_box_to_frame(data["box_2d"], crop)]
            for p in molmo["points"][:1])) if molmo else None,
        "correct": score(center, box, item["targets"], item["tolerance"]),
        "outlineHit": inside(polygons, item["targets"]) if OUTLINE else None,
        "outlineMs": outline_ms,
        "box": [round(float(v)) for v in box] if box is not None else None,
        "modelBox": [round(float(v)) for v in gemini_box_to_frame(data["box_2d"], crop)] if has_box(data) else None,
        "mark": mark,
        "automaskMs": prepared.get("automaskMs") if strategy == "masks" else None,
        "masks": len(prepared["regions"]) if strategy == "masks" else None,
        "used": used,
        "guess": data.get("guess"),
        "oneLine": data.get("one_line"),
        "confidence": data.get("confidence"),
        "usage": result.usage,
        "error": result.error or (None if result.data else f"unparsed: {result.raw_text[:200]}"),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", required=True)
    parser.add_argument("--strategies", default="marks,box")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", default=str(BENCH / "results" / "grounding.json"))
    parser.add_argument("--max-side", type=int, default=1024, help="longest side of the image sent")
    parser.add_argument("--media-resolution", default="", help="Gemini media resolution: low, medium, high")
    parser.add_argument("--outline", action="store_true", help="also score the outline the app would draw")
    parser.add_argument("--molmo-query", default="about", help="molmo: about, question or label")
    parser.add_argument("--molmo-ahead", action="store_true", help="molmo: the view is read before the question")
    args = parser.parse_args()
    global MAX_SIDE, OUTLINE, MOLMO_QUERY, MOLMO_AHEAD
    MAX_SIDE = args.max_side
    OUTLINE = args.outline
    MOLMO_QUERY, MOLMO_AHEAD = args.molmo_query, args.molmo_ahead
    if "molmo" in args.strategies.split(","):
        t0 = time.perf_counter()
        molmo = models().molmo()
        print(f"Molmo 2 4B loaded in {molmo.load_ms / 1000:.1f} s, warmed in {molmo.warm_ms / 1000:.1f} s "
              f"({(time.perf_counter() - t0):.1f} s in all), {molmo.memory_gb()} GB on the GPU")

    available = load_keys()
    questions = json.loads((BENCH / "questions.json").read_text())["questions"]
    if args.limit:
        questions = questions[: args.limit]
    want_masks = "masks" in args.strategies.split(",")
    prepared = {q["frame"]: prepare(q, masks=want_masks) for q in questions}

    rows = []
    for spec in args.models.split(","):
        provider = spec.split(":")[0]
        needed = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}[provider]
        if not available.get(needed):
            print(f"skip {spec}: {needed} is not set")
            continue
        vision = make_vision(spec)
        if args.media_resolution and hasattr(vision, "media_resolution"):
            vision.media_resolution = args.media_resolution
        for strategy in args.strategies.split(","):
            for _ in range(args.repeats):
                for item in questions:  # sequential: latency is what we are measuring
                    row = await run_one(vision, strategy, item, prepared[item["frame"]])
                    row["spec"] = spec
                    rows.append(row)
                    flag = "ok " if row["correct"] else "MISS"
                    print(f"{flag} {spec:38s} {strategy:5s} {row['latencyMs']:6d}ms  {item['question'][:44]:44s} -> {row['guess']}  {row['error'] or ''}")

    summary = {}
    for row in rows:
        key = f"{row['spec']} | {row['strategy']}"
        s = summary.setdefault(key, {"latencies": [], "correct": 0, "n": 0, "errors": 0, "outlineHits": 0, "byMark": 0})
        if row.get("molmoMs") is not None:
            m = s.setdefault("molmo", {"correct": 0, "points": 0, "onTarget": 0, "none": 0, "inBox": 0, "ms": []})
            m["correct"] += bool(row["molmoCorrect"])
            m["points"] += len(row["points"])
            m["onTarget"] += row["pointsOnTarget"]
            m["none"] += not row["points"]
            m["inBox"] += bool(row["pointInBox"])
            m["ms"].append(row["molmoMs"])
        s["n"] += 1
        s["correct"] += row["correct"]
        s["outlineHits"] += bool(row.get("outlineHit"))
        s["byMark"] += row["used"] in ("mark", "mask", "molmo")
        s["errors"] += bool(row["error"])
        if not row["error"]:
            s["latencies"].append(row["latencyMs"])
    table = {}
    for key, s in summary.items():
        lat = s.pop("latencies")
        if "molmo" in s:
            ms = s["molmo"].pop("ms")
            s["molmo"].update(p50Ms=int(np.percentile(ms, 50)), p95Ms=int(np.percentile(ms, 95)))
        table[key] = {
            **s,
            "accuracy": round(s["correct"] / s["n"], 2) if s["n"] else None,
            "p50Ms": int(np.percentile(lat, 50)) if lat else None,
            "p95Ms": int(np.percentile(lat, 95)) if lat else None,
            "outlineAccuracy": round(s["outlineHits"] / s["n"], 2) if s["n"] and args.outline else None,
        }
    print(json.dumps(table, indent=2))
    Path(args.out).write_text(json.dumps({"when": time.strftime("%Y-%m-%d %H:%M"), "summary": table, "rows": rows}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
