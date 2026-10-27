"""Actual App late interpretation; synthetic external provider and renderer only.

Real retained-file IO, native open/close, Ticket, admission, Screen and Dialogue
are unchanged. Wall-clock delays cross the actual old final cutoffs; no clock,
deadline, receipt, synthesis callback or ownership predicate is replaced.
These tests establish bounded response plumbing, not model or browser quality.
"""
from orchestrator.brain.loop import REWRITE_TOKENS
import asyncio
import copy

import pytest

from orchestrator.ticket import generations
from tests.test_late_result_action_continuation import (
    DONE, NOTE_READ, PLAN, ExternalReplies, facts, setup_case, start_held,
)
from tests.test_model_owned_conversation import action, eventually, response
from tests.test_workspace_actions_app import address


class DelayedInterpretation(ExternalReplies):
    """External send/result boundary, with passive entry/return/cancel observations."""
    def __init__(self, initial, *, delay=0, failure=None, held=False):
        super().__init__(initial)
        self.delay, self.failure, self.held = delay, failure, held
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.returned, self.cancelled = False, False
        self.output_caps = []

    async def ask(self, images, prompt, **kwargs):
        self.output_caps.append(kwargs.get('max_output_tokens'))
        result = await super().ask(images, prompt, **kwargs)
        sent = self.sends[-1]
        if sent.phase not in ('final', 'late'):
            return result
        self.entered.set()
        try:
            if self.held:
                await self.release.wait()
            await asyncio.sleep(self.delay)
            if self.failure == 'timeout':
                raise asyncio.TimeoutError('synthetic selected-provider timeout')
            if self.failure == 'provider-error':
                raise RuntimeError('synthetic external failure containing private diagnostic text')
            data = response('' if self.failure == 'empty-answer' else
                            NOTE_READ if sent.phase == 'late' else DONE,
                            scope='workspace-pixels' if self.failure == 'unsupported-input' else 'none')
            sent.reply_data = copy.deepcopy(data)
            result.data = copy.deepcopy(data)
            self.returned = True
            return result
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def prepared(tmp_path, monkeypatch, **options):
    rig, studio, entry, payload, barrier, logged = setup_case(tmp_path, monkeypatch)
    rig.provider = DelayedInterpretation(rig.provider.initial, **options)
    return rig, studio, entry, payload, barrier, logged


def original_terminal_activity(rig, initial, window, *, closed):
    """Inline receipts live on the original chat; pending completions are activity."""
    original = {(row['requestId'], row['actionId']) for row in initial['actions']}
    lines = [row for row in rig.lines() if row.get('turnId') == initial['turnId']]
    initial_chat = [row for row in lines if row.get('type') == 'chat'
                    and row.get('role') == 'assistant' and row.get('text') == initial['answer']]
    assert len(initial_chat) == 1, 'SETUP: the actual eligible initial model plan owns its inline receipts'
    inline = initial_chat[0]['actions']
    assert [(row['requestId'], row['actionId']) for row in inline] == [
        (row['requestId'], row['actionId']) for row in initial['actions']]
    for saved, returned in zip(inline, initial['actions']):
        assert all(saved[key] == value for key, value in returned.items())
    rows = [row for row in lines if row.get('type') == 'chat-action' and row.get('ok') is not None]
    terminal = [row for row in inline if row.get('ok') is not None] + rows
    assert original <= {(row.get('requestId'), row.get('actionId')) for row in terminal}
    pending = {(row['requestId'], row['actionId']) for row in initial['actions'] if row['ok'] is None}
    assert pending <= {(row.get('requestId'), row.get('actionId')) for row in rows}
    assert all(row.get('requestId') == initial['actions'][0]['requestId'] for row in rows)
    commands = list(rig.client.rendered_commands.values())
    assert [cmd['op'] for cmd in commands] == (['open', 'close'] if closed else ['open'])
    assert rig.client.items[window]['open'] is not closed
    return rows


