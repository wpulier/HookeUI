"""Exact media references survive journal restoration; no browser qualification."""
import copy

import pytest

from orchestrator.day import DayStore
from orchestrator import workspace_views as workspace


def layout(day):
    ref = {'origin': 'a' * 32, 'session': day.sid, 'document': 'p' + 'b' * 32,
           'revision': 1, 'digest': 'c' * 64}
    return {'schema': 7, 'coordinates': 'stage', 'views': [
        {'id': 'main', 'title': 'Main', 'specimen': {'visible': True, 'rect': None},
         'items': [], 'notes': [], 'input': {'source': 'a1', 'position': 3, 'retained': ref}}]}


def test_exact_media_reopens_and_older_clients_cannot_drop_reference(tmp_path):
    day = DayStore(root=tmp_path / 'sessions')
    saved = layout(day)
    workspace.save(day, saved, 0)
    reopened = DayStore(root=day.root, day=day.sid)
    assert workspace.read(reopened, schema=7)['layout'] == saved
    with pytest.raises(ValueError, match='newer layout'):
        workspace.read(reopened, schema=6)


@pytest.mark.parametrize('bad', ['foreign', 'boolean', 'unknown', 'live', 'old-format'])
def test_invalid_or_foreign_retained_inputs_refuse_without_writing(tmp_path, bad):
    day = DayStore(root=tmp_path / 'sessions')
    saved = layout(day)
    before = copy.deepcopy(day.workspace_layout)
    source = saved['views'][0]['input']
    if bad == 'foreign': source['retained']['session'] = 'elsewhere'
    elif bad == 'boolean': source['retained']['revision'] = True
    elif bad == 'unknown': source['retained']['path'] = '/mutable/original.png'
    elif bad == 'live': source.update(source='live', position=0)
    else: saved['schema'] = 6
    with pytest.raises(ValueError): workspace.save(day, saved, 0)
    assert day.workspace_layout == before
