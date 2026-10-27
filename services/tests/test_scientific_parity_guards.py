"""Synthetic H06 comparison admission regressions; no native or browser qualification."""
import copy
import hashlib
import json

import pytest

from bench import scientific_acceptance as acceptance, scientific_checks_h06b as h06b
from tests import scientific_parity as parity
from tests import test_scientific_reference_comparison as comparison
from tests.test_scientific_comparison_guards import gui_profiles

RUNTIME = 'tests/test_scientific_image_labels_parity.py::test_h06_worker_and_reference_use_approved_gui_runtimes'


def test_every_h06_child_requires_approved_runtime_and_source_evidence():
    for identity, check in acceptance.CHECKS.items():
        if identity not in h06b.checks() and not any(identity == f'P3.{family}.{child}'
                for family, children in acceptance.H06A_CHILDREN.items() for child in children):
            continue
        assert RUNTIME in check['tests'], identity
        assert {'NAPARI_ARCHIVE', 'NAPARI_REFERENCE_WHEEL'} <= set(check['requires'])
        child = {'id': identity, 'evidence_class': check['class'], 'procedure': ['synthetic'],
                 'oracle': 'synthetic', 'criteria': ['all required']}
        cases = [{'node': node, 'outcome': 'passed'} for node in check['tests']]
        results = {'cases': cases, 'junit': '/synthetic.xml', 'runtime': dict.fromkeys(check['requires'], '/synthetic')}
        assert acceptance.child_status(child, results, {identity: check})['status'] == 'pass'
        for outcome in (None, 'failed', 'skipped'):
            changed = [dict(row) for row in cases if row['node'] != RUNTIME]
            if outcome:
                changed.append({'node': RUNTIME, 'outcome': outcome})
            assert acceptance.child_status(child, {**results, 'cases': changed}, {identity: check})['status'] != 'pass'


def test_parity_reference_and_shared_observer_inputs_are_explicitly_pinned():
    pins = acceptance.check_inputs(['tests/test_scientific_points_shapes_parity.py'])
    for name in ('scientific_parity.py', 'scientific_parity_reference.py', 'scientific_pixel_profile.py', 'scientific_comparison_recipes.py',
                 'scientific_reference_dependencies.json', 'test_scientific_reference_comparison.py',
                 'scientific_napari_reference.py'):
        assert 'services/tests/' + name in pins


def approved_typography(side='reference'):
    return {'name': 'qt-glyph-96dpi-v1', 'use_96_dpi': side == 'reference',
            'font': {'family': '.AppleSystemUIFont', 'features': {'tnum': 1}, 'pixel_size': 341,
                     'point_size': 255.75, 'height': 401.59375, 'ascent': 329.671875,
                     'advances': {c: 200. for c in '0123456789abx='}}}


def approved_pixel_profile(side='reference'):
    return {'name': 'napari-gui-physical-pixels-typography-v2', 'typography': approved_typography(side), 'qt_platform': 'cocoa' if side == 'reference' else 'offscreen',
            'qt_scale_factor': '0.5' if side == 'reference' else None,
            'backend': 'CanvasBackendDesktop' if side == 'reference' else 'CanvasBackendCGL',
            'qt_viewer_dpr': 1.0, 'widget_dpr': 1.0, 'window_dpr': 1.0 if side == 'reference' else None,
            'logical_size': [256, 256], 'physical_size': [256, 256], 'pixel_scale': 1.0,
            'native_visible': side == 'reference', 'native_valid': True if side == 'reference' else None}


def approved_pair():
    from env_worker.napari_cgl import REFERENCE_WHEEL
    versions = {**comparison.PINNED_DEPENDENCIES, 'napari': '0.7.1.dev0'}
    runtime = {'napari': '0.7.1.dev0', 'versions': versions, 'napari_wheel': REFERENCE_WHEEL,
               'distributions': len(versions), 'napari_files': 'same-files', 'distributions_sha256': 'same-records',
               'pinned_sources_sha256': hashlib.sha256(json.dumps(comparison.PINNED_SOURCES, sort_keys=True).encode()).hexdigest()}
    pair = gui_profiles()
    for name, side in pair.items():
        side['runtime'] = copy.deepcopy(runtime)
        side['pixel_profile'] = approved_pixel_profile(name)
    return pair


