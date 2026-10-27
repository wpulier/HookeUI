"""EARLY_LOOK_TYPED (Slice L): when a typed request's look starts.

"sequential" (since ee959f12): the fast pass decides first and the look starts only when it needs one.
"alongside": the look starts beside the fast pass, as a spoken request's early look does; it is used when the fast
pass needs it and gave no steps, and otherwise dropped before anything else is sent and refunded to the request
once (admission.forgive), so the steps, query rounds and answer keep every call they had (ee959f12's
acquired-inputs path included). Real brain, admission, fast pass, runner and tools; only provider replies are
scripted (tests/test_brain_dispatch_budget.py's world)."""
import asyncio

import pytest

from tests.test_brain_dispatch_budget import FACT, receipt_tools, response, run, world
from tests.waiting import HANG_S

QUESTION = "What is the oval on the right?"


def timeline(script):
    """Provider entries and returns in order: ("enter"|"return", "fast"|"look"|"query")."""
    events = []

    async def traced(entry):
        kind = "fast" if entry.fast else "look" if entry.images else "query"
        events.append(("enter", kind))
        try:
            return await script(entry, kind)
        finally:
            events.append(("return", kind))

    return traced, events


@pytest.mark.parametrize("option", ["sequential", "alongside"])
def test_when_the_fast_pass_needs_a_look_it_answers_and_alongside_starts_it_at_once(tmp_path, monkeypatch, option):
    async def script(entry, kind):
        if kind == "fast":
            await asyncio.sleep(0.05)  # the fast pass takes a moment: the look either waits for it or runs beside it
            return {"intent": "question", "needs_look": True, "actions": []}
        return response("That oval is a stoma.")

    traced, events = timeline(script)
    brain, hub, providers, logged = world(tmp_path, monkeypatch, traced, fast=True)
    hub.settings.early_look_typed = option
    result = run(brain.turn(QUESTION))
    assert result["answer"] == "That oval is a stoma."
    look_entered = events.index(("enter", "look"))
    fast_returned = events.index(("return", "fast"))
    if option == "sequential":
        assert look_entered > fast_returned  # the look waits for the fast pass's decision
        assert not [r for r in logged if r.get("kind") == "early-look"]
    else:
        assert look_entered < fast_returned  # started beside it
        early = [r for r in logged if r.get("kind") == "early-look"]
        assert len(early) == 1 and early[0]["used"] is True and early[0]["forgiven"] is False
    budget = providers.sent[0].admission
    assert budget.used == len(providers.sent) == 2  # the fast pass and the one look, either way


@pytest.mark.parametrize("option", ["sequential", "alongside"])
def test_when_the_notes_answer_the_look_is_never_kept_and_alongside_refunds_it(tmp_path, monkeypatch, option):
    looking = asyncio.Event()

    async def script(entry, kind):
        if kind == "fast":
            if option == "alongside":
                await looking.wait()  # the early look really entered the provider before the notes answered
            return {"intent": "question", "needs_look": False, "answer": "It has no scale yet.", "actions": []}
        looking.set()
        await asyncio.Event().wait()  # a look that never returns: only cancelling ends it

    traced, events = timeline(script)
    brain, hub, providers, logged = world(tmp_path, monkeypatch, traced, fast=True)
    hub.settings.early_look_typed = option
    result = run(brain.turn("Does it have a scale?"))
    assert result["answer"] == "It has no scale yet."
    budget = providers.sent[0].admission
    if option == "sequential":
        assert [e.fast for e in providers.sent] == [True]
        assert budget.used == 1
    else:
        assert sorted(a.status for a in budget.attempts) == ["canceled", "done"]
        assert budget.used == 1 and budget.entered == 2  # entered, then refunded once
        early = [r for r in logged if r.get("kind") == "early-look"]
        assert len(early) == 1 and early[0]["used"] is False and early[0]["entered"] and early[0]["forgiven"]
        discarded = [r for r in logged if r.get("kind") == "look-discarded"]
        assert len(discarded) == 1 and discarded[0]["status"] == "canceled"
    assert result["calls"] == budget.used


@pytest.mark.parametrize("option", ["sequential", "alongside"])
def test_a_plan_from_the_notes_keeps_its_query_round_either_way(tmp_path, monkeypatch, option):
    """The fast pass gives a query: its results go back to a model in this request's next call. The early look is
    dropped and refunded before the query runs, so the query round and the answer have the calls they have with
    "sequential" (ee959f12: an entered look must never take them)."""
    contexts = []
    looking = asyncio.Event()

    async def script(entry, kind):
        if kind == "fast":
            if option == "alongside":
                await looking.wait()
            return {"intent": "request", "needs_look": False, "answer": "Reading the saved count.",
                    "actions": [{"tool": "read_receipt", "args": {}}]}
        if kind == "look":
            looking.set()
            await asyncio.Event().wait()
        return response(FACT)

    traced, events = timeline(script)
    brain, hub, providers, logged = world(tmp_path, monkeypatch, traced, fast=True, tools=receipt_tools(contexts))
    hub.settings.early_look_typed = option
    result = run(brain.turn("Read the retained cell count."))
    budget = providers.sent[0].admission
    assert "19" in result["answer"]
    assert contexts and all(ctx.admission is budget for ctx in contexts)
    kept = [a.phase for a in budget.attempts if a.status != "canceled"]
    assert kept[0] == "fast" and "query" in kept and "look" not in kept
    if option == "alongside":
        assert [a.phase for a in budget.attempts if a.status == "canceled"] == ["look"]
        assert budget.used == budget.entered - 1  # the look refunded: the steps' rounds kept their calls
        early = [r for r in logged if r.get("kind") == "early-look"]
        assert len(early) == 1 and early[0]["forgiven"] is True
    else:
        assert budget.used == budget.entered
    # Either way the same calls are spent on the request's own work.
    assert budget.used == len(kept)


