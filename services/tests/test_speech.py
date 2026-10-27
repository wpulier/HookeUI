"""Spoken requests (brain/speech.py): what the user says is joined into requests, acted on as
it is heard, handed over without acting twice, answered where it was said, and pointed along
with only when it is a question. The cases are from the first live session (2026-10-04):
"Magnify" ... "that" arrived as pieces, "Okay" outlined a bristle, a hand-off of three
questions reused a "close it" and got no look, a spoken reply went to whichever conversation
was open."""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator.brain import speech
from orchestrator.brain.speech import SpokenRequests
from orchestrator.brain.state import Conversation
from orchestrator.day import DayStore
from orchestrator.dialogue import Dialogue
from tests.test_brain import FakeVision
from tests.test_sight import make_brain


@pytest.fixture(autouse=True)
def quick_timings(monkeypatch):
    monkeypatch.setattr(speech, "JOIN_PIECES_S", 0.05)
    monkeypatch.setattr(speech, "JOIN_UNFINISHED_S", 1.0)  # words judged unfinished wait for the rest
    monkeypatch.setattr(speech, "POINT_AFTER_S", 0.1)


class Voice:
    """A stand-in GPT-Live session: what it is told silently."""

    def __init__(self):
        self.closed, self.told = False, []

    async def inject(self, text, respond=False, **_log):
        self.told.append(text)


def spoken(tmp_path, fast_reply, vision_reply=None):
    brain, hub, agent = make_brain(fast_reply, vision_reply or {"answer": "A stoma.", "targets": []})
    day = DayStore(root=tmp_path)
    brain.conversation = agent.conversation = Conversation(store=day)
    shown = []
    voice = Voice()
    return SpokenRequests(brain, Dialogue(day, shown.append), voice), brain, hub, agent, day, shown, voice


def test_next_spoken_action_survives_collected_previous_request(tmp_path):
    import gc
    import weakref

    s, *_ = spoken(tmp_path, {"intent": "filler", "needs_look": False})

    async def go():
        first = await s.hands("hello")
        earlier = s.requests[-1]
        old = weakref.ref(earlier.work["hands"])
        await asyncio.sleep(0)
        earlier.close()  # a closed request keeps its identity and judgment, not its held camera or context
        assert "origin" not in earlier.work and earlier.intent == "filler" and first['decision'].intent == 'filler'
        earlier.work.clear()
        gc.collect()
        assert old() is None
        result = await s.hands("photo from the internet")
        assert result['decision'].intent == 'filler'  # the planner was reached
        assert [r.heard.words for r in s.requests] == ["hello", "photo from the internet"]

    asyncio.run(go())


def test_pieces_of_one_request_are_joined_into_one_turn(tmp_path):
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {"intent": "question", "needs_look": True})

    async def go():
        s.heard("Magnify")
        await asyncio.sleep(0.02)
        s.heard("that")
        await asyncio.sleep(0.4)

    asyncio.run(go())
    assert [t.text for t in day.active.turns if t.role == "user"] == ["Magnify that"]
    # the chat shows the pieces as one line that grows, filed whole when the request settles
    assert [(line["text"], line.get("open", False)) for line in shown] == [("Magnify", True), ("Magnify that", True),
                                                                           ("Magnify that", False)]
    s.close()
    assert [(line["text"], line.get("open", False)) for line in shown][-1] == ("Magnify that", False)
    assert [l["text"] for l in day.active.lines] == ["Magnify that"]
    assert all(line["thread"] == "t1" for line in shown)


def test_a_piece_the_model_calls_filler_is_never_acted_on(tmp_path):
    s, brain, hub, agent, day, shown, voice = spoken(
        tmp_path, {"intent": "filler", "needs_look": False, "answer": "",
                   "actions": [{"tool": "highlight", "args": {"things": ["s1"]}}]})

    async def go():
        s.heard("Okay")
        await asyncio.sleep(0.5)

    asyncio.run(go())
    assert not hub.scene.objects and agent.fast_model.prompts  # the model judged it, and nothing was done


def test_unfinished_words_are_joined_with_the_rest_and_a_cancel_calls_it_off(tmp_path):
    """Live session 2026-10-04 14:08:42: "Can you close the" ... "Oh, wait, never mind"."""
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {})
    replies = {"Can you close the": {"intent": "unfinished", "needs_look": False, "answer": "", "actions": []},
               "Can you close the Oh, wait, never mind": {"intent": "cancel", "needs_look": False, "answer": "",
                                                          "actions": []}}
    seen = []

    async def ask(images, prompt, max_output_tokens=0):
        words = prompt.split('User: "', 1)[1].split('"', 1)[0]
        seen.append(words)
        return SimpleNamespace(data=replies[words], latency_ms=5.0)

    agent.fast_model.ask = ask

    async def go():
        s.heard("Can you close the")
        await asyncio.sleep(0.3)
        s.heard("Oh, wait, never mind")
        await asyncio.sleep(0.4)

    asyncio.run(go())
    assert seen == ["Can you close the", "Can you close the Oh, wait, never mind"]  # joined by the model's judgment
    assert s.cancelled_at > float("-inf")


def test_hands_act_at_once_and_the_hand_off_does_not_act_again(tmp_path):
    s, brain, hub, agent, day, shown, voice = spoken(
        tmp_path, {"intent": "request", "needs_look": False, "answer": "Selecting it.",
                   "actions": [{"tool": "select", "args": {"object": "o1"}}]})
    from tests.test_brain import obj

    obj(hub, "stoma")
    from orchestrator.ticket import current

    # External final reply is independent of the earlier planning reply.
    final = {"answer": "Selecting it.", "actions": [], "observation_scope": "none", "input_refs": []}
    agent.fast_model.rewrite = final
    entries, finished = [], []
    brain.registry.finished.append(lambda run, ctx: finished.append((run, ctx)))
    ask = agent.fast_model.ask

    async def observe(images, prompt, **kw):
        admission = current().admission
        attempt = admission.attempts[-1]
        reply = await ask(images, prompt, **kw)
        entries.append((admission, attempt, tuple(images), prompt, reply.data))
        return reply

    agent.fast_model.ask = observe  # passive external-provider observer

    async def go():
        s.heard("select that stoma")
        await asyncio.sleep(0.01)
        return await s.handoff("select that stoma", "deleg-1")  # arrives while the words are still being joined

    found = asyncio.run(go())
    assert hub.scene.selected == "o1" and found["answer"] == "Selecting it."
    assert not agent.calls  # one quick decision, no image look
    assert len(entries) == len(agent.fast_model.prompts) == 2
    admission = entries[0][0]
    assert all(entry[0] is admission and not entry[2] for entry in entries)
    assert [entry[1].phase for entry in entries] == ["hands", "query"]
    assert [attempt.phase for attempt in admission.attempts] == ["hands", "query"]
    assert admission.used == 2 and all(attempt.status == "done" for attempt in admission.attempts)
    assert {entry[1].attempt_id for entry in entries} == {attempt.attempt_id for attempt in admission.attempts}
    assert len(finished) == 1 and finished[0][0].tool == "select" and finished[0][0].ok is True
    run, ctx = finished[0]
    assert ctx.admission is admission and run.occurrence.request_id == admission.request_id
    assert found["actions"][0]["actionId"] == run.occurrence.action_id
    assert len(found["actions"]) == 1 and "selected the stoma" in entries[-1][3]
    assert entries[-1][4] == final and found["answer"] == final["answer"]
    assert any("already done" in t for t in voice.told)


