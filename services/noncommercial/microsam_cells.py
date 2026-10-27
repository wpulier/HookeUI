"""micro-sam's light-microscopy model (`vit_b_lm`): every cell in the view in one call (the `cells`
op, option CELLS "micro-sam-lm") and, as an outline model, a click on a cell (`segment`, option
OUTLINE "micro-sam-lm"). micro-sam's code is MIT and its weights CC-BY-4.0, but the weights were
trained on data licensed for non-commercial use (LIVECell CC BY-NC 4.0, TissueNet and
DynamicNuclearNet, Cellpose's dataset), so, like Cellpose-SAM, it lives outside the core
(README.md here) and is reached only through orchestrator/licences.py.

micro-sam runs in its own Python (microsam_server.py, MICROMIND_MICROSAM_PYTHON): this class starts
that process on first use, keeps it, and turns its label images and masks into polygons here.
"""
from __future__ import annotations

import os
import select
import subprocess
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from gpu_worker.worker import area_limit, mask_to_polygons
from noncommercial.microsam_server import read_message, write_message

SERVER = Path(__file__).with_name("microsam_server.py")
DEFAULT_PYTHON = Path.home() / ".cache" / "micromind" / "venvs" / "microsam" / "bin" / "python"
# Option id -> (micro-sam model type, automatic segmentation mode, input picture); see
# microsam_server.py for the modes and inputs, docs/bench/finding.md for why these.
MODELS = {"micro-sam-lm": ("vit_b_lm", "ais", "clahe-inv")}
# Its start (its Python, micro-sam's imports and the model, `load`) may take this long (its weights are the catalog's
# pinned files: never downloaded here). The run that follows has its own timeout, the caller's (live 2026-10-11: a cold
# start of 120 s under load counted against the 180 s outline timeout, and this limit was not used at all).
STARTUP_S = 300.0
# Its own Python runs niced: a cold start (torch, micro-sam's imports, the model) was the heaviest CPU work beside
# the live view (live 2026-10-11 02:38); measured +11% on a cold outline under an outside load, none when quiet
# (docs/bench/priority.md). MICROMIND_MICROSAM_PRIORITY=none starts it at normal priority.
NICE = 10


def catalog_env() -> Path | None:
    """The Python the app made for micro-sam from its catalog lockfile (orchestrator/envs.py), when it is ready."""
    from gpu_worker import manifests as catalog
    from orchestrator import envs

    m = catalog.get("micro-sam-lm")
    return envs.python(m) if m is not None else None


def python() -> Path:
    """The catalog's environment first (pinned, every package by its hash); else one set outside the app
    (MICROMIND_MICROSAM_PYTHON) or made by hand before (DEFAULT_PYTHON)."""
    made = catalog_env()
    if made is not None:
        return made
    return Path(os.environ.get("MICROMIND_MICROSAM_PYTHON") or DEFAULT_PYTHON).expanduser()


def labels_to_polygons(labels: np.ndarray, offset=(0, 0), min_area: float = 20) -> list:
    """One polygon per labelled cell (its largest piece), in frame pixels; each cell is traced
    inside its own bounding box, so a view of hundreds of cells costs one pass, not hundreds."""
    ys, xs = np.nonzero(labels)
    if not len(ys):
        return []
    values = labels[ys, xs]
    order = np.argsort(values, kind="stable")
    values, ys, xs = values[order], ys[order], xs[order]
    starts = np.flatnonzero(np.r_[True, values[1:] != values[:-1]])
    polygons = []
    for start, end in zip(starts, np.r_[starts[1:], len(values)]):
        y0, y1, x0, x1 = ys[start:end].min(), ys[start:end].max(), xs[start:end].min(), xs[start:end].max()
        mask = labels[y0:y1 + 1, x0:x1 + 1] == values[start]
        polygons.extend(mask_to_polygons(mask, offset=(offset[0] + int(x0), offset[1] + int(y0)), min_area=min_area)[:1])
    return polygons


def unpack_masks(packed: np.ndarray, count: int) -> list[np.ndarray]:
    return [((packed >> k) & 1).astype(bool) for k in range(count)]


def best_mask(masks: list[np.ndarray], scores: list[float], req: dict) -> int | None:
    """As the GPU worker picks among EdgeTAM's three masks: the best score within the area limit
    (a point is never "the whole field"), the smallest close one when asked to prefer small."""
    if not masks:
        return None
    h, w = masks[0].shape
    limit = area_limit(req, h, w)
    areas = [int(m.sum()) for m in masks]
    allowed = [i for i in range(len(masks)) if 0 < areas[i] <= limit]
    if not allowed:
        return None
    best = max(allowed, key=lambda i: scores[i])
    if req.get("prefer_small"):
        close = [i for i in allowed if scores[i] >= scores[best] - 0.08]
        best = min(close, key=lambda i: areas[i])
    return best


