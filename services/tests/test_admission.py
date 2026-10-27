"""Whether a researcher may start or run again (research/policy.py), what the day spent
(research/ledger.py) and the cap (unlimited unless set; ADR 0004). Before, the explicit-request
rule was applied by three tools in three ways (a "continue" or a revise could skip it), the cap
was checked only once it was already reached, and a run past midnight counted yesterday."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from orchestrator.brain.registry import ToolContext
from orchestrator.options import Settings
from orchestrator.research import ResearchManager, ResearchRefused, Session
from orchestrator.research.desk import Desk
from orchestrator.research.ledger import DayLedger
from orchestrator.research.policy import CHOOSABLE, admit
from tests.fakes import research_tools
from tests.test_research import FakeHub

# These start real-model researchers, which need their model's key (conftest.research_key).
pytestmark = pytest.mark.usefixtures('research_key')


@pytest.mark.parametrize("effort,deliverable", [("instant", "image"), ("instant", "annotated_image"),
                                             ("instant", "lesson"), ("quick", "video")])
def test_an_incompatible_deliverable_cannot_increase_the_requested_effort(tmp_path, effort, deliverable):
    manager = manager_with(tmp_path)
    words = f"Make a {deliverable} at {effort} effort"
    with pytest.raises(ResearchRefused, match="effort"):
        asyncio.run(manager.start(words, effort=effort, deliverable=deliverable, words=words, asked_with=words))
    assert not manager.sessions  # refused before queuing, reserving budget or writing a snapshot
    assert not list(tmp_path.rglob("frame.jpg"))


def test_a_short_lesson_can_be_made_at_quick_effort():
    """Live 2026-10-11 (the demo): "a tiny, very succinct presentation" at quick effort was refused twice for want of
    the lesson tool, not money; a lesson is one call within quick's allowance."""
    words = "Can you make a researcher that makes a tiny, very succinct presentation on this"
    admitted = admit("start", words=words, effort="quick", deliverable="lesson", asked_with=words)
    assert (admitted.effort, admitted.deliverable) == ("quick", "lesson")


@pytest.mark.parametrize("effort", ["instant", "quick", "standard", "deep"])
def test_a_written_report_keeps_its_selected_effort(effort):
    words = f"Write a report at {effort} effort"
    admitted = admit("start", words=words, effort=effort, deliverable="report", asked_with=words)
    assert admitted.effort == effort


def test_the_model_quotes_the_request_and_the_app_checks_the_quote():
    """ADMIT "quote" (the default): whether the user asked is the model's call, given as their own
    words; the app only checks they were said (in the last half minute)."""
    said = "can you show me step by step how it works"
    lesson = admit("start", words=said, effort="standard", deliverable="lesson", asked_with="show me step by step how it works")
    assert (lesson.effort, lesson.deliverable, lesson.asked) == ("standard", "lesson", True)  # keywords refused this
    with pytest.raises(ResearchRefused, match="asked_with"):
        admit("start", words=said, effort="quick", deliverable="video")  # no quote: the model did not say the user asked
    with pytest.raises(ResearchRefused, match="not what they said"):
        admit("start", words="what is this?", effort="quick", deliverable="video", asked_with="make me a video of it")
    assert admit("start", words="Find out what, um, the dark spots are", effort="quick", asked_with="find out what the dark spots are").asked
    assert not admit("start", words="look into it", effort="quick").asked  # no quote: the brain started it on its own
    report = admit("start", words="write me a report on it", effort="quick", deliverable="report", asked_with="write me a report")
    assert (report.effort, report.deliverable) == ("quick", "research")
    assert admit("revise", words="make question 2 easier", target_kind="lesson").effort == "revise"
    assert admit("revise", words="redo the video brighter", target_kind="video", asked_with="redo the video brighter").effort == "standard"
    with pytest.raises(ResearchRefused):
        admit("continue", words="go on", effort="standard", deliverable="lesson")


