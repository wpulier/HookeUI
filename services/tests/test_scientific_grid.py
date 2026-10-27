"""Grid presentation preserves native layers and refuses unrenderable layouts."""
import copy
import json
from pathlib import Path
import pytest
from env_worker import scientific_grid as grid, scientific_host, scientific_view_preferences as views
from tests.test_scientific_change_recovery import host
from tests.test_scientific_host_worker import actual_host  # noqa: F401

CASES=json.loads((Path(__file__).resolve().parents[2]/'contract/scientific-grid-cases.json').read_text())

@pytest.mark.parametrize('value',CASES['valid'])
def test_grid_settings_roundtrip(value):
 result=grid.settings(value);assert result==value and result is not value
 result['shape'][0]=99;assert value['shape'][0]!=99

@pytest.mark.parametrize('value',CASES['invalid'])
def test_grid_refused_before_mutation(value):
 session=host();session._size=(320,240)
 with pytest.raises(scientific_host.ChangeRefused):
  session.change({'frame':'current','operation':'grid','value':value})
 assert session._frame=='current' and not session.dirty

@pytest.mark.parametrize('value',[float('nan'),float('inf'),10**1000])
def test_nonfinite_grid_spacing_refused(value):
 with pytest.raises(ValueError):grid.settings({**CASES['valid'][0],'spacing':value})

def test_grid_preferences_and_legacy_do_not_invent_grid_intent():
 from tests.test_scientific_view_preferences import FRAME,CASES as VIEWS
 frame=copy.deepcopy(FRAME)
 frame['layers'][0].update(viewKey='raw',viewGeometry=VIEWS['valid']['layers'][0]['geometry'])
 legacy=views.from_frame(frame);frame['grid']=CASES['valid'][1]
 modern=views.from_frame(frame);assert modern['schema']==12 and modern['grid']==frame['grid']
 assert views.compatible(legacy,frame)==modern
 with pytest.raises(ValueError):views.preferences({**modern,'schema':11})
 del frame['grid']
 with pytest.raises(ValueError,match='grid'):views.compatible(modern,frame)

