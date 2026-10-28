"""The real `hooke` command against a real door on loopback (ADR 0026; packaging/macos/hooke): it names its agent,
takes options anywhere, refuses a door that isn't this Mac's, never loops on a fresh Mac, and rides out a restart while
following work, saying what survived. Needs macOS, swiftc and a login keychain (skipped in CI)."""
import asyncio
import hashlib
import json
import os
import platform
import plistlib
import shutil
import socket
import subprocess
import threading
import time
import uuid
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi import FastAPI

from orchestrator.door import api
from orchestrator.origin import studio_origin
from orchestrator.paths import REPO

pytestmark = pytest.mark.skipif(
    platform.system() != "Darwin" or shutil.which("swiftc") is None or os.environ.get("CI") == "true",
    reason="needs macOS, swiftc and a login keychain")


@pytest.fixture(scope="module")
def hooke(tmp_path_factory):
    out = tmp_path_factory.mktemp("hooke") / "hooke"
    subprocess.run(["swiftc", "-swift-version", "5", str(REPO / "packaging/macos/hooke/main.swift"),
                    str(REPO / "packaging/macos/HookeUI/AgentShared.swift"), "-o", str(out)],
                   check=True, capture_output=True, timeout=600)
    return out


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Door:
    """The door on a real uvicorn server, with the example replaced by work that waits until told to finish."""

    def __init__(self, home, port, work):
        self.home, self.port, self.work = home, port, work
        self.server = None

    def start(self):
        built = SimpleNamespace(paths=SimpleNamespace(home=self.home), day=SimpleNamespace(sid="s1"),
                                hub=SimpleNamespace(clients=set()))
        web = FastAPI()
        api.register(web, built, studio_origin, "X-MicroscopeMind")
        self.server = uvicorn.Server(uvicorn.Config(web, host="127.0.0.1", port=self.port, log_level="error"))
        threading.Thread(target=self.server.run, daemon=True).start()
        for _ in range(200):
            if self.server.started:
                return self
            time.sleep(0.02)
        raise RuntimeError("the door didn't start")

    def stop(self):
        self.server.should_exit = True
        time.sleep(0.5)


def run(hooke, env, *args, timeout=60):
    return subprocess.run([str(hooke), *args], env=env, capture_output=True, text=True, timeout=timeout)


