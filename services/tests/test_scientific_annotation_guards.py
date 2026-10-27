"""Synthetic H07 admission and isolation regressions; no native/browser qualification."""
import copy
import hashlib
import json
import io
import sys
from types import SimpleNamespace

import pytest

from bench import scientific_acceptance as acceptance, scientific_checks_h07 as checks
from tests import scientific_annotation_reference as reference
from tests import test_scientific_annotation_browser as browser
from tests import test_scientific_reference_comparison as comparison
from tests.test_scientific_comparison_guards import gui_profiles


def test_annotation_cases_own_fresh_sessions_and_api_recipes():
    assert browser.studio._fixture_function_marker.scope == 'function'
    assert browser.api_results._fixture_function_marker.scope == 'function'
    import inspect
    assert 'recipe' in inspect.signature(browser.api_results).parameters


@pytest.mark.parametrize('points,expected', [
    ([[60, 20], [50, 60], [80, 60]], True),
    ([[20, 30], [50, 60], [80, 60]], False),  # deleted the wrong original
    ([[60, 20], [40, 50], [80, 60]], False),  # the drag did not move its point
    ([[60, 20], [50, 60], [80, 50]], False),  # the second addition is wrong
    ([[60, 20], [50, 60]], False),
])
def test_point_journey_oracle_requires_every_surviving_point_in_order(points, expected):
    check = browser.POINTS['step-1-add-select-move-and-delete-points']['check']
    assert check({'data': points}, []) is expected


@pytest.mark.parametrize('status,busy,disabled,expected', [
    ('Annotation applied', 'false', False, True),
    ('Unsent gesture', 'false', False, False),
    ('Annotation applied', 'true', False, False),
    ('Annotation applied', 'false', True, False),
    ('Annotation applied', None, False, False),
])
def test_annotation_wait_observes_ready_without_accepting_unsent_busy_or_missing_state(status, busy, disabled, expected):
    """Run the actual generated expression in JS with a synthetic DOM; no browser/native claim."""
    import subprocess
    expressions = []
    page = SimpleNamespace(wait=lambda expression, *args: expressions.append(expression), eval=lambda _: status)
    assert browser.Annotator(SimpleNamespace(page=page)).wait() == status
    program = """
const [expression, status, busy, disabled] = JSON.parse(process.argv[1]);
const mode = {offsetParent: {}, disabled, getAttribute: () => 'Annotation mode',
  closest: () => ({parentElement: {querySelector: () => ({textContent: status})}})};
globalThis.document = {
  querySelector: selector => selector === '.scientific-annotate'
    ? (busy === null ? null : {getAttribute: () => busy}) : mode,
  querySelectorAll: () => [mode]
};
process.stdout.write(JSON.stringify(eval(expression)));
"""
    result = subprocess.run(['node', '-e', program, json.dumps([expressions[0], status, busy, disabled])],
                            text=True, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) is expected


def test_every_h07_child_requires_open_runtime_and_negative_guards():
    guards = {checks.BROWSER + name for name in (checks.OPEN, checks.SELF_CHECK,
              'test_annotation_worker_and_reference_use_approved_gui_runtimes')}
    for identity, check in checks.checks().items():
        assert guards <= set(check['tests']), identity
        assert {'NAPARI_ARCHIVE', 'NAPARI_REFERENCE_WHEEL'} <= set(check['requires'])
        child = {'id': identity, 'evidence_class': check['class'], 'procedure': ['synthetic'],
                 'oracle': 'synthetic', 'criteria': ['all required']}
        cases = [{'node': node, 'outcome': 'passed'} for node in check['tests']]
        results = {'cases': cases, 'junit': '/synthetic.xml',
                   'runtime': dict.fromkeys(check['requires'], '/synthetic')}
        assert acceptance.child_status(child, results, {identity: check})['status'] == 'pass'
        for guard in guards:
            for outcome in (None, 'failed', 'skipped'):
                changed = [dict(row) for row in cases if row['node'] != guard]
                if outcome:
                    changed.append({'node': guard, 'outcome': outcome})
                assert acceptance.child_status(child, {**results, 'cases': changed}, {identity: check})['status'] != 'pass'


def test_annotation_reference_and_runtime_inputs_are_explicitly_pinned():
    pins = acceptance.check_inputs(['tests/test_scientific_annotation_browser.py'])
    for name in ('scientific_annotation_reference.py', 'scientific_annotation_api.py', 'scientific_annotation_fixture.py',
                 'scientific_comparison_recipes.py', 'scientific_reference_dependencies.json',
                 'test_scientific_reference_comparison.py', 'scientific_napari_reference.py'):
        assert 'services/tests/' + name in pins


