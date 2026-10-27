"""Naming a click end to end (Agent.identify) with a stand-in vision model: live demo
2026-10-06, every click failed with "name 'conf' is not defined" in the log line after naming,
and nothing ran this path (the sim does not call cloud naming; the acting bench fakes the agent)."""
import asyncio
from types import SimpleNamespace

import cv2
import numpy as np

from orchestrator.agent import Agent
from orchestrator.scene import SceneObject
from tests.fakes import Hub


def test_a_click_is_named_and_logged(tmp_path):
    hub = Hub()
    image = np.full((720, 1280, 3), 200, np.uint8)
    ok, jpeg = cv2.imencode(".jpg", image)
    hub.infos = {7: hub.latest_info}
    hub.source = SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=jpeg.tobytes()) if fid == 7 else None, is_fresh=lambda: True)
    obj = hub.scene.add(SceneObject(id="o1", kind="highlight", polygons=[[[600, 300], [660, 300], [660, 360], [600, 360]]],
                                    anchor_frame=7, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="click"))
    agent = Agent(hub, interaction_log=tmp_path / "interactions.jsonl")

    async def ask(images, prompt, max_output_tokens=0):
        assert len(images) == 2  # the view with the outline, and its close-up
        return SimpleNamespace(data={"observations": ["round", "thick wall"], "label": "parenchyma cell",
                                     "answer": "A thin-walled storage cell.", "confidence": 0.92},
                               model="stand-in", latency_ms=5.0, error=None)

    agent.vision = lambda _o: SimpleNamespace(ask=ask)
    logged = []
    agent.log = logged.append
    agent.keep_image = lambda *a, **k: None
    out = asyncio.run(agent.identify(obj.id))
    assert out["label"] == hub.scene.objects["o1"].label and out["label"]
    record = next(r for r in logged if r["kind"] == "identify")
    assert record["confidence"] == hub.scene.objects["o1"].confidence
