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
    app, built, _ = build(tmp_path)
    with TestClient(app, base_url=BASE) as client:
        claimed = connect(client)
        token, cid = claimed["token"], claimed["connection"]["id"]
        refused(client.post("/api/agent/v1/operations", json={"tool": "outline", "requestKey": KEY}, headers=bearer(token)),
                400, "unknown-tool")
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
