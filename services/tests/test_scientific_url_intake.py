"""Remote intake through production HTTP and storage; a mock transport and resolver stand in for the internet."""
import hashlib
import socket
import httpx
import pytest
from orchestrator import scientific_url_intake as remote
from orchestrator.scientific_projects import ProjectStore
from orchestrator.scientific_readers import source_document
from tests.test_scientific_project_api import web,ORIGIN,HEADERS as SAVE  # noqa: F401

HEADERS={'Origin':ORIGIN,'X-MicroscopeMind':'import-scientific-url'}
BODY=bytes(range(256))*41+b'\x00\xff'
URL='https://data.example.org/datasets/cells%20one.npy?download=1'
PUBLIC={'data.example.org':'93.184.216.34','mirror.example.org':'2606:2800:220:1::1'}


def answer(status,body=b'',headers=None,extensions=None):
    """A fresh streamed answer for each request, as a server sends it (never pre-read)."""
    def respond(request):
        content=body() if callable(body) else iter([body] if body else [])
        return httpx.Response(status,content=content,headers=headers,extensions=extensions)
    return respond


class Server:
    """Answers by URL; records what each request carried."""
    def __init__(self):
        self.routes={};self.seen=[]
    def __call__(self,request):
        self.seen.append(request)
        answer=self.routes.get(str(request.url))
        if answer is None:return httpx.Response(404)
        return answer(request)


@pytest.fixture
def server(monkeypatch):
    served=Server()
    served.routes[URL]=answer(200,BODY,{'content-type':'application/octet-stream','content-length':str(len(BODY))})
    names=dict(PUBLIC)
    def resolve(host,port,type=0):
        if host not in names:raise socket.gaierror('unknown')
        family=socket.AF_INET6 if ':' in names[host] else socket.AF_INET
        return [(family,socket.SOCK_STREAM,6,'',(names[host],port))]
    monkeypatch.setattr(remote,'TRANSPORT',httpx.MockTransport(served))
    monkeypatch.setattr(remote,'RESOLVE',resolve)
    served.names=names
    return served


def post(client,built,url=URL,**changes):
    return client.post('/api/scientific-urls',headers=HEADERS,json={'session':built.day.sid,'url':url,
        'request':'remote-request-0123456789','sha256':None,'name':None,**changes})


def nothing_kept(built):
    root=built.hub.resources.projects.root
    return (built.hub.resources.projects.list(built.day.sid)==[]
            and not list(root.glob('.scientific-url-*')) and not list(root.glob('.scientific-import-*')))


def test_download_keeps_the_exact_bytes_with_a_receipt_and_a_retry_does_not_download_again(web,server):
    client,built=web
    response=post(client,built,sha256=hashlib.sha256(BODY).hexdigest())
    assert response.status_code==200,response.text
    saved=response.json()['project'];state=saved['document']['state']
    assert saved['document']['kind']=='source-file' and saved['document']['title']=='cells one.npy'
    assert state['sourceFile']['sha256']==hashlib.sha256(BODY).hexdigest()==state['sourceRemote']['sha256']
    receipt=state['sourceRemote']
    assert receipt['url']==receipt['final']==URL and receipt['redirects']==0 and receipt['size']==len(BODY)
    assert receipt['mediaType']=='application/octet-stream' and 'untrusted' in receipt['trust']
    read=client.get('/api/scientific-files/'+saved['id'],params={'session':saved['session'],'revision':1})
    assert read.content==BODY
    assert len(server.seen)==1
    assert post(client,built).json()['project']==saved and len(server.seen)==1
    assert source_document(built.hub.resources.projects,saved['id'],saved['session'],1)[1]['sha256']==receipt['sha256']
    root=built.hub.resources.projects.root
    assert not list(root.glob('.scientific-url-*')) and not list(root.glob('.scientific-import-*'))


def test_requests_carry_no_credentials_cookies_or_environment_proxy(web,server,monkeypatch,tmp_path):
    client,built=web
    netrc=tmp_path/'netrc';netrc.write_text('machine data.example.org login user password secret\n')
    monkeypatch.setenv('NETRC',str(netrc));monkeypatch.setenv('HTTPS_PROXY','http://127.0.0.1:9')
    first='https://data.example.org/start'
    server.routes[first]=answer(302,headers={'location':URL,'set-cookie':'session=tracked; Path=/'})
    assert post(client,built,url=first).status_code==200
    for request in server.seen:
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        assert request.headers['accept-encoding']=='identity'
    assert [str(r.url) for r in server.seen]==[first,URL]


