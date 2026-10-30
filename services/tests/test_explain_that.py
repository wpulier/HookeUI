""""I don't understand that, explain that": "that" is the last result the user saw or heard, read from its records
(board.last_result), and `resources(id="that")` gives each part's own record to explain it from (the live partner spec's
4.8, horizontal 4: what a thing is and what made it come from its record, never from the request's words).

Actual DayStore turns, Results, Index, Registry and Activity lines; a stand-in studio report. No model runs.
"""
import asyncio
import json
import time
from types import SimpleNamespace

from orchestrator import activity
from orchestrator.board import WhenAsked, last_result
from orchestrator.brain.registry import ToolContext
from orchestrator.brain.toolbox import Services, build
from orchestrator.screen import Screen
from orchestrator.ticket import issue, using
from tests.test_index_chronology import DAY, noon, save, stores
from tests.test_population import world  # noqa: F401
from tests.waiting import HANG_S


class Clock:
    def __init__(self, at: float) -> None:
        self.at = at

    def __call__(self) -> float:
        return self.at

    def to(self, at: float) -> float:
        self.at = at
        return at


def conversation(day, clock, *rows):
    """Turns filed at their own times: (at, role, channel, text, refs)."""
    for at, role, channel, text, refs in rows:
        clock.to(at)
        day.add_turn(role, channel, text, refs=refs, asked=day.here())
    return day.active.turns


def test_that_is_the_last_answer_with_what_its_request_made_as_of_when_the_user_asked(tmp_path):
    from orchestrator.day import DayStore

    clock = Clock(noon(DAY))
    day = DayStore(root=tmp_path / "sessions", day=DAY, clock=clock)
    base = noon(DAY)
    turns = conversation(
        day, clock,
        (base + 1, "user", "chat", "Outline the stoma and measure it", {}),
        (base + 4, "assistant", "chat", "I outlined the stoma; it is 42 µm long.",
         {"objectIds": ["o3"], "resourceIds": ["r1"]}),
        (base + 5, "note", "system", "The view changed: it now shows a leaf", {}),  # narration, not an outcome
        (base + 9, "user", "chat", "I don't understand that, explain that", {}),
        (base + 12, "assistant", "chat", "A later answer, after the request", {"resourceIds": ["r9"]}))
    found = last_result(turns, base + 9)
    assert found.resources == ("r1",) and found.outlines == ("o3",) and found.channel == "chat"
    assert found.said == ("I outlined the stoma; it is 42 µm long.",) and found.at == base + 4
    assert found.turn == turns[0].id, "the request it answered: its Activity line is request:<session>:<turn>"
    assert last_result(turns, base + 0.5) is None  # nothing had been said yet


def test_a_hand_offs_answer_and_the_voices_words_are_one_outcome(tmp_path):
    from orchestrator.day import DayStore

    clock = Clock(noon(DAY))
    day = DayStore(root=tmp_path / "sessions", day=DAY, clock=clock)
    base = noon(DAY)
    turns = conversation(
        day, clock,
        (base + 1, "user", "voice", "put the measurements in a new tab", {}),
        (base + 3, "note", "voice", "Model answer for the original request: The table is in a new Tab called Sizes.",
         {"resourceIds": ["d4"], "objectIds": []}),
        (base + 4, "assistant", "voice", "I've put the measurements in a new Tab.", {}),
        (base + 8, "user", "voice", "I don't understand that", {}))
    found = last_result(turns, base + 8)
    assert found.resources == ("d4",) and found.channel == "voice"
    assert found.said == ("The table is in a new Tab called Sizes.", "I've put the measurements in a new Tab.")


def test_a_window_opened_after_the_answer_is_what_that_means(tmp_path):
    from orchestrator.day import DayStore

    clock = Clock(noon(DAY))
    day = DayStore(root=tmp_path / "sessions", day=DAY, clock=clock)
    base = noon(DAY)
    turns = conversation(day, clock, (base + 1, "user", "chat", "What is this?", {}),
                         (base + 2, "assistant", "chat", "A stoma.", {}))
    screen = Screen(clock=lambda: base + 6)
    screen.report({"visible": True, "items": [
        {"id": "live", "kind": "live", "z": 1},
        {"id": "w-7", "kind": "document", "title": "Cell measurements", "z": 9,
         "document": {"project": day.sid, "tab": "main", "resource": {"id": "d2", "version": 1}}}]}, client=object())
    assert screen.opened_at("w-7") == base + 6
    found = last_result(turns, base + 10, screen)
    assert found.window == {"id": "w-7", "title": "Cell measurements", "kind": "document", "resource": "d2"}
    assert found.at == base + 6 and found.said == ()
    assert last_result(turns, base + 5, screen).said == ("A stoma.",), "opened after the request: not what it meant"


