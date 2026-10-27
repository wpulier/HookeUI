"""Surface controls use real native setters and preserve mesh data."""
import copy
from types import SimpleNamespace
import pytest
from env_worker import scientific_host, scientific_surface_appearance as surfaces, scientific_view_preferences as views
from tests.test_scientific_change_recovery import host
from tests.test_scientific_host_worker import actual_host  # noqa: F401

DISPLAY={'shading':'smooth','contrastLimits':[1.,4.],'gamma':1.,'autoContrast':False,
 'wireframe':{'visible':True,'width':1.5,'color':[1.,0.,0.,1.]},
 'faceNormals':{'visible':True,'width':2.,'length':3.,'color':[0.,1.,0.,1.]},
 'vertexNormals':{'visible':False,'width':1.,'length':5.,'color':[0.,0.,1.,1.]}}

@pytest.mark.parametrize('patch',[{'gamma':0},{'gamma':True},{'gamma':float('inf')},{'gamma':10**400},{'contrastLimits':[2.,2.]},{'shading':'wrong'},
 {'contrastLimits':[5,1]},{'contrastLimits':[True,4]},{'autoContrast':1},
 {'wireframe':{'visible':True,'width':-1,'color':[1,0,0,1]}},
 {'faceNormals':{'visible':True,'width':1,'length':float('nan'),'color':[0,0,0,1]}},
 {'vertexNormals':{'visible':True,'width':1,'length':1,'color':[0,0,0,2]}}])
def test_surface_refusal_preserves_current_frame_and_layer(patch):
 session=host();layer=SimpleNamespace(_type_string='surface');session._layers=[('mesh',layer)]
 with pytest.raises(scientific_host.ChangeRefused):
  session.change({'frame':'current','operation':'surface-presentation','value':{'layer':'mesh','settings':{**DISPLAY,**patch}}})
 assert vars(layer)=={'_type_string':'surface'} and session._frame=='current' and not session.dirty


def test_surface_preferences_keep_legacy_presentation():
 from tests.test_scientific_view_preferences import FRAME,CASES
 frame=copy.deepcopy(FRAME);row=frame['layers'][0]
 row.update(type='surface',viewKey='raw',viewGeometry=CASES['valid']['layers'][0]['geometry'],surfaceAppearance=DISPLAY)
 saved=views.from_frame(frame);assert saved['schema']==9
 old=copy.deepcopy(saved);old['schema']=1;del old['layers'][0]['surfaceAppearance']
 assert views.compatible(old,frame)['layers'][0]['surfaceAppearance']==DISPLAY
 with pytest.raises(ValueError):views.preferences({**saved,'schema':8})
 del row['surfaceAppearance']
 with pytest.raises(ValueError,match='surface display'):views.compatible(saved,frame)


@pytest.mark.parametrize('large_values',[False,True])
def test_actual_surface_controls_match_native_and_reopen(actual_host,tmp_path,large_values):
 from orchestrator.scientific_projects import ProjectStore
 from orchestrator.scientific_project_review import review
 from orchestrator.scientific_outputs import OutputSave
 from tests.test_scientific_surface_output import source,CREATE
 store=ProjectStore(tmp_path/'data');original=source(store,actual_host)
 with review(store,original.id,'s1',original.revision) as (_,worker):
  result,_=worker.request({'op':'python-resources','source':CREATE+("\nmesh.vertex_values=mesh.vertex_values.astype('float64')*1e19\nmesh.contrast_limits=[1e19,4e19]" if large_values else '')+"\noriginal_bytes=[a.tobytes() for a in mesh.data]"})
  assert not result['console']['error'],result
  before,_=worker.request({'op':'render','width':256,'height':192});frame=before['frame']
  desired=copy.deepcopy(DISPLAY);desired.update(shading='flat',gamma=1.5,contrastLimits=[1e19,5e19] if large_values else [0.,5.])
  changed,pixels=worker.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],
   'operation':'surface-presentation','value':{'layer':frame['layers'][0]['id'],'settings':desired}}})
  assert changed['frame']['layers'][0]['surfaceAppearance']==desired
  reply,_=worker.request({'op':'python-resources','source':NESTED_EVENTS})
  assert not reply['console']['error'],reply
  direct="mesh.shading='smooth'\nmesh.gamma=2\n"
  direct+='\n'.join(f"mesh.{native}={desired[key]!r}" for key,native in [('shading','shading'),('gamma','gamma'),('autoContrast','auto_contrast'),('contrastLimits','contrast_limits')])
  for key,path in [('wireframe','wireframe'),('faceNormals','normals.face'),('vertexNormals','normals.vertex')]:
   direct+='\n'+'\n'.join(f'mesh.{path}.{field}={value!r}' for field,value in desired[key].items())
  result,_=worker.request({'op':'python-resources','source':direct+"\nassert [a.tobytes() for a in mesh.data]==original_bytes"})
  assert not result['console']['error'],result
  native,reference=worker.request({'op':'render','width':256,'height':192});assert pixels==reference
  save=OutputSave(store,original,'surface-display-123456789',frame['layers'][0]['id'],{'semantic':'surface-mesh','axes':['z','y','x']})
  save.capture(worker);save.publish();saved=store.read(save.target,'s1');save.close()
 with review(store,saved.id,'s1',1) as (_,worker):
  reopened,_=worker.request({'op':'render','width':256,'height':192})
  assert reopened['frame']['layers'][0]['surfaceAppearance']==desired
  preferences=views.from_frame(reopened['frame'])
  altered=copy.deepcopy(preferences);altered['layers'][0]['surfaceAppearance']['shading']='none'
  altered['layers'][0]['surfaceAppearance']['vertexNormals']['visible']=True
  changed,_=worker.request({'op':'change','width':256,'height':192,'request':{'frame':reopened['frame']['frame'],'operation':'restore-view','value':altered}})
  assert views.restored(altered,views.from_frame(changed['frame']))
  # Python mutations also report their native nested settings in the next frame.
  result,_=worker.request({'op':'python-resources','source':"viewer.layers[0].normals.face.width=4"});assert not result['console']['error']
  current,_=worker.request({'op':'render','width':256,'height':192})
  assert current['frame']['layers'][0]['surfaceAppearance']['faceNormals']['width']==4
  automatic=copy.deepcopy(current['frame']['layers'][0]['surfaceAppearance']);automatic['autoContrast']=True
  auto,auto_pixels=worker.request({'op':'change','width':256,'height':192,'request':{'frame':current['frame']['frame'],
   'operation':'surface-presentation','value':{'layer':current['frame']['layers'][0]['id'],'settings':automatic}}})
  reply,_=worker.request({'op':'python-resources','source':"viewer.layers[0].auto_contrast=False\nviewer.layers[0].contrast_limits=[0,5]\nviewer.layers[0].auto_contrast=True"})
  assert not reply['console']['error'],reply
  native,native_pixels=worker.request({'op':'render','width':256,'height':192})
  assert auto_pixels==native_pixels
  assert auto['frame']['layers'][0]['surfaceAppearance']==native['frame']['layers'][0]['surfaceAppearance']
 assert store.read(original.id,'s1')==original


