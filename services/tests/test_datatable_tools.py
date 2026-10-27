"""Real shared journals/services with synthetic source tables; no provider, browser or scientific qualification."""
import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from orchestrator.brain.registry import Registry, ToolContext, ToolFailed
from orchestrator.brain.toolbox.datatable_tools import add_datatable_tools
from orchestrator.day.store import DayStore
from orchestrator.graph_math import graph
from orchestrator.resources import Index
from orchestrator.table_reading import read_accepted, page
from orchestrator.datatables import DataTableRefused
from tests.test_table_derivatives import world  # noqa: F401


def setup(world):
    app, tables, derivatives, calls, spec = world
    derivatives.evaluators['graph'] = graph
    services = SimpleNamespace(hub=app.hub, day=app.day)
    registry = Registry(); add_datatable_tools(registry, services)
    context = ToolContext('chat', 1, None, asked=app.day.here())
    async def run(name, args):
        result = registry.start(name, args, context)
        if result.task: await result.task
        return result
    return app, tables, derivatives, calls, spec, registry, context, run


def test_registered_callers_share_table_edits_numerics_exact_reads_and_trusted_producer(world):
    app, tables, service, calls, spec, registry, ctx, run = setup(world)
    async def go():
        made = await run('make_table', {'sources': [{'id': 'd1', 'revision': 0}, {'id': 'd2', 'revision': 0}]})
        assert made.ok is True, made.summary
        assert made.values['id'] == 'tb1' and made.values['by'] == 'agent:chat'
        changed = await run('edit_table', {'id': 'tb1', 'version': 1, 'edits': [{'op': 'exclude', 'rows': ['d1:1'], 'reason': 'out of focus'}]})
        assert changed.ok is True and changed.values['version'] == 2
        analyzed = await run('analyze', {'table': 'tb1', 'version': 2, 'spec': spec, 'mode': 'frozen', 'by': 'forged'})
        assert analyzed.ok is True, analyzed.summary
        assert service.version('an1')['by'] == 'agent:chat' and service.version('an1')['thread'] == ctx.asked.thread
        result = service.read('an1')['result']
        assert result['groups'][0]['n'] == 2 and len(result['exclusions']) == 1 and calls == [2]
        graphed = await run('make_graph', {'table': 'tb1', 'version': 2, 'spec': {'kind': 'box', 'value': 'value', 'group': 'condition', 'groups': ['d1', 'd2'], 'analysis': {'id': 'an1', 'version': 1}}})
        assert graphed.ok is True, graphed.summary
        assert service.read('gr1')['result']['annotations'][0]['p'] == result['p']
        await service.set_mode('an1', 1, 'follow')
        reading = await run('read_analysis', {'id': 'an1', 'version': 1})
        assert reading.ok is True and str(result['p']) in reading.summary
        assert '"version": 1' in reading.summary and calls == [2]  # reading is not evaluation
        stale = await run('edit_table', {'id': 'tb1', 'version': 1, 'edits': [{'op': 'title', 'title': 'overwrite'}]})
        assert stale.ok is False and tables.latest('tb1')['version'] == 2
    asyncio.run(go())


def test_original_project_and_request_cancellation_refuse_before_publication(world, monkeypatch):
    app, tables, service, calls, spec, registry, ctx, run = setup(world)
    entered, release = threading.Event(), threading.Event()
    cancelled = False
    from orchestrator.brain.toolbox import datatable_tools
    original = datatable_tools.require_current
    def check(context, day):
        original(context, day)
        if cancelled: raise ToolFailed('original request cancelled')
    monkeypatch.setattr(datatable_tools, 'require_current', check)
    def delayed(snapshot, spec):
        entered.set(); assert release.wait(5); return {'accepted': 'must not publish'}
    service.evaluators['graph'] = delayed
    async def go():
        nonlocal cancelled
        await tables.create(['d1', 'd2'])
        task = asyncio.create_task(run('make_graph', {'table': 'tb1', 'version': 1, 'spec': {'kind': 'box', 'value': 'value'}}))
        assert await asyncio.to_thread(entered.wait, 5)
        cancelled = True; release.set(); result = await task
        assert result.ok is False and not app.day.table_derivatives and not list(service.root.glob('**/v*.json'))
        cancelled = False
        app.day.generation += 1
        refused = await run('make_table', {'sources': [{'id': 'd1', 'revision': 0}]})
        assert refused.ok is False and list(app.day.datatables) == ['tb1']
    asyncio.run(go())