def test_a_hand_off_of_other_words_gets_its_own_look(tmp_path):
    s, brain, hub, agent, day, shown, voice = spoken(
        tmp_path, {"intent": "request", "needs_look": False, "answer": "Closing it.", "actions": [{"tool": "close_magnifier", "args": {}}]},
        {"answer": "It is a dicot stem.", "targets": []})

    async def go():
        s.heard("close it")
        await asyncio.sleep(0.3)
        agent.fast_model = FakeVision(fast={"intent": "question", "needs_look": True})
        s.heard("what kind of stem is this and what is a dicot")
        # The voice's own task text runs everything together; the request is matched by what was heard.
        return await s.handoff("close it what kind of stem is this and what is a dicot")

    found = asyncio.run(go())
    assert found["answer"] == "It is a dicot stem." and agent.calls  # looked


def test_the_reply_goes_to_the_conversation_the_words_were_said_in(tmp_path):
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {"intent": "question", "needs_look": True})
    hub.settings.reflex = "after-voice"  # as before ADR 0016: a question the voice answers alone gets no closer look

    async def go():
        s.heard("what is the green blob?")
        await asyncio.sleep(0.4)
        day.open_thread("Something else")  # the user switched before the voice finished replying
        s.replied("It looks like a chloroplast.")

    asyncio.run(go())
    assert [t.text for t in day.threads["t1"].turns] == ["what is the green blob?", "It looks like a chloroplast."]
    assert shown[-1]["thread"] == "t1" and day.threads["t2"].turns == []


def test_the_voice_is_pointed_along_with_only_when_the_model_calls_it_a_question(tmp_path):
    reply = {"intent": "statement", "needs_look": False, "actions": [{"tool": "highlight", "args": {"things": ["s1"]}}]}
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, reply)
    brain.sight = SimpleNamespace(view=SimpleNamespace(current=lambda info: True, things=[], text=lambda info: ""))
    assert asyncio.run(s.point("I'm going to change the slide now")) == []  # the model: a remark
    assert len(agent.fast_model.prompts) == 1


def test_words_that_need_a_look_start_it_and_the_hand_off_reuses_it(tmp_path):
    # W2-1: the fast pass on the heard words says "needs a look", so the vision call starts
    # then; the hand-off of the same words uses that decision and that look, with no second
    # fast pass and no second look.
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {"intent": "question", "needs_look": True},
                                                     {"answer": "That is a stoma.", "targets": []})

    async def go():
        s.heard("what is the oval in the middle")
        await asyncio.sleep(0.2)  # joined, hands ran, the look started
        assert len(agent.calls) == 1 and len(agent.fast_model.prompts) == 1
        return await s.handoff("what is the oval in the middle", "d1")

    findings = asyncio.run(go())
    assert "stoma" in findings["answer"]
    assert len(agent.calls) == 1 and len(agent.fast_model.prompts) == 1  # nothing ran twice
    turn = [r for r in agent.logged if r.get("kind") == "turn"][-1]
    assert turn["lookReused"] is True and turn["handsUsed"] is True


def test_a_look_nobody_picks_up_is_dropped_and_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(speech, "LOOK_KEPT_S", 0.3)
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {"intent": "question", "needs_look": True})
    hub.settings.reflex = "after-voice"  # as before ADR 0016: a question the voice answers alone gets no closer look

    async def go():
        s.heard("what is the oval in the middle")
        await asyncio.sleep(0.6)  # the voice answered by itself: no hand-off

    asyncio.run(go())
    assert any(r.get("kind") == "look-unused" for r in agent.logged)


def test_with_speculation_off_the_look_waits_for_the_hand_off(tmp_path):
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {"intent": "question", "needs_look": True},
                                                     {"answer": "That is a stoma.", "targets": []})
    hub.settings.speculate = "off"

    async def go():
        s.heard("what is the oval in the middle")
        await asyncio.sleep(0.2)
        assert agent.calls == []  # nothing started before the hand-off
        return await s.handoff("what is the oval in the middle", "d1")

    asyncio.run(go())
    assert len(agent.calls) == 1


def test_a_request_the_voice_never_hands_over_is_done_and_said(tmp_path):
    """Live demo 2026-10-06 00:33: "Can you zoom in on the plant cell" needed a look; GPT-Live
    said "Sure, I'll bring it closer" and never handed over, and nothing happened."""
    s, brain, hub, agent, day, shown, voice = spoken(
        tmp_path, {"intent": "request", "needs_look": True},
        {"answer": "Selecting the plant cell.", "targets": [{"box_2d": [1, 2, 3, 4], "label": "plant cell"}],
         "actions": [{"tool": "select", "args": {"object": "target 1"}}]})
    said = []
    brain.narrator = SimpleNamespace(offer=said.append)
    from orchestrator.ticket import current

    final = {"answer": "Selecting the plant cell.", "actions": [], "observation_scope": "none", "input_refs": []}
    agent.fast_model.rewrite = final
    entries, finished = [], []
    brain.registry.finished.append(lambda run, ctx: finished.append((run, ctx)))
    ask = agent.fast_model.ask

    async def observe(images, prompt, **kw):
        admission = current().admission
        attempt = admission.attempts[-1]
        reply = await ask(images, prompt, **kw)
        entries.append((admission, attempt, tuple(images), prompt, reply.data))
        return reply

    agent.fast_model.ask = observe

    async def go():
        s.heard("Can you select the plant cell")
        await asyncio.sleep(0.6)  # no hand-off comes
        return await s.handoff("Can you select the plant cell", "late-1")  # ...until now: nothing is done twice

    found = asyncio.run(go())
    assert hub.scene.selected is not None and hub.scene.objects[hub.scene.selected].label == "plant cell"
    assert len(agent.calls) == 1  # one look, used once, for both
    assert said and "selected the plant cell" in said[0].text and found["answer"] == "Selecting the plant cell."
    assert said[0].provenance == "data"  # offered facts are not an observed model utterance
    # The image look already consumed the middle attempt; reconciliation uses
    # the reserved final attempt. This expectation was stale on f331d15c too.
    assert len(entries) == 2 and [entry[1].phase for entry in entries] == ["hands", "final"]
    admission = entries[0][0]
    assert all(entry[0] is admission and not entry[2] for entry in entries)
    assert [attempt.phase for attempt in admission.attempts] == ["hands", "look", "final"]
    assert admission.used == 3 and all(attempt.status == "done" for attempt in admission.attempts)
    assert len({attempt.attempt_id for attempt in admission.attempts}) == 3
    assert len(finished) == 1 and finished[0][0].tool == "select" and finished[0][0].ok is True
    run, ctx = finished[0]
    assert ctx.admission is admission and run.occurrence.request_id == admission.request_id
    assert len(found["actions"]) == 1 and found["actions"][0]["actionId"] == run.occurrence.action_id
    assert "selected the plant cell" in entries[-1][3]
    assert entries[-1][4] == final and found["answer"] == final["answer"]


def test_a_question_the_voice_answers_itself_is_not_acted_on(tmp_path):
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {"intent": "question", "needs_look": True})
    hub.settings.reflex = "after-voice"  # as before ADR 0016: a question the voice answers alone gets no closer look

    async def go():
        s.heard("what plant is it")
        await asyncio.sleep(0.6)

    asyncio.run(go())
    assert not any(r.get("kind") == "turn" for r in agent.logged)  # the voice answered it; the app does nothing
    assert not hub.scene.objects


def _slow_fast_pass(agent, seconds):
    """The fast pass takes this long (Gemini on 2026-10-06: about 2 s)."""
    ask = agent.fast_model.ask

    async def slow(images, prompt, max_output_tokens=0):
        await asyncio.sleep(seconds)
        return await ask(images, prompt, max_output_tokens)

    agent.fast_model.ask = slow


