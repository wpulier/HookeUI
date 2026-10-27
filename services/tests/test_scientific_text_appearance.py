"""Native text controls preserve geometry, typed features and encoding identity."""
import copy
from types import SimpleNamespace
import pytest
from tests.test_scientific_change_recovery import host
from tests.test_scientific_host_worker import actual_host  # noqa: F401
from env_worker import scientific_host, scientific_view_preferences as views

DISPLAY={'visible':True,'size':18.5,'scaling':False,'blending':'translucent','anchor':'upper_left','translation':0,'rotation':25.5}


def test_text_controls_use_the_existing_native_manager_and_current_frame():
 session=host();text=SimpleNamespace(string=object(),color=object());layer=SimpleNamespace(_type_string='points',ndim=3,text=text)
 session._layers=[('points',layer)];string,color=text.string,text.color
 session.change({'frame':'current','operation':'text-presentation','value':{'layer':'points','settings':DISPLAY}})
 assert layer.text is text and text.string is string and text.color is color
 assert all(getattr(text,key)==value for key,value in DISPLAY.items())
 assert session._frame is None and session.dirty


@pytest.mark.parametrize('patch',[{'size':0},{'rotation':float('inf')},{'visible':1},{'scaling':None},{'anchor':'center-left'},
 {'translation':[1,2]},{'translation':[]},{'translation':[1,float('nan'),3]},{'blending':'opaque'},{'size':10**400},{'extra':True}])
def test_invalid_text_controls_preserve_current_frame_and_manager(patch):
 session=host();layer=SimpleNamespace(_type_string='shapes',ndim=3,text=SimpleNamespace(size=12));session._layers=[('shapes',layer)]
 with pytest.raises(scientific_host.ChangeRefused):
  session.change({'frame':'current','operation':'text-presentation','value':{'layer':'shapes','settings':{**DISPLAY,**patch}}})
 assert vars(layer.text)=={'size':12} and session._frame=='current' and not session.dirty


def test_text_preferences_keep_legacy_presentation_and_require_matching_support():
 from tests.test_scientific_view_preferences import FRAME,CASES
 frame=copy.deepcopy(FRAME);row=frame['layers'][0]
 row.update(type='points',viewKey='raw',viewGeometry=CASES['valid']['layers'][0]['geometry'],textAppearance=DISPLAY)
 saved=views.from_frame(frame);assert saved['schema']==13
 old=copy.deepcopy(saved);old['schema']=1;del old['layers'][0]['textAppearance']
 assert views.compatible(old,frame)['layers'][0]['textAppearance']==DISPLAY
 with pytest.raises(ValueError):views.preferences({**saved,'schema':12})
 del row['textAppearance']
 with pytest.raises(ValueError,match='feature text'):views.compatible(saved,frame)


