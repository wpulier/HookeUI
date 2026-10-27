import asyncio
from types import SimpleNamespace

from orchestrator import execution
from orchestrator.gpu_client import GpuClient
from orchestrator.vision import OpenAIVision
from gpu_worker.worker import execution_facts


def test_worker_receipt_distinguishes_dispatch_and_fixed_tracking():
    models = SimpleNamespace(device="cpu")
    assert execution_facts(models, {"op": "segment", "model": "sam2.1-tiny"})["model"] == "facebook/sam2.1-hiera-tiny"
    assert execution_facts(models, {"op": "track"})["adapter"] == "gpu_worker.objects.ObjectSession"
    assert execution_facts(models, {"op": "track"})["selection"] == "fixed"
    assert execution_facts(models, {"op": "segment", "model": "sam3.1-local"})["model"] == "facebook/sam3.1/sam3.1_multiplex.pt"


def test_gpu_receipt_is_requested_only_in_scope_and_stripped_from_result():
    async def go():
        client = GpuClient()
        client._loop = asyncio.get_running_loop()
        sent = []

        def send(req):
            sent.append(req)
            reply = {"ok": True, "polygons": [], "ms": 1}
            if req.get("_execution"):
                reply["_execution"] = {"model": "actual-model", "provider": "local", "adapter": "actual.adapter"}
            client._pending[req["id"]].set_result(reply)

        client._conn = SimpleNamespace(send=send)
        baseline = await client.call("segment", model="edgetam")
        log = execution.ExecutionLog(enabled=True)
        with log.request("click") as root:
            recorded = await client.call("segment", model="edgetam")
        assert recorded == baseline
        assert "_execution" not in sent[0] and sent[1]["_execution"] is True
        assert log.inspect(root.id)["steps"][0]["model"] == "actual-model"

    asyncio.run(go())


def test_vision_receipt_uses_provider_response_without_additional_calls():
    async def go():
        client = object.__new__(OpenAIVision)
        client.model, client.reasoning_effort, client.service_tier = "selected-alias", "none", None
        calls = []

        async def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(model="resolved-2026-10", output_text='{"answer":"same answer"}', usage=None)

        client.client = SimpleNamespace(responses=SimpleNamespace(create=create))
        baseline = await client.ask([b"a"], "same prompt")
        log = execution.ExecutionLog(enabled=True)
        with log.request("ask") as root:
            recorded = await client.ask([b"a"], "same prompt")
        assert calls[0] == calls[1] and len(calls) == 2
        assert baseline.data == recorded.data and baseline.model == recorded.model
        row = log.inspect(root.id)["steps"][0]
        assert row["selected"] == "selected-alias" and row["reportedModel"] == "resolved-2026-10"
        assert "same prompt" not in repr(log.inspect(root.id)) and "same answer" not in repr(log.inspect(root.id))

    asyncio.run(go())


def test_gpu_receipt_start_is_worker_ack_not_queue_submission(monkeypatch):
    from orchestrator import gpu_client
    clock = [100.0]
    monkeypatch.setattr(gpu_client.time, 'time', lambda: clock[0])
    async def go():
        client = GpuClient()
        client._loop = asyncio.get_running_loop()
        sent = []
        client._conn = SimpleNamespace(send=lambda request: sent.append(request))
        log = execution.ExecutionLog(enabled=True)
        with log.request('cells') as root:
            task = asyncio.create_task(client.call('cells', model='cellpose-sam'))
            await asyncio.sleep(0)
            row = log.inspect(root.id)['steps'][0]
            assert row['scope'] == 'cells' and 'workerStartedAt' not in row
            request = sent[0]
            clock[0] = 101.0
            client._replied({'id': request['id'], 'started': True})
            row = log.inspect(root.id)['steps'][0]
            assert row['workerStartedAt'] == 101 and 'workerFinishedAt' not in row
            clock[0] = 102.0
            client._replied({'id': request['id'], 'ok': True, 'count': 3})
            assert (await task)['count'] == 3
        row = log.inspect(root.id)['steps'][0]
        assert row['workerStartedAt'] == 101 and row['workerFinishedAt'] == 102
    asyncio.run(go())