def test_the_command_against_a_real_door(hooke, tmp_path, monkeypatch):
    from orchestrator.door import example
    channel = f"test-{uuid.uuid4().hex[:8]}"
    service = f"org.hookeui.agent.{channel}"
    started = threading.Event()
    finish = threading.Event()

    async def work(app, record, progress):
        await progress(1, 2, "Working on it")
        started.set()
        while not finish.is_set():
            await asyncio.sleep(0.05)
        return {"says": "Done.", "page": "#measurements/e1?session=s1&revision=0"}

    monkeypatch.setattr(example, "run", work)
    monkeypatch.setattr(example, "takeover", lambda app, doing="the example", actor=None: None)
    monkeypatch.setattr(api, "example_identity", lambda: ("r1", "abc.0123456789abcdef"))
    monkeypatch.setenv("HOOKE_DEV_CONFIRM", "1")
    monkeypatch.delenv("HOOKE_CONFIRM_KEY", raising=False)
    port = free_port()
    door = Door(tmp_path, port, work).start()
    env = {**os.environ, "HOOKE_CHANNEL": channel, "HOOKE_DOOR_URL": f"http://127.0.0.1:{port}"}
    try:
        # Every command names its agent.
        unnamed = run(hooke, env, "tools")
        assert unnamed.returncode != 0 and "Say which agent you are" in unnamed.stderr

        # Connect: the command asks, a developer's studio answers (HOOKE_DEV_CONFIRM), the command collects its token.
        proc = subprocess.Popen([str(hooke), "connect", "--agent", "claude-code", "--no-skill"], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        import urllib.request
        for _ in range(200):
            listed = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/agent-connections").read())
            if listed["pending"]:
                break
            time.sleep(0.05)
        pid = listed["pending"][0]["id"]
        answer = urllib.request.Request(f"http://127.0.0.1:{port}/api/agent-connections/pending/{pid}",
                                        data=json.dumps({"answer": "connect"}).encode(), method="POST",
                                        headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}",
                                                 "X-MicroscopeMind": "agent-connections"})
        urllib.request.urlopen(answer).read()
        out, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, err
        assert "Connected: Claude Code can use Hooke" in out

        # Options anywhere: the key before the tool's words, the agent last. Then a restart while following.
        follower = subprocess.Popen([str(hooke), "call", "--key", "follow-0001", "example", "{}", "--agent", "claude-code"],
                                    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert started.wait(30)
        door.stop()
        time.sleep(1.0)
        door = Door(tmp_path, port, work).start()  # the same data home: the unfinished operation is now interrupted
        out, err = follower.communicate(timeout=120)
        assert "isn't answering" in out
        assert "interrupted: Hooke stopped while this ran" in out, out + err
        assert follower.returncode == 1

        # The same key finds that operation again; it never runs it twice.
        again = run(hooke, env, "call", "example", "--agent", "claude-code", "--key", "follow-0001")
        assert "already used for this request" in again.stdout and "interrupted" in again.stdout

        # Status names the connection.
        status = run(hooke, env, "status", "--agent", "claude-code")
        assert "connected as Claude Code" in status.stdout

        # A door that isn't this Mac's loopback is never used.
        away = run(hooke, {**env, "HOOKE_DOOR_URL": "http://evil.example:80"}, "tools", "--agent", "claude-code")
        assert away.returncode != 0 and "No Hooke app here" in away.stderr
    finally:
        finish.set()
        door.stop()
        subprocess.run(["security", "delete-generic-password", "-s", service, "-a", "claude-code"], capture_output=True)


def test_a_fresh_mac_opens_hooke_instead_of_looping(hooke, tmp_path):
    """Review: on a Mac where Hooke never ran, `status --wait` answered "run status --wait". Now plain status says it
    isn't running, and --wait tries to open the app (here a stand-in that macOS can't open, so it says so and stops)."""
    folder = f"HookeUI Test {uuid.uuid4().hex[:8]}"
    bundle = tmp_path / "Hooke Test.app"
    (bundle / "Contents" / "Helpers").mkdir(parents=True)
    (bundle / "Contents" / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": "org.hookeui.test." + uuid.uuid4().hex[:6], "HookeUIChannel": "test",
        "HookeUISupportFolder": folder, "HookeUIURLScheme": "hookeui-test"}))
    shutil.copy(hooke, bundle / "Contents" / "Helpers" / "hooke")
    inside = bundle / "Contents" / "Helpers" / "hooke"
    env = {k: v for k, v in os.environ.items() if k != "HOOKE_DOOR_URL"}
    plain = run(inside, env, "status")
    assert plain.returncode == 0 and "isn't running" in plain.stdout and "--wait opens it" in plain.stdout
    waited = run(inside, env, "status", "--wait", timeout=60)
    assert waited.returncode != 0
    assert "Opening Hooke Test" in waited.stdout and "couldn't open" in waited.stderr
    assert "run hooke status --wait" not in waited.stderr
    # Called through a link on PATH, it still finds its own app (its executable's real path, not argv[0]).
    on_path = tmp_path / "bin"
    on_path.mkdir()
    (on_path / "hooke").symlink_to(inside)
    linked = subprocess.run(["hooke", "status"], env={**env, "PATH": f"{on_path}:/usr/bin:/bin"}, capture_output=True,
                            text=True, timeout=60)
    assert "isn't running" in linked.stdout and "No Hooke app here" not in linked.stderr, linked.stdout + linked.stderr
    # An option it doesn't know is refused (a mistyped --key would start a fresh request); --name=value works.
    typo = run(inside, env, "status", "--kye", "abc")
    assert typo.returncode == 2 and "there is no option --kye" in typo.stderr
    assert run(inside, env, "status", "--agent=claude-code").returncode == 0
    # A copy inside an app never uses HOOKE_DOOR_URL, even on loopback.
    pinned = run(inside, {**env, "HOOKE_DOOR_URL": "http://127.0.0.1:9"}, "status")
    assert "isn't running" in pinned.stdout
    shutil.rmtree(os.path.expanduser(f"~/Library/Application Support/{folder}"), ignore_errors=True)