@pytest.mark.parametrize('url,says',[
    ('http://data.example.org/a.npy','only over https'),
    ('https://user:secret@data.example.org/a.npy','user name or password'),
    ('https://data.example.org/a b.npy','without spaces'),
    ('https://nowhere.example.org/a.npy','could not be found'),
    ('https://loopback.example.org/a.npy','not a public internet address'),
    ('https://private.example.org/a.npy','not a public internet address'),
    ('https://metadata.example.org/a.npy','not a public internet address'),
    ('https://mapped.example.org/a.npy','not a public internet address'),
    ('https://127.0.0.1/a.npy','not a public internet address'),
    ('https://[::1/a.npy','malformed'),
    ('https://a]b/a.npy','malformed'),
    ('https://\u2603.net/a.npy','non-ASCII host name'),
    ('https://stra\u00dfe.de/a.npy','non-ASCII host name'),
    ('https://nat64.example.org/a.npy','not a public internet address'),
    ('https://compatible.example.org/a.npy','not a public internet address'),
    ('https://sitelocal.example.org/a.npy','not a public internet address'),
    ('https://sixtofour.example.org/a.npy','not a public internet address'),
    ('https://teredo.example.org/a.npy','not a public internet address'),
])
def test_addresses_hooke_will_not_download_from_are_refused_before_any_request(web,server,url,says):
    client,built=web
    server.names.update({'loopback.example.org':'127.0.0.1','private.example.org':'10.1.2.3',
                         'metadata.example.org':'169.254.169.254','mapped.example.org':'::ffff:192.168.0.1','127.0.0.1':'127.0.0.1',
                         'nat64.example.org':'64:ff9b::a00:1','compatible.example.org':'::127.0.0.1','sitelocal.example.org':'fec0::1',
                         'sixtofour.example.org':'2002:a00:1::1','teredo.example.org':'2001:0:4136:e378:8000:63bf:3fff:fdd2'})
    response=post(client,built,url=url)
    assert response.status_code==400 and says in response.json()['says'] and response.json()['next']
    assert server.seen==[] and nothing_kept(built)
    assert post(client,built).status_code==200


def redirect(to):
    return answer(301,headers={'location':to})


@pytest.mark.parametrize('case,says',[
    ('redirect-private','not a public internet address'),
    ('redirect-http','only over https'),
    ('redirect-malformed','redirected to a malformed address'),
    ('too-many-redirects','more than three times'),
    ('forbidden','refused access to this address (HTTP 403). Hooke sends no credentials'),
    ('missing','Nothing is at this address (HTTP 404)'),
    ('server-error','HTTP 503 instead of the file'),
    ('compressed','sent the file compressed (gzip)'),
    ('too-large','limited to 64 MiB. Nothing was downloaded'),
    ('grows','grew past'),
    ('short','ended after'),
    ('peer','connected to a non-public address'),
    ('digest','not the expected'),
])
def test_untrusted_answers_are_refused_and_keep_nothing(web,server,monkeypatch,case,says):
    client,built=web;start='https://data.example.org/start';expected=None;streamed=[]
    def body():
        streamed.append(1);yield BODY
    server.names['inside.example.org']='192.168.1.20'
    routes={'redirect-private':redirect('https://inside.example.org/a.npy'),
            'redirect-http':redirect('http://data.example.org/a.npy'),
            'redirect-malformed':redirect('https://[bad/'),
            'too-many-redirects':redirect(start),
            'forbidden':answer(403),'missing':answer(404),'server-error':answer(503),
            'compressed':answer(200,BODY,{'content-encoding':'gzip'}),
            'too-large':answer(200,body,{'content-length':str((64<<20)+1)}),
            'grows':answer(200,BODY),
            'short':answer(200,BODY[:10],{'content-length':'20'}),
            'peer':answer(200,BODY,extensions={'network_stream':Peer('10.0.0.7')}),
            'digest':answer(200,BODY)}
    if case=='grows':monkeypatch.setattr(remote,'MAX_BYTES',100)
    if case=='digest':expected='0'*64
    server.routes[start]=routes[case]
    response=post(client,built,url=start,sha256=expected)
    assert response.status_code in (400,413) and says in response.json()['says'],response.text
    assert response.json()['next'] and nothing_kept(built)
    if case=='too-large':assert streamed==[]
    monkeypatch.setattr(remote,'MAX_BYTES',64<<20)
    server.routes[start]=answer(200,BODY)
    retry=post(client,built,url=start,request='remote-request-corrected-01')
    assert retry.status_code==200 and retry.json()['project']['document']['state']['sourceRemote']['final']==start


