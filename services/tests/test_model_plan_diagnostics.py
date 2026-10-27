"""Synthetic SDK boundary diagnostics; no actual provider or browser acceptance.

Exercise real adapters, admission and Fast parsing. No response synthesis,
provider budget or action filtering is replaced by an observer.
"""
import asyncio
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from orchestrator import execution
from orchestrator.brain.admission import AdmissionDenied
from orchestrator.brain.dispatch import AnswerProvider, answer_phase
from orchestrator.brain.fast import decide
from orchestrator.brain.llm import FunctionCallingBrain
from orchestrator.brain.registry import Registry, action_number
from orchestrator.ticket import issue, using
from orchestrator.vision import GeminiVision, OpenAIVision
from tests.fakes import Hub


PROMPT = "Private original request Ω: preserved, never an execution field."
ANSWER = "An intact provider answer, not an application fallback."
PLAN = {"answer": ANSWER, "actions": [None, {"tool": "close_result", "args": {"researcher": "2"},
        "after_steps": [1]}], "targets": []}


def sdk_response(provider, *, text=None, limited=False, facts=True):
    text = json.dumps(PLAN) if text is None else text
    if provider == "openai":
        return NS(model="resolved-model", output_text=text,
                  usage=NS(input_tokens=91, output_tokens=47,
                           output_tokens_details=NS(reasoning_tokens=11)) if facts else None,
                  status="incomplete" if limited else "completed",
                  incomplete_details=NS(reason="max_output_tokens") if limited else None,
                  output=[])
    return NS(model_version="resolved-model", text=text,
              usage_metadata=NS(prompt_token_count=91, candidates_token_count=47,
                                thoughts_token_count=11) if facts else None,
              candidates=[NS(finish_reason="MAX_TOKENS" if limited else "STOP")])


def vision(provider, create):
    cls = OpenAIVision if provider == "openai" else GeminiVision
    model = object.__new__(cls)
    model.model = "selected-model"
    if provider == "openai":
        model.reasoning_effort, model.service_tier = "none", None
        model.client = NS(responses=NS(create=create))
    else:
        model.thinking_level, model.media_resolution = "minimal", None
        model.client = NS(aio=NS(models=NS(generate_content=create)))
    return model


def provider_row(log, root, operation="vision"):
    rows = [row for row in log.inspect(root.id)["steps"] if row["operation"] == operation]
    assert len(rows) == 1
    return rows[0]


def assert_private(data):
    encoded = json.dumps(data)
    assert PROMPT not in encoded and ANSWER not in encoded
    assert "raw_text" not in encoded and "output_text" not in encoded
    assert all(type(value) in (str, int, float, bool, type(None))
               for row in data["steps"] for value in row.values())


@pytest.mark.parametrize("provider", ["gemini", "openai"])
def test_actual_admitted_vision_retains_provider_facts_without_prose(provider):
    async def go():
        sent = []

        async def create(**kwargs):
            sent.append(kwargs)
            return sdk_response(provider)

        model, request = vision(provider, create), issue(Hub(), "turn")
        log = execution.ExecutionLog(enabled=True)
        try:
            with log.request("ask", day="2026-10-09", thread="t4", generation=1) as root:
                execution.bind(turn_id=151)
                with using(request), answer_phase("look"):
                    result = await AnswerProvider(model, model.model).ask([b"original-picture"], PROMPT,
                                                                         max_output_tokens=613)
            assert result.data == PLAN and result.raw_text == json.dumps(PLAN)
            assert len(sent) == request.admission.used == 1
            attempt = request.admission.attempts[0]
            assert attempt.status == "done"
            row = provider_row(log, root)
            assert row["requestId"] == request.admission.request_id
            assert row["attemptId"] == attempt.attempt_id and row["phase"] == "look"
            assert row["outputCap"] == 613
            assert (row["inputTokens"], row["outputTokens"], row["thinkingTokens"]) == (91, 47, 11)
            assert row["parseOk"] is True
            assert row["promptDigest"] == hashlib.sha256(PROMPT.encode()).hexdigest()
            assert row["promptBytes"] == len(PROMPT.encode())
            assert row["rawActionCount"] == 2 and row["targetCount"] == 0
            assert len(row["planDigest"]) == 64
            if provider == "gemini":
                assert row["finishReason"] == "STOP"
            else:
                assert row["responseStatus"] == "completed"
                assert row["finishReason"] is None  # no fabricated stop reason
            assert_private(log.inspect(root.id))
        finally:
            request.seal()
    asyncio.run(go())


