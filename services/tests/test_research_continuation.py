"""One continuation, the same on every engine (the researcher repair, Phase 3): a continue is given its original
request, the current finding whole by version, its open questions, its earlier answers and runs, and the new request,
as text; no response chain is carried from one run to the next (OpenAI refuses a chain that ends on finish's
unanswered call: docs/bench/research.md); what does not fit is left out whole and said."""
from __future__ import annotations

import asyncio
import json

import pytest

from orchestrator.research import ResearchManager, ResearchRefused, Session
from research_worker.budget import Effort
from research_worker.engines import SolEngine
from research_worker.limits import BRIEF_MAX
from tests.test_research import FakeHub, until
from tests.test_research_content import fake_openai, researcher, response

FINDING = {"title": "Stomata", "summary": "Guard cells open the pore when they swell.", "one_liner": "They swell.",
           "significance": "low", "limitations": ["no scale was set"], "next_questions": ["are they all open?"],
           "findingRevision": {"version": 2, "digest": "d" * 64}}


class Spy:
    """The research process, recording what each run is sent."""
    alive = True

    def __init__(self):
        self.sent = []

    async def call(self, op, **kw):
        self.sent.append(kw)
        return {"ok": True}


def finished(manager: ResearchManager, engine="gpt-6.1-sol", object_id=None, label="", **kw) -> Session:
    session = Session(rid="r2", day=manager.today(), n=2, brief="How do guard cells open the stoma? Keep it short.",
                      deliverable="research", effort="quick", engine=engine, purpose="research", label=label,
                      object_id=object_id, origin={"channel": "chat"}, status="done", result=dict(FINDING),
                      response_id="resp-old", **kw)
    manager.sessions[session.rid] = session
    return session


def manager_with_spy(tmp_path):
    manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: "2026-10-10")
    spy = Spy()

    async def worker():
        return spy

    manager._ensure_worker = worker
    return manager, spy


def test_a_continue_is_given_its_whole_story_and_no_chain(tmp_path):
    async def go():
        manager, spy = manager_with_spy(tmp_path)
        session = finished(manager)
        session.requests.append({"id": "q1", "what": "the objective in use", "status": "unanswered", "run": 0})
        session.history.append({"brief": "and the pore?", "mode": "answer", "status": "done",
                                "result": {"summary": "It is the gap between them."}})
        await manager.followup("r2", "Keep going: compare open and closed stomata.", mode="continue",
                               words="keep going: compare open and closed stomata", asked_with="keep going")
        assert await until(lambda: spy.sent)
        manager.stop()
        return session, spy.sent[0]

    session, sent = asyncio.run(go())
    assert sent["previous_response_id"] is None
    brief = sent["brief"]
    assert "The original request: How do guard cells open the stoma? Keep it short." in brief
    assert "(its finding, version 2)" in brief and "Guard cells open the pore when they swell." in brief
    assert "no scale was set" in brief and "are they all open?" in brief  # what it could not settle, what to ask
    assert "q1 (the objective in use): still open" in brief
    assert "and the pore?: It is the gap between them." in brief
    assert brief.endswith("Now, the user asks: Keep going: compare open and closed stomata.")
    # The researcher is still named by what it was asked first; the new question is its "now".
    assert session.brief == "How do guard cells open the stoma? Keep it short."
    assert session.asking == "Keep going: compare open and closed stomata."
    ended = session.history[-1]
    assert ended["result"]["summary"] == FINDING["summary"] and ended["version"]["version"] == 2


def test_every_engine_is_given_the_same_continuation(tmp_path):
    packages = []
    for engine in ("gpt-6.1-sol", "gemini-3.8-flash"):
        manager = ResearchManager(FakeHub(), root=tmp_path / engine, today=lambda: "2026-10-10")
        packages.append(manager._continuation(finished(manager, engine=engine), "Keep going."))
    assert packages[0] == packages[1]


def test_what_does_not_fit_is_left_out_whole_and_said(tmp_path):
    manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: "2026-10-10")
    session = finished(manager)
    session.history += [{"brief": f"question {i}", "mode": "answer", "status": "done",
                         "result": {"summary": "y" * 9000}} for i in range(12)]
    package = manager._continuation(session, "Keep going.")
    assert len(package) <= BRIEF_MAX
    assert "Left out here to fit, never cut: the follow-up questions answered from it" in package
    assert "action read, id r2" in package and package.endswith("Now, the user asks: Keep going.")
    session.result = {**FINDING, "report": {k: "z" * 16000 for k in ("a", "b", "c", "d", "e", "f")}}
    with pytest.raises(ResearchRefused, match="none is cut"):
        manager._continuation(session, "Keep going.")


