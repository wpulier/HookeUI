"""Closure invariants; these fixtures do not qualify the scientific workspace."""
import copy
import json

import pytest

from bench import scientific_acceptance as acceptance


@pytest.fixture
def ledger():
    return json.loads(acceptance.LEDGER.read_text())


def test_current_inventory_is_complete_as_groups_but_not_qualified(ledger):
    result = acceptance.validate(ledger)
    assert result['groups'] == 71
    assert result['qualified'] == 0 and not result['completion']


@pytest.mark.parametrize('mutation', ['missing','duplicate','unexpected','optional','no-ui','no-oracle','no-gate','source'])
def test_incomplete_inventory_cannot_be_validated(ledger,mutation):
    row = ledger['cases'][0]
    if mutation == 'missing': ledger['cases'].pop()
    elif mutation == 'duplicate': ledger['cases'].append(copy.deepcopy(row))
    elif mutation == 'unexpected': row['id'] = 'P1.unreviewed'
    elif mutation == 'optional': row['required'] = False
    elif mutation == 'no-ui': row['required_hooke_interface'] = None
    elif mutation == 'no-oracle': row['oracle'] = ''
    elif mutation == 'no-gate': row['qualification'].pop('usable')
    elif mutation == 'source': row['reference']['sources'] = ['missing.py']
    with pytest.raises(acceptance.InvalidAcceptance): acceptance.validate(ledger)


@pytest.mark.parametrize('where', ['group','configured','operational','usable','macos','windows','linux'])
@pytest.mark.parametrize('claim', ['qualified','passed','skipped'])
def test_flags_cannot_self_certify_even_with_plausible_evidence(ledger,where,claim):
    row = ledger['cases'][0]
    target = row if where == 'group' else row['qualification' if where in row['qualification'] else 'platforms'][where]
    target['status'] = claim
    target['evidence'] = [{'commit':'a'*40,'artifact':'b'*64,'signer':'the contributor','passed':True}]
    with pytest.raises(acceptance.InvalidAcceptance,match='authenticated'): acceptance.validate(ledger)


@pytest.mark.parametrize('body,passed', [
    ('<testsuites><testsuite><testcase name="real"/></testsuite></testsuites>',True),
    ('<testsuite><testcase><skipped/></testcase></testsuite>',False),
    ('<testsuite><testcase><failure/></testcase></testsuite>',False),
    ('<testsuite><testcase><error/></testcase></testsuite>',False),
])
def test_runner_requires_non_skipped_actual_results(tmp_path,body,passed):
    p=tmp_path/'result.xml';p.write_text(body)
    assert acceptance.junit_outcome(p)['passed'] is passed


@pytest.mark.parametrize('body',['','<testsuite/>','<invalid'])
def test_missing_or_empty_execution_does_not_pass(tmp_path,body):
    p=tmp_path/'result.xml';p.write_text(body)
    with pytest.raises(acceptance.InvalidAcceptance): acceptance.junit_outcome(p)


def test_unknown_case_never_runs_imported_commands(tmp_path):
    with pytest.raises(acceptance.InvalidAcceptance,match='no registered execution'):
        acceptance.run('P1.axis-position',tmp_path/'run')
    assert not (tmp_path/'run').exists()


def test_successful_component_run_does_not_satisfy_require_complete(monkeypatch, tmp_path):
    monkeypatch.setattr(acceptance.sys, 'argv', ['acceptance', '--case', 'R09.truthful-closure',
        '--output', str(tmp_path/'run'), '--require-complete'])
    monkeypatch.setattr(acceptance, 'run', lambda *args: {'passed': True, 'qualification_eligible': False})
    assert acceptance.main() == 1


def test_regression_inventory_keeps_partial_coverage_explicit():
    inventory = acceptance.regression_inventory()
    assert len(inventory['regressions']) == 9
    assert not inventory['completion'] and not inventory['qualification_eligible']
    for entry in inventory['regressions'].values():
        assert entry['tests'] and entry['scope'] and entry['remaining']


