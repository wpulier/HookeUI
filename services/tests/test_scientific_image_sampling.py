"""Display reconstruction preserves scientific data and exact accepted view state."""
import copy
from pathlib import Path
from types import SimpleNamespace
import pytest
from env_worker import scientific_host, scientific_image_sampling as sampling, scientific_view_preferences as views
from tests.test_scientific_change_recovery import host
from tests.test_scientific_host_worker import actual_host  # noqa: F401

SETTINGS={'interpolation2d':'nearest','interpolation3d':'linear','kernel2d':[[1.]]}

@pytest.mark.parametrize('patch',[
 {'interpolation2d':'unknown'},{'interpolation3d':'custom'},{'kernel2d':[]},
 {'kernel2d':[[1],[2,3]]},{'kernel2d':[[float('nan')]]},{'kernel2d':[[True]]},
 {'kernel2d':[[1e39]]},{'kernel2d':[[10**400]]},{'kernel2d':[[1]*33]},
])
def test_invalid_sampling_preserves_private_frame(patch):
 session=host();layer=SimpleNamespace(_type_string='image');session._layers=[('image',layer)]
 before=copy.deepcopy(vars(layer))
 with pytest.raises(scientific_host.ChangeRefused):
  session.change({'frame':'current','operation':'image-sampling','value':{'layer':'image','settings':{**SETTINGS,**patch}}})
 assert vars(layer)==before and session._frame=='current' and not session.dirty


def test_sampling_roundtrip_keeps_legacy_records_and_rejects_missing_support():
 from tests.test_scientific_view_preferences import FRAME,CASES
 frame=copy.deepcopy(FRAME);frame['layers'][0].update(viewKey='raw',viewGeometry=CASES['valid']['layers'][0]['geometry'],imageSampling=SETTINGS)
 saved=views.from_frame(frame);assert saved['schema']==6
 for schema in [1,3,4,5]:
  old=copy.deepcopy(saved);old['schema']=schema;del old['layers'][0]['imageSampling'];before=copy.deepcopy(old)
  assert views.compatible(old,frame)['layers'][0]['imageSampling']==SETTINGS and old==before
  with pytest.raises(ValueError):views.preferences({**saved,'schema':schema})
 del frame['layers'][0]['imageSampling']
 with pytest.raises(ValueError,match='interpolation'):views.compatible(saved,frame)


def test_actual_interpolation_matches_upstream_and_preserves_array(actual_host):
 import numpy as np
 from orchestrator import scientific_host as parent
 from tests.test_native_regions import source
 prepared=actual_host;data=(np.indices((6,24,28)).sum(axis=0)%2).astype('<u2')
 with parent.review(prepared['key'],Path(prepared['folder']).name,{'volume':source(data,[])}) as worker:
  current,_=worker.request({'op':'render','width':256,'height':192});frame=current['frame']
  with pytest.raises(parent.ChangeRefused):
   worker.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],'operation':'image-sampling','value':{'layer':frame['layers'][0]['id'],'settings':{**SETTINGS,'kernel2d':[[10**400]]}}}})
  assert worker.process.poll() is None
  changed,_=worker.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],'operation':'image-sampling','value':{'layer':frame['layers'][0]['id'],'settings':SETTINGS}}})
  frame=changed['frame']
  different=[]
  for dimension in [2,3]:
   worker.request({'op':'python-resources','source':f'viewer.dims.ndisplay={dimension}'})
   current,baseline=worker.request({'op':'render','width':256,'height':192});frame=current['frame']
   for mode in sampling.MODES:
    requested={**SETTINGS,f'interpolation{dimension}d':mode}
    changed,pixels=worker.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],'operation':'image-sampling','value':{'layer':frame['layers'][0]['id'],'settings':requested}}})
    assert changed['frame']['layers'][0]['imageSampling']==requested
    worker.request({'op':'python-resources','source':f"viewer.layers[0].interpolation{dimension}d='nearest'\nviewer.layers[0].interpolation{dimension}d={mode!r}"})
    current,reference=worker.request({'op':'render','width':256,'height':192});assert pixels==reference;frame=current['frame']
    if dimension==2:different.append(pixels!=baseline)
  assert any(different)
  worker.request({'op':'python-resources','source':'viewer.dims.ndisplay=2'})
  current,_=worker.request({'op':'render','width':256,'height':192});frame=current['frame']
  worker.request({'op':'python-resources','source':"viewer.layers[0].interpolation2d='nearest'"})
  current,nearest=worker.request({'op':'render','width':256,'height':192});frame=current['frame']
  custom={**SETTINGS,'interpolation2d':'custom','kernel2d':[[0.,.25,0.],[.25,0.,.25],[0.,.25,0.]]}
  changed,pixels=worker.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],'operation':'image-sampling','value':{'layer':frame['layers'][0]['id'],'settings':custom}}})
  assert changed['frame']['layers'][0]['imageSampling']==custom
  assert pixels!=nearest, 'The custom reconstruction must visibly change the displayed checkerboard'
  result,_=worker.request({'op':'python-resources','source':f"import numpy as np\nviewer.layers[0].interpolation2d='nearest'\nviewer.layers[0].custom_interpolation_kernel_2d=np.array({custom['kernel2d']!r},dtype=np.float32)\nviewer.layers[0].interpolation2d='custom'"})
  assert not result['console']['error']
  reference,reference_pixels=worker.request({'op':'render','width':256,'height':192})
  assert pixels==reference_pixels
  result,_=worker.request({'op':'python-resources','source':"import numpy as np\nassert np.array_equal(viewer.layers[0].data,(np.indices((6,24,28)).sum(axis=0)%2).astype('<u2'))"})
  assert not result['console']['error']


def test_actual_sampling_restores_in_new_worker(actual_host):
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
 custom={**SETTINGS,'interpolation2d':'custom','kernel2d':[[.1,.2],[.3,.4]]}
 with launch() as worker:
  current,_=worker.request({'op':'render','width':256,'height':192});frame=current['frame']
  changed,pixels=worker.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],'operation':'image-sampling','value':{'layer':frame['layers'][0]['id'],'settings':custom}}})
  saved=views.from_frame(changed['frame']);assert saved['schema']==12
  assert saved['dimensions']['margins']=={'left':[0,0,0],'right':[0,0,0]}
  assert saved['layers'][0]['imageProjection']=='mean'
 with launch() as worker:
  current,_=worker.request({'op':'render','width':256,'height':192})
  result,restored=worker.request({'op':'change','width':256,'height':192,'request':{'frame':current['frame']['frame'],'operation':'restore-view','value':saved}})
  assert views.restored(saved,views.from_frame(result['frame'])) and pixels==restored
