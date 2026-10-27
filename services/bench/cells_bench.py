"""The cell finders and click outlines on their own, against labelled truth (W7: micro-sam).

BCCD blood smears (MIT; `make corpus`), the same sampled pictures as bench/find_bench.py: every
white cell, red cell and platelet boxed by hand. No vision model is asked; this measures what the
segmenters themselves give the app.

  --finders  cell finders (option CELLS), each outlining every cell in the picture in one call:
             per class, how many labelled cells have an outline whose box matches theirs (IoU >= 0.5,
             one to one), how many outlines there were, and the time per picture (the first call,
             which loads the model, is left out). "micro-sam-lm:apg:rgb" runs micro-sam in another mode
             and input (microsam_server.py).
  --clicks   outline models (option OUTLINE), one click at the centre of each labelled cell (every
             white cell and platelet, up to 10 red cells a picture): how often the outline's box
             matches the cell's, and the time of a click (the first on each picture embeds it, as a
             click on a new live frame does).

  cd services && PYTHONPATH=. python -m bench.cells_bench [--n 12] [--finders cellpose-sam,micro-sam-lm]
                                                         [--clicks edgetam,micro-sam-lm]

Notes: BCCD does not box every red cell, so "outlines" includes real unlabelled cells. Pictures are
full slide photos (640 x 480), not phone-through-eyepiece views. Writes results/cells-bench.json and
a contact sheet (results/cells-bench.jpg: green BCCD boxes, magenta outlines, first finder per row).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np

from bench.find_bench import mask_box, match, pictures
from gpu_worker.worker import Models

HERE = Path(__file__).resolve().parent
CLASSES = ("WBC", "RBC", "Platelets")
RBC_CLICKS = 10  # red cells clicked per picture (every white cell and platelet is)


def finder_rows(models: Models, pics, finder: str) -> list[dict]:
    option, mode, picture = (finder.split(":") + ["", ""])[:3]
    rows = []
    for k, (path, truth) in enumerate(pics):
        image = cv2.imread(str(path))
        jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
        t0 = time.perf_counter()
        out = models.cells({"frame_id": k, "jpeg": jpeg, "model": option, **({"mode": mode} if mode else {}),
                             **({"input": picture} if picture else {})})
        ms = (time.perf_counter() - t0) * 1000
        boxes = [b for b in (mask_box([p]) for p in out["polygons"]) if b]
        row = {"finder": finder, "picture": path.name, "outlines": len(boxes), "ms": round(ms),
               "modelMs": out.get("modelMs", out.get("ms")), "boxes": [[round(v, 1) for v in b] for b in boxes]}
        for label in CLASSES:
            want = [t["box"] for t in truth if t["label"] == label]
            row[label] = {"truth": len(want), "hits": match(boxes, want)}
        row["allHits"] = match(boxes, [t["box"] for t in truth])
        row["allTruth"] = len(truth)
        rows.append(row)
        print(json.dumps({k2: v for k2, v in row.items() if k2 != "boxes"}), flush=True)
    return rows


def click_rows(models: Models, pics, model: str) -> list[dict]:
    rows = []
    for k, (path, truth) in enumerate(pics):
        image = cv2.imread(str(path))
        jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
        cells = [t for t in truth if t["label"] != "RBC"] + [t for t in truth if t["label"] == "RBC"][:RBC_CLICKS]
        for n, cell in enumerate(cells):
            x0, y0, x1, y1 = cell["box"]
            t0 = time.perf_counter()
            seg = models.segment({"frame_id": 5000 + k, "jpeg": jpeg, "points": [[(x0 + x1) / 2, (y0 + y1) / 2]], "model": model})
            ms = (time.perf_counter() - t0) * 1000
            box = mask_box(seg["polygons"])
            rows.append({"model": model, "picture": path.name, "label": cell["label"], "first": n == 0,
                         "hit": bool(box) and match([box], [cell["box"]]) == 1, "ms": round(ms, 1)})
    return rows


def summarise(finders: dict, clicks: dict) -> dict:
    summary = {}
    for finder, rows in finders.items():
        timed = [r["ms"] for r in rows[1:]] or [r["ms"] for r in rows]
        entry = {"outlines": sum(r["outlines"] for r in rows),
                 "recallAll": round(sum(r["allHits"] for r in rows) / max(1, sum(r["allTruth"] for r in rows)), 2),
                 "p50Ms": int(np.median(timed)), "p95Ms": int(np.percentile(timed, 95))}
        for label in CLASSES:
            t, h = sum(r[label]["truth"] for r in rows), sum(r[label]["hits"] for r in rows)
            entry[label] = f"{h}/{t} ({h / t:.2f})" if t else "0/0"
        summary[f"finder | {finder}"] = entry
    for model, rows in clicks.items():
        # The first click overall loads the model; leave it out of the times.
        timed = rows[1:] or rows
        entry = {"p50Ms": round(float(np.median([r["ms"] for r in timed])), 1),
                 "p95Ms": round(float(np.percentile([r["ms"] for r in timed], 95)), 1),
                 "newPictureP50Ms": round(float(np.median([r["ms"] for r in timed if r["first"]] or [0])), 1)}
        for label in CLASSES:
            rs = [r for r in rows if r["label"] == label]
            hits = sum(r["hit"] for r in rs)
            entry[label] = f"{hits}/{len(rs)} ({hits / len(rs):.2f})" if rs else "0/0"
        summary[f"click | {model}"] = entry
    return summary


def contact_sheet(pics, finders: dict) -> None:
    tiles = []
    for finder, rows in finders.items():
        row_tiles = []
        for (path, truth), row in list(zip(pics, rows))[:4]:
            tile = cv2.imread(str(path))
            for t in truth:
                cv2.rectangle(tile, tuple(map(int, t["box"][:2])), tuple(map(int, t["box"][2:])), (80, 255, 80), 1)
            for b in row["boxes"]:
                cv2.rectangle(tile, tuple(map(int, b[:2])), tuple(map(int, b[2:])), (255, 80, 255), 1)
            cv2.putText(tile, finder, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            row_tiles.append(cv2.resize(tile, (400, 300)))
        tiles.append(np.hstack(row_tiles))
    if tiles:
        cv2.imwrite(str(HERE / "results" / "cells-bench.jpg"), np.vstack(tiles))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=12)
    parser.add_argument("--finders", default="cellpose-sam,micro-sam-lm")
    parser.add_argument("--clicks", default="", help="outline models to click with, e.g. edgetam,micro-sam-lm")
    parser.add_argument("--out", default="cells-bench.json")
    args = parser.parse_args()
    models = Models()
    pics = pictures(args.n)
    finders = {f: finder_rows(models, pics, f) for f in args.finders.split(",") if f}
    clicks = {m: click_rows(models, pics, m) for m in args.clicks.split(",") if m}
    summary = summarise(finders, clicks)
    print(json.dumps(summary, indent=2))
    (HERE / "results" / args.out).write_text(json.dumps({"summary": summary, "finders": finders, "clicks": clicks}, indent=2))
    contact_sheet(pics, finders)


if __name__ == "__main__":
    main()
