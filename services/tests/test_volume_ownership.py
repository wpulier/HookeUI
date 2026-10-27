"""Synthetic streamed uploads/manual commands through actual session stores and tiny adapter processes."""
import asyncio

import httpx
import pytest

from orchestrator import commands
from orchestrator.paths import Paths
from orchestrator.server import create_app
from tests.test_registrations import home, volume_file, wait


def test_upload_keeps_the_session_that_started_receiving_it(home):
    web = create_app("http://127.0.0.1:1", use_gpu=False, paths=Paths.under(home / "profile"))
    app = web.state.app
    origin = app.day.here()

    async def run():
        async def body():
            yield b"a brain, "
            app.day.new("Other specimen")
            yield b"voxel sizes stated"

        async with httpx.AsyncClient(transport=httpx.ASGITransport(web), base_url="http://127.0.0.1:8770") as client:
            response = await client.post("/api/volumes/upload?name=original.tif", content=body(),
                                         headers={"Origin": "http://127.0.0.1:8770", "X-MicroscopeMind": "add-volume"})
        assert response.status_code == 200, response.text
        return response.json()["volume"]

    record = asyncio.run(run())
    assert record["session"] == origin.session and record["thread"] == origin.thread
    assert app.registrations.records("v") == []
    assert app.registrations.get(record["id"], origin.session) is not None


@pytest.mark.parametrize("kind", ["volume-add", "job-start", "job-again"])
def test_manual_volume_work_keeps_its_owner_across_thread_dispatch(home, monkeypatch, kind):
    web = create_app("http://127.0.0.1:1", use_gpu=False, paths=Paths.under(home / "profile"))
    app = web.state.app
    regs, origin = app.registrations, app.day.here()
    path = volume_file(home, "voxel sizes stated")
    message = {"type": kind, "path": str(path)}
    if kind != "volume-add":
        volume = regs.add_volume(str(path), thread=origin.thread)
        regs.describe(volume["id"], orientation="psl")
        message["volume"] = volume["id"]
        if kind == "job-again":
            message["id"] = wait(regs, regs.start(volume["id"], thread=origin.thread)["id"])["id"]
    dispatch = asyncio.to_thread

    async def switch_then_dispatch(fn, *args, **kwargs):
        app.day.new("Other specimen")
        return await dispatch(fn, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", switch_then_dispatch)
    try:
        result = asyncio.run(commands.COMMANDS[kind](app, None, message))
        record = result["volume" if kind == "volume-add" else "job"]
        assert record["session"] == origin.session and record["thread"] == origin.thread
    finally:
        regs.stop()
