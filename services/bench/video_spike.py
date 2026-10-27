"""Spike S6: can Veo 3.1 Fast turn a microscope photo into a short explanatory clip?

One held crop of a stoma (questions.json, June 14 footage), padded to 16:9 on a dark
background (never stretched), animated image-to-video at 720p for 4 s and 8 s (audio cannot be switched off in the Developer API).
Measures access, time to the finished video, cost (list price $0.10/s at 720p) and whether
the first frame still matches the photo (the clip is an illustration, but it must start from
what the user saw).

  cd services && .venv/bin/python -m bench.video_spike [--seconds 4,8]

Writes results/video/*.mp4 (git-ignored) and results/video/video.json.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np

from orchestrator.keys import load_keys
from research_worker.fidelity import alteration

HERE = Path(__file__).resolve().parent
from orchestrator.assets import store  # noqa: E402  (the assets store: assets/README.md)
FRAMES = store().bench("frames")  # regenerated from the real recordings: python -m bench.extract_frames
OUT = HERE / "results" / "video"
PROMPT = ("A still camera looks through a light microscope at an unstained leaf epidermis. The oval stoma in the centre "
          "slowly opens as its two guard cells swell, like a gentle time-lapse. Keep the camera, focus, colours and every "
          "other cell exactly as in the photo; no text, no labels, no new objects.")


def padded_crop() -> bytes:
    q = next(q for q in json.loads((HERE / "questions.json").read_text())["questions"] if q["kind"] == "shape")
    image = cv2.imread(str(FRAMES / q["frame"]))
    x, y = q["targets"][0]
    crop = image[max(0, y - 120) : y + 120, max(0, x - 160) : x + 160]
    crop = cv2.resize(crop, None, fx=720 / crop.shape[0], fy=720 / crop.shape[0], interpolation=cv2.INTER_CUBIC)
    canvas = np.full((720, 1280, 3), 24, np.uint8)
    x0 = (1280 - crop.shape[1]) // 2
    canvas[:, x0 : x0 + crop.shape[1]] = crop[:, : 1280]
    return cv2.imencode(".png", canvas)[1].tobytes()


def first_frame(mp4: Path) -> bytes | None:
    cap = cv2.VideoCapture(str(mp4))
    ok, frame = cap.read()
    cap.release()
    return cv2.imencode(".png", frame)[1].tobytes() if ok else None


def run(seconds: int, image: bytes) -> dict:
    from google import genai
    from google.genai import types

    client = genai.Client()
    t0 = time.perf_counter()
    operation = client.models.generate_videos(
        model="veo-3.1-fast-generate-preview", prompt=PROMPT,
        image=types.Image(image_bytes=image, mime_type="image/png"),
        config=types.GenerateVideosConfig(duration_seconds=seconds, aspect_ratio="16:9", resolution="720p",
                                          number_of_videos=1))
    while not operation.done:
        time.sleep(3)
        operation = client.operations.get(operation)
    took = time.perf_counter() - t0
    if operation.error:
        return {"seconds": seconds, "s": round(took, 1), "error": str(operation.error)[:300]}
    video = operation.response.generated_videos[0].video
    data = client.files.download(file=video)
    path = OUT / f"stoma-{seconds}s.mp4"
    path.write_bytes(data)
    first = first_frame(path)
    score = alteration(image, first, (0, 0, 1280, 720)) if first else None
    return {"seconds": seconds, "s": round(took, 1), "bytes": len(data), "usd": round(0.10 * seconds, 2),
            "firstFrameAlteration": round(score, 1) if score is not None else None, "file": path.name}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", default="4,8")
    args = parser.parse_args()
    load_keys()
    OUT.mkdir(parents=True, exist_ok=True)
    image = padded_crop()
    (OUT / "input.png").write_bytes(image)
    rows = []
    for seconds in [int(s) for s in args.seconds.split(",")]:
        try:
            row = run(seconds, image)
        except Exception as exc:
            row = {"seconds": seconds, "error": f"{type(exc).__name__}: {exc}"[:400]}
        print(json.dumps(row), flush=True)
        rows.append(row)
    (OUT / "video.json").write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
