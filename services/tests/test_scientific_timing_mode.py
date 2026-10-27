"""Synthetic acceptance invocation checks; no timing or native qualification is performed."""
import sys

import pytest

from bench import scientific_acceptance as acceptance


@pytest.mark.parametrize('quiet,jobs', [(False, 4), (False, 1), (True, 1)])
def test_quiet_mode_is_explicit_serial_and_bound_into_the_sealed_run(tmp_path, monkeypatch, quiet, jobs):
    observed = {}
    class Child:
        pid, returncode = 0, 0
        def __init__(self, command, **kwargs): observed['command'] = command
        def wait(self, timeout):
            observed['timeout'] = timeout
            return 0
    monkeypatch.setattr(acceptance.subprocess, 'Popen', Child)
    monkeypatch.setattr(acceptance, 'tree_identity', dict)
    monkeypatch.setattr(acceptance, 'check_inputs', lambda files: {})
    def seal(run):
        observed['sealed_execution'] = dict(run['execution'])
        return run
    monkeypatch.setattr(acceptance, 'seal_run', seal)
    checks = {'case': {'tests': ('tests/test_scientific_acceptance.py::test_case',), 'class': 'synthetic_fixture'}}
    result = acceptance.run_checks(tmp_path, checks=checks, jobs=jobs, quiet_timing=quiet)
    command = observed['command']
    assert ('--timing' in command) is quiet
    assert ('no:xdist' in command) is quiet
    assert ('-n' in command) is (jobs > 1)
    assert observed['sealed_execution'] == {'jobs': jobs, 'quiet_timing': quiet, 'timeout': 1800}
    assert result['execution'] == observed['sealed_execution']
    assert observed['timeout'] == 1800
    assert not any(arg.startswith('-m ') or arg == '--maxfail' for arg in command)


@pytest.mark.parametrize('jobs', [0, 2, 4])
def test_quiet_mode_refuses_nonserial_configuration_before_any_work(tmp_path, jobs):
    target = tmp_path / 'uncreated'
    with pytest.raises(acceptance.InvalidAcceptance, match='jobs.*1'):
        acceptance.run_checks(target, checks={}, jobs=jobs, quiet_timing=True)
    assert not target.exists()


@pytest.mark.parametrize('extra', [[], ['--jobs', '2'], ['--jobs', '1', '--no-run'],
    ['--jobs', '1', '--from', '/previous'], ['--jobs', '1', '--list'],
    ['--jobs', '1', '--ledger'], ['--jobs', '1', '--case', 'R01.producing-history']])
def test_cli_refuses_quiet_mode_that_cannot_execute_a_serial_full_run(monkeypatch, capsys, extra):
    monkeypatch.setattr(sys, 'argv', ['scientific-acceptance', '--quiet-timing', *extra])
    with pytest.raises(SystemExit) as caught:
        acceptance.main()
    assert caught.value.code == 2
    error = capsys.readouterr().err
    assert '--quiet-timing requires' in error


def test_cli_forwards_quiet_mode_to_registered_checks(tmp_path, monkeypatch, capsys):
    observed = {}
    def run(destination, **kwargs):
        observed.update(destination=destination, **kwargs)
        raise acceptance.InvalidAcceptance('synthetic stop before subprocess')
    monkeypatch.setattr(acceptance, 'run_checks', run)
    monkeypatch.setattr(sys, 'argv', ['scientific-acceptance', '--quiet-timing', '--jobs', '1',
                                    '--output', str(tmp_path / 'report')])
    assert acceptance.main() == 1
    assert observed == {'destination': tmp_path / 'report' / 'checks', 'jobs': 1, 'quiet_timing': True,
                        'checks': acceptance.CHECKS}
    assert 'synthetic stop' in capsys.readouterr().err
    assert not (tmp_path / 'report').exists()
