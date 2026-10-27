"""Immutable selected-source storage, not reader/runtime qualification."""
import hashlib
import pytest
from orchestrator.scientific_projects import ProjectStore,InvalidProject
from tests.test_scientific_arrays import project,accept


def stage(store,owner,data=b'scientific source bytes',**kw):
    draft=store.file_draft(owner.id,owner.session,component='file.source',name='specimen.ome.tif',
        size=len(data),sha256=hashlib.sha256(data).hexdigest(),max_written_bytes=len(data),**kw)
    for i in range(0,len(data),7):draft.write(data[i:i+7])
    return draft,draft.finish()


@pytest.mark.parametrize("verify_content", [True, False])
def test_selected_file_requires_acceptance_and_reopens_exactly(tmp_path, verify_content):
    store=ProjectStore(tmp_path);owner=project(store);body=b'\x00\xffnative\x01data'*101
    draft,ref=stage(store,owner,body)
    with pytest.raises(InvalidProject,match='accepted'):store.file_source(owner.id,owner.session,1,ref,verify_content=verify_content)
    accepted=accept(store,owner,ref);draft.close()
    source=ProjectStore(tmp_path).file_source(owner.id,owner.session,accepted.revision,ref,verify_content=verify_content)
    assert source.name=='specimen.ome.tif' and source.size==len(body)
    assert source.sha256==hashlib.sha256(body).hexdigest()
    assert source.read(3,40)==body[3:43]
    assert store.read(owner.id,owner.session,revision=1)==owner
    assert source.reference==ref
    source.reference['revision']='0'*64
    assert source.reference==ref


def test_partial_bad_hash_and_corrected_retry_preserve_accepted_work(tmp_path):
    store=ProjectStore(tmp_path);owner=project(store);d,ref=stage(store,owner)
    accepted=accept(store,owner,ref);d.close()
    d=store.file_draft(owner.id,owner.session,component='file.other',name='other.npy',size=4,
        sha256=hashlib.sha256(b'abcd').hexdigest(),max_written_bytes=4)
    d.write(b'ab')
    with pytest.raises(InvalidProject,match='incomplete'):d.finish()
    d.write(b'xx')
    with pytest.raises(InvalidProject,match='hash'):d.finish()
    d.close()
    assert store.read(owner.id,owner.session)==accepted
    d,ref2=stage(store,owner,b'abcd');doc=accepted.document;doc['resources'].append(ref2)
    new=store.save(owner.id,owner.session,doc,expected_revision=accepted.revision)
    assert store.file_source(new.id,new.session,new.revision,ref2).read(0,4)==b'abcd'
    assert store.file_source(accepted.id,accepted.session,accepted.revision,ref).read(0,4)==b'scie'


def test_chunk_crossings_are_bounded_and_exact(tmp_path,monkeypatch):
    import orchestrator.scientific_files as files
    monkeypatch.setattr(files,'CHUNK_BYTES',16)
    store=ProjectStore(tmp_path);owner=project(store);body=bytes(range(100))
    d,ref=stage(store,owner,body);accepted=accept(store,owner,ref)
    source=store.file_source(owner.id,owner.session,accepted.revision,ref)
    assert source.read(13,16)==body[13:29]
    with pytest.raises(InvalidProject,match='range'):source.read(0,17)
    with pytest.raises(InvalidProject,match='range'):source.read(-1,3)
    with pytest.raises(InvalidProject,match='range'):source.read(99,2)
    assert source.read(100,0)==b''
    d.close()


@pytest.mark.parametrize('name',['../private','a/b','a\\b','', 'a\x00b','.','..'])
def test_source_name_cannot_be_a_path(tmp_path,name):
    store=ProjectStore(tmp_path);owner=project(store)
    with pytest.raises(InvalidProject):store.file_draft(owner.id,owner.session,component='file.source',name=name,
        size=0,sha256=hashlib.sha256(b'').hexdigest(),max_written_bytes=0)