def test_a_sol_run_never_continues_an_earlier_runs_chain(tmp_path, monkeypatch):
    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        if kwargs.get("previous_response_id") == "resp-old":  # as the API answers a chain ending on finish's call
            raise RuntimeError("400 No tool output found for function call")
        return response(("finish", {"title": "t", "summary": "Open stomata are wider.", "one_liner": "o",
                                    "significance": "low"}), rid="resp-new")

    fake_openai(monkeypatch, create)
    r, events = researcher(tmp_path, {"effort": "quick", "previous_response_id": "resp-old", "brief": "the package"},
                           Effort("t", 30.0, 1.0, "big", frozenset({"web_search", "finish"}), 2, "low"))
    asyncio.run(r.run(SolEngine()))
    assert events[-1]["status"] == "done"
    first = sent[0]
    assert first["previous_response_id"] is None and first["input"][0]["role"] == "developer"  # its instructions, again


def test_go_deeper_continues_at_the_effort_asked_for(tmp_path):
    async def go():
        manager, spy = manager_with_spy(tmp_path)
        session = finished(manager)
        with pytest.raises(ResearchRefused):  # deep needs the user's own words
            await manager.followup("r2", "go deeper", mode="continue", words="hmm", asked_with="", effort="deep")
        assert session.effort == "quick" and session.status == "done"
        await manager.followup("r2", "go deeper on that", mode="continue", words="go deeper on that",
                               asked_with="go deeper on that", effort="standard")
        assert await until(lambda: spy.sent)
        manager.stop()
        return session, spy.sent[0]

    session, sent = asyncio.run(go())
    assert session.effort == "standard" and sent["effort"] == "standard"


def test_an_older_selected_version_is_read_never_continued(tmp_path):
    manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: "2026-10-10")
    session = finished(manager)
    session.finding_revisions = [{"version": 1, "digest": "a" * 64, "result": {}}, {"version": 2, "digest": "d" * 64, "result": {}}]
    with pytest.raises(ResearchRefused, match="older"):
        asyncio.run(manager.followup("r2", "keep going", mode="continue", words="keep going", asked_with="keep going",
                                     selection={"rid": "r2", "day": "2026-10-10", "findingVersion": 1,
                                                "findingDigest": "a" * 64}))
    assert session.status == "done" and not session.asking  # nothing was overwritten or queued


def test_a_follow_up_is_told_what_a_new_run_is_told(tmp_path):
    async def go():
        manager, spy = manager_with_spy(tmp_path)
        manager.hub.scene.context["specimen"] = "leaf epidermis peel"
        finished(manager, object_id="o3", label="stoma")
        await manager.followup("r2", "what does the pore do?", mode="answer")
        assert await until(lambda: spy.sent)
        manager.stop()
        return spy.sent[0]["snapshot"]

    snapshot = asyncio.run(go())
    assert snapshot["specimen"] == "leaf epidermis peel" and snapshot["object"] == {"id": "o3", "label": "stoma"}
    assert "learner" in snapshot


def test_a_new_researcher_builds_on_a_finished_ones_work(tmp_path, monkeypatch):
    monkeypatch.setenv("MICROMIND_RESEARCH_ENGINE", "fake")

    async def go():
        manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: "2026-10-10")
        finished(manager)
        session = await manager.start("turn Researcher 2's notes into a short quiz", effort="quick",
                                      dependencies=[{"kind": "research-work", "researcherId": "r2", "version": 2}],
                                      fake={"seconds": 0.05})
        manager.stop()
        return session

    session = asyncio.run(go())
    bound, = session.dependencies
    assert bound["result"]["summary"] == FINDING["summary"] and bound["reference"]["researcherId"] == "r2"
    assert json.dumps(bound)  # what the new researcher's brief carries (Researcher.brief)


# --- Phase 4: a busy researcher still answers -------------------------------------------------------------------

def test_a_question_asked_while_it_works_is_kept_and_asked_when_the_run_ends(tmp_path):
    async def go():
        manager, spy = manager_with_spy(tmp_path)
        session = finished(manager)
        session.status = "running"
        await manager.followup("r2", "what did the pore look like?", mode="answer")  # never refused
        assert session.pending[0]["question"] == "what did the pore look like?" and not spy.sent
        manager._finish(session, "done", dict(FINDING))
        assert await until(lambda: spy.sent)
        manager.stop()
        return session, spy.sent[0]

    session, sent = asyncio.run(go())
    assert sent["deliverable"] == "answer" and "what did the pore look like?" in sent["brief"]
    assert not session.pending


