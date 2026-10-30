"""Engines as real options (the researcher repair, Phase 7): Gemini thinks as the effort asks, says why an empty
answer ended its run and counts a site's redirect addresses once; both engines try a model call that failed for a
reason that may pass again, never their tools; fetches, videos and lessons are priced as they happened; the
scripted engine goes through the same round rules. Every model and fetch here is a fake: nothing is sent."""
import asyncio
import json
import logging
from types import SimpleNamespace

import httpx
import openai
import pytest
from google import genai
from google.genai import errors, types

from research_worker import budget as budget_module
from research_worker import engines
from research_worker import researcher as researcher_module
from research_worker import safe_fetch
from research_worker.budget import EFFORTS, PRICES, STALL_ROUNDS, Effort, image_cost, tokens_cost
from research_worker.engines import STALLED, FakeEngine, GeminiEngine, SolEngine
from research_worker.safe_fetch import FetchRefused
from tests.test_research_content import LESSON, response, researcher, stopped_after

FINISH = {"title": "t", "summary": "s", "one_liner": "o", "significance": "low"}
REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"


def steps(events, kind=None):
    return [e["text"] for e in events if e["event"] == "step" and (kind is None or e["kind"] == kind)]


def charges(events):
    return [e["charge"] for e in events if e["event"] == "cost" and e.get("charge")]


def gemini_reply(*calls, text=None, reason="STOP", queries=(), chunks=(), content=True, candidates=True, blocked=None):
    parts = [types.Part(function_call=types.FunctionCall(name=n, args=a)) for n, a in calls]
    if text:
        parts.append(types.Part(text=text))
    grounding = types.GroundingMetadata(web_search_queries=list(queries), grounding_chunks=[
        types.GroundingChunk(web=types.GroundingChunkWeb(uri=u, title=t)) for u, t in chunks]) if queries or chunks else None
    candidate = types.Candidate(content=types.Content(role="model", parts=parts) if content else None, finish_reason=reason,
                                grounding_metadata=grounding)
    return types.GenerateContentResponse(
        candidates=[candidate] if candidates else [],
        prompt_feedback=types.GenerateContentResponsePromptFeedback(block_reason=blocked) if blocked else None,
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=1000, candidates_token_count=100))


def fake_gemini(monkeypatch, generate):
    made = []

    def client(*args, **kwargs):
        made.append(kwargs)
        return SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))

    monkeypatch.setattr(genai, "Client", client)
    return made


def fake_sol(monkeypatch, create):
    made = []

    def client(*args, **kwargs):
        made.append(kwargs)
        return SimpleNamespace(responses=SimpleNamespace(create=create))

    monkeypatch.setattr(openai, "AsyncOpenAI", client)
    return made


def only_finish(config) -> bool:
    calling = config.tool_config.function_calling_config if config.tool_config else None
    return bool(calling and calling.allowed_function_names == ["finish"])


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(engines, "RETRY_WAIT_S", (0.0, 0.0))


REQUEST = httpx.Request("POST", "https://api.openai.com/v1/responses")


def status_error(cls, code):
    return cls("synthetic", response=httpx.Response(code, request=REQUEST), body=None)


# --- 1. Gemini ----------------------------------------------------------------------------------

@pytest.mark.parametrize("effort, level", [("quick", "LOW"), ("standard", "MEDIUM"), ("deep", "HIGH")])
def test_gemini_thinks_as_hard_as_the_effort_asks(tmp_path, monkeypatch, effort, level):
    configs = []

    async def generate(model, contents, config):
        configs.append(config)
        return gemini_reply(("finish", FINISH))

    made = fake_gemini(monkeypatch, generate)
    r, events = researcher(tmp_path, {"effort": effort})
    asyncio.run(r.run(GeminiEngine()))
    assert EFFORTS[effort].reasoning == level.lower()
    assert configs[0].thinking_config.thinking_level == types.ThinkingLevel[level]
    assert made[0]["http_options"]["retry_options"]["attempts"] == 1  # no hidden SDK retries under ours
    assert events[-1]["status"] == "done"


