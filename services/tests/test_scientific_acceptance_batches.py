"""Bounded registered executions and locally sealed evidence, with synthetic subprocesses."""
from pathlib import Path
import json
import subprocess
from xml.sax.saxutils import escape

import pytest

from bench import scientific_acceptance as runner

A, B = 'P1.example.a', 'P1.example.b'
FILE = 'tests/test_example.py'
GUARD = FILE + '::test_guard'
CHECKS = {
    A: {'tests': (FILE + '::test_a', GUARD), 'class': 'synthetic_fixture'},
    B: {'tests': (FILE + '::test_b', GUARD), 'class': 'synthetic_fixture'},
}


def child(ident):
    return {'id': ident, 'evidence_class': 'synthetic_fixture', 'procedure': 'run', 'oracle': 'literal', 'criteria': 'pass'}


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path / 'checkout'
    (root / 'services/tests').mkdir(parents=True)
    (root / 'services' / FILE).write_text('def test_a(): pass\ndef test_b(): pass\ndef test_guard(): pass\n')
    (root / '.gitignore').write_text('/reports/\n__pycache__/\n')
    for args in (['init', '-q'], ['add', '-A'], ['-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm', 'base']):
        subprocess.run(['git', *args], cwd=root, check=True)
    monkeypatch.setattr(runner, 'ROOT', root)
    monkeypatch.setattr(runner, 'CHECKS', CHECKS)
    monkeypatch.setattr(runner, 'check_inputs', lambda files: {f'services/{f}': runner.digest(root / 'services' / f) for f in files})
    return root


def execute(root, monkeypatch, name, selected, *, outcomes=None, code=0, during=None):
    """Exercise the production runner/sealer; only the external pytest process is synthetic."""
    commands = []
    original = subprocess.Popen
    class Process:
        pid, returncode = 123456789, code
        def __init__(self, command, **kwargs):
            commands.append(command)
            selectors = [arg for arg in command if arg.startswith('tests/')]
            cases = outcomes if outcomes is not None else [(node, 'passed') for node in selectors if '::' in node]
            if FILE in selectors:
                cases += [(FILE + '::' + test, 'passed') for test in ('test_a', 'test_b', 'test_guard')]
            xml = []
            for node, outcome in cases:
                test = node.split('::', 1)[1]
                tag = {'passed': '', 'failed': '<failure/>', 'skipped': '<skipped/>'}[outcome]
                xml.append(f'<testcase classname="tests.test_example" name="{escape(test)}">{tag}</testcase>')
            path = Path(next(arg.split('=', 1)[1] for arg in command if arg.startswith('--junitxml=')))
            path.write_text('<testsuite>' + ''.join(xml) + '</testsuite>')
        def wait(self, timeout=None):
            assert timeout in (1800, 5, None)
            if during:
                during()
            return code
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda command, **kwargs: Process(command, **kwargs)
                        if '--junitxml=' in ' '.join(command) else original(command, **kwargs))
    directory = root / 'reports' / name
    result = runner.run_checks(directory / 'checks', checks=selected, jobs=1)
    (directory / 'report.json').write_text(json.dumps({'run': result}))
    return directory, result, commands[0]


def test_selected_child_executes_its_complete_registered_nodes_and_keeps_other_child_missing(checkout, monkeypatch):
    directory, result, command = execute(checkout, monkeypatch, 'a', {A: CHECKS[A]})
    assert [arg for arg in command if arg.startswith('tests/')] == [FILE + '::test_a', GUARD]
    assert runner.child_status(child(A), runner.reuse(directory))['status'] == 'pass'
    assert runner.child_status(child(B), result)['status'] == 'missing'
    assert result['selected'] == [A]


