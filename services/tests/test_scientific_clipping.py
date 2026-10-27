"""Clipping is private display intent; malformed lists cannot partially replace it."""
import copy
from pathlib import Path
from types import SimpleNamespace
import pytest
from env_worker import scientific_host, scientific_view_preferences as views
from tests.test_scientific_change_recovery import host
from tests.test_scientific_host_worker import actual_host  # noqa: F401

PLANE={'position':[2.,11.,13.], 'normal':[1.,0.,0.], 'enabled':True}

@pytest.mark.parametrize('invalid', [None,{},[{}],[{**PLANE,'enabled':1}],
    [{**PLANE,'normal':[0,0,0]}],[{**PLANE,'position':[1,2]}],
    [{**PLANE,'normal':[float('nan'),0,1]}],[{**PLANE,'position':[True,2,3]}]])
def test_invalid_clipping_keeps_whole_previous_list(invalid):
    session=host();layer=SimpleNamespace(experimental_clipping_planes=[copy.deepcopy(PLANE)])
    session._layers=[('layer',layer)];before=copy.deepcopy(layer.experimental_clipping_planes)
    with pytest.raises(scientific_host.ChangeRefused):
        session.change({'frame':'current','operation':'clipping-planes','value':{'layer':'layer','planes':invalid}})
    assert layer.experimental_clipping_planes==before and session._frame=='current' and not session.dirty


def test_replace_disable_remove_and_stale_commands():
    session=host();layer=SimpleNamespace(experimental_clipping_planes=[]);session._layers=[('layer',layer)]
    session._description={'layers':[{'id':'layer','clippingPlanes':[]}]}
    for planes in [[PLANE,{**PLANE,'normal':[0,1,0],'enabled':False}],[]]:
        session._frame='current'
        session.change({'frame':'current','operation':'clipping-planes','value':{'layer':'layer','planes':planes}})
        assert layer.experimental_clipping_planes==planes
    with pytest.raises(scientific_host.ChangeRefused):
        session.change({'frame':'old','operation':'clipping-planes','value':{'layer':'layer','planes':[PLANE]}})
    assert layer.experimental_clipping_planes==[]


def test_remembered_clipping_preserves_old_documents_and_exact_other_fields():
    from tests.test_scientific_view_preferences import FRAME,CASES
    from tests.test_scientific_image_appearance import SETTINGS
    current=copy.deepcopy(FRAME)
    current['layers'][0].update(viewKey='raw',viewGeometry=copy.deepcopy(CASES['valid']['layers'][0]['geometry']),
                               clippingPlanes=[PLANE],imageAppearance=copy.deepcopy(SETTINGS))
    saved=views.from_frame(current);assert saved['schema']==5
    for schema in range(1,5):
        old=copy.deepcopy(saved);old['schema']=schema;del old['layers'][0]['clippingPlanes']
        if schema==1: del old['layers'][0]['imageAppearance']
        before=copy.deepcopy(old);assert views.compatible(old,current)['layers'][0]['clippingPlanes']==[PLANE]
        assert old==before
    changed=copy.deepcopy(saved);changed['layers'][0]['clippingPlanes'][0]['normal'][0]+=1e-15
    assert views.restored(saved,changed)
    changed['layers'][0]['clippingPlanes'][0]['enabled']=False
    assert not views.restored(saved,changed)
    del current['layers'][0]['clippingPlanes']
    with pytest.raises(ValueError,match='clipping'):views.compatible(saved,current)


def test_actual_mixed_clipping_matches_upstream_and_preserves_values(actual_host):
    import numpy as np
    from orchestrator import scientific_host as parent
    from tests.test_native_regions import source
    prepared=actual_host;data=np.arange(4032,dtype='<u2').reshape(6,24,28)
    with parent.review(prepared['key'],Path(prepared['folder']).name,{'volume':source(data,[])}) as worker:
        result,_=worker.request({'op':'python-resources','source':"import numpy as np\nviewer.dims.ndisplay=3\nviewer.add_points([[1,8,8],[4,16,18]],name='Points',size=3,face_color='yellow')"})
        assert not result['console']['error']
        current,_=worker.request({'op':'render','width':256,'height':192});frame=current['frame']
        for index in (0,1):
            result,_=worker.request({'op':'python-resources','source':f"viewer.layers[0].visible={index==0!r}\nviewer.layers[1].visible={index==1!r}"})
            assert not result['console']['error']
            current,baseline=worker.request({'op':'render','width':256,'height':192});frame=current['frame']
            differences=[]
            for planes in [[PLANE],[PLANE,{**PLANE,'normal':[0,1,0]}],[{**PLANE,'enabled':False}],[]]:
                changed,pixels=worker.request({'op':'change','width':256,'height':192,'request':{
                    'frame':frame['frame'],'operation':'clipping-planes','value':{'layer':frame['layers'][index]['id'],'planes':planes}}})
                assert changed['frame']['layers'][index]['clippingPlanes']==planes
                if planes and planes[0]['enabled']: differences.append(pixels!=baseline)
                else: assert pixels==baseline
                result,_=worker.request({'op':'python-resources','source':f"viewer.layers[{index}].experimental_clipping_planes=[]\nviewer.layers[{index}].experimental_clipping_planes={planes!r}"})
                assert not result['console']['error']
                current,reference=worker.request({'op':'render','width':256,'height':192});assert reference==pixels
                frame=current['frame']
            assert any(differences), 'Clipping must visibly change the actual native layer'
        result,_=worker.request({'op':'python-resources','source':"assert np.array_equal(viewer.layers[0].data,np.arange(4032,dtype='<u2').reshape(6,24,28))\nassert np.array_equal(viewer.layers[1].data,[[1,8,8],[4,16,18]])"})
        assert not result['console']['error']


def test_clipping_resource_refusals_preserve_private_work_and_retry():
    session=host();layer=SimpleNamespace(experimental_clipping_planes=[])
    session._layers=[('layer',layer)]
    session._description={'layers':[{'id':'layer','clippingPlanes':[]}]}
    for count,padding in [(65,0),(1500,0),(64,120000)]:
        session._description['other']='x'*padding
        with pytest.raises(scientific_host.ChangeRefused):
            session.change({'frame':'current','operation':'clipping-planes','value':{'layer':'layer','planes':[{**PLANE,'normal':[1,1,1]}]*count}})
        assert layer.experimental_clipping_planes==[] and session._frame=='current' and not session.dirty
    session._description['other']=''
    session.change({'frame':'current','operation':'clipping-planes','value':{'layer':'layer','planes':[PLANE]}})
    assert layer.experimental_clipping_planes==[PLANE]
