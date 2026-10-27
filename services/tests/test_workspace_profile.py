"""Presentation namespaces work without enabling controlled review execution seams."""
import asyncio

from orchestrator.commands import handle
from orchestrator.paths import Paths
from orchestrator.workspace_profile import profile_id
from tests.test_original_picture_analysis import prepare
from tests.test_sources import Client


def test_normal_profile_keeps_legacy_namespace_without_enabling_delays(tmp_path, monkeypatch):
    monkeypatch.delenv('MICROMIND_WORKSPACE_STUDY', raising=False)
    built, worker, aid, image = prepare(tmp_path, monkeypatch)
    client = Client()
    asyncio.run(handle(built, client, {'type': 'workspace-profile', 'requestId': 'profile'}))
    reply = client.replies[-1]
    assert reply['ok'] and not reply['review']
    assert reply['profile'] == profile_id(built.paths)
    assert str(tmp_path) not in str(reply)
    monkeypatch.setenv('MICROMIND_WORKSPACE_STUDY', '1')
    from orchestrator.workspace_study import profile_id as legacy_id
    assert reply['profile'] == legacy_id(built.paths)
    assert reply['profile'] != profile_id(Paths.under(tmp_path / 'another-profile'))
