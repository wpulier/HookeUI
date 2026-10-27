"""Actual confined mixed-scene capture and restoration, without creator replay."""
import pytest
from orchestrator.scientific_outputs import OutputSave
from orchestrator.scientific_projects import ProjectStore
from orchestrator.scientific_project_review import review
from tests.test_scientific_surface_output import source, CREATE
from tests.test_scientific_host_worker import actual_host  # noqa: F401


def test_actual_rgb_rgba_and_three_wide_scalar_roundtrip(tmp_path,actual_host):
    store=ProjectStore(tmp_path/'retained'); original=source(store,actual_host)
    with review(store,original.id,'s1',original.revision) as (_,worker):
        reply,_=worker.request({'op':'python-resources','source':"""import numpy as np
viewer.layers.clear()
for components in [3,4]:
    viewer.add_image(np.arange(5*7*components,dtype='uint8').reshape(5,7,components),
        rgb=True,name=f'Color {components}',scale=[2,3],translate=[4,9],axis_labels=['y','x'])
viewer.add_image(np.arange(5*7*3,dtype='uint16').reshape(5,7,3),rgb=False,name='Scalar',axis_labels=['z','y','x'])
"""})
        assert not reply['console']['error'],reply['console']['error']
        inventory,_=worker.request({'op':'layer-inventory'})
        selections=[{'layer':row['id'],'meaning':{'semantic':'intensity','axes':['y','x'] if index<2 else ['z','y','x']}}
                    for index,row in enumerate(inventory['inventory']['layers'])]
        save=OutputSave(store,original,'rgb-native-roundtrip',selections[0]['layer'],selections[0]['meaning'],selections=selections)
        save.capture(worker);save.publish();target=save.target;save.close()
    with review(store,target,'s1',1) as (_,worker):
        reply,_=worker.request({'op':'python-resources','source':"""import numpy as np
for layer,components in zip(viewer.layers[:2],[3,4]):
    assert layer.rgb and layer.ndim==2 and layer.data.dtype==np.uint8
    assert np.array_equal(layer.data,np.arange(5*7*components,dtype='uint8').reshape(5,7,components))
    assert np.array_equal(layer.data_to_world([1,1]),[6,12])
assert not viewer.layers[2].rgb and viewer.layers[2].ndim==3
assert np.array_equal(viewer.layers[2].data,np.arange(105,dtype='uint16').reshape(5,7,3))
"""})
        assert not reply['console']['error'],reply['console']['error']
        inventory,_=worker.request({'op':'layer-inventory'})
        assert inventory['inventory']['layers'][0]['inputMeaning']['axes']==['y','x']
        frame,pixels=worker.request({'op':'render','width':256,'height':192})
        assert len(frame['frame']['layers'])==3 and pixels
    assert store.read(original.id,'s1')==original


