"""Synthetic workflow scope/continuation, not runtime confinement or scientific quality."""
import asyncio
from copy import deepcopy
import json

import pytest

from agent_client.research import ProjectResearcher, WorkflowPin, load_pins, load_admissions
from tests.test_scoped_agent_client import FakeDoor, config, PROJECT, REFERENCE, OP

STUDY = 'p' + 'c' * 32
PIN = WorkflowPin('p' + 'd' * 32, 1, 'e' * 64)
ARGS = {'workflow': PIN.id, 'revision': 1, 'inputs': [REFERENCE]}


class ResearchDoor(FakeDoor):
    def __init__(self):
        super().__init__()
        self.definition = {**vars(PIN), 'name': 'Synthetic method', 'graph': {'nodes': []}}
        self.experiments = {}

    async def request(self, method, path, body=None):
        if self.revoked:
            raise ValueError('revoked')
        self.calls.append((method, path, deepcopy(body)))
        if path.startswith('workflows/'):
            return {'ok': True, 'workflow': deepcopy(self.definition)}
        if path.startswith('studies/'):
            return {'ok': True, 'study': {'id': STUDY, 'experiments': deepcopy(self.experiments)}}
        if method == 'POST' and path == 'operations':
            args = deepcopy(body['args'])
            args.pop('session')
            args.setdefault('bindings', {})
            self.operation = {'id': OP, 'tool': 'workflow', 'project': PROJECT, 'params': args,
                              'state': 'running', 'by': 'agent:original', 'byLabel': 'Original contributor'}
            self.experiments['retained-request'] = {'operation': OP, 'specification': deepcopy(args)}
        elif path.endswith('/cancel'):
            self.operation['state'] = 'cancelRequested'
        return {'ok': True, 'operation': deepcopy(self.operation)}


def client(door):
    return ProjectResearcher(config(study=STUDY), (PIN,), door)


def test_workflow_pins_checked_before_admission_and_request_preserved():
    async def scenario():
        door = ResearchDoor()
        reader = client(door)
        assert (await reader.workflows())['workflows'][0]['digest'] == PIN.digest
        for _ in range(2):
            result = await reader.run(ARGS, 'same-admission')
            assert result['operation']['id'] == OP
        posts = [b for m, p, b in door.calls if m == 'POST' and p == 'operations']
        assert posts[0] == posts[1]
        assert posts[0]['args']['study'] == STUDY
        assert posts[0]['args']['session'] == PROJECT
        door.definition['digest'] = 'f' * 64
        with pytest.raises(ValueError, match='qualified identity'):
            await reader.run(ARGS, 'another-request')
        assert len([c for c in door.calls if c[0] == 'POST']) == 2
    asyncio.run(scenario())


@pytest.mark.parametrize('arguments', [
    ARGS | {'workflow': 'p' + 'f' * 32}, ARGS | {'revision': 2},
    ARGS | {'session': 'foreign'}, ARGS | {'study': 'p' + 'f' * 32},
    ARGS | {'params': {'code': 'anything'}}, ARGS | {'file': '/outside'},
    ARGS | {'inputs': [REFERENCE | {'session': 'foreign'}]},
    ARGS | {'bindings': {'matrix': '/outside'}},
    ARGS | {'bindings': {'matrix': REFERENCE | {'session': 'foreign'}}},
    ARGS | {'bindings': {'matrix': REFERENCE | {'path': '/outside'}}},
])
def test_unqualified_workflow_arguments_never_reach_door(arguments):
    async def scenario():
        door = ResearchDoor()
        with pytest.raises(ValueError):
            await client(door).run(arguments, 'synthetic-request')
        assert door.calls == []
    asyncio.run(scenario())


def test_fresh_client_recovers_original_study_execution_without_replay_or_relabel():
    async def scenario():
        door = ResearchDoor()
        await client(door).run(ARGS, 'synthetic-request')
        reader = client(door)
        before = len(door.calls)
        recovered = await reader.observe(OP)
        assert recovered['operation']['byLabel'] == 'Original contributor'
        assert all(m == 'GET' for m, _, _ in door.calls[before:])
        cancelled = await reader.cancel(OP)
        assert cancelled['operation']['state'] == 'cancelRequested'
        assert len([c for c in door.calls if c[0] == 'POST' and c[1] == 'operations']) == 1
        door.revoked = True
        with pytest.raises(ValueError):
            await reader.observe(OP)
    asyncio.run(scenario())


