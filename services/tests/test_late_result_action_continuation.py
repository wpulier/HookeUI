"""TEST precursor: completed late query facts permit a bounded model action plan.

Actual App/COMMANDS/Brain, Registry/Runner, admission, retained files, native
presentation and Screen confirmation. Only external model replies, renderer
observations and one real file-read barrier are synthetic. No tool handler,
receipt, occurrence, Ticket, completion or continuation callback is replaced.
This establishes plumbing, not actual model judgment or browser paint.
"""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from orchestrator.ticket import current, generations
from orchestrator.workspace_observation import ClientRef
from tests.test_model_owned_conversation import (
    ExternalFileBarrier, LocalApp, action, eventually, response,
)
from tests.test_workspace_actions_app import AppStudio, CLIENT, address, saved_picture
from tests.waiting import HANG_S


PLAN = 'I will open the retained picture, read the original note, and then close that window.'
CORRECTION = 'The note has arrived; I will close the original retained-picture window now.'
DONE = 'The original retained picture was opened, its note was read, and that window is now closed.'
REFUSED = 'The original window remains open because the follow-up close was refused.'
NOTE_READ = 'The original retained note was read; the picture window remains open.'
MARKER = '<answer-facts source="app" untrusted="true">\n'


class ExternalStudio(AppStudio):
    """The actual original socket's outbound transport, not a second registry."""
    def offer(self, message, lossy=None):
        if isinstance(message, str):
            self.hear(json.loads(message))


class ExternalReplies:
    """Scripted provider entry; all semantic assertions live outside this callback."""
    def __init__(self, initial, *, refused=False, unavailable=False, answer_only=False):
        self.initial = initial
        self.refused, self.unavailable, self.answer_only = refused, unavailable, answer_only
        self.original_window = None  # set only after the public original open receipt
        self.held_after_query = None
        self.sends = []

    async def ask(self, images, prompt, **kwargs):
        ticket = current()
        admission = getattr(ticket, 'admission', None)
        attempts = admission.attempts if admission is not None else ()
        sent = SimpleNamespace(ticket=ticket, admission=admission,
            phase=attempts[-1].phase if attempts else None, images=tuple(images), prompt=prompt)
        self.sends.append(sent)
        if len(self.sends) == 1:
            data = self.initial
        elif len(self.sends) == 2 and self.held_after_query is not None:
            data = response('I will read the additional original note.',
                            actions=[action('resources', id=self.held_after_query)])
        elif (len(self.sends) == 2 and not self.answer_only) or self.held_after_query is not None:
            args = {'do': 'close', 'instance': self.original_window}
            if self.refused:
                args['size'] = {'width': 80, 'height': 60}  # actual unused-argument refusal
            data = response(CORRECTION, actions=[action('present', **args)])
        else:
            if self.unavailable:
                sent.provider_error = asyncio.TimeoutError('synthetic final provider unavailable')
                raise sent.provider_error
            data = response(NOTE_READ if self.answer_only else REFUSED if self.refused else DONE)
        sent.reply_data = copy.deepcopy(data)
        return SimpleNamespace(data=copy.deepcopy(data), latency_ms=0.0,
                               model='scripted-external-provider', error=None)


def setup_case(tmp_path, monkeypatch, *, failed_cleanup=False, refused=False,
               unavailable=False, answer_only=False):
    rig = LocalApp(tmp_path, monkeypatch)
    studio = ExternalStudio(rig.app)
    rig.app.accept_workspace(studio, ClientRef(CLIENT['workspaceId'], CLIENT['clientId'],
                                             CLIENT['pageEpoch'], CLIENT['connectionEpoch']))
    rig.app.hub.clients.remove(rig.client)
    rig.app.hub.clients.add(studio)
    rig.client = studio
    entry, payload = saved_picture(rig.app)
    actions = [action('present', do='open', source=address(entry)),
               action('resources', id=rig.record['id'])]
    if failed_cleanup:
        actions.append(action('present', do='close', instance={'step': 1, 'output': 0},
                              size={'width': 80, 'height': 60}))
    rig.provider = ExternalReplies(response(PLAN, actions=actions), refused=refused,
                                  unavailable=unavailable, answer_only=answer_only)
    logged = []
    rig.app.agent.taps.append(logged.append)  # actual interaction observer, no replacement
    barrier = ExternalFileBarrier(monkeypatch, rig.file)
    return rig, studio, entry, payload, barrier, logged


