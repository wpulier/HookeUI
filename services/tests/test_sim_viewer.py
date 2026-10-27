"""The scenario observer reacts to a received result without adding a polling interval to click time."""
import asyncio
import json

import pytest

from sim.harness import Viewer


class Connection:
    """WebSocket transport boundary: server messages arrive independently of a pending request."""

    def __init__(self):
        self.incoming = asyncio.Queue()
        self.connected = asyncio.Event()

    async def __aenter__(self):
        self.connected.set()
        return self

    async def __aexit__(self, *args):
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        return json.dumps(await self.incoming.get())

    async def send(self, message):
        pass


def test_a_result_received_within_the_deadline_is_not_lost_to_observer_polling(monkeypatch):
    async def check():
        connection = Connection()
        monkeypatch.setattr("sim.harness.websockets.connect", lambda *a, **kw: connection)
        viewer = Viewer("ws://test")
        receiver = asyncio.create_task(viewer.run())
        try:
            await connection.connected.wait()
            pending = asyncio.create_task(viewer.result_for("click", 0, timeout=0.025))
            await asyncio.sleep(0)  # begin waiting before the server response arrives
            response = {"type": "result", "for": "click", "ok": True, "objectId": "o1"}
            await connection.incoming.put(response)
            assert await pending == response
        finally:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)

    asyncio.run(check())


def test_a_command_waits_for_the_handshake_and_reports_a_later_disconnect(monkeypatch):
    async def check():
        handshake = asyncio.Event()
        received = []

        class DelayedConnection(Connection):
            async def __aenter__(self):
                await handshake.wait()
                return await super().__aenter__()

            async def send(self, message):
                received.append(json.loads(message))

        connection = DelayedConnection()
        monkeypatch.setattr("sim.harness.websockets.connect", lambda *a, **kw: connection)
        viewer = Viewer("ws://delayed")
        receiver = asyncio.create_task(viewer.run())
        command = asyncio.create_task(viewer.send({"type": "clear"}))
        try:
            await asyncio.sleep(0)
            assert not command.done()
            assert not received
            handshake.set()
            await command
            assert received == [{"type": "debug", "candidates": True}, {"type": "clear"}]
        finally:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)
            command.cancel()
            await asyncio.gather(command, return_exceptions=True)
        with pytest.raises(ConnectionError, match="connection closed"):
            await viewer.send({"type": "clear"})

    asyncio.run(check())


def test_waiters_keep_their_kind_and_cursor_and_cancelling_one_leaves_another(monkeypatch):
    async def check():
        connection = Connection()
        monkeypatch.setattr("sim.harness.websockets.connect", lambda *a, **kw: connection)
        viewer = Viewer("ws://test")
        receiver = asyncio.create_task(viewer.run())
        try:
            await connection.connected.wait()
            first = {"type": "result", "for": "click", "ok": True, "objectId": "o1"}
            await connection.incoming.put(first)
            assert await viewer.result_for("click", 0) == first
            since = len(viewer.results)
            cancelled = asyncio.create_task(viewer.result_for("ask", since))
            kept = asyncio.create_task(viewer.result_for("click", since))
            await asyncio.sleep(0)
            cancelled.cancel()
            await asyncio.gather(cancelled, return_exceptions=True)
            await connection.incoming.put({"type": "result", "for": "ask", "ok": True})
            second = {"type": "result", "for": "click", "ok": True, "objectId": "o2"}
            await connection.incoming.put(second)
            assert await kept == second
            assert await viewer.result_for("missing", len(viewer.results), timeout=0.01) is None
        finally:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)

    asyncio.run(check())
