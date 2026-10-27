"""TEST-only goal declaration contract through existing parsing and request APIs.

Canned external responses and the unchanged synthetic Screen rig are inputs.
No proof/outcome, native window success, action handler, Brain/Agent method,
executor, admission or goal-verification helper is manufactured/replaced.
The current source has no installed whole-goal verification interface: these
tests establish declaration retention/rejection, not verified completion.
"""
import copy
import json
from types import SimpleNamespace

import pytest

from orchestrator.brain.dispatch import answer_phase
from orchestrator.brain.fast import decide
from orchestrator.brain.llm import FunctionCallingBrain, _normalise
from orchestrator.commands import COMMANDS
from orchestrator.ticket import current, issue, using
from orchestrator.vision import VisionResult, _parse_json
from tests.fakes import Hub
from tests.test_brain_dispatch_budget import admitted, run, world
from tests.test_screen_workspace import REF, Rig, jpeg
from tests.test_workspace_request_observation import RequestRig


# Literal desired conditions, not a confirmation or a builder-derived oracle.
GOAL = {'conditions': [{'action': 1, 'predicate': 'zoom', 'targets': ['window-a'],
    'expected': {'value': 2.0}, 'essential': True, 'requiredPhase': 'drawn', 'tolerance': None}]}
EASIER = {'conditions': [{'action': 1, 'predicate': 'visible', 'targets': ['window-a'],
    'expected': {'value': True}, 'essential': True, 'requiredPhase': 'drawn', 'tolerance': None}]}
ACTION = {'tool': 'present', 'args': {'what': 'window-a', 'do': 'zoom', 'value': '2'}}
READ = {'tool': 'resources', 'args': {}}
ANSWER = 'Attempting the declared window change; completion has not been verified.'


def reply(goal=None, *, actions=(), scope=False):
    data = {'answer': ANSWER, 'targets': [], 'actions': copy.deepcopy(list(actions)),
            'intent': 'request', 'needs_look': False}
    if goal is not None:
        data['goal'] = copy.deepcopy(goal)
    if scope:
        data.update(observation_scope='structural-workspace', input_refs=[])
    return data


@pytest.mark.parametrize('scope', [False, True])
@pytest.mark.parametrize('selector', ['pinned-id', 'declared-output'])
def test_actual_json_provider_parser_retains_optional_goal_input(scope, selector):
    """Catches a JSON parser whitelist silently dropping declared metadata."""
    desired = copy.deepcopy(GOAL)
    if selector == 'declared-output':
        desired['conditions'][0]['targets'] = [{'step': 1, 'output': 0}]
    original = reply(desired, actions=[ACTION], scope=scope)
    parsed = _parse_json(json.dumps(original))
    assert parsed['goal'] == desired
    assert parsed['actions'] == [ACTION]
    original['goal']['conditions'][0]['expected']['value'] = 99
    assert parsed['goal']['conditions'][0]['expected'] == {'value': 2.0}


@pytest.mark.parametrize('scope', [False, True])
@pytest.mark.parametrize('selector', ['pinned-id', 'declared-output'])
def test_actual_fc_normalisation_retains_goal_without_answer_text_action_smuggling(scope, selector):
    """Catches dropping an unscoped goal or taking actions from assistant text."""
    desired = copy.deepcopy(GOAL)
    desired['conditions'][0]['action'] = 2  # present follows the actual resources call
    if selector == 'declared-output':
        desired['conditions'][0]['targets'] = [{'step': 1, 'output': 0}]
    declared = reply(desired, actions=[{'tool': 'erase_everything', 'args': {}}], scope=scope)
    calls = [('resources', {'kind': 'result'}),
             ('outline', {'targets': []}), ('present', ACTION['args'])]
    parsed = _normalise(json.dumps(declared), calls)
    assert parsed['actions'] == [
        {'tool': 'resources', 'args': {'kind': 'result'}}, ACTION]
    assert parsed.get('goal') == desired, 'MISSING_FEATURE: real FC normalisation dropped the closed declaration'
    assert parsed['answer'] == ANSWER
    assert not any(key in parsed['goal'] for key in ('commandId', 'confirmation', 'clientEpoch'))