@pytest.mark.parametrize('problem', ['exit', 'timeout', 'source', 'runtime'])
def test_an_incomplete_or_changing_execution_never_admits_passing_junit(checkout, monkeypatch, problem):
    checks = {A: {**CHECKS[A], 'requires': ('RUNTIME_CONFIG',)}}
    monkeypatch.setattr(runner, 'CHECKS', checks)
    config = checkout.parent / 'runtime.json'
    config.write_text('{"generation":"one"}')
    monkeypatch.setenv('RUNTIME_CONFIG', str(config))
    def change():
        if problem == 'source':
            (checkout / 'services' / FILE).write_text('CHANGED = True\n')
        elif problem == 'runtime':
            config.write_text('{"generation":"two"}')
        elif problem == 'timeout':
            raise subprocess.TimeoutExpired('pytest', 1800)
    if problem == 'timeout':
        monkeypatch.setattr(runner.os, 'killpg', lambda *_: None)
    # The mock timeout fires once; the process then reaps at the normal five-second cleanup boundary.
    original_change = change
    calls = []
    def once():
        if not calls:
            calls.append(True)
            original_change()
    directory, result, _ = execute(checkout, monkeypatch, problem, checks, code=2 if problem == 'exit' else 0, during=once)
    assert runner.child_status(child(A), result)['status'] != 'pass'
    with pytest.raises(runner.InvalidAcceptance):
        runner.reuse(directory)


def test_aggregate_unions_complete_children_with_original_junit_provenance(checkout, monkeypatch):
    a, _, _ = execute(checkout, monkeypatch, 'a', {A: CHECKS[A]})
    b, _, _ = execute(checkout, monkeypatch, 'b', {B: CHECKS[B]})
    combined = runner.aggregate([a, b])
    for ident, directory in [(A, a), (B, b)]:
        status = runner.child_status(child(ident), combined)
        assert status['status'] == 'pass'
        assert {e['junit'] for e in status['evidence']} == {str(directory / 'checks/junit.xml')}
        assert {e['receipt'] for e in status['evidence']} == {str(directory / 'report.json')}
    destination = checkout / 'reports/combined'
    destination.mkdir()
    (destination / 'report.json').write_text(json.dumps({'run': combined}))
    assert runner.child_status(child(A), runner.reuse(destination))['status'] == 'pass'
    (a / 'checks/junit.xml').unlink()
    with pytest.raises(runner.InvalidAcceptance, match='missing'):
        runner.reuse(destination)


def test_split_prerequisites_and_conflicting_observations_cannot_be_combined(checkout, monkeypatch):
    a, _, _ = execute(checkout, monkeypatch, 'a', {A: CHECKS[A]}, outcomes=[(FILE + '::test_a', 'passed')])
    b, _, _ = execute(checkout, monkeypatch, 'b', {B: CHECKS[B]})
    with pytest.raises(runner.InvalidAcceptance, match='mandatory'):
        runner.aggregate([a, b])
    complete, _, _ = execute(checkout, monkeypatch, 'complete', {A: CHECKS[A]})
    conflicting, _, _ = execute(checkout, monkeypatch, 'conflict', {A: CHECKS[A]}, outcomes=[
        (FILE + '::test_a', 'failed'), (GUARD, 'passed')])
    with pytest.raises(runner.InvalidAcceptance):
        runner.aggregate([complete, conflicting])


def test_aggregate_rejects_raw_junit_foreign_or_repeated_sources(checkout, monkeypatch):
    a, _, _ = execute(checkout, monkeypatch, 'a', {A: CHECKS[A]})
    with pytest.raises((runner.InvalidAcceptance, OSError)):
        runner.aggregate([a / 'checks/junit.xml'])
    with pytest.raises(runner.InvalidAcceptance, match='duplicate'):
        runner.aggregate([a, a])
    old = runner.ROOT
    monkeypatch.setattr(runner, 'ROOT', old.parent / 'foreign')
    with pytest.raises(runner.InvalidAcceptance):
        runner.aggregate([a])


def test_selection_is_only_registered_children_and_trusted_harnesses():
    selected = runner.select_checks(harnesses=['h04'])
    assert selected == runner.scientific_checks_h04.checks()
    assert all('test_the_comparison_catches_a_worker_whose_napari_behaviour_was_broken' in ' '.join(c['tests'])
               for c in selected.values())
    one = next(iter(selected))
    assert runner.select_checks(children=[one]) == {one: runner.CHECKS[one]}
    remaining = runner.select_checks(harnesses=['remaining'])
    assert not set(remaining) & set(selected)
    with pytest.raises(runner.InvalidAcceptance):
        runner.select_checks(children=['tests/test_example.py::test_any'])
    with pytest.raises(runner.InvalidAcceptance):
        runner.select_checks(harnesses=['../arbitrary-command'])