def test_a_hand_off_of_words_answered_from_notes_asks_the_fast_pass_once(tmp_path):
    """2026-10-06: 40 of 47 hand-offs asked the fast pass a second time (about 2 s), because hands
    kept no decision when there was nothing to do."""
    s, brain, hub, agent, day, shown, voice = spoken(
        tmp_path, {"intent": "question", "needs_look": False, "answer": "Those are sperm heads.", "actions": []})

    async def go():
        s.heard("what are the little dots?")
        await asyncio.sleep(0.2)
        return await s.handoff("what are the little dots?", "d1")

    found = asyncio.run(go())
    assert found["answer"] == "Those are sperm heads."
    assert len(agent.fast_model.prompts) == 1 and not agent.calls  # one decision, no look
    wait = [r for r in agent.logged if r.get("kind") == "voice-handoff-wait"][-1]
    assert wait["reused"] == "answer"


def test_a_hand_off_while_hands_is_deciding_starts_the_look_at_once(tmp_path):
    """The look starts beside the fast pass, as for a typed question, not after it; hands uses
    that same look, so there is one."""
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {"intent": "question", "needs_look": True},
                                                     {"answer": "That is a stoma.", "targets": []})
    _slow_fast_pass(agent, 0.5)

    async def go():
        s.heard("what is the oval in the middle")
        await asyncio.sleep(0.15)  # joined; hands is asking the fast pass
        handoff = asyncio.ensure_future(s.handoff("what is the oval in the middle", "d1"))
        await asyncio.sleep(0.1)
        assert len(agent.calls) == 1  # the look started before the fast pass answered
        return await handoff

    found = asyncio.run(go())
    assert "stoma" in found["answer"] and len(agent.calls) == 1 and len(agent.fast_model.prompts) == 1
    wait = [r for r in agent.logged if r.get("kind") == "voice-handoff-wait"][-1]
    assert wait["earlyLook"] is True and wait["reused"] == "look"


def test_a_look_started_early_is_dropped_when_the_notes_answer(tmp_path):
    s, brain, hub, agent, day, shown, voice = spoken(
        tmp_path, {"intent": "question", "needs_look": False, "answer": "Those are sperm heads.", "actions": []},
        {"answer": "slow look", "targets": []})
    _slow_fast_pass(agent, 0.3)
    looks = []
    start = brain.start_look

    def track(*a, **k):
        look = start(*a, **k)
        looks.append(look)
        return look

    brain.start_look = track

    async def go():
        s.heard("what are the little dots?")
        await asyncio.sleep(0.15)
        found = await s.handoff("what are the little dots?", "d1")
        await asyncio.sleep(0)
        return found

    found = asyncio.run(go())
    assert found["answer"] == "Those are sperm heads."
    assert len(looks) == 1 and looks[0].used is False and (looks[0].task.cancelled() or looks[0].task.done())


def test_the_voice_task_of_everything_since_its_last_hand_off_reuses_hands_for_its_end(tmp_path):
    """The voice's task is everything said since it last handed over; the request is at its end."""
    s, brain, hub, agent, day, shown, voice = spoken(
        tmp_path, {"intent": "request", "needs_look": False, "answer": "Selecting it.",
                   "actions": [{"tool": "select", "args": {"object": "o1"}}]})
    from tests.test_brain import obj

    obj(hub, "stoma")
    from orchestrator.ticket import current

    final = {"answer": "Selecting it.", "actions": [], "observation_scope": "none", "input_refs": []}
    agent.fast_model.rewrite = final
    entries, finished = [], []
    brain.registry.finished.append(lambda run, ctx: finished.append((run, ctx)))
    ask = agent.fast_model.ask

    async def observe(images, prompt, **kw):
        admission = current().admission
        attempt = admission.attempts[-1]
        reply = await ask(images, prompt, **kw)
        entries.append((admission, attempt, tuple(images), prompt, reply.data))
        return reply

    agent.fast_model.ask = observe

    async def go():
        s.heard("select that stoma")
        await asyncio.sleep(0.2)
        return await s.handoff("Wow, there are so many of them, I am on forty x now. Select that stoma", "d1")

    found = asyncio.run(go())
    assert found["answer"] == "Selecting it." and hub.scene.selected == "o1"
    assert len(entries) == len(agent.fast_model.prompts) == 2 and not agent.calls
    admission = entries[0][0]
    assert all(entry[0] is admission and not entry[2] for entry in entries)
    assert [entry[1].phase for entry in entries] == ["hands", "query"]
    assert [attempt.phase for attempt in admission.attempts] == ["hands", "query"]
    assert admission.used == 2 and all(attempt.status == "done" for attempt in admission.attempts)
    assert {entry[1].attempt_id for entry in entries} == {attempt.attempt_id for attempt in admission.attempts}
    assert len(finished) == 1 and finished[0][0].tool == "select" and finished[0][0].ok is True
    run, ctx = finished[0]
    assert ctx.admission is admission and run.occurrence.request_id == admission.request_id
    assert len(found["actions"]) == 1 and found["actions"][0]["actionId"] == run.occurrence.action_id
    assert "selected the stoma" in entries[-1][3]
    assert entries[-1][4] == final and found["answer"] == final["answer"]


def test_a_hand_off_looks_at_and_outlines_on_the_newest_frame(tmp_path):
    """2026-10-06: the frame from when the user began to speak was 10-17 s old by the hand-off,
    and swimming cells had moved; the look and its outlines are on what is in view now."""
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {"intent": "question", "needs_look": True},
                                                     {"answer": "That is a stoma.", "targets": []})
    asked = []
    snapshot = agent.snapshot
    agent.snapshot = lambda frame_id=None: (asked.append(frame_id), snapshot(frame_id))[1]

    async def go():
        s.heard("what is this oval?")
        await asyncio.sleep(0.2)
        return await s.handoff("what is this oval?", "d1")

    asyncio.run(go())
    assert asked and all(f is None for f in asked)  # None: the newest frame


def test_a_hand_off_just_after_the_app_looked_again_waits_for_that_look(tmp_path, monkeypatch):
    """Speed bench, live-152750: the turn started after a miss was not recorded, so a hand-off of
    the same words just after looked and outlined a second time."""
    s, brain, hub, agent, day, shown, voice = spoken(
        tmp_path, {"intent": "request", "needs_look": False, "answer": "Outlining it.",
                   "actions": [{"tool": "highlight", "args": {"things": ["s9"]}}]},  # s9 is not seen: it misses
        {"answer": "That is the cortex.", "targets": []})

    async def go():
        s.heard("highlight the blue part")
        await asyncio.sleep(0.3)  # hands missed; after POINT_AFTER_S the app looks again itself
        found = await s.handoff("highlight the blue part", "d1")
        await asyncio.sleep(0.05)
        return found

    asyncio.run(go())
    assert len(agent.calls) == 1  # one look, not two


def test_steps_that_need_no_look_are_done_at_once_and_the_look_does_the_rest(tmp_path, wall_clock):
    """"Close the magnifier and highlight the stomata", the stomata not in the notes: the close
    does not wait for the vision call."""
    s, brain, hub, agent, day, shown, voice = spoken(
        tmp_path, {"intent": "request", "needs_look": True, "answer": "Closing it and finding the stomata.",
                   "actions": [{"tool": "close_magnifier", "args": {}}]},
        {"answer": "Those are stomata.", "targets": [], "actions": [{"tool": "close_magnifier", "args": {}}]})
    closed = []
    from orchestrator.brain.registry import Tool, schema

    async def close(ctx, args):
        closed.append(asyncio.get_running_loop().time())
        return "closed the magnifier"

    brain.registry.add(Tool("close_magnifier", "Close the magnifier.", schema({}), close))
    slow = agent.look

    async def slow_look(*a, **k):
        await asyncio.sleep(0.5)
        return await slow(*a, **k)

    agent.look = slow_look

    async def go():
        t0 = asyncio.get_running_loop().time()
        s.heard("close the magnifier and highlight the stomata")
        await asyncio.sleep(0.2)
        assert closed
        wall_clock.within(closed[0] - t0, 0.3, "closing")  # closed before the look answered
        found = await s.handoff("close the magnifier and highlight the stomata", "d1")
        return found

    asyncio.run(go())
    assert len(closed) == 1 and len(agent.calls) == 1  # closed once; one look for the rest


