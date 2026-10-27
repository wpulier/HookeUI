import copy
import pytest
from orchestrator.day import DayStore
from orchestrator import workspace_views as w
from tests.test_workspace_views import layout


def migrated(day):
    return w.read(day, schema=2)['layout']


def test_migration_is_deterministic_and_refuses_old_writer(tmp_path):
    day=DayStore(root=tmp_path/'sessions'); old=layout()
    old['views'][0]['items']=[{'resource':{'day':day.sid,'id':'e1','version':0,'picture':0},'title':'Masks','pan':[.5,.5],'zoom':1,'presentation':'masks','position':[500,100]}]
    w.save(day,old,0)
    a=migrated(day); b=migrated(DayStore(root=day.root,day=day.sid))
    assert a==b and a['schema']==2
    item=a['views'][0]['items'][0]
    assert item['placementId'] and item.get('position') is None
    assert item['geometryUnavailable'] is True
    w.save(day,a,1)
    with pytest.raises(ValueError,match='Reload'):w.save(day,old,2)
    assert migrated(day)==a


def test_conflict_contains_authoritative_note_and_layout(tmp_path):
    day=DayStore(root=tmp_path/'sessions'); note={'id':'a'+'e'*32,'title':'Note','text':'Saved','base':0}
    w.save_note(day,note)
    with pytest.raises(ValueError) as err:w.save_note(day,{**note,'text':'Draft'})
    assert err.value.code=='conflict' and err.value.current['text']=='Saved'
    state=migrated(day);w.save(day,state,0)
    stale=copy.deepcopy(state);stale['views'][0]['title']='Draft'
    with pytest.raises(ValueError) as err:w.save(day,stale,0)
    assert err.value.current['layout']==state


def test_remove_restore_capacity_keeps_data_and_survives_restart(tmp_path):
    day=DayStore(root=tmp_path/'sessions'); state=migrated(day)
    for n in range(1,24):state['views'].append({**copy.deepcopy(state['views'][0]),'id':f't{n}'})
    w.save(day,state,0)
    w.arrangement(day,'remove','t1',1)
    assert len(migrated(day)['views'])==23
    restarted=DayStore(root=day.root,day=day.sid)
    assert w.read(restarted,schema=2)['removed'][0]['id']=='t1'
    w.arrangement(restarted,'restore','t1',2)
    assert len(migrated(restarted)['views'])==24


def test_completion_merges_latest_layout_and_replays_without_reopening_dismissed(tmp_path):
    day=DayStore(root=tmp_path/'sessions'); w.save(day,migrated(day),0)
    origin={'session':day.sid,'tab':'main','placementId':'pending-one','run':'run-one'}
    w.admit(day,origin)
    state=migrated(day);state['views'][0]['title']='Edited while running';w.save(day,state,1)
    item={'resource':{'day':day.sid,'id':'e1','version':0,'picture':0},'title':'Result','pan':[.5,.5],'zoom':1,'presentation':'masks'}
    w.complete(day,origin,'execution-one',item)
    assert migrated(day)['views'][0]['title']=='Edited while running'
    assert len(migrated(day)['views'][0]['items'])==1
    state=migrated(day);state['views'][0]['items']=[];w.save(day,state,w.read(day,schema=2)['revision'])
    restarted=DayStore(root=day.root,day=day.sid)
    w.complete(restarted,origin,'execution-one',item)
    assert migrated(restarted)['views'][0]['items']==[]
    assert len(w.read(restarted,schema=2)['completions'])==1


def test_persisted_execution_reconciles_inactive_project_and_removed_destination(tmp_path):
    day=DayStore(root=tmp_path/'sessions'); owner=day.sid
    w.save(day,migrated(day),0)
    origin={'session':owner,'tab':'main','placementId':'pending-r','run':'r','input':{'source':'a1'}}
    w.admit(day,origin);w.link_execution(day,origin,'x1')
    w.arrangement(day,'remove','main',1)
    day._record({'type':'result','id':'r1','session':owner,'title':'Retained','executions':['x1'],'pictures':[]})
    restored=DayStore(root=day.root,day=owner);w.reconcile(restored)
    rows=w.read(restored,schema=2)['completions']
    assert len(rows)==1 and rows[0]['state']=='available'
    assert rows[0]['item']['resource']['day']==owner
    w.reconcile(restored);assert len(w.read(restored,schema=2)['completions'])==1


