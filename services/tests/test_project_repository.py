"""Real existing stores in isolated Project folders; not live App/connector qualification."""
import json
import shutil

import pytest

from orchestrator.day.prefs import Prefs
from orchestrator.project_repository import ProjectRepository, create
from orchestrator.project_roots import ProjectLocationError, ProjectRegistry, ProjectRoot
from orchestrator.scientific_projects import Conflict


def setup(tmp_path):
    prefs = Prefs(tmp_path / 'host-preferences')
    prefs.set('provider-secret-example', 'host-only')
    root = create(tmp_path / 'projects', 'Cell research', session='2026-10-29', host_preferences=prefs)
    registry = ProjectRegistry(tmp_path / 'host')
    registry.register(root)
    return root, prefs, ProjectRepository(root), registry


def document(value):
    return {'kind': 'research-note', 'title': 'Protocol', 'state': {'value': value}, 'resources': [], 'runtimes': []}


def test_close_move_reopen_preserves_journal_and_exact_revisions(tmp_path):
    root, prefs, repo, registry = setup(tmp_path)
    with repo.edit(prefs, registry=registry, host_runtime_root=tmp_path / 'host-runtimes') as edit:
        edit.append({'type': 'line', 'thread': 't1', 'line': {'lineId': 'l1', 'role': 'user', 'text': 'Compare both methods'}})
        first = edit.create_document(document('original'))
        second = edit.save_document(first.id, document('corrected'), expected_revision=first.revision)
    before = repo.events()
    moved = tmp_path / 'renamed research'
    root.path.rename(moved)
    restored = ProjectRepository(ProjectRoot.open(moved))
    assert restored.events() == before
    assert restored.read(first.id, 1).digest == first.digest
    assert restored.read(first.id).digest == second.digest
    assert restored.read(first.id).document['state']['value'] == 'corrected'
    assert restored.owner()['origin']['origin'] == root.origin
    assert not list(moved.rglob('preferences.jsonl'))
    assert not any('host-only' in p.read_text() for p in moved.rglob('*.jsonl'))
    registry.register(restored.root, replace_inactive=True)
    with restored.edit(prefs, registry=registry, host_runtime_root=tmp_path / 'host-runtimes') as edit:
        third = edit.save_document(first.id, document('continued'), expected_revision=2)
    assert third.parent == second.digest


def test_second_writer_can_read_but_not_edit_and_old_editor_cannot_resume(tmp_path):
    root, prefs, repo, registry = setup(tmp_path)
    with repo.edit(prefs, registry=registry, host_runtime_root=tmp_path / 'host-runtimes') as edit:
        first = edit.create_document(document('kept'))
        other = ProjectRepository(ProjectRoot.open(root.path))
        assert other.read(first.id).digest == first.digest
        with pytest.raises(ProjectLocationError, match='read-only'):
            with other.edit(prefs, registry=registry, host_runtime_root=tmp_path / 'host-runtimes'):
                pytest.fail('a second writer entered')
    with pytest.raises(ProjectLocationError, match='closed'):
        edit.create_document(document('late write'))
    with repo.edit(prefs, registry=registry, host_runtime_root=tmp_path / 'host-runtimes') as edit:
        with pytest.raises(Conflict):
            edit.save_document(first.id, document('conflict'), expected_revision=2)


def test_manifest_without_matching_journal_never_opens_as_research(tmp_path):
    root, prefs, repo, registry = setup(tmp_path)
    path = root.path / 'project.json'
    value = json.loads(path.read_text())
    value['origin'] = 'f' * 32
    path.write_text(json.dumps(value))
    with pytest.raises(ProjectLocationError, match='do not match'):
        ProjectRepository(ProjectRoot.open(root.path))


def test_copied_folder_has_independent_mutable_journal_and_keeps_history(tmp_path):
    root, prefs, repo, registry = setup(tmp_path)
    with repo.edit(prefs, registry=registry, host_runtime_root=tmp_path / 'host-runtimes') as edit:
        first = edit.create_document(document('source'))
    copy = tmp_path / 'copy'
    shutil.copytree(root.path, copy)
    copied = ProjectRepository(ProjectRoot.open(copy))
    with pytest.raises(ProjectLocationError, match='not the host'):
        with copied.edit(prefs, registry=registry, host_runtime_root=tmp_path / 'host-runtimes'):
            pytest.fail('unregistered copy admitted')
    registry.register(copied.root, replace_inactive=True)
    with copied.edit(prefs, registry=registry, host_runtime_root=tmp_path / 'host-runtimes') as edit:
        edit.save_document(first.id, document('copy change'), expected_revision=1)
    assert repo.read(first.id).revision == 1
    assert copied.read(first.id).revision == 2
    with pytest.raises(ProjectLocationError, match='not the host'):
        with repo.edit(prefs, registry=registry, host_runtime_root=tmp_path / 'host-runtimes'):
            pytest.fail('original remained writable after explicit location switch')


