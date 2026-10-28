"""Outside agents' operations (ADR 0026): admitted to disk first, one request per key, restarts and cancels said truly."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from orchestrator.door import actor, api, schema
from orchestrator.door.connections import Refused
from orchestrator.door.operations import Operations, request_digest
from orchestrator.resources import maker_of
from tests.test_agent_door import BASE, bearer, build, connect, refused

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
