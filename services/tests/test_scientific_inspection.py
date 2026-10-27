"""Native samples are exact, frame-authorized and independent of scientific edits."""
import asyncio
import copy
from pathlib import Path
import numpy as np
import pytest
from env_worker import scientific_inspection as inspection
from orchestrator import scientific_host
from tests.test_scientific_host_worker import actual_host  # noqa: F401
from tests.test_native_regions import source

REQUEST={'op':'inspect','frame':'a'*64,'layer':'b'*32,'position':[10,20]}
REPLY={'frame':'a'*64,'layer':'b'*32,'world':[10.,20.],'data':[1.,2.], 'axes':['y','x'],'units':['micrometer','micrometer'],'level':None,'projection':'none','dtype':'<u8','values':['18446744073709551615']}

@pytest.mark.parametrize('patch',[{'frame':'c'*64},{'layer':'d'*32},{'world':[True,2]},{'world':[1,2,3]},{'data':[1]},{'values':[2**64-1]},{'values':['x']},{'dtype':'<U8'},{'level':True},{'level':64},{'projection':'invented'},{'axes':['y']},{'extra':True}])
def test_inspection_rejects_unbound_or_lossy_reply(patch):
 with pytest.raises(ValueError):inspection.reply({**REPLY,**patch},REQUEST)

def test_exact_native_sample_never_rounds_uint64_or_nonfinite_values():
 assert inspection.reply(REPLY,REQUEST)['values']==['18446744073709551615']
 for val in ['nan','inf','-inf','1.7976931348623157e+308','True']:
  assert inspection.reply({**REPLY,'values':[val]},REQUEST)['values']==[val]
 for bad in [{**REQUEST,'actor':'studio'},{**REQUEST,'position':[True,2]},{**REQUEST,'frame':'old'}]:
  with pytest.raises(ValueError):inspection.command(bad)
 with pytest.raises(scientific_host.Failed):scientific_host.checked_reply(REQUEST,{'ok':True,'inspection':REPLY},b'unexpected')


def test_actual_inspection_calibration_projection_precision_and_stale_refusal(actual_host):
 values=np.arange(6*24*28,dtype='<u2').reshape(6,24,28);original=values.tobytes()
 with scientific_host.review(actual_host['key'],Path(actual_host['folder']).name,{'volume':source(values,[])}) as worker:
  def execute(code):
   reply,_=worker.request({'op':'python-resources','source':code});assert not reply['console']['error'],reply
  execute("import numpy as np\nlayer=viewer.layers[0]\nlayer.scale=(2,.5,.25)\nlayer.translate=(10,20,30)\nlayer.units=('um','um','um')\nlayer.axis_labels=('z','y','x')\nviewer.dims.set_point(0,14)\nviewer.camera.center=(14,25,33)\nviewer.camera.zoom=5\nprivate_value=73")
  def render():return worker.request({'op':'render','width':320,'height':240})[0]['frame']
  frame=render();request={'op':'inspect','frame':frame['frame'],'layer':frame['layers'][0]['id'],'position':[120,160]}
  result,_=worker.request(request);sample=result['inspection']
  assert sample['world']==[14,25,33] and sample['data']==[2,10,12]
  assert sample['values']==[str(values[2,10,12])] and sample['dtype']=='<u2'
  assert sample['axes']==['z','y','x'] and sample['units']==['micrometer']*3
  assert worker.request(request)[0]==result  # reads do not consume the frame
  execute("assert private_value==73\nlayer.projection_mode='max'\nviewer.dims.margin_left=(2,0,0)\nviewer.dims.margin_right=(2,0,0)")
  with pytest.raises(scientific_host.ChangeRefused):worker.request(request)
  frame=render();request.update(frame=frame['frame'])
  result,_=worker.request(request);sample=result['inspection']
  assert sample['projection']=='max' and sample['values']==[str(values[3,10,12])]
  execute("viewer.layers.clear()\nlayer=viewer.add_labels(np.full((24,28),2**64-1,dtype=np.uint64))\nviewer.camera.center=(0,10,12)\nviewer.camera.zoom=5")
  frame=render();request.update(frame=frame['frame'],layer=frame['layers'][0]['id'])
  result,_=worker.request(request);assert result['inspection']['values']==['18446744073709551615']
  execute("assert private_value==73")
  execute("viewer.layers.clear()\nlayer=viewer.add_image(np.arange(24*28,dtype=np.uint16).reshape(24,28),scale=(2,3),translate=(10,20),rotate=90)\nviewer.camera.center=(0,-5,28)\nviewer.camera.zoom=5")
  frame=render();request.update(frame=frame['frame'],layer=frame['layers'][0]['id'])
  result,_=worker.request(request);sample=result['inspection']
  assert np.allclose(sample['data'],[4,5],rtol=0,atol=1e-12) and np.allclose(sample['world'],[-5,28],rtol=0,atol=1e-12)
  assert sample['values']==[str(4*28+5)]
  # RGB values come from native data, never the colored display pixels.
  execute("viewer.layers.clear()\nlayer=viewer.add_image(np.broadcast_to(np.array([1,128,255],dtype=np.uint8),(24,28,3)).copy(),rgb=True)\nviewer.camera.center=(0,10,12)\nviewer.camera.zoom=5")
  frame=render();request.update(frame=frame['frame'],layer=frame['layers'][0]['id'])
  result,_=worker.request(request);assert result['inspection']['values']==['1','128','255']
  outside={**request,'position':[0,0]};result,_=worker.request(outside);assert result['inspection']['values'] is None
  execute("viewer.layers.clear()\nlayer=viewer.add_image([np.full((128,128),11,dtype=np.uint16),np.full((64,64),22,dtype=np.uint16),np.full((32,32),33,dtype=np.uint16)],multiscale=True)\nviewer.camera.center=(0,64,64)\nviewer.camera.zoom=.5")
  frame=render();request.update(frame=frame['frame'],layer=frame['layers'][0]['id'])
  result,_=worker.request(request);sample=result['inspection']
  assert sample['level'] in (0,1,2) and sample['values']==[str([11,22,33][sample['level']])]
  assert sample['world']==[64,64] and sample['data']==[64,64]
  # A visible layer can be outside the current Z plane. Its native 1x1
  # placeholder must never be reported as a scientific zero.
  execute("viewer.layers.clear()\nviewer.add_image(np.ones((10,24,28),dtype=np.uint16))\nlayer=viewer.add_image(np.full((2,24,28),9,dtype=np.uint16))\nviewer.dims.set_point(0,8)\nviewer.camera.center=(8,0,0)\nviewer.camera.zoom=5")
  frame=render();request.update(frame=frame['frame'],layer=frame['layers'][1]['id'])
  result,_=worker.request(request);assert result['inspection']['values'] is None
  execute("assert layer._slice.empty and private_value==73")
 assert values.tobytes()==original


