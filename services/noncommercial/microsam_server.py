"""micro-sam in its own Python: the process `microsam_cells.py` starts and talks to.

micro-sam (MIT code; light-microscopy weights trained partly on data licensed for non-commercial
use, README.md here) pulls in about 130 packages (napari and friends), so it lives in its own
virtual environment (MICROMIND_MICROSAM_PYTHON, default ~/.cache/micromind/venvs/microsam/bin/python),
not the app's. This file runs there; its top level needs only the standard library, so the app's
side can import the wire functions below without micro-sam installed.

Wire: each message is a JSON header and a binary payload, each preceded by its length (4 bytes,
big-endian), on stdin (requests) and the process's original stdout (replies; everything micro-sam
prints goes to stderr).

  ping                                         -> {ok, device, ms}
  load    {model, mode?}                       -> {ok, ms}: the model (and its automatic segmenter) loaded now
  cells   {model, mode, input, crop?} + JPEG   -> {ok, count, ms, shape} + 16-bit PNG of instance labels
          mode "ais" (the lm decoder's instance segmentation) or "apg" (prompts from the decoder);
          input "rgb", "green", "green-inv" (the green channel inverted: a stained cell on a bright
          field becomes a bright cell on a dark one, as in the fluorescence much of its training
          data is) or "clahe-inv" (that, with local contrast evened out); on blood smears colour
          input finds almost no red cells (docs/bench/finding.md)
  segment {model, frame_id, points?, labels?, box?} + JPEG
                                               -> {ok, scores, ms, shape} + 8-bit PNG, bit k = mask k
"""
from __future__ import annotations

import json
import os
import struct
import sys
import time


def write_message(stream, header: dict, payload: bytes = b"") -> None:
    data = json.dumps(header).encode()
    stream.write(struct.pack(">I", len(data)) + data + struct.pack(">I", len(payload)) + payload)
    stream.flush()


def _read_exact(stream, n: int) -> bytes:
    chunks, got = [], 0
    while got < n:
        chunk = stream.read(n - got)
        if not chunk:
            raise EOFError("closed")
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


def read_message(stream) -> tuple[dict, bytes]:
    (n,) = struct.unpack(">I", _read_exact(stream, 4))
    header = json.loads(_read_exact(stream, n))
    (m,) = struct.unpack(">I", _read_exact(stream, 4))
    return header, _read_exact(stream, m) if m else b""


