"""Real private result storage; synthetic tables, no model or browser qualification."""
import asyncio
import json
import csv
import io
import os
import threading
from types import SimpleNamespace

import pytest

from orchestrator.day.store import DayStore
from orchestrator.resources import Index, Results
from orchestrator import tables
from tests.waiting import HANG_S


def retained(tmp_path):
    day = DayStore(root=tmp_path / 'sessions', day='2026-10-22')
    results = Results(tmp_path / 'results', day=day)
    values = {'name': 'Texture measurements', 'output': 'table.objects',
              'columns': [{'name': f'Feature {i}', 'unit': 'px', 'definition': 'Measured definition ' * 10}
                          for i in range(8)], 'rows': [[i + .25 for i in range(8)]]}
    values['producer'] = {'implementation': 'example', 'version': '1',
                          'sha256': tables.kept_table_digest(values)}
    entry = results.add('run_capability', {}, '<capability>raw agent data</capability>',
                        values=values, executions=['execution-owned'])
    assert set(entry['values']) == {'cut'}
    return day, results, entry, values


@pytest.mark.parametrize('historical', [False, True])
def test_cut_table_preview_keeps_data_action_and_reopen_routes(tmp_path, historical):
    day, results, entry, values = retained(tmp_path)
    if historical:
        journal = tmp_path / 'sessions' / day.sid / 'journal.jsonl'
        rows = [json.loads(line) for line in journal.read_text().splitlines()]
        for row in rows:
            row.pop('tableDescriptor', None)
        journal.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    day = DayStore(root=tmp_path / 'sessions', day=day.sid)
    results = Results(tmp_path / 'results', day=day)
    index = Index(None, results=results, day=day)
    assert index.get(entry['id'], day.sid, discover_tables=True).wire().get('table') is True
    hub = SimpleNamespace(resources=index)
    said = tables.made_words(hub, ['execution-owned'])
    assert said == ('Texture measurements made a table of 1 row.', entry['id'], {'session': day.sid, 'revision': 0})
    assert tables.made_words(hub, ['another-execution']) is None
    app = SimpleNamespace(hub=hub, day=day)
    table = asyncio.run(tables.read_table(app, entry['id'], day.sid, 0))
    assert list(table['rows'][0].values()) == values['rows'][0]
    assert 'Feature 0 (px)' in tables.csv_text(table)
    with pytest.raises(tables.TableRefused):
        asyncio.run(tables.read_table(app, entry['id'], '2026-10-21', 0))
    path = tmp_path / 'results' / entry['day'] / f"{entry['id']}.json"
    record = json.loads(path.read_text()); record['values']['rows'][0][0] = 999
    path.write_text(json.dumps(record))
    with pytest.raises(tables.TableRefused, match='sha256'):
        asyncio.run(tables.read_table(app, entry['id'], day.sid, 0))


def test_new_table_routing_hint_is_small_and_never_reads_rows(tmp_path, monkeypatch):
    day, results, entry, values = retained(tmp_path)
    assert entry['tableDescriptor'] == {'format': 1, 'sha256': values['producer']['sha256']}
    assert len(json.dumps(entry['tableDescriptor'])) < 100
    monkeypatch.setattr('orchestrator.resources.os.open', lambda *a, **kw: pytest.fail('New index reread table rows'))
    assert results.table_candidate(entry, day.sid)
    assert not results.table_candidate(entry, '2026-10-21')


@pytest.mark.parametrize('damage', ['missing', 'json', 'record', 'values', 'producer', 'limit', 'symlink'])
def test_legacy_table_discovery_refuses_unreadable_or_unbounded_records(tmp_path, monkeypatch, damage):
    from orchestrator import resources
    day, results, entry, _ = retained(tmp_path)
    entry.pop('tableDescriptor')
    path = tmp_path / 'results' / entry['day'] / f"{entry['id']}.json"
    record = json.loads(path.read_text())
    if damage == 'missing':
        path.unlink()
    elif damage == 'json':
        path.write_text('{')
    elif damage == 'limit':
        monkeypatch.setattr(resources, 'TABLE_DISCOVERY_BYTES', 1)
    elif damage == 'symlink':
        original = path.with_suffix('.original'); path.rename(original); path.symlink_to(original)
    else:
        if damage == 'record': record['id'] = 'd999'
        if damage == 'values': record['values'] = ['not', 'a', 'table']
        if damage == 'producer': record['values']['producer'] = 'not a producer'
        path.write_text(json.dumps(record))
    assert not results.table_candidate(entry, day.sid, discover=True)


