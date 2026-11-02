"""The owner's Publish, Start from and Update commands over isolated real Project folders; no network or Git."""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator.commands import COMMANDS
from orchestrator.project_repository import ProjectRepository
from orchestrator.project_stores import ProjectStores
from orchestrator.scientific_projects import Inherited, ProjectStore
from orchestrator.tools import ToolError
from tests.test_project_locations import setup

SIGNOFF = {'rights': True, 'noIdentifiableHumanData': True, 'permanent': True}


def host(tmp_path, name):
    folder = tmp_path / name
    folder.mkdir()
    day, locations = setup(folder)
    app = SimpleNamespace(day=day, paths=SimpleNamespace(home=folder / 'host', sessions=locations.legacy))
    return app, day, locations


def stores(app, locations):
    return ProjectStores(ProjectStore(app.paths.home / 'results/projects'), locations)


NOTE = {'kind': 'note', 'title': 'Question', 'state': {'text': 'Which cells divide?'}, 'resources': [], 'runtimes': []}


def run(app, kind, **msg):
    return asyncio.run(COMMANDS[kind](app, None, {'type': kind, **msg}))


def test_publish_to_a_folder_then_start_from_it_on_another_mac(tmp_path):
    app, day, locations = host(tmp_path, 'Mac A')
    other, other_day, other_locations = host(tmp_path, 'Mac B')
    try:
        sid = day.new('Research')
        root = locations.project(sid)
        note = stores(app, locations).create(sid, NOTE)
        (root.path / 'artifacts' / 'results').mkdir(parents=True, exist_ok=True)
        (root.path / 'artifacts' / 'results' / 'count.json').write_text('{"cells": 7}')
        day._record({'type': 'chat', 'role': 'user', 'text': 'keep this conversation private'}, strict=True)
        review = run(app, 'project-publish-prepare', session=sid)['review']
        assert review['licence'] == 'CC-BY-4.0' and not review['blocked'] and review['memberCount'] > 0
        destination = {'kind': 'folder', 'path': str(tmp_path / 'Shared drive'), 'create': True}
        with pytest.raises(ToolError, match='right to share'):
            run(app, 'project-publish', session=sid, staging=review['staging'], reviewDigest=review['reviewDigest'],
                signoff={**SIGNOFF, 'rights': False}, destination=destination)
        sent = run(app, 'project-publish', session=sid, staging=review['staging'],
                   reviewDigest=review['reviewDigest'], signoff=SIGNOFF, destination=destination)
        assert sent['ok'] and sent['published']['commit']
        status = run(app, 'project-remote-status', session=sid)
        assert [p['commit'] for p in status['published']] == [sent['published']['commit']]
        started = run(other, 'project-start-from', source={'kind': 'folder', 'path': str(tmp_path / 'Shared drive')},
                      name='Research copy')
        copy = other_locations.project(started['project']['session'])
        assert copy.origin != root.origin and copy.path.parent == other_locations.parent
        listed = stores(other, other_locations).list(copy.session)
        assert [(r.id, r.digest, r.owner) for r in listed] == [(note.id, note.digest, sid)]
        assert (copy.path / 'artifacts/results/count.json').read_text() == '{"cells": 7}'
        events = ProjectRepository(copy).events()
        assert not any('private' in str(e.get('text', '')) for e in events)
        assert events[-1]['type'] == 'project-started-from' and events[-1]['origin'] == root.origin
        # The same Mac holds the original and a copy of its publication side by side.
        beside = run(app, 'project-start-from', source={'kind': 'folder', 'path': str(tmp_path / 'Shared drive')},
                     name='Research copy')['project']
        assert beside['session'] != sid and beside['origin'] not in (root.origin, copy.origin)
        here = stores(app, locations)
        assert [r.id for r in here.list(beside['session'])] == [note.id]
        from orchestrator.resources import Index
        entry = Index.project_entry(here.list(beside['session'])[0])
        assert entry.source['inheritedFrom'] == sid and 'read-only' in entry.summary
        assert here.read(note.id, sid).digest == note.digest  # the original's own handle still reads the original
        with pytest.raises(Inherited):
            here.save(note.id, beside['session'], {**NOTE, 'title': 'Changed'}, expected_revision=1)
        # A newer publication comes into the open copy through Update.
        here.save(note.id, sid, {**NOTE, 'title': 'Revised question'}, expected_revision=1)
        review = run(app, 'project-publish-prepare', session=sid)['review']
        run(app, 'project-publish', session=sid, staging=review['staging'], reviewDigest=review['reviewDigest'],
            signoff=SIGNOFF, destination={'kind': 'folder', 'path': str(tmp_path / 'Shared drive')})
        other_day.open(copy.session, 'test')
        update = run(other, 'project-update-from', session=copy.session)['update']
        assert update['extended'] == [note.id] and not update['refused']
        assert stores(other, other_locations).read(note.id, copy.session).document['title'] == 'Revised question'
    finally:
        locations.close()
        other_locations.close()


def test_a_legacy_project_is_asked_to_move_into_a_folder_first(tmp_path):
    app, day, locations = host(tmp_path, 'Mac A')
    try:
        with pytest.raises(ToolError, match='Project folder'):
            run(app, 'project-publish-prepare', session=day.sid)
    finally:
        locations.close()


def test_a_stale_review_name_is_refused(tmp_path):
    app, day, locations = host(tmp_path, 'Mac A')
    try:
        sid = day.new('Research')
        with pytest.raises(ToolError, match='Review'):
            run(app, 'project-publish', session=sid, staging='../elsewhere', reviewDigest='0' * 64,
                signoff=SIGNOFF, destination={'kind': 'folder', 'path': str(tmp_path / 'x')})
    finally:
        locations.close()
