"""Layer composition is display-only and retained without guessing old intent."""
import copy
from pathlib import Path
from types import SimpleNamespace
import pytest
from env_worker import scientific_host, scientific_view_preferences as views
from tests.test_scientific_change_recovery import host
from tests.test_scientific_host_worker import actual_host  # noqa: F401
from tests.test_scientific_view_preferences import CASES, FRAME

MODES=('translucent','translucent_no_depth','additive','minimum','opaque','multiplicative')

@pytest.mark.parametrize('mode', MODES)
def test_layer_blending_is_admitted_atomically_and_keeps_legacy_commands(mode):
    session=host();layer=SimpleNamespace(visible=True,opacity=1,blending='translucent')
    session._layers=[('layer',layer)]
    with pytest.raises(scientific_host.ChangeRefused):
        session.change({'frame':'current','operation':'layer-presentation','value':{'layer':'layer','visible':False,'opacity':.4,'blending':'unknown'}})
    assert layer.visible and layer.opacity==1 and session._frame=='current'
    session.change({'frame':'current','operation':'layer-presentation','value':{'layer':'layer','visible':True,'opacity':.4,'blending':mode}})
    assert layer.blending==mode
    session._frame='again'
    session.change({'frame':'again','operation':'layer-presentation','value':{'layer':'layer','visible':False,'opacity':.5}})
    assert layer.blending==mode


def test_blending_preferences_upgrade_without_inventing_legacy_intent():
    frame=copy.deepcopy(FRAME)
    frame['layers'][0].update(viewKey='raw',viewGeometry=copy.deepcopy(CASES['valid']['layers'][0]['geometry']),blending='additive')
    saved=views.from_frame(frame)
    assert saved['schema']==3 and saved['layers'][0]['blending']=='additive'
    old=copy.deepcopy(CASES['valid']);upgraded=views.compatible(old,frame)
    assert upgraded['layers'][0]['blending']=='additive' and old==CASES['valid']
    for schema in (1,2):
        with pytest.raises(ValueError):views.preferences({**saved,'schema':schema})
    frame['layers'][0].pop('blending')
    with pytest.raises(ValueError,match='blend'):views.compatible(saved,frame)
    from tests.test_scientific_image_appearance import SETTINGS
    frame['layers'][0]['imageAppearance']=copy.deepcopy(SETTINGS)
    old=views.from_frame(frame);assert old['schema']==2
    frame['layers'][0]['blending']='multiplicative'
    frame['layers'][0]['imageAppearance']['gamma']=2
    upgraded=views.compatible(old,frame)
    assert upgraded['schema']==3 and upgraded['layers'][0]['blending']=='multiplicative'
    assert upgraded['layers'][0]['imageAppearance']==SETTINGS
    assert old['schema']==2 and 'blending' not in old['layers'][0]


def test_actual_mixed_layers_blend_like_upstream_and_keep_native_values(actual_host):
    import numpy as np
    from orchestrator import scientific_host as parent
    from tests.test_native_regions import source
    prepared=actual_host;data=np.arange(6*24*28,dtype='<u2').reshape(6,24,28)
    with parent.review(prepared['key'],Path(prepared['folder']).name,{'volume':source(data,[])}) as worker:
        code="""import numpy as np
viewer.layers[0].colormap='green'
viewer.add_image(np.flip(viewer.layers[0].data,axis=2).copy(),name='Red overlay',colormap='red',opacity=.5)
viewer.add_points([[2,8,8],[2,16,18]],name='Points',size=3,face_color='yellow')
"""
        result,_=worker.request({'op':'python-resources','source':code});assert not result['console']['error']
        current,_=worker.request({'op':'render','width':256,'height':192});frame=current['frame']
        for mode in MODES:
            changed,pixels=worker.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],
                'operation':'layer-presentation','value':{'layer':frame['layers'][1]['id'],'visible':True,'opacity':.5,'blending':mode}}})
            assert changed['frame']['layers'][1]['blending']==mode
            result,_=worker.request({'op':'python-resources','source':f"viewer.layers[1].blending='opaque'\nviewer.layers[1].blending={mode!r}"})
            assert not result['console']['error']
            reference,reference_pixels=worker.request({'op':'render','width':256,'height':192})
            assert reference_pixels==pixels
            frame=reference['frame']
        result,_=worker.request({'op':'python-resources','source':"assert np.array_equal(viewer.layers[0].data,np.arange(4032,dtype='<u2').reshape(6,24,28))\nassert np.array_equal(viewer.layers[1].data,np.flip(viewer.layers[0].data,axis=2))"})
        assert not result['console']['error']
