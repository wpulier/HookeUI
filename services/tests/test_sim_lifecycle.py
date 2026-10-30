"""A failed observer connection remains a failed simulation result, not a lost report."""
import asyncio
import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from sim import harness


@pytest.mark.parametrize("failure", ["connect", "cleanup", "receiver", "teardown"])
def test_observer_failures_keep_the_report_and_release_tasks(tmp_path, monkeypatch, failure):
    video = tmp_path / "generated.mp4"
    video.touch()  # The stack transport is stood in for; no video is decoded here.
    results = tmp_path / "results"
    active = []
    incoming = asyncio.Queue()

    class Connection:
        async def __aenter__(self):
            if failure == "connect":
                raise ConnectionRefusedError("scripted observer connection refusal")
            return self

        async def __aexit__(self, *args):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await incoming.get()

        async def send(self, text):
            if failure == "cleanup" and json.loads(text)["type"] == "clear" and len(ran) == 1:
                raise ConnectionResetError("scripted cleanup connection failure")

    class Stack:
        ws_url = "ws://test-refused"

        def __init__(self, *args, cloud=False):
            pass

        async def __aenter__(self):
            active.append(self)
            return self

        async def __aexit__(self, *args):
            active.remove(self)
            self.teardown = ([{"role": "bridge", "ok": False, "forcedKill": True,
                               "returncode": -9, "reaped": True}] if failure == "teardown" else [])

        async def sim(self, *args):
            pass

        async def wait(self, *args):
            return True, 0

    async def no_wall_wait(_):
        await asyncio.sleep(0)

    ran = []

    async def scenario(stack, viewer):
        ran.append(True)
        outcome = harness.Outcome("first" if len(ran) == 1 else "second")
        outcome.check("scenario received its connected observer", True)
        if failure == "receiver" and len(ran) == 1:
            await incoming.put("malformed server message")
            await asyncio.sleep(0)
        return outcome

    monkeypatch.setattr(harness, "Stack", Stack)
    monkeypatch.setattr(harness.websockets, "connect", lambda *args, **kwargs: Connection())
    monkeypatch.setattr(harness, "asyncio", SimpleNamespace(**{**vars(asyncio), "sleep": no_wall_wait}))
    monkeypatch.setattr(harness, "SCENARIOS", {"first": scenario, "second": scenario})
    monkeypatch.setattr(harness, "RESULTS", results)
    monkeypatch.setattr(harness.sys, "argv", ["sim.harness", "--video", str(video)])

    async def check():
        before = asyncio.all_tasks()
        assert await harness.main() == 1
        assert not active
        assert not (asyncio.all_tasks() - before), "the observer task outlived the scenario run"

    asyncio.run(check())
    report = json.loads((results / "latest.json").read_text())
    assert report["teardownPassed"] is (failure != "teardown")
    if failure == "teardown":
        assert report["teardown"][0]["forcedKill"]
        assert all(row["passed"] for row in report["scenarios"])
        assert len(ran) == 2, "child cleanup must not rewrite scenario outcomes"
        return
    assert report["teardown"] == []
    assert [row["name"] for row in report["scenarios"]] == ["first", "second"]
    rows = report["scenarios"]
    assert [row["passed"] for row in rows] == ([False, False] if failure == "connect" else [False, True])
    reason = "JSONDecodeError" if failure == "receiver" else "connection"
    assert any(reason in check["detail"] for check in rows[0]["checks"])
    if failure != "connect":
        assert len(ran) == 2, "cleanup failure prevented the next scenario from running"
        assert rows[0]["checks"][0]["ok"], "cleanup failure lost the scenario's original checks"
    else:
        assert not ran, "a scenario started without its observer"