def test_explicit_resource_read_has_numeric_rows_and_closed_project_read_never_rebinds_live_owner(world):
    app, tables, service, calls, spec, *_ = setup(world)
    async def go():
        source = app.day.sid
        await tables.create(['d1', 'd2'])
        await service.create('analysis', 'tb1', 1, spec, mode='frozen')
        index = Index(app.hub, day=app.day)
        assert 'rows' not in index.get('tb1').values  # metadata discovery never loads numeric snapshots
        assert index.document('tb1')['detail']['values']['snapshot']['rows'][0]['value'] == 1
        assert index.document('an1')['detail']['values']['snapshot']['result']['groups'][1]['n'] == 3
        root = app.day.root
        app.day = DayStore(root=root, day='2026-10-23.99')
        historical = Index(app.hub, day=app.day)
        assert historical.document('tb1', source)['detail']['values']['snapshot']['rows'][0]['value'] == 1
        assert historical.document('an1', source)['detail']['available'] is True
        assert app.day.sid == '2026-10-23.99' and calls == [1]
        record = historical._journal(source)['table_derivatives']['an1'][0]
        (service.root / record['snapshot']['path']).write_text('changed data')
        unreadable = historical.document('an1', source)['detail']
        assert unreadable['available'] is False and unreadable['values'] is None and 'hash' in unreadable['text']
        with pytest.raises(ToolFailed, match='hash'): historical.read('an1', source)
    asyncio.run(go())


def test_numeric_paging_preserves_exact_values_and_hash_boundary(world):
    app, tables, service, calls, spec, *_ = setup(world)
    async def go():
        await tables.create(['d1', 'd2'])
        record = tables.version('tb1', 1)
        value = read_accepted(tables.root, record, app.day.sid, 'dataset-table')
        value['notes'] = 'x' * 20000
        first = page(value); header = json.loads(first.splitlines()[0].split(': ', 1)[1])
        assert header['nextOffset'] is not None
        second = page(value, offset=header['nextOffset'], digest=header['digest'])
        assert 'Accepted numeric result page' in second
        value['notes'] += 'y'
        with pytest.raises(DataTableRefused, match='changed'): page(value, offset=header['nextOffset'], digest=header['digest'])
        foreign = {**record, 'snapshot': {**record['snapshot'], 'path': 'other/tb1/v1.json'}}
        with pytest.raises(DataTableRefused, match='Project'): read_accepted(tables.root, foreign, app.day.sid, 'dataset-table')
    asyncio.run(go())


def test_registry_declares_real_services_before_app_finishes_construction(world):
    app, tables, derivatives, *_ = world
    available = [None, None]
    calls = []
    def resolve():
        calls.append(True)
        return tuple(available)
    registry = Registry()
    add_datatable_tools(registry, SimpleNamespace(hub=SimpleNamespace(), day=app.day, table_services=resolve))
    assert set(registry.tools) == {'make_table', 'edit_table', 'analyze', 'make_graph', 'read_analysis'}
    assert calls == []  # toolbox.build does not access not-yet-constructed App services
    available[:] = [tables, derivatives]
    async def go():
        run = registry.start('make_table', {'sources': [{'id': 'd1', 'revision': 0}]}, ToolContext('chat', 1, None, asked=app.day.here()))
        await run.task
        assert run.ok is True, run.summary
        assert tables.version('tb1', 1)['by'] == 'agent:chat'
    asyncio.run(go())
    assert calls == [True]


def test_historical_numeric_reads_bind_project_not_calendar_save_date(world):
    app,tables,derivatives,calls,spec,*_=setup(world)
    app.day.open('2026-10-22.3')
    async def go():
        made=await tables.create(['d1','d2'])
        analyzed=await derivatives.create('analysis',made['id'],1,spec,mode='frozen')
        session=app.day.sid
        assert made['day']!=session and made['session']==session
        assert analyzed['day']!=session and analyzed['session']==session
        before=read_accepted(tables.root,made,session,'dataset-table')
        legacy=dict(made);legacy.pop('session')
        assert read_accepted(tables.root,legacy,session,'dataset-table')['snapshot']==before['snapshot']
        assert read_accepted(derivatives.root,analyzed,session,'analysis')['snapshot']['source']['session']==session
        app.day.open('2026-10-22.4')
        assert read_accepted(tables.root,made,session,'dataset-table')['snapshot']==before['snapshot']
        for foreign in ({**made,'session':app.day.sid}, {**made,'snapshot':{**made['snapshot'],'path':f'{app.day.sid}/{made["id"]}/v1.json'}}):
            with pytest.raises(DataTableRefused):read_accepted(tables.root,foreign,session,'dataset-table')
        foreign={**analyzed,'source':{**analyzed['source'],'session':app.day.sid}}
        with pytest.raises(DataTableRefused):read_accepted(derivatives.root,foreign,session,'analysis')
        assert calls==[1]
    asyncio.run(go())
