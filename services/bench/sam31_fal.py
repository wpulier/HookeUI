"""SAM 3.1 through fal (cloud GPU): same tests as the local probe, with round-trip timing."""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import fal_client

from orchestrator.keys import load_keys

BENCH = Path(__file__).resolve().parent
from orchestrator.assets import store  # noqa: E402  (the assets store: assets/README.md)
FRAMES = store().bench("frames")  # regenerated from the real recordings: python -m bench.extract_frames
ENDPOINT = "fal-ai/sam-3-1/image-rle"


def data_uri(path: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode()


def run(name: str, image: Path, **args) -> dict:
    t0 = time.perf_counter()
    try:
        result = fal_client.run(ENDPOINT, arguments={"image_url": data_uri(image), "include_scores": True,
                                                     "include_boxes": True, "return_multiple_masks": True,
                                                     "max_masks": 16, "apply_mask": False, **args})
    except Exception as exc:
        return {"case": name, "error": f"{type(exc).__name__}: {exc}"[:300]}
    ms = (time.perf_counter() - t0) * 1000
    rle = result.get("rle")
    count = len(rle) if isinstance(rle, list) else (1 if rle else 0)
    return {"case": name, "masks": count, "scores": [round(s, 2) for s in result.get("scores") or []][:10], "ms": round(ms)}


def main() -> None:
    load_keys()
    leaf = FRAMES / "leaf-132922-090s.jpg"
    room = BENCH.parents[1] / "experiments/iphone-camera-feed/output/opencv-index-1.jpg"
    cases = [
        ("room 'chair' (sanity)", room, {"prompt": "chair"}),
        ("leaf 'stoma'", leaf, {"prompt": "stoma"}),
        ("leaf 'cell'", leaf, {"prompt": "cell"}),
        ("leaf box on one stoma + 'stoma'", leaf, {"prompt": "stoma", "box_prompts": [{"x_min": 736, "y_min": 424, "x_max": 776, "y_max": 488}]}),
        ("leaf 'stoma' (repeat, warm)", leaf, {"prompt": "stoma"}),
    ]
    report = [run(name, image, **args) for name, image, args in cases]
    for r in report:
        print(r)
    (BENCH / "results" / "sam31" / "fal.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
