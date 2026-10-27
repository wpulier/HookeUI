"""Synthetic retained research, real server journal/completion/reopen; no worker or UI claims."""
import copy
from types import SimpleNamespace
import pytest
from orchestrator.day import DayStore
from orchestrator import workspace_views as w


def prepared(tmp_path, count=1):
    day = DayStore(root=tmp_path/'sessions')
    layout = w.read(day, schema=5)['layout']; layout['schema'] = 5
    layout['views'] += [{**copy.deepcopy(layout['views'][0]), 'id': f'tab{n}'} for n in range(1, count)]
    w.save(day, layout, 0)
    origin = {'session': day.sid, 'tab': 'main', 'placementId': 'pending', 'run': 'run'}
    w.admit(day, origin)
    session = SimpleNamespace(origin={'workspace': origin}, result={'findingRevision': {'version': 1}},
                              rid='r1', n=2, deliver='tab', label='Study', brief='Study')
    return day, session, layout


def test_new_research_is_kept_never_placed_and_keeps_the_layout_revision(tmp_path):
    day, researcher, draft = prepared(tmp_path)
    draft['views'][0]['title'] = 'Local name'
    base = w.read(day, schema=5)
    w.reconcile_research(day, [researcher]); w.reconcile_research(day, [researcher])
    researcher.result['findingRevision']['version'] = 2
    w.reconcile_research(day, [researcher])
    kept = w.read(day, schema=5)
    # No Tab was made or filled, and the layout kept its revision: an open studio's draft is still current.
    assert kept['layout'] == base['layout'] and kept['revision'] == base['revision']
    rows = kept['completions']
    assert [r['item']['resource']['version'] for r in rows] == [1, 2]
    assert all(r['state'] == 'available' and r['reason'] == w.KEPT_REASON for r in rows)
    assert all('outputTab' not in r and 'notice' not in r for r in rows)
    assert kept['pending'] == []  # its origin is complete, not waiting on a presentation
    saved = w.save(day, draft, base['revision'])
    assert [v['title'] for v in saved['layout']['views']] == ['Local name']
    reopened = w.read(DayStore(root=day.root, day=day.sid), schema=5)
    assert reopened['layout'] == saved['layout'] and len(reopened['completions']) == 2


def test_a_kept_completion_between_server_placements_still_merges_a_local_draft(tmp_path):
    day, researcher, draft = prepared(tmp_path)
    origin = researcher.origin['workspace']
    draft['views'][0]['title'] = 'Local name'
    def job(jid):
        return {'resource': {'day': day.sid, 'id': jid, 'version': None, 'picture': None}, 'title': jid,
                'zoom': 1, 'pan': [.5, .5]}
    w.reconcile_research(day, [researcher])
    assert w.complete(day, origin, 'job:j1', job('j1'))['state'] == 'placed'
    researcher.result['findingRevision']['version'] = 2
    w.reconcile_research(day, [researcher])
    assert w.complete(day, origin, 'job:j2', job('j2'))['state'] == 'placed'
    saved = w.save(day, draft, 1)  # its base predates all four completions
    assert saved['layout']['views'][0]['title'] == 'Local name'
    assert [i['resource']['id'] for i in saved['layout']['views'][0]['items']] == ['j1', 'j2']
    assert [a['item']['resource']['id'] for a in saved['completionAdditions']] == ['j1', 'j2']


def earlier_output_row(day, origin, version, state, **extra):
    """A completion row as 0.3.0 made it, when a researcher's results were placed in an output Tab."""
    import hashlib
    import json
    resource = {'day': day.sid, 'id': 'r1', 'version': version, 'picture': None}
    execution = f'research:r1:{version}'
    key = hashlib.sha256(json.dumps([execution, resource, origin['tab']], sort_keys=True).encode()).hexdigest()
    item = {'resource': resource, 'title': 'Study', 'zoom': 1, 'pan': [.5, .5],
            'placementId': origin['placementId'] + '-' + key[:12]}
    return {'id': key, 'origin': origin, 'execution': execution, 'item': item, 'state': state, **extra}


def test_an_earlier_output_tab_stays_readable_and_its_refusal_is_replaced_once(tmp_path):
    day, researcher, layout = prepared(tmp_path)
    origin = researcher.origin['workspace']
    # What 0.3.0 left in a journal: an output Tab holding version 1, and a refusal for version 2 whose
    # notice promised a placement once a Tab was removed.
    output = w._output_tab(origin, 'r1', 'Researcher 2 results')
    placed = earlier_output_row(day, origin, 1, 'placed', outputTab=copy.deepcopy(output),
                                notice='Results are in Researcher 2 results.')
    with_output = copy.deepcopy(layout)
    with_output['views'].append({**copy.deepcopy(output), 'items': [placed['item']]})
    day._record({'type': 'workspace-completion', 'completion': placed, 'layout': copy.deepcopy(with_output),
                 'revision': 2}, strict=True)
    refused = 'All 24 Tabs are in use. Remove a Tab to place these saved results.'
    day._record({'type': 'workspace-completion', 'revision': 3, 'layout': copy.deepcopy(with_output),
                 'completion': earlier_output_row(day, origin, 2, 'available', outputTab=copy.deepcopy(output),
                                                  reason=refused, notice=refused)}, strict=True)
    researcher.result['findingRevision']['version'] = 1
    w.reconcile_research(day, [researcher])
    researcher.result['findingRevision']['version'] = 2
    w.reconcile_research(day, [researcher])
    events = len(day.journal.load())
    w.reconcile_research(day, [researcher])
    assert len(day.journal.load()) == events  # replaced once, then it stands
    state = w.read(DayStore(root=day.root, day=day.sid), schema=5)
    assert state['layout'] == w.checked_layout(with_output, day.sid) and state['revision'] == 3
    assert state['layout']['views'][1]['kind'] == 'output'
    first, second = state['completions']
    assert first == placed  # the placed version and its Tab are left as they were
    assert second['state'] == 'available' and second['reason'] == w.KEPT_REASON
    assert 'notice' not in second and 'outputTab' not in second
    assert w.save(day, state['layout'], state['revision'])['revision'] == 3  # nothing changed: nothing written


