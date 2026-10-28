"""Synthetic workers, actual immutable storage; no renderer qualification claim."""
import copy
import itertools
import numpy as np
import pytest

from env_worker import native_edits as wire
from orchestrator.scientific_outputs import OutputSave
from orchestrator.scientific_saves import SaveNotStarted, SaveUnavailable
from tests.test_scientific_edits import scene, values
from tests.scientific_threads import exclude_preexisting_test_harness_threads  # noqa: F401


class Worker:
    def __init__(self):
        self.data = np.arange(35,dtype='>u2').reshape(5,7)
        self.info = {'format':'native-layer-output-v1','shape':[5,7],'dtype':'>u2','name':'New objects','type':'labels',
            'visible':True,'opacity':.75,'geometry':{'basis':[[4,9],[6,9],[4,12]],'axisLabels':['depth','width'],'units':['micrometer','micrometer']}}
        self.calls=0

    def describe_output(self,layer): return copy.deepcopy(self.info)
    def stage_output(self,layer,info,meaning,edit):
        from orchestrator.scientific_host import LayerStaging
        self.calls+=1
        assert info==self.info
        staging=LayerStaging(edit); grant=wire.bind_output(edit.specification(),staging.answer)
        for index in itertools.product(*(range(n) for n in grant.grid)):
            slices=tuple(slice(i*c,min((i+1)*c,n)) for i,c,n in zip(index,grant.chunks,grant.shape))
            grant.write(index,self.data[slices])
        staging.finish({'ok':True,'staged':edit.identity},b'')


def attempt(scene):
    store,saved,_,_=scene
    runtime={'bundle':'mine/runtime','generation':'g1','operation':'viewer'}
    doc=saved.document;doc['runtimes']=[runtime];doc['state']['scientificView']={'runtime':runtime,'inputs':{'labels':doc['resources'][0]}}
    saved=store.save(saved.id,saved.session,doc,expected_revision=saved.revision)
    save=OutputSave(store,saved,'request-1234567890123','a'*32,{'semantic':'instance-labels','axes':['z','x']},producer={'kind':'test'})
    return store,saved,save,Worker()


def test_new_output_is_atomic_independent_exact_and_reopenable(scene):
    from orchestrator.scientific_project_review import _resolve
    store,saved,save,worker=attempt(scene)
    original_ids={item.id for item in store.list(saved.session)}
    save.capture(worker)
    assert {item.id for item in store.list(saved.session)}==original_ids
    assert save.publish()==save.publish()
    output=store.read(save.target,saved.session)
    assert output.revision==1 and output.id != saved.id and worker.calls==1
    assert store.read(saved.id,saved.session)==saved
    assert values(store,output,output.document['resources'][0]).tobytes()==worker.data.tobytes()

    _,_,sources=_resolve(store,output.id,output.session,1)
    assert sources['output'].dtype=='>u2' and sources['output'].axes==('z','x')
    assert sources['output'].descriptor['semantic']=='instance-labels'
    calibration=sources['output'].descriptor['calibration']
    assert calibration=={'space':{'session':saved.session,'id':output.id,'revision':1},'axes':['z','x'],
                         'units':['micrometer','micrometer'],'affine':[[2.,0.,4.],[0.,3.,9.]]}
    assert output.document['state']['coordinateSpace']==calibration
    assert output.document['state']['scientificView']['layers'][0]['metadata']==worker.info
    assert output.document['state']['hookeOutput']['base']['digest']==saved.digest
    save.close()
    assert values(store,output,output.document['resources'][0]).tobytes()==worker.data.tobytes()


@pytest.mark.parametrize('components',[3,4])
def test_rgb_publication_preserves_components_and_spatial_calibration(scene,components):
    from env_worker import scientific_output_wire as output_wire
    from orchestrator.scientific_project_review import _resolve
    store,saved,save,worker=attempt(scene)
    save.meaning={'semantic':'intensity','axes':['y','x']}
    save.selections=[{'layer':'a'*32,'meaning':save.meaning}]
    worker.data=np.arange(5*7*components,dtype='uint8').reshape(5,7,components)
    worker.info.update(format=output_wire.RGB_FORMAT,type='image',shape=list(worker.data.shape),dtype='|u1')
    save.capture(worker);save.publish()
    output=store.read(save.target,saved.session)
    _,_,sources=_resolve(store,output.id,output.session,1)
    source=sources['output']
    assert values(store,output,output.document['resources'][0]).tobytes()==worker.data.tobytes()
    assert source.axes==('y','x','color')
    assert source.descriptor['channels']=={'axis':'color','ids':['red','green','blue','alpha'][:components]}
    assert source.descriptor['calibration']['axes']==['y','x']
    assert source.descriptor['calibration']['affine']==[[2.,0.,4.],[0.,3.,9.]]
    assert output_wire.retained(worker.info,source)==worker.info
    assert store.read(saved.id,saved.session)==saved
    save.close()