def pointing(tmp_path, fast_reply):
    """A spoken setup with the app's board, a stoma and a pore, and the turns' saved pointing recorded."""
    from orchestrator.board import Board
    from tests.test_brain import obj

    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, fast_reply)
    brain.board = Board(hub)
    stoma, pore = obj(hub, "stoma"), obj(hub, "pore", 300)
    turns = []
    original = brain.turn

    async def turn(text, channel="chat", **kw):
        turns.append(kw.get("when_asked"))
        return await original(text, channel, **kw)

    brain.turn = turn
    agent.replies.extend({"answer": "A stoma.", "targets": []} for _ in range(3))  # a look per request heard
    return s, brain, hub, stoma, pore, turns


def test_a_hand_off_is_answered_as_of_when_the_words_were_heard(tmp_path):
    """The partner's Amendment 1, C2: the hand-off can come seconds after the words; "that" means what was pointed
    at when they were heard, so the turn is given what was saved then."""
    s, brain, hub, stoma, pore, turns = pointing(tmp_path, {"intent": "question", "needs_look": True})

    async def go():
        hub.scene.select(stoma.id)
        s.heard("what is that")
        await asyncio.sleep(0.3)
        hub.scene.select(pore.id)  # before the hand-off comes
        await s.handoff("what is that", "d1")

    asyncio.run(go())
    assert turns and turns[-1].selected == stoma.id


def test_a_hand_off_in_other_words_still_gets_what_was_pointed_at_when_heard(tmp_path):
    """The executor's P6: the voice's task is everything said since its last hand-off ("microscopic view Magnify
    that"), not the request heard ("Magnify that"); 68 of 132 live hand-offs differed so."""
    s, brain, hub, stoma, pore, turns = pointing(tmp_path, {"intent": "question", "needs_look": True})

    async def go():
        s.heard("microscopic view")
        await asyncio.sleep(0.2)
        hub.scene.select(stoma.id)
        s.heard("Magnify that")
        await asyncio.sleep(0.2)
        hub.scene.select(pore.id)
        await s.handoff("microscopic view Magnify that", "d1")

    asyncio.run(go())
    assert turns and turns[-1] is not None and turns[-1].selected == stoma.id


def test_hands_act_on_what_was_pointed_at_when_the_last_piece_was_heard(tmp_path):
    """The executor's M8 and M10: hands' steps get the pointing saved when the words' last piece was heard, not
    when the pieces were joined (a selection made while they are joined is after the request)."""
    from orchestrator.brain.registry import Tool, schema
    from orchestrator.brain.toolbox.common import resolve_object

    s, brain, hub, stoma, pore, turns = pointing(
        tmp_path, {"intent": "request", "needs_look": False, "answer": "Zooming in.",
                   "actions": [{"tool": "probe", "args": {"object": "that"}}]})
    seen = []

    async def probe(ctx, args):
        seen.append(resolve_object(hub, args.get("object"), ctx).id)
        return "ok"

    brain.registry.add(Tool("probe", "", schema({"object": {"type": "string"}}), probe))
    speech.JOIN_PIECES_S = 0.2  # long enough to select something while the pieces are joined

    async def go():
        hub.scene.select(stoma.id)
        s.heard("zoom on that")
        await asyncio.sleep(0.05)
        hub.scene.select(pore.id)  # after the words, while they are being joined
        await asyncio.sleep(0.4)

    asyncio.run(go())
    assert seen == [stoma.id]


def test_what_was_pointed_at_is_kept_for_the_last_requests_heard_again_or_not(tmp_path):
    """The executor's P11: a request heard again is the newest, so it is not the first pruned."""
    s, brain, hub, stoma, pore, turns = pointing(tmp_path, {"intent": "statement", "needs_look": False, "answer": "OK.",
                                                            "actions": []})

    async def go():
        for words in ["what is that", *[f"remark {i}" for i in range(7)], "what is that", "hmm"]:
            s.heard(words)
            await asyncio.sleep(0.08)

    asyncio.run(go())
    kept = [r.heard.words for r in s.requests]
    assert len(kept) == speech.HEARD_KEPT and kept[-2:] == ["what is that", "hmm"]  # heard again: the newest


def probe_tool(hub, seen, fails=False):
    """A tool that records what "that" means to it (or fails, saying so)."""
    from orchestrator.brain.registry import Tool, ToolFailed, schema
    from orchestrator.brain.toolbox.common import resolve_object

    async def probe(ctx, args):
        seen.append(resolve_object(hub, args.get("object"), ctx).id)
        if fails:
            raise ToolFailed("it did not work", next=None)
        return "ok"

    return Tool("boom" if fails else "probe", "", schema({"object": {"type": "string"}}), probe)


def test_a_hand_off_in_words_that_fit_nothing_heard_gets_the_newest_request(tmp_path):
    """The voice's own words for the task ("zoom in on it please") need not share a word with what was heard."""
    s, brain, hub, stoma, pore, turns = pointing(tmp_path, {"intent": "question", "needs_look": True})

    async def go():
        hub.scene.select(stoma.id)
        s.heard("Magnify that")
        await asyncio.sleep(0.2)
        hub.scene.select(pore.id)
        await s.handoff("zoom in on it please", "d1")

    asyncio.run(go())
    assert turns[-1] is not None and turns[-1].selected == stoma.id


def test_a_remark_heard_after_the_request_is_never_taken_for_it(tmp_path):
    """A "hmm" heard after "Magnify that" and before the hand-off: the hand-off's words end with the request, so its
    pointing is the request's, not the remark's."""
    s, brain, hub, stoma, pore, turns = pointing(tmp_path, {"intent": "question", "needs_look": True})
    ask = brain.agent.fast_model.ask

    async def judged(images, prompt, **kw):  # the model's judgment: "hmm" is a filler
        if 'User: "hmm"' in prompt:
            return SimpleNamespace(data={"intent": "filler", "needs_look": False, "answer": "", "actions": []}, latency_ms=5.0)
        return await ask(images, prompt, **kw)

    brain.agent.fast_model.ask = judged

    async def go():
        hub.scene.select(stoma.id)
        s.heard("Magnify that")
        await asyncio.sleep(0.2)
        hub.scene.select(pore.id)
        s.heard("hmm")
        await asyncio.sleep(0.2)
        # Everything said since the last hand-off, as the voice's task carries it: only its end is the request.
        await s.handoff("okay so this is the microscopic view of the leaf and now Magnify that", "d1")

    asyncio.run(go())
    assert turns[-1] is not None and turns[-1].selected == stoma.id