@pytest.mark.parametrize('provider', ['gemini', 'openai'])
def test_fc_sdk_response_runs_actual_normalisation_and_original_answer_admission(monkeypatch, provider):
    """Catches provider-specific bypass of metadata parsing or admission."""
    from orchestrator.brain.registry import Registry
    text = json.dumps(reply(GOAL, actions=[{'tool': 'smuggled', 'args': {}}]))
    if provider == 'gemini':
        from google import genai
        response = SimpleNamespace(model_version='synthetic-fc-response',
            candidates=[SimpleNamespace(content=SimpleNamespace(parts=[
                SimpleNamespace(text=text, thought=False)]))],
            function_calls=[SimpleNamespace(name='present', args=copy.deepcopy(ACTION['args']))])

        async def send(**_kwargs):
            return response

        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=send)))
        monkeypatch.setenv('GEMINI_API_KEY', 'synthetic-test-value')
        monkeypatch.setattr(genai, 'Client', lambda **_kwargs: client)
    else:
        import openai
        response = SimpleNamespace(model='synthetic-fc-response', output_text=text,
            output=[SimpleNamespace(type='function_call', name='present',
                                    arguments=json.dumps(ACTION['args']))])

        async def send(**_kwargs):
            return response

        client = SimpleNamespace(responses=SimpleNamespace(create=send))
        monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-test-value')
        monkeypatch.setattr(openai, 'AsyncOpenAI', lambda **_kwargs: client)
    request = issue(Hub(), 'turn')

    async def scenario():
        with using(request), answer_phase('look'):
            return await FunctionCallingBrain().ask(
                provider + ':synthetic@low', jpeg(), 'Synthetic FC input.', Registry())

    try:
        parsed, _used = run(scenario())
        assert request.admission.used == 1
        assert request.admission.attempts[0].status == 'done'
        assert parsed['actions'] == [ACTION]
        assert parsed.get('goal') == GOAL, 'MISSING_FEATURE: actual FC adapter did not retain the goal'
        assert parsed['answer'] == ANSWER
    finally:
        request.seal()


@pytest.mark.parametrize('scope', [False, True])
def test_actual_fast_decision_retains_detached_goal_and_existing_allowance(tmp_path, monkeypatch, scope):
    external = reply(GOAL, actions=[ACTION], scope=scope)

    async def script(_entry):
        return external

    brain, hub, providers, _ = world(tmp_path, monkeypatch, script, fast=True)
    request = issue(hub, 'hands')

    async def scenario():
        with using(request), answer_phase('hands'):
            return await decide(brain.moment(), 'Arbitrary original request.',
                brain.agent.vision(hub.settings.fast), brain.registry.prompt_section(), hub.settings.fast)

    try:
        decision = run(scenario())
        assert decision.actions == [('present', ACTION['args'])]
        assert getattr(decision, 'goal', None) == GOAL, 'MISSING_FEATURE: actual fast decision dropped the goal'
        external['goal']['conditions'][0]['expected']['value'] = 99
        assert decision.goal == GOAL, 'MISSING_FEATURE: fast decision retained mutable provider goal authority'
        assert admitted(providers).used == 1
    finally:
        request.seal()


def invalid_goals():
    unknown_predicate = copy.deepcopy(GOAL)
    unknown_predicate['conditions'][0]['predicate'] = 'semantic_success'
    unknown_key = copy.deepcopy(GOAL)
    unknown_key['conditions'][0]['confirmation'] = {'status': 'confirmed'}
    invented_authority = copy.deepcopy(GOAL)
    invented_authority.update(commandId='model-command', clientEpoch='model-epoch')
    optional = copy.deepcopy(GOAL)
    optional['conditions'][0]['essential'] = False
    bad_selector = copy.deepcopy(GOAL)
    bad_selector['conditions'][0]['targets'] = [{'step': 1, 'output': 0, 'source': 'fallback'}]
    oversized_targets = copy.deepcopy(GOAL)
    oversized_targets['conditions'][0]['targets'] = ['window-' + str(n) for n in range(9)]
    return [unknown_predicate, unknown_key, invented_authority, optional, bad_selector,
            oversized_targets, {'conditions': []}, {'conditions': GOAL['conditions'] * 17}]


