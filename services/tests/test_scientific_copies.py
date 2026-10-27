"""Real native storage, synthetic edits; no clean-machine or UI qualification."""
import copy

import numpy as np
import pytest

from orchestrator.scientific_edits import ViewEdits
from orchestrator.scientific_projects import Conflict, InvalidProject
from tests.test_scientific_edits import scene, display, values


def prepared(scene):
    store, saved, ref, data = scene
    document = saved.document
    document['state']['scientificView'] = {'runtime': {}, 'inputs': {'labels': ref, 'alias': ref}}
    document['state']['history'] = {'old': copy.deepcopy(ref)}
    saved = store.save(saved.id, saved.session, document, expected_revision=saved.revision)
    owner = ViewEdits(store, saved.id, saved.session, saved.revision, ref)
    edit = owner.begin(display(owner), max_written_bytes=12)
    changed = data.copy(); changed[:2,:3] = 1234
    edit.write([0,0], changed[:2,:3].copy())
    return store, saved, ref, data, owner, edit, changed


def test_copy_of_conflicted_snapshot_is_complete_and_keeps_original_history(scene):
    store, saved, ref, data, owner, edit, changed = prepared(scene)
    winner = store.save(saved.id, saved.session, {**saved.document, 'title':'Concurrent winner'}, expected_revision=saved.revision)
    with pytest.raises(Conflict): edit.commit()
    candidate = store.copy_candidate(saved, edit.prepared())
    assert [p.id for p in store.list(saved.session)] == [saved.id]
    copied = candidate.publish()
    assert copied.revision == 1 and copied.id != saved.id
    assert candidate.publish() == copied
    target = copied.document['resources'][0]
    assert target['id'] == copied.id and target['session'] == saved.session
    assert copied.document['state']['scientificView']['inputs'] == {'labels': target, 'alias': target}
    assert copied.document['state']['history']['old'] == ref
    assert np.array_equal(values(store,copied,target),changed)
    assert np.array_equal(values(store,saved,ref),data)
    assert store.read(saved.id,saved.session) == winner
    candidate.close(); owner.close()
    assert store.read(copied.id,copied.session) == copied


def test_snapshot_after_source_advanced_cannot_overwrite_source(scene):
    store, saved, ref, data, owner, edit, changed = prepared(scene)
    edit.cancel()
    winner = store.save(saved.id,saved.session,{**saved.document,'title':'Winner'},expected_revision=saved.revision)
    snapshot = owner.snapshot(display(owner), max_written_bytes=12)
    snapshot.write([0,0], changed[:2,:3].copy())
    with pytest.raises(InvalidProject, match='separate document'): snapshot.commit()
    frozen = snapshot.prepared()
    candidate = store.copy_candidate(saved, frozen)
    copied = candidate.publish()
    assert store.read(saved.id,saved.session) == winner
    assert np.array_equal(values(store,copied,copied.document['resources'][0]),changed)
    candidate.close(); owner.close()


@pytest.mark.parametrize('after', [False, True])
def test_rename_failure_retries_one_exact_copy(scene, monkeypatch, after):
    from orchestrator import scientific_copies as copies
    store, saved, ref, data, owner, edit, changed = prepared(scene)
    candidate = store.copy_candidate(saved, edit.prepared())
    original = copies.os.rename
    def fail(*args, **kwargs):
        if after: original(*args, **kwargs)
        raise OSError('controlled rename acknowledgement failure')
    with monkeypatch.context() as patch:
        patch.setattr(copies.os, 'rename', fail)
        with pytest.raises(OSError): candidate.publish()
    accepted = candidate.publish()
    assert len(store.list(saved.session)) == 2
    assert np.array_equal(values(store,accepted,accepted.document['resources'][0]),changed)
    assert candidate.publish() == accepted
    candidate.close(); owner.close()


