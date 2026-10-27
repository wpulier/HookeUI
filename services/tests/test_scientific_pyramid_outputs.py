"""Small synthetic pyramids through real capture/publication/readback; no GUI qualification."""
import sys
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from env_worker import scientific_outputs as worker_outputs, scientific_output_wire as wire
from orchestrator.scientific_outputs import OutputSave
from tests.test_scientific_outputs import attempt
from tests.test_scientific_edits import scene, values
from tests.scientific_threads import exclude_preexisting_test_harness_threads  # noqa: F401


def pyramid(scene, monkeypatch, *, lazy=False):
    from env_worker.scientific_host import ReviewSession
    from env_worker.scientific_view_bindings import ViewBindings
    from orchestrator.native_regions import NativeRegionInputs
    from env_worker.native_regions import bind
    from env_worker.lazy_arrays import LazyNative, ChunkCache
    store,saved,_,_ = attempt(scene)
    expected = [(768+np.arange(24,dtype='>u2')).astype('>u2').reshape(4,6),
                (100+np.arange(6,dtype='>u2')).astype('>u2').reshape(2,3)]
    data = expected
    inputs = None
    if lazy:
        sources = {}
        for i,array in enumerate(expected):
            draft = store.array_draft(saved.id,saved.session,component=f'array.level{i}',shape=array.shape,
                dtype=array.dtype.str,axes=['y','x'],chunks=array.shape,scientific={'semantic':'intensity'},max_written_bytes=array.nbytes)
            draft.write([0,0],array); ref = draft.finish()
            doc = saved.document; doc['resources'].append(ref)
            saved = store.save(saved.id,saved.session,doc,expected_revision=saved.revision)
            sources[f'level{i}'] = store.array_source(saved.id,saved.session,saved.revision,ref)
        inputs = NativeRegionInputs(sources)
        native = bind(inputs.specifications(),inputs.answer)
        cache = ChunkCache()
        data = [LazyNative(native[f'level{i}'],cache,ident=f'level{i}') for i in range(2)]
        monkeypatch.setattr(LazyNative,'__array__',lambda *a,**kw:pytest.fail('Whole lazy level materialized'))
    layer = SimpleNamespace(data=data,_type_string='image',ndim=2,rgb=False,multiscale=True,name='Pyramid',
        visible=True,opacity=.8,axis_labels=('depth','width'),units=('micrometer','micrometer'),
        data_to_world=lambda p:(4+2*p[0],9+3*p[1]))
    viewer = SimpleNamespace(layers=[layer])
    host = object.__new__(ReviewSession);host._thread=threading.get_ident();host._closed=False;host._frame='old'
    host.viewer=viewer;host._layers=[('a'*32,layer)]
    host.editable=SimpleNamespace(input_for=lambda layer:None);host._layer_rows=lambda:[]
    host.view_bindings=ViewBindings(viewer);host.view_bindings.seal()
    save = OutputSave(store,saved,'pyramid-save-request','a'*32,{'semantic':'intensity','axes':['y','x']})
    class Worker:
        callback = None
        def describe_output(self, key):return host.describe_output(key)
        def stage_outputs(self, rows):
            from orchestrator import scientific_host as parent
            parent_worker=object.__new__(parent.ReviewWorker)
            parent_worker.work=None
            parent_worker.grants=inputs
            parent_worker.files=None
            parent_worker._allowed=lambda:None
            parent_worker.close=lambda:None
            monkeypatch.setattr(parent,'snapshot_storage',lambda *args:'memory')
            def exchange(request,staging):
                def write(header,payload):
                    result=staging.answer(header,payload)
                    if self.callback:self.callback()
                    return result
                return host.stage_outputs(request['layers'],write,snapshot=request['snapshot']),b''
            parent_worker._exchange=exchange
            parent_worker.stage_outputs(rows)
    return store,saved,save,Worker(),layer,expected,inputs


