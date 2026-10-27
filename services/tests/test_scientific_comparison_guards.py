"""Synthetic tests of evidence refusal; these do not qualify scientific behavior."""
import copy
import hashlib
import os
import sys
from types import SimpleNamespace

import pytest

from bench import scientific_acceptance as acceptance, scientific_harnesses as harnesses
from tests import test_scientific_reference_comparison as comparison


@pytest.mark.parametrize('origin,expected', [
    ({'url': 'file:///prepared.whl#sha256=approved', 'archive_info': {}}, 'approved'),
    ({'url': 'file:///prepared.whl', 'archive_info': {'hashes': {'sha256': 'approved'}}}, 'approved'),
    ({'url': 'file:///prepared.whl', 'archive_info': {'hash': 'sha256=approved'}}, 'approved'),
    ({'url': 'file:///prepared.whl#sha256=other', 'archive_info': {'hashes': {'sha256': 'approved'}}}, None),
    ({'url': 'file:///prepared.whl#sha256=approved&sha256=other', 'archive_info': {}}, None),
    ({'url': 'file:///prepared.whl', 'archive_info': {}}, None),
])
def test_runtime_wheel_origin_accepts_matching_hash_forms_and_refuses_conflicts(origin, expected):
    from tests.scientific_comparison_recipes import wheel_origin_hash
    assert wheel_origin_hash(origin) == expected


def test_comparison_children_require_the_shared_guards():
    child = comparison.CHILDREN[0]
    check = harnesses.checks(comparison.INVENTORY_DATA)[child['id']]
    own = f'tests/test_scientific_reference_comparison.py::test_compare_child[{child["id"]}]'
    results = {'cases': [{'node': own, 'outcome': 'passed'}], 'junit': '/synthetic.xml',
               'runtime': {name: '/prepared' for name in check['requires']}}
    assert acceptance.child_status(child, results, {child['id']: check})['status'] != 'pass'


def test_preparation_restores_the_original_read_grant(monkeypatch):
    monkeypatch.setenv('MICROMIND_SANDBOX_READ', '/original')
    restore = comparison._prepared_environment({'environment': {}})
    try:
        assert 'MICROMIND_SANDBOX_READ' not in os.environ
    finally:
        restore()
    restored = os.environ.get('MICROMIND_SANDBOX_READ')
    assert restored == '/original'


@pytest.mark.parametrize('harness,observation', [
    ('compare-contribution', {'listed': False, 'observed': []}),
    ('compare-enum', {'value': 'added', 'observed': {'has': False, 'reported': []}}),
    ('compare-event', {'observed': {'events': [], 'delta': {}}}),
    ('compare-event', {'error': 'LinAlgError: Singular matrix'}),
])
def test_matching_absence_is_not_positive_reference_behavior(harness, observation):
    child = next(c for c in comparison.CHILDREN if c['harness'] == harness)
    compared = {side: {'results': {child['id']: observation}} for side in ('hooke', 'reference')}
    with pytest.raises(AssertionError):
        comparison.test_compare_child(child['id'], compared)


def test_two_matching_unapproved_runtimes_do_not_qualify():
    runtime = {'napari': 'wrong-version', 'napari_files': 'same-wrong-files'}
    with pytest.raises(AssertionError):
        comparison._assert_pinned_runtime(
            {side: {'runtime': runtime} for side in ('hooke', 'reference')})


def test_all_comparison_guards_must_pass_individually():
    child = comparison.CHILDREN[0]
    check = harnesses.checks(comparison.INVENTORY_DATA)[child['id']]
    cases = [{'node': node, 'outcome': 'passed'} for node in check['tests']]
    results = {'cases': cases, 'junit': '/synthetic.xml',
               'runtime': {name: '/prepared' for name in check['requires']}}
    assert acceptance.child_status(child, results, {child['id']: check})['status'] == 'pass'
    for index in range(len(cases)):
        missing = {**results, 'cases': cases[:index] + cases[index + 1:]}
        assert acceptance.child_status(child, missing, {child['id']: check})['status'] != 'pass'
        for outcome in ('failed', 'skipped'):
            changed = [dict(case) for case in cases]
            changed[index]['outcome'] = outcome
            assert acceptance.child_status(child, {**results, 'cases': changed},
                                           {child['id']: check})['status'] != 'pass'


