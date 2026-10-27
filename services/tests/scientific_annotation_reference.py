"""The plain pinned-napari reference for P4 (H07) annotation: the same edits in a process with no Hooke code at all.

A comparison runs each edit twice: once through Hooke (its Studio Labels tools or Python panel, in the actual review
worker) and once here, in the prepared runtime's own Python, on a plain `napari.Viewer(show=False)` holding the same
fixture (tests/scientific_annotation_fixture.CODE) with the same calibration. A gesture here goes through napari's own
mouse-callback dispatch (`napari.utils.interactions.mouse_*_callbacks`) with napari's own test event shape
(`napari.utils._testsupport.MouseEvent`, fields copied), at the world positions the Studio's pointer reached, so
`draw`, `pick`, the brush, preserve, fill and history are napari's. `SNAPSHOT` is executed verbatim on both sides, so a
difference in a snapshot is a difference in behaviour, never in how it was read.

Every report uses scientific_napari_reference's approved archive/generated-wheel check and the common
scientific_comparison_recipes runtime observer and GUI profile. The worker observes through its existing console;
the independent reference uses unmodified Cocoa, never a model-only fallback. Both settings are observed, not forced
to match. Native compatibility and timings still require an actual run; no upstream limitation is inferred here.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from tests.scientific_napari_reference import pinned_source_check, runtime_python  # noqa: F401  (re-exported)


def observation_source():
    """The common observer, executed through the existing session console, without changing settings."""
    from tests import test_scientific_reference_comparison as comparison
    source = comparison.RECIPES.read_text()
    return (source + '\nimport json as _json\nprint(_json.dumps({"runtime": runtime('
            f'{sorted(comparison.PINNED_SOURCES)!r}), "profile": comparison_profile(viewer, '
            f'{hashlib.sha256(source.encode()).hexdigest()!r})}}))')


def admit(report, hooke):
    """Every native report must match the approved source, wheel, dependencies and actual GUI context."""
    report['pinned'] = pinned_source_check(report)
    from tests import test_scientific_reference_comparison as comparison
    pair = {'hooke': hooke, 'reference': report}
    comparison._assert_pinned_runtime(pair)
    comparison._assert_gui_profile(pair)
    return report

# Executed verbatim in the reference and in Hooke's console: napari's public model only.
SNAPSHOT = r'''
def _p4_snapshot(viewer, base=None):
    import hashlib, numpy as _np
    d = viewer.dims
    out = {'dims': {'order': [int(n) for n in d.order], 'displayed': [int(n) for n in d.displayed],
                    'current_step': [int(n) for n in d.current_step], 'ndisplay': int(d.ndisplay),
                    'point': [float(n) for n in d.point]},
           'camera': {'center': [float(n) for n in viewer.scene.camera.center], 'zoom': float(viewer.scene.camera.zoom),
                      'canvas': [int(n) for n in viewer.canvas.size],
                      'orientation2d': [str(n) for n in viewer.scene.camera.orientation2d]}, 'layers': []}
    for layer in viewer.layers:
        row = {'name': layer.name, 'type': layer._type_string, 'scale': [float(n) for n in layer.scale],
               'translate': [float(n) for n in layer.translate]}
        if layer._type_string == 'labels':
            data = _np.asarray(layer.data)
            row.update(shape=list(data.shape), dtype=data.dtype.str,
                       sha256=hashlib.sha256(_np.ascontiguousarray(data).tobytes()).hexdigest(),
                       mode=str(layer.mode), selected_label=str(int(layer.selected_label)),
                       background=str(int(layer.colormap.background_value)), brush_size=float(layer.brush_size),
                       n_edit_dimensions=int(layer.n_edit_dimensions), preserve_labels=bool(layer.preserve_labels),
                       contiguous=bool(layer.contiguous), show_selected_label=bool(layer.show_selected_label),
                       undo=len(layer._undo_history), redo=len(layer._redo_history))
            if base is not None and base.shape == data.shape:
                where = _np.argwhere(base != data)
                row['changed'] = [[*map(int, i), str(int(base[tuple(i)])), str(int(data[tuple(i)]))] for i in where[:20000]]
                row['changed_count'] = int(len(where))
        elif layer._type_string == 'points':
            row.update(data=[[float(v) for v in p] for p in _np.asarray(layer.data)], mode=str(layer.mode),
                       selected=sorted(int(i) for i in layer.selected_data), size=[float(s) for s in layer.size],
                       symbol=[str(s) for s in layer.symbol], current_size=float(layer.current_size),
                       current_symbol=str(layer.current_symbol))
        elif layer._type_string == 'shapes':
            row.update(data=[[[float(v) for v in p] for p in _np.asarray(s)] for s in layer.data],
                       shape_type=[str(t) for t in layer.shape_type], mode=str(layer.mode),
                       selected=sorted(int(i) for i in layer.selected_data), z_index=[int(z) for z in layer.z_index])
        out['layers'].append(row)
    return out
'''

PROGRAM = r'''
import hashlib, json, sys
from dataclasses import dataclass
import numpy as np
plan = json.loads(sys.stdin.read())
import napari
from napari.utils._proxies import ReadOnlyWrapper
from napari.utils.interactions import mouse_move_callbacks, mouse_press_callbacks, mouse_release_callbacks
from qtpy.QtWidgets import QApplication

@dataclass
class MouseEvent:  # napari.utils._testsupport.MouseEvent's fields, plus the modifiers napari's callbacks read
    type: str
    position: tuple
    is_dragging: bool = False
    dims_displayed: tuple = (0, 1)
    dims_point: list = None
    view_direction: list = None
    pos: list = (0, 0)
    button: int = None
    handled: bool = False
    modifiers: tuple = ()

def _common(button=1, modifiers=()):
    d = viewer.dims  # napari's canvas events carry dims_displayed as a list (QtViewer passes list(dims.displayed))
    return {'dims_displayed': [int(n) for n in d.displayed], 'dims_point': [float(n) for n in d.point],
            'view_direction': None, 'button': button, 'modifiers': tuple(modifiers)}

def _pos(world):  # the canvas position a vispy event carries; only differences between events are read
    return np.asarray([float(world[i]) for i in viewer.dims.displayed][::-1])

def gesture(layer, worlds, *, button=1, modifiers=()):
    """Press at the first world position, drag through the others, release at the last: napari's own dispatch."""
    common = _common(button, modifiers)
    mouse_press_callbacks(layer, ReadOnlyWrapper(MouseEvent(type='mouse_press', position=tuple(worlds[0]),
                                                            pos=_pos(worlds[0]), **common)))
    for world in worlds[1:]:
        viewer.cursor.position = tuple(world)
        mouse_move_callbacks(layer, ReadOnlyWrapper(MouseEvent(type='mouse_move', is_dragging=True,
                                                               position=tuple(world), pos=_pos(world), **common)))
    mouse_release_callbacks(layer, ReadOnlyWrapper(MouseEvent(type='mouse_release', position=tuple(worlds[-1]),
                                                              pos=_pos(worlds[-1]), **common)))

