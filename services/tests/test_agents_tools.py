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
        self.launches, self.started, self.notes, self.stopped = {}, [], [], []

    async def start(self, program, task, *, by):
        if program == "grok":
            raise Refused("Grok Build isn't installed on this Mac.", "install Grok Build from x.ai")
        launch = SimpleNamespace(id=f"ag_{len(self.started)}", label="Codex", status="working", project_name="Leaf",
                                 budget_usd=None)
        self.started.append((program, task, by))
        self.launches[launch.id] = launch
        return launch

    def wire(self):
        return [{"label": "Codex", "status": "needs-you", "task": "write a tracker", "line": "Asks to run a command",
                 "asks": [{"title": "Run a command"}], "costUsd": None}]

    def find_one(self, words):
        if words and words.lower() not in ("codex",):
            raise Refused(f"No agent called {words}.", "ask what's running")
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
    assert "starting Codex" in said and "Agents panel" in said
    assert conductor.started == [("codex", "write a stomata tracker", "voice")]
    assert "already started" in call(registry, "start_agent", args)
    assert len(conductor.started) == 1
    with pytest.raises(ToolFailed, match="isn't installed"):
        call(registry, "start_agent", {"program": "grok", "task": "count", "asked_with": "give Grok the counting task"},
             said="give Grok the counting task")


def test_agents_reads_as_untrusted_and_never_answers():
    conductor = Conductor()
    registry = tools(conductor)
    said = call(registry, "agents", {})
    assert 'untrusted="true"' in said and "waiting for the user to allow or decline" in said
    assert registry.tools["agents"].kind == "query"


def test_note_and_stop():
    conductor = Conductor()
    registry = tools(conductor)
    call(registry, "start_agent", {"program": "codex", "task": "t", "asked_with": "ask Codex to write a stomata tracker"})
    said = call(registry, "agent_note", {"agent": "Codex", "text": "use micro-sam"})
    assert "Hooke's note, not the user's approval" in said and conductor.notes[0][1] == "use micro-sam"
    with pytest.raises(ToolFailed, match="No agent called Grok"):
        call(registry, "stop_agent", {"agent": "Grok"})
    assert "revoked" in call(registry, "stop_agent", {"agent": "Codex"})
    assert set(registry.tools) == {"start_agent", "agents", "agent_note", "stop_agent"}  # none answers an ask
