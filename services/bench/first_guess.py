"""How often the instant first guess (pointing: snap-first) lands on the right thing.

For each hand-labelled question in questions.json, detect candidate spots on the frame the
way the orchestrator does, pick the spot that best fits the question's words
(orchestrator/snap.py), and count it right if it is within the question's tolerance of a
labelled target. No model calls.

  python -m bench.first_guess
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
import numpy as np

from orchestrator.candidates import propose
from orchestrator.fov import detect_fov
from orchestrator.snap import cues, pick_spot

HERE = Path(__file__).resolve().parent
from orchestrator.assets import store  # noqa: E402  (the assets store: assets/README.md)
FRAMES = store().bench("frames")  # regenerated from the real recordings: python -m bench.extract_frames


def main() -> None:
    questions = json.loads((HERE / "questions.json").read_text())["questions"]
    rows = []
    for q in questions:
        gray = cv2.imread(str(FRAMES / q["frame"]), cv2.IMREAD_GRAYSCALE)
        fov = detect_fov(gray)
        candidates = propose(gray, fov)
        t0 = time.perf_counter()
        cand = pick_spot(q["question"], candidates, fov)
        ms = (time.perf_counter() - t0) * 1000
        if cand is None:
            verdict, dist = "no guess", None
        else:
            dist = min(float(np.hypot(cand.x - tx, cand.y - ty)) for tx, ty in q["targets"])
            verdict = "right" if dist <= q["tolerance"] else "wrong"
        rows.append({"question": q["question"], "kind": q["kind"], "cues": sorted(cues(q["question"])), "verdict": verdict,
                     "distance": None if dist is None else round(dist), "ms": round(ms, 2)})
        print(f"{verdict:<9} {q['kind']:<6} {q['question']}" + ("" if dist is None else f"  ({dist:.0f} px)"))
    for kind in ("spot", "shape"):
        sub = [r for r in rows if r["kind"] == kind]
        right = sum(r["verdict"] == "right" for r in sub)
        guessed = sum(r["verdict"] != "no guess" for r in sub)
        print(f"{kind}: {right}/{len(sub)} right, {guessed - right} wrong, {len(sub) - guessed} no guess")
    print(f"pick time: max {max(r['ms'] for r in rows):.2f} ms")
    out = HERE / "results" / "first-guess.json"
    out.write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