def test_empty_file_is_retained_but_not_declared_runnable(tmp_path):
    store=ProjectStore(tmp_path);owner=project(store);d,ref=stage(store,owner,b'')
    saved=accept(store,owner,ref);source=store.file_source(owner.id,owner.session,saved.revision,ref)
    assert source.size==0 and source.read(0,0)==b''
    with pytest.raises(InvalidProject):source.read(0,1)
    assert saved.document['runtimes']==[]
    d.close()


def test_limits_and_cancel_do_not_accept_partial_data(tmp_path):
    store=ProjectStore(tmp_path);owner=project(store)
    with pytest.raises(InvalidProject):store.file_draft(owner.id,owner.session,component='file.source',name='x',
        size=10,sha256='0'*64,max_written_bytes=9)
    d=store.file_draft(owner.id,owner.session,component='file.source',name='x',size=4,sha256='0'*64,max_written_bytes=4)
    with pytest.raises(InvalidProject):d.write(b'12345')
    d.close()
    with pytest.raises(InvalidProject):d.write(b'1234')
    assert store.read(owner.id,owner.session)==owner


def test_reference_ownership_and_corruption_refuse_without_changing_head(tmp_path):
    store=ProjectStore(tmp_path);owner=project(store);d,ref=stage(store,owner);accepted=accept(store,owner,ref)
    other=project(store)
    with pytest.raises(InvalidProject):accept(store,other,ref)
    with pytest.raises(InvalidProject):store.file_source(owner.id,'other-session',accepted.revision,ref)
    folder=store._folder(owner.id)/'files'
    chunk=next(folder.glob('*.bin'));chunk.chmod(0o600);chunk.write_bytes(b'corrupt')
    with pytest.raises(InvalidProject):store.file_source(owner.id,owner.session,accepted.revision,ref)
    with pytest.raises(InvalidProject):store.save(owner.id,owner.session,accepted.document,expected_revision=accepted.revision)
    assert store.read(owner.id,owner.session)==accepted
    d.close()


def test_symlinked_storage_and_chunk_are_never_followed(tmp_path):
    store=ProjectStore(tmp_path/'projects');owner=project(store);outside=tmp_path/'outside';outside.mkdir()
    folder=store._folder(owner.id)/'files';folder.symlink_to(outside,target_is_directory=True)
    with pytest.raises((OSError,InvalidProject)):stage(store,owner)
    assert list(outside.iterdir())==[]
    folder.unlink();d,ref=stage(store,owner);accepted=accept(store,owner,ref)
    chunk=next(folder.glob('*.bin'));body=chunk.read_bytes();chunk.unlink();target=outside/'secret';target.write_bytes(body);chunk.symlink_to(target)
    with pytest.raises((OSError,InvalidProject)):store.file_source(owner.id,owner.session,accepted.revision,ref)
    assert target.read_bytes()==body and store.read(owner.id,owner.session)==accepted
    d.close()


def test_file_source_detects_changed_chunk_after_grant_was_issued(tmp_path):
    store=ProjectStore(tmp_path);owner=project(store);d,ref=stage(store,owner,b'abcd');saved=accept(store,owner,ref)
    source=store.file_source(saved.id,saved.session,saved.revision,ref)
    chunk=next((store._folder(owner.id)/'files').glob('*.bin'));chunk.chmod(0o600);chunk.write_bytes(b'xxxx')
    with pytest.raises(InvalidProject,match='hash'):source.read(0,4)
    assert store.read(saved.id,saved.session)==saved
    d.close()


