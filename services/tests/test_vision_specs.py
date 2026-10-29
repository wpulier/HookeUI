"""Model specs (options.VISION) become the right adapter settings (vision.make_vision)."""
from orchestrator.vision import make_vision


def test_specs_set_thinking_resolution_and_service_tier(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    luna = make_vision("openai:gpt-5.6-luna@none+priority")
    assert (luna.model, luna.reasoning_effort, luna.service_tier) == ("gpt-5.6-luna", "none", "priority")
    assert make_vision("openai:gpt-6-luna@none").service_tier is None
    flash = make_vision("gemini:gemini-3.5-flash@minimal")
    assert flash.model == "gemini-3.5-flash"


def test_sampling_sends_temperature_0_2_or_leaves_it_to_the_model(monkeypatch):
    """SAMPLING: every Gemini call through the agent sends 0.2 ("temperature-0.2") or no temperature at all
    ("provider-default", Google's advice for Gemini 3); each is its own client."""
    from types import SimpleNamespace

    from orchestrator.agent import Agent

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    assert make_vision("gemini:gemini-3.8-flash@low").temperature == 0.2
    assert make_vision("gemini:gemini-3.8-flash@low", temperature=None).temperature is None
    hub = SimpleNamespace(settings=SimpleNamespace(sampling="temperature-0.2"))
    agent = Agent(hub)
    fixed = agent.vision("gemini-3.8-flash")
    hub.settings.sampling = "provider-default"
    default = agent.vision("gemini-3.8-flash")
    assert fixed.temperature == 0.2 and default.temperature is None and agent.vision("gemini-3.8-flash") is default


def test_every_fast_pass_option_is_a_vision_option():
    from orchestrator.options import BRAIN_FAST, VISION

    assert all(o == "off" or o in VISION for o in BRAIN_FAST)


def test_claude_specs_set_effort_and_fast_mode(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    opus = make_vision("anthropic:claude-opus-5-5@low")
    assert (opus.model, opus.effort, opus.fast) == ("claude-opus-5-5", "low", False)
    fast = make_vision("anthropic:claude-opus-5-5@low+fast")
    assert (fast.effort, fast.fast) == ("low", True)
    assert make_vision("anthropic:claude-opus-5-5@default").effort is None


def _claude_replying(monkeypatch, reply: dict):
    import asyncio

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    vision = make_vision("anthropic:claude-opus-5-5@low+fast")
    sent = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return reply

    class Client:
        async def post(self, path, json):
            sent.update(path=path, body=json)
            return Response()

    async def ready():
        return Client()

    vision._ready = ready
    return asyncio.run(vision.ask([b"\xff\xd8\xff"], "Where? JSON {\"answer\": \"...\"}")), sent


def test_claude_answers_are_read_and_fast_mode_and_fallbacks_are_asked_for(monkeypatch):
    result, sent = _claude_replying(monkeypatch, {
        "model": "claude-opus-5-5", "stop_reason": "end_turn", "usage": {"input_tokens": 1000, "output_tokens": 50},
        "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": '{"answer": "a stoma"}'}]})
    assert result.data == {"answer": "a stoma"} and not result.error
    assert result.usage == {"input": 1000, "output": 50}
    body = sent["body"]
    assert sent["path"] == "/v1/messages" and body["speed"] == "fast" and body["fallbacks"] == "default"
    assert body["output_config"] == {"effort": "low"} and body["messages"][0]["content"][0]["type"] == "image"


def test_a_claude_refusal_is_said_as_a_refusal_never_read_as_an_answer(monkeypatch):
    result, _ = _claude_replying(monkeypatch, {
        "model": "claude-opus-5-5", "stop_reason": "refusal", "stop_details": {"type": "refusal", "category": "bio"},
        "usage": {"input_tokens": 1000, "output_tokens": 0}, "content": []})
    assert result.data is None and "refused" in result.error and "bio" in result.error
