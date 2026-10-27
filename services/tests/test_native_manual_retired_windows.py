"""TEST-only retirement budgets; real manual originals and transport lifecycle.

Synthetic retained JPEGs, socket offers and renderer receipt withholding only.
No server/model/camera/browser, substituted executor/cursor/entry/receipt, altered
capacity, delayed guard or artificial pending original. Root owns execution.
"""
import asyncio
import json
import copy

import cv2
import numpy as np

from orchestrator.app import App
from orchestrator.brain.actions import ActionRunner
from orchestrator.brain.registry import ToolContext
from orchestrator.capture import RUN
from orchestrator.paths import Paths
from orchestrator.ticket import issue, using
from orchestrator.workspace_observation import ClientRef
from orchestrator.workspace_transport import WorkspaceTransport
from tests.test_native_manual_ingress import (
    Trace, _accepted_hello, _canonical, _horizon, _offers, _records,
    _settled, _submit, _until, _receipt_schedule,
)
from tests.test_workspace_actions_app import (
    AppStudio, CONTROL_PROPERTY, DISPLAY_KIND, address, built, digest, jpeg,
    open_window, output_window, saved_picture,
)


def _reachable_canonical(owner):
    """Read only actual owner reachability, including retired cursors of pending work."""
    cursors = {id(cursor): cursor for cursor in owner.cursors.values()}
    for entry in owner.pending.values():
        cursors[id(entry.cursor)] = entry.cursor
    entries = {}
    for cursor in cursors.values():
        for entry in cursor.entries.values():
            if entry.sequence is not None:
                entries[id(entry)] = entry
    for entry in owner.pending.values():
        if entry.sequence is not None:
            entries[id(entry)] = entry
    return tuple(entries.values())


def _budget(owner):
    entries = _reachable_canonical(owner)
    pending_cursors = {id(entry.cursor): entry.cursor for entry in owner.pending.values()}
    return {'canonical': len(entries), 'pending': len(owner.pending),
            'pending_per_origin': max((sum(entry.cursor is cursor for entry in owner.pending.values())
                                       for cursor in pending_cursors.values()), default=0),
            'legacy': len(owner.legacy),
            'current': sum(len(cursor.entries) for cursor in owner.cursors.values()),
            'retired': sum(entry.cursor.revoked for entry in entries),
            'settled_retired': sum(entry.cursor.revoked and entry.settled for entry in entries)}


async def _held_prefix(app, studio, monkeypatch, *, transport=None, packet_factory=_canonical,
                       window_opener=None):
    """The existing 633-698 receipt schedule, without early release on retirement."""
    _receipt_schedule(app, monkeypatch)
    replies = _offers(studio, monkeypatch)
    if transport is None:
        transport = await _accepted_hello(app, studio)
    entry, _ = saved_picture(app, 40)
    open_one = window_opener or (lambda: open_window(app, entry))
    target = (await open_one())[0]
    sibling = (await open_one())[0]
    trace = Trace(app, monkeypatch)
    keep, held, gate = studio.keep_results, [], asyncio.Event()

    def hold_original_receipt(results):
        if trace.calls and results and results[0]['actionId'] == trace.calls[0]['run'].occurrence.action_id:
            held[:] = copy.deepcopy(results)  # received renderer evidence, never an invented ACK
            gate.set()
        else:
            keep(results)

    monkeypatch.setattr(studio, 'keep_results', hold_original_receipt)
    packet = packet_factory(studio, target, 1)
    first = asyncio.create_task(_submit(app, studio, packet, replies))
    try:
        await _until(lambda: gate.is_set() or first.done(), 'actual held original renderer boundary')
        assert gate.is_set(), 'SETUP: original must reach the unchanged renderer receipt seam'
        original = trace.original(packet)
        for n in range(2, 33):
            await studio.send_report()
            sibling_packet = packet_factory(studio, sibling, n)
            reply = await _submit(app, studio, sibling_packet, replies)
            assert reply.get('ok') is True, json.dumps(reply)
            await _settled(trace.calls[-1])
        # Capacity is meaningful only while the deliberately withheld original
        # is still pending. Report its real terminal facts if the fixture outran
        # the unchanged renderer deadline; never mislabel that as over-admission.
        held_run = original['run']
        assert not held_run.task.done(), {
            'setup': 'original settled before command 33 capacity assertion',
            'ok': held_run.ok, 'summary': held_run.summary, 'ms': held_run.ms,
            'outcome': held_run.outcome,
            'stillOwned': original['ticket'] in app.manual_commands.pending,
        }
        counts = len(trace.issued), len(trace.calls), len(trace.pins), len(trace.reads)
        refused_packet = packet_factory(studio, sibling, 33)
        refused = await _submit(app, studio, refused_packet, replies)
        assert refused.get('ok') is False and any(row.get('notAdmitted') is True for row in _records(refused))
        assert _horizon(refused, 33)['highestManualSeq'] == 33
        assert _horizon(refused, 33)['expiredThroughManualSeq'] == 0
        assert (len(trace.issued), len(trace.calls), len(trace.pins), len(trace.reads)) == counts
        assert not original['run'].task.done(), 'SETUP: original timed out before the retirement boundary'
        actual = app.manual_commands.pending[original['ticket']]
        assert actual.context is original['ctx'] and actual.context.steps.runs[1] is original['run']
        assert len(actual.cursor.entries) == 32
        assert sum(entry.settled for entry in actual.cursor.entries.values()) == 31
        return {'transport': transport, 'trace': trace, 'replies': replies, 'keep': keep,
                'held': held, 'packet': packet, 'first': first, 'original': original,
                'entry': actual, 'cursor': actual.cursor, 'sibling': sibling}
    except BaseException:
        # A schedule failure still retires real work; never turn it into a fake RED.
        monkeypatch.setattr(studio, 'keep_results', keep)
        transport.gone(studio)
        app.hub.clients.discard(studio)
        await asyncio.gather(first, return_exceptions=True)
        if trace.calls:
            await _settled(trace.calls[0])
        raise