def test_actual_grid_render_grouping_and_reopen(actual_host):
 import numpy as np
 from orchestrator import scientific_host as parent
 from tests.test_native_regions import source
 from env_worker.scientific_output_wire import calibration
 data=np.arange(6*24*28,dtype='<u2').reshape(6,24,28)
 metadata={'format':'native-layer-output-v1','shape':list(data.shape),'dtype':'<u2','name':'lower','type':'image','visible':True,'opacity':1.,
  'geometry':{'basis':[[0,0,0],[3,0,0],[0,2,0],[0,0,.5]],'axisLabels':['z','y','x'],'units':['micrometer']*3}}
 def launch():
  sources={};layers=[]
  for index,name in enumerate(['lower','middle','upper']):
   raw=source(data+index*100,[])
   info={**metadata,'name':name}
   raw.descriptor.update(semantic='intensity',calibration=calibration(info,raw.axes,{'session':'s1','id':name,'revision':1}))
   sources[name]=raw;layers.append({'input':name,'metadata':info})
  return parent.review(actual_host['key'],Path(actual_host['folder']).name,sources,retained_layers=layers)
 with launch() as worker:
  result,_=worker.request({'op':'python-resources','source':"import numpy as np\noriginals=[layer.data.copy() for layer in viewer.layers]\ncoordinates=[layer.data_to_world((1,2,3)) for layer in viewer.layers]\nfor layer,color in zip(viewer.layers,('red','green','blue')): layer.colormap=color\nviewer.reset_view()"});assert not result['console']['error'],result
  worker.request({'op':'render','width':320,'height':240})
  worker.request({'op':'python-resources','source':'viewer.reset_view()'})
  current,overlaid=worker.request({'op':'render','width':320,'height':240});frame=current['frame']
  impossible={'enabled':True,'shape':[1000000,1000000],'stride':1,'spacing':0}
  with pytest.raises(parent.ChangeRefused,match='20 pixels'):
   worker.request({'op':'change','width':320,'height':240,'request':{'frame':frame['frame'],'operation':'grid','value':impossible}})
  assert frame['grid']==CASES['valid'][0]
  worker.request({'op':'python-resources','source':'for layer in viewer.layers: layer.visible=False'})
  current,_=worker.request({'op':'render','width':320,'height':240});frame=current['frame']
  with pytest.raises(parent.ChangeRefused,match='20 pixels'):
   worker.request({'op':'change','width':320,'height':240,'request':{'frame':frame['frame'],'operation':'grid','value':impossible}})
  result,_=worker.request({'op':'python-resources','source':"assert not viewer.grid.enabled and viewer.grid.shape==(-1,-1)\nfor layer in viewer.layers: layer.visible=True"});assert not result['console']['error'],result
  current,_=worker.request({'op':'render','width':320,'height':240});frame=current['frame']
  for settings,positions in [
   ({'enabled':True,'shape':[1,-1],'stride':1,'spacing':.1},[[0,0],[0,1],[0,2]]),
   ({'enabled':True,'shape':[1,-1],'stride':-1,'spacing':8},[[0,2],[0,1],[0,0]]),
   ({'enabled':True,'shape':[-1,1],'stride':2,'spacing':0},[[0,0],[0,0],[1,0]])]:
   old=frame['frame']
   changed,pixels=worker.request({'op':'change','width':320,'height':240,'request':{'frame':old,'operation':'grid','value':settings}})
   assert changed['frame']['grid']==settings and pixels!=overlaid and len(set(pixels))>20
   with pytest.raises(parent.ChangeRefused):worker.request({'op':'change','width':320,'height':240,'request':{'frame':old,'operation':'grid','value':settings}})
   code=f"assert [list(viewer.grid.position(i,viewer.layers)) for i in range(3)]=={positions!r}\nviewer.grid.enabled=False\nviewer.grid.shape={tuple(settings['shape'])!r}\nviewer.grid.stride={settings['stride']}\nviewer.grid.spacing={settings['spacing']}\nviewer.grid.enabled=True\nassert all(np.array_equal(layer.data,data) for layer,data in zip(viewer.layers,originals))\nassert [layer.data_to_world((1,2,3)) for layer in viewer.layers]==coordinates"
   reply,_=worker.request({'op':'python-resources','source':code});assert not reply['console']['error'],reply
   current,reference=worker.request({'op':'render','width':320,'height':240});assert pixels==reference;frame=current['frame']
  large={'enabled':True,'shape':[1,3],'stride':1,'spacing':1500}
  changed,_=worker.request({'op':'change','width':320,'height':240,'request':{'frame':frame['frame'],'operation':'grid','value':large}})
  assert changed['frame']['grid']['spacing']==130
  current,pixels=worker.request({'op':'render','width':160,'height':120});frame=current['frame']
  assert frame['grid']['spacing']==50 and len(set(pixels))>20
  result,_=worker.request({'op':'python-resources','source':'assert viewer.grid.spacing==50'});assert not result['console']['error'],result
  current,reference=worker.request({'op':'render','width':160,'height':120});assert pixels==reference
  saved=views.from_frame(current['frame']);assert saved['schema']==12
  # A visibility change can grow an automatic grid; refuse it before events.
  worker.request({'op':'python-resources','source':'viewer.grid.enabled=False\nviewer.layers[2].visible=False\nprivate_work=73'})
  current,_=worker.request({'op':'render','width':40,'height':40})
  small={'enabled':True,'shape':[1,-1],'stride':1,'spacing':0}
  changed,_=worker.request({'op':'change','width':40,'height':40,'request':{'frame':current['frame']['frame'],'operation':'grid','value':small}})
  shown=changed['frame'];layer=shown['layers'][2]
  with pytest.raises(parent.ChangeRefused,match='20 pixels'):
   worker.request({'op':'change','width':40,'height':40,'request':{'frame':shown['frame'],'operation':'layer-presentation','value':{'layer':layer['id'],'visible':True,'opacity':1.}}})
  changed,_=worker.request({'op':'change','width':40,'height':40,'request':{'frame':shown['frame'],'operation':'grid','value':{**small,'stride':2}}})
  worker.request({'op':'change','width':40,'height':40,'request':{'frame':changed['frame']['frame'],'operation':'layer-presentation','value':{'layer':layer['id'],'visible':True,'opacity':1.}}})
  result,_=worker.request({'op':'python-resources','source':'assert private_work==73 and all(layer.visible for layer in viewer.layers)'});assert not result['console']['error'],result
 with launch() as worker:
  current,_=worker.request({'op':'render','width':160,'height':120})
  changed,reopened=worker.request({'op':'change','width':160,'height':120,'request':{'frame':current['frame']['frame'],'operation':'restore-view','value':saved}})
  assert views.restored(saved,views.from_frame(changed['frame'])) and reopened==pixels

