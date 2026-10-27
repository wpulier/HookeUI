"""Synthetic provider-boundary regressions over the real brain and agent paths.

No admission, loop, fast-pass, speech-handoff or tool runner is mocked. Only camera
bytes, provider responses and optional diagnostic output are stand-ins. These do
not establish model quality, actual voice behavior or browser rendering.
"""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator.agent import Agent
from orchestrator.brain.admission import RequestAdmission
from orchestrator.brain.loop import Brain
from orchestrator.brain.registry import CURRENT_RUN, Tool, ToolFailed, schema
from orchestrator.brain.speech import SpokenRequests
from orchestrator.brain.toolbox import Services, build
from orchestrator.screen import screen_of
from orchestrator.ticket import current, issue, using
from orchestrator.vision import VisionResult
from tests.fakes import Hub, conversation
from tests.test_capture import frames_hub
from tests.waiting import HANG_S


MODEL = "gemini-3.5-flash"
COMPARE = "gemini-3.8-flash"
QUESTION = "Read the retained cell count and open its original picture."
FACT = "The saved population e42 revision 3 has 19 members."
FAILURE = "The original picture is unavailable."
TRUTH = f"{FACT} {FAILURE}"


def response(answer="Checking the saved result.", *actions):
    return {"answer": answer, "targets": [], "actions": [
        {"tool": name, "args": args} for name, args in actions
    ]}


def run(coro):
    async def bounded():
        return await asyncio.wait_for(coro, HANG_S)
    return asyncio.run(bounded())


class Providers:
    """Each entry is an actual invocation of the provider's ask boundary."""
    def __init__(self, script):
        self.script, self.sent = script, []

    def model(self, model):
        async def ask(images, prompt, max_output_tokens=1500):
            request = current()
            admission = getattr(request, "admission", None)
            entry = SimpleNamespace(
                number=len(self.sent) + 1, model=model, images=images, prompt=prompt,
                request=request, admission=admission, used=getattr(admission, "used", None),
                tool=CURRENT_RUN.get(), status="running",
                fast="only your notes about it" in prompt,
            )
            self.sent.append(entry)
            try:
                data = await self.script(entry)
            except asyncio.CancelledError:
                entry.status = "canceled"
                raise
            except BaseException:
                entry.status = "failed"
                raise
            entry.status = "done"
            return VisionResult(data=data, latency_ms=1, model=model)
        return SimpleNamespace(ask=ask)


def world(tmp_path, monkeypatch, script, *, fast=False, tools=(), mode="json-actions"):
    hub = Hub()
    # A real retained Capture and Agent.snapshot/encoding over a tiny synthetic frame.
    hub.__dict__.update(frames_hub(n=1, size=(64, 64)).__dict__)
    hub.settings.vision = MODEL
    hub.settings.fast = MODEL if fast else "off"
    hub.settings.brain = mode
    hub.settings.compare = ""
    hub.settings.closeup = "single"
    memory = conversation(tmp_path / "day")
    agent = Agent(hub, memory, interaction_log=tmp_path / "interactions.jsonl")
    providers = Providers(script)
    agent._visions = {name: providers.model(name) for name in (MODEL, COMPARE)}
    # Optional logging is not the essential request ledger being exercised.
    logged = []
    monkeypatch.setattr(agent, "log", logged.append)
    monkeypatch.setattr(agent, "keep_view", lambda *args: None)
    monkeypatch.setattr(agent, "available", lambda *args: True)
    if mode == "fc":
        async def gemini(model, level, jpeg, prompt, registry):
            reply = await providers.model(MODEL).ask([jpeg], prompt)
            return reply.data, {"model": MODEL, "latency_ms": reply.latency_ms, "marks": None}
        monkeypatch.setattr(agent.fc, "_gemini", gemini)
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")  # the provider faked below its key check (agent.key_needed)
    services = Services(hub=hub, agent=agent)
    brain = Brain(hub, agent, build(services), memory)
    brain.services = services
    for tool in tools:
        brain.registry.add(tool)
    return brain, hub, providers, logged


def admitted(providers):
    assert providers.sent, "the scenario never reached its provider boundary"
    budget = providers.sent[0].admission
    assert isinstance(budget, RequestAdmission), "actual provider dispatch has no request-owned admission"
    assert all(item.admission is budget for item in providers.sent), "handoff or late work renewed the budget"
    assert len(providers.sent) <= 4, "a fifth actual answer-provider invocation was dispatched"
    if len(providers.sent) == 4:  # the answer's last word (admission.reserve_last_word), nothing else
        assert budget.attempts[3].phase == "final", "a fourth call that is not the answer's last word"
    assert budget.used == len(providers.sent)
    assert [item.used for item in providers.sent] == list(range(1, len(providers.sent) + 1))
    return budget


