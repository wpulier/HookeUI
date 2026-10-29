"""Claude as the brain with tools (BRAIN "fc" with a Claude vision option; brain/llm.py `_anthropic`, over the same
Messages API client as vision.AnthropicVision): the registry's tools as Anthropic's, the picture and the prompt sent with
the effort and server-side fallbacks the look uses, the reply read the way the other providers' are, refusals and
Anthropic's own errors said, the key asked for in words, and an acting-bench arm that chooses it. Anthropic is stood in
for at the HTTP client; nothing here calls it."""
import argparse
import asyncio
import base64
import json
from types import SimpleNamespace as NS

import pytest

from orchestrator.brain.dispatch import answer_phase
from orchestrator.brain.llm import FunctionCallingBrain
from orchestrator.brain.registry import Registry, ToolFailed
from orchestrator.options import VISION, Settings
from orchestrator.ticket import issue, using
from tests.fakes import Hub, research_tools

JPEG = b"\xff\xd8\xff\xe0synthetic-frame"
KEY = "synthetic-anthropic-key"


def stood_in(brain: FunctionCallingBrain, reply: dict, status: int = 200, fast: bool = False) -> dict:
    """Anthropic answering `reply` to whatever the brain posts; what it posted is kept in the returned dict."""
    sent = {}

    class Response:
        is_error = status >= 400
        status_code = status
        text = ""

        def json(self):
            return reply

    class Client:
        async def post(self, path, json):
            sent.update(path=path, body=json)
            return Response()

    brain._clients[("anthropic-fast" if fast else "anthropic", KEY)] = Client()
    return sent


def answer(*blocks, stop="end_turn", category=None, model="claude-sonnet-5-5"):
    content = []
    for b in blocks:
        if isinstance(b, str):
            content.append({"type": "text", "text": b})
        elif b[0] == "thinking":
            content.append({"type": "thinking", "thinking": "", "signature": "s"})
        else:
            content.append({"type": "tool_use", "id": f"toolu_{len(content)}", "name": b[0], "input": b[1]})
    return {"model": model, "stop_reason": stop, "content": content,
            "stop_details": {"type": "refusal", "category": category} if stop == "refusal" else None,
            "usage": {"input_tokens": 47000, "output_tokens": 200}}


def ask(brain, spec, registry=None):
    async def go():
        request = issue(Hub(), "turn")
        try:
            with using(request), answer_phase("look"):
                return await brain.ask(spec, JPEG, "What is this?", registry or Registry())
        finally:
            request.seal()

    return asyncio.run(go())


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)


def test_the_registry_tools_are_anthropic_tools_with_the_same_schemas():
    registry = research_tools(Hub(), None)
    tools = registry.anthropic_tools()
    openai = {t["name"]: t for t in registry.responses_tools()}
    assert [t["name"] for t in tools] == list(registry.tools)
    for tool in tools:
        assert set(tool) == {"name", "description", "input_schema"}
        assert tool["input_schema"] == openai[tool["name"]]["parameters"]  # after_steps included, as the others get it
        assert tool["input_schema"]["type"] == "object"
    assert registry.anthropic_tools() == tools


def test_a_claude_brain_turn_sends_the_picture_the_prompt_and_every_tool_and_reads_the_calls_in_order(key):
    from orchestrator.prompts import OUTLINE_SCHEMA

    registry = research_tools(Hub(), None)
    name = next(iter(registry.tools))
    brain = FunctionCallingBrain()
    sent = stood_in(brain, answer(("thinking",), "It is probably a stoma.",
                                  ("outline", {"targets": [{"box_2d": [1, 2, 3, 4], "label": "stoma", "confidence": 0.8}]}),
                                  ("missing", [1]), (name, {"after_steps": [1]}), stop="tool_use"))
    data, used = ask(brain, VISION["claude-sonnet-5-5"].spec, registry)
    body = sent["body"]
    assert sent["path"] == "/v1/messages" and body["model"] == "claude-sonnet-5-5"
    assert body["output_config"] == {"effort": "low"} and body["fallbacks"] == "default" and "speed" not in body
    assert not {"temperature", "top_p", "top_k", "thinking", "tool_choice"} & set(body)  # adaptive thinking, auto tools
    assert body["tools"][0] == {"name": "outline", "description": body["tools"][0]["description"], "input_schema": OUTLINE_SCHEMA}
    assert [t["name"] for t in body["tools"][1:]] == list(registry.tools)
    image, text = body["messages"][0]["content"]
    assert image == {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                 "data": base64.b64encode(JPEG).decode()}}
    assert text == {"type": "text", "text": "What is this?"}
    assert data["answer"] == "It is probably a stoma." and data["targets"][0]["label"] == "stoma"
    # a call whose input is not an object keeps its slot as the missing call, never a shifted later one
    assert data["actions"] == [None, {"tool": name, "args": {}, "after_steps": [1]}]
    assert used["model"] == "claude-sonnet-5-5"


