"""Real HTTP/table/correction boundaries over generated input and a stand-in Fiji process; no scientific certification."""
import asyncio
import threading
from pathlib import Path

import httpx
import pytest

from orchestrator.commands import handle
from orchestrator.server import create_app
from tests.test_fiji_particle_journey import journey  # noqa: F401
from tests.test_particle_corrections import analyzed, saved
from tests.test_sources import Client
from tests.waiting import HANG_S


@pytest.mark.parametrize('extension', ['table', 'table.csv'])
def test_table_downloads_are_pinned_to_session_and_correction_revision(journey, extension):
    built, aid, _ = journey
    async def go():
        result = await analyzed(built, aid)
        web = create_app('http://127.0.0.1:1', use_gpu=False, paths=built.paths)
        live = web.state.app
        async with httpx.AsyncClient(transport=httpx.ASGITransport(web), base_url='http://127.0.0.1') as client:
            url = f'/api/resources/{result.id}/{extension}'
            pins = {'session': built.day.sid, 'revision': 0}
            ok = await client.get(url, params=pins)
            assert ok.status_code == 200, ok.text
            stale = await client.get(url, params={**pins, 'revision': 9})
            assert stale.status_code == 409 and 'revision' in stale.json()['error'].lower()
            corrected = Client()
            await handle(live, corrected, {'type': 'population-correct', 'session': built.day.sid,
                'population': result.id, 'revision': 0, 'change': 'remove', 'member': next(iter(result.members))})
            assert corrected.replies[-1]['ok'], corrected.replies
            await saved(live.hub.evidence.get(result.id))
            assert (await client.get(url, params=pins)).status_code == 409
            current = await client.get(url, params={**pins, 'revision': 1})
            assert current.status_code == 200
            await handle(live, Client(), {'type': 'session', 'action': 'new', 'name': 'Other'})
            assert (await client.get(url, params={**pins, 'revision': 1})).status_code == 409
    asyncio.run(go())


@pytest.mark.parametrize('change', ['correction', 'session', 'cancel'])
def test_a_delayed_table_never_publishes_after_the_request_is_superseded(journey, monkeypatch, change):
    built, aid, _ = journey
    async def go():
        result = await analyzed(built, aid)
        web = create_app('http://127.0.0.1:1', use_gpu=False, paths=built.paths)
        live = web.state.app
        started, release = threading.Event(), threading.Event()
        read = Path.read_bytes
        def delayed(path):
            if path == result.path / 'capture.jpg' and not started.is_set():
                started.set()
                assert release.wait(HANG_S)
            return read(path)
        monkeypatch.setattr(Path, 'read_bytes', delayed)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(web), base_url='http://127.0.0.1') as client:
            task = asyncio.create_task(client.get(f'/api/resources/{result.id}/table',
                params={'session': built.day.sid, 'revision': 0}))
            assert await asyncio.to_thread(started.wait, HANG_S)
            try:
                if change == 'session':
                    await handle(live, Client(), {'type': 'session', 'action': 'new', 'name': 'Other'})
                elif change == 'cancel':
                    task.cancel()
                else:
                    corrected = Client()
                    await handle(live, corrected, {'type': 'population-correct', 'session': built.day.sid,
                        'population': result.id, 'revision': 0, 'change': 'remove', 'member': next(iter(result.members))})
                    assert corrected.replies[-1]['ok'], corrected.replies
            finally:
                release.set()
            if change == 'cancel':
                with pytest.raises(asyncio.CancelledError): await task
            else:
                response = await task
                assert response.status_code == 409 and 'rows' not in response.json()
    asyncio.run(go())


@pytest.mark.parametrize('damage', ['deleted', 'corrupt'])
def test_unavailable_original_evidence_is_refused_by_table_and_csv(journey, damage):
    built, aid, _ = journey
    async def go():
        result = await analyzed(built, aid)
        web = create_app('http://127.0.0.1:1', use_gpu=False, paths=built.paths)
        original = result.path / 'source.image'
        if damage == 'deleted': original.unlink()
        else: original.write_bytes(b'corrupt')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(web), base_url='http://127.0.0.1') as client:
            for suffix in ['table', 'table.csv']:
                response = await client.get(f'/api/resources/{result.id}/{suffix}',
                    params={'session': built.day.sid, 'revision': 0})
                assert response.status_code == 409 and 'source.image' in response.json()['error']
    asyncio.run(go())


def test_fiji_tables_and_actions_name_particles_and_the_original_pixel_basis(journey):
    built, aid, _ = journey
    async def go():
        await handle(built, Client(), {'type': 'source', 'open': aid})
        client = Client()
        await handle(built, client, {'type': 'action', 'capability': 'cells',
            'args': {}, 'use': {'cells': 'fiji-analyze-particles'}})
        reply = client.replies[-1]
        assert reply['ok'] and '3 particles' in reply['said'] and 'cell' not in reply['said'].lower()
        assert 'original image' in reply['said'] and 'objective' not in reply['said']
        result = built.hub.evidence.get(reply['table'])
        await saved(result)
        web = create_app('http://127.0.0.1:1', use_gpu=False, paths=built.paths)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(web), base_url='http://127.0.0.1') as client:
            response = await client.get(f'/api/resources/{result.id}/table', params={'session': built.day.sid, 'revision': 0})
            table = response.json()
            assert table['memberLabel'] == 'particle' and table['columns'][0]['label'] == 'Particle'
            assert 'original image' in table['note'] and 'camera' not in table['note'] and 'objective' not in table['note']
            assert table['definitions']['areaPx'] == result.columns['measure']['definitions']['areaPx']
    asyncio.run(go())
