"""Real Brain.quick/admission over synthetic external provider scheduling.

The delayed reply crosses the old four-second cutoff; no planner, parser,
Ticket, timer or admission is replaced. These controls establish scheduling
and accounting, not real-model planning quality or spoken latency.
"""
import asyncio

import pytest

from orchestrator.ticket import issue, using
from tests.test_brain_dispatch_budget import MODEL, world
from tests.waiting import HANG_S


QUESTION = "Save the original view, then measure the selected outline."
PLAN = {
    "intent": "request", "needs_look": False,
    "answer": "I will save this view and measure the selected outline.",
    "actions": [
        {"tool": "snapshot", "args": {"overlays": False}},
        {"tool": "measure", "args": {"thing": "o1"}, "after_steps": [1]},
    ],
}


def test_actual_quick_accepts_a_complete_plan_that_finishes_after_four_seconds(tmp_path, monkeypatch):
    async def scenario():
        async def provider(entry):
            await asyncio.sleep(4.2)  # external provider latency, not a fake application clock
            return PLAN

        brain, hub, providers, _ = world(tmp_path, monkeypatch, provider, fast=True)
        request = issue(hub, "turn")
        try:
            with using(request):
                decision = await asyncio.wait_for(brain.quick(QUESTION), HANG_S)
            assert decision is not None, "a still-current complete plan was canceled by the old four-second cutoff"
            assert decision.answer == PLAN["answer"] and decision.needs_look is False
            assert list(decision.actions[0]) == ["snapshot", {"overlays": False}]
            assert list(decision.actions[1]) == ["measure", {"thing": "o1"}]
            assert decision.actions[1].after_steps == [1]
            assert len(providers.sent) == request.admission.used == 1
            send = providers.sent[0]
            assert send.request is request and send.admission is request.admission
            assert send.images == [] and QUESTION in send.prompt
            assert send.status == "done"
            assert [(a.phase, a.status) for a in request.admission.attempts] == [("fast", "done")]
            assert request.admission.remaining == 2
        finally:
            request.seal()
    asyncio.run(scenario())


def test_actual_quick_external_cancel_propagates_and_does_not_refund_the_entered_send(tmp_path, monkeypatch):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def provider(entry):
            entered.set()
            await release.wait()
            return PLAN

        brain, hub, providers, _ = world(tmp_path, monkeypatch, provider, fast=True)
        request = issue(hub, "turn")
        task = None
        try:
            with using(request):
                task = asyncio.create_task(brain.quick(QUESTION))
                await asyncio.wait_for(entered.wait(), HANG_S)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert len(providers.sent) == request.admission.used == 1
            assert providers.sent[0].request is request and providers.sent[0].status == "canceled"
            assert [(a.phase, a.status) for a in request.admission.attempts] == [("fast", "canceled")]
            assert request.admission.remaining == 2
            release.set()
            await asyncio.sleep(0)
            assert request.admission.used == 1 and task.cancelled()
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            request.seal()
    asyncio.run(scenario())


def test_actual_quick_provider_timeout_remains_a_failed_spent_attempt(tmp_path, monkeypatch):
    async def scenario():
        async def provider(entry):
            raise asyncio.TimeoutError("synthetic external provider deadline")

        brain, hub, providers, _ = world(tmp_path, monkeypatch, provider, fast=True)
        request = issue(hub, "turn")
        try:
            with using(request):
                decision = await brain.quick(QUESTION)
            assert decision is None
            assert len(providers.sent) == request.admission.used == 1
            assert providers.sent[0].request is request and providers.sent[0].status == "failed"
            assert [(a.phase, a.status) for a in request.admission.attempts] == [("fast", "failed")]
            assert request.admission.remaining == 2
        finally:
            request.seal()
    asyncio.run(scenario())


def test_actual_quick_called_off_request_is_refused_before_provider_entry(tmp_path, monkeypatch):
    async def scenario():
        async def provider(entry):
            return PLAN

        brain, hub, providers, _ = world(tmp_path, monkeypatch, provider, fast=True)
        request = issue(hub, "turn")
        try:
            request.cancel("user called off the original request")
            with using(request):
                decision = await brain.quick(QUESTION, model=MODEL)
            assert decision is None and providers.sent == []
            assert request.admission.used == 0 and request.admission.attempts == ()
        finally:
            request.seal()
    asyncio.run(scenario())
