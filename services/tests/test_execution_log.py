"""Execution facts are a bounded side log, never conversation memory or model input."""
import asyncio
import json

import pytest


def test_nested_attempts_and_final_producer_are_distinct():
    from orchestrator import execution

    log = execution.ExecutionLog(enabled=True)
    with log.request("click", day="2026-10-06", thread="t1", generation=1) as root:
        with execution.span("outline", selected="edgetam", selection="settings") as outline:
            with execution.span("segment", provider="local", model="edgetam") as local:
                local.finish("empty")
            with execution.span("segment", provider="fal", model="sam3.1") as remote:
                remote.finish("completed")
            outline.finish("completed", producer=remote.id, fallback="empty-local-box")
            execution.bind(object_id="o1", turn_id=7)
    data = log.inspect(root.id)
    assert data["root"]["turnId"] == 7
    assert data["root"]["objectIds"] == ["o1"]
    assert [(s["operation"], s["outcome"]) for s in data["steps"]] == [
        ("outline", "completed"), ("segment", "empty"), ("segment", "completed")]
    assert data["steps"][0]["producer"] == remote.id
    assert data["steps"][0]["fallback"] == "empty-local-box"
    assert data["steps"][0]["selected"] == "edgetam"
    assert log.for_object("o1", 1) == root.id


def test_instrumentation_cannot_replace_primary_error_or_cancellation():
    from orchestrator import execution

    def broken_publish(_):
        raise RuntimeError("UI broke")

    log = execution.ExecutionLog(enabled=True, publish=broken_publish)
    error = ValueError("original error")
    with pytest.raises(ValueError) as caught:
        with log.request("click") as root:
            with execution.span("segment"):
                raise error
    assert caught.value is error
    assert log.inspect(root.id)["steps"][0]["outcome"] == "failed"
    # Raw errors may contain credentials; the receipt keeps only their type.
    assert "original error" not in json.dumps(log.inspect(root.id))
    with pytest.raises(asyncio.CancelledError):
        with log.request("ask") as cancelled:
            raise asyncio.CancelledError()
    assert log.inspect(cancelled.id)["root"]["outcome"] == "cancelled"


def test_disabled_collection_is_empty_and_never_publishes():
    from orchestrator import execution

    notices = []
    log = execution.ExecutionLog(enabled=False, publish=notices.append)
    with log.request("click") as root:
        with execution.span("segment"):
            execution.note(model="edgetam")
    assert root.id is None
    assert log.references() == [] and notices == []


def test_capacity_evicts_whole_requests_and_their_indexes():
    from orchestrator import execution

    log = execution.ExecutionLog(enabled=True, max_steps=3, max_roots=2)
    with log.request("click", generation=1) as first:
        with execution.span("segment"):
            execution.bind(object_id="o1")
    with log.request("click", generation=1) as second:
        with execution.span("segment"):
            execution.bind(object_id="o2")
    with log.request("click", generation=2) as third:
        with execution.span("segment"):
            execution.bind(object_id="o1")
    assert log.inspect(first.id)["status"] == "expired"
    assert log.for_object("o1", 1) is None
    assert log.for_object("o1", 2) == third.id
    assert len(log.references()) == 2


def test_per_request_limits_are_reported_instead_of_growing_forever():
    from orchestrator import execution

    log = execution.ExecutionLog(enabled=True, max_per_root=2)
    with log.request("ask") as root:
        for _ in range(5):
            with execution.span("segment", model="x" * 10000):
                pass
    data = log.inspect(root.id)
    assert len(data["steps"]) == 2 and data["root"]["incomplete"]
    assert len(data["steps"][0]["model"]) <= 128


