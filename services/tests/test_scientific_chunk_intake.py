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


FILE_BASE='/api/scientific-file-transfers'
FILE_HEADERS={'Origin':ORIGIN,'X-MicroscopeMind':'import-scientific-file-chunk'}


def begin_file(client,built,size=6,request='chunked-file-request-0123456789'):
    return client.post(FILE_BASE,headers=FILE_HEADERS,params={'session':built.day.sid,'request':request,
        'name':'matrix.h5ad','size':size,'bytes':size})


def test_single_file_chunks_retry_finish_recovery_and_original_project_survive_navigation(web):
    client,built=web;session=built.day.sid
    token=begin_file(client,built).json()['token'];url=FILE_BASE+'/'+token
    first=client.put(url,headers=FILE_HEADERS,params={'offset':0},content=b'abc')
    assert first.json()=={'ok':True,'offset':3}
    assert client.put(url,headers=FILE_HEADERS,params={'offset':0},content=b'abc').json()==first.json()
    assert client.put(url,headers=FILE_HEADERS,params={'offset':0},content=b'bad').status_code==400
    assert begin_file(client,built).json()['offset']==3
    # A folder endpoint cannot consume or stop the file's admitted lease.
    assert client.delete(BASE+'/'+token,headers=HEADERS).status_code==400
    assert built.scientific_uploads.get(token).offset==3
    built.day.sid='2026-10-16.999'
    assert client.put(url,headers=FILE_HEADERS,params={'offset':3},content=b'def').status_code==200
    done=client.post(url+'/finish',headers=FILE_HEADERS);assert done.status_code==200,done.text
    saved=done.json()['project'];assert saved['document']['kind']=='source-file'
    assert saved['session']==session and done.json()['sessionChanged'] is True
    assert built.hub.resources.projects.list(built.day.sid)==[]
    recovered=client.post('/api/scientific-file-transfer-recovery',headers=FILE_HEADERS,
        params={'session':session,'request':'chunked-file-request-0123456789'})
    assert recovered.json()['project']==saved
    assert client.get('/api/scientific-files/'+saved['id'],params={'session':session,'revision':1}).content==b'abcdef'
    built.day.sid=session
    retry=begin_file(client,built).json()['token']
    client.put(FILE_BASE+'/'+retry,headers=FILE_HEADERS,params={'offset':0},content=b'abcdef')
    assert client.post(FILE_BASE+'/'+retry+'/finish',headers=FILE_HEADERS).json()['project']==saved
    assert len(built.hub.resources.projects.list(session))==1


def test_single_file_stop_and_restart_retains_only_complete_original_acceptance(web):
    from orchestrator.scientific_chunk_intake import Transfers
    from orchestrator.capture import captures
    client,built=web;memory=captures(built.hub).memory;before=memory.reserved.copy()
    token=begin_file(client,built).json()['token'];url=FILE_BASE+'/'+token
    client.put(url,headers=FILE_HEADERS,params={'offset':0},content=b'abc')
    built.scientific_uploads.close()
    assert memory.reserved==before and not built.hub.resources.projects.list(built.day.sid)
    assert not list(built.hub.resources.projects.root.glob('.scientific-import-*'))
    # A new registry recovers committed receipts; it never resumes abandoned private staging.
    restarted=Transfers(built.hub.resources.projects,memory)
    assert restarted.recover(built.day.sid,'chunked-file-request-0123456789','file') is None
    item=restarted.begin(built.day.sid,'chunked-file-request-0123456789','matrix.h5ad',6,6,'file')
    item.write(0,b'abcdef');saved=item.finish();restarted.close()
    again=Transfers(built.hub.resources.projects,memory)
    assert again.recover(built.day.sid,'chunked-file-request-0123456789','file')==saved
    again.close();assert memory.reserved==before


def test_single_file_over_legacy_limit_streams_to_same_fileintake_with_bounded_buffers(web):
    import hashlib
    from orchestrator.capture import captures
    from orchestrator.scientific_files import CHUNK_BYTES
    client,built=web;memory=captures(built.hub).memory;before=memory.reserved.copy()
    size=65*CHUNK_BYTES
    started=begin_file(client,built,size);assert started.status_code==200,started.text
    token=started.json()['token'];url=FILE_BASE+'/'+token;digest=hashlib.sha256()
    assert 0<memory.reserved['analysis']-before['analysis']<32<<20
    for i in range(65):
        block=bytes([i])*CHUNK_BYTES;digest.update(block)
        response=client.put(url,headers=FILE_HEADERS,params={'offset':i*CHUNK_BYTES},content=block)
        assert response.json()['offset']==(i+1)*CHUNK_BYTES
    complete=client.post(url+'/finish',headers=FILE_HEADERS);assert complete.status_code==200,complete.text
    saved=complete.json()['project'];state=saved['document']['state']['sourceFile']
    assert state['size']==size and state['sha256']==digest.hexdigest()
    source=built.hub.resources.projects.file_source(saved['id'],saved['session'],1,state['reference'])
    assert source.read(64*CHUNK_BYTES,CHUNK_BYTES)==bytes([64])*CHUNK_BYTES
    assert memory.reserved==before


def test_single_file_metadata_origin_and_kind_refusals_do_not_publish(web):
    client,built=web
    params={'session':built.day.sid,'request':'chunked-file-request-0123456789','name':'matrix.h5ad','size':6,'bytes':7}
    assert client.post(FILE_BASE,headers=FILE_HEADERS,params=params).status_code==400
    assert client.post(FILE_BASE,headers=HEADERS,params={**params,'bytes':6}).status_code==403
    assert not built.hub.resources.projects.list(built.day.sid)
