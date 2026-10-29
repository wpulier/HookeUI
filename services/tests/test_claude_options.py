"""Claude as an option beside Gemini and GPT (options.VISION, BRAIN_FAST; vision.AnthropicVision, brain/llm.py
`_anthropic`): the registry's tools as Anthropic's tools, the picture and the question sent, the reply read the way the
other providers' are, refusals and errors said, the key asked for in words, the partner's look_hard through it, and an
acting-bench arm that chooses it. The provider is faked at the SDK's client; nothing here calls Anthropic."""
import argparse
import asyncio
import base64
import json
from types import SimpleNamespace as NS

import pytest

from orchestrator.brain.dispatch import AnswerProvider, answer_phase
from orchestrator.brain.llm import FunctionCallingBrain
from orchestrator.brain.registry import Registry, ToolFailed
from orchestrator.options import BRAIN_FAST, VISION, Settings
from orchestrator.ticket import issue, using
from orchestrator.vision import JSON_ONLY, AnthropicVision, make_vision
from tests.fakes import Hub

CLAUDE = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1", "claude-haiku-5-5")
JPEG = b"\xff\xd8\xff\xe0synthetic-frame"


def reply(*blocks, stop="end_turn", category=None):
    """A Messages API reply as the SDK gives it: content blocks by type, usage, the stop reason."""
    made = []
    for b in blocks:
        if isinstance(b, str):
            made.append(NS(type="text", text=b))
        elif b[0] == "thinking":
            made.append(NS(type="thinking", thinking=""))
        else:
            made.append(NS(type="tool_use", id=f"toolu_{len(made)}", name=b[0], input=b[1]))
    return NS(model="claude-sonnet-5-5", content=made, stop_reason=stop,
              stop_details=NS(type="refusal", category=category, explanation=None) if stop == "refusal" else None,
              usage=NS(input_tokens=1200, output_tokens=80, output_tokens_details=NS(thinking_tokens=30)))


@pytest.fixture
def claude(monkeypatch):
    """Anthropic's async client stood in for: every request kept, each answered by the next scripted reply."""
    import anthropic

    sent, replies, made = [], [], []

    async def create(**kwargs):
        sent.append(kwargs)
        answer = replies.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def client(**kwargs):
        made.append(kwargs)
        return NS(messages=NS(create=create))

    monkeypatch.setattr(anthropic, "AsyncAnthropic", client)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-anthropic-key")
    return NS(sent=sent, replies=replies, made=made)


def test_the_claude_options_sit_beside_the_others_and_change_no_default():
    for option in CLAUDE:
        o = VISION[option]
        assert o.spec == f"anthropic:{option}@low" and o.needs == "ANTHROPIC_API_KEY" and o.uses == ("anthropic-api",)
        assert o.tier == "terms" and o.permitted
    assert BRAIN_FAST["claude-haiku-5-5"].needs == "ANTHROPIC_API_KEY"
    defaults = Settings()
    assert (defaults.vision, defaults.fast, defaults.brain) == ("gemini-3.8-flash", "gemini-3.8-flash", "json-actions")
    assert Settings().update({"vision": "claude-opus-5-5", "fast": "claude-haiku-5-5", "compare": "claude-fable-5-1"}) == []


def test_every_claude_option_is_priced_and_metered_as_anthropic():
    from research_worker import api_usage
    from research_worker.budget import PRICES

    for option in CLAUDE:
        model = VISION[option].model
        assert set(PRICES[model]) == {"in", "out"} and api_usage.provider_for(model) == "Anthropic"
    assert PRICES["claude-sonnet-5-5"] == {"in": 2.00, "out": 10.00}  # Anthropic's pricing page, 2026-10-29


