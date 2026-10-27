"""Spike S4: can an image model annotate a microscope crop without changing the specimen?

A researcher's "annotated image" deliverable labels parts on the held crop. If the model
redraws the specimen (sharper, cleaner, invented detail) the picture stops being evidence,
so fidelity matters more than looks.

For four hand-labelled shapes in questions.json (stomata and ovals) a 256 px native crop
around the target is upscaled (cubic) to 1024x1024 and sent to each model with the same
instruction: add thin arrows and short labels for the visible parts, change nothing else.

  gpt-image-2.5-flare      OpenAI images.edit, quality medium
  gpt-image-2.5-sunburst   OpenAI images.edit, quality medium
  gemini-3.1-flash-image   Gemini generate_content with the image and instruction

Fidelity, on the output resized to 1024x1024 against the input (grayscale):
  unchanged   share of pixels within 20 grey levels of the input
  redrawn     share of pixels that changed by 20+ levels but are not annotation-coloured
              (low saturation, not near-white text): the specimen itself was altered
  ssim        structural similarity of the two images downscaled to 256 px
A deterministic baseline (our own outlines and labels drawn on the crop) is faithful by
construction and is the fallback.

  cd services && .venv/bin/python -m bench.annotate_spike [--limit N] [--models a,b]

Writes results/annotate/annotate.json and the images (git-ignored).
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import time
from pathlib import Path

import cv2
import numpy as np

from orchestrator.keys import load_keys

HERE = Path(__file__).resolve().parent
from orchestrator.assets import store  # noqa: E402  (the assets store: assets/README.md)
FRAMES = store().bench("frames")  # regenerated from the real recordings: python -m bench.extract_frames
OUT = HERE / "results" / "annotate"
MODELS = ["gpt-image-2.5-flare", "gpt-image-2.5-sunburst", "gemini-3.1-flash-image"]
SIDE = 1024
PROMPT = (
    "This is a photo through a light microscope of an unstained leaf epidermis, enlarged. "
    "Annotate it for a student: add thin arrows and short text labels (a few words each) "
    "pointing at the parts you can actually see, such as guard cells, the pore and "
    "neighbouring epidermal cells. Put the labels near the edges where possible. "
    "Do not change the photo itself in any way: do not redraw, sharpen, denoise, recolour, "
    "crop or add any detail to the specimen. Only add the arrows and labels on top."
)


def crops(limit: int) -> list[tuple[str, np.ndarray]]:
    questions = json.loads((HERE / "questions.json").read_text())["questions"]
    out = []
    for q in questions:
        if q["kind"] != "shape":
            continue
        image = cv2.imread(str(FRAMES / q["frame"]))
        x, y = q["targets"][0]
        x0, y0 = int(np.clip(x - 128, 0, image.shape[1] - 256)), int(np.clip(y - 128, 0, image.shape[0] - 256))
        crop = cv2.resize(image[y0 : y0 + 256, x0 : x0 + 256], (SIDE, SIDE), interpolation=cv2.INTER_CUBIC)
        out.append((f"{Path(q['frame']).stem}-{x}-{y}", crop))
        if len(out) >= limit:
            break
    return out


def openai_edit(model: str, image: np.ndarray) -> tuple[np.ndarray, dict]:
    import openai

    ok, png = cv2.imencode(".png", image)
    buf = io.BytesIO(png.tobytes())
    buf.name = "crop.png"
    result = openai.OpenAI().images.edit(model=model, image=buf, prompt=PROMPT, size="1024x1024", quality="medium")
    data = base64.b64decode(result.data[0].b64_json)
    usage = result.usage.model_dump() if getattr(result, "usage", None) else {}
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR), usage


def gemini_edit(model: str, image: np.ndarray) -> tuple[np.ndarray, dict]:
    from google import genai
    from google.genai import types

    ok, png = cv2.imencode(".png", image)
    client = genai.Client()  # keep a reference: a temporary client closes mid-call
    response = client.models.generate_content(
        model=model, contents=[types.Part.from_bytes(data=png.tobytes(), mime_type="image/png"), PROMPT])
    for part in response.candidates[0].content.parts:
        if part.inline_data is not None:
            usage = response.usage_metadata.model_dump(exclude_none=True) if response.usage_metadata else {}
            return cv2.imdecode(np.frombuffer(part.inline_data.data, np.uint8), cv2.IMREAD_COLOR), usage
    raise RuntimeError("no image in the reply: " + (response.text or "")[:200])


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a.astype(np.float64), b.astype(np.float64)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    blur = lambda x: cv2.GaussianBlur(x, (11, 11), 1.5)  # noqa: E731
    ma, mb = blur(a), blur(b)
    va, vb, cov = blur(a * a) - ma * ma, blur(b * b) - mb * mb, blur(a * b) - ma * mb
    return float((((2 * ma * mb + c1) * (2 * cov + c2)) / ((ma * ma + mb * mb + c1) * (va + vb + c2))).mean())


def fidelity(before: np.ndarray, after: np.ndarray) -> dict:
    after = cv2.resize(after, (before.shape[1], before.shape[0]), interpolation=cv2.INTER_AREA)
    g0, g1 = cv2.cvtColor(before, cv2.COLOR_BGR2GRAY), cv2.cvtColor(after, cv2.COLOR_BGR2GRAY)
    changed = np.abs(g0.astype(int) - g1.astype(int)) >= 20
    hsv = cv2.cvtColor(after, cv2.COLOR_BGR2HSV)
    annotation = (hsv[..., 1] > 80) | (hsv[..., 2] > 235) | (hsv[..., 2] < 25)
    small = lambda g: cv2.resize(g, (256, 256), interpolation=cv2.INTER_AREA)  # noqa: E731
    return {"unchanged": round(float(1 - changed.mean()), 3),
            "redrawn": round(float((changed & ~annotation).mean()), 3),
            "ssim": round(ssim(small(g0), small(g1)), 3)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument("--models", default=",".join(MODELS))
    args = parser.parse_args()
    load_keys()
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, crop in crops(args.limit):
        cv2.imwrite(str(OUT / f"{name}-input.jpg"), crop)
        for model in args.models.split(","):
            t0 = time.perf_counter()
            try:
                out, usage = (gemini_edit if model.startswith("gemini") else openai_edit)(model, crop)
                row = {"crop": name, "model": model, "s": round(time.perf_counter() - t0, 1), "usage": usage, **fidelity(crop, out)}
                cv2.imwrite(str(OUT / f"{name}-{model}.jpg"), out)
            except Exception as exc:
                row = {"crop": name, "model": model, "s": round(time.perf_counter() - t0, 1), "error": f"{type(exc).__name__}: {exc}"[:300]}
            print(json.dumps(row), flush=True)
            rows.append(row)
    (OUT / "annotate.json").write_text(json.dumps(rows, indent=2))
    print("\nmodel                     n  s(med)  unchanged  redrawn  ssim")
    for model in args.models.split(","):
        ok = [r for r in rows if r["model"] == model and "error" not in r]
        if ok:
            med = lambda k: float(np.median([r[k] for r in ok]))  # noqa: E731
            print(f"{model:24} {len(ok):2}  {med('s'):6.1f}  {med('unchanged'):9.3f}  {med('redrawn'):7.3f}  {med('ssim'):.3f}")


if __name__ == "__main__":
    main()
