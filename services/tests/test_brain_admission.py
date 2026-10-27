"""Synthetic contract for actual answer dispatches, with no provider or loop involved.

The admission object is shared by a request's hands, look, continuation and late
tasks. A send spy is the external provider boundary, not an internal counter mock.
The initial commit deliberately contains only these tests for the serial RED gate.
"""

import asyncio
import importlib
import importlib.util

import pytest
from tests.waiting import HANG_S


def admission_api():
    name = "orchestrator.brain.admission"
    assert importlib.util.find_spec(name) is not None, (
        "request-owned actual-dispatch admission is not implemented"
    )
    return importlib.import_module(name)


def run(coro):
    async def bounded():
        return await asyncio.wait_for(coro, timeout=HANG_S)

    return asyncio.run(bounded())


class ProviderSpy:
    def __init__(self):
        self.sent = []

    def send(self, label, *, result=None, error=None, entered=None, release=None):
        async def dispatch():
            self.sent.append(label)
            if entered is not None:
                entered.set()
            if release is not None:
                await release.wait()
            if error is not None:
                raise error
            return result

        return dispatch


def call(budget, provider, label, *, phase="query", reservation=None, **send_options):
    return budget.dispatch(
        attempt_id=label,
        phase=phase,
        model="synthetic-provider",
        effort="test",
        send=provider.send(label, **send_options),
        reservation=reservation,
    )


def test_one_typed_answer_needs_only_one_dispatch():
    api = admission_api()
    budget = api.RequestAdmission("run-1/request-1")
    provider = ProviderSpy()
    answer = {"answer": "A first guess.", "actions": []}

    result = run(call(budget, provider, "look-1", phase="look", result=answer))

    assert result is answer
    assert provider.sent == ["look-1"]
    assert api.MAX_CALLS == 3
    assert budget.used == 1
    assert budget.remaining == 2
    attempt, = budget.attempts
    assert (attempt.request_id, attempt.attempt_id, attempt.phase) == (
        "run-1/request-1", "look-1", "look",
    )
    assert (attempt.model, attempt.effort, attempt.status) == (
        "synthetic-provider", "test", "done",
    )


@pytest.mark.parametrize("first_phase", ["fast", "hands", "look"])
def test_handoff_query_and_late_share_the_same_three_attempts(first_phase):
    api = admission_api()
    budget = api.RequestAdmission("run-1/request-1")
    provider = ProviderSpy()

    async def exercise():
        await call(budget, provider, "first", phase=first_phase)
        # Passing the original reference into later work must not renew its cap.
        handed_off = budget
        await call(handed_off, provider, "query", phase="query")
        await call(handed_off, provider, "late", phase="late")
        with pytest.raises(api.AdmissionDenied) as refused:
            await call(budget, provider, "fourth", phase="final")
        assert refused.value.reason == "budget-exhausted"

    run(exercise())

    assert provider.sent == ["first", "query", "late"]
    assert budget.used == 3 and budget.remaining == 0


def test_final_reservation_prevents_a_query_from_spending_synthesis_capacity():
    api = admission_api()
    budget = api.RequestAdmission("run-1/request-1")
    provider = ProviderSpy()
    final = budget.reserve_final()

    async def exercise():
        await call(budget, provider, "hands", phase="hands")
        await call(budget, provider, "look", phase="look")
        with pytest.raises(api.AdmissionDenied) as refused:
            await call(budget, provider, "query", phase="query")
        assert refused.value.reason == "final-reserved"
        assert budget.used == 2
        await call(budget, provider, "final", phase="final", reservation=final)
        # Releasing a consumed reservation cannot refund an actual dispatch.
        budget.release_final(final)
        with pytest.raises(api.AdmissionDenied):
            await call(budget, provider, "late", phase="late")

    run(exercise())

    assert provider.sent == ["hands", "look", "final"]
    assert budget.used == 3 and budget.remaining == 0


