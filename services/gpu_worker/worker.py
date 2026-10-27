"""GPU worker: outline models in their own process so inference never stalls the frame pump.

Requests arrive over a multiprocessing pipe as dicts and answers go back the same way.
Operations:
  ping                                     -> {"ok": True, "device": ...}
  segment {frame_id, jpeg, points?, labels?, box?, model?, text?}
                                           -> {"polygons": [[[x, y], ...]], "score", "area", "ms"}
           model: "edgetam" (default) | "sam2.1-tiny" | "sam3.1-local" (its tracker: the thing in the box or
                  at the point; with `text` or `exemplar`, its detector: what is like the box)
                  | "micro-sam-lm" (micro-sam's light-microscopy SAM, in its own Python)
  cells   {frame_id, jpeg, crop?, model?, weights?, version?}  -> {"polygons": [...], "count", "ms"}
           model (option CELLS): "cellpose-sam" (default) | "micro-sam-lm" | "cellpose-sam-tuned" (the
           fine-tuned weights file and its version given in `weights` and `version`, orchestrator/
           finetuned.py); all research edition only (services/noncommercial/, orchestrator/licences.py)
  track   {frame_id, jpeg, start?: [{id, box}], stop?: [id]}
                                           -> {"objects": {id: {"polygons", "score", "present", "area", "box"}}, "ms"}
           EdgeTAM's video model following outlined objects frame to frame (gpu_worker/objects.py,
           option TRACK); runs on the heavy worker
  regions {frame_id, jpeg, polygons, boxes?} -> {"regions": [{"polygons", "score", "frac"}], "crop", "ms"}
           the parts inside an object: a point grid inside it (gpu_worker/regions.py), or
           EdgeTAM box prompts for `boxes` [[x0,y0,x1,y1] in frame px] (vlm-boxes dissect)
  automask {frame_id, jpeg, disc?: [cx, cy, r], points?: [[x, y]...]}
                                           -> {"regions": [{"polygons", "label", "inner", "score", "stability", "area"}], "prompts", "candidates", "ms"}
           every distinct thing in the disc as a mask (gpu_worker/automask.py; pointing "masks");
           heavy worker, clicks and tracking served between chunks
  embed   {jpegs: [crop JPEG, ...], model?} -> {"vectors": [[dim floats], ...], "dim", "model", "ms"}
           square crops of outlined things as unit vectors (gpu_worker/embed.py; model: option
           EMBED, "dinov2-small" (384) or "bioclip-2" (768)), for the labels the user teaches and
           outlines seen before today (orchestrator/taught.py, seen.py); heavy worker
  zeroshot {jpegs: [crop JPEG, ...], names: [...], template?: "a {} in a blood smear"}
                                           -> {"rankings": [[[name, share], ...], ...], "model", "ms"}
           MedSigLIP's zero-shot ranking of the names for each crop (gpu_worker/zeroshot.py), a
           line of data for naming a click (orchestrator/zeroshot.py, option ZEROSHOT); heavy worker
  molmo_load {}                            -> {"loadMs", "warmMs", "memoryGb"}
  molmo_read {frame_id, jpeg, crop?: [x, y, w, h]} -> {"ms"}
  molmo_point {frame_id, jpeg, query, crop?} -> {"points": [[x, y] frame px], "text", "tokens", "read", "ms", ...}
           Molmo 2 4B pointing (gpu_worker/molmo.py; pointing "molmo"): in its own worker process
           ("molmo"), loaded on first use; a picture read once is kept, so a request on it only
           reads its words
  load    {model}                          -> {"model", "already"}: a model loaded without running it (a
           default made ready early, orchestrator/loads.py)
  unload  {model}                          -> {"unloaded": [...], "freedMb"}: one model let go of (the memory
           guard; one Cellpose-SAM network at a time goes the same way)
  loadtest {seconds, sleep?, spin?}        -> {"threads", "ms"}: the sim harness holds the worker as a model
           load does (scenario `model-load`)

Every model's load is told to the app as it begins and ends, with its seconds and memory (`Models.loading`,
gpu_worker/loading.py), so a load is said and never taken for a hang. The process starts capped and at its
role's priority (gpu_worker/boot.py).

Requests are served by priority: clicks (`segment`, `ping`) first. A `regions` request runs
its prompts in chunks and serves any click that arrived meanwhile between chunks, so a
dissect never makes a click wait for more than one chunk (~0.1 s). Long single-call ops
(`cells`) run in a second worker process (orchestrator/gpu_client.py, "heavy").

Image embeddings are cached per model and exact decoded pixel content, so several prompts
on the same image (or a refinement right after a first answer) cost only the ~5 ms decode. Models load on
first use, so unused options cost nothing.
"""
from __future__ import annotations

import os
import hashlib
import queue
import sys
import threading
import time
import traceback
from collections import OrderedDict
from contextlib import contextmanager

import cv2
import numpy as np

from .click_diagnostics import ClickProbe
from . import model_files  # where each model's pinned files are: the models folder, else an old cache, never fetched

if os.environ.get("MICROMIND_GPU_THREADS", "").isdigit():
    cv2.setNumThreads(int(os.environ["MICROMIND_GPU_THREADS"]))  # the role's cap (gpu_worker/boot.py)

POINT_MODELS = {"edgetam": "yonigozlan/EdgeTAM-hf", "sam2.1-tiny": "facebook/sam2.1-hiera-tiny"}
DEFAULT_MODEL = "edgetam"


def area_limit(req: dict, height: int, width: int) -> float:
    """The largest mask (pixels) an outline may be. A point: 12% of the frame (a point in
    continuous tissue otherwise wins as "the whole field"). A box: up to 1.5x the box, at
    most 90% of the frame, since the box already says how big the thing is."""
    frame_area = height * width
    limit = req.get("max_area_frac", 0.12) * frame_area
    if req.get("box"):
        x0, y0, x1, y1 = req["box"]
        limit = max(limit, min(0.9 * frame_area, 1.5 * abs(x1 - x0) * abs(y1 - y0)))
    return limit


def encode_labels(labels: np.ndarray, origin=(0, 0)) -> dict:
    """A label map for the orchestrator (evidence.decode_labels): the array's bytes, zlib-compressed,
    uint16 (uint32 past 65535 labels), with where it sits in the frame."""
    import zlib

    dtype = np.uint32 if int(labels.max(initial=0)) > 65535 else np.uint16
    arr = np.ascontiguousarray(labels.astype(dtype))
    return {"dtype": str(arr.dtype), "shape": list(arr.shape), "origin": [int(origin[0]), int(origin[1])],
            "zlib": zlib.compress(arr.tobytes(), 3)}


HOLE_MIN_FRAC = 0.05  # a hole kept in an outline is at least this share of its piece's area (and 50 px²)


