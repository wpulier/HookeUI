"""Production admission with stand-in execution; no movie qualification."""
import pytest
from tests.test_scientific_project_api import web,ORIGIN,create  # noqa: F401
from orchestrator import scientific_movie


def test_movie_requires_explicit_origin_and_captured_project(web,monkeypatch):
    client,built=web
    monkeypatch.setattr(scientific_movie,'export',lambda *a,**k:pytest.fail('unadmitted execution'))
    url='/api/scientific-projects/p'+'a'*32+'/movie'
    assert client.post(url,json={}).status_code==403
    response=client.post(url,headers={'Origin':ORIGIN,'X-MicroscopeMind':'export-scientific-movie'},json={
        'session':'2020-01-01','revision':1,'request':'request-123456789','settings':{}})
    assert response.status_code==409


def test_movie_reports_original_project_and_passes_exact_revision(web,monkeypatch):
    client,built=web;saved=create(client,built).json()['project'];calls=[]
    def export(store,project,session,revision,request,options,**kwargs):
        calls.append((project,session,revision,request,options));return store.read(project,session,revision=revision)
    monkeypatch.setattr(scientific_movie,'export',export)
    body={'session':built.day.sid,'revision':1,'request':'request-123456789','settings':{'format':'mov-qtrle-v1','width':512,'height':384,'fps':10}}
    response=client.post('/api/scientific-projects/'+saved['id']+'/movie',headers={'Origin':ORIGIN,'X-MicroscopeMind':'export-scientific-movie'},json=body)
    assert response.status_code==200 and len(calls)==1
    assert calls[0][:4]==(saved['id'],built.day.sid,1,body['request'])
