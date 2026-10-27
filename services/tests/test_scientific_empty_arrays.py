"""Empty scientific outcomes retain a schema and identity, never missing chunks."""
import numpy as np
import pytest
from env_worker import native_regions as wire
from orchestrator.native_regions import NativeRegionInputs
from orchestrator.scientific_projects import ProjectStore,InvalidProject
from tests.test_scientific_arrays import project,accept
from tests.test_native_regions import source

@pytest.mark.parametrize('shape',[(0,3),(2,0,4),(0,)])
def test_empty_native_source_roundtrip_retains_dimensions_and_dtype(shape):
    data=np.empty(shape,dtype='>u2');reads=[];axes=tuple('abc'[:len(shape)])
    host=NativeRegionInputs({'empty':source(data,reads,axes=axes)})
    resource=wire.bind(host.specifications(),host.answer)['empty']
    actual=resource.read([[0,n] for n in shape])
    assert actual.shape==shape and actual.dtype.str=='>u2' and actual.nbytes==0
    assert len(reads)==1
    host.close()
    with pytest.raises(ValueError,match='closed'):resource.read([[0,n] for n in shape])


def test_empty_component_acceptance_reopens_with_no_fabricated_chunk(tmp_path):
    store=ProjectStore(tmp_path);original=project(store)
    task=store.array_draft(original.id,'s1',component='array.points',shape=[0,3],dtype='>f8',axes=['point','coordinate'],chunks=[1,3],
        scientific={'semantic':'point-set'},max_written_bytes=0)
    try:
        with pytest.raises(ValueError):task.write((0,0),np.zeros((1,3),dtype='>f8'))
        reference=task.finish();saved=accept(store,original,reference)
    finally:task.close()
    reopened=ProjectStore(tmp_path).array_source(saved.id,'s1',saved.revision,reference)
    result=reopened.read((slice(0,0),slice(0,3)))
    assert result.shape==(0,3) and result.dtype.str=='>f8' and not result.size
    assert not list((store._folder(saved.id)/'arrays').glob('*.bin'))
    assert store.read(original.id,'s1',revision=original.revision)==original


@pytest.mark.parametrize('shape',[[-1,3],[True,3],[0,2**53],[0,0.5]])
def test_empty_dimensions_do_not_bypass_shape_validation(shape):
    with pytest.raises(ValueError):wire.layout(shape,'<f8',['row','coordinate'])


def test_empty_result_does_not_make_an_incomplete_nonempty_component_publishable(tmp_path):
    store=ProjectStore(tmp_path);original=project(store)
    task=store.array_draft(original.id,'s1',component='array.points',shape=[1,3],dtype='<f8',axes=['point','coordinate'],chunks=[1,3],scientific={'semantic':'point-set'},max_written_bytes=24)
    try:
        with pytest.raises(InvalidProject,match='missing'):task.finish()
    finally:task.close()
    assert store.read(original.id,'s1')==original


def test_zero_extent_does_not_bypass_chunk_index_or_numpy_stride_limits(tmp_path):
    from env_worker.native_edits import layout
    with pytest.raises(ValueError):wire.layout([0,2**40,2**40],'<f8',['a','b','c'])
    with pytest.raises(ValueError):layout([0,65537],'<f8',['a','b'],[1,1])
    store=ProjectStore(tmp_path);owner=project(store)
    with pytest.raises(InvalidProject):store.array_draft(owner.id,'s1',component='array.empty',shape=[0,65537],dtype='<f8',axes=['a','b'],chunks=[1,1],scientific={},max_written_bytes=0)

def test_empty_slice_reads_no_unselected_chunks(tmp_path,monkeypatch):
    import orchestrator.scientific_arrays as arrays
    from tests.test_scientific_arrays import fill
    store=ProjectStore(tmp_path);owner=project(store);data=np.arange(12,dtype='<u2').reshape(3,4)
    draft=store.array_draft(owner.id,'s1',component='array.values',shape=[3,4],dtype='<u2',axes=['y','x'],chunks=[3,4],scientific={'semantic':'intensity'},max_written_bytes=24)
    ref=fill(draft,data);saved=accept(store,owner,ref);source=store.array_source(saved.id,'s1',saved.revision,ref)
    monkeypatch.setattr(arrays,'read_at',lambda *a:pytest.fail('empty selection must not read pixel chunks'))
    assert source.read((slice(1,1),slice(0,4))).shape==(0,4)


def test_empty_image_is_not_silently_admitted_as_a_viewable_result():
    from env_worker.scientific_output_wire import metadata
    with pytest.raises(ValueError,match='supported data'):
        metadata({'format':'native-layer-output-v1','type':'image','name':'Empty image','shape':[0,3],'dtype':'<f8','visible':True,'opacity':1.,'geometry':{}})

def test_empty_range_is_bounded_and_revocable_not_a_free_unlimited_request(monkeypatch):
    data=np.empty((0,3),dtype='<f8');reads=[]
    host=NativeRegionInputs({'empty':source(data,reads,axes=['row','coordinate'])})
    resource=wire.bind(host.specifications(),host.answer)['empty']
    monkeypatch.setattr(wire,'MAX_REQUESTS',2)
    for _ in range(2):assert resource.read([[0,0],[0,3]]).shape==(0,3)
    with pytest.raises(ValueError,match='allowance'):resource.read([[0,0],[0,3]])
    assert len(reads)==2