@pytest.mark.parametrize('state', ['saving', 'failed', 'interrupted'])
def test_unconfirmed_result_does_not_open_a_window_and_saved_retry_reconciles(tmp_path, state):
    day = DayStore(root=tmp_path/'sessions')
    w.save(day, migrated(day), 0)
    for run in ('good', 'attempt'):
        origin = {'session': day.sid, 'tab': 'main', 'placementId': f'pending-{run}', 'run': run}
        w.admit(day, origin)
        w.link_execution(day, origin, f'x-{run}')
    good = {'type': 'result', 'id': 'r-good', 'title': 'Earlier saved result',
            'executions': ['x-good'], 'pictures': [], 'state': 'done'}
    attempt = {'type': 'result', 'id': 'r-attempt', 'title': 'New result',
               'executions': ['x-attempt'], 'pictures': [], 'state': state}
    day._record(good)
    day._record(attempt)
    restored = DayStore(root=day.root, day=day.sid)
    w.reconcile(restored)
    items = migrated(restored)['views'][0]['items']
    assert [item['resource']['id'] for item in items] == ['r-good']
    assert any(event.get('id') == 'r-attempt' and event.get('state') == state
               for event in restored.journal.load())
    # A delayed save completes this result; a failed attempt gets a new retry identity.
    saved_id = 'r-attempt' if state == 'saving' else 'r-retry'
    restored._record({**attempt, 'id': saved_id, 'state': 'done'})
    w.reconcile(restored)
    restarted = DayStore(root=day.root, day=day.sid)
    w.reconcile(restarted)
    items = migrated(restarted)['views'][0]['items']
    assert [item['resource']['id'] for item in items] == ['r-good', saved_id]
    assert len(w.read(restarted, schema=2)['completions']) == 2


def test_removing_then_restoring_does_not_resurrect_pending_presentation(tmp_path):
    day=DayStore(root=tmp_path/'sessions');w.save(day,migrated(day),0)
    origin={'session':day.sid,'tab':'main','placementId':'pending-r','run':'r'};w.admit(day,origin)
    w.arrangement(day,'remove','main',1);w.arrangement(day,'restore','main',2)
    item={'resource':{'day':day.sid,'id':'e1','version':0,'picture':0},'title':'Result','pan':[.5,.5],'zoom':1,'presentation':'masks'}
    assert w.complete(day,origin,'x1',item)['state']=='available'
    assert migrated(day)['views'][0]['items']==[]


def test_pending_index_preserves_chronological_dismissal_and_new_runs(tmp_path):
    day = DayStore(root=tmp_path/'sessions')
    w.save(day, migrated(day), 0)
    def origin(run):
        return {'session': day.sid, 'tab': 'main', 'placementId': f'pending-{run}', 'run': run}
    w.admit(day, origin('before-remove'))
    w.arrangement(day, 'remove', 'main', 1)
    w.arrangement(day, 'restore', 'main', 2)
    w.admit(day, origin('after-restore'))
    w.admit(day, origin('dismissed'))
    w.dismiss(day, 'dismissed')
    w.admit(day, origin('completed'))
    item = {'resource': {'day': day.sid, 'id': 'e1', 'version': 0, 'picture': 0},
            'title': 'Result', 'pan': [.5, .5], 'zoom': 1, 'presentation': 'masks'}
    w.complete(day, origin('completed'), 'x1', item)
    restarted = DayStore(root=day.root, day=day.sid)
    assert [row['run'] for row in w.read(restarted, schema=2)['pending']] == ['after-restore']
    assert w.dismissed(restarted, origin('before-remove'))
    assert not w.dismissed(restarted, origin('after-restore'))


def test_unknown_reader_and_malformed_display_are_refused(tmp_path):
    day=DayStore(root=tmp_path/'sessions')
    with pytest.raises(w.WorkspaceError):w.read(day,schema=w.SCHEMA+1)
    state=migrated(day)
    state['views'][0]['items']=[{'placementId':'p1','resource':{'day':day.sid,'id':'e1','version':0,'picture':0},'title':'Result','pan':[.5,.5],'zoom':1,'display':{'reading':'not a number'}}]
    with pytest.raises(ValueError,match='display'):w.save(day,state,0)


