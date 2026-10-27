"""A model asked without its key says so in plain words, with where to add it (2026-10-25 review: on an instance with
no keys every first question in the Assistant came back "something went wrong in the app", the client's KeyError)."""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator.agent import Agent, key_needed
from orchestrator.brain.llm import FunctionCallingBrain
from orchestrator.brain.registry import ToolFailed

SAID = "Gemini 3.8 Flash needs your Google key; add it in Models › Keys, then ask again"


def test_a_vision_option_without_its_key_is_named_with_the_key_and_where_to_add_it(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    failed = key_needed("gemini-3.8-flash")
    assert isinstance(failed, ToolFailed) and failed.said() == SAID
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert key_needed("gemini-3.8-flash") is None
    assert key_needed(None) is None and key_needed("no-such-option") is None


def test_the_look_and_function_calling_fail_with_those_words_not_a_key_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    agent = Agent.__new__(Agent)
    agent._visions, agent.hub = {}, SimpleNamespace(settings=SimpleNamespace(sampling="temperature-0.2"))
    with pytest.raises(ToolFailed) as raised:
        agent.vision("gemini-3.8-flash")
    assert raised.value.said() == SAID
    # A client already made (a test's fake, or one made while the key was there) is used as it is.
    agent._visions["gemini-3.8-flash"] = "made"
    assert agent.vision("gemini-3.8-flash") == "made"
    with pytest.raises(ToolFailed) as raised:
        asyncio.run(FunctionCallingBrain().ask("gemini:gemini-3.8-flash@low", b"", "prompt", None))
    assert raised.value.said() == SAID


def test_the_ask_command_replies_with_those_words(monkeypatch):
    from orchestrator import commands

    async def ask(_app, _client, _message):
        raise key_needed("gemini-3.8-flash")

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setitem(commands.COMMANDS, "ask", ask)
    sent = []
    app = SimpleNamespace(hub=SimpleNamespace(execution=None), day=SimpleNamespace(local_only=False))
    client = SimpleNamespace(offer=sent.append)
    monkeypatch.setattr(commands.activity, "before_command", lambda *_a: None)
    monkeypatch.setattr(commands.activity, "command", lambda *_a: None)
    monkeypatch.setattr(commands, "_log_quietly", lambda *_a: False)
    asyncio.run(commands._handle(app, client, {"type": "ask", "text": "what is this?", "requestId": "r1"}))
    import json
    reply = json.loads(sent[-1])
    assert reply["ok"] is False and reply["error"] == SAID and "went wrong" not in reply["error"]
