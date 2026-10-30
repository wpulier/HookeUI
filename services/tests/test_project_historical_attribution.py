"""Synthetic accepted imports through real folder copy; no connection authority."""
from orchestrator.door import actor
from orchestrator.project_folder_copy import copy_snapshot, restore_snapshot
from orchestrator.project_repository import document_root
from orchestrator.project_roots import ProjectRoot
from orchestrator.resources import Index, maker_of
from orchestrator.scientific_file_intake import FileIntake
from orchestrator.scientific_projects import ProjectStore
from tests.test_project_folder_copy import setup


def test_restored_import_keeps_recorded_label_without_host_connections(tmp_path, monkeypatch):
    monkeypatch.setattr(actor, '_LABELS', {})
    root, *_ = setup(tmp_path)
    store = ProjectStore(document_root(root))
    who = 'agent:ac_' + 'a' * 24
    producer = {'kind': 'agent-import', 'actor': who,
                'label': 'Research worker', 'operation': 'op_' + 'b' * 24}
    intake = FileIntake(store, root.session, 'c' * 32, name='evidence.txt',
                        size=8, max_bytes=8, producer=producer)
    try:
        intake.write(b'evidence')
        original = intake.publish()
    finally:
        intake.close()
    snapshot = copy_snapshot(root, tmp_path / 'Snapshot')
    restored = restore_snapshot(snapshot.path, tmp_path / 'Working')
    moved = tmp_path / 'Moved'
    restored.path.rename(moved)
    saved = ProjectStore(document_root(ProjectRoot.open(moved))).read(original.id, root.session)
    assert saved == original
    entry = Index.project_entry(saved)
    expected = {'kind': 'agent', 'id': who[6:], 'name': 'Research worker'}
    assert maker_of(entry.kind, entry.id, entry.made_by) == expected
    assert actor._LABELS == {}
    actor.remember(who[6:], 'Current host label')
    assert maker_of(entry.kind, entry.id, entry.made_by) == expected
    assert actor.maker(who)['name'] == 'Current host label'


def test_historical_label_is_per_record_and_does_not_supply_identity(monkeypatch):
    monkeypatch.setattr(actor, '_LABELS', {})
    who = 'agent:ac_' + 'd' * 24
    first = maker_of('project', 'p1', {'actor': who, 'label': 'Original name'})
    second = maker_of('project', 'p2', {'actor': who, 'label': 'Later name'})
    assert first['name'] == 'Original name' and second['name'] == 'Later name'
    assert first['id'] == second['id']
    assert actor.maker(who)['name'] == 'An agent'
    assert maker_of('project', 'p3', {'label': 'Research worker'}) is None
    assert maker_of('project', 'p4', {'actor': who, 'label': {}})['name'] == 'An agent'
    assert maker_of('project', 'p5', {'actor': who, 'label': ' '})['name'] == 'An agent'