@pytest.mark.parametrize('operation', ['read-old', 'read-current', 'save-old', 'save-current', 'upgrade'])
def test_future_persisted_schema_is_never_downconverted_or_overwritten(tmp_path, operation):
    day = DayStore(root=tmp_path/'sessions')
    current = migrated(day)
    future = {**copy.deepcopy(current), 'schema': w.SCHEMA + 1, 'futureField': {'retained': 'value'}}
    future['views'][0]['items'] = [{
        'placementId': 'original-placement', 'resource': {'day': day.sid, 'id': 'e1', 'version': 0, 'picture': 0},
        'title': 'Retained', 'pan': [.5, .5], 'zoom': 1, 'position': [123, 456],
    }]
    day._record({'type': 'workspace-layout', 'revision': 7, 'layout': future}, strict=True)
    before = day.journal.path.read_bytes()
    with pytest.raises(w.WorkspaceError) as error:
        if operation.startswith('read-'):
            w.read(day, schema=1 if operation == 'read-old' else 2)
        elif operation.startswith('save-'):
            w.save(day, layout() if operation == 'save-old' else current, 7)
        else:
            w.upgrade(day, future, 7)
    assert error.value.code == 'unsupported-schema'
    assert day.journal.path.read_bytes() == before
    assert day.workspace_layout['layout'] == future


def test_two_store_instances_cannot_overwrite_competing_arrangements(tmp_path):
    day=DayStore(root=tmp_path/'sessions');other=DayStore(root=day.root,day=day.sid)
    state=migrated(day);w.save(day,state,0)
    first=copy.deepcopy(state);first['views'][0]['title']='First writer';w.save(day,first,1)
    second=copy.deepcopy(state);second['views'][0]['title']='Second writer'
    with pytest.raises(w.WorkspaceError) as err:w.save(other,second,1)
    assert err.value.code=='conflict'
    assert migrated(other)['views'][0]['title']=='First writer'


def test_nested_workspace_calls_read_journal_once_and_append_invalidates_cache(tmp_path, monkeypatch):
    day = DayStore(root=tmp_path/'sessions')
    reads = []
    original = day.journal.load
    monkeypatch.setattr(day.journal, 'load', lambda: (reads.append(True), original())[1])
    w.admit(day, {'session': day.sid, 'tab': 'main', 'placementId': 'pending', 'run': 'r'})
    assert len(reads) == 1  # admit and its nested read share one snapshot
    assert len(w.read(day, schema=2)['pending']) == 1
    assert len(reads) == 2  # admission appended, invalidating the first snapshot
    w.read(day, schema=2)
    assert len(reads) == 2
    other = DayStore(root=day.root, day=day.sid)
    current = w.read(other, schema=2)
    current['layout']['views'][0]['title'] = 'Written by another store'
    w.save(other, current['layout'], current['revision'])
    assert migrated(day)['views'][0]['title'] == 'Written by another store'
    assert len(reads) == 3


def test_nested_completion_replay_rebuilds_workspace_once(tmp_path, monkeypatch):
    day = DayStore(root=tmp_path/'sessions')
    w.save(day, migrated(day), 0)
    for number in range(3):
        origin = {'session': day.sid, 'tab': 'main', 'placementId': f'p{number}', 'run': f'r{number}'}
        w.admit(day, origin)
        w.link_execution(day, origin, f'x{number}')
        day._record({'type': 'result', 'id': f'r{number}', 'title': 'Result', 'executions': [f'x{number}'], 'pictures': []})
    expected = sum(event['type'].startswith('workspace-') for event in day.journal.load())
    applied = []
    original = day._apply
    def observe(event, **kwargs):
        if kwargs.get('replay') and event['type'].startswith('workspace-'):
            applied.append(event)
        return original(event, **kwargs)
    monkeypatch.setattr(day, '_apply', observe)
    w.reconcile(day)
    assert len(applied) == expected