class Peer:
    def __init__(self,address):self.address=address
    def get_extra_info(self,name):return (self.address,443) if name=='server_addr' else None


def test_a_followed_redirect_is_recorded_with_where_the_bytes_came_from(web,server):
    client,built=web;start='https://mirror.example.org/latest'
    server.routes[start]=answer(307,headers={'location':URL})
    receipt=post(client,built,url=start,name='specimen.npy').json()['project']['document']['state']['sourceRemote']
    assert receipt['url']==start and receipt['final']==URL and receipt['redirects']==1


def test_stopping_during_the_transfer_keeps_nothing_and_a_later_retry_succeeds(tmp_path,server):
    store=ProjectStore(tmp_path);stop=[]
    def chunks():
        yield BODY[:100];stop.append(True);yield BODY[100:]
    server.routes[URL]=answer(200,chunks)
    task=remote.Download(store,'s1','remote-request-0123456789',URL)
    try:
        with pytest.raises(remote.RemoteRefused,match='stopped'):task.run(lambda:bool(stop))
    finally:assert task.close()
    assert store.list('s1')==[] and not list(tmp_path.glob('.scientific-*'))
    server.routes[URL]=answer(200,BODY)
    task=remote.Download(store,'s1','remote-request-0123456789',URL)
    try:saved=task.run()
    finally:task.close()
    assert store.file_source(saved.id,'s1',1,saved.document['resources'][0]).read(0,len(BODY))==BODY


def test_the_deadline_bounds_the_whole_download(tmp_path,server,monkeypatch):
    monkeypatch.setattr(remote,'TOTAL_SECONDS',-1)
    task=remote.Download(ProjectStore(tmp_path),'s1','remote-request-0123456789',URL)
    try:
        with pytest.raises(remote.RemoteRefused,match='longer than five minutes') as refused:task.run()
        assert refused.value.status==408
    finally:task.close()
    assert server.seen==[]


def test_a_request_identity_cannot_be_reused_for_another_address(web,server):
    client,built=web;saved=post(client,built).json()['project']
    other='https://data.example.org/other.npy';server.routes[other]=answer(200,b'other')
    conflict=post(client,built,url=other)
    assert conflict.status_code==409 and len(server.seen)==1
    assert built.hub.resources.projects.read(saved['id'],built.day.sid).digest==saved['digest']


def test_studio_saves_cannot_forge_or_change_where_a_source_came_from(web,server):
    client,built=web;saved=post(client,built).json()['project'];document=saved['document']
    renamed={**document,'title':'Renamed source'}
    path=f'/api/scientific-projects/{saved["id"]}'
    ok=client.put(path,headers=SAVE,json={'session':built.day.sid,'expectedRevision':1,'document':renamed})
    assert ok.status_code==200,ok.text
    forged={**document,'state':{**document['state'],'sourceRemote':{**document['state']['sourceRemote'],'url':'https://elsewhere.example.org/x'}}}
    refused=client.put(path,headers=SAVE,json={'session':built.day.sid,'expectedRevision':2,'document':forged})
    assert refused.status_code==400 and 'written only by Hooke’s download' in refused.json()['says']
    created=client.post('/api/scientific-projects',headers=SAVE,json={'session':built.day.sid,'document':forged})
    assert created.status_code==400
    assert built.hub.resources.projects.read(saved['id'],built.day.sid).revision==2


@pytest.mark.parametrize('headers',[{},{'Origin':'https://other.invalid','X-MicroscopeMind':'import-scientific-url'},
    {'Origin':ORIGIN,'X-MicroscopeMind':'import-scientific-file'}])
def test_download_needs_the_studio_and_the_current_project(web,server,headers):
    client,built=web
    response=client.post('/api/scientific-urls',headers=headers,json={'session':built.day.sid,'url':URL,
        'request':'remote-request-0123456789','sha256':None,'name':None})
    assert response.status_code==403 and server.seen==[] and nothing_kept(built)
    moved=post(client,built,session='2020-01-01')
    assert moved.status_code==409 and server.seen==[]
    assert post(client,built,sha256='ABC').status_code==400 and post(client,built,extra=1).status_code==400
    assert nothing_kept(built)


