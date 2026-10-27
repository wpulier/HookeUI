"""TEST-only native requested-goal tracer, based on frozen bc0ce0d.

Actual App/Registry/Ticket/Runner, retained compare components, prepared leases,
native commands and Screen comparison execute unchanged. AppStudio is an
explicitly synthetic external renderer; the sole model substitute is a canned
provider. These are local contract observations, not actual browser/model proof.

Proposed missing public seam: original ToolRun.wire() exposes a detached `goal`
{id, declaration, status} and `facets`, canonical completion rows with separate
operation and goal confirmations. Facets retain original occurrence/round/step;
their goal.id identifies the same root, while commandIds distinguish commands.
No test constructs that projection or a successful ToolOutput/confirmation.
"""
import asyncio
import copy
import json
import time
from types import SimpleNamespace

import pytest

from orchestrator.brain.actions import ActionRunner
from orchestrator.brain.registry import Steps, ToolContext, ToolFailed, typed_step_output
from orchestrator.capture import RUN
from orchestrator.commands import COMMANDS
from orchestrator.completion import supported_facts
from orchestrator.ticket import current, issue, using
from tests.test_workspace_actions_app import (
    CLIENT, WORKSPACE, _ObservationClock, _causal_receipt, _comparison_parent, _faithful_absence,
    _native_command, built, digest, plan,
)
from tests.waiting import HANG_S


def condition(predicate, targets, expected, *, action=1, essential=True):
    return {'action': action, 'predicate': predicate, 'targets': copy.deepcopy(targets),
            'expected': copy.deepcopy(expected), 'essential': essential,
            'requiredPhase': 'drawn', 'tolerance': None}


def split_goal(parent, sources, *, action=1):
    outputs = [{'step': action, 'output': n} for n in range(2)]
    return {'conditions': [
        condition('source_exact', [outputs[n]], {'sourceRef': source}, action=action)
        for n, source in enumerate(sources)
    ] + [
        condition('media_ready', outputs, {'decoded': True, 'drawn': True}, action=action),
        condition('independent_windows', outputs, {'count': 2, 'distinctRoots': True}, action=action),
        condition('control', outputs,
                  {'each': ['move', 'resize', 'zoom', 'pan', 'front', 'close'], 'independent': True}, action=action),
        condition('content_size', outputs, {'minWidth': 240, 'minHeight': 180}, action=action),
        condition('closed', [parent], {'value': True}, action=action),
    ]}


def inline_report(studio):
    """Literal external AppStudio measurements at Send, through real ingress."""
    studio.seq += 1
    items = [copy.deepcopy(item) for item in studio.items.values()
             if item.get('rootId', '').startswith('synthetic-root:')]
    for item in items:
        item['controls'] = list(item['controls'].values())
    return {'type': 'screen', 'schema': 'screen-observation-v2', 'client': dict(CLIENT),
        'appRunId': RUN, 'seq': studio.seq, 'layoutRevision': studio.seq,
        'capturedMonoMs': time.monotonic() * 1000,
        'visible': True, 'complete': True, 'omitted': {},
        'viewport': {'layoutSize': [1440, 1000], 'visualRect': [0, 0, 1440, 1000],
                     'visualScale': 1, 'documentScroll': [0, 0], 'dpr': 1,
                     'visible': True, 'documentFocused': True, 'activeSurface': 'workspace'},
        'items': items, 'sources': list({i['sourceKey']: i['sourceRef'] for i in items}.values()),
        'targets': [{'id': 'target:' + i['id'], 'type': 'presentation',
                     'presentationId': i['id'], 'sourceKey': i['sourceKey'],
                     'basis': 'registered-window'} for i in items],
        'edges': [{'type': 'shows', 'from': i['id'], 'to': i['sourceKey']} for i in items],
        'interaction': {'availability': 'observed', 'unknown': []},
        'commandResults': copy.deepcopy(studio.command_results),
        'applied': list(studio.applied), 'user': []}


def close_app(app):
    app.hub._analysis.shutdown(wait=True)
    app.hub._background.shutdown(wait=True)