@pytest.mark.parametrize('invalid', invalid_goals(), ids=[
    'unknown-predicate', 'unknown-key', 'invented-authority', 'all-optional',
    'invalid-selector', 'too-many-targets', 'empty', 'too-many-conditions'])
@pytest.mark.parametrize('mode', ['fast', 'fc'])
def test_invalid_declaration_is_not_retained_as_an_admitted_goal(tmp_path, monkeypatch, invalid, mode):
    """Paired valid controls prevent this negative case certifying wholesale dropping.

    No verified-goal state exists yet; absence of an admitted declaration is the
    available observation, not fabricated downstream proof of goal rejection.
    """
    if mode == 'fc':
        parsed = _normalise(json.dumps(reply(invalid, actions=[ACTION], scope=True)),
                            [('present', copy.deepcopy(ACTION['args']))])
        assert parsed.get('goal') is None
        assert parsed['actions'] == [ACTION]
        return

    async def script(_entry):
        return reply(invalid, actions=[ACTION], scope=True)

    brain, hub, providers, _ = world(tmp_path, monkeypatch, script, fast=True)
    request = issue(hub, 'hands')

    async def scenario():
        with using(request), answer_phase('hands'):
            return await decide(brain.moment(), 'Arbitrary original request.',
                brain.agent.vision(hub.settings.fast), brain.registry.prompt_section(), hub.settings.fast)

    try:
        decision = run(scenario())
        assert getattr(decision, 'goal', None) is None
        assert admitted(providers).used == 1
    finally:
        request.seal()


def test_absent_goal_preserves_legacy_fc_and_fast_actions(tmp_path, monkeypatch):
    parsed = _normalise('Legacy response.', [('present', copy.deepcopy(ACTION['args']))])
    assert parsed['answer'] == 'Legacy response.' and parsed['actions'] == [ACTION]
    assert parsed.get('goal') is None

    async def script(_entry):
        return reply(actions=[ACTION])

    brain, hub, providers, _ = world(tmp_path, monkeypatch, script, fast=True)
    request = issue(hub, 'hands')

    async def scenario():
        with using(request), answer_phase('hands'):
            return await decide(brain.moment(), 'Arbitrary original request.',
                brain.agent.vision(hub.settings.fast), brain.registry.prompt_section(), hub.settings.fast)

    try:
        decision = run(scenario())
        assert decision.answer == ANSWER and decision.actions == [('present', ACTION['args'])]
        assert getattr(decision, 'goal', None) is None
        assert admitted(providers).used == 1
    finally:
        request.seal()


