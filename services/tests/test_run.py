"""A run on finished work (research/run.py): a follow-up answer or a new version runs on top
of the work and leaves it as it was. Before, five fields had to agree in eleven places."""
import asyncio
import json

from orchestrator.research import ResearchManager, Session
from tests.test_admission import manager_with
from tests.test_research import FakeHub


def finished(manager):
    s = Session(rid="r7", n=7, brief="about stomata", deliverable="research", effort="standard", engine="gpt-6.1-sol",
                purpose="research", label="stomata", object_id=None, origin={}, day=manager.today(), status="done",
                result={"title": "Stomata", "summary": "Pores."}, root=manager.root)
    manager.sessions[s.rid] = s
    return s


def test_while_it_answers_the_brain_and_the_studio_see_it(tmp_path):
    manager = manager_with(tmp_path)
    s = finished(manager)
    asyncio.run(manager.followup("r7", "what opens them?"))
    assert s.status == "queued" and s.run.kind == "answer"
    assert 'answering a follow-up question ("what opens them?")' in s.line()
    wire = s.wire()
    assert wire["answering"] == "what opens them?" and wire["seconds"] == 75  # the answer's time, not standard's 360
    saved = json.loads((tmp_path / s.day / "r7" / "session.json").read_text())
    assert saved["status"] == "done" and saved["canFollowUp"] is True  # on disk it is still the finished work


def test_the_run_ends_in_its_history_and_the_work_stands(tmp_path):
    manager = manager_with(tmp_path)
    s = finished(manager)
    asyncio.run(manager.followup("r7", "what opens them?"))
    s.status = "running"
    manager._finish(s, "done", {"one_liner": "Turgor in the guard cells."})
    assert s.status == "done" and s.run is None and s.result["summary"] == "Pores."
    assert s.answered["result"]["one_liner"] == "Turgor in the guard cells." and s.revised is None
    assert s.history[-1]["mode"] == "answer"


def test_an_answer_cut_off_by_a_restart_is_remembered(tmp_path):
    manager = manager_with(tmp_path)
    s = finished(manager)
    asyncio.run(manager.followup("r7", "what opens them?"))
    again = ResearchManager(FakeHub(), root=tmp_path, today=manager.today)  # the app restarted mid-answer
    back = again.get("r7")
    assert back.status == "done" and back.run is None and back.wire()["answering"] is None
    assert back.history[-1] == {**back.history[-1], "mode": "answer", "brief": "what opens them?", "status": "interrupted"}
    assert "stopped when the app restarted" in back.step