class PublicAsk:
    """Only provider input and read-only traces; original Registry.start runs."""
    def __init__(self, app, studio, monkeypatch, actions, desired, *, easier=None):
        self.app, self.studio = app, studio
        self.actions, self.desired, self.easier = actions, desired, easier
        self.sends, self.dispatches = [], []
        settings = app.hub.settings
        settings.fast, settings.compare, settings.outcome_lines = 'off', '', 'off'
        settings.pointing, settings.closeup = 'box', 'single'
        original = app.brain.registry.start

        def trace(name, args, ctx, run=None):
            actual = original(name, args, ctx, run=run)
            self.dispatches.append(SimpleNamespace(run=actual, ctx=ctx, ticket=current(),
                step=actual.step, declaration=copy.deepcopy(ctx.goal)))
            return actual

        async def provider(images, prompt, **kwargs):
            ticket = current()
            self.sends.append(SimpleNamespace(ticket=ticket, admission=ticket.admission,
                phase=ticket.admission.attempts[-1].phase, images=tuple(images)))
            first = len(self.sends) == 1
            data = {'answer': 'I am applying the requested retained-view change.', 'targets': [],
                    'actions': [{'tool': n, 'args': copy.deepcopy(a)} for n, a in self.actions] if first else [],
                    'intent': 'request', 'needs_look': False,
                    'observation_scope': 'structural-workspace', 'input_refs': []}
            declared = self.desired if first else self.easier
            if declared is not None:
                data['goal'] = copy.deepcopy(declared)
            return SimpleNamespace(data=data, model='explicitly-canned-external-provider', latency_ms=0, error=None)

        monkeypatch.setattr(app.brain.registry, 'start', trace)
        monkeypatch.setattr(app.agent, 'vision', lambda _option: SimpleNamespace(ask=provider))

    async def send(self):
        self.raw = inline_report(self.studio)
        result = await COMMANDS['ask'](self.app, self.studio,
            {'type': 'ask', 'text': 'Separate these retained originals into usable windows and close their parent.',
             'screen': self.raw})
        for trace in self.dispatches:
            if trace.run.sequence is not None:
                await asyncio.wait_for(trace.run.sequence, HANG_S)
        return result


def requested(run, desired, status):
    """Public missing-feature assertion; never infer a goal from handler ok."""
    projected = run.wire()
    goal = projected.get('goal')
    assert isinstance(goal, dict), 'MISSING_FEATURE: original ToolRun has no requested-goal projection'
    assert isinstance(goal.get('id'), str) and goal['id']
    assert goal.get('declaration') == desired, 'MISSING_FEATURE: original admitted intent/ordinals were not retained'
    assert goal.get('status') == status, 'MISSING_FEATURE: every original essential condition needs independent proof'
    facets = projected.get('facets')
    assert isinstance(facets, list) and facets, 'MISSING_FEATURE: original ToolRun has no canonical native command facets'
    for facet in facets:
        assert facet['requestId'] == run.occurrence.request_id
        assert facet['actionId'] == run.occurrence.action_id
        assert facet['plannedStep'] == run.step[0]
        assert type(facet['round']) is int and facet['round'] >= 1
        assert facet['completionScope'] == 'workspace'
        assert facet['goal']['id'] == goal['id']
        assert facet['commandIds']
        confirmation = facet.get('confirmation')
        if confirmation is None:
            assert facet['status'] != 'confirmed', 'a lost origin can have no actual comparison proof'
        else:
            assert confirmation['commandId'] in facet['commandIds']
            assert confirmation['expected'] == facet['expected']
    # Read projections cannot change original goal/facet authority.
    frozen = copy.deepcopy(projected)
    goal['declaration']['conditions'].clear()
    facets[0]['commandIds'].clear()
    assert run.wire() == frozen
    return frozen['goal'], frozen['facets']


def facet_for(facets, command):
    found = [row for row in facets if command['commandId'] in row['commandIds']]
    assert len(found) == 1, 'each actual command needs its own retained facet'
    row = found[0]
    assert row['commandIds'] == [command['commandId']]
    assert row['confirmation']['commandId'] == command['commandId']
    goal_confirmation = row['goal'].get('confirmation')
    if goal_confirmation is not None:
        assert goal_confirmation['commandId'] == command['commandId']
        assert goal_confirmation['expected'] == row['goal']['expected']
    return row


