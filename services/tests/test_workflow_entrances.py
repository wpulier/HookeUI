"""One saved workflow, one retained input, four entrances (Part 2.5, PR 3: workflows across entrances).

The integrated WorkflowService (orchestrator/workflow_service.py) is the one runner, attempt store and history; nothing
here ports or adds another. The same saved definition, run on the same retained input, is reached from the in-app brain
(its `workflow` tool, brain/toolbox/workflows.py), the studio's command path (POST /api/workflows/command, the manual
UI's), a researcher (its hands' `run` workflow operation, research/hands.py) and a connected agent through the door
(POST /api/agent/v1/operations tool "workflow", door/api.py, admitted through its Study). Each run is checked to be
the same frozen definition (id, revision, digest), the same exact input, executed by the same real steps
(workflow_execution.ScientificSteps, no stub) to the same result, all in the one store.

What the entrances do not share is asserted as it is, and reported (the coordinator's PR notes): the brain, the studio
and a researcher name a run by the caller's request identity, so the same request from any of them is the same run;
the door's runs are always its own (its request is scoped to its Study and actor). And a workflow saved in the studio
is not reachable from the door until the door associates it with the Project.

Real Project, session and operation stores, real door transport with synthetic owner consent; no model, camera or
live agent."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator import workflow_api
from orchestrator.day.store import SessionStore
from orchestrator.door import api as door_api
from orchestrator.origin import studio_origin
from orchestrator.scientific_comparisons import identity
from orchestrator.scientific_projects import ProjectStore
from tests.test_agent_door import BASE, HEADER, bearer, connect, window_key  # noqa: F401 (window_key: autouse)
from tests.test_agent_projects import run as door_run
from tests.test_workflows import graph
from tests.waiting import until

STUDIO = {"Origin": BASE, HEADER: "workflow"}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("HOOKE_STUDIO_TOKEN", "synthetic-owner-window")
    day = SessionStore(tmp_path / "sessions")
    hub = SimpleNamespace(clients=set(), resources=SimpleNamespace(projects=ProjectStore(tmp_path / "documents")))
    built = SimpleNamespace(paths=SimpleNamespace(home=tmp_path), day=day, hub=hub)
    app = FastAPI()
    door_api.register(app, built, studio_origin, HEADER)
    workflow_api.register(app, built, studio_origin, HEADER)
    with TestClient(app, base_url=BASE) as client:
        yield client, built


def settled(runner, run, session):
    """A run's status once no driver owns it (a driver started on the studio's or the door's loop runs there)."""
    until(lambda: runner.tasks.get((session, run)) is None or runner.tasks[(session, run)].done(), seconds=20)
    return runner.observed_status(run, session)


def by_door(client, headers, study, session, definition, ref, key):
    """The door's admission of the saved workflow in its Study (POST operations, tool "workflow"): the operation as
    admitted. Its run awaits the user's review in Hooke, so the operation is not waited to an end here."""
    response = client.post("/api/agent/v1/operations", headers=headers, json={"tool": "workflow", "requestKey": key, "args": {
        "study": study, "session": session, "workflow": definition["id"], "revision": definition["revision"], "inputs": [ref]}})
    assert response.status_code in (200, 202), response.text
    return response.json()["operation"]


