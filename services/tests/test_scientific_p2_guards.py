"""Synthetic refusal checks for P2 evidence; no browser/native qualification."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from bench import scientific_acceptance as acceptance, scientific_harnesses as harnesses
from tests import test_scientific_browser_p2 as p2
from tests import test_scientific_reference_comparison as comparison


def test_p2_document_scope_keeps_old_windows_out_of_controls_and_observations():
    result = subprocess.run(['node', '--test', str(Path(__file__).with_name('scientific_document_scope.test.mjs'))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_p2_configuration_coexists_with_the_native_browser_executable(tmp_path, monkeypatch):
    """One acceptance process can configure P1/P4's executable and P2's Playwright module together."""
    from types import SimpleNamespace
    executable = tmp_path / 'Chromium'
    executable.write_bytes(b'\xcf\xfa\xed\xfe')
    config = tmp_path / 'playwright.json'
    browser = {'chromium': str(executable), 'playwright': str(tmp_path / 'index.mjs')}
    config.write_text(json.dumps(browser))
    prepared = tmp_path / 'prepared.json'
    prepared.write_text(json.dumps({'key': 'mine/review#viewer'}))
    monkeypatch.setenv('HOOKE_BROWSER', str(executable))
    monkeypatch.setenv('HOOKE_BROWSER_CONFIG', str(config))
    monkeypatch.setenv('NAPARI_NATIVE_HOST_PREPARATION', str(prepared))
    monkeypatch.setenv('NAPARI_REFERENCE_PYTHON', '/prepared/python')
    monkeypatch.setattr(p2, 'shared_result', lambda *_: tmp_path / 'shared.json')
    seen = []

    def run(preparation, configuration, python, work):
        seen.append((preparation, configuration, python, work))
        return {'results': {}}

    monkeypatch.setattr(p2, '_run', run)
    result = p2.browser_run.__wrapped__(SimpleNamespace(mktemp=lambda _: tmp_path / 'work'))
    assert result == {'results': {}}
    assert seen == [({'key': 'mine/review#viewer'}, browser, '/prepared/python', tmp_path / 'work')]
    required = harnesses.HARNESS_TESTS['browser-p2']['requires']
    assert 'HOOKE_BROWSER_CONFIG' in required and 'HOOKE_BROWSER' not in required


def test_p2_children_require_runtime_and_actual_attenuation_negative():
    child = next(row for row in harnesses.harness_children() if row['harness'] == 'browser-p2')
    check = harnesses.checks()[child['id']]
    guards = ('tests/test_scientific_browser_p2.py::test_both_p2_sides_ran_approved_runtimes',
              'tests/test_scientific_browser_p2.py::test_a_worker_that_drops_attenuation_fails_the_attenuation_child')
    assert set(guards) <= set(check['tests'])
    cases = [{'node': node, 'outcome': 'passed'} for node in check['tests']]
    results = {'cases': cases, 'junit': '/synthetic.xml', 'runtime': {name: '/prepared' for name in check['requires']}}
    assert acceptance.child_status(child, results, {child['id']: check})['status'] == 'pass'
    for guard in guards:
        for outcome in (None, 'failed', 'skipped'):
            changed = [dict(row) for row in cases if row['node'] != guard]
            if outcome:
                changed.append({'node': guard, 'outcome': outcome})
            assert acceptance.child_status(child, {**results, 'cases': changed}, {child['id']: check})['status'] != 'pass'


def test_p2_replay_and_profile_are_explicit_source_pins():
    pins = acceptance.check_inputs(['tests/test_scientific_browser_p2.py'])
    for name in ('scientific_browser_p2.mjs', 'scientific_document_scope.mjs', 'scientific_p2_observation.mjs', 'scientific_reference_replay.py', 'scientific_comparison_recipes.py',
                 'scientific_reference_dependencies.json', 'test_scientific_reference_comparison.py'):
        assert 'services/tests/' + name in pins


@pytest.mark.parametrize('changed', ['none', 'missing-negative', 'source', 'wheel', 'dependency', 'missing-profile', 'model-only', 'no-stop'])
def test_p2_runtime_guard_refuses_matching_unapproved_observations(changed):
    from env_worker.napari_cgl import REFERENCE_WHEEL
    versions = {**comparison.PINNED_DEPENDENCIES, 'napari': '0.7.1.dev0'}
    runtime = {'napari': '0.7.1.dev0', 'versions': versions, 'napari_wheel': REFERENCE_WHEEL,
               'distributions': len(versions), 'napari_files': 'same-files', 'distributions_sha256': 'same-records',
               'pinned_sources_sha256': hashlib.sha256(json.dumps(comparison.PINNED_SOURCES,
                                                                sort_keys=True).encode()).hexdigest()}
    if changed == 'source': runtime['pinned_sources_sha256'] = 'wrong'
    elif changed == 'wheel': runtime['napari_wheel'] = 'wrong'
    elif changed == 'dependency': runtime['versions'] = {**versions, 'numpy': 'wrong'}
    observed = {'runtimes': {name: {side: copy.deepcopy(runtime) for side in ('hooke', 'reference')}
                             for name in (p2.TITLE, p2.SURFACE, p2.NEGATIVE)}}
    profile = {'name': comparison.PROFILE, 'viewer': 'napari.viewer.Viewer', 'ndisplay': 3, 'qt_api': 'PySide6',
               'settings': {'experimental': {'async_': False, 'dynamic_layer_controls': False}},
               'recipe_sha256': hashlib.sha256(comparison.RECIPES.read_bytes()).hexdigest()}
    observed['profiles'] = {name: {side: {**profile, 'qt_platform': platform}
                           for side, platform in (('hooke', 'offscreen'), ('reference', 'cocoa'))}
                           for name in observed['runtimes']}
    observed['cleanup'] = {name: {'ok': True, 'stopped': True} for name in observed['runtimes']}
    if changed == 'missing-profile': del observed['profiles'][p2.TITLE]
    elif changed == 'model-only': observed['profiles'][p2.TITLE]['reference']['viewer'] = 'napari.components.ViewerModel'
    elif changed == 'no-stop': observed['cleanup'][p2.TITLE]['stopped'] = False
    if changed == 'missing-negative':
        del observed['runtimes'][p2.NEGATIVE]
    if changed == 'none':
        p2.test_both_p2_sides_ran_approved_runtimes(observed)
    else:
        with pytest.raises(AssertionError):
            p2.test_both_p2_sides_ran_approved_runtimes(observed)


@pytest.mark.parametrize('missing', ['state', 'operation', 'positive'])
def test_p2_negative_needs_an_executed_attenuation_and_observed_mismatch(missing):
    negative = {'opened': {'three': True, 'from': 'Project Data'}, 'result': {'ok': False},
                'hooke': {'Native input': {'attenuation': '0.05'}}, 'reference': {'Native input': {'attenuation': '0.4'}},
                'ops': [['image-presentation', 'Native input', {'attenuation': 0.4}]]}
    result = {'negative': negative, 'results': {p2.ATTENUATION: {'ok': True}}}
    if missing == 'state': negative['hooke'] = None
    elif missing == 'operation': negative['ops'] = []
    else: result['results'][p2.ATTENUATION]['ok'] = False
    with pytest.raises(AssertionError):
        p2.test_a_worker_that_drops_attenuation_fails_the_attenuation_child(result)


@pytest.mark.parametrize('attenuation_differs', [True, False])
def test_full_appearance_negative_requires_the_actual_attenuation_difference(attenuation_differs):
    settings = {'contrastLimits': [0,3000], 'gamma': 1, 'rendering': 'attenuated_mip',
                'isoThreshold': 1500, 'attenuation': 0.4, 'autoContrast': False}
    result = {'results': {p2.ATTENUATION: {'ok': True}}, 'negative': {
        'opened': {'three': True}, 'ops': [['image-presentation', 'Native input', settings]],
        'hooke': {'Native input': {'attenuation': '0.05' if attenuation_differs else '0.4', 'contrast_limits': [0,3000]}},
        'reference': {'Native input': {'attenuation': '0.4', 'contrast_limits': ['0.0','3000.0']}}}}
    if attenuation_differs:
        p2.test_a_worker_that_drops_attenuation_fails_the_attenuation_child(result)
    else:
        with pytest.raises(AssertionError):
            p2.test_a_worker_that_drops_attenuation_fails_the_attenuation_child(result)
