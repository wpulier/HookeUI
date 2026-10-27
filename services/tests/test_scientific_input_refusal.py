"""Synthetic worker-wire and real HTTP admission; no native-reader qualification."""
import asyncio
from contextlib import contextmanager

import httpx
import pytest

from tests.test_scientific_host_worker import startup_worker
from tests.test_scientific_review_api import api, opening, ORIGIN, HEADERS


def test_declared_input_refusal_reaches_launch_without_accepting_results(tmp_path, monkeypatch):
    message = {'ok': False, 'code': 'SCIENTIFIC_INPUT_REFUSED',
               'error': 'This reader accepts uncompressed chunks only.',
               'next': 'Export an uncompressed copy and keep the original.'}
    worker, _ = startup_worker(monkeypatch, message)
    with api(tmp_path) as (rig, transport, _):
        original = rig.saved.digest
        @contextmanager
        def rejected(origin):
            worker._exchange({'op': 'open', 'inputs': {}})
            raise AssertionError('A refused reader must never become ready')
            yield
        rig.manager._open_worker = rejected
        async def run():
            async with httpx.AsyncClient(transport=transport, base_url=ORIGIN, headers=HEADERS) as client:
                response = await client.post('/api/scientific-reviews', json=opening(rig))
                assert response.status_code == 422
                assert response.json() == message
                assert (await client.get('/api/scientific-reviews')).json()['reviews'] == []
        asyncio.run(run())
        assert rig.store.read(rig.saved.id, rig.saved.session).digest == original
        assert not rig.workers and worker._stopped.is_set()


def test_factory_explicit_refusal_crosses_worker_startup_protocol(tmp_path, monkeypatch):
    import io
    import os
    import sys
    from types import SimpleNamespace
    from env_worker import scientific_host_server as server
    from env_worker.scientific_host import Context
    from env_worker.server import read_message, write_message

    def factory(context):
        context.refuse_input('Choose an uncompressed input.', 'Export a copy; keep the original.')
    class Session:
        def __init__(self, factory, inputs, **kwargs):
            factory(Context(None, inputs, None, None))
    monkeypatch.setattr(server, 'ReviewSession', Session)
    monkeypatch.setattr(server, 'load_factory', lambda *_: factory)
    monkeypatch.setattr(server.native_regions, 'bind', lambda *a, **k: {})
    incoming, output = io.BytesIO(), io.BytesIO()
    write_message(incoming, {'op': 'open', 'inputs': {}}); incoming.seek(0)
    read_fd, write_fd = os.pipe()
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(buffer=incoming))
    monkeypatch.setattr(sys, 'stdout', SimpleNamespace(buffer=output))
    try:
        with pytest.raises(ValueError): server.main(tmp_path, 'fixture:open_host', read_fd)
    finally:
        os.close(write_fd)
    with pytest.raises(OSError): os.fstat(read_fd)
    output.seek(0)
    assert read_message(output)[0] == {'op': 'starting', 'step': 'code'}
    assert read_message(output) == ({'ok': False, 'code': 'SCIENTIFIC_INPUT_REFUSED',
        'error': 'Choose an uncompressed input.', 'next': 'Export a copy; keep the original.'}, b'')
    assert output.read() == b''


@pytest.mark.parametrize('change', [
    {'next': ''}, {'next': 'x' * 1001}, {'error': 7},
    {'error': 'Traceback\nprivate source'}, {'next': 'token\x00secret'},
    {'next': 'Choose\u2028another input.'}, {'next': 'Choose\u202einput.'},
    {'extra': 'private secret'}, {'ok': 0}, {'code': 'UNRECOGNIZED'},
])
def test_malformed_refusal_never_exports_worker_content(monkeypatch, change):
    from orchestrator.scientific_host import Failed
    from orchestrator.scientific_review_api import error_response
    import json
    message = {'ok': False, 'code': 'SCIENTIFIC_INPUT_REFUSED',
               'error': 'private secret', 'next': 'Choose another input.', **change}
    worker, _ = startup_worker(monkeypatch, message)
    with pytest.raises(Failed) as caught:
        worker._exchange({'op': 'open', 'inputs': {}})
    body = json.loads(error_response(caught.value).body)
    assert body['code'] == 'SCIENTIFIC_REVIEW_FAILED'
    assert 'secret' not in body['error'] and 'Traceback' not in body['error']
    assert worker._stopped.is_set()
    with pytest.raises(ValueError, match='closed'): worker.grants.specifications()


@pytest.mark.parametrize('command', [{'op': 'open', 'inputs': {}}, {'op': 'layer-inventory'}])
def test_arbitrary_worker_exception_is_not_an_input_refusal(monkeypatch, command):
    from orchestrator.scientific_host import Failed
    from orchestrator.scientific_review_api import error_response
    import json
    worker, _ = startup_worker(monkeypatch, {'ok': False, 'error': 'ValueError: /private/secret traceback'})
    with pytest.raises(Failed) as caught: worker._exchange(command)
    body = json.loads(error_response(caught.value).body)
    assert body['code'] == 'SCIENTIFIC_REVIEW_FAILED' and '/private/secret' not in body['error']
    assert worker._stopped.is_set()


def test_input_refusal_is_not_admitted_after_worker_startup(monkeypatch):
    from orchestrator.scientific_host import Failed
    worker, _ = startup_worker(monkeypatch, {'ok': False, 'code': 'SCIENTIFIC_INPUT_REFUSED',
        'error': 'Unexpected later refusal', 'next': 'Choose another input.'})
    with pytest.raises(Failed, match='scientific interaction failed'):
        worker._exchange({'op': 'layer-inventory'})
    assert worker._stopped.is_set()


@pytest.mark.parametrize('reason,next_step', [('', 'Choose another input.'), ('x' * 1001, 'Choose another input.'),
    ('Private\ntraceback', 'Choose another input.'), ('Unsupported input.', {'arbitrary': 'metadata'})])
def test_context_refuses_malformed_user_guidance(reason, next_step):
    from env_worker.scientific_host import Context
    from env_worker.scientific_input_refusal import InputRefused
    with pytest.raises(ValueError) as caught:
        Context(None, {}, None, None).refuse_input(reason, next_step)
    assert not isinstance(caught.value, InputRefused)
    assert str(caught.value) == 'Input refusal needs bounded single-line user guidance'
