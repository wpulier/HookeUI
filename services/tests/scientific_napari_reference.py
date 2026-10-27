"""The plain pinned-napari reference: the same upstream calls in a process with no Hooke code at all.

A comparison runs each step twice: once through Hooke (its Studio control, or its Python console in the actual
review worker) and once here, in the prepared runtime's own Python, on a plain `napari.Viewer(show=False)` (napari's
own Qt widgets, including the dims sliders whose behaviour is part of napari's) holding the same arrays with the
same transforms. `SNAPSHOT` is the one piece of source both sides execute to describe the
state, so a difference in a snapshot is a difference in behaviour, never in how it was read. Expected values that
come from the fixture's definition (scientific_dims_fixture) are the independent third leg.

The reference process runs isolated (-I, an empty environment, no window shown) and reports the exact napari version,
every installed distribution's version and the hash of every napari file it loaded (checked against the pinned
archive by pinned_source_check). The snapshot reads each layer's current slice as napari holds it, without forcing a
re-slice, so a stale slice shows up as a difference; the reference only lets Qt deliver pending events first. A step
may set `result` to report something beyond the snapshot (for example the frames napari's playback requested).
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile

# Executed verbatim in the reference and in Hooke's console: no Hooke names, only napari's public model.
SNAPSHOT = r'''
def _hooke_snapshot(viewer, probes=()):
    import hashlib, numpy as _np
    d = viewer.dims
    out = {'ndim': int(d.ndim), 'ndisplay': int(d.ndisplay), 'order': [int(n) for n in d.order],
           'displayed': [int(n) for n in d.displayed], 'not_displayed': [int(n) for n in d.not_displayed],
           'displayed_order': [int(n) for n in d.displayed_order], 'current_step': [int(n) for n in d.current_step],
           'point': [float(n) for n in d.point], 'range': [[float(n) for n in r] for r in d.range],
           'nsteps': [int(n) for n in d.nsteps], 'axis_labels': [str(n) for n in d.axis_labels],
           'units': None if d.units is None else [str(u) for u in d.units], 'last_used': int(d.last_used),
           'rollable': [bool(n) for n in d.rollable], 'margin_left': [float(n) for n in d.margin_left],
           'margin_right': [float(n) for n in d.margin_right], 'thickness': [float(n) for n in d.thickness],
           'margin_left_step': [int(n) for n in d.margin_left_step],
           'margin_right_step': [int(n) for n in d.margin_right_step], 'layers': [],
           'layerlist_units': None if viewer.layers.units is None else [str(u) for u in viewer.layers.units],
           'layerlist_axis_labels': [str(n) for n in viewer.layers.axis_labels],
           'camera': {'center': [float(n) for n in viewer.camera.center], 'zoom': float(viewer.camera.zoom)}}
    for layer in viewer.layers:
        row = {'name': layer.name, 'type': layer._type_string, 'ndim': int(layer.ndim),
               'axis_labels': [str(n) for n in layer.axis_labels], 'units': [str(u) for u in layer.units],
               'scale': [float(n) for n in layer.scale], 'translate': [float(n) for n in layer.translate]}
        if layer._type_string == 'image':
            raw = _np.ascontiguousarray(layer._slice.image.raw)
            row.update(projection_mode=str(layer.projection_mode), slice_shape=list(raw.shape),
                       slice_dtype=raw.dtype.str, slice_sha256=hashlib.sha256(raw.tobytes()).hexdigest(),
                       slice_sum=float(raw.astype('float64').sum()))
        out['layers'].append(row)
    out['probes'] = []
    for name, world in probes:
        layer = viewer.layers[name]
        data = [float(n) for n in layer.world_to_data(world)]
        value = layer.get_value(world, world=True)
        out['probes'].append({'layer': name, 'world': [float(n) for n in world], 'data': data,
                              'value': None if value is None else _np.asarray(value).item()})
    return out
'''

PROGRAM = r'''
import hashlib, json, sys
import numpy as np
plan = json.loads(sys.stdin.read())
import napari

from napari.components import _viewer_key_bindings as actions
exec(plan['snapshot'])
viewer = napari.Viewer(show=False)
for spec in plan['layers']:
    data = np.arange(int(np.prod(spec['shape'])), dtype=spec['dtype']).reshape(spec['shape'])
    if spec.get('offset'):
        data = data + np.asarray(spec['offset'], dtype=spec['dtype'])
    viewer.add_image(data, name=spec['name'], **spec.get('kwargs', {}))
from qtpy.QtWidgets import QApplication
results = []
for step in plan['steps']:
    error, namespace = None, {'viewer': viewer, 'np': np, 'actions': actions, 'QApplication': QApplication}
    try:
        exec(step['code'], namespace)
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    QApplication.processEvents()  # deliver napari's pending Qt events; nothing re-slices on our behalf
    results.append({'name': step['name'], 'error': error, 'result': namespace.get('result'),
                    'state': _hooke_snapshot(viewer, [tuple(p) for p in step.get('probes', [])])})
import pathlib, importlib.metadata as md
base = pathlib.Path(napari.__file__).resolve().parent
loaded = {}
for module in list(sys.modules.values()):
    file = getattr(module, '__file__', None)
    p = pathlib.Path(file).resolve() if file else None
    if p is not None and base in p.parents:
        loaded[str(p.relative_to(base))] = hashlib.sha256(p.read_bytes()).hexdigest()
versions = {dist.metadata['Name']: dist.version for dist in md.distributions()}
print(json.dumps({'napari': napari.__version__, 'python': sys.version, 'versions': versions,
                  'loaded_napari_files': loaded, 'napari_origin': json.loads(md.distribution('napari').read_text('direct_url.json') or '{}'), 'results': results}))
'''


# napari's own playback (QtDims.play → AnimationThread) in the reference: the frames it requests, in order, with the
# time of each after Play. A hidden canvas never draws, and napari plays the next frame only after a draw, so the
# loop draws (screenshot) whenever napari waits for one, as a shown canvas would. It stops after `limit` frames or
# 4 s (once mode stops by itself).
PLAYBACK = '''
import time
viewer.dims.current_step = {start!r}
QApplication.processEvents()
frames = []
viewer.dims.events.current_step.connect(lambda e: frames.append([int(viewer.dims.current_step[{axis}]), time.monotonic()]))
qt_dims = viewer.window._qt_viewer.dims
began = time.monotonic()
qt_dims.play(axis={axis}, fps={fps}, loop_mode={mode!r}, frame_range=({first}, {last}))
playing = []
while time.monotonic() - began < 4 and len(frames) < {limit}:
    QApplication.processEvents()
    playing.append(bool(qt_dims.is_playing))
    if not viewer.dims._play_ready:
        viewer.screenshot(canvas_only=True, flash=False)
    time.sleep(0.002)
qt_dims.stop()
QApplication.processEvents()
result = {{'frames': [f for f, _ in frames], 'after_play_s': [round(t - began, 4) for _, t in frames],
           'was_playing': any(playing), 'playing_after_stop': bool(qt_dims.is_playing)}}
'''


def pinned_source_check(report):
    """Compare every napari file the reference loaded with the pinned archive (the inventory's), byte for byte.

    NAPARI_ARCHIVE (or the legacy ledger location) must match the shared approved digest.
    Only generated _version.py may come from the exact approved local wheel; absent evidence refuses the report.
    """
    import hashlib
    import os
    root = Path(__file__).resolve().parents[2]
    ledger = root / 'services/bench/specification/parity-cases.json'
    archive = os.environ.get('NAPARI_ARCHIVE') or (
        json.loads(ledger.read_text()).get('reference', {}).get('archive') if ledger.is_file() else None)
    if not archive or not Path(archive).is_file():
        raise ValueError('The pinned reference archive is unavailable; no comparison can be certified')
    from bench.scientific_inventory import _archive_files
    sources = _archive_files(Path(archive))  # approved digest, size and safe member names
    loaded = report.get('loaded_napari_files')
    if type(loaded) is not dict or not loaded:
        raise ValueError('The reference reported no loaded napari sources')
    if '_version.py' in loaded:
        sources['src/napari/_version.py'] = generated_version(report)
    same, differs, absent = 0, [], []
    for relative, sha in sorted(loaded.items()):
        source = sources.get('src/napari/' + relative)
        if source is None:
            absent.append(relative)
        elif hashlib.sha256(source).hexdigest() == sha:
            same += 1
        else:
            differs.append(relative)
    if differs or absent:
        raise ValueError(f'Reference sources differ from the approved archive: changed={differs}, absent={absent}')
    return {'archive': str(archive), 'same': same, 'differs': [], 'absent': []}


def generated_version(report):
    """Only napari's generated version file may come from the exact approved local wheel."""
    import hashlib
    import os
    from urllib.parse import unquote, urlsplit
    import zipfile
    from env_worker.napari_cgl import REFERENCE_WHEEL
    origin = report.get('napari_origin', {})
    override = os.environ.get('NAPARI_REFERENCE_WHEEL')
    url = urlsplit(Path(override).resolve().as_uri() if override else origin.get('url', ''))
    if url.scheme != 'file' or url.netloc or not url.path:
        raise ValueError('Generated reference source requires the approved local wheel')
    wheel = Path(unquote(url.path))
    if not wheel.is_file() or wheel.stat().st_size > 32 << 20:
        raise ValueError('The approved reference wheel is unavailable or oversized')
    body = wheel.read_bytes()
    if hashlib.sha256(body).hexdigest() != REFERENCE_WHEEL:
        raise ValueError('Reference wheel differs from the approved artifact')
    from io import BytesIO
    with zipfile.ZipFile(BytesIO(body)) as archive:
        name = 'napari/_version.py'
        if archive.namelist().count(name) != 1 or archive.getinfo(name).file_size > 1 << 20:
            raise ValueError('The approved wheel has no unambiguous generated version source')
        return archive.read(name)


def runtime_python(prepared):
    """The prepared integration's own interpreter: the pinned napari Hooke's worker also runs."""
    return Path(prepared['folder']) / 'bin' / 'python'


def run(python, layers, steps, *, timeout=300):
    """Run steps on a plain napari Viewer; return the reference report (raises on a failed process)."""
    plan = {'snapshot': SNAPSHOT, 'layers': layers, 'steps': steps}
    with tempfile.TemporaryDirectory(prefix='hooke-reference-') as work:
        reply = subprocess.run([str(python), '-I', '-c', PROGRAM], input=json.dumps(plan), capture_output=True,
                               text=True, timeout=timeout, cwd=work,
                               env={'PATH': '/usr/bin:/bin', 'HOME': work, 'NAPARI_CONFIG': str(Path(work) / 'n.yaml'),
                                    'NAPARI_ASYNC': '0', 'OMP_NUM_THREADS': '1',
                                    'QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM': '1'})
    if reply.returncode != 0:
        raise RuntimeError(f'The napari reference failed:\n{reply.stderr[-6000:]}')
    report = json.loads(reply.stdout.strip().splitlines()[-1])
    report['pinned'] = pinned_source_check(report)
    return report
