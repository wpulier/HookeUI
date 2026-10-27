"""Actual confined ragged geometry publication and independent reopen."""
import numpy as np
import pytest
from orchestrator.scientific_outputs import OutputSave
from orchestrator.scientific_projects import ProjectStore
from orchestrator.scientific_project_review import review
from tests.test_scientific_host_worker import actual_host  # noqa: F401
from tests.test_scientific_surface_output import source


def test_actual_ragged_shapes_restore_geometry_features_and_color_callbacks(tmp_path,actual_host):
    store=ProjectStore(tmp_path/'retained');saved=source(store,actual_host)
    with review(store,saved.id,'s1',saved.revision) as (_,worker):
        reply,_=worker.request({'op':'python-resources','source':"""import numpy as np
import pandas as pd
from env_worker.scientific_shapes import snapshot, restored
from env_worker.scientific_shape_data import components
viewer.layers.clear()
rows=[np.array([[1,2],[3,4]],dtype='float32'),np.array([[4,3],[5,7],[9,8]],dtype='float32')]
shapes=viewer.add_shapes(rows,shape_type=['line','polygon'],name='Measured shapes',
    features=pd.DataFrame({'id':np.array([2**63,2**63+1],dtype='uint64'),
                           'group':pd.Categorical(['a','b'],categories=['b','a','unused'],ordered=True)}),
    face_color='group',face_color_cycle=['red','blue','green'],edge_color='white',
    edge_width=[1,2],z_index=[4,1],text={'string':'{group}','size':10},
    scale=[.5,.25],translate=[10,20],axis_labels=['y','x'],units=['um','um'])
accepted=snapshot(shapes)
arrays=components(shapes)
reopened=restored(arrays,accepted)
assert snapshot(reopened)==accepted
assert reopened.features.equals(shapes.features)
shapes.features.loc[0,'group']='b'
reopened.features.loc[0,'group']='b'
shapes.refresh_colors();reopened.refresh_colors()
assert np.array_equal(shapes.face_color,reopened.face_color)
assert snapshot(reopened)==snapshot(shapes)
shapes.features['score']=pd.Categorical([.2,.8],categories=[.2,.5,.8],ordered=True)
try:
    snapshot(shapes)
except ValueError as error:
    assert 'feature defaults' in str(error),str(error)
else:
    raise AssertionError('Missing feature defaults must refuse before publication')
shapes.feature_defaults['score']=pd.Categorical([.2],categories=[.2,.5,.8],ordered=True)
shapes.face_color='score'
shapes.face_color_mode='colormap'
shapes.edge_color='group'
shapes.edge_color_mode='cycle'
accepted=snapshot(shapes)
reopened=restored(components(shapes),accepted)
assert snapshot(reopened)==accepted
shapes.features.loc[0,'score']=.5
reopened.features.loc[0,'score']=.5
shapes.features.loc[1,'group']='a'
reopened.features.loc[1,'group']='a'
shapes.refresh_colors();reopened.refresh_colors()
assert np.array_equal(shapes.face_color,reopened.face_color)
assert np.array_equal(shapes.edge_color,reopened.edge_color)
def different(a,b,path='state'):
    if type(a) is dict and type(b) is dict and set(a)==set(b):
        return [row for key in a for row in different(a[key],b[key],path+'.'+str(key))]
    if a==b:return []
    return [(path,str(a)[:120],str(b)[:120])]
assert snapshot(reopened)==snapshot(shapes),different(snapshot(reopened),snapshot(shapes))
shapes.current_edge_color='red'
shapes.current_face_color='blue'
accepted=snapshot(shapes)
assert snapshot(restored(components(shapes),accepted))==accepted
print('Ragged shape boundaries, uint64/category features and color callbacks restored')
"""})
        assert not reply['console']['error'],reply['console']['error']


CREATE="""import numpy as np
import pandas as pd
viewer.layers.clear()
rows=[np.array(row,dtype='float32') for row in [
    [[1,2],[3,4]], [[4,3],[5,7],[9,8]], [[10,1],[10,4],[13,4],[13,1]],
    [[14,2],[14,5],[18,5],[18,2]], [[3,10],[7,12],[11,10],[13,14]]]]
shapes=viewer.add_shapes(rows,shape_type=['line','polygon','rectangle','ellipse','path'],
    name='Measured shapes',features=pd.DataFrame({
        'id':np.array([2**63+i for i in range(5)],dtype='uint64'),
        'group':pd.Categorical(['a','b','a','b','a'],categories=['b','a','unused'],ordered=True)}),
    face_color='group',face_color_cycle=['red','blue','green'],edge_color='white',
    edge_width=[1,2,3,2,1],z_index=[4,1,3,2,0],text={'string':'{group}','size':10},
    scale=[.5,.25],translate=[10,20],axis_labels=['y','x'],units=['um','um'])
viewer.reset_view()
creating_variable=42
"""