def assert_complete_bound_conditions(desired, facets, original_steps):
    """Literal oracle: resolve only original selectors; retain every other value."""
    expected = copy.deepcopy(desired['conditions'])
    for condition in expected:
        condition['targets'] = [
            original_steps.outputs[target['step']][target['output']]['instanceId']
            if isinstance(target, dict) else target for target in condition['targets']]
    actual, predicate_ids = [], []
    for facet in facets:
        for bound in facet['goal']['expected']:
            assert isinstance(bound['id'], str) and bound['id']
            predicate_ids.append(bound['id'])
            value = {key: copy.deepcopy(v) for key, v in bound.items() if key != 'id'}
            # Action routing is the actual original facet occurrence, not a new selector.
            assert value.get('action', facet['plannedStep']) == facet['plannedStep']
            value.setdefault('action', facet['plannedStep'])
            actual.append(value)
    assert len(set(predicate_ids)) == len(predicate_ids), 'each retained goal condition needs a unique identity'
    def encode(rows):
        return sorted(json.dumps(row, sort_keys=True, allow_nan=False) for row in rows)

    assert encode(actual) == encode(expected), 'every original bound condition/value/order must survive goal compilation'


def assert_split_outputs(app, studio, run, original_steps, sources, originals):
    split = _native_command(studio, 'split')
    receipt = _causal_receipt(run.wire()['outcome'], split['commandId'], 'confirmed')
    children = [output.wire()['instanceId'] for output in run.outputs]
    assert children == split['targets'] and len(set(children)) == 2
    assert original_steps.outputs[run.step[0]] is run.outputs
    assert original_steps.runs[run.step[0]] is run
    assert original_steps.occurrences[run.step[0]] is run.occurrence
    rows = {row['id']: row for row in receipt['observed']}
    for n, iid in enumerate(children):
        output = run.outputs[n].wire()
        assert output['source'] == sources[n]
        assert output['sourceToken'] == split['fence']['targets'][iid]['sourceToken']
        assert rows[iid]['sourceRef'] == sources[n]
        assert rows[iid]['payloadBinding']['servedDigest'] == digest(originals[n])
        assert rows[iid]['phase'] == 'drawn' and rows[iid]['drawRef']
        assert studio.items[iid]['contentRect'][2] >= 240 and studio.items[iid]['contentRect'][3] >= 180
        assert studio.items[iid]['controls']['move']['targetId'] == iid
        assert studio.items[iid]['controls']['resize']['targetId'] == iid
        context = app.workspace_context(studio, request_id=run.occurrence.request_id, owner='server:metadata')
        lease = app.services.prepared_sources.borrow(output['sourceToken'], context)
        try:
            assert lease.source.to_wire() == sources[n]
        finally:
            lease.release()
    assert rows[children[0]]['rootId'] != rows[children[1]]['rootId']
    return split, children


def refuse_parent(studio, monkeypatch, parent):
    """Actual child draw, then genuine external failed close of the old root."""
    observe, seen = studio.observe_command, {}

    async def renderer(wire):
        if wire['op'] == 'close' and wire['targets'] == [parent]:
            seen['command'] = copy.deepcopy(wire)
            seen['child_report'] = copy.deepcopy(studio.app.hub.screen.completion_report(studio))
            item = studio.items[parent]  # no parent close is applied
            row = {key: wire[key] for key in ('requestId', 'actionId', 'commandId', 'appRunId')}
            row.update(presentationId=parent, reportSeq=studio.seq + 1, phase='error',
                lifetimeGeneration=item['lifetimeGeneration'], sourceGeneration=item['sourceGeneration'],
                committedRevision=wire['fence']['targets'][parent]['committedRevision'],
                committedPropertyVersions=dict(wire['fence']['targets'][parent]['committedPropertyVersions']),
                sourceToken=item['sourceToken'], sourceKey=item['sourceKey'],
                propertyVersions=dict(item['propertyVersions']), propertyOwners=dict(item['propertyOwners']),
                drawRef=None, absent=False,
                error={'code': 'RENDER_FAILED', 'reason': 'synthetic parent retirement did not apply'})
            studio.keep_results([row])
            await studio.send_report()
            return
        await observe(wire)

    monkeypatch.setattr(studio, 'observe_command', renderer)
    return seen