def _retired_original(app, held):
    """Synchronous observation: no await between genuine gone and these checks."""
    original, actual, cursor = held['original'], held['entry'], held['cursor']
    assert app.manual_commands.pending[original['ticket']] is actual
    assert actual.cursor is cursor and cursor.revoked and not actual.settled
    assert actual.ticket is original['ticket'] and actual.context is original['ctx']
    assert original['ticket'].cancelled and not original['ticket'].closed
    assert not original['run'].task.done()
    assert actual.context.steps.runs[1] is original['run']
    assert original['ctx'].request_snapshot is original['pin']
    assert original['pin'].observation.wire() == original['pin_wire']
    assert original['ctx'].steps.occurrences[1] == original['run'].occurrence
    assert held['held'], 'SETUP: actual original renderer receipt remains external'


async def _finish_retired(app, studio, held, monkeypatch):
    monkeypatch.setattr(studio, 'keep_results', held['keep'])
    await asyncio.gather(held['first'], return_exceptions=True)
    await _settled(held['original'])
    await _until(lambda: held['original']['ticket'] not in app.manual_commands.pending,
                 'actual retired original must release its pending owner after settlement')
    assert held['original']['run'].ok is False
    assert (held['original']['run'].wire().get('outcome') or {}).get('status') != 'confirmed'


def test_retired_canonical_window_drops_settled_siblings_keeps_pending_original(tmp_path, monkeypatch):
    """Catches settled replay windows retained by revoked pending originals."""
    app, studio = built(tmp_path)

    async def go():
        held = await _held_prefix(app, studio, monkeypatch)
        try:
            held['transport'].gone(studio)
            app.hub.clients.discard(studio)
            _retired_original(app, held)
            # Actual original work survives; its unnecessary settled siblings do not.
            retained = [entry.sequence for entry in held['cursor'].entries.values() if entry.settled]
            assert retained == [], f'retired pending cursor retains settled canonical siblings: {retained}'
            assert _budget(app.manual_commands)['canonical'] <= 256
            assert _budget(app.manual_commands)['legacy'] == 0
        finally:
            held['transport'].gone(studio)
            app.hub.clients.discard(studio)
            await _finish_retired(app, studio, held, monkeypatch)

    asyncio.run(go())