def test_the_hand_off_keeps_what_hands_saved_for_the_same_request(tmp_path):
    """Hands and the hand-off answer one request: the turn is given hands' own pointing, whatever words came."""
    from orchestrator.ticket import current

    # External model points at the referenced object while answering this question.
    s, brain, hub, stoma, pore, turns = pointing(tmp_path,
        {"intent": "question", "needs_look": False,
         "actions": [{"tool": "select", "args": {"object": "that"}}]})
    agent = brain.agent
    final = {"answer": "The selected outline is a stoma.", "actions": [],
             "observation_scope": "none", "input_refs": []}
    agent.fast_model.rewrite = final
    finished, entries = [], []
    brain.registry.finished.append(lambda run, ctx: finished.append((run, ctx)))
    ask = agent.fast_model.ask

    async def observe(images, prompt, **kw):
        admission = current().admission
        attempt = admission.attempts[-1]
        reply = await ask(images, prompt, **kw)
        entries.append((admission, attempt, tuple(images), prompt, reply.data))
        return reply

    agent.fast_model.ask = observe

    async def go():
        hub.scene.select(stoma.id)
        s.heard("what is that")
        await asyncio.sleep(0.2)
        # Read the actual producer's cached result; no Future or ToolContext is manufactured.
        handled = await s.requests[-1].work["hands"]
        assert handled is not None and handled["ticket"] is not None
        original = handled["ctx"]
        hands_saw = original.when_asked
        assert hands_saw.selected == stoma.id and hub.scene.selected == stoma.id
        assert len(finished) == 1 and finished[0][1] is original
        hub.scene.select(pore.id)
        found = await s.handoff("what is that", "d1")
        return found, original, hands_saw

    found, original, hands_saw = asyncio.run(go())
    assert turns[-1] is hands_saw
    assert hub.scene.selected == pore.id  # handoff did not replay the original select
    assert len(finished) == 1 and finished[0][0].tool == "select" and finished[0][0].ok is True
    run, ctx = finished[0]
    assert ctx is original and ctx.when_asked is hands_saw
    admission = ctx.admission
    assert all(entry[0] is admission and not entry[2] for entry in entries)
    assert [entry[1].phase for entry in entries] == ["hands", "query"]
    assert [attempt.phase for attempt in admission.attempts] == ["hands", "query"]
    assert admission.used == 2 and all(attempt.status == "done" for attempt in admission.attempts)
    assert len({attempt.attempt_id for attempt in admission.attempts}) == 2
    assert not agent.calls and len(found["actions"]) == 1
    assert found["actions"][0]["actionId"] == run.occurrence.action_id
    assert run.occurrence.request_id == admission.request_id
    assert entries[-1][4] == final and found["answer"] == final["answer"]


def test_a_request_the_voice_answered_itself_is_done_as_of_when_it_was_heard(tmp_path):
    """The executor's M9 (`_do_unhanded`): the voice said it would and never handed it over; the app does it, with
    what was pointed at when the words were heard."""
    s, brain, hub, stoma, pore, turns = pointing(tmp_path, {"intent": "request", "needs_look": True})

    async def go():
        hub.scene.select(stoma.id)
        s.heard("zoom on that")
        await asyncio.sleep(0.01)
        hub.scene.select(pore.id)
        await asyncio.sleep(0.5)

    asyncio.run(go())
    assert turns and turns[-1] is not None and turns[-1].selected == stoma.id


def test_a_step_that_failed_and_is_looked_at_again_is_as_of_when_it_was_heard(tmp_path):
    """The executor's M9 (`_act_or_point`): hands' step failed and no hand-off came, so the app looks again on
    its own, with what was pointed at when the words were heard."""
    s, brain, hub, stoma, pore, turns = pointing(
        tmp_path, {"intent": "request", "needs_look": False, "answer": "Zooming in.",
                   "actions": [{"tool": "boom", "args": {"object": "that"}}]})
    seen = []
    brain.registry.add(probe_tool(hub, seen, fails=True))

    async def go():
        hub.scene.select(stoma.id)
        s.heard("zoom on that")
        await asyncio.sleep(0.01)
        hub.scene.select(pore.id)
        await asyncio.sleep(0.5)

    asyncio.run(go())
    assert seen[0] == stoma.id and turns and turns[-1] is not None and turns[-1].selected == stoma.id


def test_pointing_along_with_the_voices_answer_is_as_of_when_the_words_were_heard(tmp_path):
    """The voice answered a question from its notes; the app points along, at what "that" meant then."""
    s, brain, hub, stoma, pore, turns = pointing(tmp_path, {})
    brain.sight = SimpleNamespace(view=SimpleNamespace(current=lambda info: True, things=[], text=lambda info: ""))
    replies = [{"intent": "question", "needs_look": False, "answer": "A stoma.", "actions": []},
               {"intent": "question", "needs_look": False, "answer": "", "actions": [{"tool": "select", "args": {"object": "that"}}]}]

    async def ask(images, prompt, max_output_tokens=0):
        return SimpleNamespace(data=replies.pop(0) if replies else {"intent": "filler", "actions": []}, latency_ms=5.0)

    brain.agent.fast_model.ask = ask

    async def go():
        hub.scene.select(stoma.id)
        s.heard("what is that")
        await asyncio.sleep(0.01)
        hub.scene.select(pore.id)
        await asyncio.sleep(0.5)

    asyncio.run(go())
    assert not replies and hub.scene.selected == stoma.id  # pointed along at the stoma, though the pore was selected since


def test_the_fast_pass_and_the_early_look_are_told_the_selection_then_and_now(tmp_path):
    """The executor's P10 for spoken words: what hands and its early look are told is the selection when the words
    were heard, and what is selected now when that changed."""
    s, brain, hub, stoma, pore, turns = pointing(tmp_path, {"intent": "question", "needs_look": True})
    speech.JOIN_PIECES_S = 0.2

    async def go():
        hub.scene.select(stoma.id)
        s.heard("what is that")
        await asyncio.sleep(0.05)
        hub.scene.select(pore.id)
        await asyncio.sleep(0.4)

    asyncio.run(go())
    told = f" (selected: {stoma.id} when the user asked, {pore.id} now)"
    assert told in brain.agent.fast_model.prompts[0] and brain.agent.calls[0]["tools"][2] == told


def test_hands_steps_done_before_a_look_act_on_what_was_pointed_at_when_heard(tmp_path):
    """The executor's survivor M8b: the fast pass gives steps and needs a look ("close the magnifier and highlight
    that"); the steps done at once get the pointing saved when the words were heard."""
    s, brain, hub, stoma, pore, turns = pointing(
        tmp_path, {"intent": "request", "needs_look": True, "answer": "Closing it.",
                   "actions": [{"tool": "probe", "args": {"object": "that"}}]})
    seen = []
    brain.registry.add(probe_tool(hub, seen))
    speech.JOIN_PIECES_S = 0.2

    async def go():
        hub.scene.select(stoma.id)
        s.heard("close it and highlight that")
        await asyncio.sleep(0.05)
        hub.scene.select(pore.id)  # after the words, while they are joined
        await asyncio.sleep(0.4)

    asyncio.run(go())
    assert seen and seen[0] == stoma.id


def _chat_lines(day):
    return [(l["role"], l["text"]) for l in day.active.lines if l["type"] == "chat"]


def test_interleaved_pieces_of_a_reply_and_a_request_are_one_line_each_in_the_chat_and_the_journal(tmp_path):
    """Live 2026-10-07 16:19: the voice's reply and the user's words arrived interleaved and were filed
    as separate one- and two-word lines ("Yes,", "the tools I", "have can't run ...")."""
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {"intent": "statement", "needs_look": False})

    async def go():
        s.said(" Yes,")
        s.heard("the researcher will")
        s.heard("be able to do")
        s.said(" the tools I")
        s.heard("this")
        s.said(" have can't run a motion model.")
        s.reply_done()  # the voice went quiet
        await asyncio.sleep(0.2)

    asyncio.run(go())
    # each where it began: the reply's first words came before the user's
    assert _chat_lines(day) == [("assistant", "Yes, the tools I have can't run a motion model."),
                                ("user", "the researcher will be able to do this")]
    # the studios saw each side grow in place (the same lineId), and the finished line once
    ids = {}
    for line in shown:
        ids.setdefault(line["lineId"], []).append(line)
    assert len(ids) == 2 and all(lines[-1].get("open") is not True for lines in ids.values())
    assert [t.text for t in day.active.turns if t.role == "assistant"] == ["Yes, the tools I have can't run a motion model."]
    # a restart replays the journal: the same two lines
    again = DayStore(root=tmp_path)
    assert _chat_lines(again) == _chat_lines(day)


