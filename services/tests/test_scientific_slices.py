"""Native thick slices preserve raw values, calibration and remembered intent."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from env_worker import scientific_host, scientific_slices as slices, scientific_view_preferences as views
from tests.test_scientific_change_recovery import host
from tests.test_scientific_host_worker import actual_host  # noqa: F401

CASES=json.loads((Path(__file__).resolve().parents[2]/'contract/scientific-slice-cases.json').read_text())

@pytest.mark.parametrize('case',CASES['invalid'],ids=lambda case:case['name'])
def test_refused_margins_preserve_private_work_and_frame(case):
 session=host();session.viewer.dims=SimpleNamespace(ndim=5,margin_left=(0,)*5,margin_right=(0,)*5)
 before=copy.deepcopy(vars(session.viewer.dims))
 with pytest.raises(scientific_host.ChangeRefused):
  session.change({'frame':'current','operation':'slice-margins','value':case['value']})
 assert vars(session.viewer.dims)==before and session._frame=='current' and not session.dirty

@pytest.mark.parametrize('value',[float('nan'),float('inf'),10**400])
def test_nonfinite_slice_margins_refused(value):
 with pytest.raises(ValueError):slices.margins({'left':[value,0],'right':[0,0]},2)

@pytest.mark.parametrize('value',['average','median',True,None,{}])
def test_unknown_projection_refused_before_mutation(value):
 session=host();layer=SimpleNamespace(_type_string='image',projection_mode='none');session._layers=[('image',layer)]
 with pytest.raises(scientific_host.ChangeRefused):
  session.change({'frame':'current','operation':'image-projection','value':{'layer':'image','mode':value}})
 assert layer.projection_mode=='none' and session._frame=='current' and not session.dirty

def test_thickness_and_projection_preferences_preserve_older_views():
 from tests.test_scientific_view_preferences import FRAME,CASES as VIEW_CASES
 frame=copy.deepcopy(FRAME);frame['dimensions']['margins']={'left':[1,2,3],'right':[3,2,1]}
 frame['layers'][0].update(viewKey='raw',viewGeometry=VIEW_CASES['valid']['layers'][0]['geometry'],imageProjection='max')
 saved=views.from_frame(frame);assert saved['schema']==11
 old=copy.deepcopy(saved);old['schema']=1;del old['dimensions']['margins'];del old['layers'][0]['imageProjection']
 before=copy.deepcopy(old);assert views.compatible(old,frame)==saved and old==before
 with pytest.raises(ValueError):views.preferences({**saved,'schema':10})
 del frame['dimensions']['margins']
 with pytest.raises(ValueError,match='thickness'):views.compatible(saved,frame)

def test_actual_five_dimensional_slices_match_numpy_native_and_reopen(tmp_path,actual_host):
 import numpy as np
 from orchestrator import scientific_host as parent
 from orchestrator.scientific_projects import ProjectStore
 from orchestrator.scientific_project_review import review
 from orchestrator.scientific_outputs import OutputSave
 from tests.test_scientific_surface_output import source
 data=np.arange(2*3*5*16*18,dtype='<u2').reshape(2,3,5,16,18)
 original=data.tobytes();setup="""import numpy as np
