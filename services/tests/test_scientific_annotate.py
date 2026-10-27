"""The `annotate` change (env_worker/scientific_annotate.py): closed and bounded before napari sees anything."""
import pytest

from env_worker import scientific_annotate as annotate

GESTURE = {'layer': 'a' * 32, 'mode': 'add_rectangle', 'events': [
    {'kind': 'key', 'key': 'Shift', 'down': True}, {'kind': 'move', 'point': [10.5, 20.0], 'modifiers': ['Shift']},
    {'kind': 'press', 'point': [10.5, 20.0], 'modifiers': ['Shift']}, {'kind': 'release', 'point': [30, 40], 'modifiers': []},
    {'kind': 'double', 'point': [30, 40], 'modifiers': []}, {'kind': 'key', 'key': 'Shift', 'down': False}]}


def test_gestures_actions_and_history_are_admitted():
    assert annotate.command(GESTURE, 300, 200) == GESTURE
    for names in annotate.ACTIONS.values():
        for action in names:
            assert annotate.command({'layer': 'a', 'action': action}, 300, 200)['action'] == action
    for which in ('undo', 'redo'):
        assert annotate.command({'layer': 'a', 'history': which}, 300, 200)['history'] == which


def _with(**patch):
    return {**GESTURE, **patch}


def _event(**patch):
    return _with(events=[{**GESTURE['events'][1], **patch}])


@pytest.mark.parametrize('value', [
    _with(mode='pan_zoom'), _with(mode='transform'), _with(mode='paint'), _with(events=[]),
    _with(events=[GESTURE['events'][1]] * (annotate.MAX_EVENTS + 1)), _event(kind='wheel'), _event(point=[200, 10]),
    _event(point=[10, 300]), _event(point=[-1, 10]), _event(point=[float('nan'), 1]), _event(point=[1]),
    _event(modifiers=['Hyper']), _event(modifiers=['Shift', 'Shift']), _event(extra=1),
    _with(events=[{'kind': 'key', 'key': 'Control', 'down': True}]), _with(events=[{'kind': 'key', 'key': 'Shift'}]),
    {'layer': 'a', 'action': 'eval'}, {'layer': 'a', 'action': 'undo'}, {'layer': 'a', 'history': 'reset'},
    {'layer': '', 'history': 'undo'}, {'layer': 'a'}, {'layer': 'a', 'action': 'copy', 'history': 'undo'}, None, []])
def test_anything_else_is_refused(value):
    with pytest.raises(ValueError):
        annotate.command(value, 300, 200)


@pytest.mark.parametrize('patch', [{'mode': 'draw'}, {'selected': -1}, {'creating': 1}, {'undo': 1.0}, {'extra': 1}])
def test_a_malformed_frame_row_is_refused(patch):
    with pytest.raises(ValueError):
        annotate.check_summary({'mode': 'select', 'selected': 0, 'creating': False, 'undo': 0, 'redo': 0, **patch})
