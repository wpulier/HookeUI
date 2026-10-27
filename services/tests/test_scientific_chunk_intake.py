"""Real chunk HTTP/storage with tiny synthetic folder bytes; no large/native UI qualification."""
import pytest
from tests.test_scientific_project_api import web, ORIGIN  # noqa:F401
from tests.test_scientific_directories import payload

HEADERS={'Origin':ORIGIN,'X-MicroscopeMind':'import-scientific-directory-chunk'}
BASE='/api/scientific-directory-transfers'


def begin(client,built,body,*,size=6,request='chunked-folder-request-0123456789'):
    return client.post(BASE,headers=HEADERS,params={'session':built.day.sid,'request':request,
        'name':'volume.zarr','size':size,'bytes':len(body)})


def test_chunk_upload_repeats_exact_ack_and_atomically_publishes_original_bytes(web):
    client,built=web;_,body=payload({'hidden/.zattrs':b'abc','0/data':b'def'})
    started=begin(client,built,body);assert started.status_code==200,started.text
    token=started.json()['token'];url=BASE+'/'+token
    assert begin(client,built,body).json()['token']==token
    try:
        first=client.put(url,headers=HEADERS,params={'offset':0},content=body[:17])
        assert first.json()=={'ok':True,'offset':17}
        assert client.put(url,headers=HEADERS,params={'offset':0},content=body[:17]).json()==first.json()
        assert client.put(url,headers=HEADERS,params={'offset':0},content=b'wrong').status_code==400
        assert built.hub.resources.projects.list(built.day.sid)==[]
        result=client.put(url,headers=HEADERS,params={'offset':17},content=body[17:])
        assert result.json()['offset']==len(body)
        complete=client.post(url+'/finish',headers=HEADERS);assert complete.status_code==200,complete.text
        saved=complete.json()['project']
        original=built.hub.resources.projects.file_source(saved['id'],saved['session'],1,saved['document']['resources'][0])
        assert original.read(0,6)==b'abcdef'
        # Retrying a completed request via the same captured identity produces one Project source.
        retry=begin(client,built,body).json()['token']
        client.put(BASE+'/'+retry,headers=HEADERS,params={'offset':0},content=body)
        assert client.post(BASE+'/'+retry+'/finish',headers=HEADERS).json()['project']==saved
        assert len(built.hub.resources.projects.list(built.day.sid))==1
        assert not list(built.hub.resources.projects.root.glob('.scientific-import-*'))
    finally:built.scientific_uploads.close()


def test_stop_cleans_partial_staging_and_releases_reserved_memory(web):
    from orchestrator.capture import captures
    client,built=web;_,body=payload({'data':b'abcdef'})
    memory=captures(built.hub).memory;before=memory.reserved.copy()
    token=begin(client,built,body).json()['token'];url=BASE+'/'+token
    client.put(url,headers=HEADERS,params={'offset':0},content=body[:-2])
    assert memory.reserved['analysis']>before['analysis']
    assert list(built.hub.resources.projects.root.glob('.scientific-import-*'))
    assert client.delete(url,headers=HEADERS).json()=={'ok':True}
    assert memory.reserved==before and not built.hub.resources.projects.list(built.day.sid)
    assert not list(built.hub.resources.projects.root.glob('.scientific-import-*'))
    assert client.put(url,headers=HEADERS,params={'offset':len(body)-2},content=body[-2:]).status_code==400


def test_large_metadata_admission_reserves_bounded_buffers_and_shutdown_cleans(web):
    from orchestrator.capture import captures
    client,built=web;memory=captures(built.hub).memory;before=memory.reserved.copy()
    size=65<<20
    response=client.post(BASE,headers=HEADERS,params={'session':built.day.sid,'request':'large-folder-request-0123456789',
        'name':'large.zarr','size':size,'bytes':size+100})
    assert response.status_code==200,response.text
    assert 0<memory.reserved['analysis']-before['analysis']<32<<20
    built.scientific_uploads.close()
    assert memory.reserved==before and not built.hub.resources.projects.list(built.day.sid)


def test_origin_and_chunk_limits_refuse_without_advancing_upload(web):
    client,built=web;_,body=payload({'data':b'abcdef'})
    token=begin(client,built,body).json()['token'];url=BASE+'/'+token
    try:
        assert client.delete(url,headers={'Origin':'https://other.invalid','X-MicroscopeMind':HEADERS['X-MicroscopeMind']}).status_code==403
        result=client.put(url,headers=HEADERS,params={'offset':0},content=b'x'*((1<<20)+1))
        assert result.status_code==400
        assert built.scientific_uploads.get(token).offset==0
        assert client.post(url+'/finish',headers=HEADERS).status_code==400
        assert not built.hub.resources.projects.list(built.day.sid)
    finally:built.scientific_uploads.close()


