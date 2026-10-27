"""Additional source-derived controls: provider error results and explicit scopes.

The production vision adapters return VisionResult(data=None) on network errors;
that must remain a failed spent attempt. SDK constructors default to retries.
These controls use synthetic callbacks/constructors and no real provider calls.
"""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator.brain.admission import AdmissionDenied
from orchestrator.brain.dispatch import AnswerProvider, answer_phase
from orchestrator.ticket import issue, using
from orchestrator.vision import GeminiVision, OpenAIVision, VisionResult
from tests.fakes import Hub


def test_a_provider_error_result_consumes_one_failed_attempt():
    request = issue(Hub(), "turn")
    calls = []

    async def ask():
        calls.append(1)
        return VisionResult(None, 1, "synthetic", error="provider timeout")

    async def go():
        with using(request), answer_phase("look"):
            with pytest.raises(RuntimeError, match="provider timeout"):
                await AnswerProvider(SimpleNamespace(ask=ask), "synthetic").ask()

    try:
        asyncio.run(go())
        assert calls == [1]
        assert request.admission.used == 1
        assert request.admission.attempts[0].status == "failed"
    finally:
        request.seal()


def test_an_explicit_answer_scope_without_a_request_is_refused_before_sending():
    sent = []

    async def ask():
        sent.append(1)
        return VisionResult({}, 1, "synthetic")

    async def go():
        with using(None), answer_phase("look"):
            with pytest.raises(AdmissionDenied, match="request-unbound"):
                await AnswerProvider(SimpleNamespace(ask=ask), "synthetic").ask()

    asyncio.run(go())
    assert sent == []


def test_specialist_work_outside_answer_scope_is_not_an_answer_budget_bypass():
    request = issue(Hub(), "turn")
    sent = []

    async def ask():
        sent.append(1)
        return VisionResult({}, 1, "synthetic")

    async def go():
        provider = AnswerProvider(SimpleNamespace(ask=ask), "synthetic")
        with using(request):
            await provider.ask()  # independent specialist boundary, no answer scope
            assert request.admission.used == 0
            with answer_phase("look"):
                for _ in range(3):
                    await provider.ask()
                with pytest.raises(AdmissionDenied):
                    await provider.ask()

    try:
        asyncio.run(go())
        assert len(sent) == 4 and request.admission.used == 3
    finally:
        request.seal()


def test_json_provider_clients_disable_hidden_sdk_retries(monkeypatch):
    from google import genai
    import openai

    gemini, responses = [], []
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-value")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-value")
    monkeypatch.setattr(genai, "Client", lambda **kwargs: gemini.append(kwargs))
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kwargs: responses.append(kwargs))
    GeminiVision("synthetic")
    OpenAIVision("synthetic")
    assert gemini[0]["http_options"]["retry_options"]["attempts"] == 1
    assert responses[0]["max_retries"] == 0


@pytest.mark.parametrize("provider", ["gemini", "openai"])
def test_function_calling_clients_disable_hidden_sdk_retries(monkeypatch, provider):
    from google import genai
    import openai
    from orchestrator.brain.llm import FunctionCallingBrain

    class ConstructorReached(Exception):
        pass

    seen = []

    def constructor(**kwargs):
        seen.append(kwargs)
        raise ConstructorReached  # do not construct an SDK client or send anything

    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-value")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-value")
    monkeypatch.setattr(genai, "Client", constructor)
    monkeypatch.setattr(openai, "AsyncOpenAI", constructor)
    model = FunctionCallingBrain()
    call = model._gemini if provider == "gemini" else model._openai
    with pytest.raises(ConstructorReached):
        asyncio.run(call("synthetic", "low", b"", "fixture", None))
    if provider == "gemini":
        assert seen[0]["http_options"]["retry_options"]["attempts"] == 1
    else:
        assert seen[0]["max_retries"] == 0
