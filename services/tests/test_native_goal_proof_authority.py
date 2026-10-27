"""TEST-only authority controls for held c247, authored without execution.

Actual App/Registry/Runner/Ticket/Screen/native command and comparator paths.
Only the synthetic external AppStudio renderer is delayed or adjusted. No model,
browser, camera, internal lease, successful output or proof is manufactured.
Root owns the first named RED execution before the remaining matrix is added.
"""
import asyncio
import copy

import pytest

from orchestrator.brain.registry import ToolContext
from orchestrator.completion import supported_facts
from orchestrator.ticket import issue
from tests.test_native_requested_goal_binding import close_app, condition, inline_report
from tests.test_workspace_actions_app import WORKSPACE, _native_command, address, built, open_window, plan, saved_picture
from tests.waiting import HANG_S


def test_original_ticket_closed_before_causal_report_cannot_accept_goal_or_publish_output(tmp_path, monkeypatch):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)

    async def go():
        ticket = issue(app.hub, 'turn', asked=app.day.here(), channel='chat')
        pin = app.hub.screen.pin(studio, request_id=ticket.admission.request_id,
                                 inline=inline_report(studio))
        ctx = ToolContext('chat', 1, None, 'Open the retained picture', asked=app.day.here(),
                          client=studio, workspace_id=WORKSPACE, admission=ticket.admission,
                          request_id=ticket.admission.request_id, request_snapshot=pin)
        actions = [('present', {'do': 'open', 'source': address(entry)})]
        desired = {'conditions': [condition('media_ready', [{'step': 1, 'output': 0}],
                                             {'decoded': True, 'drawn': True})]}
        ctx.retain_goal(desired, actions)
        assert ctx.goal == desired
        held, release = asyncio.Event(), asyncio.Event()
        commands = []
        observe = studio.observe_command

        async def renderer(wire):
            if wire.get('schema') == 'presentation-command-v2' and wire['op'] == 'open':
                commands.append(copy.deepcopy(wire))
                held.set()
                await release.wait()
            await observe(wire)

        monkeypatch.setattr(studio, 'observe_command', renderer)
        request = asyncio.create_task(plan(app, actions, ctx=ctx, ticket=ticket))
        try:
            await asyncio.wait_for(held.wait(), HANG_S)
            assert len(commands) == 1
            command = commands[0]
            original = ctx.steps.runs[1]
            assert command['requestId'] == ticket.admission.request_id == original.occurrence.request_id
            assert command['actionId'] == original.occurrence.action_id
            assert original.ok is None and not original.outputs
            assert not ticket.closed and ticket.superseded('session') is None
            before = app.hub.screen.observation(studio).wire()
            assert not any(row['commandId'] == command['commandId'] for row in before['commandResults'])

            # Exercise real closure, not cancel(), a day bump or task cancellation.
            ticket.close()
            assert ticket.closed and ticket.cancelled is None and ticket.superseded('session') is None
            release.set()
            runs, returned = await asyncio.wait_for(request, 8)
            assert returned is ctx and runs == [original]
            await asyncio.gather(*studio.observation_tasks)
            accepted = app.hub.screen.observation(studio).wire()
            causal = [row for row in accepted['commandResults'] if row['commandId'] == command['commandId']]
            assert len(causal) == 1 and causal[0]['phase'] == 'drawn'
            assert causal[0]['requestId'] == command['requestId']
            assert causal[0]['actionId'] == command['actionId']
            assert causal[0]['presentationId'] == command['targets'][0]
            assert causal[0]['sourceToken'] == command['instances'][0]['delivery']['sourceToken']
            assert accepted['seq'] > command['fence']['afterReportSeq']
            assert original.step == (1, ctx.steps) and ctx.steps.runs[1] is original
            assert ticket.admission.used == 0, 'native proof adds no provider dispatch'

            projected = original.wire()
            assert projected['goal']['declaration'] == desired
            assert projected['goal']['status'] == 'unmet', (
                'NGB-C1: a correct report arriving after original Ticket closure cannot fulfill its Goal')
            assert original.ok is False, 'lost original authority must refuse proof acceptance'
            assert not original.outputs and not ctx.steps.outputs.get(1), (
                'a command rejected at proof acceptance must publish no successful ToolOutput')
            facet = next(row for row in projected['facets'] if row['commandIds'] == [command['commandId']])
            assert facet['status'] == 'failed'
            assert facet['goal']['confirmation'] is None or facet['goal']['confirmation']['status'] != 'confirmed'
        finally:
            release.set()
            if not request.done():
                await asyncio.wait_for(request, 8)

    try:
        asyncio.run(go())
    finally:
        close_app(app)