def test_inspection_requires_render_receipt_without_consuming_frame_or_recording_work(tmp_path,monkeypatch):
 from tests.test_scientific_reviews import ReviewsRig,Worker
 rig=ReviewsRig(tmp_path);old=Worker.request
 def answer(self,request):
  if request['op']=='inspect':self.requests.append(copy.deepcopy(request));return {'ok':True,'inspection':REPLY},b''
  return old(self,request)
 monkeypatch.setattr(Worker,'request',answer)
 async def go():
  manager=rig.manager
  try:
   identity=await manager.open(rig.origin)
   await manager.request(rig.client,identity,{'op':'render','width':2,'height':3},snapshot=rig.pin())
   owner=manager._owners[identity];before=copy.deepcopy(owner.frame)
   for _ in range(2):
    await manager.request(rig.client,identity,REQUEST,snapshot=rig.paint(identity),draw=rig.item['scientificPaint']['draw'])
    assert owner.frame==before and owner.completed is None and owner.sequence==0
   for bad in [{**REQUEST,'frame':'c'*64},{**REQUEST,'actor':'studio'}]:
    with pytest.raises(ValueError):await manager.request(rig.client,identity,bad,snapshot=rig.pin(),draw=rig.item['scientificPaint']['draw'])
   with pytest.raises(ValueError):await manager.request(object(),identity,REQUEST,snapshot=rig.pin(),draw=rig.item['scientificPaint']['draw'])
   assert rig.store.read(rig.saved.id,rig.saved.session)==rig.saved
  finally:await manager.close()
 asyncio.run(go())


def test_actual_five_dimensional_inspection_tracks_displayed_axes_and_transforms(actual_host):
 values=np.arange(2*3*5*16*18,dtype='<u2').reshape(2,3,5,16,18)
 original=values.tobytes();expected=[1,2,3,4,5];world=[3,14,32,32,41.25]
 with scientific_host.review(actual_host['key'],Path(actual_host['folder']).name,{'volume':source(values,[],axes=('t','c','z','y','x'))}) as worker:
  setup="layer=viewer.layers[0]\nlayer.scale=(3,2,4,.5,.25)\nlayer.translate=(0,10,20,30,40)\nlayer.axis_labels=('t','c','z','y','x')\nlayer.units=('second','dimensionless','um','um','um')\nviewer.dims.ndisplay=2\nviewer.dims.point=(3,14,32,32,41.25)"
  result,_=worker.request({'op':'python-resources','source':setup});assert not result['console']['error'],result
  for order in [(0,1,2,3,4),(4,2,0,3,1),(4,3,1,0,2)]:
   a,b=order[-2:]
   code=f'viewer.dims.order={order!r}\nviewer.camera.center={(0,world[a],world[b])!r}\nviewer.camera.zoom=5'
   result,_=worker.request({'op':'python-resources','source':code});assert not result['console']['error'],result
   result,_=worker.request({'op':'render','width':320,'height':240});frame=result['frame']
   result,_=worker.request({'op':'inspect','frame':frame['frame'],'layer':frame['layers'][0]['id'],'position':[120,160]})
   sample=result['inspection'];assert sample['data']==expected and sample['world']==world
   assert sample['values']==[str(values[tuple(expected)])]
   assert sample['axes']==['t','c','z','y','x'] and sample['units']==['second','dimensionless','micrometer','micrometer','micrometer']
 assert values.tobytes()==original

