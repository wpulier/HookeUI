"""A Studio connection owns its registration from the first snapshot send onward."""
import asyncio

import pytest

from orchestrator.paths import Paths
from orchestrator.server import create_app
from tests.waiting import HANG_S


@pytest.mark.parametrize('failure', ['cancel', 'send-error'])
@pytest.mark.parametrize('another_studio', [False, True])
def test_interrupted_initial_snapshot_releases_only_its_own_client(tmp_path, monkeypatch, failure, another_studio):
    web = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))
    hub = web.state.hub
    monkeypatch.setattr(web.state.app, 'snapshot', lambda client: [{'type': 'snapshot'}])
    endpoint = next(route.endpoint for route in web.routes if getattr(route, 'path', None) == '/ws')

    async def interrupted():
        sending, receiving, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class Socket:
            headers = {'origin': 'http://127.0.0.1:8770'}

            def __init__(self, interrupted_snapshot):
                self.interrupted_snapshot = interrupted_snapshot

            async def accept(self):
                pass

            async def send_text(self, message):
                if self.interrupted_snapshot:
                    sending.set()
                    await release.wait()
                    raise RuntimeError('peer left during initial snapshot')

            async def receive_text(self):
                receiving.set()
                await asyncio.Event().wait()

        other = asyncio.create_task(endpoint(Socket(False))) if another_studio else None
        task = None
        try:
            if other is not None:
                await asyncio.wait_for(receiving.wait(), HANG_S)
            live_clients = set(hub.clients)
            hub.view_held = True
            task = asyncio.create_task(endpoint(Socket(True)))
            await asyncio.wait_for(sending.wait(), HANG_S)
            assert len(hub.clients) == 1 + int(another_studio)
            if failure == 'cancel':
                task.cancel()
                expected = asyncio.CancelledError
            else:
                release.set()
                expected = RuntimeError
            with pytest.raises(expected):
                await task
            assert hub.clients == live_clients
            assert hub.view_held is another_studio
            if other is not None:
                other.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await other
                assert not hub.clients
                assert hub.view_held is False
        finally:
            for owned in (task, other):
                if owned is not None and not owned.done():
                    owned.cancel()
            await asyncio.gather(*(owned for owned in (task, other) if owned is not None), return_exceptions=True)

    asyncio.run(interrupted())
