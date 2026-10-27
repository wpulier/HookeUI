"""TEST-only native caller vertical; actual App/COMMANDS/Registry/Runner/core/Screen.

Only retained test bytes, transport offers, source-read barriers and the existing
AppStudio renderer are synthetic. No server, model, camera or browser is started.
Observers delegate to real production methods; no executor, association, Ticket,
successful run, pin, ACK or source producer is substituted. Existing helpers stay
frozen. Canonical manualSeq is external Runtime transport input, never admission.
"""
import asyncio
import copy
import json
import threading
import time

import pytest

from orchestrator import ticket as tickets
from orchestrator.brain.actions import ActionRunner
from orchestrator.commands import COMMANDS, handle
from orchestrator.workspace_observation import ClientRef, ObservationError
from orchestrator.workspace_transport import WorkspaceTransport
from tests.test_workspace_actions_app import (
    AppStudio, CLIENT, RUN, WORKSPACE, address, built, controlled_state,
    open_window, saved_picture,
)
from tests.waiting import HANG_S, until_async


def _records(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _records(child)
    elif isinstance(value, list):
        for child in value:
            yield from _records(child)


class Trace:
    """Passive delegates at actual issuance, Registry.start and ActionRunner.run."""
    def __init__(self, app, monkeypatch):
        self.issued, self.calls, self.plans, self.pins, self.reads = [], [], [], [], []
        issue, start, run = tickets.issue, app.brain.registry.start, ActionRunner.run
        pin, read = app.hub.screen.pin, app.hub.resources.picture

        def observed_issue(*args, **kwargs):
            actual = issue(*args, **kwargs)
            self.issued.append(actual)
            return actual

        def observed_start(name, args, ctx, run=None):
            actual = start(name, args, ctx, run=run)
            if name == 'present':
                pin = ctx.request_snapshot
                self.calls.append({'run': actual, 'ctx': ctx, 'ticket': tickets.current(),
                                   'pin': pin, 'pin_wire': pin.observation.wire() if pin else None})
            return actual

        async def observed_plan(runner, actions, ctx, numbers=None):
            self.plans.append((runner, copy.deepcopy(actions), ctx, tickets.current()))
            return await run(runner, actions, ctx, numbers)

        def observed_pin(*args, **kwargs):
            actual = pin(*args, **kwargs)
            self.pins.append(actual)
            return actual

        def observed_read(*args, **kwargs):
            self.reads.append((args, kwargs))
            return read(*args, **kwargs)

        monkeypatch.setattr(tickets, 'issue', observed_issue)
        monkeypatch.setattr(app.brain.registry, 'start', observed_start)
        monkeypatch.setattr(ActionRunner, 'run', observed_plan)
        monkeypatch.setattr(app.hub.screen, 'pin', observed_pin)
        monkeypatch.setattr(app.hub.resources, 'picture', observed_read)

    def original(self, packet, index=0):
        assert len(self.calls) > index, 'MISSING_FEATURE: manual ingress must execute the actual Registry present'
        call = self.calls[index]
        run, ctx, ticket = call['run'], call['ctx'], call['ticket']
        assert ticket in self.issued, 'BEHAVIOR: original manual Ticket must be server-issued'
        assert ticket.kind == 'command' and ticket.channel == 'studio'
        assert ctx.admission is ticket.admission and ctx.admission.used == 0
        assert call['pin'] is not None, 'MISSING_FEATURE: manual Send needs the actual immutable Screen pin'
        assert any(pin is call['pin'] for pin in self.pins), 'BEHAVIOR: original pin must come from real Screen.pin'
        assert ctx.request_snapshot is call['pin'] and ctx.client is not None
        assert run.occurrence.request_id == ticket.admission.request_id != packet['requestId']
        assert run.occurrence.action_id != packet['actionId']
        assert run.step[0] == 1 and ctx.steps.runs[1] is run
        assert ctx.steps.occurrences[1] == run.occurrence
        assert any(plan[2] is ctx and plan[3] is ticket for plan in self.plans), \
            'MISSING_FEATURE: manual ingress must use the existing ActionRunner'
        return call


def _offers(socket, monkeypatch):
    replies = []
    # External synthetic websocket output only; real commands.handle offers JSON.
    monkeypatch.setattr(socket, 'offer', lambda value: replies.append(json.loads(value)), raising=False)
    return replies


async def _submit(app, socket, packet, replies):
    before = len(replies)
    await handle(app, socket, copy.deepcopy(packet))
    actual = [reply for reply in replies[before:]
              if reply.get('for') == 'present' and reply.get('requestId') == packet['requestId']]
    assert actual, 'BEHAVIOR: actual command handler must offer its originating result'
    return actual[-1]


async def _until(predicate, message):
    await until_async(predicate, what=message)


async def _settled(call):
    run = call['run']
    assert run.task is not None, 'MISSING_FEATURE: actual native operation task'
    await asyncio.wait_for(asyncio.shield(run.task), HANG_S)
    if run.sequence is not None:
        await asyncio.wait_for(asyncio.shield(run.sequence), HANG_S)
    return run


def _receipt_schedule(app, monkeypatch):
    """Script server receipt time, keeping real execution and renderer evidence.

    Capacity is tested with an unexpired original. A busy host must not turn
    the 31 sibling actions into a test of the separate four-second deadline.
    The native-confirmation tests independently exercise that unchanged limit.
    Each actual renderer report advances this clock; no pending entry, outcome,
    fence or acknowledgment is fabricated. Async task scheduling stays real.
    """
    now = [time.monotonic()]
    monkeypatch.setattr(app.hub.screen, '_observation_clock', lambda: now[0])
    send = AppStudio.send_report

    async def report(studio, *args, **kwargs):
        now[0] += 0.001
        return await send(studio, *args, **kwargs)

    monkeypatch.setattr(AppStudio, 'send_report', report)


def _result(reply, call):
    outcome = call['run'].wire().get('outcome')
    assert outcome, 'MISSING_FEATURE: native manual run must retain its causal observed outcome'
    assert outcome in list(_records(reply)), 'BEHAVIOR: reply must project its actual original ToolRun outcome'
    return outcome


def _horizon(reply, n):
    found = [row for row in _records(reply) if row.get('manualSeq') == n
             and {'highestManualSeq', 'expiredThroughManualSeq'} <= set(row)]
    assert found, 'MISSING_FEATURE: canonical manual reply needs explicit H/F transport horizon'
    row = found[-1]
    assert type(row['highestManualSeq']) is int and type(row['expiredThroughManualSeq']) is int
    assert 0 <= row['expiredThroughManualSeq'] <= row['highestManualSeq']
    return row


def _legacy(studio, *, source=None, instance=None, factor=2, correlation='legacy-one'):
    args = {'do': 'open', 'source': source} if source is not None else {
        'do': 'zoom', 'what': {'instanceId': instance}, 'zoom': {'factor': factor}}
    return {'type': 'present', 'requestId': 'browser/' + correlation,
            'actionId': 'browser-action/' + correlation, 'client': dict(studio.client_ref), **args}


def _canonical(studio, iid, n, *, level=2):
    state = studio.store().get(WORKSPACE, iid)
    return {'type': 'present', 'schema': 'presentation-manual-v2', 'manualSeq': n,
            'op': 'zoom', 'targets': [iid], 'values': {'level': level},
            'client': dict(studio.client_ref), 'appRunId': RUN,
            'requestId': f'browser/manual/{n}', 'actionId': f'browser/action/{n}',
            'commandId': f'browser/command/{n}',
            'base': {iid: {'sourceGeneration': state.source_generation,
                           'lifetimeGeneration': state.lifetime_generation,
                           'propertyVersions': dict(state.property_versions)}}}


async def _accepted_hello(app, studio):
    """Actual transport hello/server epoch; no HTTP server or fake grant authority."""
    transport = WorkspaceTransport(app)
    app.workspace_transport = transport
    app.hub.clients.add(studio)
    reply = transport.hello(studio, {'type': 'workspace-hello', 'schema': 'workspace-origin-v2',
        'requestId': 'external-hello', 'workspaceId': WORKSPACE,
        'clientId': CLIENT['clientId'], 'pageEpoch': CLIENT['pageEpoch']})
    assert reply.get('ok') is True, 'BEHAVIOR: actual accepted transport setup must succeed'
    studio.client_ref = dict(reply['client'])
    await studio.send_report()
    return transport


def _source_barrier(app, monkeypatch, entry):
    reached, release = threading.Event(), threading.Event()
    read = app.hub.resources.picture

    def held(eid, n=0, day=None, *args, **kwargs):
        if eid == entry.id:
            reached.set()
            assert release.wait(HANG_S), 'synthetic retained-source barrier was not released'
        return read(eid, n, day, *args, **kwargs)

    monkeypatch.setattr(app.hub.resources, 'picture', held)
    return reached, release


@pytest.mark.parametrize('canonical', [False, True], ids=['app-accepted-no-http-grant', 'canonical-server-epoch'])
def test_manual_ingress_owns_real_ticket_pin_runner_and_original_result(tmp_path, monkeypatch, canonical):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    replies = _offers(studio, monkeypatch)

    async def go():
        if canonical:
            await _accepted_hello(app, studio)
            iid, _, _ = await open_window(app, entry)
        else:
            assert not hasattr(app, 'workspace_transport'), 'fixture is direct App acceptance, without HTTP grant'
            iid = None
        trace = Trace(app, monkeypatch)
        packet = _canonical(studio, iid, 1) if canonical else _legacy(studio, source=address(entry))
        start = app.brain.registry.start
        def correlated_before_start(*args, **kwargs):
            assert any(row.get('type') == 'presentation-admitted' and row.get('requestId') == packet['requestId'] for row in replies)
            return start(*args, **kwargs)
        monkeypatch.setattr(app.brain.registry, 'start', correlated_before_start)
        outer = tickets.issue(app.hub, 'turn', asked=app.day.here(), channel='chat')
        try:
            with tickets.using(outer):
                first = await _submit(app, studio, packet, replies)
            assert first.get('ok') is True, 'MISSING_FEATURE: accepted native manual control must execute successfully'
            call = trace.original(packet)
            admitted = [reply for reply in replies if reply.get('type') == 'presentation-admitted']
            assert admitted == [{'type': 'presentation-admitted', 'requestId': packet['requestId'],
                'executionRequestId': call['run'].occurrence.request_id,
                'client': studio.client_ref, 'appRunId': RUN}]
            assert call['ticket'] is not outer, 'BEHAVIOR: user edit cannot borrow a preparing model request'
            await _settled(call)
            replay = await _submit(app, studio, packet, replies)
            assert replay == first, 'BEHAVIOR: terminal replay must return its actual original result'
            assert len(trace.calls) == 1 and len(trace.issued) == 2  # one outer, one manual
            outcome = _result(replay, call)
            native = [msg for msg in studio.messages if msg.get('schema') == 'presentation-command-v2'
                      and msg.get('requestId') == call['run'].occurrence.request_id]
            assert native and all(msg['actionId'] == call['run'].occurrence.action_id for msg in native)
            assert all(msg['client'] == studio.client_ref for msg in native)
            assert all(msg['commandId'] != packet.get('commandId') for msg in native)
            assert outcome['status'] == 'confirmed'  # requesting renderer now supplies causal zoom paint
            assert call['ticket'].closed, 'BEHAVIOR: terminal request releases its original Ticket'
            assert call['ctx'].request_snapshot.observation.wire() == call['pin_wire']
            if canonical:
                assert _horizon(replay, 1)['highestManualSeq'] == 1
                assert studio.store().get(WORKSPACE, iid).owners['zoom'] == call['run'].occurrence.action_id
        finally:
            outer.close()
    asyncio.run(go())


def test_manual_controls_accept_rich_workspace_within_observation_budget(tmp_path, monkeypatch):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    replies = _offers(studio, monkeypatch)

    async def go():
        await _accepted_hello(app, studio)
        for _ in range(11):
            iid, _, _ = await open_window(app, entry)
        pin = app.hub.screen.pin(studio, request_id='rich-workspace-check')
        assert len(json.dumps(pin.wire()).encode()) > 65536
        assert len(json.dumps(pin.observation.wire(), separators=(',', ':')).encode()) <= 65536
        packet = _canonical(studio, iid, 1)
        packet['op'], packet['values'] = 'resize', {'size': [400, 280]}
        reply = await _submit(app, studio, packet, replies)
        assert reply.get('ok') is True, reply
        assert studio.store().get(WORKSPACE, iid).geometry_intent.size == (400, 280)
    asyncio.run(go())


@pytest.mark.parametrize('changed', ['factor', 'target', 'base', 'correlation'])
def test_changed_canonical_replay_refuses_but_fresh_sequence_is_new_intent(tmp_path, monkeypatch, changed):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    replies = _offers(studio, monkeypatch)

    async def go():
        await _accepted_hello(app, studio)
        iid, _, _ = await open_window(app, entry)
        sibling, _, _ = await open_window(app, entry)
        trace = Trace(app, monkeypatch)
        packet = _canonical(studio, iid, 1)
        packet['values'] = {'factor': 2}
        first = await _submit(app, studio, packet, replies)
        assert first.get('ok') is True, 'MISSING_FEATURE: canonical manual-v2 must reach real native zoom'
        original = trace.original(packet)
        await _settled(original)
        before = tuple(map(controlled_state, studio.store().list(WORKSPACE)))
        altered = copy.deepcopy(packet)
        if changed == 'factor': altered['values']['factor'] = 3
        elif changed == 'target': altered['targets'] = [sibling]
        elif changed == 'base': altered['base'][iid]['propertyVersions']['zoom'] += 1
        else: altered['actionId'] += '/changed'
        refused = await _submit(app, studio, altered, replies)
        assert refused.get('ok') is False, 'BEHAVIOR: same sequence with changed original body must refuse'
        assert len(trace.calls) == len(trace.issued) == 1
        assert tuple(map(controlled_state, studio.store().list(WORKSPACE))) == before
        fresh = _canonical(studio, iid, 2)
        fresh['values'] = {'factor': 2}
        accepted = await _submit(app, studio, fresh, replies)
        assert accepted.get('ok') is True, 'BEHAVIOR: fresh sequence retains useful intentional relative zoom'
        second = trace.original(fresh, 1)
        await _settled(second)
        assert second['run'].occurrence != original['run'].occurrence
        assert studio.store().get(WORKSPACE, iid).view.zoom == 4
    asyncio.run(go())


@pytest.mark.parametrize('bad', ['unaccepted-socket', 'wire-dictionary', 'mismatched-client', 'mismatched-run', 'missing-sequence'])
def test_manual_authority_refuses_before_ticket_or_source_work(tmp_path, monkeypatch, bad):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    replies = _offers(studio, monkeypatch)

    async def go():
        await _accepted_hello(app, studio)
        iid, _, _ = await open_window(app, entry)
        trace = Trace(app, monkeypatch)
        packet = _canonical(studio, iid, 1)
        before = controlled_state(studio.store().get(WORKSPACE, iid))
        if bad == 'wire-dictionary':
            with pytest.raises(ObservationError) as refused:
                app.workspace_context(dict(studio.client_ref), request_id='external', owner='external')
            assert refused.value.code == 'CLIENT_MISMATCH'
            assert not trace.issued and not trace.calls
            return
        socket, offered = studio, replies
        if bad == 'unaccepted-socket':
            socket = AppStudio(app)
            offered = _offers(socket, monkeypatch)
        elif bad == 'mismatched-client': packet['client']['connectionEpoch'] += '/wrong'
        elif bad == 'mismatched-run': packet['appRunId'] = 'external-earlier-run'
        else: packet.pop('manualSeq')
        refused = await _submit(app, socket, packet, offered)
        assert refused.get('ok') is False, 'BEHAVIOR: closed canonical authority must refuse'
        assert not trace.issued and not trace.calls, 'BEHAVIOR: refusal cannot admit original work'
        assert controlled_state(studio.store().get(WORKSPACE, iid)) == before
    asyncio.run(go())


@pytest.mark.parametrize('cancel_join', [False, True], ids=['equal-pending-join', 'canceled-waiter'])
def test_concurrent_manual_replay_shares_original_work_and_immutable_send_pin(tmp_path, monkeypatch, cancel_join):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    ready_entry, _ = saved_picture(app, 40)
    replies = _offers(studio, monkeypatch)
    reached, release = _source_barrier(app, monkeypatch, entry)

    async def go():
        sibling, _, _ = await open_window(app, ready_entry)
        trace = Trace(app, monkeypatch)
        packet = _legacy(studio, source=address(entry), correlation='held-source')
        first = asyncio.create_task(_submit(app, studio, packet, replies))
        join = None
        try:
            await _until(lambda: reached.is_set() or first.done(), 'MISSING_FEATURE: real native retained-source preparation')
            assert reached.is_set(), 'MISSING_FEATURE: native caller must reach the actual retained-source reader'
            call = trace.original(packet)
            pinned = copy.deepcopy(call['pin_wire'])
            changed = copy.deepcopy(packet)
            changed['source'] = address(ready_entry)
            refused = await _submit(app, studio, changed, replies)
            assert refused.get('ok') is False, 'BEHAVIOR: changed pending replay cannot replace its held original'
            join = asyncio.create_task(_submit(app, studio, packet, replies))
            await asyncio.sleep(0)
            assert len(trace.calls) == len(trace.issued) == 1, 'BEHAVIOR: duplicate cannot issue or run new work'
            assert not call['run'].task.done()
            # External later layout/selection facts, through genuine Screen ingress.
            later = app.hub.screen.observation(studio).wire()
            later.update(seq=studio.seq + 1, layoutRevision=later['layoutRevision'] + 101,
                         capturedMonoMs=time.monotonic() * 1000)
            selected_target = 'target:' + sibling
            later['interaction']['selected'] = [selected_target]
            accepted = await COMMANDS['screen'](app, studio, later)
            assert accepted.get('ok') is True
            assert app.hub.screen.observation(studio).wire()['interaction']['selected'] == [selected_target]
            assert pinned['interaction']['selected'] != [selected_target]
            studio.seq = later['seq']
            if cancel_join:
                join.cancel()
                with pytest.raises(asyncio.CancelledError): await join
                assert not call['run'].task.cancelled() and not call['ticket'].cancelled
            release.set()
            await first
            if not cancel_join: await join
            await _settled(call)
            assert call['ctx'].request_snapshot.observation.wire() == pinned
            replay = await _submit(app, studio, packet, replies)
            assert replay.get('ok') is True
            assert _result(replay, call)['status'] == 'confirmed'
            assert len(trace.calls) == len(trace.issued) == 1
            assert len([state for state in studio.store().list(WORKSPACE) if state.open]) == 2
        finally:
            release.set()
            await asyncio.gather(first, *(tuple([join]) if join is not None else ()), return_exceptions=True)
    asyncio.run(go())


@pytest.mark.parametrize('sequence', [True, False, 0, -1, 1.5, '1', 2 ** 53])
def test_canonical_sequence_type_refuses_before_admission(tmp_path, monkeypatch, sequence):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    replies = _offers(studio, monkeypatch)

    async def go():
        await _accepted_hello(app, studio)
        iid, _, _ = await open_window(app, entry)
        trace = Trace(app, monkeypatch)
        packet = _canonical(studio, iid, sequence)
        state = controlled_state(studio.store().get(WORKSPACE, iid))
        refused = await _submit(app, studio, packet, replies)
        assert refused.get('ok') is False, 'BEHAVIOR: manual sequence is a positive safe integer, never coercible authority'
        assert not trace.issued and not trace.calls
        assert controlled_state(studio.store().get(WORKSPACE, iid)) == state
        valid = _canonical(studio, iid, 1)
        accepted = await _submit(app, studio, valid, replies)
        assert accepted.get('ok') is True, 'MISSING_FEATURE: invalid transport sequence must not consume a valid intent'
        await _settled(trace.original(valid))
        assert _horizon(accepted, 1)['highestManualSeq'] == 1
    asyncio.run(go())


def test_canonical_gap_delayed_lower_sequence_cannot_become_new_work(tmp_path, monkeypatch):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    replies = _offers(studio, monkeypatch)

    async def go():
        await _accepted_hello(app, studio)
        iid, _, _ = await open_window(app, entry)
        trace = Trace(app, monkeypatch)
        packet = _canonical(studio, iid, 9)
        accepted = await _submit(app, studio, packet, replies)
        assert accepted.get('ok') is True, 'MISSING_FEATURE: a positive canonical gap is useful'
        await _settled(trace.original(packet))
        before = controlled_state(studio.store().get(WORKSPACE, iid))
        delayed = _canonical(studio, iid, 8, level=3)
        refused = await _submit(app, studio, delayed, replies)
        assert refused.get('ok') is False, 'BEHAVIOR: unseen lower sequence is not a fresh operation'
        assert _horizon(refused, 8)['highestManualSeq'] == 9
        assert len(trace.issued) == len(trace.calls) == 1
        assert controlled_state(studio.store().get(WORKSPACE, iid)) == before
    asyncio.run(go())


@pytest.mark.parametrize('boundary', ['workspace-reset', 'day-counter'])
def test_canonical_reset_preserves_horizon_and_fresh_higher_intent(tmp_path, monkeypatch, boundary):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    replies = _offers(studio, monkeypatch)

    async def go():
        await _accepted_hello(app, studio)
        iid, _, _ = await open_window(app, entry)
        trace = Trace(app, monkeypatch)
        packet = _canonical(studio, iid, 10)
        accepted = await _submit(app, studio, packet, replies)
        assert accepted.get('ok') is True, 'MISSING_FEATURE: canonical control before reset'
        await _settled(trace.original(packet))
        if boundary == 'workspace-reset':
            studio.store().reset(WORKSPACE)
            # A reset empties the actual registry. The synthetic browser now
            # observes that absence; old prepared leases are not reused.
            studio.items.clear()
            studio.stack.clear()
            studio.command_results.clear()
            await studio.send_report()
        else:
            tickets.generations(app.hub).bump('session', why='external day boundary')
        count = len(trace.issued)
        refused = await _submit(app, studio, packet, replies)
        assert refused.get('ok') is False, 'BEHAVIOR: reset cannot reopen old transport admission'
        assert _horizon(refused, 10)['highestManualSeq'] == 10
        assert len(trace.issued) == count
        if boundary == 'workspace-reset':
            iid, _, _ = await open_window(app, entry)
        await studio.send_report()
        fresh = _canonical(studio, iid, 11, level=3)
        useful = await _submit(app, studio, fresh, replies)
        assert useful.get('ok') is True, 'BEHAVIOR: a fresh higher sequence can use the actual post-reset pin'
        assert _horizon(useful, 11)['highestManualSeq'] == 11
        assert studio.store().get(WORKSPACE, iid).view.zoom == 3
    asyncio.run(go())


@pytest.mark.parametrize('replace_socket', [False, True], ids=['originating-late-reply', 'no-replacement-late-reply'])
def test_late_manual_reply_keeps_original_socket_occurrence(tmp_path, monkeypatch, replace_socket):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    replies = _offers(studio, monkeypatch)
    reached, release = _source_barrier(app, monkeypatch, entry)

    async def go():
        trace = Trace(app, monkeypatch)
        packet = _legacy(studio, source=address(entry), correlation='actual-late-source')
        task = asyncio.create_task(_submit(app, studio, packet, replies))
        peer_replies = []
        try:
            await _until(lambda: reached.is_set() or task.done(), 'MISSING_FEATURE: original late source preparation')
            assert reached.is_set(), 'MISSING_FEATURE: real retained-source reader reached'
            call = trace.original(packet)
            await _until(task.done, 'BEHAVIOR: actual inline policy returns while original work stays pending')
            pending = await task
            assert call['run'].ok is None and not call['ticket'].closed
            assert any(row.get('tool') == 'present' and row.get('ok', False) is None
                       for row in _records(pending)), \
                'BEHAVIOR: inline reply must project its actual pending Registry run'
            before = len(replies)
            if replace_socket:
                peer = AppStudio(app)
                peer_replies = _offers(peer, monkeypatch)
                app.accept_workspace(peer, ClientRef(WORKSPACE, CLIENT['clientId'], CLIENT['pageEpoch'], 'actual-later-socket'))
            release.set()
            await _settled(call)
            if replace_socket:
                await asyncio.sleep(0)
                assert len(replies) == before and not peer_replies, 'BEHAVIOR: revoked origin cannot route a late receipt'
                assert call['run'].ok is False
            else:
                outcome = call['run'].wire()['outcome']
                await _until(lambda: any(outcome in list(_records(reply)) for reply in replies[before:]),
                             'MISSING_FEATURE: late result must project the original real ToolRun on its originating socket')
                late = [reply for reply in replies[before:] if outcome in list(_records(reply))]
                assert late and all(reply.get('requestId') == packet['requestId'] for reply in late)
                assert outcome['status'] == 'confirmed'
            assert len(trace.calls) == len(trace.issued) == 1
            assert call['ctx'].request_snapshot.observation.wire() == call['pin_wire']
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(go())


def test_canonical_pending_limit_refuses_before_ticket_and_recovers_with_fresh_seq(tmp_path, monkeypatch):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    replies = _offers(studio, monkeypatch)

    async def go():
        await _accepted_hello(app, studio)
        windows = [(await open_window(app, entry))[0] for _ in range(5)]
        trace = Trace(app, monkeypatch)
        keep, held = studio.keep_results, {}

        def hold(results):
            for result in results:
                held[result['commandId']] = copy.deepcopy(result)

        monkeypatch.setattr(studio, 'keep_results', hold)
        packets = [_canonical(studio, iid, n) for n, iid in enumerate(windows[:4], 1)]
        tasks = [asyncio.create_task(_submit(app, studio, packet, replies)) for packet in packets]
        try:
            await _until(lambda: len(held) == 4 or all(task.done() for task in tasks),
                         'MISSING_FEATURE: four actual original native renderer waits')
            assert len(held) == 4, 'MISSING_FEATURE: each accepted pending intent must reach the real renderer'
            originals = [trace.original(packet, index) for index, packet in enumerate(packets)]
            assert all(not call['run'].task.done() for call in originals)
            assert len(trace.issued) == 4
            denied_packet = _canonical(studio, windows[4], 5)
            pins, reads = len(trace.pins), len(trace.reads)
            untouched = controlled_state(studio.store().get(WORKSPACE, windows[4]))
            refused = await _submit(app, studio, denied_packet, replies)
            assert refused.get('ok') is False, 'BEHAVIOR: fifth pending original must refuse before admission'
            assert any(row.get('notAdmitted') is True for row in _records(refused))
            assert _horizon(refused, 5)['highestManualSeq'] == 5
            assert len(trace.issued) == len(trace.calls) == 4
            assert len(trace.pins) == pins and len(trace.reads) == reads
            assert controlled_state(studio.store().get(WORKSPACE, windows[4])) == untouched
            monkeypatch.setattr(studio, 'keep_results', keep)
            keep(list(held.values()))
            await studio.send_report()
            await asyncio.gather(*tasks)
            for original in originals:
                await _settled(original)
            repeated = await _submit(app, studio, denied_packet, replies)
            assert repeated.get('ok') is False and len(trace.issued) == 4
            fresh = _canonical(studio, windows[4], 6, level=3)
            useful = await _submit(app, studio, fresh, replies)
            assert useful.get('ok') is True, 'BEHAVIOR: released capacity permits fresh revalidated intent'
            await _settled(trace.original(fresh, 4))
            assert _horizon(useful, 6)['highestManualSeq'] == 6
        finally:
            monkeypatch.setattr(studio, 'keep_results', keep)
            if held:
                keep(list(held.values()))
                await studio.send_report()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(go())


@pytest.mark.parametrize('boundary', ['cancel', 'closed-ticket', 'session-counter', 'session-store', 'workspace-reset', 'disconnect', 'replacement', 'same-wire-reaccept'])
def test_pending_original_manual_request_cannot_cross_revocation(tmp_path, monkeypatch, boundary):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    replies = _offers(studio, monkeypatch)
    reached, release = _source_barrier(app, monkeypatch, entry)

    async def go():
        trace = Trace(app, monkeypatch)
        packet = _legacy(studio, source=address(entry), correlation=boundary)
        task = asyncio.create_task(_submit(app, studio, packet, replies))
        try:
            await _until(lambda: reached.is_set() or task.done(), 'MISSING_FEATURE: manual preparation boundary')
            assert reached.is_set(), 'MISSING_FEATURE: original native caller must reach held real source'
            call = trace.original(packet)
            if boundary == 'cancel': call['ticket'].cancel('external original command cancellation')
            elif boundary == 'closed-ticket': call['ticket'].close()
            elif boundary == 'session-counter': tickets.generations(app.hub).bump('session', why='external session change')
            elif boundary == 'session-store': app.new_session()  # actual store open/generation and App listeners
            elif boundary == 'workspace-reset': studio.store().reset(WORKSPACE)
            elif boundary == 'disconnect': app.hub.screen.gone(studio)
            else:
                ref = ClientRef(**{'workspace_id': WORKSPACE, 'client_id': CLIENT['clientId'],
                    'page_epoch': CLIENT['pageEpoch'], 'connection_epoch': studio.client_ref['connectionEpoch']})
                if boundary == 'replacement':
                    peer = AppStudio(app)
                    _offers(peer, monkeypatch)
                    app.accept_workspace(peer, ClientRef(WORKSPACE, CLIENT['clientId'], CLIENT['pageEpoch'], 'external-new-server-epoch'))
                else:
                    app.accept_workspace(studio, ref)  # same wire epoch, new actual accepted profile
            release.set()
            await task
            await _settled(call)
            assert call['run'].ok is False, 'BEHAVIOR: stale manual request cannot commit after real revocation'
            assert not [state for state in studio.store().list(WORKSPACE) if state.open]
            assert call['ctx'].request_snapshot.observation.wire() == call['pin_wire']
            assert len(trace.calls) == len(trace.issued) == 1
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(go())


def test_canonical_window_slides_and_expired_replay_never_issues_fresh_work(tmp_path, monkeypatch):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    replies = _offers(studio, monkeypatch)

    async def go():
        await _accepted_hello(app, studio)
        iid, _, _ = await open_window(app, entry)
        trace = Trace(app, monkeypatch)
        original = _canonical(studio, iid, 1)
        last = None
        for n in range(1, 70):  # beyond the approved 32 retained originals, no private association helper
            await studio.send_report()
            packet = original if n == 1 else _canonical(studio, iid, n)
            last = await _submit(app, studio, packet, replies)
            assert last.get('ok') is True, 'MISSING_FEATURE: canonical stream must remain useful beyond its retained window'
            await _settled(trace.original(packet, n - 1))
            assert _horizon(last, n)['highestManualSeq'] == n
        assert _horizon(last, 69)['expiredThroughManualSeq'] >= 1
        count = len(trace.issued)
        pins, reads = len(trace.pins), len(trace.reads)
        state = controlled_state(studio.store().get(WORKSPACE, iid))
        refused = await _submit(app, studio, original, replies)
        assert refused.get('ok') is False, 'BEHAVIOR: expired relative replay cannot become fresh intent'
        assert _horizon(refused, 1)['highestManualSeq'] == 69
        assert len(trace.issued) == len(trace.calls) == count
        assert len(trace.pins) == pins and len(trace.reads) == reads
        assert controlled_state(studio.store().get(WORKSPACE, iid)) == state
        fresh = _canonical(studio, iid, 70, level=3)
        accepted = await _submit(app, studio, fresh, replies)
        assert accepted.get('ok') is True
        assert studio.store().get(WORKSPACE, iid).view.zoom == 3
    asyncio.run(go())


def test_pending_prefix_capacity_refusal_consumes_seq_then_fresh_intent_recovers(tmp_path, monkeypatch):
    app, studio = built(tmp_path)
    _receipt_schedule(app, monkeypatch)
    ready_entry, _ = saved_picture(app, 40)
    replies = _offers(studio, monkeypatch)

    async def go():
        await _accepted_hello(app, studio)
        iid, _, _ = await open_window(app, ready_entry)
        sibling, _, _ = await open_window(app, ready_entry)
        trace = Trace(app, monkeypatch)
        gate, held_receipts = asyncio.Event(), []
        keep = studio.keep_results

        def hold_original_receipt(results):
            # Withhold only the receipt the unchanged synthetic renderer really
            # generated. Later actual Screen reports carry sibling receipts;
            # neither state nor a receipt is manufactured for the original.
            if trace.calls and results and results[0]['actionId'] == trace.calls[0]['run'].occurrence.action_id:
                held_receipts[:] = copy.deepcopy(results)
                gate.set()
            else:
                keep(results)

        monkeypatch.setattr(studio, 'keep_results', hold_original_receipt)
        first_packet = _canonical(studio, iid, 1)
        first = asyncio.create_task(_submit(app, studio, first_packet, replies))
        try:
            await _until(lambda: gate.is_set() or first.done(), 'MISSING_FEATURE: actual canonical renderer ingress')
            assert gate.is_set(), 'MISSING_FEATURE: canonical first command must reach the real renderer report seam'
            original = trace.original(first_packet)
            for n in range(2, 33):
                await studio.send_report()
                packet = _canonical(studio, sibling, n)
                accepted = await _submit(app, studio, packet, replies)
                assert accepted.get('ok') is True, 'BEHAVIOR: a held original cannot lock useful sibling controls'
                await _settled(trace.calls[-1])
            count = len(trace.issued)
            pins, reads = len(trace.pins), len(trace.reads)
            assert not original['run'].task.done(), 'SETUP: capacity requires the actual original to remain pending'
            refused_packet = _canonical(studio, sibling, 33)
            refused = await _submit(app, studio, refused_packet, replies)
            assert refused.get('ok') is False, 'BEHAVIOR: pending prefix cannot be evicted to admit over-capacity work'
            assert any(row.get('notAdmitted') is True for row in _records(refused))
            assert _horizon(refused, 33)['highestManualSeq'] == 33
            assert _horizon(refused, 33)['expiredThroughManualSeq'] == 0
            assert len(trace.issued) == count
            assert len(trace.pins) == pins and len(trace.reads) == reads
            assert not original['run'].task.done(), 'BEHAVIOR: pending first receipt remains retained at capacity'
            monkeypatch.setattr(studio, 'keep_results', keep)
            keep(held_receipts)
            # Re-observe the original rendered command in the current report.
            # Its withheld acknowledgment named an older report sequence; merely
            # copying it into this report cannot confirm the zoom. The renderer's
            # replay path updates observation identity without applying it again.
            await studio.observe_command(studio.rendered_commands[held_receipts[0]['commandId']])
            await first
            await _settled(original)
            assert original['run'].ok is True, original['run'].summary
            expired = await _submit(app, studio, refused_packet, replies)
            assert expired.get('ok') is False and len(trace.issued) == count
            fresh = _canonical(studio, sibling, 34)
            recovered = await _submit(app, studio, fresh, replies)
            assert recovered.get('ok') is True, 'BEHAVIOR: definitive nonadmission permits a fresh revalidated intent'
            assert _horizon(recovered, 34)['highestManualSeq'] == 34
        finally:
            monkeypatch.setattr(studio, 'keep_results', keep)
            if held_receipts:
                keep(held_receipts)
                await studio.send_report()
            await asyncio.gather(first, return_exceptions=True)
    asyncio.run(go())


@pytest.mark.parametrize('replace_epoch', [False, True], ids=['same-wire-profile-reaccept', 'fresh-server-incarnation'])
def test_canonical_reacceptance_cannot_reset_old_sequence_authority(tmp_path, monkeypatch, replace_epoch):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    replies = _offers(studio, monkeypatch)

    async def go():
        transport = await _accepted_hello(app, studio)
        iid, _, _ = await open_window(app, entry)
        trace = Trace(app, monkeypatch)
        packet = _canonical(studio, iid, 1)
        first = await _submit(app, studio, packet, replies)
        assert first.get('ok') is True, 'MISSING_FEATURE: actual canonical manual operation'
        await _settled(trace.original(packet))
        count = len(trace.issued)
        old_client = dict(studio.client_ref)
        if replace_epoch:
            transport.gone(studio)
            transport = await _accepted_hello(app, studio)
            assert studio.client_ref['connectionEpoch'] != old_client['connectionEpoch']
        else:
            app.accept_workspace(studio, ClientRef(WORKSPACE, old_client['clientId'], old_client['pageEpoch'], old_client['connectionEpoch']))
        refused = await _submit(app, studio, packet, replies)
        assert refused.get('ok') is False, 'BEHAVIOR: reacceptance cannot reinterpret old sequence as fresh'
        assert len(trace.issued) == count
        if replace_epoch:
            studio.store().reset(WORKSPACE)
            studio.items.clear()
            studio.stack.clear()
            studio.command_results.clear()
            await studio.send_report()
            iid, _, _ = await open_window(app, entry)
            fresh = _canonical(studio, iid, 1, level=3)
            next_index = len(trace.calls)
            useful = await _submit(app, studio, fresh, replies)
            assert useful.get('ok') is True, 'BEHAVIOR: genuinely new server incarnation can start its own sequence'
            await _settled(trace.original(fresh, next_index))
            assert _horizon(useful, 1)['highestManualSeq'] == 1
        else:
            higher = _canonical(studio, iid, 2)
            assert (await _submit(app, studio, higher, replies)).get('ok') is False, \
                'BEHAVIOR: a reaccepted identical server epoch remains revoked, not a reset H/F namespace'
            assert len(trace.issued) == count
    asyncio.run(go())


@pytest.mark.parametrize('boundary', ['cancel', 'closed-ticket', 'disconnect'])
def test_original_manual_renderer_wait_cannot_confirm_after_revocation(tmp_path, monkeypatch, boundary):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    replies = _offers(studio, monkeypatch)

    async def go():
        await _accepted_hello(app, studio)
        iid, _, _ = await open_window(app, entry)
        trace = Trace(app, monkeypatch)
        gate, receipts = asyncio.Event(), []
        keep = studio.keep_results

        def hold(results):
            receipts[:] = copy.deepcopy(results)
            gate.set()

        monkeypatch.setattr(studio, 'keep_results', hold)
        packet = _canonical(studio, iid, 1)
        task = asyncio.create_task(_submit(app, studio, packet, replies))
        try:
            await _until(lambda: gate.is_set() or task.done(), 'MISSING_FEATURE: actual manual renderer receipt boundary')
            assert gate.is_set(), 'MISSING_FEATURE: native manual command must reach actual renderer'
            call = trace.original(packet)
            assert not call['run'].task.done()
            if boundary == 'cancel': call['ticket'].cancel('external cancellation before observed receipt')
            elif boundary == 'closed-ticket': call['ticket'].close()
            else: app.hub.screen.gone(studio)
            monkeypatch.setattr(studio, 'keep_results', keep)
            keep(receipts)
            await studio.send_report()
            await task
            await _settled(call)
            assert call['run'].ok is False, 'BEHAVIOR: genuine but late renderer facts cannot restore revoked forward authority'
            outcome = call['run'].wire().get('outcome')
            assert outcome and outcome['status'] != 'confirmed'
            assert len(trace.calls) == len(trace.issued) == 1
            assert call['ctx'].request_snapshot.observation.wire() == call['pin_wire']
        finally:
            monkeypatch.setattr(studio, 'keep_results', keep)
            if receipts:
                keep(receipts)
                await studio.send_report()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(go())


def test_manual_window_activity_is_inspectable_with_original_browser_owner(tmp_path, monkeypatch):
    from orchestrator.execution import ExecutionLog
    app, studio = built(tmp_path)
    app.hub.execution = ExecutionLog(enabled=True)
    entry, _ = saved_picture(app)
    replies = _offers(studio, monkeypatch)
    async def go():
        reply = await _submit(app, studio, _legacy(studio, source=address(entry)), replies)
        assert reply['ok'] is True
    asyncio.run(go())
    roots = app.hub.execution.references()
    root = next(row for row in roots if row['kind'] == 'present')
    detail = app.hub.execution.inspect(root['id'])
    assert detail['context']['owner'] == studio.client_ref
    assert any(row['operation'] == 'tool:present' and row['outcome'] == 'completed' for row in detail['steps'])
    assert 'words' not in detail['context']  # a button click is not a fabricated spoken request


def test_manual_group_requires_each_selected_windows_original_fence(tmp_path, monkeypatch):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    replies = _offers(studio, monkeypatch)

    async def go():
        await _accepted_hello(app, studio)
        first, _, _ = await open_window(app, entry)
        second, _, _ = await open_window(app, entry)
        unrelated, _, _ = await open_window(app, entry)
        packet = _canonical(studio, first, 1)
        packet.update(op='group', targets=[first, second], values={})
        packet['base'][second] = _canonical(studio, second, 1)['base'][second]
        stale = copy.deepcopy(packet)
        stale['base'][second]['propertyVersions']['group'] += 1
        refused = await _submit(app, studio, stale, replies)
        assert refused.get('ok') is False
        assert studio.store().get(WORKSPACE, first).view.group is None
        # A fresh sequence follows the refused occurrence; no success hunting.
        packet.update(manualSeq=2, requestId='browser/manual/2', actionId='browser/action/2', commandId='browser/command/2')
        accepted = await _submit(app, studio, packet, replies)
        assert accepted.get('ok') is True, accepted
        one, two = (studio.store().get(WORKSPACE, iid) for iid in (first, second))
        assert one.view.group and one.view.group == two.view.group
        assert one.property_versions['group'] == two.property_versions['group'] == 2
        assert studio.store().get(WORKSPACE, unrelated).view.group is None
        native = [m for m in studio.messages if m.get('op') == 'group']
        assert native and all(m['client'] == studio.client_ref and set(m['targets']) == {first, second} for m in native)
        before = {iid: studio.items[iid]['rect'][:2] for iid in (first, second, unrelated)}
        move = _canonical(studio, first, 3)
        move.update(op='move', values={'position': [before[first][0] + 40, before[first][1] + 30]})
        assert (await _submit(app, studio, move, replies)).get('ok') is True
        for iid in (first, second):
            assert studio.items[iid]['rect'][:2] == [before[iid][0] + 40, before[iid][1] + 30]
        assert studio.items[unrelated]['rect'][:2] == before[unrelated]
        separate = _canonical(studio, second, 4)
        separate.update(op='ungroup', values={})
        assert (await _submit(app, studio, separate, replies)).get('ok') is True
        assert all(studio.store().get(WORKSPACE, iid).view.group is None for iid in (first, second, unrelated))

    asyncio.run(go())


@pytest.mark.parametrize('bad', ['missing-base', 'stale-lifetime', 'not-observed', 'foreign-origin', 'duplicate'])
def test_manual_group_rejects_unowned_or_incomplete_companions_atomically(tmp_path, monkeypatch, bad):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    replies = _offers(studio, monkeypatch)

    async def go():
        await _accepted_hello(app, studio)
        first, _, _ = await open_window(app, entry)
        second, _, _ = await open_window(app, entry)
        packet = _canonical(studio, first, 1)
        packet.update(op='group', targets=[first, second], values={})
        packet['base'][second] = _canonical(studio, second, 1)['base'][second]
        if bad == 'missing-base':
            del packet['base'][second]
        elif bad == 'stale-lifetime':
            packet['base'][second]['lifetimeGeneration'] = 'previous-window-lifetime'
        elif bad == 'not-observed':
            studio.items.pop(second)
            await studio.send_report()
        elif bad == 'foreign-origin':
            packet['client']['clientId'] = 'another-browser'
        else:
            packet['targets'] = [first, first]
        before = [controlled_state(studio.store().get(WORKSPACE, iid)) for iid in (first, second)]
        response = await _submit(app, studio, packet, replies)
        assert response.get('ok') is False, response
        assert [controlled_state(studio.store().get(WORKSPACE, iid)) for iid in (first, second)] == before
        assert not any(m.get('op') == 'group' for m in studio.messages)

    asyncio.run(go())


def test_failed_admission_delivery_releases_ticket_before_any_native_work(tmp_path, monkeypatch):
    from orchestrator.brain.registry import ToolFailed
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    async def go():
        trace = Trace(app, monkeypatch)
        def disconnected(message):
            raise ConnectionError('controlled transport closure')
        monkeypatch.setattr(studio, 'offer', disconnected)
        packet = _legacy(studio, source=address(entry), correlation='failed-delivery')
        with pytest.raises(ToolFailed, match='could not receive'):
            await COMMANDS['present'](app, studio, packet)
        assert len(trace.issued) == 1 and trace.issued[0].closed
        assert not trace.calls and not app.manual_commands.pending
        replies = _offers(studio, monkeypatch)
        packet = _legacy(studio, source=address(entry), correlation='corrected-delivery')
        result = await _submit(app, studio, packet, replies)
        assert result['ok']
        await _settled(trace.original(packet))
    asyncio.run(go())


def test_inline_rich_observation_keeps_its_own_budget_and_manual_payload_stays_bounded(tmp_path, monkeypatch):
    from orchestrator.commands import _manual_proposal
    from orchestrator.workspace_observation import Limits

    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    replies = _offers(studio, monkeypatch)
    seen = []
    report = app.hub.screen.report

    def capture(payload, **kwargs):
        seen.append(copy.deepcopy(payload))
        return report(payload, **kwargs)

    monkeypatch.setattr(app.hub.screen, 'report', capture)

    async def go():
        await _accepted_hello(app, studio)
        for _ in range(11):
            iid, _, _ = await open_window(app, entry)
        await studio.send_report()
        packet = _canonical(studio, iid, 1)
        packet['op'], packet['values'] = 'resize', {'size': [400, 280]}
        packet['screen'] = seen[-1]
        size = len(json.dumps(packet['screen'], separators=(',', ':')).encode())
        assert 65536 < size <= Limits().wire_bytes
        answer = await _submit(app, studio, packet, replies)
        assert answer.get('ok') is True, answer
        assert studio.store().get(WORKSPACE, iid).geometry_intent.size == (400, 280)
        with pytest.raises(ObservationError, match='64 KiB'):
            _manual_proposal({**packet, 'values': {'padding': 'x' * 65536}})
        with pytest.raises(ObservationError, match='observation budget'):
            _manual_proposal({**packet, 'screen': {'padding': 'x' * Limits().wire_bytes}})
    asyncio.run(go())
