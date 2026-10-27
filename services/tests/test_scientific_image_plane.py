"""Native image planes retain position/orientation without changing scientific data."""
import copy
from types import SimpleNamespace
import pytest
from env_worker import scientific_host
from tests.test_scientific_change_recovery import host

PLANE = {'depiction':'plane','position':[2.,11.,13.], 'normal':[1.,0.,0.], 'thickness':2.}

@pytest.mark.parametrize('patch', [
    {'normal':[0,0,0]}, {'normal':[float('nan'),0,1]}, {'position':[1,2]},
    {'thickness':-1}, {'thickness':True}, {'depiction':'unknown'},
])
def test_invalid_plane_refuses_before_any_native_mutation(patch):
    session=host()
    layer=SimpleNamespace(_type_string='image',depiction='volume',plane=SimpleNamespace(position=(0,0,0),normal=(1,0,0),thickness=1))
    session._layers=[('layer',layer)]
    before=copy.deepcopy(vars(layer))
    with pytest.raises(scientific_host.ChangeRefused):
        session.change({'frame':'current','operation':'image-plane','value':{'layer':'layer','settings':{**PLANE,**patch}}})
    assert vars(layer)==before and session._frame=='current' and not session.dirty


def test_valid_plane_changes_only_presentation_and_refuses_stale_frame():
    session=host()
    layer=SimpleNamespace(_type_string='image',depiction='volume',plane=SimpleNamespace(position=(0,0,0),normal=(1,0,0),thickness=1))
    session._layers=[('layer',layer)]
    session.change({'frame':'current','operation':'image-plane','value':{'layer':'layer','settings':PLANE}})
    assert layer.depiction=='plane' and list(layer.plane.position)==PLANE['position']
    assert list(layer.plane.normal)==PLANE['normal'] and layer.plane.thickness==2
    with pytest.raises(scientific_host.ChangeRefused):
        session.change({'frame':'old','operation':'image-plane','value':{'layer':'layer','settings':{**PLANE,'depiction':'volume'}}})
    assert layer.depiction=='plane'


def test_plane_view_roundtrip_preserves_legacy_intent_and_rejects_missing_support():
    from env_worker import scientific_view_preferences as views
    from tests.test_scientific_view_preferences import FRAME, CASES
    from tests.test_scientific_image_appearance import SETTINGS
    frame=copy.deepcopy(FRAME)
    frame['layers'][0].update(viewKey='raw',viewGeometry=copy.deepcopy(CASES['valid']['layers'][0]['geometry']),
                              imagePlane=copy.deepcopy(PLANE),imageAppearance=copy.deepcopy(SETTINGS),blending='opaque')
    saved=views.from_frame(frame);assert saved['schema']==4
    assert saved['layers'][0]['imagePlane']==PLANE
    for schema in (1,2,3):
        old=copy.deepcopy(saved);old['schema']=schema;old['layers'][0].pop('imagePlane')
        if schema<3:old['layers'][0].pop('blending')
        if schema<2:old['layers'][0].pop('imageAppearance')
        before=copy.deepcopy(old)
        assert views.compatible(old,frame)['layers'][0]['imagePlane']==PLANE
        assert old==before
        with pytest.raises(ValueError):views.preferences({**saved,'schema':schema})
    shown=copy.deepcopy(saved);shown['layers'][0]['imagePlane']['normal'][0]+=1e-15
    assert views.restored(saved,shown)
    shown['layers'][0]['imagePlane']['normal'][0]+=.001
    assert not views.restored(saved,shown)
    del frame['layers'][0]['imagePlane']
    with pytest.raises(ValueError,match='plane'):views.compatible(saved,frame)


from tests.test_scientific_host_worker import actual_host  # noqa: F401


def test_actual_planes_match_native_rendering_and_restore_without_data_changes(actual_host):
    import numpy as np
    from pathlib import Path
    from orchestrator import scientific_host as parent
    from env_worker import scientific_view_preferences as views
    from tests.test_native_regions import source
    prepared=actual_host;values=np.arange(4032,dtype='<u2').reshape(6,24,28)
    from env_worker.scientific_output_wire import calibration
    metadata={'format':'native-layer-output-v1','shape':list(values.shape),'dtype':'<u2','name':'volume',
        'type':'image','visible':True,'opacity':1.,'geometry':{'basis':[[9,8,7],[12,8,7],[9,10,7],[9,8,8]],
        'axisLabels':['z','y','x'],'units':['micrometer']*3}}
    def launch():
        data=source(values,[])
        data.descriptor.update(semantic='intensity',calibration=calibration(metadata,data.axes,{'session':'s1','id':'coordinates','revision':1}))
        return parent.review(prepared['key'],Path(prepared['folder']).name,{'volume':data},retained_layers=[{'input':'volume','metadata':metadata}])
    with launch() as worker:
        result,_=worker.request({'op':'python-resources','source':'viewer.dims.ndisplay=3'})
        assert not result['console']['error']
        current,_=worker.request({'op':'render','width':256,'height':192});frame=current['frame']
        for settings in [PLANE,{**PLANE,'normal':[1,1,0],'thickness':3},{**PLANE,'depiction':'volume'},PLANE]:
            changed,pixels=worker.request({'op':'change','width':256,'height':192,'request':{
                'frame':frame['frame'],'operation':'image-plane','value':{'layer':frame['layers'][0]['id'],'settings':settings}}})
            plane=changed['frame']['layers'][0]['imagePlane']
            assert plane['position']==settings['position'] and plane['thickness']==settings['thickness']
            np.testing.assert_allclose(plane['normal'],np.array(settings['normal'])/np.linalg.norm(settings['normal']),atol=1e-12)
            result,_=worker.request({'op':'python-resources','source':
                f"layer=viewer.layers[0]\nlayer.depiction='volume'\nlayer.plane.position={settings['position']!r}\nlayer.plane.normal={settings['normal']!r}\nlayer.plane.thickness={settings['thickness']!r}\nlayer.depiction={settings['depiction']!r}"})
            assert not result['console']['error']
            current,reference=worker.request({'op':'render','width':256,'height':192});assert pixels==reference
            frame=current['frame']
        saved=views.from_frame(frame)
        result,_=worker.request({'op':'python-resources','source':"import numpy as np\nassert np.array_equal(viewer.layers[0].data,np.arange(4032,dtype='<u2').reshape(6,24,28))"})
        assert not result['console']['error']
    with launch() as worker:
        current,_=worker.request({'op':'render','width':256,'height':192})
        restored,restored_pixels=worker.request({'op':'change','width':256,'height':192,'request':{
            'frame':current['frame']['frame'],'operation':'restore-view','value':saved}})
        assert views.restored(saved,views.from_frame(restored['frame']))
        assert restored_pixels==reference