@pytest.mark.parametrize('lazy',[False,True])
def test_pyramid_levels_publish_and_reopen_with_exact_values_and_calibration(scene,monkeypatch,lazy):
    from orchestrator.scientific_project_review import _resolve
    from orchestrator.scientific_projects import ProjectStore
    from env_worker.native_regions import bind
    from orchestrator.native_regions import NativeRegionInputs
    store,saved,save,worker,layer,expected,inputs=pyramid(scene,monkeypatch,lazy=lazy)
    before={row.id for row in store.list(saved.session)}
    save.capture(worker)
    assert {row.id for row in store.list(saved.session)}==before
    save.publish();save.close()
    # Reopen from disk through production native admission. No producing callback runs.
    store=ProjectStore(store.root)
    output,_,sources=_resolve(store,save.target,saved.session,1)
    row=output.document['state']['scientificView']['layers'][0]
    assert row['levels']==['output','level1'] and row['input']=='output'
    for i,name in enumerate(row['levels']):
        reference=output.document['state']['scientificView']['inputs'][name]
        assert values(store,output,reference).tobytes()==expected[i].tobytes()
        assert sources[name].dtype=='>u2' and sources[name].axes==('y','x')
        assert sources[name].descriptor['calibration']['affine']==[[2*(i+1),0.,4.],[0.,3*(i+1),9.]]
    assert sources['output'].descriptor['calibration']['space']==sources['level1'].descriptor['calibration']['space']
    assert output.document['state']['hookeOutput']['base']['digest']==saved.digest
    assert store.read(saved.id,saved.session)==saved
    # Production restore chooses lazy levels and keeps the layer's finest geometry.
    monkeypatch.setitem(sys.modules,'magicgui.widgets',SimpleNamespace(Container=lambda **kw:None))
    rendered=[]
    def add_image(data,**kw):
        # Synthetic native layer callback: napari initially uses placeholder
        # contrast for lazy data. Auto-contrast must use only its displayed slice.
        shown=data[0][:].copy()
        layer=SimpleNamespace(contrast_limits=[0.,1.])
        def reset_contrast_limits(*,mode):
            assert mode=='slice'
            layer.contrast_limits=[float(shown.min()),float(shown.max())]
        layer.reset_contrast_limits=reset_contrast_limits
        rendered.append((data,kw,layer));return layer
    view=SimpleNamespace(add_image=add_image)
    grants=NativeRegionInputs(sources);native=bind(grants.specifications(),grants.answer)
    worker_outputs.restore(view,native,[row],SimpleNamespace(bind=lambda **kw:pytest.fail('Pyramid made editable')),
                           SimpleNamespace(bind=lambda **kw:None))
    levels,settings,restored=rendered[0]
    assert settings['multiscale'] is True
    assert settings['affine'].tolist()==[[2.,0.,4.],[0.,3.,9.],[0.,0.,1.]]
    assert restored.contrast_limits==[768.,791.]
    # Explicit remembered appearance is restored after native layer creation;
    # it must replace the initial readable fallback, never be reset afterward.
    from env_worker.scientific_image_appearance import apply
    apply(restored,{'contrastLimits':[770.,780.],'autoContrast':False,'gamma':1.,
                    'rendering':'mip','isoThreshold':.5,'attenuation':.05})
    assert restored.contrast_limits==[770.,780.]
    for actual,original in zip(levels,expected):assert actual[:].tobytes()==original.tobytes()
    grants.close()
    if inputs:inputs.close()


def test_pyramid_freezes_all_mutable_levels_before_first_write(scene,monkeypatch):
    store,saved,save,worker,layer,expected,_=pyramid(scene,monkeypatch)
    originals=[array.copy() for array in expected]
    worker.callback=lambda:[array.fill(0) for array in layer.data]
    save.capture(worker);save.publish()
    output=store.read(save.target,saved.session)
    for name,array in zip(('output','level1'),originals):
        assert values(store,output,output.document['state']['scientificView']['inputs'][name]).tobytes()==array.tobytes()
    save.close()


@pytest.mark.parametrize('failure',['interrupt','replace-level','geometry'])
def test_pyramid_partial_capture_publishes_nothing(scene,monkeypatch,failure):
    store,saved,save,worker,layer,expected,inputs=pyramid(scene,monkeypatch,lazy=True)
    before={row.id for row in store.list(saved.session)}
    def change():
        if failure=='interrupt':inputs.close()
        elif failure=='replace-level':layer.data[1]=expected[1]
        else:layer.opacity=.1
    worker.callback=change
    with pytest.raises(RuntimeError,match='transfer failed'):save.capture(worker)
    assert {row.id for row in store.list(saved.session)}==before
    assert store.read(saved.id,saved.session)==saved
    assert not list(store.root.glob('.scientific-output-*'))
    inputs.close()