POINT_REPLY={'frame':REQUEST['frame'],'layer':REQUEST['layer'],'world':[10.,20.],'data':[1.,2.],
 'axes':['y','x'],'units':['micrometer','micrometer'],'kind':'points',
 'hit':{'selected':False,'index':3,'coordinates':['1.25','2.5'],'dtype':'<f8','features':[
  {'name':'object_id','dtype':'uint64','kind':'integer','value':'18446744073709551615'},
  {'name':'confidence','dtype':'float64','kind':'float','value':'0.125'},
  {'name':'missing','dtype':'UInt64','kind':'missing','value':None}]}}

def test_point_reply_retains_exact_features_and_rejects_malformed_or_unbound_records():
 assert inspection.reply(POINT_REPLY,REQUEST)==POINT_REPLY
 assert inspection.reply({**POINT_REPLY,'hit':None},REQUEST)['hit'] is None
 for patch in [{'frame':'c'*64},{'layer':'c'*32},{'kind':'labels'},{'data':[True,1]},
               {'hit':{**POINT_REPLY['hit'],'index':True}},
               {'hit':{**POINT_REPLY['hit'],'coordinates':['1']}},
               {'hit':{**POINT_REPLY['hit'],'features':[{'name':'x','dtype':'uint64','kind':'integer','value':2**64-1}]}},
               {'hit':{**POINT_REPLY['hit'],'features':[POINT_REPLY['hit']['features'][0]]*2}},
               {'hit':{**POINT_REPLY['hit'],'features':[{'name':'x','dtype':'x','kind':'missing','value':'unknown'}]}},
               {'hit':{**POINT_REPLY['hit'],'features':[{'name':'x','dtype':'object','kind':'string','value':'x'*4097}]}}]:
  with pytest.raises(ValueError):inspection.reply({**POINT_REPLY,**patch},REQUEST)

def test_actual_point_inspection_uses_native_hit_order_and_preserves_typed_features(actual_host):
 values=np.arange(6*24*28,dtype='<u2').reshape(6,24,28);original=values.tobytes()
 with scientific_host.review(actual_host['key'],Path(actual_host['folder']).name,{'volume':source(values,[])}) as worker:
  def execute(code):
   result,_=worker.request({'op':'python-resources','source':code});assert not result['console']['error'],result
  execute("import numpy as np\nimport pandas as pd\nviewer.layers.clear()\npoints=viewer.add_points(np.array([[2.,10.,12.],[2.,10.,12.],[4.,10.,12.]]),size=10,features=pd.DataFrame({'object_id':np.array([2,2**64-1,4],dtype=np.uint64),'score':[.25,.125,.75],'group':pd.Categorical(['a','b','c'],ordered=True),'optional':pd.array([1,None,3],dtype='UInt64'),'text':['a','<b>literal</b>','c']}))\npoints.scale=(2,.5,.25)\npoints.translate=(10,20,30)\npoints.axis_labels=('z','y','x')\npoints.units=('um','um','um')\nviewer.dims.set_point(0,14)\nviewer.camera.center=(14,25,33)\nviewer.camera.zoom=5\noriginal_points=points.data.copy()\noriginal_features=points.features.copy(deep=True)")
  frame=worker.request({'op':'render','width':320,'height':240})[0]['frame']
  request={'op':'inspect','frame':frame['frame'],'layer':frame['layers'][0]['id'],'position':[120,160]}
  answer=worker.request(request)[0]['inspection'];hit=answer['hit']
  assert answer['world']==[14,25,33] and answer['data']==[2,10,12]
  assert hit['index']==1 and hit['coordinates']==['2.0','10.0','12.0'] and hit['dtype']=='<f8'
  features={row['name']:row for row in hit['features']}
  assert features['object_id']['value']=='18446744073709551615' and features['object_id']['dtype']=='uint64'
  assert features['score']['value']=='0.125' and features['group']['value']=='b'
  assert features['optional']['kind']=='missing' and features['optional']['value'] is None
  assert features['text']['value']=='<b>literal</b>'
  assert worker.request(request)[0]['inspection']==answer
  assert worker.request({**request,'position':[0,0]})[0]['inspection']['hit'] is None
  execute("assert points.get_value((2,10,12))==1\nnp.testing.assert_array_equal(points.data,original_points)\npd.testing.assert_frame_equal(points.features,original_features)\npoints.visible=False")
  with pytest.raises(scientific_host.ChangeRefused):worker.request(request)
  frame=worker.request({'op':'render','width':320,'height':240})[0]['frame'];request.update(frame=frame['frame'])
  with pytest.raises(scientific_host.ChangeRefused):worker.request(request)
 assert values.tobytes()==original