def mask_to_polygons(mask: np.ndarray, offset=(0, 0), min_area: float = 12.0, epsilon_frac: float = 0.004,
                     *, scale: float = 1.0, admit=None) -> list[list[list[float]]]:
    """A mask's outline: each piece's outer edge, largest first, then the edges of its holes. A ring (a band of
    cortex around the vascular cylinder) is its two edges, filled even-odd (the studio, cv2.fillPoly; areas:
    orchestrator/scale.filled_area). Only holes of at least HOLE_MIN_FRAC of their piece are kept: on the outline-fit
    bench's 108 masks from boxes none had one (the largest gap 1.6%), so a stoma's pore or a cell's speck stays filled."""
    contours, hierarchy = cv2.findContours(mask.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return []
    links = hierarchy[0]

    def polygon(contour):
        epsilon = max(0.6, epsilon_frac * cv2.arcLength(contour, True))
        approx = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2).astype(float)
        if len(approx) < 3:
            return None
        if admit is not None:
            admit(len(approx))  # before materializing the retained Python coordinates
        return [[float(x * scale + offset[0]), float(y * scale + offset[1])] for x, y in approx]

    outer = sorted((i for i in range(len(contours)) if links[i][3] == -1 and cv2.contourArea(contours[i]) >= min_area),
                   key=lambda i: cv2.contourArea(contours[i]), reverse=True)
    polygons = [p for i in outer if (p := polygon(contours[i])) is not None]
    kept = set(outer)
    for i in range(len(contours)):
        parent = links[i][3]
        if parent in kept:
            hole = cv2.contourArea(contours[i])
            if hole >= max(50.0, HOLE_MIN_FRAC * cv2.contourArea(contours[parent])) and (p := polygon(contours[i])) is not None:
                polygons.append(p)
    return polygons


REGION_CHUNK = 8  # point prompts per decode in regions(); clicks are served between chunks
AUTO_CHUNK = 32  # point prompts per decode in automask() (heavy worker); clicks and tracking go between


