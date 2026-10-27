"""SAM 3.1 visual-exemplar prompting: give one example box, find every similar instance."""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np
import torch

from orchestrator.keys import load_keys

BENCH = Path(__file__).resolve().parent
from orchestrator.assets import store  # noqa: E402  (the assets store: assets/README.md)
FRAMES = store().bench("frames")  # regenerated from the real recordings: python -m bench.extract_frames
OUT = BENCH / "results" / "sam31"


def main() -> None:
    load_keys()
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    from huggingface_hub import hf_hub_download
    from sam3.model_builder import build_sam3_predictor

    ckpt = hf_hub_download("facebook/sam3.1", "sam3.1_multiplex.pt", token=os.environ.get("HF_TOKEN"))
    predictor = build_sam3_predictor(checkpoint_path=ckpt, version="sam3.1", use_fa3=False, async_loading_frames=False)
    predictor.model.to("mps")
    image = cv2.imread(str(FRAMES / "leaf-132922-090s.jpg"))
    h, w = image.shape[:2]
    stoma = (736, 424, 40, 64)  # one stoma, x y w h pixels
    box = [[stoma[0] / w, stoma[1] / h, stoma[2] / w, stoma[3] / h]]
    cases = [("exemplar only", dict(bounding_boxes=box, bounding_box_labels=[1])),
             ("exemplar + 'stoma'", dict(text="stoma", bounding_boxes=box, bounding_box_labels=[1]))]
    tiles, report = [], []
    with tempfile.TemporaryDirectory() as tmp:
        cv2.imwrite(str(Path(tmp) / "00000.jpg"), image)
        for name, prompt in cases:
            session = predictor.handle_request({"type": "start_session", "resource_path": tmp})["session_id"]
            t0 = time.perf_counter()
            out = predictor.handle_request({"type": "add_prompt", "session_id": session, "frame_index": 0, **prompt})["outputs"]
            ms = (time.perf_counter() - t0) * 1000
            predictor.handle_request({"type": "close_session", "session_id": session})
            masks = np.asarray(out.get("out_binary_masks", []))
            probs = [round(float(p), 2) for p in np.asarray(out.get("out_probs", []))]
            report.append({"case": name, "objects": int(len(masks)), "probs": probs, "ms": round(ms)})
            print(f"{name:22s} {len(masks):3d} objects {ms:6.0f} ms {probs[:10]}", flush=True)
            vis = image.copy()
            cv2.rectangle(vis, (stoma[0], stoma[1]), (stoma[0] + stoma[2], stoma[1] + stoma[3]), (80, 220, 255), 2)
            for m in masks:
                contours, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                cv2.drawContours(vis, contours, -1, (163, 79, 255), 2)
            cv2.putText(vis, f"{name}: {len(masks)}", (300, 40), 0, 0.9, (255, 255, 255), 2)
            tiles.append(cv2.resize(vis[:, 280:1120], (560, 480)))
    cv2.imwrite(str(OUT / "exemplar.jpg"), np.hstack(tiles))
    (OUT / "exemplar.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    with torch.inference_mode():
        main()
