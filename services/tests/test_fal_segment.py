"""SAM 3.1 on fal (orchestrator/fal_segment.py; ticket 62 L12, F065, F066): each admitted call is sent once and never
retried underneath, never waits longer than its timeout, is priced at fal's per-call list price, and each way it can
fail is said for what it is, with what it cost: refused before it ran (nothing billed), or perhaps run (counted at its
most). A stand-in fal (httpx's MockTransport); no real call, no real key, no money."""
import asyncio

import httpx
import numpy as np
import pytest

from orchestrator import fal_segment
from orchestrator.brain.registry import ToolFailed
from research_worker import api_usage

JPEG = b"\xff\xd8 not decoded by the stand-in"
KEY = "fal-test-key-value"


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("FAL_KEY", KEY)
    one = api_usage.UsageLedger(tmp_path / "usage")
    api_usage.configure(one)
    yield one
    api_usage.configure(None)
    one.close()


def fal(monkeypatch, answer):
    """fal stands in: `answer(request)` is its response (or raises), every request kept."""
    asked = []

    def handler(request):
        asked.append(request)
        return answer(request)

    monkeypatch.setattr(fal_segment, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return asked


def box():
    return fal_segment.segment_box(JPEG, (2, 2, 6, 6), 8, 8)


def test_a_call_is_sent_once_with_the_key_in_force_now_and_tracked_at_its_list_price(ledger, monkeypatch):
    asked = fal(monkeypatch, lambda r: httpx.Response(200, json={"rle": "9 3", "scores": [0.9]}))
    out = asyncio.run(box())
    assert out["score"] == 0.9 and out["mask"].sum() == 3
    [request] = asked
    assert str(request.url) == "https://fal.run/fal-ai/sam-3-1/image-rle"
    assert request.headers["authorization"] == f"Key {KEY}"
    [row] = ledger.breakdown()
    assert (row["provider"], row["events"], row["estimatedUsd"], row["unpriced"]) == ("fal", 1, pytest.approx(0.01), 0)


MAY_HAVE_RUN = [
    (lambda r: httpx.Response(503, json={"detail": "busy"}), "server"),
    (lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=r)), "cut-off"),
    (lambda r: (_ for _ in ()).throw(httpx.RemoteProtocolError("dropped", request=r)), "failed"),
]


@pytest.mark.parametrize("answer, outcome", MAY_HAVE_RUN)
@pytest.mark.parametrize("cap, counted", [(1.0, "$0.01"), (None, "$0.02")])
def test_a_call_that_may_have_run_is_never_sent_again_and_is_said_at_what_the_ledger_counts(ledger, monkeypatch, answer,
                                                                                          outcome, cap, counted):
    """fal_client.run retried these up to ten times: a call fal had accepted could run and be billed again while the
    ledger counted one (F065). Sent once; said as perhaps run, at what the ledger counts it: its most ($0.01) under a
    monthly limit, else the ledger's allowance for an unknown cost ($0.02, api_usage.UNKNOWN_USD)."""
    if cap is not None:
        ledger.set_cap(cap)
    asked = fal(monkeypatch, answer)
    with pytest.raises(fal_segment.Unconfirmed) as failed:
        asyncio.run(box())
    assert len(asked) == 1, "never retried underneath"
    assert "may have run" in str(failed.value) and counted in str(failed.value) and KEY not in str(failed.value)
    assert api_usage.outcome_of(failed.value) == outcome
    assert ledger.month()["estimatedUsd"] == pytest.approx(float(counted[1:])) and ledger.month()["unknownCalls"] == 1


@pytest.mark.parametrize("answer, outcome", MAY_HAVE_RUN)
def test_the_runner_records_a_call_that_may_have_run_as_failed_never_as_refused_before_running(ledger, monkeypatch,
                                                                                               answer, outcome):
    """operations.not_run_of says "refused" (never ran) for any ToolFailed: a timeout, a dropped connection or a 5xx is
    not one, so its execution record says it ran and failed, as its cost row says it may have been billed. Its words
    are said as they are, not rewritten as a raw error (operations.RAW_ERROR)."""
    from orchestrator import operations

    fal(monkeypatch, answer)
    with pytest.raises(fal_segment.Unconfirmed) as failed:
        asyncio.run(box())
    assert not isinstance(failed.value, ToolFailed) and operations.not_run_of(failed.value) == ""
    said = f"{type(failed.value).__name__}: {failed.value}"  # as operations._attempt keeps a raised error's words
    assert not operations.RAW_ERROR.match(said) and "may have run" in said


