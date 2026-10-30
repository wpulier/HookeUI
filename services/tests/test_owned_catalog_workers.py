"""Owned controlled children, never Fiji downloads or model weights. Every hang has an outer test watchdog."""
import dataclasses
import asyncio
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager

import numpy as np
import pytest

from orchestrator import env_runner, envs, fiji_bindings, fiji_generations
from orchestrator import owned_process
from tests.test_env_runner import home, contribute, standin_uv


@contextmanager
def outer_watchdog(worker):
    """Release legacy unbounded reads safely, then fail the test if this fallback was needed."""
    fired = threading.Event()

    def kill():
        fired.set()
        child = worker._process
        if child is not None:
            child.kill()
            child.wait(timeout=1)

    timer = threading.Timer(4, kill)
    timer.start()
    try:
        yield
    finally:
        timer.cancel()
        child = worker._process
        if child is not None and child.poll() is None:
            child.kill()
            child.wait(timeout=1)
        worker.stop()
        assert not fired.is_set(), 'outer watchdog had to release an unbounded worker read'


def controlled(home, monkeypatch, family, mode):
    control = home / 'worker-mode'
    control.write_text(mode)
    pid = home / 'worker-pid'
    prelude = f'''import os, time, pathlib
pid_file = pathlib.Path({str(pid)!r})
pending_pid = pid_file.with_suffix('.pending')
pending_pid.write_text(str(os.getpid()))
pending_pid.replace(pid_file)
mode = pathlib.Path({str(control)!r}).read_text()
if mode == 'startup': time.sleep(60)
'''
    if family == 'python':
        adapter = prelude + '''
import numpy as np
def segment(picture, params):
    if mode == 'run': time.sleep(60)
    return np.ones(picture.shape[:2], np.int32)
'''
        m = contribute(home, adapter)
        standin_uv(home, monkeypatch)
        folder = envs.build(m)
        m = dataclasses.replace(m, load_limit_s=.5)
        worker = env_runner.Client(env_runner.Installed(m, folder, {}, True))
        monkeypatch.setattr(env_runner, 'RUN_S', .15, raising=False)
        call = lambda: worker.run(np.full((16, 16, 3), 220, np.uint8))
        error = env_runner.Failed
    else:
        folder = home / 'controlled-fiji'
        folder.mkdir()
        script = folder / 'fiji'
        # -IS: isolated and without site (the stand-in needs only the standard library), so the interpreter's own start
        # stays well inside the controlled 0.15 s load limit; under load the site import alone could exceed it and the
        # startup timeout fired before the prelude wrote its pid (seen 1 run in 3, 2026-10-13). No limit is changed.
        script.write_text(f'#!{sys.executable} -IS\n' + prelude + '''
import json, sys
print('{"ok": true}', flush=True)
for line in sys.stdin:
    if mode == 'run': time.sleep(60)
    print('@@' + json.dumps({'ok': True, 'answer': 42}), flush=True)
''')
        script.chmod(0o700)
        monkeypatch.setattr(fiji_generations, 'fiji_dir', lambda n: folder)
        entry = fiji_generations.entry()
        monkeypatch.setattr(fiji_generations, 'entry', lambda: dataclasses.replace(entry, load_limit_s=.15))
        monkeypatch.setattr(fiji_generations, 'STEP_S', .15)
        worker = fiji_bindings.Worker()
        call = lambda: worker.call({'op': 'controlled'}, n=1)
        error = fiji_generations.Failed
    return worker, call, error, control, pid