def test_identical_epoch_keeps_revoked_horizons_and_fresh_transport_is_useful(tmp_path, monkeypatch):
    """Catches reacceptance resetting old authority or making a fresh stream unusable."""
    app, studio = built(tmp_path)

    async def go():
        held = await _held_prefix(app, studio, monkeypatch)
        transport, trace = held['transport'], held['trace']
        old_ref = dict(studio.client_ref)
        try:
            transport.gone(studio)
            app.hub.clients.discard(studio)
            _retired_original(app, held)
            app.accept_workspace(studio, ClientRef(old_ref['workspaceId'], old_ref['clientId'],
                                                  old_ref['pageEpoch'], old_ref['connectionEpoch']))
            actual_cursor = app.manual_commands.cursors[old_ref['workspaceId']]
            assert actual_cursor is held['cursor'] and actual_cursor.revoked
            assert (actual_cursor.highest, actual_cursor.expired) == (33, 0)
            counts = len(trace.issued), len(trace.calls), len(trace.pins), len(trace.reads)
            for packet in (held['packet'], _canonical(studio, held['sibling'], 34)):
                refused = await _submit(app, studio, packet, held['replies'])
                assert refused.get('ok') is False, 'identical retired epoch cannot acquire fresh sequence authority'
            assert (len(trace.issued), len(trace.calls), len(trace.pins), len(trace.reads)) == counts
            assert (actual_cursor.highest, actual_cursor.expired) == (33, 0)
            # Revocation settles the real original; no delayed callback/Ticket close is invented.
            transport.gone(studio)
            await _finish_retired(app, studio, held, monkeypatch)
            fresh_transport = await _accepted_hello(app, studio)
            held['transport'] = fresh_transport
            assert studio.client_ref['connectionEpoch'] != old_ref['connectionEpoch']
            studio.store().reset(old_ref['workspaceId'])
            studio.items.clear()
            studio.stack.clear()
            studio.command_results.clear()
            await studio.send_report()
            entry, _ = saved_picture(app, 190)
            iid, _, _ = await open_window(app, entry)
            next_index = len(trace.calls)
            fresh = _canonical(studio, iid, 1, level=3)
            useful = await _submit(app, studio, fresh, held['replies'])
            assert useful.get('ok') is True, useful
            call = trace.original(fresh, next_index)
            await _settled(call)
            assert _horizon(useful, 1)['highestManualSeq'] == 1
            assert _horizon(useful, 1)['expiredThroughManualSeq'] == 0
            assert call['run'].occurrence != held['original']['run'].occurrence
            assert call['ticket'] is not held['original']['ticket']
            assert held['original']['run'].ok is False
            assert actual_cursor.revoked and (actual_cursor.highest, actual_cursor.expired) == (33, 0)
            assert len(app.manual_commands.legacy) == 0
        finally:
            held['transport'].gone(studio)
            app.hub.clients.discard(studio)
            await _finish_retired(app, studio, held, monkeypatch)

    asyncio.run(go())


