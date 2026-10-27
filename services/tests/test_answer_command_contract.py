"""Synthetic command entry and continuation-reservation integration regressions."""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator.brain.admission import RequestAdmission
from orchestrator.brain.registry import Tool, schema
from orchestrator.brain.speech import SpokenRequests
from orchestrator.brain.toolbox import Services
from orchestrator.brain.toolbox.time import add_time_tools
from orchestrator.commands import COMMANDS
from orchestrator.screen import screen_of
from tests.test_brain_dispatch_budget import FACT, admitted, response, run, world
from tests.test_time import filled


@pytest.mark.parametrize("name", ["what_happened", "look_at_screen", "read_receipt"])
def test_direct_tool_command_has_an_admitted_request(tmp_path, monkeypatch, name):
    async def script(entry):
        return {"answer": "A cell moved left.", "changed": True}

    brain, hub, providers, _ = world(tmp_path, monkeypatch, script)
    contexts = []

    async def read(ctx, args):
        return FACT

    brain.registry.add(Tool("read_receipt", "Read the saved count", schema({}), read, kind="query"))
    add_time_tools(brain.registry, Services(hub=hub, agent=brain.agent, clip=filled()))
    brain.registry.finished.append(lambda result, ctx: contexts.append((result, ctx)))
    screen = screen_of(hub)
    screen.report({"visible": True, "viewport": [800, 600], "items": []})

    async def acquired():
        return hub.ring[1]  # synthetic workspace composition, not actual browser evidence

    monkeypatch.setattr(screen, "look", acquired)
    app = SimpleNamespace(hub=hub, brain=brain, agent=brain.agent, conversation=brain.conversation)
    result = run(COMMANDS["tool"](app, None, {"type": "tool", "name": name, "args": {}}))
    assert result["ok"] is True, result
    assert len(contexts) == 1
    tool_run, ctx = contexts[0]
    assert isinstance(ctx.admission, RequestAdmission)
    if name == "what_happened":
        assert admitted(providers) is ctx.admission
        assert "A cell moved left" in result["summary"]
    else:
        assert not providers.sent and ctx.admission.used == 0
        if name == "look_at_screen":
            assert tool_run.images and tool_run.images[0].facts["channel"] == "workspace"
        else:
            assert result["summary"] == FACT


@pytest.mark.parametrize("timing", ["inline", "late"])
def test_query_acquisition_reuses_its_new_final_reservation(tmp_path, monkeypatch, timing):
    async def go():
        release, delivered = asyncio.Event(), asyncio.Event()
        updates = []
        interpreted = "The supplied workspace picture contains one pane."

        async def script(entry):
            if entry.number == 1:
                return {"intent": "request", "needs_look": False, "answer": "Checking the workspace.", "actions": []}
            if entry.number == 2:
                return {"intent": "request", "needs_look": False, "answer": "Acquiring the workspace.",
                        "actions": [{"tool": "look_at_screen", "args": {}}]}
            assert entry.images, "the final interpretation must receive the acquired workspace pixels"
            return response(interpreted)

        brain, hub, providers, _ = world(tmp_path, monkeypatch, script, fast=True)
        screen = screen_of(hub)
        screen.report({"visible": True, "viewport": [800, 600], "items": []})

        async def acquired():
            if timing == "late":
                await release.wait()
            return hub.ring[1]

        monkeypatch.setattr(screen, "look", acquired)
        brain.registry.tools["look_at_screen"].inline_ms = 5

        async def late(ctx, result):
            if result.answer:
                updates.append(result)
                delivered.set()

        brain.report_late = late
        hands = SpokenRequests(brain)
        await hands.hands("Inspect the workspace layout.")
        result = await hands.handoff("Inspect the workspace layout.", "acquire-42")
        if timing == "late":
            assert any(action["ok"] is None for action in result["actions"])
            release.set()
            await delivered.wait()
            assert len(updates) == 1
            answer = updates[0].answer
        else:
            answer = result["answer"]
        budget = admitted(providers)
        assert len(providers.sent) == budget.used == 3, "acquisition's reserved third slot was lost"
        assert budget.attempts[-1].phase == ("late" if timing == "late" else "final")
        assert answer == interpreted
        assert not any(entry.tool is not None for entry in providers.sent), "acquisition hid an inner answer call"

    run(go())


def test_direct_tool_command_reuses_an_existing_exhausted_request(tmp_path, monkeypatch):
    from orchestrator.brain.dispatch import AnswerProvider, answer_phase
    from orchestrator.ticket import issue, using

    async def script(entry):
        return {"answer": "A cell moved left.", "changed": True}

    brain, hub, providers, _ = world(tmp_path, monkeypatch, script)
    add_time_tools(brain.registry, Services(hub=hub, agent=brain.agent, clip=filled()))
    contexts = []
    brain.registry.finished.append(lambda result, ctx: contexts.append(ctx))
    app = SimpleNamespace(hub=hub, brain=brain, agent=brain.agent, conversation=brain.conversation)
    request = issue(hub, "turn")

    async def go():
        with using(request):
            with answer_phase("look"):
                for _ in range(3):
                    await AnswerProvider(brain.agent.vision(hub.settings.vision), hub.settings.vision).ask([], "fixture")
            result = await COMMANDS["tool"](app, None, {"type": "tool", "name": "what_happened", "args": {}})
            assert result["ok"] is False and "answer limit" in result["summary"]
            assert contexts[0].admission is request.admission
            assert admitted(providers) is request.admission and len(providers.sent) == 3

    try:
        run(go())
    finally:
        request.seal()
