import asyncio

from orchestrator.hub import Client
from tests.waiting import HANG_S


def test_control_messages_survive_a_frame_flood_and_go_first():
    async def go():
        client = Client(websocket=None)
        client.offer('{"type":"chat","n":1}')
        for i in range(500):
            client.offer(b"frame-%d" % i)  # a slow browser falls behind on video
        client.offer('{"type":"result","n":2}')
        client.offer('{"type":"candidates"}', lossy=True)
        out = [await client.next_message() for _ in range(4)]
        assert out[:2] == ['{"type":"chat","n":1}', '{"type":"result","n":2}']
        # only the newest of the lossy messages are left after the control messages
        assert set(out[2:]) == {b"frame-499", '{"type":"candidates"}'}
        assert client.frames.empty() and client.control.empty()

    asyncio.run(go())


def test_next_message_waits_for_new_messages():
    async def go():
        client = Client(websocket=None)
        waiter = asyncio.create_task(client.next_message())
        await asyncio.sleep(0.01)
        assert not waiter.done()
        client.offer('{"type":"scene"}')
        assert await asyncio.wait_for(waiter, HANG_S) == '{"type":"scene"}'

    asyncio.run(go())