@pytest.mark.parametrize('mode', ['clean', 'forced', 'unreaped', 'crashed', 'truncated'])
def test_stack_teardown_retains_child_outcome_and_complete_written_stderr(tmp_path, mode):
    import hashlib
    import signal
    import subprocess

    path = tmp_path / 'sim-bridge.log'
    path.write_bytes(b'working\n')

    class Child:
        pid = 901
        returncode = None

        def __init__(self):
            self.waits = []
            self.signals = []
            self.kills = 0

        def send_signal(self, sig):
            self.signals.append(sig)

        def wait(self, timeout):
            self.waits.append(timeout)
            if mode == 'unreaped' or (mode == 'forced' and not self.kills):
                raise subprocess.TimeoutExpired('scripted child', timeout)
            self.returncode = -signal.SIGKILL if self.kills else 1 if mode == 'crashed' else 0
            # Final stderr is available only once the child has actually been waited for.
            path.write_bytes(b'working\nException in thread ' if mode == 'truncated'
                             else b'working\nfinal shutdown detail\n')
            return self.returncode

        def kill(self):
            self.kills += 1

    child = Child()
    stack = harness.Stack(tmp_path / 'unused.mp4', 18765, 18770, 0, True)
    stack.procs = [child]  # only this owned OS-process boundary is stood in for
    stack.log_dir = tmp_path
    asyncio.run(stack.__aexit__(None, None, None))
    assert child.signals == [signal.SIGTERM], 'the bridge installs its normal cleanup handler for SIGTERM'
    assert child.waits == ([8.0, 0.5] if mode in ('forced', 'unreaped') else [8.0]), (
        'preserve the full original graceful wait, followed only if needed by a bounded reap')
    record, = stack.teardown
    assert record['pid'] == child.pid and record['role'] == 'bridge'
    assert record['returncode'] == child.returncode
    assert record['forcedKill'] is (mode in ('forced', 'unreaped'))
    assert record['reaped'] is (mode != 'unreaped')
    assert record['ok'] is (mode in ('clean', 'truncated'))
    assert record['gracefulTimeoutS'] == 8.0 and record['killReapTimeoutS'] == 0.5
    assert record['log']['bytes'] == len(path.read_bytes())
    assert record['log']['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert Path(record['log']['path']).read_bytes() == path.read_bytes()
    assert record['log']['snapshotComplete']
    assert not record['log']['writerClosureObserved'], 'parent exit cannot prove descendant stderr is closed'
    assert record['log']['endsWithNewline'] is (mode != 'truncated')
    assert record['log']['mayBeIncomplete'] is (mode in ('forced', 'unreaped', 'truncated'))


@pytest.mark.parametrize('role', ['bridge', 'orchestrator'])
def test_stack_restart_preserves_twenty_second_grace_and_previous_stderr(tmp_path, monkeypatch, role):
    import signal
    import httpx

    source = tmp_path / f'sim-{role}.log'
    source.write_text('old worker started\n')

    class Child:
        pid = 702
        returncode = None

        def send_signal(self, sig):
            assert sig == (signal.SIGTERM if role == 'bridge' else signal.SIGINT)

        def wait(self, timeout):
            assert timeout == 20.0
            with source.open('a') as log:
                log.write('old worker stopped\n')
            self.returncode = 0

    child = Child()
    replacement = object()

    def start(**kwargs):
        assert child.returncode == 0, 'the prior child must be reaped before replacement starts'
        kwargs['stdout'].write('replacement started\n')
        kwargs['stdout'].close()
        return replacement

    async def check():
        stack = harness.Stack(tmp_path / 'unused.mp4', 18765, 18770, 0, True)
        await stack.http.aclose()
        stack.http = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={'state': 'live', 'gpu': {'ready': True}})))
        stack.log_dir = tmp_path
        stack.procs = [child, child]
        stack._bridge_args = stack._orch_args = {}  # OS process arguments, no server is started
        monkeypatch.setattr(harness.subprocess, 'Popen', start)
        try:
            await getattr(stack, f'restart_{role}')()
            assert stack.procs[0 if role == 'bridge' else 1] is replacement
            receipt, = stack.teardown
            assert receipt['phase'] == 'restart' and receipt['gracefulTimeoutS'] == 20.0
            assert receipt['reaped'] and receipt['ok']
            assert Path(receipt['log']['path']).read_text() == 'old worker started\nold worker stopped\n'
            assert source.read_text().endswith('replacement started\n')
        finally:
            await stack.http.aclose()

    asyncio.run(check())
