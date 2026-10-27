"""Dissect: name the parts inside an outlined object (the brain's `dissect` tool).

From the object's sharpest recent frame, the GPU worker finds candidate regions inside it
(options.DISSECT: "grid", a point grid inside the object; or "vlm-boxes", where Gemini
boxes the parts it can see and EdgeTAM outlines them). The regions are numbered on a
close-up and the vision model names them, merges pieces of one part and drops noise. The
named parts become scene objects (kind "part", parent = the object) that move with it,
are drawn in the magnifier, and on the stage only while their object is selected.

At low magnification many objects have no visible inner structure (spike S3: stomata on the
June 14 footage); then nothing is invented and the reply says to zoom in.
"""
from __future__ import annotations

import asyncio
import time

import cv2
import numpy as np

from .coords import Crop, gemini_box_to_frame
from .magnifier import object_box_in, sharpest_frame
from .marks import encode_crop, render_regions
from .prompts import DISSECT_BOXES, DISSECT_NAME
from .scene import SceneObject, capture_template
from .tools import ToolError


class Dissector:
    def __init__(self, hub, agent) -> None:
        self.hub = hub
        self.agent = agent

    async def dissect(self, obj, method: str | None = None) -> dict:
        hub = self.hub
        method = method or hub.settings.dissect
        if hub.gpu is None or not hub.gpu.ready:
            raise ToolError("the outline model is still loading", next="try again in a few seconds")
        t0 = time.perf_counter()
        loop = asyncio.get_running_loop()
        best = await loop.run_in_executor(None, sharpest_frame, hub, obj, 3.0)
        if best is None:
            raise ToolError(f"I can't see the {obj.label or 'object'} steadily enough right now",
                            next="hold the slide still for a second, then ask again")
        # The chosen view's bytes and facts, taken together (magnifier.sharpest_frame), held while
        # the parts are found and named (ADR 0010).
        from .capture import captures

        capture = best[0]
        hold = captures(hub).hold(capture, "analysis", "outline", "dissecting")
        try:
            return await self._dissect(obj, method, capture, t0)
        finally:
            hold.release()

    async def _dissect(self, obj, method: str, capture, t0: float) -> dict:
        hub = self.hub
        frame_id, info, frame = capture.id, capture, capture
        image = capture.bgr()
        shift = (info.dx - obj.anchor_offset[0], info.dy - obj.anchor_offset[1])
        polygons = [[[x + shift[0], y + shift[1]] for x, y in poly] for poly in obj.polygons]
        x0, y0, x1, y1 = object_box_in(obj, info)
        pad = 0.25 * max(x1 - x0, y1 - y0)
        crop = Crop(int(max(0, x0 - pad)), int(max(0, y0 - pad)),
                    int(min(image.shape[1], x1 + pad) - max(0, x0 - pad)), int(min(image.shape[0], y1 + pad) - max(0, y0 - pad)))
        label = f' (it was identified as "{obj.label}")' if obj.label else ""
        vision = self.agent.vision(hub.settings.vision)

        names: list[dict] = []
        summary = ""
        if method == "vlm-boxes":
            clean, _ = encode_crop(image, crop, max_side=768)
            result = await vision.ask([clean], DISSECT_BOXES.format(label=label))
            data = result.data or {}
            summary = str(data.get("summary") or "")
            parts = [p for p in data.get("parts") or [] if isinstance(p, dict) and isinstance(p.get("box_2d"), list) and len(p["box_2d"]) == 4]
            if not parts:
                return await self._finish(obj, [], summary or "no distinct parts are visible at this magnification", method, t0, capture)
            boxes = [list(gemini_box_to_frame([float(v) for v in p["box_2d"]], crop)) for p in parts]
            found = await hub.gpu.call("regions", timeout=30, frame_id=frame_id, jpeg=frame.jpeg, polygons=polygons, boxes=boxes)
            regions = found["regions"]
            names = [{"n": i + 1, "name": p.get("name"), "confidence": p.get("confidence"), "what": p.get("what")}
                     for i, p in enumerate(parts[: len(regions)])]
        else:
            found = await hub.gpu.call("regions", timeout=30, frame_id=frame_id, jpeg=frame.jpeg, polygons=polygons)
            regions = found["regions"]
            if not regions:
                return await self._finish(obj, [], "no distinct parts are visible at this magnification", method, t0, capture)
            clean, marked, _ = render_regions(image, crop, [r["polygons"] for r in regions])
            result = await vision.ask([clean, marked], DISSECT_NAME.format(label=label))
            data = result.data or {}
            summary = str(data.get("summary") or "")
            names = [p for p in data.get("parts") or [] if isinstance(p, dict) and isinstance(p.get("n"), int)]
            regions = _apply_merge_drop(regions, data.get("merge"), data.get("drop"))
            names = [p for p in names if 1 <= p["n"] <= len(regions) and regions[p["n"] - 1] is not None]

        parts = []
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        for entry in names:
            region = regions[entry["n"] - 1]
            if not region or not entry.get("name"):
                continue
            conf = entry.get("confidence")
            part = SceneObject(
                id=hub.scene.new_id("p"), kind="part", polygons=region["polygons"], anchor_frame=frame_id,
                anchor_epoch=info.epoch, anchor_offset=(info.dx, info.dy), source="dissect:" + method,
                label=str(entry["name"])[:40], status="likely",
                confidence=float(conf) if isinstance(conf, (int, float)) else None,
                seen=str(entry.get("what") or "")[:200], parent=obj.id,
            )
            template = capture_template(gray, part.bbox)
            if template is not None:
                part.template, part.template_origin = template
            parts.append(part)
        return await self._finish(obj, parts, summary, method, t0, capture)

    async def _finish(self, obj, parts: list, summary: str, method: str, t0: float, capture=None) -> dict:
        scene = self.hub.scene
        ms = round((time.perf_counter() - t0) * 1000)
        if parts:
            # The parts replace the object's earlier ones through the one gate (apply.py): only if the
            # object is still there and the request may still change parts, each placed where its own
            # pixels are verified on the live view now (contract 3) and kept unverified (not drawn)
            # where they are not.
            from .apply import apply, next_for, place_outlines

            def write(placements):
                for old in [o.id for o in scene.objects.values() if o.parent == obj.id]:
                    scene.remove(old)
                for part, placement in zip(parts, placements):
                    if not placement.verified:
                        part.track, part.lost_since = "refinding", time.time()
                    scene.add(part)

            done = await apply(self.hub, write, scope="part", capture=capture, target=(obj.id, None),
                               place=(lambda: place_outlines(self.hub, capture, [p.polygons for p in parts])) if capture is not None
                               else None)
            if not done.ok:
                self.agent.log({"kind": "dissect", "method": method, "object": obj.label, "parts": [], "ms": ms,
                                "summary": summary, "notApplied": done.reason})
                raise ToolError(f"the parts were not shown: {done.reason}", next=next_for(done.reason, "ask again in a moment"))
        self.agent.log({"kind": "dissect", "method": method, "object": obj.label, "parts": [p.label for p in parts],
                        "ms": ms, "summary": summary})
        return {"parts": [p.label for p in parts], "summary": summary, "ms": ms, "method": method,
                "picture": self._picture(obj, parts, capture) if parts else None}

    def _picture(self, obj, parts: list, capture) -> bytes | None:
        """The dissected object and its named parts, drawn by the app on the real frame they were found in (as the
        studio draws them: snapshot.draw), cropped to the object: the dissected view, its own result to keep, show in
        its own window and open again (live 2026-10-11: it lived only in the magnifier, so closing the magnifier closed
        it, and its "Parts" entry had no picture to open again)."""
        if capture is None:
            return None
        from .magnifier import object_box_in
        from .snapshot import draw

        try:
            image = capture.bgr()
            x0, y0, x1, y1 = object_box_in(obj, capture)
            pad = 0.35 * max(x1 - x0, y1 - y0, 24)
            drawn = draw(image, capture, [obj, *parts], colour=getattr(self.hub.settings, "outline_color", "pink"),
                         size_of=getattr(self.hub, "size_of", None), region=(x0 - pad, y0 - pad, x1 + pad, y1 + pad),
                         frame_time=getattr(capture, "wall_at", None))
            scale = max(1.0, 480 / max(1, min(drawn.image.shape[:2])))  # a small object enlarged so its parts read
            picture = cv2.resize(drawn.image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) if scale > 1 else drawn.image
            ok, encoded = cv2.imencode(".jpg", picture, [cv2.IMWRITE_JPEG_QUALITY, 90])
            return encoded.tobytes() if ok else None
        except Exception:
            return None  # the parts stand without their picture: said by the tool


def _apply_merge_drop(regions: list[dict], merge, drop) -> list[dict | None]:
    """Regions numbered 1..n; merged groups become the first number of the group, dropped
    and merged-away numbers become None (so numbering stays stable)."""
    out: list[dict | None] = list(regions)
    for group in merge if isinstance(merge, list) else []:
        nums = [n for n in group if isinstance(n, int) and 1 <= n <= len(regions)] if isinstance(group, list) else []
        if len(nums) < 2 or out[nums[0] - 1] is None:
            continue
        first = dict(out[nums[0] - 1])
        first["polygons"] = list(first["polygons"])
        for n in nums[1:]:
            if out[n - 1] is not None:
                first["polygons"] += out[n - 1]["polygons"]
                out[n - 1] = None
        out[nums[0] - 1] = first
    for n in drop if isinstance(drop, list) else []:
        if isinstance(n, int) and 1 <= n <= len(out):
            out[n - 1] = None
    return out