def test_fresh_client_refuses_unlinked_or_mismatched_execution():
    async def scenario():
        door = ResearchDoor()
        await client(door).run(ARGS, 'synthetic-request')
        with pytest.raises(ValueError, match='not linked'):
            await client(door).observe('op_' + 'c' * 24)
        door.operation['params']['inputs'] = [REFERENCE | {'revision': 2}]
        with pytest.raises(ValueError, match='Study admission'):
            await client(door).observe(OP)
        door.operation['params']['study'] = 'p' + 'f' * 32
        with pytest.raises(ValueError, match='another Study'):
            await client(door).observe(OP)
    asyncio.run(scenario())


def test_observation_refuses_swapped_operation_id():
    async def scenario():
        door = ResearchDoor()
        reader = client(door)
        await reader.run(ARGS, 'synthetic-request')
        door.operation['id'] = 'op_' + 'f' * 24
        with pytest.raises(ValueError, match='identity changed'):
            await reader.observe(OP)
    asyncio.run(scenario())


def test_claim_cannot_forge_authorship_or_use_another_project():
    async def scenario():
        door = ResearchDoor()
        reader = client(door)
        body = {'requestKey': 'synthetic-claim', 'statement': 'Limited observation',
                'evidence': [{'artifact': REFERENCE, 'relation': 'input'}],
                'dependsOn': [], 'limitations': 'Synthetic evidence only'}
        for forged in (body | {'by': 'user'}, body | {'evidence': [{
                'artifact': REFERENCE | {'session': 'foreign'}, 'relation': 'supports'}]}):
            with pytest.raises(ValueError):
                await reader.publish(forged)
        assert door.calls == []
        await reader.publish(body)
        assert door.calls == [('POST', 'studies/' + STUDY + '/claims', body | {'session': PROJECT})]
    asyncio.run(scenario())


def test_operator_scope_file_is_bounded_and_has_only_immutable_pins(tmp_path):
    path = tmp_path / 'scope.json'
    path.write_text(json.dumps({'format': 1, 'workflows': [vars(PIN)]}))
    assert load_pins(path) == (PIN,)
    for value in ({'format': True, 'workflows': [vars(PIN)]},
                  {'format': 1, 'workflows': [vars(PIN)] * 2},
                  {'format': 1, 'workflows': []},
                  {'format': 1, 'workflows': [vars(PIN)], 'project': 'outside'}):
        path.write_text(json.dumps(value))
        with pytest.raises(ValueError):
            load_pins(path)
    path.write_text(' ' * 65537)
    with pytest.raises(ValueError):
        load_pins(path)


def test_sdk_exposes_execution_only_when_operator_enabled():
    pytest.importorskip('mcp', reason='optional agent-client group; CI installs all groups')
    from mcp import Client
    from agent_client.__main__ import server_for
    async def scenario():
        door = ResearchDoor()
        # Supply discovery through the existing synthetic door, otherwise use the
        # execution spy above. This tests real SDK validation, not real science.
        original = door.request
        async def request(method, path, body=None):
            if path.startswith('tools?'):
                return {'ok': True, 'contract': '1', 'tools': door.rows, 'page': 1, 'pages': 1}
            return await original(method, path, body)
        door.request = request
        async with Client(server_for(client(door))) as sdk:
            tools = {t.name for t in (await sdk.list_tools()).tools}
            assert {'run_workflow', 'publish_finding', 'cancel_operation', 'observe_operation', 'list_workflows'} <= tools
            denied = await sdk.call_tool('run_workflow', {'arguments': ARGS | {'params': {}}, 'requestKey': 'sdk-request'})
            assert denied.is_error
            assert door.calls == []
            result = await sdk.call_tool('run_workflow', {'arguments': ARGS, 'requestKey': 'sdk-request'})
            assert not result.is_error
            assert json.loads(result.content[0].text)['operation']['id'] == OP
            result = await sdk.call_tool('cancel_operation', {'id': OP})
            assert not result.is_error
    asyncio.run(scenario())