@pytest.mark.parametrize('mutation', ['missing', 'scope', 'remaining', 'no-tests', 'path', 'command', 'type'])
def test_invalid_execution_mapping_is_refused(monkeypatch, mutation):
    entries = copy.deepcopy(acceptance.REGRESSIONS)
    entry = entries['R04.resource-only-code']
    if mutation == 'missing': entries.pop('R04.resource-only-code')
    elif mutation in {'scope', 'remaining'}: entry[mutation] = ''
    elif mutation == 'no-tests': entry['tests'] = ()
    elif mutation == 'path': entry['tests'] = ('tests/test_missing.py',)
    elif mutation == 'command': entry['tests'] = ('../tests/test_scientific_resource_edits.py',)
    elif mutation == 'type': entry['tests'] = (None,)
    monkeypatch.setattr(acceptance, 'REGRESSIONS', entries)
    with pytest.raises(acceptance.InvalidAcceptance): acceptance.regression_inventory()


def test_actual_component_receipt_retains_limits_and_cannot_qualify(tmp_path):
    report = acceptance.run('R04.resource-only-code', tmp_path / 'evidence')
    assert report['passed'] and report['result']['tests'] > 0
    assert report['class'] == 'synthetic_fixture'
    assert report['remaining'] == acceptance.REGRESSIONS[report['case']]['remaining']
    assert not report['completion'] and not report['qualification_eligible']
    assert report['inputs']['services/tests/conftest.py'] == acceptance.digest(
        acceptance.ROOT / 'services/tests/conftest.py')
    assert report['artifacts']['junit.xml'] == acceptance.digest(tmp_path / 'evidence/junit.xml')


def test_listing_components_does_not_satisfy_require_complete(monkeypatch):
    monkeypatch.setattr(acceptance.sys, 'argv', ['acceptance', '--list', '--require-complete'])
    assert acceptance.main() == 1


# Atomic children and their roll-up. Each R09.truthful-closure child names the test below
# that executes it (scientific_acceptance.CHECKS).

CHILD_ID = 'R09.truthful-closure.example'
CHECK = {CHILD_ID: {'tests': ('tests/test_example.py::test_case',), 'class': 'synthetic_fixture'}}


def _child(**changes):
    child = {'id': CHILD_ID, 'group': 'R09.truthful-closure', 'owner': 'H00/H14/H15', 'kind': 'procedure',
             'requirement': 'Refuse closure', 'evidence_class': 'synthetic_fixture',
             'procedure': 'Run it', 'oracle': 'The refusal', 'criteria': 'Zero skips'}
    child.update(changes)
    return child


def _results(outcome='passed', node='tests/test_example.py::test_case[one]'):
    return {'junit': '/evidence/junit.xml', 'cases': [{'node': node, 'outcome': outcome}]}


@pytest.fixture(scope='module')
def inventory():
    return acceptance.load_inventory()


def test_a_registered_passing_check_passes_only_its_own_child():
    status = acceptance.child_status(_child(), _results(), CHECK)
    assert status['status'] == 'pass'
    assert status['evidence'] == [{'junit': '/evidence/junit.xml', 'test': 'tests/test_example.py::test_case[one]',
                                   'outcome': 'passed'}]
    other = acceptance.child_status(_child(id='R09.truthful-closure.other'), _results(), CHECK)
    assert other['status'] == 'missing'


def test_missing_child_keeps_group_open(inventory):
    assert acceptance.group_status(['pass', 'missing']) == 'missing'
    assert acceptance.group_status(['pass', 'external']) == 'external'
    assert acceptance.group_status(['pass', 'external', 'missing', 'fail']) == 'fail'
    assert acceptance.group_status([]) == 'missing'
    assert acceptance.group_status(['pass', 'pass']) == 'pass'
    data = copy.deepcopy(inventory)
    data['children'] = [c for c in data['children'] if c['group'] != 'P1.playback']
    with pytest.raises(acceptance.InvalidAcceptance, match='P1.playback'):
        acceptance.load_inventory(data)


def test_lower_class_check_cannot_pass_manual_child():
    status = acceptance.child_status(_child(evidence_class='isolated_profile_browser'), _results(), CHECK)
    assert status['status'] == 'missing' and 'needs isolated_profile_browser' in status['reason']


