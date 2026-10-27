"""Actual native color rendering plus bounded, recoverable display state."""
import copy
from pathlib import Path
from types import SimpleNamespace
import pytest
from env_worker import scientific_colormap as colors, scientific_host, scientific_view_preferences as views
from tests.test_scientific_change_recovery import host
from tests.test_scientific_host_worker import actual_host  # noqa: F401

MAP={'name':'custom-test','colors':[[0.,0.,0.,1.],[1.,.5,0.,1.]],'controls':[0.,1.],'interpolation':'linear',
     'nanColor':[1.,0.,1.,0.],'highColor':None,'lowColor':None}

@pytest.mark.parametrize('patch',[
 {'colors':[]},{'colors':[[1,0,0]]},{'colors':[[True,0,0,1]]},{'colors':[[10**400,0,0,1]]},
 {'colors':[[float('nan'),0,0,1]]},{'colors':[[0,0,0,1]]*1025},{'controls':[1,0]},
 {'controls':[0,.5,1]},{'controls':[0,.9]},{'interpolation':'unknown'},{'interpolation':'zero'},
 {'nanColor':None},{'highColor':[0,0,0,2]},{'name':''},{'name':'😀'*121},
])
def test_invalid_colors_preserve_frame_and_native_layer(patch):
 session=host();layer=SimpleNamespace(_type_string='image');session._layers=[('image',layer)]
 with pytest.raises(scientific_host.ChangeRefused):
  session.change({'frame':'current','operation':'image-colormap','value':{'layer':'image','settings':{**MAP,**patch}}})
 assert vars(layer)=={'_type_string':'image'} and session._frame=='current' and not session.dirty


def test_colors_saved_by_value_not_by_registry_choice():
 from tests.test_scientific_view_preferences import FRAME,CASES
 frame=copy.deepcopy(FRAME);frame['layers'][0].update(viewKey='raw',viewGeometry=CASES['valid']['layers'][0]['geometry'],imageColormap=MAP,colormapChoices=['gray'])
 saved=views.from_frame(frame);assert saved['schema']==7 and 'colormapChoices' not in saved['layers'][0]
 for schema in [1,3,4,5,6]:
  old=copy.deepcopy(saved);old['schema']=schema;del old['layers'][0]['imageColormap'];before=copy.deepcopy(old)
  assert views.compatible(old,frame)['layers'][0]['imageColormap']==MAP and old==before
  with pytest.raises(ValueError):views.preferences({**saved,'schema':schema})
 del frame['layers'][0]['imageColormap']
 with pytest.raises(ValueError,match='colors'):views.compatible(saved,frame)


def test_actual_colors_match_native_and_preserve_data(actual_host):
 import numpy as np
 from orchestrator import scientific_host as parent
 from tests.test_native_regions import source
 prepared=actual_host;data=np.arange(4032,dtype='<u2').reshape(6,24,28)
 with parent.review(prepared['key'],Path(prepared['folder']).name,{'volume':source(data,[])}) as worker:
  current,baseline=worker.request({'op':'render','width':256,'height':192});frame=current['frame']
  assert 'viridis' in frame['layers'][0]['colormapChoices']
  for bad in [{'name':'unknown-native-map'},{**MAP,'controls':[.5,1]}]:
   with pytest.raises(parent.ChangeRefused):
    worker.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],'operation':'image-colormap','value':{'layer':frame['layers'][0]['id'],'settings':bad}}})
  for name in ['viridis','magenta','gray']:
   changed,pixels=worker.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],'operation':'image-colormap','value':{'layer':frame['layers'][0]['id'],'settings':{'name':name}}}})
   worker.request({'op':'python-resources','source':f"viewer.layers[0].colormap={name!r}"})
   current,reference=worker.request({'op':'render','width':256,'height':192});frame=current['frame'];assert pixels==reference
   if name=='viridis':assert pixels!=baseline
  custom={**MAP,'interpolation':'zero','controls':[0.,.4,1.],'highColor':[1.,0.,0.,1.],'lowColor':[0.,0.,1.,1.]}
  changed,pixels=worker.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],'operation':'image-colormap','value':{'layer':frame['layers'][0]['id'],'settings':custom}}})
  code=f"from napari.utils.colormaps import Colormap\nviewer.layers[0].colormap=Colormap({custom['colors']!r}, name='independent-reference', controls={custom['controls']!r}, interpolation='zero', nan_color={custom['nanColor']!r}, high_color={custom['highColor']!r}, low_color={custom['lowColor']!r})"
  result,_=worker.request({'op':'python-resources','source':code});assert not result['console']['error']
  current,reference=worker.request({'op':'render','width':256,'height':192});assert pixels==reference
  result,_=worker.request({'op':'python-resources','source':"import numpy as np\nassert np.array_equal(viewer.layers[0].data,np.arange(4032,dtype='<u2').reshape(6,24,28))"});assert not result['console']['error']

  result,_=worker.request({'op':'python-resources','source':"viewer.add_image(np.zeros((24,28,3),dtype=np.uint8),rgb=True,name='RGB already colored')"});assert not result['console']['error']
  current,_=worker.request({'op':'render','width':256,'height':192});frame=current['frame']
  rgb=next(layer for layer in frame['layers'] if layer['name']=='RGB already colored')
  assert 'imageColormap' not in rgb
  with pytest.raises(parent.ChangeRefused):
   worker.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],'operation':'image-colormap','value':{'layer':rgb['id'],'settings':{'name':'viridis'}}}})


