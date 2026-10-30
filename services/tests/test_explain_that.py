""""I don't understand that, explain that": "that" is the last result the user saw or heard, read from its records
(board.last_result), and `resources(id="that")` gives each part's own record to explain it from (the live partner spec's
4.8, horizontal 4: what a thing is and what made it come from its record, never from the request's words).

Actual DayStore turns, Results, Index, Registry and Activity lines; a stand-in studio report. No model runs.
"""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

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


# --- will-research "explain-graphic", measure-c4acc90 (runs judged 1, 2, 1) ---------------------------------------------
# Right after "Start a researcher to make a small labelled graphic of a stoma and put it in a new tab called Graphic when
# it's done", the user said "I don't understand that, explain that." Run 1 was answered with the placing step's status
# ("I created the new Graphic tab and put Researcher 1's labeled stoma picture inside it. An earlier attempt to switch
# tabs had a hiccup"), runs 2-3 with stomata in general, none saying who made it, from what photo, when, or its labels
# from its record. The logs: every run answered from the quick pass's notes with no tool (calls 1, actions []); in runs 1
# and 3 the hand-off's answer note that placed the picture (present do=place what=r386151:o1) kept resourceIds [], so
# "that" had no record to read at all.

ILLUSTRATION_PROMPT = ("Create a small, clean educational graphic titled 'Stoma'. Exactly three clear leader-line labels: "
                       "'Pore (opening)' pointing into the central slit; 'Guard cells' pointing to both kidney-shaped "
                       "cells; 'Chloroplasts' pointing to one dark green dot inside a guard cell.")


def stoma_researcher(root, day, sid, *, started):
    """Researcher 1 of the run: a labelled stoma illustration it made with the picture model, from the photo of the live
    view it was given when it started (its session.json, as the run kept it)."""
    from orchestrator.research.manager import Session
    from tests.test_research_entries import png
    from tests.test_resources import jpeg

    folder = root / day / "r386151"
    (folder / "outputs").mkdir(parents=True)
    (folder / "inputs").mkdir()
    (folder / "outputs" / "01-illustration.png").write_bytes(png())
    (folder / "inputs" / "frame.jpg").write_bytes(jpeg(90))
    caption = "Generated illustration (gpt-image-2.5-flare), not the specimen"
    outputs = [{"id": "o1", "kind": "image", "path": "outputs/01-illustration.png", "caption": caption,
                "content": {"kind": "text", "text": ILLUSTRATION_PROMPT + f" ({caption})"}, "generated": True,
                "altered": False, "version": 1, "replaces": None}]
    result = {"title": "Graphic", "summary": "I made a small labeled stoma illustration showing the pore (opening), "
                                             "guard cells and chloroplasts.",
              "details": ["Generated illustration: outputs/01-illustration.png.",
                          "Labels: Pore (opening), Guard cells, Chloroplasts."]}
    return Session(rid="r386151", n=1, brief="Make a small labeled graphic of a stoma and put it in a new tab called "
                                             "Graphic when it's done.",
                   deliverable="image", effort="quick", engine="gpt-6.1-sol", purpose="research", label="",
                   object_id=None, origin={"thread": "t1"}, day=day, status="done", created=started - 1.0,
                   started=started, finished=started + 33.0, outputs=outputs, result=result, sitting=sid, root=root,
                   specimen="plant tissue showing chloroplasts and stomata")


