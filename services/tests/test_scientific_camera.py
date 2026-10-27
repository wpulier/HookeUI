"""Frame-bound camera controls use the pinned native camera, never array edits."""
import copy
import json
from pathlib import Path
import pytest
from tests.test_scientific_host_worker import actual_host  # noqa: F401
from tests.test_scientific_change_recovery import host
from env_worker import scientific_host


def test_camera_controls_validate_all_fields_before_mutating():
    session=host();session.viewer.camera.orientation=('towards','down','right');session.viewer.camera.perspective=0
    before=copy.deepcopy(vars(session.viewer.camera))
    value={'center':[4,5,6],'angles':[25,40,15],'zoom':3,'orientation':['away','up','left'],'perspective':45}
    for bad in [{'perspective':180},{'orientation':['left','up','left']},{'orientation':[1,2,3]}]:
        with pytest.raises(scientific_host.ChangeRefused):
            session.change({'frame':'current','operation':'camera','value':{**value,**bad}})
        assert vars(session.viewer.camera)==before and session._frame=='current'
    session.change({'frame':'current','operation':'camera','value':value})
    assert session.viewer.camera.perspective==45 and session.viewer.camera.orientation==value['orientation']


def test_actual_camera_gestures_use_native_camera_and_preserve_arrays(actual_host):
    import numpy as np
    from orchestrator import scientific_host as parent
    from tests.test_native_regions import source
    prepared=actual_host;values=np.arange(6*24*28,dtype='<u2').reshape(6,24,28)
    with parent.review(prepared['key'],Path(prepared['folder']).name,{'volume':source(values,[])}) as worker:
        worker.request({'op':'python-resources','source':'viewer.dims.ndisplay=3'})
        current,_=worker.request({'op':'render','width':256,'height':192});frame=current['frame']
        for mode,key in [('orbit','angles'),('pan','center'),('zoom','zoom')]:
            before=copy.deepcopy(frame['camera'])
            result,pixels=worker.request({'op':'change','width':256,'height':192,'request':{
                'frame':frame['frame'],'operation':'camera-gesture','value':{
                    'mode':mode,'width':256,'height':192,'points':[[96,100],[100,115],[110,135]]}}})
            frame=result['frame']
            assert frame['camera'][key]!=before[key] and len(pixels)==256*192*4
            # Drive the pinned camera directly, independently of our command handler.
            direct = f"""import json
from vispy.app.canvas import MouseEvent
from vispy.scene.events import SceneMouseEvent
from vispy.util import keys
before=json.loads({json.dumps(before)!r})
for name in ('orientation','perspective','center','angles','zoom'):
    setattr(viewer.camera,name,before[name])
canvas=viewer.window._qt_viewer.canvas
view=canvas.camera._view
camera=view.camera
button={2 if mode=='zoom' else 1}
modifiers=(keys.SHIFT,) if {mode!r}=='pan' else ()
start=MouseEvent('mouse_press',pos=(100,96),button=button,buttons=[button],modifiers=modifiers)
camera.viewbox_mouse_event(SceneMouseEvent(start,view))
previous=start
for point in [(100,96),(115,100),(135,110)]:
    event=MouseEvent('mouse_move',pos=point,button=button,buttons=[button],modifiers=modifiers,press_event=start,last_event=previous)
    camera.viewbox_mouse_event(SceneMouseEvent(event,view))
    previous=event
camera.viewbox_mouse_event(SceneMouseEvent(MouseEvent('mouse_release',pos=(135,110),button=button,buttons=[],modifiers=modifiers,press_event=start,last_event=previous),view))
canvas.camera.on_draw(None)
"""
            result,_=worker.request({'op':'python-resources','source':direct})
            assert not result['console']['error']
            reference,reference_pixels=worker.request({'op':'render','width':256,'height':192})
            for field in ('center','angles','zoom'):
                assert np.allclose(reference['frame']['camera'][field],frame['camera'][field],rtol=1e-9,atol=1e-9)
            assert reference_pixels==pixels
            frame=reference['frame']
        result,pixels=worker.request({'op':'change','width':256,'height':192,'request':{
            'frame':frame['frame'],'operation':'camera-reset','value':{}}})
        frame=result['frame']
        worker.request({'op':'python-resources','source':'viewer.reset_view()'})
        reference,reference_pixels=worker.request({'op':'render','width':256,'height':192})
        assert reference['frame']['camera']==frame['camera'] and reference_pixels==pixels
        frame=reference['frame']
        with pytest.raises(parent.ChangeRefused):
            worker.request({'op':'change','width':256,'height':192,'request':{
                'frame':frame['frame'],'operation':'camera-gesture','value':{
                    'mode':'orbit','width':512,'height':192,'points':[[96,100],[110,135]]}}})
        assert worker.process.poll() is None
        result,_=worker.request({'op':'python-resources','source':
            'import hashlib\nprint(hashlib.sha256(viewer.layers[0].data.tobytes()).hexdigest())'})
        import hashlib
        assert result['console']['stdout']==hashlib.sha256(values.tobytes()).hexdigest()+'\n'


@pytest.mark.parametrize('patch', [
    {'mode':'plugin-callback'}, {'width':255}, {'height':0}, {'points':[]},
    {'points':[[0,0]]}, {'points':[[0,0]]*257}, {'points':[[0,0],[192,10]]},
    {'points':[[0,0],[10,256]]}, {'points':[[0,0],[-1,10]]},
    {'points':[[0,0],[float('nan'),10]]}, {'points':[[0,0],[True,10]]},
    {'extra':'undeclared'},
])
def test_malformed_camera_gesture_is_refused_before_native_access(patch):
    from env_worker.scientific_camera import prepare
    # No native viewer exists: malformed transport must fail before touching it.
    value={'mode':'orbit','width':256,'height':192,'points':[[96,100],[110,135]],**patch}
    with pytest.raises(ValueError):
        prepare(None,(256,192),value)
