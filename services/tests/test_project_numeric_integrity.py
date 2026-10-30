"""Synthetic measurements through actual table/journal and portable snapshot stores."""
import asyncio
import hashlib
import json

import pytest

from orchestrator.project_folder_copy import copy_owned_snapshot, verify_snapshot
from orchestrator.project_numeric_integrity import verify_numeric_records
from orchestrator.project_roots import ProjectLocationError, SNAPSHOT
from tests.test_project_table_routing import project


def measured(project):
    app, locations, tables, derivatives = project
    session = app.day.new('Numeric history')
    derivatives.evaluators['graph'] = lambda snapshot, spec: {'rows': len(snapshot['rows'])}
    async def run():
        first = await tables.create(['d1', 'd2'], mode='frozen')
        second = await tables.edit(first['id'], 1, [{'op': 'title', 'title': 'Reviewed measurements'}])
        analysis = await derivatives.create('analysis', first['id'], 2,
            {'method': 'welch', 'value': 'value', 'group': 'condition', 'groups': ['d1', 'd2']}, mode='frozen')
        graph = await derivatives.create('graph', first['id'], 2, {}, mode='frozen')
        return first, second, analysis, graph
    return session, asyncio.run(run())


def test_all_numeric_revisions_survive_copy_and_relocation_without_evaluation(project, tmp_path):
    app, locations, tables, derivatives = project
    session, records = measured(project)
    derivatives.evaluators = {}  # verification never evaluates stored settings
    root = locations.project(session)
    journal = app.day.journal.path.read_bytes()
    assert verify_numeric_records(root) == {'verifiedRevisions': 4}
    copied = copy_owned_snapshot(locations, session, tmp_path / 'Snapshot')
    copied.path.rename(tmp_path / 'Moved snapshot')
    assert verify_snapshot(tmp_path / 'Moved snapshot')['origin'] == root.origin
    assert app.day.journal.path.read_bytes() == journal


@pytest.mark.parametrize('record_index', [0, 1, 2, 3], ids=['old-table', 'current-table', 'analysis', 'graph'])
@pytest.mark.parametrize('damage', ['changed', 'missing'])
def test_damaged_historical_values_refuse_sealing(project, tmp_path, record_index, damage):
    app, locations, tables, derivatives = project
    session, records = measured(project)
    reader = tables if record_index < 2 else derivatives
    path = reader.path_of(records[record_index], session)
    if damage == 'missing': path.unlink()
    else: path.write_bytes(b'changed')
    with pytest.raises(ProjectLocationError, match='numeric history'):
        copy_owned_snapshot(locations, session, tmp_path / 'Snapshot')
    assert not (tmp_path / 'Snapshot').exists()
    assert app.day.journal.path.exists()


def test_outer_checksum_cannot_bless_changed_accepted_table(project, tmp_path):
    app, locations, tables, _ = project
    session, records = measured(project)
    root = locations.project(session)
    copied = copy_owned_snapshot(locations, session, tmp_path / 'Snapshot')
    relative = tables.path_of(records[0], session).relative_to(root.path)
    target = copied.path / relative
    target.chmod(0o600); target.write_bytes(b'changed')
    seal = copied.path / SNAPSHOT
    body = json.loads(seal.read_bytes())
    entry = next(item for item in body['entries'] if item['path'] == relative.as_posix())
    entry.update(bytes=7, sha256=hashlib.sha256(b'changed').hexdigest())
    seal.chmod(0o600); seal.write_text(json.dumps(body))
    with pytest.raises(ProjectLocationError, match='numeric history'):
        verify_snapshot(copied.path)


def test_limits_and_cancellation_preserve_original(project, monkeypatch):
    app, locations, _, _ = project
    session, _ = measured(project)
    root = locations.project(session)
    original = app.day.journal.path.read_bytes()
    monkeypatch.setattr('orchestrator.project_numeric_integrity.MAX_RECORDS', 1)
    with pytest.raises(ProjectLocationError, match='history allowance'):
        verify_numeric_records(root)
    def cancelled(): raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        verify_numeric_records(root, check=cancelled)
    assert app.day.journal.path.read_bytes() == original