class ScopedStudio(AppStudio):
    """TEST-local copy of the frozen renderer's command projection for eight origins.

    The observe_command body below differs only in WORKSPACE -> self.workspace_id.
    Original descriptors, fences, retained payloads and receipt rules stay intact.
    No production state or source grant is authored by this renderer.
    """
    def __init__(self, app, workspace):
        super().__init__(app)
        self.workspace_id = workspace
        self.client_ref = {'workspaceId': workspace, 'clientId': 'client:' + workspace,
                           'pageEpoch': 'page:' + workspace, 'connectionEpoch': 'unaccepted'}

    async def send_report(self, user=()):
        await super().send_report(user)
        observed = self.app.hub.screen.observation(self)
        assert observed is not None and observed.wire()['seq'] == self.seq, \
            'SETUP: scoped renderer report must pass actual accepted normalization'

    async def observe_command(self, message):
        assert message.get('schema') == 'presentation-command-v2', 'MISSING_FEATURE: generic command needs the canonical renderer schema'
        fence = message.get('fence')
        assert isinstance(fence, dict), 'MISSING_FEATURE: UUID command must expose its causal fence'
        assert fence['client'] == self.client_ref and 'clientEpoch' not in fence['client']
        assert fence['appRunId'] == RUN
        for field in ('commandId', 'requestId', 'actionId'):
            assert isinstance(fence[field], str) and fence[field]
            assert message[field] == fence[field]
        assert isinstance(fence['afterReportSeq'], int) and fence['afterReportSeq'] <= self.seq
        assert fence['issuedMono'] <= fence['deadlineMono']
        descriptors = message.get('instances')
        assert isinstance(descriptors, list), 'MISSING_FEATURE: command must serialize its committed target descriptors'
        assert 1 <= len(descriptors) <= 8
        states = {state['id']: state for state in descriptors}
        assert len(states) == len(descriptors) and set(states) == set(fence['targets'])
        assert message['targets'] == list(states)
        if message['commandId'] in self.rendered_commands:
            assert self.rendered_commands[message['commandId']] == message
            self.keep_results([{**result, 'reportSeq': self.seq + 1}
                               for result in self.command_results
                               if result['commandId'] == message['commandId']])
            await self.send_report()  # repeat observation, never reapply a relative operation
            return
        self.rendered_commands[message['commandId']] = copy.deepcopy(message)
        results = []
        for iid, lease in fence['targets'].items():
            state = states[iid]
            assert state['workspaceId'] == self.workspace_id
            assert state['lifetimeGeneration'] == lease['lifetimeGeneration']
            assert state['sourceGeneration'] == lease['sourceGeneration']
            assert state['revision'] == lease['committedRevision']
            versions, owners = state['propertyVersions'], state['propertyOwners']
            assert lease['committedPropertyVersions']
            for prop, version in lease['committedPropertyVersions'].items():
                assert versions[prop] == version and owners[prop] == lease['owner']
            assert state['sourceRef'] == lease['sourceRef']
            assert state['delivery']['sourceToken'] == lease['sourceToken']
            assert isinstance(state['delivery']['sourceToken'], str) and state['delivery']['sourceToken']
            wanted = state['sourceRef']['identity']['resource']
            wanted_payload = self.retained_payload(wanted)
            assert wanted_payload is not None
            expected_payload = lease['payloadBinding']
            assert expected_payload['servedDigest'] == digest(wanted_payload[0])
            if 'originalDigest' in expected_payload:
                assert expected_payload['originalDigest'] == digest(wanted_payload[0])
            if 'transform' in expected_payload:
                assert expected_payload['transform'] == [1, 0, 0, 1, 0, 0]
            ref = copy.deepcopy(self.source_override or state['sourceRef'])
            resource = ref['identity'].get('resource')
            assert resource, 'this synthetic renderer only decodes retained resource fixtures'
            found = self.retained_payload(resource)
            assert found is not None, 'retained payload must actually be readable'
            original = found[0]
            if ref.get('digest'):
                assert digest(original) == ref['digest'], 'actual retained file disagrees with source descriptor'
            data = jpeg(240) if self.bad_payload else original
            decoded = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            assert decoded is not None
            h, w = decoded.shape[:2]
            geometry, view = state['geometryIntent'], state['view']
            pos, size = geometry['position'], geometry['size'] or (360, 280)
            rect = [*pos, *size]
            relation = geometry['relation']
            if relation:
                # Synthetic CSS layout only. The real FloatingWindow/fit and
                # browser measurement campaign must test this separately.
                if relation.get('kind') == 'beside' and relation.get('reference') in self.items:
                    anchor = self.items[relation['reference']]['rect']
                    rect[0], rect[1] = anchor[0] + anchor[2] + relation.get('gapPx', 0), anchor[1]
            opened = state['open']
            phase = 'closed' if not opened else ('drawn' if self.draw else 'loaded')
            load_state = 'empty' if not opened else phase  # closed is a command phase, not a load state
            if opened and self.load_state_override is not None:
                load_state = self.load_state_override
                phase = 'error' if load_state == 'error' else load_state
            drawn = opened and self.draw and load_state == 'drawn'
            paint = f'synthetic-paint:{self.seq + 1}:{iid}' if drawn else None
            token = state['delivery']['sourceToken']
            payload = {'decodedToken': token, 'servedDigest': digest(data),
                       'originalDigest': digest(original), 'transform': [1, 0, 0, 1, 0, 0]}
            controls = {name: {'id': f'{name}:{iid}', 'role': ('drag-handle' if name == 'move' else 'resize-handle' if name == 'resize' else 'button'), 'editable': False,
                               'enabled': opened, 'present': opened, 'targetId': iid,
                               'stateRevision': versions[prop]}
                        for name, prop in CONTROL_PROPERTY.items()}
            controls['zoom'].update(role='button', observedValue=view['zoom'])
            if iid not in self.stack:
                self.stack.append(iid)
            if message['op'] == 'front':
                self.stack.remove(iid)
                self.stack.append(iid)
            content = [rect[0], rect[1] + 30, rect[2], rect[3] - 30]
            scale = min(content[2] / w, content[3] / h) * view['zoom']
            tx = content[2] / 2 - view['pan'][0] * w * scale
            ty = content[3] / 2 - view['pan'][1] * h * scale
            self.items[iid] = {
                'id': iid, 'rootId': 'synthetic-root:' + iid, 'workspaceId': self.workspace_id,
                'kind': DISPLAY_KIND[ref['kind']], 'sourceRef': ref, 'sourceKey': ref['key'],
                'sourceToken': token, 'sourceGeneration': state['sourceGeneration'],
                'lifetimeGeneration': state['lifetimeGeneration'], 'revision': state['revision'],
                'propertyVersions': dict(versions), 'propertyOwners': dict(owners),
                'open': opened, 'visible': opened, 'connected': opened, 'staged': False,
                'rect': rect, 'contentRect': content, 'clipRect': content, 'mode': 'floating',
                'phase': phase, 'drawRef': paint, 'viewDrawRef': 'view-' + paint if paint else None,
                'load': {'state': load_state, 'sourceKey': ref['key'], 'sourceGeneration': state['sourceGeneration'],
                         'naturalSize': [w, h], 'drawToken': paint, 'token': token},
                'payloadBinding': payload, 'controls': controls,
                'zoom': view['zoom'], 'pan': list(view['pan']), 'view': copy.deepcopy(view),
                'viewTransform': {'sourceToContent': [scale, 0, 0, scale, tx, ty],
                                  'contentOrigin': content[:2],
                                  'sourceToWorkspace': [scale, 0, 0, scale, tx + content[0], ty + content[1]],
                                  'originalSize': [w, h], 'transformRevision': state['revision']},
                'z': self.stack.index(iid) + 1,
            }
            results.append({field: fence[field] for field in ('commandId', 'requestId', 'actionId', 'appRunId')})
            results[-1].update(presentationId=iid, phase=phase, reportSeq=self.seq + 1,
                               lifetimeGeneration=state['lifetimeGeneration'], sourceGeneration=state['sourceGeneration'],
                               committedRevision=state['revision'], sourceToken=token, sourceKey=ref['key'],
                               committedPropertyVersions={prop: versions[prop] for prop in lease['committedPropertyVersions']},
                               committedPropertyOwners={prop: owners[prop] for prop in lease['committedPropertyVersions']},
                               propertyVersions=dict(versions), propertyOwners=dict(owners),
                               drawRef=paint, viewDrawRef='view-' + paint if paint else None, absent=not opened)
            if load_state == 'error':
                self.items[iid]['load']['errorCode'] = 'MEDIA_DECODE_FAILED'
                results[-1]['error'] = {'code': 'MEDIA_DECODE_FAILED', 'reason': 'synthetic renderer decode failure'}
        self.keep_results(results)
        for iid in self.stack:
            if iid in self.items:
                self.items[iid]['z'] = self.stack.index(iid) + 1
        await self.send_report()


