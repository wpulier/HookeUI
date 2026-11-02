"""A publication keeps the lines that list a Project's results, and a copy started from it lists them read-only
(ADR 0028, format v2 section 9; hooke-projects ticket 14). Real saved evidence, tables and notes on isolated hosts."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from orchestrator.analysis_math import analyze
from orchestrator.datatables import DataTables
from orchestrator.evidence import Evidence
from orchestrator.project_inherited import bring_in, index_line, owners, split
from orchestrator.project_remote_folder import folder_remote
from orchestrator.project_repository import session_root
from orchestrator.project_roots import ProjectLocationError
from orchestrator.resources import Index
from orchestrator.table_derivatives import TableDerivatives
from tests.test_evidence import population, saved, view
from tests.test_project_remote_commands import SIGNOFF, host, run

PRIVATE = 'keep this conversation private'


@pytest.fixture(autouse=True)
def measured(monkeypatch):
    async def read(app, eid, session, revision):
        return {'id': eid, 'kind': 'result-table', 'title': eid, 'revision': 0,
                'columns': [{'key': 'value', 'type': 'number', 'unit': 'µm'}, {'key': 'condition', 'type': 'text'}],
                'rows': [{'value': v, 'condition': eid} for v in ([1., 2., 3.] if eid == 'd1' else [4., 5., 6.])]}
    monkeypatch.setattr('orchestrator.tables.read_table', read)


def results(app, folder):
    """A host's result stores over its open session, as the app wires them."""
    hub = SimpleNamespace()
    hub.evidence = Evidence(hub, folder / 'host/evidence', day=app.day)
    owner = SimpleNamespace(day=app.day, hub=hub)
    hub.datatables = DataTables(owner, folder / 'host/tables')
    hub.table_derivatives = TableDerivatives(hub.datatables, folder / 'host/derived', {'analysis': analyze})
    return hub


def research(app, day, hub):
    """A population saved, a frozen table of two measurements, a note on the population, and a private chat line."""
    p = population(hub.evidence, view())
    p.session = day.sid
    hub.evidence.add(p)
    hub.evidence.schedule_save(p)
    assert saved(p).status == 'saved', p.status_why
    table = asyncio.run(hub.datatables.create(['d1', 'd2'], mode='frozen'))
    note = day.record_annotation({'day': day.sid, 'id': p.id, 'population': p.id, 'revision': p.revision},
                                 'Looks like a dividing cell', 'Division', annotation_id='', asked=None)
    day._record({'type': 'chat', 'role': 'user', 'text': PRIVATE}, strict=True)
    return p, table, note


def published_text(path):
    """Every file of the latest publication in a folder remote, as text."""
    remote = folder_remote(path)
    commit = remote.records.read_commit(remote.records.head())
    return {e['path']: remote.records.read_blob(e['ref'], 64 << 20).decode('utf-8', 'replace')
            for e in commit['entries']}


def publish(app, sid, tmp_path):
    review = run(app, 'project-publish-prepare', session=sid)['review']
    assert not review['blocked'], review['findings']
    run(app, 'project-publish', session=sid, staging=review['staging'], reviewDigest=review['reviewDigest'],
        signoff=SIGNOFF, destination={'kind': 'folder', 'path': str(tmp_path / 'Shared drive'), 'create': True})
    return review


def test_a_copy_lists_the_publications_evidence_tables_and_notes_and_no_conversation(tmp_path):
    app, day, locations = host(tmp_path, 'Mac A')
    other, other_day, other_locations = host(tmp_path, 'Mac B')
    try:
        sid = day.new('Research')
        p, table, note = research(app, day, results(app, tmp_path / 'Mac A'))
        review = publish(app, sid, tmp_path)
        assert review['publishedText'] == [{'session': sid, 'id': note['id'], 'version': 1, 'title': 'Division',
                                            'text': 'Looks like a dividing cell'}]
        assert review['resultLines'] == 3 and 'list results' in review['leftOut']['conversations']
        files = published_text(tmp_path / 'Shared drive')
        journal = files[f'project/history/sessions/{sid}/journal.jsonl']
        assert PRIVATE not in ''.join(files.values())
        assert [json.loads(line)['type'] for line in journal.splitlines()] == [
            'session-origin', 'session-imported', 'evidence-saved', 'datatable', 'annotation']
        assert '"thread"' not in journal  # no line names the conversation it was made in

        started = run(other, 'project-start-from', source={'kind': 'folder', 'path': str(tmp_path / 'Shared drive')},
                      name='Research copy')
        copy = other_locations.project(started['project']['session'])
        assert started['startedFrom']['results'] == 3
        assert set(owners(copy.path, copy.session)) == {sid}
        other_day.open(copy.session, 'test')
        hub = results(other, tmp_path / 'Mac B')
        index = Index(hub, day=other_day)
        listed = {e.kind: e for e in index.collect(copy.session)[0] if e.day == sid}
        assert {'population', 'dataset-table', 'annotation'} <= set(listed)
        assert listed['population'].id == p.id and listed['dataset-table'].id == table['id']
        assert listed['annotation'].title.startswith('Note · ')
        assert listed['annotation'].values['text'] == 'Looks like a dividing cell'
        for entry in listed.values():
            assert 'read-only' in entry.summary and entry.source['inheritedFrom'] == sid
        # Each opens as the studio asks for it: by its own session.
        assert index.document(p.id, sid) is not None
        assert index.picture(p.id, day=sid)
        assert index.document(table['id'], sid)['detail']['available'] is True
        held = ''.join(path.read_text() for path in session_root(copy).rglob('journal.jsonl'))
        assert PRIVATE not in held
        with pytest.raises(ProjectLocationError, match='read-only'):
            hub.evidence.root_for(sid, write=True)
    finally:
        locations.close()
        other_locations.close()