async def entered_speculative_turn(brain, text, channel="chat", *, delegation_id=None):
    """Trusted caller carries real already-entered work, never a fabricated attempt."""
    asked = brain.conversation.here()
    request = issue(brain.hub, "turn", asked=asked, channel=channel)
    look = None
    try:
        with using(request):
            look = brain.start_look(text)
            decision = await brain.quick(text)
            return await brain.turn(text, channel, delegation_id=delegation_id, asked=asked,
                                    request_ticket=request,
                                    handled={"ticket": request, "look": look, "decision": decision,
                                             "runs": [], "ctx": None, "later": []})
    finally:
        # Only unconsumed setup work is ours to drop. A used pending look must be
        # canceled by Brain.turn itself, so cleanup cannot satisfy its cancel oracle.
        if look is not None and not look.used:
            look.drop()
            await asyncio.gather(look.task, return_exceptions=True)
        request.seal()


def receipt_tools(contexts, *, release=None, inline_ms=int(HANG_S * 1000)):
    """The turn waits for these steps up to `inline_ms`: by default until only a hang would be later, so a step that
    answers at once is answered inline however loaded the Mac is (a worker descheduled for 10 ms ran out a 5 ms window,
    and the turn rightly deferred its answer). A test of the late path holds its step with `release` and gives a short
    window."""
    async def read(ctx, args):
        contexts.append(ctx)
        if release is not None:
            await release.wait()
        return FACT

    async def missing(ctx, args):
        contexts.append(ctx)
        raise ToolFailed(FAILURE)

    return [Tool("read_receipt", "Read saved evidence", schema({}), read, kind="query", inline_ms=inline_ms),
            Tool("missing_picture", "Open its original", schema({}), missing, inline_ms=inline_ms)]


@pytest.mark.parametrize("mode", ["json-actions", "fc"])
def test_one_call_typed_answer_is_counted_at_the_real_provider_boundary(tmp_path, monkeypatch, mode):
    async def script(entry):
        return response("This view alone does not establish a cell type.")

    brain, _, providers, _ = world(tmp_path, monkeypatch, script, mode=mode)
    result = run(brain.turn("What can you tell from this picture?"))
    assert result["answer"] == "This view alone does not establish a cell type."
    assert len(providers.sent) == result["calls"] == 1
    assert admitted(providers).remaining == 2


@pytest.mark.parametrize("fast_error", [False, True], ids=["fast-look", "failed-fast-look"])
def test_fast_speculative_look_queries_and_final_answer_share_max_three(tmp_path, monkeypatch, fast_error):
    contexts = []

    async def go():
        looking = asyncio.Event()

        async def script(entry):
            if entry.fast:
                await looking.wait()  # both calls really dispatched; no canceled-before-send ambiguity
                if fast_error:
                    raise TimeoutError("scripted fast provider timeout")
                return {"intent": "request", "needs_look": True, "actions": []}
            if entry.images:
                looking.set()
                return response("I have opened everything.", ("read_receipt", {}), ("missing_picture", {}))
            return response(TRUTH)

        brain, _, providers, _ = world(tmp_path, monkeypatch, script, fast=True, tools=receipt_tools(contexts))
        result = await entered_speculative_turn(brain, QUESTION)
        assert len(providers.sent) <= 3, "fast plus look was counted as one; query/final escaped the cap"
        budget = admitted(providers)
        assert result["calls"] == len(providers.sent)
        assert contexts and all(getattr(ctx, "admission", None) is budget for ctx in contexts)
        # A step failed with only the outcome call left: it is the outcome round over the receipts and the notes,
        # which says what happened (or would add steps for what is unfinished).
        assert "19" in result["answer"] and "unavailable" in result["answer"].lower()
        assert "opened everything" not in result["answer"]
        assert any(action["ok"] is False for action in result["actions"])
        if fast_error:
            assert sum(attempt.status == "failed" for attempt in budget.attempts) == 1

    run(go())


def test_canceled_speculative_provider_still_counts_beside_the_fast_answer(tmp_path, monkeypatch):
    async def go():
        looking, canceled = asyncio.Event(), asyncio.Event()

        async def script(entry):
            if entry.fast:
                await looking.wait()
                return {"intent": "question", "needs_look": False, "answer": "The saved note has no scale.", "actions": []}
            looking.set()
            try:
                await asyncio.Event().wait()
            finally:
                canceled.set()

        brain, _, providers, _ = world(tmp_path, monkeypatch, script, fast=True)
        result = await entered_speculative_turn(brain, "Does the saved note provide a scale?")
        await canceled.wait()
        assert result["answer"] == "The saved note has no scale."
        assert result["calls"] == len(providers.sent) == 2
        budget = admitted(providers)
        assert sorted(a.status for a in budget.attempts) == ["canceled", "done"]

    run(go())


