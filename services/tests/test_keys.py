"""Keys: one lifecycle (P1 of docs/design/2026-10-12-the-open-catalog.md). Each key comes from exactly one source (set
outside the app, saved by this app in the Keychain, the keys file); a key is tested before it is saved and a refused one
never is; adding, replacing and removing reach every consumer at once; a key set outside the app is never changed; and a
value never appears in a log, a wire or a reply. All with a stand-in Keychain and mocked HTTP, never this Mac's."""
import asyncio
import logging
import os
from types import SimpleNamespace

import httpx
import pytest

from orchestrator import key_tests, keychain, keys
from orchestrator.key_admin import KeyAdmin

PLANTED = "sk-planted-0123456789abcdef"  # a value no log, wire or reply may ever hold


class FakeKeychain:
    def __init__(self):
        self.saved = {}

    def get_password(self, service, name):
        return self.saved.get((service, name))

    def set_password(self, service, name, value):
        self.saved[(service, name)] = value

    def delete_password(self, service, name):
        self.saved.pop((service, name), None)


@pytest.fixture
def chain(monkeypatch):
    store = FakeKeychain()
    monkeypatch.setattr(keychain, "_store", store)
    keychain.forget()
    for name in keys.KNOWN_KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(keys.APPLIED, raising=False)
    yield store
    keychain.forget()


def keys_file(tmp_path, **values):
    path = tmp_path / "micromind.txt"
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()) + "# a comment\nBLANK=\n")
    return path


# --- where a key comes from ----------------------------------------------------------------------------------------
def test_each_key_has_one_source_outside_then_keychain_then_file(tmp_path, chain, monkeypatch):
    path = keys_file(tmp_path, GEMINI_API_KEY="from-file", OPENAI_API_KEY="from-file", FAL_KEY="from-file")
    keychain.set("OPENAI_API_KEY", "from-keychain")
    keychain.set("FAL_KEY", "from-keychain")
    monkeypatch.setenv("FAL_KEY", "from-shell")  # set outside the app: it wins
    resolved = keys.resolve(path)
    assert (resolved["GEMINI_API_KEY"].source, resolved["GEMINI_API_KEY"].value) == ("file", "from-file")
    assert (resolved["OPENAI_API_KEY"].source, resolved["OPENAI_API_KEY"].value) == ("keychain", "from-keychain")
    assert (resolved["FAL_KEY"].source, resolved["FAL_KEY"].value) == ("environment", "from-shell")
    assert resolved["ANTHROPIC_API_KEY"].source is None and not resolved["ANTHROPIC_API_KEY"].present
    assert keys.sources(path)["OPENAI_API_KEY"] == "keychain"


def test_applying_sets_and_unsets_only_what_the_app_put_there(tmp_path, chain, monkeypatch):
    path = keys_file(tmp_path, GEMINI_API_KEY="from-file")
    monkeypatch.setenv("FAL_KEY", "from-shell")
    keychain.set("OPENAI_API_KEY", "v1")
    changed, removed = keys.set_environment(keys.resolve(path))
    assert changed == {"GEMINI_API_KEY": "from-file", "OPENAI_API_KEY": "v1"} and removed == []
    assert os.environ[keys.APPLIED] == "GEMINI_API_KEY,OPENAI_API_KEY"
    keychain.set("OPENAI_API_KEY", "v2")  # replaced
    assert keys.set_environment(keys.resolve(path)) == ({"OPENAI_API_KEY": "v2"}, [])
    keychain.delete("OPENAI_API_KEY")  # removed, and no other source has it: gone from the environment too
    assert keys.set_environment(keys.resolve(path)) == ({}, ["OPENAI_API_KEY"])
    assert "OPENAI_API_KEY" not in os.environ and os.environ["FAL_KEY"] == "from-shell"


def test_removing_a_saved_key_falls_back_to_the_file_and_never_brings_back_the_removed_value(tmp_path, chain):
    path = keys_file(tmp_path, GEMINI_API_KEY="from-file")
    keychain.set("GEMINI_API_KEY", "saved")
    keys.set_environment(keys.resolve(path))
    assert os.environ["GEMINI_API_KEY"] == "saved"
    keychain.delete("GEMINI_API_KEY")
    assert keys.set_environment(keys.resolve(path)) == ({"GEMINI_API_KEY": "from-file"}, [])
    keychain.forget()  # a restart: the Keychain read afresh
    assert keys.resolve(path)["GEMINI_API_KEY"].value == "from-file"


