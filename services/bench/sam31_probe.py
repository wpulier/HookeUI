"""Validate SAM 3.1 (facebook/sam3.1, multiplex video model) on this Mac.

Runs Meta's predictor on a short clip of the leaf recording with a text prompt, on MPS
(Apple GPU) if it works, else CPU, and reports load time, per-frame time and what it found.
The official code targets CUDA; services/vendor/sam3 carries a small patch (Triton
optional) — see services/vendor/README.md.

  python -m bench.sam31_probe --prompt stoma --frames 30 --device mps
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np
import torch

from orchestrator.keys import load_keys

REPO = Path(__file__).resolve().parents[2]
from orchestrator.assets import store  # noqa: E402  (the assets store: assets/README.md)
VIDEO = store().real_file("2026-06-14-leaf-epidermis-3m43s")  # assets/manifest.json
OUT = Path(__file__).resolve().parent / "results" / "sam31"


def extract(start: int, count: int, folder: Path, still: str | None = None) -> list[np.ndarray]:
    if still:  # a still image repeated, for sanity checks on everyday scenes
        img = cv2.imread(still)
        for i in range(count):
            cv2.imwrite(str(folder / f"{i:05d}.jpg"), img)
        return [img] * count
    cap = cv2.VideoCapture(str(VIDEO))
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    frames = []
    for i in range(count):
        ok, img = cap.read()
        if not ok:
            break
        cv2.imwrite(str(folder / f"{i:05d}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        frames.append(img)
    return frames


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default="stoma")
    parser.add_argument("--start", type=int, default=2650)
    parser.add_argument("--frames", type=int, default=30)
    parser.add_argument("--device", default="mps", choices=["mps", "cpu"])
    parser.add_argument("--still", default=None, help="use this image (repeated) instead of the leaf recording")
    args = parser.parse_args()
    load_keys()
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    OUT.mkdir(parents=True, exist_ok=True)

    from huggingface_hub import hf_hub_download
    from sam3.model_builder import build_sam3_predictor

    ckpt = hf_hub_download("facebook/sam3.1", "sam3.1_multiplex.pt", token=os.environ.get("HF_TOKEN"))
    report: dict = {"device": args.device, "prompt": args.prompt, "checkpoint": Path(ckpt).name}

    t0 = time.perf_counter()
    predictor = build_sam3_predictor(checkpoint_path=ckpt, version="sam3.1", use_fa3=False, compile=False,
                                     async_loading_frames=False)
    model = predictor.model
    if args.device == "mps":
        model.to("mps")
    report["loadSeconds"] = round(time.perf_counter() - t0, 1)
    report["parameters"] = f"{sum(p.numel() for p in model.parameters()) / 1e6:.0f}M"
    print("loaded", report, flush=True)

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        frames = extract(args.start, args.frames, folder, args.still)
        t0 = time.perf_counter()
        session = predictor.handle_request({"type": "start_session", "resource_path": str(folder)})["session_id"]
        report["startSessionSeconds"] = round(time.perf_counter() - t0, 2)

        t0 = time.perf_counter()
        first = predictor.handle_request({"type": "add_prompt", "session_id": session, "frame_index": 0, "text": args.prompt})
        report["promptFrame0Seconds"] = round(time.perf_counter() - t0, 2)
        outputs = first.get("outputs", {})
        report["objectsFrame0"] = int(len(outputs.get("out_obj_ids", [])))

        t0 = time.perf_counter()
        per_frame = {}
        for out in predictor.handle_stream_request({"type": "propagate_in_video", "session_id": session}):
            idx = out["frame_index"]
            o = out["outputs"]
            per_frame[idx] = (o.get("out_obj_ids"), o.get("out_binary_masks"))
        elapsed = time.perf_counter() - t0
        report["propagateSeconds"] = round(elapsed, 2)
        report["msPerFrame"] = round(elapsed / max(1, len(per_frame)) * 1000)
        report["framesTracked"] = len(per_frame)
        predictor.handle_request({"type": "close_session", "session_id": session})

    # Draw frame 0 and the last frame with masks for a visual check.
    tiles = []
    for idx in (0, max(per_frame) if per_frame else 0):
        img = frames[idx].copy()
        ids, masks = per_frame.get(idx, (None, None))
        count = 0
        if masks is not None:
            for m in np.asarray(masks):
                contours, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                cv2.drawContours(img, contours, -1, (163, 79, 255), 3)
                count += 1
        cv2.putText(img, f"frame {idx}: {count} '{args.prompt}'", (20, 40), 0, 1.0, (255, 255, 255), 2)
        tiles.append(cv2.resize(img, (640, 360)))
    cv2.imwrite(str(OUT / f"sam31-{args.device}-{args.prompt.replace(' ', '_')}.jpg"), np.hstack(tiles))
    report["objectsLastFrame"] = int(len(per_frame[max(per_frame)][0])) if per_frame else 0
    print(json.dumps(report, indent=2))
    (OUT / f"report-{args.device}.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    with torch.inference_mode():
        main()