def point_worker_code(actual_host, code):
 # pandas belongs to the isolated package, not the application's environment.
 with scientific_host.review(actual_host['key'],Path(actual_host['folder']).name,{'volume':source(np.zeros((2,3,4),dtype=np.uint16),[])}) as worker:
  result,_=worker.request({'op':'python-resources','source':code})
  assert not result['console']['error'],result


def test_point_feature_encoder_preserves_nullable_precision_and_never_calls_object_stringifiers(actual_host):
 point_worker_code(actual_host,"""from env_worker.scientific_point_inspection import feature_value
import numpy as np
import pandas as pd
assert feature_value(np.uint64(2**64-1))==('integer','18446744073709551615')
assert feature_value(np.str_('native text'))==('string','native text')
assert feature_value(pd.NA)==('missing',None)
assert feature_value(np.float64(float('nan')))==('float','nan')
timestamp=pd.Timestamp('2026-10-18T12:00:00.123456789Z')
assert feature_value(timestamp)==('datetime','2026-10-18T12:00:00.123456789+00:00')
class Unsafe:
 def __str__(self):raise AssertionError('No object formatter should execute')
try:feature_value(Unsafe())
except ValueError as error:assert 'data-only' in str(error)
else:raise AssertionError('An arbitrary object must be refused')
""")


def test_point_feature_text_uses_browser_utf16_bounds():
 feature={'name':'🧬'*128,'dtype':'object','kind':'string','value':'🔬'*2048}
 answer={**POINT_REPLY,'hit':{**POINT_REPLY['hit'],'features':[feature]}}
 assert inspection.reply(answer,REQUEST)==answer
 for patch in [{'name':'🧬'*129},{'value':'🔬'*2049}]:
  bad={**answer,'hit':{**answer['hit'],'features':[{**feature,**patch}]}}
  with pytest.raises(ValueError):inspection.reply(bad,REQUEST)

def test_point_dtype_encoder_rejects_custom_formatters(actual_host):
 point_worker_code(actual_host,"""from env_worker.scientific_point_inspection import dtype_label
from pandas.api.extensions import ExtensionDtype
class CustomDtype(ExtensionDtype):
 name='custom';type=object;kind='O'
 def __str__(self):raise AssertionError('Custom dtype formatting must not execute')
try:dtype_label(CustomDtype())
except ValueError as error:assert 'data-only' in str(error)
else:raise AssertionError('A custom dtype formatter must be refused')
""")

SHAPE_REPLY={**POINT_REPLY,'kind':'shapes','hit':{'selected':False,'index':0,'geometry':'polygon','dtype':'<f4',
 'vertices':[['1','2'],['1','4'],['3','4']], 'features':POINT_REPLY['hit']['features']}}

def test_shape_reply_keeps_typed_features_and_geometry_boundaries():
 assert inspection.reply(SHAPE_REPLY,REQUEST)==SHAPE_REPLY
 assert inspection.reply({**SHAPE_REPLY,'hit':None},REQUEST)['hit'] is None
 for patch in [{'geometry':'invented'},{'geometry':'line'},{'geometry':'ellipse'}, {'index':True},
               {'vertices':[['1','2'],['1','4']]},{'vertices':[['1','2']]*257},
               {'vertices':[['nan','2'],['1','4'],['3','4']]}, {'vertices':[['1e999','2'],['1','4'],['3','4']]},
               {'vertices':[[1,2],[1,4],[3,4]]},{'features':[SHAPE_REPLY['hit']['features'][0]]*2}]:
  with pytest.raises(ValueError):inspection.reply({**SHAPE_REPLY,'hit':{**SHAPE_REPLY['hit'],**patch}},REQUEST)
 with pytest.raises(ValueError):inspection.reply({**SHAPE_REPLY,'frame':'c'*64},REQUEST)