@pytest.mark.parametrize("reply, why", [
    (gemini_reply(content=False, reason="SAFETY"), "SAFETY"),
    (gemini_reply(reason="MAX_TOKENS"), "MAX_TOKENS"),  # a candidate with no parts at all
    (gemini_reply(candidates=False, blocked="PROHIBITED_CONTENT"), "the request was blocked (PROHIBITED_CONTENT)"),
    (gemini_reply(candidates=False), "no candidates"),
])
def test_an_empty_or_blocked_gemini_answer_ends_the_run_with_its_reason(tmp_path, monkeypatch, reply, why):
    async def generate(model, contents, config):
        return reply

    fake_gemini(monkeypatch, generate)
    r, events = researcher(tmp_path, {"effort": "quick"})
    asyncio.run(r.run(GeminiEngine()))
    assert events[-1]["status"] == "failed" and events[-1]["result"] is None
    assert f"Gemini returned no answer: {why}" in steps(events, "warning")
    assert not steps(events, "debug")  # a reason, not a traceback
    assert not any("IndexError" in s or "AttributeError" in s for s in steps(events))
    assert events[-1]["spent"] > 0  # the call that came back empty was still paid for


def test_a_site_found_again_through_a_new_redirect_is_nothing_new(tmp_path, monkeypatch):
    configs, told = [], []

    async def fetch(url):
        raise FetchRefused("HTTP 404")

    async def generate(model, contents, config):
        configs.append(config)
        told.append([p.text for c in contents for p in c.parts or [] if p.text])
        if only_finish(config):
            return gemini_reply(("finish", FINISH))
        n = len(configs)  # a new redirect address every round, for the same site (as the Gemini API gives them)
        return gemini_reply(("fetch_reference_image", {"url": f"https://example.org/{n}.jpg"}),
                            queries=["stomata"], chunks=[(f"{REDIRECT}{n}", "nih.gov")])

    monkeypatch.setattr(researcher_module, "fetch_image", fetch)
    fake_gemini(monkeypatch, generate)
    r, events = researcher(tmp_path, {"effort": "quick"},
                           Effort("t", 60, 1.0, "big", frozenset({"web_search", "fetch_reference_image", "finish"}), 12, "low"))
    asyncio.run(r.run(GeminiEngine()))
    # Round 1 found nih.gov; the next STALL_ROUNDS rounds found it again, so the one after only writes up.
    assert len(configs) == STALL_ROUNDS + 2 and only_finish(configs[-1])
    assert STALLED in told[-1] and not any(STALLED in t for t in told[-2])
    assert steps(events, "stall") == [f"Nothing new in {STALL_ROUNDS} rounds: writing up what it has"]
    assert r.search_sources == [f"{REDIRECT}{n}" for n in range(1, STALL_ROUNDS + 2)]  # kept as given, for display
    assert events[-1]["status"] == "done"


def test_grounded_pages_count_by_address_unless_the_address_is_a_redirect(tmp_path):
    r, _ = researcher(tmp_path, {"effort": "quick"})
    grounding = types.GroundingMetadata(web_search_queries=["guard cells"], grounding_chunks=[
        types.GroundingChunk(web=types.GroundingChunkWeb(uri=f"{REDIRECT}a", title="nih.gov")),
        types.GroundingChunk(web=types.GroundingChunkWeb(uri=f"{REDIRECT}b", title="nih.gov")),
        types.GroundingChunk(web=types.GroundingChunkWeb(uri=f"{REDIRECT}c", title="britannica.com")),
        types.GroundingChunk(web=types.GroundingChunkWeb(uri="https://example.org/stoma", title="example.org")),
        types.GroundingChunk(web=types.GroundingChunkWeb(uri="https://example.org/guard", title="example.org"))])
    engines.searched(r, grounding)
    assert r.progress() == 4  # nih.gov, britannica.com and the two real addresses
    assert len(r.search_sources) == 5
    assert [c["what"] for c in r.budget.calls] == ["google_search"]


def test_a_cut_off_gemini_call_counts_the_function_results_it_sent(tmp_path, monkeypatch):
    rounds = []

    async def generate(model, contents, config):
        rounds.append(1)
        if len(rounds) == 1:
            return gemini_reply(("record_finding", {"text": "pores"}))
        await asyncio.sleep(30)

    async def big(name, args):
        return {"ok": True, "text": "y" * 20000}, None

    fake_gemini(monkeypatch, generate)
    r, events = researcher(tmp_path, {"effort": "quick"},
                           Effort("t", 120, 1.0, "big", frozenset({"web_search", "record_finding", "finish"}), 3, "low"))
    r.call_tool = big
    sent = []
    counted = r.cancelled_call
    r.cancelled_call = lambda *a, **k: (sent.append((a, k)), counted(*a, **k))
    stopped_after(r, GeminiEngine(), 0.3)  # the user's Stop during its second call
    assert events[-1]["status"] in ("cancelled", "partial")
    (model, chars, *_), _ = sent[0]
    assert model == "gemini-3.8-flash" and chars >= 20000 + len(r.system_prompt())


