"""Real journal admission/replay with synthetic library references; no camera use."""
import copy
import pytest
from orchestrator.day import DayStore
from orchestrator import workspace_views as w


def inputs(day):
    layout = w.read(day, schema=4)['layout']
    layout['schema'] = 4
    layout['views'][0]['input'] = {'source': 'live', 'position': 0}
    recording = copy.deepcopy(layout['views'][0])
    recording.update(id='recording', title='Recording', input={'source': 'a7', 'position': 8.5})
    layout['views'].append(recording)
    return layout


def test_input_references_survive_restart_and_removed_tab_recovery(tmp_path):
    day = DayStore(root=tmp_path/'sessions')
    layout = inputs(day)
    w.save(day, layout, 0)
    removed = w.arrangement(day, 'remove', 'recording', 1)
    reopened = DayStore(root=day.root, day=day.sid)
    restored = w.arrangement(reopened, 'restore', 'recording', removed['revision'])
    assert restored['layout'] == layout
    assert w.read(DayStore(root=day.root, day=day.sid), schema=4)['layout'] == layout
    for schema in (1, 2, 3):
        with pytest.raises(w.WorkspaceError, match='newer layout'):
            w.read(reopened, schema=schema)


@pytest.mark.parametrize('value', [None, {'source': '../private', 'position': 0},
    {'source': 'a7', 'position': -1}, {'source': 'a7', 'position': True},
    {'source': 'live', 'position': 1}, {'source': 'a7', 'position': float('nan')},
    {'source': 'a7', 'position': 0, 'pixels': [1, 2]}])
def test_invalid_or_payload_input_reference_cannot_change_saved_work(tmp_path, value):
    day = DayStore(root=tmp_path/'sessions')
    layout = inputs(day)
    w.save(day, layout, 0)
    changed = copy.deepcopy(layout); changed['views'][1]['input'] = value
    with pytest.raises(ValueError, match='input reference'):
        w.save(day, changed, 1)
    assert w.read(day, schema=4)['layout'] == layout


def test_background_completion_keeps_its_original_tab_and_inputs(tmp_path):
    day = DayStore(root=tmp_path/'sessions')
    layout = inputs(day)
    w.save(day, layout, 0)
    origin = {'session': day.sid, 'tab': 'recording', 'placementId': 'pending', 'run': 'run'}
    w.admit(day, origin)
    item = {'resource': {'day': day.sid, 'id': 'e1', 'version': 2, 'picture': 0},
            'title': 'Result', 'presentation': 'masks', 'zoom': 1, 'pan': [.5, .5]}
    result = w.complete(day, origin, 'execution', item)
    assert result['state'] == 'placed'
    saved = w.read(day, schema=4)['layout']
    assert saved['views'][0] == layout['views'][0]
    assert saved['views'][1]['input'] == layout['views'][1]['input']
    assert saved['views'][1]['items'][0]['resource'] == item['resource']
