"""Synthetic retained-table lifecycle using real journals and immutable snapshots, no UI/model claims."""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from orchestrator.analysis_math import analyze
from orchestrator.datatables import DataTables, DataTableRefused
from orchestrator.day.store import DayStore
from orchestrator.table_derivatives import TableDerivatives


@pytest.fixture
def world(tmp_path, monkeypatch):
    day = DayStore(root=tmp_path / 'sessions')
    hub = SimpleNamespace()
    app = SimpleNamespace(day=day, hub=hub)
    tables = DataTables(app, tmp_path / 'tables')
    hub.datatables = tables
    async def read(app, eid, session, revision):
        return {'id': eid, 'kind': 'result-table', 'title': eid, 'revision': 0,
                'columns': [{'key': 'value', 'type': 'number', 'unit': 'µm'}, {'key': 'condition', 'type': 'text'}],
                'rows': [{'value': value, 'condition': eid} for value in ([1., 2., 3.] if eid == 'd1' else [4., 5., 6.])]}
    monkeypatch.setattr('orchestrator.tables.read_table', read)
    calls = []
    def evaluate(snapshot, spec):
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        calls.append(snapshot['version'])
        return analyze(snapshot, spec)
    derivatives = TableDerivatives(tables, tmp_path / 'derived', {'analysis': evaluate, 'graph': lambda snap, spec: {'rows': len(snap['rows'])}})
    hub.table_derivatives = derivatives
    spec = {'method': 'welch', 'value': 'value', 'group': 'condition', 'groups': ['d1', 'd2']}
    return app, tables, derivatives, calls, spec


def test_freeze_follow_and_reopen_keep_exact_source_and_accepted_result(world):
    from orchestrator.resources import Index
    app, tables, service, calls, spec = world
    async def go():
        table = await tables.create(['d1', 'd2'])
        with pytest.raises(DataTableRefused, match='exact saved table version'):
            await service.create('analysis', table['id'], None, spec)
        made = await service.create('analysis', table['id'], 1, spec)
        assert made['id'] == 'an1'
        first = service.read('an1')
        assert first['source'] == {'session': app.day.sid, 'id': 'tb1', 'version': 1, 'sha256': table['snapshot']['sha256']}
        assert first['result']['groups'][0]['n'] == 3 and calls == [1]
        assert Index(app.hub, day=app.day).get('an1').kind == 'analysis'
        await service.set_mode('an1', 1, 'frozen')
        await tables.edit('tb1', 1, [{'op': 'exclude', 'rows': ['d1:1'], 'reason': 'out of focus'}])
        await service.settled()
        assert service.version('an1')['version'] == 2 and calls == [1]
        assert service.wire(service.version('an1'))['freshness']['state'] == 'frozen'
        await service.set_mode('an1', 2, 'follow')
        await service.settled()
        latest = service.version('an1')
        assert latest['version'] == 4 and latest['source']['version'] == 2 and calls == [1, 2]
        assert service.read('an1')['result']['groups'][0]['n'] == 2
        assert service.read('an1', 1) == first
        app.day = DayStore(root=app.day.root, day=app.day.sid, clock=app.day.clock)
        restored = TableDerivatives(tables, service.root, {})
        assert restored.read('an1')['source'] == latest['source'] and calls == [1, 2]
    asyncio.run(go())


def test_failed_follow_keeps_previous_result_and_reports_stale(world):
    app, tables, service, calls, spec = world
    async def go():
        await tables.create(['d1', 'd2'])
        made = await service.create('analysis', 'tb1', 1, spec)
        before = service.read('an1')
        def failed(*args):
            raise DataTableRefused('selected groups no longer have enough observations', next='review exclusions')
        service.evaluators['analysis'] = failed
        await tables.edit('tb1', 1, [{'op': 'exclude', 'rows': ['d1:1'], 'reason': 'out of focus'}])
        await service.settled()
        assert service.version('an1') == made and service.read('an1') == before
        state = service.wire(made)['freshness']
        assert state['state'] == 'stale' and 'enough observations' in state['words']
    asyncio.run(go())


