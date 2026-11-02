"""The owner's Publish, Start from and Update commands over isolated real Project folders; no network or Git."""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator.commands import COMMANDS
from orchestrator.project_repository import ProjectRepository
from orchestrator.tools import ToolError
from tests.test_project_locations import setup

SIGNOFF = {'rights': True, 'noIdentifiableHumanData': True, 'permanent': True}


def host(tmp_path, name):
    folder = tmp_path / name
    folder.mkdir()
    day, locations = setup(folder)
    app = SimpleNamespace(day=day, paths=SimpleNamespace(home=folder / 'host', sessions=locations.legacy))
    return app, day, locations


def run(app, kind, **msg):
    return asyncio.run(COMMANDS[kind](app, None, {'type': kind, **msg}))


def test_publish_to_a_folder_then_start_from_it_on_another_mac(tmp_path):
    app, day, locations = host(tmp_path, 'Mac A')
    other, other_day, other_locations = host(tmp_path, 'Mac B')
    try:
        sid = day.new('Research')
        root = locations.project(sid)
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
        assert copy.origin == root.origin and copy.path.parent == other_locations.parent
        assert (copy.path / 'artifacts/results/count.json').read_text() == '{"cells": 7}'
        events = ProjectRepository(copy).events()
        assert not any('private' in str(e.get('text', '')) for e in events)
        assert events[-1]['type'] == 'project-started-from'
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
