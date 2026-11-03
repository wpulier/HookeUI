"""The brain's tools on outside agents (ADR 0029): start only on the user's own words, once per request; what agents
say comes back as untrusted text; a note is Hooke's; no tool answers an agent's ask."""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator.agents.conductor import Refused
from orchestrator.brain.registry import Registry, ToolContext, ToolFailed
from orchestrator.brain.toolbox.agents import add_agent_tools


class Conductor:
    def __init__(self):
        self.launches, self.started, self.notes, self.stopped, self.answers = {}, [], [], [], []

    def summary(self):
        return ["Codex is waiting for you to allow it to run a command",
                "Claude Code finished, and its report is back for your review"]

    def waiting(self, words):
        if not any(getattr(l, "asks", None) for l in self.launches.values()):
            raise Refused("No outside agent is asking for anything now.", next=None)
        launch = next(l for l in self.launches.values() if l.asks)
        return launch, launch.asks[0]

    async def answer(self, lid, permission, allow, always=False):
        self.answers.append((lid, permission, allow, always))

    async def start(self, program, task, *, by):
        if program == "grok":
            raise Refused("Grok Build isn't installed on this Mac.", next="install Grok Build from x.ai")
        launch = SimpleNamespace(id=f"ag_{len(self.started)}", label="Codex", status="working", project_name="Leaf",
                                 budget_usd=None, asks=[])
        self.started.append((program, task, by))
        self.launches[launch.id] = launch
        return launch

    def wire(self):
        return [{"label": "Codex", "status": "needs-you", "task": "write a tracker", "line": "Asks to run a command",
                 "asks": [{"title": "Run a command"}], "costUsd": None}]

    def find_one(self, words):
        if words and words.lower() not in ("codex",):
            raise Refused(f"No agent called {words}.", next="ask what's running")
        return next(iter(self.launches.values()))

    async def note(self, lid, text, *, by):
        self.notes.append((lid, text, by))
        return "steered"

    async def stop(self, lid):
        self.stopped.append(lid)
        launch = self.launches[lid]
        launch.label = "Codex"
        return launch


def tools(conductor):
    registry = Registry()
    services = SimpleNamespace(agents=lambda: conductor, research=None, agent=SimpleNamespace(conversation=None))
    add_agent_tools(registry, services)
    return registry


def call(registry, name, args, said="ask Codex to write a stomata tracker", channel="voice"):
    ctx = ToolContext(channel=channel, turn_id=1, frame_id=None, question=said)
    return asyncio.run(registry.tools[name].handler(ctx, args))


def test_start_only_on_the_users_words_and_once_per_request():
    conductor = Conductor()
    registry = tools(conductor)
    with pytest.raises(ToolFailed, match="only when the user asked"):
        call(registry, "start_agent", {"program": "codex", "task": "write a tracker", "asked_with": "make me a tracker"},
             said="what is this cell?")
    args = {"program": "codex", "task": "write a stomata tracker", "asked_with": "ask Codex to write a stomata tracker"}
    said = call(registry, "start_agent", args)
    assert said.startswith("Codex is starting on it now in this Project") and "Leaf" not in said and "Study" not in said
    assert conductor.started == [("codex", "write a stomata tracker", "voice")]
    assert "already started" in call(registry, "start_agent", args)
    assert len(conductor.started) == 1
    with pytest.raises(ToolFailed, match="isn't installed"):
        call(registry, "start_agent", {"program": "grok", "task": "count", "asked_with": "give Grok the counting task"},
             said="give Grok the counting task")


def test_whats_everyone_doing_is_short_and_agents_words_are_untrusted():
    conductor = Conductor()
    registry = tools(conductor)
    said = call(registry, "agents", {})
    first = said.splitlines()[0]
    assert first.startswith("in one or two sentences: Codex is waiting for you to allow it to run a command; Claude")
    assert "ag_" not in first and "assignment" not in first  # no internal words in what is said
    assert 'untrusted="true"' in said
    assert registry.tools["agents"].kind == "query"


def test_an_ask_is_answered_only_with_the_users_own_words():
    conductor = Conductor()
    registry = tools(conductor)
    call(registry, "start_agent", {"program": "codex", "task": "t", "asked_with": "ask Codex to write a stomata tracker"})
    with pytest.raises(ToolFailed, match="No outside agent is asking"):
        call(registry, "answer_agent", {"answer": "allow", "asked_with": "yes"}, said="yes")
    launch = next(iter(conductor.launches.values()))
    launch.asks = [{"id": "codex:0", "title": "Run a command"}]
    with pytest.raises(ToolFailed, match="only by the user's own yes or no"):
        call(registry, "answer_agent", {"answer": "allow", "asked_with": "yes go ahead"}, said="what is this cell?")
    assert conductor.answers == []
    said = call(registry, "answer_agent", {"answer": "allow", "always": True, "asked_with": "yes, for the session"},
                said="yes, for the session")
    assert conductor.answers == [(launch.id, "codex:0", True, True)] and "allowed Codex to run a command" in said
    said = call(registry, "answer_agent", {"answer": "decline", "always": True, "asked_with": "no"}, said="no")
    assert conductor.answers[-1] == (launch.id, "codex:0", False, False) and said.startswith("declined")


def test_note_and_stop():
    conductor = Conductor()
    registry = tools(conductor)
    call(registry, "start_agent", {"program": "codex", "task": "t", "asked_with": "ask Codex to write a stomata tracker"})
    said = call(registry, "agent_note", {"agent": "Codex", "text": "use micro-sam"})
    assert "Hooke's note, not the user's approval" in said and conductor.notes[0][1] == "use micro-sam"
    with pytest.raises(ToolFailed, match="No agent called Grok"):
        call(registry, "stop_agent", {"agent": "Grok"})
    assert call(registry, "stop_agent", {"agent": "Codex"}) == "stopped Codex"
    assert set(registry.tools) == {"start_agent", "agents", "agent_note", "stop_agent", "answer_agent"}