def test_a_reply_after_a_pause_is_a_new_line_and_so_is_the_next_request(tmp_path, monkeypatch):
    monkeypatch.setattr(speech, "SPEECH_LINE_GAP_S", 0.05)
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {"intent": "statement", "needs_look": False})

    async def go():
        s.said("Sure.")
        await asyncio.sleep(0.2)  # no piece for longer than the gap: filed on its own
        s.said("Here it is.")
        s.reply_done()
        s.heard("thanks")
        await asyncio.sleep(0.2)  # the request settles: its line closes
        s.heard("one more")
        await asyncio.sleep(0.2)

    asyncio.run(go())
    assert _chat_lines(day) == [("assistant", "Sure."), ("assistant", "Here it is."), ("user", "thanks"), ("user", "one more")]


def test_a_spoken_request_keeps_its_place_before_what_was_shown_while_it_was_answered(tmp_path):
    """A spoken line is filed when it begins: the app's answer and tool lines shown while the voice was still
    replying come after the request, after a reload too, and the reply after them."""
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {"intent": "statement", "needs_look": False})

    async def go():
        s.heard("highlight the stomata")
        s.said("Sure,")
        s.dialogue.show({"type": "chat", "role": "assistant", "text": "Outlined two stomata."})
        s.said(" they're outlined.")
        s.reply_done()
        await asyncio.sleep(0.05)

    asyncio.run(go())
    expected = [("user", "highlight the stomata"), ("assistant", "Sure, they're outlined."),
                ("assistant", "Outlined two stomata.")]
    assert _chat_lines(day) == expected
    again = DayStore(root=tmp_path)
    assert _chat_lines(again) == expected
    # each spoken line is filed twice (begun, whole) and kept once
    assert sum(1 for l in again.active.lines if l.get("lineId")) == 2


def test_two_requests_with_no_reply_between_are_two_lines_each_before_its_own_answer(tmp_path):
    """The review of #200 (B1): the user's line closed only when the voice's turn ended, so two requests a few
    seconds apart with no reply between were one line, before the first one's answer. A request's line closes
    when the request settles."""
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {"intent": "statement", "needs_look": False})

    async def go():
        s.heard("show the cells")
        await asyncio.sleep(0.2)  # settled: one request
        s.dialogue.show({"type": "chat", "role": "assistant", "text": "Here are the cells."})
        s.heard("now count them")
        await asyncio.sleep(0.2)
        s.dialogue.show({"type": "chat", "role": "assistant", "text": "Twelve."})

    asyncio.run(go())
    expected = [("user", "show the cells"), ("assistant", "Here are the cells."), ("user", "now count them"),
                ("assistant", "Twelve.")]
    assert _chat_lines(day) == expected
    assert _chat_lines(DayStore(root=tmp_path)) == expected
    assert [t.text for t in day.active.turns if t.role == "user"] == ["show the cells", "now count them"]


def test_the_rest_of_words_judged_unfinished_is_the_same_line(tmp_path):
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {})
    replies = {"Can you close the": {"intent": "unfinished", "needs_look": False, "answer": "", "actions": []},
               "Can you close the magnifier": {"intent": "statement", "needs_look": False, "answer": "", "actions": []}}

    async def ask(images, prompt, max_output_tokens=0):
        words = prompt.split('User: "', 1)[1].split('"', 1)[0]
        return SimpleNamespace(data=replies[words], latency_ms=5.0)

    agent.fast_model.ask = ask

    async def go():
        s.heard("Can you close the")
        await asyncio.sleep(0.3)  # settled and judged unfinished: its line closed, waiting for the rest
        s.heard("magnifier")
        await asyncio.sleep(0.3)

    asyncio.run(go())
    assert _chat_lines(day) == [("user", "Can you close the magnifier")]
    assert _chat_lines(DayStore(root=tmp_path)) == [("user", "Can you close the magnifier")]


def test_a_line_still_growing_is_in_the_journal_nearly_whole(tmp_path, monkeypatch):
    """The review of #200 (SF3): after a crash only a line's first piece was in the journal."""
    monkeypatch.setattr(speech, "REFILE_S", 0.05)
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {"intent": "statement", "needs_look": False})

    async def go():
        s.said("The cells")
        await asyncio.sleep(0.1)
        s.said(" are dividing")
        await asyncio.sleep(0.1)
        s.said(" fast.")
        # no close: the app stops here

    asyncio.run(go())
    assert _chat_lines(DayStore(root=tmp_path)) == [("assistant", "The cells are dividing fast.")]


def test_a_spoken_line_stays_in_the_conversation_it_began_in(tmp_path):
    """The review of #200 (SF4): a reply begun before any request was filed could be filed again, whole, in
    another conversation. Its conversation is fixed by its first piece."""
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {"intent": "statement", "needs_look": False})
    first = day.active_id

    async def go():
        s.said("Looking at")
        day.open_thread("blood smear")  # the user starts another conversation meanwhile
        s.said(" the leaf.")
        s.reply_done()
        await asyncio.sleep(0.05)

    asyncio.run(go())
    again = DayStore(root=tmp_path)
    lines = {tid: [(l["role"], l["text"]) for l in t.lines if l["type"] == "chat"] for tid, t in again.threads.items()}
    assert lines[first] == [("assistant", "Looking at the leaf.")]
    assert all(not v for tid, v in lines.items() if tid != first)


def test_failed_fast_provider_recovers_spoken_action_without_delegation(tmp_path):
    """A provider error must not strand 'Can you zoom' after voice promised it."""
    from orchestrator.brain.registry import Tool, schema
    from orchestrator.ticket import current

    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {},
        {'answer': 'Opening the zoom.', 'targets': [],
         'actions': [{'tool': 'zoom', 'args': {}}]})
    attempts, zooms = [], []

    async def unavailable(*args, **kwargs):
        attempts.append(current().admission)
        raise RuntimeError('provider unavailable')

    async def zoom(ctx, args):
        zooms.append(ctx.admission)
        return 'zoom opened'

    agent.fast_model.ask = unavailable
    brain.registry.add(Tool('zoom', 'Open zoom', schema({}), zoom))

    async def go():
        s.heard('Can you zoom')
        await asyncio.sleep(.5)
        await s.handoff('Can you zoom', 'late-delegation')

    asyncio.run(go())
    assert len(zooms) == 1
    assert attempts and zooms[0] is attempts[0]
    assert zooms[0].used <= 3


def test_failed_recovery_is_persisted_and_explained_by_original_voice(tmp_path):
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {})
    delivery = []

    async def unavailable(*args, **kwargs):
        raise RuntimeError('provider unavailable')

    async def inject(text, respond=False, **_log):
        delivery.append((text, respond))

    agent.fast_model.ask = unavailable
    brain.turn = unavailable
    voice.inject = inject

    async def go():
        s.heard('Can you zoom')
        await asyncio.sleep(.4)

    asyncio.run(go())
    notices = [line for line in shown if line['type'] == 'app-notice']
    assert [line['event'] for line in notices] == ['planning-recovery', 'request-failed']
    assert notices[0]['origin']['requestId'] == notices[1]['origin']['requestId']
    assert notices[1]['facts']['confirmedResults'] == []
    assert notices[1]['facts']['errorType'] == 'RuntimeError'
    assert [respond for _, respond in delivery] == [False, True]
    assert 'correct any earlier claim' in delivery[-1][0]
    again = DayStore(root=tmp_path)
    assert [line['event'] for line in again.active.lines if line['type'] == 'app-notice'] == [
        'planning-recovery', 'request-failed']


# --- One record per request: delegations are matched by the voice's own clock (live 2026-10-10) ----------


def judging(agent, replies):
    """The fast pass's judgment per request (the model's job), by the words it is asked about."""
    async def ask(images, prompt, **kw):
        words = prompt.split('User: "', 1)[1].split('"', 1)[0] if 'User: "' in prompt else ""
        reply = replies.get(words, {"intent": "question", "needs_look": False, "answer": "Noted.", "actions": []})
        return SimpleNamespace(data=reply, latency_ms=5.0)

    agent.fast_model.ask = ask