@pytest.mark.parametrize('change', [None, 'source', 'wheel', 'dependencies', 'settings', 'viewer', 'platform', 'missing'])
def test_parity_refuses_unapproved_runtime_and_gui_context(monkeypatch, change):
    pair = approved_pair()
    report = pair['reference']
    from tests import scientific_napari_reference
    monkeypatch.setattr(scientific_napari_reference, 'pinned_source_check', lambda report: {'same': 1})
    if change == 'source': report['runtime']['pinned_sources_sha256'] = 'wrong'
    elif change == 'wheel': report['runtime']['napari_wheel'] = 'wrong'
    elif change == 'dependencies': report['runtime']['versions']['numpy'] = 'wrong'
    elif change == 'settings': report['profile']['settings']['experimental']['dynamic_layer_controls'] = True
    elif change == 'viewer': report['profile']['viewer'] = 'napari.components.viewer_model.ViewerModel'
    elif change == 'platform': report['profile']['qt_platform'] = 'offscreen'
    elif change == 'missing': del report['runtime']
    if change is None:
        parity.admit(report, pair['hooke'])
    else:
        with pytest.raises((AssertionError, KeyError)):
            parity.admit(report, pair['hooke'])


def test_parity_cannot_admit_without_approved_archive(tmp_path, monkeypatch):
    monkeypatch.setenv('NAPARI_ARCHIVE', str(tmp_path / 'missing.zip'))
    pair = approved_pair()
    with pytest.raises(ValueError, match='archive'):
        parity.admit(pair['reference'], pair['hooke'])


@pytest.mark.parametrize('fail', [None, 'scene', 'realize', 'settings', 'step'])
def test_independent_reference_observes_profile_and_closes_gui_on_failure(tmp_path, monkeypatch, fail):
    import sys
    from types import SimpleNamespace
    from importlib import metadata
    from tests import scientific_parity_reference as reference
    calls = []
    class Viewer:
        def __init__(self, **kwargs):
            calls.append(('viewer', kwargs))
            self.settings = {'first_time': True}
        def close(self): calls.append('close')
    package = tmp_path / 'napari'
    package.mkdir()
    source = package / '__init__.py'
    source.write_text('synthetic napari')
    monkeypatch.setitem(sys.modules, 'napari', SimpleNamespace(Viewer=Viewer, __file__=str(source), __version__='synthetic'))
    monkeypatch.setitem(sys.modules, 'napari.plugins', SimpleNamespace(_initialize_plugins=lambda: calls.append('plugins')))
    monkeypatch.setitem(sys.modules, 'qtpy.QtWidgets', SimpleNamespace(QApplication=SimpleNamespace(processEvents=lambda: calls.append('events'), setAttribute=lambda key, value: calls.append(('font-attribute', key, value)))))
    origin = {'url': 'file:///synthetic.whl', 'archive_info': {'hash': 'sha256=synthetic'}}
    monkeypatch.setattr(metadata, 'distribution', lambda name: SimpleNamespace(read_text=lambda name: json.dumps(origin)))
    observer = ('def runtime(paths): return {"observed_paths": paths}\n'
                'def comparison_profile(viewer, digest): return {"observed_recipe": digest, "settings": dict(viewer.settings)}\n')
    pixel_observer = 'def realize_pixel_canvas(viewer, width, height): ' + (
        'raise RuntimeError("realize failed")' if fail == 'realize' else
        'viewer.settings["first_time"] = False; return {}' if fail == 'settings' else 'return {"synthetic": True}')
    pixel_observer = ('def configure_reference_typography():\n from qtpy.QtWidgets import QApplication\n QApplication.setAttribute(\"font96\", True)\n' + pixel_observer)
    request = {'observer': observer, 'pixel_observer': pixel_observer, 'width': 64, 'height': 64, 'source_paths': ['src/napari/__init__.py'],
               'scene': 'raise RuntimeError("scene failed")' if fail == 'scene' else 'pass',
               'steps': [{'apply': 'raise RuntimeError("step failed")' if fail == 'step' else 'pass'}]}
    if fail:
        with pytest.raises(AssertionError if fail == 'settings' else RuntimeError,
                           match='changed GUI settings' if fail == 'settings' else f'{fail} failed'):
            reference.run(request, tmp_path)
    else:
        report = reference.run(request, tmp_path)
        assert report['napari_origin'] == origin
        assert report['runtime'] == {'observed_paths': request['source_paths']}
        assert report['profile'] == {'observed_recipe': hashlib.sha256(observer.encode()).hexdigest(), 'settings': {'first_time': True}}
        assert report['pixel_profile'] == {'synthetic': True}
        assert report['loaded_napari_files'] == {'__init__.py': hashlib.sha256(source.read_bytes()).hexdigest()}
    assert calls[0:3] == [('font-attribute', 'font96', True), 'plugins', ('viewer', {'show': False, 'show_welcome_screen': False})]
    assert calls[-1] == 'close'


