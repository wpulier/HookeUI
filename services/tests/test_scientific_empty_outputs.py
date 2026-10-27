"""Actual confined empty-result save/reopen; no model or clean-machine claims."""
import pytest
from orchestrator.scientific_outputs import OutputSave
from orchestrator.scientific_projects import ProjectStore
from orchestrator.scientific_project_review import review
from tests.test_scientific_surface_output import source
from tests.test_scientific_host_worker import actual_host  # noqa: F401

@pytest.mark.parametrize('kind,shape,axes,semantic',[
    ('points',(0,3),['z','y','x'],'point-set'),
    ('vectors',(0,2,3),['z','y','x'],'vector-set'),
])
def test_actual_empty_result_is_retained_with_exact_schema_and_reopens(tmp_path,actual_host,kind,shape,axes,semantic):
    store=ProjectStore(tmp_path/'retained');original=source(store,actual_host)
    with review(store,original.id,'s1',original.revision) as (_,worker):
        code=f"""import numpy as np
import pandas as pd
viewer.layers.clear()
empty=viewer.add_{kind}(np.empty({shape!r},dtype='<f8'),name='No detections',features=pd.DataFrame({{'id':np.array([],dtype='uint64')}}))
assert empty.data.shape=={shape!r}
creating_variable=42
"""
        result,_=worker.request({'op':'python-resources','source':code})
        assert not result['console']['error'],result['console']['error']
        inventory,_=worker.request({'op':'layer-inventory'});layer=inventory['inventory']['layers'][0]
        expected=worker.describe_output(layer['id']);assert expected['shape']==list(shape)
        save=OutputSave(store,original,'empty-result-123456789',layer['id'],{'semantic':semantic,'axes':axes})
        try:save.capture(worker);save.publish();target=save.target;saved=store.read(target,'s1')
        finally:save.close()
    assert store.read(original.id,'s1')==original
    native=store.array_source(target,'s1',1,saved.document['resources'][0])
    assert native.shape==shape and native.dtype=='<f8'
    assert not list((store._folder(target)/'arrays').glob('*.bin'))
    with review(store,target,'s1',1) as (_,worker):
        result,_=worker.request({'op':'python-resources','source':f"""assert 'creating_variable' not in globals()
layer=viewer.layers[0]
assert layer.data.shape=={shape!r} and layer.data.dtype.str=='<f8'
assert str(layer.features['id'].dtype)=='uint64' and len(layer.features)==0
"""})
        assert not result['console']['error'],result['console']['error']
        frame,pixels=worker.request({'op':'render','width':256,'height':192});assert pixels
        assert worker.describe_output(frame['frame']['layers'][0]['id'])==expected


def test_reference_empty_tracks_refusal_keeps_the_host_and_accepted_source_usable(tmp_path,actual_host):
    store=ProjectStore(tmp_path/'retained');original=source(store,actual_host)
    with review(store,original.id,'s1',original.revision) as (_,worker):
        reply,_=worker.request({'op':'python-resources','source':"import numpy as np\nviewer.add_tracks(np.empty((0,4)))"})
        assert 'IndexError' in reply['console']['error'] and 'size 0' in reply['console']['error']
        # An upstream constructor failure must not take down the private kernel,
        # replace the source, or prevent a corrected, nonempty operation.
        reply,_=worker.request({'op':'python-resources','source':"assert len(viewer.layers)==1\nviewer.add_tracks(np.array([[1,0,1,2],[1,1,2,3]],dtype=float),name='Corrected tracks')"})
        assert not reply['console']['error'],reply['console']['error']
        frame,pixels=worker.request({'op':'render','width':256,'height':192})
        assert pixels and len(frame['frame']['layers'])==2
    assert store.read(original.id,'s1')==original


@pytest.mark.parametrize('nonempty',[False,True])
@pytest.mark.parametrize('shapes',[False,True])
def test_actual_empty_layers_save_together_without_missing_chunk_or_identity_loss(tmp_path,actual_host,nonempty,shapes):
    store=ProjectStore(tmp_path/'retained');original=source(store,actual_host)
    with review(store,original.id,'s1',original.revision) as (_,worker):
        code="""import numpy as np
viewer.layers.clear()
viewer.add_points(np.empty((0,3)),name='Empty points')
viewer.add_vectors(np.empty((0,2,3)),name='Empty vectors')
"""
        if shapes:code+="viewer.add_shapes(ndim=3,name='Empty regions')\n"
        if nonempty:code+="viewer.add_image(np.arange(6,dtype='>u2').reshape(2,3),name='Nonempty image')"
        reply,_=worker.request({'op':'python-resources','source':code})
        assert not reply['console']['error'],reply['console']['error']
        inventory,_=worker.request({'op':'layer-inventory'})
        meanings={'points':{'semantic':'point-set','axes':['z','y','x']},'vectors':{'semantic':'vector-set','axes':['z','y','x']},
                  'image':{'semantic':'intensity','axes':['y','x']},'shapes':{'semantic':'shape-set','axes':['z','y','x']}}
        selections=[{'layer':row['id'],'meaning':meanings[row['type']]} for row in inventory['inventory']['layers']]
        expected=[worker.describe_output(row['layer']) for row in selections]
        save=OutputSave(store,original,'empty-set-123456789',selections[0]['layer'],selections[0]['meaning'],selections=selections)
        try:save.capture(worker);save.publish();target=save.target
        finally:save.close()
    with review(store,target,'s1',1) as (_,worker):
        frame,pixels=worker.request({'op':'render','width':256,'height':192});assert pixels
        assert [worker.describe_output(row['id']) for row in frame['frame']['layers'] if row['type']!='image']==[item for item in expected if item['type']!='image']
        if nonempty:
            result,_=worker.request({'op':'python-resources','source':"import numpy as np\nimage=viewer.layers['Nonempty image'].data\nassert image.dtype.str=='>u2' and np.array_equal(image,np.arange(6).reshape(2,3))"})
            assert not result['console']['error'],result['console']['error']
    assert store.read(original.id,'s1')==original
