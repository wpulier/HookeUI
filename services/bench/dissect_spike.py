"""Spike S3: how should "dissect" find the sub-parts of an outlined object?

For each hand-labelled shape in questions.json (stomata and other ovals) this outlines the
object with EdgeTAM from a click (as the studio does), crops the frame to the mask's box
+15% at native resolution, and finds regions inside the object three ways:

  grid       8x8 point grid inside the object, one EdgeTAM embedding of the crop, one
             single-positive-point prompt per point (all 3 multimask outputs are
             candidates), score > 0.6, clip to the object, keep 1-60% of the object area,
             mask-IoU NMS at 0.7 (higher score wins)
  amg        transformers mask-generation pipeline, SAM 2.1 tiny, points_per_batch 64,
             library defaults otherwise (32x32 points, pred_iou 0.88, stability 0.95),
             then the same clip / area / NMS
  grid-relaxed  the same grid with score > 0.2 (sub-part masks on these blurry ovals score
             0.25-0.6, so the spec'd 0.6 keeps almost nothing), top 12 by score
  amg-loose  the same pipeline with 16x16 points, pred_iou 0.5, stability 0.7
  vlm-boxes  Gemini 3.8 Flash (thinking LOW, JSON) returns named boxes of the visible
             sub-parts; each box becomes an EdgeTAM box prompt; same clip / area filter

For grid and amg the regions are drawn as numbered contours and Gemini 3.8 Flash names
each number ({parts:[{n,name,confidence}], merge:[[n,..]], drop:[n]}).
The names are best guesses from the vision model; the region finders recognise nothing.

Images sent to Gemini are the native crop upscaled (cubic) to 512 px on the long side so
the numbers are legible; the region methods themselves see the native crop.

  cd services && .venv/bin/python -m bench.dissect_spike [--no-vlm] [--limit N]

Writes results/dissect/dissect.json and contact sheets (git-ignored images).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import cv2
import numpy as np

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

from gpu_worker.worker import Models  # noqa: E402
from orchestrator.keys import load_keys  # noqa: E402

HERE = Path(__file__).resolve().parent
from orchestrator.assets import store  # noqa: E402  (the assets store: assets/README.md)
FRAMES = store().bench("frames")  # regenerated from the real recordings: python -m bench.extract_frames
OUT = HERE / "results" / "dissect"
CROP_PAD = 0.15
VLM_SIDE = 512
TILE = 300
METHODS = ("grid", "grid-relaxed", "amg", "amg-loose", "vlm-boxes")
NAMED = ("grid", "grid-relaxed", "amg", "amg-loose")
MAX_REGIONS = 12
# Distinct BGR colours for numbered regions.
PALETTE = [(60, 60, 255), (255, 160, 40), (60, 220, 60), (255, 60, 255), (40, 220, 255), (255, 255, 60),
           (140, 80, 255), (0, 140, 255), (200, 120, 0), (180, 255, 120), (120, 0, 200), (255, 200, 200)]

BOX_PROMPT = """This is a crop from a light-microscope view of a leaf, seen through a phone on the eyepiece.
The object of interest fills the middle of the crop and is outlined in thin white.
List the visible sub-parts inside that object (for example, for a stoma: the pore and each
guard cell; for a cell: nucleus, chloroplasts, wall). Only parts you can actually see; do not
list the whole object itself. Give each part its own box.
Return JSON: {"object": "<best-guess name of the whole object>",
 "parts": [{"name": "<part name>", "box_2d": [ymin, xmin, ymax, xmax]}]}
