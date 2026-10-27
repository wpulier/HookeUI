"""Controlling researchers and editing their work without confusion (after the first live
session, 2026-10-04): stable numbers all day, versions of what they made (a revision is a new
version; the old one stays), revising one thing cheaply, steering one that is still working,
and asking when a reference fits two researchers."""
import asyncio
import json
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from orchestrator.brain.registry import Registry, ToolContext, ToolFailed
from orchestrator.research.desk import Ambiguous
from tests.fakes import desk, research_tools, researchers
from orchestrator.presenter import Presenter, describe, index_of, items
from orchestrator.research import ResearchManager, ResearchRefused, Session
from research_worker import researcher as researcher_module
from research_worker.researcher import Researcher
from tests.test_research import FakeHub, until

LESSON = {"title": "Guard cells", "key_ideas": ["Two guard cells surround each pore"],
          "questions": [{"question": "What surrounds the pore?", "answer": "Two guard cells", "hint": "count them"},
                        {"question": "Why do guard cells swell when they take up water by osmosis?", "answer": "turgor pressure"},
                        {"question": "Where are stomata most common?", "answer": "On the underside of leaves"}]}


@pytest.fixture
def fake_engine(monkeypatch):
    monkeypatch.setenv("MICROMIND_RESEARCH_ENGINE", "fake")


def saved(root, day, rid, n, status="done", created=0.0, **extra):
    directory = root / day / rid
    directory.mkdir(parents=True, exist_ok=True)
    data = {"rid": rid, "n": n, "brief": f"brief {rid}", "deliverable": "research", "effort": "quick", "engine": "gpt-6.1-sol",
            "purpose": "research", "label": f"label {rid}", "status": status, "created": created, "spent": 0.01,
            "outputs": [], "result": {"title": rid, "summary": "s"} if status == "done" else None, **extra}
    (directory / "session.json").write_text(json.dumps(data))


# --- stable numbers, everything of today restored -----------------------------------------

def test_numbers_stay_the_same_across_restarts_and_all_of_today_comes_back(tmp_path):
    day = time.strftime("%Y-%m-%d")
    statuses = ["done", "failed", "cancelled", "timeout", "partial", "running"] * 3  # 18 of them: more than 12
    for i, status in enumerate(statuses):
        saved(tmp_path, day, f"r{i:02d}", n=i + 3, status=status, created=float(i),
              origin={"channel": "voice", "thread": "t-stomata"} if i == 0 else {})
    saved(tmp_path, day, "rdup", n=5, created=100.0)  # an old restart gave a number twice
    manager = ResearchManager(FakeHub(), root=tmp_path)
    research = {s.rid: s for s in manager.sessions.values()}
    assert len(research) == 19  # all of today, not the latest 12
    assert [research[f"r{i:02d}"].n for i in range(18)] == [i + 3 for i in range(18)]  # never renumbered
    assert research["rdup"].n == 21  # the later of the two fives gets the next free number, saved
    assert json.loads((tmp_path / day / "rdup" / "session.json").read_text())["n"] == 21
    assert research["r05"].status == "interrupted"  # it was working when the app stopped
    assert research["r00"].wire()["thread"] == "t-stomata" and research["r00"].origin["thread"] == "t-stomata"
    assert manager.by_number(8).rid == "r05"

    async def start():
        return await manager.start("new one", effort="quick", fake={"seconds": 0.1})

    manager._pump = lambda: None  # do not run it
    assert asyncio.run(start()).n == 22  # after every number used today
    again = ResearchManager(FakeHub(), root=tmp_path)  # another restart: the same numbers
    assert {s.rid: s.n for s in again.sessions.values() if s.rid in research} == {r: s.n for r, s in research.items()}
    # A day's list (finished work that made something) keeps the numbers too.
    assert [s.n for s in sorted(again.read_day(day), key=lambda s: s.created)] == [3, 9, 15, 21]


# --- versions -------------------------------------------------------------------------------

