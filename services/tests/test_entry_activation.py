"""Synthetic Cellpose check child through the public setup path: no model code, private assets or account keys."""
import asyncio
import json
import sys
import pytest

from gpu_worker import manifests as catalog
from orchestrator import commands, mine, operations
from tests.capability_rig import Rig
from tests.test_mine import home, a_file  # noqa: F401 - isolated catalog and stand-in weights
from tests.waiting import HANG_S, until_async


def test_added_entry_activation_uses_the_public_example_and_original_session(home, monkeypatch):
    monkeypatch.setitem(operations.INSTALLED, 'cellpose', lambda: '')
    key = mine.add(str(a_file(home)), 'Test cells')['model']
    seen = home / 'input.json'
    script = "import json,sys; from pathlib import Path; p=Path(sys.argv[1]); Path(sys.argv[2]).write_text(json.dumps({'path':str(p),'exists':p.is_file()})); print(json.dumps({'ok':p.is_file() and 'public-examples' in str(p),'steps':[]}))"
    monkeypatch.setattr(mine, 'check_command', lambda weights, frame: [sys.executable, '-c', script, str(frame), str(seen)])
    app = Rig(home / 'app').app
    notices = []
    original = app.turned_on
    def said(job, words, *, asked=None):
        notices.append(asked.session); original(job, words, asked=asked)
    app.turned_on = said
    session = app.day.sid
    async def run():
        app._loop = asyncio.get_running_loop()
        reply = await commands.model_turn_on(app, None, {'model': key})
        assert reply['state'] == 'running'
        app.day.new('Another session', 'user')
        await until_async(lambda: not app.turn_on.wire())
        app.turn_on.advance()
        assert mine.state(catalog.mine()[key])['on'], json.loads(seen.read_text())
        assert notices == [session]
        assert not app.turn_on.wire()
        await app.stop()
    asyncio.run(run())


@pytest.mark.parametrize("finish", ["cancel", "stop"])
@pytest.mark.parametrize("command", [commands.model_turn_on, commands.model_activate])
def test_cancelling_entry_setup_reaps_its_exact_check_before_close_returns(home, monkeypatch, finish, command):
    import os
    import signal
    monkeypatch.setitem(operations.INSTALLED, 'cellpose', lambda: '')
    key = mine.add(str(a_file(home)), 'Test cells')['model']
    pidfile = home / 'owned-pid'
    # Existence means a complete handshake, not the instant write_text opens an empty file.
    script = ("import os,json,time; from pathlib import Path; p=Path(%r); "
              "pending=p.with_suffix('.partial'); pending.write_text(str(os.getpid())); pending.replace(p); "
              "time.sleep(60); print(json.dumps({'ok':True,'steps':[]}))") % str(pidfile)
    monkeypatch.setattr(mine, 'check_command', lambda *args: [sys.executable, '-c', script])
    app = Rig(home / 'app').app
    async def run():
        app._loop = asyncio.get_running_loop()
        pid = None
        try:
            await command(app, None, {'model': key})
            await until_async(pidfile.exists)
            pid = int(pidfile.read_text())
            if finish == 'cancel':
                assert app.turn_on.cancel('model:' + key)
                await app.turn_on.close()
            else:
                await app.stop()
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
            assert not mine.state(catalog.mine()[key])['on']
        finally:
            try:
                if pid is not None:
                    os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            await app.stop()
    asyncio.run(run())


@pytest.mark.parametrize('retry_passes', [True, False])
def test_retry_uses_its_own_result_after_a_retained_failure(home, monkeypatch, retry_passes):
    monkeypatch.setitem(operations.INSTALLED, 'cellpose', lambda: '')
    key = mine.add(str(a_file(home)), 'Retry cells')['model']
    job = 'model:' + key
    entered, release = home / 'retry-entered', home / 'retry-release'
    calls = []
    def check_command(*args):
        calls.append(True)
        check = {'ok': retry_passes if len(calls) > 1 else False, 'steps': [
            {'ok': False, 'says': 'Current check' if len(calls) > 1 else 'Old check'}]}
        wait = (f"Path({str(entered)!r}).touch(); "
                f"\nwhile not Path({str(release)!r}).exists() and time.monotonic()<end: time.sleep(.01)\n") if len(calls) > 1 else ''
        script = "import json,time; from pathlib import Path; end=time.monotonic()+60; " + wait + f"print({json.dumps(check)!r})"
        return [sys.executable, '-c', script]
    monkeypatch.setattr(mine, 'check_command', check_command)
    app = Rig(home / 'app').app
    notices = []
    app.turned_on = lambda *args, **kwargs: notices.append(kwargs['asked'].session)
    async def run():
        app._loop = asyncio.get_running_loop()
        try:
            await commands.model_turn_on(app, None, {'model': key})
            await until_async(lambda: app.turn_on.wire().get(job, {}).get('state') == 'failed')
            origin = app.day.sid
            await commands.model_turn_on(app, None, {'model': key})
            await until_async(entered.exists)
            app.day.new('Changed while retrying', 'user')
            app.turn_on.advance()
            assert app.turn_on.wire()[job]['state'] == 'running'
            release.touch()
            await until_async(lambda: (not app.turn_on.wire()) if retry_passes else
                              app.turn_on.wire().get(job, {}).get('state') == 'failed')
            assert mine.state(catalog.mine()[key])['on'] is retry_passes
            assert notices == ([origin] if retry_passes else [])
            if not retry_passes:
                assert 'Old check' not in app.turn_on.wire()[job]['says']
        finally:
            release.touch()
            await app.stop()
    asyncio.run(run())


