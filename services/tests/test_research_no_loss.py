"""Nothing a researcher found, wrote or was told is lost on the way (the researcher repair, Phase 1):
forced write-ups ask for everything, a malformed or partly invalid finish is kept and never "done", a run that
stopped keeps its findings, restarts and days never change or charge saved work, and a question stays bound to the
run that asked it."""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from orchestrator.research import ResearchManager, ResearchRefused, Session
from orchestrator.research.manager import _read_inbox
from research_worker import researcher as researcher_module
from research_worker.budget import Effort
from research_worker.engines import STALLED, WRAP_UP, FakeEngine, SolEngine, make_engine
from research_worker.session_index import IndexUnavailable, SessionIndex
from research_worker.work import UnreadableReply, json_reply
from tests.test_research import FakeHub
from tests.test_research_content import fake_openai, researcher, response

ONE_ROUND = Effort("t", 30.0, 1.0, "big", frozenset({"web_search", "record_finding", "finish"}), 1, "low")
TWO_ROUNDS = Effort("t", 30.0, 1.0, "big", frozenset({"web_search", "record_finding", "finish"}), 2, "low")
LONG = "Guard cells swell when potassium ions flow in, " * 30  # well past every old cut (500, 800, 1000)


def done(events):
    return next(e for e in reversed(events) if e.get("event") == "done")


def text_reply(text: str):
    return SimpleNamespace(id="resp", usage=SimpleNamespace(input_tokens=10, output_tokens=10), output=[], output_text=text)


def test_a_forced_write_up_asks_for_everything_it_found():
    for told in (WRAP_UP, STALLED):
        assert "report" in told and "claims" in told and "limitations" in told and "tables" in told


def test_an_unparseable_finish_is_told_and_sent_again_never_an_empty_done(tmp_path, monkeypatch):
    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        if len(sent) == 1:
            bad = response(("finish", {}))
            bad.output[0].arguments = '{"title": "Guard cells", "summary": "cut off mid'
            return bad
        return response(("finish", {"title": "t", "summary": "Guard cells open the pore.", "one_liner": "o",
                                    "significance": "low"}))

    fake_openai(monkeypatch, create)
    r, events = researcher(tmp_path, {"effort": "quick"}, TWO_ROUNDS)
    asyncio.run(r.run(SolEngine()))
    told = next(i for i in sent[1]["input"] if i.get("type") == "function_call_output")
    assert "not valid JSON" in json.loads(told["output"])["error"]
    assert done(events)["status"] == "done" and done(events)["result"]["summary"] == "Guard cells open the pore."


def test_an_unparseable_last_finish_keeps_the_findings_and_what_was_sent(tmp_path, monkeypatch):
    async def create(**kwargs):
        if not any(i.get("type") == "function_call_output" for i in kwargs["input"]):
            return response(("record_finding", {"text": LONG, "sources": ["https://example.org/stomata"]}))
        bad = response(("finish", {}))
        bad.output[0].arguments = '{"summary": "half'
        return bad

    fake_openai(monkeypatch, create)
    r, events = researcher(tmp_path, {"effort": "quick"}, TWO_ROUNDS)
    asyncio.run(r.run(SolEngine()))
    end = done(events)
    assert end["status"] == "partial"  # never "done", never an empty result
    assert end["result"]["findings"][0]["text"] == LONG  # whole, not cut at 500
    assert end["result"]["sources"] == ["https://example.org/stomata"]
    assert end["result"]["rejected"][0]["submitted"] == '{"summary": "half'


def test_an_invalid_part_on_the_last_chance_keeps_the_rest_and_says_it_is_partial(tmp_path, monkeypatch):
    async def create(**kwargs):
        return response(("finish", {"title": "Stomata", "summary": "Guard cells open the pore.", "one_liner": "o",
                                    "significance": "low", "tables": [{"title": "Widths", "columns": ["a"], "rows": "no"}]}))

    fake_openai(monkeypatch, create)
    r, events = researcher(tmp_path, {"effort": "quick"}, ONE_ROUND)
    asyncio.run(r.run(SolEngine()))
    end = done(events)
    assert end["status"] == "partial" and end["result"]["summary"] == "Guard cells open the pore."
    rejected, = end["result"]["rejected"]
    assert rejected["part"] == "tables" and '"rows": "no"' in rejected["submitted"] and rejected["error"]
    assert any("tables could not be kept" in x for x in end["result"]["incomplete"])