def lesson_session(tmp_path) -> tuple[ResearchManager, Session]:
    manager = ResearchManager(FakeHub(), root=tmp_path)
    s = Session(rid="r7", n=7, brief="a quiz", deliverable="lesson", effort="standard", engine="gpt-6.1-sol", purpose="research",
                label="stomata", object_id=None, origin={}, day=manager.today(), status="running", root=tmp_path)
    manager.sessions[s.rid] = s
    old = {"kind": "lesson", "title": "Guard cells", "key_ideas": [], "steps": [
        {"id": "q1", "prompt": "Old one?", "answer": "a", "hint": ""}, {"id": "q2", "prompt": "Hard two?", "answer": "b", "hint": ""}]}
    manager._event({"event": "output", "rid": "r7", "kind": "html", "path": "outputs/01-lesson.html", "caption": "Lesson", "content": old})
    manager._event({"event": "output", "rid": "r7", "kind": "image", "path": "outputs/02-annotated.png", "caption": "Labels",
                    "content": {"kind": "labels", "items": [{"name": "pore", "where": "centre", "box": None}]}})
    new = {**old, "steps": [old["steps"][0], {"id": "q2", "prompt": "Easy two?", "answer": "b", "hint": ""}]}
    manager._event({"event": "output", "rid": "r7", "kind": "html", "path": "outputs/03-lesson.html", "caption": "Lesson v2",
                    "content": new, "replaces": "o1", "change": "make question 2 easier"})
    return manager, s


def test_versions_current_outputs_and_steps_from_the_latest(tmp_path):
    manager, s = lesson_session(tmp_path)
    assert [(o["id"], o["version"], o["replaces"], o["change"]) for o in s.outputs] == [
        ("o1", 1, None, None), ("o2", 1, None, None), ("o3", 2, "o1", "make question 2 easier")]
    assert [o["id"] for o in s.current_outputs()] == ["o2", "o3"]
    assert [o["id"] for o in s.versions_of("o1")] == [o["id"] for o in s.versions_of("o3")] == ["o1", "o3"]
    assert s.steps()[1]["prompt"] == "Easy two?" and s.lesson()["steps"][1]["prompt"] == "Easy two?"
    assert "version 2: make question 2 easier" in s.readable("q2")
    assert "older version" in s.readable() and "Easy two?" in s.readable() and "Hard two?" not in s.readable()
    # A revision of an old version replaces the current one: lines never branch.
    manager._event({"event": "output", "rid": "r7", "kind": "html", "path": "outputs/04-lesson.html", "caption": "v3",
                    "content": {**s.lesson(), "title": "Guard cells 3"}, "replaces": "o1", "change": "shorter"})
    assert s.outputs[-1]["replaces"] == "o3" and s.outputs[-1]["version"] == 3 and s.lesson()["title"] == "Guard cells 3"
    wired = {o["id"]: o["current"] for o in s.wire()["outputs"]}
    assert wired == {"o1": False, "o2": True, "o3": False, "o4": True}
    # Outputs saved before versions existed come back as version 1 of their own line.
    s.status = "done"
    manager._save(s)
    path = tmp_path / s.day / s.rid / "session.json"
    data = json.loads(path.read_text())
    data["outputs"] = [{k: v for k, v in o.items() if k not in ("id", "version", "replaces", "change", "current")}
                       for o in data["outputs"][:2]]
    path.write_text(json.dumps(data))
    back = ResearchManager(FakeHub(), root=tmp_path).get("r7")
    assert [(o["id"], o["version"], o["replaces"]) for o in back.outputs] == [("o1", 1, None), ("o2", 1, None)]


def test_the_presenter_shows_the_latest_version_first_and_keeps_the_old_one(tmp_path):
    manager, s = lesson_session(tmp_path)
    listed = items(s)
    assert [(it["id"], it["current"]) for it in listed] == [("o3", True), ("o2", True), ("o1", False)]
    assert describe(s, 0).startswith("Researcher 7's lesson, version 2") and "older version" in describe(s, 2)
    assert index_of(s, "o3") == 0 and index_of(s, "o1") == 2
    presenter = Presenter(SimpleNamespace(broadcast=lambda m: None), manager)
    presenter.go_to_step("r7", "q2")
    assert presenter.index == 0 and "Easy two?" in presenter.state_text()


# --- revising -------------------------------------------------------------------------------