def test_timely_original_proof_survives_normal_ticket_close_and_becomes_historical_only_on_view_change(tmp_path):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)

    async def go():
        ticket = issue(app.hub, 'turn', asked=app.day.here(), channel='chat')
        pin = app.hub.screen.pin(studio, request_id=ticket.admission.request_id,
                                 inline=inline_report(studio))
        ctx = ToolContext('chat', 1, None, 'Open the retained picture', asked=app.day.here(),
                          client=studio, workspace_id=WORKSPACE, admission=ticket.admission,
                          request_id=ticket.admission.request_id, request_snapshot=pin)
        actions = [('present', {'do': 'open', 'source': address(entry)})]
        desired = {'conditions': [condition('media_ready', [{'step': 1, 'output': 0}],
                                             {'decoded': True, 'drawn': True})]}
        ctx.retain_goal(desired, actions)
        runs, returned = await plan(app, actions, ctx=ctx, ticket=ticket)
        assert returned is ctx and len(runs) == 1
        original = runs[0]
        assert not ticket.closed and ticket.cancelled is None
        assert original.ok is True and len(original.outputs) == 1
        iid = original.outputs[0]['instanceId']
        command = _native_command(studio, 'open')
        assert command['requestId'] == original.occurrence.request_id == ticket.admission.request_id
        assert command['actionId'] == original.occurrence.action_id
        assert command['targets'] == [iid]
        before = original.wire()
        assert before['goal']['declaration'] == desired and before['goal']['status'] == 'confirmed'
        facet = next(row for row in before['facets'] if row['commandIds'] == [command['commandId']])
        assert facet['confirmation']['status'] == facet['goal']['confirmation']['status'] == 'confirmed'
        assert facet['goal']['confirmation']['commandId'] == command['commandId']
        current = supported_facts(before['facets'], app.hub.screen.completion_report(studio))
        assert any(f['kind'] == 'drawn' and f['scope'] == 'current'
                   and f['actionId'] == original.occurrence.action_id for f in current)

        # Normal completion releases the request after its proof was accepted.
        ticket.close()
        assert ticket.closed and ticket.cancelled is None and ticket.superseded('session') is None
        assert original.wire() == before, 'normal post-success closure must not erase accepted proof'
        assert original.outputs[0]['instanceId'] == iid

        # An external renderer now observes a hidden root, with the original
        # causal ACK and source still retained. This is not a new command/proof.
        count = len([m for m in studio.messages if m.get('schema') == 'presentation-command-v2'])
        studio.items[iid]['visible'] = False
        await studio.send_report()
        after = original.wire()
        assert after['goal']['id'] == before['goal']['id'] and after['goal']['status'] == 'unmet'
        assert after['facets'] == before['facets'], 'current visibility cannot rewrite earlier causal proof'
        assert original.ok is True and original.outputs[0]['instanceId'] == iid
        historical = supported_facts(after['facets'], app.hub.screen.completion_report(studio))
        assert any(f['kind'] == 'drawn' and f['scope'] == 'historical'
                   and f['actionId'] == original.occurrence.action_id for f in historical)
        assert not any(f['kind'] == 'drawn' and f['scope'] == 'current' for f in historical)
        assert len([m for m in studio.messages if m.get('schema') == 'presentation-command-v2']) == count
        assert ticket.admission.used == 0

    try:
        asyncio.run(go())
    finally:
        close_app(app)


