"""Synthetic provider/process boundaries; no model, provider, installer or live app runs."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from orchestrator import commands
from orchestrator.research.manager import ResearchManager
from research_worker.budget import OverBudget
from tests.test_research import FakeHub
from tests.test_research_answer_kept import finished_with_a_question
from tests.test_research_content import researcher
from tests.test_usage_cap import ledger
from tests.waiting import HANG_S, until_async


def test_builder_is_bounded_even_when_ordinary_research_is_unlimited(tmp_path):
    r, _ = researcher(tmp_path, {'effort': 'build', 'unlimited_budget': True})
    with pytest.raises(OverBudget):
        r.budget.check(2.01)


def test_builder_deadline_stops_its_engine_and_retains_a_terminal_receipt(tmp_path):
    r, events = researcher(tmp_path, {'effort': 'build'})
    r.effort = replace(r.effort, seconds=.01)
    stopped = []

    class Engine:
        async def run(self, owner):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.append(True)

    asyncio.run(asyncio.wait_for(r.run(Engine()), .2))
    assert stopped and events[-1]['event'] == 'done'
    assert events[-1]['status'] == 'timeout'


def test_cancellation_keeps_findings_without_starting_more_paid_checks(tmp_path):
    r, events = researcher(tmp_path, {'effort': 'quick'})
    checks = []

    async def checked():
        checks.append('sent')

    r.check_claims = r.check_evidence = checked

    class Engine:
        async def run(self, owner):
            owner.result = {'claims': [{'claim': 'retained'}], 'evidence': ['retained']}
            raise asyncio.CancelledError

    asyncio.run(r.run(Engine()))
    assert not checks and events[-1]['status'] == 'cancelled'
    assert events[-1]['result']['claims']


@pytest.mark.parametrize('before,after,stops', [(None, 1., True), (2., 1., True), (1., 2., False), (1., None, False)])
def test_saved_cap_waits_for_owned_research_cancellation(tmp_path, ledger, before, after, stops):
    ledger.set_cap(before)

    async def scenario():
        manager = ResearchManager(FakeHub(), root=tmp_path / 'research')
        session = finished_with_a_question(manager, status='running')
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        class Worker:
            async def call(self, op, **payload):
                calls.append((op, payload))
                started.set()
                await release.wait()
                manager._finish(session, 'cancelled', {'summary': 'kept'})
                return {'ok': True, 'terminated': True}

        manager._worker = Worker()
        app = NS(hub=manager.hub, research=manager)
        task = asyncio.create_task(commands.usage_cap(app, None, {'usd': after}))
        if stops:
            await asyncio.wait_for(started.wait(), HANG_S)
            assert not task.done(), 'Saved must wait for the child stop receipt'
            release.set()
        result = await asyncio.wait_for(task, HANG_S)
        assert result['ok'] and ledger.cap() == after
        assert bool(calls) is stops
        if stops:
            assert session.result['summary'] == 'kept'
            assert 'stopped' in result['says']

    asyncio.run(scenario())


def test_worker_confirms_cancellation_only_after_the_researcher_finishes(tmp_path, monkeypatch):
    import queue
    from research_worker import worker

    incoming = queue.Queue()
    messages = []

    class Connection:
        recv = incoming.get

        def send(self, message):
            messages.append(message)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class Engine:
            async def run(self, r):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    await release.wait()

        monkeypatch.setattr(worker, 'make_engine', lambda name: Engine())
        serving = asyncio.create_task(worker._main(Connection()))
        incoming.put({'id': 1, 'op': 'spawn', 'rid': 'r1', 'dir': str(tmp_path), 'effort': 'quick'})
        await asyncio.wait_for(entered.wait(), HANG_S)
        incoming.put({'id': 2, 'op': 'cancel', 'rid': 'r1', 'wait': True})
        await asyncio.sleep(.01)
        assert not any(m.get('reply_to') == 2 for m in messages)
        release.set()
        await until_async(lambda: any(m.get('reply_to') == 2 for m in messages), every=.001)
        terminal = next(i for i, m in enumerate(messages) if m.get('event') == 'done')
        receipt = next(i for i, m in enumerate(messages) if m.get('reply_to') == 2)
        assert terminal < receipt and messages[receipt]['terminated']
        incoming.put(None)
        await serving

    asyncio.run(scenario())