def test_journal_changed_during_read_is_not_cached(tmp_path, monkeypatch):
    day = DayStore(root=tmp_path/'sessions')
    current = migrated(day)
    w.save(day, current, 0)
    changed = copy.deepcopy(current)
    changed['views'][0]['title'] = 'Appended during read'
    original = day.journal.load
    reads = []
    def append_during_first_read():
        events = original()
        reads.append(True)
        if len(reads) == 1:
            day.journal.append({'type': 'workspace-layout', 'revision': 2, 'layout': changed})
        return events
    monkeypatch.setattr(day.journal, 'load', append_during_first_read)
    assert migrated(day) == current
    assert migrated(day) == changed
    assert len(reads) == 2
    assert migrated(day) == changed
    assert len(reads) == 2


def test_completion_retries_only_placement_when_capacity_is_freed(tmp_path):
    day=DayStore(root=tmp_path/'sessions');state=migrated(day)
    item={'resource':{'day':day.sid,'id':'e1','version':0,'picture':0},'title':'Result','pan':[.5,.5],'zoom':1,'presentation':'masks'}
    state['views'][0]['items']=[{**copy.deepcopy(item),'placementId':f'p{i}'} for i in range(64)]
    w.save(day,state,0);origin={'session':day.sid,'tab':'main','placementId':'pending','run':'r'};w.admit(day,origin)
    assert w.complete(day,origin,'x1',item)['state']=='available'
    current=w.read(day,schema=2);current['layout']['views'][0]['items'].pop();w.save(day,current['layout'],current['revision'])
    assert w.complete(day,origin,'x1',item)['state']=='placed'
    assert len(w.read(day,schema=2)['completions'])==1
    assert len(migrated(day)['views'][0]['items'])==64


def test_retained_research_reconnect_association_is_deduplicated(tmp_path):
    from types import SimpleNamespace
    day=DayStore(root=tmp_path/'sessions');w.save(day,migrated(day),0)
    origin={'session':day.sid,'tab':'main','placementId':'pending','run':'r'};w.admit(day,origin)
    researcher=SimpleNamespace(origin={'workspace':origin},result={'findingRevision':{'version':2}},rid='r1',label='Fixture research',brief='Fixture')
    w.reconcile_research(day,[researcher]);w.reconcile_research(day,[researcher])
    rows=w.read(day,schema=2)['completions'];assert len(rows)==1
    assert rows[0]['item']['resource']['version']==2


def test_successful_computation_does_not_repeat_fallible_origin_write(monkeypatch):
    from types import SimpleNamespace
    from orchestrator.operations import Runner
    runner=Runner.__new__(Runner);rows=[];runner.log=rows.append
    ticket=SimpleNamespace(id='request',workspace_origin={'run':'r'},workspace_store=lambda:None)
    monkeypatch.setattr(w,'link_execution',lambda *a: (_ for _ in ()).throw(OSError('storage unavailable after completion')))
    runner._use(SimpleNamespace(id='execution',operation='cells'),ticket,False)
    assert rows[0]['execution']=='execution'


def scientific_layout(day):
    import json
    from pathlib import Path
    state = w.read(day, schema=3)['layout']
    state['schema'] = 3
    settings = json.loads((Path(__file__).resolve().parents[2] / 'contract/scientific-view-preferences-cases.json').read_text())['valid']
    state['views'][0]['items'] = [{'resource': {'day': day.sid, 'id': 'p'+'a'*32, 'version': 2, 'picture': None},
        'placementId': 'scientific-window', 'title': 'Saved scientific data', 'zoom': 1, 'pan': [.5,.5],
        'display': {'scientific': settings}}]
    return state


def test_scientific_preferences_upgrade_only_on_successful_cas_and_survive_reopen(tmp_path):
    day = DayStore(root=tmp_path/'sessions')
    original = w.save(day, migrated(day), 0)
    candidate = scientific_layout(day)
    with pytest.raises(w.WorkspaceError, match='changed'):
        w.save(day, candidate, 0)
    assert w.read(day, schema=2) == original
    accepted = w.save(day, candidate, original['revision'])
    assert day.workspace_layout['migration'] == {'from': 2}
    reopened = DayStore(root=day.root, day=day.sid)
    assert w.read(reopened, schema=3) == accepted
    for schema in (1, 2):
        with pytest.raises(w.WorkspaceError, match='Reload'):
            w.read(reopened, schema=schema)
    with pytest.raises(w.WorkspaceError, match='Reload'):
        w.save(reopened, original['layout'], accepted['revision'])
    w.arrangement(reopened, 'remove', 'main', accepted['revision'])
    w.arrangement(reopened, 'restore', 'main', accepted['revision']+1)
    assert w.read(reopened, schema=3)['layout'] == candidate


