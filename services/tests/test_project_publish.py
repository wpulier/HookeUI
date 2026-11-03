"""Publish a Project and start from it on another host (ADR 0028), through a folder remote; no network or Git."""
import json

import pytest

from orchestrator.day.prefs import Prefs
from orchestrator.project_folder_copy import copy_snapshot
from orchestrator.project_publish import last_publication, prepare, publish
from orchestrator.project_remote_folder import folder_remote
from orchestrator.project_remote_layers import RemoteRefused
from orchestrator.project_repository import ProjectRepository, create, document_root, session_root
from orchestrator.project_roots import ProjectRegistry
from orchestrator.project_start_from import start_from, update_from
from orchestrator.scientific_exchange import BundleRefused
from orchestrator.scientific_projects import Inherited, ProjectStore
from tests.test_project_remote import PIXELS, project

SIGNOFF = {'rights': True, 'noIdentifiableHumanData': True, 'permanent': True, 'author': 'Ada Researcher'}


def seal(root):
    return lambda destination: copy_snapshot(root, destination)


def maker(parent, registry, prefs, session):
    """A host's `create`: a new Project with its own origin and handle, registered like the app's own."""
    def make(avoid):
        assert session not in avoid
        root = create(parent, 'Research copy', session=session, host_preferences=prefs)
        registry.register(root)
        return root
    return make


def elsewhere(tmp_path, name='Other Mac', session='2026-11-02'):
    folder = tmp_path / name
    registry, prefs = ProjectRegistry(folder / 'host'), Prefs(folder / 'host')
    return folder, registry, prefs, maker(folder / 'Projects', registry, prefs, session)


def published(tmp_path, **kw):
    root, repo, prefs, registry, note, source = project(tmp_path, **kw)
    transcripts = root.path / 'artifacts' / 'research' / 'transcripts' / '2026-10-29'
    transcripts.mkdir(parents=True)
    (transcripts / 'r1.jsonl').write_text('{"said": "a researcher transcript"}\n')
    (root.path / 'artifacts' / 'results').mkdir(exist_ok=True)
    (root.path / 'artifacts' / 'results' / 'count.json').write_text('{"cells": 12}')
    remote = folder_remote(tmp_path / 'Public', create=True)
    review = prepare(seal(root), tmp_path / 'Review')
    return root, repo, prefs, registry, note, source, remote, review


def test_publish_then_start_from_on_another_host_keeps_every_document_and_no_conversation(tmp_path):
    root, repo, _, _, note, source, remote, review = published(tmp_path)
    assert review['licence'] == 'CC-BY-4.0' and not review['blocked']
    assert review['leftOut']['transcripts'] == 1
    receipt = publish(review['staging'], review_digest=review['reviewDigest'], signoff=SIGNOFF, remote=remote)
    assert receipt['event']['type'] == 'project-published' and receipt['parent'] is None
    paths = {e['path'] for e in remote.records.read_commit(receipt['commit'])['entries']}
    assert {'LICENSE', 'CITATION.cff', 'ro-crate-metadata.json', 'README.md'} <= paths
    assert not any('artifacts/research' in p for p in paths)
    readme = remote.records.read_blob(next(e['ref'] for e in remote.records.read_commit(receipt['commit'])['entries']
                                           if e['path'] == 'README.md'), 1 << 20).decode()
    assert '# Research' in readme and 'Start from this project' in readme and 'cells.tif' in readme
    # Another Mac: its own host folder and registry.
    other, registry, prefs, make = elsewhere(tmp_path)
    working, ancestry, came = start_from(remote, make, staging=tmp_path / 'Fetch',
                                         source={'kind': 'folder', 'location': 'Public'})
    assert working.origin != root.origin and working.session == '2026-11-02'
    assert sorted(came['added']) == sorted([note.id, source.id]) and ancestry['documents'] == 2
    copy = ProjectRepository(working)
    for original in (note, source):
        held = copy.read(original.id, 1)
        assert held.digest == original.digest and held.document == original.document  # byte for byte
        assert held.session == working.session and held.owner == root.session
    assert {r.id for r in copy.documents()[0]} == {note.id, source.id}
    journals = ''.join(p.read_text() for p in session_root(working).rglob('journal.jsonl'))
    assert 'private conversation' not in journals
    assert (working.path / 'artifacts/results/count.json').read_text() == '{"cells": 12}'
    assert not (working.path / 'artifacts/research').exists()
    with copy.edit(prefs, registry=registry, host_runtime_root=other / 'runtimes') as edit:
        edit.append(ancestry)
    events = ProjectRepository(working).events()
    assert any(e.get('type') == 'project-started-from' and e['snapshotSha256'] == receipt['snapshotSha256']
               for e in events)


