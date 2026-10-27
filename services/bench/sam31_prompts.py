"""Which text prompts does SAM 3.1 respond to on microscope footage? One model load, many
prompts, detection on a single frame each (no tracking)."""
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

from orchestrator.assets import store  # noqa: E402  (the assets store: assets/README.md)

FRAMES = store().bench("frames")
OUT = Path(__file__).resolve().parent / "results" / "sam31"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frame", default=str(FRAMES / "leaf-132922-090s.jpg"))
    parser.add_argument("--prompts", default="stoma,stomata,oval,pore,guard cell,cell,plant cell,dark spot,dust,bubble,ring")
    args = parser.parse_args()
    load_keys()
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    from huggingface_hub import hf_hub_download
    from sam3.model_builder import build_sam3_predictor

    ckpt = hf_hub_download("facebook/sam3.1", "sam3.1_multiplex.pt", token=os.environ.get("HF_TOKEN"))
    predictor = build_sam3_predictor(checkpoint_path=ckpt, version="sam3.1", use_fa3=False, async_loading_frames=False)
    predictor.model.to("mps")
    image = cv2.imread(args.frame)
    results = []
    tiles = []
    with tempfile.TemporaryDirectory() as tmp:
        cv2.imwrite(str(Path(tmp) / "00000.jpg"), image)
        for prompt in [p.strip() for p in args.prompts.split(",") if p.strip()]:
            session = predictor.handle_request({"type": "start_session", "resource_path": tmp})["session_id"]
            t0 = time.perf_counter()
            out = predictor.handle_request({"type": "add_prompt", "session_id": session, "frame_index": 0, "text": prompt})["outputs"]
            ms = (time.perf_counter() - t0) * 1000
            predictor.handle_request({"type": "close_session", "session_id": session})
            masks = np.asarray(out.get("out_binary_masks", []))
            probs = [round(float(p), 2) for p in np.asarray(out.get("out_probs", []))]
            results.append({"prompt": prompt, "objects": int(len(masks)), "probs": probs, "ms": round(ms)})
            print(f"{prompt:12s} {len(masks):3d} objects  {ms:6.0f} ms  {probs[:8]}", flush=True)
            if len(masks):
                vis = image.copy()
                for m in masks:
                    contours, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    cv2.drawContours(vis, contours, -1, (163, 79, 255), 2)
                cv2.putText(vis, f"'{prompt}': {len(masks)}", (300, 40), 0, 1.0, (255, 255, 255), 2)
                tiles.append(cv2.resize(vis[:, 280:1120], (420, 360)))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "prompts.json").write_text(json.dumps({"frame": Path(args.frame).name, "results": results}, indent=2))
    if tiles:
        while len(tiles) % 3:
            tiles.append(np.zeros_like(tiles[0]))
        rows = [np.hstack(tiles[i : i + 3]) for i in range(0, len(tiles), 3)]
        cv2.imwrite(str(OUT / "prompts.jpg"), np.vstack(rows))


if __name__ == "__main__":
    with torch.inference_mode():
        main()