def test_failed_manifest_publication_retries_same_completed_bytes(tmp_path,monkeypatch):
    import orchestrator.scientific_files as module
    store=ProjectStore(tmp_path);owner=project(store);data=b'abcd'
    writer=store.file_draft(owner.id,owner.session,component='file.source',name='source.npy',size=4,
        sha256=hashlib.sha256(data).hexdigest(),max_written_bytes=4);writer.write(data)
    put=module._put
    def fail(folder,body,suffix):
        if suffix=='.json':raise OSError('controlled metadata disk failure')
        return put(folder,body,suffix)
    with monkeypatch.context() as patch:
        patch.setattr(module,'_put',fail)
        with pytest.raises(OSError):writer.finish()
    assert store.read(owner.id,owner.session)==owner
    ref=writer.finish();assert writer.finish()==ref
    saved=accept(store,owner,ref)
    assert store.file_source(saved.id,saved.session,saved.revision,ref).read(0,4)==data
    writer.close()


def test_manifest_serializer_covers_the_declared_chunk_count_without_huge_file():
    from orchestrator import scientific_files as files
    import json
    meta={'format':files.FORMAT,'name':'large.bin','size':files.MAX_CHUNKS*files.CHUNK_BYTES,
          'sha256':'b'*64,'chunkBytes':files.CHUNK_BYTES,'chunks':['a'*64]*files.MAX_CHUNKS}
    encoded=files._encode(meta)
    assert 2<<20<len(encoded)<=files.MAX_MANIFEST
    assert json.loads(encoded)==meta


def test_source_verification_can_stop_between_chunks_and_retry(tmp_path,monkeypatch):
    from orchestrator import scientific_files as files
    monkeypatch.setattr(files,'CHUNK_BYTES',4)
    store=ProjectStore(tmp_path);owner=project(store);body=b'abcdefghijkl'
    writer=store.file_draft(owner.id,owner.session,component='file.source',name='bytes.bin',size=12,
        sha256=None,max_written_bytes=12)
    for i in range(0,12,4):writer.write(body[i:i+4])
    ref=writer.finish();saved=accept(store,owner,ref);writer.close()
    reads=[];read=files._read
    def observed(folder,name,limit):
        result=read(folder,name,limit)
        if name.endswith('.bin'):reads.append(name)
        return result
    def check():
        if reads:raise InvalidProject('controlled Stop during verification')
    with monkeypatch.context() as patch:
        patch.setattr(files,'_read',observed)
        with pytest.raises(InvalidProject,match='controlled Stop'):
            store.file_source(saved.id,saved.session,saved.revision,ref,check=check)
    assert len(reads)==1 and store.read(saved.id,saved.session)==saved
    assert store.file_source(saved.id,saved.session,saved.revision,ref).read(0,4)==b'abcd'


def test_metadata_save_verifies_source_outside_the_publication_lock(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from orchestrator import scientific_files as files
    store=ProjectStore(tmp_path);owner=project(store);draft,ref=stage(store,owner,b'original')
    saved=accept(store,owner,ref);draft.close()
    locked=[];scans=[];lock=store._locked;manifest=files._manifest
    @contextmanager
    def observed_lock(folder):
        with lock(folder):
            locked.append(folder)
            try:yield
            finally:locked.pop()
    def observed_manifest(*args,**kwargs):
        assert not locked
        scans.append(True)
        return manifest(*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(store,'_locked',observed_lock)
        patch.setattr(files,'_manifest',observed_manifest)
        updated=store.save(saved.id,saved.session,{**saved.document,'title':'Renamed source'},expected_revision=saved.revision)
    assert scans==[True] and updated.document['resources']==saved.document['resources']
    assert store.file_source(updated.id,updated.session,updated.revision,ref).read(0,8)==b'original'


def test_copy_bounds_aggregate_source_metadata_before_more_files(monkeypatch):
    from orchestrator import scientific_files as files
    calls=[]
    def manifest(*args,**kwargs):
        calls.append(args[3]);return {'chunks':['a'*64]*10}
    monkeypatch.setattr(files,'_manifest',manifest)
    monkeypatch.setattr(files,'RETAINED_METADATA_BYTES',5700)
    with pytest.raises(InvalidProject,match='copy metadata allowance'):
        files.copy_records(None,'p','s',{'resources':[{'component':'file.one'},{'component':'file.two'},{'component':'file.three'}]})
    assert len(calls)==2
