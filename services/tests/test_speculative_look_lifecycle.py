"""Synthetic real-admission regressions for unused speculative look failures.

Task._log_traceback is inspected only as asyncio's diagnostic flag: True means a
completed exception is still eligible for 'Task exception was never retrieved'.
Cleanup retrieves expected RED exceptions so this suite does not create noise.
"""
import asyncio

import pytest

from orchestrator.ticket import issue, using
from tests.test_brain_dispatch_budget import admitted, run, world


@pytest.mark.parametrize("timing", ["already-failed", "cancel-then-fail"])
def test_discarded_failed_look_is_observed_once_with_request_provenance(tmp_path, monkeypatch, timing):
    async def go():
        entered = asyncio.Event()

        async def script(entry):
            entered.set()
            if timing == "cancel-then-fail":
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    raise RuntimeError("synthetic failure while canceling look") from None
            raise RuntimeError("synthetic unused look failed")

        brain, hub, providers, logs = world(tmp_path, monkeypatch, script)
        request = issue(hub, "hands")
        look = None
        try:
            with using(request):
                look = brain.start_look("Inspect this synthetic picture.")
            await entered.wait()
            if timing == "already-failed":
                await asyncio.wait({look.task})  # wait for completion without consuming the exception
                assert look.task._log_traceback
            assert look.drop() is True
            await asyncio.wait({look.task})
            await asyncio.sleep(0)  # allow the discard observer's task-done callback to run
            assert look.task._log_traceback is False, "discard left a failed speculative exception unobserved"
            budget = admitted(providers)
            assert budget is request.admission and budget.used == 1
            attempt = budget.attempts[0]
            assert attempt.status == "failed" and attempt.phase == "look"
            records = [record for record in logs if record.get("kind") == "look-discarded"]
            assert len(records) == 1, "discard must retain exactly one essential outcome record"
            record = records[0]
            assert record.get("requestId") == budget.request_id
            assert record.get("attemptIds") == [attempt.attempt_id]
            assert record.get("status") == "failed"
            assert "synthetic" in record.get("error", "") and "look" in record.get("error", "")
            look.drop()
            await asyncio.sleep(0)
            assert len([r for r in logs if r.get("kind") == "look-discarded"]) == 1
            assert budget.used == len(providers.sent) == 1  # no retry, refund or replacement look
        finally:
            if look is not None:
                if not look.task.done():
                    look.task.cancel()
                    await asyncio.gather(look.task, return_exceptions=True)
                elif not look.task.cancelled():
                    look.task.exception()  # expected RED cleanup, after all behavioral assertions
            request.seal()

    run(go())


def test_a_used_look_keeps_its_exception_for_its_rightful_consumer(tmp_path, monkeypatch):
    async def go():
        async def script(entry):
            raise RuntimeError("synthetic claimed look failed")

        brain, hub, providers, logs = world(tmp_path, monkeypatch, script)
        request = issue(hub, "hands")
        try:
            with using(request):
                look = brain.start_look("Inspect the requested source.")
            look.used = True
            await asyncio.wait({look.task})
            assert look.drop() is False
            with pytest.raises(RuntimeError, match="synthetic claimed look failed"):
                await look.task
            assert not [r for r in logs if r.get("kind") == "look-discarded"]
            assert admitted(providers).used == 1
            assert request.admission.attempts[0].status == "failed"
        finally:
            request.seal()

    run(go())