def test_fixed_admission_survives_client_restart_and_lost_reply_without_new_key():
    async def scenario():
        door = ResearchDoor()
        scope = [{'requestKey': 'one-permitted-run', 'arguments': deepcopy(ARGS)}]
        reader = ProjectResearcher(config(study=STUDY), (PIN,), door, admissions=scope)
        # Freeze at startup; mutating the caller's configuration object cannot widen it.
        scope[0]['arguments']['inputs'][0]['revision'] = 99
        shown = await reader.workflows()
        assert shown['admissions'][0]['arguments']['inputs'] == [REFERENCE]
        original = door.request
        lost = True
        async def request(method, path, body=None):
            nonlocal lost
            result = await original(method, path, body)
            if method == 'POST' and path == 'operations' and lost:
                lost = False
                raise OSError('reply lost after admission')
            return result
        door.request = request
        with pytest.raises(OSError):
            await reader.run(ARGS, 'one-permitted-run')
        fresh = ProjectResearcher(config(study=STUDY), (PIN,), door,
            admissions=[{'requestKey': 'one-permitted-run', 'arguments': ARGS}])
        result = await fresh.run(ARGS | {'bindings': {}}, 'one-permitted-run')
        assert result['operation']['id'] == OP
        before = len(door.calls)
        for args, key in [(ARGS, 'another-key'),
                          (ARGS | {'inputs': [REFERENCE | {'revision': 2}]}, 'one-permitted-run')]:
            with pytest.raises(ValueError, match='allowance'):
                await fresh.run(args, key)
        assert len(door.calls) == before
        posts = [b for m, p, b in door.calls if m == 'POST' and p == 'operations']
        # The supported door owns actual idempotency; neither client changes its key/input intent.
        assert len(posts) == 2
        assert {p['requestKey'] for p in posts} == {'one-permitted-run'}
        assert posts[0] == posts[1]
    asyncio.run(scenario())


def test_zero_admissions_allows_existing_evidence_and_claims_but_no_execution():
    async def scenario():
        door = ResearchDoor()
        await client(door).run(ARGS, 'existing-run')
        reviewer = ProjectResearcher(config(study=STUDY), (PIN,), door, admissions=[])
        assert not reviewer.can_admit
        assert (await reviewer.workflows())['admissions'] == []
        assert (await reviewer.observe(OP))['operation']['id'] == OP
        before = len(door.calls)
        with pytest.raises(ValueError, match='allowance'):
            await reviewer.run(ARGS, 'existing-run')
        assert len(door.calls) == before
    asyncio.run(scenario())


@pytest.mark.parametrize('scope', [
    {}, [{'requestKey': 'allowed-key'}], [{'requestKey': 'allowed-key', 'arguments': ARGS, 'extra': 1}],
    [{'requestKey': 'allowed-key', 'arguments': ARGS}] * 2,
    [{'requestKey': 'allowed-key', 'arguments': ARGS | {'params': {}}}],
    [{'requestKey': 'allowed-key', 'arguments': ARGS | {'inputs': [REFERENCE | {'session': 'foreign'}]}}],
])
def test_malformed_admission_scope_refuses_before_any_door_request(scope):
    door = ResearchDoor()
    with pytest.raises(ValueError):
        ProjectResearcher(config(study=STUDY), (PIN,), door, admissions=scope)
    assert door.calls == []


def test_admission_file_is_bounded_and_empty_is_explicit_read_only(tmp_path):
    path = tmp_path / 'allowance.json'
    path.write_text(json.dumps({'format': 1, 'admissions': []}))
    assert load_admissions(path) == []
    for body in ({'format': True, 'admissions': []}, {'format': 1, 'admissions': [], 'extra': 1},
                 {'format': 1, 'admissions': [None] * 33}, {'format': 1, 'admissions': {}}):
        path.write_text(json.dumps(body))
        with pytest.raises(ValueError):
            load_admissions(path)
    path.write_text(' ' * 65537)
    with pytest.raises(ValueError):
        load_admissions(path)


def test_sdk_hides_execution_for_empty_admission_allowance():
    pytest.importorskip('mcp', reason='optional agent-client group; CI installs all groups')
    from mcp import Client
    from agent_client.__main__ import server_for
    async def scenario():
        door = ResearchDoor()
        original = door.request
        async def request(method, path, body=None):
            if path.startswith('tools?'):
                return {'ok': True, 'contract': '1', 'tools': door.rows, 'page': 1, 'pages': 1}
            return await original(method, path, body)
        door.request = request
        reader = ProjectResearcher(config(study=STUDY), (PIN,), door, admissions=[])
        async with Client(server_for(reader)) as sdk:
            names = {t.name for t in (await sdk.list_tools()).tools}
            assert 'run_workflow' not in names
            assert {'read_study', 'observe_operation', 'publish_finding'} <= names
            denied = await sdk.call_tool('run_workflow', {'arguments': ARGS, 'requestKey': 'blocked-request'})
            assert denied.is_error
            assert not any(method == 'POST' for method, _, _ in door.calls)
    asyncio.run(scenario())
