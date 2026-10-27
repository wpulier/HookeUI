"""Native display controls refuse stale/invalid changes and preserve scientific values."""
import copy
from pathlib import Path
from types import SimpleNamespace
import pytest
from env_worker import scientific_host, scientific_path_appearance as paths, scientific_view_preferences as views
from tests.test_scientific_change_recovery import host
from tests.test_scientific_host_worker import actual_host  # noqa: F401

VECTOR={'kind':'vectors','length':2.5,'width':3.,'style':'arrow','projection':'none'}
TRACK={'kind':'tracks','width':3.,'tail':12,'head':4,'showIds':True,'showTail':True,'showGraph':True,'hideCompleted':False}

@pytest.mark.parametrize('base,patch',[(VECTOR,{'length':float('inf')}),(VECTOR,{'length':10**400}),
 (VECTOR,{'width':-1}),(VECTOR,{'style':'sphere'}),(VECTOR,{'projection':'wrong'}),
 (TRACK,{'width':21}),(TRACK,{'width':.4}),(TRACK,{'head':1.5}),(TRACK,{'tail':True}),
 (TRACK,{'showGraph':1}),(TRACK,{'kind':'vectors'})])
def test_refused_settings_do_not_consume_private_frame(base,patch):
 session=host();layer=SimpleNamespace(_type_string=base['kind'],_max_width=20);session._layers=[('path',layer)]
 before=copy.deepcopy(vars(layer))
 with pytest.raises(scientific_host.ChangeRefused):
  session.change({'frame':'current','operation':'path-presentation','value':{'layer':'path','settings':{**base,**patch}}})
 assert vars(layer)==before and session._frame=='current' and not session.dirty


def test_legacy_preferences_do_not_reset_native_path_display():
 from tests.test_scientific_view_preferences import FRAME,CASES
 frame=copy.deepcopy(FRAME);row=frame['layers'][0]
 row.update(type='vectors',viewKey='raw',viewGeometry=CASES['valid']['layers'][0]['geometry'],pathAppearance=VECTOR)
 saved=views.from_frame(frame);assert saved['schema']==8
 old=copy.deepcopy(saved);old['schema']=1;del old['layers'][0]['pathAppearance']
 assert views.compatible(old,frame)['layers'][0]['pathAppearance']==VECTOR
 assert 'pathAppearance' not in old['layers'][0]
 with pytest.raises(ValueError):views.preferences({**saved,'schema':7})
 del row['pathAppearance']
 with pytest.raises(ValueError,match='path display'):views.compatible(saved,frame)


@pytest.mark.parametrize('kind',['vectors','tracks'])
def test_actual_path_controls_match_native_and_restore_ranges(actual_host,kind):
 import numpy as np
 from orchestrator import scientific_host as parent
 from tests.test_native_regions import source
 from tests.test_scientific_vectors_output import CREATE as VECTOR_CREATE
 from tests.test_scientific_tracks_output import CREATE as TRACK_CREATE
 prepared=actual_host;input_data=np.arange(4032,dtype='<u2').reshape(6,24,28)
 def launch():return parent.review(prepared['key'],Path(prepared['folder']).name,{'volume':source(input_data,[])})
 create=VECTOR_CREATE if kind=='vectors' else TRACK_CREATE
 changed_settings=({**VECTOR,'length':4.25,'width':5.,'style':'triangle','projection':'all'} if kind=='vectors'
    else {**TRACK,'width':7.,'tail':1,'head':0,'showIds':False,'showTail':False,'showGraph':False,'hideCompleted':True})
 with launch() as worker:
  result,_=worker.request({'op':'python-resources','source':create+'\noriginal_bytes=points.data.tobytes()'})
  assert not result['console']['error']
  # Bind this test-created layer through the production binding object, only for
  # retained-view tests. Ordinary output save creates this binding on reopen.
  result,_=worker.request({'op':'python-resources','source':"assert 'viewer' in globals()"})
  current,_=worker.request({'op':'render','width':256,'height':192});frame=current['frame']
  if kind=='vectors':
   with pytest.raises(parent.ChangeRefused):
    worker.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],'operation':'path-presentation','value':{'layer':frame['layers'][0]['id'],'settings':{**VECTOR,'length':2**53-1}}}})
   # The exact frame remains usable: correction follows without discarding the session.
  changed,pixels=worker.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],'operation':'path-presentation','value':{'layer':frame['layers'][0]['id'],'settings':changed_settings}}})
  assert changed['frame']['layers'][0]['pathAppearance']==changed_settings
  direct='\n'.join(f'setattr(points,{native!r},{changed_settings[key]!r})' for key,native in paths.NATIVE[kind].items())
  result,_=worker.request({'op':'python-resources','source':direct+'\nassert points.data.tobytes()==original_bytes'})
  assert not result['console']['error']
  native,reference=worker.request({'op':'render','width':256,'height':192});assert pixels==reference
  # Native projection must calculate the same new bounds without touching the
  # current layer or triggering events; the original native state remains intact.
  if kind=='vectors':
   result,_=worker.request({'op':'python-resources','source':"from env_worker.scientific_path_appearance import projected_ranges, refresh_ranges\nfrom types import SimpleNamespace\nbefore_range=list(viewer.dims.range)\nbefore_length=points.length\noriginal_copy=type(points).__copy__\ndef forbidden_copy(self): raise AssertionError('Preflight invoked native copy')\ntype(points).__copy__=forbidden_copy\nseen=[]\npoints.events.length.connect(lambda event:seen.append(1))\nprojected=projected_ranges(viewer,SimpleNamespace(key_for=lambda layer:'v'),{'layers':[{'key':'v','pathAppearance':{'kind':'vectors','length':2.5}}]})\nassert points.length==before_length and list(viewer.dims.range)==before_range and not seen\ntype(points).__copy__=original_copy\npoints.length=2.5\nrefresh_ranges(viewer)\nassert projected==[list(bounds) for bounds in viewer.dims.range]\npoints.length=before_length"})
   assert not result['console']['error'],result

   result,_=worker.request({'op':'python-resources','source':"viewer.dims.events.range.connect(lambda event:setattr(points,'opacity',.4))\npoints.length=3.5"})
   assert not result['console']['error']
   callback,_=worker.request({'op':'render','width':256,'height':192})
   assert callback['frame']['layers'][0]['opacity']==.4
