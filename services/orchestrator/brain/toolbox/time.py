"""Time: what happened in the last minute (frames shown to the vision model in order), and
watching for something to happen (orchestrator/clip.py, orchestrator/watcher.py)."""
from __future__ import annotations

import asyncio

import cv2
import numpy as np

from ..registry import Registry, Tool, ToolContext, ToolFailed, schema

LONGEST_S = 60.0
FRAMES = 8

HAPPENED = """You are shown {n} frames of a live microscope view, oldest first, from the last {seconds:.0f} s.
{frames}
The user asks: "{question}"
Describe what changed between the frames, in order: what moved, appeared, disappeared, divided,
burst or changed shape, and roughly when (by frame). Where the notes above say the slide itself
moved between two frames, things shifting together is the slide moving, not them. Say only what
the frames show; if they are too blurry or too similar to tell, say so.
Reply with JSON only: {{"answer": "2-4 plain sentences", "changed": true or false}}"""

LOOK = """You are shown the live microscope view now. Answer this yes/no question about it:
"{question}"
Say yes only if the picture clearly shows it.
Reply with JSON only: {{"yes": true or false, "seen": "one short sentence on what you see that decides it"}}"""


def _crop(jpeg: bytes, fov, side: int = 512) -> bytes | None:
    from ...marks import encode_crop, fov_crop

    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return None
    h, w = image.shape[:2]
    out, _ = encode_crop(image, fov_crop(fov, w, h), max_side=side, jpeg_quality=85)
    return out


def frame_notes(frames) -> str:
    """What the app measured about each frame shown: when, and whether the slide moved since the one before."""
    newest = frames[-1].wall_at
    lines = []
    for i, f in enumerate(frames):
        note = f"Frame {i + 1}: {newest - f.wall_at:.0f} s ago"
        if i:
            prev = frames[i - 1]
            if f.epoch != prev.epoch:
                note += "; the slide was moved far (or the view lost) since the frame before"
            else:
                moved = float(np.hypot(f.dx - prev.dx, f.dy - prev.dy))
                note += f"; the slide moved {moved:.0f} px since the frame before" if moved >= 8 else "; slide still"
        lines.append(note + ".")
    return "\n".join(lines)


