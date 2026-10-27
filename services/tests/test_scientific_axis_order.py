"""Native axis shortcuts retain dimension identity and refuse stale actions."""
from pathlib import Path
from types import SimpleNamespace
import pytest
from env_worker import scientific_host as local
from tests.test_scientific_change_recovery import host
from tests.test_scientific_host_worker import actual_host  # noqa: F401

@pytest.mark.parametrize('value',[None,{},[],True,'rotate',''])
def test_invalid_axis_action_preserves_frame_and_private_work(value):
 session=host();calls=[];session.viewer.dims=SimpleNamespace(roll=lambda:calls.append('roll'),transpose=lambda:calls.append('transpose'))
 with pytest.raises(local.ChangeRefused):session.change({'frame':'current','operation':'axis-order','value':value})
 assert not calls and not session.dirty and session._frame=='current'

@pytest.mark.parametrize('action',['roll','transpose'])
def test_axis_shortcut_uses_native_method_only_after_current_frame_admission(action):
 session=host();calls=[];session.viewer.dims=SimpleNamespace(roll=lambda:calls.append('roll'),transpose=lambda:calls.append('transpose'))
 with pytest.raises(local.ChangeRefused):session.change({'frame':'old','operation':'axis-order','value':action})
 assert not calls and not session.dirty and session._frame=='current'
 session.change({'frame':'current','operation':'axis-order','value':action})
 assert calls==[action] and session.dirty and session._frame is None

def test_actual_axis_shortcuts_match_reference_with_singletons_and_locked_axes(actual_host):
 import numpy as np
 from orchestrator import scientific_host as parent
 from tests.test_native_regions import source
 shape=(2,1,4,8,10);data=np.arange(np.prod(shape),dtype='<u2').reshape(shape);original=data.tobytes()
 prepared=actual_host
 with parent.review(prepared['key'],Path(prepared['folder']).name,{'volume':source(data,[],axes=('t','c','z','y','x'))}) as worker:
  result,_=worker.request({'op':'python-resources','source':"import numpy as np\nlayer=viewer.layers[0]\nlayer.scale=(3,2,4,.5,.25)\nlayer.translate=(0,10,20,30,40)\nviewer.dims.rollable=(False,True,True,True,True)\nviewer.dims.point=tuple(layer.data_to_world((1,0,2,3,4)))\nlayer.contrast_limits=(0,640)"})
  assert not result['console']['error'],result
  for dimensions in (2,3):
   for order in [(0,1,2,3,4),(4,1,0,3,2),(1,3,0,4,2)]:
    for action in ('roll','transpose'):
     result,_=worker.request({'op':'python-resources','source':f'viewer.dims.ndisplay={dimensions}\nviewer.dims.order={order!r}'})
     assert not result['console']['error'],result
     worker.request({'op':'render','width':256,'height':192})
     worker.request({'op':'python-resources','source':'viewer.reset_view()'})
     current,_=worker.request({'op':'render','width':256,'height':192});before=current['frame']
     expected=list(order)
     if action=='transpose':expected[-2],expected[-1]=expected[-1],expected[-2]
     else:
      movable=[axis for axis in order if axis!=0 and shape[axis]>1];rotated=iter(movable[-1:]+movable[:-1])
      expected=[next(rotated) if axis in movable else axis for axis in order]
     changed,pixels=worker.request({'op':'change','width':256,'height':192,'request':{'frame':before['frame'],'operation':'axis-order','value':action}})
     assert changed['frame']['dimensions']['order']==expected
     assert changed['frame']['dimensions']['point']==before['dimensions']['point']
     assert len(set(pixels))>4
     displayed=expected[-dimensions:];indices=[1,0,2,3,4]
     index=','.join('slice(None)' if axis in displayed else str(indices[axis]) for axis in range(5))
     permutation=tuple(sorted(displayed).index(axis) for axis in displayed)
     code=f'assert np.array_equal(layer._slice.image.raw,np.transpose(layer.data[{index}],{permutation!r}))\nviewer.dims.order={order!r}\nviewer.dims.{action}()'
     result,_=worker.request({'op':'python-resources','source':code});assert not result['console']['error'],result
     native,reference=worker.request({'op':'render','width':256,'height':192});assert pixels==reference
     assert native['frame']['dimensions']['order']==expected
     with pytest.raises(parent.ChangeRefused):worker.request({'op':'change','width':256,'height':192,'request':{'frame':before['frame'],'operation':'axis-order','value':action}})
  result,_=worker.request({'op':'python-resources','source':"assert np.array_equal(layer.data,np.arange(640,dtype='<u2').reshape(2,1,4,8,10))\nassert layer.data_to_world((1,0,2,3,4))==(3,10,28,31.5,41)"})
  assert not result['console']['error'],result
 assert data.tobytes()==original
