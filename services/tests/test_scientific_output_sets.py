"""Atomic mixed-output storage with controlled workers; native coverage is separate."""
import numpy as np
import pytest

from orchestrator.scientific_outputs import OutputSave
from orchestrator.scientific_saves import SaveNotStarted, SaveUnavailable
from tests.test_scientific_outputs import attempt, Worker
from tests.test_scientific_edits import scene, values


class SetWorker:
    def __init__(self):
        self.parts = {'a'*32:Worker(), 'b'*32:Worker()}
        second=self.parts['b'*32]
        second.data=np.arange(35,dtype='<f4').reshape(5,7)/35
        second.info.update(name='Probability',type='image',dtype='<f4')
        self.calls=0

    def describe_output(self, layer): return self.parts[layer].describe_output(layer)

    def stage_outputs(self, rows):
        self.calls+=1
        for row in rows:
            self.parts[row['layer']].stage_output(row['layer'],row['metadata'],row['meaning'],row['grants']['output'])


def setup_set(scene):
    store,saved,_,_=attempt(scene)
    selections=[{'layer':'a'*32,'meaning':{'semantic':'instance-labels','axes':['y','x']}},
                {'layer':'b'*32,'meaning':{'semantic':'probabilities','axes':['y','x']}}]
    save=OutputSave(store,saved,'mixed-output-request','a'*32,selections[0]['meaning'],selections=selections)
    return store,saved,save,SetWorker()


def test_two_new_outputs_publish_as_one_document_without_rewriting_source(scene):
    from orchestrator.scientific_project_review import _resolve
    store,saved,save,worker=setup_set(scene)
    before={row.id for row in store.list(saved.session)}
    save.capture(worker)
    assert {row.id for row in store.list(saved.session)}==before
    status=save.publish()
    assert status['layers']==['a'*32,'b'*32] and save.publish()==status
    output=store.read(save.target,saved.session)
    assert {row.id for row in store.list(saved.session)}==before|{output.id}
    assert store.read(saved.id,saved.session)==saved and worker.calls==1
    _,_,sources=_resolve(store,output.id,output.session,1)
    layers=output.document['state']['scientificView']['layers']
    assert len(layers)==2 and len(sources)==2
    for row,part in zip(layers,worker.parts.values()):
        ref=output.document['state']['scientificView']['inputs'][row['input']]
        assert values(store,output,ref).tobytes()==part.data.tobytes()
        assert row['metadata']==part.info
    provenance=output.document['state']['hookeOutput']
    assert provenance['format']==2 and len(provenance['outputs'])==2
    assert provenance['base']['digest']==saved.digest
    assert provenance['outputs'][0]['calibration']['space'] != provenance['outputs'][1]['calibration']['space']
    save.close()


@pytest.mark.parametrize('after',[False,True])
def test_group_publication_retry_keeps_all_captured_bytes(scene,monkeypatch,after):
    import orchestrator.scientific_outputs as module
    store,saved,save,worker=setup_set(scene);save.capture(worker)
    expected=[part.data.tobytes() for part in worker.parts.values()]
    rename=module.os.rename
    def fail(source,target):
        if after: rename(source,target)
        raise OSError('controlled publication failure')
    with monkeypatch.context() as patch:
        patch.setattr(module.os,'rename',fail)
        with pytest.raises(SaveUnavailable):save.publish()
    for part in worker.parts.values():part.data[:]=0
    save.publish(); output=store.read(save.target,saved.session)
    assert [values(store,output,ref).tobytes() for ref in output.document['resources']]==expected
    assert worker.calls==1 and store.read(saved.id,saved.session)==saved
    save.close()


def test_group_bad_second_meaning_refuses_before_any_transfer(scene):
    store,saved,save,worker=setup_set(scene)
    worker.parts['b'*32].info['type']='labels'
    with pytest.raises(SaveNotStarted):save.capture(worker)
    assert worker.calls==0 and not store._folder(save.target).exists()
    assert store.read(saved.id,saved.session)==saved


def test_generated_scene_title_preserves_maximum_length_native_name(scene):
    store,saved,save,worker=setup_set(scene)
    worker.parts['a'*32].info['name']='λ'*240
    save.capture(worker);save.publish()
    output=store.read(save.target,saved.session)
    assert len(output.document['title'])<=240
    assert output.document['state']['scientificView']['layers'][0]['metadata']['name']=='λ'*240
    save.close()


def test_oversized_reopen_envelope_is_refused_before_transfer(scene,monkeypatch):
    import orchestrator.scientific_host as module
    store,saved,save,worker=setup_set(scene)
    # Controlled message budget exercises the actual encoder/preflight, not a
    # huge allocation. Actual native large-state coverage is in the host suite.
    monkeypatch.setattr(module,'MAX_HEADER',1024)
    with pytest.raises(SaveNotStarted,match='combined scientific metadata'):save.capture(worker)
    assert worker.calls==0 and not store._folder(save.target).exists()
    assert not list(store.root.glob('.scientific-output-*'))


