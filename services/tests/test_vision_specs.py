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