@pytest.mark.parametrize('family',['points','shapes'])
@pytest.mark.parametrize('dimensions',[2,3])
def test_actual_feature_text_matches_native_preserves_ids_and_reopens(actual_host,tmp_path,family,dimensions):
 from orchestrator.scientific_projects import ProjectStore
 from orchestrator.scientific_project_review import review
 from orchestrator.scientific_outputs import OutputSave
 from orchestrator.scientific_host import ChangeRefused
 from tests.test_scientific_surface_output import source
 from tests.test_scientific_points_output import CREATE as POINTS
 from tests.test_scientific_shapes_output import CREATE as SHAPES
 store=ProjectStore(tmp_path/'data');original=source(store,actual_host)
 with review(store,original.id,'s1',original.revision) as (_,worker):
  create=POINTS if family=='points' else SHAPES
  if family=='shapes' and dimensions==3:
   create=create.replace('shapes=viewer.add_shapes',"rows=[np.column_stack([np.full(len(row),2,dtype='float32'),row]) for row in rows]\nshapes=viewer.add_shapes")
   create=create.replace("scale=[.5,.25],translate=[10,20],axis_labels=['y','x'],units=['um','um']","scale=[2,.5,.25],translate=[5,10,20],axis_labels=['z','y','x'],units=['um']*3")
  code=create+f'\nlayer={family}\n'
  code+='import copy\noriginal_data=copy.deepcopy(layer.data)\noriginal_features=layer.features.copy(deep=True)\noriginal_defaults=layer.feature_defaults.copy(deep=True)\noriginal_string=layer.text.string\noriginal_text_color=layer.text.color\noriginal_values=layer.text.values.copy()\noriginal_colors=layer.face_color.copy()\n'
  position='layer.data[0]' if family=='points' else 'layer.data[0][0]'
  code+=f'viewer.dims.ndisplay={dimensions}\nviewer.dims.point=tuple(layer.data_to_world({position}))\nviewer.reset_view()'
  reply,_=worker.request({'op':'python-resources','source':code});assert not reply['console']['error'],reply
  worker.request({'op':'render','width':256,'height':192})
  reply,_=worker.request({'op':'python-resources','source':'viewer.reset_view()'});assert not reply['console']['error']
  before,_=worker.request({'op':'render','width':256,'height':192});frame=before['frame'];row=frame['layers'][0]
  desired={**DISPLAY,'translation':[.125]*len(row['scale']),'scaling':dimensions==3}
  with pytest.raises(ChangeRefused):
   worker.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],'operation':'text-presentation','value':{'layer':row['id'],'settings':{**desired,'size':0}}}})
  changed,pixels=worker.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],'operation':'text-presentation','value':{'layer':row['id'],'settings':desired}}})
  assert changed['frame']['layers'][0]['textAppearance']==desired
  assert len(set(pixels))>4,'Native reference must contain visible scientific geometry'
  with pytest.raises(ChangeRefused):
   worker.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],'operation':'text-presentation','value':{'layer':row['id'],'settings':desired}}})
  assertions='''assert all(np.array_equal(a,b) for a,b in zip(layer.data,original_data))
assert layer.features.equals(original_features) and layer.feature_defaults.equals(original_defaults)
assert layer.text.string is original_string and layer.text.color is original_text_color
assert np.array_equal(layer.text.values,original_values) and np.array_equal(layer.face_color,original_colors)
'''
  reply,_=worker.request({'op':'python-resources','source':'layer.text.visible=False'});assert not reply['console']['error']
  _,without_text=worker.request({'op':'render','width':256,'height':192});assert pixels!=without_text, 'Text reference must include visible labels'
  code='layer.text.size=9\nlayer.text.rotation=0\n'+ '\n'.join(f'layer.text.{key}={value!r}' for key,value in desired.items())+'\n'+assertions
  reply,_=worker.request({'op':'python-resources','source':code});assert not reply['console']['error'],reply
  native,reference=worker.request({'op':'render','width':256,'height':192});assert pixels==reference
  semantic='point-set' if family=='points' else 'shape-set'
  save=OutputSave(store,original,'text-display-123456789',row['id'],{'semantic':semantic,'axes':['z','y','x'][-len(row['scale']):]})
  save.capture(worker);save.publish();saved=store.read(save.target,'s1');save.close()
 with review(store,saved.id,'s1',1) as (_,worker):
  reopened,_=worker.request({'op':'render','width':256,'height':192});assert reopened['frame']['layers'][0]['textAppearance']==desired
  settings=views.from_frame(reopened['frame']);assert settings['schema']==13
  altered=copy.deepcopy(settings);altered['layers'][0]['textAppearance']={**desired,'size':22.25,'anchor':'lower_right'}
  restored,_=worker.request({'op':'change','width':256,'height':192,'request':{'frame':reopened['frame']['frame'],'operation':'restore-view','value':altered}})
  assert views.restored(altered,views.from_frame(restored['frame']))
  reply,_=worker.request({'op':'python-resources','source':"assert viewer.layers[0].features['id'].dtype.name=='uint64'\nassert int(viewer.layers[0].features['id'].iloc[-1])>2**63\nviewer.layers[0].text.size=14.75"})
  assert not reply['console']['error'],reply
  shown,_=worker.request({'op':'render','width':256,'height':192});assert shown['frame']['layers'][0]['textAppearance']['size']==14.75
 assert store.read(original.id,'s1')==original and store.read(saved.id,'s1')==saved


def test_actual_text_event_invalidates_frame_and_disconnects(actual_host,tmp_path):
 from orchestrator.scientific_projects import ProjectStore
 from orchestrator.scientific_project_review import review
 from tests.test_scientific_surface_output import source
 from tests.test_scientific_points_output import CREATE
 store=ProjectStore(tmp_path/'data');original=source(store,actual_host)
 with review(store,original.id,'s1',original.revision) as (_,worker):
  code=CREATE+'''\nimport threading
from types import SimpleNamespace
from env_worker.scientific_host import ReviewSession, Stale
probe=ReviewSession.__new__(ReviewSession)
probe._connections=[];probe.editable=probe.view_bindings=None
probe.panel=SimpleNamespace(invalidate=lambda:None)
probe._closed=False;probe._thread=threading.get_ident()
try:
    for event in probe._layer_events(points):probe._connect(event)
    count=len(probe._connections)
    for event in probe._layer_events(points):probe._connect(event)
    assert len(probe._connections)==count
    probe._frame='f'*64
    points.text.size=31
    assert probe._frame is None, 'Native text changed but the previous frame is still admitted'
    try:probe._current('f'*64)
    except Stale:pass
    else:raise AssertionError('Old text frame was admitted')
finally:
    for event in probe._connections:event.disconnect(probe._invalidate)
probe._frame='e'*64
points.text.size=29
assert probe._frame=='e'*64, 'Disconnected text listener remained active'
'''
  reply,_=worker.request({'op':'python-resources','source':code});assert not reply['console']['error'],reply