# --- 2. A model call that failed for a reason that may pass is tried again ------------------------

def test_a_failed_sol_call_is_tried_again_and_no_tool_runs_twice(tmp_path, monkeypatch, no_wait):
    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        if len(sent) == 1:
            return response(("record_finding", {"text": "pores"}))
        if len(sent) == 2:
            raise status_error(openai.InternalServerError, 503)
        return response(("finish", FINISH))

    made = fake_sol(monkeypatch, create)
    r, events = researcher(tmp_path, {"effort": "quick"},
                           Effort("t", 120, 1.0, "big", frozenset({"web_search", "record_finding", "finish"}), 8, "low"))
    asyncio.run(r.run(SolEngine()))
    assert made[0]["max_retries"] == 0  # no hidden SDK retries under ours
    assert events[-1]["status"] == "done" and len(sent) == 3
    assert len(r.recorded) == 1  # the finding the first round recorded was not recorded again
    assert sent[1]["input"] == sent[2]["input"]  # the same call, sent again
    assert any("HTTP 503" in s and "trying again" in s and "usage uncertain" in s for s in steps(events, "warning"))
    uncertain = [c for c in charges(events) if c.get("uncertain")]
    assert len(uncertain) == 1 and uncertain[0]["model"] == "gpt-6.1-sol" and uncertain[0]["usd"] > 0
    assert events[-1]["spent_estimated"] == pytest.approx(uncertain[0]["usd"], abs=1e-4)


def test_a_rate_limit_is_tried_again_and_not_counted_as_spent(tmp_path, monkeypatch, no_wait):
    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        if len(sent) == 1:
            raise status_error(openai.RateLimitError, 429)
        return response(("finish", FINISH))

    fake_sol(monkeypatch, create)
    r, events = researcher(tmp_path, {"effort": "quick"},
                           Effort("t", 120, 1.0, "big", frozenset({"web_search", "finish"}), 8, "low"))
    asyncio.run(r.run(SolEngine()))
    assert events[-1]["status"] == "done" and len(sent) == 2
    assert not [c for c in charges(events) if c.get("uncertain")] and events[-1]["spent_estimated"] == 0
    assert any("HTTP 429" in s and "trying again" in s for s in steps(events, "warning"))


def test_a_credit_refusal_ends_the_run_at_once_with_what_to_do(tmp_path, monkeypatch, no_wait):
    sent = []
    body = {"message": "You exceeded your current quota.", "type": "insufficient_quota", "param": None, "code": "insufficient_quota"}

    async def create(**kwargs):
        sent.append(kwargs)
        raise openai.RateLimitError("Error code: 429", response=httpx.Response(429, request=REQUEST), body=body)

    fake_sol(monkeypatch, create)
    r, events = researcher(tmp_path, {"effort": "quick"},
                           Effort("t", 120, 1.0, "big", frozenset({"web_search", "finish"}), 8, "low"))
    asyncio.run(r.run(SolEngine()))
    assert len(sent) == 1  # never tried again: it would fail the same way until credits are added
    assert events[-1]["status"] == "failed" and not charges(events) and events[-1]["spent_estimated"] == 0
    warnings = steps(events, "warning")
    assert not any("trying again" in s for s in warnings)
    assert warnings[-1] == "OpenAI says this account has no credits left. Add credits, or choose another model for Researching."
    assert "insufficient_quota" not in json.dumps([s for s in steps(events)])  # the provider's raw text is not the story


def test_a_plain_rate_limit_is_not_a_credit_refusal():
    limit = openai.RateLimitError("Error code: 429", response=httpx.Response(429, request=REQUEST),
                                  body={"message": "Slow down", "type": "requests", "code": "rate_limit_exceeded"})
    assert engines._credit_refusal(limit) is None
    assert engines._credit_refusal(status_error(openai.RateLimitError, 429)) is None
    assert engines._credit_refusal(errors.ClientError(429, {"error": {"code": 429, "message": "x", "status": "RESOURCE_EXHAUSTED"}})) is None