def test_while_a_continue_works_its_earlier_finding_is_still_read(tmp_path):
    async def go():
        manager, spy = manager_with_spy(tmp_path)
        session = finished(manager)
        await manager.followup("r2", "keep going", mode="continue", words="keep going", asked_with="keep going")
        manager.stop()
        return session

    session = asyncio.run(go())
    text = session.readable(limit=None)
    assert "Its finding (version 2), from before this run; a new run is working on: keep going" in text
    assert "Guard cells open the pore when they swell." in text and "Still working; no written result yet." not in text


def test_a_stopped_researchers_kept_question_is_said_never_asked(tmp_path):
    async def go():
        manager, spy = manager_with_spy(tmp_path)
        session = finished(manager)
        session.status = "running"
        await manager.followup("r2", "and the walls?", mode="answer")
        manager._finish(session, "cancelled", None)
        await asyncio.sleep(0.1)
        manager.stop()
        return session, spy

    session, spy = asyncio.run(go())
    assert not spy.sent and "was not asked (it was stopped)" in session.step and not session.pending


def test_a_restart_says_a_kept_question_was_never_asked(tmp_path):
    manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: "2026-10-10")
    session = finished(manager)
    session.status = "running"
    asyncio.run(manager.followup("r2", "and the walls?", mode="answer"))
    back = ResearchManager(FakeHub(), root=tmp_path, today=lambda: "2026-10-10").read_day("2026-10-10", kept_only=False)[0]
    lost = back.history[-1]
    assert lost["brief"] == "and the walls?" and lost["status"] == "interrupted" and "restarted" in lost["why"]


def test_a_note_to_a_finished_researcher_says_how_to_ask_it(tmp_path):
    manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: "2026-10-10")
    finished(manager)
    with pytest.raises(ResearchRefused) as refused:
        manager.note("r2", "focus on the walls")
    assert "researcher_followup" in refused.value.next


# --- Phase 5: the model says what it meant; bindings, not word lists ---------------------------------------

def test_a_revise_target_is_named_by_id_and_words_only_fall_back(tmp_path):
    manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: "2026-10-10")
    logged = []
    manager.log = logged.append
    session = finished(manager, outputs=[{"id": "o1", "kind": "html", "path": "outputs/a.html", "version": 1}])
    assert manager.target_output(session, "the slides thing")["id"] == "o1"  # the only one: no word decides
    session.outputs.append({"id": "o2", "kind": "image", "path": "outputs/b.png", "version": 1, "content": {"kind": "labels"}})
    with pytest.raises(ResearchRefused) as unknown:
        manager.target_output(session, "the thingy")
    assert "o1 (lesson)" in unknown.value.next and "o2 (labelled picture)" in unknown.value.next
    assert manager.target_output(session, "the quiz")["id"] == "o1"
    assert logged[-1]["kind"] == "override" and logged[-1]["chose"] == "o1"
    assert "[made: o1 lesson, o2 labelled picture]" in session.line()


def test_a_description_beats_a_pronoun_and_words_match_whole(tmp_path):
    from orchestrator.research.desk import Desk

    manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: "2026-10-10")
    heart = finished(manager)
    heart.rid, heart.n, heart.brief = "r3", 3, "how the heart pumps"
    manager.sessions = {"r3": heart}
    mitosis = finished(manager)
    mitosis.rid, mitosis.n, mitosis.brief, mitosis.deliverable = "r4", 4, "mitosis in onion root tips", "video"
    manager.sessions = {"r3": heart, "r4": mitosis}
    desk = Desk(manager, shown=lambda: "r3")
    assert desk.find("the video showing mitosis") is mitosis  # "showing" never sends it to the one on screen
    assert desk.find("this one") is heart and desk.find("") is heart
    with pytest.raises(ResearchRefused):
        desk.find("the art one")  # "art" is not "heart"


def test_a_quote_is_matched_as_said():
    from orchestrator.research.policy import quoted

    assert quoted("I’d like two clips", "uh i'd like 2 clips of that please")
    assert not quoted("make a lesson", "what is that?")