def test_a_child_process_never_takes_the_parents_keys_for_ones_set_outside(tmp_path, chain):
    keychain.set("OPENAI_API_KEY", "v1")
    keys.set_environment(keys.resolve(keys_file(tmp_path)))
    child = dict(os.environ)  # a worker started now inherits this
    assert "OPENAI_API_KEY" not in keys.outside(child)
    keys.apply_values({"OPENAI_API_KEY": "v2"}, [], child)
    assert child["OPENAI_API_KEY"] == "v2"
    keys.apply_values({}, ["OPENAI_API_KEY"], child)
    assert "OPENAI_API_KEY" not in child
    shell = {"OPENAI_API_KEY": "from-shell"}
    keys.apply_values({"OPENAI_API_KEY": "v3"}, [], shell)  # never over a key set outside the app
    assert shell["OPENAI_API_KEY"] == "from-shell"


# --- testing a key -------------------------------------------------------------------------------------------------
def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("service,status,state", [
    ("gemini", 200, "accepted"), ("openai", 200, "accepted"), ("anthropic", 200, "accepted"),
    ("gemini", 400, "rejected"), ("openai", 401, "rejected"), ("fal", 403, "rejected"),
    ("openai", 400, "unchecked"), ("openai", 429, "unchecked"), ("huggingface", 503, "unchecked"),
])
def test_a_key_test_is_decided_by_the_status_never_the_message(service, status, state):
    outcome = key_tests.check(service, PLANTED, client(lambda r: httpx.Response(status, json={"error": "invalid key"})))
    assert outcome.state == state
    if status == 429:
        assert outcome.why == "rate limited or out of quota"


def test_a_timeout_or_no_connection_is_never_called_a_refused_key():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request):
        raise httpx.ConnectError("no route", request=request)

    assert key_tests.check("gemini", PLANTED, client(timeout)) == key_tests.Outcome("unchecked", "timed out")
    assert key_tests.check("gemini", PLANTED, client(refused)) == key_tests.Outcome("unchecked", "no connection")


def test_the_key_goes_in_a_header_and_never_comes_back():
    seen = {}

    def answer(request):
        seen.update(request.headers)
        return httpx.Response(200, json={"name": PLANTED})  # a service echoing the key back

    outcome = key_tests.check("huggingface", PLANTED, client(answer))
    assert seen["authorization"] == f"Bearer {PLANTED}"
    assert PLANTED not in repr(outcome)


def test_every_service_has_a_test():
    from gpu_worker.manifests import services

    assert set(services()) <= set(key_tests.CALLS)


# --- the lifecycle, through every consumer -------------------------------------------------------------------------
class Channel:
    def __init__(self):
        self.alive, self.calls = True, []

    async def call(self, op, timeout=10.0, **payload):
        self.calls.append((op, payload))
        return {"ok": True}


class Gpu(Channel):
    ready = True

    async def call(self, op, timeout=10.0, priority=None, **payload):
        self.calls.append((op, payload))
        return {"ok": True}


def fake_app(tmp_path, **file_keys):
    research, presentations, gpu = Channel(), Channel(), Gpu()
    app = SimpleNamespace(
        paths=SimpleNamespace(keys_file=keys_file(tmp_path, **file_keys)),
        agent=SimpleNamespace(_visions={"gemini-3.8-flash": object()}, fc=SimpleNamespace(_clients={"openai": object()})),
        research=SimpleNamespace(_worker=research, presentations=SimpleNamespace(worker=presentations)),
        hub=SimpleNamespace(gpu_clients=lambda: [gpu]), published=0)
    app.publish_settings = lambda: setattr(app, "published", app.published + 1)
    return app, research, presentations, gpu


def test_add_replace_and_remove_reach_every_consumer(tmp_path, chain, monkeypatch):
    app, research, presentations, gpu = fake_app(tmp_path, OPENAI_API_KEY="from-file")
    admin = KeyAdmin(app)
    monkeypatch.setattr(key_tests, "check", lambda service, key, client=None: key_tests.Outcome("accepted", detail="5 models"))

    result = asyncio.run(admin.save("openai", f"  {PLANTED}  "))
    assert result == {"ok": True, "state": "accepted", "says": "OpenAI accepted the key. Each model now shows what else it needs, if anything."}
    assert os.environ["OPENAI_API_KEY"] == PLANTED and keychain.get("OPENAI_API_KEY") == PLANTED
    assert app.agent._visions == {} and app.agent.fc._clients == {}  # rebuilt with the new key on their next use
    assert research.calls[-1] == ("keys", {"set": {"OPENAI_API_KEY": PLANTED}, "unset": []})
    assert presentations.calls[-1] == research.calls[-1] and gpu.calls == []  # only HF_TOKEN goes to the GPU workers
    assert app.published == 1

    asyncio.run(admin.save("openai", "sk-second"))  # replaced
    assert os.environ["OPENAI_API_KEY"] == "sk-second" and research.calls[-1][1]["set"] == {"OPENAI_API_KEY": "sk-second"}

    result = asyncio.run(admin.remove("openai"))  # removed: the keys file's key is used again
    assert result["ok"] and "keys file" in result["says"]
    assert os.environ["OPENAI_API_KEY"] == "from-file" and research.calls[-1][1]["set"] == {"OPENAI_API_KEY": "from-file"}

    asyncio.run(admin.save("huggingface", "hf_token"))
    assert gpu.calls[-1] == ("keys", {"set": {"HF_TOKEN": "hf_token"}, "unset": []})
    asyncio.run(admin.remove("huggingface"))
    assert gpu.calls[-1] == ("keys", {"set": {}, "unset": ["HF_TOKEN"]}) and "HF_TOKEN" not in os.environ