def test_every_original_paint_and_fill_case_remains_mandatory():
    child = {'id': 'P4.label-tools.step-1-apply-each-supported-tool-to-known',
             'evidence_class': 'isolated_profile_browser', 'procedure': ['synthetic'],
             'oracle': 'synthetic', 'criteria': ['every brush and fill mode']}
    check = checks.checks()[child['id']]
    bounded = [checks.BROWSER + f'{checks.PAINT}[{brush}]' for brush in (1, 3, 7)] + [
        checks.BROWSER + f'{checks.FILL}[{case}]' for case in ('connected-fill', 'global-fill', 'preserve', 'overwrite')]
    assert set(bounded) <= set(check['tests'])
    for omitted in bounded:
        results = {'junit': '/synthetic.xml', 'runtime': dict.fromkeys(check['requires'], '/synthetic'),
                   'cases': [{'node': node, 'outcome': 'passed'} for node in check['tests'] if node != omitted]}
        assert acceptance.child_status(child, results, {child['id']: check})['status'] != 'pass'


@pytest.mark.parametrize('fail', [False, True])
def test_reference_observes_actual_gui_and_closes_after_setup_failure(tmp_path, monkeypatch, capsys, fail):
    """Exercise the subprocess program with synthetic imports, without launching Qt or a native package."""
    calls = []
    class Viewer:
        def __init__(self, **kwargs): calls.append(('viewer', kwargs))
        def close(self): calls.append('close')
    package = tmp_path / 'napari'
    package.mkdir()
    source = package / '__init__.py'
    source.write_text('synthetic napari')
    monkeypatch.setitem(sys.modules, 'napari', SimpleNamespace(Viewer=Viewer, __file__=str(source), __version__='synthetic'))
    monkeypatch.setitem(sys.modules, 'napari.utils._proxies', SimpleNamespace(ReadOnlyWrapper=object))
    monkeypatch.setitem(sys.modules, 'napari.utils.interactions', SimpleNamespace(
        mouse_move_callbacks=None, mouse_press_callbacks=None, mouse_release_callbacks=None))
    monkeypatch.setitem(sys.modules, 'qtpy.QtWidgets', SimpleNamespace(QApplication=SimpleNamespace(processEvents=lambda: None)))
    from importlib import metadata
    origin = {'url': 'file:///synthetic.whl', 'archive_info': {'hash': 'sha256=synthetic'}}
    monkeypatch.setattr(metadata, 'distribution', lambda name: SimpleNamespace(read_text=lambda name: json.dumps(origin)))
    monkeypatch.setattr(metadata, 'distributions', lambda: [])
    observer = ('def runtime(paths):\n    calls.append(("runtime", paths))\n    return {"observed": True}\n'
                'def comparison_profile(viewer, digest):\n    calls.append(("profile", digest))\n    return {"observed": True}\n')
    plan = {'snapshot': 'def _p4_snapshot(viewer, base): return {}', 'observer': observer,
            'source_paths': ['src/napari/__init__.py'], 'recipe_sha256': 'recipe-digest', 'steps': [],
            'setup': 'raise RuntimeError("setup failed")' if fail else 'pass'}
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(plan)))
    if fail:
        with pytest.raises(RuntimeError, match='setup failed'):
            exec(reference.PROGRAM, {'calls': calls})
    else:
        exec(reference.PROGRAM, {'calls': calls})
        report = json.loads(capsys.readouterr().out)
        assert report['napari_origin'] == origin
        assert report['runtime'] == report['profile'] == {'observed': True}
    assert calls == [('viewer', {'show': False}), ('runtime', plan['source_paths']),
                     ('profile', 'recipe-digest'), 'close']


def test_annotation_report_cannot_escape_archive_admission(tmp_path, monkeypatch):
    monkeypatch.setenv('NAPARI_ARCHIVE', str(tmp_path / 'missing.zip'))
    monkeypatch.setattr(reference.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=json.dumps({'loaded_napari_files': {}, 'results': [{'state': 'plausible'}]})))
    with pytest.raises(ValueError, match='archive'):
        reference.run('/synthetic/python', '', [], hooke={})


def approved_pair():
    from env_worker.napari_cgl import REFERENCE_WHEEL
    versions = {**comparison.PINNED_DEPENDENCIES, 'napari': '0.7.1.dev0'}
    runtime = {'napari': '0.7.1.dev0', 'versions': versions, 'napari_wheel': REFERENCE_WHEEL,
               'distributions': len(versions), 'napari_files': 'same-files', 'distributions_sha256': 'same-records',
               'pinned_sources_sha256': hashlib.sha256(json.dumps(comparison.PINNED_SOURCES,
                                                                sort_keys=True).encode()).hexdigest()}
    pair = gui_profiles()
    for side in pair.values():
        side['runtime'] = copy.deepcopy(runtime)
    return pair


