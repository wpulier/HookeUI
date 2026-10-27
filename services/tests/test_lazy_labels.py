"""Regional Labels components: real storage/transport, synthetic native layer callbacks.

These checks do not qualify an actual napari/UI or larger-than-memory workflow.
"""
from collections import deque
from types import SimpleNamespace
import sys

import numpy as np
import pytest

from env_worker.lazy_arrays import ChunkCache,MaterializationRefused
from env_worker.lazy_labels import LazyLabels,DirtyBudget
from env_worker.scientific_layer_exports import EditableLayers,FreezeBudget
from env_worker.scientific_labels import LabelsEditor,Refused
from env_worker.native_edits import bind as bind_edit,BaseChunk
from orchestrator.scientific_edits import ResourceEdits
from orchestrator.scientific_host import LayerStaging
from orchestrator.scientific_projects import ProjectStore,InvalidProject
from tests.test_scientific_edits import scene,values  # noqa: F401
from tests.test_scientific_arrays import bound
from tests.scientific_threads import exclude_preexisting_test_harness_threads  # noqa: F401


def regional(scene,*,budget=None):
    store,accepted,reference,_=scene
    source=bound(store,accepted,reference)
    return LazyLabels(source,ChunkCache(),budget or DirtyBudget(),ident='labels'),source


def layer_for(data):
    layer=SimpleNamespace(data=data,_type_string='labels',ndim=data.ndim,rgb=False,multiscale=False,
        name='Labels',visible=True,opacity=1.,axis_labels=('y','x'),units=('pixel','pixel'),scale=(1.,1.),
        _slice_input=SimpleNamespace(ndisplay=2,order=(0,1)),_undo_history=deque(maxlen=100),
        _redo_history=deque(maxlen=100),_staged_history=[],_block_history=False,
        brush_size=1,n_edit_dimensions=2,preserve_labels=False,contiguous=True,selected_label=0,
        _prev_selected_label=0,colormap=SimpleNamespace(background_value=0),refresh=lambda:None)
    layer.data_to_world=lambda point:tuple(float(v) for v in point)
    def begin():layer._block_history=True
    def paint(point,value,refresh=False):
        key=tuple(np.array([round(v)],dtype=np.int64) for v in point)
        old=data[key].copy()
        if np.any(old!=value):
            layer._staged_history.append((key,old,value));data[key]=value
    def commit():
        if layer._staged_history:layer._undo_history.append(layer._staged_history)
        layer._staged_history=[];layer._block_history=False;layer._redo_history.clear()
    def replay(undo):
        before,after=(layer._undo_history,layer._redo_history) if undo else (layer._redo_history,layer._undo_history)
        atoms=before.pop();after.append(atoms)
        for key,old,new in reversed(atoms):data[key]=old if undo else new
    def reset():layer._undo_history.clear();layer._redo_history.clear();layer._staged_history.clear()
    layer._begin_stroke=begin;layer.paint=paint;layer._commit_stroke=commit
    layer.undo=lambda:replay(True);layer.redo=lambda:replay(False);layer._reset_history=reset
    return layer


def editor_for(scene,monkeypatch):
    monkeypatch.setitem(sys.modules,'napari.layers.labels._labels_utils',SimpleNamespace(interpolate_coordinates=lambda *args:[]))
    data,source=regional(scene);layer=layer_for(data)
    registry=EditableLayers(SimpleNamespace(layers=[layer]),{'labels':source})
    registry.bind(layer=layer,input='labels');registry.seal()
    return LabelsEditor(layer,registry),registry,data,layer