def test_the_review_digest_and_every_signoff_are_required(tmp_path):
    *_, remote, review = published(tmp_path)
    with pytest.raises(RemoteRefused, match='reviewed'):
        publish(review['staging'], review_digest='0' * 64, signoff=SIGNOFF, remote=remote)
    for missing in ('rights', 'noIdentifiableHumanData', 'permanent'):
        with pytest.raises(RemoteRefused):
            publish(review['staging'], review_digest=review['reviewDigest'],
                    signoff={**SIGNOFF, missing: False}, remote=remote)
    assert remote.records.head() is None


def test_transcripts_travel_only_when_chosen(tmp_path):
    root, *_ = published(tmp_path)
    remote = folder_remote(tmp_path / 'With transcripts', create=True)
    review = prepare(seal(root), tmp_path / 'Review 2', include_transcripts=True)
    receipt = publish(review['staging'], review_digest=review['reviewDigest'], signoff=SIGNOFF, remote=remote)
    paths = {e['path'] for e in remote.records.read_commit(receipt['commit'])['entries']}
    assert any('artifacts/research/transcripts' in p for p in paths)


def test_a_secret_blocks_publication(tmp_path):
    *_, remote, review = published(tmp_path, text='token: sk-abcdefghijklmnopqrstu')
    assert review['blocked'] and any(f['kind'] == 'secret' for f in review['findings'])
    with pytest.raises(RemoteRefused, match='findings'):
        publish(review['staging'], review_digest=review['reviewDigest'], signoff=SIGNOFF, remote=remote)
    assert remote.records.head() is None


def test_a_second_publication_chains_and_update_brings_it_in(tmp_path):
    root, repo, prefs, registry, note, source, remote, review = published(tmp_path)
    first = publish(review['staging'], review_digest=review['reviewDigest'], signoff=SIGNOFF, remote=remote)
    with repo.edit(prefs, registry=registry, host_runtime_root=tmp_path / 'runtimes') as edit:
        edit.save_document(note.id, {**note.document, 'state': {'text': 'Revised question'}}, expected_revision=1)
        edit.append(first['event'])
    parent = last_publication(ProjectRepository(root).events(), remote.describe())
    assert parent == first['commit']
    working, _, _ = start_from(remote, elsewhere(tmp_path)[3], staging=tmp_path / 'Fetch')
    review2 = prepare(seal(root), tmp_path / 'Review 2')
    second = publish(review2['staging'], review_digest=review2['reviewDigest'], signoff=SIGNOFF, remote=remote,
                     parent=parent)
    assert second['parent'] == first['commit']
    result = update_from(working, remote, origin=root.origin, staging=tmp_path / 'Update')
    assert note.id in result['extended'] and source.id in result['unchanged'] and not result['refused']
    assert ProjectRepository(working).read(note.id).document['state']['text'] == 'Revised question'
    with pytest.raises(BundleRefused, match='different Project'):
        update_from(working, remote, origin='f' * 32, staging=tmp_path / 'Update elsewhere')
    again = update_from(working, remote, origin=root.origin, staging=tmp_path / 'Update again')
    assert not again['extended'] and not again['added']


def test_a_started_project_reads_what_it_inherited_and_never_saves_over_it(tmp_path):
    root, repo, prefs, registry, note, source, remote, review = published(tmp_path)
    publish(review['staging'], review_digest=review['reviewDigest'], signoff=SIGNOFF, remote=remote)
    other, other_registry, other_prefs, make = elsewhere(tmp_path)
    working, _, _ = start_from(remote, make, staging=tmp_path / 'Fetch')
    with ProjectRepository(working).edit(other_prefs, registry=other_registry,
                                         host_runtime_root=other / 'runtimes') as edit:
        with pytest.raises(Inherited, match='read-only here'):
            edit.save_document(note.id, {**note.document, 'state': {'text': 'My own change'}}, expected_revision=1)
        mine = edit.create_document({**note.document, 'title': 'My follow-up'})
    copy = ProjectRepository(working)
    assert copy.read(note.id).document == note.document
    assert copy.read(mine.id).owner is None and {r.id for r in copy.documents()[0]} == {note.id, source.id, mine.id}
    # The owner's own handle reads the same inherited document, as a reference inside it would name it.
    assert ProjectStore(document_root(working)).read(note.id, root.session).digest == note.digest