def test_stage_envelope_refusal_does_not_exchange_or_close_worker(monkeypatch):
    from types import SimpleNamespace
    import orchestrator.scientific_host as module
    from env_worker.native_edits import OUTPUT_FORMAT
    worker=object.__new__(module.ReviewWorker);calls=[]
    worker.work=None;worker._exchange=lambda *args:calls.append('exchange');worker.close=lambda:calls.append('close')
    monkeypatch.setattr(module,'snapshot_storage',lambda *args:'memory')
    monkeypatch.setattr(module,'MAX_HEADER',1024)
    info=Worker().info
    rows=[]
    for token in ('a','b'):
        grant=SimpleNamespace(specification=lambda token=token:{'format':OUTPUT_FORMAT,'token':token*64,
              'shape':[5,7],'dtype':'>u2','axes':['y','x'],'chunks':[5,7]})
        rows.append({'layer':token*32,'metadata':info,'meaning':{'semantic':'instance-labels','axes':['y','x']},'grants':{'output':grant}})
    with pytest.raises(module.CaptureRefused,match='metadata'):worker.stage_outputs(rows)
    assert calls==[]


def test_group_partial_transport_publishes_nothing_and_cleans_owned_stage(scene):
    store,saved,save,worker=setup_set(scene)
    before={row.id for row in store.list(saved.session)}
    def fail(rows):
        row=rows[0]
        worker.parts[row['layer']].stage_output(row['layer'],row['metadata'],row['meaning'],row['grants']['output'])
        raise OSError('controlled second-layer transfer failure')
    worker.stage_outputs=fail
    with pytest.raises(RuntimeError,match='transfer failed'):save.capture(worker)
    assert {row.id for row in store.list(saved.session)}==before
    assert not list(store.root.glob('.scientific-output-*'))
    assert store.read(saved.id,saved.session)==saved


@pytest.mark.parametrize('storage',['files','memory'])
@pytest.mark.parametrize('declaration_mismatch',[False,True])
def test_worker_freezes_every_output_before_first_transport_callback(monkeypatch,storage,declaration_mismatch):
    import sys,threading
    from types import SimpleNamespace
    from env_worker.scientific_host import ReviewSession
    from env_worker.scientific_outputs import describe
    from env_worker.native_edits import OUTPUT_FORMAT
    from tests.test_scientific_layer_exports import setup
    frames=sys._current_frames;harness=set(frames())-{threading.get_ident()}
    monkeypatch.setattr(sys,'_current_frames',lambda:{key:value for key,value in frames().items() if key not in harness})
    _,viewer,first,_=setup(); _,_,second,_=setup()
    viewer.layers.append(second); rows=[]; expected=[]
    for index,layer in enumerate(viewer.layers):
        layer.name=f'Output {index}';layer.visible=True;layer.opacity=1
        layer.data+=index;expected.append(layer.data.copy())
        rows.append({'layer':('a' if index==0 else 'b')*32,'metadata':describe(layer),
            'meaning':{'semantic':'instance-labels','axes':['y','x']},
            'components':[{'name':'output','edit':{'format':OUTPUT_FORMAT,'token':f'grant-{index}',
                'shape':[4,6],'dtype':'>u2','axes':['y','x'],'chunks':[2,3]}}]})
    host=object.__new__(ReviewSession);host._thread=threading.get_ident();host._closed=False;host._frame='old'
    host.viewer=viewer;host._layers=list(zip([row['layer'] for row in rows],viewer.layers))
    host.editable=SimpleNamespace(input_for=lambda layer:None);host._layer_rows=lambda:[]
    from env_worker.scientific_view_bindings import ViewBindings
    host.view_bindings=ViewBindings(viewer);host.view_bindings.seal()
    if declaration_mismatch:
        host.view_bindings.bind_output(layer=second,key='second',meaning={'semantic':'class-labels','axes':['y','x']})
    received={f'grant-{i}':np.empty_like(data) for i,data in enumerate(expected)}
    def exchange(header,payload):
        assert host._frame is None
        for layer in viewer.layers:layer.data[:]=0
        y,x=header['index'];part=np.frombuffer(payload,dtype='>u2').reshape(header['shape'])
        received[header['token']][y*2:y*2+2,x*3:x*3+3]=part
        return {'ok':True,'kind':OUTPUT_FORMAT,'index':header['index'],'bytes':len(payload)},b''
    if declaration_mismatch:
        with pytest.raises(ValueError,match='integration-declared'):
            host.stage_outputs(rows,exchange,snapshot=storage)
        assert [layer.data.tobytes() for layer in viewer.layers]==[row.tobytes() for row in expected]
        return
    result=host.stage_outputs(rows,exchange,snapshot=storage)
    assert result['staged']==['grant-0','grant-1']
    assert [row.tobytes() for row in received.values()]==[row.tobytes() for row in expected]