class MicroSamCells:
    def __init__(self, device=None, command: list[str] | None = None) -> None:
        self.command = command
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def _start(self) -> subprocess.Popen:
        if self._process is not None and self._process.poll() is None:
            return self._process
        command = self.command
        if command is None:
            exe = python()
            if not exe.exists():
                raise RuntimeError(f"micro-sam is not installed here (no Python at {exe}; "
                                   "see services/noncommercial/README.md)")
            command = [str(exe), str(SERVER)]
            if NICE and os.environ.get("MICROMIND_MICROSAM_PRIORITY", "nice") != "none" and Path("/usr/bin/nice").exists():
                command = ["/usr/bin/nice", "-n", str(NICE), *command]
        # It inherits the worker's environment (its thread caps, gpu_worker/boot.py), and is told where micro-sam's
        # pinned files are (gpu_worker/model_files.py): it never fetches them itself.
        from gpu_worker.model_files import model_file

        pinned = {"MICROMIND_MICROSAM_CHECKPOINT": str(model_file("micro-sam-lm", "vit_b_lm")),
                  "MICROMIND_MICROSAM_DECODER": str(model_file("micro-sam-lm", "vit_b_lm_decoder"))}
        made = catalog_env() if self.command is None else None
        if made is not None:  # the catalog's environment: a clean one, so nothing of this shell resolves another install
            from orchestrator import envs

            env = {**envs.clean_env(d=made.parent.parent), **pinned}
        else:
            env = {**os.environ, **pinned}
        self._process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         cwd=str(SERVER.parent.parent), env=env)
        return self._process

    @property
    def pid(self) -> int | None:
        process = self._process
        return process.pid if process is not None and process.poll() is None else None

    def load(self, option: str = "micro-sam-lm", limit_s: float = STARTUP_S) -> None:
        """Start micro-sam's Python and load the model (and its automatic segmenter) there, within `limit_s`
        (STARTUP_S): its start, not a run, is what that limit is for. Past it the process is stopped and the
        load fails, said so."""
        model_type, mode, _picture = MODELS.get(option, MODELS["micro-sam-lm"])
        with self._lock:
            process = self._start()
            deadline = time.monotonic() + limit_s
            try:
                write_message(process.stdin, {"op": "load", "model": model_type, "mode": mode})
                ready, _, _ = select.select([process.stdout], [], [], max(0.0, deadline - time.monotonic()))
                if not ready:
                    process.kill()
                    self._process = None
                    raise RuntimeError(f"micro-sam did not start within {limit_s:.0f} s")
                reply, _ = read_message(process.stdout)
            except (EOFError, BrokenPipeError, OSError) as exc:
                self._process = None
                raise RuntimeError(f"micro-sam stopped while starting ({type(exc).__name__})") from exc
        if not reply.get("ok"):
            raise RuntimeError(f"micro-sam: {reply.get('error', 'failed to load')}")

    def call(self, header: dict, payload: bytes = b"") -> tuple[dict, bytes]:
        with self._lock:
            process = self._start()
            try:
                write_message(process.stdin, header, payload)
                reply, data = read_message(process.stdout)
            except (EOFError, BrokenPipeError, OSError) as exc:
                self._process = None
                raise RuntimeError(f"micro-sam stopped ({type(exc).__name__})") from exc
        if not reply.get("ok"):
            raise RuntimeError(f"micro-sam: {reply.get('error', 'failed')}")
        return reply, data

    def outline(self, req: dict) -> dict:
        model_type, mode, picture = MODELS.get(req.get("model") or "micro-sam-lm", MODELS["micro-sam-lm"])
        mode = req.get("mode") or mode  # benches try micro-sam's other modes ("apg") and inputs
        picture = req.get("input") or picture
        t0 = time.perf_counter()
        reply, png = self.call({"op": "cells", "model": model_type, "mode": mode, "input": picture, "crop": req.get("crop"),
                                  "generate": req.get("generate") or {}}, req["jpeg"])
        labels = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
        x, y = (req.get("crop") or (0, 0))[:2]
        polygons = labels_to_polygons(labels, offset=(x, y))
        from gpu_worker.worker import encode_labels

        # The label map itself is the evidence; micro-sam's automatic segmentation gives no per-cell
        # score, so none is given (never invented).
        return {"polygons": polygons, "count": len(polygons), "labels": encode_labels(labels, (x, y)), "scores": {},
                "score": None, "ms": round((time.perf_counter() - t0) * 1000, 1), "modelMs": reply.get("ms")}

    def segment(self, req: dict) -> dict:
        model_type = MODELS.get(req.get("model") or "micro-sam-lm", MODELS["micro-sam-lm"])[0]
        t0 = time.perf_counter()
        header = {"op": "segment", "model": model_type, "frame_id": req.get("frame_id"),
                  "points": req.get("points"), "labels": req.get("labels"), "box": req.get("box")}
        reply, png = self.call(header, req["jpeg"])
        packed = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
        masks = unpack_masks(packed, len(reply["scores"]))
        best = best_mask(masks, reply["scores"], req)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        if best is None:
            return {"polygons": [], "score": 0.0, "area": 0, "ms": ms}
        return {"polygons": mask_to_polygons(masks[best]), "score": float(reply["scores"][best]),
                "area": int(masks[best].sum()), "ms": ms}

    def close(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.stdin.close()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.terminate()
        self._process = None