@pytest.mark.parametrize('path,delay', [('reserved-final', 2.25), ('final-only-late', 4.25)])
def test_slow_eligible_selected_provider_reply_reaches_original_late_answer(tmp_path, monkeypatch, path, delay):
    rig, studio, entry, payload, barrier, logged = prepared(tmp_path, monkeypatch, delay=delay)
    async def scenario():
        request = None
        try:
            if path == 'reserved-final':
                request, initial, window, ticket = await start_held(rig, studio, barrier)
            else:
                # Actual second planning round adds the held read; only the
                # protected third interpretation remains after it completes.
                immediate = rig.app.hub.resources.results.add('compare', {}, 'SYNTHETIC_IMMEDIATE_ORIGINAL_NOTE',
                    values={'basis': 'synthetic retained text'}, thread=rig.app.day.active_id)
                rig.provider.initial = response(PLAN, actions=[action('present', do='open', source=address(entry)),
                    action('resources', id=immediate['id'])])
                rig.provider.held_after_query = rig.record['id']
                await studio.send_report()
                request = asyncio.create_task(rig.ask(PLAN))
                await eventually(barrier.entered.is_set, 'SETUP: actual second plan enters original retained IO')
                initial = await asyncio.wait_for(asyncio.shield(request), 4)
                assert [row['ok'] for row in initial['actions']] == [True, True, None]
                assert [send.phase for send in rig.provider.sends] == ['fast', 'query']
                window = initial['actions'][0]['outputs'][0]['instanceId']
                rig.provider.original_window = window
                ticket = rig.provider.sends[0].ticket
            assert rig.app.hub.resources.picture(entry.id, 0, entry.day)[0] == payload
            barrier.release.set()
            await eventually(rig.provider.entered.is_set, 'SETUP: actual reserved interpretation enters provider')
            assert not ticket.closed and rig.provider.sends[-1].admission.used == 3
            await eventually(lambda: ticket.closed, 'SETUP: original interpretation and owned work settle')
            rows = original_terminal_activity(rig, initial, window, closed=path == 'reserved-final')
            admission = rig.assert_budget()
            assert admission.used == 3 and admission.remaining == 0
            assert [attempt.phase for attempt in admission.attempts] == ['fast', 'query', 'final' if path == 'reserved-final' else 'late']
            assert rig.provider.returned and not rig.provider.cancelled, (
                'MISSING_FEATURE: a valid selected-provider reply must not be discarded by the old short cutoff')
            assert all(attempt.status == 'done' for attempt in admission.attempts)
            expected = DONE if path == 'reserved-final' else NOTE_READ
            assert rig.provider.sends[-1].reply_data == response(expected)
            assert any(row.get('answer') == expected for row in rows), (
                'MISSING_FEATURE: literal eligible model text must reach original late activity')
            assert all(row.get('turnId') == initial['turnId'] for row in rows)
            assert facts(rig.provider.sends[-1])['inputs']['images'] == []
            if path == 'reserved-final':
                assert rig.provider.output_caps[-1] == REWRITE_TOKENS  # 600 since 2026-10-11 (300 cut answers off)
            completed = [row for row in logged if row.get('kind') == 'query-answer']
            assert len(completed) == 1 and completed[0]['answer'] == expected and completed[0]['calls'] == 3
        finally:
            barrier.release.set()
            rig.provider.release.set()
            if request is not None:
                await asyncio.gather(request, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        barrier.release.set()
        rig.close()


@pytest.mark.parametrize('failure', ['timeout', 'provider-error', 'unsupported-input', 'empty-answer'])
def test_unavailable_final_keeps_receipts_and_bounded_original_failure_diagnostic(tmp_path, monkeypatch, failure):
    rig, studio, _entry, _payload, barrier, logged = prepared(tmp_path, monkeypatch, failure=failure)
    async def scenario():
        request, initial, window, ticket = await start_held(rig, studio, barrier)
        try:
            barrier.release.set()
            await eventually(lambda: ticket.closed, 'SETUP: actual correction and unsuccessful final settle')
            original_terminal_activity(rig, initial, window, closed=True)
            admission = rig.assert_budget()
            assert admission.used == 3 and [send.phase for send in rig.provider.sends] == ['fast', 'query', 'final']
            assert admission.attempts[-1].status == ('failed' if failure in ('timeout', 'provider-error') else 'done')
            completed = [row for row in logged if row.get('kind') == 'query-answer']
            assert len(completed) == 1 and completed[0]['answer'] == ''
            assert not any(row.get('answer') == DONE for row in rig.lines())
            assert rig.assistant_texts() == [initial['answer']], (
                'BEHAVIOR: unavailable final cannot add prose beyond the actual initial model plan')
            diagnostics = [row for row in logged if row.get('kind') == 'answer-interpretation-failed']
            assert len(diagnostics) == 1, 'MISSING_FEATURE: final suppression must retain its actual failure boundary'
            diagnostic = diagnostics[0]
            assert diagnostic['turnId'] == initial['turnId']
            assert diagnostic['requestId'] == admission.request_id
            assert diagnostic['attemptId'] == admission.attempts[-1].attempt_id
            assert diagnostic['phase'] == 'final' and diagnostic['reason'] == failure and diagnostic['calls'] == 3
            if failure in ('timeout', 'provider-error'):
                assert diagnostic['errorType'] == ('TimeoutError' if failure == 'timeout' else 'RuntimeError')
            assert all(value is None or type(value) in (str, int, float, bool) for value in diagnostic.values())
            assert all(len(value) <= 128 for value in diagnostic.values() if isinstance(value, str))
            assert 'private diagnostic text' not in str(diagnostic)
        finally:
            barrier.release.set()
            await asyncio.gather(request, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        barrier.release.set()
        rig.close()


@pytest.mark.parametrize('retirement', ['cancel', 'day'])
def test_original_retirement_during_entered_final_keeps_spend_and_never_publishes_old_reply(tmp_path, monkeypatch, retirement):
    rig, studio, _entry, _payload, barrier, _logged = prepared(tmp_path, monkeypatch, held=True)
    async def scenario():
        request, initial, window, ticket = await start_held(rig, studio, barrier)
        try:
            barrier.release.set()
            await eventually(rig.provider.entered.is_set, 'SETUP: final entered after actual successful correction')
            admission = rig.assert_budget()
            assert admission.used == 3 and admission.attempts[-1].status == 'running'
            original_terminal_activity(rig, initial, window, closed=True)
            if retirement == 'cancel':
                ticket.cancel('original request retired during final provider IO')
            else:
                generations(rig.app.hub).bump('session', why='original day retired during final provider IO')
            rig.provider.release.set()
            await eventually(lambda: ticket.closed, 'SETUP: entered provider and original owner actually settle')
            assert rig.assert_budget().used == 3 and admission.remaining == 0
            assert ticket.superseded('session')
            assert not any(row.get('answer') == DONE for row in rig.lines())
            assert DONE not in rig.assistant_texts()
            assert [cmd['op'] for cmd in studio.rendered_commands.values()] == ['open', 'close']
        finally:
            barrier.release.set()
            rig.provider.release.set()
            await asyncio.gather(request, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        barrier.release.set()
        rig.close()
