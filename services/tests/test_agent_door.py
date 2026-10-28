"""The outside agents' door (ADR 0026): connecting once, refusing everything else, and the shared contract."""
import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.door import api, native, schema
from orchestrator.door.connections import Connections, PENDING_SECONDS
from orchestrator.origin import studio_origin

HEADER = "X-MicroscopeMind"
BASE = "http://127.0.0.1:8770"
STUDIO = {"Origin": BASE, HEADER: "agent-connections"}
SECRET = "a-secret-the-command-made-" + "x" * 20


def _window_key():
    """The key the Mac app makes for each launch (main.swift AgentConfirm), here made by the test."""
    import base64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return key, base64.b64encode(raw).decode()


WINDOW, WINDOW_PUBLIC = _window_key()


@pytest.fixture(autouse=True)
def window_key(monkeypatch):
    monkeypatch.setenv("HOOKE_CONFIRM_KEY", WINDOW_PUBLIC)
    monkeypatch.delenv("HOOKE_DEV_CONFIRM", raising=False)


def signed(pending, answer, key=None):
    """The answer as the Hooke window's native dialog sends it."""
    import base64
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    signature = (key or WINDOW).sign(native.message(pending["id"], pending["code"], answer), ec.ECDSA(hashes.SHA256()))
    return {"answer": answer, "signature": base64.b64encode(signature).decode()}


class Offers:
    kind = "studio"  # a studio's socket (origin.socket_kind); a script's doesn't count as an open studio

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
    assert client.post(f"/api/agent-connections/pending/{pending['id']}", json=signed(pending, "connect")).status_code == 200
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
    # Only the Hooke window's own dialog may answer: the studio's Origin and header, which any local program can copy,
    # are not enough, nor is an answer signed for something else or by another key.
    for headers in ({}, STUDIO, {"Origin": "http://127.0.0.1:9999", HEADER: "agent-connections"}):
        refused(client.post(f"/api/agent-connections/pending/{pending['id']}", json={"answer": "connect"},
                            headers=headers), 403, "native-confirm-required")
    refused(client.post(f"/api/agent-connections/pending/{pending['id']}",
                        json={"answer": "connect", "signature": signed(pending, "decline")["signature"]}), 403, "bad-confirm")
    other, _ = _window_key()
    refused(client.post(f"/api/agent-connections/pending/{pending['id']}", json=signed(pending, "connect", other)),
            403, "bad-confirm")
    refused(client.post(f"/api/agent-connections/pending/{pending['id']}",
                        json={**signed(pending, "connect"), "signature": "not base64!"}), 403, "bad-confirm")

    assert client.post(f"/api/agent-connections/pending/{pending['id']}", json=signed(pending, "connect")).status_code == 200
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
    client.post(f"/api/agent-connections/pending/{pending['id']}", json=signed(pending, "decline"))
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
    monkeypatch.setattr(api, "listing", lambda built: [fixture, {**fixture, "id": "other", "name": "Other", "task": "x"}])
    value = client.get("/api/agent/v1/tools", params={"q": "spots"}, headers=bearer(token)).json()
    assert value["total"] == 1 and value["tools"][0]["id"] == "spot-measure"

    def unreadable(built):
        raise OSError("catalog gone")
    monkeypatch.setattr(api, "listing", unreadable)
    refused(client.get("/api/agent/v1/tools", headers=bearer(token)), 503, "catalog-unreadable")


def test_the_real_listing_is_everything_an_agent_may_run_in_the_shared_shape(tmp_path):
    """The listing names no capability: it is whatever this Hooke has, each described from its own declaration, in
    contract/agent/capability.schema.json's shape, limited to tools declared runnable for agents (default no) and never
    one that changes a setting. Where each runs is said; cloud work is listed but not runnable through the door."""
    from orchestrator import agent_runnable
    from orchestrator.door.listing import LOCAL_ONLY, changes_settings, everything
    from tests.capability_rig import Rig
    built = Rig(tmp_path).app
    registry = built.brain.registry
    # Every tool is declared, one way or the other: a new tool is never offered until someone decides.
    undeclared = set(registry.tools) - set(agent_runnable.RUNNABLE) - set(agent_runnable.NOT_RUNNABLE)
    assert not undeclared, f"declare these in orchestrator/agent_runnable.py: {sorted(undeclared)}"
    assert not set(agent_runnable.RUNNABLE) & set(agent_runnable.NOT_RUNNABLE)
    assert not [t for t in agent_runnable.RUNNABLE if t in registry.tools and changes_settings(registry.tools[t])]

    listed = everything(built, {"revision": "0123456789abcdef", "sourceDigest": "f" * 64})
    assert len(listed) > 10, len(listed)
    for tool in listed:
        assert schema.problems(tool, "capability") == [], tool["id"]
        assert tool["runsOn"] in ("this Mac", "a cloud service", "not known")
        if tool["runsOn"] != "this Mac":
            assert tool["state"] == "unavailable" and tool["why"] == LOCAL_ONLY
    ids = [t["id"] for t in listed]
    assert len(set(ids)) == len(ids)
    assert not set(ids) & set(api.DOOR_ACTIONS)
    rows = {r["id"]: r for r in built.capability_status()["capabilities"] if r.get("actor") == "agent"}
    tool_of = {cid: (r.get("how") or {}).get("tool") for cid, r in rows.items()}
    assert all(agent_runnable.runnable(tool_of[i]) for i in ids)
    # Never navigation, presentation, researchers or settings, whatever their kind says.
    for cid, name in tool_of.items():
        if name in ("switch_session", "new_session", "show_me", "start_researcher", "set_research_cap", "present"):
            assert cid not in ids, cid
    assert any(t["runsOn"] == "this Mac" and t["state"] == "available" for t in listed)
    assert any(i.startswith("catalog:") for i in ids)