def test_accepted_cancel_during_activation_save_leaves_it_off_and_silent(home, monkeypatch):
    """Pause the filesystem write after the proof, then cancel through the Studio command."""
    import threading
    from pathlib import Path
    monkeypatch.setitem(operations.INSTALLED, 'cellpose', lambda: '')
    key = mine.add(str(a_file(home)), 'Publishing cells')['model']
    entered, release = threading.Event(), threading.Event()
    real_write = Path.write_text
    def write(path, *args, **kwargs):
        value = real_write(path, *args, **kwargs)
        if 'activation.json' in path.name and path.name.startswith('.'):
            entered.set()
            assert release.wait(HANG_S), 'test publication barrier expired'
        return value
    monkeypatch.setattr(Path, 'write_text', write)
    monkeypatch.setattr(mine, 'check_command', lambda *a: [sys.executable, '-c', "print('{\"ok\":true,\"steps\":[]}')"])
    app = Rig(home / 'app').app
    notices = []
    app.turned_on = lambda *args, **kwargs: notices.append(args)
    async def run():
        app._loop = asyncio.get_running_loop()
        try:
            await commands.model_turn_on(app, None, {'model': key})
            assert await asyncio.to_thread(entered.wait, HANG_S)
            # The write is blocked off-loop: the Studio can still issue and accept Stop.
            # Well under the barrier's HANG_S: a Stop that waited for the blocked write would run out here.
            reply = await asyncio.wait_for(commands.capability_turn_on_cancel(app, None, {'id': 'model:' + key, 'attempt': app.turn_on.wire()['model:' + key]['attempt']}), 20)
            assert reply['cancelled'] is True
            release.set()
            await app.turn_on.close()
            assert mine.state(catalog.mine()[key])['on'] is False
            assert notices == []
            assert not app.turn_on.wire()
        finally:
            release.set()
            await app.stop()
    asyncio.run(run())


@pytest.mark.parametrize('finish', ['cancel', 'stop', 'changed'])
def test_activation_that_committed_finishes_once_while_withdrawal_waits_off_loop(home, monkeypatch, finish):
    import threading
    from pathlib import Path
    monkeypatch.setitem(operations.INSTALLED, 'cellpose', lambda: '')
    key = mine.add(str(a_file(home)), 'Final cells')['model']
    entered, release = threading.Event(), threading.Event()
    actual = Path.replace
    def replace(path, target):
        if Path(target).name == mine.ACTIVATION:
            entered.set()
            assert release.wait(HANG_S), 'test rename barrier expired'
        return actual(path, target)
    monkeypatch.setattr(Path, 'replace', replace)
    monkeypatch.setattr(mine, 'check_command', lambda *a: [sys.executable, '-c', "print('{\"ok\":true,\"steps\":[]}')"])
    app = Rig(home / 'app').app
    notices = []
    app.turned_on = lambda *args, **kwargs: notices.append(kwargs['asked'].session)
    async def run():
        app._loop = asyncio.get_running_loop()
        timer = None
        try:
            origin = app.day.sid
            await commands.model_turn_on(app, None, {'model': key})
            assert await asyncio.to_thread(entered.wait, HANG_S)
            app.day.new('After admission', 'user')
            if finish == 'changed':
                manifest = catalog.mine_root() / key.split('/', 1)[1] / 'model.toml'
                manifest.write_text(manifest.read_text().replace('Final cells', 'Changed cells'))
                app.turn_on.advance()
                ending = asyncio.create_task(
                    until_async(lambda: app.turn_on.wire().get('model:' + key, {}).get('state') == 'failed'))
            else:
                ending = asyncio.create_task(app.stop() if finish == 'stop' else
                            commands.capability_turn_on_cancel(app, None, {'id': 'model:' + key, 'attempt': app.turn_on.wire()['model:' + key]['attempt']}))
            # Fails safely if the command ever waits for this disk lock on the event loop.
            timer = threading.Timer(HANG_S, release.set); timer.start()
            pulse = asyncio.Event()
            asyncio.get_running_loop().call_later(.01, pulse.set)
            await pulse.wait()
            assert not release.is_set(), 'the live loop was blocked on activation publication'
            assert not ending.done()
            release.set()
            reply = await ending
            if finish == 'cancel':
                assert reply['cancelled'] is False, 'publication already owns the terminal transition'
                await until_async(lambda: not app.turn_on.wire())
            assert mine.state(catalog.mine()[key])['on'] is True
            assert notices == ([] if finish == 'changed' else [origin])
            if finish == 'changed':
                assert 'changed' in app.turn_on.wire()['model:' + key]['says']
        finally:
            release.set()
            if timer: timer.cancel()
            await app.stop()
    asyncio.run(run())
