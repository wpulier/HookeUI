"""The `labels-tool` and `annotate` changes on the actual confined review worker (no browser): the frame reports each
layer's napari tool state, napari's own actions and gestures change it, and Hooke's annotation history undoes them.

Opt-in: NAPARI_ANNOTATION_PREPARATION (the prepared annotation review integration). The browser journeys are in
tests/test_scientific_annotation_browser.py; this checks the worker protocol they ride on.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from gpu_worker import manifests as catalog
from tests import scientific_annotation_fixture as fx


@pytest.fixture
def prepared(monkeypatch):
    location = os.environ.get('NAPARI_ANNOTATION_PREPARATION')
    if not location:
        pytest.skip('requires the prepared annotation review integration')
    value = json.loads(Path(location).read_text())
    for name, setting in value['environment'].items():
        monkeypatch.setenv(name, setting)
    monkeypatch.delenv('MICROMIND_SANDBOX_READ', raising=False)
    catalog.manifests.cache_clear()
    try:
        yield value
    finally:
        catalog.manifests.cache_clear()


def seeded(store, prepared):
    bundle, operation = prepared['key'].split('#')
    runtime = {'bundle': bundle, 'generation': Path(prepared['folder']).name, 'operation': operation}
    saved = store.create('s1', {'kind': 'scene', 'title': 'H07 worker', 'state': {}, 'resources': [], 'runtimes': [runtime]})
    data = fx.values()
    draft = store.array_draft(saved.id, 's1', component='array.labels', shape=data.shape, dtype=data.dtype.str,
                              axes=list(fx.AXES), chunks=list(data.shape), scientific=fx.scientific(),
                              max_written_bytes=data.nbytes)
    draft.write([0, 0, 0, 0], data)
    reference = draft.finish()
    document = {**saved.document, 'resources': [reference],
                'state': {'scientificView': {'runtime': runtime, 'inputs': {'labels': reference}}}}
    return store.save(saved.id, 's1', document, expected_revision=saved.revision)


def test_labels_tool_and_points_shapes_annotation_on_the_actual_worker(tmp_path, prepared):
    from orchestrator.scientific_host import ChangeRefused
    from orchestrator.scientific_project_review import review
    from orchestrator.scientific_projects import ProjectStore
    store = ProjectStore(tmp_path / 'data')
    saved = seeded(store, prepared)
    with review(store, saved.id, 's1', saved.revision) as (_, worker):
        first, _ = worker.request({'op': 'render', 'width': 256, 'height': 192})
        labels = next(row for row in first['frame']['layers'] if row['type'] == 'labels')
        assert labels['labelsTool']['mode'] == 'pan_zoom' and labels['labelsTool']['selectedLabel'] == '1'
        changed, _ = worker.request({'op': 'change', 'width': 256, 'height': 192, 'request': {
            'frame': first['frame']['frame'], 'operation': 'labels-tool', 'value': {'layer': labels['id'], 'action': 'new_label'}}})
        row = next(r for r in changed['frame']['layers'] if r['id'] == labels['id'])
        assert row['labelsTool']['selectedLabel'] == str(fx.BIG + 1), 'napari new_label: the largest label plus one'
        changed, _ = worker.request({'op': 'change', 'width': 256, 'height': 192, 'request': {
            'frame': changed['frame']['frame'], 'operation': 'labels-tool',
            'value': {'layer': labels['id'], 'settings': {'mode': 'fill', 'brushSize': 9, 'preserve': True}}}})
        row = next(r for r in changed['frame']['layers'] if r['id'] == labels['id'])['labelsTool']
        assert (row['mode'], row['brushSize'], row['preserve']) == ('fill', 9, True)
        with pytest.raises(ChangeRefused):  # refused before anything applies
            worker.request({'op': 'change', 'width': 256, 'height': 192, 'request': {
                'frame': changed['frame']['frame'], 'operation': 'labels-tool',
                'value': {'layer': labels['id'], 'settings': {'editDimensions': 8}}}})
        # A Shapes layer made in Python, then a rectangle drawn by napari from frame pixels.
        reply, _ = worker.request({'op': 'python-resources', 'source': "viewer.add_shapes(name='P4 shapes', ndim=2)\n"
                                   "viewer.dims.current_step = (1, 2, 0, 0)"})
        assert not reply['console']['error'], reply['console']['error']
        frame, _ = worker.request({'op': 'render', 'width': 256, 'height': 192})
        shapes = next(r for r in frame['frame']['layers'] if r['type'] == 'shapes')
        assert shapes['annotation'] == {'mode': 'pan_zoom', 'selected': 0, 'creating': False, 'undo': 0, 'redo': 0}
        events = [{'kind': 'move', 'point': [60.0, 80.0], 'modifiers': []}, {'kind': 'press', 'point': [60.0, 80.0], 'modifiers': []},
                  {'kind': 'move', 'point': [90.0, 120.0], 'modifiers': []}, {'kind': 'move', 'point': [120.0, 170.0], 'modifiers': []},
                  {'kind': 'release', 'point': [120.0, 170.0], 'modifiers': []}]
        drawn, _ = worker.request({'op': 'change', 'width': 256, 'height': 192, 'request': {
            'frame': frame['frame']['frame'], 'operation': 'annotate',
            'value': {'layer': shapes['id'], 'mode': 'add_rectangle', 'events': events}}})
        row = next(r for r in drawn['frame']['layers'] if r['id'] == shapes['id'])
        assert row['elementCount'] == 1 and row['annotation']['undo'] == 1 and row['annotation']['mode'] == 'add_rectangle'
        camera, (cy, cx) = drawn['frame']['camera'], drawn['frame']['camera']['center'][-2:]
        zoom = camera['zoom']
        expected = sorted([(cy + (y - 96) / zoom, cx + (x - 128) / zoom) for y in (60, 120) for x in (80, 170)])
        reply, _ = worker.request({'op': 'python-resources', 'source':
                                   "import json\nprint(json.dumps([[float(v) for v in p] for p in viewer.layers['P4 shapes'].data[0]]))"})
        drawn_corners = sorted(tuple(p) for p in json.loads(reply['console']['stdout'].strip().splitlines()[-1]))
        assert np.allclose(drawn_corners, expected, atol=1e-6), (drawn_corners, expected)
        drawn, _ = worker.request({'op': 'render', 'width': 256, 'height': 192})  # Python ran: a new current frame
        undone, _ = worker.request({'op': 'change', 'width': 256, 'height': 192, 'request': {
            'frame': drawn['frame']['frame'], 'operation': 'annotate', 'value': {'layer': shapes['id'], 'history': 'undo'}}})
        assert next(r for r in undone['frame']['layers'] if r['id'] == shapes['id'])['elementCount'] == 0
        with pytest.raises(ChangeRefused):
            worker.request({'op': 'change', 'width': 256, 'height': 192, 'request': {
                'frame': undone['frame']['frame'], 'operation': 'annotate', 'value': {'layer': shapes['id'], 'history': 'undo'}}})
        redone, _ = worker.request({'op': 'change', 'width': 256, 'height': 192, 'request': {
            'frame': undone['frame']['frame'], 'operation': 'annotate', 'value': {'layer': shapes['id'], 'history': 'redo'}}})
        assert next(r for r in redone['frame']['layers'] if r['id'] == shapes['id'])['elementCount'] == 1