def test_specs_set_the_effort_and_leave_sampling_alone(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    sonnet = make_vision("anthropic:claude-sonnet-5-5@low", temperature=0.2)  # the Gemini temperature is not Claude's
    assert isinstance(sonnet, AnthropicVision) and (sonnet.model, sonnet.effort) == ("claude-sonnet-5-5", "low")
    assert make_vision("anthropic:claude-opus-5-5").effort == "low"
    assert make_vision("anthropic:claude-fable-5-1@default").effort is None
    assert make_vision("anthropic:claude-haiku-5-5@medium").effort == "medium"


def test_a_claude_option_without_its_key_is_said_with_the_key_and_where_to_add_it(monkeypatch):
    from orchestrator.agent import Agent, key_needed

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    said = "Claude Sonnet 5.5 needs your Anthropic key; add it in Models › Keys, then ask again"
    assert key_needed("claude-sonnet-5-5").said() == said
    agent = Agent.__new__(Agent)
    agent._visions, agent.hub = {}, NS(settings=NS(sampling="temperature-0.2"))
    with pytest.raises(ToolFailed) as raised:
        agent.vision("claude-sonnet-5-5")
    assert raised.value.said() == said
    with pytest.raises(ToolFailed) as raised:
        asyncio.run(FunctionCallingBrain().ask("anthropic:claude-sonnet-5-5@low", JPEG, "prompt", Registry()))
    assert raised.value.said() == said


def test_the_registry_tools_are_anthropic_tools_with_the_same_schemas():
    from tests.fakes import research_tools

    registry = research_tools(Hub(), None)  # the brain's toolbox (toolbox.build) over the test hub
    tools = registry.anthropic_tools()
    openai = {t["name"]: t for t in registry.responses_tools()}
    assert [t["name"] for t in tools] == list(registry.tools)
    for tool in tools:
        assert set(tool) == {"name", "description", "input_schema"}
        assert tool["input_schema"] == openai[tool["name"]]["parameters"]  # after_steps included, as the others get it
        assert tool["input_schema"]["type"] == "object"
    assert registry.anthropic_tools() == tools  # made once while the tools are unchanged


def test_function_calling_sends_the_picture_the_prompt_and_every_tool_and_reads_the_calls_in_order(claude):
    from orchestrator.prompts import OUTLINE_SCHEMA
    from tests.fakes import research_tools

    registry = research_tools(Hub(), None)
    name = next(iter(registry.tools))
    claude.replies.append(reply(("thinking",), "It is probably a stoma.",
                                ("outline", {"targets": [{"box_2d": [1, 2, 3, 4], "label": "stoma", "confidence": 0.8}]}),
                                (name, {"after_steps": [1]}), stop="tool_use"))

    async def go():
        request = issue(Hub(), "turn")
        try:
            with using(request), answer_phase("look"):
                return await FunctionCallingBrain().ask("anthropic:claude-sonnet-5-5@low", JPEG, "What is this?", registry)
        finally:
            request.seal()

    data, used = asyncio.run(go())
    sent = claude.sent[0]
    assert claude.made == [{"api_key": "synthetic-anthropic-key", "max_retries": 0}]
    assert sent["model"] == "claude-sonnet-5-5" and sent["output_config"] == {"effort": "low"}
    assert not {"temperature", "top_p", "top_k", "thinking", "tool_choice"} & set(sent)  # adaptive thinking, auto tools
    assert sent["tools"][0] == {"name": "outline", "description": sent["tools"][0]["description"], "input_schema": OUTLINE_SCHEMA}
    assert [t["name"] for t in sent["tools"][1:]] == list(registry.tools)
    image, text = sent["messages"][0]["content"]
    assert image == {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                 "data": base64.b64encode(JPEG).decode()}}
    assert text == {"type": "text", "text": "What is this?"}
    assert data["answer"] == "It is probably a stoma." and data["targets"][0]["label"] == "stoma"
    assert data["actions"] == [{"tool": name, "args": {}, "after_steps": [1]}]
    assert used["model"] == "claude-sonnet-5-5"


def test_a_refused_function_calling_turn_is_a_failure_said_never_an_empty_answer(claude):
    claude.replies.append(reply(stop="refusal", category="bio"))
    with pytest.raises(RuntimeError, match=r"declined to answer \(bio\)"):
        asyncio.run(FunctionCallingBrain().ask("anthropic:claude-opus-5-5@low", JPEG, "prompt", Registry()))


def test_a_look_sends_the_pictures_first_asks_for_json_and_counts_thinking_once(claude):
    claude.replies.append(reply(("thinking",), '```json\n{"answer": "A stoma.", "label": "stoma"}\n```'))
    vision = make_vision(VISION["claude-sonnet-5-5"].spec)
    result = asyncio.run(vision.ask([JPEG, b"\x89PNG\r\n\x1a\nwindow"], "Name it as JSON.", max_output_tokens=600))
    sent = claude.sent[0]
    assert sent["system"] == JSON_ONLY and sent["max_tokens"] == 600 and sent["output_config"] == {"effort": "low"}
    first, second, question = sent["messages"][0]["content"]
    assert (first["source"]["media_type"], second["source"]["media_type"]) == ("image/jpeg", "image/png")
    assert question == {"type": "text", "text": "Name it as JSON."}
    assert result.data == {"answer": "A stoma.", "label": "stoma"} and result.error == ""
    assert result.usage == {"input": 1200, "output": 80}  # output already includes thinking: never added again