@pytest.mark.timing  # its child must start within the 0.15 s `controlled` gives it: a loaded Mac overruns that
@pytest.mark.parametrize('family', ['python', 'fiji'])
@pytest.mark.parametrize('phase', ['startup', 'run'])
def test_stalled_child_is_reaped_and_the_next_request_recovers(home, monkeypatch, family, phase):
    worker, call, error, control, pid = controlled(home, monkeypatch, family, phase)
    with outer_watchdog(worker):
        with pytest.raises(error, match=f'{phase} timed out'):
            call()
        old_pid = int(pid.read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(old_pid, 0)
        control.write_text('ready')
        if family == 'python':
            worker.m = dataclasses.replace(worker.m, load_limit_s=2)
        result = call()
        assert result.get('count', result.get('answer')) == (1 if family == 'python' else 42)
        assert int(pid.read_text()) != old_pid


# 3.5: the next proof slower than the old 3 s wait, as a first import of its native libraries on a fresh Mac (CI) can be.
@pytest.mark.parametrize('next_proof_s', [0, 3.5])
def test_cancelled_adapter_proof_releases_install_queue_and_retains_failure(home, monkeypatch, next_proof_s):
    import shutil
    from gpu_worker import manifests as catalog
    from tests.test_env_runner import ADAPTER

    m = contribute(home, 'import time\n' + ADAPTER.replace('import cv2', 'time.sleep(60)\n    import cv2'))
    standin_uv(home, monkeypatch)
    other = home / 'repo' / 'models' / 'next-spots'
    shutil.copytree(home / 'repo' / 'models' / m.id, other)
    manifest = other / 'model.toml'
    manifest.write_text(manifest.read_text().replace('dark-spots', 'next-spots'))
    (other / 'adapter.py').write_text(ADAPTER if not next_proof_s else
                                      'import time\n' + ADAPTER.replace('import cv2', f'time.sleep({next_proof_s})\n    import cv2'))
    catalog.manifests.cache_clear()
    created = threading.Event()
    children = []
    Client = env_runner.Client

    def tracked(*args, **kwargs):
        child = Client(*args, **kwargs)
        children.append(child)
        created.set()
        return child

    monkeypatch.setattr(env_runner, 'Client', tracked)
    from types import SimpleNamespace
    from orchestrator import activity

    class Said(activity.Activity):
        def __init__(self):
            self.lines = {}

        def add(self, kind, text, *, id=None, state='done', **kw):
            self.lines[id] = (state, text)

    said = Said()
    queue = envs.Installs(hub=SimpleNamespace(activities=said))
    queue.start(m.id)
    assert created.wait(3), queue.wire()
    try:
        # What is measured: Stop ends the first proof's process at once and the queue moves on to the next install,
        # within the watchdog's 4 s.
        with outer_watchdog(children[0]):
            queue.start('next-spots')
            queue.cancel(m.id)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                rows = {r['model']: r for r in queue.wire()}
                if rows[m.id]['state'] == 'cancelled' and rows['next-spots']['state'] != 'queued':
                    break
                time.sleep(.02)
            assert rows['next-spots']['state'] != 'queued', rows  # the Stop released the queue
        # The next install's own proof is a new process importing its native libraries, slow the first time on a fresh
        # Mac (CI, 2026-10-26: still installing after 3 s); its finishing is waited for, not timed.
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            rows = {r['model']: r for r in queue.wire()}
            if rows['next-spots']['state'] in ('done', 'failed'):
                break
            time.sleep(.05)
        # The user's Stop is said as stopped, never as a failure of its code (C7 Window B, finding 8).
        assert rows[m.id]['state'] == 'cancelled', rows
        assert rows[m.id]['says'] == "Stopped: Dark spots wasn't installed (you pressed Stop)", rows
        assert said.lines[rows[m.id]['activity']] == ('done', rows[m.id]['says']), said.lines
        assert rows['next-spots']['state'] == 'done', rows
        assert not env_runner.proof(m)['ok'] and env_runner.proof(m)['stopped']
        assert 'cancelled' in env_runner.proof(m)['why']  # what ended it, kept in the proof's own record
        assert env_runner.ready(m) == ("its install was stopped before its proof finished (you pressed Stop): "
                                       "Install proves it"), "the card says stopped, not 'failed its proof'"
    finally:
        queue.cancel(m.id)
        queue.cancel('next-spots')
        queue._thread.join(timeout=3)




async def _child_started(request, pid, within: float = 30.0) -> None:
    """Its child wrote its pid: else the request's own failure (it can end before its child starts, its run limit being
    2 s here) or, past `within`, every thread's stack, never a wait without end (Codex's diagnosis of 511205a2)."""
    import faulthandler
    import time

    deadline = time.monotonic() + within
    while not pid.exists():
        if request.done():
            request.result()  # its own exception, as it was raised
            raise AssertionError("the request ended before its child wrote its pid")
        if time.monotonic() > deadline:
            faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
            raise AssertionError(f"its child wrote no pid in {within:.0f} s (every thread's stack is above)")
        await asyncio.sleep(.01)

@pytest.mark.timing  # its child must start within the 0.15 s `controlled` gives it: a loaded Mac overruns that
@pytest.mark.parametrize('family', ['python', 'fiji'])
def test_cancelled_waiter_leaves_the_request_holding_the_worker_alone(home, monkeypatch, family):
    worker, call, error, control, pid = controlled(home, monkeypatch, family, 'run')
    monkeypatch.setattr(env_runner, 'RUN_S', 2)
    monkeypatch.setattr(fiji_generations, 'STEP_S', 2)

    async def go():
        first = asyncio.create_task(owned_process.off_loop(call))
        await _child_started(first, pid)
        old_pid = int(pid.read_text())
        second = asyncio.create_task(owned_process.off_loop(call))
        await asyncio.sleep(.05)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(second, 1)
        assert not first.done()
        os.kill(old_pid, 0)  # cancelling the queued request did not kill the active request's child
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(first, 1)
        with pytest.raises(ProcessLookupError):
            os.kill(old_pid, 0)
        control.write_text('ready')
        result = await owned_process.off_loop(call)
        assert result.get('count', result.get('answer')) == (1 if family == 'python' else 42)

    with outer_watchdog(worker):
        asyncio.run(go())


@pytest.mark.parametrize('family', ['python', 'fiji'])
def test_command_output_is_drained_while_the_owned_child_runs(home, monkeypatch, family):
    """A pipe-filling proof/update command must finish, rather than deadlock waiting to exit before reading."""
    monkeypatch.setattr(envs, 'STEP_S', 1)
    monkeypatch.setattr(fiji_generations, 'STEP_S', 1)
    marker = 'x' * 200_000
    if family == 'python':
        out = envs._run([sys.executable, '-c', f'print("x" * {len(marker)})'], dict(os.environ), lambda: False, 'fixture')
    else:
        folder = home / 'fiji-output'
        folder.mkdir()
        script = folder / 'fiji'
        script.write_text(f'#!{sys.executable}\nprint("x" * {len(marker)})\n')
        script.chmod(0o700)
        monkeypatch.setattr(fiji_generations, 'fiji_dir', lambda n, environ: folder)
        monkeypatch.setattr(fiji_generations, '_launcher', lambda n, environ: script)
        out = fiji_generations.run_fiji(1, [])
        assert out == marker + '\n'
        return
    # An install step's output is drained to its end and only its last words kept (owned_process.drained: code nobody
    # reviewed can't fill the app's memory by printing).
    assert out == (marker + '\n')[-owned_process.KEEP_BYTES:]


@pytest.mark.parametrize('family', ['python', 'fiji'])
@pytest.mark.parametrize('phase', ['ready', 'run'])
def test_app_stop_reaps_retained_and_busy_workers_without_waiting_for_their_lock(home, monkeypatch, family, phase):
    from tests.standin_workers import build_app

    built = build_app(home / 'app')
    worker, call, error, control, pid = controlled(home, monkeypatch, family, phase)
    # What this test asserts is the stop, not a deadline: the controlled 0.15 s limits are the stalled-child tests'
    # (above), so its run, step and startup are each given room. The startup was left at 0.15 s, which a loaded host's
    # interpreter start exceeded in the 2026-10-15 gate ("Fiji's startup timed out" before the ready worker answered).
    monkeypatch.setattr(env_runner, 'RUN_S', 20)
    monkeypatch.setattr(fiji_generations, 'STEP_S', 20)
    if family == 'python':
        worker.m = dataclasses.replace(worker.m, load_limit_s=20)
    else:
        entry = fiji_generations.entry()
        monkeypatch.setattr(fiji_generations, 'entry', lambda: dataclasses.replace(entry, load_limit_s=20))
    if family == 'python':
        env_runner._clients[worker.m.key] = worker
    else:
        monkeypatch.setattr(fiji_bindings, 'WORKER', worker)
    bystander = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)

    async def go():
        active = asyncio.create_task(owned_process.off_loop(call))
        try:
            async with asyncio.timeout(2):
                while not pid.exists():
                    await asyncio.sleep(.01)
            old_pid = int(pid.read_text())
            if phase == 'ready':
                await active
            await asyncio.wait_for(built.stop(), 2)
            assert bystander.poll() is None, 'shutdown must touch only this app\'s owned children'
            with pytest.raises(ProcessLookupError):
                os.kill(old_pid, 0)
            if phase == 'run':
                with pytest.raises(error):
                    await asyncio.wait_for(active, 1)
            build_app(home / 'fresh-app')
            control.write_text('ready')
            result = await owned_process.off_loop(call)
            assert result.get('count', result.get('answer')) == (1 if family == 'python' else 42)
        finally:
            if not active.done():
                active.cancel()
            await asyncio.gather(active, return_exceptions=True)

    try:
        with outer_watchdog(worker):
            asyncio.run(go())
    finally:
        bystander.terminate()
        bystander.wait(timeout=1)