@pytest.mark.parametrize('changed', ['none', 'source', 'wheel', 'dependency', 'duplicate'])
def test_runtime_observations_must_match_the_frozen_pins(changed):
    import hashlib
    import json
    from env_worker.napari_cgl import REFERENCE_WHEEL
    versions = {**comparison.PINNED_DEPENDENCIES, 'napari': '0.7.1.dev0'}
    runtime = {'napari': '0.7.1.dev0', 'versions': versions, 'napari_wheel': REFERENCE_WHEEL,
               'distributions': len(versions), 'napari_files': 'same-files', 'distributions_sha256': 'same-records',
               'pinned_sources_sha256': hashlib.sha256(json.dumps(comparison.PINNED_SOURCES,
                                                                sort_keys=True).encode()).hexdigest()}
    if changed == 'source': runtime['pinned_sources_sha256'] = 'wrong'
    elif changed == 'wheel': runtime['napari_wheel'] = 'wrong'
    elif changed == 'dependency': runtime['versions'] = {**versions, 'numpy': 'wrong'}
    elif changed == 'duplicate': runtime['distributions'] += 1
    compared = {side: {'runtime': runtime} for side in ('hooke', 'reference')}
    if changed == 'none':
        comparison._assert_pinned_runtime(compared)
    else:
        with pytest.raises(AssertionError):
            comparison._assert_pinned_runtime(compared)


def gui_profiles():
    """Synthetic receipts; real settings are independently observed by each native process."""
    profile = {'name': comparison.PROFILE, 'viewer': 'napari.viewer.Viewer', 'ndisplay': 2,
               'qt_api': 'PySide6', 'qt_platform': 'offscreen',
               'settings': {'experimental': {'async_': False, 'dynamic_layer_controls': False}},
               'recipe_sha256': hashlib.sha256(comparison.RECIPES.read_bytes()).hexdigest()}
    return {'hooke': {'profile': profile}, 'reference': {'profile': {**copy.deepcopy(profile), 'qt_platform': 'cocoa'}}}


def test_declared_gui_platform_difference_is_recorded_and_accepted():
    comparison._assert_gui_profile(gui_profiles())


@pytest.mark.parametrize('changed', ['settings', 'controls', 'profile', 'viewer', 'platform', 'recipe', 'async', 'missing'])
def test_gui_profile_refuses_unequal_or_unknown_context(changed):
    pair = gui_profiles()
    reference = pair['reference']['profile']
    if changed == 'settings': reference['settings']['unknown_setting'] = True
    elif changed == 'controls': reference['settings']['experimental']['dynamic_layer_controls'] = True
    elif changed == 'profile':
        for side in pair.values(): side['profile']['name'] = 'model-only'
    elif changed == 'viewer': reference['viewer'] = 'napari.components.viewer_model.ViewerModel'
    elif changed == 'platform': reference['qt_platform'] = 'offscreen'
    elif changed == 'recipe': reference['recipe_sha256'] = 'old'
    elif changed == 'async':
        for side in pair.values(): side['profile']['settings']['experimental']['async_'] = True
    elif changed == 'missing': del reference['settings']
    with pytest.raises(AssertionError):
        comparison._assert_gui_profile(pair)


def test_one_extra_event_is_not_normalized_away():
    child = next(c for c in comparison.CHILDREN if c['harness'] == 'compare-event')
    event = {'type': 'colormap', 'payload': {'index': 1}, 'source': True}
    pair = {'hooke': {'results': {child['id']: {'observed': {'events': [event, event], 'delta': {}}}}},
            'reference': {'results': {child['id']: {'observed': {'events': [event], 'delta': {}}}}}}
    with pytest.raises(AssertionError):
        comparison.test_compare_child(child['id'], pair)