def test_shapes_saved_from_3d_retain_color_storage_and_callbacks(tmp_path,actual_host):
    store=ProjectStore(tmp_path/'retained');saved=source(store,actual_host)
    create=CREATE.replace('shapes=viewer.add_shapes',
        "rows=[np.column_stack([np.full(len(row),2,dtype='float32'),row]) for row in rows]\nshapes=viewer.add_shapes")
    create=create.replace("scale=[.5,.25],translate=[10,20],axis_labels=['y','x'],units=['um','um']",
        "scale=[2,.5,.25],translate=[5,10,20],axis_labels=['z','y','x'],units=['um']*3")
    with review(store,saved.id,'s1',saved.revision) as (_,worker):
        reply,_=worker.request({'op':'python-resources','source':create+'''
from env_worker.scientific_shapes import snapshot, restored
from env_worker.scientific_shape_data import components
viewer.dims.ndisplay=3
assert shapes.face_color.dtype==np.dtype('float32')
assert shapes.edge_color.dtype==np.dtype('float32')
accepted=snapshot(shapes)
reopened=restored(components(shapes),accepted)
assert snapshot(reopened)==accepted
for side in ('face','edge'):
    assert getattr(reopened,side+'_color').dtype==getattr(shapes,side+'_color').dtype
    assert np.array_equal(getattr(reopened,side+'_color'),getattr(shapes,side+'_color'))
shapes.features.loc[0,'group']='b'
reopened.features.loc[0,'group']='b'
shapes.refresh_colors();reopened.refresh_colors()
assert snapshot(reopened)==snapshot(shapes)
'''})
        assert not reply['console']['error'],reply['console']['error']


@pytest.mark.parametrize('dimensions',[2,3])
def test_actual_all_shape_types_publish_atomically_and_reopen_without_creator(tmp_path,actual_host,monkeypatch,dimensions):
    store=ProjectStore(tmp_path/'retained');saved=source(store,actual_host)
    axes=['y','x'] if dimensions==2 else ['z','y','x']
    create=CREATE
    if dimensions==3:
        create=create.replace('shapes=viewer.add_shapes',"rows=[np.column_stack([np.full(len(row),2,dtype='float32'),row]) for row in rows]\nshapes=viewer.add_shapes")
        create=create.replace("scale=[.5,.25],translate=[10,20],axis_labels=['y','x'],units=['um','um']",
            "scale=[2,.5,.25],translate=[5,10,20],axis_labels=['z','y','x'],units=['um']*3, rotate=[[1,0,0],[0,0,-1],[0,1,0]],shear=[.1,.2,.3],affine=[[1,0,0,3],[0,1,0,2],[0,0,1,1],[0,0,0,1]]")
    with review(store,saved.id,'s1',saved.revision) as (_,worker):
        reply,_=worker.request({'op':'python-resources','source':create})
        assert not reply['console']['error'],reply['console']['error']
        inventory,_=worker.request({'op':'layer-inventory'});layer=inventory['inventory']['layers'][0]
        expected=worker.describe_output(layer['id'])
        _,pixels=worker.request({'op':'render','width':256,'height':192})
        save=OutputSave(store,saved,'shapes-output-123456789',layer['id'],{'semantic':'shape-set','axes':axes})
        save.capture(worker)
        assert not store._folder(save.target).exists()
        from orchestrator.scientific_arrays import ArrayDraft
        from orchestrator.scientific_saves import SaveUnavailable
        finish=ArrayDraft.finish;calls=[]
        def partial(draft):
            calls.append(draft)
            if len(calls)==2:raise OSError('controlled later component failure')
            return finish(draft)
        with monkeypatch.context() as patch:
            patch.setattr(ArrayDraft,'finish',partial)
            with pytest.raises(SaveUnavailable):save.publish()
        assert len(save.references)==1 and not store._folder(save.target).exists()
        reply,_=worker.request({'op':'python-resources','source':'shapes.data=[row+100 for row in shapes.data]'})
        assert not reply['console']['error'],reply['console']['error']
        save.publish();output=store.read(save.target,'s1');save.close()
    assert worker.process.poll() is not None and store.read(saved.id,'s1')==saved
    descriptor=output.document['state']['scientificResource']
    from gpu_worker.semantic_resources import parse_descriptor
    assert parse_descriptor(descriptor).wire()['semantic']=='shape-set'
    offsets=store.array_source(output.id,'s1',1,descriptor['relations']['offsets'])
    assert np.array_equal(offsets.read((slice(0,6),)),np.array([0,2,5,9,13,17],dtype='<u8'))
    with review(store,output.id,'s1',1) as (_,worker):
        inventory,_=worker.request({'op':'layer-inventory'})
        assert worker.describe_output(inventory['inventory']['layers'][0]['id'])==expected
        reply,_=worker.request({'op':'python-resources','source':"""import numpy as np
s=viewer.layers[0]
assert 'creating_variable' not in globals()
assert s.shape_type==['line','polygon','rectangle','ellipse','path']
assert [len(row) for row in s.data]==[2,3,4,4,4]
assert list(s.features['id'])==[2**63+i for i in range(5)]
assert str(s.features['id'].dtype)=='uint64'
assert s.features['group'].cat.ordered
assert list(s.features['group'].cat.categories)==['b','a','unused']
assert np.array_equal(s.data[0][:,-2:],np.array([[1,2],[3,4]],dtype='float32'))
assert list(s.scale)[-2:]==[.5,.25] and list(s.translate)[-2:]==[10,20]
viewer.reset_view()
"""})
        assert not reply['console']['error'],reply['console']['error']
        _,reopened=worker.request({'op':'render','width':256,'height':192})
        assert reopened==pixels
    assert worker.process.poll() is not None