def test_a_refused_key_is_never_saved_and_an_unchecked_one_only_when_asked(tmp_path, chain, monkeypatch):
    app, research, _, _ = fake_app(tmp_path)
    admin = KeyAdmin(app)
    monkeypatch.setattr(key_tests, "check", lambda *a, **k: key_tests.Outcome("rejected"))
    assert asyncio.run(admin.save("gemini", PLANTED)) == {"ok": False, "state": "rejected", "says": "Google didn't accept that key."}
    assert keychain.get("GEMINI_API_KEY") is None and "GEMINI_API_KEY" not in os.environ and research.calls == []

    monkeypatch.setattr(key_tests, "check", lambda *a, **k: key_tests.Outcome("unchecked", "timed out"))
    result = asyncio.run(admin.save("gemini", PLANTED))
    assert result["state"] == "unchecked" and not result["ok"] and "timed out" in result["says"]
    assert keychain.get("GEMINI_API_KEY") is None
    assert asyncio.run(admin.save("gemini", PLANTED, unchecked=True))["says"].startswith("Saved without checking it.")
    assert keychain.get("GEMINI_API_KEY") == PLANTED


def test_a_key_set_outside_the_app_is_never_changed_or_removed(tmp_path, chain, monkeypatch):
    app, _, _, _ = fake_app(tmp_path)
    admin = KeyAdmin(app)
    monkeypatch.setenv("GEMINI_API_KEY", "from-shell")
    monkeypatch.setattr(key_tests, "check", lambda *a, **k: pytest.fail("never tested"))
    assert "set outside the app" in asyncio.run(admin.save("gemini", PLANTED))["says"]
    assert "set outside the app" in asyncio.run(admin.remove("gemini"))["says"]
    assert os.environ["GEMINI_API_KEY"] == "from-shell" and admin.facts()["GEMINI_API_KEY"]["canSave"] is False


def test_no_value_reaches_a_log_the_facts_or_a_reply(tmp_path, chain, monkeypatch, caplog):
    app, research, presentations, gpu = fake_app(tmp_path)
    research.alive = False  # a worker that went away: logged, never with the key
    gpu.call = lambda *a, **k: (_ for _ in ()).throw(RuntimeError(f"boom {PLANTED}"))  # never echoed by us
    admin = KeyAdmin(app)
    monkeypatch.setattr(key_tests, "check", lambda *a, **k: key_tests.Outcome("accepted"))
    with caplog.at_level(logging.DEBUG):
        replies = [asyncio.run(admin.save("huggingface", PLANTED)), asyncio.run(admin.save("openai", PLANTED))]
    facts = admin.facts()
    assert PLANTED not in repr(replies) and PLANTED not in repr(facts) and PLANTED not in caplog.text
    assert facts["OPENAI_API_KEY"]["source"] == "keychain" and facts["OPENAI_API_KEY"]["checked"]["outcome"] == "accepted"