def test_regional_gesture_undo_redo_save_reopen_uses_exact_base_without_full_reads(scene,monkeypatch):
    store,original,reference,expected=scene
    editor,registry,data,layer=editor_for(scene,monkeypatch)
    blank=data.fingerprint()
    assert editor.apply('paint',points=[[1,4]],label='65535',brush=1,preserve=False,contiguous=True)['changed']
    assert data[1,4]==65535 and data.dirty_bytes==12
    editor.apply('undo');assert data.fingerprint()==blank and data.dirty_bytes==0
    editor.apply('redo');assert data[1,4]==65535
    owner=ResourceEdits(store,original.id,'s1',original.revision,reference,layer='labels')
    edit=owner.begin(max_written_bytes=12);staging=LayerStaging(edit)
    native=bind_edit(staging.specification,staging.answer)
    before=data.requests
    with registry.staged_chunks(layer,'labels',native,base=edit.base_digests(),storage='memory') as chunks:
        sent=list(chunks)
        assert sum(isinstance(item,BaseChunk) for _,item in sent)==3
        for index,item in sent:native.put(index,item)
    assert data.requests==before,'Save must not read untouched source chunks'
    staging.finish({'ok':True,'staged':native._token},b'')
    saved=edit.commit();assert saved.revision==original.revision+1
    expected=expected.copy();expected[1,4]=65535
    reopened=ProjectStore(store.root)
    got=values(reopened,saved,saved.document['resources'][0])
    assert got.dtype.str=='>u2' and got.tobytes()==expected.tobytes()
    assert values(reopened,original,reference)[1,4]!=65535
    again=LazyLabels(bound(reopened,saved,saved.document['resources'][0]),ChunkCache(),DirtyBudget(),ident='reopened')
    assert again[1,4]==65535 and again.dirty_bytes==0


def test_regional_history_refuses_outside_mutation_and_rolls_back_callback_failure(scene,monkeypatch):
    editor,_,data,layer=editor_for(scene,monkeypatch)
    original=data[:].copy();called=[]
    def refresh():
        if not called:
            called.append(True);data[0,0]=99
            raise RuntimeError('controlled callback failure')
    layer.refresh=refresh
    with pytest.raises(RuntimeError,match='callback failure'):
        editor.apply('paint',points=[[1,4]],label='65535',brush=1,preserve=False,contiguous=True)
    assert data[:].tobytes()==original.tobytes() and data.dirty_bytes==0
    assert not layer._undo_history and not editor.undo
    data[0,0]=77
    with pytest.raises(Refused,match='another tool changed'):
        editor.apply('paint',points=[[1,4]],label='65535',brush=1,preserve=False,contiguous=True)


def test_shared_dirty_allowance_and_snapshot_are_atomic(scene):
    budget=DirtyBudget(12)
    first,_=regional(scene,budget=budget);second,_=regional(scene,budget=budget)
    snapshot=first.snapshot();first[0,0]=65000
    assert budget.used==12
    with pytest.raises(MaterializationRefused,match='memory allowance'):second[0,0]=65000
    assert second.dirty_bytes==0
    first.restore(snapshot);assert budget.used==0
    second[0,0]=65000;assert budget.used==12
    with pytest.raises(ValueError,match='original working array'):first.restore(second.snapshot())


def test_retained_dirty_versions_and_mid_assignment_failure_are_distinct_from_current_budget(scene,monkeypatch):
    data,_=regional(scene)
    data[0,0]=65000;before=data.snapshot();old=next(iter(before.chunks.values()))
    data[0,0]=64000;new=next(iter(data._dirty.values()))
    assert old is not new and len(old)+len(new)==2*data.dirty_bytes
    # Current-map accounting does not pretend the held rollback bytes disappeared.
    assert data._budget.used==data.dirty_bytes==12 and np.frombuffer(old,dtype='>u2')[0]==65000
    fingerprint=data.fingerprint();read=data._chunk
    def fail(index):
        if index==(0,1):raise RuntimeError('controlled second-chunk read failure')
        return read(index)
    with monkeypatch.context() as patch:
        patch.setattr(data,'_chunk',fail)
        with pytest.raises(RuntimeError,match='second-chunk'):
            data[0:2,:]=123
    assert data.fingerprint()==fingerprint and data[0,0]==64000
    data.restore(before);assert data[0,0]==65000