def asked_brain(brain, slow=()):
    """The words each brain turn was given, in order (`slow`: words whose answer takes half a second, as live)."""
    asked = []
    turn = brain.turn

    async def recording(text, *a, **k):
        asked.append(text)
        if text in slow:
            await asyncio.sleep(0.5)
        return await turn(text, *a, **k)

    brain.turn = recording
    return asked


def test_a_delegation_is_the_request_said_before_it_never_earlier_words_already_done(tmp_path):
    """Live 20:33: "And also stop highlighting the one" was done by hands; 40 s later "Can you try to magnify the
    eye right now" was handed over with both joined, and the look planned a clear and a zoom on "target 1"."""
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {})
    from tests.test_brain import obj

    obj(hub, "eye")
    judging(agent, {"And also stop highlighting the one": {"intent": "request", "needs_look": False, "answer": "Clearing it.",
                                                            "actions": [{"tool": "clear", "args": {"what": "outlines"}}]}})
    asked = asked_brain(brain)

    async def go():
        s.heard("And also stop highlighting the one", span=(1000, 2400))
        await asyncio.sleep(0.3)
        s.heard("Can you try to magnify the eye right now", span=(41000, 43000))
        return await s.handoff("And also stop highlighting the one Can you try to magnify the eye right now", "d1",
                               offset_ms=43200)

    asyncio.run(go())
    assert asked == ["Can you try to magnify the eye right now"]
    wait = [r for r in agent.logged if r.get("kind") == "voice-handoff-wait"][-1]
    assert wait["matchedBy"] == "offset" and wait["spanMs"] == [41000, 43000]


def test_two_unrelated_requests_in_progress_stay_separate(tmp_path):
    """A pending photo lookup and a new "zoom in" are two requests: the delegation for one never takes in the other."""
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {})
    judging(agent, {})
    asked = asked_brain(brain, slow=("pull up an image online of an eye",))

    async def go():
        s.heard("pull up an image online of an eye", span=(1000, 2500))
        await asyncio.sleep(0.2)
        first = asyncio.ensure_future(s.handoff("pull up an image online of an eye", "d1", offset_ms=2600))
        await asyncio.sleep(0.2)
        s.heard("zoom in", span=(5000, 5600))
        second = await s.handoff("zoom in", "d2", offset_ms=5700)
        await first
        return second

    asyncio.run(go())
    assert sorted(asked) == ["pull up an image online of an eye", "zoom in"]
    assert [r.delegations for r in s.requests] == [["d1"], ["d2"]]


def test_a_delegation_before_any_words_it_can_belong_to_starts_nothing_new(tmp_path, monkeypatch):
    """Missing or inconsistent timing never selects an older request: the voice is told nothing new was heard."""
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {})
    asked = asked_brain(brain)
    monkeypatch.setattr(speech, "WAIT_FOR_WORDS_S", 0.3)

    async def go():
        s.heard("zoom in", span=(5000, 5600))
        await asyncio.sleep(0.2)
        first = await s.handoff("zoom in", "d1", offset_ms=5700)
        # Much later, a delegation whose own words never reached the app: never "zoom in" again.
        later = await s.handoff("", "d2", offset_ms=30000)
        return first, later

    first, later = asyncio.run(go())
    assert asked == ["zoom in"] and later.get("unmatched") is True
    from orchestrator.voice.openai_live import findings_text

    assert "no new request" in findings_text(later)


def test_the_voice_handing_the_same_request_over_twice_gets_the_same_answer(tmp_path, monkeypatch):
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {})
    asked = asked_brain(brain)
    monkeypatch.setattr(speech, "WAIT_FOR_WORDS_S", 0.3)

    async def go():
        s.heard("what is that", span=(1000, 1800))
        await asyncio.sleep(0.2)
        one = await s.handoff("what is that", "d1", offset_ms=1900)
        two = await s.handoff("what is that", "d2", offset_ms=2100)
        return one, two

    one, two = asyncio.run(go())
    assert asked == ["what is that"] and one["answer"] == two["answer"]


def test_a_repeated_request_is_a_new_request(tmp_path):
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {})
    judging(agent, {})
    asked = asked_brain(brain)

    async def go():
        s.heard("zoom in", span=(1000, 1500))
        await asyncio.sleep(0.2)
        await s.handoff("zoom in", "d1", offset_ms=1600)
        s.heard("zoom in", span=(9000, 9500))
        await s.handoff("zoom in", "d2", offset_ms=9600)

    asyncio.run(go())
    assert asked == ["zoom in", "zoom in"] and len(s.requests) == 2


def test_a_correction_calls_off_what_the_request_before_has_not_finished_and_keeps_what_it_made(tmp_path):
    """Live 20:31: "Can you take a snapshot of it" ... "No, a snapshot of the zoomed in eye". The model judges the
    correction; the earlier request's unfinished work is called off, its result is not spoken as the answer now."""
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {})
    judging(agent, {"No, a snapshot of the zoomed in eye": {"intent": "request", "needs_look": False, "corrects": True,
                                                            "answer": "Taking it of the zoomed eye.", "actions": []}})
    asked = asked_brain(brain, slow=("Can you take a snapshot of it",))

    async def go():
        s.heard("Can you take a snapshot of it", span=(1000, 2500))
        await asyncio.sleep(0.2)
        first = asyncio.ensure_future(s.handoff("Can you take a snapshot of it", "d1", offset_ms=2600))
        await asyncio.sleep(0.05)
        s.heard("No, a snapshot of the zoomed in eye", span=(4000, 5500))
        await asyncio.sleep(0.2)
        second = await s.handoff("No, a snapshot of the zoomed in eye", "d2", offset_ms=5600)
        return await first, second

    first, second = asyncio.run(go())
    earlier, correction = s.requests
    assert correction.relation == "correction" and earlier.superseded_by is correction
    assert earlier.cancelled and all(t.cancelled for t in earlier.members)  # what it had not finished is called off
    assert first.get("superseded") is True and not second.get("superseded")
    assert asked[-1] == "No, a snapshot of the zoomed in eye"
    assert any(r.get("kind") == "voice-correction" for r in agent.logged)


def test_the_rest_of_unfinished_words_is_one_request_that_takes_the_delegation(tmp_path):
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {})
    judging(agent, {"Can you close the": {"intent": "unfinished", "needs_look": False, "answer": "", "actions": []}})
    asked = asked_brain(brain)

    async def go():
        s.heard("Can you close the", span=(1000, 1800))
        await asyncio.sleep(0.2)
        s.heard("magnifier", span=(2600, 3000))
        return await s.handoff("Can you close the magnifier", "d1", offset_ms=3100)

    asyncio.run(go())
    assert asked == ["Can you close the magnifier"]
    assert s.requests[0].superseded_by is s.requests[1]
    assert not [line for line in shown if line['type'] == 'chat' and line.get('role') == 'assistant']


def test_a_spoken_requests_steps_are_in_the_chat_as_a_typed_ones_are_each_once(tmp_path):
    """Voice/chat parity (live 2026-10-11): by voice, the chat showed no step working, no photo card and no "Photo
    displayed", while typed the same request did. A spoken request's steps are filed into the chat as the typed
    path files them (commands.ask: chat-action rows), once each, whether hands did them or the hand-off did, so
    the studio's step rows, "Working..." and late updates in place are the same for both."""
    s, brain, hub, agent, day, shown, voice = spoken(
        tmp_path, {"intent": "request", "needs_look": False, "answer": "Selecting it.",
                   "actions": [{"tool": "select", "args": {"object": "o1"}}]})
    from tests.test_brain import obj

    obj(hub, "stoma")
    agent.fast_model.rewrite = {"answer": "Selecting it.", "actions": [], "observation_scope": "none", "input_refs": []}

    async def go():
        s.heard("select that stoma")
        await asyncio.sleep(0.01)
        found = await s.handoff("select that stoma", "deleg-1")
        await asyncio.sleep(0.05)
        return found

    found = asyncio.run(go())
    rows = [line for line in shown if line.get("type") == "chat-action"]
    assert [row["tool"] for row in rows] == ["select"]  # hands did it; the hand-off reused it: one row
    row = rows[0]
    assert row["standalone"] is True and row["actionId"] == found["actions"][0]["actionId"]
    assert row["ok"] is True and row["thread"] == "t1"


