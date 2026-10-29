"""Who's powering what (Part 2.5; orchestrator/models_in_use.py). Live 2026-10-28, asked "what models or capabilities do
you have access to", the voice answered "Gemini and GPT": no tool said which model does which job. Now one tool and the
performance meter's panel name each job's model, where it runs, its recent speed and where to change it, from the same
settings the work reads. A real app (create_app), no camera, no provider call."""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from tests.test_resources import app_at

JOBS = ("Thinking (the brain)", "Talking (the voice)", "Thinking behind the voice", "Looking closely (for the voice)",
        "Seeing (the eyes)", "Running look", "Quick decisions", "Pointing", "Outlining", "Cells", "Look-alikes", "Researching", "Presentation", "Pictures", "Video")


def ask(built, name="models_in_use"):
    from orchestrator.brain.registry import ToolContext

    async def go():
        call = built.brain.registry.start(name, {}, ToolContext("voice", 1, None, "what models are powering you right now?",
                                                                asked=built.day.here()))
        await call.task
        return call

    return asyncio.run(go())


def test_what_models_are_powering_you_names_each_jobs_model_and_where_it_runs(tmp_path):
    from orchestrator.options import BRAIN, VISION
    from research_worker import api_usage

    api_usage._latency.clear()  # this process's calls so far are other tests'
    web = app_at(tmp_path)
    built = web.state.app
    assert "models_in_use" in built.brain.registry.tools  # before: there was no such tool, only the whole catalogue
    settings = built.hub.settings
    settings.update({"outline": "sam2.1-tiny"})
    api_usage.note_latency(VISION[settings.vision].spec.split(":", 1)[1].split("@")[0], 1800)
    api_usage.note_latency("outline:sam2.1-tiny", 70)
    call = ask(built)
    assert call.ok, call.error
    said = call.summary
    lines = {line.split(":", 1)[0].lstrip("- "): line for line in said.splitlines() if line.startswith("- ")}
    assert list(lines) == list(JOBS)
    seeing = VISION[settings.vision].label
    assert lines["Thinking (the brain)"].startswith(f"- Thinking (the brain): {seeing} (cloud), acting by: {BRAIN[settings.brain].label}")
    # The default voice is the live partner (GPT Realtime 2.1; Will, 29 Oct: "dont default to anything old").
    assert "Talking (the voice): One brain (hears · sees · acts) (cloud)" in lines["Talking (the voice)"]
    # The one-brain voice's thinking (think.py), and how to change it: "switch your brain to Claude".
    assert "Thinking behind the voice: Claude Opus 5.5 (cloud)" in lines["Thinking behind the voice"]
    assert "use <model> from now on" in lines["Thinking behind the voice"]
    # The partner's close look (partner_tools.look_hard), by default Gemini 4 Argon.
    assert "Looking closely (for the voice): Gemini 4 Argon (cloud)" in lines["Looking closely (for the voice)"]
    assert "use <model> from now on" in lines["Looking closely (for the voice)"]
    assert "Outlining: SAM 2.1 tiny (this Mac)" in lines["Outlining"] and "70 ms" in lines["Outlining"]
    assert "Cells: Cellpose-SAM (this Mac)" in lines["Cells"] and "no calls in the last 10 minutes" in lines["Cells"]
    assert "1.8 s" in lines["Seeing (the eyes)"] and "use <model> from now on" in lines["Seeing (the eyes)"]
    assert "Video: Veo 3.1 Fast (cloud)" in lines["Video"] and "fixed in the code" in lines["Video"]
    # The tool reads the settings for its list, never as the work in progress's reads (the acting bench's check).
    assert call.reads == []


def test_the_performance_meters_panel_has_one_row_per_job_from_the_same_builder(tmp_path):
    from orchestrator import models_in_use

    web = app_at(tmp_path)
    built = web.state.app
    with TestClient(web) as client:
        rows = client.get("/api/health").json()["models"]["inUse"]
    assert [r["title"] for r in rows] == list(JOBS)
    assert rows == models_in_use.rows(built.hub.settings)
    for row in rows:
        assert {"role", "model", "where", "speedWords", "change"} <= set(row)
    assert {r["role"]: r["where"] for r in rows}["cells"] == "this Mac"
