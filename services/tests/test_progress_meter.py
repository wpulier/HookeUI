"""scripts/progress.py counts finished units from the plan's own records and never more than they say."""
import importlib.util
import json
import subprocess

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('progress', ROOT / 'scripts/progress.py')
progress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(progress)


def test_launch_rows_count_only_demonstrated_evidence():
    table = '\n'.join([
        '| # | Need | Slice | Procedure | Expected | Evidence |',
        '|---|---|---|---|---|---|',
        '| 1 | Install | L7a | VM | Opens | Demonstrated on candidate sha256 abc |',
        '| 2 | Example | L7a | Open | Runs | pending |',
        '| 3 | Data | L14 | Open | Kept | pending (earlier VM demonstration does not carry over) |',
    ])
    rows = progress.launch_rows(table)
    assert [r['row'] for r in rows] == [1, 2, 3]
    assert [r['evidence'].lower().startswith('demonstrated') for r in rows] == [True, False, False]


def test_the_real_launch_table_has_24_rows():
    assert progress.launch()['total'] == 24


def test_q_and_p8_are_qualification_and_everything_else_is_parity():
    assert progress.qualification('Q') and progress.qualification('P8.clean-install')
    assert not progress.qualification('P3.labels') and not progress.qualification('W1.ome-zarr')


def test_historical_report_counters_remain_visible_without_current_credit(tmp_path):
    report = {'run': {'tree': {'commit': 'abc123'}},
              'groups': {'P1.a': {'status': 'pass'}, 'P1.b': {'status': 'missing'}, 'Q': {'status': 'missing'}},
              'children': {'P1.a.x': {'group': 'P1.a', 'status': 'pass'},
                           'P1.b.x': {'group': 'P1.b', 'status': 'fail'},
                           'Q.x': {'group': 'Q', 'status': 'missing'}}}
    path = tmp_path / 'report.json'
    path.write_text(json.dumps(report))
    parity, qualified = progress.acceptance(path)
    assert (parity['done'], parity['reported_done'], parity['reported_total']) == (0, 1, 2)
    assert qualified['done'] == 0 and qualified['reported_total'] == 1
    assert parity['total'] + qualified['total'] > 3000
    assert parity['source']['commit'] == 'abc123'


def test_without_a_report_nothing_counts_as_passing():
    parity, qualified = progress.acceptance(None)
    assert parity['done'] == 0 and qualified['done'] == 0
    assert parity['total'] + qualified['total'] > 3000


def test_weights_sum_to_one_and_overall_stays_a_fraction():
    assert abs(sum(progress.WEIGHTS.values()) - 1) < 1e-9
    assert 0 <= progress.measure(None)['overall'] <= 1


def test_unsigned_subset_cannot_shrink_scope_or_claim_current_passes(tmp_path):
    path = tmp_path / 'report.json'
    path.write_text(json.dumps({'run': {'tree': {'commit': 'old'}},
        'groups': {'P1.a': {'status': 'pass'}},
        'children': {'P1.a.x': {'group': 'P1.a', 'status': 'pass'}}}))
    parity, qualified = progress.acceptance(path)
    assert parity['total'] + qualified['total'] > 3000
    assert parity['done'] == 0
    assert parity['reported_done'] == 1
    assert not parity['source']['current']
    assert parity['source']['note']


def test_full_completion_is_not_inferred_from_a_weighted_percentage(monkeypatch):
    track = {'done': 1, 'total': 1}
    source = {'completion': False, 'completion_blockers': ['independent qualification missing'],
              'current': True, 'workflow_groups': {}}
    monkeypatch.setattr(progress, 'acceptance', lambda path: (
        {**track, 'source': source}, {**track, 'source': source}))
    for name in ('launch', 'maintenance'):
        monkeypatch.setattr(progress, name, lambda: dict(track))
    result = progress.measure()
    assert result['overall'] == 1
    assert result['completion'] is False
    assert 'independent qualification missing' in result['completion_blockers']


def test_require_complete_refuses_missing_evidence(capsys):
    assert progress.main(['--require-complete', '--json']) == 1
    assert json.loads(capsys.readouterr().out)['completion'] is False