def test_a_stale_draft_merges_an_earlier_output_tab_placement_around_a_kept_row(tmp_path):
    day, researcher, draft = prepared(tmp_path)
    origin = researcher.origin['workspace']
    # What 0.3.0 left at revision 2: version 1 placed in the output Tab it made for its researcher.
    output = w._output_tab(origin, 'r1', 'Researcher 2 results')
    placed = earlier_output_row(day, origin, 1, 'placed', outputTab=copy.deepcopy(output),
                                notice='Results are in Researcher 2 results.')
    with_output = w.read(day, schema=5)['layout']
    with_output['views'].append({**copy.deepcopy(output), 'items': [placed['item']]})
    day._record({'type': 'workspace-completion', 'completion': placed, 'layout': copy.deepcopy(with_output),
                 'revision': 2}, strict=True)
    researcher.result['findingRevision']['version'] = 2
    w.reconcile_research(day, [researcher])  # version 2 is only kept: no new revision
    assert w.read(day, schema=5)['revision'] == 2
    draft['views'][0]['title'] = 'Local name'
    saved = w.save(day, draft, 1)  # its base predates the output Tab and the kept row
    main, tab = saved['layout']['views']
    assert main['title'] == 'Local name' and main['items'] == []
    assert tab['id'] == output['id'] and tab['kind'] == 'output'
    assert tab['items'] == [placed['item']] and tab['items'][0]['resource']['version'] == 1
    (addition,) = saved['completionAdditions']
    assert addition['view'] == output and addition['tab'] == output['id'] and addition['item'] == placed['item']


def test_legacy_research_keeps_origin_tab(tmp_path):
    day, researcher, _ = prepared(tmp_path)
    researcher.deliver = None
    w.reconcile_research(day, [researcher])
    state = w.read(day, schema=5)
    assert len(state['layout']['views']) == 1
    assert state['layout']['views'][0]['items'][0]['resource']['id'] == 'r1'
    assert 'outputTab' not in state['completions'][0]


@pytest.mark.parametrize('change', ['kind', 'version', 'picture'])
def test_derived_windows_require_exact_matching_kind_and_revision(tmp_path, change):
    day, _, layout = prepared(tmp_path)
    resource = {'day': day.sid, 'id': 'tb1', 'version': 1, 'picture': None}
    item = {'resource': resource, 'placementId': 'table', 'presentation': 'datatable', 'title': 'Table', 'zoom': 1, 'pan': [.5, .5]}
    layout['views'][0]['items'] = [item]
    assert w.checked_layout(layout, day.sid) == layout
    resource[{'kind': 'id', 'version': 'version', 'picture': 'picture'}[change]] = {'kind': 'an1', 'version': None, 'picture': 0}[change]
    with pytest.raises(ValueError, match='exact saved kind and revision'):
        w.checked_layout(layout, day.sid)


def test_new_delivery_mode_is_persisted_and_legacy_record_is_unchanged(tmp_path, monkeypatch):
    import asyncio
    import json
    from orchestrator.research import ResearchManager
    from tests.test_research import FakeHub
    async def go():
        manager = ResearchManager(FakeHub(), root=tmp_path/'research')
        monkeypatch.setattr(manager, '_pump', lambda: None)
        async def snapshot(*args):
            return {}
        monkeypatch.setattr(manager, '_take_snapshot', snapshot)
        session = await manager.start('Bounded fixture', fake={'seconds': 0},
                                      origin={'workspace': {'session': manager.session(), 'tab': 'main', 'placementId': 'pending', 'run': 'run'}})
        assert session.deliver == 'tab'
        session.result = {'summary': 'Retained synthetic result'}
        session.status = 'done'
        manager._save(session)
        path = manager.root / session.day / session.rid / 'session.json'
        from tests.test_subject import as_real
        as_real(manager.root)  # synthetic persistence fixture only; no provider call
        record = json.loads(path.read_text())
        assert record['deliver'] == 'tab'
        restored = ResearchManager(FakeHub(), root=manager.root)
        assert restored.get(session.rid).deliver == 'tab'
        del record['deliver']
        path.write_text(json.dumps(record))
        legacy = ResearchManager(FakeHub(), root=manager.root).get(session.rid)
        assert legacy.deliver is None and 'deliver' not in legacy.wire()
    asyncio.run(go())