@pytest.mark.parametrize('parent_closes', [True, False], ids=['whole-goal', 'confirmed-children-only'])
def test_public_split_binds_forthcoming_outputs_and_separate_parent_facet(tmp_path, monkeypatch, parent_closes):
    app, studio = built(tmp_path)

    async def go():
        parent, originals = await _comparison_parent(app)
        sources = [source.to_wire() for source in studio.store().get(WORKSPACE, parent).source.components]
        desired = split_goal(parent, sources)
        retired = {} if parent_closes else refuse_parent(studio, monkeypatch, parent)
        ask = PublicAsk(app, studio, monkeypatch,
                       [('present', {'do': 'split', 'what': {'instanceId': parent}})], desired)
        result = await ask.send()
        assert len(ask.dispatches) == 1
        trace = ask.dispatches[0]
        run, ctx = trace.run, trace.ctx
        assert trace.step is run.step and trace.step[1] is ctx.steps
        assert trace.declaration == ctx.goal == desired
        assert ctx.request_snapshot.timing == 'client-send'
        assert ctx.request_snapshot.observation.wire()['seq'] == ask.raw['seq']
        assert any(row['id'] == parent for row in ctx.request_snapshot.observation.wire()['items'])
        assert ctx.client is studio and ctx.request_id == run.occurrence.request_id == trace.ticket.admission.request_id
        assert ctx.admission is trace.ticket.admission
        split, children = assert_split_outputs(app, studio, run, trace.step[1], sources, originals)
        close = _native_command(studio, 'close') if parent_closes else retired['command']
        assert split['commandId'] != close['commandId'] and close['targets'] == [parent]
        removal = _causal_receipt(run.wire()['outcome'], close['commandId'])
        assert run.ok is parent_closes and (removal['status'] == 'confirmed') is parent_closes
        assert studio.items[parent]['open'] is (not parent_closes)
        assert all(send.admission is ctx.admission for send in ask.sends)
        assert ctx.admission.used == len(ask.sends) == result['calls'] <= 3
        goal, facets = requested(run, desired, 'confirmed' if parent_closes else 'unmet')
        child_facet, close_facet = facet_for(facets, split), facet_for(facets, close)
        assert child_facet['status'] == child_facet['confirmation']['status'] == 'confirmed'
        assert (close_facet['status'] == 'confirmed') is parent_closes
        assert child_facet['round'] == close_facet['round'] == 1
        assert child_facet['sourceRefs'] == sources
        bound = child_facet['goal']['expected']
        for n, source in enumerate(sources):
            assert any(e['predicate'] == 'source_exact' and e['targets'] == [children[n]]
                       and e['expected'] == {'sourceRef': source} for e in bound)
        assert any(e['predicate'] == 'independent_windows' and e['targets'] == children for e in bound)
        assert close_facet['goal']['expected'][0]['targets'] == [parent]
        assert close_facet['goal']['expected'][0]['predicate'] == 'closed'
        assert len(bound) + len(close_facet['goal']['expected']) == len(desired['conditions'])
        assert child_facet['goal']['confirmation']['status'] == 'confirmed'
        assert (close_facet['goal']['confirmation']['status'] == 'confirmed') is parent_closes
        assert_complete_bound_conditions(desired, [child_facet, close_facet], trace.step[1])
        facts = supported_facts(facets, app.hub.screen.completion_report(studio))
        assert any(f['kind'] == 'drawn' and f['scope'] == 'current'
                   and f['presentationIds'] and set(f['presentationIds']) <= set(children) for f in facts)
        if not parent_closes:
            assert any(f['kind'] in {'failure', 'limitation'} for f in facts)
            assert not any(f['kind'] == 'closed' and f['scope'] == 'current' for f in facts)
        assert goal['status'] != 'confirmed' or parent_closes
    try:
        asyncio.run(go())
    finally:
        close_app(app)


@pytest.mark.parametrize('variant', ['extra-essential', 'extra-optional', 'wrong-order', 'wrong-source',
                                     'missing-output', 'missing-front'])
