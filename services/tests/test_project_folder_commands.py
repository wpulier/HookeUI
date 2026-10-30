"""Owner UI commands over isolated real Project journals; no Finder or user data."""
import asyncio
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from orchestrator.commands import COMMANDS
from orchestrator.door import schema
from orchestrator.project_storage_status import describe
from orchestrator.tools import ToolError
from tests.test_project_locations import setup
from tests.waiting import HANG_S, until_async


def test_inspector_matches_connector_projection_and_reveals_only_retained_location(tmp_path, monkeypatch):
    day, locations = setup(tmp_path)
    try:
        sid = day.new('Research')
        app = SimpleNamespace(day=day)
        calls = []
        monkeypatch.setattr(sys, 'platform', 'darwin')
        monkeypatch.setattr(subprocess, 'Popen', lambda args, **kw: calls.append(args))
        before = day.journal.path.read_bytes()
        reply = asyncio.run(COMMANDS['project-storage'](app, None, {'session': sid}))
        assert reply['ok'] is True  # required by the real Studio request transport
        status = reply['storage']
        schema.check(status, 'project-storage')
        assert status == describe(day, sid)
        result = asyncio.run(COMMANDS['project-reveal'](app, None, {'session': sid, 'path': '/untrusted'}))
        assert result == {'ok': True, 'path': str(locations.project(sid).path)}
        assert calls == [['open', '-R', status['location']]]
        assert day.journal.path.read_bytes() == before
    finally:
        locations.close()


def test_inspector_missing_location_never_reveals_or_creates_an_empty_replacement(tmp_path, monkeypatch):
    day, locations = setup(tmp_path)
    try:
        sid = day.new('Research')
        root = locations.project(sid)
        root.path.rename(tmp_path / 'Moved')
        calls = []
        monkeypatch.setattr(subprocess, 'Popen', lambda args, **kw: calls.append(args))
        with pytest.raises(ToolError):
            asyncio.run(COMMANDS['project-reveal'](SimpleNamespace(day=day), None, {'session': sid}))
        assert not calls and not root.path.exists() and not (day.root / sid).exists()
    finally:
        locations.close()


def test_inspector_refuses_stale_project_and_away_back_during_read(tmp_path, monkeypatch):
    from orchestrator import project_storage_status
    day, locations = setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = project_storage_status.describe
    def delayed(*args):
        entered.set()
        assert release.wait(HANG_S)
        return original(*args)
    try:
        sid = day.new('Research')
        other = day.create_retained('Other', actor='user').sid
        app = SimpleNamespace(day=day)
        with pytest.raises(ToolError, match='Project changed'):
            asyncio.run(COMMANDS['project-storage'](app, None, {'session': other}))
        monkeypatch.setattr(project_storage_status, 'describe', delayed)
        async def scenario():
            pending = asyncio.create_task(COMMANDS['project-reveal'](app, None, {'session': sid}))
            await until_async(entered.is_set)
            day.switch_session(other); day.switch_session(sid)
            release.set()
            with pytest.raises(ToolError, match='Project changed'):
                await pending
        asyncio.run(scenario())
    finally:
        release.set(); locations.close()
