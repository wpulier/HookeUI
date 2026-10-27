"""A run's conversation with its model is kept round by round (research_worker/transcript.py; the demo of 2026-10-11:
Researcher 3 never called `run`, and why could not be read). Every model here is a fake: nothing is sent."""
import asyncio
import json

import openai

from research_worker import transcript
from research_worker.budget import Effort
from research_worker.engines import GeminiEngine, SolEngine
from tests.test_research_content import researcher
from tests.test_research_engines import FINISH, fake_gemini, fake_sol, gemini_reply, only_finish, status_error

TOOLS = frozenset({"web_search", "record_finding", "finish"})


def kept(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_each_sol_round_keeps_what_it_was_sent_offered_and_said(tmp_path, monkeypatch):
    from tests.test_research_content import response

    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        return response(("record_finding", {"text": "pores"})) if len(sent) == 1 else response(("finish", FINISH))

    fake_sol(monkeypatch, create)
    path = tmp_path / "logs" / "transcripts" / "2026-10-11" / "r1.jsonl"
    r, events = researcher(tmp_path, {"effort": "quick", "brief": "how fast do they swim?", "transcript": str(path),
                                      "run_id": "a1"}, Effort("t", 120, 1.0, "big", TOOLS, 8, "low"), photo=True)
    r.budget.unlimited = True  # search is offered only without a spending limit (engines.NO_SEARCH)
    asyncio.run(r.run(SolEngine()))
    assert events[-1]["status"] == "done"
    first, second = kept(path)
    assert (first["rid"], first["runId"], first["round"], first["model"]) == ("r1", "a1", 1, "gpt-6.1-sol")
    assert first["tools"][:3] == ["web_search", "record_finding", "finish"] and first["toolChoice"] == "auto"
    # The instructions only by their hash, and only in the round that sent them (the response chain carries them).
    prompt = sent[0]["input"][0]["content"]
    assert first["instructions"] == {"sha256": transcript.sha(prompt), "chars": len(prompt)}
    assert second["instructions"] is None and prompt not in json.dumps(first)
    words = json.dumps(first["sent"])
    assert "how fast do they swim?" in words and "<picture: a data URL of" in words and "base64" not in words
    call, = first["reply"]["output"]
    assert call["name"] == "record_finding" and json.loads(call["arguments"]) == {"text": "pores"}
    # The next round's request carries the tool's result, as it went to the model.
    output, = [item for item in second["sent"] if item.get("type") == "function_call_output"]
    assert output["output"] == sent[1]["input"][0]["output"]
    assert second["reply"]["output"][0]["name"] == "finish"


def test_a_gemini_round_keeps_its_contents_and_pictures_by_size(tmp_path, monkeypatch):
    async def generate(model, contents, config):
        return gemini_reply(("finish", FINISH), queries=["stomata speed"], chunks=[("https://example.org/a", "example")])

    fake_gemini(monkeypatch, generate)
    path = tmp_path / "t.jsonl"
    r, _ = researcher(tmp_path, {"effort": "quick", "brief": "how fast?", "transcript": str(path)},
                      Effort("t", 120, 1.0, "big", TOOLS, 8, "low"), photo=True)
    r.budget.unlimited = True  # search is offered only without a spending limit (engines._limit)
    asyncio.run(r.run(GeminiEngine()))
    line, = kept(path)
    assert line["engine"] == "gemini" and line["tools"][0] == "google_search" and "finish" in line["tools"]
    assert line["instructions"]["sha256"] == transcript.sha(r.system_prompt())
    words = json.dumps(line["sent"])
    assert "how fast?" in words and "bytes>" in words
    assert line["reply"]["content"]["parts"][0]["function_call"]["name"] == "finish"
    assert line["reply"]["searches"] == ["stomata speed"] and line["reply"]["pages"][0]["uri"] == "https://example.org/a"
    assert line["reply"]["finishReason"] == "STOP" and line["reply"]["usage"]["prompt_token_count"] == 1000


def test_a_call_that_fails_keeps_its_request_and_the_error(tmp_path, monkeypatch):
    async def create(**kwargs):
        raise status_error(openai.BadRequestError, 400)

    fake_sol(monkeypatch, create)
    path = tmp_path / "t.jsonl"
    r, events = researcher(tmp_path, {"effort": "quick", "transcript": str(path)}, Effort("t", 120, 1.0, "big", TOOLS, 8, "low"))
    asyncio.run(r.run(SolEngine()))
    line, = kept(path)
    assert events[-1]["status"] == "failed" and line["round"] == 1 and "BadRequestError" in line["reply"]["error"]


def test_no_transcript_is_kept_unless_the_app_names_where(tmp_path, monkeypatch):
    async def generate(model, contents, config):
        return gemini_reply(("finish", FINISH))

    fake_gemini(monkeypatch, generate)
    r, events = researcher(tmp_path, {"effort": "quick"}, Effort("t", 120, 1.0, "big", TOOLS, 8, "low"))
    asyncio.run(r.run(GeminiEngine()))
    assert events[-1]["status"] == "done" and {p.name for p in tmp_path.rglob("*.jsonl")} <= {"steps.jsonl"}


def test_a_round_past_the_cap_keeps_its_long_strings_cut_and_marked():
    record = {"round": 3, "tools": ["finish"], "sent": [{"type": "function_call_output", "output": "page " * 40000}],
              "reply": {"output": [{"name": "finish", "arguments": "{}"}]}}
    line = transcript.line(record)
    assert len(line.encode()) <= transcript.LINE_MAX
    back = json.loads(line)
    assert "[cut: " in back["sent"][0]["output"] and back["cut"].startswith("strings past")
    assert back["reply"] == record["reply"] and back["round"] == 3
    huge = {"round": 4, "sent": [str(i) * 300 for i in range(2000)]}  # too many pieces for cutting to help
    back = json.loads(transcript.line(huge))
    assert back == {"round": 4, "cut": back["cut"]} and "only its head is kept" in back["cut"]


def test_the_app_names_each_runs_transcript_in_the_performance_logs(tmp_path):
    from orchestrator.research import ResearchManager
    from orchestrator.saves import DATED_LOGS
    from tests.test_research import FakeHub
    from tests.test_research_answer_kept import finished_with_a_question

    manager = ResearchManager(FakeHub(), root=tmp_path / "research", today=lambda: "2026-10-07")
    session = finished_with_a_question(manager, status="running")
    spawned = {}

    class Worker:
        async def call(self, op, **kwargs):
            spawned.update(kwargs, op=op)

    async def ensure():
        return Worker()

    async def watchdog(rid, limit):
        return None

    manager._ensure_worker, manager._watchdog = ensure, watchdog
    asyncio.run(manager._launch(session))
    assert spawned["transcript"] is None  # tests and a manager made alone keep none
    manager.transcripts = tmp_path / "logs" / "transcripts"
    session.status = "running"
    asyncio.run(manager._launch(session))
    assert spawned["transcript"] == str(tmp_path / "logs" / "transcripts" / session.day / f"{session.rid}.jsonl")
    assert "transcripts" in DATED_LOGS  # pruned with the other dated performance logs (make logs-prune)
