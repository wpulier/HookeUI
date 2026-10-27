import asyncio
import json

from fastapi.testclient import TestClient

from orchestrator import execution, tools
from orchestrator.brain.registry import Tool, schema
from orchestrator.paths import Paths
from orchestrator.server import create_app
from tests.test_brain import make
from tests.test_outline_seeded import FakeHub, SQUARE


def test_seeded_refinement_receipt_names_the_retained_seed(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test")

    async def empty(*args):
        return {"polygons": [], "score": 0, "ms": 15}

    monkeypatch.setattr(tools, "_segment_fal", empty)
    log = execution.ExecutionLog(enabled=True)

    async def go():
        with log.request("click") as root:
            result = await tools.segment(FakeHub([SQUARE]), "edgetam>sam3.1-fal", 1, b"", 120, 215)
        return root, result

    root, result = asyncio.run(go())
    assert result["polygons"] == [SQUARE]
    rows = log.inspect(root.id)["steps"]
    # The runner's receipt (operations.py): the operation, its attempt, the composite's two steps.
    by = {r["selected"]: r for r in rows if r.get("selected")}
    operation = rows[0]
    assert operation["selected"] == "edgetam>sam3.1-fal" and operation["operation"] == "outline"
    assert {"edgetam", "sam3.1-fal"} <= set(by)
    assert operation["fallback"] == "empty-refinement" and operation["producer"] == by["edgetam"]["id"]
    assert by["sam3.1-fal"]["outcome"] == "empty"
    assert result["madeBy"]["implementation"] == "edgetam" and result["madeBy"]["fallbackFrom"] == "edgetam>sam3.1-fal"


def test_supplied_box_skips_seed_in_receipt(monkeypatch):
    from orchestrator.operations import INSTALLED

    monkeypatch.setitem(INSTALLED, "sam3", lambda: "")  # FakeHub's SAM worker is available for this receipt test.
    log = execution.ExecutionLog(enabled=True)

    async def go():
        with log.request("click") as root:
            await tools.segment(FakeHub([SQUARE]), "edgetam>sam3.1-local", 1, b"", 0, 0, box=(10, 10, 50, 50))
        return root

    root = asyncio.run(go())
    rows = log.inspect(root.id)["steps"]
    by = {r["selected"]: r for r in rows if r.get("selected")}
    assert rows[0]["selected"] == "edgetam>sam3.1-local" and "edgetam" not in by  # a box needs no seed
    assert rows[0]["producer"] == by["sam3.1-local"]["id"]


def test_tool_receipts_have_individual_ids_and_never_enter_context():
    async def select(ctx, args):
        return "selected it"

    reply = {"answer": "Selecting it.", "targets": [], "actions": [
        {"tool": "select", "args": {}}, {"tool": "select", "args": {}}]}
    baseline, _, baseline_agent = make([reply], [Tool("select", "", schema({}), select)])
    asyncio.run(baseline.turn("select it twice"))
    brain, hub, agent = make([reply], [Tool("select", "", schema({}), select)])
    hub.execution = execution.ExecutionLog(enabled=True)
    result = asyncio.run(brain.turn("select it twice"))
    assert agent.calls == baseline_agent.calls
    assert [t.text for t in brain.conversation.turns] == [t.text for t in baseline.conversation.turns]
    assert brain.view_note() == baseline.view_note()
    assert brain.voice_recap() == baseline.voice_recap()
    assert "execution" not in repr(result)
    root = hub.execution.references()[0]
    assert root["turnId"] == result["turnId"]
    rows = [r for r in hub.execution.inspect(root["id"])["steps"] if r["operation"] == "tool:select"]
    assert len(rows) == 2 and rows[0]["id"] != rows[1]["id"]
    assert all(r["outcome"] == "completed" for r in rows)


def test_inspection_is_read_only_and_blind_routes_remain_hidden(tmp_path):
    api = create_app("http://127.0.0.1:1", use_gpu=False, paths=Paths.under(tmp_path))
    built = api.state.app
    log = execution.ExecutionLog(enabled=True)
    built.hub.execution = log
    before = built.dialogue.history()
    with log.request("click", thread=built.day.active_id) as root:
        execution.bind(trial_id="t1")
        with execution.span("segment", model="edgetam"):
            pass
    client = TestClient(api)  # no lifespan: no servers, camera or GPU startup
    response = client.get(f"/api/execution/{root.id}")
    assert response.status_code == 200 and response.json()["status"] == "blind"
    assert "edgetam" not in response.text
    log.reveal("t1")
    assert client.get(f"/api/execution/{root.id}").json()["steps"][0]["model"] == "edgetam"
    assert built.dialogue.history() == before
    assert client.get("/api/execution/missing").json()["status"] == "expired"


def test_snapshot_provenance_is_separate_from_replay_and_cleared_day_is_filtered(tmp_path):
    api = create_app("http://127.0.0.1:1", use_gpu=False, paths=Paths.under(tmp_path))
    built = api.state.app
    log = built.hub.execution = execution.ExecutionLog(enabled=True)
    with log.request("click", day=built.day.day, thread=built.day.active_id, generation=built.day.generation - 1):
        pass
    with execution.request(built.hub, "click") as current:
        pass
    refs = [m for m in built.snapshot() if m["type"] == "execution-list"]
    assert [r["id"] for r in refs[0]["entries"]] == [current.id]
    assert "execution" not in repr(built.dialogue.history())