def test_operation_success_does_not_substitute_for_the_original_declared_goal(tmp_path, monkeypatch, variant):
    app, studio = built(tmp_path)

    async def go():
        parent, originals = await _comparison_parent(app)
        sources = [source.to_wire() for source in studio.store().get(WORKSPACE, parent).source.components]
        desired = split_goal(parent, sources)
        if variant.startswith('extra-'):
            desired['conditions'].append(condition('rect', [{'step': 1, 'output': 0}],
                {'rect': [9999, 9999, 360, 280]}, essential=variant == 'extra-essential'))
        elif variant == 'wrong-order':
            for n in range(2):
                desired['conditions'][n]['expected'] = {'sourceRef': sources[1 - n]}
        elif variant == 'wrong-source':
            desired['conditions'][0]['expected'] = {'sourceRef': studio.store().get(WORKSPACE, parent).source.to_wire()}
        elif variant == 'missing-output':
            desired['conditions'][0]['targets'] = [{'step': 1, 'output': 2}]  # allowed desired index, never produced
        supplied = {}
        if variant == 'missing-front':
            send = studio.send_report

            async def report(user=()):
                wire = _native_command(studio)
                if wire['op'] == 'split' and not supplied:
                    iid = wire['targets'][1]
                    del studio.items[iid]['controls']['front']  # external DOM registration alone is missing
                    supplied.update(child=iid, command=copy.deepcopy(wire))
                await send(user)

            monkeypatch.setattr(studio, 'send_report', report)
        ask = PublicAsk(app, studio, monkeypatch,
                       [('present', {'do': 'split', 'what': {'instanceId': parent}})], desired)
        await ask.send()
        run = ask.dispatches[0].run
        assert run.ok is True
        split, _ = assert_split_outputs(app, studio, run, run.step[1], sources, originals)
        close = _native_command(studio, 'close')
        assert _causal_receipt(run.outcome, close['commandId'], 'confirmed')
        assert ask.dispatches[0].ctx.goal == desired
        if variant == 'missing-front':
            assert supplied and supplied['command']['commandId'] == split['commandId']
            operation = _causal_receipt(run.outcome, split['commandId'], 'confirmed')
            observed = next(row for row in operation['observed'] if row['id'] == supplied['child'])
            assert 'front' not in observed['controls']
            assert app.hub.screen.observation(studio).wire()['seq'] == studio.seq
        _, facets = requested(run, desired, 'confirmed' if variant == 'extra-optional' else 'unmet')
        child = facet_for(facets, split)
        assert child['confirmation']['status'] == 'confirmed', 'narrow operation proof and real outputs must survive'
        goal_proof = child['goal']['confirmation']
        if variant == 'extra-optional':
            assert goal_proof['status'] == 'confirmed' and goal_proof['unmatched']
        elif variant == 'missing-output':
            assert goal_proof is None or goal_proof['status'] != 'confirmed', 'an unpublished identity supplies no proof'
        else:
            assert goal_proof['status'] != 'confirmed', 'a stronger or unresolved essential cannot inherit operation success'
        if variant == 'missing-front':
            assert_complete_bound_conditions(desired, [child, facet_for(facets, close)], run.step[1])
            control = next(e for e in child['goal']['expected'] if e['predicate'] == 'control')
            assert 'front' in control['expected']['each'] and control['essential'] is True
            assert any(row['predicateId'] == control['id'] and row['code'] == 'CONTROL_UNAVAILABLE'
                       for row in goal_proof['unmatched'])
    try:
        asyncio.run(go())
    finally:
        close_app(app)