class GoalRig(Rig):
    """Actual App with unchanged canonical synthetic authority and real handlers."""
    def __init__(self, tmp_path, monkeypatch, *, camera, continuation=False, quick=False):
        super().__init__(tmp_path)
        self.accept()
        settings = self.app.hub.settings
        settings.fast, settings.compare = 'gemini-3.5-flash' if quick else 'off', ''
        settings.pointing, settings.closeup, settings.outcome_lines = 'box', 'single', 'off'
        self.surface_allowed = False
        self.dispatches, self.sends = [], []
        self.external = reply(GOAL, actions=[READ] if quick else
            [ACTION, READ] if continuation else [ACTION], scope=True)
        original_start = self.app.brain.registry.start

        def trace_start(name, args, ctx, run=None):
            actual = original_start(name, args, ctx, run=run)
            self.dispatches.append(SimpleNamespace(name=name, ctx=ctx, run=actual, ticket=current(),
                declaration=copy.deepcopy(getattr(ctx, 'goal', None))))
            return actual

        monkeypatch.setattr(self.app.brain.registry, 'start', trace_start)

        async def ask(images, prompt, **_kwargs):
            ticket = current()
            self.sends.append(SimpleNamespace(ticket=ticket, admission=ticket.admission,
                phase=ticket.admission.attempts[-1].phase, images=tuple(images)))
            if self.sends[-1].phase in ('look', 'fast'):
                data = self.external
            else:
                # Exercise both continuation and synthesis attempts to relax the
                # original essential goal, without any claimed verified effect.
                data = reply(EASIER, actions=[READ] if continuation and len(self.sends) == 2 else [], scope=True)
            return VisionResult(data=data, latency_ms=0, model='explicitly-canned-external-provider')

        monkeypatch.setattr(self.app.agent, 'vision', lambda _option: SimpleNamespace(ask=ask))
        self.use_camera = camera

    async def send(self):
        if self.use_camera:
            await RequestRig.camera(self)  # unchanged synthetic frame/real Hub analysis helper
        raw = self.message(seq=11)
        await self.ingest(raw)
        return await COMMANDS['ask'](self.app, self.client,
            {'type': 'ask', 'text': 'Apply the declared change to the original window.', 'screen': raw})

    def close(self):
        self.app.hub._analysis.shutdown(wait=True)
        self.app.hub._background.shutdown(wait=True)


@pytest.mark.parametrize('camera,quick', [(False, False), (True, False), (True, True)],
    ids=['notes-fast-reconstruction', 'json-agent-look', 'camera-fast-reconstruction'])
def test_original_actual_dispatch_retains_detached_goal_and_origin(tmp_path, monkeypatch, camera, quick):
    """Catches goal loss between real planner reply and original Registry dispatch."""
    rig = GoalRig(tmp_path, monkeypatch, camera=camera, quick=quick)
    try:
        result = run(rig.send())
        assert rig.dispatches, 'BEHAVIOR: actual Registry did not receive the selected action'
        first = rig.dispatches[0]
        assert first.name == ('resources' if quick else 'present') and first.run.occurrence is not None
        assert first.declaration == GOAL, 'MISSING_FEATURE: actual original dispatch lost the declared goal'
        assert first.ctx.client is rig.client and first.ctx.workspace_id == REF.workspace_id
        assert first.ctx.request_snapshot.observation.wire()['seq'] == 11
        assert first.ctx.request_id == first.run.occurrence.request_id == first.ticket.admission.request_id
        assert first.ctx.admission is first.ticket.admission
        rig.external['goal']['conditions'][0]['expected']['value'] = 99
        assert getattr(first.ctx, 'goal', None) == GOAL, 'MISSING_FEATURE: original context aliases provider data'
        assert all(send.admission is first.ctx.admission for send in rig.sends)
        assert first.ctx.admission.used == len(rig.sends) == result['calls'] <= 3
    finally:
        rig.close()


@pytest.mark.parametrize('camera', [False, True], ids=['notes-fast-continuation', 'json-continuation'])
def test_continuation_and_synthesis_cannot_replace_original_essential_goal(tmp_path, monkeypatch, camera):
    """Uses actual failing present and actual resource reads, never fake success."""
    rig = GoalRig(tmp_path, monkeypatch, camera=camera, continuation=True)
    try:
        result = run(rig.send())
        assert len(rig.sends) >= 2, 'BEHAVIOR: real query continuation was not reached'
        assert len(rig.dispatches) >= 3, 'BEHAVIOR: continuation did not dispatch its real resource read'
        first = rig.dispatches[0]
        for dispatched in rig.dispatches:
            assert dispatched.declaration == GOAL, 'MISSING_FEATURE: continuation replaced/lost the original essential goal'
            assert dispatched.ctx is first.ctx
            assert dispatched.run.occurrence.request_id == first.run.occurrence.request_id
            assert dispatched.ctx.request_snapshot is first.ctx.request_snapshot
        assert getattr(first.ctx, 'goal', None) == GOAL, 'MISSING_FEATURE: synthesis weakened the original goal'
        assert rig.dispatches[-1].run.occurrence.action_id != rig.dispatches[1].run.occurrence.action_id
        assert first.ctx.admission.used == len(rig.sends) == result['calls'] <= 3
    finally:
        rig.close()