def test_with_keywords_a_new_researcher_keeps_its_selected_compatible_effort():
    with pytest.raises(ResearchRefused, match="effort is one of"):  # a choice is refused, never replaced
        admit("start", words="what is this?", effort="describe", deliverable="research", mode="keywords")
    assert "describe" not in CHOOSABLE and "revise" not in CHOOSABLE  # the app's own efforts are never offered
    report = admit("start", words="write me a report on it", effort="quick", deliverable="report", mode="keywords")
    assert (report.effort, report.deliverable) == ("quick", "research")
    assert admit("start", words="draw it", effort="quick", deliverable="image", mode="keywords").effort == "quick"
    assert admit("start", words="make me a lesson", effort="standard", deliverable="lesson", mode="keywords").effort == "standard"
    with pytest.raises(ResearchRefused, match="deliverable is one of"):
        admit("start", words="find out", effort="quick", deliverable="hologram", mode="keywords")
    with pytest.raises(ResearchRefused, match="explicitly"):
        admit("start", words="tell me about it", effort="quick", deliverable="video", mode="keywords")
    with pytest.raises(ResearchRefused, match="explicitly"):
        admit("start", words="look into it", effort="deep", deliverable="research", mode="keywords")


def test_with_keywords_asked_and_explicit_read_the_same_words():
    # "find out" was said a moment before; the piece that starts it only says "do it"
    assert admit("start", words="find out what the dark spots are. do it", effort="quick", mode="keywords").asked
    assert not admit("start", words="", effort="quick", mode="keywords").asked  # the brain started it on its own


def test_with_keywords_every_kind_of_run_again_is_admitted_in_one_place():
    assert admit("answer").effort == "quick"
    assert admit("revise", words="make question 2 easier", target_kind="lesson", mode="keywords").effort == "revise"
    with pytest.raises(ResearchRefused, match="explicitly"):
        admit("revise", words="make it brighter", target_kind="video", mode="keywords")
    assert admit("revise", words="redo the video brighter", target_kind="video", mode="keywords").effort == "standard"
    with pytest.raises(ResearchRefused, match="explicitly"):
        admit("continue", words="go on", effort="standard", deliverable="lesson", mode="keywords")


def manager_with(tmp_path, cap=None):
    hub = FakeHub()
    hub.settings = Settings()
    hub.settings.research_cap = cap
    manager = ResearchManager(hub, root=tmp_path, today=lambda: "2026-10-05")
    manager._pump = lambda: None  # nothing runs
    return manager


def finished(manager, n, deliverable="lesson", spent=0.0, day="2026-10-05", purpose="research"):
    s = Session(rid=f"r{n}", n=n, brief="b", deliverable=deliverable, effort="standard", engine="gpt-6.1-sol",
                purpose=purpose, label=f"label {n}", object_id=None, origin={}, day=day, status="done", spent=spent,
                result={"title": "t"}, root=manager.root)
    manager.sessions[s.rid] = s
    return s


def test_the_manager_admits_so_no_path_can_skip_it(tmp_path):
    manager = manager_with(tmp_path)
    with pytest.raises(ResearchRefused, match="asked"):
        asyncio.run(manager.start("a quiz", effort="quick", deliverable="lesson", words="what is it"))
    session = asyncio.run(manager.start("a quiz", effort="standard", deliverable="lesson", words="make me a quiz",
                                        asked_with="make me a quiz"))
    assert session.effort == "standard" and session.origin["asked"]
    finished(manager, 7)
    with pytest.raises(ResearchRefused, match="asked"):
        asyncio.run(manager.followup("r7", "more", mode="continue", words="and?"))
    assert asyncio.run(manager.followup("r7", "what is q2 about?", mode="answer")).run.kind == "answer"
    # A scripted researcher (the scenario harness) is test plumbing: not admitted, not refused.
    assert asyncio.run(manager.start("t", effort="quick", deliverable="video", fake={"seconds": 0.1})).deliverable == "video"


def test_without_a_cap_nothing_is_refused_for_money(tmp_path):
    manager = manager_with(tmp_path, cap=None)
    finished(manager, 1, spent=500.0)
    assert asyncio.run(manager.start("look into it", effort="deep", deliverable="research", words="research it in depth", asked_with="research it in depth"))


def test_a_cap_reserves_a_runs_maximum_and_gives_back_what_it_did_not_spend(tmp_path):
    manager = manager_with(tmp_path, cap=0.40)
    first = asyncio.run(manager.start("look into it", effort="quick", words="look into it"))  # may cost up to $0.25
    with pytest.raises(ResearchRefused, match=r"\$0.15 left"):
        asyncio.run(manager.start("and this", effort="quick", words="look into this"))  # 0.25 + 0.25 > 0.40
    first.status, first.spent = "running", 0.05
    manager._finish(first, "done", {"title": "x"})  # spent $0.05 of its $0.25
    assert asyncio.run(manager.start("and this", effort="quick", words="look into this"))
    assert abs(manager.spent_today() - 0.05) < 1e-9


