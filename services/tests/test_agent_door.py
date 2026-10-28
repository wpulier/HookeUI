"""The outside agents' door (ADR 0026): connecting once, refusing everything else, and the shared contract."""
import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.door import api, schema
from orchestrator.door.connections import Connections, PENDING_SECONDS
from orchestrator.origin import studio_origin

HEADER = "X-MicroscopeMind"
BASE = "http://127.0.0.1:8770"
STUDIO = {"Origin": BASE, HEADER: "agent-connections"}
SECRET = "a-secret-the-command-made-" + "x" * 20


class Offers:
    def __init__(self):
        self.sent = []

    def offer(self, message):
        self.sent.append(json.loads(message))


def build(home):
    offers = Offers()
    built = SimpleNamespace(paths=SimpleNamespace(home=home), day=SimpleNamespace(sid="2026-10-28.1"),
                            hub=SimpleNamespace(clients={offers}))
    app = FastAPI()
    api.register(app, built, studio_origin, HEADER)
    return app, built, offers


@pytest.fixture
def door(tmp_path):
    app, built, offers = build(tmp_path)
    with TestClient(app, base_url=BASE) as client:
        yield client, built, offers


def ask(client, label="Claude Code", kind="claude-code", secret=SECRET):
    return client.post("/api/agent/v1/connect", json={"label": label, "kind": kind,
                                                      "secretSha256": hashlib.sha256(secret.encode()).hexdigest()})


def connect(client, label="Claude Code", secret=SECRET):
    pending = ask(client, label, secret=secret).json()["pending"]
    assert client.post(f"/api/agent-connections/pending/{pending['id']}", json={"answer": "connect"},
                       headers=STUDIO).status_code == 200
    claimed = client.post(f"/api/agent/v1/connect/{pending['id']}/claim", json={"secret": secret})
    assert claimed.status_code == 200, claimed.text
    return claimed.json()


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def refused(response, status, code):
    assert response.status_code == status, response.text
    value = response.json()
    assert schema.problems(value, "refusal") == []
    assert value["code"] == code
    return value


def test_connecting_takes_a_confirm_in_hooke_and_the_askers_secret(door):
    client, built, offers = door
    response = ask(client)
    assert response.status_code == 202
    pending = response.json()["pending"]
    assert pending["code"] in response.json()["next"]  # the code the CLI prints is the one Hooke shows
    assert offers.sent[-1]["type"] == "agent-connections" and offers.sent[-1]["pending"][0]["id"] == pending["id"]

    waiting = client.post(f"/api/agent/v1/connect/{pending['id']}/claim", json={"secret": SECRET})
    assert waiting.status_code == 202 and "token" not in waiting.json()
    refused(client.post(f"/api/agent/v1/connect/{pending['id']}/claim", json={"secret": "someone else"}),
            403, "wrong-secret")
    # Only the Hooke window may answer: no Origin, another page or a missing header is refused.
    for headers in ({}, {"Origin": "http://127.0.0.1:9999", HEADER: "agent-connections"}, {"Origin": BASE}):
        refused(client.post(f"/api/agent-connections/pending/{pending['id']}", json={"answer": "connect"},
                            headers=headers), 403, "studio-only")

    assert client.post(f"/api/agent-connections/pending/{pending['id']}", json={"answer": "connect"},
                       headers=STUDIO).status_code == 200
    claimed = client.post(f"/api/agent/v1/connect/{pending['id']}/claim", json={"secret": SECRET}).json()
    assert claimed["token"].startswith("hk_local_")
    assert schema.problems(claimed["connection"], "connection") == []
    assert claimed["connection"]["label"] == "Claude Code" and claimed["connection"]["id"].startswith("ac_")
    # The token is handed over once.
    refused(client.post(f"/api/agent/v1/connect/{pending['id']}/claim", json={"secret": SECRET}),
            410, "connection-expired")
    # Only its hash is kept.
    kept = (built.paths.home / "agents" / "connections.json").read_text()
    assert claimed["token"] not in kept and hashlib.sha256(claimed["token"].encode()).hexdigest() in kept
    assert (built.paths.home / "agents" / "connections.json").stat().st_mode & 0o077 == 0

    status = client.get("/api/agent/v1/status", headers=bearer(claimed["token"]))
    assert status.status_code == 200
    value = status.json()
    assert value["connection"]["id"] == claimed["connection"]["id"] and value["contract"] == "1"
    assert value["project"]["session"] == "2026-10-28.1" and value["studio"]["open"] is True
    assert status.headers["Hooke-Contract"] == "1"


def test_a_declined_request_gives_no_token(door):
    client, _, _ = door
    pending = ask(client).json()["pending"]
    client.post(f"/api/agent-connections/pending/{pending['id']}", json={"answer": "decline"}, headers=STUDIO)
    refused(client.post(f"/api/agent/v1/connect/{pending['id']}/claim", json={"secret": SECRET}), 403, "declined")


def test_disconnect_is_final_and_survives_a_restart(door, tmp_path):
    client, _, _ = door
    first = connect(client)
    cid = first["connection"]["id"]
    assert client.post(f"/api/agent-connections/{cid}/disconnect", headers=STUDIO).status_code == 200
    refused(client.get("/api/agent/v1/status", headers=bearer(first["token"])), 401, "disconnected")

    # Connecting again is a new connection with a new id; the old token stays refused.
    second = connect(client, secret=SECRET + "-again")
    assert second["connection"]["id"] != cid
    refused(client.get("/api/agent/v1/status", headers=bearer(first["token"])), 401, "disconnected")
    assert client.get("/api/agent/v1/status", headers=bearer(second["token"])).status_code == 200

    # After a restart (a new store over the same data home), the revoked token is still refused.
    app, _, _ = build(tmp_path)
    with TestClient(app, base_url=BASE) as again:
        refused(again.get("/api/agent/v1/status", headers=bearer(first["token"])), 401, "disconnected")
        assert again.get("/api/agent/v1/status", headers=bearer(second["token"])).status_code == 200


