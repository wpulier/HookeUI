import os
from pathlib import Path
import sys

import pytest

from orchestrator import envs, interpreter_identity


def test_managed_symlink_is_preserved_but_redirection_cannot_widen_grant(tmp_path):
    executable = tmp_path / 'runtime/bin/python'
    executable.parent.mkdir(parents=True)
    executable.symlink_to(sys.executable)
    identity = interpreter_identity.capture(executable)
    assert interpreter_identity.verify(executable, identity) == Path(sys.executable).resolve().parent.parent
    other = tmp_path / 'unrelated/bin/python'
    other.parent.mkdir(parents=True)
    other.write_text('#!/bin/sh\nexit 0\n')
    executable.unlink()
    executable.symlink_to(other)
    with pytest.raises(envs.Failed, match='changed after preparation'):
        interpreter_identity.verify(executable, identity)


def test_changed_interpreter_bytes_refuse_execution(tmp_path):
    executable = tmp_path / 'runtime/bin/python'
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b'original')
    identity = interpreter_identity.capture(executable)
    executable.write_bytes(b'changed!')
    with pytest.raises(envs.Failed, match='changed after preparation'):
        interpreter_identity.verify(executable, identity)


def test_older_receipt_uses_trusted_offline_managed_lookup(tmp_path, monkeypatch):
    executable = tmp_path / 'runtime/bin/python'
    executable.parent.mkdir(parents=True)
    executable.symlink_to(sys.executable)
    calls = []
    def lookup(command, environment, stop, what, cwd):
        calls.append(command)
        assert '--managed-python' in command and '--offline' in command
        assert '--no-config' in command and '--no-python-downloads' in command
        assert 'UV_PYTHON_PREFERENCE' not in environment
        return str(Path(sys.executable).resolve())
    monkeypatch.setattr(envs, '_run', lookup)
    result = interpreter_identity.recover(executable.parent.parent, '3.13.5', {})
    assert result == interpreter_identity.capture(executable) and len(calls) == 1
    assert not list(executable.parent.parent.glob('.interpreter-check-*'))
    for invalid in ('--system', '', None, '../3.13.5'):
        with pytest.raises(envs.Failed):
            interpreter_identity.recover(executable.parent.parent, invalid, {})
    assert len(calls) == 1


def test_special_file_is_refused_without_opening_it_for_execution(tmp_path):
    executable = tmp_path / 'runtime/bin/python'
    executable.parent.mkdir(parents=True)
    os.mkfifo(executable)
    with pytest.raises(envs.Failed):
        interpreter_identity.capture(executable)
