"""Synthetic replay/transport regressions, not native or browser qualification."""
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import numpy as np

from tests import scientific_comparison_recipes as recipes


def test_camera_replay_uses_orientation_before_angles_without_model_update():
    calls = []

    class Camera:
        def __setattr__(self, name, value):
            calls.append((name, value))
        def update(self, value):
            raise AssertionError('unordered/batched model update is not the control setter sequence')

    value = {'center': [5, 19.5, 30], 'angles': [0, 0, 25], 'zoom': 18,
             'perspective': 40, 'orientation': ['towards', 'up', 'right']}
    recipes.apply_equivalent(['camera', value], SimpleNamespace(camera=Camera()))
    assert calls == [(name, value[name]) for name in ('orientation', 'perspective', 'center', 'angles', 'zoom')]


def test_bounded_console_transfer_and_owned_session_cleanup():
    result = subprocess.run(['node', '--test', str(Path(__file__).with_name('scientific_p2_observation.test.mjs'))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_reference_rebuilds_exact_json_in_a_gui_draws_and_closes(tmp_path, monkeypatch):
    """Exercise the replay lifecycle without importing Qt or a native runtime."""
    import sys
    import pytest
    from tests import scientific_reference_replay as replay
    calls, viewers = [], []

    class Viewer:
        def __init__(self, **kwargs):
            assert kwargs == {'show': False, 'show_welcome_screen': False}
            self.camera = SimpleNamespace()
            self.dims = SimpleNamespace()
            self.window = SimpleNamespace(_qt_viewer=SimpleNamespace(canvas=SimpleNamespace()))
            self.layers = []
            viewers.append(self)
        def add_layer(self, layer):
            self.layers.append(layer)
        def screenshot(self, **kwargs):
            assert kwargs == {'size': (384, 512), 'canvas_only': True, 'flash': False}
            calls.append('draw')
        def close(self):
            calls.append('close')

    def create(data, state, kind):
        assert kind == 'surface'
        assert type(state['opacity']) is float and state['opacity'] == 1.0
        assert state['metadata']['native_integer'] == 9007199254740993
        assert data[0].dtype.str == '>u2' and data[0].tolist() == [1, 65535]
        return state

    monkeypatch.setitem(sys.modules, 'napari', SimpleNamespace(Viewer=Viewer))
    monkeypatch.setitem(sys.modules, 'napari.layers', SimpleNamespace(Layer=SimpleNamespace(create=create)))
    monkeypatch.setitem(sys.modules, 'napari.plugins', SimpleNamespace(_initialize_plugins=lambda: None))
    source = tmp_path / 'recipes.py'
    source.write_text('''
PROFILE = 'fixture'
def apply_equivalent(op, viewer):
    for key, value in op[1].items(): setattr(viewer.camera, key, value)
def snapshot(names, viewer): return {'angle': viewer.camera.angles}
def comparison_profile(viewer, digest): return {'viewer': type(viewer).__name__, 'recipe': digest}
def runtime(paths): return {'sources': paths}
''')
    camera = {'center': [0., 0., 0.], 'angles': [0., 0., 25.], 'zoom': 1., 'perspective': 0.,
              'orientation': ['towards', 'down', 'right'], 'mouse_pan': True, 'mouse_zoom': True, 'synced': True}
    start = {'layers': {'mesh': {'type': 'surface', 'state': {'opacity': 1.0,
            'metadata': {'native_integer': 9007199254740993}}, 'data': [{'dtype': '>u2', 'values': [1, 65535]}]}},
             'camera': camera, 'ndisplay': 3}
    request = {'recipes': str(source), 'profile': 'fixture', 'initial': json.dumps(start), 'canvas_size': [384, 512],
               'ops': [['camera', {**camera, 'angles': [0., 0., -25.]}]],
               'checkpoints': [{'id': 'orientation', 'ops': 1}], 'source_paths': ['pinned.py']}
    result = replay.run(request)
    assert calls == ['draw', 'draw', 'close']
    assert result['states'] == {'initial': {'angle': [0., 0., 25.]}, 'orientation': {'angle': [0., 0., -25.]}}
    assert result['profile']['viewer'] == 'Viewer'
    calls.clear()
    monkeypatch.setattr(Viewer, 'screenshot', lambda self, **kw: (_ for _ in ()).throw(ValueError('draw failed')))
    with pytest.raises(ValueError, match='draw failed'):
        replay.run(request)
    assert calls == ['close']


def test_image_appearance_replay_keeps_complete_submitted_values_and_attenuation_negative():
    from env_worker import scientific_image_appearance as production
    # Synthetic native attributes preserve the same int/float distinction that
    # napari's contrast/iso setters retained in the actual cab1a851 receipt.
    initial = dict(contrast_limits=[0.0, 3000.0], gamma=1.0, rendering='mip',
                   iso_threshold=1500.0, attenuation=0.05, auto_contrast=False,
                   data=np.array([2**63 + 1], dtype='uint64'))
    submitted = {'contrastLimits': [0, 3000], 'gamma': 1, 'rendering': 'attenuated_mip',
                 'isoThreshold': 1500, 'attenuation': 0.3, 'autoContrast': False}
    actual, reference = SimpleNamespace(**initial), SimpleNamespace(**initial)
    production.apply(actual, production.settings(submitted))
    assert type(actual.contrast_limits[0]) is int and type(initial['contrast_limits'][0]) is float
    assert recipes.normalize(actual.iso_threshold) != recipes.normalize(initial['iso_threshold'])
    recipes.apply_equivalent(['image-presentation', 'volume', submitted], SimpleNamespace(layers={'volume': reference}))
    assert recipes.normalize(vars(reference)) == recipes.normalize(vars(actual))
    assert reference.data.dtype == 'uint64' and int(reference.data[0]) == 2**63 + 1

    class Broken(SimpleNamespace):
        def __setattr__(self, name, value):
            if name != 'attenuation': super().__setattr__(name, value)

    broken = Broken(**initial)
    production.apply(broken, production.settings(submitted))
    assert recipes.normalize(vars(broken)) != recipes.normalize(vars(reference))
    assert broken.attenuation == 0.05 and reference.attenuation == 0.3


def test_surface_appearance_replay_keeps_unchanged_defaults_and_setter_order():
    from env_worker import scientific_surface_appearance as production
    calls = {'actual': [], 'reference': []}

    class Layer:
        def __init__(self, side):
            object.__setattr__(self, 'side', side)
            object.__setattr__(self, 'wireframe', SimpleNamespace())
            object.__setattr__(self, 'normals', SimpleNamespace(face=SimpleNamespace(), vertex=SimpleNamespace()))
            object.__setattr__(self, 'contrast_limits', [1.0, 5.0])
        def __setattr__(self, name, value):
            calls[self.side].append((name, value))
            object.__setattr__(self, name, value)

    line = {'visible': False, 'width': 1, 'color': [0, 0, 0, 1]}
    submitted = {'shading': 'flat', 'gamma': 1, 'contrastLimits': [1, 5], 'autoContrast': False,
                 'wireframe': line, 'faceNormals': {**line, 'length': 10},
                 'vertexNormals': {**line, 'length': 10}}
    actual, reference = Layer('actual'), Layer('reference')
    production.apply(actual, production.settings(submitted))
    recipes.apply_equivalent(['surface-presentation', 'mesh', submitted], SimpleNamespace(layers={'mesh': reference}))
    assert calls['reference'] == calls['actual']
    assert [key for key, _ in calls['reference']] == ['shading', 'gamma', 'auto_contrast', 'contrast_limits', 'auto_contrast']
    assert recipes.normalize(reference.contrast_limits) == [1, 5]
    assert vars(reference.wireframe) == vars(actual.wireframe)
    assert vars(reference.normals.face) == vars(actual.normals.face)
    assert vars(reference.normals.vertex) == vars(actual.normals.vertex)
