import copy
import pytest
from env_worker.scientific_label_wire import command, receipt


def request():
    return {'op':'labels','request':'edit-1234567890123456','frame':'a'*64,'layer':'b'*32,
        'action':'paint','points':[[12,13],[14,15]],'width':512,'height':384,
        'label':'18446744073709551615','brush':3,'preserve':False,'contiguous':True}


@pytest.mark.parametrize('change',[
    {'points':[[float('nan'),1]]},{'points':[[1,512]]},{'points':[[384,1]]},
    {'points':[[1,2]]*257},{'points':[]},{'points':[[True,1]]},{'label':2**64-1},
    {'brush':True},{'brush':257},{'action':'fill'},{'action':'polygon'},
    {'request':'short'},{'frame':'old'},{'layer':'other'},{'actor':'human'},
])
def test_invalid_gesture_is_refused(change):
    with pytest.raises(ValueError): command({**request(),**change})


def test_closed_actions_and_receipt_identity():
    original=request(); assert command(original)==original
    assert command(original) is not original
    for action in ('undo','redo','reset'):
        value={key:val for key,val in original.items() if key not in ('brush','preserve','contiguous','label')}
        value.update(action=action,points=[])
        assert command(value)==value
        with pytest.raises(ValueError): command({**value,'points':[[1,2]]})
    outcome={'request':original['request'],'state':'applied','changed':True,'label':'7','message':''}
    assert receipt(outcome,original['request'])==outcome
    for change in ({'request':'other'},{'state':'refused'},{'changed':1},{'label':2**64-1},{'message':'a'*2001}):
        with pytest.raises(ValueError): receipt({**outcome,**change},original['request'])


def test_edit_dimensions_are_explicit_and_bounded_without_changing_legacy_plane_edits():
    assert 'dimensions' not in command(request())
    for dimensions in (2, 3, 8):
        assert command({**request(), 'dimensions': dimensions})['dimensions'] == dimensions
    for dimensions in (True, 1, 9, 3.0, '3', None):
        with pytest.raises(ValueError):
            command({**request(), 'dimensions': dimensions})
    history = {key: val for key, val in request().items()
               if key not in ('label', 'brush', 'preserve', 'contiguous')}
    with pytest.raises(ValueError):
        command({**history, 'action': 'undo', 'points': [], 'dimensions': 3})


def polygon():
    value = {key: val for key, val in request().items() if key not in ('brush', 'contiguous')}
    return {**value, 'action': 'polygon', 'points': [[2,2],[2,6],[6,2]]}


def test_polygon_is_a_bounded_plane_operation_with_no_ignored_brush_settings():
    value = polygon()
    assert command(value) == value
    for change in ({'points': []}, {'points': [[2,2],[2,6]]}, {'points': [[2,2]]*3},
                   {'points': [[2,2],[3,3],[4,4]]}, {'brush': 3}, {'contiguous': True},
                   {'dimensions': 3}, {'dimensions': 2}, {'preserve': 1}):
        with pytest.raises(ValueError):
            command({**value, **change})