def test_the_fast_pass_failing_leaves_the_early_look_to_answer(tmp_path, monkeypatch):
    async def script(entry, kind):
        if kind == "fast":
            raise TimeoutError("scripted fast provider timeout")
        return response("That oval is a stoma.")

    traced, events = timeline(script)
    brain, hub, providers, logged = world(tmp_path, monkeypatch, traced, fast=True)
    hub.settings.early_look_typed = "alongside"
    result = run(brain.turn(QUESTION))
    assert result["answer"] == "That oval is a stoma."
    early = [r for r in logged if r.get("kind") == "early-look"]
    assert len(early) == 1 and early[0]["used"] is True and early[0]["fast"] is None


def test_a_turn_cancelled_while_the_fast_pass_decides_takes_its_early_look_with_it(tmp_path, monkeypatch):
    looking, cancelled = asyncio.Event(), asyncio.Event()

    async def script(entry, kind):
        if kind == "look":
            looking.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        await asyncio.Event().wait()  # the fast pass never answers

    traced, events = timeline(script)
    brain, hub, providers, logged = world(tmp_path, monkeypatch, traced, fast=True)
    hub.settings.early_look_typed = "alongside"

    async def go():
        task = asyncio.create_task(brain.turn(QUESTION))
        await looking.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(cancelled.wait(), HANG_S)

    run(go())


def test_alongside_keeps_the_acquired_inputs_path_through_the_app(tmp_path, monkeypatch):
    """ee959f12 through the actual App: the fast pass selects a retained picture (resources inspect=true) while the
    camera was also wanted. With the look started beside it, that look is dropped and refunded before the inspection
    runs, so the query round answers from the retained picture with the same calls, and still says the camera part
    was not supplied."""
    from tests.test_mixed_camera_retained_context import (CURRENT_PARTIAL_ANSWER, acquired, fast_reply, intake_camera,
                                                          retained_action, send)
    from tests.test_resource_inspection import CURRENT_CAMERA_UNSUPPLIED, actual_scheduler_fixture

    actions, box = [], {}

    def reply(sent):
        if sent.phase == "fast":
            return fast_reply(actions, needs_camera=True)
        if sent.phase == "look":  # the early look: whatever it says is never used
            return {"answer": "UNUSED EARLY LOOK", "targets": [], "actions": []}
        assert len(sent.images) == 1 and CURRENT_CAMERA_UNSUPPLIED in sent.prompt
        return {"answer": CURRENT_PARTIAL_ANSWER, "actions": [], "observation_scope": "retained-source", "input_refs": [1]}

    traced = actual_scheduler_fixture(tmp_path, monkeypatch, reply)
    box["traced"] = traced
    rig = traced.rig
    rig.app.hub.settings.early_look_typed = "alongside"
    actions.append(retained_action(rig, inspect=True))
    async def entered_together(images, prompt, **kwargs):
        """The external provider, each send attributed to the attempt it entered as; the fast pass answers only once
        the early look has really entered its provider."""
        from types import SimpleNamespace

        from orchestrator.ticket import current

        ticket = current()
        attempt = ticket.admission.attempts[-1]  # read on entry, before any other attempt can register
        sent = SimpleNamespace(ticket=ticket, admission=ticket.admission, attempt=attempt, phase=attempt.phase,
                               images=tuple(images), prompt=prompt)
        traced.sends.append(sent)
        if attempt.phase == "look":
            box["looking"].set()
        elif attempt.phase == "fast":
            await box["looking"].wait()
        return SimpleNamespace(data=reply(sent), model="explicitly-canned-scheduler-provider", latency_ms=0.0, error=None)

    monkeypatch.setattr(rig.provider, "ask", entered_together)
    try:
        async def scenario():
            box["looking"] = asyncio.Event()
            await intake_camera(rig)
            await rig.ingest()
            result = await send(rig, text="Read the selected evidence and explain it.")
            budget = traced.sends[0].admission
            assert result["answer"] == CURRENT_PARTIAL_ANSWER and "UNUSED EARLY LOOK" not in result["answer"]
            phases = [sent.phase for sent in traced.sends]
            assert sorted(phases) == ["fast", "look", "query"]
            assert budget.used == 2 == result["calls"]  # fast and query; the early look refunded
            assert len(acquired(traced)) == 1 and traced.started[0][0].ok is True
        asyncio.run(scenario())
    finally:
        rig.close()


def test_the_option_is_a_setting_with_both_values():
    from orchestrator import options

    assert set(options.EARLY_LOOK_TYPED) == {"sequential", "alongside"}
    assert options.GROUPS["early_look_typed"] is options.EARLY_LOOK_TYPED
    settings = options.Settings()
    assert settings.to_wire()["early_look_typed"] == settings.early_look_typed
    assert settings.update({"early_look_typed": "alongside"}) == [] and settings.early_look_typed == "alongside"
    assert settings.update({"early_look_typed": "eager"}) == ["unknown early_look_typed option 'eager'"]