def test_one_saved_workflow_on_one_retained_input_is_the_same_work_from_every_entrance(rig):
    from orchestrator.brain.registry import Registry, ToolContext
    from orchestrator.brain.toolbox.workflows import add_workflow_tools
    from orchestrator.research import ResearchManager
    from orchestrator.research.hands import Hands
    from orchestrator.research.manager import WorkerAttempt
    from tests.test_research import FakeHub
    from tests.test_research_hands import session as research_session

    client, built = rig
    # An agent-owned Project the user has open (the plan's step 9: the door continues in an open Project it owns).
    agent = connect(client)
    door = bearer(agent["token"])
    sid = door_run(client, door, "project.create", {"name": "Nuclei"}, "nuclei-project-001")["outcome"]["project"]["session"]
    built.day = SessionStore(built.day.root, session=sid)
    built.hub.execution_day = built.day
    runner = built.hub.workflow_service
    image = runner.workflows.store.create(sid, {"kind": "scene", "title": "Nuclei", "state": {}, "resources": [], "runtimes": []})
    ref = identity(image)

    # The saved workflow: made through the door, so the door may run it in this Project; every entrance reads one library.
    made = client.post("/api/agent/v1/workflows/command", headers=door,
                       json={"project": sid, "action": "create", "name": "Inspect", "graph": graph(), "request": "1" * 32})
    assert made.status_code == 200, made.text
    definition = made.json()["workflow"]
    assert client.get("/api/workflows").json()["workflows"][0]["id"] == definition["id"]
    start = {"action": "start", "workflow": definition["id"], "revision": definition["revision"], "inputs": [ref]}
    runs = {}

    # 1. The in-app brain: its workflow tool, from this Project's conversation.
    registry = Registry()
    add_workflow_tools(registry, SimpleNamespace(hub=built.hub, day=built.day))

    async def brain(request):
        call = registry.start("workflow", {"command": {**start, "request": request}},
                              ToolContext("voice", 1, None, "run my Inspect workflow on the nuclei", asked=built.day.here()))
        await call.task
        assert call.ok, (call.summary, call.error)
        run = call.values["run"]
        if (sid, run["id"]) in runner.tasks:
            await runner.tasks[(sid, run["id"])]  # its driver runs on this loop (the brain's), to its end
        return run

    runs["brain"] = asyncio.run(brain("2" * 32))

    # 2. The studio's command path, as the Workflows page sends it.
    studio = client.post("/api/workflows/command", headers=STUDIO, json={**start, "session": sid, "request": "3" * 32})
    assert studio.status_code == 200, studio.text
    runs["studio"] = studio.json()["run"]

    # 3. A researcher: its hands run the workflow command for it (research/hands.py), on its own attempt.
    manager = ResearchManager(FakeHub(), root=built.paths.home / "research")
    manager.hands = Hands(built.hub)
    researcher = research_session(sitting=sid)
    researcher._attempt = WorkerAttempt(0, {}, completion=SimpleNamespace(done=lambda: False),
                                        record={"dispatchedAt": time.time()})
    manager.sessions[researcher.rid] = researcher

    async def by_researcher(request):
        reply = await manager._request({"op": "run", "rid": researcher.rid, "operation": "workflow",
                                        "runId": researcher._attempt.id, "params": {**start, "request": request}})
        assert reply["ok"], reply
        run = reply["values"]["run"]
        if (sid, run["id"]) in runner.tasks:
            await runner.tasks[(sid, run["id"])]
        return run

    runs["researcher"] = asyncio.run(by_researcher("4" * 32))

    # 4. A connected agent through the door: admitted through a Study in this Project.
    study = client.post("/api/agent/v1/studies", headers=door, json={
        "session": sid, "requestKey": "nuclei-study-0001", "title": "Inspect the nuclei", "objective": "Inspect them",
        "criteria": "Review the evidence", "budgetUsd": 0, "inputs": [{k: ref[k] for k in ("id", "revision", "digest")}]})
    assert study.status_code == 200, study.text
    operation = by_door(client, door, study.json()["study"]["id"], sid, definition, ref, "nuclei-door-run-01")
    assert operation["execution"]["kind"] == "workflow" and operation["by"] == "agent:" + agent["connection"]["id"]
    runs["door"] = {"id": operation["execution"]["id"]}

    # The same work: one frozen definition, one exact input, the same real steps to the same result, in one store.
    status = {entrance: settled(runner, run["id"], sid) for entrance, run in runs.items()}
    first = status["brain"]
    for entrance, s in status.items():
        assert s["definition"] == first["definition"], entrance
        assert (s["definition"]["id"], s["definition"]["revision"]) == (definition["id"], definition["revision"])
        assert s["inputs"] == [ref], entrance
        assert [(a["node"], a["status"], a["output"]) for a in s["attempts"]] == [("check", "awaiting-review", ref)], entrance
        assert s["status"] == "awaiting-review", entrance
    assert {s["actor"] for s in status.values()} == {"agent:voice", "studio", "researcher:" + researcher.rid, "agent:" + agent["connection"]["id"]}
    listed = {r["id"] for r in client.get("/api/workflow-runs", params={"session": sid}).json()["runs"]}
    assert {r["id"] for r in runs.values()} <= listed  # every entrance's run is in the one history the studio lists

    # Identity: the brain, the studio and a researcher name a run by the request; the same request is the same run.
    again = client.post("/api/workflows/command", headers=STUDIO, json={**start, "session": sid, "request": "2" * 32})
    assert again.json()["run"]["id"] == runs["brain"]["id"]
    assert asyncio.run(by_researcher("3" * 32))["id"] == runs["studio"]["id"]
    # The door's run is its own: its request is scoped to its Study and actor, and its retry is that same run.
    retry = by_door(client, door, study.json()["study"]["id"], sid, definition, ref, "nuclei-door-run-01")
    assert retry["execution"]["id"] == runs["door"]["id"] and runs["door"]["id"] not in {runs[e]["id"] for e in ("brain", "studio", "researcher")}
    asyncio.run(runner.close())


def test_a_workflow_saved_in_the_studio_is_not_reachable_from_the_door_until_associated(rig):
    """A gap, asserted as it is (reported): the door lists and runs only the workflows created or duplicated through the
    door for that Project (door/projects.py `require_workflow`), and duplicate needs that association too, so a
    workflow the user saved in the studio (or the Assistant saved) can't be continued by a connected agent."""
    client, built = rig
    agent = connect(client)
    door = bearer(agent["token"])
    sid = door_run(client, door, "project.create", {"name": "Nuclei"}, "nuclei-project-002")["outcome"]["project"]["session"]
    saved = client.post("/api/workflows/command", headers=STUDIO,
                        json={"action": "create", "name": "Mine", "graph": graph(), "request": "5" * 32})
    assert saved.status_code == 200, saved.text
    workflow = saved.json()["workflow"]["id"]
    assert client.get(f"/api/agent/v1/workflows?session={sid}", headers=door).json()["workflows"] == []
    copied = client.post("/api/agent/v1/workflows/command", headers=door, json={
        "project": sid, "action": "duplicate", "workflow": workflow, "revision": 1, "name": "Mine", "request": "6" * 32})
    assert copied.status_code == 403 and copied.json()["code"] == "workflow-access-refused"
    asyncio.run(built.hub.workflow_service.close())