def test_revising_question_2_changes_only_question_2(tmp_path, fake_engine):
    edit = {"steps": [{"id": "q1", "prompt": "The model rewrote this one too", "answer": "?"},
                      {"id": "q2", "prompt": "Do guard cells swell with water?", "answer": "Yes", "hint": "think balloons"},
                      {"id": "q3", "prompt": "And this one", "answer": "?"}]}

    async def go():
        hub = FakeHub()
        manager = ResearchManager(hub, root=tmp_path)
        done = []
        manager.on_done.append(lambda s: done.append(s.revised))
        registry = research_tools(hub, manager)
        session = await manager.start("a stomata quiz", effort="standard", deliverable="lesson",
                                      fake={"seconds": 0.1, "lesson": LESSON, "revise": {"seconds": 0.5, "lesson": edit}})
        assert await until(lambda: done)
        before = [dict(st) for st in session.steps()]
        ctx = ToolContext("voice", 1, None, "make question 2 easier", "deleg-2")
        run = registry.start("revise_work", {"researcher": str(session.n), "target": "quiz", "change": "make question 2 easier"}, ctx)
        await run.task
        assert run.ok and "version 2 of its lesson" in run.summary, run.summary
        assert await until(lambda: session.wire()["status"] == "revising")
        on_disk = json.loads((tmp_path / session.day / session.rid / "session.json").read_text())
        assert on_disk["status"] == "done"  # on disk it is still the work it was
        with pytest.raises(ResearchRefused, match="still working"):  # more work while it revises: refused
            await manager.followup(session.rid, "and another?", mode="continue", words="and another?", asked_with="and another?")
        assert await until(lambda: len(done) == 2)
        manager.stop()
        return session, before, done

    session, before, done = asyncio.run(go())
    after = session.steps()
    assert after[0] == before[0] and after[2] == before[2]  # not named: copied as they were
    assert after[1]["prompt"] == "Do guard cells swell with water?" and after[1]["id"] == "q2"
    v1, v2 = [o for o in session.outputs if o["kind"] == "html"]
    assert v2["replaces"] == v1["id"] and v2["version"] == 2 and v2["change"] == "make question 2 easier"
    assert (tmp_path / session.day / session.rid / v2["path"]).read_text().count("Do guard cells swell") == 1
    assert done[-1] == {"id": v2["id"], "version": 2, "kind": "lesson", "change": "make question 2 easier", "status": "done"}
    assert session.history[-1]["mode"] == "revise" and session.history[-1]["version"] == 2
    assert session.status == "done" and session.result["title"]  # the result is unchanged
    assert items(session)[0]["id"] == v2["id"]


def test_the_revision_is_opened_and_announced(tmp_path):
    manager, s = lesson_session(tmp_path)
    s.status, s.origin = "done", {"channel": "chat", "asked": True}
    s.last = {"mode": "revise", "output": "o3", "version": 2, "kind": "lesson", "change": "make question 2 easier", "status": "done"}
    from tests.fakes import announcer

    shown = []
    a = announcer(tmp_path, manager, presenter=SimpleNamespace(show=lambda rid, index=None: shown.append((rid, index))))
    asyncio.run(a.announcer.research_finished(s))
    completed, = [row for row in a.lines if row.get('event') == 'completed']
    assert shown == [] and completed['facts']['last'] == s.last
    output, = [o for o in completed['facts']['outputs'] if o['id'] == 'o3']
    assert output['version'] == 2 and output['replaces'] == 'o1' and output['change'] == 'make question 2 easier'
    presentation, = [row for row in a.lines if row.get('event') == 'presentation']
    assert presentation['facts']['presentation'] == {'researchId': 'r7',
        'report': 'retained in Researchers; original browser unavailable', 'contentVerified': False}
    assert all(row['type'] == 'app-notice' for row in a.lines)


def test_a_lesson_revision_is_one_edit_call_and_the_page_written_from_the_edit(tmp_path, monkeypatch):
    calls = []

    async def edit(prompt, r):
        calls.append(prompt)
        r.spend("gpt-6.1-sol", 0.01)
        return {"title": "Renamed", "steps": [{"id": "q1", "prompt": "Rewritten anyway"}, {"id": "q2", "prompt": "Easier two?",
                                                                                          "answer": "b"}]}

    async def write(prompt, r, **_):
        calls.append(prompt)
        r.spend("gpt-6.1-sol", 0.05)
        return "<!doctype html><html><body>page</body></html>", "gpt-6.1-sol"

    monkeypatch.setattr(researcher_module, "edit_json", edit)
    monkeypatch.setattr(researcher_module, "write_lesson", write)
    old = {"kind": "lesson", "title": "Guard cells", "key_ideas": ["k"], "steps": [
        {"id": "q1", "prompt": "One?", "answer": "a", "hint": ""}, {"id": "q2", "prompt": "Hard two?", "answer": "b", "hint": ""}]}
    events = []
    spec = {"rid": "r1", "dir": str(tmp_path / "r1"), "effort": "revise", "deliverable": "revise", "brief": "make question 2 easier",
            "revise": {"kind": "lesson", "change": "make question 2 easier", "target": {"id": "o1", "kind": "html", "content": old}}}
    asyncio.run(Researcher(spec, events.append, None).run(SimpleNamespace(offline=False)))
    output = next(e for e in events if e["event"] == "output")
    assert [s["prompt"] for s in output["content"]["steps"]] == ["One?", "Easier two?"] and output["content"]["title"] == "Guard cells"
    assert len(calls) == 2 and '"Hard two?"' in calls[0] and "Question 2 (id=\"q2\"): Easier two?" in calls[1]
    assert "make question 2 easier" in calls[1] and output["replaces"] == "o1" and events[-1]["status"] == "done"
    assert events[-1]["spent"] <= 0.2