# Appended TEST-only ordinal regressions. The original cases/helpers above are frozen.
def ordinal_reply(position, reference, malformed, *, template=GOAL):
    """Literal external slots; never use the production parser to make the oracle."""
    desired = copy.deepcopy(template)
    if reference == 'condition-action':
        desired['conditions'][0]['action'] = 2
    else:
        desired['conditions'][0]['action'] = 1
        desired['conditions'][0]['targets'] = [{'step': 2, 'output': 0}]
    raw = [malformed, ACTION, READ] if position == 'leading' else [ACTION, malformed, READ]
    return reply(desired, actions=raw, scope=True)


def valid_multi_reply(reference):
    desired = copy.deepcopy(GOAL)
    desired['conditions'][0]['action'] = 2  # the actual present after the resource read
    if reference == 'output-step':
        desired['conditions'][0]['targets'] = [{'step': 2, 'output': 0}]
    return reply(desired, actions=[READ, ACTION], scope=True)


def query_provider(rig, monkeypatch, initial, query):
    """Only external parsed responses are scripted; existing real dispatch is retained."""
    sends = []

    async def ask(images, prompt, **_kwargs):
        ticket = current()
        phase = ticket.admission.attempts[-1].phase
        sends.append(SimpleNamespace(ticket=ticket, admission=ticket.admission,
                                     phase=phase, images=tuple(images)))
        data = initial if phase in ('look', 'fast') else query if phase == 'query' else reply(scope=True)
        return VisionResult(data=data, latency_ms=0, model='explicitly-canned-external-provider')

    monkeypatch.setattr(rig.app.agent, 'vision', lambda _option: SimpleNamespace(ask=ask))
    return sends


@pytest.mark.parametrize('position', ['leading', 'middle'])
@pytest.mark.parametrize('reference', ['condition-action', 'output-step'])
@pytest.mark.parametrize('malformed', [None, {'args': {}}], ids=['null-entry', 'missing-tool'])
def test_fast_rejects_goal_when_lenient_raw_action_parsing_shifts_ordinals(
        tmp_path, monkeypatch, position, reference, malformed):
    """Catches admission against compacted actions instead of original raw slots."""
    external = ordinal_reply(position, reference, malformed)

    async def script(_entry):
        return external

    brain, hub, providers, _ = world(tmp_path, monkeypatch, script, fast=True)
    request = issue(hub, 'hands')

    async def scenario():
        with using(request), answer_phase('hands'):
            return await decide(brain.moment(), 'An unchanged arbitrary request.',
                brain.agent.vision(hub.settings.fast), brain.registry.prompt_section(), hub.settings.fast)

    try:
        decision = run(scenario())
        assert decision.actions == [('present', {'what': 'window-a', 'do': 'zoom', 'value': '2'}),
                                    ('resources', {})], 'BEHAVIOR: legacy lenient actions must remain executable'
        assert decision.goal is None, 'MISSING_FEATURE: Fast admitted a goal whose raw action/output ordinals shifted'
        assert admitted(providers).used == 1
    finally:
        request.seal()


@pytest.mark.parametrize('position', ['leading', 'middle'])
@pytest.mark.parametrize('reference', ['condition-action', 'output-step'])
@pytest.mark.parametrize('malformed', [None, {'args': {}}], ids=['null-entry', 'missing-tool'])
def test_public_json_initial_rejects_shifted_goal_before_actual_dispatch(
        tmp_path, monkeypatch, position, reference, malformed):
    """The real Agent/Brain/Registry must not retain compacted ordinal authority."""
    rig = GoalRig(tmp_path, monkeypatch, camera=True)
    rig.external = ordinal_reply(position, reference, malformed)
    try:
        result = run(rig.send())
        assert [d.name for d in rig.dispatches] == ['present', 'resources']
        assert rig.dispatches[1].run.ok is True, 'BEHAVIOR: unchanged real resource handler must execute'
        assert all(d.declaration is None for d in rig.dispatches), (
            'MISSING_FEATURE: JSON initial goal was retained at dispatch after raw positions changed')
        first = rig.dispatches[0]
        assert first.ctx.goal is None
        assert first.ctx.client is rig.client and first.ctx.request_snapshot.observation.wire()['seq'] == 11
        assert all(d.run.occurrence.request_id == first.ctx.request_id for d in rig.dispatches)
        assert first.ctx.admission.used == len(rig.sends) == result['calls'] <= 3
    finally:
        rig.close()