@pytest.mark.parametrize('failure', ['cancel', 'disk', 'corrupt', 'collision'])
def test_failed_copy_publishes_nothing_and_preserves_source(scene, monkeypatch, failure):
    from types import SimpleNamespace
    from orchestrator import scientific_copies as copies
    store, saved, ref, data, owner, edit, changed = prepared(scene)
    frozen = edit.prepared()
    candidate = store.copy_candidate(saved, frozen)
    if failure == 'disk': monkeypatch.setattr(copies.shutil, 'disk_usage', lambda _: SimpleNamespace(free=0))
    if failure == 'corrupt':
        record = copies.arrays._manifest(store,saved.id,frozen.reference)
        path = store.root / saved.id / 'arrays' / (record['parts']['0,0'] + '.bin')
        path.chmod(0o600); path.write_bytes(b'bad')
    if failure == 'collision':
        store.create(saved.session, {'kind':'note','title':'Other work','state':{},'resources':[],'runtimes':[]}, identity=candidate.identity)
    before = store.list(saved.session)
    with pytest.raises((InvalidProject,OSError)):
        candidate.publish(lambda: failure == 'cancel')
    assert store.list(saved.session) == before
    assert store.read(saved.id,saved.session) == saved
    assert np.array_equal(values(store,saved,ref),data)
    candidate.close(); owner.close()


def test_second_component_rehomes_without_changing_opaque_history(scene):
    store, saved, ref, data, owner, edit, changed = prepared(scene)
    edit.cancel(); owner.close()
    writer = store.array_draft(saved.id,saved.session,component='array.raw',shape=data.shape,
        dtype=data.dtype.str,axes=['y','x'],chunks=list(data.shape),scientific={'semantic':'intensity'},max_written_bytes=data.nbytes)
    writer.write([0,0],data); second = writer.finish()
    document = saved.document; document['resources'].append(second)
    document['state']['scientificView']['inputs']['raw'] = second
    saved = store.save(saved.id,saved.session,document,expected_revision=saved.revision)
    owner = ViewEdits(store,saved.id,saved.session,saved.revision,ref)
    edit = owner.snapshot(display(owner),max_written_bytes=12); edit.write([0,0],changed[:2,:3].copy())
    candidate = store.copy_candidate(saved,edit.prepared()); copied = candidate.publish()
    resources = copied.document['resources']
    assert len(resources) == 2 and all(r['id'] == copied.id for r in resources)
    assert copied.document['state']['scientificView']['inputs']['raw'] == resources[1]
    assert np.array_equal(values(store,copied,resources[1]),data)
    assert copied.document['state']['history']['old'] == ref
    candidate.close(); owner.close()


def test_cancellation_after_chunk_copy_leaves_no_public_skeleton(scene):
    store, saved, ref, data, owner, edit, changed = prepared(scene)
    candidate = store.copy_candidate(saved,edit.prepared())
    calls = []
    def stopping():
        calls.append(1)
        assert len(store.list(saved.session)) == 1
        return len(calls) >= 4
    with pytest.raises(InvalidProject,match='stopped'): candidate.publish(stopping)
    assert len(store.list(saved.session)) == 1
    candidate.close(); owner.close()
    assert not list(store.root.glob('.scientific-copy-*'))


def test_rename_is_inside_real_runtime_retention_lock_without_staging_registration(scene, monkeypatch):
    import fcntl
    from orchestrator import scientific_copies as copies, scientific_retention as retention
    store,saved,ref,data,owner,edit,changed=prepared(scene)
    runtime=store.root/'runtime'; store.runtime_root=runtime; store.register_runtime()
    candidate=store.copy_candidate(saved,edit.prepared())
    original=copies.os.rename
    def checked(source,target):
        with (runtime/retention.METADATA/'.lock').open('rb') as lock:
            with pytest.raises(BlockingIOError): fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert len(list((runtime/retention.METADATA).glob('*.json'))) == 1
        assert [p.id for p in store.list(saved.session)] == [saved.id]
        original(source,target)
    monkeypatch.setattr(copies.os,'rename',checked)
    copied=candidate.publish()
    assert copied.id != saved.id
    candidate.close();owner.close()


