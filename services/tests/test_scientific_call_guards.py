"""Synthetic P7 guard checks; these do not qualify an extension or native writer."""
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from bench import scientific_acceptance as acceptance, scientific_harnesses as harnesses
from tests import scientific_comparison_recipes as recipes
from tests import test_scientific_reference_comparison as comparison


def test_every_call_child_requires_the_actual_broken_writer_guard():
    checks = harnesses.checks(comparison.INVENTORY_DATA)
    required = 'tests/test_scientific_reference_comparison.py::test_a_broken_behaviour_in_hookes_worker_is_caught[writer-values]'
    children = [c for c in comparison.CHILDREN if c['harness'] == 'compare-call']
    assert children
    for child in children:
        check = checks[child['id']]
        assert required in check['tests']
        cases = [{'node': node, 'outcome': 'passed'} for node in check['tests']]
        results = {'cases': cases, 'junit': '/synthetic.xml',
                   'runtime': {name: '/prepared' for name in check['requires']}}
        assert acceptance.child_status(child, results, {child['id']: check})['status'] == 'pass'
        for outcome in (None, 'failed', 'skipped'):
            changed = [dict(case) for case in cases if case['node'] != required]
            if outcome is not None:
                changed.append({'node': required, 'outcome': outcome})
            assert acceptance.child_status(child, {**results, 'cases': changed},
                                           {child['id']: check})['status'] != 'pass'


def test_writer_negative_changes_saved_values_even_though_save_returns_success(tmp_path, monkeypatch):
    template, symbol, broken = comparison.BREAKS['writer-values']
    assert (template, symbol) == ('compare-call', 'Layer.save')
    monkeypatch.chdir(tmp_path)
    class Layer:
        def __init__(self): self.data = np.array([[1., 2.], [3., 4.]])
        def save(self, name, plugin):
            np.savetxt(name, self.data, delimiter=',')
            return [name]
    held = Layer()
    original = held.data.copy()
    viewer = SimpleNamespace(open=lambda name, plugin: np.loadtxt(name, delimiter=','))
    monkeypatch.setattr(recipes, '_holding', lambda viewer, kinds, action: action([held]))
    monkeypatch.setattr(recipes, '_opened', lambda viewer, action: action())
    monkeypatch.setitem(sys.modules, 'napari.layers', SimpleNamespace(Layer=Layer))
    correct = recipes.observe(template, symbol, viewer)
    exec(broken, {})
    corrupted = recipes.observe(template, symbol, viewer)
    assert 'error' not in correct and 'error' not in corrupted
    assert correct['observed']['returned'] == corrupted['observed']['returned']
    assert correct['observed']['files'] != corrupted['observed']['files']
    assert correct['observed']['read_back'] != corrupted['observed']['read_back']
    np.testing.assert_array_equal(held.data, original)


def test_matching_writer_exceptions_never_qualify():
    child = next(c for c in comparison.CHILDREN if c['source']['symbol'] == 'Layer.save'
                 and c['harness'] == 'compare-call')
    pair = {side: {'results': {child['id']: {'error': 'Writer failure'}}} for side in ('hooke', 'reference')}
    with pytest.raises(AssertionError):
        comparison.test_compare_child(child['id'], pair)


@pytest.mark.parametrize('fails', [False, True])
def test_subset_probe_validates_both_pairs_restores_environment_and_cannot_grant_completion(tmp_path, monkeypatch, fails):
    from tests import scientific_call_probe as probe
    calls = []
    monkeypatch.setattr(comparison, '_prepared_environment', lambda prepared: lambda: calls.append('restore'))
    monkeypatch.setattr(comparison, '_hooke', lambda prepared, root, cases, broken=None:
                        {'scene': {}, 'cases': cases, 'broken': broken})
    monkeypatch.setattr(comparison, '_reference', lambda python, cases, scene: {'cases': cases})
    monkeypatch.setattr(comparison, 'test_the_session_holds_the_retained_volume', lambda pair: calls.append('volume'))
    def admit(pair):
        calls.append('admit')
        if fails: raise AssertionError('source changed')
    monkeypatch.setattr(comparison, 'test_both_sides_ran_the_same_pinned_napari', admit)
    monkeypatch.setattr(comparison, 'test_compare_child', lambda ident, pair: calls.append('positive'))
    monkeypatch.setattr(comparison, 'test_a_broken_behaviour_in_hookes_worker_is_caught',
                        lambda name, pair: calls.append(('negative', name)))
    if fails:
        with pytest.raises(AssertionError, match='source changed'):
            probe.run({}, 'prepared-python', 'Layer.save', tmp_path)
        assert calls == ['volume', 'admit', 'restore']
    else:
        result = probe.run({}, 'prepared-python', 'Layer.save', tmp_path)
        assert result['evidence_class'] == 'actual_upstream_worker_diagnostic'
        assert result['qualified'] is False and result['mapped_completion'] is False
        assert 'cases' not in result and 'junit' not in result
        assert calls == ['volume', 'admit', 'positive', 'admit', ('negative', 'writer-values'), 'restore']
        assert result['positive']['hooke']['broken'] is None
        assert result['negative']['hooke']['broken'] == comparison.BREAKS['writer-values'][2]