def test_unused_final_reservation_can_be_released_without_spending_a_call():
    api = admission_api()
    budget = api.RequestAdmission("run-1/request-1")
    provider = ProviderSpy()
    final = budget.reserve_final()
    assert budget.used == 0
    assert budget.remaining == 2
    budget.release_final(final)
    assert budget.remaining == 3

    async def exercise():
        for label in ("first", "second", "third"):
            await call(budget, provider, label)

    run(exercise())
    assert provider.sent == ["first", "second", "third"]


def test_final_reservation_cannot_be_borrowed_by_another_request_or_a_query():
    api = admission_api()
    first = api.RequestAdmission("run-1/request-1")
    second = api.RequestAdmission("run-1/request-2")
    provider = ProviderSpy()
    final = first.reserve_final()

    async def exercise():
        with pytest.raises(api.AdmissionDenied) as wrong_request:
            await call(second, provider, "foreign", phase="final", reservation=final)
        assert wrong_request.value.reason == "invalid-reservation"
        with pytest.raises(api.AdmissionDenied) as wrong_phase:
            await call(first, provider, "query", reservation=final)
        assert wrong_phase.value.reason == "invalid-reservation"
        await call(first, provider, "late", phase="late", reservation=final)
        with pytest.raises(api.AdmissionDenied) as already_used:
            await call(first, provider, "again", phase="final", reservation=final)
        assert already_used.value.reason == "invalid-reservation"

    run(exercise())
    assert provider.sent == ["late"]
    assert first.used == 1 and second.used == 0


def test_four_concurrent_callers_dispatch_at_most_three_providers():
    api = admission_api()
    budget = api.RequestAdmission("run-1/request-1")
    provider = ProviderSpy()

    async def exercise():
        release = asyncio.Event()
        entered = [asyncio.Event() for _ in range(3)]
        tasks = [asyncio.create_task(call(
            budget, provider, str(index), entered=event, release=release,
        )) for index, event in enumerate(entered)]
        try:
            await asyncio.gather(*(event.wait() for event in entered))
            with pytest.raises(api.AdmissionDenied) as refused:
                await call(budget, provider, "fourth")
            assert refused.value.reason == "budget-exhausted"
            assert budget.used == 3
        finally:
            release.set()
            await asyncio.gather(*tasks)

    run(exercise())
    assert provider.sent == ["0", "1", "2"]
    assert len(budget.attempts) == 3


def test_the_answer_keeps_its_own_call_once_when_the_three_are_spent():
    """Live 2026-10-11 01:07 "highlight the blue area": recovery spent the third call and the user got a bare error
    card. The answer's last word: once per request, a final synthesis beyond the three; nothing else."""
    api = admission_api()
    budget = api.RequestAdmission("run-1/request-1")
    provider = ProviderSpy()

    async def exercise():
        for label in ("0", "1", "2"):
            await call(budget, provider, label)
        with pytest.raises(api.AdmissionDenied):
            await call(budget, provider, "query-beyond")
        last = budget.reserve_last_word()
        assert last is not None
        with pytest.raises(api.AdmissionDenied) as refused:
            await call(budget, provider, "query-with-it", reservation=last)
        assert refused.value.reason == "invalid-reservation"
        await call(budget, provider, "answer", phase="final", reservation=last)
        assert budget.reserve_last_word() is None  # once per request

    run(exercise())
    assert provider.sent == ["0", "1", "2", "answer"]


def test_a_confirmed_omission_gets_one_more_final_writing_and_nothing_else():
    """Slice 4 (2026-10-11): after the answer left out a failure it was told, one more writing, once per request,
    a final synthesis only; beside the last word, at most MAX_CALLS + 2 entries (+1 for a forgiven look)."""
    api = admission_api()
    budget = api.RequestAdmission("run-1/request-1")
    provider = ProviderSpy()

    async def exercise():
        for label in ("0", "1", "2"):
            await call(budget, provider, label)
        last = budget.reserve_last_word()
        await call(budget, provider, "answer", phase="final", reservation=last)
        again = budget.reserve_recheck()
        assert again is not None
        with pytest.raises(api.AdmissionDenied) as refused:
            await call(budget, provider, "look-with-it", phase="look", reservation=again)
        assert refused.value.reason == "invalid-reservation"
        await call(budget, provider, "answer-again", phase="final", reservation=again)
        assert budget.reserve_recheck() is None  # once per request
        with pytest.raises(api.AdmissionDenied):
            await call(budget, provider, "beyond", phase="final", reservation=again)  # spent

    run(exercise())
    assert provider.sent == ["0", "1", "2", "answer", "answer-again"]