@pytest.mark.parametrize('change', [None, 'source', 'wheel', 'dependencies', 'settings', 'viewer', 'platform', 'missing'])
def test_annotation_requires_observed_approved_runtime_and_gui_context(monkeypatch, change):
    pair = approved_pair()
    report = pair['reference']
    # Isolate shared runtime/profile admission here; the real archive refusal is tested above.
    monkeypatch.setattr(reference, 'pinned_source_check', lambda report: {'same': 1, 'differs': [], 'absent': []})
    if change == 'source': report['runtime']['pinned_sources_sha256'] = 'wrong'
    elif change == 'wheel': report['runtime']['napari_wheel'] = 'wrong'
    elif change == 'dependencies': report['runtime']['versions']['numpy'] = 'wrong'
    elif change == 'settings': report['profile']['settings']['experimental']['dynamic_layer_controls'] = True
    elif change == 'viewer': report['profile']['viewer'] = 'napari.components.viewer_model.ViewerModel'
    elif change == 'platform': report['profile']['qt_platform'] = 'offscreen'
    elif change == 'missing': del report['runtime']
    if change is None:
        reference.admit(report, pair['hooke'])
    else:
        with pytest.raises((AssertionError, KeyError)):
            reference.admit(report, pair['hooke'])


@pytest.mark.parametrize('change,match', [
    ({'revision': 2}, 'accepted revision'),
    ({'revision': 4}, 'accepted revision'),
    ({'document': 'other'}, 'another document'),
    ({'session': 'other'}, 'another Project'),
])
def test_saved_labels_reopening_refuses_a_plausible_snapshot_from_the_wrong_input(change, match):
    previous = ({'session': 'project', 'document': 'labels', 'revision': 2}, {'review': 'old-review'})
    request = {**previous[0], 'revision': 3, **change}
    with pytest.raises(AssertionError, match=match):
        browser._accepted_reopening(previous, request, 3, {'review': 'new-review'})


@pytest.mark.parametrize('response', [{}, {'review': ''}, {'review': None}, {'review': 'old-review'}])
def test_saved_labels_reopening_requires_a_new_actual_review(response):
    previous = ({'session': 'project', 'document': 'labels', 'revision': 2}, {'review': 'old-review'})
    with pytest.raises(AssertionError, match='new review'):
        browser._accepted_reopening(previous, {**previous[0], 'revision': 3}, 3, response)


def test_saved_labels_reopening_accepts_only_the_retained_revision_in_a_new_review():
    previous = ({'session': 'project', 'document': 'labels', 'revision': 2}, {'review': 'old-review'})
    assert browser._accepted_reopening(previous, {**previous[0], 'revision': 3}, 3,
                                      {'review': 'new-review'}) == 'new-review'


def test_both_ordered_history_cases_remain_mandatory():
    identity = 'P4.label-history.step-2-undo-each-gesture-and-redo'
    check = checks.checks()[identity]
    child = {'id': identity, 'evidence_class': check['class'], 'procedure': ['synthetic'],
             'oracle': 'exact three-step history in each direction', 'criteria': ['both directions']}
    required = {checks.BROWSER + f'{checks.HISTORY}[{direction}]' for direction in ('undo', 'redo')}
    assert required <= set(check['tests'])
    for omitted in required:
        for outcome in (None, 'failed', 'skipped'):
            cases = [{'node': node, 'outcome': 'passed'} for node in check['tests'] if node != omitted]
            if outcome:
                cases.append({'node': omitted, 'outcome': outcome})
            results = {'junit': '/synthetic.xml', 'runtime': dict.fromkeys(check['requires'], '/synthetic'),
                       'cases': cases}
            assert acceptance.child_status(child, results, {identity: check})['status'] != 'pass'


def test_polyline_acceptance_requires_normal_and_inflight_double_click_completion():
    for child in ('P4.shape-create.polyline', 'P4.shape-create.step-1-create-each-supported-shape'):
        required = checks.checks()[child]['tests']
        assert checks.BROWSER + checks.create('polyline') in required
        assert checks.BROWSER + checks.create('polyline-fast-double') in required


def test_every_cancellation_case_remains_required_with_its_own_fresh_session():
    for child in ('P4.edit-conflicts.step-1-start-a-gesture-and-lose-focus',
                  'P4.edit-conflicts.step-2-cancel-a-pointer-and-disconnect',
                  'P4.edit-conflicts.reset-polygon', 'P4.edit-conflicts.shortcut-napari-reset-polygon'):
        required = checks.checks()[child]['tests']
        for case in ('focus', 'pointer-cancel', 'escape', 'disconnect'):
            assert checks.BROWSER + f'{checks.INTERRUPTED}[{case}]' in required