with box coordinates normalised to 0-1000 over this image."""

NAME_PROMPT = """This is a crop from a light-microscope view of a leaf, seen through a phone on the eyepiece.
The object of interest fills the middle of the crop and is outlined in thin white. Coloured
numbered contours mark candidate regions inside it, found by an automatic segmenter (they may
be wrong, overlapping or meaningless).
For each number, name the part of the object that region covers (e.g. for a stoma: pore, guard
cell; for a cell: nucleus, chloroplast, cell wall). If a region is not a meaningful part
(background, a fragment, a lighting artefact), put its number in "drop". If several numbers
together cover one part, list them in "merge".
Return JSON: {"object": "<best-guess name of the whole object>",
 "parts": [{"n": <number>, "name": "<part>", "confidence": <0-1>}],
 "merge": [[<n>, <n>]], "drop": [<n>]}"""


# ---------------------------------------------------------------- geometry helpers

def poly_mask(polygons, shape) -> np.ndarray:
    mask = np.zeros(shape[:2], np.uint8)
    for poly in polygons:
        cv2.fillPoly(mask, [np.round(np.array(poly)).astype(np.int32)], 1)
    return mask.astype(bool)


def crop_box(mask: np.ndarray, pad: float = CROP_PAD) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask)
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    px, py = (x1 - x0) * pad, (y1 - y0) * pad
    h, w = mask.shape
    return (int(max(0, x0 - px)), int(max(0, y0 - py)), int(min(w, x1 + px)), int(min(h, y1 + py)))


def iou(a: np.ndarray, b: np.ndarray) -> float:
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / union) if union else 0.0


def refine(cands: list[tuple[np.ndarray, float]], obj: np.ndarray, lo=0.01, hi=0.60, nms=0.7,
           extra: list | None = None, cap: int = MAX_REGIONS) -> tuple[list[dict], dict]:
    """Clip to the object, keep lo..hi of its area, mask-IoU NMS (higher score wins)."""
    obj_area = obj.sum()
    counts = {"candidates": len(cands)}
    kept_area = []
    for i, (m, s) in enumerate(cands):
        clipped = m & obj
        frac = clipped.sum() / obj_area
        if lo <= frac <= hi:
            kept_area.append({"mask": clipped, "score": float(s), "frac": float(frac),
                              **({"name": extra[i]} if extra else {})})
    counts["afterArea"] = len(kept_area)
    kept: list[dict] = []
    for r in sorted(kept_area, key=lambda r: -r["score"]):
        if all(iou(r["mask"], k["mask"]) <= nms for k in kept):
            kept.append(r)
    counts["afterNms"] = len(kept)
    return kept[:cap], counts


def inside_point(mask: np.ndarray) -> tuple[int, int]:
    """A point well inside the region (distance-transform maximum) for its number."""
    dist = cv2.distanceTransform(np.pad(mask.astype(np.uint8), 1), cv2.DIST_L2, 3)[1:-1, 1:-1]
    y, x = np.unravel_index(int(dist.argmax()), dist.shape)
    return int(x), int(y)


# ---------------------------------------------------------------- region methods

class Dissector:
    def __init__(self) -> None:
        self.models = Models()
        self.torch = self.models.torch
        self._amg = None
        self._key = 10_000

    def fresh_embed(self, crop_rgb: np.ndarray):
        """Always a new embedding so every method's time includes it."""
        self._key += 1
        return self.models.embed("edgetam", self._key, crop_rgb)

    def object_mask(self, image_bgr: np.ndarray, x: int, y: int) -> tuple[np.ndarray, dict]:
        jpeg = cv2.imencode(".jpg", image_bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
        self._key += 1
        seed = self.models.segment({"frame_id": self._key, "jpeg": jpeg, "points": [[x, y]], "model": "edgetam"})
        return poly_mask(seed["polygons"], image_bgr.shape), seed

    def _decode(self, embeddings, size, **prompt):
        processor, model = self.models.point_model("edgetam")
        enc = processor(original_sizes=[list(size)], return_tensors="pt", **prompt).to(self.models.device)
        with self.torch.inference_mode():
            out = model(image_embeddings=embeddings, input_points=enc.get("input_points"),
                        input_labels=enc.get("input_labels"), input_boxes=enc.get("input_boxes"),
                        multimask_output=True)
        masks = processor.post_process_masks(out.pred_masks.cpu(), [list(size)])[0]  # [N, 3, h, w]
        scores = out.iou_scores[0].float().cpu().numpy()  # [N, 3]
        return masks.numpy().astype(bool), scores

    def grid(self, crop_rgb: np.ndarray, obj: np.ndarray, n: int = 8, min_score: float = 0.6) -> tuple[list[dict], dict]:
        t0 = time.perf_counter()
        ys, xs = np.nonzero(obj)
        x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        pts = []
        for j in range(n):
            for i in range(n):
                px, py = x0 + (i + 0.5) * (x1 - x0) / n, y0 + (j + 0.5) * (y1 - y0) / n
                if obj[int(py), int(px)]:
                    pts.append([float(px), float(py)])
        embeddings, _sizes = self.fresh_embed(crop_rgb)
        t_embed = time.perf_counter()
        masks, scores = self._decode(embeddings, obj.shape,
                                     input_points=[[[p] for p in pts]], input_labels=[[[1] for _ in pts]])
        cands = [(masks[i, k], scores[i, k]) for i in range(len(pts)) for k in range(masks.shape[1]) if scores[i, k] > min_score]
        regions, counts = refine(cands, obj)
        t1 = time.perf_counter()
        return regions, {**counts, "points": len(pts), "rawMasks": int(masks.shape[0] * masks.shape[1]),
                         "ms": round((t1 - t0) * 1000), "embedMs": round((t_embed - t0) * 1000)}

    def amg(self, crop_rgb: np.ndarray, obj: np.ndarray, loose: bool = False) -> tuple[list[dict], dict]:
        from PIL import Image

        if self._amg is None:
            from transformers import pipeline

            self._amg = pipeline("mask-generation", model="facebook/sam2.1-hiera-tiny", device="mps")
        kwargs = {"points_per_batch": 64}
        if loose:
            kwargs.update(points_per_crop=16, pred_iou_thresh=0.5, stability_score_thresh=0.7)
        t0 = time.perf_counter()
        out = self._amg(Image.fromarray(crop_rgb), **kwargs)
        cands = [(np.asarray(m).astype(bool), float(s)) for m, s in zip(out["masks"], out["scores"])]
        regions, counts = refine(cands, obj)
        return regions, {**counts, "ms": round((time.perf_counter() - t0) * 1000)}

    def boxes(self, crop_rgb: np.ndarray, obj: np.ndarray, parts: list[dict]) -> tuple[list[dict], dict]:
        h, w = obj.shape
        boxes, names = [], []
        for p in parts:
            b = p.get("box_2d") or []
            if len(b) != 4:
                continue
            ymin, xmin, ymax, xmax = (float(v) for v in b)
            boxes.append([xmin / 1000 * w, ymin / 1000 * h, xmax / 1000 * w, ymax / 1000 * h])
            names.append(str(p.get("name", "?")))
        t0 = time.perf_counter()
        if not boxes:
            return [], {"candidates": 0, "afterArea": 0, "afterNms": 0, "ms": 0, "boxes": []}
        embeddings, _sizes = self.fresh_embed(crop_rgb)
        masks, scores = self._decode(embeddings, obj.shape, input_boxes=[boxes])
        best = scores.argmax(axis=1)
        cands = [(masks[i, best[i]], scores[i, best[i]]) for i in range(len(boxes))]
        regions, counts = refine(cands, obj, extra=names)
        return regions, {**counts, "ms": round((time.perf_counter() - t0) * 1000),
                         "boxes": [[round(v, 1) for v in b] for b in boxes], "boxNames": names}


# ---------------------------------------------------------------- drawing

def upscale(img: np.ndarray, side: int) -> tuple[np.ndarray, float]:
    s = side / max(img.shape[:2])
    return cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC), s