def test_a_proposal_is_started_on_the_users_yes(tmp_path, monkeypatch):
    monkeypatch.setenv("MICROMIND_RESEARCH_ENGINE", "fake")
    from orchestrator.brain.registry import ToolContext
    from tests.fakes import research_tools

    async def go():
        manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: "2026-10-10")
        tools = research_tools(manager.hub, manager).tools
        start = tools["start_researcher"].handler
        from orchestrator.brain.registry import ToolFailed

        with pytest.raises(ToolFailed) as refused:
            await start(ToolContext("chat", 1, None, "can you explain stomata?"),
                        {"brief": "explain stomata as a lesson", "effort": "standard", "deliverable": "lesson"})
        pid = refused.value.next.split("confirms '")[1].split("'")[0]
        with pytest.raises(ToolFailed):  # a yes that was never said starts nothing
            await start(ToolContext("chat", 2, None, "what about the pore?"), {"brief": "-", "confirms": pid, "asked_with": "yes"})
        said = await start(ToolContext("chat", 3, None, "yes please"), {"brief": "-", "confirms": pid, "asked_with": "yes"})
        session = next(iter(manager.sessions.values()))
        manager.stop()
        return said, session

    said, session = asyncio.run(go())
    assert session.deliverable == "lesson" and session.brief == "explain stomata as a lesson" and "lesson" in said


def test_a_late_yes_carries_research_on_and_a_lesson_still_needs_their_words(tmp_path):
    """#194 review, kept: a late answer carries research on; a lesson made again needs the user's own words for it."""
    async def go():
        manager, spy = manager_with_spy(tmp_path)
        session = finished(manager)
        session.result_run = {"runId": "run-a", "run": 0}
        session.requests.append({"id": "q1", "run": 0, "runId": "run-a", "what": "15 more seconds", "status": "unanswered"})
        await manager.carry_on("r2", "q1", "yes", words="yes", asked_with="yes")
        assert await until(lambda: spy.sent)
        lesson = finished(manager)
        lesson.rid, lesson.n, lesson.deliverable, lesson.effort = "r5", 5, "lesson", "standard"
        manager.sessions = {"r5": lesson}
        lesson.result_run = {"runId": "run-b", "run": 0}
        lesson.requests.append({"id": "q1", "run": 0, "runId": "run-b", "what": "a photo", "status": "unanswered"})
        with pytest.raises(ResearchRefused, match="starts only when the user asked"):
            await manager.carry_on("r5", "q1", "yes", words="yes", asked_with="yes")
        manager.stop()
        return spy.sent[0], lesson

    sent, lesson = asyncio.run(go())
    assert 'answered your question q1 (15 more seconds) after you had finished: "yes"' in sent["brief"]
    assert lesson.status == "done" and lesson.requests[0]["status"] == "unanswered"


def test_a_lesson_edit_takes_the_steps_the_model_says_it_changed():
    from research_worker.work import edited_lesson

    old = {"title": "Stomata", "key_ideas": ["a"], "steps": [
        {"id": "q1", "prompt": "One?", "answer": "1", "hint": ""}, {"id": "q2", "prompt": "Two?", "answer": "2", "hint": ""},
        {"id": "q3", "prompt": "Three?", "answer": "3", "hint": ""}]}
    removed, changed = edited_lesson(old, {**old, "steps": [old["steps"][0], old["steps"][2]], "removed": ["q2"]},
                                     "remove the second question")
    assert [s["id"] for s in removed["steps"]] == ["q1", "q3"] and changed == ["q2"]
    edit = {"steps": [old["steps"][0], {"id": "q2", "prompt": "Two, harder?", "answer": "2"}, old["steps"][2],
                      {"prompt": "Four?", "answer": "4"}, {"id": "q1", "prompt": "sneaky change"}],
            "touched": ["q2"], "added": True}
    content, changed = edited_lesson(old, edit, "make q2 harder and add a fourth")
    assert [s["prompt"] for s in content["steps"]] == ["One?", "Two, harder?", "Three?", "Four?"]  # q1 copied as it was
    assert content["steps"][3]["id"] == "q4" and set(changed) == {"q2", "q4"}


def test_choices_are_refused_never_replaced_and_report_reads_as_research():
    from orchestrator.research.policy import admit

    with pytest.raises(ResearchRefused, match="effort is one of"):
        admit("start", words="look", effort="turbo")
    assert admit("start", words="write a report", effort="quick", deliverable="report").deliverable == "research"


# --- Phase 6: say what happens -------------------------------------------------------------------------------

