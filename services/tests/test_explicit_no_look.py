"""Explicit model no-look decisions survive empty prose, without word rules.

Real fast and spoken paths use synthetic provider replies. Missing needs_look is
covered separately; two spoken requests must not consume image providers merely
because this line-formatting fixture has no assistant prose.
"""
import asyncio

import pytest

from orchestrator.brain.dispatch import answer_phase
from orchestrator.brain.fast import decide
from orchestrator.brain.speech import SpokenRequests
from orchestrator.ticket import issue, using
from tests.test_brain_dispatch_budget import admitted, response, run, world


@pytest.mark.parametrize("intent", ["statement", "filler", "question", "request"])
@pytest.mark.parametrize("decision", ["explicit-no-look", "missing"])
def test_fast_preserves_explicit_no_look_separately_from_missing(tmp_path, monkeypatch, intent, decision):
    async def script(entry):
        return {"intent": intent, "answer": "", "actions": [],
                **({"needs_look": False} if decision == "explicit-no-look" else {})}

    brain, hub, providers, _ = world(tmp_path, monkeypatch, script, fast=True)
    request = issue(hub, "hands")

    async def go():
        with using(request), answer_phase("hands"):
            return await decide(brain.moment(), "Unchanged arbitrary request text.", brain.agent.vision(hub.settings.fast),
                                brain.registry.prompt_section(), hub.settings.fast)

    try:
        result = run(go())
        assert result.intent == intent and result.answer == "" and result.actions == []
        assert result.needs_look is (decision == "missing"), "empty prose overrode the model's explicit picture decision"
        assert admitted(providers).used == 1 and not providers.sent[0].images
    finally:
        request.seal()


@pytest.mark.parametrize("intent", ["statement", "filler", "question", "request"])
def test_two_spoken_no_look_requests_do_not_start_image_providers(tmp_path, monkeypatch, intent):
    from orchestrator.brain import speech

    monkeypatch.setattr(speech, "JOIN_PIECES_S", 0.001)
    monkeypatch.setattr(speech, "POINT_AFTER_S", 30)  # no unrelated voice-pointing timer in this transcript test

    async def script(entry):
        if entry.images:
            return response("Unexpected synthetic image response.")  # expose the dispatch, never pop an empty fixture
        return {"intent": intent, "needs_look": False, "answer": "", "actions": []}

    brain, _, providers, _ = world(tmp_path, monkeypatch, script, fast=True)
    heard = SpokenRequests(brain)

    async def go():
        heard.heard("First arbitrary words.")
        await asyncio.sleep(0.03)
        heard.heard("Second arbitrary words.")
        await asyncio.sleep(0.03)
        assert len(heard.requests) == 2, "both distinct requests must reach hands"
        # A settled request keeps its identity and judgment; its hands task is read from its own record.
        tasks = [request.work.get("hands") for request in heard.requests]
        await asyncio.gather(*(task for task in tasks if task is not None))
        await asyncio.sleep(0)
        assert len(providers.sent) == 2, "explicit no-look speech dispatched an unsolicited picture provider"
        assert all(not entry.images for entry in providers.sent)
        budgets = [entry.admission for entry in providers.sent]
        assert budgets[0] is not budgets[1]  # two requests, never a renewed allowance for one request
        assert all(budget.used == 1 and budget.attempts[0].phase == "hands" for budget in budgets)
        heard.close()

    run(go())
