"""H02 slice 6: two actual confined napari kernels from one input, each output resolving only its own producer.

Opt-in. NAPARI_EDITABLE_HOST_PREPARATION names an independently prepared integration that binds an editable
input `volume` ({'key', 'folder', 'environment'}). Absence is a skip, which the acceptance runner counts as a
failure. The kernels and saves are real; there is no browser and no manual paint edit (the prepared
integration's editable layer is an Image), so this does not qualify the manual-editor part of R01.
"""
import json
import os
from pathlib import Path

import numpy as np
import pytest

from gpu_worker import manifests as catalog
from orchestrator import scientific_exchange as exchange, scientific_host
from orchestrator.scientific_history import ScientificHistory
from orchestrator.scientific_projects import ProjectStore
from orchestrator.scientific_provenance import resolve
from orchestrator.scientific_saves import LayerSave, SaveConflict


@pytest.fixture
def prepared(monkeypatch, tmp_path):
    location = os.environ.get('NAPARI_EDITABLE_HOST_PREPARATION')
    if not location:
        pytest.skip('requires an independently prepared editable napari integration')
    prepared = json.loads(Path(location).read_text())
    for name, value in prepared['environment'].items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv('MICROMIND_HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('MICROMIND_SANDBOX_READ', raising=False)
    catalog.manifests.cache_clear()
    try:
        yield prepared
    finally:
        catalog.manifests.cache_clear()


def test_actual_kernels_from_one_input_resolve_only_their_own_producers_after_restart_and_reimport(prepared, tmp_path):
    from tests.test_scientific_arrays import fill
    store = ProjectStore(tmp_path / 'retained')
    original = np.arange(6 * 24 * 28, dtype='<u2').reshape(6, 24, 28)
    generation = Path(prepared['folder']).name
    owner, member = prepared['key'].split('#')
    runtime = {'bundle': owner, 'generation': generation, 'operation': member}
    saved = store.create('s1', {'kind': 'scene', 'title': 'Two kernels', 'state': {}, 'resources': [], 'runtimes': [runtime]})
    draft = store.array_draft(saved.id, 's1', component='array.volume', shape=original.shape, dtype='<u2',
                              axes=['z', 'y', 'x'], chunks=[1, 12, 14], scientific={'semantic': 'intensity'},
                              max_written_bytes=original.nbytes)
    ref = fill(draft, original)
    saved = store.save(saved.id, 's1', {**saved.document, 'resources': [ref],
                       'state': {'scientificView': {'runtime': runtime, 'inputs': {'volume': ref}}}}, expected_revision=1)
    # The integration hands napari a read-only array, so each kernel assigns a changed copy.
    kernels = [('first-kernel', 'data = viewer.layers[0].data.copy()\ndata.flat[3] = 11\nviewer.layers[0].data = data\n'),
               ('second-kernel', 'data = viewer.layers[0].data.copy()\ndata[2] = 22\nviewer.layers[0].data = data\nprint("whole plane")\n')]
    histories, saves = [], []
    for name, code in kernels:
        history = ScientificHistory(store, saved, name, tab='tab', placement='place')
        cell = 'cell-' + name.ljust(16, '0')
        with scientific_host.review(prepared['key'], generation,
                                    {'volume': store.array_source(saved.id, 's1', saved.revision, ref)}) as worker:
            shown, _ = worker.request({'op': 'layer-inventory'})
            layer = next(row for row in shown['inventory']['layers'] if row.get('editableInput') == 'volume')
            assert history.begin(cell, code)
            result, _ = worker.request({'op': 'python-resources', 'source': code})
            assert not result['console']['error']
            history.finish(cell, result['console'])
            save = LayerSave(store, saved, f'save-{name}'.ljust(20, '0'), None, layer['id'],
                             producer=history.producer(), input='volume')
            save.capture(worker)
        histories.append(history)
        saves.append(save)
    saves[0].publish()
    with pytest.raises(SaveConflict):
        saves[1].publish()
    copied = saves[1].copy(None, None)['resource']
    results = [(saved.id, saved.revision + 1), (copied['id'], 1)]
    restarted = ProjectStore(store.root)
    expected_chunks = [1, 4]  # flat[3] lies in one chunk; plane 2 spans its 2 x 2 chunks
    for (project, revision), history, chunks, (_, code) in zip(results, histories, expected_chunks, kernels):
        found = resolve(restarted, project, 's1', revision)
        assert found['problems'] == []
        [edit] = found['edits']
        assert edit['producer']['verified'] and edit['producer']['history']['id'] == history.record.id
        assert [c['source'] for c in edit['producer']['cells']] == [code]
        assert edit['changed']['chunks'] == chunks and edit['runtimes'] == [runtime]
        bundle = tmp_path / f'{project}.zip'
        exchange.export(restarted, project, 's1', revision, destination=bundle)
        elsewhere = ProjectStore(tmp_path / 'elsewhere')
        exchange.reimport(elsewhere, bundle)
        assert resolve(elsewhere, project, 's1', revision) == found
    first = restarted.array_source(saved.id, 's1', saved.revision + 1, restarted.read(saved.id, 's1').document['resources'][0])
    values = first.read(tuple(slice(0, n) for n in original.shape))
    expected = original.copy()
    expected.flat[3] = 11
    assert values.tobytes() == expected.tobytes()