def test_an_invalid_part_with_rounds_left_is_sent_back_to_fix(tmp_path, monkeypatch):
    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        table = {"title": "Widths", "columns": ["a"], "rows": "no" if len(sent) == 1 else [["1"]]}
        return response(("finish", {"title": "t", "summary": "s", "one_liner": "o", "significance": "low", "tables": [table]}))

    fake_openai(monkeypatch, create)
    r, events = researcher(tmp_path, {"effort": "quick"}, TWO_ROUNDS)
    asyncio.run(r.run(SolEngine()))
    assert done(events)["status"] == "done" and done(events)["result"]["tables"] and "rejected" not in done(events)["result"]


def test_a_plain_text_answer_is_partial_and_says_it_searched_nothing(tmp_path, monkeypatch):
    async def create(**kwargs):
        return text_reply("Guard cells open and close the stoma.")

    fake_openai(monkeypatch, create)
    r, events = researcher(tmp_path, {"effort": "quick"}, TWO_ROUNDS)
    asyncio.run(r.run(SolEngine()))
    end = done(events)
    assert end["status"] == "partial" and end["result"]["summary"] == "Guard cells open and close the stoma."
    assert any("own knowledge" in x for x in end["result"]["incomplete"])


def test_a_run_that_never_wrote_up_keeps_what_it_recorded(tmp_path, monkeypatch):
    async def create(**kwargs):  # it never calls finish, even when told to
        return response(("record_finding", {"text": LONG}))

    fake_openai(monkeypatch, create)
    r, events = researcher(tmp_path, {"effort": "quick"}, TWO_ROUNDS)
    asyncio.run(r.run(SolEngine()))
    end = done(events)
    # The same finding again is nothing new, so the stall rule makes it write up; every one it recorded is kept whole.
    findings = [f["text"] for f in end["result"]["findings"]]
    assert end["status"] == "partial" and len(findings) >= 2 and set(findings) == {LONG}
    finding = next(e for e in events if e.get("event") == "finding")
    assert finding["text"] == LONG


def test_an_unreadable_check_reply_is_a_failed_check_not_zero_right(tmp_path, monkeypatch):
    with pytest.raises(UnreadableReply):
        json_reply("no json here", strict=True)
    assert json_reply("no json here") == {}

    async def no_answers(prompt, r):
        return {"verdict": "fine"}  # JSON, but no answers to grade

    monkeypatch.setattr(researcher_module, "fast_json", no_answers)
    r, _ = researcher(tmp_path, {"effort": "standard", "lesson_check": "check"})
    lesson = {"title": "t", "steps": [{"id": "q1", "prompt": "p", "answer": "a"}]}
    content, record = asyncio.run(r._check_lesson(lesson))
    assert content == lesson and record is None
    steps = [json.loads(line) for line in (r.dir / "steps.jsonl").read_text().splitlines()]
    # Plain on its card; the reason it failed is its detail (Slice 7, 2026-10-11).
    assert any(s["text"].startswith("The lesson check didn't work") and s.get("detail", "").startswith("UnreadableReply")
               for s in steps)
    assert not any("answered right" in s for s in steps)


def test_a_snapshot_never_returns_an_earlier_close_up_as_fresh(tmp_path):
    r, _ = researcher(tmp_path, {"effort": "quick"}, photo=True)
    old = r.crop_jpeg
    (r.dir / "inputs" / "frame2.jpg").write_bytes(b"\xff\xd8 new frame")

    async def only_frame(op, **kw):
        return {"ok": True, "frame": "inputs/frame2.jpg"}

    r.request = only_frame
    reply, picture = asyncio.run(r.tool_snapshot({}))
    assert picture == b"\xff\xd8 new frame" and picture != old and "whole field" in reply["note"]

    async def nothing(op, **kw):
        return {"ok": True}

    r.request, r.last_snapshot = nothing, 0.0
    reply, picture = asyncio.run(r.tool_snapshot({}))
    assert picture is None and "no new picture" in reply["error"]


def test_the_run_tool_runs_past_the_efforts_time(tmp_path):
    r, _ = researcher(tmp_path, {"effort": "quick"})
    r.started_at = time.monotonic() - 10 * r.effort.seconds  # long past it: no clock refuses the app's operations
    asked = []

    async def segment(op, **kw):
        asked.append(kw)
        return {"ok": True, "id": "e3", "title": "Cell outlines", "words": "outlined 40 cells"}

    r.request = segment
    reply, _ = asyncio.run(r.tool_run({"operation": "segment"}))
    assert reply["ok"] and asked[0]["wait"] == researcher_module.RUN_WAIT_MAX_S