@pytest.mark.parametrize("provider", ["gemini", "openai"])
def test_unreadable_limited_response_stays_a_failed_spent_attempt(provider):
    async def go():
        sent = []

        async def create(**kwargs):
            sent.append(kwargs)
            return sdk_response(provider, text='{"answer":"unfinished', limited=True)

        model, request = vision(provider, create), issue(Hub(), "turn")
        log = execution.ExecutionLog(enabled=True)
        try:
            with log.request("ask") as root:
                with using(request), answer_phase("fast"):
                    with pytest.raises(RuntimeError, match="unreadable reply"):
                        await AnswerProvider(model, model.model).ask([], PROMPT, max_output_tokens=700)
            row = provider_row(log, root)
            assert row["parseOk"] is False
            assert row["finishReason"] == ("MAX_TOKENS" if provider == "gemini" else "max_output_tokens")
            assert row["outputCap"] == 700
            assert row["planDigest"] is None and row["rawActionCount"] is None
            assert len(sent) == request.admission.used == 1
            assert request.admission.attempts[0].status == "failed"
            assert_private(log.inspect(root.id))
        finally:
            request.seal()
    asyncio.run(go())


@pytest.mark.parametrize("provider", ["gemini", "openai"])
def test_fc_provider_diagnostics_preserve_original_malformed_slot(monkeypatch, provider):
    async def go():
        sent = []

        async def create(**kwargs):
            sent.append(kwargs)
            reply = sdk_response(provider)
            if provider == "openai":
                reply.output = [NS(type="function_call", name="missing", arguments="{bad"),
                                NS(type="function_call", name="close_result",
                                   arguments='{"researcher":"2","after_steps":[1]}')]
            else:
                reply.candidates[0].content = NS(parts=[NS(text=ANSWER, thought=False)])
                reply.function_calls = [NS(name="missing", args=[1]),
                                        NS(name="close_result", args={"researcher": "2", "after_steps": [1]})]
                # Gemini's typed argument contract differs: use its valid calls;
                # malformed external JSON slot control is OpenAI specific.
                reply.function_calls = reply.function_calls[1:]
            return reply

        if provider == "openai":
            import openai
            monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kwargs: NS(responses=NS(create=create)))
        else:
            from google import genai
            monkeypatch.setattr(genai, "Client", lambda **kwargs: NS(aio=NS(models=NS(generate_content=create))))
        monkeypatch.setenv("OPENAI_API_KEY", "synthetic")
        monkeypatch.setenv("GEMINI_API_KEY", "synthetic")
        request, model = issue(Hub(), "turn"), FunctionCallingBrain()
        log = execution.ExecutionLog(enabled=True)
        try:
            with log.request("ask") as root:
                with using(request), answer_phase("look"):
                    data, used = await model.ask(provider + ":selected-model@low", b"original-picture", PROMPT, Registry())
            assert data["answer"] == (json.dumps(PLAN) if provider == "openai" else ANSWER)
            assert len(sent) == request.admission.used == 1
            if provider == "openai":
                assert data["actions"][0] is None
                assert data["actions"][1]["after_steps"] == [1]
            else:
                assert data["actions"][0]["after_steps"] == [1]
            row = provider_row(log, root, "function-calling")
            sent_cap = (sent[0]["max_output_tokens"] if provider == "openai"
                        else sent[0]["config"].max_output_tokens)
            assert row["outputCap"] == sent_cap and row["parseOk"] is True
            assert row["inputTokens"] == 91 and row["outputTokens"] == 47
            assert row["attemptId"] == request.admission.attempts[0].attempt_id
            assert row["rawActionCount"] == len(data["actions"])
            assert row["promptDigest"] == hashlib.sha256(PROMPT.encode()).hexdigest()
            assert_private(log.inspect(root.id))
        finally:
            request.seal()
    asyncio.run(go())