def test_copy_of_copy_preserves_complete_lineage_and_all_prior_data(scene):
    store,saved,ref,data,owner,edit,changed=prepared(scene)
    first=store.copy_candidate(saved,edit.prepared()); copied=first.publish(); first.close(); owner.close()
    prior=copied.document['state']['hookeCopy']
    ref=copied.document['resources'][0]
    owner=ViewEdits(store,copied.id,copied.session,copied.revision,ref)
    edit=owner.snapshot(display(owner),max_written_bytes=12)
    newest=changed.copy();newest[2:,3:]=2345
    edit.write([1,1],newest[2:,3:].copy())
    second=store.copy_candidate(copied,edit.prepared()); result=second.publish()
    history=result.document['state']['hookeCopy']
    assert history['format']==2 and history['history'][0]==prior
    assert len(history['history'])==2 and history['history'][1]['source']['digest']==copied.digest
    assert np.array_equal(values(store,result,result.document['resources'][0]),newest)
    assert np.array_equal(values(store,copied,ref),changed)
    assert np.array_equal(values(store,saved,saved.document['resources'][0]),data)
    assert copied.document['state']['hookeCopy']==prior
    second.close();owner.close()


@pytest.mark.parametrize('damage',['null','unknown','wrong-target','bad-digest','extra-field','cycle'])
def test_unknown_or_changed_lineage_is_not_overwritten(scene,damage):
    store,saved,ref,data,owner,edit,changed=prepared(scene)
    candidate=store.copy_candidate(saved,edit.prepared());copied=candidate.publish();candidate.close();owner.close()
    document=copied.document; lineage=document['state']['hookeCopy']
    if damage=='null':document['state']['hookeCopy']=None
    elif damage=='unknown':lineage['format']=99
    elif damage=='wrong-target':lineage['target']['id']=saved.id
    elif damage=='bad-digest':lineage['snapshot']['revision']='named-model-version'
    elif damage=='extra-field':lineage['extra']='integration state'
    elif damage=='cycle':
        again=copy.deepcopy(lineage);again['source']['id']=lineage['target']['id'];again['target']['id']=saved.id
        again['original']['id']=again['snapshot']['id']=again['source']['id']
        document['state']['hookeCopy']={'format':2,'history':[lineage,again]}
    copied=store.save(copied.id,copied.session,document,expected_revision=copied.revision)
    owner=ViewEdits(store,copied.id,copied.session,copied.revision,copied.document['resources'][0])
    snapshot=owner.snapshot(display(owner),max_written_bytes=0)
    frozen=snapshot.prepared();before=store.list(copied.session)
    with pytest.raises(InvalidProject,match='lineage'):store.copy_candidate(copied,frozen)
    assert store.list(copied.session)==before and store.read(copied.id,copied.session)==copied
    owner.close()


def test_third_copy_extends_version_two_history_without_nested_rewriting(scene):
    store,saved,ref,data,owner,edit,changed=prepared(scene)
    snapshots=[]
    for _ in range(3):
        candidate=store.copy_candidate(saved,edit.prepared());saved=candidate.publish();candidate.close();owner.close()
        snapshots.append(saved)
        owner=ViewEdits(store,saved.id,saved.session,saved.revision,saved.document['resources'][0])
        edit=owner.snapshot(display(owner),max_written_bytes=0)
    history=saved.document['state']['hookeCopy']['history']
    assert len(history)==3
    assert history[:2]==snapshots[1].document['state']['hookeCopy']['history']
    assert history[0]==snapshots[0].document['state']['hookeCopy']
    assert all(np.array_equal(values(store,item,item.document['resources'][0]),changed) for item in snapshots)
    owner.close()


def test_copy_history_accepts_thirty_two_then_refuses_without_publication(scene):
    from orchestrator.scientific_copies import MAX_COPY_HISTORY
    store,saved,ref,data,owner,edit,changed=prepared(scene)
    for index in range(MAX_COPY_HISTORY):
        candidate=store.copy_candidate(saved,edit.prepared())
        copied=candidate.publish();candidate.close();owner.close()
        lineage=copied.document['state']['hookeCopy']
        history=[lineage] if index==0 else lineage['history']
        assert len(history)==index+1
        assert np.array_equal(values(store,copied,copied.document['resources'][0]),changed)
        saved=copied
        owner=ViewEdits(store,saved.id,saved.session,saved.revision,saved.document['resources'][0])
        edit=owner.snapshot(display(owner),max_written_bytes=0)
    frozen=edit.prepared();before=store.list(saved.session)
    with pytest.raises(InvalidProject,match='full copy lineage'):
        store.copy_candidate(saved,frozen)
    assert store.list(saved.session)==before
    assert store.read(saved.id,saved.session)==saved
    owner.close()


