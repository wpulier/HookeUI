"""Refuse stale, malformed and oversized panel messages before callbacks."""
import copy
import sys
import pytest
from env_worker import scientific_native_panel_wire as wire
from env_worker.scientific_native_panel import callback_errors, CallbackFailed, BoundaryFailed
from env_worker.scientific_callback_errors import recoverable_callback


def state():
    return dict(format=wire.FORMAT,panel='a'*32,frame='b'*64,width=32,height=24,pixelRatio=1,focus=None,
        accessibility=[dict(id='1',parent=None,role='Client',name='Tool',description='',value='',rect=[0,0,32,24],
            disabled=False,focused=False,focusable=False,invisible=False,checked=False,checkable=False,readOnly=False)])


def test_bounded_panel_frame_keeps_accessibility_and_copies_nested_values():
    original=state();checked=wire.frame(original,b'\0'*(32*24*4))
    assert checked==original
    checked['accessibility'][0]['name']='Changed'
    assert original['accessibility'][0]['name']=='Tool'


@pytest.mark.parametrize('change',[
    lambda v:v.update(width=True),lambda v:v.update(height=1025),lambda v:v.update(pixelRatio=True),
    lambda v:v.update(frame='c'*63),lambda v:v.update(extra='not allowed'),
    lambda v:v['accessibility'][0].update(parent='1'),lambda v:v['accessibility'][0].update(disabled=1),
    lambda v:v['accessibility'][0].update(rect=[0,0,-1,5]),
    lambda v:v['accessibility'][0].update(name='\ud800'),
    lambda v:v['accessibility'][0].update(name='😀'*501),
    lambda v:v['accessibility'].append(copy.deepcopy(v['accessibility'][0])),
    lambda v:v['accessibility'].append({**v['accessibility'][0],'id':'2','parent':'missing'}),
])
def test_panel_frame_rejects_untrusted_metadata(change):
    value=state();change(value)
    with pytest.raises(ValueError):wire.frame(value)


def test_panel_frame_checks_pixels_and_aggregate_metadata_budget():
    with pytest.raises(ValueError):wire.frame(state(),b'\0')
    value=state();root=value['accessibility'][0]
    value['accessibility'] += [{**root,'id':str(i),'parent':'1','name':'x'*1000} for i in range(2,100)]
    with pytest.raises(ValueError):wire.frame(value)


@pytest.mark.parametrize('op,value',[('click',[31,23]),('key','Tab'),('key','Backtab'),('text','µm')])
def test_panel_command_uses_exact_frame(op,value):
    current=state();request=dict(panel=current['panel'],frame=current['frame'],op=op,value=value)
    assert wire.command(request,current)==request
    request['frame']='c'*64
    with pytest.raises(ValueError):wire.command(request,current)


@pytest.mark.parametrize('op,value',[('click',[32,0]),('click',[True,1]),('click',[-1,0]),
    ('key','Ctrl+V'),('key','Meta+Q'),('text',''),('text','\0'),('text','\ud800'),('text','x'*4001),('run','arbitrary')])
def test_panel_command_rejects_unsupported_events(op,value):
    current=state()
    with pytest.raises(ValueError):wire.command(dict(panel=current['panel'],frame=current['frame'],op=op,value=value),current)


def test_callback_capture_restores_hook_and_never_hides_a_later_fatal_error():
    hook=sys.excepthook
    with pytest.raises(BoundaryFailed) as failure:
        with callback_errors():
            sys.excepthook(ValueError,ValueError('first'),None)
            sys.excepthook(MemoryError,MemoryError('fatal'),None)
    assert not recoverable_callback(failure.value) and sys.excepthook is hook
    with pytest.raises(CallbackFailed):
        with callback_errors():raise ValueError('ordinary')
    assert sys.excepthook is hook


def test_callback_error_overflow_is_fatal_without_allocating_an_unbounded_list():
    hook=sys.excepthook
    with pytest.raises(BoundaryFailed) as failure:
        with callback_errors():
            for _ in range(65):sys.excepthook(ValueError,ValueError('bounded'),None)
    assert not recoverable_callback(failure.value) and sys.excepthook is hook


@pytest.mark.parametrize('value',[
    {'points':[[0,0]],'button':'left','cancel':False},
    {'points':[[0,0],[32,23]],'button':'left','cancel':False},
    {'points':[[0,0],[1,1]],'button':'middle','cancel':False},
    {'points':[[0,0],[1,1]],'button':'left','cancel':1},
    {'points':[[0,0]]*257,'button':'left','cancel':False},
])
def test_drag_is_a_bounded_transaction(value):
    current=state()
    with pytest.raises(ValueError):wire.command(dict(panel=current['panel'],frame=current['frame'],op='drag',value=value),current)


def test_valid_drag_and_wheel_preserve_coordinates_and_cancellation():
    current=state()
    for op,value in [('drag',{'points':[[0,0],[31,23]],'button':'right','cancel':True}),
                     ('wheel',{'point':[31,23],'angleDelta':[0,-120]})]:
        request=dict(panel=current['panel'],frame=current['frame'],op=op,value=value)
        assert wire.command(request,current)==request
