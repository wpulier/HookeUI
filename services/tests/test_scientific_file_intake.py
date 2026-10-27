"""Actual storage/atomic publication; no reader or fresh-machine qualification."""
import hashlib
import pytest
from orchestrator.scientific_projects import ProjectStore,InvalidProject,Conflict
from orchestrator.scientific_file_intake import FileIntake
from tests.test_scientific_arrays import project


def attempt(store,body=b'selected bytes',request='user-request-012345'):
    owner=FileIntake(store,'s1',request,name='sample.npy',size=len(body),max_bytes=1024)
    for i in range(0,len(body),3):owner.write(body[i:i+3])
    return owner


def test_complete_import_is_one_retained_source_and_duplicate_request_is_idempotent(tmp_path):
    store=ProjectStore(tmp_path);good=project(store);owner=attempt(store)
    assert store.list('s1')==[good]
    saved=owner.publish();owner.close()
    assert saved.revision==1 and saved.document['runtimes']==[]
    source=store.file_source(saved.id,'s1',1,saved.document['resources'][0])
    assert source.read(0,source.size)==b'selected bytes'
    assert source.sha256==hashlib.sha256(b'selected bytes').hexdigest()
    retry=attempt(store);assert retry.publish()==saved;retry.close()
    assert len(store.list('s1'))==2 and store.read(good.id,'s1')==good
    changed=attempt(store,b'different bytes')
    try:
        with pytest.raises(Conflict):changed.publish()
    finally:changed.close()
    assert store.read(saved.id,'s1')==saved
    assert not list(tmp_path.glob('.scientific-import-*'))


@pytest.mark.parametrize('failure',['partial','oversize','stop','disk'])
def test_failed_import_preserves_other_work_and_corrected_retry(tmp_path,monkeypatch,failure):
    from types import SimpleNamespace
    from orchestrator import scientific_file_intake as intake
    store=ProjectStore(tmp_path);good=project(store);owner=None
    with monkeypatch.context() as patch:
        if failure=='disk':patch.setattr(intake.shutil,'disk_usage',lambda _:SimpleNamespace(free=0))
        try:
            with pytest.raises((InvalidProject,OSError)):
                owner=FileIntake(store,'s1','user-request-012345',name='sample.npy',size=4,max_bytes=1024)
                owner.write(b'ab' if failure=='partial' else b'abcde' if failure=='oversize' else b'abcd')
                owner.publish(lambda:failure=='stop')
        finally:
            if owner:owner.close()
    assert store.list('s1')==[good]
    retry=attempt(store,b'abcd');saved=retry.publish();retry.close()
    assert saved.revision==1 and store.read(good.id,'s1')==good


@pytest.mark.parametrize('after',[False,True])
def test_lost_publication_acknowledgement_retries_without_duplicate(tmp_path,monkeypatch,after):
    from orchestrator import scientific_file_intake as intake
    store=ProjectStore(tmp_path);owner=attempt(store);rename=intake.os.rename
    def fail(*args,**kw):
        if after:rename(*args,**kw)
        raise OSError('controlled rename/ack failure')
    with monkeypatch.context() as patch:
        patch.setattr(intake.os,'rename',fail)
        with pytest.raises(OSError):owner.publish()
    saved=owner.publish();owner.close()
    assert store.list('s1')==[saved]


def test_private_metadata_failure_retries_before_final_publication(tmp_path,monkeypatch):
    store=ProjectStore(tmp_path);owner=attempt(store)
    publish=owner.private._publish
    def fail(*args,**kw):raise OSError('controlled private manifest failure')
    with monkeypatch.context() as patch:
        patch.setattr(owner.private,'_publish',fail)
        with pytest.raises(OSError):owner.publish()
    assert store.list('s1')==[]
    saved=owner.publish();owner.close()
    assert saved.document['resources'] and saved.document['state']['sourceFile']


def test_failed_cleanup_remains_owned_and_retries(tmp_path,monkeypatch):
    from orchestrator import scientific_file_intake as intake
    store=ProjectStore(tmp_path);owner=attempt(store)
    def fail(*args,**kw):raise OSError('controlled cleanup failure')
    with monkeypatch.context() as patch:
        patch.setattr(intake.shutil,'rmtree',fail)
        assert not owner.close() and owner in intake._pending
    assert intake.retry_cleanup()==[] and owner not in intake._pending
    assert store.list('s1')==[] and not list(tmp_path.glob('.scientific-import-*'))


def test_stop_during_validation_publishes_nothing_and_retries(tmp_path,monkeypatch):
    from orchestrator import scientific_files as files
    monkeypatch.setattr(files,'CHUNK_BYTES',4)
    store=ProjectStore(tmp_path);good=project(store);owner=attempt(store,b'abcdefghijkl')
    read=files._read;reads=[]
    def observed(folder,name,limit):
        result=read(folder,name,limit)
        if name.endswith('.bin'):reads.append(name)
        return result
    with monkeypatch.context() as patch:
        patch.setattr(files,'_read',observed)
        with pytest.raises(InvalidProject,match='stopped'):owner.publish(lambda:bool(reads))
    assert len(reads)==1 and store.list('s1')==[good]
    saved=owner.publish();owner.close()
    assert saved.document['resources'] and len(store.list('s1'))==2


@pytest.mark.parametrize('field,value',[('name','different.npy'),('size',99),('sha256','a'*64),('format',True),('reference',{})])
def test_source_identity_cannot_disagree_with_retained_bytes(tmp_path,field,value):
    store=ProjectStore(tmp_path);owner=attempt(store);saved=owner.publish();owner.close()
    document=saved.document;document['state']['sourceFile'][field]=value
    with pytest.raises(InvalidProject):store.save(saved.id,'s1',document,expected_revision=1)
    assert store.read(saved.id,'s1')==saved
    assert store.file_source(saved.id,'s1',1,saved.document['resources'][0]).read(0,14)==b'selected bytes'