def test_explicit_grid_bounds_apply_even_when_all_layers_are_hidden():
 from types import SimpleNamespace
 class Grid:
  def __init__(self,**kwargs):pass
  def actual_shape(self,layers):return (1,1)
 viewer=SimpleNamespace(grid=Grid(),layers=[])
 with pytest.raises(ValueError,match='20 pixels'):
  grid.admit(viewer,{'enabled':True,'shape':[1000000,1000000],'stride':1,'spacing':0},(320,240))

@pytest.mark.parametrize('dimension',[2,3])
def test_actual_mixed_grid_saved_scene_reopens_without_creator_replay(tmp_path,actual_host,dimension):
 from orchestrator.scientific_outputs import OutputSave
 from orchestrator.scientific_projects import ProjectStore
 from orchestrator.scientific_project_review import review
 from tests.test_scientific_surface_output import source, CREATE
 store=ProjectStore(tmp_path/'retained');original=source(store,actual_host)
 with review(store,original.id,'s1',original.revision) as (_,worker):
  code=CREATE+"""
labels=viewer.add_labels(np.arange(60,dtype='>u2').reshape(3,4,5),name='Instances',scale=[2,.5,.25],translate=[10,20,30])
points=viewer.add_points(np.array([[1,2,3],[2,3,4]],dtype='<f8'),name='Landmarks',
    features=pd.DataFrame({'id':np.array([2**63,2**63+1],dtype='uint64')}),scale=[2,.5,.25],translate=[10,20,30])
"""
  result,_=worker.request({'op':'python-resources','source':code});assert not result['console']['error'],result
  inventory,_=worker.request({'op':'layer-inventory'});meanings={'surface':'surface-mesh','labels':'instance-labels','points':'point-set'}
  selections=[{'layer':row['id'],'meaning':{'semantic':meanings[row['type']],'axes':['z','y','x']}} for row in inventory['inventory']['layers']]
  save=OutputSave(store,original,'grid-mixed-request',selections[0]['layer'],selections[0]['meaning'],selections=selections)
  save.capture(worker);save.publish();target=save.target;save.close()
 accepted=store.read(target,'s1')
 with review(store,target,'s1',1) as (_,worker):
  result,_=worker.request({'op':'python-resources','source':f'assert "creating_variable" not in globals()\nviewer.dims.ndisplay={dimension}\nviewer.dims.point=(12,21,30.75)\nviewer.reset_view()'});assert not result['console']['error'],result
  worker.request({'op':'render','width':320,'height':240})
  worker.request({'op':'python-resources','source':'viewer.reset_view()'})
  current,_=worker.request({'op':'render','width':320,'height':240})
  settings={'enabled':True,'shape':[1,3],'stride':1,'spacing':5}
  changed,pixels=worker.request({'op':'change','width':320,'height':240,'request':{'frame':current['frame']['frame'],'operation':'grid','value':settings}})
  assert len(set(pixels))>20
  result,_=worker.request({'op':'python-resources','source':"viewer.grid.enabled=False\nviewer.grid.shape=(1,3)\nviewer.grid.stride=1\nviewer.grid.spacing=5\nviewer.grid.enabled=True"});assert not result['console']['error'],result
  current,native=worker.request({'op':'render','width':320,'height':240});assert pixels==native
  saved=views.from_frame(current['frame'])
 with review(ProjectStore(tmp_path/'retained'),target,'s1',1) as (_,worker):
  current,_=worker.request({'op':'render','width':320,'height':240})
  changed,pixels=worker.request({'op':'change','width':320,'height':240,'request':{'frame':current['frame']['frame'],'operation':'restore-view','value':saved}})
  assert views.restored(saved,views.from_frame(changed['frame'])) and pixels==native
  result,_=worker.request({'op':'python-resources','source':"""import numpy as np
assert 'creating_variable' not in globals()
mesh,labels,points=viewer.layers
assert np.array_equal(mesh.data[0],[[0,0,0],[0,5,0],[0,0,5],[5,0,0]])
assert mesh.data[0].dtype.str=='>f8'
assert np.array_equal(labels.data,np.arange(60).reshape(3,4,5)) and labels.data.dtype.str=='>u2'
assert np.array_equal(points.data,[[1,2,3],[2,3,4]])
assert list(points.features['id'])==[2**63,2**63+1]
"""});assert not result['console']['error'],result
 assert store.read(original.id,'s1')==original and store.read(target,'s1')==accepted
