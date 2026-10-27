"""Real journal/layout admission with synthetic retained resource references."""
import copy
import pytest
from orchestrator.day import DayStore
from orchestrator import workspace_views as w


def running(day):
    state = w.read(day, schema=3)['layout']
    state['schema'] = 3
    w.save(day, state, 0)
    origin = {'session': day.sid, 'tab': 'main', 'placementId': 'pending', 'run': 'run'}
    w.admit(day, origin)
    return state, origin


def result(day, version=0):
    return {'resource': {'day': day.sid, 'id': 'e1', 'version': version, 'picture': 0},
            'title': 'Masks', 'presentation': 'masks', 'zoom': 1, 'pan': [.5, .5]}


def test_local_arrangement_retains_three_server_correction_completions(tmp_path):
    day = DayStore(root=tmp_path/'sessions')
    draft, origin = running(day)
    draft['views'][0]['title'] = 'My arrangement'
    for version in (1, 2, 3):
        w.complete(day, origin, 'execution', result(day, version))
    accepted = w.save(day, draft, 1)
    assert accepted['revision'] == 5
    assert accepted['layout']['views'][0]['title'] == 'My arrangement'
    assert [i['resource']['version'] for i in accepted['layout']['views'][0]['items']] == [1, 2, 3]
    assert len({i['placementId'] for i in accepted['layout']['views'][0]['items']}) == 3
    assert w.read(DayStore(root=day.root, day=day.sid), schema=3)['layout'] == accepted['layout']


def test_lost_merged_reply_retries_exact_request_without_duplicate_placements(tmp_path):
    day = DayStore(root=tmp_path/'sessions')
    draft, origin = running(day)
    draft['views'][0]['title'] = 'Local'
    w.complete(day, origin, 'execution', result(day))
    first = w.save(day, draft, 1)
    replayed = w.save(DayStore(root=day.root, day=day.sid), draft, 1)
    assert replayed['revision'] == first['revision']
    assert replayed['layout'] == first['layout']
    assert replayed['completionAdditions'] == first['completionAdditions']


@pytest.mark.parametrize('intervening', ['user-edit', 'remove-restore', 'gap', 'forged-title',
                                       'forged-placement', 'unknown-origin', 'expired-base'])
def test_server_addition_proof_never_accepts_user_edits_or_opaque_history(tmp_path, intervening):
    day = DayStore(root=tmp_path/'sessions')
    draft, origin = running(day)
    if intervening == 'user-edit':
        other = copy.deepcopy(draft); other['views'][0]['title'] = 'Other client'
        w.save(day, other, 1)
    elif intervening == 'remove-restore':
        w.arrangement(day, 'remove', 'main', 1)
        w.arrangement(day, 'restore', 'main', 2)
    row = w.complete(day, origin, 'execution', result(day))
    if intervening in ('gap', 'forged-title', 'forged-placement', 'unknown-origin'):
        events = day.journal.load()
        event = next(e for e in reversed(events) if e['type'] == 'workspace-completion')
        event = copy.deepcopy(event)
        event['revision'] += 2 if intervening == 'gap' else 1
        if intervening == 'forged-title': event['layout']['views'][0]['title'] = 'Forged'
        if intervening == 'forged-placement': event['completion']['item']['placementId'] = 'forged'
        if intervening == 'unknown-origin': event['completion']['origin']['run'] = 'unknown'
        day._record(event, strict=True)
    elif intervening == 'expired-base':
        for _ in range(65): day._record({'type': 'diagnostic'}, strict=True)
    before = w.read(day, schema=3)
    draft['views'][0]['title'] = 'Local'
    with pytest.raises(w.WorkspaceError) as error: w.save(day, draft, 1)
    assert error.value.code == 'conflict'
    assert w.read(day, schema=3) == before


@pytest.mark.parametrize('local_change', ['removed-tab', 'collision', 'moved-placement', 'schema', 'capacity'])
def test_incompatible_draft_keeps_server_completion_and_refuses_save(tmp_path, local_change):
    day = DayStore(root=tmp_path/'sessions')
    draft, origin = running(day)
    row = w.complete(day, origin, 'execution', result(day))
    if local_change == 'removed-tab': draft['views'] = []
    elif local_change == 'collision':
        draft['views'][0]['items'] = [{**row['item'], 'zoom': 2}]
    elif local_change == 'moved-placement':
        draft['views'].append({**copy.deepcopy(draft['views'][0]), 'id': 'other', 'items': [row['item']]})
    elif local_change == 'schema': draft['schema'] = 2
    else:
        draft['views'][0]['items'] = [{**result(day), 'placementId': f'local-{n}'} for n in range(64)]
    before = w.read(day, schema=3)
    with pytest.raises(w.WorkspaceError): w.save(day, draft, 1)
    assert w.read(day, schema=3) == before
