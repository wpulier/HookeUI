"""Voice-facing scene tools and the video feed, built on the hub.

`Locator` is the `locate(description)` tool: it grounds the description on a frozen frame
with the vision model (box strategy), outlines the result in the scene with the GPU worker,
and returns a short answer for the voice model. It also supports the speculative start
measured in docs/bench/live.md: when the user's final transcript looks like a "what's
that?" question, grounding starts at once, and the model's tool call (which arrives
~0.5-1 s later) joins the job already running instead of starting a second one.

`feed_frames` pushes the eyepiece crop of the latest fresh frame to a voice session at
1 fps. Stale frames are never sent (same freshness contract as the overlay).
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from .. import tools
from ..coords import gemini_box_to_frame
from ..marks import encode_crop, fov_crop
from ..prompts import LOCATE_WITH_BOX
from .base import ToolSpec, VoiceSession

LOCATE_INTENT = re.compile(
    r"\b(what('s| is| are)|what's that|where('s| is)|which|highlight|outline|show me|point (to|at)|find|is that|that (dot|spot|thing|shape|oval|line))\b",
    re.I,
)

LOCATE_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {
            "type": "string",
            "description": "What to find, as seen: position in the view, size, shape, colour. E.g. 'small dark dot near the top'.",
        }
    },
    "required": ["description"],
}


def wants_locate(text: str) -> bool:
    return bool(LOCATE_INTENT.search(text or ""))


class Locator:
    def __init__(self, hub, vision, *, join_window_s: float = 6.0) -> None:
        self.hub = hub
        self.vision = vision  # orchestrator.vision.GeminiVision (gemini-3.8-flash, thinking "low")
        self.join_window_s = join_window_s
        self._job: asyncio.Task | None = None
        self._job_started = 0.0

    def spec(self, **overrides: Any) -> ToolSpec:
        return ToolSpec(
            name="locate",
            description="Find and outline one thing in the current microscope view.",
            parameters=LOCATE_SCHEMA,
            handler=self.handle,
            **overrides,
        )

    def speculate(self, transcript: str) -> bool:
        """Call with each final user transcript. Starts grounding if it looks like a locate
        question and no job is already running."""
        if not wants_locate(transcript) or self._recent_job():
            return False
        self._start(transcript)
        return True

    async def handle(self, args: dict) -> dict:
        if not self._recent_job():
            self._start(str(args.get("description") or ""))
        return await asyncio.shield(self._job)

    def _recent_job(self) -> bool:
        return self._job is not None and time.monotonic() - self._job_started < self.join_window_s

    def _start(self, question: str) -> None:
        self._job_started = time.monotonic()
        self._job = asyncio.create_task(self._locate(question))

    async def _locate(self, question: str) -> dict:
        from ..capture import captures

        hub = self.hub
        latest = hub.latest_info
        # The view it looks at, kept for the outline that follows the look (ADR 0010).
        info = captures(hub).get(latest.id) if latest is not None else None
        frame = info
        if info is None or not info.jpeg or not hub.source.is_fresh():
            return {"found": False, "error": "no fresh camera frame"}
        guess = tools.show_first_guess(hub, info.id, question, "voice") if hub.settings.pointing == "snap-first" else None
        from ..capture import decoding

        crop = fov_crop(info.fov, info.width, info.height)
        with decoding(hub, info, "look", owner="voice locate") as held:  # the decode is counted (invariant 3)
            clean, _scale = encode_crop(held.bgr(), crop)
        result = await self.vision.ask([clean], LOCATE_WITH_BOX.format(question=question, context=""))
        data = result.data or {}
        if result.error or not isinstance(data.get("box_2d"), list) or len(data["box_2d"]) != 4:
            tools.resolve_first_guess(hub, guess, [])
            return {"found": False, "error": result.error or "could not find it", "likely": data.get("guess")}
        box = gemini_box_to_frame(data["box_2d"], crop)
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        outlined = True
        try:
            obj = await tools.outline_at(hub, info, cx, cy, box=box, source="voice",
                                         label=str(data.get("guess") or ""), status="likely")
            tools.resolve_first_guess(hub, guess, [obj])
        except tools.ToolError:
            outlined = False
            tools.resolve_first_guess(hub, guess, [])
        return {
            "found": True,
            "outlined_on_screen": outlined,
            "likely": data.get("guess"),
            "one_line": data.get("one_line"),
            "confidence": data.get("confidence"),
        }


def view_jpeg(hub, max_side: int = 768) -> bytes | None:
    """Eyepiece crop of the latest fresh frame, for the voice model's video input."""
    from ..capture import CaptureGone, Refused, decoding

    info = hub.latest_info
    if info is None or not hub.source.is_fresh():
        return None
    try:
        with decoding(hub, info, "look", owner="the voice's view") as held:  # counted (invariant 3)
            image = held.bgr()
            if image is None:
                return None
            jpeg, _ = encode_crop(image, fov_crop(info.fov, info.width, info.height), max_side=max_side, jpeg_quality=80)
    except (CaptureGone, Refused):
        return None
    return jpeg


async def feed_frames(hub, session: VoiceSession, fps: float = 1.0) -> None:
    loop = asyncio.get_running_loop()
    while not getattr(session, "closed", False):
        jpeg = await loop.run_in_executor(None, view_jpeg, hub)
        if jpeg is not None:
            await session.send_frame(jpeg)
        await asyncio.sleep(1.0 / fps)