def test_days_are_kept_apart_and_what_is_on_disk_counts(tmp_path):
    day = {"id": "2026-10-04"}
    manager = manager_with(tmp_path)
    manager.today = lambda: day["id"]
    manager.spending.today = manager.today
    finished(manager, 1, spent=2.0, day="2026-10-04")
    assert manager.spent_today() == 2.0
    day["id"] = "2026-10-05"  # past midnight, the app still running
    assert manager.spent_today() == 0.0
    finished(manager, 2, spent=0.5, day="2026-10-05")
    directory = tmp_path / "2026-10-05" / "rdesc"
    directory.mkdir(parents=True)
    (directory / "session.json").write_text(json.dumps({"purpose": "description", "spent": 0.03}))  # not in memory
    manager.spending._on_disk.clear()
    assert abs(manager.spent_today() - 0.53) < 1e-9


def test_a_refusal_never_billed_is_no_spend_and_estimates_are_said_apart(tmp_path):
    """Live 2026-10-25: two presentations OpenAI refused for want of credit (HTTP 429), saved by a build before that
    rule, kept their $0.40 reservations as estimates, so Research today said $1.20 while Spending had $0.40 of research.
    A job refused with 429 or for want of credit counts nothing, saved or in memory; other estimates stay counted and
    are said apart (`estimated`), of every researcher, not only the ones the studio lists."""
    from orchestrator.research import ledger

    day = "2026-10-25"
    manager = manager_with(tmp_path)
    manager.today = lambda: day
    manager.spending.today = manager.today
    raw = "Error code: 429 - {'error': {'message': 'You have no credits remaining.', 'type': 'insufficient_quota'}}"
    old_refusal = {'state': 'failed', 'cost': 0.0, 'estimatedCost': 0.4, 'outcomeUnknown': True, 'dispatchedAt': 1.0,
                   'error': raw, 'day': day}  # saved before the rule: no providerStatus, its raw words
    directory = tmp_path / day / "r602534"
    directory.mkdir(parents=True)
    (directory / "session.json").write_text(json.dumps({"purpose": "research", "spent": 0.104, "presentationJobs": [old_refusal]}))
    assert abs(manager.spent_today() - 0.104) < 1e-9 and manager.spending.estimated() == 0.0
    finished(manager, 2, spent=0.05, day=day)
    session = manager.get("r2")
    session.presentation_jobs = [
        {'state': 'failed', 'cost': 0.0, 'estimatedCost': 0.0, 'providerStatus': 429, 'error': 'OpenAI has no credits left.', 'day': day},
        {'state': 'failed', 'cost': 0.0, 'estimatedCost': 0.4, 'error': 'Connection reset', 'dispatchedAt': 1.0, 'day': day},  # maybe billed
        {'state': 'ready', 'cost': 0.02, 'estimatedCost': 0.0, 'receipt': {'usd': 0.02}, 'day': day}]
    session.spent_estimated = 0.01
    assert abs(manager.spent_today() - (0.104 + 0.05 + 0.01 + 0.4 + 0.02)) < 1e-9
    assert abs(manager.estimated_today() - (0.01 + 0.4)) < 1e-9  # what the studio says as "up to … more"
    assert manager.list_wire()["estimatedToday"] == round(0.41, 4)
    assert ledger.never_billed({'error': 'OpenAI has no credits left, so the presentation was not made.'})
    assert not ledger.never_billed({'error': 'Connection reset', 'estimatedCost': 0.4})


def test_numbers_name_only_researchers(tmp_path):
    manager = manager_with(tmp_path)
    finished(manager, 0, purpose="description")
    manager.brain_session()  # the brain's own corrections (n 0)
    with pytest.raises(ResearchRefused):
        Desk(manager).find("0")
    with pytest.raises(ResearchRefused):
        Desk(manager).find("brain")


