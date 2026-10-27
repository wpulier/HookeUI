"""User-owned scientific work survives package lifecycle changes and interrupted saves."""
import shutil
from concurrent.futures import ThreadPoolExecutor

import pytest

from orchestrator.scientific_projects import Conflict, InvalidProject, ProjectStore


def document():
    return {"kind": "animation", "title": "Cells over time", "state": {"layers": [{"id": "layer-a", "name": "Cells"}],
            "keyframes": [{"zoom": 1}, {"zoom": 2}]},
            "resources": [{"session": "s1", "id": "d1", "revision": 0}],
            "runtimes": [{"bundle": "mine/animation", "generation": "abc123", "operation": "animate"}]}


def test_owned_creation_identity_is_idempotent_without_overwriting_later_work(tmp_path):
    store = ProjectStore(tmp_path / 'projects')
    identity = 'p' + 'a' * 32
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(lambda _: store.create('s1', document(), identity=identity), range(2))
    assert first == second
    changed = document()
    changed['title'] = 'New accepted work'
    latest = store.save(identity, 's1', changed, expected_revision=1)
    assert store.create('s1', document(), identity=identity) == first
    assert store.read(identity, 's1') == latest
    with pytest.raises(Conflict): store.create('s1', changed, identity=identity)
    with pytest.raises(InvalidProject): store.create('s2', document(), identity=identity)
    assert store.read(identity, 's1') == latest


def test_project_revisions_survive_update_removal_and_reopen(tmp_path):
    store = ProjectStore(tmp_path / 'work')
    first = store.create('s1', document())
    change = document()
    change['state']['keyframes'][1]['zoom'] = 3
    change['runtimes'][0]['generation'] = 'def456'
    second = store.save(first.id, 's1', change, expected_revision=1)
    runtime = tmp_path / 'catalog' / 'envs' / 'animation'
    runtime.mkdir(parents=True)
    (runtime / 'checkpoint').write_text('installed software only')
    shutil.rmtree(runtime)
    reopened = ProjectStore(tmp_path / 'work')
    assert reopened.read(first.id, 's1', revision=1).document == document()
    assert reopened.read(first.id, 's1').document == change
    assert second.revision == 2 and second.parent == first.digest
    assert [p.id for p in reopened.list('s1')] == [first.id]
    assert reopened.list('another-session') == []
    with pytest.raises(InvalidProject, match='session'):
        reopened.read(first.id, 'another-session')


def test_stale_and_concurrent_saves_preserve_the_accepted_version(tmp_path):
    one, two = ProjectStore(tmp_path), ProjectStore(tmp_path)
    project = one.create('s1', document())
    def edit(store, title):
        try:
            return store.save(project.id, 's1', {**document(), 'title': title}, expected_revision=1).revision
        except Conflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: edit(*pair), [(one, 'First edit'), (two, 'Other edit')]))
    assert sorted(results, key=str) == [2, 'conflict']
    assert one.read(project.id, 's1', revision=1).document == document()
    assert one.read(project.id, 's1').document['title'] in ('First edit', 'Other edit')


def test_lost_edit_acknowledgement_replays_exact_revision_without_replacing_later_work(tmp_path):
    store = ProjectStore(tmp_path)
    first = store.create('s1', document())
    change = {**document(), 'title': 'One gesture'}
    saved = store.save(first.id, 's1', change, expected_revision=1, transaction='a' * 64)
    later = store.save(first.id, 's1', {**change, 'title': 'Other writer'}, expected_revision=2)
    reopened = ProjectStore(tmp_path)
    replay = reopened.save(first.id, 's1', change, expected_revision=1, transaction='a' * 64)
    assert replay == saved and replay.transaction == 'a' * 64
    assert reopened.read(first.id, 's1') == later
    assert reopened.read(first.id, 's1', revision=1).transaction is None
    for changed, transaction in (({**change, 'title': 'Different bytes'}, 'a' * 64),
                                 (change, 'b' * 64), (change, None)):
        with pytest.raises(Conflict):
            reopened.save(first.id, 's1', changed, expected_revision=1, transaction=transaction)
    with pytest.raises(InvalidProject, match='session'):
        reopened.save(first.id, 's2', change, expected_revision=1, transaction='a' * 64)


def test_duplicate_concurrent_edit_acknowledges_only_one_publication(tmp_path):
    first = ProjectStore(tmp_path).create('s1', document())
    change = {**document(), 'title': 'One accepted edit'}
    def save(_):
        return ProjectStore(tmp_path).save(first.id, 's1', change, expected_revision=1,
                                           transaction='c' * 64)
    with ThreadPoolExecutor(max_workers=2) as pool:
        left, right = pool.map(save, range(2))
    assert left == right and left.revision == 2
    assert ProjectStore(tmp_path).read(first.id, 's1') == left


@pytest.mark.parametrize('transaction', ['', 'A' * 64, 'a' * 63, True, {}, 1])
def test_invalid_edit_transaction_leaves_accepted_project_intact(tmp_path, transaction):
    store = ProjectStore(tmp_path)
    first = store.create('s1', document())
    with pytest.raises(InvalidProject, match='transaction'):
        store.save(first.id, 's1', document(), expected_revision=1, transaction=transaction)
    assert store.read(first.id, 's1') == first