def test_actual_shape_inspection_uses_native_z_order_and_preserves_ragged_geometry(actual_host):
 values=np.arange(6*24*28,dtype='<u2').reshape(6,24,28);original=values.tobytes()
 with scientific_host.review(actual_host['key'],Path(actual_host['folder']).name,{'volume':source(values,[])}) as worker:
  def execute(code):
   result,_=worker.request({'op':'python-resources','source':code});assert not result['console']['error'],result
  execute("""import numpy as np
import pandas as pd
viewer.layers.clear()
polygon=np.array([[2,5,7],[2,5,17],[2,15,17],[2,15,7],[2,10,4]],dtype=np.float32)
rectangle=np.array([[2,6,8],[2,6,16],[2,14,16],[2,14,8]],dtype=np.float32)
shapes=viewer.add_shapes([polygon,rectangle],shape_type=['polygon','rectangle'],z_index=[9,1],features=pd.DataFrame({'id':np.array([2**64-1,7],dtype=np.uint64),'class':pd.Categorical(['top','bottom'],ordered=True)}))
shapes.scale=(2,.5,.25)
shapes.translate=(10,20,30)
shapes.axis_labels=('z','y','x')
shapes.units=('um','um','um')
viewer.dims.set_point(0,14)
viewer.camera.center=(14,25,33)
viewer.camera.zoom=5
original_shapes=[a.copy() for a in shapes.data]
original_features=shapes.features.copy(deep=True)
original_selection=set(shapes.selected_data)
assert shapes.get_value((2,10,12))[0]==0
""")
  frame=worker.request({'op':'render','width':320,'height':240})[0]['frame']
  request={'op':'inspect','frame':frame['frame'],'layer':frame['layers'][0]['id'],'position':[120,160]}
  answer=worker.request(request)[0]['inspection'];hit=answer['hit']
  assert answer['kind']=='shapes' and answer['world']==[14,25,33] and answer['data']==[2,10,12]
  assert hit['index']==0 and hit['geometry']=='polygon' and hit['dtype']=='<f4'
  assert hit['vertices']==[['2.0','5.0','7.0'],['2.0','5.0','17.0'],['2.0','15.0','17.0'],['2.0','15.0','7.0'],['2.0','10.0','4.0']]
  assert hit['features'][0]['value']=='18446744073709551615' and hit['features'][1]['value']=='top'
  assert worker.request(request)[0]['inspection']==answer
  assert worker.request({**request,'position':[0,0]})[0]['inspection']['hit'] is None
  execute("theta=np.linspace(0,2*np.pi,257,endpoint=False)\nlarge=np.column_stack([np.full(257,2),10+5*np.sin(theta),12+5*np.cos(theta)])\nshapes.add(large,shape_type='polygon',z_index=20)")
  frame=worker.request({'op':'render','width':320,'height':240})[0]['frame'];request.update(frame=frame['frame'])
  with pytest.raises(scientific_host.ChangeRefused,match='256-vertex'):worker.request(request)
  execute("assert shapes.nshapes==3\nshapes.remove([2])\nshapes.selected_data=original_selection")
  frame=worker.request({'op':'render','width':320,'height':240})[0]['frame'];request.update(frame=frame['frame'])
  assert worker.request(request)[0]['inspection']['hit']['features'][0]['value']=='18446744073709551615'
  execute("assert set(shapes.selected_data)==original_selection\nfor a,b in zip(shapes.data,original_shapes):np.testing.assert_array_equal(a,b)\npd.testing.assert_frame_equal(shapes.features,original_features)\nshapes.visible=False")
  with pytest.raises(scientific_host.ChangeRefused):worker.request(request)
  frame=worker.request({'op':'render','width':320,'height':240})[0]['frame'];request.update(frame=frame['frame'])
  with pytest.raises(scientific_host.ChangeRefused):worker.request(request)
 assert values.tobytes()==original

@pytest.mark.parametrize('kind,vertices',[
 ('line',[[2,10,8],[2,10,16]]),
 ('path',[[2,10,8],[2,10,12],[2,14,16]]),
 ('rectangle',[[2,6,8],[2,6,16],[2,14,16],[2,14,8]]),
 ('ellipse',[[2,6,8],[2,6,16],[2,14,16],[2,14,8]]),
 ('polygon',[[2,5,7],[2,5,17],[2,15,17],[2,15,7],[2,10,4]]),
])
def test_actual_shape_inspection_preserves_each_native_geometry(kind,vertices,actual_host):
 with scientific_host.review(actual_host['key'],Path(actual_host['folder']).name,{'volume':source(np.zeros((6,24,28),dtype=np.uint16),[])}) as worker:
  setup=f"import numpy as np\nviewer.layers.clear()\nshape=viewer.add_shapes([np.array({vertices!r},dtype=np.float32)],shape_type={kind!r},edge_width=3)\nshape.scale=(2,.5,.25)\nshape.translate=(10,20,30)\nviewer.dims.set_point(0,14)\nviewer.camera.center=(14,25,33)\nviewer.camera.zoom=5"
  result,_=worker.request({'op':'python-resources','source':setup});assert not result['console']['error'],result
  if kind=='ellipse':
   # Pinned native triangulation misses the exact fan center. Preserve that
   # reference behavior, and prove a nearby interior hit instead of inventing one.
   result,_=worker.request({'op':'python-resources','source':'assert shape.get_value((2,10,12))==(None,None)\nassert shape.get_value((2,9,11))[0]==0'})
   assert not result['console']['error'],result
   frame=worker.request({'op':'render','width':320,'height':240})[0]['frame']
   assert worker.request({'op':'inspect','frame':frame['frame'],'layer':frame['layers'][0]['id'],'position':[120,160]})[0]['inspection']['hit'] is None
   result,_=worker.request({'op':'python-resources','source':'viewer.camera.center=(14,24.5,32.75)'})
   assert not result['console']['error'],result
  else:
   result,_=worker.request({'op':'python-resources','source':'assert shape.get_value((2,10,12))[0]==0'})
   assert not result['console']['error'],result
  frame=worker.request({'op':'render','width':320,'height':240})[0]['frame']
  request={'op':'inspect','frame':frame['frame'],'layer':frame['layers'][0]['id'],'position':[120,160]}
  result=worker.request(request)[0]['inspection']['hit']
  assert result['index']==0 and result['geometry']==kind and result['features']==[]
  assert result['vertices']==[[str(float(v)) for v in row] for row in vertices]