def photo(tmp_path, rid="r1") -> bytes:
    image = np.full((240, 320, 3), 90, np.uint8)
    cv2.circle(image, (160, 120), 40, (40, 160, 60), -1)
    jpeg = cv2.imencode(".jpg", image)[1].tobytes()
    (tmp_path / rid / "inputs").mkdir(parents=True, exist_ok=True)
    (tmp_path / rid / "inputs" / "crop.jpg").write_bytes(jpeg)
    return jpeg


def test_a_renamed_label_is_drawn_by_the_app_and_a_compound_change_is_read_by_the_model(tmp_path, monkeypatch):
    photo(tmp_path)
    labels = [{"name": "stoma", "where": "the slit", "box": [0.4, 0.4, 0.6, 0.6]},
              {"name": "nucleus", "where": "dark dot", "box": [0.1, 0.1, 0.2, 0.2]}]
    asked = []

    async def edit(prompt, r, image=None):  # the model reads the change: no word pattern decides what it meant
        asked.append(prompt)
        r.spend("gpt-6.1-sol", 0.001)
        return {"labels": [{**labels[0], "name": "guard cell pair"}]}  # renamed one, removed the other

    async def no_picture_model(*a, **k):
        raise AssertionError("only renamed and removed: the app draws them itself")

    monkeypatch.setattr(researcher_module, "edit_json", edit)
    monkeypatch.setattr(Researcher, "_image_call", no_picture_model)
    events = []
    change = "rename stoma to guard cell pair and remove the nucleus"
    spec = {"rid": "r1", "dir": str(tmp_path / "r1"), "effort": "revise", "deliverable": "revise", "brief": "x",
            "revise": {"kind": "labelled picture", "change": change,
                       "target": {"id": "o2", "kind": "image", "path": "outputs/01-annotated.png",
                                  "content": {"kind": "labels", "items": labels}}}}
    asyncio.run(Researcher(spec, events.append, None).run(SimpleNamespace(offline=False)))
    output = next(e for e in events if e["event"] == "output")
    assert [l["name"] for l in output["content"]["items"]] == ["guard cell pair"]  # never "guard cell pair and remove..."
    assert output["replaces"] == "o2" and output["change"] == change and change in asked[0]
    assert "drawn by the app" in output["caption"] and not output["altered"]
    assert events[-1]["status"] == "done"


def test_an_illustration_is_edited_from_its_current_version(tmp_path, monkeypatch):
    (tmp_path / "r1" / "outputs").mkdir(parents=True)
    (tmp_path / "r1" / "outputs" / "01-illustration.png").write_bytes(b"version one")
    (tmp_path / "r1" / "outputs" / "02-illustration.png").write_bytes(b"version two")
    seen = []

    async def image_call(self, kind, prompt, source, count=True):
        seen.append((source, prompt))
        return {"png": b"version three", "model": "gpt-image-2.5-flare"}

    monkeypatch.setattr(Researcher, "_image_call", image_call)
    events = []
    spec = {"rid": "r1", "dir": str(tmp_path / "r1"), "effort": "revise", "deliverable": "revise", "brief": "x",
            "revise": {"kind": "illustration", "change": "make it simpler",
                       "target": {"id": "o2", "kind": "image", "path": "outputs/02-illustration.png", "generated": True,
                                  "content": {"kind": "text", "text": "a stoma"}}}}
    asyncio.run(Researcher(spec, events.append, None).run(SimpleNamespace(offline=False)))
    assert seen[0][0] == b"version two" and "make it simpler" in seen[0][1]
    output = next(e for e in events if e["event"] == "output")
    assert (tmp_path / "r1" / output["path"]).read_bytes() == b"version three" and output["replaces"] == "o2"
    assert output["generated"] and "not the specimen" in output["caption"]


