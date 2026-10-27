"""The reviewed runner for a Hugging Face transformers segmentation model (catalog/templates/README.md; C3).

Copied byte for byte into an entry made from a Hugging Face link (orchestrator/link_recognise.py). It loads the model's
own pinned files (config.json, preprocessor_config.json, model.safetensors) from MICROMIND_ENTRY_FILES with transformers'
own classes only: `local_files_only`, `use_safetensors`, never `trust_remote_code` (a config asking for its makers' code,
`auto_map`, is refused). It runs inside macOS's sandbox like any model someone else made (orchestrator/sandbox.py).

- **Semantic models** (SegFormer, UperNet, DPT, BEiT, MobileViT): each pixel's class, then every connected region of a
  class that isn't background is one thing found. Background is the classes named so ("background", "bg",
  "unlabeled", "void"). When none is named background, every predicted class is retained; class zero has no special
  meaning. These are connected semantic regions, not the model's own instance predictions.
- **Classes that aren't things, said by a person:** a model whose config names its classes only by number (Coralscapes'
  SegFormer: `"13": 13`) can't say which are background. An optional `classes.json` beside this adapter in the entry's
  folder, `{"notThings": [0, 13, 14], "source": "<where that comes from: the dataset card and its revision>"}`, adds
  those classes to background. It is part of the entry, so a change is a new version, proven again. A file that can't
  be read, or names a class the model doesn't have, stops the adapter with the reason: it never runs on a guess.
- **Universal models** (Mask2Former, MaskFormer): their own instances, as the model's processor gives them.

`segment(picture, params)` takes the camera's picture (H, W, 3 uint8 RGB) and returns an int32 label map of its size.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

SEMANTIC = {"segformer", "upernet", "dpt", "beit", "mobilevit", "mobilevitv2", "data2vec-vision"}
UNIVERSAL = {"mask2former", "maskformer"}
BACKGROUND = {"background", "bg", "_background_", "unlabeled", "unlabelled", "void"}
_MODEL: dict = {}


def _load() -> dict:
    if _MODEL:
        return _MODEL
    import torch
    from transformers import AutoImageProcessor

    d = Path(os.environ["MICROMIND_ENTRY_FILES"])
    config = json.loads((d / "config.json").read_text())
    if config.get("auto_map"):
        raise RuntimeError("it needs its makers' own code (auto_map): the reviewed runner never runs it")
    kind = str(config.get("model_type") or "")
    if kind in SEMANTIC:
        from transformers import AutoModelForSemanticSegmentation as Auto
    elif kind in UNIVERSAL:
        from transformers import AutoModelForUniversalSegmentation as Auto
    else:
        raise RuntimeError(f"its kind of model ({kind or 'not stated'}) isn't one the reviewed runner knows")
    torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
    processor = AutoImageProcessor.from_pretrained(d, local_files_only=True)
    model = Auto.from_pretrained(d, local_files_only=True, use_safetensors=True, trust_remote_code=False).eval()
    labels = {int(k): str(v) for k, v in (config.get("id2label") or {}).items()}
    background = {i for i, name in labels.items() if name.strip().lower() in BACKGROUND}
    background |= _not_things(Path(__file__).parent / "classes.json", labels)
    _MODEL.update(kind=kind, processor=processor, model=model, background=background)
    return _MODEL


def _not_things(path: Path, labels: dict) -> set:
    """The classes a person said aren't things (classes.json), checked against the model's own classes."""
    if not path.exists():
        return set()
    try:
        said = json.loads(path.read_text())
        ids = said["notThings"]
        if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
            raise ValueError("notThings must be a list of class numbers")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"its classes.json can't be read ({exc})") from exc
    unknown = sorted(set(ids) - set(labels)) if labels else []
    if unknown:
        raise RuntimeError(f"its classes.json names classes the model doesn't have: {unknown}")
    return set(ids)


def segment(picture: np.ndarray, params: dict) -> np.ndarray:
    import torch

    m = _load()
    h, w = picture.shape[:2]
    inputs = m["processor"](images=picture, return_tensors="pt")
    with torch.inference_mode():
        out = m["model"](**inputs)
    if m["kind"] in UNIVERSAL:
        found = m["processor"].post_process_instance_segmentation(out, target_sizes=[(h, w)])[0]
        segmentation = found["segmentation"].cpu().numpy().astype(np.int64)
        return np.where(segmentation >= 0, segmentation + 1, 0).astype(np.int32)  # -1: nothing there
    from scipy import ndimage

    classes = m["processor"].post_process_semantic_segmentation(out, target_sizes=[(h, w)])[0].cpu().numpy()
    labels = np.zeros((h, w), np.int32)
    n = 0
    for c in np.unique(classes):
        if int(c) in m["background"]:
            continue
        regions, k = ndimage.label(classes == c)
        labels[regions > 0] = regions[regions > 0] + n
        n += k
    return labels
