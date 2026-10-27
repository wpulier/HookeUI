"""/api/spending as the studio reads it (Standards review of 64f49d1b, P3-2): today's and this month's tracked rows come
from one breakdown, and each period's total is its rows' own sum, never counting a hold for work in progress or the
allowance an unknown cost is counted at under a limit (those stay in the month's counted balance)."""
import pytest
from fastapi.testclient import TestClient

from research_worker import api_usage
from tests.test_resources import app_at

PRICED = "gemini-3.8-flash"


@pytest.fixture
def web(tmp_path):
    app = app_at(tmp_path / "app")
    ledger = api_usage.UsageLedger(tmp_path / "usage")
    app.state.usage = ledger
    api_usage.configure(ledger)
    yield app, ledger
    api_usage.configure(None)
    ledger.close()


def test_each_periods_total_is_the_sum_of_its_rows(web):
    app, ledger = web
    ledger.set_cap(20.0)
    ledger.record(PRICED, 0.4, provider="Google", area="Chat", basis="Reported tokens", key="a")
    ledger.record(PRICED, None, provider="Google", area="Chat", basis="Usage or price unavailable", key="b")
    ledger.admit(PRICED, "Google", "Live", 0.3)  # a call in flight: held for the limit, not tracked spend
    body = TestClient(app).get("/api/spending").json()
    rows = [e for e in body["entries"] if e["provider"] != "Earlier research"]
    assert body["estimatedUsd"] == pytest.approx(sum(e["estimatedUsd"] for e in body["entries"]))
    assert body["unpriced"] == sum(e["unpriced"] for e in body["entries"]) == 1
    assert sum(e["estimatedUsd"] for e in rows) == pytest.approx(0.4), "the hold is not tracked spend"
    month = body["month"]
    assert sum(e["estimatedUsd"] for e in month["entries"]) == pytest.approx(0.4)
    assert month["estimatedUsd"] > 0.4 and month["cap"] == 20.0, "the limit still counts the hold"