def test_a_window_is_opened_once_until_it_closes(tmp_path):
    at = [100.0]
    screen, studio = Screen(clock=lambda: at[0]), object()
    window = {"id": "w-1", "kind": "document", "title": "Table", "z": 3}
    screen.report({"visible": True, "items": [window]}, client=studio)
    at[0] = 110.0
    screen.report({"visible": True, "items": [window]}, client=studio)
    assert screen.opened_at("w-1") == 100.0  # still the first time it was shown open
    screen.report({"visible": True, "items": [{**window, "open": False}]}, client=studio)
    at[0] = 120.0
    screen.report({"visible": True, "items": [window]}, client=studio)
    assert screen.opened_at("w-1") == 120.0  # opened again
    screen.gone(studio)
    assert screen.opened_at("w-1") is None


def test_explain_that_reads_what_made_it_from_what_when_and_its_numbers(world, tmp_path, monkeypatch):
    """The tool the brain calls for "explain that": the facts, never prose the code wrote."""
    day, saved, index = stores(world, tmp_path)
    entry = save(day, saved, monkeypatch, noon(DAY) + 2, tool="compare", values={"count": 7, "meanAreaUm2": 31.5})
    clock = Clock(noon(DAY))
    day.clock = clock
    turns = conversation(day, clock, (noon(DAY) + 1, "user", "chat", "Compare the two outline models", {}),
                         (noon(DAY) + 3, "assistant", "chat", "Both found 7 cells.", {"resourceIds": [entry["id"]]}))
    sink = activity.Activity(world, SimpleNamespace(here=day.here, day=day, show=lambda *a, **k: None),
                             clock=lambda: noon(DAY) + 1)
    world.activities = sink
    sink.add("request", "Compare the two outline models", id=f"request:{day.sid}:{turns[0].id}", state="done",
             steps=[{"tool": "compare_models", "ok": True, "summary": "compared EdgeTAM and SAM 2.1 tiny on 7 cells",
                     "models": [{"role": "drawn by", "name": "EdgeTAM", "said": "EdgeTAM (on this Mac)"}]}],
             details={"answer": {"model": "Claude Opus 5.5"}})
    world.settings.level = "technical"
    registry = build(Services(hub=world, day=day))
    asked = day.here()
    ctx = ToolContext("chat", 12, None, "I don't understand that, explain that", asked=asked,
                      when_asked=WhenAsked(noon(DAY) + 9))

    async def go():
        request = issue(world, "turn", asked=asked)
        with using(request):
            run = registry.start("resources", {"id": "that"}, ctx)
            request.track(run.task)
            request.seal()
            await asyncio.wait_for(run.task, HANG_S)
        return run

    run = asyncio.run(go())
    assert run.ok, run.summary
    head, _, rest = run.summary.partition("\n")
    facts = json.loads(head.removeprefix("The last result, from its records: "))
    assert facts["said"] == ["Both found 7 cells."] and facts["level"] == "technical" and facts["askedBy"] == "typing"
    assert facts["at"] == time.strftime("%H:%M:%S", time.localtime(noon(DAY) + 3))
    assert facts["request"]["answeredBy"] == "Claude Opus 5.5"
    assert facts["request"]["steps"][0]["models"] == [{"role": "drawn by", "name": "EdgeTAM"}]
    result, = facts["results"]
    assert result["id"] == entry["id"] and result["made"] == time.strftime("%H:%M:%S", time.localtime(noon(DAY) + 2))
    assert result["method"] == {"name": "Side by side"} and result["title"] == entry["title"]  # what made it
    assert f"{entry['id']} in full:" in rest and '"meanAreaUm2": 31.5' in rest  # its numbers, from its own reading


def test_with_nothing_said_yet_that_is_asked_about_never_guessed(world, tmp_path):
    day, _, _ = stores(world, tmp_path)
    registry = build(Services(hub=world, day=day))

    async def go():
        run = registry.start("resources", {"id": "that"}, ToolContext("chat", 1, None, asked=day.here(),
                                                                       when_asked=WhenAsked(time.time())))
        await run.task
        return run

    run = asyncio.run(go())
    assert run.ok is False and "for \"that\" to mean" in run.summary