@pytest.mark.parametrize('change', ['schema', 'resource', 'version', 'picture', 'presentation', 'settings'])
def test_scientific_preferences_reject_inexact_or_invalid_placement_without_writes(tmp_path, change):
    day = DayStore(root=tmp_path/'sessions')
    before = w.read(day, schema=3)
    state = scientific_layout(day)
    item = state['views'][0]['items'][0]
    if change == 'schema': state['schema'] = 2
    if change == 'resource': item['resource']['id'] = 'image-one'
    if change == 'version': item['resource']['version'] = None
    if change == 'picture': item['resource']['picture'] = 0
    if change == 'presentation': item['presentation'] = 'masks'
    if change == 'settings': item['display']['scientific']['layers'][0]['opacity'] = 2
    with pytest.raises(ValueError): w.save(day, state, before['revision'])
    assert w.read(day, schema=3) == before


@pytest.mark.parametrize('action', [None, 'remove', 'restore', 'dismiss', 'save'])
def test_old_client_cannot_mutate_new_layout_even_through_non_save_actions(tmp_path, action):
    import asyncio
    from types import SimpleNamespace
    from orchestrator.commands import workspace_layout
    day = DayStore(root=tmp_path/'sessions')
    state = scientific_layout(day)
    before = w.save(day, state, 0)
    message = {'session': day.sid, 'schema': 2, 'base': before['revision']}
    if action == 'save':
        old = copy.deepcopy(state); old['schema'] = 2; old['views'][0]['items'] = []
        message['layout'] = old
    elif action is not None: message.update(action=action, tab='main', run='pending-one')
    reply = asyncio.run(workspace_layout(SimpleNamespace(day=day), None, message))
    assert reply['ok'] is False and reply['code'] == 'unsupported-schema'
    assert w.read(day, schema=3) == before


def test_one_execution_places_one_window_its_masks(tmp_path):
    """S5b: a run that saved masks and an answer places the masks only; the answer stays in Data. An answer that keeps
    a table is a window of its own, and an answer of an execution with no masks is still placed."""
    import json
    day = DayStore(root=tmp_path/'sessions')
    w.save(day, migrated(day), 0)
    for run in ('masked', 'table', 'plain'):
        origin = {'session': day.sid, 'tab': 'main', 'placementId': f'pending-{run}', 'run': run}
        w.admit(day, origin)
        w.link_execution(day, origin, f'x-{run}')
    for run in ('masked', 'table'):
        folder = tmp_path / f'evidence-{run}'
        folder.mkdir()
        (folder / 'manifest.json').write_text(json.dumps({'executions': [f'x-{run}'], 'title': 'Masks', 'version': 1}))
        day._record({'type': 'evidence-saved', 'id': f'e-{run}', 'path': str(folder)})
    table = {'producer': {'sha256': 'a' * 64}, 'columns': ['n'], 'rows': [[1]]}
    day._record({'type': 'result', 'id': 'r-masked', 'title': 'Answer', 'executions': ['x-masked'], 'pictures': [], 'state': 'done'})
    day._record({'type': 'result', 'id': 'r-table', 'title': 'Table', 'executions': ['x-table'], 'pictures': [],
                 'state': 'done', 'values': table})
    day._record({'type': 'result', 'id': 'r-plain', 'title': 'Answer', 'executions': ['x-plain'], 'pictures': [], 'state': 'done'})
    restored = DayStore(root=day.root, day=day.sid)
    w.reconcile(restored)
    w.reconcile(restored)
    items = migrated(restored)['views'][0]['items']
    assert sorted((item['resource']['id'], item.get('presentation')) for item in items) == [
        ('e-masked', 'masks'), ('e-table', 'masks'), ('r-plain', None), ('r-table', 'table')]
