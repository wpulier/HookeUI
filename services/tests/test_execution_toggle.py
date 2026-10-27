"""Switching diagnostics must not change work, context, or revive old scopes."""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator import commands, execution
from orchestrator.gpu_client import GpuClient
from orchestrator.paths import Paths
from orchestrator.server import create_app


def test_off_clears_receipts_and_pending_notices_without_reviving_them():
    async def go():
        notices = []
        log = execution.ExecutionLog(enabled=True, publish=notices.append)
        with log.request("click") as old:
            with execution.span("segment"):
                execution.bind(object_id="o1")
        drain = log._drain_handle
        log.set_enabled(False)
        assert drain.cancelled()
        assert not log.enabled and log.references() == []
        assert log.for_object("o1") is None and log._steps == 0
        assert log.inspect(old.id)["status"] == "expired"
        await asyncio.sleep(0.06)
        assert notices == []
        log.set_enabled(True)
        with log.request("click") as fresh:
            pass
        assert fresh.id != old.id
        await asyncio.sleep(0.06)
        assert [n["entry"]["id"] for n in notices] == [fresh.id]
    asyncio.run(go())


def test_off_during_work_stops_metadata_even_after_reenabled_and_keeps_the_result():
    async def go():
        log = execution.ExecutionLog(enabled=True)
        release = asyncio.Event()
        started = asyncio.Event()
        sent = []
        client = GpuClient()
        client._loop = asyncio.get_running_loop()

        def send(req):
            sent.append(req)
            client._pending[req["id"]].set_result({"ok": True, "polygons": [[1, 2]], "ms": 1})

        client._conn = SimpleNamespace(send=send)

        async def work():
            with log.request("click"):
                with execution.span("outline"):
                    started.set()
                    await release.wait()
                    assert not execution.recording() and execution.root_id() is None
                    execution.note(model="late-model")
                    execution.bind(object_id="late")
                    return await client.call("segment", model="edgetam")

        task = asyncio.create_task(work())
        await started.wait()
        log.set_enabled(False)
        log.set_enabled(True)
        release.set()
        assert await task == {"ok": True, "polygons": [[1, 2]], "ms": 1}
        assert "_execution" not in sent[0]
        assert log.references() == [] and log.for_object("late") is None
        assert not execution.recording()
    asyncio.run(go())


def test_disabled_scopes_skip_fact_copying_and_timing_but_preserve_errors(monkeypatch):
    log = execution.ExecutionLog(enabled=True)
    with log.request("click"):
        with execution.span("outline") as stage:
            log.set_enabled(False)
            def unwanted(*args):
                pytest.fail("disabled instrumentation ran")
            monkeypatch.setattr(execution, "_facts", unwanted)
            monkeypatch.setattr(execution.time, "perf_counter", unwanted)
            stage.finish("completed", model="late")
            execution.note(model="late")
            execution.finish("completed")
            with execution.span("segment"):
                pass
            error = ValueError("primary error")
            with pytest.raises(ValueError) as caught:
                with log.request("click"):
                    raise error
            assert caught.value is error
    assert log.references() == []


def test_disabling_during_an_already_dispatched_gpu_call_keeps_its_result_only():
    async def go():
        log = execution.ExecutionLog(enabled=True)
        client = GpuClient()
        client._loop = asyncio.get_running_loop()
        sent = asyncio.Event()
        dispatched = []
        def send(req):
            dispatched.append(req)
            sent.set()
        client._conn = SimpleNamespace(send=send)
        async def work():
            with log.request("click"):
                return await client.call("segment", model="edgetam")
        task = asyncio.create_task(work())
        await sent.wait()
        assert dispatched[0]["_execution"]
        log.set_enabled(False)
        client._pending[dispatched[0]["id"]].set_result({
            "ok": True, "polygons": [[1, 2]], "_execution": {"model": "late-model"}})
        assert await task == {"ok": True, "polygons": [[1, 2]]}
        assert log.references() == [] and log._pending == {}
    asyncio.run(go())


def test_switch_does_not_swallow_primary_task_cancellation():
    async def go():
        log = execution.ExecutionLog(enabled=True)
        started = asyncio.Event()
        async def work():
            with log.request("click"):
                with execution.span("segment"):
                    started.set()
                    await asyncio.Event().wait()
        task = asyncio.create_task(work())
        await started.wait()
        log.set_enabled(False)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert log.references() == []
    asyncio.run(go())


def test_recording_command_is_separate_from_models_chat_and_reconnects(tmp_path, monkeypatch):
    from orchestrator import downloads

    # The disk's free space is live telemetry on the wire (the setup sheet's exact fit): held still here, so the comparison
    # below is of the app's own state, which the toggle must not change.
    monkeypatch.setattr(downloads, "disk", lambda environ=None: {"freeBytes": 10 * (1 << 30), "spareBytes": downloads.SPARE})
    api = create_app("http://127.0.0.1:1", use_gpu=False, paths=Paths.under(tmp_path))
    app = api.state.app
    app.hub.execution = execution.ExecutionLog(enabled=True)
    before = (app.settings_wire(), app.capabilities_wire(), app.dialogue.history(), app.brain.voice_recap())
    sent = []
    app.hub.broadcast = sent.append
    command = commands.COMMANDS["execution-recording"]
    reply = asyncio.run(command(app, None, {"enabled": False}))
    assert reply == {"ok": True, "enabled": False}
    assert sent == [{"type": "execution-list", "enabled": False, "generation": app.day.generation, "entries": []}]
    assert sent[0] in app.snapshot()
    assert before == (app.settings_wire(), app.capabilities_wire(), app.dialogue.history(), app.brain.voice_recap())
    for invalid in ("false", 0, None):
        with pytest.raises(commands.tools.ToolError):
            asyncio.run(command(app, None, {"enabled": invalid}))
    assert not app.hub.execution.enabled
    assert asyncio.run(command(app, None, {"enabled": True})) == {"ok": True, "enabled": True}
    assert app.hub.execution.enabled