@pytest.fixture
def sealed_checkout(tmp_path, monkeypatch):
    """A git checkout of its own with one sealed run, so staleness is the real tree's."""
    import subprocess
    root = tmp_path / 'checkout'
    (root / 'services').mkdir(parents=True)
    (root / '.gitignore').write_text('/reports/\n')
    (root / 'services/under_test.py').write_text('VALUE = 1\n')
    (root / 'services/tests').mkdir()
    (root / 'services/tests/test_example.py').write_text('def test_case(): pass\n')
    for args in (['init', '-q'], ['add', '-A'], ['-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm', 'base']):
        subprocess.run(['git', *args], cwd=root, check=True)
    monkeypatch.setattr(acceptance, 'ROOT', root)
    monkeypatch.setattr(acceptance, 'CHECKS', CHECK)
    previous = root / 'reports/previous'
    previous.mkdir(parents=True)
    junit = previous / 'junit.xml'
    junit.write_text('<testsuite><testcase classname="tests.test_example" name="test_case[one]"/></testsuite>')
    tree = acceptance.tree_identity()
    run = acceptance.seal_run({**_results(), 'kind': 'execution-v2', 'selected': [CHILD_ID],
        'registration': CHECK, 'selectors': list(CHECK[CHILD_ID]['tests']), 'exit': 0, 'timed_out': False,
        'junit': str(junit), 'tree': tree, 'tree_after': tree, 'inputs': {}, 'inputs_after': {},
        'runtime': {}, 'runtime_inputs': {}, 'runtime_inputs_after': {}})
    (previous / 'report.json').write_text(json.dumps({'run': run}))
    return root, previous


def test_stale_evidence_is_not_current(sealed_checkout):
    root, previous = sealed_checkout
    assert acceptance.child_status(_child(), acceptance.reuse(previous), CHECK)['status'] == 'pass'
    (root / 'services/under_test.py').write_text('VALUE = 2\n')  # the code under test, not a hashed test file
    status = acceptance.child_status(_child(), acceptance.reuse(previous), CHECK)
    assert status['status'] == 'missing' and 'code under test changed' in status['reason']
    (root / 'services/under_test.py').write_text('VALUE = 1\n')
    (root / 'services/new_module.py').write_text('NEW = True\n')
    assert 'untracked_sha256' in acceptance.child_status(_child(), acceptance.reuse(previous), CHECK)['reason']


@pytest.mark.parametrize('forgery', ['edited', 'unsigned', 'foreign', 'junit', 'no-key'])
def test_retained_reports_cannot_be_edited_or_imported(sealed_checkout, forgery):
    root, previous = sealed_checkout
    report = json.loads((previous / 'report.json').read_text())
    run = report['run']
    if forgery == 'edited': run['cases'][0]['outcome'] = 'passed' if run['cases'][0]['outcome'] != 'passed' else 'skipped'
    elif forgery == 'unsigned': run.pop('seal')
    elif forgery == 'foreign':
        run['checkout'] = '/another/checkout'
        run['seal'] = acceptance._seal(run, acceptance._seal_key())
    elif forgery == 'junit': (previous / 'junit.xml').write_text('<testsuite><testcase name="forged"/></testsuite>')
    elif forgery == 'no-key': (root / 'reports/scientific-acceptance/.seal-key').unlink()
    (previous / 'report.json').write_text(json.dumps(report))
    with pytest.raises(acceptance.InvalidAcceptance, match='refused'):
        acceptance.reuse(previous)


@pytest.mark.parametrize('evidence', ['synthetic_fixture', 'clean_os_installation'])
@pytest.mark.parametrize('field', ['procedure', 'oracle', 'criteria'])
def test_null_criterion_cannot_pass(field, evidence):
    # An unspecified child is not ready even when outside evidence would later qualify it.
    status = acceptance.child_status(_child(**{field: None}, evidence_class=evidence), _results(), CHECK)
    assert status['status'] == 'missing' and f'null {field}' in status['reason']


