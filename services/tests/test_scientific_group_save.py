"""Synthetic capture workers, real native storage and atomic Project publication."""
import itertools

import numpy as np
import pytest

from orchestrator.scientific_group_save import LayersSave
from orchestrator.scientific_host import CaptureRefused
from orchestrator.scientific_projects import InvalidProject
from orchestrator.scientific_saves import SaveConflict, SaveNotStarted, SaveUnavailable
from tests.test_scientific_edits import scene, values
from tests.test_scientific_saves import Worker


@pytest.fixture
def multiple(scene):
    store, saved, first, data = scene
    writer = store.array_draft(saved.id, saved.session, component='array.second', shape=data.shape,
        dtype=data.dtype.str, axes=['y', 'x'], chunks=[2, 3],
        scientific={'semantic': 'instance-labels'}, max_written_bytes=data.nbytes)
    for y, x in itertools.product(range(2), repeat=2):
        writer.write([y, x], (data + 100).astype(data.dtype)[y*2:y*2+2, x*3:x*3+3])
    second = writer.finish()
    document = saved.document
    document['resources'].append(second)
    document['state']['scientificView'] = {'runtime': {}, 'inputs': {'labels': first, 'second': second}}
    saved = store.save(saved.id, saved.session, document, expected_revision=saved.revision)
    return store, saved, data


LAYERS = [{'layer': 'a'*32, 'input': 'labels'}, {'layer': 'b'*32, 'input': 'second'}]


class GroupWorker:
    def __init__(self, data):
        self.data = {'labels': (data + 50).astype(data.dtype), 'second': (data + 150).astype(data.dtype)}
        self.calls = 0

    def stage_resources(self, layers):
        self.calls += 1
        for layer, input, edit in layers:
            Worker(self.data[input]).stage_layer(None, layer, input, edit)


def pending(multiple):
    store, saved, data = multiple
    save = LayersSave(store, saved, 'save-1234567890123456', LAYERS, producer={'kind': 'test', 'seed': 42})
    worker = GroupWorker(data)
    save.capture(worker)
    return save, worker


def verify(store, saved, expected):
    for name, reference in saved.document['state']['scientificView']['inputs'].items():
        assert reference in saved.document['resources']
        assert np.array_equal(values(store, saved, reference), expected[name])


def test_all_layers_publish_once_and_old_values_survive_reopen(multiple):
    store, saved, data = multiple
    save, worker = pending(multiple)
    assert store.read(saved.id, saved.session) == saved
    outcome = save.publish()
    assert save.publish() == outcome and worker.calls == 1
    accepted = store.read(saved.id, saved.session)
    assert accepted.revision == saved.revision + 1
    assert outcome['layers'] == ['a'*32, 'b'*32]
    provenance = accepted.document['state']['hookeEdit']
    assert provenance['format'] == 2 and provenance['transaction'] == accepted.transaction
    assert len(provenance['changes']) == 2
    assert all(row['producer'] == {'kind': 'test', 'seed': 42} for row in provenance['changes'])
    assert all(row['authority'] == 'accepted-resource' and row['frame'] is None for row in provenance['changes'])
    verify(store, accepted, worker.data)
    verify(store, saved, {'labels': data, 'second': data + 100})
    save.close()
    from orchestrator.scientific_projects import ProjectStore
    reopened = ProjectStore(store.root)
    verify(reopened, reopened.read(saved.id, saved.session), worker.data)


@pytest.mark.parametrize('after', [False, True])
def test_retry_after_failed_ack_never_recaptures_or_partially_saves(multiple, monkeypatch, after):
    store, saved, data = multiple
    save, worker = pending(multiple)
    expected = {name: array.copy() for name, array in worker.data.items()}
    original = store.save
    def fail(*args, **kwargs):
        if after: original(*args, **kwargs)
        raise OSError('controlled ack failure')
    with monkeypatch.context() as patch:
        patch.setattr(store, 'save', fail)
        with pytest.raises(SaveUnavailable, match='could not be confirmed'): save.publish()
    if not after: assert store.read(saved.id, saved.session) == saved
    for array in worker.data.values(): array[:] = 0
    save.publish()
    accepted = store.read(saved.id, saved.session)
    assert accepted.revision == saved.revision+1 and worker.calls == 1
    verify(store, accepted, expected)


@pytest.mark.parametrize('before_capture', [True, False])
def test_conflict_keeps_all_changes_in_one_copy_and_can_copy_again(multiple, before_capture):
    store, saved, data = multiple
    save = LayersSave(store, saved, 'save-1234567890123456', LAYERS)
    worker = GroupWorker(data)
    if not before_capture: save.capture(worker)
    winner = store.save(saved.id, saved.session, {**saved.document, 'title': 'Newer work'}, expected_revision=saved.revision)
    with pytest.raises(SaveConflict):
        if before_capture: save.capture(worker)
        else: save.publish()
    assert store.read(saved.id, saved.session) == winner
    copied = save.copy(worker, None)
    assert save.copy(worker, None) == copied and worker.calls == 1
    target = store.read(copied['resource']['id'], saved.session)
    verify(store, target, worker.data)
    assert store.read(saved.id, saved.session) == winner
    lineage = target.document['state']['hookeCopy']
    assert lineage['format'] == 2 and len(lineage['history'][0]['changes']) == 2
    assert all(row['output']['id'] == target.id for row in target.document['state']['hookeEdit']['changes'])
    # The copied provenance remains readable for a subsequent conflict/copy.
    again = LayersSave(store, target, 'save-2234567890123456', LAYERS)
    again.capture(worker)
    store.save(target.id, target.session, {**target.document, 'title': 'Another winner'}, expected_revision=target.revision)
    with pytest.raises(SaveConflict): again.publish()
    copied_again = again.copy(worker, None)
    newest = store.read(copied_again['resource']['id'], saved.session)
    assert len(newest.document['state']['hookeCopy']['history']) == 2
    verify(store, newest, worker.data)
    save.close(); again.close()


def test_refused_capture_and_cancel_publish_no_layer(multiple):
    store, saved, data = multiple
    save = LayersSave(store, saved, 'save-1234567890123456', LAYERS)
    class Refused:
        def stage_resources(self, layers): raise CaptureRefused('second layer changed calibration')
    with pytest.raises(SaveNotStarted, match='Private work remains'): save.capture(Refused())
    assert store.read(saved.id, saved.session) == saved
    corrected, worker = pending(multiple)
    with pytest.raises(SaveUnavailable, match='stopped'): corrected.publish(lambda: True)
    assert store.read(saved.id, saved.session) == saved


def test_same_component_cannot_enter_twice(multiple):
    store, saved, _ = multiple
    with pytest.raises(InvalidProject, match='once'):
        LayersSave(store, saved, 'save-1234567890123456', [LAYERS[0], {**LAYERS[1], 'input': 'labels'}])
    assert store.read(saved.id, saved.session) == saved


def test_manifest_preparation_failure_keeps_exact_captured_layers_for_retry(multiple, monkeypatch):
    store, saved, data = multiple
    save, worker = pending(multiple)
    original = save.layers[1].edit.prepared
    with monkeypatch.context() as patch:
        patch.setattr(save.layers[1].edit, 'prepared', lambda: (_ for _ in ()).throw(OSError('controlled manifest failure')))
        with pytest.raises(SaveUnavailable, match='could not be confirmed'): save.publish()
    assert store.read(saved.id, saved.session) == saved
    assert save.status()['state'] == 'staged' and save.edit is not None
    save.publish()
    assert worker.calls == 1
    verify(store, store.read(saved.id, saved.session), worker.data)