def test_surface_intensity_controls_do_not_use_integer_identity_range():
 value=copy.deepcopy(DISPLAY);value['contrastLimits']=[1e19,2e19]
 assert surfaces.settings(value)==value


NESTED_EVENTS="""import threading
from types import SimpleNamespace
from env_worker.scientific_host import ReviewSession, ChangeRefused
from env_worker.scientific_surface_appearance import events
probe=object.__new__(ReviewSession)
probe._closed=False;probe._thread=threading.get_ident();probe._connections=[]
probe.editable=probe.view_bindings=None;probe.panel=SimpleNamespace(invalidate=lambda:None)
probe.dirty=False;probe._layers=[]
try:
    for event in events(mesh):probe._connect(event)
    for target in [mesh.wireframe,mesh.normals.face,mesh.normals.vertex]:
        old=target.width;probe._frame='accepted'
        target.width=old+1
        assert probe._frame is None
        try:
            probe.change({'frame':'accepted','operation':'surface-presentation','value':{}})
        except ChangeRefused:pass
        else:raise AssertionError('A nested event left a stale frame authoritative')
        target.width=old
finally:
    for event in probe._connections:event.disconnect(probe._invalidate)
"""


import json
from pathlib import Path
CASES=json.loads((Path(__file__).resolve().parents[2]/'contract/scientific-surface-appearance-cases.json').read_text())

@pytest.mark.parametrize('case',CASES['invalid'],ids=lambda case:case['name'])
def test_shared_surface_contract_rejects_in_frame(case):
 from env_worker.scientific_view_wire import frame as validate
 from tests.test_scientific_view_preferences import FRAME
 value={**CASES['valid'],**case['patch']}
 with pytest.raises(ValueError):surfaces.settings(value)
 frame=copy.deepcopy(FRAME);frame['layers'][0].update(type='surface',surfaceAppearance=value)
 with pytest.raises(ValueError):validate(frame)


def test_malformed_surface_settings_refuse_before_acceptance(actual_host,tmp_path,monkeypatch):
 from orchestrator.scientific_projects import ProjectStore
 from orchestrator.scientific_project_review import review
 from orchestrator.scientific_outputs import OutputSave
 from orchestrator.scientific_saves import SaveNotStarted
 from env_worker import scientific_structured_state as state
 from tests.test_scientific_surface_output import source,CREATE
 store=ProjectStore(tmp_path/'data');saved=source(store,actual_host)
 with review(store,saved.id,'s1',saved.revision) as (_,worker):
  result,_=worker.request({'op':'python-resources','source':CREATE});assert not result['console']['error']
  inventory,_=worker.request({'op':'layer-inventory'});layer=inventory['inventory']['layers'][0]
  expected=worker.describe_output(layer['id'])
  for key,value in [('shading','wrong'),('colormap','invalid'),('gamma',0),('contrast_limits',[2,1]),('wireframe',{'visible':False}),('normals',{'face':{}})]:
   info=copy.deepcopy(expected);info['state']['state']['value'][key]=state.pack(value)['state']
   save=OutputSave(store,saved,'surface-invalid-'+key,layer['id'],{'semantic':'surface-mesh','axes':['z','y','x']})
   with monkeypatch.context() as patch:
    patch.setattr(worker,'describe_output',lambda _:info)
    with pytest.raises(SaveNotStarted):save.capture(worker)
   assert worker.process.poll() is None and store.read(saved.id,'s1')==saved
   assert not store._folder(save.target).exists() and not list(store.root.glob('.scientific-output-*'))
  save=OutputSave(store,saved,'surface-corrected-123456',layer['id'],{'semantic':'surface-mesh','axes':['z','y','x']})
  save.capture(worker);save.publish();save.close()
  assert store.read(save.target,'s1').revision==1