def test_copy_preserves_selected_source_bytes_and_rehomes_reader_bindings(scene):
    from tests.test_scientific_files import stage
    store,saved,ref,data,owner,edit,changed=prepared(scene)
    edit.cancel();owner.close()
    writer,file_ref=stage(store,saved,b'original instrument bytes')
    document=saved.document
    document['resources'].append(file_ref)
    document['state']['scientificView']['files']={'instrument':file_ref}
    document['state']['history']['source']=file_ref
    saved=store.save(saved.id,saved.session,document,expected_revision=saved.revision);writer.close()
    owner=ViewEdits(store,saved.id,saved.session,saved.revision,ref)
    edit=owner.begin(display(owner),max_written_bytes=12);edit.write([0,0],changed[:2,:3].copy())
    candidate=store.copy_candidate(saved,edit.prepared());copied=candidate.publish()
    target=copied.document['state']['scientificView']['files']['instrument']
    assert target['id']==copied.id and target['revision']==file_ref['revision']
    assert target in copied.document['resources']
    assert copied.document['state']['history']['source']==file_ref
    assert store.file_source(copied.id,copied.session,copied.revision,target).read(0,25)==b'original instrument bytes'
    assert store.file_source(saved.id,saved.session,saved.revision,file_ref).read(0,25)==b'original instrument bytes'
    assert np.array_equal(values(store,copied,copied.document['resources'][0]),changed)
    assert store.read(saved.id,saved.session)==saved
    candidate.close();owner.close()


@pytest.mark.parametrize('mode', ['tool', 'initializeTool'])
def test_copy_retained_scene_preserves_controls_and_rehomes_exact_inputs(scene, mode):
    from orchestrator.scientific_copies import PreparedArray
    from orchestrator.scientific_outputs import OutputSave
    from orchestrator.scientific_project_review import _resolve
    from tests.test_scientific_outputs import attempt
    from tests.test_scientific_tool_save import ToolWorker
    store, base, ordinary, _ = attempt(scene)
    worker = ToolWorker()
    output = OutputSave(store, base, ordinary.identity, ordinary.layer, ordinary.meaning, tool=True)
    try:
        output.capture(worker); output.publish()
        saved = store.read(output.target, base.session)
        if mode == 'initializeTool':
            body = saved.document
            body['state']['scientificView'].pop('tool')
            body['state']['scientificView']['initializeTool'] = 1
            saved = store.save(saved.id, saved.session, body, expected_revision=saved.revision)
        view = saved.document['state']['scientificView']
        ref = view['inputs']['output']
        original = store.array_source(saved.id, saved.session, saved.revision, ref)
        # An unchanged accepted resource is enough to fork the whole scene. No
        # callback runs, and opaque codec state retains its stable layer names.
        prepared = PreparedArray(store, saved.id, saved.session, saved.revision, ref, ref)
        candidate = store.copy_candidate(saved, prepared)
        try:
            copied = candidate.publish()
            reopened, _, sources = _resolve(store, copied.id, copied.session, copied.revision)
            assert reopened == copied
            target_view = copied.document['state']['scientificView']
            assert target_view[mode] == view[mode]
            assert target_view['layers'] == view['layers']
            assert target_view['inputs']['output']['id'] == copied.id != saved.id
            region = tuple(slice(0, n) for n in original.shape)
            assert np.array_equal(sources['output'].read(region), original.read(region))
            assert store.read(saved.id, saved.session) == saved
            assert candidate.publish() == copied
        finally:
            candidate.close()
    finally:
        output.close(); ordinary.close()