@pytest.mark.parametrize('fault', ['paint', 'control', 'content', 'lifetime', 'socket'])
def test_real_child_verification_failure_keeps_the_original_root_goal_unmet(tmp_path, monkeypatch, fault):
    app, studio = built(tmp_path)

    async def go():
        parent, _ = await _comparison_parent(app)
        sources = [source.to_wire() for source in studio.store().get(WORKSPACE, parent).source.components]
        desired = split_goal(parent, sources)
        send, injected = studio.send_report, {}
        clock = _ObservationClock(monkeypatch, app) if fault == 'paint' else None

        async def renderer_report(user=()):
            wire = _native_command(studio)
            if wire['op'] == 'split' and not injected:
                iid = wire['targets'][1]
                injected.update(command=copy.deepcopy(wire), child=iid)
                item = studio.items[iid]
                if fault == 'paint':
                    item.update(phase='loaded', drawRef=None)
                    item['load'].update(state='loaded', drawToken=None)
                    ack = next(r for r in studio.command_results
                               if r['commandId'] == wire['commandId'] and r['presentationId'] == iid)
                    ack.update(phase='loaded', drawRef=None)
                    clock.after(wire, 4.25)  # actual loaded pixels never acquire a paint before deadline
                elif fault == 'control':
                    del item['controls']['resize']
                elif fault == 'content':
                    item['contentRect'][2:] = [80, 60]
                elif fault == 'lifetime':
                    item['lifetimeGeneration'] = 'synthetic-replaced-root'
                else:
                    studio.app.hub.screen.gone(studio)  # real original socket retirement
                    return
            _faithful_absence(studio)
            await send(user)

        monkeypatch.setattr(studio, 'send_report', renderer_report)
        ask = PublicAsk(app, studio, monkeypatch,
                       [('present', {'do': 'split', 'what': {'instanceId': parent}})], desired)
        await ask.send()
        assert injected, 'failure must occur after actual committed child targets exist'
        run = ask.dispatches[0].run
        assert run.ok is False and not run.outputs
        assert run.outcome['status'] != 'confirmed'
        assert not any(m.get('op') == 'close' and m.get('targets') == [parent] for m in studio.messages)
        assert studio.items[parent]['open']
        requested(run, desired, 'unmet')
    try:
        asyncio.run(go())
    finally:
        close_app(app)


def test_later_easier_final_declaration_cannot_replace_an_unmet_original_split_goal(tmp_path, monkeypatch):
    app, studio = built(tmp_path)

    async def go():
        parent, _ = await _comparison_parent(app)
        sources = [source.to_wire() for source in studio.store().get(WORKSPACE, parent).source.components]
        desired = split_goal(parent, sources)
        desired['conditions'].append(condition('rect', [{'step': 1, 'output': 0}], {'rect': [9999, 9999, 360, 280]}))
        easier = {'conditions': [condition('visible', [{'step': 1, 'output': 0}], {'value': True})]}
        ask = PublicAsk(app, studio, monkeypatch,
                       [('present', {'do': 'split', 'what': {'instanceId': parent}}), ('resources', {})], desired, easier=easier)
        result = await ask.send()
        assert len(ask.sends) >= 2 and any(s.phase == 'query' for s in ask.sends)
        run, ctx = ask.dispatches[0].run, ask.dispatches[0].ctx
        assert run.ok is True and len(run.outputs) == 2
        assert all(trace.ctx.goal == desired for trace in ask.dispatches)
        assert ctx.admission.used == len(ask.sends) == result['calls'] <= 3
        requested(run, desired, 'unmet')
    try:
        asyncio.run(go())
    finally:
        close_app(app)


def test_carried_split_keeps_original_steps_occurrence_and_goal_after_a_new_round(tmp_path, monkeypatch):
    app, studio = built(tmp_path)

    async def go():
        parent, originals = await _comparison_parent(app)
        sources = [source.to_wire() for source in studio.store().get(WORKSPACE, parent).source.components]
        desired = split_goal(parent, sources)
        ticket = issue(app.hub, 'turn', asked=app.day.here(), channel='chat')
        ctx = ToolContext('chat', 1, None, 'Separate originals', asked=app.day.here(), client=studio,
                          workspace_id=WORKSPACE, admission=ticket.admission, request_id=ticket.admission.request_id)
        actions = [('present', {'do': 'split', 'what': {'instanceId': parent}})]
        ctx.retain_goal(desired, actions)
        runs, _ = await plan(app, actions, ctx=ctx, ticket=ticket)
        original, steps = runs[0], runs[0].step[1]
        assert original.ok is True
        split, children = assert_split_outputs(app, studio, original, steps, sources, originals)
        # Exact carry reuses real output lists/run, without a second split dispatch.
        ctx.steps = Steps.carrying(actions, {1: (1, steps)}, steps)
        carried = ctx.steps
        assert carried.runs[1] is original and carried.outputs[1] is original.outputs
        assert carried.occurrences[1] is original.occurrence and original.step[1] is steps
        # AppStudio keeps closed items; a real removed root no longer reports a
        # source grant that the confirmed retirement has already released.
        retirement = _native_command(studio, 'close')
        assert _causal_receipt(original.outcome, retirement['commandId'], 'confirmed')
        closed = studio.items[parent]
        assert closed['open'] is closed['connected'] is closed['visible'] is False
        assert closed['load']['state'] == 'empty'
        _faithful_absence(studio)
        assert parent not in studio.items
        await studio.send_report()
        assert app.hub.screen.observation(studio).wire()['seq'] == studio.seq
        # A later duplicate changes the current round/output map without editing
        # either original child's proved properties or making its proof historical.
        later, _ = await plan(app, [('present', {'do': 'duplicate', 'what': {'instanceId': children[0]}})],
                              ctx=ctx, ticket=ticket)
        assert later[0].ok is True and later[0].step[1] is not steps
        assert later[0].occurrence.action_id != original.occurrence.action_id
        assert later[0].outputs[0]['instanceId'] not in children
        assert original.outputs is steps.outputs[1] is carried.outputs[1]
        assert len([m for m in studio.messages if m.get('op') == 'split']) == 1
        _, facets = requested(original, desired, 'confirmed')
        child = facet_for(facets, split)
        assert child['plannedStep'] == 1 and child['round'] == 1
        assert all(later[0].occurrence.action_id != f['actionId'] for f in facets)
    try:
        asyncio.run(go())
    finally:
        close_app(app)


