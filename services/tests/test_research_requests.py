"""A researcher asks the user for evidence it lacks (UX document §2; docs/design/2026-10-07-the-partner.md §2.2):
said once where it was asked for, filed on the researcher, answered by the user's next note, kept across a
restart. Nothing waits for the answer, and nothing records or saves for the user (ADR 0008)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

from orchestrator.research import ResearchManager
from orchestrator.day import DayStore
from orchestrator.research.run import Delivery
from tests.fakes import announcer
from tests.test_research import FakeHub, until
from tests.test_research_content import researcher

FOOTAGE = {"what": "15 more seconds of this field", "why": "how the odd ones move", "seconds": 15}


def test_a_researcher_asks_at_most_twice_and_carries_on(tmp_path):
    r, events = researcher(tmp_path, {"effort": "quick"})
    assert "request_evidence" in r.tool_names()
    first, _ = asyncio.run(r.call_tool("request_evidence", FOOTAGE))
    assert first["ok"] and first["id"] == "q1" and "carry on" in first["note"]
    asked = [e for e in events if e.get("event") == "request"]
    assert asked and asked[0]["what"] == FOOTAGE["what"] and asked[0]["seconds"] == 15
    asyncio.run(r.call_tool("request_evidence", {"what": "the objective in use", "why": "the cells' size"}))
    third, _ = asyncio.run(r.call_tool("request_evidence", {"what": "a sharper view", "why": "the edges"}))
    assert "most in one run" in third["error"]
    fresh, _ = researcher(tmp_path / "fresh", {"effort": "quick"})
    empty, _ = asyncio.run(fresh.call_tool("request_evidence", {"what": "", "why": "x"}))
    assert "say what you need" in empty["error"]
    for spec in ({"effort": "describe", "purpose": "description"}, {"effort": "quick", "deliverable": "answer"}):
        other, _ = researcher(tmp_path / spec["effort"], spec)
        assert "request_evidence" not in other.tool_names()  # nobody to ask, or it answers from its work


def test_a_request_is_said_filed_answered_by_a_note_and_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("MICROMIND_RESEARCH_ENGINE", "fake")

    async def go():
        day = DayStore(root=tmp_path / 'sessions')
        voice = SimpleNamespace(closed=False)
        asked = day.here()
        delivery = Delivery(asked=asked, day=day.day, voice_owner=voice)
        manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: day.day)
        heard, done = [], []
        manager.on_request.append(lambda s, q: heard.append(q))
        manager.on_done.append(done.append)
        session = await manager.start("are the odd ones moving differently?", effort="quick",
                                      origin={"channel": "voice", 'delegation_id': 'd4', 'thread': asked.thread},
                                      delivery=delivery, fake={"seconds": 1.0, "request": [FOOTAGE]})
        assert await until(lambda: heard)
        manager.note(session.rid, "skip wikipedia")  # a steering note answers nothing
        manager.note(session.rid, "yes, recording now", answers="q1")  # the user's answer, naming the question
        assert await until(lambda: done)
        manager.stop()
        return manager, session, heard, day, voice, asked

    manager, session, heard, day, voice, asked = asyncio.run(go())
    assert heard[0]["what"] == FOOTAGE["what"] and heard[0]["status"] in ("asked", "answered")
    request = session.wire()["requests"][0]
    assert request["status"] == "answered" and request["answer"] == "yes, recording now" and request["id"] == "q1"
    assert any(n["text"].startswith("Your question q1 (15 more seconds of this field): yes") for n in session.notes)
    session.engine = "gpt-6.1-sol"  # scripted researchers are not restored; a real one keeps its requests
    manager._save(session)
    assert ResearchManager(FakeHub(), root=tmp_path).get(session.rid).requests[0]["answer"] == "yes, recording now"

    shown = announcer(tmp_path / "chat", manager, day=day)
    shown.announcer.research_request(session, request)
    notice, = shown.lines
    assert notice['type'] == 'app-notice' and notice['event'] == 'request' and notice['facts']['request'] == request
    assert notice['facts']['request']['seconds'] == 15 and notice['facts']['request']['why'] == 'how the odd ones move'
    assert not shown.spoken
    assert shown.logged[0]["kind"] == "research-request" and shown.logged[0]["seconds"] == 15
    spoken = announcer(tmp_path / "voice", manager, voice=voice, day=day)
    spoken.announcer.research_request(session, request)
    line, = spoken.spoken
    assert line.delegation_id == 'd4' and line.voice_owner is voice and line.asked is asked
    assert line.provenance == 'data' and line.notify is True
    assert json.loads(line.text)['facts']['request'] == request  # full original q-ID/run/answer, not an app-written spoken sentence



def test_an_answer_goes_to_this_runs_question_and_unanswered_ones_are_closed(tmp_path):
    from orchestrator.research import ResearchRefused, Session

    manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: "2026-10-07")
    session = Session(rid="r5", day="2026-10-07", n=5, brief="b", deliverable="", effort="quick", engine="fake",
                      purpose="research", label="", object_id=None, origin={"channel": "chat"}, status="running")
    manager.sessions[session.rid] = session
    manager._event({"rid": "r5", "event": "request", "id": "q1", **FOOTAGE})
    manager._finish(session, "done", {"title": "t", "summary": "s", "one_liner": "o", "significance": "low"})
    assert session.requests[0]["status"] == "unanswered"  # closed with its run, never answered later
    session.status = "running"
    session.history.append({"brief": "b", "status": "done"})  # a follow-up run (continue, answer)
    manager._event({"rid": "r5", "event": "request", "id": "q1", "what": "the objective in use", "why": "their size"})
    assert [(r["id"], r["run"]) for r in session.requests] == [("q1", 0), ("q2", 1)]  # numbered by the manager
    import pytest

    with pytest.raises(ResearchRefused):
        manager.note("r5", "yes", answers="q1")  # the earlier run's question: closed
    manager.note("r5", "40x", answers="q2")
    assert session.requests[1]["status"] == "answered" and session.requests[1]["answer"] == "40x"
    assert "asking the user for" not in session.line()


def test_a_brain_started_researchers_question_is_filed_not_posted_and_quiet_modes_never_speak_it(tmp_path):
    from orchestrator.research import Session
    from orchestrator.brain.narrator import Narrator
    from tests.test_narrator import FakeVoice, settle

    day = DayStore(root=tmp_path / 'sessions', day='2026-10-07',
                   clock=lambda: datetime(2026, 10, 7, 12).timestamp())
    manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: day.day)
    voice = FakeVoice()
    original = day.here()
    delivery = Delivery(asked=original, day=day.day, voice_owner=voice)
    request = {"id": "q1", **FOOTAGE, "status": "asked"}
    on_its_own = Session(rid="r6", day="2026-10-07", n=6, brief="b", deliverable="", effort="quick", engine="fake",
                         purpose="research", label="", object_id=None,
                         origin={'channel': 'voice', 'asked': False, 'delegation_id': 'd0', 'thread': original.thread},
                         status="running", requests=[request], _delivery=delivery)
    heard = announcer(tmp_path / "own", manager, voice=voice, day=day)
    heard.announcer.research_request(on_its_own, request)
    notice, = heard.lines
    assert not heard.spoken and notice['type'] == 'app-notice' and notice['facts']['request'] == request
    assert heard.logged[0]['kind'] == 'research-request'
    assert "asking the user for 15 more seconds of this field (q1)" in on_its_own.line()  # on its chip and in the roster
    asked = Session(rid='r7', day=day.day, n=7, brief='b', deliverable='', effort='quick', engine='fake',
                    purpose='research', label='', object_id=None,
                    origin={'channel': 'voice', 'delegation_id': 'd1', 'thread': original.thread},
                    status='running', _delivery=delivery)
    quiet = announcer(tmp_path / 'quiet', manager, voice=voice, day=day)

    async def go():
        narrator = Narrator(lambda: voice, lambda *a, **k: None, quieter=lambda: True)
        quiet.announcer.narrator = narrator
        quiet.announcer.research_request(asked, request)
        line, = narrator.waiting
        assert line.voice_owner is voice and line.asked is original and line.notify is True and line.provenance == 'data'
        await settle(narrator)
        await asyncio.gather(*voice.speech_tasks)
        assert narrator.delivered[-1][1] == 'voice'

    asyncio.run(go())
    sent, = voice.injected
    assert sent['kwargs'] == {'respond': True, 'delegation_id': 'd1'}
    assert sent['text'].startswith('Requested notification:') and '"id": "q1"' in sent['text']
    notice, = quiet.lines
    assert notice['type'] == 'app-notice' and notice['facts']['request'] == request
    # Real quieter mode preserves explicitly requested delivery; a missing original owner still refuses it.
    missing = Session(rid='r8', day=day.day, n=8, brief='b', deliverable='', effort='quick', engine='fake',
                      purpose='research', label='', object_id=None,
                      origin={'channel': 'voice', 'delegation_id': 'd2'}, status='running')
    absent = announcer(tmp_path / 'absent', manager, voice=voice, day=day)
    absent.announcer.research_request(missing, request)
    assert not absent.spoken and absent.lines[0]['facts']['request'] == request and len(voice.injected) == 1


def test_an_instant_researcher_is_not_offered_it(tmp_path):
    instant, _ = researcher(tmp_path, {"effort": "instant"})
    assert "request_evidence" not in instant.tool_names()