@pytest.mark.parametrize('change',['components','type','axes','meaning'])
def test_rgb_invalid_declaration_refuses_before_transfer(scene,change):
    from env_worker import scientific_output_wire as output_wire
    store,saved,save,worker=attempt(scene)
    save.meaning={'semantic':'intensity','axes':['y','x']}
    save.selections=[{'layer':'a'*32,'meaning':save.meaning}]
    worker.info.update(format=output_wire.RGB_FORMAT,type='image',shape=[5,7,3],dtype='|u1')
    if change=='components':worker.info['shape'][-1]=2
    if change=='type':worker.info['type']='labels'
    if change=='axes':save.meaning['axes']=['y','color']
    if change=='meaning':save.meaning['semantic']='probabilities'
    with pytest.raises(SaveNotStarted):save.capture(worker)
    assert worker.calls==0 and store.read(saved.id,saved.session)==saved


@pytest.mark.parametrize('after',[False,True])
def test_failed_publication_retries_same_snapshot_and_identity(scene,monkeypatch,after):
    import orchestrator.scientific_outputs as module
    store,saved,save,worker=attempt(scene);save.capture(worker);expected=worker.data.copy()
    rename=module.os.rename
    def fail(source,target):
        if after: rename(source,target)
        raise OSError('controlled rename acknowledgement failure')
    with monkeypatch.context() as patch:
        patch.setattr(module.os,'rename',fail)
        with pytest.raises(SaveUnavailable,match='same captured'):save.publish()
    worker.data[:]=0
    save.publish();output=store.read(save.target,saved.session)
    assert worker.calls==1 and output.revision==1
    assert values(store,output,output.document['resources'][0]).tobytes()==expected.tobytes()
    assert store.read(saved.id,saved.session)==saved
    save.close()


def test_manifest_failure_does_not_require_recapture(scene,monkeypatch):
    from orchestrator.scientific_arrays import ArrayDraft
    store,saved,save,worker=attempt(scene);save.capture(worker)
    finish=ArrayDraft.finish
    with monkeypatch.context() as patch:
        patch.setattr(ArrayDraft,'finish',lambda self: (_ for _ in ()).throw(OSError('controlled full disk')))
        with pytest.raises(SaveUnavailable):save.publish()
    assert worker.calls==1
    save.publish();assert worker.calls==1
    save.close()


@pytest.mark.parametrize('change',['meaning','axes','oversize','float_labels'])
def test_bad_declarations_do_not_accept_or_start_transfer(scene,change):
    store,saved,save,worker=attempt(scene)
    if change=='meaning':save.meaning['semantic']='intensity'
    if change=='axes':save.meaning['axes']=['x','y','z']
    if change=='oversize':worker.info['shape']=[10000,10000]
    if change=='float_labels':worker.info['dtype']='>f4'
    with pytest.raises(SaveNotStarted):save.capture(worker)
    assert worker.calls==0 and not store._folder(save.target).exists()
    assert store.read(saved.id,saved.session)==saved


def test_cancel_before_publish_preserves_original_and_no_visible_output(scene):
    store,saved,save,worker=attempt(scene);save.capture(worker)
    with pytest.raises(SaveUnavailable,match='stopped'):save.publish(lambda:True)
    save.close();assert not store._folder(save.target).exists()
    assert store.read(saved.id,saved.session)==saved


def test_workers_have_no_new_output_commit_or_existing_reference_authority(scene):
    store,saved,save,worker=attempt(scene);save.capture(worker)
    spec=save.grant.specification();assert 'reference' not in spec and 'projectRevision' not in spec
    grant=wire.bind_output(spec,lambda *args:pytest.fail('No worker publication'))
    with pytest.raises(ValueError,match='Only the parent'):grant.commit()
    with pytest.raises(ValueError):wire.bind(spec,lambda *args:None)
    save.close()