def test_owner_change_during_compute_publishes_nothing(world):
    app, tables, service, calls, spec = world
    entered, release = threading.Event(), threading.Event()
    def paused(snapshot, spec):
        entered.set()
        assert release.wait(5)
        return {'rows': len(snapshot['rows'])}
    service.evaluators['graph'] = paused
    async def go():
        await tables.create(['d1', 'd2'])
        task = asyncio.create_task(service.create('graph', 'tb1', 1, {}))
        assert await asyncio.to_thread(entered.wait, 5)
        app.day.generation += 1
        release.set()
        with pytest.raises(DataTableRefused, match='Project changed'):
            await task
        assert not app.day.table_derivatives and not list(service.root.glob('*/gr*/v*.json'))
    asyncio.run(go())


def test_concurrent_derivatives_are_distinct_and_corruption_refuses(world):
    app, tables, service, calls, spec = world
    async def go():
        await tables.create(['d1', 'd2'])
        a, b = await asyncio.gather(service.create('graph', 'tb1', 1, {}), service.create('graph', 'tb1', 1, {}))
        assert {a['id'], b['id']} == {'gr1', 'gr2'}
        assert service.read(a['id'])['result'] == {'rows': 6}
        path = service.root / a['snapshot']['path']
        path.write_text('{}')
        with pytest.raises(DataTableRefused, match='accepted hash'):
            service.read(a['id'])
        assert service.read(b['id'])['result'] == {'rows': 6}
        (service.root / b['snapshot']['path']).unlink()
        with pytest.raises(DataTableRefused, match='file is unavailable'):
            service.read(b['id'])
    asyncio.run(go())


def test_graph_annotations_use_only_accepted_same_source_analysis(world):
    app, tables, service, calls, spec = world
    received = []
    def graph(snapshot, settings, *, analysis=None):
        received.append(analysis)
        return {'p': analysis['p']} if analysis else {'rows': len(snapshot['rows'])}
    service.evaluators['graph'] = graph
    async def go():
        await tables.create(['d1', 'd2'])
        analysis = await service.create('analysis', 'tb1', 1, spec, mode='frozen')
        result = service.read(analysis['id'])['result']
        reference = {'id': analysis['id'], 'version': 1}
        saved = await service.create('graph', 'tb1', 1, {'analysis': reference}, mode='frozen')
        assert received == [result] and calls == [1]
        assert service.read(saved['id'])['result'] == {'p': result['p']}
        with pytest.raises(DataTableRefused, match='exact saved analysis revision'):
            await service.create('graph', 'tb1', 1, {'analysis': {**reference, 'p': .001}})
        with pytest.raises(DataTableRefused, match='does not use this exact table'):
            await service.create('graph', 'tb1', 1, {'analysis': {'id': saved['id'], 'version': 1}})
        await tables.edit('tb1', 1, [{'op': 'exclude', 'rows': ['d1:1'], 'reason': 'out of focus'}])
        with pytest.raises(DataTableRefused, match='does not use this exact table'):
            await service.create('graph', 'tb1', 2, {'analysis': reference})
        assert received == [result] and calls == [1]
    asyncio.run(go())


def test_derived_result_keeps_exact_comparison_warning_without_recomputing_on_read(world):
    app, tables, service, calls, spec = world
    async def go():
        sources = await tables._read_sources([{'id': 'd1', 'revision': 0}, {'id': 'd2', 'revision': 0}], app.day.sid)
        comparison = {'note': 'Same objects, not independent samples.', 'capture': 5, 'arms': []}
        await tables._create_tables(sources, title='Synthetic method comparison', by='user', mode='frozen',
                                    edits=None, owner=tables.capture(), comparison=comparison)
        accepted = await service.create('analysis', 'tb1', 1, spec, mode='frozen')
        snapshot = service.read(accepted['id'], accepted['version'])
        assert snapshot['comparison']['note'] == comparison['note']
        assert service.wire(accepted, snapshot)['comparison'] == snapshot['comparison']
        assert calls == [1]
    asyncio.run(go())