def test_update_brings_a_newer_table_version_in_once(tmp_path):
    app, day, locations = host(tmp_path, 'Mac A')
    other, other_day, other_locations = host(tmp_path, 'Mac B')
    try:
        sid = day.new('Research')
        hub = results(app, tmp_path / 'Mac A')
        _, table, _ = research(app, day, hub)
        publish(app, sid, tmp_path)
        started = run(other, 'project-start-from', source={'kind': 'folder', 'path': str(tmp_path / 'Shared drive')},
                      name='Research copy')
        copy = other_locations.project(started['project']['session'])
        asyncio.run(hub.datatables.edit(table['id'], 1, [{'op': 'title', 'title': 'Reviewed measurements'}]))
        publish(app, sid, tmp_path)
        other_day.open(copy.session, 'test')
        update = run(other, 'project-update-from', session=copy.session)['update']
        assert update['resultsAdded'] == 1 and not update['refused']
        again = run(other, 'project-update-from', session=copy.session)['update']
        assert again['resultsAdded'] == 0
        index = Index(results(other, tmp_path / 'Mac B'), day=other_day)
        tables = [e for e in index.collect(copy.session)[0] if e.kind == 'dataset-table']
        assert [(t.id, t.version, t.title) for t in tables] == [(table['id'], 2, 'Reviewed measurements')]
    finally:
        locations.close()
        other_locations.close()


def test_a_note_holding_a_secret_blocks_the_publication(tmp_path):
    app, day, locations = host(tmp_path, 'Mac A')
    try:
        sid = day.new('Research')
        hub = results(app, tmp_path / 'Mac A')
        p, *_ = research(app, day, hub)
        day.record_annotation({'day': sid, 'id': p.id, 'population': p.id, 'revision': p.revision},
                              'token sk_live_' + 'a1B2c3D4e5F6g7H8i9', 'Key', asked=None)
        review = run(app, 'project-publish-prepare', session=sid)['review']
        assert review['blocked']
        assert any(f['path'].endswith(f'history/sessions/{sid}/journal.jsonl') and f['kind'] == 'secret'
                   for f in review['findings'])
    finally:
        locations.close()


def test_only_lines_that_list_results_can_travel_and_an_inherited_journal_holds_nothing_else(tmp_path):
    local = {'type': 'evidence-saved', 'id': 'e1', 'path': '/Users/someone/evidence/e1', 'thread': 't1'}
    portable = {**local, 'path': 'project:' + 'a' * 32 + '/artifacts/evidence/2026-10-07/e1/' + 'b' * 32}
    assert index_line(local) is None  # it would only say where this Mac keeps files
    assert b'thread' not in index_line(portable)
    assert index_line({'type': 'chat', 'id': 'x', 'text': PRIVATE}) is None
    app, day, locations = host(tmp_path, 'Mac A')
    try:
        sid = day.new('Research')
        from orchestrator.day import ownership
        owner = ownership.snapshot(session_root(locations.project(sid)), sid)
        body = ownership.journal_bytes(owner) + index_line(portable) + b'\n'
        assert split(body, sid) == (owner, [index_line(portable)])
        assert split(body + b'{"type":"chat"}\n', sid) is None
        assert split(body + b'{"type":"annota', sid) == (owner, [index_line(portable)])  # a torn line is not read
        elsewhere = tmp_path / 'Sessions'
        elsewhere.mkdir()
        assert bring_in(elsewhere, sid, owner, [index_line(portable)]) == 1
        assert bring_in(elsewhere, sid, owner, [index_line(portable)]) == 0
        (elsewhere / sid / 'journal.jsonl').write_bytes(body + b'{"type":"chat"}\n')
        with pytest.raises(ValueError, match='different session'):
            bring_in(elsewhere, sid, owner, [index_line(portable)])
    finally:
        locations.close()