def test_a_keys_file_macos_privacy_refuses_is_said_never_as_not_added(tmp_path, chain, monkeypatch):
    """F-L8-3: ~/Desktop/micromind.txt is there, but macOS privacy (TCC) refuses the app's Python the Desktop folder
    (EPERM, no prompt). Every key used to look "Not added"; now each says the file can't be read, and how to fix it."""
    import errno
    from pathlib import Path

    from orchestrator import readiness

    app, _, _, _ = fake_app(tmp_path, OPENAI_API_KEY="from-file")
    refused = app.paths.keys_file
    real_open = Path.open

    def tcc(self, *args, **kwargs):
        if self == refused:
            raise PermissionError(errno.EPERM, "Operation not permitted", str(self))
        return real_open(self, *args, **kwargs)

    assert keys.file_problem(refused) == "" and keys.file_problem(tmp_path / "missing.txt") == ""
    monkeypatch.setattr(Path, "open", tcc)
    admin = KeyAdmin(app)
    facts = admin.facts()
    why = facts["OPENAI_API_KEY"]["fileBlocked"]
    assert facts["OPENAI_API_KEY"]["present"] is False
    assert "isn't allowed to read your keys file" in why and "Privacy & Security" in why and "Keychain" in why
    assert all(f.get("fileBlocked") == why for f in facts.values())
    monkeypatch.setenv("MICROMIND_KEYS_FILE", str(refused))
    step = readiness.key_step("OPENAI_API_KEY")
    assert step.how == why and "put OPENAI_API_KEY" not in step.how

    keychain.set("GEMINI_API_KEY", "from-keychain")  # a key that is there, from another source, says nothing of the file
    assert "fileBlocked" not in admin.facts()["GEMINI_API_KEY"]


# --- the route: only the studio --------------------------------------------------------------------------------------
def test_only_the_studio_can_save_or_remove_a_key(tmp_path, chain, monkeypatch):
    from fastapi.testclient import TestClient

    from orchestrator.paths import Paths
    from orchestrator.server import create_app

    monkeypatch.setattr(key_tests, "check", lambda *a, **k: key_tests.Outcome("accepted"))
    web = TestClient(create_app("http://127.0.0.1:1", use_gpu=False, paths=Paths.under(tmp_path)))
    studio = {"X-MicroscopeMind": "save-key", "Origin": "http://127.0.0.1:5180"}
    body = {"value": PLANTED}
    assert web.post("/api/keys/openai", json=body).status_code == 403  # no header, no origin: a script or a page
    assert web.post("/api/keys/openai", json=body, headers={"X-MicroscopeMind": "save-key"}).status_code == 403
    assert web.post("/api/keys/openai", json=body, headers={**studio, "Origin": "http://localhost:3000"}).status_code == 403
    assert web.post("/api/keys/openai", json=body, headers={**studio, "Origin": "https://example.org"}).status_code == 403
    saved = web.post("/api/keys/openai", json=body, headers=studio)
    assert saved.status_code == 200 and saved.json()["ok"] and PLANTED not in saved.text
    assert keychain.get("OPENAI_API_KEY") == PLANTED
    assert web.delete("/api/keys/openai").status_code == 403
    assert web.delete("/api/keys/openai", headers=studio).json()["ok"]
    assert keychain.get("OPENAI_API_KEY") is None
    wire = web.get("/api/capabilities").text
    assert PLANTED not in wire and '"keys"' in wire


def test_the_research_workers_loop_applies_a_key_change(chain, monkeypatch):
    """The research process's own loop (research_worker/worker.py), over a stand-in pipe: the keys op sets and unsets in
    that process's environment, as its researchers' clients read it."""
    import queue

    from research_worker import worker

    class Conn:
        def __init__(self, messages):
            self.inbox, self.sent = queue.Queue(), []
            for m in messages:
                self.inbox.put(m)

        def recv(self):
            return self.inbox.get(timeout=5)

        def send(self, message):
            self.sent.append(message)

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    conn = Conn([{"id": 1, "op": "keys", "set": {"OPENAI_API_KEY": "v1"}, "unset": []}, None])
    asyncio.run(worker._main(conn))
    assert os.environ["OPENAI_API_KEY"] == "v1" and conn.sent == [{"reply_to": 1, "ok": True}]
    conn = Conn([{"id": 2, "op": "keys", "set": {}, "unset": ["OPENAI_API_KEY"]}, None])
    asyncio.run(worker._main(conn))
    assert "OPENAI_API_KEY" not in os.environ


def test_a_second_stack_keeps_its_saved_keys_in_a_file_of_its_own(tmp_path, monkeypatch):
    """MICROMIND_KEYCHAIN=file:<path>: Add key on a second stack never touches this Mac's Keychain."""
    path = tmp_path / "keys.json"
    monkeypatch.setenv("MICROMIND_KEYCHAIN", f"file:{path}")
    monkeypatch.setattr(keychain, "_store", None)
    keychain.forget()
    assert keychain.enabled()
    keychain.set("OPENAI_API_KEY", "v1")
    assert (path.stat().st_mode & 0o777) == 0o600
    keychain.forget()
    assert keychain.get("OPENAI_API_KEY") == "v1"
    keychain.delete("OPENAI_API_KEY")
    keychain.forget()
    assert keychain.get("OPENAI_API_KEY") is None