def test_cli_selects_registered_children_without_shrinking_report(tmp_path, monkeypatch):
    ident = next(iter(runner.CHECKS))
    chosen = []
    monkeypatch.setattr(runner, 'run_checks', lambda destination, **options: chosen.append(options['checks']))
    destination = tmp_path / 'report'
    monkeypatch.setattr(runner.sys, 'argv', ['acceptance', '--child', ident, '--jobs', '1',
        '--output', str(destination), '--archive', str(tmp_path / 'absent.zip')])
    assert runner.main() == 0
    assert chosen == [{ident: runner.CHECKS[ident]}]
    report = json.loads((destination / 'report.json').read_text())
    assert len(report['children']) > 3000
    assert report['completion'] is False and report['qualification_eligible'] is False


def test_cli_repeated_from_uses_original_sealed_receipts(checkout, monkeypatch):
    a, _, _ = execute(checkout, monkeypatch, 'a', {A: CHECKS[A]})
    b, _, _ = execute(checkout, monkeypatch, 'b', {B: CHECKS[B]})
    # Keep the full real inventory in the report; synthetic registrations only exercise aggregation.
    monkeypatch.setattr(runner, 'regression_inventory', lambda: None)
    monkeypatch.setattr(runner, 'load_inventory', lambda **_: {'children': [], 'groups': {},
        'census': {'unreviewed': 0}})
    destination = checkout / 'reports/combined-cli'
    monkeypatch.setattr(runner.sys, 'argv', ['acceptance', '--from', str(a), '--from', str(b),
        '--output', str(destination), '--archive', str(checkout / 'absent.zip')])
    assert runner.main() == 0
    admitted = runner.reuse(destination)
    assert runner.child_status(child(A), admitted)['status'] == 'pass'
    assert runner.child_status(child(B), admitted)['status'] == 'pass'


@pytest.mark.parametrize('mutation', ['runtime-config', 'missing-leaf', 'edited-cases', 'edited-junit', 'stale-source'])
def test_aggregation_never_launders_changed_or_missing_originals(checkout, monkeypatch, mutation):
    config = checkout.parent / 'runtime.json'
    config.write_text('{"generation":"one"}')
    checks = {A: {**CHECKS[A], 'requires': ('RUNTIME_CONFIG',)}}
    monkeypatch.setattr(runner, 'CHECKS', checks)
    monkeypatch.setenv('RUNTIME_CONFIG', str(config))
    directory, _, _ = execute(checkout, monkeypatch, 'a', checks)
    if mutation == 'runtime-config':
        config.write_text('{"generation":"two"}')
    elif mutation == 'missing-leaf':
        (directory / 'report.json').unlink()
    elif mutation == 'edited-cases':
        report = json.loads((directory / 'report.json').read_text())
        report['run']['selected'] = []
        (directory / 'report.json').write_text(json.dumps(report))
    elif mutation == 'edited-junit':
        (directory / 'checks/junit.xml').write_text('<testsuite/>')
    else:
        (checkout / 'services' / FILE).write_text('CHANGED = True\n')
    with pytest.raises((runner.InvalidAcceptance, OSError)):
        runner.aggregate([directory])


def test_consistent_duplicate_runs_keep_provenance_without_extra_child_credit(checkout, monkeypatch):
    first, _, _ = execute(checkout, monkeypatch, 'first', {A: CHECKS[A]})
    second, _, _ = execute(checkout, monkeypatch, 'second', {A: CHECKS[A]})
    result = runner.aggregate([first, second])
    status = runner.child_status(child(A), result)
    assert status['status'] == 'pass'
    assert {e['receipt'] for e in status['evidence']} == {str(p / 'report.json') for p in (first, second)}
    assert runner.child_status(child(B), result)['status'] == 'missing'


