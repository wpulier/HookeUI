"""Production HTTP/storage, synthetic files; no reader certification."""
import pytest
from tests.test_scientific_project_api import web,ORIGIN,create  # noqa: F401

HEADERS={'Origin':ORIGIN,'X-MicroscopeMind':'import-scientific-file'}


def upload(client,built,body=b'native source bytes',**changes):
    return client.post('/api/scientific-files',headers=HEADERS,params={
        'session':built.day.sid,'name':'specimen.npy','size':str(len(body)),
        'request':'source-upload-0123456789',**changes},content=body)


def test_public_upload_retains_exact_original_and_retry_does_not_duplicate(web):
    client,built=web;good=create(client,built).json()['project']
    response=upload(client,built);assert response.status_code==200,response.text
    saved=response.json()['project'];sid=saved['session']
    assert saved['document']['kind']=='source-file' and saved['document']['runtimes']==[]
    assert upload(client,built).json()['project']==saved
    read=client.get('/api/scientific-files/'+saved['id'],params={'session':sid,'revision':1})
    assert read.status_code==200 and read.content==b'native source bytes'
    assert read.headers['content-disposition'].startswith('attachment;')
    assert client.get('/api/scientific-projects/'+good['id'],params={'session':sid}).json()['project']==good
    assert len(built.hub.resources.projects.list(sid))==2
    assert not list(built.hub.resources.projects.root.glob('.scientific-import-*'))


def test_a_retained_scientific_file_is_an_input_of_its_project_in_the_projects_list(web):
    """F-L8-6: All projects said "0 inputs" for the project holding the scientific work: the list counted only the
    library's opened sources. An imported file or folder is an input; a saved scene is not. The list is sent again."""
    client,built=web;sid=built.day.sid
    sent=[];built.sessions_changed=lambda *a,**k:sent.append(True)
    counts=lambda:next(r for r in client.get('/api/sessions').json()['sessions'] if r['id']==sid)['counts']
    create(client,built)
    assert counts()['sources']==0,'a scene is not an input'
    assert upload(client,built).status_code==200 and sent
    assert counts()['sources']==1
    assert upload(client,built).status_code==200 and counts()['sources']==1,'a retried import is one input'
    assert built.hub.resources.projects.inputs_by_session()=={sid:1}


@pytest.mark.parametrize('change',[{'name':'../private'},{'size':'9999999999'},{'size':'01'},
    {'session':'2020-01-01'},{'size':'4'},{'size':'100'},{'request':'bad'}])
def test_failed_intake_does_not_publish_and_corrected_retry_succeeds(web,change):
    client,built=web;response=upload(client,built,**change)
    assert response.status_code in (400,409,413) and response.json()['next']
    assert built.hub.resources.projects.list(built.day.sid)==[]
    assert not list(built.hub.resources.projects.root.glob('.scientific-import-*'))
    assert upload(client,built).status_code==200


@pytest.mark.parametrize('headers',[{}, {'Origin':'https://other.invalid','X-MicroscopeMind':'import-scientific-file'},
    {'Origin':ORIGIN,'X-MicroscopeMind':'actor:user'}])
def test_import_rejects_forged_actor_and_cross_origin_before_writing(web,headers):
    client,built=web
    response=client.post('/api/scientific-files',headers=headers,content=b'not admitted')
    assert response.status_code==403 and built.hub.resources.projects.list(built.day.sid)==[]


def test_request_key_cannot_overwrite_retained_source_and_wrong_session_cannot_read(web):
    client,built=web;saved=upload(client,built).json()['project']
    assert upload(client,built,body=b'different source').status_code==409
    response=client.get('/api/scientific-files/'+saved['id'],params={'session':'2020-01-01','revision':1})
    assert response.status_code==400
    assert built.hub.resources.projects.read(saved['id'],built.day.sid).digest==saved['digest']


def test_cancel_download_during_verification_releases_owned_memory(web,monkeypatch):
    import asyncio,threading,time
    from orchestrator.capture import captures
    from tests.waiting import until_async,HANG_S
    client,built=web;saved=upload(client,built).json()['project']
    endpoint=next(route.endpoint for route in client.app.routes if getattr(route,'path','')=='/api/scientific-files/{project}')
    class Request:
        async def is_disconnected(self):return False
    started=threading.Event();finished=threading.Event();source=built.hub.resources.projects.file_source
    memory=captures(built.hub).memory;before=memory.reserved.copy()
    def verifying(*args,check=lambda:None,**kwargs):
        started.set();deadline=time.monotonic()+HANG_S
        try:
            while time.monotonic()<deadline:
                check();finished.wait(.01)
            raise AssertionError('verification did not receive cancellation')
        finally:finished.set()
    async def run():
        with monkeypatch.context() as patch:
            patch.setattr(built.hub.resources.projects,'file_source',verifying)
            task=asyncio.create_task(endpoint(Request(),saved['id'],saved['session'],1))
            await until_async(started.is_set)
            assert memory.reserved['analysis']>before['analysis']
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
    asyncio.run(run())
    assert finished.is_set() and memory.reserved==before
    ref=saved['document']['resources'][0]
    assert source(saved['id'],saved['session'],1,ref).read(0,19)==b'native source bytes'
    assert client.get('/api/scientific-files/'+saved['id'],params={'session':saved['session'],'revision':1}).status_code==200