def test_failed_publication_keeps_old_head_and_retry_works(tmp_path, monkeypatch):
    from orchestrator import scientific_projects
    store = ProjectStore(tmp_path)
    first = store.create('s1', document())
    replace = scientific_projects.os.replace
    def refuse(source, target):
        raise OSError('controlled disk failure')
    monkeypatch.setattr(scientific_projects.os, 'replace', refuse)
    with pytest.raises(OSError, match='controlled disk failure'):
        store.save(first.id, 's1', {**document(), 'title': 'Unaccepted'}, expected_revision=1)
    assert store.read(first.id, 's1').digest == first.digest
    monkeypatch.setattr(scientific_projects.os, 'replace', replace)
    assert store.save(first.id, 's1', {**document(), 'title': 'Retry'}, expected_revision=1).revision == 2
    assert store.read(first.id, 's1', revision=1).digest == first.digest


def test_migration_is_a_new_revision_and_payloads_are_detached(tmp_path):
    store = ProjectStore(tmp_path)
    original = document()
    first = store.create('s1', original)
    original['state']['layers'].clear()
    assert store.read(first.id, 's1').document['state']['layers']
    migrated = store.read(first.id, 's1').document
    migrated['state']['keyframes'].append({'zoom': 4})
    store.save(first.id, 's1', migrated, expected_revision=1)
    assert len(store.read(first.id, 's1', revision=1).document['state']['keyframes']) == 2


@pytest.mark.parametrize('update', [
    {'state': {'not_json': float('nan')}}, {'title': 'x' * 241},
    {'resources': [{'session': 's1', 'id': 'd1'}]},
    {'runtimes': [{'bundle': 'mine/animation'}]},
    {'unknown': True}, {'state': {'huge': 'x' * (2 << 20)}},
    {'state': {1: 'must not become a string key'}}, {'state': {'coordinates': (1, 2)}},
])
def test_invalid_documents_cannot_replace_saved_work(tmp_path, update):
    store = ProjectStore(tmp_path)
    first = store.create('s1', document())
    with pytest.raises(InvalidProject):
        store.save(first.id, 's1', {**document(), **update}, expected_revision=1)
    assert store.read(first.id, 's1').digest == first.digest


def test_corrupt_or_symlinked_records_are_contained(tmp_path):
    store = ProjectStore(tmp_path)
    healthy = store.create('s1', document())
    broken = store.create('s1', {**document(), 'title': 'Broken'})
    record = tmp_path / broken.id / 'revisions' / f'{broken.digest}.json'
    record.write_text('{}')
    with pytest.raises(InvalidProject):
        store.read(broken.id, 's1')
    assert [p.id for p in store.list('s1')] == [healthy.id]
    assert store.scan('s1')[1] == [{'id': broken.id, 'reason': 'Project revision content changed'}]
    outside = tmp_path / 'outside'
    outside.mkdir()
    link = tmp_path / ('p' + 'a' * 32)
    link.symlink_to(tmp_path / healthy.id, target_is_directory=True)
    with pytest.raises(InvalidProject, match='symlink'):
        store.read(link.name, 's1')
    with pytest.raises(InvalidProject):
        store.read('../outside', 's1')


def test_create_does_not_acknowledge_an_unsynced_project_directory(tmp_path, monkeypatch):
    from orchestrator import scientific_projects
    store = ProjectStore(tmp_path)
    sync = scientific_projects.os.fsync
    def fail_root(fd):
        stat = scientific_projects.os.fstat(fd)
        if (stat.st_dev, stat.st_ino) == (tmp_path.stat().st_dev, tmp_path.stat().st_ino):
            raise OSError('controlled project-directory flush failure')
        return sync(fd)
    monkeypatch.setattr(scientific_projects.os, 'fsync', fail_root)
    with pytest.raises(OSError, match='project-directory flush'):
        store.create('s1', document())


def test_exact_content_resources_survive_project_edits_and_reopen(tmp_path):
    store = ProjectStore(tmp_path)
    original = document()
    reference = {'session': 's1', 'id': 'd2', 'revision': 'a' * 64, 'component': 'picture-0'}
    original['resources'].append(reference)
    first = store.create('s1', original)
    second = store.save(first.id, 's1', {**original, 'title': 'Reviewed'}, expected_revision=1)
    reopened = ProjectStore(tmp_path)
    assert reopened.read(first.id, 's1', revision=1).document == original
    assert reopened.read(first.id, 's1').document['resources'] == original['resources']
    assert type(second.document['resources'][0]['revision']) is int
    assert second.document['resources'][1] == reference


@pytest.mark.parametrize('revision', [True, -1, 2**53, 0.5, '0', 'latest', 'A' * 64, 'a' * 63, None])
def test_invalid_resource_revision_cannot_replace_scientific_work(tmp_path, revision):
    store = ProjectStore(tmp_path)
    first = store.create('s1', document())
    changed = document()
    changed['resources'][0]['revision'] = revision
    with pytest.raises(InvalidProject):
        store.save(first.id, 's1', changed, expected_revision=1)
    assert store.read(first.id, 's1').digest == first.digest