def draw_object(view: np.ndarray, obj: np.ndarray, s: float) -> None:
    big = cv2.resize(obj.astype(np.uint8), (view.shape[1], view.shape[0]), interpolation=cv2.INTER_NEAREST)
    contours, _ = cv2.findContours(big, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    cv2.drawContours(view, contours, -1, (255, 255, 255), 1, cv2.LINE_AA)


def draw_regions(crop_bgr: np.ndarray, obj: np.ndarray, regions: list[dict], side: int,
                 fill: bool, thickness: int = 2) -> np.ndarray:
    view, s = upscale(crop_bgr, side)
    if fill:
        overlay = view.copy()
        for i, r in enumerate(regions):
            big = cv2.resize(r["mask"].astype(np.uint8), (view.shape[1], view.shape[0]), interpolation=cv2.INTER_NEAREST)
            overlay[big.astype(bool)] = PALETTE[i % len(PALETTE)]
        view = cv2.addWeighted(overlay, 0.35, view, 0.65, 0)
    draw_object(view, obj, s)
    for i, r in enumerate(regions):
        big = cv2.resize(r["mask"].astype(np.uint8), (view.shape[1], view.shape[0]), interpolation=cv2.INTER_NEAREST)
        contours, _ = cv2.findContours(big, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(view, contours, -1, PALETTE[i % len(PALETTE)], thickness, cv2.LINE_AA)
    for i, r in enumerate(regions):
        x, y = inside_point(cv2.resize(r["mask"].astype(np.uint8), (view.shape[1], view.shape[0]),
                                       interpolation=cv2.INTER_NEAREST))
        label = str(i + 1)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.rectangle(view, (x - tw // 2 - 3, y - th // 2 - 3), (x + tw // 2 + 3, y + th // 2 + 4), (0, 0, 0), -1)
        cv2.putText(view, label, (x - tw // 2, y + th // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.55, PALETTE[i % len(PALETTE)], 1, cv2.LINE_AA)
    return view


def tile(view: np.ndarray, caption: str) -> np.ndarray:
    s = TILE / max(view.shape[:2])
    small = cv2.resize(view, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    out = np.zeros((TILE + 26, TILE, 3), np.uint8)
    y0, x0 = (TILE - small.shape[0]) // 2, (TILE - small.shape[1]) // 2
    out[y0 : y0 + small.shape[0], x0 : x0 + small.shape[1]] = small
    cv2.putText(out, caption, (6, TILE + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (230, 230, 230), 1, cv2.LINE_AA)
    return out


def label_strip(text: str, width: int, height: int = 30) -> np.ndarray:
    strip = np.zeros((height, width, 3), np.uint8)
    cv2.putText(strip, text, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    return strip


def jpeg(img: np.ndarray) -> bytes:
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()


# ---------------------------------------------------------------- main

async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-vlm", action="store_true", help="skip every Gemini call")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    load_keys()
    OUT.mkdir(parents=True, exist_ok=True)
    vision = None
    if not args.no_vlm:
        from orchestrator.vision import GeminiVision

        vision = GeminiVision("gemini-3.8-flash", thinking_level="low")
    questions = [q for q in json.loads((HERE / "questions.json").read_text())["questions"] if q["kind"] == "shape"]
    if args.limit:
        questions = questions[: args.limit]
    d = Dissector()

    # Warm-up: load EdgeTAM and the AMG pipeline, run each once so timings exclude loading.
    warm = cv2.imread(str(FRAMES / questions[0]["frame"]))
    wobj, _ = d.object_mask(warm, *questions[0]["targets"][0])
    x0, y0, x1, y1 = crop_box(wobj)
    wc = cv2.cvtColor(warm[y0:y1, x0:x1], cv2.COLOR_BGR2RGB)
    d.grid(wc, wobj[y0:y1, x0:x1])
    d.amg(wc, wobj[y0:y1, x0:x1], loose=True)

    rows = []
    tiles: dict[str, list[np.ndarray]] = {m: [] for m in METHODS}
    named_tiles: dict[str, list[np.ndarray]] = {m: [] for m in NAMED}
    for qi, q in enumerate(questions, start=1):
        image = cv2.imread(str(FRAMES / q["frame"]))
        x, y = q["targets"][0]
        obj_full, seed = d.object_mask(image, x, y)
        if not obj_full.any():
            print(json.dumps({"target": qi, "frame": q["frame"], "error": "EdgeTAM found no object"}), flush=True)
            continue
        cx0, cy0, cx1, cy1 = crop_box(obj_full)
        crop_bgr = image[cy0:cy1, cx0:cx1].copy()
        crop_rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        obj = obj_full[cy0:cy1, cx0:cx1]
        row = {"target": qi, "frame": q["frame"], "question": q["question"], "click": [x, y],
               "crop": [cx0, cy0, cx1 - cx0, cy1 - cy0], "objectArea": int(obj.sum()),
               "objectMs": seed["ms"], "methods": {}}
        plain_vlm = None
        if vision:
            plain_vlm, _ = upscale(crop_bgr, VLM_SIDE)
            draw_object(plain_vlm, obj, 0)

        found: dict[str, list[dict]] = {}
        found["grid"], info = d.grid(crop_rgb, obj)
        row["methods"]["grid"] = info
        found["grid-relaxed"], info = d.grid(crop_rgb, obj, min_score=0.2)
        row["methods"]["grid-relaxed"] = info
        found["amg"], info = d.amg(crop_rgb, obj)
        row["methods"]["amg"] = info
        found["amg-loose"], info = d.amg(crop_rgb, obj, loose=True)
        row["methods"]["amg-loose"] = info
        if vision:
            res = await vision.ask([jpeg(plain_vlm)], BOX_PROMPT)
            parts = (res.data or {}).get("parts") or []
            found["vlm-boxes"], info = d.boxes(crop_rgb, obj, parts)
            row["methods"]["vlm-boxes"] = {**info, "vlmMs": round(res.latency_ms), "totalMs": round(res.latency_ms) + info["ms"],
                                           "object": (res.data or {}).get("object"), "error": res.error or None,
                                           "kept": [r["name"] for r in found["vlm-boxes"]]}
        else:
            found["vlm-boxes"] = []

        for m in METHODS:
            if m not in row["methods"]:
                continue
            info = row["methods"][m]
            ms = info.get("totalMs", info["ms"])
            view = draw_regions(crop_bgr, obj, found[m], 600, fill=True)
            tiles[m].append(tile(view, f"#{qi} {m}: {len(found[m])} reg, {ms} ms"))
            info["regions"] = [{"n": i + 1, "frac": round(r["frac"], 3), "score": round(r["score"], 2),
                                **({"name": r["name"]} if "name" in r else {})} for i, r in enumerate(found[m])]

        if vision:
            for m in NAMED:
                if not found[m]:
                    row["methods"][m]["naming"] = {"skipped": "no regions"}
                    continue
                marked = draw_regions(crop_bgr, obj, found[m], VLM_SIDE, fill=False)
                cv2.imwrite(str(OUT / f"t{qi}-{m}-marked.jpg"), marked)
                res = await vision.ask([jpeg(marked)], NAME_PROMPT)
                row["methods"][m]["naming"] = {"ms": round(res.latency_ms), "result": res.data, "error": res.error or None,
                                               "usage": res.usage}
                names = {int(p.get("n", -1)): p.get("name") for p in ((res.data or {}).get("parts") or []) if isinstance(p, dict)}
                dropped = set((res.data or {}).get("drop") or [])
                cap = "; ".join(f"{n}:{'DROP' if n in dropped else names.get(n, '?')}" for n in range(1, len(found[m]) + 1))
                named_tiles[m].append(np.vstack([tile(marked, f"#{qi} {m} named in {round(res.latency_ms)} ms"),
                                                 label_strip(cap[:48], TILE, 22), label_strip(cap[48:96], TILE, 22)]))
        rows.append(row)
        print(json.dumps({k: v for k, v in row.items() if k != "methods"} | {
            m: {k: v for k, v in row["methods"][m].items() if k in ("afterNms", "ms", "totalMs", "kept")} |
               ({"names": [p.get("name") for p in ((row["methods"][m].get("naming") or {}).get("result") or {}).get("parts", [])]}
                if m in NAMED and vision else {})
            for m in row["methods"]}), flush=True)

    # Contact sheets: one per method (rows of 4 targets) and one overview (methods x targets).
    def grid_sheet(items: list[np.ndarray], per_row: int = 4) -> np.ndarray:
        blank = np.zeros_like(items[0])
        rows_ = [items[i : i + per_row] + [blank] * (per_row - len(items[i : i + per_row])) for i in range(0, len(items), per_row)]
        return np.vstack([np.hstack(r) for r in rows_])

    for m in METHODS:
        if tiles[m]:
            cv2.imwrite(str(OUT / f"sheet-{m}.jpg"), grid_sheet(tiles[m]))
    for m in NAMED:
        if named_tiles[m]:
            cv2.imwrite(str(OUT / f"sheet-{m}-named.jpg"), grid_sheet(named_tiles[m]))
    present = [m for m in METHODS if tiles[m]]
    overview = np.vstack([np.hstack(tiles[m]) for m in present])
    cv2.imwrite(str(OUT / "overview.jpg"), overview)

    def med(m, key):
        vals = [r["methods"][m].get(key) for r in rows if m in r["methods"] and r["methods"][m].get(key) is not None]
        return float(np.median(vals)) if vals else None

    summary = {}
    for m in present:
        summary[m] = {"medianRegions": med(m, "afterNms"), "medianMs": med(m, "totalMs" if m == "vlm-boxes" else "ms")}
        if m == "vlm-boxes":
            summary[m]["medianVlmMs"] = med(m, "vlmMs")
            summary[m]["medianSegMs"] = med(m, "ms")
        if m in NAMED and vision:
            lat = [r["methods"][m]["naming"]["ms"] for r in rows if "ms" in (r["methods"][m].get("naming") or {})]
            summary[m]["medianNamingMs"] = float(np.median(lat)) if lat else None
    print(json.dumps(summary, indent=2))
    (OUT / "dissect.json").write_text(json.dumps({"rows": rows, "summary": summary}, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
