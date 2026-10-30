"""Real application/door storage with synthetic connections; no live-client qualification."""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.app import App
from orchestrator.door import api
from orchestrator.origin import studio_origin
from orchestrator.paths import Paths
from orchestrator.project_locations import ProjectLocations
from orchestrator.project_roots import ProjectRegistry, ProjectRoot, ProjectLocationError, writer
from orchestrator.project_repository import ProjectRepository
from tests.test_agent_door import BASE, HEADER, connect, bearer, window_key  # noqa: F401
from tests.test_agent_projects import run


def build(paths, parent):
    locations = ProjectLocations(paths.sessions, ProjectRegistry(paths.home), parent)
    return App.build('http://127.0.0.1:1', paths, use_gpu=False, project_locations=locations)


def door(built):
    http = FastAPI()
    api.register(http, built, studio_origin, HEADER)
    return TestClient(http, base_url=BASE)


def close(built):
    asyncio.run(built.stop())


def test_app_connector_creation_import_study_and_relocation_keep_exact_history(tmp_path):
    paths = Paths.under(tmp_path / 'host')
    parent = tmp_path / 'Projects'
    built = build(paths, parent)
    try:
        original = built.day.sid
        with door(built) as client:
            h = bearer(connect(client)['token'])
            made = run(client, h, 'project.create', {'name': 'Portable research'}, 'portable-create')
            sid = made['outcome']['project']['session']
            assert built.day.sid == original
            root = built.day.locations.project(sid)
            assert root.path.parent == parent
            source = tmp_path / 'protocol.txt'
            source.write_text('Compare exact retained evidence. Synthetic integration data.')
            imported = run(client, h, 'project.import', {'file': str(source)}, 'portable-import', sid)
            ref = imported['outcome']['artifact']
            created = client.post('/api/agent/v1/studies', headers=h, json={
                'session': sid, 'requestKey': 'portable-study', 'title': 'Retained evidence',
                'objective': 'Compare saved inputs', 'criteria': 'Exact historical revisions',
                'budgetUsd': 0, 'inputs': [{k: ref[k] for k in ('id', 'revision', 'digest')}]})
            assert created.status_code == 200, created.text
            study = created.json()['study']
            evidence = client.get(f"/api/agent/v1/studies/{study['id']}?session={sid}", headers=h)
            assert evidence.status_code == 200, evidence.text
            before = evidence.json()
            saved = built.hub.resources.projects.read(ref['id'], sid, revision=ref['revision'])
            raw = built.hub.resources.projects.file_source(saved.id, sid, saved.revision,
                saved.document['state']['sourceFile']['reference']).read(0, source.stat().st_size)
            source.unlink()
            assert not list((paths.results / 'projects').glob('p*'))
        with pytest.raises(ProjectLocationError, match='read-only'):
            with writer(root): pass
        built.day.switch_session(sid)
        assert built.day.sid == sid
        events = ProjectRepository(root).events()
    finally:
        close(built)
    with writer(root): pass
    moved = tmp_path / 'Moved Project'
    root.path.rename(moved)
    registry = ProjectRegistry(paths.home)
    registry.register(ProjectRoot.open(moved), replace_inactive=True)
    restarted = build(paths, parent)
    try:
        assert restarted.day.sid == sid
        with door(restarted) as client:
            # The original host connection remains host-local; nothing was carried in the folder.
            after = client.get(f"/api/agent/v1/studies/{study['id']}?session={sid}", headers=h)
            assert after.status_code == 200, after.text
            assert after.json() == before
            current = restarted.hub.resources.projects.read(ref['id'], sid, revision=ref['revision'])
            assert current == saved
            assert restarted.hub.resources.projects.file_source(current.id, sid, current.revision,
                current.document['state']['sourceFile']['reference']).read(0, len(raw)) == raw
            assert ProjectRepository(ProjectRoot.open(moved)).events() == events
            assert not list(moved.rglob('projects.json'))  # host grants are not research records
            assert not list(moved.rglob('preferences.jsonl'))
    finally:
        close(restarted)


def test_failed_app_build_releases_admitted_project_writer(tmp_path, monkeypatch):
    paths = Paths.under(tmp_path / 'host')
    built = build(paths, tmp_path / 'Projects')
    owner = built.day.create_retained('Preserved', actor='user')
    sid = owner.sid
    root = built.day.locations.project(sid)
    close(built)
    locations = ProjectLocations(paths.sessions, ProjectRegistry(paths.home), tmp_path / 'Projects')
    locations.writable(sid)
    def fail(*args, **kwargs):
        raise RuntimeError('injected startup failure')
    monkeypatch.setattr(App, '_build', fail)
    with pytest.raises(RuntimeError, match='startup failure'):
        App.build('http://127.0.0.1:1', paths, use_gpu=False, project_locations=locations)
    assert locations.closed
    with writer(root): pass


def test_app_rejects_other_host_resolver_without_closing_it(tmp_path):
    paths = Paths.under(tmp_path / 'host')
    locations = ProjectLocations(tmp_path / 'unrelated/sessions', ProjectRegistry(tmp_path / 'other'), tmp_path / 'Projects')
    try:
        with pytest.raises(ProjectLocationError, match='this host'):
            App.build('http://127.0.0.1:1', paths, use_gpu=False, project_locations=locations)
        assert not locations.closed
    finally:
        locations.close()