class LeanVision:
    """The pointing reflex's model, scripted: any prompt gets `reply` (option REFLEX "lean")."""

    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    async def ask(self, images, prompt, max_output_tokens=0):
        self.prompts.append(prompt)
        return SimpleNamespace(data=self.reply, latency_ms=5.0)


def test_the_lean_reflex_points_at_listed_ids_only_from_the_words_the_eyes_and_the_board(tmp_path):
    # The 2026-10-11 demo: "what's the little dot" was answered and never pointed at. The reflex sees only
    # the words, the eyes' things and the board, and only ids those list are highlighted (never an invented s9).
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {"intent": "question", "needs_look": False})
    hub.settings.reflex = "lean"
    brain.sight = SimpleNamespace(view=SimpleNamespace(current=lambda info: True, things=[],
                                                       text=lambda info: "Things in view: s1 cell nucleus (centre); s2 cell wall (left)."))
    agent.fast_model = LeanVision({"about": ["s1", "s9"], "intent": "question"})
    from orchestrator.ticket import issue, using

    async def decide():
        ticket = issue(brain.hub, "hands", asked=brain.conversation.here(), channel="voice")  # as point() runs it
        with using(ticket):
            try:
                return await brain.reflex("what's the little dot")
            finally:
                ticket.seal()

    decision = asyncio.run(decide())
    assert [tuple(a) for a in decision.actions] == [("highlight", {"things": ["s1"]})]
    prompt = agent.fast_model.prompts[-1]
    assert "what's the little dot" in prompt and "s1 cell nucleus" in prompt
    assert "Tools" not in prompt and len(prompt) < 2500  # no tool list, no history: the lean prompt


def test_the_lean_reflex_never_points_for_a_remark(tmp_path):
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {"intent": "statement", "needs_look": False})
    hub.settings.reflex = "lean"
    brain.sight = SimpleNamespace(view=SimpleNamespace(current=lambda info: True, things=[],
                                                       text=lambda info: "Things in view: s1 cell nucleus (centre)."))
    agent.fast_model = LeanVision({"about": ["s1"], "intent": "statement"})
    assert asyncio.run(s.point("So this is the microscope I was telling you about")) == []


def test_with_the_lean_reflex_pointing_starts_at_once_even_when_the_voice_hands_over(tmp_path):
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {"intent": "question", "needs_look": False, "answer": "A nucleus."})
    hub.settings.reflex = "lean"
    started = []

    async def point(request, channel="voice"):
        started.append(request.heard.words)
        return []

    s.point = point

    async def go():
        s.heard("what's the little dot")
        await asyncio.sleep(0.15)

    asyncio.run(go())
    assert started == ["what's the little dot"]


def test_a_request_is_newest_until_the_user_says_something_more_than_filler(tmp_path):
    from orchestrator.brain.speech import Heard

    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {"intent": "question", "needs_look": False})
    first = s._request(Heard("what's the little dot", 1.0))
    assert voice.spoken is s and s.newest(first.admission.request_id)
    filler = s._request(Heard("okay", 2.0))
    filler.intent = "filler"
    assert s.newest(first.admission.request_id)  # "okay" is not a newer question
    second = s._request(Heard("and the big one?", 3.0))
    second.intent = "question"
    assert not s.newest(first.admission.request_id) and s.newest(second.admission.request_id)
    assert s.newest("not-a-request")  # unknown: nothing is withheld on a guess


def test_with_talk_ended_a_spoken_requests_answer_is_filed_in_the_chat(tmp_path):
    from orchestrator.brain.speech import Heard

    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {"intent": "question", "needs_look": False})
    request = s._request(Heard("re-highlight the vascular bundle", 1.0))
    voice.closed = True  # Talk stopped while it was being answered (live 2026-10-11 16:36:22)
    s._file_turn(request, {"answer": "I re-highlighted the vascular bundle.", "turnId": 7, "actions": [], "asked": None})
    s._file_turn(request, {"answer": "I re-highlighted the vascular bundle.", "turnId": 7, "actions": [], "asked": None})
    lines = [line for line in shown if line.get("type") == "chat" and line.get("role") == "assistant"]
    assert [line["text"] for line in lines] == ["I re-highlighted the vascular bundle."]  # once


def test_a_spoken_request_to_act_is_not_narrated_while_it_runs_but_a_questions_answer_is_said(tmp_path):
    # The user's 2026-10-11 decision: "Put a picture of a stoma" is the voice's "Sure", the card, then the outcome;
    # the answer written before its steps run ("I'm putting up a reference picture... It's being done") is told.
    for intent, spoken_draft in (("request", False), ("question", True)):
        s, brain, hub, agent, day, shown, voice = spoken(tmp_path / intent, {"intent": intent, "needs_look": True},
                                                         {"answer": "Putting up a picture of a stoma.", "targets": []})
        hub.settings.reflex = "after-voice"  # the hand-off answers both here (the closer look has its own test)
        said = []

        async def say(text):
            said.append(text)

        async def go():
            s.heard("put a picture of a stoma")
            await asyncio.sleep(0.2)
            return await s.handoff("put a picture of a stoma", "d1", say)

        asyncio.run(go())
        assert bool(said) is spoken_draft, intent  # a request's early answer is never said; a question's is


def test_with_the_reflex_a_question_the_voice_answers_alone_gets_its_closer_look_finished(tmp_path):
    # Voice journeys run 5 (2026-10-11): "what's the little dot" with no dot in the eyes' notes; the voice guessed and
    # the look hands had started was dropped. Now it finishes (its outlines drawn) and the voice is given what it found
    # beside what it said, to add or correct only if it differs (the model judges).
    s, brain, hub, agent, day, shown, voice = spoken(tmp_path, {"intent": "question", "needs_look": True},
                                                     {"answer": "It is a chloroplast.", "targets": []})
    offered = []
    brain.narrator = SimpleNamespace(offer=offered.append)

    async def go():
        s.heard("what's the little dot")
        await asyncio.sleep(0.6)

    asyncio.run(go())
    assert any(r.get("kind") == "turn" for r in agent.logged)  # the closer look ran, without a hand-off
    judged = [r for r in agent.logged if r.get("kind") == "look-closer"]
    assert judged and offered  # a model judged whether it adds; without a judgment it is told silently
    assert offered[-1].delivery == "silent" or "added" in offered[-1].text


def test_a_closer_look_that_corrects_the_voice_is_said_and_one_that_repeats_is_told(tmp_path):
    for judged, spoken_line in (({"adds": True, "say": "It's a chloroplast, not a stoma."}, True),
                                ({"adds": False, "say": ""}, False)):
        s, brain, hub, agent, day, shown, voice = spoken(tmp_path / str(spoken_line), {"intent": "question", "needs_look": True},
                                                         {"answer": "It is a chloroplast.", "targets": []})
        offered = []
        brain.narrator = SimpleNamespace(offer=offered.append)
        plain = agent.fast_model.ask

        async def ask(images, prompt, max_output_tokens=0, judged=judged, plain=plain):
            if "A closer look at the picture then found" in prompt:
                return SimpleNamespace(data=judged, latency_ms=5.0)
            return await plain(images, prompt, max_output_tokens)

        agent.fast_model.ask = ask

        async def go():
            s.heard("what's the little dot")
            await asyncio.sleep(0.6)

        asyncio.run(go())
        last = offered[-1]
        assert (last.delivery != "silent" and "chloroplast, not a stoma" in last.text) is spoken_line
        assert (last.delivery == "silent") is (not spoken_line)