def test_expired_transfer_is_revoked_and_cleans_partial_data(web,monkeypatch):
    from orchestrator import scientific_chunk_intake as intake
    callbacks=[]
    class Timer:
        def __init__(self,seconds,callback):callbacks.append(callback)
        def start(self):pass
        def cancel(self):pass
    monkeypatch.setattr(intake.threading,'Timer',Timer)
    client,built=web;_,body=payload({'data':b'abcdef'})
    token=begin(client,built,body).json()['token']
    client.put(BASE+'/'+token,headers=HEADERS,params={'offset':0},content=body[:-2])
    item=built.scientific_uploads.get(token);item.touched-=intake.IDLE_SECONDS+1
    callbacks[0]()
    with pytest.raises(ValueError,match='expired'):built.scientific_uploads.get(token)
    assert not list(built.hub.resources.projects.root.glob('.scientific-import-*'))
    assert not built.hub.resources.projects.list(built.day.sid)


def test_recovery_uses_original_request_after_lost_finish_reply(web):
    client,built=web;_,body=payload({'data':b'abcdef'})
    token=begin(client,built,body).json()['token'];url=BASE+'/'+token
    client.put(url,headers=HEADERS,params={'offset':0},content=body)
    accepted=client.post(url+'/finish',headers=HEADERS).json()['project']
    params={'session':built.day.sid,'request':'chunked-folder-request-0123456789'}
    recovered=client.post('/api/scientific-directory-transfer-recovery',headers=HEADERS,params=params)
    assert recovered.json()['project']==accepted
    assert len(built.hub.resources.projects.list(built.day.sid))==1
    assert client.post('/api/scientific-directory-transfer-recovery',headers=HEADERS,
        params={**params,'request':'different-original-request-0123456789'}).json()['project'] is None


def test_idle_expiry_does_not_interrupt_active_finish_but_stop_still_does(web,monkeypatch):
    import threading
    from orchestrator import scientific_chunk_intake as intake
    callbacks=[]
    class Timer:
        def __init__(self,seconds,callback):callbacks.append(callback)
        def start(self):pass
        def cancel(self):pass
    monkeypatch.setattr(intake.threading,'Timer',Timer)
    client,built=web;_,body=payload({'data':b'abcdef'})
    token=begin(client,built,body).json()['token'];item=built.scientific_uploads.get(token)
    item.write(0,body)
    started=threading.Event();proceed=threading.Event();stopped=[]
    def publishing(check):
        started.set();assert proceed.wait(2)
        stopped.append(check())
    monkeypatch.setattr(item.owner,'publish',publishing)
    thread=threading.Thread(target=item.finish);thread.start()
    try:
        assert started.wait(2)
        item.touched-=intake.IDLE_SECONDS+1;callbacks[0]()
        assert built.scientific_uploads.get(token) is item and not item.stopping.is_set()
        item.stopping.set();proceed.set();thread.join(2)
        assert not thread.is_alive() and stopped==[True]
    finally:
        proceed.set();thread.join(2);built.scientific_uploads.close()
    assert not list(built.hub.resources.projects.root.glob('.scientific-import-*'))


def test_overlapping_recovery_both_join_original_finish_before_reporting_absence(web,monkeypatch):
    import threading
    client,built=web;_,body=payload({'data':b'abcdef'})
    token=begin(client,built,body).json()['token'];item=built.scientific_uploads.get(token);item.write(0,body)
    publishing=threading.Event();release=threading.Event();both_joining=threading.Event();joining=[];outcomes=[]
    def publish(check):
        publishing.set();assert release.wait(3)
        assert check()  # Both recoveries requested Stop before this publication boundary.
    monkeypatch.setattr(item.owner,'publish',publish)
    original_close=item.close
    def joined():
        if threading.current_thread().name.startswith('recovery'):
            joining.append(True)
            if len(joining)==2:both_joining.set()
        original_close()
    monkeypatch.setattr(item,'close',joined)
    finish=threading.Thread(target=item.finish);finish.start();assert publishing.wait(2)
    def recover():outcomes.append(built.scientific_uploads.recover(built.day.sid,'chunked-folder-request-0123456789'))
    first=threading.Thread(target=recover,name='recovery-first');second=threading.Thread(target=recover,name='recovery-second')
    first.start();second.start()
    try:
        assert both_joining.wait(2),'A recovery reported absence without joining the original finish'
        assert outcomes==[]
    finally:
        release.set();finish.join(2);first.join(2);second.join(2);built.scientific_uploads.close()
    assert outcomes==[None,None]