def test_actual_custom_map_restores_in_fresh_worker(actual_host):
 import numpy as np
 from orchestrator import scientific_host as parent
 from tests.test_native_regions import source
 from env_worker.scientific_output_wire import calibration
 prepared=actual_host;data=np.arange(4032,dtype='<u2').reshape(6,24,28)
 metadata={'format':'native-layer-output-v1','shape':list(data.shape),'dtype':'<u2','name':'volume','type':'image','visible':True,'opacity':1.,
  'geometry':{'basis':[[0,0,0],[1,0,0],[0,1,0],[0,0,1]],'axisLabels':['z','y','x'],'units':['micrometer']*3}}
 def launch():
  raw=source(data,[]);raw.descriptor.update(semantic='intensity',calibration=calibration(metadata,raw.axes,{'session':'s1','id':'coordinates','revision':1}))
  return parent.review(prepared['key'],Path(prepared['folder']).name,{'volume':raw},retained_layers=[{'input':'volume','metadata':metadata}])
 with launch() as worker:
  current,_=worker.request({'op':'render','width':256,'height':192});frame=current['frame']
  changed,pixels=worker.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],'operation':'image-colormap','value':{'layer':frame['layers'][0]['id'],'settings':MAP}}})
  saved=views.from_frame(changed['frame']);assert saved['schema']==12
  assert saved['dimensions']['margins']=={'left':[0,0,0],'right':[0,0,0]}
  assert saved['layers'][0]['imageProjection']=='mean'
 with launch() as worker:
  worker.request({'op':'python-resources','source':"from napari.utils.colormaps import Colormap, ensure_colormap\nensure_colormap(Colormap([[0,0,1,1],[0,1,0,1]],name='custom-test'))"})
  current,_=worker.request({'op':'render','width':256,'height':192})
  result,restored=worker.request({'op':'change','width':256,'height':192,'request':{'frame':current['frame']['frame'],'operation':'restore-view','value':saved}})
  actual=views.from_frame(result['frame'])
  assert actual['layers'][0]['imageColormap']['name']=='custom-test (1)'
  renamed=copy.deepcopy(saved);renamed['layers'][0]['imageColormap']['name']='custom-test (1)'
  assert views.restored(renamed,actual) and pixels==restored


def test_palette_name_limits_match_browser_code_units():
 assert colors.settings({**MAP,'name':'😀'*120})['name']=='😀'*120
 with pytest.raises(ValueError):colors.check_choices(['😀'*121])


