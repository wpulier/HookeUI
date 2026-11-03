"""The Agents panel's routes (ADR 0029): only the Hooke window may start, reply, answer or stop; an outside agent's
token never reaches them."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.agents import api
from orchestrator.agents.conductor import Refused
from orchestrator.origin import studio_origin

BASE = "http://127.0.0.1:8770"
WINDOW = {"Origin": BASE, "X-MicroscopeMind": "agents"}


class Conductor:
    def __init__(self):
        self.calls = []
        self.side = SimpleNamespace(door="")

    def wire(self):
        return []

    async def programs(self, fresh=False):
        return [{"id": "codex", "installed": True}]

    async def start(self, program, task, **kw):
        self.calls.append(("start", program, task, kw))
        return SimpleNamespace(wire=lambda: {"id": "ag_1", "program": program})

    async def answer(self, lid, permission, allow, always=False):
        self.calls.append(("answer", lid, permission, allow, always))

    async def note(self, lid, text, by):
        if lid == "gone":
            raise Refused("Codex has ended.", "start a new agent to continue")
        self.calls.append(("note", lid, text))
        return "steered"


@pytest.fixture
def window(monkeypatch):
    monkeypatch.delenv("HOOKE_STUDIO_TOKEN", raising=False)
    conductor = Conductor()
    app = FastAPI()
    api.register(app, SimpleNamespace(conductor=conductor), studio_origin, "X-MicroscopeMind")
    with TestClient(app, base_url=BASE) as client:
        yield client, conductor


def test_the_window_starts_answers_and_replies(window):
    client, conductor = window
    assert client.get("/api/agents", headers={"Referer": BASE + "/"}).json()["programs"][0]["id"] == "codex"
    started = client.post("/api/agents", json={"program": "codex", "task": "count"}, headers=WINDOW)
    assert started.status_code == 202 and conductor.calls[0][:3] == ("start", "codex", "count")
    assert client.post("/api/agents/ag_1/answer", json={"permission": "codex:0", "answer": "allow", "always": True},
                       headers=WINDOW).json() == {"answered": "allow"}
    assert conductor.calls[-1] == ("answer", "ag_1", "codex:0", True, True)
    assert client.post("/api/agents/ag_1/note", json={"text": "use micro-sam"}, headers=WINDOW).json() == {
        "sent": "steered"}
    ended = client.post("/api/agents/gone/note", json={"text": "x"}, headers=WINDOW)
    assert ended.status_code == 409 and ended.json()["next"] == "start a new agent to continue"


def test_nothing_but_the_window_reaches_them(window):
    client, conductor = window
    for headers in ({}, {"Origin": "https://example.com", "X-MicroscopeMind": "agents"},
                    {"Origin": BASE, "X-MicroscopeMind": "agent-connections"},
                    {**WINDOW, "Authorization": "Bearer hk_local_an-outside-agent"}):
        assert client.post("/api/agents", json={"program": "codex", "task": "x"}, headers=headers).status_code == 403
        assert client.post("/api/agents/ag_1/answer", json={"permission": "p", "answer": "allow"},
                           headers=headers).status_code == 403
    assert client.get("/api/agents", headers={"Origin": "https://example.com"}).status_code == 403
    assert client.post("/api/agents/ag_1/answer", json={"permission": "p", "answer": "yes"},
                       headers=WINDOW).status_code == 400
    assert conductor.calls == []


def test_in_the_mac_app_only_its_window_token_counts(window, monkeypatch):
    client, conductor = window
    monkeypatch.setenv("HOOKE_STUDIO_TOKEN", "the-window-token")
    assert client.post("/api/agents", json={"program": "codex", "task": "x"}, headers=WINDOW).status_code == 403
    assert client.get("/api/agents").status_code == 403
    ok = client.post("/api/agents", json={"program": "codex", "task": "x"},
                     headers={**WINDOW, "X-Hooke-Studio": "the-window-token"})
    assert ok.status_code == 202