def test_a_timed_out_call_is_tried_at_most_twice_more(tmp_path, monkeypatch, no_wait):
    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        raise openai.APITimeoutError(request=REQUEST)

    fake_sol(monkeypatch, create)
    r, events = researcher(tmp_path, {"effort": "quick"},
                           Effort("t", 120, 1.0, "big", frozenset({"web_search", "finish"}), 8, "low"))
    r.budget.unlimited = True  # retries without a spending limit (a limited run's are in test_usage_cap)
    asyncio.run(r.run(SolEngine()))
    assert len(sent) == 1 + engines.RETRIES == 3
    assert events[-1]["status"] == "failed"
    assert len([c for c in charges(events) if c.get("uncertain")]) == 3  # each attempt may have been billed


def test_a_call_that_never_answers_is_cut_and_sent_again(tmp_path, monkeypatch, no_wait):
    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        if len(sent) == 1:
            await asyncio.sleep(30)  # hangs: no run clock, so only the call's own limit ends it
        return response(("finish", FINISH))

    fake_sol(monkeypatch, create)
    monkeypatch.setattr(engines, "CALL_TIMEOUT_S", 0.1)
    r, events = researcher(tmp_path, {"effort": "quick"},
                           Effort("t", 0.05, 1.0, "big", frozenset({"web_search", "finish"}), 8, "low"))
    asyncio.run(r.run(SolEngine()))
    assert len(sent) == 2 and events[-1]["status"] == "done"  # past its effort's time, still written up
    assert any("TimeoutError" in s and "trying again" in s for s in steps(events, "warning"))


def test_a_call_that_never_answers_at_all_fails_with_why(tmp_path, monkeypatch, no_wait):
    async def create(**kwargs):
        await asyncio.sleep(30)

    fake_sol(monkeypatch, create)
    monkeypatch.setattr(engines, "CALL_TIMEOUT_S", 0.05)
    r, events = researcher(tmp_path, {"effort": "quick"},
                           Effort("t", 120, 1.0, "big", frozenset({"web_search", "finish"}), 8, "low"))
    r.budget.unlimited = True  # retries without a spending limit (a limited run's are in test_usage_cap)
    asyncio.run(r.run(SolEngine()))
    assert events[-1]["status"] == "failed"
    assert "The model did not answer in 0 s, 3 times" in steps(events, "warning")


def test_a_definite_failure_is_not_tried_again(tmp_path, monkeypatch, no_wait):
    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        raise status_error(openai.BadRequestError, 400)

    fake_sol(monkeypatch, create)
    r, events = researcher(tmp_path, {"effort": "quick"},
                           Effort("t", 120, 1.0, "big", frozenset({"web_search", "finish"}), 8, "low"))
    asyncio.run(r.run(SolEngine()))
    assert len(sent) == 1 and events[-1]["status"] == "failed" and not charges(events)


@pytest.mark.parametrize("failure", [
    lambda: errors.ServerError(503, {"error": {"code": 503, "message": "unavailable", "status": "UNAVAILABLE"}}),
    lambda: httpx.ReadTimeout("synthetic"),
    lambda: TimeoutError(),
])
def test_a_failed_gemini_call_is_tried_again(tmp_path, monkeypatch, no_wait, failure):
    calls = []

    async def generate(model, contents, config):
        calls.append(list(contents))
        if len(calls) == 1:
            raise failure()
        return gemini_reply(("finish", FINISH))

    fake_gemini(monkeypatch, generate)
    r, events = researcher(tmp_path, {"effort": "quick"},
                           Effort("t", 120, 1.0, "big", frozenset({"web_search", "finish"}), 8, "low"))
    asyncio.run(r.run(GeminiEngine()))
    assert events[-1]["status"] == "done" and len(calls) == 2 and calls[0] == calls[1]
    assert len([c for c in charges(events) if c.get("uncertain")]) == 1


