"""Swin2SR enlarge: the middle of the view enlarged two times by Swin2SR (caidas/swin2SR-lightweight-x2-64).

run(picture, params) takes the view (a uint8 RGB array) and `size`, the square of its middle it enlarges. The model's
own pinned files are read from MICROMIND_ENTRY_FILES with transformers' own classes, local files only, safetensors, never
its makers' code. No network, nothing installed at run time."""
import os
from pathlib import Path

import numpy as np

_MODEL: dict = {}


def _load() -> dict:
    if not _MODEL:
        import torch
        from transformers import Swin2SRForImageSuperResolution, Swin2SRImageProcessor

        d = Path(os.environ["MICROMIND_ENTRY_FILES"])
        torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
        _MODEL["processor"] = Swin2SRImageProcessor.from_pretrained(d, local_files_only=True)
        _MODEL["model"] = Swin2SRForImageSuperResolution.from_pretrained(
            d, local_files_only=True, use_safetensors=True, trust_remote_code=False).eval()
    return _MODEL


def run(picture, params):
    import torch

    size = int(params["size"])
    h, w = picture.shape[:2]
    if min(h, w) < size:
        return {"unsupported": f"the view is {w}x{h}, smaller than the {size} px it enlarges"}
    top, left = (h - size) // 2, (w - size) // 2
    part = np.ascontiguousarray(picture[top:top + size, left:left + size])
    loaded = _load()
    inputs = loaded["processor"](part, return_tensors="pt")
    with torch.no_grad():
        out = loaded["model"](**inputs).reconstruction[0]
    enlarged = (out.clamp(0, 1).permute(1, 2, 0).numpy() * 255).round().astype(np.uint8)
    return np.ascontiguousarray(enlarged[: 2 * size, : 2 * size])  # the processor pads to its window: cut back