def add_time_tools(registry: Registry, services) -> None:
    hub, agent, clip, watcher = services.hub, services.agent, services.clip, services.watcher

    if clip is not None and agent is not None:
        async def what_happened(ctx: ToolContext, args: dict) -> str:
            try:
                seconds = float(args.get("seconds") or 30.0)
            except (TypeError, ValueError):
                seconds = 30.0
            seconds = max(2.0, min(LONGEST_S, seconds))
            frames = clip.window(seconds, FRAMES)
            if len(frames) < 2:
                raise ToolFailed("I have not been watching long enough yet: ask again in a few seconds",
                                 next=None)  # no next: the message says what to do
            from ...capture import reserving

            latest = hub.latest_info
            with reserving(hub, getattr(latest, "width", 0) or 0, getattr(latest, "height", 0) or 0, "look",
                           "what happened"):  # the clip's frames decoded one at a time (invariant 3)
                images = await asyncio.to_thread(lambda: [c for c in (_crop(f.jpeg, f.fov) for f in frames) if c])
            span = frames[-1].wall_at - frames[0].wall_at
            prompt = HAPPENED.format(n=len(images), seconds=span, frames=frame_notes(frames),
                                     question=ctx.question or "What just happened?")
            from ..dispatch import query_answer

            option = hub.settings.vision
            result = await query_answer(agent.vision(option), option, images, prompt, max_output_tokens=600)
            data = result.data or {}
            if not data.get("answer"):
                raise ToolFailed(f"the vision model gave no answer ({result.error or 'empty'})", next="ask again in a moment")
            return (f"What the vision model saw in {len(images)} frames over the last {span:.0f} s "
                    f"(changed: {'yes' if data.get('changed') else 'no'}): {str(data['answer'])[:600]}")

        registry.add(Tool("what_happened", "Look back over the last minute: frames from the last few seconds up to a "
                          "minute, shown to the vision model in order, say what moved, appeared, divided or changed "
                          "(\"what just happened?\", \"did you see that?\", \"what was that that swam past?\").",
                          schema({"seconds": {"type": "number", "description": "how far back (default 30, at most 60)"}}),
                          what_happened, kind="query", inline_ms=8000, family="time"))

    if watcher is not None:
        async def watch(ctx: ToolContext, args: dict) -> str:
            from ..registry import CURRENT_RUN

            asked, day = ctx.asked, services.day
            original_day = getattr(asked, "day", None)
            run = CURRENT_RUN.get()
            occurrence = run.occurrence if run is not None else None
            origin = {"delegation_id": ctx.delegation_id, "asked": asked, "channel": ctx.channel,
                      "day": original_day, "thread": getattr(asked, "thread", None),
                      "voice_owner": ctx.voice_owner if ctx.channel == "voice" else None,
                      "turn_id": ctx.turn_id or None,
                      "request_id": occurrence.request_id if occurrence is not None else ctx.request_id,
                      "action_id": occurrence.action_id if occurrence is not None else None}
            kind = str(args.get("by") or "").strip().lower()
            what = str(args.get("what") or "").strip() or "it"

            def num(key):
                try:
                    return float(args[key]) if args.get(key) is not None else None
                except (TypeError, ValueError):
                    return None

            try:
                minutes = num("minutes") or 5.0
                w = watcher.start(kind, what, below=num("below"), above=num("above"),
                                  question=str(args.get("question") or "").strip(), seconds=minutes * 60,
                                  origin=origin)
            except ValueError as exc:
                raise ToolFailed(str(exc), next=None) from exc  # no next: the message says what was wrong
            how = {"motion": "measuring what moves every few seconds", "appears": "watching for something new in the view",
                   "look": "looking every 15 s"}[w.kind]
            return f"watching for {w.what} ({how}, up to {minutes:.0f} min); I'll say when it happens"

        registry.add(Tool("watch", "Watch for something to happen and say when it does (\"tell me when they stop "
                          "moving\", \"let me know if something swims in\", \"tell me when it divides\"). by=\"motion\" "
                          "measures the share of small things moving and fires below or above a share you set; "
                          "by=\"appears\" fires when something new appears in a still view; by=\"look\" asks the vision "
                          "model a yes/no question about the view every 15 s and fires on yes.",
                          schema({"what": {"type": "string", "description": "what the user is waiting for, in their words"},
                                  "by": {"type": "string", "description": "motion | appears | look"},
                                  "below": {"type": "number", "description": "motion: fire when the moving share is below this (0-1)"},
                                  "above": {"type": "number", "description": "motion: fire when it is above this (0-1)"},
                                  "question": {"type": "string", "description": "look: the yes/no question"},
                                  "minutes": {"type": "number", "description": "give up after (default 5, at most 15)"}},
                                 ["what", "by"]), watch, inline_ms=200, family="time"))

        async def stop_watching(ctx: ToolContext, args: dict) -> str:
            stopped = watcher.stop(str(args.get("which") or "") or None)
            if not stopped:
                return "I was not watching for anything"
            return "stopped watching for " + ", ".join(w.what for w in stopped)

        registry.add(Tool("stop_watching", "Stop watching for something (or for everything).",
                          schema({"which": {"type": "string", "description": "what to stop watching for; empty: all"}}),
                          stop_watching, inline_ms=100, family="time"))

        async def watching(ctx: ToolContext, args: dict) -> str:
            return watcher.describe()

        registry.add(Tool("watching", "What the app is watching for now, and the latest measurement.", schema({}),
                          watching, kind="query", inline_ms=100, family="time"))


def motion_share(hub):
    """For the watcher: the share of followed things moving over the last 4 s, and the words."""
    from ...motiontracks import measure_clip
    from .motion import recent

    async def measure():
        frames = recent(hub, 4.0)
        if len(frames) < 10:
            return None
        reading = hub.scale_reading() if hasattr(hub, "scale_reading") else None
        from ...capture import Refused

        try:  # one frame decoded at a time, in a reservation (invariant 3); a background check, kept nowhere
            done = await asyncio.to_thread(measure_clip, hub, frames, reading.um_per_px if reading else None, "", False)
        except Refused:
            return None  # no room now: this check waits for the next
        m = done["movement"]
        if not m.counted:
            return None
        return len(m.moving) / len(m.counted), done["words"]

    return measure


def look_question(hub, agent):
    """For the watcher: a yes/no question about the view now, answered by the vision model."""
    async def ask(question: str):
        try:
            info, image, crop = agent.snapshot(None)
        except Exception:
            return None
        from ...marks import encode_crop

        jpeg, _ = await asyncio.to_thread(encode_crop, image, crop, 768, 85)
        result = await agent.vision(hub.settings.vision).ask([jpeg], LOOK.format(question=question), max_output_tokens=200)
        data = result.data or {}
        if "yes" not in data:
            return None
        return bool(data["yes"]), str(data.get("seen") or "")[:200]

    return ask