def test_actual_mesh_labels_and_points_save_as_one_scene_and_reopen(tmp_path,actual_host):
    store=ProjectStore(tmp_path/'retained'); original=source(store,actual_host)
    with review(store,original.id,'s1',original.revision) as (_,worker):
        code=CREATE+"""
labels=viewer.add_labels(np.arange(60,dtype='>u2').reshape(3,4,5),name='Instances')
points=viewer.add_points(np.array([[1,2,3],[2,3,4]],dtype='<f8'),name='Landmarks',
    features=pd.DataFrame({'id':np.array([2**63,2**63+1],dtype='uint64')}))
"""
        reply,_=worker.request({'op':'python-resources','source':code})
        assert not reply['console']['error'],reply['console']['error']
        inventory,_=worker.request({'op':'layer-inventory'})
        layers=inventory['inventory']['layers']
        meanings={'surface':'surface-mesh','labels':'instance-labels','points':'point-set'}
        selections=[{'layer':row['id'],'meaning':{'semantic':meanings[row['type']],'axes':['z','y','x']}} for row in layers]
        save=OutputSave(store,original,'mixed-native-request',selections[0]['layer'],selections[0]['meaning'],selections=selections)
        save.capture(worker);save.publish();target=save.target;save.close()
        # Publication does not discard unrelated private work or claim it was saved.
        reply,_=worker.request({'op':'python-resources','source':"assert creating_variable == 42\nmesh.data[0][:]=99\nlabels.data[:]=0"})
        assert not reply['console']['error'],reply['console']['error']
    with review(store,target,'s1',1) as (_,worker):
        reply,_=worker.request({'op':'python-resources','source':"""import numpy as np
assert 'creating_variable' not in globals()
assert [layer.name for layer in viewer.layers]==['Measured mesh','Instances','Landmarks']
mesh,labels,points=viewer.layers
assert mesh.data[0].dtype.str=='>f8'
assert np.array_equal(mesh.data[0],[[0,0,0],[0,5,0],[0,0,5],[5,0,0]])
assert mesh.data[1].dtype.str=='<i4' and mesh.data[2].dtype.str=='>f4'
assert labels.data.dtype.str=='>u2'
assert np.array_equal(labels.data,np.arange(60).reshape(3,4,5))
assert points.data.dtype.str=='<f8' and np.array_equal(points.data,[[1,2,3],[2,3,4]])
assert list(points.features['id'])==[2**63,2**63+1]
print('PASS: mixed scientific scene, native components and features reopened without replay')
"""})
        assert not reply['console']['error'],reply['console']['error']
        assert 'PASS:' in reply['console']['stdout']
        frame,pixels=worker.request({'op':'render','width':320,'height':240})
        assert len(frame['frame']['layers'])==3 and pixels
    assert store.read(original.id,'s1')==original


def test_actual_large_individual_states_refuse_combination_and_allow_correction(tmp_path,actual_host):
    from orchestrator.scientific_saves import SaveNotStarted
    store=ProjectStore(tmp_path/'retained');original=source(store,actual_host)
    with review(store,original.id,'s1',original.revision) as (_,worker):
        reply,_=worker.request({'op':'python-resources','source':"""import numpy as np
import pandas as pd
viewer.layers.clear()
for name in ['First','Second']:
    viewer.add_points(np.arange(4,dtype='float64').reshape(2,2),name=name,
        metadata={'note':'x'*45000},features=pd.DataFrame({'note':['short']*2}))
unrelated_private_work=73
"""})
        assert not reply['console']['error'],reply['console']['error']
        inventory,_=worker.request({'op':'layer-inventory'})
        selections=[{'layer':row['id'],'meaning':{'semantic':'point-set','axes':['y','x']}} for row in inventory['inventory']['layers']]
        import json
        metadata=[worker.describe_output(row['layer']) for row in selections]
        assert all(len(json.dumps(row).encode()) < 96 << 10 for row in metadata)
        assert len(json.dumps(metadata).encode()) > 128 << 10
        save=OutputSave(store,original,'large-set-request',selections[0]['layer'],selections[0]['meaning'],selections=selections)
        with pytest.raises(SaveNotStarted,match='metadata'):save.capture(worker)
        assert worker.process.poll() is None and not store._folder(save.target).exists()
        reply,_=worker.request({'op':'python-resources','source':"""assert unrelated_private_work==73
for layer in viewer.layers:
    layer.metadata={'note':'short'}
print('Private work survived metadata refusal')
"""})
        assert not reply['console']['error'],reply['console']['error']
        retry=OutputSave(store,original,'corrected-set-request',selections[0]['layer'],selections[0]['meaning'],selections=selections)
        retry.capture(worker);retry.publish();target=retry.target;retry.close()
    with review(store,target,'s1',1) as (_,worker):
        reply,_=worker.request({'op':'python-resources','source':"assert len(viewer.layers)==2\nassert all(layer.metadata=={'note':'short'} and set(layer.features['note'])=={'short'} for layer in viewer.layers)"})
        assert not reply['console']['error'],reply['console']['error']