def test_actual_point_selection_is_frame_bound_and_preserves_native_data(actual_host):
 with scientific_host.review(actual_host['key'],Path(actual_host['folder']).name,{'volume':source(np.zeros((6,24,28),dtype=np.uint16),[])}) as worker:
  def execute(code):
   result,_=worker.request({'op':'python-resources','source':code});assert not result['console']['error'],result
  execute("""import numpy as np
import pandas as pd
viewer.layers.clear()
points=viewer.add_points(np.array([[2,10,12],[2,18,20],[4,10,12]],dtype=np.float64),features=pd.DataFrame({'id':np.array([2**64-1,2**63+1,3],dtype=np.uint64),'group':pd.Categorical(['a','b','a'],ordered=True)}),face_color='group',face_color_cycle=['red','blue'],size=[3,5,7],symbol=['o','s','+'])
viewer.dims.set_point(0,2)
viewer.camera.center=(2,10,12)
viewer.camera.zoom=5
original_data=points.data.copy()
original_features=points.features.copy(deep=True)
original_colors=points.face_color.copy()
original_sizes=points.size.copy()
original_symbols=points.symbol.copy()
original_layer=points
other=viewer.add_points(np.array([[2,20,22]],dtype=np.float64),name="Other points")
assert viewer.layers.selection.active is other
""")
  def render():return worker.request({'op':'render','width':320,'height':240})[0]['frame']
  frame=render();layer=frame['layers'][0]['id']
  def change(frame,action,index):return worker.request({'op':'change','width':320,'height':240,'request':{'frame':frame['frame'],'operation':'points-selection','value':{'layer':layer,'action':action,'index':index}}})[0]['frame']
  def inspect(frame):return worker.request({'op':'inspect','frame':frame['frame'],'layer':layer,'position':[120,160]})[0]['inspection']['hit']
  assert inspect(frame)['selected'] is False
  execute('selection_highlights=[]\npoints.events.highlight.connect(lambda event:selection_highlights.append(1))')
  frame=render()
  previous=frame;frame=change(frame,'replace',0)
  assert inspect(frame)['selected'] is True
  with pytest.raises(scientific_host.ChangeRefused):change(previous,'toggle',1)
  execute('assert points.selected_data=={0}\nassert viewer.layers.selection.active is other\nassert len(selection_highlights)==1, selection_highlights')
  frame=render();frame=change(frame,'toggle',1)
  execute('assert points.selected_data=={0,1}')
  frame=render();frame=change(frame,'toggle',0)
  assert inspect(frame)['selected'] is False
  execute('assert points.selected_data=={1}')
  frame=render()
  for action,index in [('replace',2),('replace',99),('replace',True),('replace',-1),('replace',1.5),('unknown',0),('clear',0)]:
   with pytest.raises(scientific_host.ChangeRefused):change(frame,action,index)
  frame=change(frame,'clear',None)
  execute("""assert not points.selected_data
assert points is original_layer
np.testing.assert_array_equal(points.data,original_data)
pd.testing.assert_frame_equal(points.features,original_features)
np.testing.assert_array_equal(points.face_color,original_colors)
np.testing.assert_array_equal(points.size,original_sizes)
np.testing.assert_array_equal(points.symbol,original_symbols)
points.visible=False
""")
  frame=render()
  with pytest.raises(scientific_host.ChangeRefused):change(frame,'replace',0)
  execute('points.visible=True\nviewer.dims.ndisplay=3')
  frame=render()
  with pytest.raises(scientific_host.ChangeRefused):change(frame,'replace',0)


def test_point_selection_readout_requires_an_exact_boolean():
 for selected in [None,0,1,'selected']:
  with pytest.raises(ValueError):inspection.reply({**POINT_REPLY,'hit':{**POINT_REPLY['hit'],'selected':selected}},REQUEST)
 for selected in [False,True]:
  assert inspection.reply({**POINT_REPLY,'hit':{**POINT_REPLY['hit'],'selected':selected}},REQUEST)['hit']['selected'] is selected


