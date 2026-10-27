"""Whether a Cellpose model file someone added can be offered (P5 of docs/design/2026-10-12-the-open-catalog.md; ADR
0021): run by orchestrator/mine.py in a process of its own when the user turns it on, never in the app or its workers.

    python -m noncommercial.cellpose_file_check WEIGHTS FRAME.jpg

Three steps, each said, the first that fails ending it:
1. **It holds only weights:** `torch.load(weights_only=True)`. A file that needs code to load (pickled objects) is
   refused without running any of it.
2. **This Cellpose builds a model from it:** `models.CellposeModel(pretrained_model=...)`.
3. **It finds cells:** on the supplied check image, it returns instance masks of a sane count and size.

Prints one JSON object: {"ok", "steps": [{"says", "ok"}], "cells", "medianArea", "ms"}. Exit 0 either way; the
caller reads the verdict.
"""
from __future__ import annotations

import json
import sys
import time

MAX_CELLS = 20_000  # more than this on one view is noise, not cells


def check(weights: str, frame: str) -> dict:
    steps: list[dict] = []
    out = {"ok": False, "steps": steps}
    t0 = time.perf_counter()
    try:
        import torch

        torch.load(weights, map_location="cpu", weights_only=True)
        steps.append({"says": "It holds only weights (nothing that runs code to load)", "ok": True})
    except Exception as exc:  # noqa: BLE001 - said in plain words, never run
        steps.append({"says": "It needs code to load, not just weights, so it was refused without running it "
                              f"({type(exc).__name__})", "ok": False})
        return out
    try:
        from noncommercial.cellpose_cells import checked_model

        device = torch.device("mps") if torch.backends.mps.is_available() else torch.device("cpu")
        # Checked where Cellpose-SAM's answers are proven (cellpose_device.py), as it will run: a GPU that computes wrongly
        # would fail a good file or pass a bad one.
        model, proof = checked_model(device, weights)
        out["device"] = {k: v for k, v in proof.items() if k != "tried"}
        steps.append({"says": "This Cellpose builds a model from it", "ok": True})
    except Exception as exc:  # noqa: BLE001
        steps.append({"says": f"Not a Cellpose model this version can load: {str(exc)[:200] or type(exc).__name__}", "ok": False})
        return out
    try:
        import numpy as np

        from noncommercial.cellpose_cells import CELLPROB_THRESHOLD, FLOW_THRESHOLD, picture

        # Exactly what the app gives a cell finder (cellpose_cells.picture: the frame's green channel), so a model that
        # passes here is checked on the input it will be given.
        with open(frame, "rb") as handle:
            channel = picture(handle.read())
        masks, _flows, _styles = model.eval(channel, flow_threshold=FLOW_THRESHOLD, cellprob_threshold=CELLPROB_THRESHOLD,
                                            batch_size=1)
        labels = np.asarray(masks)
        ids, areas = np.unique(labels[labels > 0], return_counts=True)
        cells = int(len(ids))
        out["cells"] = cells
        out["medianArea"] = float(np.median(areas)) if cells else 0.0
        sane = 1 <= cells <= MAX_CELLS and (not cells or out["medianArea"] >= 4)
        steps.append({"says": f"It found {cells} cells on the supplied check image" if sane else
                      f"It found {cells} cells on the supplied check image: not a working cell finder", "ok": sane})
        out["ok"] = sane
    except Exception as exc:  # noqa: BLE001
        steps.append({"says": f"It failed on the supplied check image: {str(exc)[:200] or type(exc).__name__}", "ok": False})
    finally:
        out["ms"] = round((time.perf_counter() - t0) * 1000)
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(json.dumps({"ok": False, "steps": [{"says": "usage: cellpose_file_check WEIGHTS FRAME", "ok": False}]}))
        return 0
    print(json.dumps(check(argv[0], argv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