def test_output_capture_freezes_before_callbacks_and_refuses_changed_metadata(monkeypatch):
    from tests.test_scientific_layer_exports import setup
    from env_worker.scientific_outputs import capture,describe
    from env_worker.native_edits import bind_output,OUTPUT_FORMAT
    import sys,threading
    frames=sys._current_frames;harness=set(frames())-{threading.get_ident()}
    monkeypatch.setattr(sys,'_current_frames',lambda:{key:value for key,value in frames().items() if key not in harness})
    _,viewer,layer,_=setup();layer.name='Output';layer.visible=True;layer.opacity=.7
    info=describe(layer);meaning={'semantic':'instance-labels','axes':['y','x']}
    spec={'format':OUTPUT_FORMAT,'token':'parent-grant','shape':[4,6],'dtype':'>u2','axes':['y','x'],'chunks':[2,3]}
    expected=layer.data.copy();received=[]
    def write(header,payload):
        received.append((header['index'],np.frombuffer(payload,dtype='>u2').reshape(header['shape']).copy()))
        layer.data[:]=0
        return {'ok':True,'kind':OUTPUT_FORMAT,'index':header['index'],'bytes':len(payload)},b''
    grant=bind_output(spec,write)
    with capture(viewer,layer,info,meaning,grant,'files') as chunks:
        for index,chunk in chunks:grant.write(index,chunk)
    actual=np.empty_like(expected)
    for (y,x),chunk in received:actual[y*2:y*2+2,x*3:x*3+3]=chunk
    assert actual.tobytes()==expected.tobytes()
    layer.offset=2
    with pytest.raises(ValueError,match='changed before'):
        with capture(viewer,layer,info,meaning,grant,'files'):pytest.fail('Changed layer accepted')


def test_completed_output_release_defers_owned_cleanup_without_losing_data(scene,monkeypatch):
    import orchestrator.scientific_outputs as module
    store,saved,save,worker=attempt(scene);save.capture(worker);save.publish();target=save.target
    stage=save.stage
    original=module.shutil.rmtree
    with monkeypatch.context() as patch:
        patch.setattr(module.shutil,'rmtree',lambda path:(_ for _ in ()).throw(OSError('controlled staging cleanup failure')))
        assert save.release() is False
        assert stage.exists() and save in module._pending_cleanup
    assert store.read(target,saved.session).revision==1
    assert module.retry_cleanup()==[] and not stage.exists() and save not in module._pending_cleanup
    assert store.read(saved.id,saved.session)==saved


def test_untrusted_worker_cannot_publish_negative_object_labels(scene):
    store,saved,save,worker=attempt(scene)
    worker.data=worker.data.astype('>i2');worker.data[0,0]=-1;worker.info['dtype']='>i2'
    with pytest.raises(RuntimeError,match='transfer failed'):save.capture(worker)
    assert not store._folder(save.target).exists() and store.read(saved.id,saved.session)==saved


def test_retained_scene_cannot_present_different_calibration_than_its_resource(scene):
    from orchestrator.scientific_project_review import _resolve
    store,saved,save,worker=attempt(scene);save.capture(worker);save.publish()
    output=store.read(save.target,saved.session);document=output.document
    document['state']['scientificView']['layers'][0]['metadata']['geometry']['basis'][0][0]+=1
    changed=store.save(output.id,output.session,document,expected_revision=1)
    with pytest.raises(ValueError,match='calibration'):_resolve(store,output.id,output.session,changed.revision)
    _resolve(store,output.id,output.session,1)
    save.close()


