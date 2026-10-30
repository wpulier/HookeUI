"""Host-owned byte snapshots; not complete dependency collection or live UI proof."""
import pytest

from orchestrator.project_folder_copy import copy_owned_snapshot, copy_snapshot, verify_snapshot
from orchestrator.project_repository import ProjectRepository
from orchestrator.project_roots import COPY_INTENT, ProjectLocationError, writer
from tests.test_project_locations import setup


def test_host_can_snapshot_retained_research_without_releasing_its_writer(tmp_path):
    day, locations = setup(tmp_path)
    try:
        project = day.create_retained('Research', actor='user')
        root = locations.project(project.sid)
        project._record({'type':'chat','text':'Original question'})
        project._record({'type':'workflow-observed','operation':'original-run','state':'running'})
        original = ProjectRepository(root).events()
        # The host retains its lease; another host cannot start a competing copy.
        with pytest.raises(ProjectLocationError, match='read-only'):
            copy_snapshot(root, tmp_path / 'Other host')
        copied = copy_owned_snapshot(locations, project.sid, tmp_path / 'Snapshot')
        assert ProjectRepository(copied).events() == original
        assert verify_snapshot(copied.path)['externalDependencies'] == 'not-verified'
        with pytest.raises(ProjectLocationError, match='read-only'):
            with writer(root): pass
        project._record({'type':'chat','text':'Later research continues in the original'})
        assert ProjectRepository(copied).events() == original
        assert ProjectRepository(root).events() != original
        assert any(e.get('state') == 'running' for e in ProjectRepository(copied).events())
        with pytest.raises(ProjectLocationError, match='snapshot'):
            with writer(copied): pass
    finally:
        locations.close()


def test_observed_source_change_leaves_incomplete_copy_without_stopping_original_work(tmp_path):
    day, locations = setup(tmp_path)
    try:
        project = day.create_retained('Research', actor='user')
        root = locations.project(project.sid)
        destination = tmp_path / 'Changed'
        changed = False
        def change():
            nonlocal changed
            if destination.exists() and not changed:
                changed = True
                project._record({'type':'chat','text':'Research changed during snapshot'})
        with pytest.raises(ProjectLocationError, match='changed'):
            copy_owned_snapshot(locations, project.sid, destination, check=change)
        assert (destination / COPY_INTENT).is_file()
        assert any(e.get('text') == 'Research changed during snapshot' for e in ProjectRepository(root).events())
        assert verify_snapshot(copy_owned_snapshot(locations, project.sid, tmp_path / 'Retry').path)
    finally:
        locations.close()


def test_closed_or_legacy_locations_never_start_snapshot(tmp_path):
    day, locations = setup(tmp_path)
    destination = tmp_path / 'No copy'
    with pytest.raises(ProjectLocationError, match='legacy'):
        copy_owned_snapshot(locations, day.sid, destination)
    project = day.create_retained('Research', actor='user')
    locations.close()
    with pytest.raises(ProjectLocationError, match='closed'):
        copy_owned_snapshot(locations, project.sid, destination)
    assert not destination.exists()


def test_shutdown_during_snapshot_preserves_incomplete_marker(tmp_path):
    day, locations = setup(tmp_path)
    project = day.create_retained('Research', actor='user')
    target = tmp_path / 'Stopped'
    def stopped():
        if target.exists(): locations.close()  # isolated scope has no background producers
    with pytest.raises(ProjectLocationError, match='closed'):
        copy_owned_snapshot(locations, project.sid, target, check=stopped)
    assert (target / COPY_INTENT).exists()


def test_bundle_publisher_cannot_enter_during_owned_copy(tmp_path, monkeypatch):
    import threading
    from orchestrator import project_folder_copy as copies
    from orchestrator.project_repository import document_root, session_root
    from orchestrator.scientific_import_transaction import publishing, _visibility_lock, ImportPending
    from orchestrator.file_locks import fcntl
    from tests.waiting import HANG_S
    day, locations = setup(tmp_path)
    project = day.create_retained('Research', actor='user')
    root = locations.project(project.sid)
    roots = (document_root(root), session_root(root))
    start, probed, finished = threading.Event(), threading.Event(), threading.Event()
    errors = []
    refused = []
    def publish():
        try:
            assert start.wait(HANG_S)
            for path in roots:
                try:
                    with _visibility_lock(path, fcntl.LOCK_EX | fcntl.LOCK_NB): pass
                except ImportPending:
                    refused.append(path)
            probed.set()
            with publishing(*roots):
                # Real publication cannot begin until the copy exits both guards.
                finished.set()
        except BaseException as error:
            errors.append(error)
            probed.set()
    thread = threading.Thread(target=publish, daemon=True)
    thread.start()
    verify = copies.verify_owner
    def after_owner(captured):
        result = verify(captured)
        start.set()
        assert probed.wait(HANG_S)
        assert refused == list(roots)
        assert not finished.is_set()
        return result
    monkeypatch.setattr(copies, 'verify_owner', after_owner)
    try:
        copied = copy_owned_snapshot(locations, project.sid, tmp_path / 'Protected')
        thread.join(HANG_S)
        assert not thread.is_alive() and not errors and finished.is_set()
        monkeypatch.setattr(copies, 'verify_owner', verify)
        assert verify_snapshot(copied.path)
    finally:
        start.set()
        thread.join(HANG_S)
        locations.close()


@pytest.mark.parametrize('section', ['artifacts/records', 'history/sessions'])
def test_pending_bundle_is_refused_before_snapshot_destination(tmp_path, section):
    from orchestrator.scientific_import_transaction import INTENT, ImportPending
    day, locations = setup(tmp_path)
    try:
        project = day.create_retained('Research', actor='user')
        root = locations.project(project.sid)
        (root.path / section / INTENT).write_text('{"format":1}')
        destination = tmp_path / 'Refused'
        with pytest.raises(ImportPending):
            copy_owned_snapshot(locations, project.sid, destination)
        assert not destination.exists()
    finally:
        locations.close()
