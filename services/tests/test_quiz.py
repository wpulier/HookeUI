"""A live quiz on the slide (W6-2, orchestrator/quiz.py): find and evidence steps in a lesson,
a click on a find step judged by the model from the facts the app gives it, and how the user did
on each step kept in the day's journal, so "go over what I missed" works after a restart too."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from orchestrator import learner
from orchestrator.brain.registry import ToolContext
from orchestrator.brain.toolbox import Services, build
from orchestrator.day import DayStore
from orchestrator.presenter import Presenter
from orchestrator.quiz import Quiz, judge_prompt, sight_at, step_line, tally
from orchestrator.research.content import render
from orchestrator.research import Session
from orchestrator.scene import SceneObject
from orchestrator.sight import Seen, SightView
from research_worker.results import lesson_content, step_kind
from research_worker.work import SCHEMAS, edited_lesson
from tests.fakes import Hub, desk, researchers
from tests.waiting import HANG_S

QUESTIONS = [
    {"question": "What do guard cells control?", "answer": "the pore's opening", "hint": "think gas exchange"},
    {"question": "Find a guard cell on your slide and click it.", "answer": "a bean-shaped cell beside a pore",
     "hint": "look beside a slit", "kind": "find", "target": "guard cell"},
    {"question": "The app calls this outline a stoma. What would confirm or refute it?", "kind": "evidence",
     "answer": "two guard cells around a pore would confirm it"},
    {"question": "A made-up kind is a plain question", "answer": "yes", "kind": "dance"},
]


def lesson():
    return lesson_content("Guard cells", ["two guard cells surround a pore"], QUESTIONS)


def session(steps, object_id=None, n=4):
    s = Session(rid=f"r{n}", day="2026-10-10", n=n, engine="fake", purpose="research", deliverable="lesson", effort="standard",
                        label="guard cells", brief="a quiz on guard cells", status="done", created=1.0, finished=1.0,
                        object_id=object_id, outputs=[{"id": "o1", "kind": "html", "path": "outputs/l.html", "caption": "Interactive lesson"}],
                        result={"title": "Guard cells", "summary": "A quiz."}, origin={})
    s.steps = lambda: list(steps)
    s.line = lambda: f"{s.name}: {s.label}, {s.status}"
    return s


def outline(hub, label="", status="outline", source="click", box=(100, 100, 140, 140), **more):
    x0, y0, x1, y1 = box
    return hub.scene.add(SceneObject(id=hub.scene.new_id(), kind="highlight", polygons=[[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]],
                                     anchor_frame=7, anchor_epoch=0, anchor_offset=(0.0, 0.0), source=source, label=label,
                                     status=status, **more))


def stack(tmp_path, steps, object_id=None):
    """A hub, a day, one lesson researcher, the presenter and the brain's tools over them."""
    hub = Hub()
    hub.infos = {7: hub.latest_info}
    day = DayStore(root=tmp_path / "sessions")
    s = session(steps, object_id)
    manager = researchers(s)
    presenter = Presenter(hub, manager, outcomes=day.outcomes_of)
    registry = build(Services(hub=hub, research=manager, desk=desk(manager, presenter), presenter=presenter, day=day))
    return SimpleNamespace(hub=hub, day=day, session=s, manager=manager, presenter=presenter, registry=registry)


def run_tool(registry, name, args):
    async def go():
        run = registry.start(name, args, ToolContext("chat", 1, 7, ""))
        await asyncio.wait_for(run.task, HANG_S)
        return run

    return asyncio.run(go())


# --- step kinds in a lesson's content -------------------------------------------------------
def test_find_and_evidence_steps_are_kept_in_lesson_content_and_plain_ones_keep_their_shape():
    steps = lesson()["steps"]
    assert steps[0] == {"id": "q1", "prompt": "What do guard cells control?", "answer": "the pore's opening",
                        "hint": "think gas exchange"}  # a plain question: the shape lessons always had
    assert steps[1]["kind"] == "find" and steps[1]["target"] == "guard cell"
    assert steps[2]["kind"] == "evidence" and "target" not in steps[2]
    assert "kind" not in steps[3] and step_kind(steps[3]) == "question"
    assert json.loads(json.dumps(lesson())) == lesson()  # what the worker emits is what the app reads
    text = "\n".join(render(lesson()))
    assert "q2 (question 2 of 4, find it on the slide and click it: guard cell)" in text
    assert "q3 (question 3 of 4, evidence for the app's name for an outline)" in text
    item = SCHEMAS["make_lesson"]["parameters"]["properties"]["questions"]["items"]["properties"]
    assert item["kind"]["enum"] == ["question", "find", "evidence"] and "target" in item