@pytest.mark.parametrize('component', ['shape-offsets','shape-vertices'])
def test_corrupt_shape_transfer_reaps_worker_and_allows_corrected_retry(tmp_path,actual_host,monkeypatch,component):
    import orchestrator.scientific_outputs as module
    store=ProjectStore(tmp_path/'retained');saved=source(store,actual_host)
    with review(store,saved.id,'s1',saved.revision) as (_,worker):
        reply,_=worker.request({'op':'python-resources','source':CREATE})
        assert not reply['console']['error'],reply['console']['error']
        inventory,_=worker.request({'op':'layer-inventory'});layer=inventory['inventory']['layers'][0]
        save=OutputSave(store,saved,'shapes-corrupt-123456789',layer['id'],{'semantic':'shape-set','axes':['y','x']})
        answer=module.OutputGrant.answer;corrupted=[]
        def corrupt(grant,header,payload):
            if grant.draft.scientific['semantic']==component:
                data=np.frombuffer(payload,dtype=grant.draft.dtype).copy()
                data[0]=1 if component=='shape-offsets' else np.nan
                payload=data.tobytes();corrupted.append(component)
            return answer(grant,header,payload)
        with monkeypatch.context() as patch:
            patch.setattr(module.OutputGrant,'answer',corrupt)
            with pytest.raises(RuntimeError,match='transfer failed'):save.capture(worker)
        assert corrupted==[component] and worker.process.poll() is not None
        assert not store._folder(save.target).exists() and not list(store.root.glob('.scientific-output-*'))
        assert store.read(saved.id,'s1')==saved
    with review(store,saved.id,'s1',saved.revision) as (_,worker):
        reply,_=worker.request({'op':'python-resources','source':CREATE})
        assert not reply['console']['error'],reply['console']['error']
        inventory,_=worker.request({'op':'layer-inventory'});layer=inventory['inventory']['layers'][0]
        save=OutputSave(store,saved,'shapes-corrected-123456789',layer['id'],{'semantic':'shape-set','axes':['y','x']})
        save.capture(worker)
        from orchestrator.scientific_saves import SaveUnavailable
        with pytest.raises(SaveUnavailable,match='stopped'):save.publish(lambda:True)
        assert not store._folder(save.target).exists()
        save.publish();save.close()
        assert store.read(save.target,'s1').revision==1 and store.read(saved.id,'s1')==saved