@pytest.mark.timing  # its child must start within the 0.15 s `controlled` gives it: a loaded Mac overruns that
def test_app_stop_cancels_a_busy_adapter_proof_and_a_fresh_app_can_install(home, monkeypatch):
    from gpu_worker import manifests as catalog
    from tests.standin_workers import build_app

    built = build_app(home / 'proof-app')
    worker, call, error, control, pid = controlled(home, monkeypatch, 'python', 'run')
    monkeypatch.setattr(env_runner, 'RUN_S', 20)
    monkeypatch.setattr(env_runner, 'Client', lambda *args, **kwargs: worker)
    queue = built.installs

    async def go():
        queue.start('dark-spots')
        async with asyncio.timeout(2):
            while not pid.exists():
                await asyncio.sleep(.01)
        old_pid = int(pid.read_text())
        await asyncio.wait_for(built.stop(), 2)
        with pytest.raises(ProcessLookupError):
            os.kill(old_pid, 0)
        assert queue.wire()[0]['state'] == 'failed'
        assert 'cancelled' in queue.wire()[0]['says']
        assert not env_runner.proof(catalog.get(worker.m.key))['ok']  # the queue's entry, not the worker's timing override
        control.write_text('ready')
        fresh = build_app(home / 'fresh-proof-app')
        fresh.installs.start('dark-spots')
        async with asyncio.timeout(2):
            while fresh.installs.wire()[0]['state'] not in ('done', 'failed'):
                await asyncio.sleep(.02)
        assert fresh.installs.wire()[0]['state'] == 'done'
        await fresh.stop()

    try:
        with outer_watchdog(worker):
            asyncio.run(go())
    finally:
        queue.cancel('dark-spots')
        queue._thread.join(timeout=3)