@pytest.fixture
def sealed_meter_run(tmp_path, monkeypatch):
    runner = progress.runner
    inventory = runner.load_inventory()
    child = next(c for c in inventory['children'] if c['evidence_class'] == 'synthetic_fixture'
                 and all(c.get(k) for k in ('procedure', 'oracle', 'criteria')))
    root = tmp_path / 'checkout'
    root.mkdir()
    (root / '.gitignore').write_text('/reports/\n')
    (root / 'subject.py').write_text('VALUE = 1\n')
    (root / 'services/tests').mkdir(parents=True)
    (root / 'services/tests/test_example.py').write_text('def test_case(): pass\n')
    for args in (['init', '-q'], ['add', '-A'], ['-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm', 'base']):
        subprocess.run(['git', *args], cwd=root, check=True)
    monkeypatch.setattr(runner, 'ROOT', root)
    monkeypatch.setattr(runner, 'load_inventory', lambda: inventory)
    monkeypatch.setattr(runner, 'CHECKS', {child['id']: {
        'tests': ('tests/test_example.py::test_case',), 'class': 'synthetic_fixture'}})
    previous = root / 'reports/previous'
    previous.mkdir(parents=True)
    junit = previous / 'junit.xml'
    junit.write_text('<testsuite><testcase classname="tests.test_example" name="test_case"/></testsuite>')
    tree = runner.tree_identity()
    run = runner.seal_run({'kind': 'execution-v2', 'selected': [child['id']],
        'registration': runner.CHECKS, 'selectors': ['tests/test_example.py::test_case'], 'exit': 0, 'timed_out': False,
        'junit': str(junit), 'tree': tree, 'tree_after': tree, 'inputs': {}, 'inputs_after': {},
        'runtime': {}, 'runtime_inputs': {}, 'runtime_inputs_after': {},
        'cases': [{'node': 'tests/test_example.py::test_case', 'outcome': 'passed'}]})
    path = previous / 'report.json'
    # Derived counters are not sealed. Inventing passes here must have no effect.
    path.write_text(json.dumps({'run': run, 'children': {'forged': {'group': 'Q', 'status': 'pass'}},
                                'groups': {'Q': {'status': 'pass'}}, 'completion': True}))
    return root, path, junit


@pytest.mark.parametrize('mutation', ['none', 'source', 'junit', 'run'])
def test_meter_reuses_sealed_cases_and_rejects_stale_or_edited_evidence(sealed_meter_run, mutation):
    root, path, junit = sealed_meter_run
    if mutation == 'source':
        (root / 'subject.py').write_text('VALUE = 2\n')
    elif mutation == 'junit':
        junit.write_text('<testsuite/>')
    elif mutation == 'run':
        report = json.loads(path.read_text())
        report['run']['cases'][0]['outcome'] = 'skipped'
        path.write_text(json.dumps(report))
    parity, qualified = progress.acceptance(path)
    assert parity['done'] == (1 if mutation == 'none' else 0)
    assert parity['source']['current'] == (mutation == 'none')
    assert qualified['done'] == 0 and qualified['reported_done'] == 1
    assert parity['total'] + qualified['total'] > 3000
    assert not parity['source']['completion']
    assert len(parity['source']['workflow_groups']) == 8


def test_meter_recomputes_sealed_aggregate_and_loses_credit_when_an_original_is_missing(sealed_meter_run):
    root, path, junit = sealed_meter_run
    runner = progress.runner
    combined = root / 'reports/combined'
    combined.mkdir()
    run = runner.aggregate([path.parent])
    target = combined / 'report.json'
    target.write_text(json.dumps({'run': run, 'completion': True,
        'children': {'forged': {'group': 'Q', 'status': 'pass'}}}))
    parity, qualified = progress.acceptance(target)
    assert parity['done'] == 1 and qualified['done'] == 0
    assert parity['total'] + qualified['total'] > 3000
    assert parity['source']['current'] and not parity['source']['completion']
    path.unlink()
    parity, qualified = progress.acceptance(target)
    assert parity['done'] == qualified['done'] == 0
    assert not parity['source']['current']