@pytest.mark.parametrize('values', [None, [], {'producer': 'not a producer'}, {'producer': {'sha256': 'bad'}, 'columns': {}, 'rows': []}])
def test_table_reader_refuses_malformed_retained_values(values):
    assert tables.result_table('d1', {'title': 'Malformed'}, values) is None


def test_legacy_discovery_cache_never_reparses_unchanged_rows_and_invalidates_changes(tmp_path, monkeypatch):
    from orchestrator import resources
    day, results, entry, _ = retained(tmp_path)
    entry.pop('tableDescriptor')
    monkeypatch.setattr(resources, 'TABLE_DISCOVERY_CACHE', 1)
    assert results.table_candidate(entry, day.sid, discover=True)
    with monkeypatch.context() as patch:
        patch.setattr(resources.json, 'loads', lambda *a, **kw: pytest.fail('Unchanged legacy table parsed twice'))
        assert results.table_candidate(entry, day.sid, discover=True)
    path = tmp_path / 'results' / entry['day'] / f"{entry['id']}.json"
    path.write_text(json.dumps({'id': entry['id'], 'values': {}}))
    assert not results.table_candidate(entry, day.sid, discover=True)
    assert len(results._table_candidates) == 1


def test_action_reads_table_off_loop_and_keeps_original_project_during_switch(tmp_path, monkeypatch):
    day, results, entry, _ = retained(tmp_path)
    hub = SimpleNamespace(resources=SimpleNamespace(results=results))
    owner = day.sid
    read = results.values
    entered, release = threading.Event(), threading.Event()
    main_thread = threading.get_ident()
    def blocked(rid, session=None):
        assert threading.get_ident() != main_thread
        assert session == owner
        entered.set()
        assert release.wait(HANG_S), 'table read was not released'
        return read(rid, session)
    monkeypatch.setattr(results, 'values', blocked)
    async def go():
        pending = asyncio.create_task(tables.made_words_async(hub, ['execution-owned']))
        try:
            assert await asyncio.to_thread(entered.wait, HANG_S)
            results.day = DayStore(root=tmp_path / 'sessions', day='2026-10-21')
        finally:
            release.set()
        said = await pending
        assert said[1:] == (entry['id'], {'session': owner, 'revision': 0})
    asyncio.run(go())


def test_csv_preserves_retained_float_precision_and_large_integer_ids():
    values = [0.5000000158700323, 0.9999999771043828, 1.234567890123456e-12, 2**63 + 7]
    table = {'columns': [{'key': str(i), 'label': str(i), 'unit': ''} for i in range(4)],
             'rows': [dict(zip(map(str, range(4)), values))]}
    exported = list(csv.reader(io.StringIO(tables.csv_text(table))))[1]
    assert [float(v) for v in exported[:3]] == values[:3]
    assert int(exported[3]) == values[3]


def test_cold_synchronous_index_never_discovers_legacy_rows(tmp_path, monkeypatch):
    day, results, entry, _ = retained(tmp_path)
    # This is the in-memory journal record after loading an older Project.
    actual = results.get(entry['id'], day.sid)
    actual.pop('tableDescriptor')
    index = Index(None, results=results, day=day)
    original_open = os.open
    def metadata_only(path, *args, **kwargs):
        if str(path).startswith(str(results.root)):
            pytest.fail('Metadata lookup read legacy rows')
        return original_open(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr('orchestrator.resources.os.open', metadata_only)
        assert index.get(entry['id'], day.sid).wire().get('table') is None
    # The off-loop API opts in, then subsequent metadata-only readers share its hint.
    assert index.get(entry['id'], day.sid, discover_tables=True).wire()['table'] is True
    with monkeypatch.context() as patch:
        patch.setattr('orchestrator.resources.os.open', metadata_only)
        assert index.get(entry['id'], day.sid).wire()['table'] is True
