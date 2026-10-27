"""Real storage; optional actual packaged codec with explicitly synthetic frames."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
from orchestrator import scientific_movie as movie
from orchestrator.scientific_projects import ProjectStore,InvalidProject,Conflict
OPTIONS={'format':'mov-qtrle-v1','width':16,'height':12,'fps':10}


@pytest.mark.parametrize('change',[{'width':0},{'width':1025},{'height':True},{'fps':0},{'fps':61},{'format':'mp4'},{'extra':1}])
def test_invalid_settings(change):
    with pytest.raises(InvalidProject):movie.settings({**OPTIONS,**change})


def test_no_system_fallback(monkeypatch):
    monkeypatch.setattr(movie.shutil,'which',lambda *a,**k:'/opt/homebrew/bin/ffmpeg')
    with pytest.raises(InvalidProject,match='no system fallback'):movie.encoder({'PATH':'/opt/homebrew/bin'})


@pytest.fixture
def stored(tmp_path):
    store=ProjectStore(tmp_path/'projects')
    runtime={'bundle':'example','generation':'a'*16,'operation':'viewer'}
    saved=store.create('2026-10-18',{'kind':'scene','title':'saved sequence','resources':[],
        'runtimes':[runtime],'state':{'scientificView':{'runtime':runtime,'inputs':{},'layers':[],'tool':{}}}})
    return store,saved


def fake_render(monkeypatch,saved,failure=False):
    events=[]
    @contextmanager
    def review(*args,**kwargs):
        events.append('start')
        try:yield saved,SimpleNamespace(request=lambda _:({'sequence':{'format':'scientific-frame-sequence-v1','name':'test','count':3}},b''))
        finally:events.append('reaped')
    def encode(worker,sequence,options,binary,work,check):
        check()
        if failure:raise InvalidProject('synthetic encoder failure')
        path=work/'movie.mov';path.write_bytes(b'synthetic codec bytes')
        return path,'c'*64
    monkeypatch.setattr(movie.scientific_project_review,'review',review)
    monkeypatch.setattr(movie,'encoder',lambda _:(Path('/synthetic-encoder'),'b'*64))
    monkeypatch.setattr(movie,'_encode',encode)
    return events


def test_retry_recovers_without_execution(stored,monkeypatch):
    store,saved=stored;events=fake_render(monkeypatch,saved)
    result=movie.export(store,saved.id,saved.session,1,'same-request-123456',OPTIONS)
    assert events==['start','reaped']
    assert movie.export(store,saved.id,saved.session,1,'same-request-123456',OPTIONS)==result
    assert events==['start','reaped']
    assert result.document['state']['movieExport']['source']['digest']==saved.digest
    with pytest.raises(Conflict):movie.export(store,saved.id,saved.session,1,'same-request-123456',{**OPTIONS,'fps':11})
    assert store.read(saved.id,saved.session)==saved
    assert not list(store.root.glob('.scientific-*'))


def test_failure_preserves_source(stored,monkeypatch):
    store,saved=stored;events=fake_render(monkeypatch,saved,True)
    with pytest.raises(InvalidProject,match='synthetic'):movie.export(store,saved.id,saved.session,1,'failed-request-123456',OPTIONS)
    assert events==['start','reaped'] and store.list(saved.session)==[saved]
    assert not list(store.root.glob('.scientific-*'))


def test_cancel_preserves_source(stored,monkeypatch):
    store,saved=stored;fake_render(monkeypatch,saved)
    from orchestrator.owned_process import Stopped
    with pytest.raises(Stopped):movie.export(store,saved.id,saved.session,1,'cancel-request-123456',OPTIONS,stopping=lambda:True)
    assert store.list(saved.session)==[saved]


@pytest.mark.parametrize('format', ['mov-qtrle-v1', 'mov-argb-v1'])
def test_actual_packaged_codec_roundtrip_synthetic_rgba(tmp_path, format):
    path=os.environ.get('HOOKE_TEST_MOVIE_FFMPEG')
    if not path:pytest.skip('requires actual packaged codec; synthetic-frame codec evidence only')
    frames=[]
    class Worker:
        def request(self,request):
            rgba=bytes(v for y in range(12) for x in range(16) for v in (x*13,y*17,request['index']*61,255 if x%2 else 137))
            frames.append(rgba);return {},rgba
    output,digest=movie._encode(Worker(),{'count':3},movie.settings({**OPTIONS, 'format':format}),Path(path).resolve(),tmp_path,lambda:None)
    assert output.stat().st_size>0
    assert digest==hashlib.sha256(b''.join(frames)).hexdigest()


def test_cancelled_retry_does_not_verify_or_execute(stored,monkeypatch):
    store,saved=stored;events=fake_render(monkeypatch,saved)
    movie.export(store,saved.id,saved.session,1,'retry-cancel-123456',OPTIONS)
    monkeypatch.setattr(store,'file_source',lambda *a,**k:pytest.fail('verification after cancellation'))
    from orchestrator.owned_process import Stopped
    with pytest.raises(Stopped):movie.export(store,saved.id,saved.session,1,'retry-cancel-123456',OPTIONS,stopping=lambda:True)
    assert events==['start','reaped']


@pytest.mark.parametrize('revision',[None,True,0,-1,'1'])
def test_movie_never_substitutes_latest_revision(stored,revision):
    store,saved=stored
    with pytest.raises(InvalidProject,match='exact accepted'):movie.export(store,saved.id,saved.session,revision,'invalid-revision-123456',OPTIONS)


def test_native_movie_format_records_exact_codec_and_keeps_legacy(stored,monkeypatch):
    store,saved=stored;fake_render(monkeypatch,saved)
    options=movie.settings({**OPTIONS,'format':'mov-argb-v1'})
    result=movie.export(store,saved.id,saved.session,1,'native-request-123456',options)
    assert result.document['state']['movieExport']['encoder']['codec']=='rawvideo'
    assert result.document['state']['movieExport']['settings']==options
    legacy=movie.export(store,saved.id,saved.session,1,'legacy-request-123456',OPTIONS)
    assert legacy.document['state']['movieExport']['encoder']['codec']=='qtrle'
    with pytest.raises(Conflict):movie.export(store,saved.id,saved.session,1,'native-request-123456',OPTIONS)