def hover(layer, world):
    """The pointer moves over the canvas: napari's viewer updates the hovered value, then the move callbacks run."""
    viewer.mouse_over_canvas = True
    viewer.cursor.position = tuple(world)
    viewer.update_status_from_cursor()
    mouse_move_callbacks(layer, ReadOnlyWrapper(MouseEvent(type='mouse_move', is_dragging=False,
                                                           position=tuple(world), pos=_pos(world), **_common())))

def replay(layer, requests):
    """The Studio's annotate requests, as napari itself takes them in its own canvas: an action runs napari's
    key-binding function; a gesture sets the mode (when it differs) and its pointer and Shift events become real vispy
    events on napari's canvas (moves paced like a person's pointer, past napari's 5 ms mouse-move throttle)."""
    import time
    from vispy.util import keys
    from napari.layers.points import _points_key_bindings as point_actions
    from napari.layers.shapes import _shapes_key_bindings as shape_actions
    canvas = viewer.window._qt_viewer.canvas._scene_canvas
    named = {'Shift': keys.SHIFT, 'Control': keys.CONTROL, 'Alt': keys.ALT, 'Meta': keys.META}
    viewer.layers.selection.active = layer
    for request in requests:
        if 'action' in request:
            (point_actions if layer._type_string == 'points' else shape_actions).__dict__[request['action']](layer)
            continue
        if 'history' in request:
            raise ValueError('napari keeps no Points or Shapes history; Hooke\'s Undo/Redo has no napari equivalent')
        if str(layer.mode) != request['mode']:
            layer.mode = request['mode']
        press = None
        for event in request['events']:
            if event['kind'] == 'key':
                (canvas.events.key_press if event['down'] else canvas.events.key_release)(
                    key=keys.SHIFT, modifiers=(keys.SHIFT,) if event['down'] else ())
                continue
            y, x = event['point']
            mods = tuple(named[m] for m in event['modifiers'])
            if event['kind'] == 'press':
                press = canvas.events.mouse_press(pos=(x, y), button=1, buttons=[1], modifiers=mods)
            elif event['kind'] == 'move':
                canvas.events.mouse_move(pos=(x, y), button=1 if press else None, buttons=[1] if press else [],
                                         modifiers=mods, press_event=press)
                QApplication.processEvents(); time.sleep(0.012); QApplication.processEvents()
            elif event['kind'] == 'release':
                canvas.events.mouse_release(pos=(x, y), button=1, buttons=[], modifiers=mods, press_event=press)
                press = None
            else:
                canvas.events.mouse_double_click(pos=(x, y), button=1, buttons=[1], modifiers=mods)
            QApplication.processEvents()

