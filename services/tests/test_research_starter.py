"""Real isolated Project journals and routes, with an untrusted page transport fixture."""
import asyncio
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from orchestrator import research_starter as starters
from orchestrator.commands import COMMANDS
from orchestrator.day.store import SessionStore
from orchestrator.paths import Paths
from orchestrator.project_locations import ProjectLocations
from orchestrator.project_roots import ProjectRegistry
from orchestrator.server import create_app
from research_worker import safe_fetch


@pytest.mark.parametrize('portable', [False, True])
def test_preview_accept_reopen_and_retry_keep_one_owning_project(tmp_path, portable):
    paths = Paths.under(tmp_path / 'home')
    locations = ProjectLocations(paths.sessions, ProjectRegistry(tmp_path / 'host'), tmp_path / 'Projects') if portable else None
    server = create_app('http://127.0.0.1:1', False, paths, project_locations=locations)
    client = TestClient(server, base_url='http://localhost:8770')
    app = server.state.app
    original = app.day.sid
    headers = {'Origin':'http://localhost:8770', 'X-MicroscopeMind':'preview-research-link'}
    try:
        def preview(url):
            response = client.post('/api/research-starter/preview', json={'url':url}, headers=headers)
            assert response.is_success
            return response.json()

        brief = preview(starters.CHALLENGE)
        assert brief == preview(starters.ORGANIZER) == preview(starters.CHALLENGE + 'lightmycells2/')
        assert app.day.sid == original and len(app.day.catalogue.list()) == 1
        assert client.post('/api/research-starter/preview', json={'url':starters.CHALLENGE}).status_code == 403
        intent = {**brief, 'title':'My cell study', 'context':'Compare my predictions against the reference.'}
        request = {'cmd':'session', 'action':'new', 'researchStarter':intent, 'starterRequestId':str(uuid4())}
        opened = asyncio.run(COMMANDS['session'](app, None, request))['session']
        assert opened != original and app.day.name == intent['title']
        assert client.get('/api/research-starter', params={'session':original}).status_code == 409
        kept = client.get('/api/research-starter', params={'session':opened}).json()['starter']
        assert kept['original'] == brief['original'] and kept['context'] == intent['context']
        assert 'maximum-intensity' in kept['original']
        app.day.switch_session(original)
        assert app.day.research_starter is None
        assert asyncio.run(COMMANDS['session'](app, None, request))['session'] == opened
        assert len(app.day.catalogue.list()) == 2 and app.day.research_starter == kept
        assert SessionStore(app.day.root, session=opened, locations=locations).research_starter == kept
    finally:
        if locations:
            locations.close()


def test_unfamiliar_page_and_blocked_source(monkeypatch):
    html = '<title>Cell scale</title><meta name="description" content="Compare the paired images.">' \
           '<script>runEveryCommand()</script><p>Use the paired reference measurements.</p>'
    async def get(client, url, accept_image):
        if url.endswith('/blocked'):
            raise safe_fetch.FetchRefused('HTTP 403')
        return httpx.Response(200, headers={'content-type':'text/html'}), html.encode(), url
    monkeypatch.setattr(safe_fetch, '_get', get)
    read = asyncio.run(starters.preview('https://research.example/study'))
    assert read['title'] == 'Cell scale' and read['context'] == 'Compare the paired images.'
    assert read['status'] == 'read' and 'paired reference' in read['original']
    assert 'runEveryCommand' not in read['original']
    starters.verify_preview(read, read['receipt'])
    with pytest.raises(ValueError, match='excerpt could not be verified'):
        starters.verify_preview({**read, 'original': 'User-entered words'}, read['receipt'])
    unavailable = asyncio.run(starters.preview('https://research.example/blocked'))
    assert unavailable['source'].endswith('/blocked') and unavailable['original'] == '' and unavailable['status'] == 'unreadable'
    accepted = starters.accepted({**unavailable, 'context':'Pasted abstract.'}, str(uuid4()))
    assert accepted['context'] == 'Pasted abstract.' and not accepted['original']


def test_private_link_never_reaches_the_transport(monkeypatch):
    async def never(*args, **kwargs):
        pytest.fail('private origin reached the transport')
    monkeypatch.setattr(httpx.AsyncClient, 'send', never)
    blocked = asyncio.run(starters.preview('http://127.0.0.1/secret'))
    assert blocked['status'] == 'unreadable' and blocked['original'] == ''
    with pytest.raises(ValueError):
        asyncio.run(starters.preview('https://guest:private@example.org/'))
    with pytest.raises(ValueError):
        starters.accepted({**blocked, 'source':'javascript:alert(1)'}, str(uuid4()))

def test_redirect_rebind_is_refused_before_connect_and_public_ip_keeps_tls_name(monkeypatch):
    import socket
    from orchestrator import scientific_url_intake as public_web

    # Initial hostname policy passes; the resolver used at connect time changes the redirect answer.
    monkeypatch.setattr(safe_fetch, 'check_url', lambda url: True)
    def addresses(host, port, **unused):
        address = '100.64.1.3' if host == 'changed.example' else '93.184.216.34'
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, port))]
    monkeypatch.setattr(public_web, 'RESOLVE', addresses)
    connected = []
    async def actual_connect(transport, request):
        connected.append((request.url.host, request.headers['host'], request.extensions['sni_hostname']))
        return httpx.Response(302, headers={'location':'https://changed.example/private'},
                              stream=httpx.ByteStream(b''))
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', actual_connect)
    assert asyncio.run(starters.preview('https://first.example/study'))['status'] == 'unreadable'
    assert connected == [('93.184.216.34', 'first.example', 'first.example')]

def test_recent_projects_distinguish_background_activity_from_an_explicit_blank(tmp_path):
    store = SessionStore(tmp_path / 'sessions')
    initial = store.sid
    def row(sid):
        return store.catalogue.get(sid).wire()
    assert row(initial)['launcherBootstrap']
    store._record({'type':'line', 'thread':'t1', 'line':{
        'type':'activity', 'kind':'model', 'id':'catalog-peek:1', 'text':'Nothing new.'}})
    assert row(initial)['launcherBootstrap']
    blank = store.new()
    assert blank != initial and 'launcherBootstrap' not in row(blank)
    assert row(initial)['launcherBootstrap']
    store.switch_session(initial)
    assert 'launcherBootstrap' not in row(initial)