def test_target_names_and_refusals(tmp_path):
    manager, s = lesson_session(tmp_path)
    s.status = "done"
    assert manager.target_output(s, "quiz")["id"] == "o3" and manager.target_output(s, "o1")["id"] == "o3"
    assert manager.target_output(s, "labels")["id"] == "o2" and manager.target_output(s, None)["id"] == "o3"
    with pytest.raises(ResearchRefused, match="made no video"):
        manager.target_output(s, "video")
    s.status = "running"
    with pytest.raises(ResearchRefused, match="still working: I can pass it a note"):
        asyncio.run(manager.followup("r7", "make it shorter", mode="revise"))


# --- which researcher ----------------------------------------------------------------------

def test_a_reference_that_fits_two_researchers_asks_which():
    def made(n, deliverable, label, kind):
        return SimpleNamespace(rid=f"r{n}", n=n, name=f"Researcher {n}", purpose="research", deliverable=deliverable,
                               label=label, brief=f"about {label}", status="done", created=float(n),
                               outputs=[{"kind": kind}] if kind else [], result={"title": label.title()})

    notes, picture = made(8, "research", "segment joint", None), made(9, "image", "segment joint", "image")
    manager = researchers(notes, picture)
    with pytest.raises(Ambiguous, match="Do you mean Researcher 9's picture on segment joint or Researcher 8's notes"):
        desk(manager).find("the joint one")
    assert desk(manager).find("the joint picture") is picture  # what it made settles it
    assert desk(manager).find("researcher 8") is notes
    on_screen = SimpleNamespace(open=True, rid="r8")
    assert desk(manager, on_screen).find("the joint one") is notes  # the one on screen wins a tie


# --- steering one that is working ---------------------------------------------------------

def test_a_note_reaches_the_working_researcher(tmp_path, fake_engine):
    async def go():
        manager = ResearchManager(FakeHub(), root=tmp_path)
        done = []
        manager.on_done.append(done.append)
        session = await manager.start("what is a stoma", effort="quick", fake={"seconds": 1.2})
        manager.note(session.rid, "focus on the guard cells")
        assert session.wire()["notes"] == 1
        assert await until(lambda: done)
        with pytest.raises(ResearchRefused, match="has finished, so a note can't reach it"):
            manager.note(session.rid, "too late")
        manager.stop()
        return session

    session = asyncio.run(go())
    assert "notes from the user: focus on the guard cells" in session.result["summary"]
    steps = (tmp_path / session.day / session.rid / "steps.jsonl").read_text()
    assert "Got a note: focus on the guard cells" in steps
    assert json.loads((tmp_path / session.day / session.rid / "inbox.jsonl").read_text())["text"] == "focus on the guard cells"
    assert ResearchManager(FakeHub(), root=tmp_path).sessions == {}  # scripted ones are not restored (fake engine)


def test_notes_already_read_are_not_read_again(tmp_path):
    events = []
    (tmp_path / "r1").mkdir()
    (tmp_path / "r1" / "inbox.jsonl").write_text(json.dumps({"text": "old note"}) + "\n" + json.dumps({"text": "new note"}) + "\n")
    r = Researcher({"rid": "r1", "dir": str(tmp_path / "r1"), "effort": "quick", "brief": "x", "inbox_from": 1}, events.append, None)
    assert r.take_notes() == ["new note"] and r.take_notes() == []
    assert Researcher.note_message(["a", "b"]) == "A note from the user while you work: a / b"


def test_every_action_on_a_researcher_is_one_log_record():
    import asyncio

    from orchestrator.brain.registry import Registry, Tool, ToolContext, ToolFailed, schema
    from orchestrator.brain.toolbox.control import log_research_control

    async def revise(ctx, args):
        return "Researcher 10 is revising its lesson (question 2 easier)"

    async def refuse(ctx, args):
        raise ToolFailed("Researcher 3 is still working")

    registry = Registry()
    registry.add(Tool("revise_work", "", schema({}), revise))
    registry.add(Tool("researcher_followup", "", schema({}), refuse))
    logged = []
    log_research_control(registry, logged.append)
    log_research_control(registry, logged.append)  # wrapping twice logs once

    async def go():
        for name in ("revise_work", "researcher_followup"):
            run = registry.start(name, {"change": "question 2 easier"}, ToolContext("voice", 1, 7, "make question 2 easier", "d1"))
            await run.task

    asyncio.run(go())
    assert [(r["op"], r["ok"]) for r in logged] == [("revise", True), ("followup", False)]
    assert logged[0]["args"] == {"change": "question 2 easier"} and logged[0]["delegationId"] == "d1"
    assert logged[1]["summary"] == "Researcher 3 is still working"
