"""Synthetic real-Brain controls for a successful lookup but missing interpretation.

A raw receipt remains useful after final/late model failure; it is not a completed
interpretation. Provider spies enter the actual request admission boundary.
"""
import asyncio
import json

import pytest

from orchestrator.brain.registry import CURRENT_RUN, Tool, schema
from tests.test_brain_dispatch_budget import FACT, admitted, response, run, world


@pytest.mark.parametrize("stage", ["final", "late"])
@pytest.mark.parametrize("outcome", ["failure", "timeout", "empty", "success"])
def test_successful_receipt_does_not_hide_unavailable_interpretation(tmp_path, monkeypatch, stage, outcome):
    async def go():
        release, delivered = asyncio.Event(), asyncio.Event()
        updates, contexts, runs = [], [], []
        completed = "The saved source reports 19 members; that count alone does not establish cell type."

        async def read(ctx, args):
            contexts.append(ctx)
            runs.append(CURRENT_RUN.get())
            if stage == "late":
                await release.wait()
            return FACT

        async def script(entry):
            if entry.number == 1:
                return response("Interpreting the saved evidence.", ("read_receipt", {}))
            if stage == "final" and entry.number == 2:
                return response("This count proves they are all one cell type.")
            if outcome == "failure":
                raise RuntimeError("synthetic interpretation provider failed")
            if outcome == "timeout":
                raise TimeoutError("synthetic interpretation provider timed out")
            return {} if outcome == "empty" else response(completed)

        tool = Tool("read_receipt", "Read the retained source", schema({}), read, kind="query", inline_ms=5)
        brain, _, providers, logged = world(tmp_path, monkeypatch, script, tools=[tool])

        async def late(ctx, result):
            # Both the raw late operation and its attempted interpretation can
            # report. Observe synthesis settlement, not the presence of prose.
            if any(row.get("kind") == "query-answer" and row["turnId"] == ctx.turn_id for row in logged):
                updates.append((ctx, result))
                delivered.set()

        brain.report_late = late
        result = await brain.turn("Interpret the retained count.", "voice" if stage == "late" else "chat",
                                  delegation_id="interpret-42" if stage == "late" else None)
        if stage == "late":
            assert any(action["ok"] is None for action in result["actions"])
            release.set()
            await delivered.wait()
            assert updates
            ctx, update = next((item for item in updates if item[1].answer), updates[-1])
            assert all(item[0].turn_id == result["turnId"] for item in updates)
            assert ctx.turn_id == result["turnId"] and ctx.delegation_id == "interpret-42"
            assert update.ok is True and update.summary == FACT
            answer = update.answer
        else:
            assert [(action["tool"], action["ok"]) for action in result["actions"]] == [("read_receipt", True)]
            answer = result["answer"]
        assert len(runs) == 1 and runs[0].ok is True and runs[0].summary == FACT
        receipt = runs[0].wire()
        assert receipt["requestId"] == providers.sent[0].admission.request_id and receipt["actionId"]
        if stage == "late":
            assert update.wire()["requestId"] == receipt["requestId"]
            assert update.wire()["actionId"] == receipt["actionId"]
        interpretation_inputs = providers.sent[1:] if stage == "late" else providers.sent[-1:]
        for entry in interpretation_inputs:
            packet = entry.prompt.split('<answer-facts source="app" untrusted="true">\n', 1)[1]
            facts = json.loads(packet.split('\n</answer-facts>', 1)[0])
            assert [(row["tool"], row["ok"], row["summary"]) for row in facts["operations"]] == [
                ("read_receipt", True, FACT)]
            assert facts["operations"][0]["requestId"] == receipt["requestId"]
            assert facts["operations"][0]["actionId"] == receipt["actionId"]
        budget = admitted(providers)
        assert all(ctx.admission is budget for ctx in contexts)
        if stage == "final":
            assert len(providers.sent) == 3
            assert budget.attempts[-1].phase == stage
        else:
            # A useful query answer needs no final. Failed, timed-out or empty
            # query output leaves the original protected final allowance useful.
            phases = ["look", "query"] if outcome == "success" else ["look", "query", "final"]
            status = "failed" if outcome in ("failure", "timeout") else "done"
            statuses = ["done"] + [status] * (len(phases) - 1)
            assert len(providers.sent) == len(phases)
            assert [attempt.phase for attempt in budget.attempts] == phases
            assert [attempt.status for attempt in budget.attempts] == statuses
            assert [entry.status for entry in providers.sent] == statuses
            assert all(not entry.images for entry in interpretation_inputs)
        if stage == "late":
            synthesis = [row for row in logged if row.get("kind") == "query-answer"]
            assert len(synthesis) == 1 and synthesis[0]["answer"] == answer
        if outcome == "success":
            assert answer == completed
        else:
            assert answer == "", "an unavailable interpretation must not become receipt-as-assistant prose"
            if stage == "final":
                assert result["answerStatus"] == "not-published"
            else:
                assert synthesis[0]["actions"][0]["summary"] == FACT
            assert budget.attempts[-1].status == ("done" if outcome == "empty" else "failed")

    run(go())
