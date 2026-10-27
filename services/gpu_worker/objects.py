"""Following outlined objects from frame to frame with EdgeTAM's video model (option TRACK).

Slide motion (orchestrator/motion.py) moves every outline by the slide's move, which is right for
tissue and wrong for things that move on their own (sperm, protists) or that the slide's motion
loses. EdgeTAM's video model keeps a memory of each object it was shown and finds it again in each
new frame, with a confidence score per object (its object-score logit: above 0, present).

One streaming session holds the followed objects. Each step is one frame (half resolution): new
objects are prompted with their box on that frame, every object gets its outline and score, and
objects no longer wanted are dropped. The session is started again from the latest confident boxes
every RESEED_FRAMES steps, so its memory (one entry per frame per object) stays bounded.

Used by the GPU worker's `track` op (gpu_worker/worker.py, on the heavy worker so clicks never
wait for it) and, in-process, by bench/object_tracking.py.
"""
from __future__ import annotations

import time

import cv2
import numpy as np

REPO = "yonigozlan/EdgeTAM-hf"
SCALE = 0.5  # frames are halved before the model sees them (measured ~100 ms a step for one object)
RESEED_FRAMES = 120  # steps before the session starts again from the latest confident boxes
PRESENT_LOGIT = 0.0  # EdgeTAM's object-score logit above which the object is in the frame


class ObjectSession:
    def __init__(self, device: str | None = None) -> None:
        import torch
        from transformers import EdgeTamVideoModel, Sam2VideoProcessor

        self.torch = torch
        self.device = device or ("mps" if torch.backends.mps.is_available() else "cpu")
        from .model_files import companions, model_dir

        path = model_dir("edgetam-video")  # EdgeTAM's pinned files (model_files.py), never fetched here
        companions("edgetam-video")  # its backbone config, from the same folder
        self.model = EdgeTamVideoModel.from_pretrained(path, local_files_only=True).to(self.device).eval()
        self.processor = Sam2VideoProcessor.from_pretrained(path, local_files_only=True)
        self.session = None
        self.frame_idx = 0
        self.ids: dict[str, int] = {}  # our object id -> the session's integer id
        self.boxes: dict[str, list[float]] = {}  # last confident box of each object (frame px)

    def reset(self) -> None:
        """Forget every object and the session's memory: the caller starts again from verified
        positions (objtrack's resync after a failed step)."""
        self.session = None
        self.frame_idx = 0
        self.ids = {}
        self.boxes = {}

    def _new_session(self) -> None:
        self.session = self.processor.init_video_session(inference_device=self.device, dtype=self.torch.float32)
        self.frame_idx = 0
        self.ids = {}

    def step(self, jpeg: bytes, start: list[dict] | None = None, stop: list[str] | None = None) -> dict:
        """One frame: `start` [{"id", "box": [x0, y0, x1, y1]}] (frame px) begins following those
        objects on this frame; `stop` [ids] drops objects. Returns {"objects": {id: {"polygons",
        "score", "present", "area", "box"}}, "ms"}."""
        from .worker import mask_to_polygons

        t0 = time.perf_counter()
        bgr = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        small = cv2.resize(bgr, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_AREA)
        inputs = self.processor(images=cv2.cvtColor(small, cv2.COLOR_BGR2RGB), device=self.device, return_tensors="pt")
        for oid in stop or []:
            self.boxes.pop(oid, None)
        wanted = {s["id"]: s["box"] for s in start or []}
        dropped = any(oid not in self.boxes for oid in self.ids)
        if self.session is None or dropped or self.frame_idx >= RESEED_FRAMES:
            # Start again, prompting every object still followed with its last confident box.
            wanted = {**{oid: box for oid, box in self.boxes.items()}, **wanted}
            self._new_session()
        for oid, box in wanted.items():
            self.ids.setdefault(oid, max(self.ids.values(), default=0) + 1)
            self.boxes[oid] = list(box)
        if wanted:
            # Every object prompted on this frame in one call: each call replaces the session's list
            # of objects with new inputs, and an object left off it is tracked from a memory it never
            # had ("maskmem_features in conditioning outputs cannot be empty": 363 failed steps on
            # 2026-10-06, whenever two or more objects were prompted on one frame).
            self.processor.add_inputs_to_inference_session(
                inference_session=self.session, frame_idx=self.frame_idx, obj_ids=[self.ids[oid] for oid in wanted],
                input_boxes=[[[v * SCALE for v in box] for box in wanted.values()]], original_size=inputs.original_sizes[0])
        objects: dict[str, dict] = {}
        if self.ids:
            with self.torch.inference_mode():
                output = self.model(inference_session=self.session, frame=inputs.pixel_values[0])
            masks = self.processor.post_process_masks([output.pred_masks], original_sizes=inputs.original_sizes, binarize=True)[0]
            masks = masks.reshape(-1, *masks.shape[-2:]).cpu().numpy().astype(np.uint8)
            scores = output.object_score_logits.flatten().cpu().tolist()
            by_int = {v: k for k, v in self.ids.items()}
            for n, obj_int in enumerate(output.object_ids):
                oid = by_int.get(int(obj_int))
                if oid is None or n >= len(masks):
                    continue
                mask = cv2.resize(masks[n], (bgr.shape[1], bgr.shape[0]), interpolation=cv2.INTER_NEAREST)
                ys, xs = np.nonzero(mask)
                present = scores[n] > PRESENT_LOGIT and len(xs) > 0
                box = [float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())] if len(xs) else None
                if present and box is not None:
                    self.boxes[oid] = box
                objects[oid] = {"polygons": mask_to_polygons(mask) if present else [], "score": round(float(scores[n]), 2),
                                "present": bool(present), "area": int(mask.sum()), "box": box}
            self.frame_idx += 1
        return {"objects": objects, "ms": round((time.perf_counter() - t0) * 1000, 1)}