class Models:
    def __init__(self) -> None:
        import torch

        cap = thread_cap()
        if cap:
            torch.set_num_threads(cap)  # the role's cap (gpu_worker/boot.py), right after torch's import
        self.torch = torch
        self.device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
        self.notify = None  # (message) -> None: a load told to the app (gpu_worker/loading.py), set by serve
        self.held: dict[str, float] = {}  # model -> when it was loaded here (what `unload` can let go of)
        self._point: dict[str, tuple] = {}
        self._cells: dict[str, object] = {}  # CELLS option -> its cell finder
        self.lane = None  # lane.Lane: background networks here wait while another worker answers a click (set by serve)
        self._sam31 = None
        self._embedders: dict[str, object] = {}  # option EMBED's models, loaded on first use
        self._embed_failed: dict[str, tuple[float, str]] = {}  # a model that failed to load: when, and why
        self._zeroshot = None
        self._molmo = None
        self.embeddings: OrderedDict[tuple[str, tuple[int, ...], str, bytes], tuple] = OrderedDict()

    # --- loads, told as they happen (gpu_worker/loading.py) --------------------------------
    def memory_mb(self) -> float:
        """What this process holds now (MB): its physical footprint, Metal's memory included (gpu_worker/footprint.py),
        else its resident memory and the GPU memory torch has from the driver."""
        from .footprint import footprint_mb

        now = footprint_mb()
        if now is not None:
            return now
        total = 0.0
        try:
            import subprocess

            done = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True, timeout=2)
            total += float(done.stdout.strip() or 0) / 1024
        except Exception:
            pass
        try:
            if self.device.type == "mps":
                total += self.torch.mps.driver_allocated_memory() / 2**20
        except Exception:
            pass
        return total

    @contextmanager
    def loading(self, model: str, limit: float | None = None, child=None):
        """A model loading here, told to the app as it begins and ends (or fails), with how long it took and how
        much memory it took; `child()` gives the pid of a process started for it (micro-sam's), whose memory is
        then its own."""
        from .loading import limit_s

        from .footprint import Peak, footprint_mb, peak_mb

        notify = getattr(self, "notify", None) or (lambda message: None)
        before = self.memory_mb()
        t0 = time.perf_counter()
        notify({"load": "loading", "model": model, "limitS": float(limit or limit_s(model))})
        try:
            with Peak() as peak:
                yield
        except BaseException as exc:
            notify({"load": "failed", "model": model, "seconds": round(time.perf_counter() - t0, 1),
                    "error": f"{type(exc).__name__}: {exc}"[:300]})
            raise
        seconds = round(time.perf_counter() - t0, 1)
        pid = child() if child is not None else None
        measured = footprint_mb() is not None  # the kernel's count, not resident memory (footprint.py)
        if pid:  # a process started for it (micro-sam's Python): all it holds, and the most it held, are the model's
            size = child_memory_mb(pid)
            top = peak_mb(pid) or size
        else:
            size = max(0.0, self.memory_mb() - before)
            top = max(size, (peak.mb - before) if peak.mb is not None else size)
        if not hasattr(self, "held"):
            self.held = {}
        self.held[model] = time.time()
        _log(f"{model} loaded in {seconds:.1f}s ({size:.0f} MB, {top:.0f} MB at its peak)")
        notify({"load": "loaded", "model": model, "seconds": seconds, "memoryMb": round(size), "peakMb": round(top),
                **({"how": "footprint"} if measured else {}), **({"pid": int(pid)} if pid else {})})

    def load(self, req: dict) -> dict:
        """Load a model without running it (a preload of a default, orchestrator/loads.py); one already
        loaded is said to be."""
        model = req.get("model")
        already = model in self.held
        if model in POINT_MODELS:
            self.point_model(model)
        elif model == "sam3.1-local":
            self.sam31()
        elif model in CELL_FINDERS:
            self.cell_finder(model, req.get("weights"))
        elif model == "edgetam-video":
            self.objects()
        elif model in EMBED_MODELS:
            self.embedder(model)
        elif model == "medsiglip":
            self.zeroshot_model()
        elif model == "molmo2-4b":
            self.molmo()
        else:
            raise ValueError(f"unknown model {model!r}")
        return {"model": model, "already": already}

    def unload(self, model: str) -> dict:
        """Let go of one model here, and give its memory back (the memory guard, orchestrator/loads.py; and
        one Cellpose-SAM network at a time). A model not loaded here is said to be."""
        before = self.memory_mb()
        dropped = []
        if model in self._point:
            self._point.pop(model)
            for key in [k for k in self.embeddings if k[0] == model]:
                self.embeddings.pop(key, None)
            dropped.append(model)
        elif model == "sam3.1-local" and self._sam31 is not None:
            self._sam31 = None
            dropped.append(model)
        elif model in CELL_FINDERS:
            for key in [k for k in self._cells if k.split("@", 1)[0] == model]:
                finder = self._cells.pop(key)
                close = getattr(finder, "close", None)
                if close is not None:
                    close()  # micro-sam's own Python stops
                dropped.append(model)
        elif model == "edgetam-video" and getattr(self, "_objects", None) is not None:
            self._objects = None
            dropped.append(model)
        elif model in self._embedders:
            self._embedders.pop(model)
            dropped.append(model)
        elif model == "medsiglip" and self._zeroshot is not None:
            self._zeroshot = None
            dropped.append(model)
        elif model == "molmo2-4b" and self._molmo is not None:
            self._molmo = None
            dropped.append(model)
        getattr(self, "held", {}).pop(model, None)
        if dropped:
            self._free_gpu()
            freed = max(0.0, before - self.memory_mb())
            _log(f"{model} unloaded ({freed:.0f} MB back)")
            (getattr(self, "notify", None) or (lambda message: None))({"load": "unloaded", "model": model,
                                                                       "freedMb": round(freed)})
            return {"unloaded": sorted(set(dropped)), "freedMb": round(freed)}
        return {"unloaded": [], "freedMb": 0}

    def point_model(self, name: str = DEFAULT_MODEL):
        if name not in self._point:
            with self.loading(name):
                from transformers import EdgeTamModel, Sam2Model, Sam2Processor

                repo = POINT_MODELS[name]
                cls = EdgeTamModel if "edgetam" in repo.lower() else Sam2Model
                path = model_files.model_dir(name)  # its pinned revision, never fetched here (model_files.py)
                model_files.companions(name)  # EdgeTAM's backbone config, from its folder too
                processor = Sam2Processor.from_pretrained(path, local_files_only=True)
                model = cls.from_pretrained(path, local_files_only=True).to(self.device).eval()
                self._point[name] = (processor, model)
        return self._point[name]

    def embed(self, name: str, frame_id: int, image_rgb: np.ndarray, probe=None):
        # Captures retain their own IDs and provenance. Only this pure image
        # computation is shared: paused sources may emit identical pixels under
        # new IDs, while a reused ID must never hide changed pixels or a crop.
        pixels = np.ascontiguousarray(image_rgb)
        key = (name, pixels.shape, pixels.dtype.str, hashlib.sha256(memoryview(pixels)).digest())
        if probe is not None:
            probe.facts["probeEmbeddingHit"] = key in self.embeddings
        if key in self.embeddings:
            self.embeddings.move_to_end(key)
            return self.embeddings[key]
        processor, model = self.point_model(name)
        inputs = processor(images=image_rgb, return_tensors="pt").to(self.device)
        with self.torch.inference_mode():
            embeddings = model.get_image_embeddings(inputs["pixel_values"])
        self.embeddings[key] = (embeddings, inputs["original_sizes"])
        while len(self.embeddings) > 6:
            self.embeddings.popitem(last=False)
        return self.embeddings[key]

    def sam31(self):
        """Meta's SAM 3.1 multiplex predictor (Apple Silicon patch, services/vendor)."""
        if self._sam31 is None:
            with self.loading("sam3.1-local"):
                from sam3.model_builder import build_sam3_predictor

                ckpt = str(model_files.model_file("sam3.1-local", "sam3.1_multiplex.pt"))  # pinned; never fetched here
                predictor = build_sam3_predictor(checkpoint_path=ckpt, version="sam3.1", use_fa3=False, async_loading_frames=False)
                predictor.model.to(self.device)
                self._sam31 = predictor
        return self._sam31

    def segment_sam31(self, req: dict) -> dict:
        """Outline the thing in a box or at a point. With words (`text`) or `exemplar`, SAM 3.1's detector finds what
        is like the box, and the match overlapping it is kept; otherwise its tracker outlines the one thing the box
        (as SAM 2 takes one: two corners, labels 2 and 3) or the point holds, as the other outline models do.
        On the outline-fit bench the detector drew nothing on 11 of 54 targets, all tissue regions, which are not
        instances of anything (docs/bench/outline-fit.md); the tracker's answer is read from its first frame
        (an image session's add_prompt returns no masks for a tracker object, 2026-10-11)."""
        import tempfile

        image = cv2.imdecode(np.frombuffer(req["jpeg"], np.uint8), cv2.IMREAD_COLOR)
        h, w = image.shape[:2]
        exemplar = bool(req.get("text") or req.get("exemplar"))
        if exemplar and not req.get("box"):
            # The detector takes boxes; a point becomes a small box around it.
            x, y = req["points"][0]
            r = req.get("point_radius", 16)
            req = {**req, "box": [x - r, y - r, x + r, y + r]}
        predictor = self.sam31()
        t0 = time.perf_counter()
        with tempfile.TemporaryDirectory() as tmp, self.torch.inference_mode():
            cv2.imwrite(f"{tmp}/00000.jpg", image)
            session = predictor.handle_request({"type": "start_session", "resource_path": tmp})["session_id"]
            try:
                if exemplar:
                    x0, y0, x1, y1 = req["box"]
                    prompt = {"bounding_boxes": [[x0 / w, y0 / h, (x1 - x0) / w, (y1 - y0) / h]], "bounding_box_labels": [1]}
                    if req.get("text"):
                        prompt["text"] = req["text"]
                elif req.get("box"):
                    x0, y0, x1, y1 = req["box"]
                    prompt = {"points": [[x0 / w, y0 / h], [x1 / w, y1 / h]], "point_labels": [2, 3], "obj_id": 1}
                else:
                    points = req["points"]
                    prompt = {"points": [[x / w, y / h] for x, y in points],
                              "point_labels": list(req.get("labels") or [1] * len(points)), "obj_id": 1}
                out = predictor.handle_request({"type": "add_prompt", "session_id": session, "frame_index": 0, **prompt})["outputs"]
                if not exemplar and not len(out.get("out_binary_masks", [])):
                    for step in predictor.handle_stream_request({"type": "propagate_in_video", "session_id": session,
                                                                 "propagation_direction": "forward", "start_frame_index": 0}):
                        if step.get("frame_index") == 0:
                            out = step["outputs"]
                            break
            finally:
                predictor.handle_request({"type": "close_session", "session_id": session})
        masks = np.asarray(out.get("out_binary_masks", []))
        probs = np.asarray(out.get("out_probs", []))
        ms = round((time.perf_counter() - t0) * 1000, 1)
        if not len(masks):
            return {"polygons": [], "score": 0.0, "area": 0, "ms": ms}
        if exemplar:
            # The exemplar itself: the match holding the box's centre, else the likeliest.
            x0, y0, x1, y1 = req["box"]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            best = max(range(len(masks)), key=lambda i: (masks[i][int(min(max(cy, 0), h - 1)), int(min(max(cx, 0), w - 1))],
                                                         probs[i] if len(probs) > i else 0))
        else:
            ids = np.asarray(out.get("out_obj_ids", [])).tolist()
            best = ids.index(1) if 1 in ids else 0
        mask = masks[best].astype(bool)
        return {"polygons": mask_to_polygons(mask), "score": float(probs[best]) if len(probs) > best else 0.0,
                "area": int(mask.sum()), "ms": ms}

    def segment(self, req: dict) -> dict:
        name = req.get("model") or DEFAULT_MODEL
        if name == "sam3.1-local":
            return self.segment_sam31(req)
        if name in GATED_SEGMENT:
            return self.cell_finder(name).segment(req)
        probe = ClickProbe.start(req)
        processor, model = self.point_model(name)
        image = cv2.cvtColor(cv2.imdecode(np.frombuffer(req["jpeg"], np.uint8), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        t0 = time.perf_counter()
        embeddings, original_sizes = (self.embed(name, req["frame_id"], image, probe=probe) if probe
                                      else self.embed(name, req["frame_id"], image))
        if probe:
            probe.mark("Embed")
        kwargs = {}
        if req.get("points"):
            kwargs["input_points"] = [[[list(p) for p in req["points"]]]]
            kwargs["input_labels"] = [[list(req.get("labels") or [1] * len(req["points"]))]]
        if req.get("box"):
            kwargs["input_boxes"] = [[list(req["box"])]]
        # embed already resized/normalized this image. Prompt coordinates need only
        # its original dimensions, including when the frame's embedding is cached.
        prompt = processor(original_sizes=original_sizes, return_tensors="pt", **kwargs).to(self.device)
        with self.torch.inference_mode():
            out = model(
                image_embeddings=embeddings,
                input_points=prompt.get("input_points"),
                input_labels=prompt.get("input_labels"),
                input_boxes=prompt.get("input_boxes"),
                multimask_output=True,
            )
        if probe:
            probe.mark("Submit")
        masks = processor.post_process_masks(out.pred_masks.cpu(), original_sizes.cpu())[0][0]
        scores = out.iou_scores[0, 0].float().cpu().numpy()
        areas = [int(m.sum()) for m in masks]
        # A point in continuous tissue often scores highest as "the whole field"; that is
        # never what someone pointing at a thing means. A box already says how big the thing
        # is, so a boxed region (a ring of xylem, a band of cortex) may fill its box: up to
        # 1.5x the box, never the whole frame (a 12% cap made large tissue impossible to
        # outline: live session 2026-10-04, 15:36).
        max_area = area_limit(req, image.shape[0], image.shape[1])
        allowed = [i for i in range(len(areas)) if 0 < areas[i] <= max_area]
        if not allowed:
            result = {"polygons": [], "score": 0.0, "area": 0, "ms": round((time.perf_counter() - t0) * 1000, 1)}
            return probe.finish(result) if probe else result
        best = max(allowed, key=lambda i: scores[i])
        if req.get("prefer_small"):
            # For tiny dots the highest-scoring mask is often a larger surrounding region;
            # take the smallest mask whose score is close to the best.
            close = [i for i in allowed if scores[i] >= scores[best] - 0.08]
            best = min(close, key=lambda i: areas[i])
        mask = masks[best].numpy().astype(bool)
        ms = (time.perf_counter() - t0) * 1000
        out = {
            "polygons": mask_to_polygons(mask),
            "score": float(scores[best]),
            "area": int(mask.sum()),
            "ms": round(ms, 1),
        }
        if req.get("candidates"):
            # Every mask the model offered for this prompt, best score first: what a check can choose among when the
            # one kept by score is not the thing meant (onion cells: part of a cell, one cell or a few, by click).
            out["candidates"] = [{"polygons": mask_to_polygons(masks[i].numpy().astype(bool)), "score": float(scores[i]),
                                  "area": areas[i]} for i in sorted(allowed, key=lambda i: -scores[i])]
        return probe.finish(out) if probe else out

    def regions(self, req: dict, between_chunks=lambda: None) -> dict:
        """The parts inside an object (see gpu_worker/regions.py)."""
        from gpu_worker.regions import CROP_PAD, crop_box, grid_points, polygon_mask, refine

        t0 = time.perf_counter()
        image = cv2.imdecode(np.frombuffer(req["jpeg"], np.uint8), cv2.IMREAD_COLOR)
        obj = polygon_mask(req["polygons"], image.shape)
        if not obj.any():
            return {"regions": [], "crop": None, "ms": 0.0}
        x0, y0, x1, y1 = crop_box(obj, CROP_PAD)
        crop = cv2.cvtColor(image[y0:y1, x0:x1], cv2.COLOR_BGR2RGB)
        obj_crop = obj[y0:y1, x0:x1]
        processor, model = self.point_model("edgetam")
        key = ("crop", req["frame_id"], x0, y0, x1, y1)
        embeddings, _ = self.embed("edgetam", key, crop)  # type: ignore[arg-type]
        between_chunks()
        size = [obj_crop.shape[0], obj_crop.shape[1]]

        def decode(**prompt):
            enc = processor(original_sizes=[size], return_tensors="pt", **prompt).to(self.device)
            with self.torch.inference_mode():
                out = model(image_embeddings=embeddings, input_points=enc.get("input_points"),
                            input_labels=enc.get("input_labels"), input_boxes=enc.get("input_boxes"),
                            multimask_output=True)
            masks = processor.post_process_masks(out.pred_masks.cpu(), [size])[0].numpy().astype(bool)
            return masks, out.iou_scores[0].float().cpu().numpy()

        candidates = []
        if req.get("boxes"):
            boxes = [[b[0] - x0, b[1] - y0, b[2] - x0, b[3] - y0] for b in req["boxes"]]
            masks, scores = decode(input_boxes=[boxes])
            for i in range(len(boxes)):
                k = int(scores[i].argmax())
                candidates.append((masks[i, k], float(scores[i, k])))
        else:
            points = grid_points(obj_crop, int(req.get("grid", 8)))
            for start in range(0, len(points), REGION_CHUNK):
                chunk = points[start:start + REGION_CHUNK]
                # Always a full chunk (the last point repeated, its extra masks ignored): on the
                # Mac's GPU every new prompt count compiles a new graph, ~0.8 s that a waiting
                # click would sit behind.
                padded = chunk + [chunk[-1]] * (REGION_CHUNK - len(chunk))
                masks, scores = decode(input_points=[[[p] for p in padded]], input_labels=[[[1] for _ in padded]])
                candidates += [(masks[i, k], float(scores[i, k])) for i in range(len(chunk)) for k in range(masks.shape[1])]
                between_chunks()  # let a waiting click go first
        kept = refine(candidates, obj_crop)
        regions = [{"polygons": mask_to_polygons(r["mask"], offset=(x0, y0), min_area=6)[:1], "score": r["score"], "frac": r["frac"]}
                   for r in kept]
        return {"regions": [r for r in regions if r["polygons"]], "crop": [x0, y0, x1, y1],
                "ms": round((time.perf_counter() - t0) * 1000, 1)}

    def automask(self, req: dict, between_chunks=lambda: None) -> dict:
        """Every distinct thing in the disc as a mask, from a grid of point prompts plus the
        detected spots (gpu_worker/automask.py; pointing option "masks"). Runs on the heavy
        worker, serving waiting clicks and tracking between chunks."""
        from gpu_worker.automask import GRID, MAX_MASKS, WORK_SIDE, disc_mask, disc_points, label_point, passes, select

        torch = self.torch
        F = torch.nn.functional
        t0 = time.perf_counter()
        image = cv2.imdecode(np.frombuffer(req["jpeg"], np.uint8), cv2.IMREAD_COLOR)
        h, w = image.shape[:2]
        disc = req.get("disc")  # [cx, cy, r] in frame px
        if disc:
            cx, cy, r = disc
            x0, y0 = max(0, int(cx - r)), max(0, int(cy - r))
            x1, y1 = min(w, int(np.ceil(cx + r))), min(h, int(np.ceil(cy + r)))
        else:
            x0, y0, x1, y1 = 0, 0, w, h
        crop = cv2.cvtColor(image[y0:y1, x0:x1], cv2.COLOR_BGR2RGB)
        ch, cw = crop.shape[:2]
        local = (disc[0] - x0, disc[1] - y0, disc[2]) if disc else None
        points = disc_points(cw, ch, local, int(req.get("grid", GRID)))
        for sx, sy in req.get("points") or []:  # the detected spots: small things between grid points
            lx, ly = sx - x0, sy - y0
            if 0 <= lx < cw and 0 <= ly < ch and (local is None or np.hypot(lx - local[0], ly - local[1]) <= local[2] * 0.92):
                points.append([float(lx), float(ly)])
        processor, model = self.point_model("edgetam")
        embeddings, _ = self.embed("edgetam", ("auto", req["frame_id"], x0, y0, x1, y1), crop)  # type: ignore[arg-type]
        between_chunks()
        k = WORK_SIDE / max(ch, cw)
        wh, ww = max(1, round(ch * k)), max(1, round(cw * k))
        valid = disc_mask((wh, ww), (local[0] * k, local[1] * k, local[2] * k) if local else None)
        valid_t = torch.from_numpy(valid).to(self.device)
        valid_area = int(valid.sum())
        candidates = []
        for start in range(0, len(points), AUTO_CHUNK):
            chunk = points[start:start + AUTO_CHUNK]
            padded = chunk + [chunk[-1]] * (AUTO_CHUNK - len(chunk))  # one prompt count: one compiled graph
            enc = processor(original_sizes=[[ch, cw]], input_points=[[[p] for p in padded]],
                            input_labels=[[[1] for _ in padded]], return_tensors="pt").to(self.device)
            with torch.inference_mode():
                out = model(image_embeddings=embeddings, input_points=enc.get("input_points"),
                            input_labels=enc.get("input_labels"), multimask_output=True)
                n = len(chunk)
                logits = out.pred_masks[0, :n].reshape(n * 3, 1, *out.pred_masks.shape[-2:])
                up = F.interpolate(logits.float(), (wh, ww), mode="bilinear", align_corners=False)[:, 0]
                hard = up > 0
                area = hard.sum((1, 2))
                inside = (hard & valid_t).sum((1, 2))
                stability = (up > 1).sum((1, 2)).float() / (up > -1).sum((1, 2)).clamp(min=1).float()
                scores = out.iou_scores[0, :n].reshape(-1).float()
            area, inside, stability, scores = (t.cpu().numpy() for t in (area, inside, stability, scores))
            good = [i for i in range(n * 3) if passes(int(area[i]), int(inside[i]), valid_area, float(scores[i]), float(stability[i]))]
            if good:
                masks = (hard[good] & valid_t).cpu().numpy()
                candidates += [{"mask": masks[j], "score": float(scores[i]), "stability": float(stability[i])}
                               for j, i in enumerate(good)]
            between_chunks()  # let a waiting click (or a tracking step) go first
        kept = select(candidates, int(req.get("cap", MAX_MASKS)))
        regions = []
        for region in kept:
            polys = mask_to_polygons(region["mask"], min_area=4, epsilon_frac=0.006)[:2]
            if not polys:
                continue
            lx, ly, inner = label_point(region["mask"], [o["mask"] for o in kept if o is not region])
            ys, xs = np.nonzero(region["mask"])
            by0, by1, bx0, bx1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
            bits = region["mask"][by0:by1, bx0:bx1]
            regions.append({
                "polygons": [[[x / k + x0, y / k + y0] for x, y in poly] for poly in polys],
                "label": [lx / k + x0, ly / k + y0], "inner": round(inner / k, 1),
                "score": round(region["score"], 3), "stability": round(region["stability"], 3),
                "area": int(region["mask"].sum() / (k * k)),
                # The mask itself, as EdgeTAM produced it at the working size (evidence: measured as it
                # is, never redrawn): bits in its box, the box's corner in frame px, frame px per bit.
                "mask": {"bits": np.packbits(bits).tobytes(), "shape": [int(bits.shape[0]), int(bits.shape[1])],
                         "x0": float(bx0 / k + x0), "y0": float(by0 / k + y0), "scale": float(1 / k)},
            })
        return {"regions": regions, "prompts": len(points), "candidates": len(candidates),
                "ms": round((time.perf_counter() - t0) * 1000, 1)}

    def track(self, req: dict) -> dict:
        """Follow outlined objects with EdgeTAM's video model (gpu_worker/objects.py). `reset`:
        the follower lost track of what this session holds (a failed or timed-out step): start
        afresh, forgetting every object, before this step's `start`."""
        objects = self.objects()
        if req.get("reset"):
            objects.reset()
        lane = getattr(self, "lane", None)
        if lane is not None and lane.how == "all":
            lane.wait()  # a step is one network's run: it starts after a click another worker is answering
        out = objects.step(req["jpeg"], req.get("start"), req.get("stop"))
        return {**out, "session": req.get("session"), "seq": req.get("seq")}

    def objects(self):
        """EdgeTAM's video model and the follower's session (gpu_worker/objects.py), loaded on first use."""
        if getattr(self, "_objects", None) is None:
            with self.loading("edgetam-video"):
                from .objects import ObjectSession

                self._objects = ObjectSession(str(self.device))
        return self._objects

    def embedder(self, name: str):
        """Option EMBED's model (gpu_worker/embed.py), loaded on first use."""
        from .embed import embedder

        if name not in self._embedders:
            failed = self._embed_failed.get(name)
            if failed is not None and time.time() - failed[0] < EMBED_RETRY_S:
                raise RuntimeError(failed[1])  # the same reason at once, not another load (the M3.5 review)
            try:
                with self.loading(name):
                    self._embedders[name] = embedder(name, str(self.device))
            except Exception as exc:
                from .model_files import NotDownloaded

                if not isinstance(exc, NotDownloaded):
                    # Not downloaded is said at once and costs no load, so it is never held: once Download publishes
                    # the files the next call loads them (the catalog audit's G6: held 10 minutes, the card said Ready).
                    self._embed_failed[name] = (time.time(), str(exc))
                raise
            self._embed_failed.pop(name, None)
        return self._embedders[name]

    def embed_crops(self, req: dict) -> dict:
        """Vectors of outlined things' crops, by option EMBED's model (gpu_worker/embed.py)."""
        from .embed import DEFAULT

        return self.embedder(req.get("model") or DEFAULT).embed_jpegs(req["jpegs"])

    def zeroshot_model(self):
        if self._zeroshot is None:
            with self.loading("medsiglip"):
                from .zeroshot import ZeroShot

                self._zeroshot = ZeroShot(str(self.device))
        return self._zeroshot

    def zeroshot(self, req: dict) -> dict:
        """MedSigLIP's ranking of a few names for each crop (gpu_worker/zeroshot.py)."""
        return self.zeroshot_model().rank_jpegs(req["jpegs"], req.get("names") or [], req.get("template") or "{}")

    def molmo(self):
        """Molmo 2 4B (gpu_worker/molmo.py), loaded and warmed on first use."""
        if self._molmo is None:
            with self.loading("molmo2-4b"):
                from .molmo import Molmo

                self._molmo = Molmo(str(self.device) if self.device.type == "mps" else "cpu",
                                    "float16" if self.device.type == "mps" else "float32")
                blank = np.zeros((64, 64, 3), np.uint8)
                t0 = time.perf_counter()
                self._molmo.point(("warm",), blank, "Point to the dot.")
                self._molmo.warm_ms = round((time.perf_counter() - t0) * 1000)
        return self._molmo

    def molmo_load(self, req: dict) -> dict:
        molmo = self.molmo()
        return {"loadMs": molmo.load_ms, "warmMs": molmo.warm_ms, "memoryGb": molmo.memory_gb()}

    def molmo_read(self, req: dict) -> dict:
        from .molmo import view_of

        image, _offset = view_of(req)
        return self.molmo().read((req["frame_id"], tuple(req.get("crop") or ())), image)

    def molmo_point(self, req: dict, between=lambda: None) -> dict:
        """Molmo's points for `query` on the frame (cut to `crop`), in frame pixels."""
        from .molmo import view_of

        image, (ox, oy) = view_of(req)
        out = self.molmo().point((req["frame_id"], tuple(req.get("crop") or ())), image, req["query"], between=between)
        return {**out, "points": [[x + ox, y + oy] for x, y in out["points"]]}

    def cell_finder(self, option: str, weights: str | None = None):
        """The cell finder of a CELLS option (or a gated outline model), loaded on first use.
        Their weights are non-commercial: their code lives outside the core and is reached only
        through the licence router (orchestrator/licences.py). Fine-tuned weights are loaded from the
        file the call names; one Cellpose-SAM network (the base or one fine-tune version) is kept at a time."""
        key = option if weights is None else f"{option}@{weights}"
        if key not in self._cells:
            from orchestrator import licences

            component, cls = CELL_FINDERS[option]
            module = licences.adapter(component)
            made = getattr(module, cls)
            if option in CELLPOSE_FAMILY:
                # One Cellpose-SAM network in memory at a time (about 1.2 GB each): the base or one fine-tune version.
                for other in {k.split("@", 1)[0] for k in self._cells if k.split("@", 1)[0] in CELLPOSE_FAMILY and k != key}:
                    self.unload(other)
                for other in [k for k in self._cells if k.split("@", 1)[0] in CELLPOSE_FAMILY and k != key]:
                    self._cells.pop(other)  # another fine-tune version of the same option
            # micro-sam starts its own Python and loads its model there, within its own start limit (STARTUP_S).
            holder: dict = {}
            with self.loading(option, limit=getattr(module, "STARTUP_S", None),
                              child=lambda: getattr(holder.get("finder"), "pid", None)):
                finder = made(self.device, weights=weights) if weights is not None else made(self.device)
                holder["finder"] = finder
                lane = getattr(self, "lane", None)
                if lane is not None and option in CELLPOSE_FAMILY:
                    lane.hook(getattr(getattr(finder, "model", None), "net", None))  # each tile waits for clicks
                start = getattr(finder, "load", None)
                if start is not None:
                    start(option)
            self._cells[key] = finder
        return self._cells[key]

    def _free_gpu(self) -> None:
        import gc

        gc.collect()
        empty = getattr(getattr(self.torch, "mps", None), "empty_cache", None)
        if empty is not None and self.device.type == "mps":
            empty()

    def loadtest(self, req: dict) -> dict:
        """For the sim harness (scenario `model-load`): hold this worker as a model load does, `sleep` s
        waiting (as on the disk), then `seconds` more on as many threads as a load may use here (hashing
        releases the GIL, so each thread is a core's work), at this worker's priority."""
        import hashlib

        threads = int(req.get("spin") or thread_cap() or os.cpu_count() or 1)
        t0 = time.perf_counter()
        with self.loading("test-load"):
            time.sleep(float(req.get("sleep") or 0.0))
            until = time.monotonic() + float(req.get("seconds") or 5.0)
            block = b"\0" * (1 << 20)

            def spin() -> None:
                while time.monotonic() < until:
                    hashlib.sha256(block).digest()

            spinners = [threading.Thread(target=spin, daemon=True) for _ in range(threads)]
            for t in spinners:
                t.start()
            for t in spinners:
                t.join()
        self.held.pop("test-load", None)
        return {"threads": threads, "ms": round((time.perf_counter() - t0) * 1000, 1)}

    def cells(self, req: dict) -> dict:
        option = req.get("model") or DEFAULT_CELLS
        if option not in CELL_FINDERS:
            raise ValueError(f"unknown cell finder {option!r}")
        if option in TUNED_CELLS and not req.get("weights"):
            raise ValueError(f"{option} needs the fine-tuned weights file to load (orchestrator/finetuned.py)")
        return self.cell_finder(option, req.get("weights") if option in TUNED_CELLS else None).outline(req)


def _log(message: str) -> None:
    import sys

    print(f"[gpu-worker {time.strftime('%H:%M:%S')}] {message}", file=sys.stderr, flush=True)


def child_memory_mb(pid: int) -> float:
    """What a process the worker started holds (MB): its physical footprint (micro-sam's Python), else its resident
    memory."""
    from .footprint import footprint_mb

    now = footprint_mb(int(pid))
    if now is not None:
        return now
    try:
        import subprocess

        done = subprocess.run(["ps", "-o", "rss=", "-p", str(int(pid))], capture_output=True, text=True, timeout=2)
        return float(done.stdout.strip() or 0) / 1024
    except Exception:
        return 0.0


def thread_cap() -> int | None:
    """The CPU threads this worker may use (gpu_worker/boot.py sets it by role), or None for no cap."""
    try:
        return int(os.environ["MICROMIND_GPU_THREADS"])
    except (KeyError, ValueError):
        return None


URGENT_OPS = {"ping", "segment"}  # a person is waiting on these (clicks)

# The components each op runs on (orchestrator/licences.py). The worker is the second gate
# behind the orchestrator's: a request for something this edition may not use is refused.
SEGMENT_MODELS = {"edgetam": ("edgetam",), "sam2.1-tiny": ("sam2.1",), "sam3.1-local": ("sam3.1",),
                  "micro-sam-lm": ("micro-sam-lm",)}
# Cell finders (option CELLS) and gated outline models: option -> (component, class in its adapter).
CELL_FINDERS = {"cellpose-sam": ("cellpose-sam", "CellposeCells"), "micro-sam-lm": ("micro-sam-lm", "MicroSamCells"),
                "cellpose-sam-tuned": ("cellpose-sam-tuned", "CellposeTunedCells"),
                # A Cellpose model the user added and turned on (orchestrator/mine.py): its checked weights, by path.
                "cellpose-file": ("cellpose-sam-yours", "CellposeTunedCells")}
TUNED_CELLS = {"cellpose-sam-tuned", "cellpose-file"}  # loaded from the weights file each call names
CELLPOSE_FAMILY = {"cellpose-sam", "cellpose-sam-tuned", "cellpose-file"}  # one of them loaded at a time (cell_finder)
DEFAULT_CELLS = "cellpose-sam"
GATED_SEGMENT = {"micro-sam-lm"}  # outline models reached through the licence router
EMBED_MODELS = {"dinov2-small": ("dinov2-small",), "bioclip-2": ("bioclip-2",), "dinov3-small": ("dinov3-small",)}
EMBED_RETRY_S = 600.0  # a picture model that failed to load is not tried again for this long (each try stalls the worker)
OP_COMPONENTS = {
    "ping": lambda req: (),
    "warmup": lambda req: ("edgetam",),
    "segment": lambda req: SEGMENT_MODELS.get(req.get("model") or DEFAULT_MODEL, ()),
    "regions": lambda req: ("edgetam",),
    "automask": lambda req: ("edgetam",),
    "cells": lambda req: CELL_FINDERS.get(req.get("model") or DEFAULT_CELLS, ("cellpose-sam",))[:1],
    "track": lambda req: ("edgetam",),
    "embed": lambda req: EMBED_MODELS.get(req.get("model") or "dinov2-small", ()),
    "zeroshot": lambda req: ("medsiglip",),
    "molmo_load": lambda req: ("molmo2-4b",),
    "molmo_read": lambda req: ("molmo2-4b",),
    "molmo_point": lambda req: ("molmo2-4b",),
    "loadtest": lambda req: (),
    "load": lambda req: MODEL_COMPONENTS.get(req.get("model"), ()),
    "unload": lambda req: (),
}
# The components a model loads (op `load`): an outline model, a cell finder, a picture model, or one of the fixed ones.
MODEL_COMPONENTS = {**SEGMENT_MODELS, **{k: v[:1] for k, v in CELL_FINDERS.items()}, **EMBED_MODELS,
                    "edgetam-video": ("edgetam",), "medsiglip": ("medsiglip",), "molmo2-4b": ("molmo2-4b",)}


def licence_refusal(req: dict) -> dict | None:
    """The reply for a request this edition may not run, or None when it may."""
    from orchestrator import licences

    components = OP_COMPONENTS.get(req.get("op"), lambda r: ())(req)
    if licences.permitted(components):
        return None
    return {"ok": False, "licence": True, "error": licences.why_not(components)}


def execution_facts(models, req: dict) -> dict:
    """Facts from this dispatch, only when the caller requested an inspection receipt.
    Never load a model or look up a revision for instrumentation. Hosted/local weight
    revision remains unverified unless the already-loaded model exposes its commit."""
    op, name = req["op"], req.get("model")
    facts = {"provider": "local", "device": str(models.device), "scope": op,
             "identity": "adapter-dispatch; weight revision unverified"}
    if op == "segment" and (name or DEFAULT_MODEL) in POINT_MODELS:
        name = name or DEFAULT_MODEL
        facts.update(adapter="gpu_worker.worker.Models.segment", model=POINT_MODELS[name], role="outline", optionId=name)
        loaded = getattr(models, "_point", {}).get(name)
        commit = getattr(getattr(loaded[1], "config", None), "_commit_hash", None) if loaded else None
        if commit:
            facts["identity"] = "loaded-commit:" + str(commit)[:64]
    elif op == "segment" and name == "sam3.1-local":
        facts.update(adapter="gpu_worker.worker.Models.segment_sam31", model="facebook/sam3.1/sam3.1_multiplex.pt",
                     role="outline", optionId=name)
    elif op in {"segment", "cells"}:
        name = name or DEFAULT_CELLS
        component = CELL_FINDERS.get(name)
        if component is None:
            return {}
        loaded = getattr(models, "_cells", {})
        finder = loaded.get(name if name not in TUNED_CELLS else f"{name}@{req.get('weights')}")
        cls = type(finder) if finder is not None else None
        if name in TUNED_CELLS and req.get("version"):
            facts["weightsVersion"] = req["version"]  # the fine-tune's version the call named (finetuned.py)
        facts.update(adapter=cls.__module__ + "." + cls.__qualname__ if cls else "gpu_worker.worker.Models.cell_finder", model=name,
                     role="cells" if op == "cells" else "outline", optionId=name)
        if name == "micro-sam-lm":
            # Read configuration only from the adapter already loaded by licences.adapter().
            # Core modules never import a gated implementation just to describe it.
            module = sys.modules.get(cls.__module__) if cls else None
            config = getattr(module, "MODELS", {}).get(name)
            if config:
                facts["model"] = "micro-sam/" + config[0]
    elif op == "track":
        from .objects import REPO
        facts.update(adapter="gpu_worker.objects.ObjectSession", model=REPO, selection="fixed")
    elif op in {"regions", "automask"}:
        facts.update(adapter="gpu_worker.worker.Models." + op, model=POINT_MODELS[DEFAULT_MODEL], selection="fixed",
                     role="outline", optionId=DEFAULT_MODEL)
    elif op == "embed":
        from .embed import BIOCLIP, DEFAULT, DINOV3_MODEL, MODEL
        weights = {"bioclip-2": BIOCLIP, "dinov3-small": DINOV3_MODEL}.get(name or DEFAULT, MODEL)  # the M3.5 review
        facts.update(adapter="gpu_worker.embed", model=weights,
                     selected=name or DEFAULT, role="embed", optionId=name or DEFAULT)
    elif op == "zeroshot":
        from .zeroshot import MODEL
        facts.update(adapter="gpu_worker.zeroshot", model=MODEL, selection="fixed")
    elif op.startswith("molmo_"):
        from .molmo import MODEL
        facts.update(adapter="gpu_worker.molmo", model=MODEL, selection="fixed")
    else:
        return {}
    return facts


def serve(conn, role: str | None = None) -> None:
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    t0 = time.perf_counter()
    models = Models()
    _log(f"torch ready on {models.device} in {time.perf_counter() - t0:.1f}s")
    from .lane import opened

    lane = models.lane = opened(role)  # MICROMIND_CLICK_LANE (gpu_worker/lane.py); None: as before

    # A reader thread files requests by priority class (gpu_worker/inbox.py: explicit clicks first,
    # the newest first; then other short work; then background work) and notes drops.
    from .inbox import Inbox

    inbox = Inbox(URGENT_OPS)

    def reader() -> None:
        while True:
            try:
                req = conn.recv()
            except (EOFError, OSError):
                req = None
            if lane is not None and req is not None and req.get("op") != "drop" and inbox.klass(req) == 0:
                req["_lane"] = True
                lane.enter()  # a click is being answered here: until its reply is sent (handle)
            inbox.put(req)
            if req is None:
                return

    threading.Thread(target=reader, daemon=True, name="gpu-reader").start()
    stopping = False
    current: list = []  # the requests being handled (one inside another between chunks): a load is told for the innermost

    def notify(message: dict) -> None:
        conn.send({"id": current[-1] if current else 0, **message})

    models.notify = notify

    def handle(req: dict) -> None:
        current.append(req.get("id"))
        try:
            _handle(req)
        finally:
            current.pop()
            if req.get("_lane"):
                lane.leave()

    def _handle(req: dict) -> None:
        reply = {"id": req.get("id")}
        refused = inbox.refusal(req)
        if refused is not None:
            conn.send(refused)
            return
        conn.send({"id": req.get("id"), "started": True})  # the client's hang check counts from here
        waited = lane.waited_s if lane is not None else 0.0
        try:
            op = req["op"]
            refusal = licence_refusal(req)
            if refusal is not None:
                reply.update(refusal)
            elif op == "ping":
                reply.update(ok=True, device=str(models.device))
            elif op == "keys":  # HF_TOKEN added, replaced or removed in the app (key_admin.py): gated downloads read it
                from orchestrator.keys import apply_values

                apply_values(req.get("set") or {}, req.get("unset") or [])
                reply.update(ok=True)
            elif op == "warmup":
                t0 = time.perf_counter()
                models.point_model()
                _log(f"point model loaded in {time.perf_counter() - t0:.1f}s")
                blank = np.zeros((720, 1280, 3), np.uint8)
                ok, jpeg = cv2.imencode(".jpg", blank)
                t0 = time.perf_counter()
                models.segment({"frame_id": -1, "jpeg": jpeg.tobytes(), "points": [[640, 360]]})
                _log(f"point warm-up {time.perf_counter() - t0:.1f}s")
                t0 = time.perf_counter()
                models.segment({"frame_id": -1, "jpeg": jpeg.tobytes(), "box": [600, 320, 680, 400]})
                _log(f"box warm-up {time.perf_counter() - t0:.1f}s")
                t0 = time.perf_counter()
                disc = [[[640 + 60 * np.cos(a), 360 + 40 * np.sin(a)] for a in np.linspace(0, 2 * np.pi, 24)]]
                models.regions({"frame_id": -1, "jpeg": jpeg.tobytes(), "polygons": disc})
                _log(f"regions warm-up {time.perf_counter() - t0:.1f}s")
                reply.update(ok=True)
            elif op == "segment":
                reply.update(ok=True, **models.segment(req))
            elif op == "regions":
                reply.update(ok=True, **models.regions(req, between_chunks=serve_urgent))
            elif op == "automask":
                # On the heavy worker: a tracking step waiting behind it goes between chunks too.
                reply.update(ok=True, **models.automask(req, between_chunks=lambda: serve_urgent(also=("track",))))
            elif op == "cells":
                reply.update(ok=True, **models.cells(req))
            elif op == "track":
                reply.update(ok=True, **models.track(req))
            elif op == "embed":
                reply.update(ok=True, **models.embed_crops(req))
            elif op == "zeroshot":
                reply.update(ok=True, **models.zeroshot(req))
            elif op == "molmo_load":
                reply.update(ok=True, **models.molmo_load(req))
            elif op == "molmo_read":
                reply.update(ok=True, **models.molmo_read(req))
            elif op == "molmo_point":
                # Its own worker, so nothing waits behind it; a click sent here anyway goes between words.
                reply.update(ok=True, **models.molmo_point(req, between=serve_urgent))
            elif op == "loadtest":
                reply.update(ok=True, **models.loadtest(req))
            elif op == "load":
                reply.update(ok=True, **models.load(req))
            elif op == "unload":
                reply.update(ok=True, **models.unload(str(req.get("model"))))
            else:
                reply.update(ok=False, error=f"unknown op {op}")
        except Exception as exc:
            reply.update(ok=False, error=f"{type(exc).__name__}: {exc}", trace=traceback.format_exc()[-1500:])
        if lane is not None and lane.waited_s > waited:
            reply["laneWaitedMs"] = round((lane.waited_s - waited) * 1000, 1)  # how long it gave way to clicks
        diagnostics = reply.pop("_clickDiagnostics", {})
        if req.get("_execution") and reply.get("ok"):
            try:
                reply["_execution"] = {**execution_facts(models, req), **diagnostics}
            except Exception:
                pass  # instrumentation cannot turn a successful dispatch into a failure
        conn.send(reply)

    def serve_urgent(also: tuple[str, ...] = ()) -> None:
        """Between chunks of a long op: answer clicks and other short work (or an op in `also`)."""
        nonlocal stopping
        waiting = []
        while True:
            try:
                cls, req = inbox.get_nowait()
            except queue.Empty:
                break
            if req is None:
                stopping = True
                continue
            if cls <= 1 or req.get("op") in also:
                handle(req)
            else:
                waiting.append((cls, req))
        for cls, req in waiting:
            inbox.put_back(cls, req)

    while not stopping:
        req = inbox.get()
        if req is None:
            return
        handle(req)
