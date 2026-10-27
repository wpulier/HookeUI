"""Time and visualize local outline models on Apple Silicon (MPS).

Point-prompt models (the "click / what's that?" path):
  SAM 2.1 tiny, EdgeTAM: one image-embedding pass per frame, then a cheap decode per prompt.
Automatic cell model (the "highlight the cells / nuclei" path):
  Cellpose-SAM: one pass segments every cell in the frame.

Usage:
  python -m bench.local_segmenters --frame path.jpg --points "756,456;544,530;750,140"
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np
import torch

COLORS = [(255, 64, 255), (64, 220, 255), (80, 255, 120), (255, 180, 40), (255, 90, 90)]


def sync(device: torch.device) -> None:
    if device.type == "mps":
        torch.mps.synchronize()


def timed(fn, device, repeats=5):
    fn()  # warm-up (kernel compilation, caches)
    sync(device)
    times = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        out = fn()
        sync(device)
        times.append((time.perf_counter() - t0) * 1000)
    return out, round(float(np.median(times)), 1)


def run_point_model(name: str, model_id: str, image_rgb: np.ndarray, points, device, out_dir: Path) -> dict:
    from transformers import Sam2Model, Sam2Processor, EdgeTamModel

    model_cls = EdgeTamModel if "edgetam" in model_id.lower() else Sam2Model
    t0 = time.perf_counter()
    processor = Sam2Processor.from_pretrained(model_id)
    model = model_cls.from_pretrained(model_id).to(device).eval()
    load_s = round(time.perf_counter() - t0, 1)

    base = processor(images=image_rgb, return_tensors="pt").to(device)

    def embed():
        with torch.inference_mode():
            return model.get_image_embeddings(base["pixel_values"])

    embeddings, embed_ms = timed(embed, device)

    masks_out, decode_times = [], []
    for x, y in points:
        inputs = processor(
            images=image_rgb, input_points=[[[[x, y]]]], input_labels=[[[1]]], return_tensors="pt"
        ).to(device)

        def decode():
            with torch.inference_mode():
                return model(
                    image_embeddings=embeddings,
                    input_points=inputs["input_points"],
                    input_labels=inputs["input_labels"],
                    multimask_output=True,
                )

        outputs, ms = timed(decode, device)
        decode_times.append(ms)
        masks = processor.post_process_masks(outputs.pred_masks.cpu(), inputs["original_sizes"])[0][0]
        best = int(outputs.iou_scores[0, 0].argmax())
        masks_out.append(masks[best].numpy().astype(bool))

    overlay = image_rgb[:, :, ::-1].copy()
    for i, (mask, (x, y)) in enumerate(zip(masks_out, points)):
        color = COLORS[i % len(COLORS)]
        contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(overlay, contours, -1, color, 3)
        cv2.circle(overlay, (int(x), int(y)), 6, color, -1)
    cv2.imwrite(str(out_dir / f"points-{name}.jpg"), overlay)
    return {
        "model": model_id,
        "loadSeconds": load_s,
        "embedMs": embed_ms,
        "decodeMsPerPoint": round(float(np.median(decode_times)), 1),
        "maskAreasPx": [int(m.sum()) for m in masks_out],
    }


def run_cellpose(image_rgb: np.ndarray, device, out_dir: Path, scales=(1.0, 0.5)) -> dict:
    from cellpose import models

    t0 = time.perf_counter()
    model = models.CellposeModel(gpu=True, device=device)
    load_s = round(time.perf_counter() - t0, 1)
    results = {"model": "cellpose-sam (cpsam)", "loadSeconds": load_s, "runs": []}
    for scale in scales:
        img = cv2.resize(image_rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale != 1 else image_rgb

        def run():
            masks, _flows, _styles = model.eval(img, batch_size=8)
            return masks

        masks, ms = timed(run, device, repeats=2)
        n = int(masks.max())
        results["runs"].append({"scale": scale, "size": f"{img.shape[1]}x{img.shape[0]}", "ms": ms, "objects": n})
        overlay = img[:, :, ::-1].copy()
        rng = np.random.default_rng(1)
        for label in range(1, n + 1):
            m = (masks == label).astype(np.uint8)
            contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(overlay, contours, -1, tuple(int(c) for c in rng.integers(80, 255, 3)), 1)
        cv2.imwrite(str(out_dir / f"cellpose-{scale}.jpg"), overlay)
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frame", required=True)
    parser.add_argument("--points", default="", help='"x,y;x,y" in full-frame pixels')
    parser.add_argument("--models", default="sam2.1-tiny,edgetam,cellpose")
    parser.add_argument("--out", default="bench/results/local")
    args = parser.parse_args()

    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    image_rgb = cv2.cvtColor(cv2.imread(args.frame), cv2.COLOR_BGR2RGB)
    points = [tuple(float(v) for v in p.split(",")) for p in args.points.split(";") if p]

    report = {"device": str(device), "frame": Path(args.frame).name, "results": {}}
    wanted = args.models.split(",")
    if "sam2.1-tiny" in wanted:
        report["results"]["sam2.1-tiny"] = run_point_model("sam2.1-tiny", "facebook/sam2.1-hiera-tiny", image_rgb, points, device, out_dir)
    if "edgetam" in wanted:
        report["results"]["edgetam"] = run_point_model("edgetam", "yonigozlan/EdgeTAM-hf", image_rgb, points, device, out_dir)
    if "cellpose" in wanted:
        report["results"]["cellpose"] = run_cellpose(image_rgb, device, out_dir)
    print(json.dumps(report, indent=2))
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
