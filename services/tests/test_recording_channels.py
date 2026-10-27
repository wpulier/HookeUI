"""Recording capability evidence; bytes alone never imply microphone or camera capture."""
import asyncio
import json

import httpx
from fastapi import FastAPI

from orchestrator.recordings import add_recording_routes, recording_label


def test_two_recordings_do_not_collide_and_channels_survive_restart(tmp_path, monkeypatch):
    async def no_convert(*_):
        pass
    monkeypatch.setattr('orchestrator.recordings._make_playable', no_convert)
    async def scenario():
        app = FastAPI()
        add_recording_routes(app, tmp_path)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as client:
            first = (await client.post('/api/recordings/start', json={'ext': 'webm'})).json()['id']
            second = (await client.post('/api/recordings/start', json={'ext': 'webm'})).json()['id']
            assert first != second
            assert (await client.post(f'/api/recordings/{first}/channels', json={'channel': 'microphone', 'state': 'unavailable'})).status_code == 200
            assert (await client.post(f'/api/recordings/{first}/channels', json={'channel': 'screen', 'state': 'available', 'historyGap': True, 'grant': 'never store this'})).status_code == 200
            assert (await client.post(f'/api/recordings/{first}/channels', json={'channel': 'invented', 'state': 'captured'})).status_code == 400
            assert (await client.post(f'/api/recordings/{first}/append', content=b'original-chunk')).status_code == 200
            assert (await client.post(f'/api/recordings/{first}/stop', json={'channelHistoryIncomplete': True})).json()['ok']
            assert (await client.post(f'/api/recordings/{first}/channels', json={'channel': 'camera', 'state': 'captured'})).status_code == 404
            assert (await client.post(f'/api/recordings/{second}/stop')).json()['ok'] is False
        record = tmp_path / f'MicroscopeMind-{first}.channels.jsonl'
        events = [json.loads(line) for line in record.read_text().splitlines()]
        assert events[0]['channels'] == dict.fromkeys(['screen', 'tabAudio', 'microphone', 'camera'], 'unavailable')
        assert events[-1]['event'] == 'stop'
        assert events[-1]['channelHistoryIncomplete'] is True
        assert any(e.get('historyGap') is True for e in events)
        assert [e['sequence'] for e in events] == list(range(1, len(events) + 1))
        assert all(e['at'].endswith('+00:00') and e['monoNs'] > 0 for e in events)
        assert 'never store this' not in record.read_text()
        assert (tmp_path / f'MicroscopeMind-{first}.webm').read_bytes() == b'original-chunk'
        # A process restart does not need the in-memory active map to recover capabilities.
        fresh = FastAPI()
        add_recording_routes(fresh, tmp_path)
        assert record.is_file()
    asyncio.run(scenario())


def test_recording_scope_is_bounded_and_retained_without_inventing_capture(tmp_path):
    async def run():
        app=FastAPI();add_recording_routes(app,tmp_path)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            refused=await client.post('/api/recordings/start',json={'ext':'webm','scope':'phone-secret'})
            assert refused.status_code==400 and not list(tmp_path.iterdir())
            for body,scope in [({'ext':'webm'},'workspace'),({'ext':'webm','scope':'annotated'},'annotated')]:
                result=await client.post('/api/recordings/start',json=body)
                rid=result.json()['id']
                record=json.loads((tmp_path/f'MicroscopeMind-{rid}.channels.jsonl').read_text().splitlines()[0])
                assert record['scope']==scope
                assert recording_label(tmp_path/f'MicroscopeMind-{rid}.channels.jsonl')==('Annotated view' if scope=='annotated' else 'Workspace recording')
                assert set(record['channels'].values())=={'unavailable'}
    asyncio.run(run())


def test_malformed_recording_scope_keeps_legacy_label(tmp_path):
    sidecar=tmp_path/'recording.channels.jsonl'
    for scope in ([],{},None):
        sidecar.write_text(json.dumps({'schema':1,'event':'start','scope':scope})+'\n')
        assert recording_label(sidecar)=='Screen recording'


