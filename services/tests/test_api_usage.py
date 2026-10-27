"""Synthetic billing evidence: no provider calls and no user data."""
import asyncio
from types import SimpleNamespace as NS

import pytest

from research_worker import api_usage as usage
from research_worker.budget import Budget, EFFORTS, OverBudget
from research_worker.researcher import Researcher


@pytest.fixture
def ledger(tmp_path):
    ledger = usage.UsageLedger(tmp_path)
    usage.configure(ledger)
    yield ledger
    usage.configure(None)
    ledger.close()


def today(ledger, research=0):
    return ledger.summary(usage.time.strftime('%Y-%m-%d'), research)


def test_tokens_thinking_unknown_and_history_are_not_double_counted(ledger):
    response = NS(usage_metadata=NS(prompt_token_count=1000, candidates_token_count=100, thoughts_token_count=200))
    usage.response('gemini-3.8-flash', response, provider='Google')
    usage.response('new-model', response, provider='Google')
    usage.record('veo-3.1-fast', .8, area='Research')
    report = today(ledger, research=1.05)
    assert report['estimatedUsd'] == pytest.approx(1.051875)
    assert report['unpriced'] == 1
    assert next(r for r in report['entries'] if r['provider'] == 'Earlier research')['estimatedUsd'] == .25
    # A second refresh never adds costs again.
    assert today(ledger, research=1.05) == report


def test_restarts_day_isolation_and_cumulative_voice_upsert(tmp_path):
    ledger = usage.UsageLedger(tmp_path)
    ledger.record('gpt-live-1', .05, key='voice-one', units=60, day='2026-10-10')
    ledger.record('gpt-live-1', .1, key='voice-one', units=120, day='2026-10-10')
    ledger.record('gpt-live-1', .2, key='voice-two', units=240, day='2026-10-11')
    started = ledger.summary('2026-10-10')['trackingSince']
    ledger.close()
    restored = usage.UsageLedger(tmp_path)
    assert restored.summary('2026-10-10')['estimatedUsd'] == .1
    assert restored.summary('2026-10-10')['trackingSince'] == started
    assert restored.summary('2026-10-11')['estimatedUsd'] == .2
    restored.close()


def test_independent_presentation_is_not_seeded_again_as_earlier_research(ledger):
    usage.record('gpt-6.1-sol', .08, area='Research', key='research-run')
    usage.record('claude-opus-5-5', .12, area='Presentations', key='presentation-job')
    report = today(ledger, research=.25)
    assert report['estimatedUsd'] == pytest.approx(.25)
    assert next(row for row in report['entries'] if row['provider'] == 'Earlier research')['estimatedUsd'] == .05
    usage.record('claude-opus-5-5', .15, area='Presentations', key='presentation-job')
    assert today(ledger, research=.28)['estimatedUsd'] == pytest.approx(.28)


def test_voice_usage_does_not_double_count_repeated_events(ledger, monkeypatch):
    now = [0]
    monkeypatch.setattr(usage.time, 'monotonic', lambda: now[0])
    voice = usage.VoiceUsage('gpt-live-1')
    voice.update(60)
    voice.update(60)
    voice.update(30)
    assert today(ledger)['estimatedUsd'] == .05
    now[0] = 90
    voice.finish()
    assert today(ledger)['estimatedUsd'] == .075
    assert today(ledger)['entries'][0]['events'] == 1


def test_cancellation_is_unknown_not_free(ledger):
    async def run():
        async with usage.attempt('gemini-3.8-flash'):
            raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())
    assert today(ledger)['unpriced'] == 1


def test_search_counts_queries_and_missing_usage_is_visible(ledger):
    response = NS(usage_metadata=NS(prompt_token_count=0, candidates_token_count=0, thoughts_token_count=0),
                  candidates=[NS(grounding_metadata=NS(web_search_queries=['one', 'two']))])
    usage.response('gemini-3.8-flash', response, provider='Google', search=True)
    assert today(ledger)['estimatedUsd'] == .028
    usage.response('gemini-3.8-flash', NS(), provider='Google', search=True)
    assert today(ledger)['unpriced'] == 2


def test_unlimited_removes_money_gate_but_keeps_round_time_and_image_bounds(tmp_path):
    r = Researcher(dict(rid='r1', dir=str(tmp_path), effort='standard', unlimited_budget=True), lambda _: None, lambda: None)
    r.budget.add('video', 100)
    r.budget.check(1000)
    assert 'no spending limit' in r.system_prompt()
    assert r.effort.seconds == EFFORTS['standard'].seconds
    assert r.effort.max_rounds == EFFORTS['standard'].max_rounds
    r.budget.images = r.effort.max_images
    assert not r.budget.image_allowed()
    limited = Budget(EFFORTS['standard'])
    with pytest.raises(OverBudget):
        limited.check(100)


