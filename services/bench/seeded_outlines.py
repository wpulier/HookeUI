"""Does SAM 3.1 outline the whole object from a click? Fixed seed box vs EdgeTAM seed box.

For each hand-labelled shape in questions.json (stomata and other ovals), click its centre
and outline it with:
  edgetam               EdgeTAM from the click (the studio's default)
  sam3.1-fal            SAM 3.1 on fal from a fixed 32 px box around the click
  edgetam>sam3.1-fal    SAM 3.1 on fal from EdgeTAM's mask box, padded 10%
  (--local adds the same two SAM 3.1 variants on this Mac)
and report mask area and box size relative to EdgeTAM, plus a contact sheet.

  python -m bench.seeded_outlines [--local]
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import cv2
import numpy as np

from gpu_worker.worker import Models
from orchestrator import fal_segment
from orchestrator.keys import load_keys
from orchestrator.tools import SEED_PAD, padded_bbox

HERE = Path(__file__).resolve().parent
from orchestrator.assets import store  # noqa: E402  (the assets store: assets/README.md)
FRAMES = store().bench("frames")  # regenerated from the real recordings: python -m bench.extract_frames
OUT = HERE / "results" / "seeded"
COLORS = {"edgetam": (255, 180, 40), "sam3.1-fal": (90, 90, 255), "edgetam>sam3.1-fal": (255, 64, 255),
          "sam3.1-local": (60, 160, 255), "edgetam>sam3.1-local": (80, 255, 120)}


def poly_mask(polygons, shape) -> np.ndarray:
    mask = np.zeros(shape[:2], np.uint8)
    for poly in polygons:
        cv2.fillPoly(mask, [np.round(np.array(poly)).astype(np.int32)], 1)
    return mask.astype(bool)


def stats(mask: np.ndarray, ref: np.ndarray | None) -> dict:
    area = int(mask.sum())
    out = {"area": area}
    if ref is not None and ref.sum():
        out["areaVsEdgetam"] = round(area / int(ref.sum()), 2)
        inter, union = int((mask & ref).sum()), int((mask | ref).sum())
        out["iouVsEdgetam"] = round(inter / union, 2) if union else 0.0
    return out


async def fal_box(jpeg: bytes, box, w: int, h: int) -> tuple[np.ndarray | None, float]:
    out = await fal_segment.segment_box(jpeg, box, w, h)
    return out["mask"], out["ms"]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true", help="also run SAM 3.1 on this Mac")
    args = parser.parse_args()
    load_keys()
    OUT.mkdir(parents=True, exist_ok=True)
    questions = [q for q in json.loads((HERE / "questions.json").read_text())["questions"] if q["kind"] == "shape"]
    models = Models()
    rows, tiles = [], []
    for q in questions:
        image = cv2.imread(str(FRAMES / q["frame"]))
        h, w = image.shape[:2]
        jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
        x, y = q["targets"][0]
        base = {"frame_id": hash(q["frame"]) & 0xFFFF, "jpeg": jpeg}
        seed = models.segment({**base, "points": [[x, y]], "model": "edgetam"})
        masks = {"edgetam": (poly_mask(seed["polygons"], image.shape), seed["ms"])}
        fixed = (x - 16, y - 16, x + 16, y + 16)
        seeded = padded_bbox(seed["polygons"], SEED_PAD) if seed["polygons"] else fixed
        m, ms = await fal_box(jpeg, fixed, w, h)
        masks["sam3.1-fal"] = (m if m is not None else np.zeros((h, w), bool), ms)
        m, ms = await fal_box(jpeg, seeded, w, h)
        masks["edgetam>sam3.1-fal"] = (m if m is not None else np.zeros((h, w), bool), ms + seed["ms"])
        if args.local:
            for name, box in (("sam3.1-local", fixed), ("edgetam>sam3.1-local", seeded)):
                r = models.segment({**base, "box": list(box), "model": "sam3.1-local"})
                masks[name] = (poly_mask(r["polygons"], image.shape), r["ms"] + (seed["ms"] if ">" in name else 0))
        ref = masks["edgetam"][0]
        row = {"frame": q["frame"], "question": q["question"], "click": [x, y]}
        for name, (mask, ms) in masks.items():
            row[name] = {**stats(mask, ref if name != "edgetam" else None), "ms": round(ms)}
        rows.append(row)
        print(json.dumps(row), flush=True)

        # Contact sheet tile: 240 px crop around the click, every outline drawn.
        tile = image.copy()
        for name, (mask, _ms) in masks.items():
            contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(tile, contours, -1, COLORS[name], 2)
        x0, y0 = int(min(max(x - 120, 0), w - 240)), int(min(max(y - 120, 0), h - 240))
        tiles.append(cv2.resize(tile[y0 : y0 + 240, x0 : x0 + 240], (360, 360)))
    sheet = np.hstack(tiles) if tiles else np.zeros((10, 10, 3), np.uint8)
    legend = np.zeros((40, sheet.shape[1], 3), np.uint8)
    lx = 10
    for name in masks:
        cv2.putText(legend, name, (lx, 27), 0, 0.6, COLORS[name], 2)
        lx += 230
    cv2.imwrite(str(OUT / "seeded-outlines.jpg"), np.vstack([legend, sheet]))

    summary = {}
    for name in rows[0]:
        if isinstance(rows[0][name], dict):
            summary[name] = {
                "medianAreaVsEdgetam": float(np.median([r[name].get("areaVsEdgetam", 1.0) for r in rows])),
                "medianIouVsEdgetam": float(np.median([r[name].get("iouVsEdgetam", 1.0) for r in rows])),
                "medianMs": float(np.median([r[name]["ms"] for r in rows])),
            }
    print(json.dumps(summary, indent=2))
    (OUT / "seeded-outlines.json").write_text(json.dumps({"rows": rows, "summary": summary}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