def test_a_definite_gemini_failure_is_not_tried_again(tmp_path, monkeypatch, no_wait):
    calls = []

    async def generate(model, contents, config):
        calls.append(1)
        raise errors.ClientError(400, {"error": {"code": 400, "message": "bad", "status": "INVALID_ARGUMENT"}})

    fake_gemini(monkeypatch, generate)
    r, events = researcher(tmp_path, {"effort": "quick"},
                           Effort("t", 120, 1.0, "big", frozenset({"web_search", "finish"}), 8, "low"))
    asyncio.run(r.run(GeminiEngine()))
    assert len(calls) == 1 and events[-1]["status"] == "failed"


# --- 3. A fetch that failed for a reason that may pass may be tried once more ---------------------

def test_a_passing_fetch_failure_may_be_tried_once_more(tmp_path, monkeypatch):
    calls = []

    async def fetch(url):
        calls.append(url)
        raise FetchRefused("HTTP 503", transient=True)

    monkeypatch.setattr(researcher_module, "fetch_image", fetch)
    r, _ = researcher(tmp_path, {"effort": "quick"})
    first, _ = asyncio.run(r.call_tool("fetch_reference_image", {"url": "https://example.org/a.jpg"}))
    second, _ = asyncio.run(r.call_tool("fetch_reference_image", {"url": "https://example.org/a.jpg"}))
    third, _ = asyncio.run(r.call_tool("fetch_reference_image", {"url": "https://example.org/a.jpg"}))
    assert "HTTP 503" in first["error"] and "once more" in first["error"]
    assert "HTTP 503" in second["error"] and "already tried" in third["error"]
    assert calls == ["https://example.org/a.jpg"] * 2  # the third ask never reached the network


def test_a_passing_failure_then_a_definite_one_blocks_the_url(tmp_path, monkeypatch):
    calls = []

    async def fetch(url):
        calls.append(url)
        raise FetchRefused("ReadTimeout", transient=True) if len(calls) == 1 else FetchRefused("HTTP 404")

    monkeypatch.setattr(researcher_module, "fetch_image", fetch)
    r, _ = researcher(tmp_path, {"effort": "quick"})
    for _ in range(3):
        asyncio.run(r.call_tool("fetch_reference_image", {"url": "https://example.org/b.jpg"}))
    assert len(calls) == 2 and "HTTP 404" in r.failed_urls["https://example.org/b.jpg"]


async def fetch_through(answer):
    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        return await safe_fetch.fetch_image("https://example.org/a.jpg", client=client)


