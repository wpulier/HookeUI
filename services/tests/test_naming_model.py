"""Which model names an outline (option NAMING_MODEL, Agent.naming_option): the close look's model by default (Will's
click on his leaf footage, 29 Oct: Gemini 4 Argon named the stoma 7 of 7; Claude Opus 5.5 fast marked it unsure 5 of
7), the quick look's on request, and the quick look's when the close look's key is missing so a name still comes."""
from types import SimpleNamespace

from orchestrator.agent import Agent
from orchestrator.options import Settings


def agent_with(settings):
    agent = Agent.__new__(Agent)
    agent.hub = SimpleNamespace(settings=settings)
    return agent


def test_the_close_look_names_an_outline_by_default_and_the_quick_look_on_request(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "set")
    settings = Settings()
    assert settings.naming_model == "close-look"
    assert agent_with(settings).naming_option() == settings.look_hard == "gemini-4-argon"
    settings.naming_model = "quick-look"
    assert agent_with(settings).naming_option() == settings.vision


def test_without_the_close_looks_key_the_quick_look_names_it(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    settings = Settings()
    assert agent_with(settings).naming_option() == settings.vision
