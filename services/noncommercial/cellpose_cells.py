"""Cellpose-SAM: outline every cell in the view in one call (the `cells` op of the heavy GPU
worker). Cellpose's code is BSD-3-Clause, but its weights were trained on data licensed for
non-commercial use, so this lives outside the core (README.md here).

Also here (session knowledge K4): the same finder with weights fine-tuned on the user's corrected cells
(`CellposeTunedCells`, CELLS "cellpose-sam-tuned"), and the fine-tune itself (`train`), run only by the
explicit job bench/finetune_cells.py, never by the app. Fine-tuned weights inherit Cellpose's tier."""
from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np

# How a view goes in, the same for finding, fine-tuning and replaying: the green channel of the crop the
# finder was given (best contrast for plant tissue), as a 2-D picture.
CHANNEL = 1
FLOW_THRESHOLD = 0.8
CELLPROB_THRESHOLD = -2.0
SAM_BLOCK = 256  # Cellpose-SAM trains on 256 px blocks (cellpose.train.train_seg refuses any other)
BASE_WEIGHTS = "cpsam_v2"  # what CellposeModel loads when none is named (cellpose 4.2), and what a fine-tune starts from


def pinned_weights() -> Path:
    """Cellpose-SAM's pinned weights file (gpu_worker/model_files.py: the models folder, else cellpose's own folder at
    that file's hash): never the one cellpose would fetch from its repository's main."""
    from gpu_worker.model_files import model_file

    return model_file("cellpose-sam", BASE_WEIGHTS)


def base_weights(digest: bool = True) -> dict:
    """What the base network is, for a fine-tune's provenance: cellpose's version, its weights file, its size and
    (with `digest`) its SHA-256. A file not downloaded yet is said so."""
    import hashlib
    import importlib.metadata

    path = pinned_weights()
    out = {"cellpose": importlib.metadata.version("cellpose"), "weights": BASE_WEIGHTS, "file": str(path),
           "bytes": path.stat().st_size if path.is_file() else None}
    if digest and path.is_file():
        sha = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                sha.update(block)
        out["sha256"] = sha.hexdigest()
    elif not path.is_file():
        out["note"] = "not downloaded here yet: cellpose downloads it on first use"
    return out


def picture(jpeg: bytes, crop=None) -> np.ndarray:
    """The 2-D picture the finder is given: the green channel of the crop (x, y, w, h) of the view."""
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    x, y, w, h = crop or (0, 0, image.shape[1], image.shape[0])
    return image[y : y + h, x : x + w, CHANNEL]


def checked_model(device, weights: str | None = None):
    """A Cellpose model on a device proven to give Cellpose-SAM's answer (cellpose_device.py), and that proof. The verdict
    is the base network's on the probe; fine-tuned weights or your own model's (`weights`) run where the base network
    was proven, since their answers on the probe legitimately differ from the base's. Without the base network's own
    download (your own model needs only its file) the device can't be checked: the CPU is used, said, and nothing is kept."""
    import torch
    from cellpose import models

    from gpu_worker import manifests as catalog
    from gpu_worker.model_files import NotDownloaded, folder

    from . import cellpose_device

    def make(dev, bf16, path):
        return models.CellposeModel(gpu=dev.type != "cpu", device=dev, pretrained_model=str(path or base), use_bfloat16=bf16)

    try:
        base = pinned_weights()
    except (NotDownloaded, KeyError, OSError):
        if weights is None:
            raise
        cpu = torch.device("cpu")
        proof = {"device": "cpu", "dtype": "bfloat16", "checked": "the CPU is the reference device"}
        if getattr(device, "type", str(device)) != "cpu":
            proof["says"] = cellpose_device.NO_BASE_WORDS
        return make(cpu, True, weights), proof
    entry = catalog.get("cellpose-sam")
    # Beside the model's revisions, never inside the published revision (read-only): model_files.folder.
    record = (folder(entry) if entry is not None else base.parent) / cellpose_device.RECORD
    proof, probed = cellpose_device.verdict(lambda dev, bf16: make(dev, bf16, None), device, weights=base, record=record)
    if probed is not None and weights is None:
        return probed, proof  # the base network that passed the probe is the one wanted
    del probed
    return make(cellpose_device.placed(proof, device), proof["dtype"] == "bfloat16", weights), proof