def test_canceling_a_turn_before_it_runs_never_enters_a_provider(tmp_path, monkeypatch):
    async def script(entry):
        pytest.fail("canceled-before-dispatch turn reached a provider")

    brain, _, providers, _ = world(tmp_path, monkeypatch, script)

    async def go():
        task = asyncio.create_task(brain.turn(QUESTION))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(go())
    assert providers.sent == []


def test_canceling_a_dispatched_turn_retains_its_consumed_attempt(tmp_path, monkeypatch):
    async def go():
        started = asyncio.Event()

        async def script(entry):
            started.set()
            await asyncio.Event().wait()

        brain, _, providers, _ = world(tmp_path, monkeypatch, script)
        task = asyncio.create_task(brain.turn(QUESTION))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        budget = admitted(providers)
        assert budget.used == 1 and budget.attempts[0].status == "canceled"

    run(go())


@pytest.mark.parametrize("replay", [False, True], ids=["handoff", "same-delegation-replay"])
def test_hands_early_look_and_voice_handoff_keep_one_admission(tmp_path, monkeypatch, replay):
    contexts = []

    async def go():
        hands_entered, looking = asyncio.Event(), asyncio.Event()

        async def script(entry):
            if entry.fast:
                hands_entered.set()
                await looking.wait()
                return {"intent": "request", "needs_look": True, "actions": []}
            if entry.images:
                looking.set()
                return response("Reading the retained count.", ("read_receipt", {}))
            return response(FACT)

        brain, hub, providers, _ = world(tmp_path, monkeypatch, script, fast=True, tools=receipt_tools(contexts))
        hub.settings.speculate = "on"
        spoken = SpokenRequests(brain)
        hands = spoken.hands(QUESTION)
        try:
            await hands_entered.wait()
            first = await spoken.handoff(QUESTION, delegation_id="delegation-42")
            assert "19" in first["answer"]
            budget = admitted(providers)
            assert all(getattr(ctx, "admission", None) is budget for ctx in contexts)
            assert len({entry.request.id for entry in providers.sent}) >= 2  # actual hands + handoff tickets
            before = len(providers.sent)
            if replay:
                again = await spoken.handoff(QUESTION, delegation_id="delegation-42")
                assert again == first
                assert len(providers.sent) == before, "same delegation replay dispatched a fresh answer"
                assert len(contexts) == 1, "replayed handoff repeated the query"
        finally:
            spoken.close()
            if not hands.done():
                hands.cancel()
            await asyncio.gather(hands, return_exceptions=True)

    run(go())


def test_late_query_and_failed_sibling_keep_the_original_admission_and_failures(tmp_path, monkeypatch):
    contexts = []

    async def go():
        looking, release, answered = asyncio.Event(), asyncio.Event(), asyncio.Event()
        delivered = []

        async def script(entry):
            if entry.fast:
                await looking.wait()
                return {"intent": "request", "needs_look": True, "actions": []}
            if entry.images:
                looking.set()
                return response("Reading it.", ("missing_picture", {}), ("read_receipt", {}))
            return response(TRUTH)

        brain, _, providers, logs = world(tmp_path, monkeypatch, script, fast=True,
                                         tools=receipt_tools(contexts, release=release, inline_ms=5))

        async def late(ctx, result):
            if result.answer:
                delivered.append((ctx, result))
                answered.set()

        brain.report_late = late
        result = await entered_speculative_turn(brain, QUESTION, "voice", delegation_id="late-42")
        assert not release.is_set() and any(a["ok"] is None for a in result["actions"])
        release.set()
        await answered.wait()
        assert len(providers.sent) <= 3, "late work used a copied permission after the real budget was spent"
        budget = admitted(providers)
        ctx, final = delivered[0]
        assert getattr(ctx, "admission", None) is budget
        assert (ctx.turn_id, ctx.delegation_id) == (result["turnId"], "late-42")
        assert "19" in final.answer and "unavailable" in final.answer.lower()
        assert len(delivered) == 1
        assert [log for log in logs if log.get("kind") == "query-answer"][-1]["calls"] == budget.used

    run(go())


