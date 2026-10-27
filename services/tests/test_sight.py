import asyncio
import time
from types import SimpleNamespace

from orchestrator import sight as S
from orchestrator.brain.loop import Brain
from orchestrator.brain.speech import SpokenRequests
from orchestrator.brain.state import Conversation
from orchestrator.brain.toolbox import Services, build
from orchestrator.presenter import Presenter, items
from orchestrator.sight import Seen, Sight, SightView
from tests.fakes import conversation as day_conversation
from tests.test_brain import FakeAgent, FakeHub, FakeVision, obj


def info(epoch=0, dx=0.0, dy=0.0, steady=True):
    return SimpleNamespace(id=1, epoch=epoch, dx=dx, dy=dy, steady=steady, width=1280, height=720,
                           fov=SimpleNamespace(cx=640, cy=360, radius=300))


def view(epoch=0, dx=0.0):
    return SightView(1, epoch, dx, 0.0, 300.0, "Leaf epidermis with two stomata.", "leaf epidermis",
                     [Seen("s1", "stoma", "centre", (600, 340, 680, 400), 0.8)], "sharp")


def test_sight_looks_only_after_the_slide_settles_and_something_changed():
    hub = SimpleNamespace(scene=SimpleNamespace(objects={}), clients={1})
    sight = Sight(hub, agent=None)
    t = 1000.0
    assert not sight.due(info(steady=False), t)                     # moving
    assert not sight.due(info(), t + 0.3)                           # not settled yet
    assert sight.due(info(), t + S.SETTLE_S + 0.1)                  # settled, never looked
    sight.view, sight._last_look = view(), t + 1
    assert not sight.due(info(), t + 3)                             # just looked
    assert not sight.due(info(dx=50), t + 10)                       # small move: still current
    assert sight.due(info(dx=150), t + 10)                          # moved a third of the eyepiece
    assert sight.due(info(epoch=1), t + 10)                         # continuity lost


def test_seen_boxes_follow_the_slide_and_are_dropped_after_a_lost_epoch():
    v = view(dx=10.0)
    assert v.box_now(v.things[0], info(dx=30.0)) == (620, 340, 700, 400)
    assert v.box_now(v.things[0], info(epoch=2)) is None
    assert "The slide moved since this look" in v.text(info(dx=400.0)) and "s1" not in v.text(info(dx=400.0)) and "s1 stoma (centre)" in v.text(info(dx=10.0))


def make_brain(fast_reply, vision_reply, vision_delay=0.0, final_answer=None):
    hub = FakeHub()
    hub.settings.fast = "gemini-3.8-flash"
    conversation = day_conversation()
    agent = FakeAgent(hub, conversation, [vision_reply])
    slow = agent.look

    async def ask_model(*a, **k):
        await asyncio.sleep(vision_delay)
        return await slow(*a, **k)

    agent.look = ask_model
    final = ({"answer": final_answer, "actions": [], "observation_scope": "none", "input_refs": []}
             if final_answer is not None else None)
    agent.fast_model = FakeVision(fast=fast_reply, rewrite=final)
    services = Services(hub=hub, agent=agent, magnifier=SimpleNamespace(open=False, follow=lambda _id: None))
    brain = Brain(hub, agent, build(services), conversation)
    brain.services = services
    return brain, hub, agent


def test_fast_pass_acts_at_once_without_waiting_for_the_look(wall_clock):
    brain, hub, agent = make_brain({"needs_look": False, "answer": "Selecting the stoma.",
                                    "actions": [{"tool": "select", "args": {"object": "o1"}}]},
                                   {"answer": "slow answer", "targets": []}, vision_delay=5,
                                   final_answer="The stoma is selected.")
    obj(hub, "stoma")
    t0 = time.perf_counter()
    result = asyncio.run(brain.turn("select that stoma"))
    wall_clock.within(time.perf_counter() - t0, 1.0, "the turn")
    assert result["fast"] and result["answer"] == "The stoma is selected." and hub.scene.selected == "o1"
    assert not agent.calls  # no camera look was needed to select the original outline
    assert result["model"].endswith("from what it had seen") and result["fast"]


def test_fast_pass_hands_over_when_it_needs_a_look():
    brain, hub, agent = make_brain({"needs_look": True}, {"answer": "That is a stoma.", "targets": []})
    result = asyncio.run(brain.turn("what is the dark thing at the top?"))
    assert not result["fast"] and result["answer"] == "That is a stoma." and len(agent.calls) == 1


def test_teach_me_starts_what_the_model_decides_and_nothing_more():
    """"Teach me" used to add a researcher in code whatever the model said; now the model decides
    (2026-10-06: code supplies facts, the model decides)."""
    from tests.fakes import fast

    brain, hub, agent = make_brain({"intent": "request", "needs_look": False, "answer": "Making you a lesson.",
                                    "actions": [{"tool": "start_researcher", "args": {"brief": "stomata",
                                                                                      "deliverable": "lesson"}}]}, {})
    d = asyncio.run(fast(brain, "Can you teach me more about stomata"))
    assert dict(d.actions)["start_researcher"]["deliverable"] == "lesson"
    brain, hub, agent = make_brain({"intent": "request", "needs_look": False, "answer": "Outlining it.",
                                    "actions": [{"tool": "highlight", "args": {"things": ["s1"]}}]}, {})
    d = asyncio.run(fast(brain, "Can you teach me more about stomata"))
    assert "start_researcher" not in dict(d.actions)  # no code adding one behind the model's back