def test_named_connections_keep_separate_actors_and_revocation(hooke, tmp_path, monkeypatch):
    """Real CLI/Keychain/door, synthetic consent and operation: not a user's connection approval."""
    import urllib.request
    from orchestrator.door import example

    channel = f"test-{uuid.uuid4().hex}"
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = {k: v for k, v in os.environ.items() if k not in ('HOOKE_CONNECTION', 'HOOKE_AGENT')}
    env.update(HOOKE_CHANNEL=channel, HOOKE_DOOR_URL=base)

    async def work(app, record, progress):
        return {'says': 'Synthetic identity check', 'actor': record['by']}

    monkeypatch.setattr(example, 'run', work)
    monkeypatch.setattr(example, 'takeover', lambda app, doing='the example', actor=None: None)
    monkeypatch.setattr(api, 'example_identity', lambda: ('r1', 'abc.0123456789abcdef'))
    monkeypatch.setenv('HOOKE_DEV_CONFIRM', '1')
    monkeypatch.delenv('HOOKE_CONFIRM_KEY', raising=False)
    door = Door(tmp_path, port, work).start()
    processes = []

    def studio(path, body=None):
        request = urllib.request.Request(base + '/api/agent-connections' + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type': 'application/json', 'Origin': base,
                     'X-MicroscopeMind': 'agent-connections'})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    def arguments(name):
        return ['--agent', 'claude-code', *(['--connection', name] if name else [])]

    def status(name, environment=env):
        result = run(hooke, environment, 'status', '--json', *arguments(name))
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    try:
        identities = {}
        for name in (None, 'lead', 'reviewer'):
            proc = subprocess.Popen([str(hooke), 'connect', '--no-skill', *arguments(name)],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            processes.append(proc)
            from tests.waiting import until
            pending = until(lambda: studio('')['pending'])
            studio('/pending/' + pending[0]['id'], {'answer': 'connect'})
            out, err = proc.communicate(timeout=60)
            assert proc.returncode == 0, out + err
            record = status(name)
            identities[name] = record['connection']['id']
            assert record['connectionName'] == name

        assert len(set(identities.values())) == 3
        assert status(None)['connection']['id'] == identities[None]
        assert status(None, {**env, 'HOOKE_CONNECTION': 'lead'})['connection']['id'] == identities['lead']
        assert status('reviewer', {**env, 'HOOKE_CONNECTION': 'lead'})['connection']['id'] == identities['reviewer']
        # The same request key stays independent across connections; retries retain the original.
        operations = {}
        for name in ('lead', 'reviewer'):
            result = run(hooke, env, 'example', '--json', '--key', 'named-request-0001', *arguments(name))
            assert result.returncode == 0, result.stdout + result.stderr
            listing = run(hooke, env, 'operations', '--json', *arguments(name))
            payload = json.loads(listing.stdout)
            operation = payload['operations'][0]
            assert operation['by'] == 'agent:' + identities[name]
            operations[name] = operation['id']
            again = run(hooke, env, 'example', '--json', '--key', 'named-request-0001', *arguments(name))
            assert again.returncode == 0, again.stdout + again.stderr
            retried = json.loads(run(hooke, env, 'operations', '--json', *arguments(name)).stdout)
            assert [item['id'] for item in retried['operations']] == [operations[name]]
        assert operations['lead'] != operations['reviewer']

        studio('/' + identities['lead'] + '/disconnect', {})
        refused = run(hooke, env, 'status', '--json', *arguments('lead'))
        assert refused.returncode != 0 and 'disconnected' in refused.stdout.lower()
        assert status('reviewer')['connection']['id'] == identities['reviewer']
        assert status(None)['connection']['id'] == identities[None]
        absent = run(hooke, env, 'tools', '--json', *arguments('missing'))
        assert absent.returncode != 0 and '--connection missing' in absent.stdout
        missing_status = status('missing')
        assert missing_status['connected'] is False
        assert missing_status['connectionName'] == 'missing'
        assert missing_status['next'] == 'hooke connect --agent claude-code --connection missing'
        text_status = run(hooke, env, 'status', *arguments('missing'))
        assert '--agent claude-code --connection missing' in text_status.stdout
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
                proc.communicate(timeout=10)
        door.stop()
        for account in ('claude-code', 'claude-code:lead', 'claude-code:reviewer'):
            subprocess.run(['security', 'delete-generic-password', '-s', f'org.hookeui.agent.{channel}',
                            '-a', account], capture_output=True)


@pytest.mark.parametrize('name', ['', '../reviewer', 'lead:other', 'a' * 65, 'lead\n'])
def test_invalid_connection_names_refuse_before_access(hooke, name):
    result = run(hooke, os.environ, 'status', '--json', '--connection=' + name)
    assert result.returncode != 0
    assert json.loads(result.stdout)['says'] == 'Invalid connection name.'