def test_a_scripted_researcher_stays_scripted_and_the_engine_that_ran_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("MICROMIND_RESEARCH_ENGINE", "gemini-3.8-flash")
    assert isinstance(make_engine("fake"), FakeEngine)
    r, events = researcher(tmp_path, {"effort": "quick", "fake": {"seconds": 0.05}})
    asyncio.run(r.run(FakeEngine()))
    assert done(events)["engine"] == "fake"


def test_the_session_index_passes_on_the_apps_own_reason(tmp_path):
    from tests.test_research_session import Stub  # noqa: F401 - the stub's routes
    import threading
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        index = SessionIndex(f"http://127.0.0.1:{server.server_address[1]}", "1999-01-01")
        with pytest.raises(IndexUnavailable, match="400: bad day"):
            index.summary()
    finally:
        server.shutdown()
    r, _ = researcher(tmp_path, {"effort": "quick", "resources_url": "https://10.0.0.5:8770", "day": "2026-10-10"})
    reply, _ = asyncio.run(r.tool_session({"action": "summary"}))
    assert "only from this machine" in reply["error"]
    steps = [json.loads(line) for line in (r.dir / "steps.jsonl").read_text().splitlines()]
    assert any("Today's session can't be read here" in s["text"] and "only from this machine" in s.get("detail", "")
               for s in steps)


# --- the app's side ------------------------------------------------------------------------------------------

def _session(manager: ResearchManager, **kw) -> Session:
    session = Session(rid=kw.pop("rid", "r5"), day=kw.pop("day", manager.today()), n=5, brief="b", deliverable="research",
                      effort="quick", engine="gpt-6.1-sol", purpose="research", label="", object_id=None,
                      origin={"channel": "chat"}, status=kw.pop("status", "done"), **kw)
    manager.sessions[session.rid] = session
    return session


def test_a_restart_never_changes_a_newer_result(tmp_path):
    day = "2026-10-09"
    detail = "Measured width 12.5 µm across 14 guard cell pairs on the 40x view; median aperture 3.2 " + "x" * 120 + " 7"
    folder = tmp_path / day / "r123"
    folder.mkdir(parents=True)
    (folder / "session.json").write_text(json.dumps({
        "rid": "r123", "n": 1, "brief": "b", "effort": "quick", "engine": "gpt-6.1-sol", "purpose": "research",
        "status": "done", "result": {"title": "t", "summary": "s", "details": [detail]}}))
    back, = ResearchManager(FakeHub(), root=tmp_path, today=lambda: day).read_day(day, kept_only=False)
    assert back.result["details"] == [detail]


def test_another_sessions_researcher_is_not_continued_here(tmp_path):
    """ADR 0017: only this session's work is continued, whatever date it was started on."""
    manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: "2026-10-10")
    session = _session(manager, day="2026-10-09", status="running")  # the session of Oct 9 (one saved before sessions)
    words = "Researcher 5 belongs to “Oct 9”; switch to it to continue"
    with pytest.raises(ResearchRefused, match=words):
        asyncio.run(manager.followup(session.rid, "and the pore?"))
    with pytest.raises(ResearchRefused, match=words):
        manager.note(session.rid, "focus on the pore")
    with pytest.raises(ResearchRefused, match=words):
        asyncio.run(manager.carry_on(session.rid, "q1", "yes"))
    mine = _session(manager, rid="r6", day="2026-10-09", status="running", sitting="2026-10-10")  # this session's, yesterday
    assert manager.note(mine.rid, "focus on the pore") is mine and mine.notes[-1]["text"] == "focus on the pore"


def test_a_presentation_only_researcher_is_refused_before_anything_is_reserved(tmp_path):
    manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: "2026-10-10")
    session = _session(manager, outputs=[{"id": "o1", "kind": "presentation", "path": "outputs/o1.json", "version": 1}])
    held = []
    manager.spending.hold = lambda *a, **k: held.append(a)
    with pytest.raises(ResearchRefused, match="nothing it can make a new version of"):
        asyncio.run(manager.followup(session.rid, "make it shorter", mode="revise", words="make it shorter",
                                     asked_with="make it shorter"))
    assert held == [] and session.status == "done"


