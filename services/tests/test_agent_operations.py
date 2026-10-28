"""Outside agents' operations (ADR 0026): admitted to disk first, one request per key, restarts and cancels said truly."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from orchestrator.door import actor, api, schema
from orchestrator.door.connections import Refused
from orchestrator.door.operations import Operations, request_digest
from orchestrator.resources import maker_of
from tests.test_agent_door import BASE, bearer, build, connect, refused, window_key  # noqa: F401 (autouse)

AGENT = "agent:ac_" + "1" * 24
KEY = "first-request-0001"


def admit(ops, work, key=KEY, params=None):
    return ops.admit(by=AGENT, label="Claude Code", tool="example", project="example", input_revision="r1",
                     generation="abc.0123456789abcdef", params=params or {}, request_key=key, work=work)


async def settle(ops):
    while ops._tasks:
        await asyncio.sleep(0.01)


def test_admission_is_on_disk_before_work_starts_and_a_key_means_one_request(tmp_path):
    async def go():
        ops = Operations(tmp_path)
        seen = {}

        async def work(record, progress):
            seen["file"] = json.loads((tmp_path / "agents" / "operations" / f"{record['id']}.json").read_text())
            await progress(1, 1, "done")
            return {"says": "ok"}

        first, made = await admit(ops, work)
        assert made and first["state"] == "admitted"
        await settle(ops)
        assert seen["file"]["requestKey"] == KEY  # the record existed before the work ran
        again, made_again = await admit(ops, work)
        assert not made_again and again["id"] == first["id"] and again["state"] == "succeeded"
        with pytest.raises(Refused) as conflict:
            await admit(ops, work, params={"different": True})
        assert conflict.value.status == 409 and conflict.value.code == "request-key-conflict"
        with pytest.raises(Refused) as bad:
            await admit(ops, work, key="short")
        assert bad.value.code == "bad-request-key"
        done = ops.get(first["id"])
        assert done["outcome"] == {"says": "ok"} and done["progress"]["says"] == "done"
        assert schema.problems(done, "operation") == []
        assert "requestSha256" not in done
    asyncio.run(go())


def test_the_digest_binds_every_field():
    base = request_digest("example", "p", "r1", "g", {"a": 1})
    for changed in (request_digest("other", "p", "r1", "g", {"a": 1}), request_digest("example", "q", "r1", "g", {"a": 1}),
                    request_digest("example", "p", "r2", "g", {"a": 1}), request_digest("example", "p", "r1", "h", {"a": 1}),
                    request_digest("example", "p", "r1", "g", {"a": 2})):
        assert changed != base
    assert request_digest("example", "p", "r1", "g", {"b": 2, "a": 1}) == request_digest("example", "p", "r1", "g", {"a": 1, "b": 2})


def test_a_restart_marks_unfinished_work_interrupted_and_keeps_finished_work(tmp_path):
    folder = tmp_path / "agents" / "operations"
    folder.mkdir(parents=True)
    base = {"requestKey": "key-00000001", "requestSha256": "x", "tool": "example", "project": "p", "by": AGENT,
            "at": 1.0, "params": {}, "outcome": None}
    (folder / ("op_" + "a" * 24 + ".json")).write_text(json.dumps({**base, "id": "op_" + "a" * 24, "state": "running"}))
    (folder / ("op_" + "b" * 24 + ".json")).write_text(json.dumps({**base, "id": "op_" + "b" * 24, "requestKey": "key-00000002",
                                                                  "state": "succeeded", "outcome": {"says": "kept"}}))
    (folder / ("op_" + "c" * 24 + ".json")).write_text("{damaged")
    ops = Operations(tmp_path)
    interrupted = ops.get("op_" + "a" * 24)
    assert interrupted["state"] == "interrupted" and "Hooke stopped while this ran" in interrupted["outcome"]["says"]
    assert json.loads((folder / ("op_" + "a" * 24 + ".json")).read_text())["state"] == "interrupted"  # said on disk too
    assert ops.get("op_" + "b" * 24)["outcome"] == {"says": "kept"}
    assert ops.operations()["unreadable"] == ["op_" + "c" * 24 + ".json"]
    assert (folder / ("op_" + "c" * 24 + ".json")).read_text() == "{damaged"  # never overwritten


def test_cancel_is_requested_until_the_work_says_it_stopped(tmp_path):
    async def go():
        ops = Operations(tmp_path)
        started = asyncio.Event()

        async def work(record, progress):
            started.set()
            await asyncio.sleep(30)

        record, _ = await admit(ops, work)
        await started.wait()
        asked = await ops.cancel(record["id"])
        assert asked["state"] == "cancelRequested"
        await settle(ops)
        assert ops.get(record["id"])["state"] == "cancelled"
    asyncio.run(go())


def test_failures_are_said_as_they_happened(tmp_path):
    async def go():
        ops = Operations(tmp_path)

        async def refuses(record, progress):
            raise Refused(409, "measurements-unavailable", "Gone.", next="reopen it")

        async def breaks(record, progress):
            raise RuntimeError("the worker crashed")

        a, _ = await admit(ops, refuses, key="key-refuses-1")
        b, _ = await admit(ops, breaks, key="key-breaks-01")
        await settle(ops)
        assert ops.get(a["id"])["outcome"] == {"code": "measurements-unavailable", "says": "Gone.", "next": "reopen it"}
        assert ops.get(b["id"])["state"] == "failed" and ops.get(b["id"])["outcome"]["says"] == "the worker crashed"
    asyncio.run(go())


def test_an_agents_work_is_made_by_it_by_its_label():
    actor.remember("ac_" + "1" * 24, "Claude Code")
    assert maker_of("population", "p1", {"actor": AGENT}) == {"kind": "agent", "id": "ac_" + "1" * 24, "name": "Claude Code"}
    assert maker_of("population", "p1", {"actor": "agent:ac_" + "9" * 24})["name"] == "An agent"
    assert maker_of("population", "p1", {"actor": "you"})["kind"] == "you"
    assert maker_of("population", "p1", {"by": "agent:chat"})["kind"] == "assistant"  # the brain's own, unchanged


def test_the_door_starts_the_example_once_per_key_and_opens_only_known_pages(tmp_path, monkeypatch):
    from orchestrator.door import example
    calls = []

    async def fake_run(app, record, progress):
        calls.append(record["by"])
        await progress(3, 3, "Reading the measurements")
        return {"says": "Found 271 regions in the example image.", "count": 271,
                "page": "#measurements/p" + "0" * 32 + "?session=s&revision=0"}

    monkeypatch.setattr(example, "run", fake_run)
    monkeypatch.setattr(api, "example_identity", lambda: ("r1", "abc.0123456789abcdef"))
    monkeypatch.setattr(api, "listing", lambda built: [])
    app, built, _ = build(tmp_path)
    with TestClient(app, base_url=BASE) as client:
        claimed = connect(client)
        token, cid = claimed["token"], claimed["connection"]["id"]
        refused(client.post("/api/agent/v1/operations", json={"tool": "outline", "requestKey": KEY}, headers=bearer(token)),
                404, "unknown-capability")
        refused(client.post("/api/agent/v1/operations", json={"tool": "example", "args": {"x": 1}, "requestKey": KEY},
                            headers=bearer(token)), 400, "bad-request")
        started = client.post("/api/agent/v1/operations", json={"tool": "example", "requestKey": KEY}, headers=bearer(token))
        assert started.status_code == 202 and started.json()["made"] is True
        op = started.json()["operation"]
        for _ in range(100):
            got = client.get(f"/api/agent/v1/operations/{op['id']}", headers=bearer(token)).json()["operation"]
            if got["state"] == "succeeded":
                break
        assert got["outcome"]["count"] == 271 and got["by"] == f"agent:{cid}" and calls == [f"agent:{cid}"]
        again = client.post("/api/agent/v1/operations", json={"tool": "example", "requestKey": KEY}, headers=bearer(token))
        assert again.status_code == 200 and again.json()["made"] is False and calls == [f"agent:{cid}"]
        listed = client.get("/api/agent/v1/operations", headers=bearer(token)).json()
        assert [o["id"] for o in listed["operations"]] == [op["id"]]

        refused(client.post("/api/agent/v1/open", json={"page": "javascript:alert(1)"}, headers=bearer(token)), 400, "bad-page")
        page = got["outcome"]["page"]
        nobody = client.post("/api/agent/v1/open", json={"page": page}, headers=bearer(token)).json()
        assert nobody["shown"] is False and "No Hooke window" in nobody["says"]

        async def opened(client_, page_):
            return {"visible": True}
        built.open_page = opened
        assert client.post("/api/agent/v1/open", json={"page": page}, headers=bearer(token)).json()["shown"] is True

        async def background(client_, page_):
            return {"visible": False}
        built.open_page = background
        assert client.post("/api/agent/v1/open", json={"page": page}, headers=bearer(token)).json()["shown"] is False

        refused(client.get(f"/api/agent/v1/operations/{op['id']}"), 401, "not-connected")


def test_a_cancel_right_after_admission_never_damages_the_record_or_frees_its_key(tmp_path):
    """Review B2: a cancel's write and the work's progress writes ran at once through one shared temporary file, so a
    record could be damaged; after a restart its key was free and the same key made a second operation."""
    async def go():
        ops = Operations(tmp_path)

        async def busy(record, progress):
            for step in range(50):
                await progress(step, 50, f"step {step}")
            return {"says": "done"}

        made = []
        for n in range(20):
            record, _ = await admit(ops, busy, key=f"concurrent-{n:04d}")
            await ops.cancel(record["id"])
            made.append(record)
        await settle(ops)
        return made

    made = asyncio.run(go())
    again = Operations(tmp_path)  # a restart
    assert again.operations(limit=100)["unreadable"] == []
    assert not list((tmp_path / "agents" / "operations").glob("*.tmp"))
    for n, record in enumerate(made):
        assert again.get(record["id"])["state"] in ("cancelled", "succeeded")

        async def nothing(record_, progress):
            return {}
        same, made_now = asyncio.run(admit(again, nothing, key=f"concurrent-{n:04d}"))
        assert not made_now and same["id"] == record["id"]  # the key still means that one request


def test_a_cancel_before_or_at_the_start_ends_the_record_as_cancelled(tmp_path):
    """Review B1: `_run` overwrote cancelRequested with running, and a cancel landing on its first save left the
    record running until a restart, with its task still listed."""
    async def go():
        ops = Operations(tmp_path)

        async def slow(record, progress):
            await asyncio.sleep(30)

        before, _ = await admit(ops, slow, key="cancel-before-start")
        asked = await ops.cancel(before["id"])  # before the task has run at all
        assert asked["state"] == "cancelRequested"
        at_start, _ = await admit(ops, slow, key="cancel-at-the-start")
        await asyncio.sleep(0)  # the task begins and reaches its first save
        await ops.cancel(at_start["id"])
        await settle(ops)
        for _ in range(20):
            await asyncio.sleep(0.01)  # the ending's own write
        return ops, before["id"], at_start["id"]

    ops, first, second = asyncio.run(go())
    for op_id in (first, second):
        assert ops.get(op_id)["state"] == "cancelled"
        assert op_id not in ops._tasks
        assert Operations(tmp_path).get(op_id)["state"] == "cancelled"  # and so on disk


def test_the_example_never_takes_over_what_the_researcher_sees(tmp_path, monkeypatch):
    """Review B3: the example opens its own Project and stage. It runs only when that takes nothing from anyone: Talk
    isn't live, and no Hooke window shows a Project with the researcher's work (empty and the example's own are fine).
    A refusal records no operation."""
    from types import SimpleNamespace
    from orchestrator import examples
    from orchestrator.door import example

    studio = SimpleNamespace(kind="studio")
    def app(day, clients=(studio,), voice=None):
        return SimpleNamespace(day=day, hub=SimpleNamespace(clients=list(clients)), brain=SimpleNamespace(voice=voice))

    empty = SimpleNamespace(sources={}, on_stage=None, ids_used={}, last_user=0, guide=None, name="")
    working = SimpleNamespace(sources={"a1": 1.0}, on_stage="a1", ids_used={"e": 2}, last_user=5.0, guide=None,
                              name="Blood smear")
    own = SimpleNamespace(**{**vars(working), "guide": examples.GUIDE})
    assert example.takeover(app(empty)) is None
    assert example.takeover(app(own)) is None
    assert example.takeover(app(working, clients=())) is None  # no window: nobody's view
    assert example.takeover(app(working, clients=(SimpleNamespace(kind="script"),))) is None  # a script isn't a view
    busy = example.takeover(app(working))
    assert busy.code == "hooke-busy" and "Blood smear" in busy.says
    talking = example.takeover(app(empty, voice=SimpleNamespace(closed=False)))
    assert talking.code == "hooke-busy" and "Talk" in talking.says

    # Through the door: refused before anything is admitted.
    monkeypatch.setattr(api, "example_identity", lambda: ("r1", "abc.0123456789abcdef"))
    web, built, _ = build(tmp_path)
    built.day = working
    with TestClient(web, base_url=BASE) as client:
        token = connect(client)["token"]
        refused(client.post("/api/agent/v1/operations", json={"tool": "example", "requestKey": KEY}, headers=bearer(token)),
                409, "hooke-busy")
        assert client.get("/api/agent/v1/operations", headers=bearer(token)).json()["operations"] == []


def test_hooke_stopping_interrupts_work_it_never_calls_it_cancelled(tmp_path):
    """Found by test_hooke_command: a server shutdown cancels in-flight tasks, and that was recorded as "cancelled",
    though nobody asked. Only a cancel someone asked for is cancelled; Hooke stopping is interrupted."""
    async def go():
        ops = Operations(tmp_path)
        started = asyncio.Event()

        async def slow(record, progress):
            started.set()
            await asyncio.sleep(30)

        record, _ = await admit(ops, slow, key="shutdown-0001")
        await started.wait()
        for task in list(ops._tasks.values()):
            task.cancel()  # what the server's shutdown does
        await settle(ops)
        for _ in range(20):
            await asyncio.sleep(0.01)
        return ops, record["id"]

    ops, op_id = asyncio.run(go())
    done = ops.get(op_id)
    assert done["state"] == "interrupted" and "Hooke stopped" in done["outcome"]["says"]


CAPABILITY = {"id": "measure-things", "name": "Measure things", "task": "Measures what is open", "description": "",
              "inputs": [{"type": "workspace", "label": "the open Project"}], "outputs": [],
              "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
              "credit": {}, "licence": [], "generation": "app-0123.0123456789abcdef", "state": "available"}


def door_with_capability(tmp_path, monkeypatch, ran):
    from orchestrator.door import work

    async def fake(app, record, progress, tool, preset, args):
        ran.append((tool, preset, args, record["by"], record["project"], record["inputRevision"], record["generation"]))
        await asyncio.sleep(0.2)
        return {"says": "Measured 3 things.", "project": record["project"]}

    monkeypatch.setattr(work, "run_capability", fake)
    from orchestrator.door import listing as door_listing
    monkeypatch.setattr(door_listing, "model_places", lambda built: {"a-hosted-model": "cloud"})
    monkeypatch.setattr(api, "listing", lambda built: [
        {**CAPABILITY, "inputSchema": {"type": "object", "properties": {"size": {"type": "integer"}}, "required": [],
                                       "additionalProperties": False}},
        {**CAPABILITY, "id": "needs-setup", "state": "unavailable", "why": "Download 16 MB", "next": "Download it"}])
    web, built, _ = build(tmp_path)
    built.capability_status = lambda: {"capabilities": [
        {"id": "measure-things", "actor": "agent", "how": {"tool": "measure", "args": {"what": "all"}}}]}
    built.day.on_stage = "asset-1"
    return web, built


def test_any_listed_capability_runs_only_in_the_agents_own_open_project(tmp_path, monkeypatch):
    """The door names no capability: `tool` is any id of the listing. It runs only in a Project the calling agent made,
    named by id and open: never the researcher's, never whatever is open (review: with no window, `run` used to act on
    the researcher's last Project). Arguments must match its schema. Each run is admitted with that Project, its stage
    and the generation, runs by its own tool as the agent, and a retry finds it even while Hooke is busy."""
    from orchestrator.door import work
    ran = []
    web, built = door_with_capability(tmp_path, monkeypatch, ran)
    with TestClient(web, base_url=BASE) as client:
        claimed = connect(client)
        token, cid = claimed["token"], claimed["connection"]["id"]
        actor = f"agent:{cid}"
        post = lambda body: client.post("/api/agent/v1/operations", json=body, headers=bearer(token))
        refused(post({"tool": "nothing-like-it", "requestKey": KEY}), 404, "unknown-capability")
        not_ready = refused(post({"tool": "needs-setup", "requestKey": KEY}), 409, "not-ready")
        assert "Download 16 MB" in not_ready["says"] and not_ready["next"] == "Download it"
        refused(post({"tool": "measure-things", "requestKey": KEY}), 400, "project-required")
        # The researcher's open Project, with no window open: not the agent's, so nothing runs there.
        refused(post({"tool": "measure-things", "project": built.day.sid, "requestKey": KEY}), 403, "not-your-project")
        work._remember_project(built, "2026-10-28.9", "agent:ac_" + "9" * 24)  # another agent's Project
        built.day.sid = "2026-10-28.9"
        refused(post({"tool": "measure-things", "project": "2026-10-28.9", "requestKey": KEY}), 403, "not-your-project")
        work._remember_project(built, "2026-10-28.7", actor)  # its own, but not the one open
        refused(post({"tool": "measure-things", "project": "2026-10-28.7", "requestKey": KEY}), 409, "project-not-open")
        built.day.sid = "2026-10-28.7"
        refused(post({"tool": "measure-things", "project": "2026-10-28.7", "args": {"secret": 1}, "requestKey": KEY}),
                400, "bad-arguments")
        refused(post({"tool": "measure-things", "project": "2026-10-28.7", "args": {"size": "a-hosted-model"},
                      "requestKey": KEY}), 409, "cloud-argument")
        started = post({"tool": "measure-things", "project": "2026-10-28.7", "args": {"size": 3}, "requestKey": KEY})
        assert started.status_code == 202, started.text
        op = started.json()["operation"]
        # A retry while Hooke is busy finds its work, never a refusal.
        built.day.sid = "2026-10-28.1"
        again = post({"tool": "measure-things", "project": "2026-10-28.7", "args": {"size": 3}, "requestKey": KEY})
        assert again.status_code == 200 and again.json()["operation"]["id"] == op["id"]
        refused(post({"tool": "measure-things", "project": "2026-10-28.7", "args": {"size": 4}, "requestKey": KEY}),
                409, "request-key-conflict")
        for _ in range(200):
            got = client.get(f"/api/agent/v1/operations/{op['id']}", headers=bearer(token)).json()["operation"]
            if got["state"] == "succeeded":
                break
            import time; time.sleep(0.01)
        assert got["outcome"]["says"] == "Measured 3 things."
        assert ran == [("measure", {"what": "all"}, {"size": 3}, actor, "2026-10-28.7", "asset-1",
                        "app-0123.0123456789abcdef")]


def test_a_run_can_be_cancelled(tmp_path, monkeypatch):
    from orchestrator.door import work
    ran = []
    web, built = door_with_capability(tmp_path, monkeypatch, ran)

    async def slow(app, record, progress, tool, preset, args):
        await asyncio.sleep(30)
    monkeypatch.setattr(work, "run_capability", slow)
    with TestClient(web, base_url=BASE) as client:
        claimed = connect(client)
        token, actor = claimed["token"], "agent:" + claimed["connection"]["id"]
        work._remember_project(built, built.day.sid, actor)
        op = client.post("/api/agent/v1/operations", json={"tool": "measure-things", "project": built.day.sid,
                                                            "requestKey": KEY}, headers=bearer(token)).json()["operation"]
        asked = client.post(f"/api/agent/v1/operations/{op['id']}/cancel", headers=bearer(token)).json()["operation"]
        assert asked["state"] == "cancelRequested"
        for _ in range(200):
            got = client.get(f"/api/agent/v1/operations/{op['id']}", headers=bearer(token)).json()["operation"]
            if got["state"] == "cancelled":
                break
            import time; time.sleep(0.01)
        assert got["state"] == "cancelled"


def test_a_real_run_on_the_rig_is_the_agents_and_stays_in_its_project(tmp_path):
    """Unmocked: the real app (tests/capability_rig.py: stand-in workers and models), a Project the agent made, a
    capability picked from the real listing and run by its own tool. Its execution is recorded as the agent's, never as
    "you", and its outcome carries the measurements' preview and page."""
    from orchestrator.door import work
    from orchestrator.door.listing import everything
    from orchestrator.operations import runner
    from tests.capability_rig import Rig
    rig = Rig(tmp_path)
    app = rig.app
    actor = "agent:ac_" + "4" * 24
    work._remember_project(app, app.day.sid, actor)
    rig.still()
    listed = {t["id"]: t for t in everything(app, {"revision": "abc", "sourceDigest": "f" * 64})}
    rows = {r["id"]: r for r in app.capability_status()["capabilities"] if r.get("actor") == "agent"}
    pick = next(cid for cid, r in rows.items()
                if (r.get("how") or {}).get("tool") == "outline_cells" and listed.get(cid, {}).get("state") == "available")
    how = rows[pick]["how"]
    record = {"by": actor, "project": app.day.sid, "inputRevision": app.day.on_stage, "capabilityName": listed[pick]["name"]}
    steps = []

    async def progress(done, of, says):
        steps.append(says)

    outcome = asyncio.run(work.run_capability(app, record, progress, how["tool"], dict(how.get("args") or {}), {}))
    assert outcome["project"] == app.day.sid and outcome["count"] > 0 and outcome["page"].startswith("#measurements/")
    assert len(outcome["preview"]) <= 5
    assert steps[0] == f"Running {listed[pick]['name']}"
    made = runner(app.hub).executions
    assert outcome["executions"] and all(made[e].actor == actor for e in outcome["executions"])


def test_open_takes_a_real_file_and_never_takes_over_the_screen(tmp_path, monkeypatch):
    from orchestrator.door import work
    opened = []

    async def fake(app, record, progress, path):
        opened.append(path)
        return {"says": "Opened it.", "project": "2026-10-28.2"}

    monkeypatch.setattr(work, "open_file", fake)
    picture = tmp_path / "cells.png"
    picture.write_bytes(b"not empty")
    (tmp_path / "notes.txt").write_text("private")
    web, built, _ = build(tmp_path)
    with TestClient(web, base_url=BASE) as client:
        token = connect(client)["token"]
        refused(client.post("/api/agent/v1/operations", json={"tool": "open", "args": {"file": "cells.png"},
                                                               "requestKey": KEY}, headers=bearer(token)), 400, "bad-request")
        refused(client.post("/api/agent/v1/operations", json={"tool": "open", "args": {"file": str(tmp_path / "gone.png")},
                                                               "requestKey": KEY}, headers=bearer(token)), 404, "no-such-file")
        refused(client.post("/api/agent/v1/operations", json={"tool": "open", "args": {"file": str(tmp_path / "notes.txt")},
                                                               "requestKey": KEY}, headers=bearer(token)), 400, "not-a-picture")
        monkeypatch.setattr(work, "takeover", lambda app, actor=None: Refused(409, "hooke-busy", "Busy.", next="wait"))
        refused(client.post("/api/agent/v1/operations", json={"tool": "open", "args": {"file": str(picture)},
                                                               "requestKey": KEY}, headers=bearer(token)), 409, "hooke-busy")
        monkeypatch.setattr(work, "takeover", lambda app, actor=None: None)
        started = client.post("/api/agent/v1/operations", json={"tool": "open", "args": {"file": str(picture)},
                                                                 "requestKey": KEY}, headers=bearer(token))
        assert started.status_code == 202, started.text
        for _ in range(100):
            if opened:
                break
            import time; time.sleep(0.01)
        assert opened == [str(picture)]


def test_a_project_an_agent_made_is_its_own_place_to_work(tmp_path):
    from types import SimpleNamespace
    from orchestrator.door import work
    me, other = "agent:ac_" + "1" * 24, "agent:ac_" + "2" * 24
    app = SimpleNamespace(paths=SimpleNamespace(home=tmp_path), day=SimpleNamespace(sid="2026-10-28.5"),
                          hub=SimpleNamespace(clients=[SimpleNamespace(kind="studio")]), brain=SimpleNamespace(voice=None))
    assert work.own_project(app, "2026-10-28.5", me).code == "not-your-project"
    work._remember_project(app, "2026-10-28.5", me)
    assert work.own_project(app, "2026-10-28.5", me) is None
    assert work.own_project(app, "2026-10-28.5", other).code == "not-your-project"  # one agent's Project isn't another's
    app.day.sid = "2026-10-28.6"
    assert work.own_project(app, "2026-10-28.5", me).code == "project-not-open"
    app.day.sid = "2026-10-28.5"
    app.brain.voice = SimpleNamespace(closed=False)
    assert work.own_project(app, "2026-10-28.5", me).code == "hooke-busy"  # never while Talk is live


def test_open_may_leave_the_agents_own_project_but_not_anyone_elses(tmp_path):
    from types import SimpleNamespace
    from orchestrator.door import work
    me, other = "agent:ac_" + "1" * 24, "agent:ac_" + "2" * 24
    working = SimpleNamespace(sid="2026-10-28.5", sources={"a1": 1.0}, on_stage="a1", ids_used={"e": 2}, last_user=5.0,
                              guide=None, name="Claude Code: cells")
    app = SimpleNamespace(paths=SimpleNamespace(home=tmp_path), day=working,
                          hub=SimpleNamespace(clients=[SimpleNamespace(kind="studio")]), brain=SimpleNamespace(voice=None))
    work._remember_project(app, "2026-10-28.5", me)
    assert work.takeover(app, me) is None  # its own work: it may open another file
    busy = work.takeover(app, other)  # another agent's work, on screen: not this agent's to switch away from
    assert busy.code == "hooke-busy" and "opening your file" in busy.says and "example" not in busy.says


def test_a_cloud_model_named_in_the_arguments_is_refused_and_runs_stay_local(tmp_path, monkeypatch):
    """Review blocker: where work runs was decided from the capability's preset, but the arguments could name a cloud
    model (a model comparison given a hosted implementation). Now any model the arguments name is checked, an unknown
    component counts as cloud, and the run itself is held to local work (api_usage.local_only)."""
    from types import SimpleNamespace
    from orchestrator.door import listing, work
    from tests.capability_rig import Rig
    app = Rig(tmp_path / "rig").app
    places = listing.model_places(app)
    cloud = sorted(k for k, v in places.items() if v == "cloud")
    local = sorted(k for k, v in places.items() if v == "mac")
    assert cloud and local
    with pytest.raises(Refused) as refused:
        work.check_models({"implementations": [local[0], cloud[0]]}, places)
    assert refused.value.code == "cloud-argument" and cloud[0] in refused.value.says
    work.check_models({"implementations": [local[0]], "note": "any words"}, places)  # words aren't models

    unknown_tool = SimpleNamespace(uses=("a-model-the-licence-table-does-not-know",))
    assert listing.locality({"roles": []}, unknown_tool, {}) == "unknown"

    # The run itself is local-only, whatever its arguments.
    seen = {}

    async def fake_run_tool(app_, name, args, **kw):
        from research_worker import api_usage
        seen["local_only"] = api_usage._local_only.get()
        seen["args"] = args
        return {"ok": True, "summary": "done", "executions": []}

    from orchestrator import commands
    monkeypatch.setattr(commands, "run_tool", fake_run_tool)
    actor = "agent:ac_" + "5" * 24
    work._remember_project(app, app.day.sid, actor)
    record = {"by": actor, "project": app.day.sid, "inputRevision": app.day.on_stage}

    async def progress(*a):
        pass

    asyncio.run(work.run_capability(app, record, progress, "run_capability", {"capability": "spot-measure"}, {"size": 2}))
    assert seen == {"local_only": True, "args": {"capability": "spot-measure", "params": {"size": 2}}}
    # A row with no entry in its preset passes the agent's arguments to its tool as they are.
    asyncio.run(work.run_capability(app, record, progress, "run_capability", {}, {"capability": "spot-measure", "params": {}}))
    assert seen["args"] == {"capability": "spot-measure", "params": {}}


def test_arguments_never_point_outside_the_agents_project():
    with pytest.raises(Refused) as refused:
        __import__("orchestrator.door.work", fromlist=["check_args"]).check_args(
            {"properties": {"session": {}}}, {"session": "2026-10-28.1"}, "2026-10-28.7")
    assert refused.value.code == "not-your-project"
    from orchestrator.door.work import check_args
    check_args({"properties": {"session": {}}}, {"session": "2026-10-28.7"}, "2026-10-28.7")  # its own is fine
    for key in ("thread", "researcher"):
        with pytest.raises(Refused) as refused:
            check_args({"properties": {key: {}}}, {key: "x"}, "2026-10-28.7")
        assert refused.value.code == "bad-arguments"


def test_only_reviewed_catalog_entries_are_runnable_for_agents():
    from types import SimpleNamespace
    from orchestrator import agent_runnable
    assert agent_runnable.catalog_runnable(SimpleNamespace(namespace=""))
    assert not agent_runnable.catalog_runnable(SimpleNamespace(namespace="mine"))
    assert not agent_runnable.catalog_runnable(SimpleNamespace(namespace="someones-channel"))
    assert not agent_runnable.catalog_runnable(None)


def test_operations_and_keys_are_each_connections_own(tmp_path):
    async def go():
        ops = Operations(tmp_path)
        me, other = "agent:ac_" + "1" * 24, "agent:ac_" + "2" * 24

        async def work(record, progress):
            return {"says": "ok"}

        mine, _ = await ops.admit(by=me, label="A", tool="t", project="p", input_revision=None, generation=None,
                                  params={}, request_key=KEY, work=work)
        theirs, made = await ops.admit(by=other, label="B", tool="t", project="p", input_revision=None, generation=None,
                                       params={}, request_key=KEY, work=work)
        assert made and theirs["id"] != mine["id"]  # the same key, another connection: its own operation
        await settle(ops)
        with pytest.raises(Refused):
            ops.get(mine["id"], other)
        with pytest.raises(Refused):
            await ops.cancel(mine["id"], other)
        assert [o["id"] for o in ops.operations(by=other)["operations"]] == [theirs["id"]]
        assert ops.known(KEY, "t", {}, other)["id"] == theirs["id"]
    asyncio.run(go())
