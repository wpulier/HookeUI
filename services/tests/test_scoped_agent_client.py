"""Synthetic scope tests; no real connection, model, host data or blind science claim."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

import pytest

from agent_client.scoped import Configuration, Door, ProjectReader, inline_schema
from orchestrator.door.project_actions import descriptors


PROJECT = '2030-01-01'
REFERENCE = {'session': PROJECT, 'id': 'p' + 'a' * 32, 'revision': 1, 'digest': 'b' * 64}
OP = 'op_' + 'a' * 24


def config(**changes):
    return Configuration(**({'cli': Path(sys.executable), 'agent': 'codex', 'connection': 'synthetic',
        'project': PROJECT, 'door_url': 'http://127.0.0.1:1'} | changes))


class FakeDoor:
    def __init__(self):
        self.calls = []
        self.rows = descriptors({'revision': 'test', 'sourceDigest': '1' * 64})
        self.operation = {'id': OP, 'project': PROJECT, 'tool': 'project.state', 'state': 'admitted'}
        self.revoked = False

    async def request(self, method, path, body=None):
        self.calls.append((method, path, deepcopy(body)))
        if self.revoked:
            raise ValueError('revoked')
        if path.startswith('tools?'):
            return {'ok': True, 'contract': '1', 'tools': self.rows, 'page': 1, 'pages': 1}
        if method == 'POST':
            self.operation['tool'] = body['tool']
        return {'ok': True, 'operation': deepcopy(self.operation)}


def test_discovery_only_reads_and_shared_schema():
    async def scenario():
        reader = ProjectReader(config(), FakeDoor())
        rows = await reader.descriptions()
        assert {r['id'] for r in rows} == {'project.state', 'artifact.read'}
        artifact = next(r for r in rows if r['id'] == 'artifact.read')
        resolved = inline_schema(artifact['inputSchema'])
        assert resolved['properties']['reference']['required'] == ['id', 'session', 'revision', 'digest']
        assert '$ref' not in json.dumps(resolved)
    asyncio.run(scenario())


@pytest.mark.parametrize('action', ['project.import', 'project.list', 'project.create', 'workflow',
    '../../status', 'open', 'project.export', 'artifact/read'])
def test_unsupported_actions_never_reach_host(action):
    async def scenario():
        door = FakeDoor()
        reader = ProjectReader(config(), door)
        with pytest.raises(ValueError):
            await reader.read(action, {'file': '/synthetic/forbidden'}, 'synthetic-request')
        assert door.calls == []
    asyncio.run(scenario())


@pytest.mark.parametrize('args', [
    {'reference': REFERENCE | {'session': 'another-project'}},
    {'reference': REFERENCE | {'revision': True}},
    {'reference': REFERENCE | {'id': '../../outside'}},
    {'reference': REFERENCE, 'length': 65537},
    {'reference': REFERENCE, 'file': '/outside'},
    {'reference': REFERENCE, 'session': 'another-project'},
])
def test_bad_reference_or_extra_arguments_never_admit(args):
    async def scenario():
        door = FakeDoor()
        with pytest.raises(ValueError):
            await ProjectReader(config(), door).read('artifact.read', args, 'synthetic-request')
        assert not any(method == 'POST' for method, _, _ in door.calls)
    asyncio.run(scenario())


def test_observation_uses_original_handle_and_refuses_other_operations():
    async def scenario():
        door = FakeDoor()
        reader = ProjectReader(config(), door)
        with pytest.raises(ValueError):
            await reader.observe('op_' + 'c' * 24)
        assert door.calls == []
        first = await reader.read('project.state', {'limit': 4}, 'synthetic-request')
        assert first['operation']['id'] == OP
        assert door.calls[-1][2]['project'] == PROJECT
        assert (await reader.observe(OP))['operation']['id'] == OP
        assert len([c for c in door.calls if c[0] == 'POST']) == 1
        door.operation['project'] = 'another-project'
        with pytest.raises(ValueError):
            await reader.observe(OP)
        door.revoked = True
        with pytest.raises(ValueError):
            await reader.observe(OP)
    asyncio.run(scenario())


def test_same_request_key_reaches_authoritative_server_unchanged():
    async def scenario():
        door = FakeDoor()
        reader = ProjectReader(config(), door)
        for _ in range(2):
            await reader.read('artifact.read', {'reference': REFERENCE}, 'same-request-key')
        posts = [c[2] for c in door.calls if c[0] == 'POST']
        assert posts[0] == posts[1]
        assert posts[0]['args']['reference'] == REFERENCE
    asyncio.run(scenario())


def test_new_server_parameters_cannot_silently_expand_client_scope():
    async def scenario():
        door = FakeDoor()
        row = next(r for r in door.rows if r['id'] == 'project.state')
        row['inputSchema']['properties']['file'] = {'type': 'string'}
        with pytest.raises(ValueError):
            await ProjectReader(config(), door).read('project.state', {'file': '/outside'}, 'synthetic-request')
        assert door.calls == []
    asyncio.run(scenario())


def test_study_locator_is_operator_fixed():
    async def scenario():
        door = FakeDoor()
        reader = ProjectReader(config(study='p' + 'd' * 32), door)
        await reader.study()
        assert door.calls == [('GET', 'studies/p' + 'd' * 32 + '?session=' + PROJECT, None)]
        with pytest.raises(ValueError):
            await ProjectReader(config(), door).study()
    asyncio.run(scenario())


@pytest.mark.parametrize('changes', [{'door_url': 'https://example.com'},
    {'door_url': 'http://127.0.0.1:1/?outside=1'}, {'door_url': 'http://secret@127.0.0.1:1'},
    {'project': '../other'}, {'study': 'p123?session=other'}, {'connection': '--other'}])
def test_operator_configuration_refuses_ambiguous_routes(changes):
    with pytest.raises(ValueError):
        config(**changes)


@pytest.mark.parametrize('ref', ['https://example.com/schema', '../../credentials.schema.json',
                                 'unknown.schema.json'])
def test_schema_resolution_never_reads_external_paths(ref):
    with pytest.raises(ValueError):
        inline_schema({'$ref': ref})


def test_cli_process_receives_json_as_argument_and_no_provider_environment(tmp_path, monkeypatch):
    # An actual child process, but a synthetic stand-in for the CLI/Keychain.
    cli = tmp_path / 'cli'
    cli.write_text('#!' + sys.executable + '\nimport json,sys,os\n'
        'print(json.dumps({"ok":True,"args":sys.argv[1:],"secret":os.getenv("TEST_PROVIDER_SECRET")}))\n')
    cli.chmod(0o700)
    monkeypatch.setenv('TEST_PROVIDER_SECRET', 'synthetic-secret')
    async def scenario():
        result = await Door(config(cli=cli)).request('POST', 'operations', {'text': '$(touch forbidden); `x`'})
        assert result['secret'] is None
        assert json.loads(result['args'][3]) == {'text': '$(touch forbidden); `x`'}
        assert result['args'][-5:] == ['--agent', 'codex', '--connection', 'synthetic', '--json']
    asyncio.run(scenario())


@pytest.mark.parametrize('size', [180000, 3 * 1024 * 1024])
def test_cli_reply_consumes_all_chunks_but_stops_at_bound(tmp_path, size):
    cli = tmp_path / 'cli'
    cli.write_text('#!' + sys.executable + '\nimport json\n'
        f'print(json.dumps({{"ok":True,"content":"x"*{size}}}))\n')
    cli.chmod(0o700)
    async def scenario():
        if size > 2 * 1024 * 1024:
            with pytest.raises(ValueError, match='limit'):
                await Door(config(cli=cli)).request('GET', 'synthetic')
        else:
            result = await Door(config(cli=cli)).request('GET', 'synthetic')
            assert len(result['content']) == size
    asyncio.run(scenario())


def test_cancelling_observation_reaps_only_its_cli_child(tmp_path):
    from tests.waiting import until_async
    ready = tmp_path / 'ready'
    cli = tmp_path / 'cli'
    cli.write_text('#!' + sys.executable + '\nimport os,time\nfrom pathlib import Path\n'
        f'Path({str(ready)!r}).write_text(str(os.getpid()))\ntime.sleep(120)\n')
    cli.chmod(0o700)
    async def scenario():
        task = asyncio.create_task(Door(config(cli=cli)).request('GET', 'synthetic'))
        try:
            await until_async(ready.exists)
            pid = int(ready.read_text())
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    asyncio.run(scenario())