def test_retained_history_can_outgrow_partial_transfer_limits(tmp_path):
    root, prefs, repo, registry = setup(tmp_path)
    # An accepted long conversation must not disable the Project on its next read.
    text = 'long retained conversation ' * 700000
    with repo.edit(prefs, registry=registry, host_runtime_root=tmp_path / 'host-runtimes') as edit:
        edit.append({'type': 'line', 'thread': 't1', 'line': {'lineId': 'large', 'role': 'user', 'text': text}})
    assert (root.path / 'history/sessions/2026-10-29/journal.jsonl').stat().st_size > 16 << 20
    reopened = ProjectRepository(ProjectRoot.open(root.path))
    assert any(row.get('line', {}).get('text') == text for row in reopened.events())
    with reopened.edit(prefs, registry=registry, host_runtime_root=tmp_path / 'host-runtimes') as edit:
        saved = edit.create_document(document('continued after a long conversation'))
    assert reopened.read(saved.id).digest == saved.digest


def test_saved_research_reads_on_filesystem_read_only_project(tmp_path):
    root, prefs, repo, registry = setup(tmp_path)
    with repo.edit(prefs, registry=registry, host_runtime_root=tmp_path / 'host-runtimes') as edit:
        saved = edit.create_document(document('readable without write permission'))
    paths = [root.path, *root.path.rglob('*')]
    modes = {p: p.stat().st_mode & 0o777 for p in paths}
    try:
        for p in paths:
            p.chmod(0o500 if p.is_dir() else 0o400)
        reopened = ProjectRepository(ProjectRoot.open(root.path))
        assert reopened.events()
        documents, problems = reopened.documents()
        assert len(documents) == 1 and not problems
        assert reopened.read(saved.id).digest == saved.digest
    finally:
        for p, mode in modes.items():
            p.chmod(mode)


def test_read_only_lock_still_refuses_publication_in_progress(tmp_path):
    from orchestrator.scientific_import_transaction import publishing, ImportPending
    from orchestrator.project_repository import session_root, document_root
    root, prefs, repo, registry = setup(tmp_path)
    with publishing(session_root(root)):
        with pytest.raises(ImportPending):
            repo.events()
    with publishing(document_root(root)):
        with pytest.raises(ImportPending):
            repo.documents()


def test_creation_sync_failure_never_publishes_registry_and_keeps_recovery(tmp_path, monkeypatch):
    from orchestrator.scientific_projects import ProjectStore
    prefs = Prefs(tmp_path / 'host')
    sync = ProjectStore._sync_directory
    def fail_history(folder):
        if folder.name == 'history':
            raise OSError('injected directory sync failure')
        sync(folder)
    monkeypatch.setattr(ProjectStore, '_sync_directory', staticmethod(fail_history))
    with pytest.raises(OSError, match='sync failure'):
        create(tmp_path / 'projects', 'Interrupted', session='2026-10-29', host_preferences=prefs)
    roots = list((tmp_path / 'projects').glob('Interrupted*'))
    assert len(roots) == 1
    assert (roots[0] / 'history/sessions/2026-10-29/journal.jsonl').exists()
    assert not (tmp_path / 'host/project-locations.json').exists()


def test_writable_store_registers_host_runtime_retention(tmp_path):
    from orchestrator.scientific_retention import BINDINGS, METADATA
    from orchestrator.project_repository import document_root
    root, prefs, repo, registry = setup(tmp_path)
    runtimes = tmp_path / 'host-runtimes'
    with repo.edit(prefs, registry=registry, host_runtime_root=runtimes) as edit:
        saved = edit.create_document(document('registered history'))
    assert str(runtimes.resolve()) in json.loads((document_root(root) / BINDINGS).read_text())
    assert list((runtimes / METADATA).glob('*.json'))
    assert repo.read(saved.id).digest == saved.digest