@pytest.mark.parametrize('position', ['leading', 'middle'])
@pytest.mark.parametrize('reference', ['condition-action', 'output-step'])
@pytest.mark.parametrize('malformed', [None, {'args': {}}], ids=['null-entry', 'missing-tool'])
def test_public_json_query_rejects_first_goal_when_raw_positions_changed(
        tmp_path, monkeypatch, position, reference, malformed):
    """No original goal: query continuation must validate before its first admission."""
    rig = GoalRig(tmp_path, monkeypatch, camera=True)
    sends = query_provider(rig, monkeypatch, reply(actions=[READ], scope=True),
                           ordinal_reply(position, reference, malformed))
    try:
        result = run(rig.send())
        assert any(s.phase == 'query' for s in sends), 'BEHAVIOR: actual query continuation was not reached'
        assert [d.name for d in rig.dispatches] == ['resources', 'present', 'resources']
        assert rig.dispatches[0].run.ok is True and rig.dispatches[2].run.ok is True
        assert all(d.declaration is None for d in rig.dispatches), (
            'MISSING_FEATURE: query admitted its first goal against a compacted raw action list')
        first = rig.dispatches[0]
        assert first.ctx.goal is None and all(d.ctx is first.ctx for d in rig.dispatches)
        assert all(d.run.occurrence.request_id == first.ctx.request_id for d in rig.dispatches)
        assert len({d.run.occurrence.action_id for d in rig.dispatches}) == 3
        assert first.ctx.admission.used == len(sends) == result['calls'] <= 3
    finally:
        rig.close()


@pytest.mark.parametrize('position', ['leading', 'middle'])
@pytest.mark.parametrize('reference', ['condition-action', 'output-step'])
def test_malformed_query_keeps_first_valid_goal_and_immutable_original_context(
        tmp_path, monkeypatch, position, reference):
    """Rejection of a later declaration must not clear or weaken earlier intent."""
    rig = GoalRig(tmp_path, monkeypatch, camera=True)
    initial = valid_multi_reply('condition-action')
    desired = copy.deepcopy(initial['goal'])
    query = ordinal_reply(position, reference, {'args': {}}, template=EASIER)
    sends = query_provider(rig, monkeypatch, initial, query)
    try:
        result = run(rig.send())
        assert [d.name for d in rig.dispatches] == ['resources', 'present', 'present', 'resources']
        assert any(s.phase == 'query' for s in sends)
        first = rig.dispatches[0]
        assert all(d.declaration == desired for d in rig.dispatches)
        assert all(d.ctx is first.ctx and d.ctx.request_snapshot is first.ctx.request_snapshot
                   for d in rig.dispatches)
        initial['goal']['conditions'][0]['expected']['value'] = 99
        query['goal']['conditions'][0]['expected']['value'] = False
        projected = first.ctx.goal
        projected['conditions'][0]['expected']['value'] = 101
        assert first.ctx.goal == desired
        assert all(d.run.occurrence.request_id == first.ctx.request_id for d in rig.dispatches)
        assert len({d.run.occurrence.action_id for d in rig.dispatches}) == 4
        assert first.ctx.admission.used == len(sends) == result['calls'] <= 3
    finally:
        rig.close()