def test_revising_a_lesson_keeps_each_steps_kind():
    old = lesson()
    # A named step edited: what the edit leaves out (its kind, its target) stays.
    named, changed = edited_lesson(old, {"steps": [{"id": "q2", "prompt": "Find one guard cell and click it."}]},
                                   "make question 2 shorter")
    assert changed == ["q2"] and named["steps"][1] == {**old["steps"][1], "prompt": "Find one guard cell and click it."}
    # The whole lesson returned without kinds: each step keeps its own, matched by its id.
    whole, _ = edited_lesson(old, {"title": "Guard cells", "steps": [{"id": s["id"], "prompt": s["prompt"] + "!",
                                                                     "answer": s["answer"]} for s in old["steps"]]},
                             "add some energy")
    assert [step_kind(s) for s in whole["steps"]] == ["question", "find", "evidence", "question"]
    assert whole["steps"][1]["target"] == "guard cell"


# --- outcomes: journal events of the session ------------------------------------------------------
def test_outcomes_are_journal_events_that_survive_a_restart(tmp_path):
    day = DayStore(root=tmp_path)
    day.record_outcome("r4", "q1", "right", by="click")
    day.record_outcome("r4", "q2", "wrong")
    day.record_outcome("r4", "q2", "right")  # the latest for a step counts
    day.record_outcome("r4", "q3", "skipped")
    with pytest.raises(ValueError):
        day.record_outcome("r4", "q4", "brilliant")
    assert day.outcomes_of("r4") == {"q1": "right", "q2": "right", "q3": "skipped"}
    events = [json.loads(line) for line in day.journal.path.read_text().splitlines()]
    assert [e["outcome"] for e in events if e["type"] == "quiz-outcome"] == ["right", "wrong", "right", "skipped"]
    again = DayStore(root=tmp_path)  # a restart: rebuilt from the journal
    assert again.outcomes_of("r4") == {"q1": "right", "q2": "right", "q3": "skipped"}
    assert again.outcomes["r4"]["q1"]["by"] == "click" and again.outcomes_of("r9") == {}
    left = again.sid
    again.new()  # a new session starts the quiz afresh too
    assert again.outcomes_of("r4") == {}
    assert again.elsewhere(left).outcomes_of("r4") == {"q1": "right", "q2": "right", "q3": "skipped"}  # kept there


# --- a click on a find step -------------------------------------------------------------------
class Judge:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    async def __call__(self, prompt):
        self.prompts.append(prompt)
        return dict(self.reply)


def quiz_for(s, reply):
    judge, said, logged = Judge(reply), [], []
    sight = SightView(frame_id=7, epoch=0, dx=0.0, dy=0.0, radius=300.0, summary="A leaf epidermis.", specimen="leaf",
                      things=[Seen("s1", "guard cell", "left of centre", (90, 90, 150, 150), 0.7),
                              Seen("s2", "epidermis", "everywhere", (0, 0, 1280, 720), 0.9)], focus="sharp")
    quiz = Quiz(s.hub, s.presenter, s.day, ask=judge, say=lambda text, oid, facts: said.append((text, oid, facts)),
                sight_view=lambda: sight, log=logged.append)
    return quiz, judge, said, logged