@pytest.mark.parametrize("answer, transient", [
    (lambda request: httpx.Response(503, request=request), True),
    (lambda request: httpx.Response(429, request=request), True),
    (lambda request: httpx.Response(404, request=request), False),
    (lambda request: httpx.Response(403, request=request), False),
    (lambda request: httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>hi</p>", request=request), False),
])
def test_fetch_failures_say_whether_they_may_pass(monkeypatch, answer, transient):
    monkeypatch.setattr(safe_fetch, "check_url", lambda url: None)  # no address lookup: nothing leaves this machine
    with pytest.raises(FetchRefused) as refused:
        asyncio.run(fetch_through(answer))
    assert refused.value.transient is transient


def test_a_fetch_that_times_out_may_pass(monkeypatch):
    def slow(request):
        raise httpx.ReadTimeout("synthetic", request=request)

    monkeypatch.setattr(safe_fetch, "check_url", lambda url: None)
    with pytest.raises(FetchRefused) as refused:
        asyncio.run(fetch_through(slow))
    assert refused.value.transient and str(refused.value) == "ReadTimeout"


# --- 4. Costs ------------------------------------------------------------------------------------

def test_a_cut_off_video_is_estimated(tmp_path, monkeypatch):
    async def create(**kwargs):  # Gemini Omni Flash's Interactions API (research_worker/video.py)
        await asyncio.sleep(30)

    monkeypatch.setattr(genai, "Client", lambda *a, **k: SimpleNamespace(aio=SimpleNamespace(
        interactions=SimpleNamespace(create=create))))
    r, events = researcher(tmp_path, {"effort": "standard", "deliverable": "video"})
    with pytest.raises(TimeoutError):
        asyncio.run(asyncio.wait_for(r.tool_make_video({"motion": "the cells drift", "seconds": 6}), 0.2))
    charge, = charges(events)
    assert charge["model"] == researcher_module.VIDEO_MODEL and charge["usd"] == pytest.approx(PRICES["gemini-omni-flash"] * 6)
    assert r.spent_estimated == pytest.approx(0.6)


@pytest.mark.parametrize("key, writer", [("synthetic-test-value", researcher_module.LESSON_CLAUDE),
                                         (None, researcher_module.LESSON_FALLBACK)])
def test_a_cut_off_lesson_is_priced_as_the_model_writing_it(tmp_path, monkeypatch, key, writer):
    if key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    else:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    async def write(prompt, _researcher, **_):
        await asyncio.sleep(30)

    monkeypatch.setattr(researcher_module, "write_lesson", write)
    r, events = researcher(tmp_path, {"effort": "standard", "deliverable": "lesson"})
    content = researcher_module.lesson_content(LESSON["title"], LESSON["key_ideas"], LESSON["questions"])
    with pytest.raises(TimeoutError):
        asyncio.run(asyncio.wait_for(r._lesson_page(content), 0.2))
    charge, = charges(events)
    assert charge["model"] == writer


def test_an_unpriced_model_is_priced_as_a_fallback_and_said_once(caplog):
    with caplog.at_level(logging.WARNING, logger=budget_module.__name__):
        assert tokens_cost("no-such-model-7", 1_000_000, 0) == PRICES["gpt-6.1-sol"]["in"]
        assert tokens_cost("no-such-model-7", 1_000_000, 0) == PRICES["gpt-6.1-sol"]["in"]
        assert image_cost("no-such-picture-model-7", 1_000_000) == PRICES["gpt-image-2.5-flare"]["text_in"]
        assert tokens_cost("gpt-6.1-sol", 1_000_000, 0) == PRICES["gpt-6.1-sol"]["in"]
    said = [rec.getMessage() for rec in caplog.records]
    assert len(said) == 2 and "no-such-model-7" in said[0] and "no-such-picture-model-7" in said[1]


# --- 5. The scripted engine goes through the same round rules --------------------------------

def test_scripted_rounds_that_find_nothing_end_in_a_write_up(tmp_path):
    r, events = researcher(tmp_path, {"effort": "quick", "fake": {"seconds": 0.05, "finding": "pores", "idle": 8}})
    asyncio.run(r.run(FakeEngine()))
    assert steps(events, "stall") == [f"Nothing new in {STALL_ROUNDS} rounds: writing up what it has"]
    assert len([s for s in steps(events) if s.startswith("Looked again")]) == STALL_ROUNDS
    assert events[-1]["status"] == "done" and events[-1]["result"]["summary"]


def test_a_scripted_run_writes_up_when_time_is_nearly_up(tmp_path):
    effort = Effort("t", 1.0, 1.0, "big", frozenset({"web_search", "record_finding", "finish"}), 8, "low")
    r, events = researcher(tmp_path, {"effort": "quick", "fake": {"seconds": 0.8, "idle": 3}}, effort)
    asyncio.run(r.run(FakeEngine()))
    assert "Its time is nearly up: told to plan its write-up" in steps(events, "step")
    assert not [s for s in steps(events) if s.startswith("Looked again")]  # it heeded it: no round after
    assert events[-1]["status"] == "done"


def test_a_scripted_run_near_its_cap_writes_up_and_says_so(tmp_path):
    effort = Effort("t", 30, 0.0105, "big", frozenset({"web_search", "record_finding", "finish"}), 8, "low")
    r, events = researcher(tmp_path, {"effort": "quick", "fake": {"seconds": 0.05, "idle": 3}}, effort)
    asyncio.run(r.run(FakeEngine()))
    assert events[-1]["status"] == "over_budget" and events[-1]["result"]["summary"]
    assert not [s for s in steps(events) if s.startswith("Looked again")]


def test_a_scripted_deep_run_plans_in_a_round_of_its_own(tmp_path):
    plan = {"searches": ["stomata density"], "sources": "botany texts"}
    r, events = researcher(tmp_path, {"effort": "deep", "fake": {"seconds": 0.05, "plan": plan, "idle": 1}})
    asyncio.run(r.run(FakeEngine()))
    said = steps(events)
    assert said.index("Plan: stomata density (sources: botany texts)") < said.index("Looking it up (simulated researcher)")
    assert r.round_n == 4  # the plan, the work, one idle round, the write-up
    assert events[-1]["status"] == "done"
