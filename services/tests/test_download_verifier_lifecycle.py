"""Actual App lifecycle, a controlled verifier child, no owner catalog or downloads."""
import asyncio
import json
import os
import signal
import sys

import pytest

from orchestrator import downloads, thumbnails
from tests.standin_workers import build_app
from tests.test_env_runner import home
from tests.waiting import HANG_S


def test_verification_cli_reports_the_isolated_catalog(home, capsys):
    assert downloads.main(['verify']) == 0
    assert json.loads(capsys.readouterr().out) == {}


@pytest.mark.timing  # its verifier child must start inside the 0.3 s VERIFY_S it gives it (a loaded Mac overruns it)
@pytest.mark.parametrize('outcome', ['timeout', 'cancel', 'stop'])
def test_app_reaps_verifier_on_timeout_cancellation_or_stop_and_can_verify_again(home, monkeypatch, outcome):
    built = build_app(home / 'app')
    changed = []
    monkeypatch.setattr(built, 'catalog_changed', lambda: changed.append(True))
    monkeypatch.setattr(downloads, 'VERIFY_S', .3, raising=False)
    pid = home / 'verifier-pid'
    mode = home / 'verifier-mode'
    mode.write_text('hang')
    script = home / 'verifier.py'
    script.write_text(f'''import os, pathlib, signal, time
pathlib.Path({str(pid)!r}).write_text(str(os.getpid()))
if pathlib.Path({str(mode)!r}).read_text() == 'hang':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(60)
print('{{"controlled": "changed file"}}')
''')
    monkeypatch.setattr(thumbnails, 'background', lambda command: [sys.executable, str(script)])

    async def go():
        task = asyncio.create_task(built.verify_downloads(first_after=0))
        built._tasks.append(task)
        try:
            async with asyncio.timeout(2):
                while not pid.exists():
                    assert not task.done(), 'verification did not use its owned child'
                    await asyncio.sleep(.01)
            child_pid = int(pid.read_text())
            if outcome == 'cancel':
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 2)
            elif outcome == 'stop':
                await asyncio.wait_for(built.stop(), HANG_S)
            else:
                await asyncio.wait_for(task, HANG_S)
            with pytest.raises(ProcessLookupError):
                os.kill(child_pid, 0)
            assert not changed, 'an interrupted check must not publish a success/change'
            assert getattr(built, '_verifying', None) is None
            if outcome != 'stop':
                mode.write_text('ready')
                await asyncio.wait_for(built.verify_downloads(first_after=0), HANG_S)
                assert changed == [True]
        finally:
            # Finite outer watchdog for regressions: kill only the fixture's explicitly recorded owned child.
            if pid.exists():
                try:
                    os.kill(int(pid.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(go())