def test_a_click_on_a_find_step_gives_the_model_the_facts_and_keeps_its_verdict(tmp_path):
    s = stack(tmp_path, lesson()["steps"])
    s.presenter.go_to_step("r4", "q2")
    clicked = outline(s.hub, label="guard cell", status="unsure", maybe=["subsidiary cell"], confidence=0.55,
                      seen="A bean-shaped cell beside a slit.")
    # The model says "wrong" although the names match: code compares no names, the verdict is the model's.
    quiz, judge, said, logged = quiz_for(s, {"verdict": "wrong", "say": "That looks like a subsidiary cell; look right beside the slit."})
    assert quiz.find_step() is not None
    result = asyncio.run(quiz.clicked(clicked.id))
    prompt = judge.prompts[0]
    assert 'What to find: "guard cell"' in prompt and "Find a guard cell on your slide" in prompt
    assert f'{clicked.id}, named "guard cell" (the app is not sure; maybe subsidiary cell; confidence 0.55; outlined by the user\'s click)' in prompt
    assert 'What naming saw in it: "A bean-shaped cell beside a slit."' in prompt
    assert 'running look\'s name for that spot: "guard cell" (left of centre' in prompt  # the smallest box holding the click
    assert learner.of(s.hub.settings) in prompt
    assert result == {"verdict": "wrong", "say": "That looks like a subsidiary cell; look right beside the slit."}
    assert s.day.outcomes_of("r4") == {"q2": "wrong"}
    click_facts = {"rid": "r4", "step": "q2", "objectId": clicked.id, "target": "guard cell",
                   "label": "guard cell", "status": "unsure", "sight": "guard cell"}
    assert said == [("That looks like a subsidiary cell; look right beside the slit.", clicked.id,
                     {**click_facts, "verdict": "wrong", "judgeStatus": "judged", "suppliedVerdict": "wrong",
                      "feedbackAvailable": True})]
    assert logged[0]["kind"] == "quiz-click" and logged[0]["sight"] == "guard cell" and logged[0]["verdict"] == "wrong"
    # Not sure: it says what would help and records nothing.
    quiz.ask = Judge({"verdict": "unsure", "say": "Click it again a little more precisely."})
    result = asyncio.run(quiz.clicked(clicked.id))
    assert s.day.outcomes_of("r4") == {"q2": "wrong"} and said[-1][2]["verdict"] == "unsure"
    assert result == {"verdict": "unsure", "say": "Click it again a little more precisely."}
    assert said[-1] == ("Click it again a little more precisely.", clicked.id,
                        {**click_facts, "verdict": "unsure", "judgeStatus": "judged", "suppliedVerdict": "unsure",
                         "feedbackAvailable": True})
    assert [e["outcome"] for e in s.day.journal.load() if e["type"] == "quiz-outcome"] == ["wrong"]
    # Right without model feedback: keep the verdict as facts, with no invented sentence.
    quiz.ask = Judge({"verdict": "right"})
    result = asyncio.run(quiz.clicked(clicked.id))
    assert result == {"verdict": "right", "say": None}
    assert s.day.outcomes_of("r4") == {"q2": "right"} and said[-1][0] is None
    assert said[-1] == (None, clicked.id,
                        {**click_facts, "verdict": "right", "judgeStatus": "judged", "suppliedVerdict": "right",
                         "feedbackAvailable": False})
    assert [e["outcome"] for e in s.day.journal.load() if e["type"] == "quiz-outcome"] == ["wrong", "right"]


def test_a_click_is_judged_only_while_a_find_step_is_open(tmp_path):
    s = stack(tmp_path, lesson()["steps"])
    clicked = outline(s.hub, label="guard cell")
    quiz, judge, said, _ = quiz_for(s, {"verdict": "right", "say": "Yes."})
    assert quiz.find_step() is None and asyncio.run(quiz.clicked(clicked.id)) is None  # no lesson open
    s.presenter.go_to_step("r4", "q1")  # a plain question
    assert asyncio.run(quiz.clicked(clicked.id)) is None
    s.presenter.go_to_step("r4", "q2")
    s.presenter.close()  # the lesson closed: clicks are clicks again
    assert asyncio.run(quiz.clicked(clicked.id)) is None
    assert judge.prompts == [] and said == [] and s.day.outcomes_of("r4") == {}


def test_the_click_command_hands_a_named_click_to_the_quiz(tmp_path):
    from orchestrator import commands

    s = stack(tmp_path, lesson()["steps"])
    s.presenter.go_to_step("r4", "q2")
    clicked = outline(s.hub)
    quiz, judge, said, _ = quiz_for(s, {"verdict": "right", "say": "Yes, a guard cell."})

    async def identify(oid):  # naming the click (agent.identify) names it first
        s.hub.scene.update(oid, label="guard cell", status="likely")

    app = SimpleNamespace(agent=SimpleNamespace(identify=identify, log=lambda r: None), hub=s.hub, quiz=quiz)
    asyncio.run(commands._identify(app, clicked.id))
    assert f'{clicked.id}, named "guard cell" (the app\'s best guess' in judge.prompts[0]
    assert said[0][0] == "Yes, a guard cell." and s.day.outcomes_of("r4") == {"q2": "right"}


def test_the_running_looks_name_counts_only_where_the_click_is_and_the_slide_has_not_jumped():
    view = SightView(frame_id=7, epoch=0, dx=0.0, dy=0.0, radius=300.0, summary="", specimen="", focus="",
                     things=[Seen("s1", "stoma", "centre", (600, 300, 640, 340), 0.8)])
    info = SimpleNamespace(epoch=0, dx=10.0, dy=0.0)  # the slide moved 10 px right since the look
    assert sight_at(view, info, 615, 320).name == "stoma" and sight_at(view, info, 645, 320).name == "stoma"
    assert sight_at(view, info, 100, 100) is None
    assert sight_at(view, SimpleNamespace(epoch=1, dx=0.0, dy=0.0), 615, 320) is None  # continuity lost
    assert "nothing listed there" in judge_prompt({"prompt": "Find a stoma", "kind": "find"}, None, None, "")