def writer_pair():
    def observed(values, digest):
        return {'observed': {'returned': ['w-layer.csv'], 'files': {'w-layer.csv': digest * 64},
                'read_back': {'w-layer.csv': [['points', {'dtype': '<f8', 'shape': [2, 2], 'values': values}]]}}}
    return {'hooke': {'results': {'writer-values': observed([['0.0', '0.0'], ['0.0', '0.0']], 'b')}},
            'reference': {'results': {'writer-values': observed([['1.0', '2.0'], ['3.0', '4.0']], 'a')}}}


@pytest.mark.parametrize('failure', ['error', 'console', 'empty-files', 'empty-readback', 'empty-return',
                                    'different-return', 'unchanged-files', 'wrong-reference', 'wrong-shape'])
def test_writer_guard_requires_successful_corrupted_values_not_any_difference(monkeypatch, failure):
    monkeypatch.setattr(comparison, '_assert_gui_profile', lambda pair: None)
    monkeypatch.setattr(comparison, '_assert_pinned_runtime', lambda pair: None)
    pair = writer_pair()
    comparison.test_a_broken_behaviour_in_hookes_worker_is_caught('writer-values', {'negatives': {'writer-values': pair}})
    result = pair['hooke']['results']['writer-values']
    if failure in ('error', 'console'):
        pair['hooke']['results']['writer-values'] = {failure: 'writer stopped before producing output'}
    elif failure == 'empty-files': result['observed']['files'] = {}
    elif failure == 'empty-readback': result['observed']['read_back'] = {}
    elif failure == 'empty-return': result['observed']['returned'] = []
    elif failure == 'different-return': result['observed']['returned'] = ['other.csv']
    elif failure == 'unchanged-files': result['observed']['files'] = {'w-layer.csv': 'a' * 64}
    elif failure == 'wrong-reference':
        pair['reference']['results']['writer-values']['observed']['read_back']['w-layer.csv'][0][1]['values'] = []
    else: result['observed']['read_back']['w-layer.csv'][0][1]['shape'] = [1, 4]
    with pytest.raises(AssertionError):
        comparison.test_a_broken_behaviour_in_hookes_worker_is_caught('writer-values', {'negatives': {'writer-values': pair}})