exec(plan['snapshot'])
viewer = napari.Viewer(show=False)
try:
    exec(plan['observer'])
    observed_runtime = runtime(plan['source_paths'])
    observed_profile = comparison_profile(viewer, plan['recipe_sha256'])
    namespace = {'viewer': viewer, 'np': np, 'gesture': gesture, 'hover': hover, 'replay': replay, 'QApplication': QApplication}
    exec(plan['setup'], namespace)
    base = namespace.get('base')
    results = []
    for step in plan['steps']:
        error = None
        namespace.pop('result', None)
        try:
            exec(step['code'], namespace)
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
        QApplication.processEvents()
        results.append({'name': step['name'], 'error': error, 'result': namespace.get('result'),
                        'state': _p4_snapshot(viewer, namespace.get('base'))})
    import pathlib, importlib.metadata as md
    root = pathlib.Path(napari.__file__).resolve().parent
    loaded = {}
    for module in list(sys.modules.values()):
        file = getattr(module, '__file__', None)
        p = pathlib.Path(file).resolve() if file else None
        if p is not None and root in p.parents:
            loaded[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    versions = {dist.metadata['Name']: dist.version for dist in md.distributions()}
    report = {'napari': napari.__version__, 'python': sys.version, 'versions': versions,
              'napari_origin': json.loads(md.distribution('napari').read_text('direct_url.json') or '{}'),
              'runtime': observed_runtime, 'profile': observed_profile,
              'loaded_napari_files': loaded, 'results': results}
finally:
    viewer.close()
print(json.dumps(report, default=str))
'''


def same_view(camera, step):
    """Reference code that puts its viewer where the worker's was: the same slice, canvas size and camera, so
    napari's own canvas_to_world turns the Studio's frame pixels into the same world positions there."""
    height, width = camera['canvas']
    return (f"viewer.dims.current_step = {tuple(step)!r}\n"
            f"viewer.canvas.size = ({height}, {width})\n"
            f"viewer.window._qt_viewer.canvas._scene_canvas.size = ({width}, {height})\nQApplication.processEvents()\n"
            f"viewer.scene.camera.center = {tuple(camera['center'])!r}\nviewer.scene.camera.zoom = {camera['zoom']!r}\n"
            f"assert tuple(viewer.canvas.size) == ({height}, {width}), viewer.canvas.size\n"
            # A visible native view draws before interaction. Drawing updates per-layer screen scale,
            # including Shapes' initial vertex radius; setting camera.zoom alone leaves stale values.
            f"viewer.screenshot(size=({height}, {width}), canvas_only=True, flash=False)\n")


def worlds(pixels):
    """Reference code: napari's canvas_to_world for frame pixels (y, x), as the worker resolves a gesture."""
    return f"[[float(v) for v in viewer.canvas_to_world(tuple(p))] for p in {[list(map(float, p)) for p in pixels]!r}]"


def run(python, setup, steps, *, hooke, timeout=300):
    """Run `setup` then each step on a plain napari Viewer; return the report (raises on a failed process)."""
    from tests import test_scientific_reference_comparison as comparison
    observer = comparison.RECIPES.read_text()
    plan = {'snapshot': SNAPSHOT, 'setup': setup, 'steps': steps, 'observer': observer,
            'source_paths': sorted(comparison.PINNED_SOURCES),
            'recipe_sha256': hashlib.sha256(observer.encode()).hexdigest()}
    with tempfile.TemporaryDirectory(prefix='hooke-annotation-reference-') as work:
        reply = subprocess.run([str(python), '-I', '-c', PROGRAM], input=json.dumps(plan), capture_output=True,
                               text=True, timeout=timeout, cwd=work,
                               env={'PATH': '/usr/bin:/bin', 'HOME': work, 'TMPDIR': work,
                                    'NAPARI_CONFIG': str(Path(work) / 'n.yaml'), 'QT_API': 'pyside6',
                                    'QT_QPA_PLATFORM': 'cocoa', 'NAPARI_ASYNC': '0', 'OMP_NUM_THREADS': '1',
                                    'QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM': '1'})
    if reply.returncode != 0:
        raise RuntimeError(f'The napari reference failed:\n{reply.stderr[-6000:]}')
    return admit(json.loads(reply.stdout.strip().splitlines()[-1]), hooke)