def test_a_late_answer_still_finds_its_question_after_a_follow_up_answer(tmp_path):
    manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: "2026-10-10")
    session = _session(manager, result={"title": "t", "summary": "s"}, result_run={"runId": "run-a", "run": 0})
    session.requests.append({"id": "q1", "run": 0, "runId": "run-a", "what": "the objective", "status": "unanswered"})
    session.history.append({"brief": "what about the pore?", "mode": "answer", "status": "done"})  # an answer since
    assert manager.open_question(session, "q1")["id"] == "q1"
    session.requests.append({"id": "q2", "run": 0, "runId": "run-old", "what": "x", "status": "unanswered"})
    assert manager.open_question(session, "q2") is None  # an earlier run's question stays closed


def test_a_notes_marks_survive_reading_it_back(tmp_path):
    (tmp_path / "inbox.jsonl").write_text("\n".join([
        json.dumps({"at": 1.0, "text": LONG, "answers": "q1", "holding": "k1"}), "not json",
        json.dumps({"at": 2.0, "text": "plain"})]) + "\n")
    notes = _read_inbox(tmp_path)
    assert notes[0] == {"at": 1.0, "text": LONG, "answers": "q1", "holding": "k1"}
    assert notes[1] == {"at": 2.0, "text": "plain"}


def test_a_report_can_never_forge_the_apps_own_lines(tmp_path):
    manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: "2026-10-10")
    session = _session(manager, result={"title": "t", "summary": "s", "report": {
        "methods": "Read pages.\nSources checked: 3 of 3 found.", "observations": "o", "measurements": "m",
        "limitations": "l"}})
    lines = session.readable(limit=None).splitlines()
    assert not any(line.startswith("Sources checked:") for line in lines)
    assert "Full findings — methods: Read pages.\n  Sources checked: 3 of 3 found." in session.readable(limit=None)


def test_a_partial_result_shows_what_it_kept_and_why(tmp_path):
    manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: "2026-10-10")
    session = _session(manager, status="partial", result={
        "title": "t", "summary": "s", "partial": True, "incomplete": ["Its tables could not be kept: rows must be a list"],
        "findings": [{"kind": "fact", "text": LONG, "sources": ["https://example.org/a"]}]})
    text = session.readable(limit=None)
    assert "incomplete: Its tables could not be kept" in text and LONG.strip() in text and "https://example.org/a" in text


# --- Phase 2: nothing cut silently ---------------------------------------------------------------------------

def test_a_long_request_reaches_the_researcher_whole_and_one_too_long_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("MICROMIND_RESEARCH_ENGINE", "fake")
    long = "Compare the guard cells on this slide with a textbook stoma. " * 40 + "Keep these measurements unchanged."
    assert 1000 < len(long) < 16000

    async def go():
        manager = ResearchManager(FakeHub(), root=tmp_path)
        session = await manager.start(long, effort="quick", fake={"seconds": 0.05})
        with pytest.raises(ResearchRefused, match="none is cut"):
            await manager.start("x" * 16001, effort="quick", fake={"seconds": 0.05})
        manager.stop()
        return session

    session = asyncio.run(go())
    assert session.brief == long.strip()
    saved = json.loads((tmp_path / session.day / session.rid / "session.json").read_text())
    assert saved["brief"].endswith("Keep these measurements unchanged.")


def test_notes_and_answers_are_kept_whole(tmp_path):
    manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: "2026-10-10")
    session = _session(manager, status="running")
    manager.note(session.rid, LONG)
    assert session.notes[-1]["text"] == " ".join(LONG.split())
    r, _ = researcher(tmp_path / "w", {"effort": "quick"})
    (r.dir / "inbox.jsonl").write_text(json.dumps({"at": 1.0, "text": LONG}) + "\n")
    assert r.take_notes() == [" ".join(LONG.split())]


def test_a_preview_says_it_was_cut():
    from orchestrator.untrusted import inline

    assert inline("short", 60) == "short"
    cut = inline("Guard cells swell when potassium ions flow in and the pore opens wide", 30)
    assert cut.endswith("…") and len(cut) <= 30 and not cut[:-1].endswith(" ")


def test_the_brains_roster_counts_who_it_leaves_out(tmp_path):
    manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: "2026-10-10")
    for n in range(8):
        _session(manager, rid=f"r{n}", sitting="2026-10-10")
    text = manager.lines()
    assert "and 2 earlier in this session" in text and "research_status lists them" in text
    assert "and 2 earlier in this session" not in manager.lines(limit=None)


