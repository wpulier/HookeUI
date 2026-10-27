"""Rejected commands remain inspectable before a model/tool can start."""
import asyncio
import json
from types import SimpleNamespace

from orchestrator import commands, execution


def test_the_users_studio_actions_are_logged_beside_the_apps(monkeypatch):
    """Live 2026-10-11: a clear of every outline arrived while a request was outlining, and nothing said who sent
    it. A studio action that changes the board is a record of its own; a screen report is not."""
    logged = []
    app = SimpleNamespace(agent=SimpleNamespace(log=logged.append), hub=SimpleNamespace(), day=SimpleNamespace(local_only=False))
    client = SimpleNamespace(offer=lambda _reply: None)

    async def done(*_):
        return {'ok': True}

    monkeypatch.setitem(commands.COMMANDS, 'clear', done)
    monkeypatch.setitem(commands.COMMANDS, 'screen', done)
    asyncio.run(commands._handle(app, client, {'type': 'clear', 'kind': 'cell', 'requestId': 'r1'}))
    asyncio.run(commands._handle(app, client, {'type': 'screen', 'items': []}))
    assert logged == [{'kind': 'command', 'command': 'clear', 'ok': True, 'args': {'kind': 'cell'}}]


def test_admission_refusal_records_safe_code_without_payload(monkeypatch):
    log = execution.ExecutionLog(enabled=True)
    replies = []
    app = SimpleNamespace(day=SimpleNamespace(local_only=False))
    client = SimpleNamespace(offer=replies.append)

    async def rejected(*_):
        return {'ok': False, 'code': 'SOURCE_UNRESOLVED',
                'error': 'Private source transport detail must not enter the execution trace'}

    monkeypatch.setitem(commands.COMMANDS, 'ask', rejected)
    with log.request('ask') as root:
        asyncio.run(commands._handle(app, client, {'type': 'ask', 'requestId': 'request-1'}))
    result = log.inspect(root.id)
    assert result['root']['outcome'] == 'failed'
    assert any(step['operation'] == 'command-rejected' and step['rejection'] == 'SOURCE_UNRESOLVED'
               and step['outcome'] == 'failed' for step in result['steps'])
    assert 'Private source transport' not in json.dumps(result)
    assert json.loads(replies[0])['code'] == 'SOURCE_UNRESOLVED'


def test_a_refused_window_change_keeps_its_reason_in_its_record_and_its_trace(monkeypatch):
    """The demo of 2026-10-11: 10 of the user's window changes were refused in under 35 ms, and the command record kept
    only `ok`. A refusal's own words (a failed run's summary when the reply has no error), its code and what to do next
    are in the record, and its code in the trace."""
    log = execution.ExecutionLog(enabled=True)
    logged, replies = [], []
    app = SimpleNamespace(agent=SimpleNamespace(log=logged.append), hub=SimpleNamespace(), day=SimpleNamespace(local_only=False))
    client = SimpleNamespace(offer=replies.append)
    said = "the magnifier is at least 280 px wide; say a width of 280 px or more"

    async def refused(*_):
        return {"ok": False, "summary": said, "run": {
            "tool": "present", "ok": False, "summary": said, "next": "say a width of 280 px or more",
            "outcome": {"status": "failed", "failure": {"code": "REQUEST_CANCELED", "reason": "not this"}}}}

    monkeypatch.setitem(commands.COMMANDS, 'present', refused)
    with log.request('present') as root:
        asyncio.run(commands._handle(app, client, {'type': 'present', 'schema': 'presentation-manual-v2', 'op': 'resize',
                                                   'requestId': 'r1'}))
    record, = logged
    assert record['ok'] is False and record['error'] == said and record['code'] == 'REQUEST_CANCELED'
    assert record['next'] == 'say a width of 280 px or more' and record['args']['op'] == 'resize'
    assert any(step['operation'] == 'command-rejected' and step['rejection'] == 'REQUEST_CANCELED'
               for step in log.inspect(root.id)['steps'])
    assert 'code' not in json.loads(replies[0])  # the reply the studio gets is unchanged
    assert commands.refusal({'ok': True, 'summary': 'resized'}) == {}