def test_recording_data_open_uses_exact_owned_movie_and_retains_library_identity(tmp_path):
    from orchestrator.library import Library
    from orchestrator.resources import Entry

    rid = 'MicroscopeMind-20261022-201400-012345abcdef'
    day = '2026-10-22'
    movie = tmp_path / f'{rid}.mp4'
    movie.write_bytes(b'synthetic media fixture; no codec qualification')
    entry = Entry(rid, 'recording', 'Annotated view · 20:14', day, 1792714440)
    library_path = tmp_path / 'library.jsonl'
    resolve = lambda ident, owner: entry if ident == rid and owner == day else None

    async def scenario():
        library = Library(library_path)
        app = FastAPI()
        add_recording_routes(app, tmp_path, resolve=resolve, library=library)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as client:
            url = f'/api/recordings/{rid}/asset'
            assert (await client.post(url, params={'day': 'another-project'})).status_code == 404
            assert not library_path.exists()
            entry.state = 'recording'
            assert (await client.post(url, params={'day': day})).status_code == 404
            entry.state = 'done'
            opened = await client.post(url, params={'day': day})
            assert opened.status_code == 200
            asset = opened.json()['asset']
            assert asset['kind'] == 'recording' and asset['title'] == entry.title
            assert asset['origin'] == day and asset['file'] == movie.name
            assert library.get(asset['id']).path == movie
            # The real Library persists this identity for preview, Tab input and restart.
            reopened_library = Library(library_path)
            assert reopened_library.get(asset['id']).path == movie
            again = (await client.post(url, params={'day': day})).json()['asset']
            assert again['id'] == asset['id']
            movie.unlink()
            outside = tmp_path.parent / f'{tmp_path.name}-outside.mp4'
            outside.write_bytes(b'not owned recording')
            movie.symlink_to(outside)
            try:
                assert (await client.post(url, params={'day': day})).status_code == 404
            finally:
                outside.unlink()
    asyncio.run(scenario())


def test_recording_open_keeps_original_webm_while_mp4_conversion_appears(tmp_path):
    from orchestrator.library import Library
    from orchestrator.resources import Entry

    rid = 'MicroscopeMind-20261022-201400-012345abcdef'
    day = '2026-10-22'
    original = tmp_path / f'{rid}.webm'
    original.write_bytes(b'synthetic original; not codec evidence')
    entry = Entry(rid, 'recording', 'Annotated view', day, 1792714440)
    library = Library(tmp_path / 'library.jsonl')

    async def scenario():
        app = FastAPI()
        add_recording_routes(app, tmp_path, resolve=lambda ident, owner: entry if (ident, owner) == (rid, day) else None,
                             library=library)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as client:
            url = f'/api/recordings/{rid}/asset?day={day}'
            first = (await client.post(url)).json()['asset']
            (tmp_path / f'{rid}.mp4').write_bytes(b'partial asynchronous conversion')
            reopened = await client.post(url)
            assert reopened.status_code == 200
            assert reopened.json()['asset']['id'] == first['id']
            assert reopened.json()['asset']['file'] == original.name
            assert library.get(first['id']).path == original
    asyncio.run(scenario())


def test_private_studio_stack_explicitly_isolates_browser_recordings(monkeypatch, tmp_path):
    from tests.scientific_studio_stack import Stack
    from orchestrator.paths import Paths

    monkeypatch.setattr(Stack, 'start', lambda self: None)  # Configuration only: no build, servers or devices.
    stack = Stack({'environment': {'MICROMIND_CATALOG': str(tmp_path / 'catalog'),
                                   'MICROMIND_ENVS': str(tmp_path / 'envs')}}, build=False)
    try:
        assert Paths.from_env(stack.env).recordings == stack.saves / 'recordings'
        assert Paths.from_env(stack.env).recordings.is_relative_to(stack.home)
    finally:
        stack.close()
