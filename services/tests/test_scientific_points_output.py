"""Actual confined Points publication/reopen, typed data and native rendering."""
from pathlib import Path
import numpy as np
import pytest
from orchestrator.scientific_projects import ProjectStore
from orchestrator.scientific_outputs import OutputSave
from orchestrator.scientific_project_review import review
from tests.test_scientific_arrays import fill
from tests.test_scientific_host_worker import actual_host  # noqa: F401

CREATE = """import numpy as np
import pandas as pd
from env_worker.scientific_points import snapshot
viewer.layers.clear()
points=viewer.add_points(np.array([[1.25,2.5,4],[7,8.5,9],[4,6,7]],dtype=np.float64),
    features=pd.DataFrame({'id':np.array([2**63+1,2**63+2,2**63+3],dtype='uint64'),
        'class':pd.Categorical(['B','A','B'],categories=['B','A'],ordered=True)}),
    size=[3,7,5],symbol=['o','s','o'],face_color='class',border_width=[.1,.2,.3],
    scale=[2,.5,.25],translate=[10,20,30],axis_labels=['z','y','x'],units=['um']*3,
    text={'string':'{class}','size':12},name='Measured points')
original_state=snapshot(points)
keep_private=314
viewer.dims.ndisplay=3
viewer.reset_view()
"""