def test_a_call_refused_before_it_ran_is_recorded_as_never_run(ledger, monkeypatch):
    from orchestrator import operations

    fal(monkeypatch, lambda r: httpx.Response(401, json={"detail": "No user found for Key ID and Secret"}))
    with pytest.raises(fal_segment.FalRefused) as failed:
        asyncio.run(box())
    assert isinstance(failed.value, ToolFailed) and operations.not_run_of(failed.value) == "refused"


@pytest.mark.parametrize("status, words", [(401, "refused your key"), (403, "refused your key"),
                                           (429, "out of credit")])
def test_a_call_refused_before_it_ran_is_said_so_and_costs_nothing(ledger, monkeypatch, status, words):
    ledger.set_cap(1.0)
    asked = fal(monkeypatch, lambda r: httpx.Response(status, json={"detail": "No user found for Key ID and Secret"}))
    with pytest.raises(ToolFailed) as failed:
        asyncio.run(box())
    assert len(asked) == 1 and words in str(failed.value) and "nothing was billed" in str(failed.value)
    assert f"HTTP {status}" in str(failed.value) and KEY not in failed.value.said()
    assert ledger.month()["estimatedUsd"] == 0 and ledger.month()["unknownCalls"] == 0
    [row] = ledger.breakdown()
    assert row["estimatedUsd"] == 0 and "nothing billed" in row["basis"]


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ConnectTimeout])
def test_no_connection_means_nothing_was_sent(ledger, monkeypatch, error):
    """A connection never made (refused, or not made in time) sent nothing: not run, nothing billed."""
    ledger.set_cap(1.0)
    fal(monkeypatch, lambda r: (_ for _ in ()).throw(error("no route", request=r)))
    with pytest.raises(fal_segment.FalRefused, match="nothing was sent") as failed:
        asyncio.run(box())
    assert failed.value.ran is False and "may have run" not in str(failed.value)
    assert ledger.month()["estimatedUsd"] == 0 and ledger.month()["unknownCalls"] == 0


def test_under_a_limit_it_is_asked_at_its_list_price_and_refused_before_sending_when_that_doesnt_fit(ledger, monkeypatch):
    ledger.set_cap(0.015)
    asked = fal(monkeypatch, lambda r: httpx.Response(200, json={"rle": [], "scores": []}))
    asyncio.run(box())
    with pytest.raises(api_usage.CapReached, match=r"up to \$0\.01"):
        asyncio.run(box())
    assert len(asked) == 1, "the refused one was never sent"


def test_every_match_is_sent_once_and_tracked_too(ledger, monkeypatch):
    asked = fal(monkeypatch, lambda r: httpx.Response(200, json={"rle": ["0 2", "4 2"], "scores": [0.8, 0.3]}))
    found = asyncio.run(fal_segment.segment_all(JPEG, 4, 2))
    assert len(asked) == 1 and len(found) == 1 and isinstance(found[0]["mask"], np.ndarray)
    assert ledger.breakdown()[0]["estimatedUsd"] == pytest.approx(0.01)


def test_the_price_its_card_says_is_the_one_the_ledger_charges_with_where_and_when_it_was_read():
    """catalog/services/fal.toml's `costs` (what the card says) and api_usage.CALL_PRICES (what the ledger charges) name
    the same price and the same day it was read: a price changed in one place only, or gone stale, is caught here."""
    from gpu_worker import manifests

    usd, source, read = api_usage.CALL_PRICES[fal_segment.ENDPOINT]
    said = manifests.services()["fal"].costs.split(";")[0]
    assert f"${usd:.2f}" in said and read in said and "pricing API" in said and "pricing API" in source
    assert api_usage.call_basis(fal_segment.ENDPOINT) == f"List price per call ({source}, read {read}) · before discounts"
