"""The `labels-tool` change and the `labelsTool` frame row (env_worker/scientific_labels_tool.py): closed and bounded."""
import pytest

from env_worker import scientific_labels_tool as tool

STATE = {'mode': 'paint', 'selectedLabel': '18446744073709551615', 'background': '0', 'brushSize': 7, 'preserve': False,
         'contiguous': True, 'editDimensions': 2, 'showSelected': False}


def test_every_napari_labels_action_and_setting_is_admitted():
    for action in tool.ACTIONS:
        assert tool.command({'layer': 'a' * 32, 'action': action})['action'] == action
    settings = {'mode': 'erase', 'selectedLabel': '1152921504606846979', 'brushSize': 256, 'preserve': True,
                'contiguous': False, 'editDimensions': 8, 'showSelected': True}
    assert tool.command({'layer': 'a', 'settings': settings})['settings'] == settings
    assert tool.check(dict(STATE)) == STATE


@pytest.mark.parametrize('value', [
    {'layer': 'a', 'action': 'eval'}, {'layer': 'a', 'action': 'undo'}, {'layer': 'a'},
    {'layer': 'a', 'action': 'new_label', 'settings': {}}, {'layer': '', 'action': 'new_label'},
    {'layer': 'a', 'settings': {}}, {'layer': 'a', 'settings': {'brushSize': 0}},
    {'layer': 'a', 'settings': {'brushSize': 257}}, {'layer': 'a', 'settings': {'brushSize': 3.0}},
    {'layer': 'a', 'settings': {'selectedLabel': 7}}, {'layer': 'a', 'settings': {'selectedLabel': '-1'}},
    {'layer': 'a', 'settings': {'selectedLabel': '1' * 21}}, {'layer': 'a', 'settings': {'mode': 'transform'}},
    {'layer': 'a', 'settings': {'editDimensions': 1}}, {'layer': 'a', 'settings': {'preserve': 1}},
    {'layer': 'a', 'settings': {'opacity': 1}}, [], None])
def test_anything_else_is_refused(value):
    with pytest.raises(ValueError):
        tool.command(value)


@pytest.mark.parametrize('patch', [{'mode': 'draw'}, {'selectedLabel': 5}, {'brushSize': 0}, {'preserve': 'yes'},
                                   {'editDimensions': 0}, {'extra': 1}])
def test_a_malformed_frame_row_is_refused(patch):
    with pytest.raises(ValueError):
        tool.check({**STATE, **patch})


class _Layer:
    _type_string, ndim = 'labels', 3

    def __init__(self, dtype):
        import numpy as np
        self.data = np.zeros((2, 3, 4), dtype)


@pytest.mark.parametrize('settings', [{'editDimensions': 4}, {'selectedLabel': '256'}])
def test_what_this_layer_cannot_take_is_refused_before_anything_applies(settings):
    """The layer's own limits are checked in preparation (admit), so the worker reports a refusal, not a failure."""
    with pytest.raises(ValueError):
        tool.admit(_Layer('uint8'), {'layer': 'a' * 32, 'settings': settings})
    tool.admit(_Layer('uint16'), {'layer': 'a' * 32, 'settings': {'editDimensions': 3, 'selectedLabel': '256'}})