def test_numbered_runner_passes_bind_goal_to_original_second_step_before_outputs_exist(tmp_path):
    app, studio = built(tmp_path)

    async def go():
        parent, originals = await _comparison_parent(app)
        sources = [source.to_wire() for source in studio.store().get(WORKSPACE, parent).source.components]
        desired = split_goal(parent, sources, action=2)
        actions = [('resources', {}), ('present', {'do': 'split', 'what': {'instanceId': parent}})]
        ticket = issue(app.hub, 'turn', asked=app.day.here(), channel='chat')
        pin = app.hub.screen.pin(studio, request_id=ticket.admission.request_id, inline=inline_report(studio))
        ctx = ToolContext('chat', 1, None, 'Separate originals after reading resources', asked=app.day.here(),
            client=studio, workspace_id=WORKSPACE, admission=ticket.admission,
            request_id=ticket.admission.request_id, request_snapshot=pin)
        ctx.retain_goal(desired, actions)
        original_steps = ctx.steps

        async def on_late(run, context):
            pass  # only the existing Runner completes these real runs

        runner = ActionRunner(app.brain.registry, app.hub, lambda: None, on_late, board=app.brain.board)
        with using(ticket):
            first = await runner.run(actions[:1], ctx, numbers=[1])
            await asyncio.wait_for(first[0].sequence, HANG_S)
            assert first[0].ok is True and 2 not in original_steps.outputs
            second = await runner.run(actions[1:], ctx, numbers=[2])
            await asyncio.wait_for(second[0].sequence, HANG_S)
        run = second[0]
        assert run.ok is True and ctx.steps is original_steps and run.step == (2, original_steps)
        assert first[0].occurrence.action_id != run.occurrence.action_id
        split, children = assert_split_outputs(app, studio, run, original_steps, sources, originals)
        assert ticket.admission.used == 0, 'goal comparison adds no provider dispatch'
        _, facets = requested(run, desired, 'confirmed')
        child = facet_for(facets, split)
        assert child['plannedStep'] == 2 and child['round'] == 1
        assert any(e['predicate'] == 'source_exact' and e['targets'] == [children[1]]
                   and e['expected'] == {'sourceRef': sources[1]} for e in child['goal']['expected'])
    try:
        asyncio.run(go())
    finally:
        close_app(app)


def test_repeated_identical_intentional_actions_do_not_borrow_each_others_forthcoming_output(tmp_path, monkeypatch):
    app, studio = built(tmp_path)

    async def go():
        parent, _ = await _comparison_parent(app)
        source = studio.store().get(WORKSPACE, parent).source.to_wire()
        action = ('present', {'do': 'duplicate', 'what': {'instanceId': parent}})
        actions = [action, action]
        desired = {'conditions': [condition('source_exact', [{'step': 2, 'output': 0}], {'sourceRef': source}, action=2)]}
        ask = PublicAsk(app, studio, monkeypatch, actions, desired)
        await ask.send()
        first, second = [trace.run for trace in ask.dispatches]
        assert first.ok is second.ok is True
        assert first.step[0] == 1 and second.step[0] == 2 and first.step[1] is second.step[1]
        assert first.occurrence.request_id == second.occurrence.request_id
        assert first.occurrence.action_id != second.occurrence.action_id
        a, b = first.outputs[0]['instanceId'], second.outputs[0]['instanceId']
        assert a != b
        _, facets = requested(second, desired, 'confirmed')
        expected = [e for row in facets for e in row['goal']['expected']]
        assert any(e['predicate'] == 'source_exact' and e['targets'] == [b] for e in expected)
        assert not any(a in e['targets'] for e in expected)
        assert all(row['actionId'] == second.occurrence.action_id for row in facets)
    try:
        asyncio.run(go())
    finally:
        close_app(app)