def test_the_shared_schemas_load_and_check():
    for name in schema.NAMES:
        assert schema.load(name)["$id"].startswith("hooke:agent/")
    assert schema.problems({"ok": False, "code": "x", "says": "", "next": ""}, "refusal")
    assert schema.problems("short", "request-key")
    assert schema.problems({"id": "op_" + "0" * 16, "requestKey": "key-12345", "state": "running", "tool": "t",
                            "project": "p", "by": "agent:ac_" + "1" * 24, "at": 1.0}, "operation") == []
    assert schema.problems({"id": "op_" + "0" * 16, "requestKey": "key-12345", "state": "running", "tool": "t",
                            "project": "p", "by": "someone", "at": 1.0}, "operation")


def test_without_the_apps_key_only_a_developer_can_answer(door, monkeypatch):
    """A source checkout has no Mac app and so no window key: answering then takes HOOKE_DEV_CONFIRM=1, set knowingly."""
    client, _, _ = door
    monkeypatch.delenv("HOOKE_CONFIRM_KEY")
    pending = ask(client).json()["pending"]
    refused(client.post(f"/api/agent-connections/pending/{pending['id']}", json={"answer": "connect"}, headers=STUDIO),
            403, "native-confirm-required")
    monkeypatch.setenv("HOOKE_DEV_CONFIRM", "1")
    refused(client.post(f"/api/agent-connections/pending/{pending['id']}", json={"answer": "connect"}), 403,
            "native-confirm-required")  # even then, only the studio's own page
    assert client.post(f"/api/agent-connections/pending/{pending['id']}", json={"answer": "connect"},
                       headers=STUDIO).status_code == 200


def test_an_unreadable_window_key_refuses_every_answer(door, monkeypatch):
    client, _, _ = door
    monkeypatch.setenv("HOOKE_CONFIRM_KEY", "bm90IGEga2V5")
    pending = ask(client).json()["pending"]
    refused(client.post(f"/api/agent-connections/pending/{pending['id']}", json=signed(pending, "connect")), 503,
            "confirm-key-unreadable")


def test_a_socket_opens_only_for_the_studio_or_a_script_with_the_local_token(tmp_path, monkeypatch):
    """ADR 0026: an Origin alone can't tell the studio from a script. With the Mac app's studio token set, only a socket
    presenting it is the studio; without one (a source checkout's browser), a loopback Origin is; a script presents the
    data home's local token; anything else is refused."""
    from orchestrator import origin
    monkeypatch.delenv("HOOKE_STUDIO_TOKEN", raising=False)
    token = origin.local_token(tmp_path)
    assert origin.local_token(tmp_path) == token  # made once
    assert (tmp_path / "agents" / "local-token").stat().st_mode & 0o077 == 0
    script = {"authorization": f"Bearer {token}"}
    assert origin.socket_kind({"origin": BASE}, {}, tmp_path) == "studio"
    assert origin.socket_kind({}, {}, tmp_path) is None  # no Origin and no token: refused (it was allowed before)
    assert origin.socket_kind({"origin": "https://evil.example"}, {}, tmp_path) is None
    assert origin.socket_kind(script, {}, tmp_path) == "script"
    assert origin.socket_kind({"authorization": "Bearer wrong"}, {}, tmp_path) is None
    monkeypatch.setenv("HOOKE_STUDIO_TOKEN", "the-windows-token")
    assert origin.socket_kind({"origin": BASE}, {}, tmp_path) is None  # in the Mac app a copied Origin is not the studio
    assert origin.socket_kind({"origin": BASE}, {"studio": "the-windows-token"}, tmp_path) == "studio"
    assert origin.socket_kind({}, {"studio": "a-guess"}, tmp_path) is None
    assert origin.socket_kind(script, {}, tmp_path) == "script"


def test_status_counts_only_the_real_studio_as_open(tmp_path):
    app, built, offers = build(tmp_path)
    with TestClient(app, base_url=BASE) as client:
        token = connect(client)["token"]
        offers.kind = "script"
        assert client.get("/api/agent/v1/status", headers=bearer(token)).json()["studio"]["open"] is False
        offers.kind = "studio"
        assert client.get("/api/agent/v1/status", headers=bearer(token)).json()["studio"]["open"] is True


def test_the_studio_names_the_playbook_of_its_own_channel():
    """Found on a fresh Mac: Beta's Settings said to fetch hookeui.org/get-started.md, Stable's page, not yet there."""
    assert api.playbook({"HOOKE_CHANNEL": "beta"}) == "https://hookeui.org/get-started-beta.md"
    assert api.playbook({"HOOKE_CHANNEL": "stable"}) == "https://hookeui.org/get-started.md"
    assert api.playbook({}) == "https://hookeui.org/get-started.md"


@pytest.mark.parametrize('channel', ['beta', 'stable', 'development'])
def test_studio_setup_reports_actual_channel(door, monkeypatch, channel):
    client, _built, offers = door
    monkeypatch.setenv('HOOKE_CHANNEL', channel)
    data = client.get('/api/agent-connections', headers=STUDIO).json()
    assert data['channel'] == channel
    ask(client)
    assert offers.sent[-1]['channel'] == channel
