"""Real isolated retained bytes and workflows; no live-user or scientific quality claim."""
import pytest

from orchestrator.day.store import SessionStore
from orchestrator.project_locations import ProjectLocations
from orchestrator.project_repository import ProjectRepository
from orchestrator.project_roots import ProjectLocationError, ProjectRegistry, ProjectRoot
from orchestrator.project_stores import ProjectStores
from orchestrator.scientific_file_intake import FileIntake
from orchestrator.scientific_projects import ProjectStore, InvalidProject
from orchestrator.scientific_comparisons import identity
from orchestrator.workflows import Workflows


def setup(tmp_path):
    host = tmp_path / 'host'
    locations = ProjectLocations(host / 'sessions', ProjectRegistry(host), tmp_path / 'Projects')
    day = SessionStore(locations.legacy, session='2026-10-29', locations=locations)
    stores = ProjectStores(ProjectStore(host / 'results/projects'), locations)
    return day, stores, locations


def document(title='Protocol'):
    return {'kind': 'note', 'title': title, 'state': {'text': 'Inspect exact evidence'}, 'resources': [], 'runtimes': []}


def test_captured_import_and_workflow_stay_with_original_project_after_switch(tmp_path):
    day, stores, locations = setup(tmp_path)
    try:
        first = day.new('Matrix study')
        intake = FileIntake(stores, first, 'a'*32, name='source.bin', size=5, max_bytes=5)
        second = day.new('Microscopy')
        try:
            intake.write(b'abcde'); saved = intake.publish()
        finally:
            intake.close()
        assert saved.session == first and day.sid == second
        ref = saved.document['resources'][0]
        assert stores.file_source(saved.id, first, 1, ref).read(0, 5) == b'abcde'
        assert not stores.list(second) and not list(stores.legacy.root.glob('p*'))
        w = Workflows(stores)
        graph = {'format': 1, 'input': {'kind': 'scientific-scene'}, 'nodes': [
            {'id': 'review', 'name': 'Inspect', 'kind': 'review', 'needs': ['input'], 'settings': {}}]}
        definition = w.create('Review retained file', graph, 'b'*32)
        run = w.start(definition.id, 1, first, [identity(saved)], 'c'*32, actor='studio')
        claim = w.claim(run.id, first)
        w.finish(claim, {'status': 'awaiting-review', 'output': identity(saved)})
        w.review(run.id, first, claim['attempt']['id'], identity(saved), True, 'd'*32)
        assert w.status(run.id, first)['status'] == 'completed'
        assert not stores.list(second)
        root = locations.project(first)
        assert {r.id for r in ProjectRepository(root).documents()[0]} == {r.id for r in stores.list(first)}
        assert w.library.root == stores.legacy.root.parent / 'workflow-library'
        assert not (root.path / 'artifacts/workflow-library').exists()
    finally:
        locations.close()


def test_move_reopen_keeps_all_scientific_revisions_and_captured_store_refuses(tmp_path):
    day, stores, locations = setup(tmp_path)
    sid = day.new('Revision history')
    first = stores.create(sid, document())
    changed = stores.save(first.id, sid, document('Reviewed protocol'), expected_revision=1)
    captured = stores.for_session(sid)
    root = locations.project(sid)
    locations.close()
    moved = tmp_path / 'Moved research'
    root.path.rename(moved)
    with pytest.raises(ProjectLocationError):
        captured.save(first.id, sid, document('late write'), expected_revision=2)
    fresh = ProjectLocations(locations.legacy, locations.registry, locations.parent)
    try:
        fresh.registry.register(ProjectRoot.open(moved), replace_inactive=True)
        reopened = ProjectStores(stores.legacy, fresh)
        assert reopened.read(first.id, sid, revision=1) == first
        assert reopened.read(first.id, sid, revision=2) == changed
        assert reopened.save(first.id, sid, document('Continued'), expected_revision=2).revision == 3
        assert not (locations.legacy / sid).exists()
    finally:
        fresh.close()


def test_second_host_reads_without_runtime_or_writer_but_cannot_publish(tmp_path):
    day, stores, locations = setup(tmp_path)
    sid = day.new('Owned')
    first = stores.create(sid, document())
    second = ProjectLocations(locations.legacy, locations.registry, locations.parent)
    try:
        other = ProjectStores(ProjectStore(tmp_path / 'another/results'), second)
        assert other.read(first.id, sid) == first
        assert not other.for_session(sid).runtime_root
        assert not list(locations.project(sid).path.rglob('.runtime-roots.json'))
        with pytest.raises(ProjectLocationError, match='read-only'):
            other.save(first.id, sid, document('forbidden'), expected_revision=1)
        assert stores.read(first.id, sid) == first
    finally:
        second.close(); locations.close()


def test_bound_store_rejects_another_project_and_router_refuses_unowned_paths(tmp_path):
    day, stores, locations = setup(tmp_path)
    try:
        a = day.new('A'); b = day.new('B')
        bound = stores.for_session(a)
        with pytest.raises(InvalidProject, match='another Project'):
            bound.create(b, document())
        with pytest.raises(InvalidProject, match='captured Project'):
            _ = stores.root
        with pytest.raises(InvalidProject, match='captured Project'):
            stores._folder('p'+'0'*32)
        assert not stores.list(a) and not stores.list(b)
    finally:
        locations.close()


def test_scientific_export_reads_bound_files_without_selecting_the_project(tmp_path):
    from orchestrator.scientific_exchange import export, reimport
    day, stores, locations = setup(tmp_path)
    try:
        sid = day.new('Retained source')
        intake = FileIntake(stores, sid, 'e'*32, name='source.bin', size=5, max_bytes=5)
        try:
            intake.write(b'abcde'); saved = intake.publish()
        finally:
            intake.close()
        other = day.new('Currently open')
        bundle = tmp_path / 'scientific.zip'
        export(stores, saved.id, sid, destination=bundle, sessions=locations)
        target = ProjectStore(tmp_path / 'imported')
        reimport(target, bundle)
        restored = target.read(saved.id, sid, revision=1)
        assert restored == saved and day.sid == other
        assert target.file_source(saved.id, sid, 1, saved.document['resources'][0]).read(0, 5) == b'abcde'
    finally:
        locations.close()


def test_native_review_and_publication_keep_original_arrays_after_switch(tmp_path):
    import numpy as np
    from tests.test_scientific_arrays import draft, fill, accept
    from tests.test_scientific_edits import display, values
    from orchestrator.scientific_edits import ViewEdits
    day, stores, locations = setup(tmp_path)
    try:
        sid = day.new('Microscopy')
        original = stores.create(sid, {'kind':'scene', 'title':'Labels', 'state':{}, 'resources':[], 'runtimes':[]})
        data = np.arange(24, dtype='>u2').reshape(4, 6)
        ref = fill(draft(stores, original, data, chunks=(2,3)), data)
        accepted = accept(stores, original, ref)
        owner = ViewEdits(stores, accepted.id, sid, accepted.revision, ref)
        edit = owner.begin(display(owner), max_written_bytes=12)
        other = day.new('Other work')
        edit.write([0,1], np.full((2,3), 65, dtype='>u2'))
        changed = edit.commit()
        assert changed.session == sid and day.sid == other
        assert np.array_equal(values(stores, accepted, ref), data)
        expected = data.copy(); expected[:2,3:] = 65
        assert np.array_equal(values(stores, changed, changed.document['resources'][0]), expected)
        assert stores.list(other) == []
        owner.close()
    finally:
        locations.close()