def test_catalog_withdrawal_keeps_the_loop_running_and_shutdown_reaps_the_retired_child(home, monkeypatch):
    from gpu_worker import manifests as catalog
    from orchestrator import operations, options
    from tests.standin_workers import build_app
    from tests.test_contributed_contracts import run_image
    from tests.test_env_runner import install

    adapter = '''
import signal
import numpy as np
signal.signal(signal.SIGTERM, signal.SIG_IGN)
def segment(picture, params):
    return np.ones(picture.shape[:2], np.int32)
'''
    m = contribute(home, adapter)
    assert install(home, monkeypatch)['state'] == 'done'
    built = build_app(home / 'withdrawal-app')
    built.hub.settings.update({'cells': m.id})
    assert run_image(m, np.full((16, 16, 3), 220, np.uint8))['count'] == 1
    worker = env_runner.client(env_runner.in_use(m))
    child = worker._process
    entry = home / 'repo' / 'models' / m.id
    retired = home / 'removed-entry'
    bystander = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)

    async def go():
        built.catalog_changed()  # warm normal capability reads before measuring only withdrawal
        entry.rename(retired)
        catalog.manifests.cache_clear()
        loop = asyncio.get_running_loop()
        heartbeat = loop.create_future()
        t0 = loop.time()
        loop.call_soon(lambda: heartbeat.set_result((child.poll(), loop.time() - t0)))
        try:
            built.catalog_changed()
            assert m.id not in options.GROUPS['cells']
            assert operations.OPERATIONS['cells'].impl(m.id) is None
            assert built.hub.settings.cells == options.Settings().cells
            status, elapsed = await heartbeat
            assert status is None, f'loop callback waited {elapsed:.3f}s until the resistant child was reaped'
            built.catalog_changed()  # a further rescan must not forget the cleanup already in progress
        finally:
            await asyncio.wait_for(built.stop(), 2)
        assert child.returncode is not None
        assert bystander.poll() is None, 'withdrawal must stop only the owned adapter child'
        retired.rename(entry)
        catalog.manifests.cache_clear()
        fresh = build_app(home / 'fresh-withdrawal-app')
        try:
            impl = operations.OPERATIONS['cells'].impl(m.id)
            from types import SimpleNamespace
            import cv2

            picture = cv2.imencode('.png', np.full((16, 16, 3), 220, np.uint8))[1].tobytes()
            result = await operations.ADAPTERS[('cells', m.id)](None, SimpleNamespace(jpeg=picture), None, impl, {})
            assert result['count'] == 1
        finally:
            await fresh.stop()

    try:
        with outer_watchdog(worker):
            asyncio.run(go())
    finally:
        bystander.terminate()
        bystander.wait(timeout=1)