@pytest.mark.parametrize('paint_case', [
    'stale-prior-paint', 'fresh-command-paint', 'missing-baseline', 'unknown-command-paint',
])
def test_original_zoom_goal_requires_known_baseline_and_new_causal_view_paint(tmp_path, monkeypatch, paint_case):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)

    async def go():
        send = studio.send_report
        prior, rendered = {}, {}

        async def renderer_report(user=()):
            commands = [m for m in studio.messages if m.get('schema') == 'presentation-command-v2']
            if not commands:
                await send(user)  # initial accepted observation, before any native command
                return
            wire = commands[-1]
            for iid in wire['targets']:
                item = studio.items[iid]
                ack = next(row for row in studio.command_results
                           if row['commandId'] == wire['commandId'] and row['presentationId'] == iid)
                # AppStudio has actually decoded and drawn the retained bytes.
                # These are test-local external paint observations, not lease
                # edits or receipts supplied to the production comparator.
                assert item['load']['state'] == ack['phase'] == 'drawn'
                if wire['op'] == 'open':
                    if paint_case == 'missing-baseline':
                        item.pop('viewDrawRef', None)
                        ack.pop('viewDrawRef', None)
                    else:
                        paint = 'synthetic-view:' + item['load']['drawToken']
                        item['viewDrawRef'] = ack['viewDrawRef'] = paint
                elif wire['op'] == 'zoom':
                    assert iid in prior, 'the original accepted view must precede its zoom command'
                    descriptor = next(state for state in wire['instances'] if state['id'] == iid)
                    touched = ack['committedPropertyVersions']
                    for prop, version in touched.items():
                        assert descriptor['propertyVersions'][prop] == item['propertyVersions'][prop] == version
                        assert descriptor['propertyOwners'][prop] == item['propertyOwners'][prop] == ack['propertyOwners'][prop]
                    # Retain this received command's touched commitment, apart
                    # from the current observed maps. Never infer an owner from
                    # desired Goal values, an internal lease or a later state.
                    ack['committedPropertyOwners'] = {
                        prop: descriptor['propertyOwners'][prop] for prop in touched}
                    paint = ('synthetic-view:' + item['load']['drawToken'])
                    if paint_case == 'stale-prior-paint':
                        paint = prior[iid]['viewDrawRef']
                    elif paint_case == 'unknown-command-paint':
                        paint = None
                    item['viewDrawRef'] = ack['viewDrawRef'] = paint
                    rendered.update(command=copy.deepcopy(wire), item=copy.deepcopy(item), ack=copy.deepcopy(ack))
            await send(user)

        monkeypatch.setattr(studio, 'send_report', renderer_report)
        iid, opened, _ = await open_window(app, entry)
        assert opened.ok is True
        baseline = app.hub.screen.completion_report(studio)
        prior[iid] = copy.deepcopy(next(row for row in baseline['items'] if row['id'] == iid))
        old = prior[iid]
        assert old['view']['zoom'] == 1 and old['phase'] == 'drawn'
        if paint_case == 'missing-baseline':
            assert old.get('viewDrawRef') is None
        else:
            assert isinstance(old['viewDrawRef'], str) and old['viewDrawRef']
        ticket = issue(app.hub, 'turn', asked=app.day.here(), channel='chat')
        pin = app.hub.screen.pin(studio, request_id=ticket.admission.request_id,
                                 inline=inline_report(studio))
        pinned = next(row for row in pin.observation.wire()['items'] if row['id'] == iid)
        assert pinned.get('viewDrawRef') == old.get('viewDrawRef')
        actions = [('present', {'do': 'zoom', 'what': {'instanceId': iid}, 'zoom': {'factor': 2}})]
        desired = {'conditions': [
            condition('source_exact', [iid], {'sourceRef': copy.deepcopy(old['sourceRef'])}),
            condition('media_ready', [iid], {'decoded': True, 'drawn': True}),
            condition('zoom', [iid], {'value': 2}),
        ]}
        ctx = ToolContext('chat', 1, None, 'Zoom this retained view by two', asked=app.day.here(),
                          client=studio, workspace_id=WORKSPACE, admission=ticket.admission,
                          request_id=ticket.admission.request_id, request_snapshot=pin)
        ctx.retain_goal(desired, actions)
        assert ctx.goal == desired, 'the exact original zoom Goal must be admitted'
        runs, returned = await plan(app, actions, ctx=ctx, ticket=ticket)
        assert returned is ctx and len(runs) == 1 and rendered
        await asyncio.gather(*studio.observation_tasks)
        original = runs[0]
        command = rendered['command']
        actual = app.hub.screen.completion_report(studio)
        item = next(row for row in actual['items'] if row['id'] == iid)
        ack = next(row for row in actual['commandResults']
                   if row['commandId'] == command['commandId'] and row['presentationId'] == iid)
        lease = command['fence']['targets'][iid]
        assert command['op'] == 'zoom' and command['targets'] == [iid]
        assert command['requestId'] == original.occurrence.request_id == ticket.admission.request_id
        assert command['actionId'] == original.occurrence.action_id
        assert ack['commandId'] == command['commandId'] and ack['actionId'] == command['actionId']
        assert ack['requestId'] == command['requestId'] and ack['phase'] == 'drawn'
        assert actual['seq'] > command['fence']['afterReportSeq']
        assert item['sourceRef'] == old['sourceRef'] == lease['sourceRef']
        assert item['sourceToken'] == ack['sourceToken'] == old['sourceToken'] == lease['sourceToken']
        assert item['lifetimeGeneration'] == old['lifetimeGeneration'] == lease['lifetimeGeneration']
        assert item['sourceGeneration'] == old['sourceGeneration'] == lease['sourceGeneration']
        assert item['view']['zoom'] == item['controls']['zoom']['observedValue'] == 2
        version = lease['committedPropertyVersions']['zoom']
        assert version > old['propertyVersions']['zoom']
        assert item['propertyVersions']['zoom'] == ack['observedPropertyVersions']['zoom'] == version
        assert item['propertyOwners']['zoom'] == ack['observedPropertyOwners']['zoom'] == command['actionId']
        assert ack['committedPropertyVersions'] == lease['committedPropertyVersions']
        assert set(ack['committedPropertyOwners']) == set(lease['committedPropertyVersions'])
        for prop, committed in lease['committedPropertyVersions'].items():
            assert ack['committedPropertyVersions'][prop] == ack['observedPropertyVersions'][prop] == item['propertyVersions'][prop] == committed
            assert ack['committedPropertyOwners'][prop] == ack['observedPropertyOwners'][prop] == item['propertyOwners'][prop] == lease['owner'] == command['actionId']
        control = item['controls']['zoom']
        assert control['stateRevision'] == version and control['target'] == iid
        assert control['present'] is control['enabled'] is True and control['editable'] is False
        assert item.get('viewDrawRef') == ack.get('viewDrawRef') == rendered['item'].get('viewDrawRef')
        if paint_case == 'stale-prior-paint':
            assert item['viewDrawRef'] == old['viewDrawRef']
        elif paint_case == 'unknown-command-paint':
            assert item['viewDrawRef'] is None
        else:
            assert isinstance(item['viewDrawRef'], str) and item['viewDrawRef']
            assert item['viewDrawRef'] != old.get('viewDrawRef')
        assert not ticket.closed and ticket.superseded('session') is None
        assert original.step == (1, ctx.steps) and ctx.steps.runs[1] is original
        assert ticket.admission.used == 0

        projected = original.wire()
        assert projected['goal']['declaration'] == desired
        facet = next(row for row in projected['facets'] if row['commandIds'] == [command['commandId']])
        # Operation completion now requires the same original browser's causal
        # paint. Dispatch alone is a failed action, never successful zoom.
        assert not original.outputs
        if paint_case == 'fresh-command-paint':
            assert original.ok is True
            assert original.outcome['status'] == facet['status'] == 'confirmed'
            assert facet['confirmation']['status'] == 'confirmed'
        else:
            assert original.ok is False
            assert facet['confirmation']['status'] != 'confirmed'
            assert facet['error']['code'] in {'VERIFICATION_UNAVAILABLE', 'CONFIRM_TIMEOUT'}  # unavailable before or at the deadline
        proof = facet['goal']['confirmation']
        assert proof is not None and proof['commandId'] == command['commandId']
        assert proof['requestId'] == command['requestId'] and proof['actionId'] == command['actionId']
        assert proof['expected'] == facet['goal']['expected']
        non_paint = [e['id'] for e in proof['expected'] if e['predicate'] in ('source_exact', 'media_ready')]
        assert len(non_paint) == 2 and set(non_paint).issubset(proof['matched']), {
            'reason': 'source/media and causal ownership must pass before isolating paint',
            'case': paint_case, 'error': proof.get('error'), 'unmatched': proof['unmatched'],
        }
        if paint_case == 'fresh-command-paint':
            assert ack['reportSeq'] == actual['seq'], 'the fresh paint ACK must belong to this accepted report'
            assert proof['status'] == 'confirmed' and not proof['unmatched'], {
                'case': paint_case, 'status': proof['status'], 'error': proof.get('error'),
                'matched': proof['matched'], 'unmatched': proof['unmatched'],
                'ack': {k: ack.get(k) for k in (
                    'commandId', 'requestId', 'actionId', 'appRunId', 'reportSeq', 'viewDrawRef',
                    'committedPropertyVersions', 'committedPropertyOwners',
                    'observedPropertyVersions', 'observedPropertyOwners')},
                'registeredControl': control, 'leaseOwner': lease['owner'],
                'leaseTouchedVersions': lease['committedPropertyVersions'],
            }
            assert projected['goal']['status'] == 'confirmed', (
                'genuinely new observed paint can prove the exact Goal despite the operation guard')
        else:
            assert projected['goal']['status'] == 'unmet', (
                'SPEC-P2: stale, absent-baseline or unknown paint cannot prove the original changed view')
            assert proof['status'] != 'confirmed'

    try:
        asyncio.run(go())
    finally:
        close_app(app)
