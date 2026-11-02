"""Publish a Project and start from it on another host (ADR 0028), through a folder remote; no network or Git."""
import json

import pytest

from orchestrator.day.prefs import Prefs
from orchestrator.project_folder_copy import copy_snapshot
from orchestrator.project_publish import last_publication, prepare, publish
from orchestrator.project_remote_folder import folder_remote
from orchestrator.project_remote_layers import RemoteRefused
from orchestrator.project_repository import ProjectRepository, document_root, session_root
from orchestrator.project_roots import ProjectLocationError, ProjectRegistry
from orchestrator.project_start_from import start_from, update_from
from tests.test_project_remote import PIXELS, project

SIGNOFF = {'rights': True, 'noIdentifiableHumanData': True, 'permanent': True, 'author': 'Ada Researcher'}


def seal(root):
    return lambda destination: copy_snapshot(root, destination)


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
    elsewhere = tmp_path / 'Other Mac'
    working, ancestry = start_from(remote, elsewhere / 'Research copy', staging=tmp_path / 'Fetch',
                                   source={'kind': 'folder', 'location': 'Public'})
    registry = ProjectRegistry(elsewhere / 'host')
    registry.register(working)
    copy = ProjectRepository(working)
    assert working.origin == root.origin
    assert copy.read(note.id, 1) == note and copy.read(source.id, 1) == source
    journal = (session_root(working) / working.session / 'journal.jsonl').read_text()
    assert 'private conversation' not in journal
    assert (working.path / 'artifacts/results/count.json').read_text() == '{"cells": 12}'
    assert not (working.path / 'artifacts/research').exists()
    with copy.edit(Prefs(elsewhere / 'host'), registry=registry, host_runtime_root=elsewhere / 'runtimes') as edit:
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
    working, _ = start_from(remote, tmp_path / 'Copy', staging=tmp_path / 'Fetch')
    review2 = prepare(seal(root), tmp_path / 'Review 2')
    second = publish(review2['staging'], review_digest=review2['reviewDigest'], signoff=SIGNOFF, remote=remote,
                     parent=parent)
    assert second['parent'] == first['commit']
    result = update_from(working, remote, staging=tmp_path / 'Update')
    assert note.id in result['extended'] and source.id in result['unchanged'] and not result['refused']
    assert ProjectRepository(working).read(note.id).document['state']['text'] == 'Revised question'
    again = update_from(working, remote, staging=tmp_path / 'Update again')
    assert not again['extended'] and not again['added']


def test_update_refuses_a_document_that_changed_in_both_places(tmp_path):
    root, repo, prefs, registry, note, source, remote, review = published(tmp_path)
    publish(review['staging'], review_digest=review['reviewDigest'], signoff=SIGNOFF, remote=remote)
    elsewhere = tmp_path / 'Other Mac'
    working, _ = start_from(remote, elsewhere / 'Copy', staging=tmp_path / 'Fetch')
    other = ProjectRegistry(elsewhere / 'host')
    other.register(working)
    with ProjectRepository(working).edit(Prefs(elsewhere / 'host'), registry=other,
                                         host_runtime_root=elsewhere / 'runtimes') as edit:
        edit.save_document(note.id, {**note.document, 'state': {'text': 'My own change'}}, expected_revision=1)
    with repo.edit(prefs, registry=registry, host_runtime_root=tmp_path / 'runtimes') as edit:
        edit.save_document(note.id, {**note.document, 'state': {'text': 'Author change'}}, expected_revision=1)
    head = remote.records.head()
    review2 = prepare(seal(root), tmp_path / 'Review 2')
    publish(review2['staging'], review_digest=review2['reviewDigest'], signoff=SIGNOFF, remote=remote, parent=head)
    result = update_from(working, remote, staging=tmp_path / 'Update')
    assert [r['id'] for r in result['refused']] == [note.id]
    assert ProjectRepository(working).read(note.id).document['state']['text'] == 'My own change'


def test_a_started_from_copy_beside_the_original_on_one_host_is_refused_by_the_registry(tmp_path):
    root, repo, prefs, registry, note, source, remote, review = published(tmp_path)
    publish(review['staging'], review_digest=review['reviewDigest'], signoff=SIGNOFF, remote=remote)
    working, _ = start_from(remote, tmp_path / 'Copy', staging=tmp_path / 'Fetch')
    with pytest.raises(ProjectLocationError, match='independent'):
        registry.register(working)