class CellposeCells:
    def __init__(self, device) -> None:
        self.model, self.proof = checked_model(device)

    def outline(self, req: dict) -> dict:
        """Every cell: the label map itself (the specialist's own output: one label per cell, never
        overlapping), each label's score (the mean cell probability inside it, from Cellpose's
        `cellprob`), and one outline per label for display."""
        from noncommercial.microsam_cells import labels_to_polygons
        from gpu_worker.worker import encode_labels

        image = cv2.imdecode(np.frombuffer(req["jpeg"], np.uint8), cv2.IMREAD_COLOR)
        x, y, w, h = req.get("crop") or (0, 0, image.shape[1], image.shape[0])
        channel = image[y : y + h, x : x + w, CHANNEL]  # green channel: best contrast for plant tissue
        t0 = time.perf_counter()
        # All workers share the physical GPU. One tile per submission keeps this background
        # network from delaying the click worker; tiling, pixels and mask thresholds stay unchanged.
        labels, flows, _styles = self.model.eval(channel, flow_threshold=FLOW_THRESHOLD, cellprob_threshold=CELLPROB_THRESHOLD,
                                                 batch_size=1)
        labels = np.asarray(labels)
        scores = {}
        try:
            prob = 1.0 / (1.0 + np.exp(-np.asarray(flows[2], np.float32)))  # cellprob logits -> probability
            flat = labels.ravel().astype(np.int64)
            sums = np.bincount(flat, weights=prob.ravel(), minlength=int(labels.max()) + 1)
            counts = np.bincount(flat, minlength=int(labels.max()) + 1)
            scores = {int(k): round(float(sums[k] / counts[k]), 4) for k in range(1, len(counts)) if counts[k]}
        except Exception:
            scores = {}  # no per-cell score rather than an invented one
        polygons = labels_to_polygons(labels, offset=(x, y), min_area=20)  # one pass, each cell in its own box
        return {"polygons": polygons, "count": int(len(np.unique(labels)) - (1 if (labels == 0).any() else 0)),
                "labels": encode_labels(labels, (x, y)), "scores": scores, "score": "cellprob",
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                # Which device made it and how that device was proven (cellpose_device.py): kept with the result.
                "producer": {"device": {k: v for k, v in self.proof.items() if k != "tried"}}}


class CellposeTunedCells(CellposeCells):
    """Cellpose-SAM with weights fine-tuned on the user's corrected cells (orchestrator/finetuned.py): the
    same input and settings as the base finder, so a replay compares the weights and nothing else."""

    def __init__(self, device, weights: str) -> None:
        self.weights = str(weights)
        self.model, self.proof = checked_model(device, self.weights)

    def outline(self, req: dict) -> dict:
        return {**super().outline(req), "weights": Path(self.weights).parent.name}


def train(images: list[np.ndarray], labels: list[np.ndarray], *, epochs: int = 100, learning_rate: float = 1e-5,
          weight_decay: float = 0.1, out: Path, name: str, net=None, device=None, block: int | None = None,
          min_masks: int = 1) -> dict:
    """Fine-tune Cellpose-SAM on `images` (2-D pictures as `picture` makes them) and their label maps (0 is no
    cell, 1..n each cell), with cellpose's own trainer (cellpose.train.train_seg: AdamW, its flows, its
    augmentations). `net` is the network to train (default: Cellpose-SAM's own, loaded here); the weights are
    written under `out` and their file returned with the losses per epoch. The held-out days are never given to
    the trainer, not even as its test data: what it keeps could never depend on them."""
    import torch
    from cellpose import train as cellpose_train

    if net is None:
        # Trained only where Cellpose-SAM's answers are proven (cellpose_device.py): a GPU that computes wrongly would
        # train wrongly without saying so.
        if device is None:
            device = torch.device("mps") if torch.backends.mps.is_available() else torch.device("cpu")
        net = checked_model(device)[0].net
    if block is None:
        block = SAM_BLOCK if getattr(net, "backbone", "") == "sam_vitl" else 256
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(0)
    np.random.seed(0)
    path, train_losses, _ = cellpose_train.train_seg(
        net, train_data=list(images), train_labels=list(labels), n_epochs=int(epochs), learning_rate=float(learning_rate),
        weight_decay=float(weight_decay), batch_size=1, bsize=int(block), save_path=str(out), model_name=name,
        min_train_masks=int(min_masks), normalize=True)
    return {"file": str(path), "trainLosses": [round(float(v), 5) for v in train_losses], "block": int(block),
            "cellposeBackbone": getattr(net, "backbone", None)}