@pytest.mark.parametrize('outcome', ['skipped', 'failed'])
def test_skipped_test_fails_child(outcome):
    assert acceptance.child_status(_child(), _results(outcome), CHECK)['status'] == 'fail'
    nothing = acceptance.child_status(_child(), _results(node='tests/test_example.py::test_other'), CHECK)
    assert nothing['status'] == 'fail' and 'executed no test' in nothing['reason']


def test_a_check_whose_runtime_is_absent_stays_missing(tmp_path, monkeypatch):
    needs = {CHILD_ID: {**CHECK[CHILD_ID], 'requires': ('NAPARI_REFERENCE_PYTHON',)}}
    # Without the runtime its case skips: missing, never a pass and never a failure of Hooke.
    for results in (_results('skipped'), {**_results(), 'runtime': {}}):
        status = acceptance.child_status(_child(), results, needs)
        assert status['status'] == 'missing' and 'NAPARI_REFERENCE_PYTHON' in status['reason']
    present = {'runtime': {'NAPARI_REFERENCE_PYTHON': '/runtime/bin/python'}}
    assert acceptance.child_status(_child(), {**_results(), **present}, needs)['status'] == 'pass'
    assert acceptance.child_status(_child(), {**_results('skipped'), **present}, needs)['status'] == 'fail'
    # The runner passes a required runtime through by its path and records it; nothing else leaks in.
    seen = {}
    class Child:
        pid, returncode = 0, 0
        def __init__(self, command, *, env, **_):
            seen.update(env)
        def wait(self, timeout=None):
            return 0
    monkeypatch.setattr(acceptance.subprocess, 'Popen', Child)
    monkeypatch.setattr(acceptance, 'seal_run', lambda run: run)
    monkeypatch.setattr(acceptance, 'tree_identity', dict)
    monkeypatch.setenv('NAPARI_REFERENCE_PYTHON', '/runtime/bin/python')
    monkeypatch.setenv('UNRELATED_SECRET', 'never passed')
    checks = {CHILD_ID: {'tests': ('tests/test_scientific_acceptance.py::test_case',), 'class': 'synthetic_fixture',
                         'requires': ('NAPARI_REFERENCE_PYTHON',)}}
    run = acceptance.run_checks(tmp_path / 'checks', checks=checks, jobs=1)
    assert run['runtime'] == {'NAPARI_REFERENCE_PYTHON': '/runtime/bin/python'}
    assert seen['NAPARI_REFERENCE_PYTHON'] == '/runtime/bin/python' and 'UNRELATED_SECRET' not in seen
    monkeypatch.delenv('NAPARI_REFERENCE_PYTHON')
    assert acceptance.run_checks(tmp_path / 'again', checks=checks, jobs=1)['runtime'] == {}


@pytest.mark.parametrize('declared', ['status', 'evidence'])
def test_external_evidence_cannot_be_declared_in_the_inventory(inventory, declared):
    external = _child(evidence_class='clean_os_installation')
    assert acceptance.child_status(external, _results(), CHECK)['status'] == 'external'
    data = copy.deepcopy(inventory)
    data['children'][0][declared] = 'pass' if declared == 'status' else [{'signer': 'the contributor', 'passed': True}]
    with pytest.raises(acceptance.InvalidAcceptance, match='executed checks'):
        acceptance.load_inventory(data)


def test_committed_inventory_covers_every_group_ledger_case_and_reviewed_row(inventory):
    groups = {c['group'] for c in inventory['children']}
    assert len(inventory['groups']) == 80 and groups == set(inventory['groups'])
    ledger = {c['id'] for c in json.loads(acceptance.LEDGER.read_text())['cases']}
    assert ledger <= groups
    ids = {c['id'] for c in inventory['children']}
    assert {'P4.label-tools.encircle-start', 'P4.shape-edit.back', 'R09.truthful-closure.failed-simulation'} <= ids
    assert set(acceptance.CHECKS) <= ids
    assert not inventory['qualification_eligible']