def test_explain_that_after_a_researchers_picture_was_placed_reads_the_picture_and_its_researcher(tmp_path):
    """resources(id="that") on the run's last result: the picture placed in the Graphic Tab (r386151:o1), with who made
    it and with which models, from what photo, when, and its labels, all from its records."""
    from types import SimpleNamespace

    from tests.test_research_entries import index_with

    web, built, index = index_with(tmp_path)
    root = tmp_path / "research-root"
    started = time.time() - 120
    s = stoma_researcher(root, index.today(), index.session(), started=started)
    index.research = SimpleNamespace(researchers=lambda day=None, session=None: [s], root=root, get=lambda rid: s)
    day = built.day
    asked = day.here()
    day.add_turn("user", "voice", "Put the picture in a new Tab named Graphic and show it when it lands.", asked=asked)
    day.add_turn("note", "voice", "Model answer for the original request: I created the Graphic Tab and placed "
                 "Researcher 1's stoma illustration there in front.", refs={"resourceIds": ["r386151:o1"]}, asked=asked)
    day.add_turn("assistant", "voice", "Researcher 1's labeled stoma illustration is open in the new Graphic Tab.",
                 asked=asked)
    ctx = ToolContext("voice", 9, None, "I don't understand that. Explain that.", asked=asked,
                      when_asked=WhenAsked(time.time() + 1))

    async def go():
        request = issue(built.hub, "turn", asked=asked)
        with using(request):
            run = built.brain.registry.start("resources", {"id": "that"}, ctx)
            request.track(run.task)
            request.seal()
            await asyncio.wait_for(run.task, HANG_S)
        return run

    run = asyncio.run(go())
    assert run.ok, run.summary
    head, guidance, *rest = run.summary.split("\n")
    facts = json.loads(head.removeprefix("The last result, from its records: "))
    picture, = facts["results"]
    assert picture["id"] == "r386151:o1" and picture["kind"] == "figure"
    assert picture["maker"] == {"kind": "researcher", "id": "r386151", "name": "Researcher 1"}
    assert picture["method"] == {"name": "Research", "implementation": "GPT-6.1 Sol + web search"}  # with which model
    researcher = picture["researcher"]
    assert researcher["name"] == "Researcher 1" and researcher["engine"] == "GPT-6.1 Sol + web search"
    assert researcher["asked"].startswith("Make a small labeled graphic of a stoma")
    assert researcher["given"] == ("a photo of the live view when it started ("
                                   + time.strftime("%H:%M:%S", time.localtime(started)) + ")")  # from what
    assert researcher["finished"] == time.strftime("%H:%M:%S", time.localtime(started + 33.0))  # when
    assert "Explain the thing itself" in guidance and "never the status of the request" in guidance
    reading = "\n".join(rest)
    assert "gpt-image-2.5-flare" in reading  # the picture model, as its record names it
    assert "Labels: Pore (opening), Guard cells, Chloroplasts." in reading  # its labels, from its researcher's record
    assert reading.count("<researchers") == 0 or 'untrusted="true"' in reading  # a researcher's words stay wrapped


def test_a_researchers_picture_placed_in_a_tab_is_the_result_its_request_names(tmp_path, monkeypatch):
    """Runs 1 and 3: present do=place put Researcher 1's picture in the Graphic Tab ("opened there from its saved work
    (r386151:o1)"), and the hand-off's answer kept resourceIds []: the step now keeps the saved work it placed, as a
    reopen does, so its request's answer names it and "that" can be read."""
    from orchestrator import placement
    from tests.test_placement_put import opens_pictures, saved
    from tests.test_workspace_display import built as display_built
    from tests.test_table_window import asked as asked_tool

    async def go():
        app, studio = await display_built(tmp_path, monkeypatch)
        figure = SimpleNamespace(id="r386151:o1", kind="figure", version=None, title="Researcher 1's picture",
                                 shown="Generated illustration (gpt-image-2.5-flare), not the specimen",
                                 day=studio.project, pictures=["/x"], untrusted=True,
                                 source={"researcher": "r386151", "outputId": "o1", "line": "o1"}, values={},
                                 media="generated", state="done", summary="")
        saved(app, monkeypatch, **{"r386151:o1": figure})
        source = {"day": studio.project, "id": "r386151", "version": 1, "picture": 0, "outputId": "o1"}
        monkeypatch.setattr(placement, "picture_source", lambda services, entry: source if entry is figure else None)
        opens_pictures(studio, monkeypatch)
        await studio.send_report()
        run = await asked_tool(app, studio, "present", {"do": "place", "what": "r386151:o1", "tab": "Graphic",
                                                        "placement": "window"})
        assert run.ok is True, run.summary
        assert "opened there from its saved work (r386151:o1)" in run.summary
        assert run.resource is not None and run.resource["id"] == "r386151:o1" and run.resource["day"] == studio.project
    asyncio.run(go())


