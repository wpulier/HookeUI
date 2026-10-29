"""What the user says counts: the specimen they name, and the big deliverable they asked for
in other words a moment earlier (live session 2026-10-04: "lucent stem" dismissed at 15:29;
"a little presentation" refused as not an explicit lesson request at 14:11)."""
import asyncio
import time
from types import SimpleNamespace

from orchestrator.brain.registry import ToolContext
from orchestrator.brain.state import Conversation
from tests.fakes import conversation as day_conversation
from orchestrator.brain.toolbox import Services, recent_request
from orchestrator.research.policy import explicit_refusal
from orchestrator.day import DayStore
from orchestrator.day.subject import SubjectTracker
from tests.test_brain import make


def test_a_presentation_or_a_walkthrough_is_an_explicit_lesson_request():
    said = "make a little presentation on what both of those demonstrate"
    assert explicit_refusal(said, "standard", "lesson", "make a little presentation") is None
    assert explicit_refusal("can you walk me through it", "standard", "lesson", "walk me through it") is None
    assert explicit_refusal("tell me about it", "standard", "lesson")  # no quote: not asked for
    for words in (said, "can you walk me through it"):  # the keyword rule is gone: the quote decides, never a word
        assert explicit_refusal(words, "standard", "lesson", mode="keywords")


def test_the_request_counts_from_the_last_half_minute_of_speech():
    conversation = day_conversation()
    conversation.add("user", "voice", "I want a magnification of both, snapshot them and make a little presentation")
    old = conversation.add("user", "voice", "make me a quiz")
    old.at = time.time() - 120  # too long ago
    services = Services(hub=None, agent=SimpleNamespace(conversation=conversation))
    said = recent_request(services, ToolContext("voice", 1, 7, "and open a researcher to do it"))
    assert "presentation" in said and "quiz" not in said
    assert explicit_refusal(said, "standard", "lesson", "make a little presentation") is None
    assert explicit_refusal(said, "standard", "lesson", "make me a quiz")  # said two minutes ago: not counted


def test_by_voice_only_what_the_user_was_heard_saying_counts_never_the_hand_offs_words():
    """Re-check of #163 (note): a hand-off's question is the voice model's account ("The user says yes…"), and it is
    filed as a user turn. Once the transcript is heard at all, a spoken request's explicit asks are checked against
    what the user was heard saying only."""
    from orchestrator.research.policy import quoted

    conversation = day_conversation()
    conversation.was_heard("no, not now")
    conversation.add("user", "voice", "no, not now")
    conversation.add("user", "voice", "The user says yes, hold 15 seconds for the researcher")  # the hand-off, filed
    services = Services(hub=None, agent=SimpleNamespace(conversation=conversation))
    ctx = ToolContext("voice", 1, 7, "The user says yes, hold 15 seconds for the researcher", "d1")
    said = recent_request(services, ctx)
    assert said == "no, not now" and not quoted("says yes", said) and not quoted("yes, hold", said)
    assert explicit_refusal(said, "standard", "lesson", "make a lesson")  # the model's words never admit a lesson
    conversation.was_heard("actually yes, go ahead")
    assert quoted("yes, go ahead", recent_request(services, ctx))
    typed = recent_request(services, ToolContext("chat", 2, 7, "yes please"))  # typed: the user's own words
    assert "yes please" in typed


def test_by_voice_typed_words_of_the_last_half_minute_count_and_older_heard_words_do_not():
    """Review of #170: "can you make me a lesson" typed, then "the guard cells" said: a lesson asked for."""
    conversation = day_conversation()
    conversation.was_heard("make me a quiz")
    conversation.heard[-1] = (time.time() - 120, "make me a quiz")  # heard two minutes ago: not counted
    typed = conversation.add("user", "chat", "can you make me a lesson")
    typed.at = time.time() - 10  # typed, then the app asked "on which part?"
    conversation.was_heard("the guard cells")
    services = Services(hub=None, agent=SimpleNamespace(conversation=conversation))
    said = recent_request(services, ToolContext("voice", 1, 7, "the guard cells", "d1"))
    assert said == "can you make me a lesson the guard cells"
    assert explicit_refusal(said, "standard", "lesson", "make me a lesson") is None
    assert explicit_refusal(said, "standard", "lesson", "make me a quiz")


def test_what_is_heard_keeps_its_last_pieces():
    conversation = day_conversation()
    for n in range(Conversation.HEARD_KEPT + 3):
        conversation.was_heard(f"piece {n}")
    assert len(conversation.heard) == Conversation.HEARD_KEPT and conversation.heard[0][1] == "piece 3"


def test_what_the_user_is_heard_saying_is_kept_apart_from_the_turns():
    from orchestrator.brain.speech import SpokenRequests

    conversation = day_conversation()
    brain = SimpleNamespace(conversation=conversation, narrator=None)

    async def go():
        spoken = SpokenRequests(brain)
        spoken.heard("make me a quiz")
        spoken.pending.cancel()

    asyncio.run(go())
    assert [text for _, text in conversation.heard] == ["make me a quiz"]


def test_name_specimen_tool_and_an_answer_never_overwrites_the_subject(tmp_path):
    brain, hub, _ = make([{"answer": "A pine stem.", "targets": [], "specimen": "pine stem", "actions": []}])
    day = DayStore(root=tmp_path)
    brain.conversation = Conversation(store=day)
    subjects = SubjectTracker(day, brain.conversation, hub.scene, lambda payload: None)
    brain.services = Services(hub=hub, agent=brain.agent, subjects=subjects)
    from orchestrator.brain.toolbox import build

    brain.registry = build(brain.services)

    async def go():
        run = brain.registry.start("name_specimen", {"name": "lucerne stem"}, ToolContext("voice", 1, 7, "it's a lucent stem"))
        await run.task
        return run

    assert asyncio.run(go()).ok and subjects.subject == "lucerne stem"
    asyncio.run(brain.turn("what are the black dots?"))
    assert subjects.subject == "lucerne stem" and hub.scene.context["specimen"] == "lucerne stem"