def test_actual_shape_selection_preserves_vertices_features_style_and_native_order(actual_host):
 with scientific_host.review(actual_host['key'],Path(actual_host['folder']).name,{'volume':source(np.zeros((6,24,28),dtype=np.uint16),[])}) as worker:
  def execute(code):
   result,_=worker.request({'op':'python-resources','source':code});assert not result['console']['error'],result
  execute("""import numpy as np
import pandas as pd
viewer.layers.clear()
polygon=np.array([[2,5,7],[2,5,17],[2,15,17],[2,15,7],[2,10,4]],dtype=np.float32)
rectangle=np.array([[2,6,8],[2,6,16],[2,14,16],[2,14,8]],dtype=np.float32)
remote=rectangle.copy();remote[:,0]=4
shapes=viewer.add_shapes([polygon,rectangle,remote],shape_type=['polygon','rectangle','ellipse'],z_index=[9,1,20],edge_width=[2,3,4],features=pd.DataFrame({'id':np.array([2**64-1,2**63+1,3],dtype=np.uint64),'class':pd.Categorical(['a','b','a'],ordered=True)}),face_color='class',face_color_cycle=['red','blue'])
other=viewer.add_points(np.array([[2,20,22]],dtype=np.float32))
assert viewer.layers.selection.active is other
shapes.scale=(2,.5,.25);shapes.translate=(10,20,30)
viewer.dims.set_point(0,14);viewer.camera.center=(14,25,33);viewer.camera.zoom=5
original_vertices=[a.copy() for a in shapes.data]
original_features=shapes.features.copy(deep=True)
original_colors=shapes.face_color.copy()
original_edges=shapes.edge_color.copy()
original_widths=list(shapes.edge_width)
original_z=list(shapes.z_index)
original_layer=shapes
""")
  def render():return worker.request({'op':'render','width':320,'height':240})[0]['frame']
  frame=render();layer=frame['layers'][0]['id']
  def inspect(frame):return worker.request({'op':'inspect','frame':frame['frame'],'layer':layer,'position':[120,160]})[0]['inspection']['hit']
  def change(frame,action,index):return worker.request({'op':'change','width':320,'height':240,'request':{'frame':frame['frame'],'operation':'shapes-selection','value':{'layer':layer,'action':action,'index':index}}})[0]['frame']
  assert inspect(frame)['index']==0 and inspect(frame)['selected'] is False
  previous=frame;frame=change(frame,'replace',0)
  assert inspect(frame)['selected'] is True
  with pytest.raises(scientific_host.ChangeRefused):change(previous,'toggle',1)
  execute('assert shapes.selected_data=={0}\nassert viewer.layers.selection.active is other')
  frame=render();frame=change(frame,'toggle',1)
  execute('assert shapes.selected_data=={0,1}')
  frame=render();frame=change(frame,'toggle',0)
  assert inspect(frame)['selected'] is False
  execute('assert shapes.selected_data=={1}')
  frame=render()
  for action,index in [('replace',2),('replace',99),('replace',True),('replace',-1),('replace',1.5),('unknown',0),('clear',0)]:
   with pytest.raises(scientific_host.ChangeRefused):change(frame,action,index)
  frame=change(frame,'clear',None)
  execute("""assert not shapes.selected_data
assert shapes is original_layer
for a,b in zip(shapes.data,original_vertices):np.testing.assert_array_equal(a,b)
pd.testing.assert_frame_equal(shapes.features,original_features)
np.testing.assert_array_equal(shapes.face_color,original_colors)
np.testing.assert_array_equal(shapes.edge_color,original_edges)
assert list(shapes.edge_width)==original_widths and list(shapes.z_index)==original_z
shapes.visible=False
""")
  frame=render()
  with pytest.raises(scientific_host.ChangeRefused):change(frame,'replace',0)
  execute('shapes.visible=True\nviewer.dims.ndisplay=3')
  frame=render()
  with pytest.raises(scientific_host.ChangeRefused):change(frame,'replace',0)
  execute('viewer.dims.ndisplay=2\nviewer.grid.enabled=True')
  frame=render()
  with pytest.raises(scientific_host.ChangeRefused):change(frame,'replace',0)
  execute('viewer.grid.enabled=False\nviewer.camera.center=(14,25,33)\nviewer.camera.zoom=5')
  frame=render();frame=change(frame,'replace',0)
  assert inspect(frame)['selected'] is True


def test_shape_selection_readout_requires_an_exact_boolean():
 for selected in [None,0,1,'selected']:
  with pytest.raises(ValueError):inspection.reply({**SHAPE_REPLY,'hit':{**SHAPE_REPLY['hit'],'selected':selected}},REQUEST)
 for selected in [False,True]:
  assert inspection.reply({**SHAPE_REPLY,'hit':{**SHAPE_REPLY['hit'],'selected':selected}},REQUEST)['hit']['selected'] is selected