@pytest.mark.parametrize('failure', [None, 'communicate', 'guard-start', 'guard-close'])
def test_reference_launcher_uses_private_cocoa_and_reaps_on_errors(tmp_path, monkeypatch, failure):
    from contextlib import nullcontext
    from tests import scientific_napari_reference
    calls, launched = [], {}
    class Process:
        returncode = 0
        def communicate(self, request, timeout):
            launched['request'] = json.loads(request)
            if failure == 'communicate':
                raise RuntimeError('communicate failed')
            return json.dumps({'results': []}).encode(), None
        def poll(self): return None if failure else 0
    process = Process()
    def start(command, **kwargs):
        launched.update(command=command, **kwargs)
        return process
    class Guard:
        def __init__(self, *args): pass
        def start(self):
            if failure == 'guard-start':
                raise RuntimeError('guard-start failed')
            return self
        def check(self): calls.append('check')
        def close(self):
            calls.append('close')
            if failure == 'guard-close':
                raise RuntimeError('guard-close failed')
    monkeypatch.setattr(parity, 'reference_python', lambda: tmp_path / 'runtime/bin/python')
    monkeypatch.setattr(parity.subprocess, 'Popen', start)
    monkeypatch.setattr(parity.worker_resources, 'Guard', Guard)
    monkeypatch.setattr(parity.owned_process, 'watching', lambda *args: nullcontext())
    monkeypatch.setattr(parity.owned_process, 'terminate', lambda p: calls.append(('reap', p)))
    monkeypatch.setattr(scientific_napari_reference, 'pinned_source_check', lambda report: {'same': 1})
    work = tmp_path / 'reference'
    if failure:
        with pytest.raises(RuntimeError, match=f'{failure} failed'):
            parity.reference_run('', [], width=64, height=64, work=work)
        assert calls[-1] == ('reap', process)
    else:
        assert parity.reference_run('', [], width=64, height=64, work=work)['pinned'] == {'same': 1}
        assert json.loads((work / 'report.json').read_text()) == {'results': []}
    assert launched['command'] == [str(tmp_path / 'runtime/bin/python'), '-I', '-u', str(parity.PROBE), str(work)]
    assert launched['env']['QT_QPA_PLATFORM'] == 'cocoa'
    assert launched['env']['QT_SCALE_FACTOR'] == '0.5'
    assert launched['env']['HOME'] == launched['env']['TMPDIR'] == str(work)
    assert launched['env']['QT_API'] == 'pyside6' and launched['start_new_session'] is True
    if failure != 'guard-start':
        assert launched['request']['observer'] == comparison.RECIPES.read_text()
        assert launched['request']['pixel_observer'] == parity.PIXEL_OBSERVER.read_text()
        assert launched['request']['source_paths'] == sorted(comparison.PINNED_SOURCES)
