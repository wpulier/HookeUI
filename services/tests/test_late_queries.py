"""A query behind slow work keeps its step references and answers when its evidence arrives."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from orchestrator.brain.registry import ACTIVITY_KEYS, CURRENT_RUN, Tool, record_made, schema
from tests.test_brain import make
from orchestrator.ticket import cancel_open, current, generations
from tests.waiting import HANG_S


@pytest.mark.parametrize("model_answers", [True, False])
@pytest.mark.parametrize("channel", ["chat", "voice"])
def test_delayed_measurements_finish_the_original_answer_without_repeating_tools(model_answers, channel):
    async def go():
        release, answered = asyncio.Event(), asyncio.Event()
        used, reports, prompts, contexts, provider_requests, provider_phases = [], [], [], [], [], []

        async def segment(ctx, args):
            used.append("segment")
            contexts.append(ctx)
            await release.wait()
            record_made(ctx, "e9")
            return "saved candidate masks e9"

        async def measure(ctx, args):
            used.append("measure")
            contexts.append(ctx)
            assert ctx.steps.produced[1] == ["e9"]
            return "e9: c1 length 92 px, area 3641 px²; c2 length 74 px, area 2363 px²; no physical scale"

        brain, _, agent = make([{"answer": "Finding and measuring the candidates.", "targets": [], "actions": [
            {"tool": "outline_cells", "args": {}}, {"tool": "query_population", "args": {"of": "@1"}}]},
            {"answer": "The other request is answered.", "targets": [], "actions": []}],
            [Tool("outline_cells", "", schema({}), segment, inline_ms=10),
             Tool("query_population", "", schema({}), measure, kind="query", inline_ms=10)])

        async def ask(images, prompt, max_output_tokens=0):
            assert not images  # the completed query supplies the facts, not a newer specimen frame
            prompts.append(prompt)
            provider_requests.append(current())
            provider_phases.append(current().admission.attempts[-1].phase)
            return SimpleNamespace(data={"answer": "The two candidates measure 92 and 74 pixels; no physical scale."}
                                   if model_answers else {}, latency_ms=1)

        async def report(ctx, run):
            if run.tool == "query_population" and any(row.get("kind") == "query-answer" and row["turnId"] == ctx.turn_id for row in agent.logged):
                reports.append((ctx.channel, ctx.delegation_id, ctx.turn_id, run.wire()))
                answered.set()

        agent.vision = lambda model: SimpleNamespace(ask=ask)
        brain.report_late = report
        result = await asyncio.wait_for(brain.turn("find and measure the two longest", channel, delegation_id="d9"), 1)
        assert result["calls"] == 1 and len(agent.calls) == 1
        assert all(a["ok"] is None for a in result["actions"])
        assert not release.is_set()  # the live reply did not wait for the slow work
        other = await asyncio.wait_for(brain.turn("an unrelated question", channel), 1)
        assert other["answer"] == "The other request is answered."
        release.set()
        await asyncio.wait_for(answered.wait(), HANG_S)
        expected_phases = ["query"] if model_answers else ["query", "final"]
        assert used == ["segment", "measure"] and len(prompts) == len(expected_phases)
        assert provider_phases == expected_phases
        assert "92 px" in prompts[0] and "no physical scale" in prompts[0]
        assert reports
        final = next((row for row in reports if row[3].get("answer")), reports[-1])
        assert final[:3] == (channel, "d9", result["turnId"])
        assert final[3]["tool"] == "query_population" and final[3]["ok"] is True
        if model_answers:
            assert final[3]["answer"] == "The two candidates measure 92 and 74 pixels; no physical scale."
        else:
            assert not final[3].get("answer")  # useful data remains separate from assistant text
        assert "3641" in final[3]["summary"]  # the original measurement receipt is preserved
        facts = json.loads(prompts[0].split('<answer-facts source="app" untrusted="true">\n', 1)[1]
                           .split('\n</answer-facts>', 1)[0])
        assert [(row["tool"], row["ok"]) for row in facts["operations"]] == [
            ("outline_cells", True), ("query_population", True)]
        assert facts["operations"][0]["summary"] == "saved candidate masks e9"
        assert facts["operations"][1]["summary"] == final[3]["summary"]
        assert all(ctx.admission is provider_requests[0].admission for ctx in contexts)
        assert all(request is provider_requests[0] for request in provider_requests)
        assert provider_requests[0].admission.used == (2 if model_answers else 3)
        assert [(a.phase, a.status) for a in provider_requests[0].admission.attempts] == [
            ("look", "done"), *((phase, "done") for phase in expected_phases)]
        assert "@step references in new actions" in prompts[0]
        if not model_answers:
            assert "Do not replay actions." in prompts[1]
            final_facts = json.loads(prompts[1].split('<answer-facts source="app" untrusted="true">\n', 1)[1]
                                    .split('\n</answer-facts>', 1)[0])
            assert final_facts["operations"] == facts["operations"]
        assert all(row["requestId"] == provider_requests[0].admission.request_id for row in facts["operations"])
        synthesis = [row for row in agent.logged if row.get("kind") == "query-answer"]
        assert len(synthesis) == 1 and synthesis[0]["answer"] == final[3].get("answer", "")

    asyncio.run(go())


def test_delayed_query_at_the_call_limit_uses_its_facts_without_an_extra_model_call():
    async def go():
        release, answered = asyncio.Event(), asyncio.Event()
        reads, reports, contexts, original_runs, finals = [], [], [], [], []

        async def read(ctx, args):
            reads.append(1)
            contexts.append(ctx)
            original_runs.append(CURRENT_RUN.get())
            # The final slot is reserved after query 1: only query 2's plan
            # can be admitted before the original late interpretation.
            if len(reads) == 2:
                await release.wait()
            return "The retained measurement is 92 px."

        again = {"answer": "Checking.", "targets": [], "actions": [{"tool": "query_population", "args": {}}]}
        brain, _, agent = make([again, again, again], [Tool("query_population", "", schema({}), read, kind="query", inline_ms=10)])

        actual_ask = agent.fast_model.ask

        async def unavailable_final(images, prompt, **kwargs):
            finals.append((current(), tuple(images), prompt))
            return await actual_ask(images, prompt, **kwargs)  # the real scripted boundary fails

        agent.fast_model.ask = unavailable_final

        async def report(ctx, run):
            if (original_runs and run.occurrence == original_runs[-1].occurrence
                    and run.step == original_runs[-1].step
                    and any(row.get("kind") == "query-answer" and row["turnId"] == ctx.turn_id for row in agent.logged)):
                reports.append((ctx, run.wire()))
                answered.set()

        brain.report_late = report
        result = await brain.turn("measure it")
        assert result["calls"] == 2 and len(agent.calls) == 2
        assert len(reads) == 2 and result["actions"][-1]["ok"] is None
        assert not finals and not agent.fast_model.prompts
        release.set()
        await asyncio.wait_for(answered.wait(), HANG_S)
        assert len(agent.calls) == 2 and len(agent.fast_model.prompts) == len(finals) == 1
        assert len(reads) == 2 and reports
        ctx, receipt = reports[-1]
        assert ctx.turn_id == result["turnId"] and ctx.channel == "chat"
        assert receipt["ok"] is True and receipt["summary"] == "The retained measurement is 92 px."
        assert not receipt.get("answer")  # no fourth send and no receipt-to-prose fallback
        assert all(context.admission is ctx.admission for context in contexts)
        assert finals[0][0].admission is ctx.admission and finals[0][1] == ()
        assert ctx.admission.used == 3 and len(ctx.admission.attempts) == 3
        assert [(attempt.phase, attempt.status) for attempt in ctx.admission.attempts] == [
            ("look", "done"), ("query", "done"), ("late", "failed")]
        assert len({run.occurrence.action_id for run in original_runs}) == 2
        for run in original_runs:
            number, steps = run.step
            assert steps.runs[number] is run and steps.occurrences[number] == run.occurrence
            assert run.occurrence.request_id == ctx.admission.request_id and run.ok is True
        facts = json.loads(finals[0][2].split('<answer-facts source="app" untrusted="true">\n', 1)[1]
                           .split('\n</answer-facts>', 1)[0])
        assert facts["operations"] == [{k: v for k, v in run.wire().items() if k not in ACTIVITY_KEYS}
                                       for run in original_runs]  # what only Activity says is never told
        synthesis = [row for row in agent.logged if row.get("kind") == "query-answer"]
        assert len(synthesis) == 1 and synthesis[0]["answer"] == ""
        assert len(synthesis[0]["actions"]) == 2 and all(row["ok"] is True for row in synthesis[0]["actions"])
        assert [(row["requestId"], row["actionId"]) for row in synthesis[0]["actions"]] == [
            (run.occurrence.request_id, run.occurrence.action_id) for run in original_runs]

    asyncio.run(go())


@pytest.mark.parametrize("supersede", ["cancel", "day"])
@pytest.mark.parametrize("during_model", [True, False])
def test_deferred_answer_respects_cancellation_and_day_ownership(supersede, during_model):
    async def go():
        release_query, model_started, release_model = asyncio.Event(), asyncio.Event(), asyncio.Event()
        requests, published, calls = [], [], []

        async def read(ctx, args):
            requests.append(current())
            await release_query.wait()
            return "retained measurement: 92 px"

        brain, hub, agent = make([{"answer": "Reading.", "actions": [{"tool": "query_population", "args": {}}]}],
                                [Tool("query_population", "", schema({}), read, kind="query", inline_ms=10)])

        async def ask(images, prompt, max_output_tokens=0):
            calls.append(prompt)
            model_started.set()
            await release_model.wait()
            return SimpleNamespace(data={"answer": "It measures 92 pixels."}, latency_ms=1)

        async def report(ctx, run):
            published.append(run.wire())

        agent.vision = lambda model: SimpleNamespace(ask=ask)
        brain.report_late = report
        await brain.turn("read the saved measurement", "chat")
        request = requests[0]
        if during_model:
            release_query.set()
            await asyncio.wait_for(model_started.wait(), HANG_S)
        if supersede == "cancel":
            assert request.id in cancel_open("chat", time.monotonic() + 1, "never mind")
        else:
            generations(hub).bump("session", why="a new day")
        release_query.set()
        release_model.set()

        async def finished():
            while not request.closed:
                await asyncio.sleep(0.001)

        await asyncio.wait_for(finished(), HANG_S)
        assert not any(r.get("answer") for r in published)
        assert len(calls) == (1 if during_model else 0)

    asyncio.run(go())


def test_late_synthesis_retains_receipts_from_earlier_inline_query_rounds():
    async def go():
        release, answered = asyncio.Event(), asyncio.Event()
        prompts = []

        async def read(ctx, args):
            if args['part'] == 'count':
                return 'saved source e42 revision 3 has 19 members'
            await release.wait()
            return 'largest member c81 area 2200 px²'

        brain, _, agent = make([
            {'answer': 'Reading the count.', 'actions': [{'tool': 'query_population', 'args': {'part': 'count'}}]},
            {'answer': 'Reading the largest.', 'actions': [{'tool': 'query_population', 'args': {'part': 'largest'}}]},
        ], [Tool('query_population', '', schema({}), read, kind='query', inline_ms=10)])

        async def ask(images, prompt, max_output_tokens=0):
            prompts.append(prompt)
            return SimpleNamespace(data={'answer': '19 members; largest c81 area 2200 square pixels.'}, latency_ms=1)

        async def report(ctx, result):
            if result.answer:
                answered.set()

        agent.vision = lambda model: SimpleNamespace(ask=ask)
        brain.report_late = report
        result = await brain.turn('Read the retained count and largest area')
        assert result['calls'] == 2 and not release.is_set()
        release.set()
        await asyncio.wait_for(answered.wait(), HANG_S)
        assert len(prompts) == 1
        assert 'saved source e42 revision 3 has 19 members' in prompts[0]
        assert 'largest member c81 area 2200 px²' in prompts[0]

    asyncio.run(go())