@pytest.mark.parametrize('reference', ['condition-action', 'output-step'])
def test_fully_valid_multi_action_fast_goal_preserves_original_ordinals(tmp_path, monkeypatch, reference):
    external = valid_multi_reply(reference)
    desired = copy.deepcopy(external['goal'])

    async def script(_entry):
        return external

    brain, hub, providers, _ = world(tmp_path, monkeypatch, script, fast=True)
    request = issue(hub, 'hands')

    async def scenario():
        with using(request), answer_phase('hands'):
            return await decide(brain.moment(), 'An unchanged arbitrary request.',
                brain.agent.vision(hub.settings.fast), brain.registry.prompt_section(), hub.settings.fast)

    try:
        decision = run(scenario())
        assert decision.actions == [('resources', {}),
                                    ('present', {'what': 'window-a', 'do': 'zoom', 'value': '2'})]
        assert decision.goal == desired, 'BEHAVIOR: valid multi-action goal must remain usable'
        assert admitted(providers).used == 1
    finally:
        request.seal()


@pytest.mark.parametrize('phase', ['initial', 'query'])
@pytest.mark.parametrize('reference', ['condition-action', 'output-step'])
def test_public_json_valid_multi_action_goal_is_retained_before_dispatch(tmp_path, monkeypatch, phase, reference):
    rig = GoalRig(tmp_path, monkeypatch, camera=True)
    declared = valid_multi_reply(reference)
    desired = copy.deepcopy(declared['goal'])
    initial = declared if phase == 'initial' else reply(actions=[READ], scope=True)
    query = reply(scope=True) if phase == 'initial' else declared
    sends = query_provider(rig, monkeypatch, initial, query)
    try:
        result = run(rig.send())
        names = ['resources', 'present'] if phase == 'initial' else ['resources', 'resources', 'present']
        assert [d.name for d in rig.dispatches] == names
        admitted_dispatches = rig.dispatches if phase == 'initial' else rig.dispatches[1:]
        assert all(d.declaration == desired for d in admitted_dispatches), (
            'BEHAVIOR: valid original positions must retain a usable goal at actual dispatch')
        first = rig.dispatches[0]
        assert first.ctx.goal == desired and all(d.ctx is first.ctx for d in rig.dispatches)
        assert first.ctx.client is rig.client and first.ctx.request_snapshot.observation.wire()['seq'] == 11
        assert first.ctx.admission.used == len(sends) == result['calls'] <= 3
    finally:
        rig.close()


@pytest.mark.parametrize('reference', ['condition-action', 'output-step'])
def test_actual_fc_multi_call_order_retains_goal_despite_malformed_text_actions(monkeypatch, reference):
    """Only actual FC calls define positions; malformed assistant-text actions are ignored."""
    import openai
    from orchestrator.brain.registry import Registry

    declared = valid_multi_reply(reference)
    desired = copy.deepcopy(declared['goal'])
    declared['actions'] = [None, {'tool': 'smuggled', 'args': {}}, ACTION]
    declared.pop('observation_scope')
    declared.pop('input_refs')
    response = SimpleNamespace(model='synthetic-fc-response', output_text=json.dumps(declared),
        output=[SimpleNamespace(type='function_call', name='resources', arguments='{}'),
                SimpleNamespace(type='function_call', name='outline', arguments='{"targets":[]}'),
                SimpleNamespace(type='function_call', name='present', arguments=json.dumps(ACTION['args']))])

    async def send(**_kwargs):
        return response

    client = SimpleNamespace(responses=SimpleNamespace(create=send))
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-test-value')
    monkeypatch.setattr(openai, 'AsyncOpenAI', lambda **_kwargs: client)
    request = issue(Hub(), 'turn')

    async def scenario():
        with using(request), answer_phase('look'):
            return await FunctionCallingBrain().ask(
                'openai:synthetic@low', jpeg(), 'Synthetic FC input.', Registry())

    try:
        parsed, _used = run(scenario())
        assert parsed['actions'] == [READ, ACTION]
        assert parsed['goal'] == desired and parsed['answer'] == ANSWER
        assert request.admission.used == 1 and request.admission.attempts[0].status == 'done'
    finally:
        request.seal()
