"""Synthetic real-query provider guards; no actual model or browser evidence.

Source audit found direct answer providers in these three handlers. The test-only
checkpoint precedes their admission fixes so the parent can observe fresh RED.
"""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator.brain.dispatch import AnswerProvider, answer_phase
from orchestrator.ticket import issue, using
from orchestrator.vision import VisionResult
from tests.fakes import Hub


@pytest.mark.parametrize("used", [0, 3], ids=["available", "exhausted"])
@pytest.mark.parametrize("name", ["seen_before", "contrast", "what_happened"])
def test_inline_answer_queries_share_request_admission(tmp_path, monkeypatch, name, used):
    """These three query handlers contain answer-model calls, not specialist jobs."""
    from datetime import datetime
    import numpy as np
    from orchestrator.brain.registry import Registry, ToolContext
    from orchestrator.brain.toolbox import Services
    from orchestrator.brain.toolbox.memory import add_memory_tools
    from orchestrator.brain.toolbox.magnifier import add_magnifier_tools
    from orchestrator.brain.toolbox.time import add_time_tools
    from tests.test_memory_tool import day_log
    from tests.test_time import filled
    from tests.test_brain import obj

    hub, sent = Hub(), []
    request = issue(hub, "turn")

    async def ask(*args, **kwargs):
        sent.append(1)
        return VisionResult({"answer": "Synthetic query answer.", "same": [], "changed": True}, 1, "synthetic")

    image = np.full((72, 128, 3), 120, np.uint8)
    agent = SimpleNamespace(vision=lambda _: SimpleNamespace(ask=ask),
                            snapshot=lambda _: (None, image, SimpleNamespace(x=0, y=0, width=128, height=72)))
    registry = Registry()
    args = {}
    if name == "seen_before":
        import orchestrator.brain.toolbox.memory as memory
        monkeypatch.setattr(memory, "datetime", type("D", (datetime,), {
            "now": staticmethod(lambda: datetime(2026, 10, 6, 2, 0))}))
        add_memory_tools(registry, Services(hub=hub, agent=agent, history_log=day_log(tmp_path),
                                           day=SimpleNamespace(day="2026-10-06", sid="2026-10-06", date="2026-10-06")))
    elif name == "contrast":
        import cv2
        import orchestrator.screen as screen
        from orchestrator.magnifier import Magnifier

        # Synthetic camera transport supplies one coherent retained frame. The real
        # crop/comparison producers derive its dimensions and source facts themselves.
        pixels = np.full((360, 640, 3), 120, np.uint8)
        pixels[100:121, 100:121] = (50, 180, 80)
        pixels[100:121, 150:171] = (180, 70, 50)
        encoded, jpeg = cv2.imencode(".jpg", pixels)
        assert encoded
        frame = SimpleNamespace(jpeg=jpeg.tobytes())
        info = SimpleNamespace(id=7, width=640, height=360, epoch=0, dx=0.0, dy=0.0,
                               wall_at=100.0, steady=True, focus=1.0, fov=None)
        hub.infos, hub.latest_info = {7: info}, info
        hub.source = SimpleNamespace(get=lambda fid: frame if fid == 7 else None,
                                     is_fresh=lambda: True)
        first, second = obj(hub, "first"), obj(hub, "second", x=150)

        async def shown(*args, **kwargs):
            pass  # synthetic display acknowledgement; the provider boundary remains real

        monkeypatch.setattr(screen, "picture_shown", shown)
        add_magnifier_tools(registry, Services(hub=hub, agent=agent, magnifier=Magnifier(hub)))
        args = {"a": first.id, "b": second.id}
    else:
        add_time_tools(registry, Services(hub=hub, agent=agent, clip=filled()))

    async def go():
        with using(request):
            # Optional real synthetic attempts consume the request before the query.
            with answer_phase("look"):
                for _ in range(used):
                    await AnswerProvider(SimpleNamespace(ask=ask), "synthetic").ask()
            run = registry.start(name, args, ToolContext("chat", 1, 7, "Compare the source pictures."))
            await run.task
            expected = used + int(used < 3)
            assert len(sent) == expected, f"{name} made an uncounted fourth answer call"
            assert request.admission.used == expected
            if used < 3:
                assert run.ok is True and "Synthetic query answer" in run.summary, run.summary
                assert request.admission.attempts[-1].phase == "query"
            else:
                assert run.ok is False and "answer limit" in run.summary.lower(), run.summary

    try:
        asyncio.run(go())
    finally:
        request.seal()