async def _scoped_hello(app, studio, transport):
    app.hub.clients.add(studio)
    reply = transport.hello(studio, {'type': 'workspace-hello', 'schema': 'workspace-origin-v2',
        'requestId': 'hello:' + studio.workspace_id, 'workspaceId': studio.workspace_id,
        'clientId': studio.client_ref['clientId'], 'pageEpoch': studio.client_ref['pageEpoch']})
    assert reply.get('ok') is True, json.dumps(reply)
    studio.client_ref = dict(reply['client'])
    studio.client_id = reply['client']['clientId']
    studio.page_epoch = reply['client']['pageEpoch']
    studio.connection_epoch = reply['client']['connectionEpoch']
    await studio.send_report()


async def _scoped_open(app, studio, entry):
    await studio.send_report()
    ctx = ToolContext('chat', 1, None, 'Open retained synthetic bytes', asked=app.day.here())
    ctx.client, ctx.workspace_id = studio, studio.workspace_id
    ctx.when_asked = app.brain.board.when_asked()
    late = []

    async def observed_late(run, context):
        late.append(run)

    runner = ActionRunner(app.brain.registry, app.hub, lambda: None, observed_late, board=app.brain.board)
    with using(issue(app.hub, 'turn', asked=app.day.here(), channel='chat')):
        runs = await runner.run([('present', {'do': 'open', 'source': address(entry)})], ctx)
        await _settled({'run': runs[0]})
    assert runs[0].ok is True, runs[0].summary
    return output_window(ctx), runs[0], ctx


