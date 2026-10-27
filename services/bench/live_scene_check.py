"""End-to-end voice check on a running camera bridge (simulated or live).

Runs the production pieces in one process: Hub (frames, motion, candidates, scene, GPU
worker), voice.scene_tools.Locator + feed_frames, and GeminiLiveSession with the settings in
recommended_config below (the Gemini Live settings measured in docs/bench/live.md; the app's
own voice is GPT-Live, orchestrator/voice/route.py). Speaks questions with macOS `say` into the session (real-time
PCM, as a microphone would) and reports latencies, transcripts and what landed in the scene.

  # bridge on a private port, playing a recording (the simulated iPhone):
  python3 experiments/iphone-camera-feed/scripts/iphone_bridge_server.py --port 18865 \\
      --simulate /path/to/your-recording.mp4
  cd services && .venv/bin/python -m bench.live_scene_check --bridge-url http://127.0.0.1:18865
"""
from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
from pathlib import Path

from bench.live_probe import Mic, tts
from orchestrator.hub import Hub
from orchestrator.keys import load_keys
from orchestrator.vision import GeminiVision
from orchestrator.prompts import LIVE_VOICE_SYSTEM
from orchestrator.voice.base import VoiceConfig, VoiceEvent
from orchestrator.voice.gemini_live import GeminiLiveSession
from orchestrator.voice.scene_tools import Locator, feed_frames

def recommended_config(model: str, tools: list) -> VoiceConfig:
    """Gemini Live as measured in docs/bench/live.md (it was voice/route.py's before GPT-Live
    became the app's voice)."""
    extra: dict = {}
    if "extended-thinking" in model:
        extra["thinking_level"] = "low"  # required by this model; "minimal" is rejected
    else:
        extra["thinking_budget"] = 0  # thinking_level is rejected; budget 0 cuts ~0.3-0.4 s
    return VoiceConfig(model=model, system_prompt=LIVE_VOICE_SYSTEM, tools=tools, silence_ms=300, extra=extra)


QUESTIONS = ["What's the little dark dot near the top?", "What is the oval shape in the middle?"]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bridge-url", default="http://127.0.0.1:18865")
    parser.add_argument("--model", default="gemini-3.8-live")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    load_keys()

    hub = Hub(args.bridge_url, use_gpu=True)
    await hub.start()
    t0 = time.monotonic()
    while time.monotonic() - t0 < 90 and not (hub.latest_info and hub.gpu and hub.gpu.ready):
        await asyncio.sleep(0.5)
    if not (hub.latest_info and hub.gpu.ready):
        raise SystemExit(f"hub not ready: {hub.health()}")
    print(f"hub ready in {time.monotonic() - t0:.1f}s: {hub.source.fps():.0f} fps")

    locator = Locator(hub, GeminiVision("gemini-3.8-flash", thinking_level="low"))
    session = GeminiLiveSession(recommended_config(args.model, [locator.spec()]))
    events: list[VoiceEvent] = []

    def on_event(event: VoiceEvent) -> None:
        events.append(event)
        if event.kind == "input_transcript" and not event.data.get("interim"):
            locator.speculate(event.text)

    session.on(on_event)
    await session.start()
    mic = Mic(session)
    tasks = [asyncio.create_task(mic.run()), asyncio.create_task(feed_frames(hub, session))]
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        speech = {q: tts(q, Path(tmp)) for q in QUESTIONS}
    try:
        await asyncio.sleep(3)
        for question in QUESTIONS:
            before_objects = set(hub.scene.objects)
            start = len(events)
            mic.say(speech[question])
            await mic.done.wait()
            end = mic.speech_end_at
            new_obj_at = None
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                await asyncio.sleep(0.05)
                if new_obj_at is None and set(hub.scene.objects) - before_objects:
                    new_obj_at = time.monotonic()
                ev = events[start:]
                kinds = [e.kind for e in ev]
                if "tool_result" in kinds:
                    after = kinds[kinds.index("tool_result"):]
                    if "audio" in after and "turn_complete" in after[after.index("audio"):]:
                        break
            ev = events[start:]
            rel = lambda t: None if t is None else round((t - end) * 1000)  # noqa: E731
            first = lambda k: next((e for e in ev if e.kind == k), None)  # noqa: E731
            result = first("tool_result")
            new = [hub.scene.objects[i] for i in set(hub.scene.objects) - before_objects]
            row = {
                "question": question,
                "heard": "".join(e.text for e in ev if e.kind == "input_transcript" and not e.data.get("interim")).strip(),
                "transcriptMs": rel(next((e.at for e in ev if e.kind == "input_transcript" and not e.data.get("interim")), None)),
                "firstAudioMs": rel(first("audio").at if first("audio") else None),
                "toolCallMs": rel(first("tool_call").at if first("tool_call") else None),
                "outlineInSceneMs": rel(new_obj_at),
                "toolResult": result.result if result else None,
                "said": "".join(e.text for e in ev if e.kind == "output_transcript"),
                "sceneObjects": [{"id": o.id, "label": o.label, "status": o.status, "bbox": [round(v) for v in o.bbox], "frame": o.anchor_frame, "note": o.note} for o in new],
            }
            rows.append(row)
            print(json.dumps(row, indent=1))
            await asyncio.sleep(2)
    finally:
        for task in tasks:
            task.cancel()
        await session.close()
        await hub.stop()
    if args.out:
        Path(args.out).write_text(json.dumps({"when": time.strftime("%Y-%m-%d %H:%M"), "bridge": args.bridge_url, "rows": rows}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