def test_a_disconnected_studio_stops_the_download_and_releases_its_memory(web,server):
    import asyncio,json,time
    from orchestrator.capture import captures
    from starlette.datastructures import Headers
    client,built=web;memory=captures(built.hub).memory;before=memory.reserved.copy()
    endpoint=next(route.endpoint for route in client.app.routes if getattr(route,'path','')=='/api/scientific-urls')
    gone=[];sent=[]
    def chunks():
        for _ in range(400):
            sent.append(1);gone.append(True);time.sleep(.01);yield b'x'*1024
    server.routes[URL]=answer(200,chunks)
    class Request:
        headers=Headers({'origin':ORIGIN,'x-microscopemind':'import-scientific-url'})
        url=type('U',(),{'port':8770})()
        async def stream(self):
            yield json.dumps({'session':built.day.sid,'url':URL,'request':'remote-request-0123456789','sha256':None,'name':None}).encode()
        async def is_disconnected(self):return bool(gone)
    response=asyncio.run(endpoint(Request()))
    assert response.status_code==400 and b'Download stopped' in response.body
    assert len(sent)<400 and nothing_kept(built) and memory.reserved==before


def test_the_client_ignores_environment_settings_and_follows_no_redirect_by_itself(tmp_path,server,monkeypatch):
    made=[];real=httpx.Client
    def client(**kwargs):
        made.append(kwargs);return real(**kwargs)
    monkeypatch.setattr(httpx,'Client',client)
    task=remote.Download(ProjectStore(tmp_path),'s1','remote-request-0123456789',URL)
    try:task.run()
    finally:task.close()
    assert made[0]['trust_env'] is False and made[0]['follow_redirects'] is False
    assert made[0]['headers']['Accept-Encoding']=='identity' and 'cookies' not in made[0]


def test_the_store_refuses_a_receipt_that_does_not_match_the_retained_bytes(tmp_path,server):
    store=ProjectStore(tmp_path)
    task=remote.Download(store,'s1','remote-request-0123456789',URL)
    try:saved=task.run()
    finally:task.close()
    state=saved.document['state']
    for change in ({'sha256':'0'*64},{'size':1},{'trust':'trusted'},{'extra':1},{'format':True},{'retrieved':'yesterday'},
                   {'url':'http://data.example.org/x'},{'mediaType':'x'*101}):
        forged={**saved.document,'state':{**state,'sourceRemote':{**state['sourceRemote'],**change}}}
        with pytest.raises(remote.InvalidProject,match='download receipt'):store.save(saved.id,'s1',forged,expected_revision=1)
    assert store.read(saved.id,'s1')==saved


def test_a_nat64_address_of_a_public_host_is_allowed(web,server):
    client,built=web;server.names['data.example.org']='64:ff9b::5db8:d822'  # 93.184.216.34 through NAT64
    assert post(client,built).status_code==200


def test_a_stuck_name_lookup_is_abandoned_and_refused(tmp_path,server,monkeypatch):
    import threading
    release=threading.Event()
    def stuck(*args,**kwargs):
        release.wait(5);raise socket.gaierror('late')
    monkeypatch.setattr(remote,'RESOLVE',stuck);monkeypatch.setattr(remote,'CONNECT_SECONDS',0.2)
    task=remote.Download(ProjectStore(tmp_path),'s1','remote-request-0123456789',URL)
    try:
        with pytest.raises(remote.RemoteRefused,match='took too long'):task.run()
    finally:task.close();release.set()
    assert server.seen==[]


def test_an_identical_request_finishing_first_elsewhere_answers_with_its_receipt(tmp_path,server,monkeypatch):
    store=ProjectStore(tmp_path)
    first=remote.Download(store,'s1','remote-request-0123456789',URL)
    try:saved=first.run()
    finally:first.close()
    server.routes[URL]=answer(200,BODY,{'content-type':'text/plain'})  # a different receipt for the same bytes
    second=remote.Download(store,'s1','remote-request-0123456789',URL)
    calls=[]
    real=second._existing
    def raced():
        calls.append(1)
        return None if len(calls)==1 else real()  # in flight when the first finished
    monkeypatch.setattr(second,'_existing',raced)
    try:assert second.run()==saved
    finally:second.close()
    assert len(calls)==2 and len(server.seen)==2


def test_a_stage_that_cannot_be_removed_is_retried_later(tmp_path,server,monkeypatch):
    from orchestrator import scientific_file_intake as intake
    task=remote.Download(ProjectStore(tmp_path),'s1','remote-request-0123456789',URL)
    task.run()
    with monkeypatch.context() as patch:
        patch.setattr(remote.shutil,'rmtree',lambda *a,**k:None)
        assert task.close() is False and task in intake._pending
    assert intake.retry_cleanup()==[] and task not in intake._pending and not list(tmp_path.glob('.scientific-url-*'))
