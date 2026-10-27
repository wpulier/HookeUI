"""The app acts only when asked, and lets the user lead; the model judges what the words are
(live session 2026-10-04: "Okay" outlined a bristle, "and" a nucleus; the user asked to stop
being shown things at 15:07. Live session 2026-10-06: code must not judge for the model)."""
import asyncio
from types import SimpleNamespace

from orchestrator.brain.context import scene_table
from tests.fakes import fast
from orchestrator.brain.registry import ToolContext
from orchestrator.brain.speech import SpokenRequests, findings as brain_findings
from tests.test_honest import seen_brain


def fast_brain(reply):
    brain, hub, agent = seen_brain(reply, {})
    calls = []

    async def ask(images, prompt, max_output_tokens=0):
        calls.append(prompt)
        return SimpleNamespace(data=reply, latency_ms=5.0)

    agent.vision = lambda _m: SimpleNamespace(ask=ask)
    return brain, hub, agent, calls


HIGHLIGHT = {"intent": "request", "needs_look": False, "answer": "Outlining the xylem.",
             "actions": [{"tool": "highlight", "args": {"things": ["s1"]}}]}


def test_every_piece_of_speech_goes_to_the_model_and_a_filler_never_acts():
    brain, hub, agent, calls = fast_brain({**HIGHLIGHT, "intent": "filler"})
    assert asyncio.run(SpokenRequests(brain)._hands_run("Okay")) is None or not hub.scene.objects
    assert calls and "Okay" in calls[-1]  # the model judged it, not a word count
    assert not hub.scene.objects


def test_a_remark_never_acts_even_if_the_model_proposes_it():
    brain, _, _, _ = fast_brain({**HIGHLIGHT, "intent": "statement"})
    d = asyncio.run(fast(brain, "So I wanna become more observant myself"))
    assert d.actions == [] and d.intent == "statement"


def test_a_question_keeps_the_pointing_the_model_chose():
    brain, _, _, _ = fast_brain({**HIGHLIGHT, "intent": "question"})
    assert asyncio.run(fast(brain, "where is the xylem?")).actions  # no keyword decides it


def test_while_the_user_leads_only_their_own_request_points():
    brain, hub, _, calls = fast_brain({**HIGHLIGHT, "intent": "question"})
    hub.settings.user_leads = True
    assert asyncio.run(fast(brain, "where is the xylem?")).actions == []
    assert "The user is leading" in calls[-1]
    brain2, hub2, _, _ = fast_brain(HIGHLIGHT)
    hub2.settings.user_leads = True
    assert asyncio.run(fast(brain2, "highlight the xylem")).actions  # asked: it does
    assert asyncio.run(SpokenRequests(brain).point("So that's the xylem, right?")) == []  # never points on its own
    assert "The user is leading" in brain.view_note() and "The user is leading" in scene_table(brain.moment())[0]


def test_the_voice_points_along_only_when_the_model_says_it_was_a_question():
    brain, _, _, calls = fast_brain({"intent": "statement", "actions": [{"tool": "highlight", "args": {"things": ["s1"]}}]})
    assert asyncio.run(SpokenRequests(brain).point("I'm going to change the slide now")) == []
    assert calls  # the model was asked, and judged it a remark


def test_set_lead_turns_it_on_and_off_and_tells_the_studio():
    brain, hub, _, _ = fast_brain({})
    published = []
    brain.services.publish_settings = lambda: published.append(hub.settings.user_leads)

    async def go(on):
        run = brain.registry.start("set_lead", {"user_leads": on}, ToolContext("voice", 1, 7, "let me find it myself"))
        await run.task
        return run

    assert asyncio.run(go(True)).ok and hub.settings.user_leads
    asyncio.run(go(False))
    assert published == [True, False] and hub.settings.to_wire()["userLeads"] is False


def test_findings_name_every_outline():
    brain, hub, _, _ = fast_brain({})
    from tests.test_brain import obj

    o = obj(hub, "")
    findings = brain_findings(brain, {"answer": "Outlining it.", "objectIds": [o.id], "actions": [], "model": "m"})
    assert findings["outlined"] == ["an unnamed outline"]  # never "Nothing was outlined" over an outline


def test_unfinished_and_cancelled_words_are_the_models_judgment():
    brain, hub, agent, _ = fast_brain({"intent": "unfinished", "needs_look": False, "answer": "", "actions": []})
    s = SpokenRequests(brain)
    assert asyncio.run(s._hands_run("Can you close the")) is None and s.carry
    assert s.carry[1].heard.words == "Can you close the" and s.carry[1].intent == "unfinished"  # the request waits
    brain2, _, agent2, _ = fast_brain({"intent": "cancel", "needs_look": False, "answer": "", "actions": []})
    s2 = SpokenRequests(brain2)
    asyncio.run(s2._hands_run("wait for it to settle, actually never mind"))
    assert s2.cancelled_at > float("-inf") and agent2.logged[-1]["why"] == "called off"