def test_malformed_shape_state_cannot_publish_and_corrected_retry_works(tmp_path,actual_host,monkeypatch):
    import copy
    from env_worker import scientific_structured_state as state
    from orchestrator.scientific_saves import SaveNotStarted
    store=ProjectStore(tmp_path/'retained');saved=source(store,actual_host)
    with review(store,saved.id,'s1',saved.revision) as (_,worker):
        reply,_=worker.request({'op':'python-resources','source':CREATE})
        assert not reply['console']['error'],reply['console']['error']
        inventory,_=worker.request({'op':'layer-inventory'});layer=inventory['inventory']['layers'][0]
        expected=worker.describe_output(layer['id'])
        changes=[('face','mode','invalid'),('face','property','missing'),('edge','cycleIndex',-1),
            ('edge','current',np.array([0,0,0,2.])),('settings','edge_width',[1]),
            ('settings','z_index',[1.5]*5),('settings','face_color',np.zeros((4,4))),
            ('settings','face_colormap','invalid'),
            (None,'currentWidth',-1)]
        for side,key,value in changes:
            info=copy.deepcopy(expected);root=info['state']['state']['value']
            destination=root if side is None else root[side]['value']
            destination[key]=state.pack(value)['state']
            save=OutputSave(store,saved,'shapes-metadata-'+key,layer['id'],{'semantic':'shape-set','axes':['y','x']})
            with monkeypatch.context() as patch:
                patch.setattr(worker,'describe_output',lambda _:info)
                with pytest.raises(SaveNotStarted):save.capture(worker)
            assert not store._folder(save.target).exists() and not list(store.root.glob('.scientific-output-*'))
            assert store.read(saved.id,'s1')==saved and worker.process.poll() is None
        save=OutputSave(store,saved,'shapes-metadata-corrected',layer['id'],{'semantic':'shape-set','axes':['y','x']})
        save.capture(worker);save.publish();save.close()
        assert store.read(save.target,'s1').revision==1

@pytest.mark.parametrize('dimensions',[2,3])
def test_actual_empty_shapes_preserve_defaults_and_reopen_for_new_annotations(tmp_path,actual_host,dimensions):
    store=ProjectStore(tmp_path/'retained');original=source(store,actual_host)
    axes=['y','x'] if dimensions==2 else ['z','y','x']
    with review(store,original.id,'s1',original.revision) as (_,worker):
        reply,_=worker.request({'op':'python-resources','source':f"""import numpy as np
import pandas as pd
viewer.layers.clear()
shapes=viewer.add_shapes(ndim={dimensions},name='No regions',
 features=pd.DataFrame({{'id':np.array([],dtype='uint64'),'group':pd.Categorical([],categories=['b','a'],ordered=True)}}),
 feature_defaults={{'id':np.array([2**63+1],dtype='uint64'),'group':pd.Categorical(['b'],categories=['b','a'],ordered=True)}},
 edge_color='red',face_color='transparent',edge_width=3,scale={[.5]*dimensions!r},translate={[10]*dimensions!r})
creating_variable=42
"""})
        assert not reply['console']['error'],reply['console']['error']
        inventory,_=worker.request({'op':'layer-inventory'});layer=inventory['inventory']['layers'][0]
        assert layer['elementCount']==0
        expected=worker.describe_output(layer['id'])
        save=OutputSave(store,original,'empty-shapes-123456789',layer['id'],{'semantic':'shape-set','axes':axes})
        try:save.capture(worker);save.publish();target=save.target
        finally:save.close()
    output=store.read(target,'s1',revision=1)
    descriptor=output.document['state']['scientificResource']
    offsets=store.array_source(target,'s1',1,descriptor['relations']['offsets'])
    assert offsets.read((slice(0,1),)).tolist()==[0]
    with review(store,target,'s1',1) as (_,worker):
        frame,pixels=worker.request({'op':'render','width':256,'height':192});assert pixels
        assert frame['frame']['layers'][0]['elementCount']==0
        assert worker.describe_output(frame['frame']['layers'][0]['id'])==expected
        reply,_=worker.request({'op':'python-resources','source':f"""import numpy as np
assert 'creating_variable' not in globals()
shapes=viewer.layers[0]
assert shapes.data==[] and shapes.ndim=={dimensions}
assert str(shapes.features['id'].dtype)=='uint64'
assert shapes.features['group'].cat.ordered
assert list(shapes.features['group'].cat.categories)==['b','a']
assert int(shapes.feature_defaults['id'].iloc[0])==2**63+1
shapes.add(np.array({[[1]*dimensions,[3]*dimensions]!r},dtype='float32'),shape_type='line')
assert len(shapes.data)==1 and int(shapes.features['id'].iloc[0])==2**63+1
"""})
        assert not reply['console']['error'],reply['console']['error']
    assert store.read(original.id,'s1')==original and store.read(target,'s1',revision=1)==output
