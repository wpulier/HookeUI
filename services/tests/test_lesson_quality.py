"""Lessons look good (2026-10-11, live: a lesson said "The lesson is queued; its on-screen display is not yet confirmed",
listed its limitations twice and left out the user's own slide, and a restyle could only reorder text blocks). No app
state in what a researcher writes, the limitations once, the slide's own picture in the lesson, and a presentation's
look as real choices the studio applies. No model is called here."""
import asyncio
import json

import pytest

from research_worker.presentation import caveats, validate
from research_worker.presentation_lesson import PHOTO, prompt, validate as validate_lesson
from tests.test_research_content import LESSON, researcher

RESULT = {"summary": "Large thin-walled pith cells", "limitations": ["No scale or stain is known"],
          "report": {"methods": "Looked", "observations": "Seen", "measurements": "None",
                     "limitations": "No scale or stain is known; the walls are not resolved"}}
STEPS = {"steps": [{"id": "q1", "prompt": "What fills the centre?", "answer": "Pith", "hint": "the middle"}]}
PAGE = ('<!doctype html><html><head><title>t</title></head><body><h1>Pith</h1>'
        '<section id="q1">What fills the centre? Pith the middle</section></body></html>')


def test_make_lesson_never_tells_the_model_the_apps_state(tmp_path):
    r, _ = researcher(tmp_path, {"effort": "standard", "deliverable": "lesson", "independent_presentation": True})
    reply, _ = asyncio.run(r.tool_make_lesson(LESSON))
    said = json.dumps(reply).lower()
    assert reply["ok"] and "pending" not in said and "queued" not in said and "producer" not in said
    assert "not about the lesson or the app" in reply["next"]


def test_the_finish_asks_for_limitations_about_the_specimen_never_the_app():
    from research_worker.work import SCHEMAS

    finish = SCHEMAS["finish"]["parameters"]["properties"]
    assert "never the app" in finish["limitations"]["description"]
    assert "never the app's own state" in finish["report"]["description"]


def test_the_limitations_are_shown_once():
    allowed = ["summary", "limitations", "report:limitations"]
    assert caveats(allowed) == ["summary", "limitations"]  # the list stands for the report's section
    assert caveats(["summary", "report:limitations"]) == ["summary", "report:limitations"]
    plan = validate({"layout": "report", "blocks": [{"ref": "summary"}, {"ref": "report:limitations"}, {"ref": "limitations"}]},
                    RESULT)
    assert [b["ref"] for b in plan["blocks"]] == ["summary", "limitations"]  # the same caveats twice: once
    with pytest.raises(ValueError, match="limitations"):
        validate({"layout": "report", "blocks": [{"ref": "summary"}]}, RESULT)
    lesson = validate_lesson({"html": PAGE, "evidenceRefs": ["summary", "limitations"]}, RESULT, STEPS)
    assert lesson["evidenceRefs"] == ["summary", "limitations"]  # no longer made to cite both
    asked = prompt(RESULT, STEPS, "make it prettier")
    assert "Show the limitations once" in asked and '"report:limitations"]' not in asked.split("Required evidenceRefs")[1][:80]


def test_the_lesson_shows_the_users_own_slide():
    kept = validate_lesson({"html": PAGE, "evidenceRefs": ["summary", "limitations"]}, RESULT, STEPS, picture=True)
    assert kept["html"].count(PHOTO) == 1 and "Captured: your slide" in kept["html"]
    assert kept["html"].index(PHOTO) > kept["html"].index("<body>")  # in the page, at its top
    placed = PAGE.replace("<h1>Pith</h1>", f'<h1>Pith</h1><img src="{PHOTO}"><p>Captured</p>')
    again = validate_lesson({"html": placed, "evidenceRefs": ["summary", "limitations"]}, RESULT, STEPS, picture=True)
    assert again["html"] == placed  # the model placed it: left where it is
    without = validate_lesson({"html": PAGE, "evidenceRefs": ["summary", "limitations"]}, RESULT, STEPS)
    assert PHOTO not in without["html"]  # no picture kept: none promised
    assert "Show the student's own slide near the top" in prompt(RESULT, STEPS, "", picture=True)
    assert "own slide" not in prompt(RESULT, STEPS, "")


def test_a_lesson_job_carries_the_slide_when_the_researcher_kept_one(tmp_path, monkeypatch):
    from tests.test_research_presentations import setup

    async def scenario():
        manager, session, process, args = setup(tmp_path, monkeypatch)
        inputs = tmp_path / session.day / session.rid / "inputs"
        inputs.mkdir(parents=True, exist_ok=True)
        (inputs / ("crop.jpg" if session.has_crop else "frame.jpg")).write_bytes(b"\xff\xd8 slide")
        old = manager.presentations.current(session, "lesson")
        manager.presentations.begin(session.rid, "with-slide", **args, kind="lesson", expected_base=old["id"])
        await asyncio.sleep(0)
        assert session.presentation_jobs[-1]["picture"] is True
        assert process.calls[0][1]["picture"] is True
        manager.presentations.stop()

    asyncio.run(scenario())


def test_a_presentations_look_is_checked_choices():
    blocks = [{"ref": "summary"}, {"ref": "limitations"}, {"ref": "report:observations"}]
    plan = validate({"layout": "briefing", "blocks": blocks,
                     "style": {"theme": "paper", "density": "spacious", "pictures": "lead", "emphasis": ["report:observations"]}},
                    RESULT)
    assert plan["style"] == {"theme": "paper", "density": "spacious", "pictures": "lead", "emphasis": ["report:observations"]}
    for bad in ({"theme": "neon"}, {"emphasis": ["details"]}, {"emphasis": ["summary"] * 4}, {"font": "serif"}):
        with pytest.raises(ValueError, match="style|emphasis"):
            validate({"layout": "briefing", "blocks": blocks, "style": bad}, RESULT)
    assert "style" not in validate({"layout": "report", "blocks": blocks}, RESULT)