@pytest.mark.parametrize(
    "error", [RuntimeError("provider failed"), TimeoutError("provider timed out")],
    ids=["failure", "timeout"],
)
def test_a_failed_dispatched_attempt_is_not_refunded(error):
    api = admission_api()
    budget = api.RequestAdmission("run-1/request-1")
    provider = ProviderSpy()

    async def exercise():
        with pytest.raises(type(error)) as failure:
            await call(budget, provider, "failed", error=error)
        assert failure.value is error
        await call(budget, provider, "retry")
        await call(budget, provider, "final", phase="final")
        with pytest.raises(api.AdmissionDenied):
            await call(budget, provider, "fourth")

    run(exercise())
    assert provider.sent == ["failed", "retry", "final"]
    assert budget.used == 3
    assert budget.attempts[0].status == "failed"


def test_cancellation_before_dispatch_spends_nothing():
    api = admission_api()
    budget = api.RequestAdmission("run-1/request-1")
    provider = ProviderSpy()

    async def exercise():
        queued = asyncio.Event()
        ready = asyncio.Event()

        async def waiting_for_source():
            queued.set()
            await ready.wait()
            await call(budget, provider, "canceled")

        task = asyncio.create_task(waiting_for_source())
        await queued.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert budget.used == 0 and budget.remaining == 3
        assert budget.attempts == ()
        await call(budget, provider, "actual")

    run(exercise())
    assert provider.sent == ["actual"]


def test_cancellation_after_dispatch_consumes_the_attempt_and_propagates():
    api = admission_api()
    budget = api.RequestAdmission("run-1/request-1")
    provider = ProviderSpy()

    async def exercise():
        entered = asyncio.Event()
        release = asyncio.Event()
        task = asyncio.create_task(call(
            budget, provider, "canceled", entered=entered, release=release,
        ))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert budget.used == 1
        assert budget.attempts[0].status == "canceled"
        await call(budget, provider, "query")
        await call(budget, provider, "late", phase="late")
        with pytest.raises(api.AdmissionDenied):
            await call(budget, provider, "fourth")

    run(exercise())
    assert provider.sent == ["canceled", "query", "late"]


def test_same_attempt_replay_is_refused_but_an_explicit_retry_counts_again():
    api = admission_api()
    budget = api.RequestAdmission("run-1/request-1")
    provider = ProviderSpy()

    async def exercise():
        entered = asyncio.Event()
        release = asyncio.Event()
        original = asyncio.create_task(call(
            budget, provider, "look-1", phase="look", entered=entered, release=release,
        ))
        try:
            await entered.wait()
            with pytest.raises(api.AdmissionDenied) as replay:
                await call(budget, provider, "look-1", phase="look")
            assert replay.value.reason == "duplicate-attempt"
            assert budget.used == 1
        finally:
            release.set()
            await original
        with pytest.raises(api.AdmissionDenied):
            await call(budget, provider, "look-1", phase="look")
        await call(budget, provider, "look-2", phase="look")

    run(exercise())
    assert provider.sent == ["look-1", "look-2"]
    assert budget.used == 2


@pytest.mark.parametrize("phase", ["research", "specialist"])
def test_separately_budgeted_jobs_cannot_be_a_fourth_answer_dispatch(phase):
    api = admission_api()
    budget = api.RequestAdmission("run-1/request-1")
    provider = ProviderSpy()

    async def exercise():
        with pytest.raises(api.AdmissionDenied) as wrong_class:
            await call(budget, provider, "not-an-answer", phase=phase)
        assert wrong_class.value.reason == "invalid-phase"
        assert budget.used == 0
        for label in ("hands", "look", "query"):
            await call(budget, provider, label, phase=label)
        with pytest.raises(api.AdmissionDenied):
            await call(budget, provider, "fourth", phase=phase)

    run(exercise())
    assert provider.sent == ["hands", "look", "query"]
    assert budget.used == 3
