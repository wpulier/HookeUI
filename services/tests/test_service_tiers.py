"""OpenAI service tiers in a spec ('@none+priority', '@low+ultrafast') are sent as `service_tier`, never as part of the
reasoning effort, and are priced at their multiple of the list price, by the tier OpenAI says it served
(brain/llm.py, vision.py, research_worker/api_usage.py)."""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator.brain import dispatch
from orchestrator.brain.llm import FunctionCallingBrain
from research_worker import api_usage


class _Registry:
    def responses_tools(self):
        return []


class _Responses:
    def __init__(self, served):
        self.kwargs = None
        self.served = served

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output=[], output_text="", model=kwargs["model"], service_tier=self.served,
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=100))


def _brain(served):
    brain = FunctionCallingBrain.__new__(FunctionCallingBrain)
    responses = _Responses(served)
    brain._clients = {}

    async def client(which, make):
        return SimpleNamespace(responses=responses)

    brain._client = client
    return brain, responses


@pytest.fixture
def recorded(monkeypatch):
    rows = []
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(api_usage, "record", lambda model, usd=None, **kw: rows.append((model, usd, kw.get("basis"))))
    return rows


@pytest.mark.parametrize("spec, effort, tier", [
    ("openai:gpt-5.6-luna@none+priority", "low", "priority"),
    ("openai:gpt-6.1-sol@low+ultrafast", "low", "ultrafast"),
    ("openai:gpt-6.1-sol@medium", "medium", None),
])
def test_function_calling_sends_the_tier_apart_from_the_effort(monkeypatch, recorded, spec, effort, tier):
    brain, responses = _brain(served=tier or "default")

    async def straight(call, **_):
        return await call()

    monkeypatch.setattr(dispatch, "dispatch_answer", straight)
    asyncio.run(brain.ask(spec, b"\xff\xd8jpeg", "close that", _Registry()))
    assert responses.kwargs["reasoning"] == {"effort": effort}  # never "none+priority"
    assert responses.kwargs.get("service_tier") == tier


def test_ultrafast_is_priced_at_six_times_the_list_price(recorded):
    result = SimpleNamespace(service_tier="ultrafast", usage=SimpleNamespace(input_tokens=1_000_000, output_tokens=100_000))
    api_usage.response("gpt-6.1-sol", result, provider="OpenAI", area="Chat", tier="ultrafast")
    model, usd, basis = recorded[-1]
    assert usd == pytest.approx((2.00 + 0.1 * 10.00) * 6)  # $12 in and $60 out a million (OpenAI pricing, 2026-11-03)
    assert "ultrafast tier" in basis


def test_the_served_tier_is_billed_not_the_asked_one(recorded):
    result = SimpleNamespace(service_tier="default", usage=SimpleNamespace(input_tokens=1_000_000, output_tokens=0))
    api_usage.response("gpt-6.1-sol", result, provider="OpenAI", area="Chat", tier="ultrafast")
    assert recorded[-1][1] == pytest.approx(2.00)


def test_an_unknown_tier_is_never_priced_as_standard(recorded):
    result = SimpleNamespace(usage=SimpleNamespace(input_tokens=1000, output_tokens=100))
    api_usage.response("gpt-6.1-sol", result, provider="OpenAI", area="Chat", tier="scale")
    assert recorded[-1][1] is None and recorded[-1][2] == "Usage or price unavailable"


def test_tier_factors():
    assert api_usage.tier_factor(None) == api_usage.tier_factor("default") == 1.0
    assert api_usage.tier_factor("priority") == api_usage.tier_factor("fast") == 2.0
    assert api_usage.tier_factor("ultrafast") == 6.0
    assert api_usage.tier_factor("scale") is None


def test_think_options_reach_their_model_and_tier():
    from orchestrator.voice.think import ClaudeThinker, SolThinker, thinker

    fast = thinker("gpt-6.1-sol-ultrafast")
    assert isinstance(fast, SolThinker) and (fast.model, fast.effort, fast.tier) == ("gpt-6.1-sol", "low", "ultrafast")
    plain = thinker("gpt-6.1-sol")
    assert (plain.model, plain.tier) == ("gpt-6.1-sol", None)
    assert isinstance(thinker("claude-opus-5-5"), ClaudeThinker)


def test_acting_candidates_are_looks_too():
    """The fast pass calls its option as a look (brain/loop.py: `model = settings.fast`), so every BRAIN_FAST model is
    also a VISION option with a spec."""
    from orchestrator.options import BRAIN_FAST, VISION

    for option in BRAIN_FAST:
        if option != "off":
            assert VISION[option].spec, option
    assert VISION["gpt-6.1-sol-ultrafast"].spec == "openai:gpt-6.1-sol@low+ultrafast"