def test_parallel_and_late_children_keep_their_original_request():
    from orchestrator import execution

    async def go():
        log = execution.ExecutionLog(enabled=True)
        late = asyncio.Event()

        async def child():
            await late.wait()
            with execution.span("name", model="gemini"):
                execution.bind(object_id="o1")

        with log.request("click", thread="t1") as first:
            task = asyncio.create_task(child())
        with log.request("click", thread="t2") as second:
            with execution.span("segment", model="sam2"):
                pass
            late.set()
            await task
        assert log.inspect(first.id)["root"]["objectIds"] == ["o1"]
        assert [s["model"] for s in log.inspect(first.id)["steps"]] == ["gemini"]
        assert [s["model"] for s in log.inspect(second.id)["steps"]] == ["sam2"]

    asyncio.run(go())


def test_blind_identity_cannot_be_inspected_before_reveal():
    from orchestrator import execution

    log = execution.ExecutionLog(enabled=True)
    with log.request("click") as root:
        execution.bind(trial_id="t1")
        with execution.span("segment", model="sam3.1"):
            pass
    assert log.inspect(root.id)["status"] == "blind"
    assert "sam3.1" not in json.dumps(log.inspect(root.id))
    log.reveal("t1")
    assert log.inspect(root.id)["steps"][0]["model"] == "sam3.1"


def test_unrecognized_fields_and_model_content_are_not_retained():
    from orchestrator import execution

    log = execution.ExecutionLog(enabled=True)
    with log.request("ask") as root:
        with execution.span("vision"):
            execution.note(provider="openai", model="gpt-6-luna", prompt="private prompt", answer="AI claim", jpeg=b"large")
    encoded = json.dumps(log.inspect(root.id))
    assert "private prompt" not in encoded and "AI claim" not in encoded and "jpeg" not in encoded


def test_dropped_child_cannot_overwrite_its_parent():
    from orchestrator import execution

    log = execution.ExecutionLog(enabled=True, max_per_root=1)
    with log.request("click") as root:
        with execution.span("outline", model="retained"):
            with execution.span("dropped-child"):
                execution.finish("failed", model="must-not-replace-parent")
    row = log.inspect(root.id)["steps"][0]
    assert row["model"] == "retained" and row["outcome"] == "completed"


def test_burst_has_one_scheduled_drain_and_a_bounded_pending_set(monkeypatch):
    from orchestrator import execution
    from types import SimpleNamespace

    scheduled = []
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: SimpleNamespace(call_later=lambda *args: scheduled.append(args)))
    log = execution.ExecutionLog(enabled=True, max_roots=2, publish=lambda _: None)
    for _ in range(100):
        with log.request("click"):
            pass
    assert len(log._pending) == 2 and len(scheduled) == 1


def test_recording_fault_runs_the_primary_action_once(monkeypatch):
    from orchestrator import execution
    from types import SimpleNamespace

    log = execution.ExecutionLog(enabled=True)
    owner = SimpleNamespace(hub=SimpleNamespace(execution=log))
    calls = []

    @execution.traced("brain", request="ask")
    async def action(owner):
        calls.append(owner)
        return {"answer": "unchanged"}

    monkeypatch.setattr(log, "request", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("broken receipt")))
    assert asyncio.run(action(owner)) == {"answer": "unchanged"}
    assert calls == [owner]


def test_secondary_message_cannot_evict_a_primary_control_message():
    from orchestrator.hub import Client, Hub
    from types import SimpleNamespace

    client = Client(None)
    for n in range(8):
        client.offer(f"primary-{n}")
    hub = SimpleNamespace(clients={client})
    Hub.broadcast_execution(hub, {"type": "execution", "entry": {"id": "e1"}})
    assert client.control.qsize() == 8 and client.control_overflow == 0
    assert [client.control.get_nowait() for _ in range(8)] == [f"primary-{n}" for n in range(8)]


def test_broken_timing_instrument_cannot_stop_an_action(monkeypatch):
    from orchestrator import execution

    log = execution.ExecutionLog(enabled=True)
    monkeypatch.setattr(execution.time, "perf_counter", lambda: (_ for _ in ()).throw(RuntimeError("clock unavailable")))
    completed = []
    with log.request("click"):
        with execution.span("segment"):
            completed.append(True)
    assert completed == [True]
    assert log.references()[0]["incomplete"]