def test_started_projects_sit_beside_the_original_and_each_other_on_one_host(tmp_path):
    root, repo, prefs, registry, note, source, remote, review = published(tmp_path)
    publish(review['staging'], review_digest=review['reviewDigest'], signoff=SIGNOFF, remote=remote)
    first, _, _ = start_from(remote, maker(tmp_path / 'Projects', registry, prefs, '2026-10-29.2'),
                             staging=tmp_path / 'Fetch')
    second, _, _ = start_from(remote, maker(tmp_path / 'Projects', registry, prefs, '2026-10-29.3'),
                              staging=tmp_path / 'Fetch 2')
    assert len({root.origin, first.origin, second.origin}) == 3
    assert {r['session'] for r in registry.locations().values()} == {'2026-10-29', '2026-10-29.2', '2026-10-29.3'}
    for copy in (first, second):
        assert ProjectRepository(copy).read(note.id).digest == note.digest
    assert {r.id for r in ProjectRepository(root).documents()[0]} == {note.id, source.id}


def test_the_new_project_never_takes_a_handle_the_publication_uses(tmp_path):
    *_, remote, review = published(tmp_path)
    publish(review['staging'], review_digest=review['reviewDigest'], signoff=SIGNOFF, remote=remote)
    seen = []
    with pytest.raises(AssertionError):
        start_from(remote, lambda avoid: seen.append(avoid) or (_ for _ in ()).throw(AssertionError),
                   staging=tmp_path / 'Fetch')
    assert '2026-10-29' in seen[0]


def test_a_publication_and_a_restored_backup_carry_the_projects_current_name(tmp_path):
    """A rename is a journal event; project.json keeps the name the Project was made with (live test, ticket 13)."""
    from orchestrator.project_backup import back_up, restore
    root, repo, prefs, registry, *_ = project(tmp_path)
    with repo.edit(prefs, registry=registry, host_runtime_root=tmp_path / 'runtimes') as edit:
        edit.append({'type': 'session-renamed', 'name': 'Leaf cells', 'by': 'user', 'auto': False})
    review = prepare(seal(root), tmp_path / 'Review')
    assert review['name'] == 'Leaf cells' and root.name == 'Research'
    remote = folder_remote(tmp_path / 'Public', create=True)
    publish(review['staging'], review_digest=review['reviewDigest'], signoff=SIGNOFF, remote=remote)
    readme = next(e for e in remote.records.read_commit(remote.records.head())['entries'] if e['path'] == 'README.md')
    assert remote.records.read_blob(readme['ref'], 1 << 20).decode().startswith('# Leaf cells')
    drive = folder_remote(tmp_path / 'Drive', create=True)
    back_up(seal(root), drive, staging=tmp_path / 'Backup', scan_secrets=False)
    restored, _ = restore(drive, tmp_path / 'Elsewhere', lambda r: r, staging=tmp_path / 'Restore')
    assert restored.path.name == 'Leaf cells'


def test_a_started_project_reads_an_inherited_files_bytes_under_its_own_handle(tmp_path):
    """The studio asks for an inherited document's file under the copy's own handle (live test, ticket 13): its
    preview, text and download must read it, though its reference names the publication's owner."""
    from tests.test_project_remote import PIXELS
    root, repo, prefs, registry, note, source, remote, review = published(tmp_path)
    publish(review['staging'], review_digest=review['reviewDigest'], signoff=SIGNOFF, remote=remote)
    working, _, _ = start_from(remote, elsewhere(tmp_path)[3], staging=tmp_path / 'Fetch')
    store = ProjectStore(document_root(working))
    held = store.read(source.id, working.session, revision=1)
    assert held.owner == root.session and held.session == working.session
    ref = held.document['state']['sourceFile']['reference']
    file = store.file_source(source.id, working.session, 1, ref)
    assert file.read(0, 1 << 20) == PIXELS[:1 << 20] and file.session == root.session


def test_a_started_project_holding_inherited_files_can_be_sealed_backed_up_and_published(tmp_path):
    """Live test, ticket 13: sealing checked an inherited document's files under the copy's own handle and refused,
    so a started Project could not be exported, backed up or published."""
    from orchestrator.project_backup import back_up
    from orchestrator.project_folder_copy import copy_snapshot
    root, repo, prefs, registry, note, source, remote, review = published(tmp_path)
    publish(review['staging'], review_digest=review['reviewDigest'], signoff=SIGNOFF, remote=remote)
    working, _, _ = start_from(remote, elsewhere(tmp_path)[3], staging=tmp_path / 'Fetch')
    sealed = copy_snapshot(working, tmp_path / 'Sealed copy')
    assert sealed.origin == working.origin
    back_up(seal(working), folder_remote(tmp_path / 'Drive', create=True), staging=tmp_path / 'B', scan_secrets=False)
    again = prepare(seal(working), tmp_path / 'Review again')
    assert not again['blocked'] and again['origin'] == working.origin