def test_a_notice_reaches_only_a_verified_successor_talk_session(tmp_path):
    from types import SimpleNamespace

    from orchestrator.day import DayStore
    from orchestrator.research.run import Delivery
    from tests.fakes import announcer

    day = DayStore(root=tmp_path / "sessions")
    asked = day.here()
    browser, other_browser = object(), object()
    manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: day.day)
    session = finished(manager)
    session.origin = {"channel": "voice", "delegation_id": "d1", "thread": asked.thread}
    ended = SimpleNamespace(closed=True)  # the Talk press that asked has ended
    session._delivery = Delivery(asked=asked, day=day.day, voice_owner=ended, client=browser)
    request = {"id": "q1", "what": "15 more seconds", "why": "how they move", "status": "asked", "run": 0}
    for talk, heard in ((SimpleNamespace(closed=False, browser_client=browser), 1),
                        (SimpleNamespace(closed=False, browser_client=other_browser), 0)):
        shown = announcer(tmp_path / str(heard), manager, voice=talk, day=day)
        shown.announcer.research_request(session, request)
        assert len(shown.spoken) == heard  # only the same browser's Talk, in the same conversation
        if heard:
            assert shown.spoken[0].voice_owner is talk


def test_held_footage_outlives_an_answer_in_between(tmp_path):
    from types import SimpleNamespace

    from orchestrator.held import wire

    released = []
    held = SimpleNamespace(release=lambda rid, why: released.append(rid), on_held=[])
    research = SimpleNamespace(on_done=[])
    wire(held, research, SimpleNamespace(), SimpleNamespace())
    hook = research.on_done[-1]
    hook(SimpleNamespace(rid="r2", answered={"brief": "and the pore?"}, revised=None))
    assert released == []
    hook(SimpleNamespace(rid="r2", answered=None, revised=None))
    assert released == ["r2"]


def test_a_researcher_is_told_only_what_it_can_do_and_why_not(tmp_path):
    answer, _ = researcher(tmp_path / "a", {"effort": "quick", "deliverable": "answer"})
    prompt = answer.system_prompt()
    assert "fetch_reference_image" not in prompt and "save_findings" not in prompt and "finish.claims" in prompt
    reply, _ = asyncio.run(answer.call_tool("fetch_reference_image", {"url": "https://example.org/a.jpg"}))
    assert "answers from its earlier work" in reply["error"] and "effort" not in reply["error"]
    description, _ = researcher(tmp_path / "d", {"effort": "describe", "purpose": "description"})
    assert "finish.claims" not in description.system_prompt()


def test_a_cited_page_no_search_returned_is_said(tmp_path):
    r, _ = researcher(tmp_path, {"effort": "quick"})
    r.search_sources = ["https://example.org/stomata"]
    r.final_round = True
    asyncio.run(r.tool_finish({"title": "t", "summary": "s", "one_liner": "o", "significance": "low",
                               "sources": ["https://example.org/stomata/", "https://made-up.example/page"]}))
    assert r.result["sources_not_seen"] == ["https://made-up.example/page"]


def test_a_deep_researcher_may_schedule_clips(tmp_path):
    manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: "2026-10-10")
    session = finished(manager)
    session.effort = "deep"
    with pytest.raises(ResearchRefused) as refused:
        manager.start_mission("r2", "motion", 60, 600, acquisition={"seconds": 5})
    assert "effort" not in str(refused.value)  # refused for the camera here, never for its effort
    with pytest.raises(ResearchRefused, match="effort"):
        manager.start_mission("r2", "motion", 60, 600)  # an analysis mission's checkpoints are no deep run


# --- Phase 8: pacing, told or forced -------------------------------------------------------------------------

def test_told_pacing_gives_the_facts_and_lets_the_model_decide(tmp_path, monkeypatch):
    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        if len(sent) < 5:  # the same finding for four rounds: "forced" would make it write up after three
            return response(("record_finding", {"text": "same thing"}))
        return response(("finish", {"title": "t", "summary": "s", "one_liner": "o", "significance": "low"}))

    fake_openai(monkeypatch, create)
    r, events = researcher(tmp_path, {"effort": "quick", "pacing": "told"},
                           Effort("t", 60.0, 1.0, "big", frozenset({"web_search", "record_finding", "finish"}), 8, "low"))
    asyncio.run(r.run(SolEngine()))
    told = [i for i in sent[1]["input"] if i.get("role") == "user" and "The app:" in json.dumps(i)]
    assert told and "rounds are left of this run" in json.dumps(told[-1])
    steps = [e for e in events if e.get("event") == "step"]
    assert not any(e.get("kind") == "stall" for e in steps), steps  # never made to write up
    assert len(sent) == 5 and events[-1]["status"] == "done", (events[-1], [e.get("text") for e in steps])