def test_actual_layer_selection_native_activation_multiselect_and_recovery(actual_host):
 with scientific_host.review(actual_host['key'],Path(actual_host['folder']).name,{'volume':source(np.zeros((6,24,28),dtype=np.uint16),[])}) as worker:
  def execute(code):
   result,_=worker.request({'op':'python-resources','source':code});assert not result['console']['error'],result
  execute("""import numpy as np
image=viewer.layers[0]
points=viewer.add_points(np.array([[2,5,6],[2,12,14]],dtype=np.float32),size=[3,7])
shapes=viewer.add_shapes([np.array([[2,4,5],[2,4,15],[2,14,15],[2,14,5]],dtype=np.float32)],shape_type='rectangle')
original_points=points.data.copy()
original_shape=shapes.data[0].copy()
original_point_selection=set(points.selected_data)
original_shape_selection=set(shapes.selected_data)
events=[]
def selection_changed(event):
    events.append(viewer.layers.selection.active)
viewer.layers.selection.events.active.connect(selection_changed)
""")
  def render():return worker.request({'op':'render','width':320,'height':240})[0]['frame']
  frame=render();a,b,c=[layer['id'] for layer in frame['layers']]
  assert frame['layerSelection']=={'selected':[c],'active':c}
  def change(frame,action,layer):return worker.request({'op':'change','width':320,'height':240,'request':{'frame':frame['frame'],'operation':'layer-selection','value':{'layer':layer,'action':action}}})[0]['frame']
  old=frame;frame=change(frame,'replace',b)
  assert frame['layerSelection']=={'selected':[b],'active':b}
  with pytest.raises(scientific_host.ChangeRefused):change(old,'replace',a)
  execute('assert viewer.layers.selection.active is points\nassert events[-1] is points')
  frame=render();frame=change(frame,'toggle',c)
  assert frame['layerSelection']=={'selected':[b,c],'active':None}
  frame=change(frame,'toggle',b)
  assert frame['layerSelection']=={'selected':[c],'active':c}
  frame=change(frame,'clear',None)
  assert frame['layerSelection']=={'selected':[],'active':None}
  for action,layer in [('replace','f'*32),('replace',None),('replace',True),('clear',c),('unknown',a)]:
   with pytest.raises(scientific_host.ChangeRefused):change(frame,action,layer)
  frame=change(frame,'replace',a)
  assert frame['layerSelection']=={'selected':[a],'active':a}
  execute("""np.testing.assert_array_equal(points.data,original_points)
np.testing.assert_array_equal(shapes.data[0],original_shape)
assert not image.data.any()
assert points.selected_data==original_point_selection and shapes.selected_data==original_shape_selection
viewer.layers.remove(shapes)
""")
  frame=render()
  with pytest.raises(scientific_host.ChangeRefused):change(frame,'replace',c)
  frame=change(frame,'replace',b)
  assert frame['layerSelection']=={'selected':[b],'active':b}


def test_layer_selection_metadata_rejects_invented_active_or_foreign_ids():
 from env_worker.scientific_view_wire import frame as validate
 import json
 valid=json.loads((Path(__file__).resolve().parents[2]/'contract/scientific-view-cases.json').read_text())['valid'];identity=valid['layers'][0]['id']
 for value in [{'selected':[identity],'active':None},{'selected':[],'active':identity},
               {'selected':['f'*32],'active':'f'*32},{'selected':[identity,identity],'active':None},
               {'selected':[True],'active':True},{'selected':[],'active':None,'extra':1}]:
  with pytest.raises(ValueError):validate({**valid,'layerSelection':value})
 for value in [{'selected':[],'active':None},{'selected':[identity],'active':identity}]:
  assert validate({**valid,'layerSelection':value})['layerSelection']==value


def test_native_multiselect_event_revokes_frame_without_active_layer_change(actual_host):
 with scientific_host.review(actual_host['key'],Path(actual_host['folder']).name,{'volume':source(np.zeros((6,24,28),dtype=np.uint16),[])}) as worker:
  result,_=worker.request({'op':'python-resources','source':'''import threading
import numpy as np
from types import SimpleNamespace
from env_worker.scientific_host import ReviewSession, Stale
viewer.add_image(np.ones((24,28),dtype=np.uint8))
viewer.add_image(np.full((24,28),2,dtype=np.uint8))
viewer.layers.selection={viewer.layers[0],viewer.layers[1]}
assert viewer.layers.selection.active is None
# Probe the production subscription and admission methods with actual native
# events. The marker is tested inside this call, before Python's blanket
# invalidation, so that it cannot hide a missing selection subscription.
probe=ReviewSession.__new__(ReviewSession)
probe.viewer=viewer;probe._connections=[];probe.editable=probe.view_bindings=None
probe.panel=SimpleNamespace(invalidate=lambda:None)
probe._closed=False;probe._thread=threading.get_ident()
try:
    for event in probe._view_events():probe._connect(event)
    count=len(probe._connections)
    for event in probe._view_events():probe._connect(event)
    assert len(probe._connections)==count
    probe._frame='f'*64
    viewer.layers.selection.add(viewer.layers[2])
    assert viewer.layers.selection.active is None
    assert probe._frame is None, 'Native membership changed but the previous frame is still admitted'
    try:probe._current('f'*64)
    except Stale:pass
    else:raise AssertionError('Old selection frame was admitted')
finally:
    for event in probe._connections:event.disconnect(probe._invalidate)
probe._frame='e'*64
viewer.layers.selection.remove(viewer.layers[2])
assert probe._frame=='e'*64, 'Disconnected selection listener remained active'
'''});assert not result['console']['error'],result['console']