def test_a_failed_step_gets_the_recovery_call_when_only_the_outcome_call_is_left(tmp_path, monkeypatch):
    """Live 2026-10-10 20:33: hands and the look spent two calls, the third was held for rewording the answer,
    and the failed zoom was never recovered. Recovery gets that call; the receipts say what happened."""
    contexts = []

    async def script(entry):
        if entry.fast:
            return {"intent": "request", "needs_look": True, "actions": []}
        if "Results of every step already attempted" in entry.prompt:
            return response("Reading the saved result instead.", ("read_receipt", {}))
        return response("Opening the original picture.", ("missing_picture", {}))

    brain, _, providers, _ = world(tmp_path, monkeypatch, script, fast=True, tools=receipt_tools(contexts))
    result = run(entered_speculative_turn(brain, QUESTION))
    budget = admitted(providers)
    tools = [(a["tool"], a["ok"]) for a in result["actions"]]
    assert ("missing_picture", False) in tools and ("read_receipt", True) in tools  # recovered, failure kept
    assert tools.index(("missing_picture", False)) < tools.index(("read_receipt", True))
    # Recovery used the third call; what it read went back to the model in the answer's own last word.
    assert budget.used == 4 and [a.phase for a in budget.attempts][-1] == "final"


@pytest.mark.parametrize("error", [RuntimeError("provider failed"), TimeoutError("provider timed out")],
                         ids=["failure", "timeout"])
def test_failed_final_provider_uses_supported_facts_and_retains_failed_effect(tmp_path, monkeypatch, error):
    contexts = []

    async def script(entry):
        if entry.images:
            return response("I opened the original picture.", ("read_receipt", {}), ("missing_picture", {}))
        raise error

    brain, _, providers, _ = world(tmp_path, monkeypatch, script, tools=receipt_tools(contexts))
    result = run(brain.turn(QUESTION))
    budget = admitted(providers)
    assert result["calls"] == len(providers.sent) <= 3
    assert any(a.status == "failed" for a in budget.attempts)
    assert [(a['tool'], a['ok'], a['summary']) for a in result['actions']] == [
        ('read_receipt', True, FACT), ('missing_picture', False, FAILURE)] * 2
    assert contexts and all(ctx.admission is budget for ctx in contexts)
    assert all(a['requestId'] == budget.request_id for a in result['actions'])
    assert len({a['actionId'] for a in result['actions']}) == 4
    final = [entry for entry in providers.sent if budget.attempts[entry.number - 1].phase == 'final']
    assert len(final) == 1 and final[0].status == 'failed'
    assert FACT in final[0].prompt and FAILURE in final[0].prompt
    assert all(a['actionId'] in final[0].prompt for a in result['actions'])
    assert result['answer'] == '' and result['answerStatus'] == 'not-published'
    assert not any(t.role == 'assistant' for t in brain.conversation.turns)
    assert "I opened" not in result["answer"]
    assert not result["answer"].startswith("Completed step results")


def test_closer_look_rounds_are_actual_dispatches_not_one_agent_look(tmp_path, monkeypatch):
    contexts = []

    async def script(entry):
        if entry.number <= 2:
            return {**response("Looking closer."), "closer": [200, 200, 800, 800]}
        return response("I opened the original picture.", ("read_receipt", {}), ("missing_picture", {}))

    brain, hub, providers, _ = world(tmp_path, monkeypatch, script, tools=receipt_tools(contexts))
    hub.settings.closeup = "naming+answers"
    result = run(brain.turn(QUESTION))
    assert len(providers.sent) <= 3, "answer_closer made uncounted inner answer calls"
    budget = admitted(providers)
    assert result["calls"] == budget.used
    assert result["answer"].strip() and "I opened" not in result["answer"]


def test_comparison_answer_uses_the_same_request_budget(tmp_path, monkeypatch):
    contexts = []

    async def script(entry):
        if entry.model == COMPARE:
            return response("The second model cannot infer a physical scale.")
        if entry.images:
            return response("Reading the count.", ("read_receipt", {}))
        return response(FACT)

    brain, hub, providers, _ = world(tmp_path, monkeypatch, script, tools=receipt_tools(contexts))
    hub.settings.compare = COMPARE

    async def go():
        result = await brain.turn(QUESTION)
        if result.get("compare") is not None:
            await result["compare"]
        assert len(providers.sent) <= 3, "comparison was exempted from answer admission"
        admitted(providers)

    run(go())


def test_workspace_lookup_acquires_pixels_without_an_inner_answer_provider(tmp_path, monkeypatch):
    async def script(entry):
        if entry.number == 1:
            return response("Inspecting the workspace.", ("look_at_screen", {"question": "What is shown?"}))
        return response("The workspace picture has one visible pane.")

    brain, hub, providers, _ = world(tmp_path, monkeypatch, script)
    screen = screen_of(hub)
    screen.report({"visible": True, "viewport": [800, 600], "items": []})

    async def acquired(*args, **kwargs):
        return hub.ring[1]  # explicitly synthetic workspace bytes, not real browser evidence

    monkeypatch.setattr(screen, "look", acquired)
    result = run(brain.turn("Inspect the workspace layout."))
    assert result["answer"].strip()
    assert not any(entry.tool is not None for entry in providers.sent), "lookup tool dispatched its own hidden answer model"
    admitted(providers)