def test_opus_fast_mode_asks_for_fast_speed_on_its_own_client(key, monkeypatch):
    brain = FunctionCallingBrain()
    sent = stood_in(brain, answer("Done."), fast=True)
    ask(brain, VISION["claude-opus-5-5-fast"].spec)
    assert sent["body"]["speed"] == "fast" and sent["body"]["model"] == "claude-opus-5-5"


def test_the_brains_client_is_the_looks_with_fallbacks_and_fast_mode_betas(monkeypatch):
    from orchestrator.vision import AnthropicVision

    plain, fast = AnthropicVision.http_client(KEY, False), AnthropicVision.http_client(KEY, True)
    assert plain.headers["anthropic-beta"] == AnthropicVision.BETAS and plain.headers["x-api-key"] == KEY
    assert fast.headers["anthropic-beta"] == AnthropicVision.BETAS + "," + AnthropicVision.FAST_BETA
    assert str(plain.base_url).startswith("https://api.anthropic.com")


def test_a_refused_brain_turn_is_said_as_a_refusal_never_an_empty_answer(key):
    brain = FunctionCallingBrain()
    stood_in(brain, answer(stop="refusal", category="bio"))
    with pytest.raises(RuntimeError, match=r"refused by the model's safeguards \(bio\)"):
        ask(brain, VISION["claude-opus-5-5"].spec)


def test_anthropics_own_reason_is_said(key):
    brain = FunctionCallingBrain()
    stood_in(brain, {"type": "error", "error": {"type": "invalid_request_error",
                                                "message": "Your credit balance is too low to access the Anthropic API."}}, 400)
    with pytest.raises(RuntimeError, match="Anthropic 400: Your credit balance is too low"):
        ask(brain, VISION["claude-sonnet-5-5"].spec)


def test_without_the_key_the_brain_says_which_key_and_where(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ToolFailed) as raised:
        asyncio.run(FunctionCallingBrain().ask(VISION["claude-sonnet-5-5"].spec, JPEG, "prompt", Registry()))
    assert raised.value.said() == "Claude Sonnet 5.5 needs your Anthropic key; add it in Models › Keys, then ask again"


def test_sonnet_is_a_choice_beside_opus_and_no_default_changed():
    from research_worker import api_usage
    from research_worker.budget import PRICES

    sonnet = VISION["claude-sonnet-5-5"]
    assert sonnet.spec == "anthropic:claude-sonnet-5-5@low" and sonnet.needs == "ANTHROPIC_API_KEY"
    assert sonnet.uses == ("anthropic-api",) and sonnet.permitted
    defaults = Settings()
    assert (defaults.vision, defaults.fast, defaults.brain) == ("gemini-3.8-flash", "gemini-3.8-flash", "json-actions")
    assert Settings().update({"vision": "claude-sonnet-5-5", "brain": "fc"}) == []
    assert PRICES["claude-sonnet-5-5"] == {"in": 2.00, "out": 10.00}  # Anthropic's pricing page, read 2026-10-29
    assert api_usage.provider_for("claude-sonnet-5-5") == "Anthropic"


def test_the_acting_bench_takes_a_claude_brain_arm(tmp_path, monkeypatch):
    """`--arm claude:vision=claude-sonnet-5-5,brain=fc`: each case runs with those settings, which the world's settings
    take without a problem (World raises on any)."""
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
    args = argparse.Namespace(cases=str(cases), case=None, arm=["claude:vision=claude-sonnet-5-5,brain=fc"],
                              set=[], attempts=1, out=str(tmp_path / "out"), verbose=False)
    run.live(args)
    assert ran == [({"vision": "claude-sonnet-5-5", "brain": "fc"}, "real")]
    assert Settings().update(ran[0][0]) == []
    assert json.loads((tmp_path / "out" / "claude" / "summary.json").read_text())["settings"] == ran[0][0]
