"""A retained image larger than the eager allowance opens lazily in the actual confined napari worker (H08).

Needs the prepared private bundle (NAPARI_NATIVE_HOST_PREPARATION or NAPARI_HOST_PREPARATION); otherwise it
skips. The parent's reads are recorded where they happen (orchestrator.native_regions.Source.read), so the test
measures what actually crossed the native transport, not what the worker says it did.
"""
from pathlib import Path

import numpy as np
import pytest

import json
import os

from gpu_worker import manifests as catalog


@pytest.fixture
def actual_host(monkeypatch):
    """The prepared private review bundle: NAPARI_NATIVE_HOST_PREPARATION (the acceptance runner passes it through)
    or NAPARI_HOST_PREPARATION (as tests/test_scientific_host_worker.py); without either, every test here skips."""
    location = os.environ.get('NAPARI_NATIVE_HOST_PREPARATION') or os.environ.get('NAPARI_HOST_PREPARATION')
    if not location:
        pytest.skip('requires a prepared private review bundle (NAPARI_NATIVE_HOST_PREPARATION); not browser qualification')
    prepared = json.loads(Path(location).read_text())
    for name, value in prepared['environment'].items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv('MICROMIND_SANDBOX_READ', raising=False)
    catalog.manifests.cache_clear()
    try:
        yield prepared
    finally:
        catalog.manifests.cache_clear()

SHAPE = (48, 1024, 1024)            # uint16: 96 MiB, above the 64 MiB eager allowance


def ask(worker, request):
    """worker.request, saying the worker's own error output when it fails (its work folder goes at close)."""
    try:
        return worker.request(request)
    except Exception:
        log = worker.work / 'stderr.log'
        print('worker stderr:', log.read_text()[-3000:] if log.exists() else '(none)')
        raise


def values():
    z, y, x = np.ogrid[:SHAPE[0], :SHAPE[1], :SHAPE[2]]
    return ((z * 1000 + y * 3 + x) % 65521).astype('<u2')


def test_a_large_retained_image_is_read_by_slice_in_the_actual_worker(actual_host):
    from orchestrator import scientific_host as parent
    from env_worker.scientific_output_wire import calibration
    from tests.test_native_regions import source
    prepared, data, reads = actual_host, values(), []
    metadata = {'format': 'native-layer-output-v1', 'shape': list(SHAPE), 'dtype': '<u2', 'name': 'large stack',
                'type': 'image', 'visible': True, 'opacity': 1.,
                'geometry': {'basis': [[0, 0, 0], [2, 0, 0], [0, .65, 0], [0, 0, .65]],
                             'axisLabels': ['z', 'y', 'x'], 'units': ['micrometer'] * 3}}
    volume = source(data, reads)
    volume.descriptor.update(semantic='intensity',
                             calibration=calibration(metadata, volume.axes, {'session': 's1', 'id': 'coordinates', 'revision': 1}))
    full = data.nbytes
    with parent.review(prepared['key'], Path(prepared['folder']).name, {'volume': volume},
                       retained_layers=[{'input': 'volume', 'metadata': metadata}]) as worker:
        first, pixels = ask(worker, {'op': 'render', 'width': 256, 'height': 192})
        assert len(set(pixels)) > 10
        opened = sum(np.prod([s.stop - s.start for s in r]) * 2 for r in reads)
        assert 0 < opened < full / 4, f'opening read {opened / 2**20:.1f} MiB of {full / 2**20:.0f} MiB'
        for z in (5, 30, 47):
            done, _ = ask(worker, {'op': 'python-resources', 'source': (
                f"viewer.dims.set_point(0, {z * 2})\n"
                f"layer = viewer.layers[0]\n"
                f"assert type(layer.data).__name__ == 'LazyNative', type(layer.data)\n"
                f"assert int(layer.data[{z}, 500, 600]) == {int(data[z, 500, 600])}\n")})
            assert not done['console']['error'], done['console']
            ask(worker, {'op': 'render', 'width': 256, 'height': 192})
        refused, _ = ask(worker, {'op': 'python-resources', 'source': (
            "import numpy as np\n"
            "try:\n    np.asarray(viewer.layers[0].data)\n"
            "except ValueError as error:\n    assert 'lazily, by slice' in str(error), error\n"
            "else:\n    raise AssertionError('a whole-array read was allowed')\n"
            "stats = viewer.layers[0].data.stats\n"
            "assert stats['residentBytes'] <= 256 << 20 and stats['misses'] > 0, stats\n")})
        assert not refused['console']['error'], refused['console']
        total = sum(np.prod([s.stop - s.start for s in r]) * 2 for r in reads)
        assert all(np.prod([s.stop - s.start for s in r]) * 2 <= 1 << 20 for r in reads)
        assert total < full / 2, f'navigating four planes read {total / 2**20:.1f} MiB of {full / 2**20:.0f} MiB'
    assert worker.process.poll() is not None