def test_new_palette_reserves_all_image_rows_and_choice_count():
 class Map:
  name='new-custom'
  colors=SimpleNamespace(tolist=lambda:MAP['colors'])
  controls=SimpleNamespace(tolist=lambda:MAP['controls'])
  interpolation='linear';nan_color=SimpleNamespace(tolist=lambda:MAP['nanColor']);high_color=low_color=None
 cmap=Map()
 from env_worker import scientific_view_wire as wire
 frame={'layers':[{'id':str(i),'imageColormap':MAP,'colormapChoices':['gray']} for i in range(64)]}
 import json
 projected=copy.deepcopy(frame)
 for row in projected['layers']:row['colormapChoices'].append(cmap.name)
 projected['layers'][0]['imageColormap']=colors.snapshot_map(cmap)
 size=len(json.dumps(projected,ensure_ascii=False,separators=(',',':')).encode())
 frame['padding']='x'*(wire.MAX_BYTES-256-size+1)
 before=copy.deepcopy(frame)
 with pytest.raises(ValueError):colors.reserve(frame,{'0':cmap})
 assert frame==before
 frame={'layers':[{'id':'image','imageColormap':MAP,'colormapChoices':[str(n) for n in range(1024)]}]}
 with pytest.raises(ValueError,match='choices'):colors.reserve(frame,{'image':cmap})


def test_actual_restore_budget_refusal_keeps_default_frame_for_retry(actual_host):
 import numpy as np
 from orchestrator import scientific_host as parent
 from tests.test_native_regions import source
 from env_worker.scientific_output_wire import calibration
 prepared=actual_host;data=np.arange(4032,dtype='<u2').reshape(6,24,28)
 metadata={'format':'native-layer-output-v1','shape':list(data.shape),'dtype':'<u2','name':'volume','type':'image','visible':True,'opacity':1.,
  'geometry':{'basis':[[0,0,0],[1,0,0],[0,1,0],[0,0,1]],'axisLabels':['z','y','x'],'units':['micrometer']*3}}
 raw=source(data,[]);raw.descriptor.update(semantic='intensity',calibration=calibration(metadata,raw.axes,{'session':'s1','id':'coordinates','revision':1}))
 with parent.review(prepared['key'],Path(prepared['folder']).name,{'volume':raw},retained_layers=[{'input':'volume','metadata':metadata}]) as worker:
  code="from napari.utils.colormaps import Colormap, ensure_colormap\nfrom napari.utils.colormaps.colormap_utils import AVAILABLE_COLORMAPS\nfor i in range(1024-len(AVAILABLE_COLORMAPS)):\n ensure_colormap(Colormap([[0,0,0,1],[1,1,1,1]],name=f'budget-{i}'))"
  result,_=worker.request({'op':'python-resources','source':code});assert not result['console']['error']
  current,pixels=worker.request({'op':'render','width':256,'height':192});frame=current['frame'];saved=views.from_frame(frame)
  assert len(frame['layers'][0]['colormapChoices'])==1024
  wanted=copy.deepcopy(saved);wanted['layers'][0]['imageColormap']=MAP
  with pytest.raises(parent.RestoreRefused):
   worker.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],'operation':'restore-view','value':wanted}})
  assert worker.process.poll() is None
  restored,after=worker.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],'operation':'restore-view','value':saved}})
  assert pixels==after and views.restored(saved,views.from_frame(restored['frame']))


def test_restore_budget_projects_other_presentation_settings_together():
 from tests.test_scientific_view_preferences import FRAME,CASES
 from env_worker import scientific_view_wire as wire
 frame=copy.deepcopy(FRAME);frame['layers'][0].update(viewKey='raw',viewGeometry=CASES['valid']['layers'][0]['geometry'],imageColormap=MAP,colormapChoices=['gray'])
 saved=views.from_frame(frame)
 saved['layers'][0]['imageSampling']={'interpolation2d':'custom','interpolation3d':'linear','kernel2d':[[.1]*32 for _ in range(32)]}
 frame['padding']='x'*(wire.MAX_BYTES-25000)
 with pytest.raises(ValueError):colors.reserve(views.restored_frame_budget(saved,frame),{})
 assert 'imageSampling' not in frame['layers'][0]
