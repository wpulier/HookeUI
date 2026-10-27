"""The optional monthly cap on cloud models (research_worker/api_usage.py; C7, with Codex's review of b1603d91): none by
default. Set, a call is admitted only at its own most (its price and token limits; one whose price is unknown isn't asked),
its row written before it is sent and settled by its real charge; concurrent calls never spend the same allowance; a call
whose usage never returns stays counted at its most, across restarts; voice is admitted for its first minutes and ended at
the cap; a researcher's run is refused when its most doesn't fit and held against the month while it runs. No real money."""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from orchestrator.research.ledger import DayLedger
from orchestrator.research.policy import ResearchRefused
from research_worker import api_usage
from tests.waiting import HANG_S

PRICED = "gemini-3.8-flash"


@pytest.fixture
def ledger(tmp_path):
    one = api_usage.UsageLedger(tmp_path / "usage")
    api_usage.configure(one)
    yield one
    api_usage.configure(None)
    one.close()


def test_no_cap_by_default_admits_every_call_and_reserves_nothing(ledger):
    assert ledger.cap() is None and ledger.admit(PRICED, "Google", "Live", 1.0) is None
    assert ledger.month() == {**ledger.month(), "estimatedUsd": 0, "cap": None}


def test_a_call_is_bounded_by_its_own_price_and_one_without_a_price_isnt_asked(ledger):
    most = api_usage.bound(PRICED, 10_000, 1_000)
    assert most == pytest.approx((10_000 * 0.75 + 1_000 * 3.75) / 1e6)
    assert api_usage.bound("no-such-model") is None
    ledger.set_cap(5.0)
    with pytest.raises(api_usage.CapReached, match="price isn't known"):
        ledger.admit("fal-ai/sam-3-1", "fal", "Segmentation", api_usage.bound("fal-ai/sam-3-1"))


def test_a_call_billed_per_call_is_bounded_by_its_list_price_admitted_under_a_cap_and_tracked(ledger):
    """fal bills SAM 3.1's image-rle endpoint per call (its pricing API: $0.01, read 2026-10-20), so under a limit it is
    asked at that most and its charge is tracked, never "price unknown"; fal-ai/sam-3-1 itself, billed per compute
    second with no limit the request can set, stays unbounded (above)."""
    endpoint = "fal-ai/sam-3-1/image-rle"
    assert api_usage.call_price(endpoint) == api_usage.bound(endpoint) == pytest.approx(0.01)
    assert api_usage.call_price("fal-ai/sam-3-1") is None and api_usage.call_price(PRICED) is None
    ledger.set_cap(0.025)

    async def one():
        async with api_usage.attempt(endpoint, provider="fal", area="Segmentation"):
            pass
        api_usage.record(endpoint, api_usage.call_price(endpoint), provider="fal", area="Segmentation",
                         basis=api_usage.call_basis(endpoint))

    asyncio.run(one())
    asyncio.run(one())
    with pytest.raises(api_usage.CapReached, match=r"up to \$0\.01"):
        asyncio.run(one())
    assert ledger.month()["estimatedUsd"] == pytest.approx(0.02) and ledger.month()["inFlightUsd"] == 0
    [row] = ledger.breakdown()
    assert (row["provider"], row["events"], row["estimatedUsd"], row["unpriced"]) == ("fal", 2, pytest.approx(0.02), 0)
    assert row["basis"] == "List price per call (fal's pricing API, read 2026-10-20) · before discounts", \
        "where the price came from and when, so a stale price is visible"