def test_every_door_request_is_checked_before_its_token(door):
    client, _, _ = door
    token = connect(client)["token"]
    refused(client.get("/api/agent/v1/status"), 401, "not-connected")
    refused(client.get("/api/agent/v1/status", headers=bearer("hk_local_made-up")), 401, "not-connected")
    refused(client.get("/api/agent/v1/status", headers={**bearer(token), "Origin": BASE}), 403, "browser-refused")
    refused(client.get("/api/agent/v1/status", headers={**bearer(token), "Host": "evil.example:8770"}), 403, "not-local")
    refused(client.get("/api/agent/v1/status", headers={**bearer(token), "Hooke-Contract": "2"}), 400,
            "contract-version")
    refused(client.post("/api/agent/v1/connect", json={"label": "x", "secretSha256": "nope"}), 400, "bad-request")
    refused(client.post("/api/agent/v1/connect", json={"label": "x", "secretSha256": "0" * 64},
                        headers={"Origin": "https://evil.example"}), 403, "browser-refused")


def test_pending_requests_are_limited_and_expire(tmp_path):
    now = [1000.0]
    store = Connections(tmp_path, clock=lambda: now[0])
    digest = hashlib.sha256(SECRET.encode()).hexdigest()
    made = [store.request("Agent", "other", digest) for _ in range(5)]
    with pytest.raises(Exception) as error:
        store.request("Agent", "other", digest)
    assert getattr(error.value, "code", "") == "too-many-pending"
    now[0] += PENDING_SECONDS + 1
    assert store.pending() == []
    with pytest.raises(Exception) as error:
        store.answer(made[0].id, True)
    assert getattr(error.value, "code", "") == "connection-expired"
    assert store.request("Agent", "other", digest).state == "pending"


def test_labels_are_labels(tmp_path):
    store = Connections(tmp_path)
    digest = hashlib.sha256(SECRET.encode()).hexdigest()
    p = store.request("  Claude\nCode\x07 " + "y" * 80, "not-a-kind", digest)
    assert p.label.startswith("Claude Code") and len(p.label) <= 40 and "\x07" not in p.label
    assert p.kind == "other"
    assert store.request("", "codex", digest).label == "An agent"


def test_an_unreadable_list_refuses_the_door_but_the_app_starts(tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "connections.json").write_text("{not json")
    app, _, _ = build(tmp_path)
    with TestClient(app, base_url=BASE) as client:
        refused(ask(client), 503, "connections-unreadable")
        refused(client.get("/api/agent/v1/status", headers=bearer("hk_local_x")), 503, "connections-unreadable")
    assert (tmp_path / "agents" / "connections.json").read_text() == "{not json"  # never overwritten


def test_tools_need_a_connection_and_answer_in_the_shared_shape(door, monkeypatch):
    client, _, _ = door
    refused(client.get("/api/agent/v1/tools"), 401, "not-connected")
    token = connect(client)["token"]
    fixture = {"id": "spot-measure", "name": "Spot measure", "task": "Measure bright spots", "description": "",
               "inputs": [{"type": "image", "label": "an image"}],
               "outputs": [{"id": "out", "type": "table", "label": "a table"}],
               "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
               "credit": {}, "licence": ["MIT"], "generation": "abc1234.0123456789abcdef", "state": "available"}
    monkeypatch.setattr(api, "listing", lambda: [fixture, {**fixture, "id": "other", "name": "Other", "task": "x"}])
    value = client.get("/api/agent/v1/tools", params={"q": "spots"}, headers=bearer(token)).json()
    assert value["total"] == 1 and value["tools"][0]["id"] == "spot-measure"

    def unreadable():
        raise OSError("catalog gone")
    monkeypatch.setattr(api, "listing", unreadable)
    refused(client.get("/api/agent/v1/tools", headers=bearer(token)), 503, "catalog-unreadable")


def test_the_real_catalog_lists_in_the_shared_shape():
    """Every capability the door lists, read from the catalog itself, fits contract/agent/capability.schema.json; so a
    new catalog entry reaches agents with no change to the door."""
    listed = api.listing()
    assert listed, "the catalog lists nothing"
    for tool in listed:
        assert schema.problems(tool, "capability") == [], tool["id"]
    assert len({t["id"] for t in listed}) == len(listed)


def test_the_shared_schemas_load_and_check():
    for name in schema.NAMES:
        assert schema.load(name)["$id"].startswith("hooke:agent/")
    assert schema.problems({"ok": False, "code": "x", "says": "", "next": ""}, "refusal")
    assert schema.problems("short", "request-key")
    assert schema.problems({"id": "op_" + "0" * 16, "requestKey": "key-12345", "state": "running", "tool": "t",
                            "project": "p", "by": "agent:ac_" + "1" * 24, "at": 1.0}, "operation") == []
    assert schema.problems({"id": "op_" + "0" * 16, "requestKey": "key-12345", "state": "running", "tool": "t",
                            "project": "p", "by": "someone", "at": 1.0}, "operation")