def test_the_cap_is_set_by_voice_or_chat_and_kept_until_changed(tmp_path):
    from orchestrator.day import DayStore

    manager = manager_with(tmp_path)
    day = DayStore(root=tmp_path / "sessions")
    published = []
    registry = research_tools(manager.hub, manager, day=day, publish_settings=lambda: published.append(1))

    async def cap(value):
        run = registry.start("set_research_cap", {"dollars": value, "asked_with": "cap research"},
                             ToolContext("voice", 1, 7, "cap research"))
        await run.task
        return run

    assert asyncio.run(cap("$10")).ok and manager.hub.settings.research_cap == 10.0 and day.prefs["researchCap"] == 10.0
    assert "no daily research cap" in asyncio.run(cap("none")).summary and manager.hub.settings.research_cap is None
    assert DayStore(root=tmp_path / "sessions").prefs["researchCap"] is None  # no cap is kept too (day/prefs.py)
    assert asyncio.run(cap("lots")).ok is False and published == [1, 1]


def test_the_cap_changes_only_in_the_users_own_words(tmp_path):
    manager = manager_with(tmp_path, cap=5.0)
    registry = research_tools(manager.hub, manager)

    async def cap(question):
        # The model quotes what it took as the user's request; the app checks the quote against what they said.
        run = registry.start("set_research_cap", {"dollars": "none", "asked_with": "remove the research cap"},
                             ToolContext("chat", 1, 7, question))
        await run.task
        return run

    # A researcher's text reaching the model must never lift the cap: nothing the user said asks for it.
    refused = asyncio.run(cap("what did the researcher find about stomata?"))
    assert refused.ok is False and manager.hub.settings.research_cap == 5.0
    assert asyncio.run(cap("remove the research cap")).ok and manager.hub.settings.research_cap is None


def test_two_starts_at_once_cannot_both_slip_under_the_cap(tmp_path):
    manager = manager_with(tmp_path, cap=0.30)  # room for one quick run (up to $0.25), not two

    async def both():
        return await asyncio.gather(manager.start("one", effort="quick", words="look into it"),
                                    manager.start("two", effort="quick", words="look into this"), return_exceptions=True)

    results = asyncio.run(both())
    assert sum(isinstance(r, ResearchRefused) for r in results) == 1


def test_a_cap_that_is_not_an_amount_never_stops_the_app(monkeypatch):
    monkeypatch.setenv("MICROMIND_RESEARCH_CAP", "lots")
    assert Settings().research_cap is None
    monkeypatch.setenv("MICROMIND_RESEARCH_CAP", "12")
    assert Settings().research_cap == 12.0


def test_a_press_of_look_deeper_allows_a_closer_look_or_a_quick_researcher_only():
    """The card's Look deeper asks in words the studio composed (notes.ts DEEPER_TEXT): they never count as
    the user asking for a deep or standard run, a lesson or a video (the partner §1b, §2.7)."""
    import pathlib
    import re

    from orchestrator.research.policy import STUDIO_PHRASES

    pressed = "Look into this one more closely"
    assert admit("start", words=pressed, effort="quick").effort == "quick"
    for effort, deliverable in (("deep", None), ("standard", None), ("standard", "lesson"), ("standard", "video")):
        with pytest.raises(ResearchRefused, match="own words|asked for one"):
            admit("start", words=pressed, effort=effort, deliverable=deliverable, asked_with=pressed)
    # The user's own words still count, alongside a press.
    said = f"{pressed}. Actually, do a deep dive on it"
    assert admit("start", words=said, effort="deep", asked_with="do a deep dive on it").effort == "deep"
    # The phrase is the studio's own (one source of truth for the words a press sends).
    notes = (pathlib.Path(__file__).resolve().parents[2] / "apps" / "studio" / "src" / "notes.ts").read_text()
    sent = re.search(r"DEEPER_TEXT = '([^']+)'", notes).group(1)
    assert sent.lower() in STUDIO_PHRASES


@pytest.mark.parametrize("effort", ["instant", "quick", "standard", "deep"])
def test_start_receipt_states_the_effort_that_was_actually_admitted(tmp_path, effort):
    manager = manager_with(tmp_path)
    registry = research_tools(manager.hub, manager)
    words = f"Please do {effort} research about these saved shapes"
    async def go():
        result = registry.start("start_researcher", {"brief": words, "effort": effort, "asked_with": words},
                                ToolContext("chat", 1, 7, words))
        await result.task
        return result
    result = asyncio.run(go())
    assert result.ok
    assert f"{effort} effort" in result.summary
    assert "deeper look" not in result.summary