def test_different_runtime_configurations_cannot_supply_duplicate_guards(checkout, monkeypatch):
    checks = {ident: {**check, 'requires': ('RUNTIME_CONFIG',)} for ident, check in CHECKS.items()}
    monkeypatch.setattr(runner, 'CHECKS', checks)
    paths = []
    for name, ident in [('one', A), ('two', B)]:
        config = checkout.parent / (name + '.json')
        config.write_text(json.dumps({'generation': name}))
        monkeypatch.setenv('RUNTIME_CONFIG', str(config))
        directory, _, _ = execute(checkout, monkeypatch, name, {ident: checks[ident]})
        paths.append(directory)
    with pytest.raises(runner.InvalidAcceptance, match='conflicting runtime'):
        runner.aggregate(paths)


def test_nested_aggregate_cannot_replace_original_execution(checkout, monkeypatch):
    leaf, _, _ = execute(checkout, monkeypatch, 'a', {A: CHECKS[A]})
    directory = checkout / 'reports/aggregate'
    directory.mkdir()
    (directory / 'report.json').write_text(json.dumps({'run': runner.aggregate([leaf])}))
    with pytest.raises(runner.InvalidAcceptance, match='nested'):
        runner.aggregate([directory])


def test_failed_selected_run_keeps_unselected_children_missing(checkout, monkeypatch):
    _, result, _ = execute(checkout, monkeypatch, 'failed', {A: CHECKS[A]}, code=2)
    assert runner.child_status(child(A), result)['status'] == 'fail'
    assert runner.child_status(child(B), result)['status'] == 'missing'


def test_small_real_pytest_executions_produce_reusable_sealed_leaves(checkout):
    directories = []
    for name, ident in [('a', A), ('b', B)]:
        directory = checkout / 'reports' / name
        result = runner.run_checks(directory / 'checks', checks={ident: CHECKS[ident]}, jobs=1)
        assert result['exit'] == 0 and not result['timed_out']
        (directory / 'report.json').write_text(json.dumps({'run': result}))
        directories.append(directory)
    result = runner.aggregate(directories)
    assert runner.child_status(child(A), result)['status'] == 'pass'
    assert runner.child_status(child(B), result)['status'] == 'pass'


@pytest.mark.parametrize('timeout', [0, 1801, True])
def test_execution_deadline_cannot_be_removed_or_extended(tmp_path, timeout):
    with pytest.raises(runner.InvalidAcceptance, match='bounded deadline'):
        runner.run_checks(tmp_path / 'never-created', timeout=timeout)
    assert not (tmp_path / 'never-created').exists()


def test_old_single_receipt_without_current_execution_contract_is_refused(checkout, monkeypatch):
    directory, run, _ = execute(checkout, monkeypatch, 'a', {A: CHECKS[A]})
    legacy = {k: v for k, v in run.items() if k not in ('kind', 'selected', 'registration', 'selectors', 'seal')}
    (directory / 'report.json').write_text(json.dumps({'run': runner.seal_run(legacy)}))
    with pytest.raises(runner.InvalidAcceptance, match='current sealed execution'):
        runner.reuse(directory)


def test_raw_tmp_path_evidence_stays_inside_each_original_execution(checkout):
    (checkout / 'services' / FILE).write_text(
        'def test_a(tmp_path):\n'
        '    (tmp_path / "raw-observation.json").write_text("{\\"observed\\": true}")\n'
        'def test_guard(): pass\n')
    first = checkout / 'reports/first/checks'
    second = checkout / 'reports/second/checks'
    run_one = runner.run_checks(first, checks={A: CHECKS[A]}, jobs=1)
    run_two = runner.run_checks(second, checks={A: CHECKS[A]}, jobs=1)
    assert run_one['exit'] == run_two['exit'] == 0
    for directory in (first, second):
        evidence = list((directory / 'temporary').rglob('raw-observation.json'))
        assert len(evidence) == 1
        assert json.loads(evidence[0].read_text()) == {'observed': True}
    assert f'--basetemp={first / "temporary"}' in run_one['command']
    assert f'--basetemp={second / "temporary"}' in run_two['command']
    with pytest.raises(runner.InvalidAcceptance, match='empty'):
        runner.run_checks(first, checks={A: CHECKS[A]}, jobs=1)
    assert list((first / 'temporary').rglob('raw-observation.json'))