# --- going over what was missed --------------------------------------------------------------
def test_go_over_what_i_missed_reoffers_the_wrong_and_skipped_steps_after_a_restart_too(tmp_path):
    s = stack(tmp_path, lesson()["steps"])
    assert run_tool(s.registry, "result_step", {"researcher": "the quiz", "to": "next"}).ok
    assert run_tool(s.registry, "mark_step", {"outcome": "right"}).summary.startswith("noted: q1 right")  # the step open
    run_tool(s.registry, "mark_step", {"researcher": "4", "step": "2", "outcome": "wrong"})
    run_tool(s.registry, "mark_step", {"step": "q4", "outcome": "skipped"})
    assert run_tool(s.registry, "mark_step", {"step": "q9", "outcome": "right"}).ok is False
    assert run_tool(s.registry, "mark_step", {"step": "q3", "outcome": "so-so"}).ok is False
    first = run_tool(s.registry, "result_step", {"to": "missed"})
    assert first.ok and s.presenter.step == "q2" and "step 2 of 4 (q2)" in first.summary
    assert "They had it wrong earlier today." in first.summary
    assert "How they did today: q1 right, q2 wrong, q4 skipped; not marked yet: q3." in first.summary
    assert 'This is a find step: the user looks for "guard cell"' in first.summary
    assert run_tool(s.registry, "result_step", {"to": "missed"}).summary.startswith("Researcher 4's lesson is open on step 4")
    assert s.presenter.step == "q4"
    run_tool(s.registry, "result_step", {"to": "missed"})
    assert s.presenter.step == "q2"  # round again from the start
    assert s.presenter.wire()["outcomes"] == {"q1": "right", "q2": "wrong", "q4": "skipped"}
    assert s.presenter.wire()["stepKind"] == "find"
    assert "how the user did today: q1 right, q2 wrong, q4 skipped" in s.presenter.state_text()

    # A restart: the day comes back from its journal, and so does what is left to go over.
    later = stack(tmp_path, lesson()["steps"])
    assert run_tool(later.registry, "result_step", {"researcher": "the quiz", "to": "missed"}).ok and later.presenter.step == "q2"
    run_tool(later.registry, "mark_step", {"step": "q2", "outcome": "right"})
    run_tool(later.registry, "mark_step", {"step": "q4", "outcome": "right"})
    done = run_tool(later.registry, "result_step", {"to": "missed"})
    assert done.ok is False and done.summary == "nothing missed to go over (q1 right, q2 right, q4 right; not marked yet: q3)"


def test_an_evidence_step_gives_the_apps_guess_and_with_the_user_leading_asks_first(tmp_path):
    s = stack(tmp_path, lesson()["steps"])
    stoma = outline(s.hub, label="stoma", status="likely", source="ai:test")
    s.session.object_id = stoma.id  # the researcher was started on it
    s.hub.settings.user_leads = False
    told = run_tool(s.registry, "result_step", {"researcher": "4", "to": "q3"}).summary
    assert f'This is an evidence step about outline {stoma.id}, named "stoma" (the app\'s best guess; outlined by the app)' in told
    assert "Present that name as the app's guess" in told and "ask what they see" not in told
    s.hub.settings.user_leads = True  # an instruction to the model, not a gate
    told = run_tool(s.registry, "result_step", {"to": "again"}).summary
    assert "The user leads today: first ask what they see in it, and give the app's guess only after they have answered." in told
    voice = s.presenter.state_text(answers=False)
    assert "an evidence step" in voice and "they are asked what they see before the guess" in voice and '"stoma"' not in voice
    # Nothing outlined: the user is asked to click the thing first.
    s.hub.scene.remove(stoma.id)
    assert "nothing is outlined or selected now" in step_line(lesson()["steps"][2], s.hub, s.session)


def test_tally_says_what_is_marked_and_what_is_not():
    steps = lesson()["steps"]
    assert tally(steps, {}) == "no step marked yet"
    assert tally(steps, {"q1": "right", "q3": "wrong"}) == "q1 right, q3 wrong; not marked yet: q2, q4"


def test_owned_find_step_is_captured_before_naming_and_not_replaced_by_peer(tmp_path):
    s = stack(tmp_path, lesson()["steps"])
    quiz, judge, said, _ = quiz_for(s, {"verdict": "right", "say": "Yes."})
    class Client:
        def offer(self, value): pass
    a, b = Client(), Client()
    s.presenter.for_client(a).go_to_step("r4", "q2")
    captured = quiz.capture_click(a)
    s.presenter.for_client(a).go_to_step("r4", "q1")
    s.presenter.for_client(b).go_to_step("r4", "q1")
    clicked = outline(s.hub, label="guard cell")
    asyncio.run(quiz.clicked(clicked.id, captured=captured))
    assert s.day.outcomes_of("r4") == {"q2": "right"}
    assert len(judge.prompts) == 1