def test_disk_error_is_visible_and_does_not_break_interaction(ledger, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('full')
    monkeypatch.setattr(ledger, 'record', fail)
    usage.record('gpt-6.1-sol', .1)
    assert today(ledger)['recordingErrors'] == 1


@pytest.mark.parametrize('cap,unlimited', [(None, True), (2.0, False)])
def test_manager_passes_unlimited_to_worker(tmp_path, cap, unlimited):
    from orchestrator.research.manager import ResearchManager
    from tests.test_research import FakeHub
    from tests.test_research_answer_kept import finished_with_a_question

    hub = FakeHub()
    hub.settings.research_cap = cap
    manager = ResearchManager(hub, root=tmp_path)
    session = finished_with_a_question(manager, status='running')
    dispatched = {}

    class Worker:
        async def call(self, op, **payload):
            dispatched.update(payload)

    async def ensure():
        return Worker()

    async def watchdog(*args):
        pass

    manager._ensure_worker, manager._watchdog = ensure, watchdog
    asyncio.run(manager._launch(session))
    assert dispatched['unlimited_budget'] is unlimited


def test_known_fast_pass_and_unknown_priority_prices_are_distinct(ledger):
    usage.response('gemini-3.5-flash', NS(usage_metadata=NS(prompt_token_count=1000, candidates_token_count=100)), provider='Google')
    usage.response('gpt-6-luna', NS(usage=NS(input_tokens=1000, output_tokens=100)), provider='OpenAI')
    usage.response('gpt-6-luna', NS(usage=NS(input_tokens=1000, output_tokens=100)), provider='OpenAI', tier='priority')
    report = today(ledger)
    assert report['estimatedUsd'] == pytest.approx(.00255)
    assert report['unpriced'] == 1


def test_late_research_charges_do_not_erase_retained_history(ledger):
    day = usage.time.strftime('%Y-%m-%d')
    ledger.seed_research(day, .27)
    ledger.record('gpt-6.1-sol', .1, area='Research')
    # The run timed out before its session total could be updated.
    assert today(ledger, research=.27)['estimatedUsd'] == .37
    assert today(ledger, research=.37)['estimatedUsd'] == .37


def test_interactions_usage_and_search_are_attributed(ledger):
    result = NS(usage=NS(total_input_tokens=1000, total_output_tokens=100,
                        total_thought_tokens=200), steps=[
        NS(type='google_search_call', arguments={'queries': ['one', 'two']}),
        NS(type='thought', text='must never be recorded')])
    usage.response('gemini-3.8-flash', result, provider='Google', search=True)
    assert today(ledger)['estimatedUsd'] == pytest.approx(.029875)
    assert today(ledger)['unpriced'] == 0


def test_unknown_settlement_keeps_enforcement_allowance_out_of_analytics_across_restart(tmp_path):
    ledger = usage.UsageLedger(tmp_path)
    ledger.set_cap(1)
    token = ledger.admit('gpt-example', 'OpenAI', 'Research', .10)
    ledger.settle(token, None, 'Interrupted before usage returned')
    row, = ledger.breakdown()
    assert row['estimatedUsd'] == 0 and row['unpriced'] == 1
    assert ledger.month()['estimatedUsd'] == .10 and ledger.month()['unknownCalls'] == 1
    assert ledger.month()['unknownAllowanceUsd'] == .10
    assert ledger.summary(usage.time.strftime('%Y-%m-%d'))['unpriced'] == 1
    ledger.close()
    reopened = usage.UsageLedger(tmp_path)
    row, = reopened.breakdown()
    assert row['estimatedUsd'] == 0 and row['unpriced'] == 1
    assert reopened.month()['estimatedUsd'] == .10
    # A later actual usage report replaces the allowance, including its unknown status.
    reopened.settle(token, .03, 'Provider usage at configured list prices')
    row, = reopened.breakdown()
    assert row['estimatedUsd'] == .03 and row['unpriced'] == 0
    assert reopened.month()['estimatedUsd'] == .03
    reopened.close()


def test_legacy_unknown_settlement_is_migrated_without_losing_its_limit_allowance(tmp_path):
    ledger = usage.UsageLedger(tmp_path)
    # The public ledger's persisted shape before allowance/charge separation.
    ledger.record('gpt-example', .20, key='old-call', basis='Interrupted or failed: counted at its most')
    ledger.set_cap(.25)
    ledger.close()
    reopened = usage.UsageLedger(tmp_path)
    row, = reopened.breakdown()
    assert row['estimatedUsd'] == 0 and row['unpriced'] == 1
    assert reopened.month()['estimatedUsd'] == .20
    with pytest.raises(usage.CapReached):
        reopened.admit('gpt-example', 'OpenAI', 'Live', .10)
    reopened.close()