@pytest.mark.parametrize('mutation', ['duplicate', 'unknown-group', 'prefix', 'owner', 'stale-count', 'orphan-check',
                                      'reviewed-row', 'archive', 'no-requirement', 'unhashed-source',
                                      'rules-drift', 'manual-drift'])
def test_incomplete_or_forged_inventory_is_refused(inventory, mutation):
    data = copy.deepcopy(inventory)
    checks = dict(acceptance.CHECKS)
    first = data['children'][0]
    if mutation == 'duplicate': data['children'].append(copy.deepcopy(first))
    elif mutation == 'unknown-group': first['group'] = 'P1.unreviewed'
    elif mutation == 'prefix': first['id'] = 'P9.somewhere.else'
    elif mutation == 'owner': first['owner'] = 'H99'
    elif mutation == 'stale-count': data['groups'][first['group']]['children'] += 1
    elif mutation == 'orphan-check': checks['R09.truthful-closure.invented'] = checks['R09.truthful-closure.component']
    elif mutation == 'reviewed-row':
        data['children'] = [c for c in data['children'] if c['id'] != 'P4.label-tools.encircle-start']
        data['groups']['P4.label-tools']['children'] -= 1
    elif mutation == 'archive': data['archive_sha256'] = '0' * 64
    elif mutation == 'no-requirement': first['requirement'] = ' '
    elif mutation in ('rules-drift', 'manual-drift'): data[mutation.split('-')[0] + '_sha256'] = '0' * 64
    elif mutation == 'unhashed-source':
        sourced = next(c for c in data['children'] if c.get('source', {}).get('path'))
        data['sources'].pop(sourced['source']['path'])
    with pytest.raises(acceptance.InvalidAcceptance):
        acceptance.load_inventory(data, checks=checks)


def test_report_follows_children_and_never_completes(inventory, tmp_path):
    report = acceptance.acceptance_report(inventory, None)
    assert report['totals']['children']['pass'] == 0 and not report['completion']
    assert report['children']['R04.resource-only-code.component']['reason'] == 'not run in this invocation'
    every_check_passes = {'junit': str(tmp_path / 'junit.xml'), 'cases': [
        {'node': t if '::' in t else t + '::test_any', 'outcome': 'passed'}
        for c in acceptance.CHECKS.values() for t in c['tests']]}
    needing = {i for i, c in acceptance.CHECKS.items() if c.get('requires')}
    report = acceptance.acceptance_report(inventory, every_check_passes)
    assert report['totals']['children']['pass'] == len(acceptance.CHECKS) - len(needing) and needing
    assert all(report['children'][i]['status'] == 'missing' for i in needing)  # passed cases, but no runtime recorded
    every_check_passes['runtime'] = {name: '/prepared' for c in acceptance.CHECKS.values() for name in c.get('requires', ())}
    report = acceptance.acceptance_report(inventory, every_check_passes)
    assert report['totals']['children']['pass'] == len(acceptance.CHECKS)
    assert report['groups']['R09.truthful-closure']['status'] == 'missing'  # failed-simulation is not executable yet
    assert all(g['status'] != 'pass' for g in report['groups'].values()) and not report['completion']
    blockers = ' '.join(report['completion_blockers'])
    assert 'no child traced to pinned napari source' in blockers and 'W.cellposesam' in blockers
    assert 'unpinned reference source: napari_console' in blockers
    acceptance.write_report(report, tmp_path / 'report')
    markdown = (tmp_path / 'report/report.md').read_text()
    assert all(f'## {group}' in markdown for group in inventory['groups'])
    assert '[passed](../junit.xml)' in markdown


def test_default_run_reports_without_claiming_completion(tmp_path, monkeypatch):
    monkeypatch.setattr(acceptance.sys, 'argv', ['acceptance', '--no-run', '--output', str(tmp_path / 'r'),
                                                 '--archive', str(tmp_path / 'absent.zip'), '--require-complete'])
    assert acceptance.main() == 1
    report = json.loads((tmp_path / 'r/report.json').read_text())
    assert not report['source_verified'] and not report['completion']
    assert set(report['totals']['children']) == set(acceptance.STATES)


