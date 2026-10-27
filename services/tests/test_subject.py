"""The subject in view (day/subject.py): it changes freely within a day and a conversation,
after two looks agree; the user's name for it wins; it is kept with the conversation."""
import asyncio
from types import SimpleNamespace

from orchestrator.brain.state import Conversation
from orchestrator.day import DayStore
from orchestrator.day.subject import SubjectTracker
from orchestrator.research import ResearchManager
from orchestrator.scene import Scene
from orchestrator.sight import SIGHT, USER_NAMED


def look(specimen, same=None):
    return SimpleNamespace(specimen=specimen, same=same)


def tracker(tmp_path):
    day = DayStore(root=tmp_path)
    conversation = Conversation(store=day)
    scene, sent = Scene(), []
    return SubjectTracker(day, conversation, scene, sent.append), day, conversation, scene, sent


def as_real(root):
    """Scripted (fake) researchers are not restored; make the saved ones look like real work."""
    import json

    for path in root.glob("*/*/session.json"):
        data = json.loads(path.read_text())
        path.write_text(json.dumps({**data, "engine": "gpt-6.1-sol"}))


def test_a_new_subject_is_noticed_after_two_looks_and_nothing_is_cleared(tmp_path):
    subjects, day, conversation, scene, sent = tracker(tmp_path)
    assert subjects.observe(look("leaf epidermis")) is False
    assert scene.context["specimen"] == "leaf epidermis" and day.active.subject == "leaf epidermis"
    told = []
    subjects.on_change.append(lambda old, new: told.append((old, new)))

    assert subjects.observe(look("an apple slice", same=False)) is True  # unsure: look again soon
    assert "specimen" not in scene.context  # nothing describes the view from the old one meanwhile
    assert subjects.subject == "leaf epidermis"  # ...but the next look is still asked about it
    subjects.observe(look("leaf epidermis", same=True))
    assert scene.context["specimen"] == "leaf epidermis"  # it was the same after all
    subjects.observe(look("an apple slice", same=False))
    assert not told  # the count starts again after a look that agreed

    subjects.observe(look("an apple slice", same=False))
    assert told == [("leaf epidermis", "an apple slice")] and scene.context["specimen"] == "an apple slice"
    assert "now shows an apple slice" in conversation.text() and len(day.threads) == 1  # recorded, no new conversation
    assert sent[-1]["type"] == "session" and sent[-1]["specimen"] == "an apple slice"


def test_a_change_is_confirmed_even_when_the_second_look_could_not_compare(tmp_path):
    subjects, *_ , scene, _ = tracker(tmp_path)
    subjects.observe(look("cheek epithelial cells"))
    subjects.observe(look("blood smear", same=False))
    subjects.observe(look("blood smear", same=None))
    assert scene.context["specimen"] == "blood smear" and subjects.subject == "blood smear"


def test_the_user_names_the_specimen_and_the_look_takes_it_as_a_hint(tmp_path):
    subjects, day, conversation, scene, _ = tracker(tmp_path)
    subjects.observe(look("gymnosperm stem, ~100x"))
    assert subjects.name("lucerne (alfalfa) stem") == "lucerne (alfalfa) stem"
    assert scene.context["specimen"] == "lucerne (alfalfa) stem" and subjects.user_named() == "lucerne (alfalfa) stem"
    assert ("The user says the specimen is lucerne (alfalfa) stem (the app had called it gymnosperm stem, ~100x)"
            in conversation.turns[-1].text)
    prompt = SIGHT.format(context=USER_NAMED.format(name=subjects.user_named()), same="", same_key="")
    assert "says it is: lucerne (alfalfa) stem" in prompt
    subjects.observe(look("blood smear", same=False))
    subjects.observe(look("blood smear", same=False))  # a new slide: the user's name was for the old one
    assert subjects.subject == "blood smear" and subjects.user_named() == ""


def test_an_answer_fills_the_subject_only_when_there_is_none(tmp_path):
    subjects, day, *_ = tracker(tmp_path)
    subjects.guess("pine stem")
    assert subjects.subject == "pine stem"
    subjects.name("lucerne stem")
    subjects.guess("pine stem")
    assert subjects.subject == "lucerne stem"


def test_the_subject_comes_back_after_a_restart_and_the_user_name_sticks(tmp_path):
    subjects, *_ = tracker(tmp_path)
    subjects.observe(look("dicot stem"))
    subjects.name("lucerne stem")
    again, day, _, scene, _ = tracker(tmp_path)
    assert again.subject == "lucerne stem" and scene.context["specimen"] == "lucerne stem"
    assert again.user_named() == "lucerne stem" and day.subjects["lucerne stem"]["named_by"] == "user"


def test_researchers_are_filed_by_session(tmp_path, monkeypatch):
    """ADR 0017: a researcher belongs to the session it was started in. Midnight changes nothing; another session
    open does not show it (its work is that session's), and a restart in its session brings it back."""
    monkeypatch.setenv("MICROMIND_RESEARCH_ENGINE", "fake")
    from tests.test_research import FakeHub as ResearchHub, until

    async def go():
        day = {"id": "2026-10-05"}
        open_ = {"sid": "2026-10-05"}
        manager = ResearchManager(ResearchHub(), root=tmp_path, today=lambda: day["id"], session=lambda: open_["sid"])
        done = []
        manager.on_done.append(done.append)
        await manager.start("teach me about stomata", effort="quick", fake={"seconds": 0.2})
        assert await until(lambda: done)
        before = manager.lines()
        day["id"] = "2026-10-06"  # the next day, the same session
        overnight = manager.lines()
        open_["sid"] = "2026-10-06"  # another session
        manager.session_event({"type": "session-opened", "session": open_["sid"]})
        after = manager.lines()
        manager.stop()
        as_real(tmp_path)
        again = ResearchManager(ResearchHub(), root=tmp_path, today=lambda: day["id"],
                                session=lambda: "2026-10-05")  # restarted in its session, a day later
        return before, overnight, after, again

    before, overnight, after, again = asyncio.run(go())
    assert before.startswith("Researcher 1") and overnight == before
    assert after == ""
    restored = again.researchers()
    assert len(restored) == 1 and restored[0].sitting == "2026-10-05" and restored[0].day == "2026-10-05"