@pytest.mark.parametrize('change',['rgb','labels','dtype','grow','missing-level','wrong-calibration'])
def test_pyramid_invalid_layout_or_reopen_refuses(scene,monkeypatch,change):
    from orchestrator.scientific_project_review import _resolve
    store,saved,save,worker,layer,_,_=pyramid(scene,monkeypatch)
    if change in ('missing-level','wrong-calibration'):
        save.capture(worker);save.publish()
        output=store.read(save.target,saved.session);doc=output.document
        row=doc['state']['scientificView']['layers'][0]
        if change=='missing-level':row['levels'].pop()
        else:row['metadata']['levels'][1]['shape']=[1,3]
        # Imported document tampering: real reopen must reject, not silently substitute a level.
        changed=store.save(output.id,output.session,doc,expected_revision=1)
        with pytest.raises(ValueError):_resolve(store,output.id,output.session,changed.revision)
        save.close();return
    if change=='rgb':layer.rgb=True
    if change=='labels':layer._type_string='labels'
    if change=='dtype':layer.data[1]=layer.data[1].astype('<f4')
    if change=='grow':layer.data[1]=np.zeros((5,7),dtype='>u2')
    with pytest.raises(ValueError):worker_outputs.describe(layer)


def test_pyramid_wire_aggregate_limit_cannot_be_split_across_levels():
    from tests.test_scientific_outputs import Worker
    info=Worker().info;info.update(type='image',shape=[16384,16384],dtype='>u2')
    info['levels']=[{'shape':[16384,16384],'dtype':'>u2'}]*3
    with pytest.raises(ValueError,match='aggregate'):wire.metadata(info)


def test_large_streamed_publication_needs_owned_sources_and_keeps_default_storage_limit(scene,monkeypatch):
    """Scale the work allowance down, retaining real chunks/grants/publication, not a large-data claim."""
    from orchestrator import scientific_arrays as arrays
    from orchestrator.scientific_host import ReviewWorker,Failed
    from orchestrator.scientific_projects import InvalidProject
    store,saved,save,worker,layer,expected,inputs=pyramid(scene,monkeypatch,lazy=True)
    parent=object.__new__(ReviewWorker);parent.grants=inputs;parent.files=None;parent._allowed=lambda:None
    worker.admit_streamed_output=parent.admit_streamed_output
    info=worker_outputs.describe(layer)
    forged={**info,'regionalSources':[{**info['regionalSources'][0],'revision':'f'*64}]}
    with pytest.raises(Failed,match='admitted immutable source'):parent.admit_streamed_output(forged)
    monkeypatch.setattr(arrays,'MAX_NEW_BYTES',16)
    save.capture(worker)
    assert save.publication_bytes==sum(a.nbytes for a in expected)
    # Default storage validation still refuses this work; only this admitted
    # private OutputSave publication receives its exact larger byte allowance.
    for name,grant in save.grants.items():save.references[name]=grant.draft.finish()
    save.reference=save.references[save.primary]
    doc=save.document()
    with pytest.raises(InvalidProject,match='bounded save allowance'):
        save.private._publish(save.private._folder(save.target),saved.session,doc,[])
    # Stop during final hashing publishes no partial document; retry uses the
    # captured bytes, never invokes the producing worker again.
    from orchestrator.scientific_saves import SaveUnavailable
    read=arrays.read_at;checked=[]
    def stop_after_chunk(directory,name,*args):
        body=read(directory,name,*args)
        if name.endswith('.bin'):checked.append(name)
        return body
    with monkeypatch.context() as patch:
        patch.setattr(arrays,'read_at',stop_after_chunk)
        with pytest.raises(SaveUnavailable,match='stopped'):
            save.publish(lambda:bool(checked))
    assert len(checked)==1 and save.target not in {row.id for row in store.list(saved.session)}
    save.publish();output=store.read(save.target,saved.session)
    for name,expected_array in zip(('output','level1'),expected):
        assert values(store,output,output.document['state']['scientificView']['inputs'][name]).tobytes()==expected_array.tobytes()
    assert store.read(saved.id,saved.session)==saved
    inputs.close();save.close()