layer=viewer.layers[0]
layer.scale=(3,2,4,.5,.25)
layer.translate=(0,10,20,30,40)
layer.axis_labels=('t','c','z','y','x')
viewer.dims.axis_labels=layer.axis_labels
viewer.dims.ndisplay=2
viewer.dims.point=(3,12,28,30,40)
layer.contrast_limits=(0,8640)
"""
 store=ProjectStore(tmp_path/'retained');original_project=source(store,actual_host)
 with review(store,original_project.id,'s1',original_project.revision) as (_,worker):
  create="import numpy as np\nviewer.layers[0].data=np.arange(2*3*5*16*18,dtype='<u2').reshape(2,3,5,16,18)\n"+setup
  result,_=worker.request({'op':'python-resources','source':create});assert not result['console']['error'],result
  current,_=worker.request({'op':'render','width':256,'height':192})
  save=OutputSave(store,original_project,'slab-native-123456789',current['frame']['layers'][0]['id'],{'semantic':'intensity','axes':['t','c','z','y','x']})
  save.capture(worker);save.publish();saved=store.read(save.target,'s1');save.close()
 view_setup="import numpy as np\nlayer=viewer.layers[0]\nviewer.dims.axis_labels=layer.axis_labels\nviewer.dims.ndisplay=2\nviewer.dims.point=(3,12,28,30,40)\nlayer.contrast_limits=(0,8640)"
 with review(store,saved.id,'s1',saved.revision) as (_,worker):
  result,_=worker.request({'op':'python-resources','source':view_setup});assert not result['console']['error'],result
  worker.request({'op':'render','width':256,'height':192})
  worker.request({'op':'python-resources','source':'viewer.reset_view()'})
  current,_=worker.request({'op':'render','width':256,'height':192});frame=current['frame']
  changed,_=worker.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],'operation':'slice-margins','value':CASES['margins']}})
  frame=changed['frame'];assert frame['dimensions']['margins']==CASES['margins']
  for dimension in (2,3):
   worker.request({'op':'python-resources','source':f'viewer.dims.ndisplay={dimension}\nviewer.reset_view()'})
   current,_=worker.request({'op':'render','width':256,'height':192});frame=current['frame']
   for mode in CASES['projections']:
    # Fixed fixture bounds keep both single slices and their sums visibly resolved.
    ceiling=8640*((18 if dimension==2 else 6) if mode=='sum' else 1)
    result,_=worker.request({'op':'python-resources','source':f'layer.contrast_limits=(0,{ceiling})'})
    assert not result['console']['error'],result
    current,_=worker.request({'op':'render','width':256,'height':192});frame=current['frame']
    changed,pixels=worker.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],'operation':'image-projection','value':{'layer':frame['layers'][0]['id'],'mode':mode}}})
    assert changed['frame']['layers'][0]['imageProjection']==mode and len(set(pixels))>4
    # Independent numeric oracle on the native slice, before its display conversion.
    selection='layer.data[1,1,2]' if dimension==2 else 'layer.data[1,1]'
    slab='layer.data[:,:,1:4]' if dimension==2 else 'layer.data'
    axes='(0,1,2)' if dimension==2 else '(0,1)'
    expected=selection if mode=='none' else f'np.{mode}({slab},axis={axes})'
    code=f"assert np.array_equal(layer._slice.image.raw,{expected}), (viewer.dims.order,viewer.dims.point,viewer.dims.current_step,layer._slice.image.raw.shape,{expected}.shape,layer._slice.image.raw.flat[0],{expected}.flat[0])\nlayer.projection_mode='none'\nlayer.projection_mode={mode!r}"
    result,_=worker.request({'op':'python-resources','source':code});assert not result['console']['error'],result
    native,reference=worker.request({'op':'render','width':256,'height':192});assert pixels==reference;frame=native['frame']
  remembered=views.from_frame(frame)
  oldframe=frame['frame'];result,_=worker.request({'op':'python-resources','source':'viewer.dims.margin_left=(0,0,0,0,0)'})
  assert not result['console']['error']
  with pytest.raises(parent.ChangeRefused):
   worker.request({'op':'change','width':256,'height':192,'request':{'frame':oldframe,'operation':'slice-margins','value':CASES['margins']}})
 with review(store,saved.id,'s1',saved.revision) as (_,worker):
  worker.request({'op':'python-resources','source':view_setup})
  current,_=worker.request({'op':'render','width':256,'height':192})
  restored,_=worker.request({'op':'change','width':256,'height':192,'request':{'frame':current['frame']['frame'],'operation':'restore-view','value':remembered}})
  assert views.restored(remembered,views.from_frame(restored['frame']))
  result,_=worker.request({'op':'python-resources','source':"assert np.array_equal(layer.data,np.arange(2*3*5*16*18,dtype='<u2').reshape(2,3,5,16,18))\nassert layer.data_to_world((0,0,0,0,0))==(0,10,20,30,40)\nassert layer.data_to_world((1,1,1,1,1))==(3,12,24,30.5,40.25)"})
  assert not result['console']['error'],result
 assert store.read(original_project.id,'s1')==original_project and store.read(saved.id,'s1')==saved
 retained=store.array_source(saved.id,'s1',saved.revision,saved.document['resources'][0]);assert retained.read(tuple(slice(0,n) for n in retained.shape)).tobytes()==original
 assert data.tobytes()==original


class _Dims:
    """Only what navigation validation reads; set_point and last_used record what would run natively."""
    def __init__(self, ndim=5, ndisplay=2, nsteps=(5, 3, 1, 11, 13)):
        self.ndim, self.ndisplay, self.nsteps = ndim, ndisplay, nsteps
        self.order = tuple(range(ndim))
        self.last_used, self.calls = 0, []

    @property
    def displayed(self):
        return self.order[-self.ndisplay:]

    @property
    def not_displayed(self):
        return self.order[:-self.ndisplay]

    def set_point(self, axis, value):
        self.calls.append((axis, value))


@pytest.mark.parametrize('operation,value', [
    ('viewer-action', 'delete_selected_layers'), ('viewer-action', None), ('dims-point', {'axis': 3, 'value': 1.0}),
    ('dims-point', {'axis': 0, 'value': float('nan')}), ('dims-point', {'axis': 0, 'value': True}),
    ('dims-point', {'axis': 0}), ('dims-point', {'axis': 9, 'value': 1}), ('dims-focus', 2), ('dims-focus', 4),
    ('dims-focus', True), ('dims-other', 0)])
def test_navigation_is_refused_before_any_native_call(operation, value):
    dims = _Dims()
    with pytest.raises(ValueError):
        slices.prepare_navigation(SimpleNamespace(dims=dims), operation, value)
    assert dims.calls == [] and dims.last_used == 0


def test_navigation_refuses_what_this_view_cannot_do():
    with pytest.raises(ValueError, match='three dimensions'):
        slices.prepare_navigation(SimpleNamespace(dims=_Dims(ndim=2, nsteps=(11, 13))), 'viewer-action', 'toggle_ndisplay')
    with pytest.raises(ValueError, match='more than one slice'):
        slices.prepare_navigation(SimpleNamespace(dims=_Dims(nsteps=(1, 1, 1, 11, 13))), 'viewer-action',
                                  'increment_dims_right')


def test_world_point_and_focus_run_napari_setters_only_when_applied():
    dims = _Dims()
    apply = slices.prepare_navigation(SimpleNamespace(dims=dims), 'dims-point', {'axis': 0, 'value': 16.3})
    assert dims.calls == []
    apply()
    assert dims.calls == [(0, 16.3)]
    focus = slices.prepare_navigation(SimpleNamespace(dims=dims), 'dims-focus', 1)
    assert dims.last_used == 0
    focus()
    assert dims.last_used == 1
    assert slices.navigation(SimpleNamespace(last_used=1, rollable=(True, False), units=None)) == \
        {'lastUsed': 1, 'rollable': [True, False], 'units': None}