def test_a_call_its_caller_knows_was_refused_before_it_ran_costs_nothing_and_one_that_may_have_run_stays_at_its_most(ledger):
    """F065/F066: a caller that knows what happened says so on its exception (`outcome`, `ran`); a refusal before
    running (a key refused, a rate limit, never sent) is $0 with nothing held, anything else unknown at its most."""
    endpoint = "fal-ai/sam-3-1/image-rle"
    ledger.set_cap(1.0)

    class Said(RuntimeError):
        def __init__(self, outcome, ran):
            super().__init__("said")
            self.outcome, self.ran = outcome, ran

    async def failing(exc):
        async with api_usage.attempt(endpoint, provider="fal", area="Segmentation"):
            raise exc

    for exc in (Said("auth", False), Said("rate-limit", False), Said("connection", False)):
        with pytest.raises(Said):
            asyncio.run(failing(exc))
    assert ledger.month()["estimatedUsd"] == 0 and ledger.month()["unknownCalls"] == 0
    [row] = ledger.breakdown()
    assert row["events"] == 3 and row["estimatedUsd"] == 0 and row["unpriced"] == 0
    assert "Refused before it ran: key, permission or billing refused (HTTP 401/403); nothing billed" in row["basis"]
    with pytest.raises(Said):
        asyncio.run(failing(Said("cut-off", None)))
    assert api_usage.outcome_of(Said("cut-off", None)) == "cut-off"
    assert ledger.month()["estimatedUsd"] == pytest.approx(0.01) and ledger.month()["unknownCalls"] == 1