@pytest.mark.parametrize("intent,retained", [("request", 1), ("statement", 0)])
def test_fast_records_raw_slots_and_actual_filtered_plan_without_changing_it(intent, retained):
    async def go():
        reply = {**PLAN, "intent": intent, "needs_look": False}
        seen = []

        async def create(**kwargs):
            seen.append(kwargs)
            return sdk_response("openai", text=json.dumps(reply))

        moment = NS(user_leads=False, view=None, seen="", outlined=(), selected=None,
                    screen="", awareness={}, researchers="", history="", digest="", learner="", held="", stage="",
                    conversation_id="t4", conversation_day="2026-10-09")
        request, log = issue(Hub(), "turn"), execution.ExecutionLog(enabled=True)
        try:
            with log.request("ask") as root:
                with using(request), answer_phase("fast"):
                    result = await decide(moment, PROMPT, vision("openai", create), "original tools", "gemini-3.5-flash")
            assert result.answer == ANSWER and len(result.actions) == retained
            if retained:
                assert action_number(result.actions[0], 1) == 2
                assert result.actions[0].after_steps == [1]
            assert len(seen) == request.admission.used == 1
            rows = [row for row in log.inspect(root.id)["steps"] if row.get("scope") == "fast-plan"]
            assert len(rows) == 1
            assert rows[0]["rawActionCount"] == 2 and rows[0]["actionCount"] == retained
            assert len(rows[0]["planDigest"]) == 64
            assert rows[0]["requestId"] == request.admission.request_id
            assert_private(log.inspect(root.id))
        finally:
            request.seal()
    asyncio.run(go())


def test_concurrent_reply_order_keeps_actual_request_and_attempt_association():
    async def go():
        ready, release = asyncio.Event(), asyncio.Event()
        first, second = issue(Hub(), "turn"), issue(Hub(), "turn")
        log = execution.ExecutionLog(enabled=True)

        async def call(request, phase, held):
            async def create(**kwargs):
                if held:
                    ready.set()
                    await release.wait()
                return sdk_response("openai")
            with log.request("ask", thread="t1" if held else "t2") as root:
                with using(request), answer_phase(phase):
                    await AnswerProvider(vision("openai", create), "selected-model").ask([], PROMPT)
            return root

        try:
            slow = asyncio.create_task(call(first, "fast", True))
            await ready.wait()
            second_root = await call(second, "look", False)
            release.set()
            first_root = await slow
            for request, root, phase in [(first, first_root, "fast"), (second, second_root, "look")]:
                row = provider_row(log, root)
                assert row["requestId"] == request.admission.request_id
                assert row["attemptId"] == request.admission.attempts[0].attempt_id
                assert row["phase"] == phase and request.admission.used == 1
        finally:
            release.set()
            first.seal()
            second.seal()
    asyncio.run(go())


@pytest.mark.parametrize("enabled", [True, False])
def test_unknown_provider_facts_and_exhausted_budget_are_not_invented(enabled):
    async def go():
        sent = []

        async def create(**kwargs):
            sent.append(kwargs)
            return NS(model="resolved", output_text=json.dumps(PLAN), usage=None)

        request = issue(Hub(), "turn")
        log = execution.ExecutionLog(enabled=enabled)
        model = vision("openai", create)
        try:
            with log.request("ask") as root:
                with using(request), answer_phase("look"):
                    for _ in range(3):
                        result = await AnswerProvider(model, model.model).ask([], PROMPT)
                        assert result.data == PLAN
                    with pytest.raises(AdmissionDenied, match="budget-exhausted"):
                        await AnswerProvider(model, model.model).ask([], PROMPT)
            assert len(sent) == request.admission.used == 3
            if enabled:
                rows = log.inspect(root.id)["steps"]
                assert len(rows) == 3
                for row in rows:
                    assert row["inputTokens"] is None and row["outputTokens"] is None
                    assert row["thinkingTokens"] is None and row["finishReason"] is None
                    assert row["responseStatus"] is None
            else:
                assert log.references() == []
        finally:
            request.seal()
    asyncio.run(go())


def test_failed_optional_diagnostic_does_not_change_provider_result_or_admission(monkeypatch):
    async def go():
        async def create(**kwargs):
            return sdk_response("openai")

        def unavailable(**facts):
            if "promptDigest" in facts or "attemptId" in facts:
                raise RuntimeError("diagnostic sink unavailable")

        monkeypatch.setattr(execution, "note", unavailable)
        request, model = issue(Hub(), "turn"), vision("openai", create)
        try:
            with using(request), answer_phase("look"):
                result = await AnswerProvider(model, model.model).ask([], PROMPT)
            assert result.data == PLAN
            assert request.admission.used == 1 and request.admission.attempts[0].status == "done"
        finally:
            request.seal()
    asyncio.run(go())
