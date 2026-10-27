"""Conversation rename keeps its captured Project; real journals, no app/model."""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator import commands
from orchestrator import tools
from orchestrator.day.store import DayStore


def test_delayed_rename_refuses_another_project_with_same_thread_id(tmp_path):
    original = DayStore(root=tmp_path, day='2026-10-22')
    current = DayStore(root=tmp_path, day='2026-10-22.2')
    assert original.active_id == current.active_id
    before = current.wire()
    with pytest.raises(tools.ToolError, match='Project'):
        asyncio.run(commands.thread(SimpleNamespace(day=current), None, {
            'action': 'rename', 'session': original.sid, 'id': original.active_id, 'title': 'Wrong owner'}))
    assert current.wire() == before
    asyncio.run(commands.thread(SimpleNamespace(day=original), None, {
        'action': 'rename', 'session': original.sid, 'id': original.active_id, 'title': 'Accepted name'}))
    assert original.wire()['threads'][0]['title'] == 'Accepted name'
