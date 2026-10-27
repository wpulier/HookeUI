"""seen_before: the view now beside today's earlier views; the model says which are alike, the
times come from the log (brain/toolbox/memory.py)."""
import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import cv2
import numpy as np

from orchestrator.brain.registry import ToolContext
from orchestrator.brain.toolbox import Services, build
from orchestrator.brain.toolbox.memory import earlier_views


def day_log(tmp_path, n=30):
    crops = tmp_path / "crops" / "2026-10-06"
    crops.mkdir(parents=True)
    lines = []
    for i in range(n):
        name = f"{i:03d}-sight.jpg"
        cv2.imwrite(str(crops / name), np.full((90, 160, 3), 40 + i, np.uint8))
        specimen = "leaf" if i < 10 else ("blood" if i < 20 else "sperm")
        for _ in range(2):  # two looks in each stretch: one stands for it
            lines.append({"kind": "sight", "at": f"2026-10-06T01:{i:02d}:00", "specimen": f"{specimen} {i}",
                          "image": f"crops/2026-10-06/{name}"})
    lines.append({"kind": "turn", "at": "2026-10-06T01:40:00", "image": "gpt-image-2.5-flare"})  # not a picture
    log = tmp_path / "interactions.jsonl"
    log.write_text("\n".join(json.dumps(x) for x in lines))
    return log


def test_earlier_views_are_spread_over_the_day_one_per_stretch(tmp_path):
    views = earlier_views(day_log(tmp_path), "2026-10-06", datetime(2026, 10, 6, 2, 0), most=8)
    assert len(views) == 8 and views[0]["specimen"] == "leaf 0" and views[-1]["specimen"] == "sperm 29"
    recent = earlier_views(day_log(tmp_path / "b"), "2026-10-06", datetime(2026, 10, 6, 1, 29, 30), most=50)
    assert recent[-1]["specimen"] == "sperm 28"  # the last minute is not "earlier"


def test_seen_before_says_when_from_the_log_and_what_the_model_judged(tmp_path, monkeypatch):
    log = day_log(tmp_path)
    asked = {}

    async def ask(images, prompt, max_output_tokens=0):
        asked.update(n=len(images), prompt=prompt)
        return SimpleNamespace(data={"same": [1, 99], "answer": "Yes, like the leaf earlier."}, error="")

    from tests.fakes import Hub

    image = np.full((720, 1280, 3), 120, np.uint8)
    agent = SimpleNamespace(vision=lambda o: SimpleNamespace(ask=ask),
                            snapshot=lambda frame_id=None: (None, image, SimpleNamespace(x=0, y=0, width=1280, height=720)))
    import orchestrator.brain.toolbox.memory as memory

    monkeypatch.setattr(memory, "datetime", type("D", (datetime,), {"now": staticmethod(lambda: datetime(2026, 10, 6, 2, 0))}))
    hub = Hub()
    registry = build(Services(hub=hub, agent=agent, history_log=log, day=SimpleNamespace(day="2026-10-06", sid="2026-10-06", date="2026-10-06")))

    async def go():
        from orchestrator.ticket import issue, using

        request = issue(hub, "turn")
        try:
            with using(request):
                run = registry.start("seen_before", {}, ToolContext("voice", 1, 7, "have we seen this before?"))
                await run.task
                return run
        finally:
            request.seal()

    run = asyncio.run(go())
    assert run.ok, run.summary
    assert asked["n"] == 2 and '"have we seen this before?"' in asked["prompt"]
    assert "01:00 (leaf 0)" in run.summary and "Yes, like the leaf earlier." in run.summary  # 99: no such picture, dropped