def facts(send):
    return json.loads(send.prompt.split(MARKER, 1)[1].split('\n</answer-facts>', 1)[0])


async def start_held(rig, studio, barrier):
    await studio.send_report()  # actual accepted original Send pin, before asking
    request = asyncio.create_task(rig.ask(PLAN))
    await eventually(barrier.entered.is_set, 'SETUP: original query enters actual retained-file IO')
    initial = await asyncio.wait_for(asyncio.shield(request), HANG_S)
    opened = initial['actions'][0]
    assert opened['tool'] == 'present' and opened['ok'] is True
    window = next(output for output in opened['outputs'] if output['kind'] == 'presentation')['instanceId']
    assert studio.items[window]['open'] and studio.items[window]['load']['state'] == 'drawn'
    assert initial['actions'][1]['ok'] is None and not barrier.release.is_set()
    assert len(rig.provider.sends) == 1 and rig.provider.sends[0].admission.used == 1
    assert rig.provider.sends[0].admission.remaining == 1, 'BEHAVIOR: original final slot remains reserved while query waits'
    rig.provider.original_window = window  # external reply uses the actual original output, never latest selection
    return request, initial, window, rig.provider.sends[0].ticket


@pytest.mark.parametrize('case', ['all-success', 'failed-cleanup', 'refused-correction', 'unavailable-final'])
def test_completed_late_results_allow_only_deliberate_original_window_correction(tmp_path, monkeypatch, case):
    rig, studio, entry, payload, barrier, logged = setup_case(tmp_path, monkeypatch,
        failed_cleanup=case != 'all-success', refused=case == 'refused-correction',
        unavailable=case == 'unavailable-final')
    async def scenario():
        request, initial, window, ticket = await start_held(rig, studio, barrier)
        original = [(run['requestId'], run['actionId']) for run in initial['actions']]
        try:
            assert len(set(original)) == len(initial['actions'])
            assert studio.items[window]['sourceRef']['identity']['resource']['id'] == entry.id
            assert rig.app.hub.resources.picture(entry.id, 0, entry.day)[0] == payload
            barrier.release.set()
            await eventually(lambda: ticket.closed, 'SETUP: original producer and late interpretation settle')
            assert len(rig.provider.sends) >= 2
            continuation = rig.provider.sends[1]
            supplied = facts(continuation)
            statuses = supplied['operationStatuses']
            assert [(row['requestId'], row['actionId']) for row in statuses[:len(original)]] == original
            assert [row['ok'] for row in statuses[:len(original)]] == (
                [True, True] if case == 'all-success' else [True, True, False])
            assert rig.record['id'] in continuation.prompt and window in continuation.prompt
            assert supplied['inputs']['images'] == [] and supplied['limitations'] == []
            assert continuation.reply_data['actions'][0]['args']['instance'] == window
            native_commands = list(studio.rendered_commands.values())
            assert len([cmd for cmd in native_commands if cmd['op'] == 'open']) == 1, 'BEHAVIOR: original successful open is never replayed'
            closes = [cmd for cmd in native_commands if cmd['op'] == 'close']
            if case == 'refused-correction':
                assert not closes and studio.items[window]['open']
            else:
                assert len(closes) == 1 and closes[0]['targets'] == [window], (
                    'MISSING_FEATURE: model-selected late correction must execute once on the original window')
                assert not studio.items[window]['open']
            admission = rig.assert_budget()
            assert admission.used == 3, 'MISSING_FEATURE: one action-capable continuation preserves the original final round'
            assert [send.phase for send in rig.provider.sends] == ['fast', 'query', 'final']
            assert admission.remaining == 0
            final = rig.provider.sends[-1]
            final_statuses = facts(final)['operationStatuses']
            assert [(row['requestId'], row['actionId']) for row in final_statuses[:len(original)]] == original
            correction = final_statuses[-1]
            assert len(final_statuses) == len(original) + 1
            assert correction['requestId'] == original[0][0]
            assert (correction['requestId'], correction['actionId']) not in original
            assert correction['tool'] == 'present' and correction['ok'] is (case != 'refused-correction')
            if case == 'unavailable-final':
                assert admission.attempts[-1].status == 'failed'
                assert not any(row.get('answer') == DONE for row in rig.lines())
                assert DONE not in rig.assistant_texts()
            else:
                expected = REFUSED if case == 'refused-correction' else DONE
                assert final.reply_data == response(expected)
                assert any(row.get('answer') == expected and row.get('turnId') == initial['turnId'] for row in rig.lines()), (
                    'MISSING_FEATURE: actual final model answer must reach the original late UI')
            receipts = [row for row in rig.lines() if row.get('type') == 'chat-action'
                        and row.get('requestId') == correction['requestId']
                        and row.get('actionId') == correction['actionId'] and row.get('ok') is not None]
            assert receipts and receipts[-1]['ok'] == correction['ok'], 'BEHAVIOR: correction terminal activity survives even without an answer'
            assert all(row.get('turnId') == initial['turnId'] for row in receipts)
            assert logged  # actual original interaction logging observer was reached
        finally:
            barrier.release.set()
            await asyncio.gather(request, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        barrier.release.set()
        rig.close()


@pytest.mark.parametrize('retirement', ['cancel', 'day'])
def test_late_original_request_retirement_prevents_model_actions_and_extra_dispatch(tmp_path, monkeypatch, retirement):
    rig, studio, _entry, _payload, barrier, _logged = setup_case(tmp_path, monkeypatch)
    async def scenario():
        request, _initial, window, ticket = await start_held(rig, studio, barrier)
        try:
            if retirement == 'cancel':
                ticket.cancel('original request cancelled while retained IO waited')
            else:
                generations(rig.app.hub).bump('session', why='original day retired while retained IO waited')
            barrier.release.set()
            await eventually(lambda: ticket.closed, 'SETUP: original file worker actually returns and closes its Ticket')
            assert rig.assert_budget().used == 1
            assert ticket.superseded('session')
            assert studio.items[window]['open']
            assert [cmd['op'] for cmd in studio.rendered_commands.values()] == ['open']
            assert not any(row.get('answer') == DONE for row in rig.lines())
        finally:
            barrier.release.set()
            await asyncio.gather(request, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        barrier.release.set()
        rig.close()


def test_eligible_action_free_late_query_answer_keeps_original_success_without_extra_send(tmp_path, monkeypatch):
    rig, studio, _entry, _payload, barrier, _logged = setup_case(tmp_path, monkeypatch, answer_only=True)
    async def scenario():
        request, initial, window, ticket = await start_held(rig, studio, barrier)
        try:
            barrier.release.set()
            await eventually(lambda: ticket.closed, 'SETUP: completed original query settles its actual model answer')
            assert rig.assert_budget().used == 2
            assert rig.provider.sends[-1].reply_data == response(NOTE_READ)
            assert studio.items[window]['open']
            assert [cmd['op'] for cmd in studio.rendered_commands.values()] == ['open']
            assert any(row.get('answer') == NOTE_READ and row.get('turnId') == initial['turnId'] for row in rig.lines())
        finally:
            barrier.release.set()
            await asyncio.gather(request, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        barrier.release.set()
        rig.close()


def test_third_entered_late_reply_cannot_start_uninterpretable_extra_effects_or_fourth_send(tmp_path, monkeypatch):
    rig, studio, entry, _payload, barrier, _logged = setup_case(tmp_path, monkeypatch)
    immediate = rig.app.hub.resources.results.add('compare', {}, 'SYNTHETIC_IMMEDIATE_ORIGINAL_NOTE',
        values={'basis': 'synthetic retained text'}, thread=rig.app.day.active_id)
    rig.provider.initial = response(PLAN, actions=[action('present', do='open', source=address(entry)),
                                                  action('resources', id=immediate['id'])])
    rig.provider.held_after_query = rig.record['id']
    async def scenario():
        await studio.send_report()
        request = asyncio.create_task(rig.ask(PLAN))
        try:
            await eventually(barrier.entered.is_set, 'SETUP: real second planning send adds a held original query')
            initial = await asyncio.wait_for(asyncio.shield(request), 4)
            assert len(rig.provider.sends) == 2
            assert [send.phase for send in rig.provider.sends] == ['fast', 'query']
            assert rig.provider.sends[0].admission.remaining == 0, 'BEHAVIOR: final reservation is protected after actual query dispatch'
            assert len(initial['actions']) == 3 and [run['ok'] for run in initial['actions']] == [True, True, None]
            window = initial['actions'][0]['outputs'][0]['instanceId']
            rig.provider.original_window = window
            ticket = rig.provider.sends[0].ticket
            barrier.release.set()
            await eventually(lambda: ticket.closed, 'SETUP: third actual late provider and original source settle')
            assert rig.assert_budget().used == 3
            assert [attempt.status for attempt in ticket.admission.attempts] == ['done', 'done', 'done']
            assert rig.provider.sends[-1].phase in ('late', 'final')
            assert rig.provider.sends[-1].reply_data['actions'][0]['args']['instance'] == window
            assert studio.items[window]['open']
            assert [cmd['op'] for cmd in studio.rendered_commands.values()] == ['open'], (
                'BEHAVIOR: a final-only reply cannot execute new work whose result has no interpretation allowance')
            assert not any(row.get('answer') == DONE for row in rig.lines())
        finally:
            barrier.release.set()
            await asyncio.gather(request, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        barrier.release.set()
        rig.close()


class ExternalRefusalAnswer(ExternalReplies):
    """Same external correction script; its final reply describes actual refusal."""
    async def ask(self, images, prompt, **kwargs):
        reply = await super().ask(images, prompt, **kwargs)
        if len(self.sends) == 3:
            reply.data = response(REFUSED)
            self.sends[-1].reply_data = copy.deepcopy(reply.data)
        return reply


async def accepted_manual(rig, studio, *, correlation, **args):
    """Real App manual ingress and retained replay, not a synthetic user owner."""
    from orchestrator.commands import COMMANDS
    packet = {'type': 'present', 'requestId': 'browser/' + correlation,
              'actionId': 'browser-action/' + correlation,
              'client': dict(studio.client_ref), **args}
    deadline = asyncio.get_running_loop().time() + 3
    reply = await COMMANDS['present'](rig.app, studio, copy.deepcopy(packet))
    while reply.get('pending'):
        assert asyncio.get_running_loop().time() < deadline, 'SETUP: original manual command must actually settle'
        await asyncio.sleep(0.01)
        reply = await COMMANDS['present'](rig.app, studio, copy.deepcopy(packet))
    assert reply.get('ok') is True and reply['run']['ok'] is True, 'SETUP: actual independent manual operation must succeed'
    return reply['run']


@pytest.mark.parametrize('intervening', ['revised-property', 'closed-lifetime'])
def test_late_original_output_ancestry_does_not_override_independent_manual_change(tmp_path, monkeypatch, intervening):
    rig, studio, _entry, _payload, barrier, _logged = setup_case(tmp_path, monkeypatch)
    rig.provider = ExternalRefusalAnswer(rig.provider.initial)
    async def scenario():
        request, initial, window, ticket = await start_held(rig, studio, barrier)
        original = initial['actions'][0]['outputs'][0]
        try:
            if intervening == 'revised-property':
                manual = await accepted_manual(rig, studio, correlation=intervening,
                    do='resize', instance={'instanceId': window}, size={'width': 180, 'height': 140})
                assert studio.items[window]['open'] and studio.items[window]['rect'][2:] == [180, 140]
                assert studio.items[window]['propertyVersions']['geometry'] != original['propertyVersions']['geometry']
            else:
                manual = await accepted_manual(rig, studio, correlation=intervening,
                    do='close', instance={'instanceId': window})
                assert not studio.items[window]['open']
            assert manual['requestId'] != initial['actions'][0]['requestId']
            commands_before = len(studio.rendered_commands)
            barrier.release.set()
            await eventually(lambda: ticket.closed, 'SETUP: original query and actual follow-up refusal settle')
            assert rig.assert_budget().used == 3
            assert rig.provider.sends[1].reply_data['actions'] == [action('present', do='close', instance=window)]
            statuses = facts(rig.provider.sends[-1])['operationStatuses']
            assert [row['ok'] for row in statuses] == [True, True, False]
            assert statuses[-1]['requestId'] == initial['actions'][0]['requestId']
            assert statuses[-1]['actionId'] != manual['actionId']
            assert len(studio.rendered_commands) == commands_before, 'BEHAVIOR: original ancestry never revives a user-revised or closed target'
            assert studio.items[window]['open'] is (intervening == 'revised-property')
            assert any(row.get('answer') == REFUSED and row.get('turnId') == initial['turnId'] for row in rig.lines())
        finally:
            barrier.release.set()
            await asyncio.gather(request, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        barrier.release.set()
        rig.close()
def test_late_request_cannot_borrow_an_independent_window_created_after_its_original_send(tmp_path, monkeypatch):
    rig, studio, entry, _payload, barrier, _logged = setup_case(tmp_path, monkeypatch)
    rig.provider = ExternalRefusalAnswer(response('I will read the original retained note.',
                                                actions=[action('resources', id=rig.record['id'])]))
    async def scenario():
        await studio.send_report()
        request = asyncio.create_task(rig.ask('Read the original note, then close only a window this request actually opened.'))
        try:
            await eventually(barrier.entered.is_set, 'SETUP: original query holds real IO before the independent window exists')
            initial = await asyncio.wait_for(asyncio.shield(request), 4)
            assert len(initial['actions']) == 1 and initial['actions'][0]['ok'] is None
            assert not studio.rendered_commands
            ticket = rig.provider.sends[0].ticket
            manual = await accepted_manual(rig, studio, correlation='independent-open', do='open', source=address(entry))
            window = next(output for output in manual['outputs'] if output['kind'] == 'presentation')['instanceId']
            assert manual['requestId'] != initial['actions'][0]['requestId']
            assert studio.items[window]['open'] and studio.items[window]['load']['state'] == 'drawn'
            rig.provider.original_window = window  # deliberate external model mistake, not original request authority
            barrier.release.set()
            await eventually(lambda: ticket.closed, 'SETUP: original query and borrowed-UUID refusal actually settle')
            assert rig.assert_budget().used == 3
            supplied = facts(rig.provider.sends[1])
            assert len(supplied['operationStatuses']) == 1 and supplied['operationStatuses'][0]['tool'] == 'resources'
            assert [row['ok'] for row in facts(rig.provider.sends[-1])['operationStatuses']] == [True, False]
            assert studio.items[window]['open']
            assert [cmd['op'] for cmd in studio.rendered_commands.values()] == ['open']
            assert any(row.get('answer') == REFUSED and row.get('turnId') == initial['turnId'] for row in rig.lines())
        finally:
            barrier.release.set()
            await asyncio.gather(request, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        barrier.release.set()
        rig.close()
