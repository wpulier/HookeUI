"""Actual prepared reader/napari execution over synthetic sources; not qualification."""
import io,json,os
from pathlib import Path
import numpy as np
import pytest
from gpu_worker import manifests as catalog
from orchestrator import scientific_readers,scientific_project_review
from orchestrator.scientific_projects import ProjectStore
from orchestrator.scientific_file_intake import FileIntake
from orchestrator.scientific_outputs import OutputSave


@pytest.fixture
def reader_runtime(monkeypatch):
    location=os.environ.get('NAPARI_SOURCE_READER_PREPARATION')
    if not location:pytest.skip('requires independently prepared actual source reader; no qualification implied')
    prepared=json.loads(Path(location).read_text())
    for name,value in prepared['environment'].items():monkeypatch.setenv(name,value)
    monkeypatch.delenv('MICROMIND_SANDBOX_READ',raising=False);catalog.manifests.cache_clear()
    try:yield prepared
    finally:catalog.manifests.cache_clear()


def source(store,body,identity='reader-source-012345'):
    owner=FileIntake(store,'s1',identity,name='selected.npy',size=len(body),max_bytes=1<<20)
    try:owner.write(body);return owner.publish()
    finally:owner.close()


def test_actual_declared_reader_native_values_save_and_reopen_without_original_handle(tmp_path,reader_runtime):
    stream=io.BytesIO();values=np.asfortranarray(np.arange(60,dtype='>u2').reshape(3,4,5));np.save(stream,values,allow_pickle=False)
    store=ProjectStore(tmp_path);original=source(store,stream.getvalue())
    key=reader_runtime['key'];generation=Path(reader_runtime['folder']).name
    row=next(row for row in scientific_readers.discover(store,original.id,'s1',1)['readers'] if row['key']==key)
    assert row['canReview'] and 'unproven' in row['scope']
    bound=scientific_readers.bind(store,original.id,'s1',1,key,generation)
    with scientific_project_review.review(store,bound.id,'s1',bound.revision) as (_,worker):
        header,pixels=worker.request({'op':'render','width':320,'height':240});assert pixels
        result,_=worker.request({'op':'python-resources','source':"import numpy as np\nassert viewer.layers[0].data.dtype.str=='>u2'\nassert np.array_equal(viewer.layers[0].data,np.arange(60).reshape(3,4,5))"})
        assert not result['console']['error'],result['console']['error']
        inventory,_=worker.request({'op':'layer-inventory'});layer=inventory['inventory']['layers'][0]
        save=OutputSave(store,bound,'reader-output-01234',layer['id'],{'semantic':'intensity','axes':['z','y','x']})
        save.capture(worker);saved=save.publish();target=save.target;save.close()
    with scientific_project_review.review(store,target,'s1',1) as (_,worker):
        result,_=worker.request({'op':'python-resources','source':"import numpy as np\nassert not files\nassert viewer.layers[0].data.dtype.str=='>u2'\nassert np.array_equal(viewer.layers[0].data,np.arange(60).reshape(3,4,5))"})
        assert not result['console']['error'],result['console']['error']
    assert store.read(original.id,'s1',revision=1)==original
    assert store.file_source(original.id,'s1',1,original.document['resources'][0]).read(0,len(stream.getvalue()))==stream.getvalue()


def test_actual_bad_source_refuses_and_corrected_source_runs_without_disturbing_saved_work(tmp_path,reader_runtime):
    store=ProjectStore(tmp_path);bad=source(store,b'not a numpy array')
    key=reader_runtime['key'];generation=Path(reader_runtime['folder']).name
    bound=scientific_readers.bind(store,bad.id,'s1',1,key,generation)
    with pytest.raises(scientific_project_review.scientific_host.Failed):
        with scientific_project_review.review(store,bound.id,'s1',1):pytest.fail('malformed source was admitted')
    assert store.read(bad.id,'s1',revision=1)==bad
    body=io.BytesIO();np.save(body,np.ones((4,5),dtype='<u2'),allow_pickle=False)
    good=source(store,body.getvalue(),'reader-corrected-012345')
    accepted=scientific_readers.bind(store,good.id,'s1',1,key,generation)
    with scientific_project_review.review(store,accepted.id,'s1',1) as (_,worker):
        frame,pixels=worker.request({'op':'render','width':160,'height':120});assert pixels
    assert store.read(bad.id,'s1',revision=1)==bad
