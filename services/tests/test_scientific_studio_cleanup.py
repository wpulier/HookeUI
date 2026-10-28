"""Synthetic process-boundary failures; these never start an app or native worker."""
import signal
from types import SimpleNamespace

import pytest

from tests import scientific_studio_stack as stacks


def test_stack_readiness_timeout_reaps_its_process_and_removes_private_home(tmp_path, monkeypatch):
    home = tmp_path / 'private-home'
    def temporary_home(**kwargs):
        home.mkdir()
        return str(home)
    class Process:
        returncode = None
        def poll(self): return self.returncode
        def send_signal(self, value): assert value == signal.SIGINT
        def wait(self, timeout): self.returncode = 0
        def kill(self): self.returncode = -9
    process = Process()
    monkeypatch.setattr(stacks.tempfile, 'mkdtemp', temporary_home)
    monkeypatch.setattr(stacks.subprocess, 'Popen', lambda *a, **k: process)
    clock = iter([0, 121])
    # Replace this module's clock, not Python's shared time module: unrelated
    # background writers may still be alive in the same test process.
    monkeypatch.setattr(stacks, 'time', SimpleNamespace(monotonic=lambda: next(clock), sleep=stacks.time.sleep))
    with pytest.raises(RuntimeError, match='did not become ready'):
        stacks.Stack({'environment': {'MICROMIND_CATALOG': str(tmp_path), 'MICROMIND_ENVS': str(tmp_path)}}, build=False)
    assert process.poll() is not None
    assert not home.exists()


def test_browser_fixture_restores_signal_when_stack_construction_fails(monkeypatch):
    from tests import test_scientific_dims_browser as browser
    original = object()
    installed = [original]
    def change_signal(number, handler):
        assert number == signal.SIGTERM
        previous, installed[0] = installed[0], handler
        return previous
    monkeypatch.setattr(signal, 'signal', change_signal)
    monkeypatch.setattr(browser, 'preparation', lambda: {})
    monkeypatch.setattr(browser.browser_cdp, 'executable', lambda: '/synthetic/browser')
    # Failure at the external build boundary, before any subprocess exists.
    def unavailable_build(): raise RuntimeError('synthetic build failure')
    monkeypatch.setattr(stacks, 'build_studio', unavailable_build)
    fixture = browser.studio.__wrapped__()
    with pytest.raises(RuntimeError, match='synthetic build failure'):
        next(fixture)
    assert installed[0] is original
