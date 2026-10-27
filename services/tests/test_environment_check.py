"""Actual offline checker over synthetic distribution metadata; not package qualification."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from orchestrator import environment_check as checker, envs, sandbox, owned_process
from tests.waiting import HANG_S, until

pytestmark = pytest.mark.skipif(sys.platform != 'darwin' or not sandbox.available(), reason='actual macOS confinement')


@pytest.fixture
def private(monkeypatch):
    monkeypatch.delenv('MICROMIND_SANDBOX_READ', raising=False)
    return SimpleNamespace(namespace='mine', members=(), channel=None)


def runtime(tmp_path):
    folder = tmp_path / 'runtime'
    folder.mkdir()
    (folder / 'bin').mkdir()
    (folder / 'bin/python').symlink_to(sys.executable)
    return folder


@pytest.mark.parametrize('dependency', [None, '1', '2'])
def test_actual_checker_detects_missing_and_conflicting_requirements(tmp_path, private, dependency):
    tool = shutil.which('uv')
    if not tool:
        pytest.skip('test needs the existing uv executable; it installs nothing')
    folder = tmp_path / 'runtime'
    subprocess.run([tool, 'venv', '--offline', '--python', sys.executable, str(folder)],
                   env={'PATH': '/usr/bin:/bin', 'HOME': str(tmp_path)},
                   check=True, capture_output=True, timeout=HANG_S)
    site = folder / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'
    parent = site / 'example-1.dist-info'
    parent.mkdir()
    (parent / 'METADATA').write_text('Metadata-Version: 2.1\nName: example\nVersion: 1\nRequires-Dist: required-dependency>=2\n')
    if dependency:
        child = site / f'required_dependency-{dependency}.dist-info'
        child.mkdir()
        (child / 'METADATA').write_text(f'Metadata-Version: 2.1\nName: required-dependency\nVersion: {dependency}\n')
    if dependency != '2':
        with pytest.raises(envs.Failed, match='incomplete or incompatible'):
            checker.check(private, folder, tool, {})
    else:
        assert checker.check(private, folder, tool, {})['ok'] is True
    assert not (folder / envs.RECEIPT).exists()


def script(tmp_path, body):
    tool = tmp_path / 'checker'
    tool.write_text('#!/bin/sh\n' + body)
    tool.chmod(0o700)
    return tool


def test_checker_output_is_bounded_and_unrelated_data_survives(tmp_path, private):
    folder = runtime(tmp_path)
    protected = tmp_path / 'protected'
    protected.write_text('keep')
    tool = script(tmp_path, 'while true; do printf "lots of output\\n"; done\n')
    with pytest.raises(envs.Failed, match='output allowance'):
        checker.check(private, folder, tool, {})
    assert protected.read_text() == 'keep'


def test_checker_cancellation_reaps_its_owned_process(tmp_path, private):
    folder = runtime(tmp_path)
    tool = script(tmp_path, 'echo $$ > owner.pid\nwhile true; do :; done\n')
    stop = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(checker.check, private, folder, tool, {}, stop=stop.is_set)
        until(lambda: list(folder.glob('.dependency-check-*/owner.pid')))
        pid = int(next(folder.glob('.dependency-check-*/owner.pid')).read_text())
        stop.set()
        with pytest.raises(envs.Failed, match='cancelled'):
            future.result(timeout=HANG_S)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_no_dependency_check_runs_without_required_confinement(tmp_path, private, monkeypatch):
    folder = runtime(tmp_path)
    tool = script(tmp_path, 'echo launched > bad\n')
    monkeypatch.setattr(sandbox, 'available', lambda: False)
    private.name = 'Private tool'
    with pytest.raises(envs.Failed, match='not run without'):
        checker.check(private, folder, tool, {})
    assert not list(folder.glob('.dependency-check-*'))


def test_late_supervision_failure_cannot_return_a_successful_check(tmp_path, private, monkeypatch):
    folder = runtime(tmp_path)
    tool = script(tmp_path, 'exit 0\n')
    class LateFailure:
        def __init__(self, *args):
            self.closed = False
        def start(self):
            return self
        def close(self):
            self.closed = True
        def check(self):
            if self.closed:
                raise owned_process.Stopped('late supervision failure')
    monkeypatch.setattr(checker.worker_resources, 'Guard', LateFailure)
    with pytest.raises(envs.Failed, match='late supervision failure'):
        checker.check(private, folder, tool, {})