def test_calls_in_flight_never_spend_the_same_remaining_allowance(ledger):
    ledger.set_cap(0.105)  # room for five calls of up to $0.02, not six
    admitted, refused = [], []
    start = threading.Barrier(10)

    def call():
        start.wait()
        try:
            admitted.append(ledger.admit(PRICED, "Google", "Live", 0.02))
        except api_usage.CapReached:
            refused.append(1)

    threads = [threading.Thread(target=call) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(admitted) == 5 and len(refused) == 5
    for token in admitted:
        ledger.settle(token, 0.001, "Reported tokens")
    assert ledger.month()["estimatedUsd"] == pytest.approx(0.005), "settled to what they really cost"
    assert ledger.admit(PRICED, "Google", "Live", 0.02) is not None, "what was settled below its most is allowance again"


def test_usage_that_never_returns_stays_counted_at_its_most_across_a_restart(ledger, tmp_path):
    ledger.set_cap(1.0)
    token = ledger.admit(PRICED, "Google", "Live", 0.3)
    ledger.settle(token, None, "Interrupted or failed")
    again = api_usage.UsageLedger(tmp_path / "usage")
    try:
        assert again.cap() == 1.0 and again.month()["estimatedUsd"] == pytest.approx(0.3)
        assert again.month()["inFlightUsd"] == 0, "settled, its charge unknown: counted at its most, not in flight"
    finally:
        again.close()
    ledger.admit(PRICED, "Google", "Live", 0.4)  # never settled: in flight at its most until it is
    restarted = api_usage.UsageLedger(tmp_path / "usage")
    try:
        assert restarted.month()["estimatedUsd"] == pytest.approx(0.7) and restarted.month()["inFlightUsd"] == pytest.approx(0.4)
    finally:
        restarted.close()


def test_a_call_is_settled_by_the_usage_its_caller_records_and_a_refused_one_is_never_sent(ledger):
    ledger.set_cap(1.0)
    sent = []

    async def ask(most, usd, fail=False):
        async with api_usage.attempt(PRICED, provider="Google", most=most):
            sent.append(most)
            if fail:
                raise TimeoutError("cut off")
        api_usage.record(PRICED, usd, provider="Google", area="Live", basis="Reported tokens")

    asyncio.run(ask(0.5, 0.01))
    assert ledger.month()["estimatedUsd"] == pytest.approx(0.01), "its row settled to its real charge"
    with pytest.raises(TimeoutError):
        asyncio.run(ask(0.2, 0.0, fail=True))
    assert ledger.month()["estimatedUsd"] == pytest.approx(0.21), "a call cut off stays counted at its most"
    with pytest.raises(api_usage.CapReached):
        asyncio.run(ask(0.9, 0.0))
    assert sent == [0.5, 0.2], "the refused call was never sent"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_voice_holds_its_minutes_ahead_by_the_clock_even_with_no_usage_and_ends_before_they_dont_fit(ledger):
    """Codex's review of d1252d67: the allowance is renewed on time, not only when usage arrives, and the session is
    counted by the time it has been open (sent but unreported audio is never free)."""
    ahead = api_usage.VOICE_BOUND_MIN * api_usage.VOICE_USD_PER_MIN
    ledger.set_cap(ahead / 2)
    with pytest.raises(api_usage.CapReached):
        api_usage.VoiceUsage("gpt-live-1", clock=Clock())  # its first minutes don't fit: refused before it opens
    assert ledger.month()["inFlightUsd"] == 0, "a refused session holds nothing"
    ledger.set_cap(1.0)
    clock = Clock()
    voice = api_usage.VoiceUsage("gpt-live-1", clock=clock)
    assert ledger.month()["inFlightUsd"] == pytest.approx(ahead)
    clock.now += 60 * 10  # ten minutes, and no usage event at all
    assert voice.renew()
    assert ledger.month()["estimatedUsd"] == pytest.approx(0.5 + ahead), "its open time counted, its minutes held again"
    clock.now += 60 * 8
    assert voice.renew(), "$0.90 open and $0.10 ahead: exactly the cap"
    clock.now += 60
    assert not voice.renew(), "the next minutes no longer fit: the route ends the session"
    voice.finish()
    assert ledger.month()["inFlightUsd"] == 0, "ended: nothing held"


def test_admission_is_one_authority_across_connections_and_reads_the_cap_now(ledger, tmp_path):
    other = api_usage.UsageLedger(tmp_path / "usage")  # another connection to the same ledger (another process's)
    try:
        other.set_cap(0.105)
        admitted, refused = [], []
        start = threading.Barrier(10)

        def call(one):
            start.wait()
            try:
                admitted.append(one.admit(PRICED, "Google", "Live", 0.02))
            except api_usage.CapReached:
                refused.append(1)

        threads = [threading.Thread(target=call, args=(ledger if i % 2 else other,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(admitted) == 5 and len(refused) == 5, "two connections never spend the same allowance"
        other.set_cap(0.0)
        with pytest.raises(api_usage.CapReached):
            ledger.admit(PRICED, "Google", "Live", 0.001)  # the cap lowered elsewhere holds at once
    finally:
        other.close()


def _local(stamp: str) -> float:
    import time

    return time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))


def _rows(ledger, like: str) -> dict:
    with ledger.lock:
        return dict(ledger.db.execute("SELECT id, usd FROM charges WHERE id LIKE ?", (like,)).fetchall())


def test_a_voice_session_across_a_month_counts_its_seconds_on_each_side_of_the_boundary(ledger):
    """Codex's diagnosis of 89e834b5, point 5: 30 s before midnight of a month's end and 30 s after, renewed only after
    it, count $0.025 in each month (its own rate), the new month's allowance held, the old month's released."""
    ledger.set_cap(5.0)
    clock, wall = Clock(), SimpleNamespace(now=_local("2026-10-31 23:59:30"))
    voice = api_usage.VoiceUsage("gpt-live-1", clock=clock, wall=lambda: wall.now)
    old_hold = voice.hold
    clock.now += 60
    wall.now += 60  # 00:00:30 on the first of the month, with no observation at the boundary
    assert voice.renew()
    assert voice.month == "2026-11" and voice.month_from == pytest.approx(30)
    assert _rows(ledger, f"{voice.key}:2026-10")[f"{voice.key}:2026-10"] == pytest.approx(0.025)
    assert _rows(ledger, f"{voice.key}:2026-11")[f"{voice.key}:2026-11"] == pytest.approx(0.025)
    assert _rows(ledger, old_hold)[old_hold] == 0, "last month's minutes ahead released"
    assert ledger.month("2026-11")["inFlightUsd"] == pytest.approx(api_usage.VOICE_BOUND_MIN * api_usage.VOICE_USD_PER_MIN)
    voice.finish()
    assert ledger.month("2026-11")["inFlightUsd"] == 0 and ledger.month("2026-10")["estimatedUsd"] == pytest.approx(0.025)


def test_within_its_minutes_ahead_of_a_months_end_the_next_months_are_held_before_it_turns(ledger):
    ledger.set_cap(5.0)
    clock, wall = Clock(), SimpleNamespace(now=_local("2026-10-31 23:58:30"))
    voice = api_usage.VoiceUsage("gpt-live-1", clock=clock, wall=lambda: wall.now)
    ahead = api_usage.VOICE_BOUND_MIN * api_usage.VOICE_USD_PER_MIN
    assert ledger.month("2026-11")["inFlightUsd"] == pytest.approx(ahead), "held before November began"
    clock.now += 60
    wall.now += 60  # 23:59:30, still October
    assert voice.renew() and ledger.month("2026-11")["inFlightUsd"] == pytest.approx(ahead)
    voice.finish()
    assert ledger.month("2026-11")["inFlightUsd"] == 0 and ledger.month("2026-10")["inFlightUsd"] == 0


def test_finish_is_terminal_and_a_renewal_already_running_never_restores_its_hold(ledger):
    """Codex's diagnosis of 89e834b5, point 6: a renewal paused inside its ledger write while the session finishes."""
    ledger.set_cap(5.0)
    voice = api_usage.VoiceUsage("gpt-live-1", clock=Clock())
    inside, go = threading.Event(), threading.Event()
    real = ledger.renew

    def paused(*args, **kwargs):
        inside.set()
        go.wait(5)
        return real(*args, **kwargs)

    ledger.renew = paused
    renewing = threading.Thread(target=voice.renew)
    renewing.start()
    assert inside.wait(HANG_S)
    finishing = threading.Thread(target=voice.finish)
    finishing.start()
    go.set()
    renewing.join(HANG_S)
    finishing.join(HANG_S)
    ledger.renew = real
    assert ledger.month()["inFlightUsd"] == 0, "the finish came after the renewal it waited for"
    assert not voice.renew() and ledger.month()["inFlightUsd"] == 0, "nothing renews a finished session"
    voice.update(120.0)
    assert ledger.month()["inFlightUsd"] == 0


def test_a_researchers_later_run_in_a_new_month_is_counted_in_that_month(ledger, tmp_path, monkeypatch):
    """Codex's diagnosis of 89e834b5, point 1: the same researcher held and released in October, then held again in
    November, is November's $0.25, so a second $0.25 doesn't fit a $0.25 cap."""
    import time as clock_module

    real = clock_module.strftime
    month = {"now": "2026-10-20"}

    def strftime(fmt, t=None):
        return real(fmt, t if t is not None else clock_module.strptime(month["now"], "%Y-%m-%d"))

    monkeypatch.setattr(clock_module, "strftime", strftime)
    days = DayLedger(tmp_path / "research", lambda: month["now"], lambda: [])
    ledger.set_cap(0.25)
    run = SimpleNamespace(rid="r1", spent=0.0, spent_estimated=0.0)
    days.hold(run, 0.25)
    days.release("r1")
    month["now"] = "2026-11-03"
    days.hold(run, 0.25)
    assert ledger.month("2026-11")["inFlightUsd"] == pytest.approx(0.25)
    with pytest.raises(api_usage.CapReached):
        ledger.admit(PRICED, "Google", "Live", 0.25)
    days.release("r1")
    assert ledger.month("2026-11")["estimatedUsd"] == pytest.approx(0.0)


def test_a_search_that_cant_be_bounded_isnt_asked_under_a_cap(ledger):
    async def ask():
        async with api_usage.attempt(PRICED, provider="Google", area="Web lookup", out_tokens=1000, in_tokens=2000,
                                     unbounded="its searches are billed per query"):
            pass

    asyncio.run(ask())  # no cap: asked as before
    ledger.set_cap(5.0)
    with pytest.raises(api_usage.CapReached, match="billed per query"):
        asyncio.run(ask())


def test_a_bound_counts_thinking_inside_the_output_limit_the_provider_enforces(ledger):
    price = api_usage.PRICES[PRICED]
    assert api_usage.bound(PRICED, 1000, 2000) == pytest.approx((1000 * price["in"] + 2000 * price["out"]) / 1e6)


def test_a_monthly_cap_alone_bounds_each_research_run_to_its_reserved_most(ledger):
    from orchestrator.research.manager import _month_capped

    assert not _month_capped()
    ledger.set_cap(5.0)
    assert _month_capped(), "with only a monthly cap, a run is dispatched with its effort's budget, never unlimited"


def test_a_researchers_run_is_refused_when_its_most_doesnt_fit_and_held_while_it_runs(ledger, tmp_path):
    days = DayLedger(tmp_path / "research", lambda: "2026-10-13", lambda: [])
    ledger.set_cap(1.0)
    with pytest.raises(ResearchRefused, match="monthly cap"):
        days.check(None, 5.0)  # a deep run's most
    days.check(None, 0.25)
    run = SimpleNamespace(rid="r1", spent=0.0, spent_estimated=0.0)
    days.hold(run, 0.25)
    assert ledger.month()["inFlightUsd"] == pytest.approx(0.25)
    days.release("r1")
    assert ledger.month()["estimatedUsd"] == pytest.approx(0.0), "its own charges count instead, as they came"


@pytest.mark.usefixtures('research_key')  # starts a real-model researcher, which needs its model's key
def test_a_research_run_is_really_dispatched_with_a_finite_budget_under_a_monthly_cap_alone(ledger, tmp_path):
    """Not only the helper (Codex's review of d1252d67): what the research process is sent to start."""
    from tests.capability_rig import Rig

    rig = Rig(tmp_path / "rig")
    assert rig.hub.settings.research_cap is None
    ledger.set_cap(5.0)
    asyncio.run(rig.turn([("start_researcher", {"brief": "what is this stoma", "effort": "quick",
                                                 "deliverable": "research"})], "look into what this is"))
    sent = [p for p in rig.research.spawned]
    assert sent and sent[-1].get("unlimited_budget") is False, sent[-1:] or "nothing was dispatched"
    ledger.set_cap(None)
    asyncio.run(rig.turn([("start_researcher", {"brief": "what is this guard cell", "effort": "quick",
                                                 "deliverable": "research"})], "look into that too"))
    assert rig.research.spawned[-1].get("unlimited_budget") is True, "no cap of any kind: as before"


# --- a limited research run: every actual attempt bounded to what is left (Codex's diagnosis of 89e834b5, point 2) --
def _limited(tmp_path, cap: float, spent: float = 0.0, tools=frozenset({"web_search", "record_finding", "finish"})):
    from research_worker.budget import Effort
    from tests.test_research_content import researcher

    r, events = researcher(tmp_path, {"effort": "quick"}, Effort("t", 120, cap, "big", tools, 8, "low"))
    r.budget.spent = spent
    assert not r.budget.unlimited
    return r, events


def test_a_limited_run_with_nothing_left_sends_no_write_up_and_keeps_what_it_found(tmp_path, monkeypatch):
    from research_worker.engines import SolEngine
    from tests.test_research_engines import fake_sol

    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        raise AssertionError("no call fits: none is sent")

    fake_sol(monkeypatch, create)
    r, events = _limited(tmp_path, 0.25, spent=0.24)
    r.recorded.append({"text": "pores open in light", "source": None})
    asyncio.run(r.run(SolEngine()))
    assert sent == [], "neither a round nor its write-up was sent"
    assert events[-1]["status"] == "partial" and r.result is not None, "what it found is kept"


def test_each_attempt_is_sized_to_what_is_left_and_a_retry_that_no_longer_fits_is_never_sent(tmp_path, monkeypatch):
    import httpx
    import openai

    from research_worker import engines
    from research_worker.engines import SolEngine
    from tests.test_research_engines import fake_sol, status_error

    monkeypatch.setattr(engines, "RETRY_WAIT_S", (0.0, 0.0))
    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        raise status_error(openai.InternalServerError, 503)

    fake_sol(monkeypatch, create)
    r, events = _limited(tmp_path, 0.6)
    asyncio.run(r.run(SolEngine()))
    assert len(sent) == 1, "the failed attempt may have been billed at its most: a second doesn't fit"
    first = sent[0]
    assert 0 < first["max_output_tokens"] <= engines.MAX_OUTPUT_TOKENS
    assert all(t.get("type") != "web_search" for t in first["tools"]), "search can't be bounded: not offered"
    assert first["reasoning"] == {"effort": "low"}, "its thinking level is never lowered"
    assert r.committed() <= 0.6 + 1e-9 and events[-1]["status"] in ("over_budget", "partial", "failed")


def test_a_limited_gemini_run_is_not_offered_search_and_its_output_is_bounded(tmp_path, monkeypatch):
    from research_worker.engines import GeminiEngine
    from tests.test_research_engines import FINISH, fake_gemini, gemini_reply

    configs = []

    async def generate(model, contents, config):
        configs.append(config)
        return gemini_reply(("finish", FINISH))

    fake_gemini(monkeypatch, generate)
    r, events = _limited(tmp_path, 1.0)
    asyncio.run(r.run(GeminiEngine()))
    assert configs and configs[0].max_output_tokens and all(not getattr(t, "google_search", None) for t in configs[0].tools)
    assert any("Web search isn't offered within a budget limit" in e.get("text", "") for e in events if e["event"] == "step")


def test_a_limited_runs_failed_attempt_stays_counted_at_its_most_through_its_settlement(tmp_path, monkeypatch):
    """Codex's recheck of 73626a0d: what a failed attempt may have cost is the run's spend as reported to the app (its
    cost events and its end), never only an internal reserve the release forgets."""
    import openai

    from research_worker import engines
    from research_worker.engines import SolEngine
    from tests.test_research_engines import fake_sol, status_error

    monkeypatch.setattr(engines, "RETRY_WAIT_S", (0.0, 0.0))

    async def create(**kwargs):
        raise status_error(openai.InternalServerError, 503)

    fake_sol(monkeypatch, create)
    r, events = _limited(tmp_path, 0.6)
    asyncio.run(r.run(SolEngine()))
    uncertain = [e["charge"] for e in events if e["event"] == "cost" and (e.get("charge") or {}).get("uncertain")]
    assert len(uncertain) == 1 and uncertain[0]["usd"] > 0.3, "counted at its most, not a small estimate"
    assert events[-1]["spent_estimated"] == pytest.approx(uncertain[0]["usd"], abs=1e-4)
    assert r.committed() <= 0.6 + 1e-9


def test_a_limited_runs_edit_and_check_calls_are_sent_with_an_output_limit_and_counted_at_most_when_they_fail(
        tmp_path, monkeypatch):
    import openai
    from google import genai

    from research_worker import researcher as researcher_module

    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        raise ConnectionError("lost")

    monkeypatch.setattr(openai, "AsyncOpenAI", lambda *a, **k: SimpleNamespace(responses=SimpleNamespace(create=create)))
    configs = []

    async def generate(model, contents, config):
        configs.append(config)
        raise ConnectionError("lost")

    monkeypatch.setattr(genai, "Client", lambda *a, **k: SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(
        generate_content=generate))))
    r, events = _limited(tmp_path, 1.0)
    with pytest.raises(ConnectionError):
        asyncio.run(researcher_module.edit_json("relabel it", r))
    with pytest.raises(ConnectionError):
        asyncio.run(researcher_module.fast_json("check it", r))
    assert 0 < sent[0]["max_output_tokens"] <= researcher_module.EDIT_MAX_OUTPUT
    assert 0 < configs[0].max_output_tokens <= researcher_module.CHECK_MAX_OUTPUT
    charged = [e["charge"]["usd"] for e in events if e["event"] == "cost" and e.get("charge")]
    assert len(charged) == 2 and all(c > 0.001 for c in charged), "each failed call counted at its most"
    r2, _ = _limited(tmp_path / "late", 1.0, spent=0.999)
    with pytest.raises(researcher_module.OverBudget):
        asyncio.run(researcher_module.edit_json("relabel it", r2))
    assert len(sent) == 1, "nothing fits: not sent"