def _scoped_packet(studio, iid, n, *, level=2):
    state = studio.store().get(studio.workspace_id, iid)
    return {'type': 'present', 'schema': 'presentation-manual-v2', 'manualSeq': n,
            'op': 'zoom', 'targets': [iid], 'values': {'level': level},
            'client': dict(studio.client_ref), 'appRunId': RUN,
            'requestId': f'browser/manual/{n}', 'actionId': f'browser/action/{n}',
            'commandId': f'browser/command/{n}',
            'base': {iid: {'sourceGeneration': state.source_generation,
                           'lifetimeGeneration': state.lifetime_generation,
                           'propertyVersions': dict(state.property_versions)}}}


def test_global_canonical_budget_counts_retired_pending_at_original_admission(tmp_path, monkeypatch):
    """Real replacement count, then settled fresh use; no pending-through-source-open claim.

    The original renderer wait naturally ends on retirement. Canonical manual
    controls have no source-worker seam; the eight-workspace bound also prevents
    a ninth prepared origin. Read the real count synchronously after replacement,
    rather than delaying cancellation or pretending a 257th admission was reached.
    """
    app = App.build('http://127.0.0.1:1', Paths.under(tmp_path), use_gpu=False)
    _receipt_schedule(app, monkeypatch)
    transport = WorkspaceTransport(app)
    app.workspace_transport = transport
    studios = [ScopedStudio(app, f'synthetic-budget-{n}') for n in range(8)]
    replies = {studio: _offers(studio, monkeypatch) for studio in studios}
    broadcast = app.hub.broadcast

    def route(message):
        broadcast(message)
        if message.get('schema') == 'presentation-command-v2':
            matches = [studio for studio in studios
                       if studio in app.hub.clients and message['fence']['client'] == studio.client_ref]
            assert len(matches) == 1, 'SETUP: actual native fence has one originating synthetic renderer'
            matches[0].hear(message)

    monkeypatch.setattr(app.hub, 'broadcast', route)
    monkeypatch.setattr(app.hub.screen, 'broadcast', route)

    async def go():
        held = None
        try:
            entry, _ = saved_picture(app, 40)
            for studio in studios:
                await _scoped_hello(app, studio, transport)
            for studio in studios[:7]:
                iid, _, _ = await _scoped_open(app, studio, entry)
                for n in range(1, 33):
                    await studio.send_report()
                    packet = _scoped_packet(studio, iid, n)
                    reply = await _submit(app, studio, packet, replies[studio])
                    assert reply.get('ok') is True, json.dumps(reply)
                    # The actual owner exposes this original; no fabricated result/task.
                    actual = app.manual_commands.cursors[studio.workspace_id].entries[n]
                    run = actual.run or actual.context.steps.runs[1]
                    await _settled({'run': run})
                    await _until(lambda: actual.settled, 'actual settled canonical sibling')
                assert len(app.manual_commands.cursors[studio.workspace_id].entries) == 32
            old = studios[-1]
            held = await _held_prefix(app, old, monkeypatch, transport=transport,
                packet_factory=_scoped_packet, window_opener=lambda: _scoped_open(app, old, entry))
            assert _budget(app.manual_commands)['canonical'] == 256
            assert _budget(app.manual_commands)['current'] == 256
            legacy_before = len(app.manual_commands.legacy)
            transport.gone(old)
            app.hub.clients.discard(old)
            _retired_original(app, held)
            retired = _budget(app.manual_commands)
            assert retired['canonical'] <= 256 and retired['legacy'] == legacy_before

            fresh = ScopedStudio(app, old.workspace_id)
            studios[-1] = fresh
            fresh_replies = _offers(fresh, monkeypatch)
            # No await from genuine gone through actual hello/reaccept and these
            # ownership/count reads: the real old task has not resumed yet.
            app.hub.clients.add(fresh)
            hello = transport.hello(fresh, {'type': 'workspace-hello', 'schema': 'workspace-origin-v2',
                'requestId': 'hello:' + fresh.workspace_id, 'workspaceId': fresh.workspace_id,
                'clientId': fresh.client_ref['clientId'], 'pageEpoch': fresh.client_ref['pageEpoch']})
            assert hello.get('ok') is True, hello
            fresh.client_ref = dict(hello['client'])
            fresh.client_id = hello['client']['clientId']
            fresh.page_epoch = hello['client']['pageEpoch']
            fresh.connection_epoch = hello['client']['connectionEpoch']
            assert fresh.client_ref['connectionEpoch'] != old.client_ref['connectionEpoch']
            fresh_cursor = app.manual_commands.cursors[fresh.workspace_id]
            assert fresh_cursor is not held['cursor'] and fresh_cursor.socket is fresh
            assert not fresh_cursor.revoked and not fresh_cursor.entries
            assert (fresh_cursor.highest, fresh_cursor.expired) == (0, 0)
            _retired_original(app, held)
            replacement = _budget(app.manual_commands)
            assert replacement['canonical'] == 225 and replacement['current'] == 224
            assert replacement['retired'] == 1 and replacement['settled_retired'] == 0
            assert replacement['pending'] == replacement['pending_per_origin'] == 1
            assert replacement['legacy'] == legacy_before
            assert app.manual_commands._canonical_count() == 225, (
                'BEHAVIOR: actual canonical count includes the original retained by its retired pending owner')
            assert (held['cursor'].highest, held['cursor'].expired) == (33, 0)

            # Allow ordinary cancellation/settlement before any fresh source
            # acquisition. Its original receipt remains failed, never borrowed.
            await _finish_retired(app, old, held, monkeypatch)
            assert _budget(app.manual_commands)['canonical'] == 224
            assert _budget(app.manual_commands)['retired'] == 0
            await fresh.send_report()
            iid, _, _ = await _scoped_open(app, fresh, entry)
            trace = held['trace']
            counts = len(trace.issued), len(trace.calls), len(trace.pins), len(trace.reads)
            refused = await _submit(app, fresh, held['packet'], fresh_replies)
            assert refused.get('ok') is False, 'retired original packet cannot acquire fresh incarnation authority'
            assert (len(trace.issued), len(trace.calls), len(trace.pins), len(trace.reads)) == counts
            assert (fresh_cursor.highest, fresh_cursor.expired) == (0, 0)
            assert (held['cursor'].highest, held['cursor'].expired) == (33, 0)
            first_fresh = len(trace.calls)
            for n in range(1, 33):
                await fresh.send_report()
                packet = _scoped_packet(fresh, iid, n, level=3)
                useful = await _submit(app, fresh, packet, fresh_replies)
                assert useful.get('ok') is True, useful
                call = trace.original(packet, first_fresh + n - 1)
                await _settled(call)
                assert call['run'].occurrence != held['original']['run'].occurrence
                assert call['ticket'] is not held['original']['ticket']
                assert _horizon(useful, n)['highestManualSeq'] == n
                assert _horizon(useful, n)['expiredThroughManualSeq'] == 0
                budget = _budget(app.manual_commands)
                assert budget['canonical'] == budget['current'] == 224 + n
                assert budget['retired'] == budget['settled_retired'] == 0
                assert budget['pending'] <= 32 and budget['pending_per_origin'] <= 4
                assert budget['legacy'] == legacy_before <= 256
            assert all(_budget(app.manual_commands)[key] <= limit
                       for key, limit in [('canonical', 256), ('pending', 32), ('pending_per_origin', 4), ('legacy', 256)])
            # A full settled fresh cursor trims its oldest prefix normally and
            # remains useful; no retired original survives this acquisition.
            await fresh.send_report()
            useful = await _submit(app, fresh, _scoped_packet(fresh, iid, 33), fresh_replies)
            assert useful.get('ok') is True, useful
            await _settled(trace.calls[-1])
            assert _horizon(useful, 33)['highestManualSeq'] == 33
            assert _horizon(useful, 33)['expiredThroughManualSeq'] == 1
            assert _budget(app.manual_commands)['canonical'] == 256
            assert len(app.manual_commands.legacy) == legacy_before <= 256
        finally:
            for studio in studios:
                transport.gone(studio)
                app.hub.clients.discard(studio)
            if held is not None:
                transport.gone(held['cursor'].socket)
                app.hub.clients.discard(held['cursor'].socket)
                await _finish_retired(app, held['cursor'].socket, held, monkeypatch)
            for studio in studios:
                await asyncio.gather(*studio.observation_tasks, return_exceptions=True)

    asyncio.run(go())