def test_view_note_says_what_it_sees_and_what_is_on_screen():
    brain, hub, agent = make_brain({}, {})
    obj(hub, "stoma")
    hub.scene.select("o1")
    brain.sight = SimpleNamespace(view=view())
    note = brain.view_note()
    assert "context only, do not read aloud" in note and "two stomata" in note
    assert "Selected: o1" in note and 'Outlined: o1 "stoma"' in note


def test_presenter_shows_the_requested_lesson_first():
    session = SimpleNamespace(day="2026-10-10", rid="r1", name="Researcher 1", deliverable="lesson", outputs=[
        {"kind": "image", "path": "outputs/01-a.png", "caption": "labels"},
        {"kind": "html", "path": "outputs/02-lesson.html", "caption": "Interactive lesson: stomata"}],
        result={"title": "Stomata", "summary": "..."})
    assert [i["kind"] for i in items(session)] == ["html", "image", "summary"]
    sent = []
    presenter = Presenter(SimpleNamespace(broadcast=sent.append), SimpleNamespace(get=lambda rid: session if rid == "r1" else None))
    assert "lesson" in presenter.show("r1") and sent[-1] == {"type": "presenter", "open": True, "rid": "r1", "index": 0, "step": None,
                                                                     "stepKind": None, "outcomes": {}, "shelfRemoved": [], "outputId": None, "findingRevision": None}
    assert "beside the live view" in presenter.state_text()
    presenter.close()
    assert sent[-1]["open"] is False and presenter.state_text() == ""


def test_fast_pass_falls_back_to_the_look_when_its_actions_fail():
    final_answer = "The view contains a stoma, but o9 was not found and selection failed."
    brain, hub, agent = make_brain({"needs_look": False, "answer": "Zooming in.",
                                    "actions": [{"tool": "select", "args": {"object": "o9"}}]},
                                   {"answer": "That is a stoma.", "targets": []}, final_answer=final_answer)
    result = asyncio.run(brain.turn("select the thing"))
    # The look answered; the select still did not happen, and the answer says so.
    assert not result["fast"] and result["answer"] == final_answer and hub.scene.selected is None
    assert result["actions"][0]["ok"] is False and len(agent.calls) == 1
    rewrites = [p for p in agent.fast_model.prompts if "only your notes" not in p]
    assert len(rewrites) == 1 and "o9" in rewrites[0]  # actual failure facts reach the external final provider


def test_hands_act_on_heard_words_and_a_later_hand_off_does_not_act_twice():
    calls = {"fast": 0, "followup": 0}
    brain, hub, agent = make_brain({"needs_look": False, "answer": "Selecting it.",
                                    "actions": [{"tool": "select", "args": {"object": "o1"}}]},
                                   {"answer": "vision answer", "targets": []}, final_answer="The stoma is selected.")
    quick = brain.quick

    async def counted(q, **kw):
        calls["followup" if kw.get("followup") else "fast"] += 1
        return await quick(q, **kw)

    brain.quick = counted
    obj(hub, "stoma")

    async def go():
        speech = SpokenRequests(brain)
        done = await speech.hands("Select that stoma.")
        findings = await speech.handoff("select that stoma")
        return done, findings

    done, result = asyncio.run(go())
    assert done is not None and hub.scene.selected == "o1"
    assert "from what it had seen" in result["model"] and result["answer"] == "The stoma is selected." and calls["fast"] == 1
    assert not agent.calls  # no look, and the hand-off did not act twice
    turn = next(row for row in agent.logged if row.get('kind') == 'turn')
    assert calls['followup'] == 1 and turn['calls'] == 2
    assert len(turn['actions']) == 1


def test_a_question_starts_a_look_only_when_the_view_may_have_changed():
    # ADR 0016's See loop: the user starting to talk looks again when the slide moved, something new appeared,
    # things are moving or the look is old; a still slide with a recent look costs nothing.
    import time as clock

    logged = []
    hub = SimpleNamespace(scene=SimpleNamespace(objects={}), clients={1}, latest_info=info())
    sight = Sight(hub, SimpleNamespace(log=logged.append))
    sight.view, sight._last_look = view(), clock.monotonic()

    async def still():
        return 0.0, "nothing moved"

    async def swimming():
        return 0.6, "6 of 10 small things moved, about 40 px/s"

    assert asyncio.run(sight.question_coming(still)) is None and not sight.recheck
    assert logged[-1]["reason"] is None and logged[-1]["lookAgain"] is False
    assert sight.motion_words() == "nothing moved"
    assert asyncio.run(sight.question_coming(swimming)) == "things are moving" and sight.recheck
    assert "6 of 10" in sight.motion_words()
    sight.recheck = False
    hub.latest_info = info(dx=150)
    assert asyncio.run(sight.question_coming(still)) == "the slide moved"
    hub.latest_info = info()
    sight.still_changed("content", hub.latest_info)
    sight.recheck = False
    assert asyncio.run(sight.question_coming(still)) == "something new appeared"
    sight._content_since_look, sight._last_look = False, clock.monotonic() - S.QUESTION_AGE_S - 1
    assert asyncio.run(sight.question_coming(still)) == "the look is old"


def test_question_looks_keep_to_their_budget():
    hub = SimpleNamespace(scene=SimpleNamespace(objects={}), clients={1}, latest_info=info(dx=150))
    sight = Sight(hub, SimpleNamespace(log=lambda r: None))
    sight.view = view()
    looked = [asyncio.run(sight.question_coming()) is not None for _ in range(S.QUESTION_LOOKS + 2)]
    assert looked == [True] * S.QUESTION_LOOKS + [False, False]