def test_the_spending_menus_month_lists_what_was_tracked_by_provider_and_holds_apart(ledger):
    ledger.set_cap(5.0)
    ledger.record(PRICED, 0.4, provider="Google", area="Chat", basis="Reported tokens", key="a")
    ledger.record(PRICED, None, provider="Google", area="Chat", basis="Usage or price unavailable", key="b")
    token = ledger.admit(PRICED, "Google", "Live", 0.3)  # a call still in flight: held, not tracked spend
    voice = api_usage.VoiceUsage("gpt-live-1", clock=Clock())
    rows = ledger.breakdown()
    assert [(r["provider"], r["area"], r["events"], r["unpriced"]) for r in rows] == [("Google", "Chat", 2, 1),
                                                                                     ("OpenAI", "Voice", 1, 0)]
    assert rows[0]["estimatedUsd"] == pytest.approx(0.4)
    import time as clock_module

    assert ledger.breakdown(clock_module.strftime("%Y-%m-%d")) == rows, "today is read the same way as the month"
    assert sum(r["estimatedUsd"] for r in rows) == pytest.approx(0.4), "holds and unknown allowances are never in it"
    assert ledger.month()["estimatedUsd"] > 0.4, "while the limit counts them"
    assert ledger.month()["inFlightUsd"] == pytest.approx(0.3 + api_usage.VOICE_BOUND_MIN * api_usage.VOICE_USD_PER_MIN)
    ledger.settle(token, 0.1, "Reported tokens")
    voice.finish()
    assert {r["area"] for r in ledger.breakdown()} == {"Chat", "Live", "Voice"}


