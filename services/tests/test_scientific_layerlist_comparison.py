"""Synthetic admission negatives; actual collection behavior needs both prepared runtimes."""
import copy
import pytest
from bench import scientific_harnesses
from tests import scientific_comparison_recipes as recipes


def report(symbol):
    keys = recipes.LAYERLIST_PROOFS[symbol]
    return {'observed': {'proven': dict.fromkeys(keys, True), 'cases': dict.fromkeys(keys, {'state': []}), 'restored': True}}


@pytest.mark.parametrize('symbol', ['LayerList.__getitem__', 'LayerList.__setitem__'])
def test_collection_admission_requires_every_observation_and_positive(symbol):
    original = report(symbol)
    recipes.validate_layerlist_observation(symbol, original)
    for key in recipes.LAYERLIST_PROOFS[symbol]:
        for part in ('proven', 'cases'):
            broken = copy.deepcopy(original)
            del broken['observed'][part][key]
            with pytest.raises(AssertionError):
                recipes.validate_layerlist_observation(symbol, broken)
        broken = copy.deepcopy(original)
        broken['observed']['proven'][key] = False
        with pytest.raises(AssertionError):
            recipes.validate_layerlist_observation(symbol, broken)


def test_collection_children_require_both_controlled_broken_behaviors():
    registration = scientific_harnesses.HARNESS_TESTS['compare-layerlist']
    assert set(scientific_harnesses.COMPARISON_GUARDS) <= set(registration['prerequisites'])
    for name in ('layerlist-lookup', 'layerlist-replacement'):
        assert f'tests/test_scientific_reference_comparison.py::test_a_broken_behaviour_in_hookes_worker_is_caught[{name}]' in registration['prerequisites']
    assert registration['class'] == 'actual_upstream_worker'


@pytest.mark.parametrize('name,symbol,changed', [
    ('layerlist-lookup', 'LayerList.__getitem__', 'name'),
    ('layerlist-replacement', 'LayerList.__setitem__', 'integer'),
])
def test_negative_must_complete_and_break_its_declared_operation(name, symbol, changed):
    from tests.test_scientific_reference_comparison import _assert_layerlist_negative
    expected = report(symbol)
    actual = copy.deepcopy(expected)
    actual['observed']['proven'][changed] = False
    actual['observed']['cases'][changed] = {'state': ['different actual observation']}
    _assert_layerlist_negative(name, actual, expected)
    for bad in ({'error': 'crash'}, expected, {'observed': {**actual['observed'], 'cases': {}}},
                {'observed': {**actual['observed'], 'restored': False}}):
        with pytest.raises(AssertionError):
            _assert_layerlist_negative(name, bad, expected)
    stale = copy.deepcopy(expected)
    stale['observed']['restored'] = False
    with pytest.raises(AssertionError):
        recipes.validate_layerlist_observation(symbol, stale)


@pytest.mark.parametrize('symbol,case', [('LayerList.__getitem__', 'index'), ('LayerList.__setitem__', 'integer')])
@pytest.mark.parametrize('field,value', [
    ('values', [[999, 999, 999], [999, 999, 999]]),
    ('dtype', '>u2'),
    ('shape', [3, 2]),
    ('sha256', 'b' * 64),
])
def test_final_collection_observation_preserves_distinct_native_evidence(monkeypatch, symbol, case, field, value):
    # Synthetic recipe output at the registered comparison seam: same nesting as real collection state.
    # Native leaves are already normalized by _state; observation must not discard them a second time.
    import json
    native = {'dtype': '<u2', 'shape': [2, 3], 'values': [[10, 11, 12], [13, 14, 15]]}
    if field == 'sha256':
        native = {'dtype': '<u2', 'shape': [100, 100], 'sha256': 'a' * 64}
    source = report(symbol)['observed']
    source['cases'][case] = {'state': {'layers': [['retained specimen', {'data': [native]}]],
                                     'selected': ['retained specimen'], 'active': 'retained specimen'}}
    monkeypatch.setitem(recipes.LAYERLIST, symbol, lambda _: copy.deepcopy(source))
    before = json.loads(json.dumps(recipes.observe('compare-layerlist', symbol, None)))
    source['cases'][case]['state']['layers'][0][1]['data'][0][field] = value
    after = json.loads(json.dumps(recipes.observe('compare-layerlist', symbol, None)))
    assert before != after, f'{field} difference vanished at the final comparison boundary'
    actual = before['observed']['cases'][case]['state']['layers'][0][1]['data'][0]
    expected = {'dtype': '<u2', 'shape': [2, 3], 'values': [[10, 11, 12], [13, 14, 15]]}
    if field == 'sha256':
        expected = {'dtype': '<u2', 'shape': [100, 100], 'sha256': 'a' * 64}
    assert actual == expected