def test_a_refused_or_failed_look_keeps_why_and_an_answer_attempt_fails_saying_it(claude):
    import httpx2

    import anthropic
    from orchestrator.brain.dispatch import refusal

    claude.replies += [reply(stop="refusal", category="cyber"),
                       anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))]
    vision = make_vision(VISION["claude-haiku-5-5"].spec)
    refused = asyncio.run(vision.ask([JPEG], "prompt"))
    assert refused.data is None and refused.error == "Refusal: claude-sonnet-5-5 declined to answer (cyber)"
    offline = asyncio.run(vision.ask([JPEG], "prompt"))
    assert offline.data is None and offline.error.startswith("APIConnectionError: ")
    assert refusal(offline.error).kind == "connection"  # said as no connection, as OpenAI's same error is

    claude.replies.append(reply(stop="refusal", category=None))

    async def answer():
        request = issue(Hub(), "turn")
        try:
            with using(request), answer_phase("look"):
                await AnswerProvider(vision, "claude-haiku-5-5").ask([JPEG], "prompt")
        finally:
            request.seal()

    with pytest.raises(RuntimeError, match="claude-haiku-5-5 did not answer \\(Refusal: .*declined to answer\\)"):
        asyncio.run(answer())


def partner_tools(vision_option, camera=JPEG):
    from orchestrator.agent import Agent
    from orchestrator.voice.partner_tools import PartnerTools

    hub = NS(settings=NS(vision=vision_option, sampling="temperature-0.2"))

    async def seen(max_side=768):
        return (camera, 7, None) if camera else None

    async def snapshot():
        return {"jpeg": b"\xff\xd8window"}

    return PartnerTools(NS(agent=Agent(hub)), hub, NS(camera=seen, snapshot=snapshot))


def test_look_hard_asks_the_chosen_claude_with_the_frame_the_window_and_the_question(claude):
    claude.replies.append(reply('{"answer": "Three round cells; the middle one is largest."}'))
    found = asyncio.run(partner_tools("claude-sonnet-5-5").look_hard({"question": "Which is largest?", "window": True}))
    assert found == {"ok": True, "answer": "Three round cells; the middle one is largest.", "model": "claude-sonnet-5-5"}
    camera, window, question = claude.sent[0]["messages"][0]["content"]
    assert base64.b64decode(camera["source"]["data"]) == JPEG and base64.b64decode(window["source"]["data"]) == b"\xff\xd8window"
    assert question["type"] == "text" and question["text"].endswith("Question: Which is largest?")


def test_look_hard_without_the_anthropic_key_says_which_key_and_where(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    found = asyncio.run(partner_tools("claude-fable-5-1").look_hard({"question": "What is this?"}))
    assert found == {"ok": False, "error": "Claude Fable 5.1 needs your Anthropic key; add it in Models › Keys, then ask again"}


def test_the_acting_bench_takes_a_claude_arm(tmp_path, monkeypatch):
    """`--arm claude:vision=claude-sonnet-5-5,fast=claude-haiku-5-5`: each case runs with those settings, which the
    world's settings take without a problem (World raises on any)."""
    from bench.acting import run
    from bench.acting.run import Report

    cases = tmp_path / "live.json"
    cases.write_text(json.dumps({"pictures": {"leaf": {"picture": "leaf.jpg", "slide": {}}},
                                 "cases": [{"id": "name-it", "on": "leaf", "events": [], "expect": {}}]}))
    monkeypatch.setattr("orchestrator.keys.load_keys", lambda *a, **k: {})
    ran = []

    async def run_case(case, scale, models):
        ran.append((case["settings"], models))
        return Report(case["id"], True)

    monkeypatch.setattr(run, "run_case", run_case)
    monkeypatch.setenv("MICROMIND_HOME", str(tmp_path / "home"))
    args = argparse.Namespace(cases=str(cases), case=None, arm=["claude:vision=claude-sonnet-5-5,fast=claude-haiku-5-5"],
                              set=[], attempts=1, out=str(tmp_path / "out"), verbose=False)
    run.live(args)
    assert ran == [({"vision": "claude-sonnet-5-5", "fast": "claude-haiku-5-5"}, "real")]
    assert Settings().update(ran[0][0]) == []
    summary = json.loads((tmp_path / "out" / "claude" / "summary.json").read_text())
    assert summary["settings"] == {"vision": "claude-sonnet-5-5", "fast": "claude-haiku-5-5"}