def test_a_check_can_name_one_parametrized_case_exactly():
    check = {CHILD_ID: {'tests': ('tests/test_example.py::test_case[one]',), 'class': 'synthetic_fixture'}}
    assert acceptance.child_status(_child(), _results(), check)['status'] == 'pass'
    other = _results(node='tests/test_example.py::test_case[two]')
    assert acceptance.child_status(_child(), other, check)['status'] == 'fail'


def test_a_required_runtime_decides_missing_versus_fail_and_pass():
    check = {CHILD_ID: {'tests': ('tests/test_example.py::test_case',), 'class': 'synthetic_fixture',
                        'requires': ('PREPARED_RUNTIME',)}}
    # Its test skips without the runtime: that is neither a pass nor a failure of Hooke.
    skipped = acceptance.child_status(_child(), _results(outcome='skipped'), check)
    assert skipped['status'] == 'missing' and 'PREPARED_RUNTIME' in skipped['reason']
    # A run that names the runtime but whose test still skipped fails, as any required skip does.
    present = {**_results(outcome='skipped'), 'runtime': {'PREPARED_RUNTIME': '/prepared'}}
    assert acceptance.child_status(_child(), present, check)['status'] == 'fail'
    passed = {**_results(), 'runtime': {'PREPARED_RUNTIME': '/prepared'}}
    assert acceptance.child_status(_child(), passed, check)['status'] == 'pass'


def test_run_passes_only_required_runtimes_and_keeps_a_group_on_one_worker(tmp_path, monkeypatch):
    seen = {}

    class Ran:
        pid, returncode = 0, 0

        def __init__(self, command, cwd, env, **_):
            seen.update(command=command, env=env)
            (tmp_path / 'junit.xml').write_text('<testsuite><testcase classname="tests.test_example" name="test_case"/></testsuite>')

        def wait(self, timeout=None):
            return 0
    monkeypatch.setattr(acceptance.subprocess, 'Popen', Ran)
    monkeypatch.setattr(acceptance, 'seal_run', lambda run: run)
    monkeypatch.setattr(acceptance, 'tree_identity', lambda: {})
    monkeypatch.setattr(acceptance, 'check_inputs', lambda files: {})
    monkeypatch.setenv('PREPARED_RUNTIME', '/prepared')
    monkeypatch.setenv('UNRELATED_SECRET', 'never passed')
    check = {CHILD_ID: {'tests': ('tests/test_scientific_acceptance.py::test_case',), 'class': 'synthetic_fixture',
                        'requires': ('PREPARED_RUNTIME', 'ABSENT_RUNTIME')}}
    run = acceptance.run_checks(tmp_path, checks=check, jobs=4)
    assert run['runtime'] == {'PREPARED_RUNTIME': '/prepared'}
    assert seen['env']['PREPARED_RUNTIME'] == '/prepared' and 'UNRELATED_SECRET' not in seen['env']
    assert 'ABSENT_RUNTIME' not in seen['env']
    assert seen['command'][seen['command'].index('--dist') + 1] == 'loadgroup'


def test_a_grouped_test_reported_as_name_at_group_is_still_its_own_node(tmp_path):
    junit = tmp_path / 'junit.xml'
    junit.write_text('<testsuite><testcase classname="tests.test_scientific_acceptance" name="test_case@hooke-studio-browser"/>'
                     '<testcase classname="tests.test_scientific_acceptance" name="test_case[one]@hooke-studio-browser"/></testsuite>')
    assert [c['node'] for c in acceptance.junit_cases(junit)] == [
        'tests/test_scientific_acceptance.py::test_case', 'tests/test_scientific_acceptance.py::test_case[one]']

def test_every_registered_prerequisite_must_execute_before_a_child_passes():
    checks = {CHILD_ID: {'tests': ('tests/test_example.py::test_case',
                                  'tests/test_example.py::test_runtime_pin'),
                         'class': 'synthetic_fixture'}}
    result = acceptance.child_status(_child(), _results(), checks)
    assert result['status'] == 'fail'
    assert 'test_runtime_pin' in result['reason']
