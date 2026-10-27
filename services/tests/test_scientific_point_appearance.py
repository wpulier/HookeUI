"""Point controls use pinned upstream setters with exact scientific preservation."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from env_worker import scientific_host, scientific_point_appearance as points, scientific_view_preferences as views
from tests.test_scientific_change_recovery import host
from tests.test_scientific_host_worker import actual_host  # noqa: F401

CASES=json.loads((Path(__file__).resolve().parents[2]/'contract/scientific-point-appearance-cases.json').read_text())
DISPLAY=CASES['valid']

@pytest.mark.parametrize('case',CASES['invalid'],ids=lambda case:case['name'])
def test_invalid_point_controls_preserve_current_frame_and_native_layer(case):
 session=host();layer=SimpleNamespace(_type_string='points');session._layers=[('points',layer)]
 with pytest.raises(scientific_host.ChangeRefused):
  session.change({'frame':'current','operation':'point-presentation','value':{'layer':'points','settings':{**DISPLAY,**case['patch']}}})
 assert vars(layer)=={'_type_string':'points'} and session._frame=='current' and not session.dirty

@pytest.mark.parametrize('value',[float('nan'),float('inf'),10**400])
def test_nonfinite_point_display_is_refused(value):
 with pytest.raises(ValueError):points.settings({**DISPLAY,'antialiasing':value})


def test_point_preferences_preserve_legacy_view_and_refuse_missing_support():
 from tests.test_scientific_view_preferences import FRAME,CASES as VIEW_CASES
 frame=copy.deepcopy(FRAME);row=frame['layers'][0]
 row.update(type='points',viewKey='raw',viewGeometry=VIEW_CASES['valid']['layers'][0]['geometry'],pointAppearance=DISPLAY)
 saved=views.from_frame(frame);assert saved['schema']==10
 old=copy.deepcopy(saved);old['schema']=1;del old['layers'][0]['pointAppearance']
 assert views.compatible(old,frame)['layers'][0]['pointAppearance']==DISPLAY
 with pytest.raises(ValueError):views.preferences({**saved,'schema':9})
 del row['pointAppearance']
 with pytest.raises(ValueError,match='point display'):views.compatible(saved,frame)


@pytest.mark.parametrize('projection',['none','all','rescale_linear','rescale_spherical'])
@pytest.mark.parametrize('dimensions',[2,3])
def test_actual_point_controls_match_native_preserve_features_and_reopen(actual_host,tmp_path,projection,dimensions):
 from orchestrator.scientific_host import ChangeRefused
 from orchestrator.scientific_projects import ProjectStore
 from orchestrator.scientific_project_review import review
 from orchestrator.scientific_outputs import OutputSave
 from tests.test_scientific_surface_output import source
 from tests.test_scientific_points_output import CREATE
 store=ProjectStore(tmp_path/'data');original=source(store,actual_host)
 with review(store,original.id,'s1',original.revision) as (_,worker):
  code=CREATE+f'\nviewer.dims.ndisplay={dimensions}\nviewer.dims.point=tuple(points.data_to_world(points.data[0]))\nviewer.reset_view()\n'
  code+='original_coordinates=points.data.copy()\noriginal_features=points.features.copy(deep=True)\noriginal_sizes=points.size.copy()\noriginal_symbols=points.symbol.copy()\noriginal_colors=points.face_color.copy()'
  result,_=worker.request({'op':'python-resources','source':code});assert not result['console']['error'],result
  # Fit after the production render establishes its viewport. Fitting the default
  # large native canvas first can leave every marker outside the smaller image.
  worker.request({'op':'render','width':256,'height':192})
  result,_=worker.request({'op':'python-resources','source':'viewer.reset_view()'})
  assert not result['console']['error'],result
  before,_=worker.request({'op':'render','width':256,'height':192});frame=before['frame']
  desired={**DISPLAY,'projection':projection,'shading':'spherical' if dimensions==3 else 'none',
           'canvasLimits':[2,1e19] if projection=='all' else DISPLAY['canvasLimits']}
  with pytest.raises(ChangeRefused):
   worker.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],
    'operation':'point-presentation','value':{'layer':frame['layers'][0]['id'],'settings':{**desired,'canvasLimits':[80,2]}}}})
  assert worker.process.poll() is None
  changed,pixels=worker.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],
   'operation':'point-presentation','value':{'layer':frame['layers'][0]['id'],'settings':desired}}})
  assert changed['frame']['layers'][0]['pointAppearance']==desired
  assert len(set(pixels))>4, 'Reference comparisons must render actual colored points, not an empty frame'
  with pytest.raises(ChangeRefused):
   worker.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],
    'operation':'point-presentation','value':{'layer':frame['layers'][0]['id'],'settings':desired}}})
  code="points.shading='none'\npoints.projection_mode='all'\npoints.antialiasing=0\npoints.canvas_size_limits=(0,10000)\n"
  code+='\n'.join(f'points.{native}={desired[key]!r}' for key,native in [('shading','shading'),('projection','projection_mode'),('antialiasing','antialiasing'),('canvasLimits','canvas_size_limits')])
  code+='\nassert np.array_equal(points.data,original_coordinates)\nassert points.features.equals(original_features)\nassert np.array_equal(points.size,original_sizes)\nassert np.array_equal(points.symbol,original_symbols)\nassert np.array_equal(points.face_color,original_colors)'
  result,_=worker.request({'op':'python-resources','source':code});assert not result['console']['error'],result
  native,reference=worker.request({'op':'render','width':256,'height':192});assert pixels==reference
  assert native['frame']['layers'][0]['pointAppearance']==desired
  save=OutputSave(store,original,'point-display-123456789',frame['layers'][0]['id'],{'semantic':'point-set','axes':['z','y','x']})
  save.capture(worker);save.publish();saved=store.read(save.target,'s1');save.close()
 with review(store,saved.id,'s1',1) as (_,worker):
  reopened,_=worker.request({'op':'render','width':256,'height':192});assert reopened['frame']['layers'][0]['pointAppearance']==desired
  preferences=views.from_frame(reopened['frame']);altered=copy.deepcopy(preferences)
  altered['layers'][0]['pointAppearance']={**desired,'antialiasing':2.25,'projection':'rescale_linear'}
  changed,_=worker.request({'op':'change','width':256,'height':192,'request':{'frame':reopened['frame']['frame'],'operation':'restore-view','value':altered}})
  assert views.restored(altered,views.from_frame(changed['frame']))
  result,_=worker.request({'op':'python-resources','source':"assert int(viewer.layers[0].features['id'].iloc[0])==2**63+1\nassert viewer.layers[0].size.tolist()==[3,7,5]\nviewer.layers[0].antialiasing=3.5"})
  assert not result['console']['error'],result
  shown,_=worker.request({'op':'render','width':256,'height':192});assert shown['frame']['layers'][0]['pointAppearance']['antialiasing']==3.5
 assert store.read(original.id,'s1')==original and store.read(saved.id,'s1')==saved
