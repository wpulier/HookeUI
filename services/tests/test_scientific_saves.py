"""Accepted snapshot recovery with real native storage and a synthetic export worker."""
import itertools
from types import SimpleNamespace
import numpy as np
import pytest

from orchestrator import scientific_saves as saves
from orchestrator.scientific_projects import InvalidProject
from tests.test_scientific_edits import scene, values
from tests.test_scientific_host_worker import frame


def setup(scene):
    store, saved, ref, data = scene
    doc = saved.document
    doc['state']['scientificView'] = {'runtime': {}, 'inputs': {'labels': ref}}
    saved = store.save(saved.id, saved.session, doc, expected_revision=saved.revision)
    shown = frame()['frame']
    shown['layers'] = [{'id': 'd' * 32, 'editableInput': 'labels'}]
    return store, saved, shown, data


class Worker:
    def __init__(self, data): self.data, self.calls = data, 0
    def stage_layer(self, frame, layer, input, edit):
        self.calls += 1
        spec = edit.specification()
        shape, chunks = spec['shape'], spec['chunks']
        for index in itertools.product(*(range((n+c-1)//c) for n,c in zip(shape,chunks))):
            slices = tuple(slice(i*c,min((i+1)*c,n)) for i,c,n in zip(index,chunks,shape))
            edit.write(index, self.data[slices])


def test_save_accepts_exact_values_once_without_retargeting_original(scene):
    store, saved, shown, data = setup(scene)
    changed = data.copy(); changed[1, 4] = 65535
    worker = Worker(changed)
    save = saves.LayerSave(store, saved, 'save-1234567890123456', shown, 'd'*32)
    save.capture(worker)
    assert store.read(saved.id, saved.session) == saved
    outcome = save.publish()
    assert save.publish() == outcome and worker.calls == 1
    accepted = store.read(saved.id, saved.session)
    ref = accepted.document['resources'][0]
    provenance = accepted.document['state']['hookeEdit']
    assert provenance['authority'] == 'accepted-resource'
    assert provenance['frame'] is None and provenance['view'] is None
    assert accepted.revision == saved.revision+1 and accepted.document['state']['scientificView']['inputs']['labels'] == ref
    assert np.array_equal(values(store, accepted, ref), changed)
    assert np.array_equal(values(store, saved, saved.document['resources'][0]), data)
    save.close()
    assert store.read(saved.id, saved.session) == accepted


@pytest.mark.parametrize('after', [False, True])
def test_publication_retry_never_recaptures_or_reexecutes(scene, monkeypatch, after):
    store, saved, shown, data = setup(scene)
    worker = Worker(data.copy())
    save = saves.LayerSave(store, saved, 'save-1234567890123456', shown, 'd'*32)
    save.capture(worker)
    original = store.save
    def fail(*args, **kwargs):
        if after: original(*args, **kwargs)
        raise OSError('controlled publication failure')
    monkeypatch.setattr(store, 'save', fail)
    with pytest.raises(saves.SaveUnavailable, match='could not be confirmed'): save.publish()
    worker.data[:] = 0
    monkeypatch.setattr(store, 'save', original)
    save.publish()
    accepted = store.read(saved.id, saved.session)
    assert accepted.revision == saved.revision+1 and worker.calls == 1
    assert np.array_equal(values(store, accepted, accepted.document['resources'][0]), data)


def test_concurrent_save_preserves_winner_and_refuses_rebase(scene):
    store, saved, shown, data = setup(scene)
    save = saves.LayerSave(store, saved, 'save-1234567890123456', shown, 'd'*32)
    save.capture(Worker(data))
    winner = store.save(saved.id, saved.session, {**saved.document, 'title': 'Winning changes'}, expected_revision=saved.revision)
    with pytest.raises(saves.SaveUnavailable, match='accepted first'): save.publish()
    with pytest.raises(saves.SaveUnavailable, match='not available'): save.publish()
    assert save.status()['state'] == 'conflict' and store.read(saved.id, saved.session) == winner


def test_newer_revision_before_capture_has_a_durable_conflict_and_recovery_reference(scene):
    store, saved, shown, data = setup(scene)
    winner = store.save(saved.id, saved.session, {**saved.document, 'title': 'Winning changes'}, expected_revision=saved.revision)
    save = saves.LayerSave(store, saved, 'save-1234567890123456', shown, 'd'*32)
    worker = Worker(data.copy())
    with pytest.raises(saves.SaveUnavailable, match='newer revision'):
        save.capture(worker)
    status = save.status()
    assert status['state'] == 'conflict' and status['resource'] is None
    assert status['latest'] == {'day': winner.session, 'id': winner.id, 'version': winner.revision, 'picture': None}
    with pytest.raises(saves.SaveUnavailable, match='not available'): save.publish()
    assert worker.calls == 0 and store.read(saved.id, saved.session) == winner
    save.close()
    assert np.array_equal(values(store, saved, saved.document['resources'][0]), data)


def test_recovery_reference_read_failure_does_not_erase_the_conflict(scene, monkeypatch):
    store, saved, shown, data = setup(scene)
    winner = store.save(saved.id, saved.session, {**saved.document, 'title':'Newer work'}, expected_revision=saved.revision)
    save = saves.LayerSave(store, saved, 'save-1234567890123456', shown, 'd'*32)
    original, calls = store.read, []
    def read(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2: raise OSError('controlled recovery-reference failure')
        return original(*args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(store, 'read', read)
        with pytest.raises(saves.SaveConflict): save.capture(Worker(data))
    assert save.status()['state'] == 'conflict' and 'latest' not in save.status()
    assert store.read(saved.id, saved.session) == winner
    save.close()


def test_stop_disk_and_missing_binding_leave_data_unchanged(scene, monkeypatch):
    store, saved, shown, data = setup(scene)
    with pytest.raises(InvalidProject): saves.LayerSave(store,saved,'save-1234567890123456',shown,'other')
    worker = Worker(data)
    save = saves.LayerSave(store,saved,'save-1234567890123456',shown,'d'*32)
    with monkeypatch.context() as patch:
        patch.setattr(saves.shutil,'disk_usage',lambda _: SimpleNamespace(free=0))
        with pytest.raises(InvalidProject, match='space'): save.capture(worker)
    assert worker.calls == 0
    save.capture(worker)
    with pytest.raises(saves.SaveUnavailable, match='stopped'): save.publish(lambda: True)
    assert store.read(saved.id, saved.session) == saved


def test_memory_refusal_can_retry_without_reexporting(scene, monkeypatch):
    from contextlib import contextmanager
    store, saved, shown, data = setup(scene)
    worker = Worker(data)
    save = saves.LayerSave(store,saved,'save-1234567890123456',shown,'d'*32)
    @contextmanager
    def refuse(_):
        raise saves.jobs.Failed('controlled memory refusal')
        yield
    with monkeypatch.context() as patch:
        patch.setattr(saves.jobs.MEMORY, 'reserve', refuse)
        with pytest.raises(saves.SaveUnavailable, match='memory'): save.capture(worker)
    assert worker.calls == 0 and store.read(saved.id,saved.session) == saved
    save.capture(worker)
    with monkeypatch.context() as patch:
        patch.setattr(saves.jobs.MEMORY, 'reserve', refuse)
        with pytest.raises(saves.SaveUnavailable, match='memory'): save.publish()
    assert save.status()['state'] == 'staged'
    save.publish()
    assert worker.calls == 1 and store.read(saved.id,saved.session).revision == saved.revision+1


def test_failed_export_cannot_be_misreported_as_recoverable_publication(scene):
    store, saved, shown, data = setup(scene)
    worker = Worker(data)
    def broken(*_): raise InvalidProject('invalid chunk')
    worker.stage_layer = broken
    save = saves.LayerSave(store,saved,'save-1234567890123456',shown,'d'*32)
    with pytest.raises(RuntimeError, match='export failed'): save.capture(worker)
    assert save.edit is None and store.read(saved.id,saved.session) == saved
    save.close()


@pytest.mark.parametrize('after', [False, True])
def test_manifest_storage_failure_retries_the_frozen_snapshot(scene, monkeypatch, after):
    from orchestrator import scientific_arrays
    store, saved, shown, data = setup(scene)
    changed = data.copy(); changed[1,4] = 65535
    worker = Worker(changed)
    save = saves.LayerSave(store,saved,'save-1234567890123456',shown,'d'*32)
    save.capture(worker)
    original = scientific_arrays._put
    def fail(folder, body, extension):
        if extension == '.json':
            if after: original(folder,body,extension)
            raise OSError('controlled manifest storage failure')
        return original(folder,body,extension)
    with monkeypatch.context() as patch:
        patch.setattr(scientific_arrays,'_put',fail)
        with pytest.raises(saves.SaveUnavailable): save.publish()
    assert save.status()['state'] == 'staged'
    worker.data[:] = 0
    save.publish()
    accepted = store.read(saved.id,saved.session)
    expected = data.copy(); expected[1,4] = 65535
    assert worker.calls == 1 and np.array_equal(values(store,accepted,accepted.document['resources'][0]), expected)


def test_invalid_finalization_is_terminal_not_an_impossible_retry(scene, monkeypatch):
    from orchestrator import scientific_arrays
    store, saved, shown, data = setup(scene)
    save = saves.LayerSave(store,saved,'save-1234567890123456',shown,'d'*32)
    save.capture(Worker(data))
    def fail(_): raise InvalidProject('invalid metadata')
    monkeypatch.setattr(scientific_arrays,'_encode_manifest',fail)
    with pytest.raises(saves.SaveUnavailable, match='cannot be saved'): save.publish()
    assert save.status()['state'] == 'failed'
    with pytest.raises(saves.SaveUnavailable, match='not available'): save.publish()
    assert store.read(saved.id,saved.session) == saved


@pytest.mark.parametrize('captured', [False, True])
def test_conflict_copy_keeps_snapshot_and_never_reexecutes_after_capture(scene, captured):
    store, saved, shown, data = setup(scene)
    changed = data.copy(); changed[1,4] = 61000
    worker = Worker(changed.copy())
    save = saves.LayerSave(store,saved,'save-1234567890123456',shown,'d'*32)
    if captured: save.capture(worker)
    winner = store.save(saved.id,saved.session,{**saved.document,'title':'Winner'},expected_revision=saved.revision)
    with pytest.raises(saves.SaveConflict):
        if captured: save.publish()
        else: save.capture(worker)
    if captured: worker.data[:] = 0
    result = save.copy(worker,shown)
    assert result['state'] == 'copied' and worker.calls == 1
    assert save.copy(worker,None) == result and worker.calls == 1
    copied = store.read(result['resource']['id'], saved.session)
    assert np.array_equal(values(store,copied,copied.document['resources'][0]),changed)
    assert store.read(saved.id,saved.session) == winner
    save.close()
    assert store.read(copied.id,copied.session) == copied


@pytest.mark.parametrize('copying', [False, True])
def test_frozen_reference_survives_validation_read_failure_without_reexport(scene, monkeypatch, copying):
    from orchestrator import scientific_copies
    store,saved,shown,data=setup(scene)
    worker=Worker(data.copy())
    save=saves.LayerSave(store,saved,'save-1234567890123456',shown,'d'*32)
    if copying:
        store.save(saved.id,saved.session,{**saved.document,'title':'Winner'},expected_revision=saved.revision)
        with pytest.raises(saves.SaveConflict): save.capture(worker)
    else: save.capture(worker)
    def fail(*args,**kwargs): raise OSError('controlled frozen reference reread failure')
    with monkeypatch.context() as patch:
        patch.setattr(scientific_copies.PreparedArray,'__init__',fail)
        with pytest.raises(saves.SaveUnavailable):
            if copying: save.copy(worker,shown)
            else: save.publish()
    worker.data[:]=0
    outcome=save.copy(worker,shown) if copying else save.publish()
    accepted=store.read(outcome['resource']['id'],saved.session)
    assert np.array_equal(values(store,accepted,accepted.document['resources'][0]),data)
    assert worker.calls == 1
    save.close()


def test_retained_volume_requires_only_its_existing_staging_budget(scene, monkeypatch):
    store, saved, shown, data = setup(scene)
    worker = Worker(data.copy())
    save = saves.LayerSave(store,saved,'save-1234567890123456',shown,'d'*32)
    def disk(path):
        assert path == store.root
        return SimpleNamespace(free=data.nbytes+saves.SAVE_WORKING_BYTES+saves.SPARE)
    with monkeypatch.context() as patch:
        patch.setattr(saves.shutil, 'disk_usage', disk)
        save.capture(worker)
    save.publish()
    accepted = store.read(saved.id, saved.session)
    assert worker.calls == 1
    assert np.array_equal(values(store, accepted, accepted.document['resources'][0]), data)
    save.close()


def test_resource_capture_refusal_preserves_other_private_work_and_can_be_corrected(scene):
    from orchestrator.scientific_host import CaptureRefused
    store,saved,_,data=setup(scene)
    class PrivateWorker:
        unrelated='keep me'
        corrected=False
        def stage_resource(self,layer,input,edit):
            if not self.corrected: raise CaptureRefused('Layer calibration changed')
            Worker((data+5).astype(data.dtype)).stage_layer(None,layer,input,edit)
    worker=PrivateWorker()
    attempt=saves.LayerSave(store,saved,'save-1234567890123456',None,'d'*32,input='labels')
    with pytest.raises(saves.SaveNotStarted,match='Private changes are still available'): attempt.capture(worker)
    attempt.close()
    assert worker.unrelated=='keep me' and store.read(saved.id,saved.session)==saved
    worker.corrected=True
    retry=saves.LayerSave(store,saved,'retry-123456789012345',None,'d'*32,input='labels')
    retry.capture(worker);retry.publish()
    accepted=store.read(saved.id,saved.session)
    assert np.array_equal(values(store,accepted,accepted.document['resources'][0]),data+5)
    assert worker.unrelated=='keep me'
    retry.close()