def test_original_rotation_limitation_is_not_an_admitted_positive_recipe():
    from tests.scientific_comparison_recipes import EVENTS, LIMITATIONS, recipes
    assert EVENTS['Layer.events.rotate'] != LIMITATIONS['out-of-plane-rotation-2d'][1]
    assert 'upstream-limitation' not in recipes()
    assert not any(c['harness'] == 'upstream-limitation' for c in comparison.CHILDREN)


def test_positive_rotation_requires_retained_limitation_evidence():
    child = next(c for c in comparison.CHILDREN if c['source']['symbol'] == 'Layer.events.rotate')
    with pytest.raises(KeyError, match='limitation'):
        comparison.test_compare_child(child['id'], {})


@pytest.mark.parametrize('observation', [None, {'error': 'different failure'}, {'observed': {'events': ['rotated']}}])
def test_changed_or_missing_limitation_requires_reassessment(monkeypatch, observation):
    # Runtime-pin rejection has its own synthetic tests above; isolate the classification rule here.
    monkeypatch.setattr(comparison, '_assert_pinned_runtime', lambda pair: None)
    pair = gui_profiles()
    for side in pair.values():
        side['results'] = {comparison.LIMITATION['id']: {'error': 'LinAlgError: Singular matrix'}}
    if observation is None:
        del pair['reference']['results'][comparison.LIMITATION['id']]
    else:
        pair['reference']['results'][comparison.LIMITATION['id']] = observation
    with pytest.raises((AssertionError, KeyError)):
        comparison._assert_retained_limitation({'limitation': pair})


@pytest.mark.parametrize('fail', [False, True])
def test_independent_reference_uses_gui_and_closes_it_after_observation(tmp_path, monkeypatch, fail):
    from tests import scientific_reference_process as reference
    calls = []
    class Viewer:
        def __init__(self, **kwargs):
            calls.append(('viewer', kwargs))
            self.dims = SimpleNamespace(ndisplay=None)
        def add_image(self, data, **kwargs): calls.append(('image', data, kwargs))
        def close(self): calls.append(('close',))
    monkeypatch.setitem(sys.modules, 'napari', SimpleNamespace(Viewer=Viewer))
    monkeypatch.setitem(sys.modules, 'napari.plugins', SimpleNamespace(_initialize_plugins=lambda: calls.append(('plugins',))))
    monkeypatch.setattr('numpy.load', lambda path: 'synthetic-volume')
    recipe = tmp_path / 'recipes.py'
    recipe.write_text(f'''PROFILE = {comparison.PROFILE!r}
def comparison_profile(viewer, digest):
    return {{'viewer': type(viewer).__name__, 'ndisplay': viewer.dims.ndisplay, 'digest': digest}}
def observe(*args):
    {'raise RuntimeError("observation failed")' if fail else 'return {"events": ["actual callback fixture"]}'}
def runtime(paths): return {{'paths': paths}}
''')
    request = {'profile': comparison.PROFILE, 'recipes': str(recipe), 'volume': 'ignored',
               'volume_name': 'retained', 'ndisplay': 2, 'source_paths': ['pinned'],
               'cases': [{'id': 'case', 'template': 'event', 'symbol': 'changed'}]}
    if fail:
        with pytest.raises(RuntimeError, match='observation failed'): reference.run(request)
    else:
        result = reference.run(request)
        assert result['results']['case']['events'] == ['actual callback fixture']
        assert result['profile'] == {'viewer': 'Viewer', 'ndisplay': 2,
                                     'digest': hashlib.sha256(recipe.read_bytes()).hexdigest()}
    assert calls == [('plugins',), ('viewer', {'show': False, 'show_welcome_screen': False}),
                     ('image', 'synthetic-volume', {'name': 'retained'}), ('close',)]


def test_reference_refuses_unknown_profile_before_importing_napari():
    from tests.scientific_reference_process import run
    with pytest.raises(ValueError, match='Unknown scientific comparison profile'):
        run({'recipes': str(comparison.RECIPES), 'profile': 'model-only'})
