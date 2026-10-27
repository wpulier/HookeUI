"""Synthetic public-contract regressions, not package qualification."""
import pytest
from env_worker.scientific_sequence import Binding, description
from orchestrator.scientific_host import checked_reply, Failed


def test_order_count_and_version_are_explicit():
    selected=[];count=[3]
    b=Binding(version=1,name='stack',count=lambda:count[0],select=selected.append)
    with pytest.raises(ValueError): b.frame(0)
    assert b.begin()['count']==3
    with pytest.raises(ValueError): b.begin()
    for index in (True,1):
        with pytest.raises(ValueError): b.frame(index)
    b.frame(0)
    with pytest.raises(ValueError): b.frame(0)
    count[0]=4
    with pytest.raises(ValueError,match='changed'): b.frame(1)
    assert selected==[0]
    count[0]=3;b.frame(1);b.frame(2)
    with pytest.raises(ValueError): b.frame(3)
    assert selected==[0,1,2]


@pytest.mark.parametrize('count',[0,True,4097,1.2,None,'3'])
def test_bad_count_never_selects(count):
    b=Binding(version=1,name='stack',count=lambda:count,select=lambda _:pytest.fail('ran'))
    with pytest.raises(ValueError):b.begin()


@pytest.mark.parametrize('version',[0,2,True,'1'])
def test_unsupported_version_refuses(version):
    with pytest.raises(ValueError):Binding(version=version,name='stack',count=lambda:1,select=lambda _:None)


def test_parent_checks_declared_sequence():
    good={'format':'scientific-frame-sequence-v1','name':'stack','count':3}
    checked_reply({'op':'sequence-begin'},{'ok':True,'sequence':good},b'')
    with pytest.raises(Failed):checked_reply({'op':'sequence-begin'},{'ok':True,'sequence':good},b'pixels')
    with pytest.raises(ValueError):description({**good,'count':True})


@pytest.mark.parametrize('phase',['count','select','render'])
def test_gui_reported_callback_failure_cannot_become_an_exported_frame(phase):
    import sys
    from env_worker.scientific_host import ReviewSession
    from env_worker.scientific_native_panel import CallbackFailed
    def fail():
        error=RuntimeError('Qt reported callback failure')
        sys.excepthook(type(error),error,None)
    host=object.__new__(ReviewSession);host._check=lambda:None
    def count():
        if phase=='count':fail()
        return 1
    def select(index):
        if phase=='select':fail()
    def render(width,height):
        if phase=='render':fail()
        return {},b'stale frame'
    host.sequence=Binding(version=1,name='failure',count=count,select=select);host.render=render
    previous=sys.excepthook
    with pytest.raises(CallbackFailed):
        host.begin_sequence();host.render_sequence(0,1,1)
    assert sys.excepthook is previous


def test_sequence_transport_admits_only_exact_frame_payload_before_read(monkeypatch):
    import io,threading
    from contextlib import nullcontext
    from types import SimpleNamespace
    from orchestrator import scientific_host as parent
    worker=object.__new__(parent.ReviewWorker)
    worker._stopped=threading.Event();worker._lock=threading.Lock();worker._allowed=lambda:None
    worker.process=SimpleNamespace(stdin=io.BytesIO(),stdout=io.BytesIO());worker.guard=None
    worker.grants=SimpleNamespace(close=lambda:None);worker.close=lambda:None
    monkeypatch.setattr(parent.owned_process,'watching',lambda *a,**k:nullcontext())
    header={'ok':True,'frame':{'width':2,'height':3}}
    def read(stream,**options):
        with pytest.raises(parent.Failed):options['admit'](header,25)
        options['admit'](header,24)
        return header,b'0'*24
    monkeypatch.setattr(parent,'read_message',read)
    assert worker._exchange({'op':'sequence-render','index':0,'width':2,'height':3})==(header,b'0'*24)