def test_one_admitted_attempt_is_never_retried_by_the_sdk_underneath(tmp_path, monkeypatch):
    """The C7 reviewer's finding: an AsyncOpenAI() made with its default two retries could spend one admitted most three
    times. Every research helper call's client is made with retries off."""
    import openai
    from google import genai

    from research_worker import researcher as researcher_module

    made = []

    def client(*args, **kwargs):
        made.append(("openai", kwargs))

        async def create(**_):
            raise ConnectionError("lost")
        return SimpleNamespace(responses=SimpleNamespace(create=create))

    def gclient(*args, **kwargs):
        made.append(("gemini", kwargs))

        async def generate(**_):
            raise ConnectionError("lost")
        return SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))

    monkeypatch.setattr(openai, "AsyncOpenAI", client)
    monkeypatch.setattr(genai, "Client", gclient)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r, _ = _limited(tmp_path, 1.0)
    for call in (lambda: researcher_module.edit_json("x", r), lambda: researcher_module.fast_json("x", r),
                 lambda: researcher_module.write_lesson("x", r, out=4000)):
        with pytest.raises(ConnectionError):
            asyncio.run(call())
    assert [m[0] for m in made] == ["openai", "gemini", "openai"]
    assert made[0][1].get("max_retries") == 0 and made[2][1].get("max_retries") == 0
    assert made[1][1]["http_options"]["retry_options"]["attempts"] == 1
