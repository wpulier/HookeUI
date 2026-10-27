"""Real session files and HTTP readers; no paid calls or browser-layout claims."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.research import ResearchManager
from orchestrator.research.files import add_research_routes
from tests.test_research import FakeHub
from tests.test_research_versions import saved


def test_historical_identity_never_falls_back_to_current_reused_id(tmp_path):
    saved(tmp_path, '2026-10-09', 'r1', 1, result={'summary': 'Yesterday retained'})
    saved(tmp_path, '2026-10-10', 'r1', 1, result={'summary': 'Today retained'})
    manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: '2026-10-10')
    app = FastAPI()
    add_research_routes(app, manager)
    client = TestClient(app)
    before = {path: path.read_bytes() for path in tmp_path.rglob('session.json')}
    assert client.get('/api/research/r1?day=2026-10-09').json()['result']['summary'] == 'Yesterday retained'
    assert client.get('/api/research/r1?day=2026-10-08').status_code == 404
    assert client.get('/api/research/r1').json()['result']['summary'] == 'Today retained'
    assert client.get('/api/research/r1?day=../2026-10-09').status_code == 400
    assert {path: path.read_bytes() for path in tmp_path.rglob('session.json')} == before
    assert manager.queue == []


def test_saved_presentation_json_export_reopens_without_jobs(tmp_path):
    from tests.test_research_presentations import setup, done
    from _pytest.monkeypatch import MonkeyPatch
    import asyncio
    async def scenario():
        with MonkeyPatch.context() as monkeypatch:
            manager, session, process, args = setup(tmp_path, monkeypatch)
            job = manager.presentations.begin(session.rid, 'export', **args)
            await asyncio.sleep(0)
            manager.presentations.event(done(session, job))
            output = session.outputs[-1]
            app = FastAPI(); add_research_routes(app, manager)
            before = (tmp_path / session.day / session.rid / 'session.json').read_bytes()
            client = TestClient(app)
            response = client.get(session.base + '/' + output['path'])
            assert response.status_code == 200 and response.json()['plan'] == output['content']
            assert response.headers['x-content-type-options'] == 'nosniff'
            assert client.get(session.base + '/inputs/private.json').status_code == 404
            assert (tmp_path / session.day / session.rid / 'session.json').read_bytes() == before
            assert len(process.calls) == 1
            manager.presentations.stop()
    asyncio.run(scenario())


def test_capture_thumbnail_keeps_original_pixels_and_refuses_change_during_read(tmp_path, monkeypatch):
    import copy, hashlib, json
    from types import SimpleNamespace
    from tests.test_research_versions import lesson_session
    from orchestrator import resources
    manager, session = lesson_session(tmp_path)
    document = {'entry':{'day':session.day,'id':'m4','version':None,'source':{'frame':42,'seq':902}},'detail':{'values':{}}}
    digest = hashlib.sha256(json.dumps(document,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    manager._finish(session,'done',{'summary':'Saved','report':{'methods':'read','observations':'one','measurements':'none','limitations':'one'},
        'captures':[{'source':{'day':session.day,'id':'m4','version':None,'contentRevision':digest},'frames':[{'picture':0,'frame':42,'seq':902}]}]})
    basis=session.result['findingRevision'];race=False;calls=[]
    def picture(eid,n,day,max_bytes):
        calls.append((eid,n,day,max_bytes))
        if race:document['entry']['source']['seq']=903
        return b'original-jpeg-bytes','image/jpeg'
    monkeypatch.setattr(resources,'resources_of',lambda hub:SimpleNamespace(document=lambda *args:copy.deepcopy(document),picture=picture))
    app=FastAPI();add_research_routes(app,manager);client=TestClient(app)
    url=f'/api/research/{session.rid}/findings/{basis["version"]}/captures/0/0?day={session.day}&digest={basis["digest"]}'
    before=(tmp_path/session.day/session.rid/'session.json').read_bytes()
    response=client.get(url)
    assert response.status_code==200 and response.content==b'original-jpeg-bytes'
    assert response.headers['x-content-type-options']=='nosniff'
    assert calls==[('m4',0,session.day,4*1024*1024)]
    race=True
    assert client.get(url).status_code==409
    assert client.get(url.replace('captures/0/0','captures/0/1')).status_code==409
    assert client.get(url.replace(session.day,'../bad')).status_code==400
    assert (tmp_path/session.day/session.rid/'session.json').read_bytes()==before
