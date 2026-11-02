"""Actual SDK protocol, synthetic door. Install the optional agent-client group."""
import asyncio
import json

import pytest

pytest.importorskip('mcp', reason='optional agent-client group; CI installs all groups')
from mcp import Client

from agent_client.__main__ import server_for
from agent_client.scoped import ProjectReader
from tests.test_scoped_agent_client import FakeDoor, config, REFERENCE, OP


def test_sdk_discovery_reads_and_explicit_refusals():
    async def scenario():
        door = FakeDoor()
        reader = ProjectReader(config(study='p' + 'c' * 32), door)
        async with Client(server_for(reader)) as client:
            listed = await client.list_tools()
            assert {t.name for t in listed.tools} == {'project_state', 'artifact_read', 'observe_read', 'read_study'}
            # Actual protocol call bypassing advertised names still refuses.
            denied = await client.call_tool('project_import', {'file': '/outside'})
            assert denied.is_error
            assert not any(c[0] == 'POST' for c in door.calls)
            denied = await client.call_tool('artifact_read', {'arguments': {
                'reference': REFERENCE | {'session': 'foreign'}}, 'requestKey': 'synthetic-request'})
            assert denied.is_error
            allowed = await client.call_tool('artifact_read', {'arguments': {
                'reference': REFERENCE}, 'requestKey': 'synthetic-request'})
            assert not allowed.is_error
            assert json.loads(allowed.content[0].text)['operation']['id'] == OP
            observed = await client.call_tool('observe_read', {'id': OP})
            assert not observed.is_error
            denied = await client.call_tool('read_study', {'session': 'foreign'})
            assert denied.is_error
            door.revoked = True
            denied = await client.call_tool('observe_read', {'id': OP})
            assert denied.is_error
    asyncio.run(scenario())


def test_sdk_does_not_expose_host_failure_details():
    class BadDoor(FakeDoor):
        async def request(self, method, path, body=None):
            if method == 'POST':
                raise OSError('synthetic confidential host detail')
            return await super().request(method, path, body)
    async def scenario():
        async with Client(server_for(ProjectReader(config(), BadDoor()))) as client:
            result = await client.call_tool('project_state', {
                'arguments': {}, 'requestKey': 'synthetic-request'})
            assert result.is_error
            assert 'confidential' not in result.content[0].text
    asyncio.run(scenario())