def test_regional_basic_and_coordinate_writes_preserve_uint64_ids_and_refuse_whole_conversion():
    # Nontrivial on-demand pixels, not a fixture claiming full-scale qualification.
    class Source:
        shape=(2048,2048);dtype='>u8';chunks=(128,128);reference={'revision':'a'*64}
        def read(self,bounds,*,view):
            assert view
            y,x=bounds
            return (np.arange(*y,dtype=np.uint64)[:,None]*2048+np.arange(*x,dtype=np.uint64)[None,:]+2**60).astype(self.dtype)
    array=LazyLabels(Source(),ChunkCache(),DirtyBudget(),ident='uint64')
    with pytest.raises(MaterializationRefused,match='lazily'):np.asarray(array)
    array[2:4,3:5]=2**63+123
    key=(np.array([2,3,1000]),np.array([3,4,1000]))
    array[key]=np.array([2**63+124,2**63+125,2**63+126],dtype='>u8')
    assert array[key].tolist()==[2**63+124,2**63+125,2**63+126]
    assert array[0,0]==2**60
    with pytest.raises(MaterializationRefused):array[:]=1


def test_exact_base_keep_rejects_forged_token_payload_and_modified_base_chunk(scene):
    store,original,reference,_=scene
    owner=ResourceEdits(store,original.id,'s1',original.revision,reference,layer='labels')
    edit=owner.begin(max_written_bytes=12)
    message={'op':'keep-native-base-chunk','token':edit.identity,'index':[0,0]}
    with pytest.raises(InvalidProject):edit.answer({**message,'token':'forged'})
    with pytest.raises(InvalidProject):edit.answer(message,b'x')
    edit.write([0,0],np.full((2,3),5,dtype='>u2'))
    with pytest.raises(InvalidProject,match='exactly its accepted base'):edit.answer(message)
    assert store.read(original.id,'s1')==original


def test_restore_selects_regional_labels_without_reading_and_binds_normal_editor(scene,monkeypatch):
    from env_worker import scientific_outputs as outputs
    from env_worker.scientific_output_wire import calibration
    store,original,reference,pixels=scene
    # Use the real metadata produced by the existing eager layer; only the size
    # threshold is lowered to exercise production restore with small fixture bytes.
    eager=layer_for(pixels);metadata=outputs.describe(eager)
    writer=store.array_draft(original.id,'s1',component='array.calibrated',shape=pixels.shape,
        dtype=pixels.dtype.str,axes=['y','x'],chunks=[2,3],max_written_bytes=pixels.nbytes,
        scientific={'semantic':'instance-labels','calibration':calibration(metadata,['y','x'],
            {'session':'s1','id':original.id,'revision':original.revision})})
    from tests.test_scientific_arrays import fill,accept
    reference=fill(writer,pixels);original=accept(store,original,reference)
    source=bound(store,original,reference)
    row={'input':'labels','metadata':metadata}
    layers=[]
    def add_labels(data,**kwargs):
        layer=layer_for(data);layers.append(layer);return layer
    viewer=SimpleNamespace(layers=layers,add_labels=add_labels)
    registry=EditableLayers(viewer,{'labels':source})
    monkeypatch.setattr(outputs,'EAGER_BYTES',16)
    monkeypatch.setitem(sys.modules,'magicgui.widgets',SimpleNamespace(Container=lambda **kwargs:None))
    outputs.restore(viewer,{'labels':source},[row],registry,SimpleNamespace(bind=lambda **kwargs:None))
    assert type(layers[0].data) is LazyLabels and layers[0].data.requests==0
    assert registry.input_for(layers[0])=='labels'
    assert layers[0].data[:].tobytes()==pixels.tobytes()


def test_save_snapshot_is_immutable_during_transport_and_wrong_base_refuses(scene):
    store,original,reference,_=scene
    data,source=regional(scene);layer=layer_for(data)
    registry=EditableLayers(SimpleNamespace(layers=[layer]),{'labels':source})
    registry.bind(layer=layer,input='labels')
    data[0,0]=65535
    edit=ResourceEdits(store,original.id,'s1',original.revision,reference,layer='labels').begin(max_written_bytes=24)
    staging=LayerStaging(edit);native=bind_edit(staging.specification,staging.answer)
    with registry.staged_chunks(layer,'labels',native,storage='memory') as chunks:
        data[0,0]=123
        for index,item in chunks:native.put(index,item)
    saved=edit.commit()
    assert values(store,saved,saved.document['resources'][0])[0,0]==65535 and data[0,0]==123
    other=bind_edit({**staging.specification,'reference':{**reference,'revision':'b'*64}},lambda *args:None)
    with pytest.raises(ValueError,match='requested retained input'):
        with registry.staged_chunks(layer,'labels',other):pass