def test_an_ome_zarr_pyramid_opens_as_one_multiscale_layer_and_reads_only_what_is_shown(actual_host, tmp_path):
    from orchestrator import ngff_sources as ngff
    from orchestrator import scientific_host as parent
    from env_worker.scientific_output_wire import calibration
    from bench import h14_fixtures
    root = tmp_path / 'big.ome.zarr'
    shape = (4, 4096, 4096)                       # level 0: 128 MiB of uint16, three levels
    h14_fixtures.write_ome_zarr(root, shape, levels=3)
    from tests.ngff_fixtures import selected
    projects, saved = selected(root, tmp_path / "projects")
    store = ngff.SelectedStore(projects, saved.id, saved.session, saved.revision)
    pyramid = ngff.open_multiscale(store)
    sources = {f'level{i}': s for i, s in enumerate(pyramid.sources())}
    metadata = {'format': 'native-layer-output-v1', 'shape': list(shape), 'dtype': '<u2', 'name': 'pyramid',
                'type': 'image', 'visible': True, 'opacity': 1.,
                'geometry': {'basis': [[0, 0, 0], [2, 0, 0], [0, .65, 0], [0, 0, .65]],
                             'axisLabels': ['z', 'y', 'x'], 'units': ['micrometer'] * 3}}
    space = {'session': 's1', 'id': 'coordinates', 'revision': 1}
    sources['level0'].descriptor['calibration'] = calibration(metadata, sources['level0'].axes, space)
    before = len(store.audit)
    with parent.review(actual_host['key'], Path(actual_host['folder']).name, sources,
                       retained_layers=[{'input': 'level0', 'metadata': metadata, 'levels': list(sources)}]) as worker:
        _, pixels = ask(worker, {'op': 'render', 'width': 256, 'height': 192})
        assert len(set(pixels)) > 10
        done, _ = ask(worker, {'op': 'python-resources', 'source': (
            "layer = viewer.layers[0]\n"
            "assert layer.multiscale and all(type(level).__name__ == 'LazyNative' for level in layer.data), type(layer.data)\n"
            "assert [tuple(level.shape) for level in layer.data] == [(4, 4096, 4096), (4, 2048, 2048), (4, 1024, 1024)]\n"
            "assert int(layer.data[2][3, 100, 200]) == "
            f"{int(h14_fixtures.big_value(3, 400, 800))}\n"
            "assert int(layer.data[0][1, 4095, 17]) == "
            f"{int(h14_fixtures.big_value(1, 4095, 17))}\n")})
        assert not done['console']['error'], done['console']
    read = sum(row['bytes'] for row in store.audit[before:])
    level0 = shape[0] * shape[1] * shape[2] * 2
    assert 0 < read < level0 / 8, f'showing the pyramid read {read / 2**20:.1f} MiB of a {level0 / 2**20:.0f} MiB level 0'