@pytest.mark.parametrize('family,dtype',[('points','<f8'),('tracks','<f8'),('vectors','<f8'),('vectors','>f8')])
def test_structured_save_reopen_preserves_values_features_coordinates_relationships_and_source(tmp_path,actual_host,family,dtype):
    from tests.test_scientific_tracks_output import CREATE as TRACKS_CREATE, DATA as TRACKS_DATA
    from tests.test_scientific_vectors_output import CREATE as VECTORS_CREATE, DATA as VECTORS_DATA
    code={'points':CREATE,'tracks':TRACKS_CREATE,'vectors':VECTORS_CREATE}[family]
    if family=='vectors':code=code.replace("dtype='float64'",f"dtype='{dtype}'")
    semantic={'points':'point-set','tracks':'track-coordinates','vectors':'vector-set'}[family]
    axes=['t','y','x'] if family=='tracks' else ['z','y','x']
    expected_data=np.array([[1.25,2.5,4],[7,8.5,9],[4,6,7]]) if family=='points' else TRACKS_DATA if family=='tracks' else VECTORS_DATA
    expected_data=expected_data.astype(dtype)
    prepared=actual_host;store=ProjectStore(tmp_path/'retained')
    bundle,operation=prepared['key'].split('#')
    runtime={'bundle':bundle,'generation':Path(prepared['folder']).name,'operation':operation}
    saved=store.create('s1',{'kind':'scene','title':'Source','state':{},'resources':[],'runtimes':[runtime]})
    data=np.arange(60,dtype='<u2').reshape(3,4,5)
    draft=store.array_draft(saved.id,'s1',component='array.source',shape=data.shape,dtype=data.dtype.str,
        axes=['z','y','x'],chunks=[1,4,5],scientific={'semantic':'intensity'},max_written_bytes=data.nbytes)
    ref=fill(draft,data);doc=saved.document;doc['resources']=[ref]
    doc['state']['scientificView']={'runtime':runtime,'inputs':{'volume':ref}}
    saved=store.save(saved.id,'s1',doc,expected_revision=1)
    with review(store,saved.id,'s1',saved.revision) as (_,worker):
        reply,_=worker.request({'op':'python-resources','source':code})
        assert not reply['console']['error'],reply['console']['error']
        inventory,_=worker.request({'op':'layer-inventory'});layer=inventory['inventory']['layers'][0]
        expected=worker.describe_output(layer['id'])
        frame,pixels=worker.request({'op':'render','width':256,'height':192})
        save=OutputSave(store,saved,'point-output-123456789',layer['id'],{'semantic':semantic,'axes':axes})
        save.capture(worker)
        # A lost storage acknowledgement retries the same captured values, not Python.
        original_sync=store._sync_directory
        failed=False
        def fail_once(folder):
            nonlocal failed
            if not failed:
                failed=True;raise OSError('controlled acknowledgement failure')
            return original_sync(folder)
        store._sync_directory=fail_once
        import pytest
        from orchestrator.scientific_saves import SaveUnavailable
        with pytest.raises(SaveUnavailable):save.publish()
        reply,_=worker.request({'op':'python-resources','source':'points.data=points.data+100'})
        assert not reply['console']['error']
        first=save.publish();assert save.publish()==first
        output=store.read(save.target,'s1');save.close()
        reply,_=worker.request({'op':'python-resources','source':'print(keep_private)'})
        assert reply['console']['stdout']=='314\n'
    assert worker.process.poll() is not None and not worker.work.exists()
    # Reconstruct through the production retained-resource resolver after store restart.
    store=ProjectStore(tmp_path/'retained')
    with review(store,output.id,'s1',1) as (_,worker):
        inventory,_=worker.request({'op':'layer-inventory'});layer=inventory['inventory']['layers'][0]
        assert 'editableInput' not in layer  # array-only Save cannot pretend to save changed features
        assert worker.describe_output(layer['id'])==expected
        reply,_=worker.request({'op':'python-resources','source':'viewer.dims.ndisplay=3\nviewer.reset_view()'})
        assert not reply['console']['error'],reply['console']['error']
        _,reopened_pixels=worker.request({'op':'render','width':256,'height':192})
        assert reopened_pixels==pixels
        assertion="assert viewer.layers[0].face_color_mode=='cycle'" if family=='points' else "assert viewer.layers[0].graph=={2:[1],3:[1]}\nassert viewer.layers[0].display_graph and viewer.layers[0].display_id"
        if family=='vectors':assertion="""layer=viewer.layers[0]
assert layer.edge_color_mode=='cycle' and layer.length==2.5
events=[]
layer.events.edge_color_mode.connect(lambda event: events.append(event.type))
layer.edge_color_mode='direct'
layer.edge_color_mode='cycle'
from napari.layers import Vectors
reference=Vectors(layer.data,features=layer.features,edge_color='class')
reference_events=[]
reference.events.edge_color_mode.connect(lambda event: reference_events.append(event.type))
reference.edge_color_mode='direct'
reference.edge_color_mode='cycle'
assert len(reference_events)==2 and events==reference_events,(events,reference_events)
"""
        reply,_=worker.request({'op':'python-resources','source':"assert 'keep_private' not in globals()\n"+assertion})
        assert not reply['console']['error'],reply['console']['error']
        if family in ('vectors','tracks'):
            from env_worker import scientific_view_preferences as views
            current,_=worker.request({'op':'render','width':256,'height':192})
            before=current['frame'];appearance=dict(before['layers'][0]['pathAppearance'])
            appearance.update({'length':4.25,'width':5.,'style':'triangle'} if family=='vectors' else
                              {'width':7.,'tail':1,'head':0,'showGraph':False,'hideCompleted':True})
            shown,display_pixels=worker.request({'op':'change','width':256,'height':192,'request':{'frame':before['frame'],
                'operation':'path-presentation','value':{'layer':before['layers'][0]['id'],'settings':appearance}}})
            remembered=views.from_frame(shown['frame']);assert remembered['schema']==12
            assert remembered['dimensions']['margins']=={'left':[0,0,0],'right':[0,0,0]}
    assert worker.process.poll() is not None
    if family in ('vectors','tracks'):
        with review(store,output.id,'s1',1) as (_,worker):
            current,_=worker.request({'op':'render','width':256,'height':192})
            # Wrong remembered ranges must refuse before any presentation writes.
            import copy
            wrong=copy.deepcopy(remembered);wrong['dimensions']['ranges'][0][1]+=1
            from orchestrator.scientific_host import RestoreRefused
            with pytest.raises(RestoreRefused):
                worker.request({'op':'change','width':256,'height':192,'request':{'frame':current['frame']['frame'],'operation':'restore-view','value':wrong}})
            shown,restored_pixels=worker.request({'op':'change','width':256,'height':192,'request':{'frame':current['frame']['frame'],'operation':'restore-view','value':remembered}})
            assert restored_pixels==display_pixels and views.restored(remembered,views.from_frame(shown['frame']))
            assert worker.describe_output(shown['frame']['layers'][0]['id'])['shape']==expected['shape']
    assert store.read(saved.id,'s1')==saved
    source=store.array_source(output.id,'s1',1,output.document['resources'][0])
    assert source.axes=={'points':('object','coordinate'),'tracks':('observation','field'),'vectors':('vector','component','coordinate')}[family]
    if family=='vectors':
        columns=source.descriptor['coordinate_columns']
        assert columns['components']=={'axis':'component','origin':0,'displacement':1}
        assert columns['calibration']['affine']==[[2,0,0,10],[0,.5,0,20],[0,0,.25,30]]
        from types import SimpleNamespace
        from env_worker.scientific_output_wire import retained
        altered=source.descriptor
        altered['coordinate_columns']['components']={'axis':'component','origin':1,'displacement':0}
        with pytest.raises(ValueError,match='origins and displacements'):
            retained(expected,SimpleNamespace(shape=source.shape,dtype=source.dtype,axes=source.axes,descriptor=altered))
    assert source.descriptor['semantic']==semantic
    assert source.descriptor['coordinate_columns']['calibration']['axes']==axes
    retained_data=source.read(tuple(slice(0,n) for n in expected_data.shape))
    assert retained_data.dtype.str==expected_data.dtype.str and retained_data.tobytes()==expected_data.tobytes()
    original=store.array_source(saved.id,'s1',saved.revision,ref)
    assert original.read(tuple(slice(0,n) for n in data.shape)).tobytes()==data.tobytes()