@pytest.mark.parametrize('failure', ['positive-reference', 'positive-comparison', 'negative-reference', 'negative-comparison'])
def test_probe_keeps_completed_observations_and_failure_without_completion_credit(tmp_path, monkeypatch, failure):
    import json
    from tests import scientific_call_probe as probe
    restored = []
    monkeypatch.setattr(comparison, '_prepared_environment', lambda prepared: lambda: restored.append(True))
    monkeypatch.setattr(comparison, '_hooke', lambda prepared, root, cases, broken=None:
                        {'scene': {}, 'observation': 'corrupt writer' if broken else 'positive values'})
    references = []
    def reference(*args):
        phase = 'negative' if references else 'positive'
        references.append(phase)
        if failure == phase + '-reference': raise RuntimeError(f'{phase} reference failed')
        return {'observation': phase + ' reference values'}
    monkeypatch.setattr(comparison, '_reference', reference)
    monkeypatch.setattr(comparison, 'test_the_session_holds_the_retained_volume', lambda pair: None)
    monkeypatch.setattr(comparison, 'test_both_sides_ran_the_same_pinned_napari', lambda pair: None)
    def compare(*args):
        if failure == 'positive-comparison': raise AssertionError('positive mismatch')
    monkeypatch.setattr(comparison, 'test_compare_child', compare)
    monkeypatch.setattr(comparison, 'test_a_broken_behaviour_in_hookes_worker_is_caught',
                        lambda *args: (_ for _ in ()).throw(AssertionError('negative mismatch')))
    with pytest.raises((RuntimeError, AssertionError)) as caught:
        probe.run({}, 'prepared-python', 'Layer.save', tmp_path)
    receipt = json.loads((tmp_path / 'diagnostic.json').read_text())
    assert restored == [True]
    assert receipt['status'] == 'failed' and receipt['stage'] == failure
    assert receipt['qualified'] is False and receipt['mapped_completion'] is False
    assert receipt['error'] == {'type': type(caught.value).__name__, 'message': str(caught.value)}
    assert receipt['positive']['hooke']['observation'] == 'positive values'
    if failure != 'positive-reference':
        assert receipt['positive']['reference']['observation'] == 'positive reference values'
    if failure.startswith('negative'):
        assert receipt['negative']['hooke']['observation'] == 'corrupt writer'
    if failure == 'negative-comparison':
        assert receipt['negative']['reference']['observation'] == 'negative reference values'
    assert (tmp_path / 'diagnostic.json').stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob('.diagnostic-*'))


def test_probe_refuses_to_overwrite_an_earlier_attempt(tmp_path, monkeypatch):
    from tests import scientific_call_probe as probe
    receipt = tmp_path / 'diagnostic.json'
    receipt.write_bytes(b'previous failed attempt\n')
    called = []
    monkeypatch.setattr(comparison, '_prepared_environment', lambda prepared: called.append(True))
    with pytest.raises(FileExistsError): probe.run({}, 'python', 'Layer.save', tmp_path)
    assert receipt.read_bytes() == b'previous failed attempt\n' and called == []


def test_failed_atomic_receipt_update_preserves_last_stage_and_original_error(tmp_path, monkeypatch):
    import json
    from tests import scientific_call_probe as probe
    restored = []
    monkeypatch.setattr(comparison, '_prepared_environment', lambda prepared: lambda: restored.append(True))
    error = AssertionError('worker diagnostic failed')
    def failed_worker(*args):
        def failed_replace(*args): raise OSError('disk unavailable')
        monkeypatch.setattr(probe.os, 'replace', failed_replace)
        raise error
    monkeypatch.setattr(comparison, '_hooke', failed_worker)
    with pytest.raises(AssertionError) as caught:
        probe.run({}, 'python', 'Layer.save', tmp_path)
    assert caught.value is error and restored == [True]
    assert 'disk unavailable' in error.__notes__[0]
    saved = json.loads((tmp_path / 'diagnostic.json').read_text())
    assert saved['status'] == 'running' and saved['stage'] == 'positive-hooke'
    assert saved['qualified'] is False and saved['mapped_completion'] is False
    assert not list(tmp_path.glob('.diagnostic-*'))


def test_probe_records_restore_failure_without_repeating_cleanup(tmp_path, monkeypatch):
    import json
    from tests import scientific_call_probe as probe
    attempts = []
    def restore():
        attempts.append(True)
        raise RuntimeError('environment restore failed')
    monkeypatch.setattr(comparison, '_prepared_environment', lambda prepared: restore)
    monkeypatch.setattr(comparison, '_hooke', lambda *args: {'scene': {}})
    monkeypatch.setattr(comparison, '_reference', lambda *args: {})
    for name in ('test_the_session_holds_the_retained_volume', 'test_both_sides_ran_the_same_pinned_napari',
                 'test_compare_child', 'test_a_broken_behaviour_in_hookes_worker_is_caught'):
        monkeypatch.setattr(comparison, name, lambda *args: None)
    with pytest.raises(RuntimeError, match='environment restore failed'):
        probe.run({}, 'python', 'Layer.save', tmp_path)
    assert attempts == [True]
    receipt = json.loads((tmp_path / 'diagnostic.json').read_text())
    assert receipt['status'] == 'failed' and receipt['stage'] == 'environment-restore'
    assert receipt['mapped_completion'] is False