def test_a_request_about_the_last_result_is_answered_from_its_record(world, tmp_path):
    """The quick pass judges the words are about the last result ("about_last", told only while there is one): the turn
    then reads that thing's record (resources id "that") and the answer is written from it, never from the notes'
    status line (the run's answer, kept here as what the quick pass wrote)."""
    from orchestrator.brain.loop import Brain
    from orchestrator.brain.state import Conversation
    from tests.test_brain import FakeAgent, FakeVision

    day, saved_results, index = stores(world, tmp_path)
    entry = save(day, saved_results, pytest.MonkeyPatch(), noon(DAY) + 2, tool="compare", values={"count": 7})
    clock = Clock(noon(DAY))
    day.clock = clock
    conversation(day, clock, (noon(DAY) + 1, "user", "voice", "Put the picture in a new Tab named Graphic", {}),
                 (noon(DAY) + 3, "note", "voice", "Model answer for the original request: I created the Graphic Tab "
                  "and placed the picture there in front.", {"resourceIds": [entry["id"]]}))
    clock.to(noon(DAY) + 9)
    world.settings.fast = "gemini-3.8-flash"
    status = ("In simple terms, I created the new Graphic tab and put Researcher 1's labeled stoma picture inside it. "
              "An earlier attempt to switch tabs had a hiccup, but the tab and picture are already open in front right now.")
    explained = "It is the comparison picture made at 12:00:02: both models found 7 cells."
    talk = Conversation(store=day)
    agent = FakeAgent(world, talk, [])
    agent.fast_model = FakeVision(fast={"intent": "question", "needs_look": False, "answer": status, "actions": [],
                                        "about_last": True},
                                  rewrite={"intent": "question", "needs_look": False, "answer": explained, "actions": []})
    services = Services(hub=world, agent=agent, day=day)
    brain = Brain(world, agent, build(services), talk)
    brain.services = services
    result = asyncio.run(brain.turn("I don't understand that. Explain that.", "voice",
                                    when_asked=WhenAsked(noon(DAY) + 9)))
    reads = [a for a in result["actions"] if a["tool"] == "resources"]
    assert reads and reads[0]["ok"], result["actions"]
    assert reads[0]["summary"].startswith("The last result, from its records:")
    answered = [p for p in agent.fast_model.prompts if "only your notes" not in p]
    assert answered and "The last result, from its records:" in answered[0], "the answer round is given the record"
    assert entry["id"] in answered[0]
    assert result["answer"] == explained, result["answer"]


def test_only_while_there_is_a_last_result_is_the_brain_asked_whether_the_words_are_about_it(world, tmp_path):
    """Context earns its place: the judgment is asked for in the last-result line, which is there only while a last
    result is; the voice's notes keep the fact without the brain's reply field."""
    from orchestrator.brain.context import _last_result_words

    day, _, _ = stores(world, tmp_path)
    clock = Clock(noon(DAY))
    day.clock = clock
    brain = SimpleNamespace(services=SimpleNamespace(day=day), hub=world)
    asked = SimpleNamespace(at=noon(DAY) + 9)
    assert _last_result_words(brain, asked) == "", "nothing said yet: no line, nothing asked"
    conversation(day, clock, (noon(DAY) + 1, "user", "chat", "Put the picture in a new Tab", {}),
                 (noon(DAY) + 3, "assistant", "chat", "It is in the Graphic Tab.", {"resourceIds": ["r1:o1"]}))
    line = _last_result_words(brain, asked)
    assert line.startswith('The last result the user saw or heard ("that", "it"): said: It is in the Graphic Tab.; '
                           "made or shown: r1:o1.")
    assert '"about_last": true' in line
    voice = _last_result_words(brain, asked, for_voice=True)
    assert voice == line[:len(voice)] and "about_last" not in voice