class Server:
    """The models, loaded on first use and kept; one image embedding per frame is reused by
    several prompts on it (a click and its refinement)."""

    def __init__(self) -> None:
        import torch
        from micro_sam import util

        self.util = util
        self.device = util.get_device(None)
        self.torch = torch
        self.models: dict[str, tuple] = {}  # model_type -> (predictor, state)
        self.segmenters: dict[tuple[str, str], object] = {}
        self.embedded: tuple | None = None  # (model_type, frame_id) of the predictor's current image

    def model(self, model_type: str):
        if model_type not in self.models:
            # The pinned files the app found for it (microsam_cells.py, gpu_worker/model_files.py); never fetched here.
            pinned = {"checkpoint_path": os.environ.get("MICROMIND_MICROSAM_CHECKPOINT"),
                      "decoder_path": os.environ.get("MICROMIND_MICROSAM_DECODER")} if model_type == "vit_b_lm" else {}
            if not all(pinned.values()) or not pinned:
                # micro_sam would fetch them itself (pooch, into its own cache): never here (the catalog audit, G1).
                raise RuntimeError(f"micro-sam's {model_type} files weren't given (the catalog's pinned revision): "
                                   "download micro-sam on the Models page")
            predictor, state = self.util.get_sam_model(model_type=model_type, device=self.device, return_state=True,
                                                       **pinned)
            self.models[model_type] = (predictor, state)
        return self.models[model_type]

    def segmenter(self, model_type: str, mode: str):
        key = (model_type, mode)
        if key not in self.segmenters:
            from micro_sam.automatic_segmentation import get_predictor_and_segmenter

            predictor, state = self.model(model_type)
            _p, segmenter = get_predictor_and_segmenter(model_type=model_type, device=self.device, segmentation_mode=mode,
                                                        predictor=predictor, state=state)
            self.segmenters[key] = segmenter
        return self.segmenters[key]

    def cells(self, header: dict, jpeg: bytes) -> tuple[dict, bytes]:
        import cv2
        import numpy as np
        from micro_sam.automatic_segmentation import automatic_instance_segmentation

        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        x, y, w, h = header.get("crop") or (0, 0, image.shape[1], image.shape[0])
        crop = image[y : y + h, x : x + w]
        kind = header.get("input", "rgb")
        if kind == "green":
            picture = crop[:, :, 1]
        elif kind == "green-inv":
            picture = 255 - crop[:, :, 1]
        elif kind == "clahe-inv":
            picture = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(255 - crop[:, :, 1])
        else:
            picture = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        picture = np.ascontiguousarray(picture)
        model_type, mode = header.get("model", "vit_b_lm"), header.get("mode", "ais")
        predictor, _state = self.model(model_type)
        segmenter = self.segmenter(model_type, mode)
        t0 = time.perf_counter()
        labels = automatic_instance_segmentation(predictor=predictor, segmenter=segmenter, input_path=picture, ndim=2,
                                                 verbose=False, **header.get("generate", {}))
        self.embedded = None  # the predictor now holds this image, not a click's frame
        ms = round((time.perf_counter() - t0) * 1000, 1)
        # Numbered 1..n (0 the background) so the labels fit a 16-bit PNG.
        ids, inverse = np.unique(np.asarray(labels), return_inverse=True)
        relabelled = (inverse.reshape(np.shape(labels)) + (0 if ids[0] == 0 else 1)).astype(np.uint16)
        count = int(len(ids) - (1 if ids[0] == 0 else 0))
        ok, png = cv2.imencode(".png", relabelled)
        return {"ok": True, "count": count, "ms": ms, "shape": list(np.shape(labels))}, png.tobytes()

    def segment(self, header: dict, jpeg: bytes) -> tuple[dict, bytes]:
        import cv2
        import numpy as np

        model_type = header.get("model", "vit_b_lm")
        predictor, _state = self.model(model_type)
        t0 = time.perf_counter()
        key = (model_type, header.get("frame_id"))
        if self.embedded != key or header.get("frame_id") is None:
            image = cv2.cvtColor(cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
            predictor.set_image(image)
            self.embedded = key
        kwargs = {}
        if header.get("points"):
            kwargs["point_coords"] = np.array(header["points"], np.float32)
            kwargs["point_labels"] = np.array(header.get("labels") or [1] * len(header["points"]), np.int32)
        if header.get("box"):
            kwargs["box"] = np.array(header["box"], np.float32)
        masks, scores, _logits = predictor.predict(multimask_output=True, **kwargs)
        packed = np.zeros(masks.shape[1:], np.uint8)
        for k, mask in enumerate(masks[:8]):
            packed |= (mask.astype(np.uint8) << k)
        ok, png = cv2.imencode(".png", packed)
        return {"ok": True, "scores": [float(s) for s in scores[:8]], "ms": round((time.perf_counter() - t0) * 1000, 1),
                "shape": list(packed.shape)}, png.tobytes()


def _refuse_network() -> None:
    """make catalog-proof: this Python reaches nothing beyond this Mac either (gpu_worker/offline.py, loaded by its path:
    micro-sam's own Python has none of the app's packages on its path)."""
    if os.environ.get("MICROMIND_REFUSE_NETWORK") != "1":
        return
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("micromind_offline", Path(__file__).resolve().parent.parent / "gpu_worker" / "offline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.refuse()


def main() -> None:
    _refuse_network()
    # Replies go on the original stdout only; anything the libraries print goes to stderr.
    out = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    stdin = sys.stdin.buffer
    t0 = time.perf_counter()
    server = Server()
    print(f"[micro-sam] ready on {server.device} in {time.perf_counter() - t0:.1f}s", file=sys.stderr, flush=True)
    while True:
        try:
            header, payload = read_message(stdin)
        except EOFError:
            return  # the app closed the pipe (or exited): so do we
        try:
            op = header.get("op")
            if op == "ping":
                reply, data = {"ok": True, "device": str(server.device)}, b""
            elif op == "load":  # the model (and its automatic segmenter) before a run needs it: a start said as one
                t0 = time.perf_counter()
                server.model(header["model"])
                if header.get("mode"):
                    server.segmenter(header["model"], header["mode"])
                reply, data = {"ok": True, "ms": round((time.perf_counter() - t0) * 1000, 1)}, b""
            elif op == "cells":
                reply, data = server.cells(header, payload)
            elif op == "segment":
                reply, data = server.segment(header, payload)
            else:
                reply, data = {"ok": False, "error": f"unknown op {op}"}, b""
        except Exception as exc:  # noqa: BLE001 - every failure goes back as the reply
            import traceback

            reply, data = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-1500:]}, b""
        write_message(out, reply, data)


if __name__ == "__main__":
    main()