@pytest.mark.parametrize('interrupt',[False,True])
@pytest.mark.parametrize('decoded',[False,True])
def test_immutable_lazy_image_streams_to_atomic_native_publication(scene,monkeypatch,interrupt,decoded):
    from types import SimpleNamespace
    import orchestrator.scientific_outputs as parent
    from orchestrator.native_regions import NativeRegionInputs
    from env_worker import native_regions, lazy_arrays
    from env_worker.scientific_outputs import capture,describe
    store,saved,save,_=attempt(scene)
    original=store.array_source(saved.id,saved.session,saved.revision,saved.document['resources'][0])
    inputs=NativeRegionInputs({'image':original})
    native=native_regions.bind(inputs.specifications(),inputs.answer)['image']
    lazy=lazy_arrays.LazyNative(native,lazy_arrays.ChunkCache())
    if decoded:
        from dataclasses import replace
        from tests.test_scientific_directories import transfer
        from orchestrator.source_files import SourceFileInputs
        from env_worker.source_files import bind
        from env_worker.scientific_host import Context
        imported=transfer(store,{'pixels.raw':scene[3].tobytes()})
        source=store.file_source(imported.id,imported.session,1,imported.document['resources'][0])
        inputs.close()
        inputs=SourceFileInputs({'source':replace(source,directory=imported.document['state']['sourceDirectory'])},regional=('source',))
        files=bind(inputs.specifications(),inputs.answer)
        def decode(slices):
            # Tiny synthetic format decoder; source bytes pass through real grants.
            with files['source'].open('pixels.raw',view=True) as stream:
                return np.frombuffer(stream.read(),dtype='>u2').reshape(scene[3].shape)[slices]
        context=Context(None,{},None,None,files)
        lazy=context.decode_image(input='source',shape=scene[3].shape,dtype='>u2',axes=('y','x'),decode=decode)
    layer=SimpleNamespace(data=lazy,_type_string='image',ndim=2,rgb=False,multiscale=False,
        name='Retained intensity',visible=True,opacity=1.0,axis_labels=('y','x'),units=('pixel','pixel'),
        data_to_world=lambda point:tuple(float(v) for v in point))
    save.meaning={'semantic':'intensity','axes':['y','x']}
    save.selections=[{'layer':'a'*32,'meaning':save.meaning}]
    # Small real pixels, several real write grants; no native viewer or large-data claim.
    monkeypatch.setattr(parent,'MAX_REGION_BYTES',12)
    monkeypatch.setattr(lazy_arrays.LazyNative,'__array__',lambda *a,**kw:pytest.fail('Whole lazy image materialized'))
    class Streaming:
        def describe_output(self,key):return describe(layer)
        def stage_output(self,key,info,meaning,edit):
            from orchestrator.scientific_host import LayerStaging
            staging=LayerStaging(edit);grant=wire.bind_output(edit.specification(),staging.answer)
            with capture(SimpleNamespace(layers=[layer]),layer,info,meaning,grant,'memory') as chunks:
                for number,(index,block) in enumerate(chunks):
                    grant.write(index,block)
                    if interrupt and number==0:inputs.close()
            staging.finish({'ok':True,'staged':edit.identity},b'')
    ids={row.id for row in store.list(saved.session)}
    if interrupt:
        with pytest.raises(RuntimeError,match='transfer failed'):save.capture(Streaming())
        assert {row.id for row in store.list(saved.session)}==ids
    else:
        save.capture(Streaming());save.publish()
        from orchestrator.scientific_project_review import _resolve
        output,_,sources=_resolve(store,save.target,saved.session,1)
        assert sources['output'].dtype=='>u2' and sources['output'].axes==('y','x')
        assert values(store,output,output.document['resources'][0]).tobytes()==scene[3].tobytes()
        assert output.document['state']['scientificView']['layers'][0]['metadata']==describe(layer)
    assert store.read(saved.id,saved.session)==saved
    save.close();inputs.close()



def test_regional_image_metadata_keeps_other_output_size_limits():
    from env_worker import scientific_output_wire as output
    info=Worker().info
    info.update(type='image',shape=[8192,8192],dtype='>u2')
    assert output.metadata(info)==info  # metadata only: no claim of 128 MiB runtime acceptance
    with pytest.raises(ValueError):output.metadata({**info,'type':'labels'})
    with pytest.raises(ValueError):output.metadata({**info,'shape':[32768,32768]})


def test_large_metadata_requires_regional_identity_and_finite_transfer_deadline():
    from env_worker import scientific_output_wire as wire
    info=Worker().info
    info.update(type='image',shape=[5*1024,1024,1024],dtype='|u1',geometry={
        'basis':[[0,0,0],[1,0,0],[0,1,0],[0,0,1]],'axisLabels':['z','y','x'],'units':['pixel']*3})
    with pytest.raises(ValueError,match='limits'):wire.metadata(info)
    reference={'session':'s1','id':'p'+'1'*32,'component':'array.raw','revision':'2'*64}
    info['regionalSources']=[reference]
    assert wire.metadata(info)==info
    assert 60<wire.transfer_seconds(5 << 30)<=wire.transfer_seconds(wire.MAX_STREAMED_BYTES)<3600
    assert wire.transfer_seconds(64 << 20)==60
    with pytest.raises(ValueError):wire.transfer_seconds(wire.MAX_STREAMED_BYTES+1)
    with pytest.raises(ValueError):wire.metadata({**info,'type':'labels'})


def test_context_advertises_streamed_image_save_without_mutable_host_limits():
    from env_worker.scientific_host import Context
    from env_worker.scientific_output_wire import MAX_STREAMED_BYTES
    context=Context(None,{},None,None)
    assert context.streamed_image_publication=={'version':1,'maximum_bytes':MAX_STREAMED_BYTES}
    description=context.streamed_image_publication
    description['maximum_bytes']=1
    assert context.streamed_image_publication['maximum_bytes']==MAX_STREAMED_BYTES
    with pytest.raises(AttributeError):context.streamed_image_publication=description


def test_worker_output_still_requires_runtime_when_native_inputs_have_none(scene):
    from orchestrator.scientific_projects import InvalidProject
    store,saved,_,_=scene
    doc=saved.document;doc['runtimes']=[]
    doc['state']['scientificView']={'inputs':{'labels':doc['resources'][0]}}
    saved=store.save(saved.id,saved.session,doc,expected_revision=saved.revision)
    with pytest.raises(InvalidProject,match='retained scientific runtime'):
        OutputSave(store,saved,'request-1234567890123','a'*32,{'semantic':'instance-labels','axes':['z','x']})