@pytest.mark.parametrize('reference', ['same-step', 'later-step', 'failed-earlier'])
def test_goal_forthcoming_selector_does_not_weaken_earlier_output_action_arguments(tmp_path, monkeypatch, reference):
    app, studio = built(tmp_path)

    async def go():
        parent, _ = await _comparison_parent(app)
        source = studio.store().get(WORKSPACE, parent).source.to_wire()
        if reference == 'same-step':
            actions = [('present', {'do': 'duplicate', 'what': {'step': 1, 'output': 0}})]
            action_number = 1
        elif reference == 'later-step':
            actions = [('present', {'do': 'duplicate', 'what': {'step': 2, 'output': 0}}),
                       ('present', {'do': 'duplicate', 'what': {'instanceId': parent}})]
            action_number = 1
        else:
            actions = [('present', {'do': 'duplicate', 'what': {'instanceId': 'w-missing-original'}}),
                       ('present', {'do': 'duplicate', 'what': {'step': 1, 'output': 0}})]
            action_number = 2
        desired = {'conditions': [condition('source_exact', [{'step': action_number, 'output': 0}],
                                             {'sourceRef': source}, action=action_number)]}
        ask = PublicAsk(app, studio, monkeypatch, actions, desired)
        await ask.send()
        target = ask.dispatches[action_number - 1].run
        assert target.ok is False and not target.outputs
        if reference == 'later-step':
            assert ask.dispatches[1].run.ok is True and len(ask.dispatches[1].run.outputs) == 1
        assert not any(m.get('actionId') == target.occurrence.action_id and m.get('commandId') for m in studio.messages)
        # Goal references are intent, never an exception to the real argument resolver.
        ctx = ask.dispatches[action_number - 1].ctx
        with pytest.raises(ToolFailed):
            typed_step_output(ctx, {'step': action_number, 'output': 0}, kind='presentation')
        projected = target.wire().get('goal')
        assert isinstance(projected, dict), 'MISSING_FEATURE: pre-command refusal must retain original unmet goal'
        assert projected['declaration'] == desired and projected['status'] == 'unmet'
    try:
        asyncio.run(go())
    finally:
        close_app(app)


def test_later_published_output_cannot_satisfy_an_earlier_actions_goal_fence(tmp_path, monkeypatch):
    app, studio = built(tmp_path)

    async def go():
        parent, _ = await _comparison_parent(app)
        source = studio.store().get(WORKSPACE, parent).source.to_wire()
        action = ('present', {'do': 'duplicate', 'what': {'instanceId': parent}})
        desired = {'conditions': [condition('source_exact', [{'step': 2, 'output': 0}],
                                             {'sourceRef': source}, action=1)]}
        ask = PublicAsk(app, studio, monkeypatch, [action, action], desired)
        await ask.send()
        first, later = [trace.run for trace in ask.dispatches]
        assert first.ok is later.ok is True and len(first.outputs) == len(later.outputs) == 1
        assert first.occurrence.action_id != later.occurrence.action_id
        assert first.outputs[0]['instanceId'] != later.outputs[0]['instanceId']
        _, facets = requested(first, desired, 'unmet')
        assert all(row['actionId'] == first.occurrence.action_id for row in facets)
        assert not any(later.outputs[0]['instanceId'] in row['confirmation']['targets'] for row in facets)
        assert not any(m['commandId'] in row['commandIds'] for row in facets for m in studio.messages
                       if m.get('commandId') and m.get('actionId') == later.occurrence.action_id)
    try:
        asyncio.run(go())
    finally:
        close_app(app)
