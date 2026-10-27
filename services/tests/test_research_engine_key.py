"""A researcher asked for while the model researchers use has no key is refused at once, in plain words naming the key
and where to add it (0.4.0 release candidate: with a Google key alone, Gemini as the Assistant and GPT-6.1 Sol for
researchers, every researcher ended "could not finish: Something went wrong" on its card, Tab and pill). With the key
there, from the keys file or the Keychain, it starts as before."""
import asyncio
import json

import pytest

from orchestrator import keychain
from orchestrator.brain.registry import ToolContext
from orchestrator.keys import load_keys, sources
from orchestrator.options import Settings
from orchestrator.research import ResearchManager
from orchestrator.research.manager import engine_key_refusal
from orchestrator.research.policy import ResearchRefused
from tests.fakes import research_tools
from tests.test_research import FakeHub

SAYS = "GPT-6.1 Sol, the model researchers use, needs your OpenAI key"
NEXT = "add it in Models › Keys, then try again"


def manager_with(tmp_path, research: str | None = None):
    hub = FakeHub()
    hub.settings = Settings()  # researchers think with the default model, GPT-6.1 Sol
    if research is not None:
        hub.settings.research = research
    manager = ResearchManager(hub, root=tmp_path, today=lambda: "2026-10-26")
    manager._pump = lambda: None  # nothing runs: what is asked of the research process is not under test here
    return manager


def started_nothing(manager, tmp_path):
    return (not manager.sessions and not manager.queue and not manager.proposals and not manager.spending._held
            and not list(tmp_path.rglob("session.json")))


def test_a_researcher_without_its_models_key_is_refused_at_once_and_nothing_starts(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-google-key")  # the usual first setup: a Google key only
    manager = manager_with(tmp_path)
    with pytest.raises(ResearchRefused) as refused:
        asyncio.run(manager.start("what is this stoma", effort="quick", words="look into what this is",
                                  asked_with="look into what this is"))
    assert str(refused.value) == SAYS and refused.value.next == NEXT
    assert started_nothing(manager, tmp_path)
    # Before its admission: a video not asked for in the user's words is not filed as a proposal it could never start.
    with pytest.raises(ResearchRefused) as refused:
        asyncio.run(manager.start("a video of it", effort="standard", deliverable="video", words="what is it"))
    assert str(refused.value) == SAYS and started_nothing(manager, tmp_path)


def test_the_assistant_is_told_the_key_and_where_to_add_it_and_no_researcher_is_said_to_start(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-google-key")
    manager = manager_with(tmp_path)
    registry = research_tools(manager.hub, manager)
    words = "research what this stoma is"

    async def go(args):
        run = registry.start("start_researcher", args, ToolContext("chat", 1, 7, words))
        await run.task
        return run

    run = asyncio.run(go({"brief": words, "effort": "quick", "asked_with": words}))
    assert run.ok is False and run.summary == f"{SAYS}; {NEXT}"
    assert "Researcher" not in run.summary and "went wrong" not in run.summary
    run = asyncio.run(go({"brief": "a video of it", "effort": "standard", "deliverable": "video"}))
    assert run.ok is False and run.summary == f"{SAYS}; {NEXT}"  # never "ask the user whether to make it"
    assert started_nothing(manager, tmp_path)


def test_a_key_from_the_keys_file_starts_it_as_before(tmp_path, monkeypatch):
    keys = tmp_path / "keys.txt"
    keys.write_text("OPENAI_API_KEY=test-openai-key\n")
    load_keys(keys)  # as the app does at its start
    assert sources(keys)["OPENAI_API_KEY"] == "file"  # the keys file is where it is, nothing else
    manager = manager_with(tmp_path / "research")
    session = asyncio.run(manager.start("what is this stoma", effort="quick", words="look into what this is"))
    assert session.status == "queued" and session.engine == "gpt-6.1-sol"
    assert list(manager.sessions) == [session.rid] and manager.queue == [session.rid]


def test_a_key_saved_in_the_keychain_starts_it_as_before(tmp_path, monkeypatch):
    saved = tmp_path / "keychain.json"
    saved.write_text(json.dumps({"OPENAI_API_KEY": "test-openai-key"}))
    monkeypatch.setenv("MICROMIND_KEYCHAIN", f"file:{saved}")  # a stand-in Keychain, never this Mac's
    monkeypatch.setattr(keychain, "_read", {})
    load_keys(tmp_path / "no-keys-file.txt")
    assert sources(tmp_path / "no-keys-file.txt")["OPENAI_API_KEY"] == "keychain"  # the Keychain is where it is
    manager = manager_with(tmp_path / "research")
    session = asyncio.run(manager.start("what is this stoma", effort="quick", words="look into what this is"))
    assert session.status == "queued" and session.engine == "gpt-6.1-sol"


def test_the_model_that_runs_decides_which_key(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-google-key")
    # Gemini chosen for researchers: a Google key is all it needs.
    manager = manager_with(tmp_path / "gemini", research="gemini-3.8-flash")
    assert asyncio.run(manager.start("what is this", effort="quick", words="look into it")).engine == "gemini-3.8-flash"
    # The research process's own override is what runs (research_worker/engines.make_engine), so it decides.
    monkeypatch.setenv("MICROMIND_RESEARCH_ENGINE", "gemini-3.8-flash")
    assert engine_key_refusal("gpt-6.1-sol") is None
    monkeypatch.setenv("MICROMIND_RESEARCH_ENGINE", "gpt-6.1-sol")
    assert str(engine_key_refusal("gemini-3.8-flash")) == SAYS
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.delenv("MICROMIND_RESEARCH_ENGINE")
    gemini = engine_key_refusal("gemini-3.8-flash")
    assert str(gemini) == "Gemini 3.8 Flash, the model researchers use, needs your Google key" and gemini.next == NEXT


def test_a_scripted_researcher_needs_no_key(tmp_path):
    manager = manager_with(tmp_path)
    assert asyncio.run(manager.start("t", effort="quick", fake={"seconds": 0.1})).engine == "fake"
    assert engine_key_refusal("fake") is None and engine_key_refusal("no-such-engine") is None


def test_a_worker_channel_that_runs_no_model_needs_no_key(tmp_path, monkeypatch):
    """The acting bench's stand-in research process (bench/acting/world.py ResearchWorker) never calls a model: it says
    runs_a_model = False, so a researcher started through it is not refused for a key it would never use."""

    class StandIn:
        alive = True
        runs_a_model = False

    manager = manager_with(tmp_path)
    manager._worker = StandIn()
    session = asyncio.run(manager.start("what is this stoma", effort="quick", words="look into what this is"))
    assert session.status == "queued" and list(manager.sessions) == [session.rid]
    # The real process channel (or none yet: it starts on the first spawn) still needs the key.
    manager_real = manager_with(tmp_path / "real")
    with pytest.raises(ResearchRefused):
        asyncio.run(manager_real.start("what is this stoma", effort="quick", words="look into what this is"))