def test_a_labels_layer_over_64_mib_is_edited_and_saved_sending_only_its_changed_chunk(actual_host, tmp_path, monkeypatch):
    """P5.incremental-workflow.native-large-layer-save: opened through bounded reads in the confined napari worker,
    edited, saved; only the changed chunk crosses the pipe (the rest are kept by digest)."""
    import itertools
    from orchestrator import scientific_host as parent
    from orchestrator import scientific_project_review as reviews
    from orchestrator.scientific_projects import ProjectStore
    from orchestrator.scientific_saves import LayerSave
    from env_worker.scientific_output_wire import calibration
    shape, chunks = (80, 1024, 1024), (1, 1024, 1024)                 # |u1 labels, 80 MiB, one MiB per chunk
    data = (np.arange(int(np.prod(shape)), dtype=np.uint32) % 251).astype('|u1').reshape(shape)
    metadata = {'format': 'native-layer-output-v1', 'shape': list(shape), 'dtype': '|u1', 'name': 'large labels',
                'type': 'labels', 'visible': True, 'opacity': 1.,
                'geometry': {'basis': [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
                             'axisLabels': ['z', 'y', 'x'], 'units': ['pixel'] * 3}}
    key, operation = actual_host['key'].split('#')
    runtime = {'bundle': key, 'generation': Path(actual_host['folder']).name, 'operation': operation}
    store = ProjectStore(tmp_path / 'projects')
    first = store.create('s1', {'kind': 'scene', 'title': 'Large labels', 'state': {}, 'resources': [], 'runtimes': [runtime]})
    draft = store.array_draft(first.id, 's1', component='array.labels', shape=list(shape), dtype='|u1', axes=['z', 'y', 'x'],
                              chunks=list(chunks), max_written_bytes=data.nbytes, scientific={
                                  'semantic': 'instance-labels',
                                  'calibration': calibration(metadata, ('z', 'y', 'x'), {'session': 's1', 'id': 'coordinates', 'revision': 1})})
    for index in itertools.product(*(range(n) for n in draft.grid)):
        draft.write(index, np.ascontiguousarray(data[tuple(slice(i * c, (i + 1) * c) for i, c in zip(index, chunks))]))
    ref = draft.finish()
    document = first.document
    document['resources'] = [ref]
    document['state']['scientificView'] = {'runtime': runtime, 'inputs': {'labels': ref},
                                           'layers': [{'input': 'labels', 'metadata': metadata}]}
    saved = store.save(first.id, 's1', document, expected_revision=1)
    sent = []
    answer = parent.LayerStaging.answer
    def counting(self, header, payload):
        sent.append((header.get('op'), len(payload)))
        return answer(self, header, payload)
    monkeypatch.setattr(parent.LayerStaging, 'answer', counting)
    with reviews.review(store, saved.id, 's1', saved.revision) as (_, worker):
        shown, _ = ask(worker, {'op': 'render', 'width': 256, 'height': 192})
        layer = shown['frame']['layers'][0]
        assert layer.get('editableInput') == 'labels', layer
        done, _ = ask(worker, {'op': 'python-resources', 'source':
                                  "layer = viewer.layers[0]\nlayer.data[37, 5, 9] = 200\nlayer.refresh()\n"})
        assert not done['console']['error'], done['console']
        shown, _ = ask(worker, {'op': 'render', 'width': 256, 'height': 192})
        save = LayerSave(store, saved, 'large-labels-save-0001', shown['frame'], layer['id'])
        try:
            save.capture(worker)
            receipt = save.publish()
        finally:
            save.close()
    writes = [n for op, n in sent if op == 'write-native-chunk']
    keeps = [n for op, n in sent if op == 'keep-native-chunk']
    assert len(writes) == 1 and writes[0] == 1 << 20 and len(keeps) == 79, (len(writes), len(keeps))
    latest = store.read(saved.id, 's1')
    new = next(r for r in latest.document['resources'] if r['component'] == 'array.labels')
    source = store.array_source(saved.id, 's1', latest.revision, new)
    assert source.read((slice(37, 38), slice(5, 6), slice(9, 10))).item() == 200
    assert np.array_equal(source.read((slice(36, 37), slice(0, 1024), slice(0, 1024))), data[36:37])
    old = store.array_source(saved.id, 's1', saved.revision, ref)
    assert old.read((slice(37, 38), slice(5, 6), slice(9, 10))).item() == data[37, 5, 9]