def test_an_answer_carries_its_cut_marker(tmp_path):
    from orchestrator.brain.claims import answer_facts

    run = SimpleNamespace(wire=lambda: {"tool": "read_result", "ok": True, "summary": "x" * 5000}, args={})
    facts = json.dumps(answer_facts([run]))
    assert "more characters not shown here" in facts


def test_every_sourced_point_is_kept_and_those_past_the_check_are_said_so():
    from research_worker import claims

    raw = [{"point": f"point {i} " + "word " * 120, "source": f"https://example.org/{i}"} for i in range(14)]
    kept = claims.claims_of(raw)
    assert len(kept) == 14 and kept[0]["point"].endswith("word")

    async def fetch(url):
        return "point word " * 50

    checked = asyncio.run(claims.check(kept, fetch))
    assert all(c["found"] is None and "first 12" in c["why"] for c in checked[12:])


def test_limits_on_what_fits_are_refused_never_cut(tmp_path):
    r, _ = researcher(tmp_path, {"effort": "quick"}, photo=True)
    reply, _ = asyncio.run(r.tool_annotate_image({"labels": [{"name": f"l{i}", "where": "here"} for i in range(7)]}))
    assert "at most 6 labels" in reply["error"]
    reply, _ = asyncio.run(r.tool_annotate_image({"labels": [{"name": "a" * 41, "where": "here"}]}))
    assert "at most 40 characters" in reply["error"]
    r2, _ = researcher(tmp_path / "l", {"effort": "standard"})
    reply, _ = asyncio.run(r2.tool_make_lesson({"title": "t", "key_ideas": ["k"],
                                                "questions": [{"question": f"q{i}?", "answer": "a"} for i in range(9)]}))
    assert "at most 8 questions" in reply["error"]


def test_a_cited_picture_is_matched_by_its_page_or_its_own_address(tmp_path):
    r, _ = researcher(tmp_path, {"effort": "quick"})
    r.refs = {"https://commons.wikimedia.org/wiki/file:stoma.jpg": {"path": "sources/a.jpg"}}
    r.ref_names = {r._norm_url("https://upload.wikimedia.org/x/Stoma.jpg"): "https://commons.wikimedia.org/wiki/file:stoma.jpg",
                   r._norm_url("https://commons.wikimedia.org/wiki/File:Stoma.jpg"): "https://commons.wikimedia.org/wiki/file:stoma.jpg"}
    r.final_round = True
    asyncio.run(r.tool_finish({"title": "t", "summary": "s", "one_liner": "o", "significance": "low",
                               "refs": ["https://upload.wikimedia.org/x/Stoma.jpg#top"]}))
    assert r.result["refs"] == [{"path": "sources/a.jpg"}]


def test_specimen_pictures_are_looked_at_closely():
    from research_worker.engines import _detail

    assert _detail("snapshot") == "high" and _detail("session") == "high" and _detail("fetch_reference_image") == "auto"


def test_a_long_report_is_read_a_page_at_a_time_pinned_to_its_revision(tmp_path):
    from orchestrator.brain.registry import ToolContext, ToolFailed
    from tests.fakes import research_tools

    manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: "2026-10-10")
    report = {k: f"{k}: " + "Each guard cell pair was measured on the 40x view. " * 80 for k in
              ("methods", "observations", "measurements", "limitations")}
    session = _session(manager, result={"title": "t", "summary": "s", "report": report})
    from orchestrator.presenter import Presenter

    hub = FakeHub()
    read = research_tools(hub, manager, Presenter(hub, manager)).tools["read_result"].handler
    ctx = ToolContext("chat", 1, None, "what did researcher 5 find?")
    first = asyncio.run(read(ctx, {"researcher": "5", "part": "report"}))
    assert "(more: read_result with part report, offset" in first
    offset = int(first.split("offset ")[1].split(",")[0])
    revision = first.split("revision ")[1].split(")")[0]
    pages, at = [first], offset
    while "(more:" in pages[-1]:
        pages.append(asyncio.run(read(ctx, {"researcher": "5", "part": "report", "offset": at, "revision": revision})))
        if "(more:" in pages[-1]:
            at = int(pages[-1].split("(more: read_result with part report, offset ")[1].split(",")[0])
    text = " ".join(pages)
    assert all(k + ": " in text for k in report) and "limitations: Each guard" in text  # the end was reached
    session.result = {**session.result, "report": {**report, "methods": "methods: re-measured"}}  # its report changed
    with pytest.raises(ToolFailed, match="changed since that page"):
        asyncio.run(read(ctx, {"researcher": "5", "part": "report", "offset": offset, "revision": revision}))
