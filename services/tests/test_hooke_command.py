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
