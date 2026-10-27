"""Synthetic native-boundary failures: history must never overwrite unrelated work.

These small layer/binding stand-ins exercise Hooke's transaction owner. They are
not native napari or UI qualification; the preserved worker/browser cases do that.
"""
import copy
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from env_worker.scientific_annotate import Annotations, same, snapshot


class Features:
    def __init__(self):
        self.values = [17]

    def copy(self, *, deep):
        assert deep
        return copy.deepcopy(self)

    def equals(self, other):
        return self.values == other.values


class Points:
    _type_string = 'points'
    visible = True
    mode = 'select'

    def __init__(self):
        self._data = np.array([[1., 2.]])
        self.features = Features()
        self.selected_data = {0}
        self.size = np.array([10.])
        self.symbol = np.array(['o'])
        self.face_color = np.array([[1., 0., 0., 1.]])
        self.border_color = np.array([[0., 0., 0., 1.]])
        self.border_width = np.array([1.])
        self.shown = np.array([True])
        self.fail_writes = 0

    @property
    def data(self):
        return self._data

    @data.setter
    def data(self, value):
        self._data = value
        if self.fail_writes:
            self.fail_writes -= 1
            raise RuntimeError('native data callback failed after mutation')


@pytest.fixture
def editing(monkeypatch):
    def paste(layer):
        layer.data = layer.data + 3
    bindings = SimpleNamespace(paste=paste)
    monkeypatch.setitem(sys.modules, 'napari.layers.points', SimpleNamespace(_points_key_bindings=bindings))
    viewer = SimpleNamespace(dims=SimpleNamespace(ndisplay=2), grid=SimpleNamespace(enabled=False),
                             layers=SimpleNamespace(selection=SimpleNamespace(active=None)))
    layer = Points()
    editor = Annotations(viewer)
    return editor, layer, bindings


def action(editor, layer):
    return editor.apply('layer', layer, {'action': 'paste'}, (100, 100))


def history(editor, layer, which):
    return editor.apply('layer', layer, {'history': which}, (100, 100))


@pytest.mark.parametrize('which', ['undo', 'redo'])
@pytest.mark.parametrize('change', ['pixels', 'features', 'style'])
def test_history_refuses_external_changes_without_overwriting_them(editing, which, change):
    editor, layer, _ = editing
    action(editor, layer)
    if which == 'redo':
        history(editor, layer, 'undo')
    if change == 'pixels':
        layer.data[0, 0] = 99
    elif change == 'features':
        layer.features.values[0] = 99
    else:
        layer.face_color[0, 0] = .5
    external = snapshot(layer)
    book = copy.deepcopy(editor.history)
    with pytest.raises(ValueError, match='changed'):
        history(editor, layer, which)
    assert same(snapshot(layer), external)
    assert len(editor.history['layer']['undo']) == len(book['layer']['undo'])
    assert len(editor.history['layer']['redo']) == len(book['layer']['redo'])


@pytest.mark.parametrize('which', ['undo', 'redo'])
def test_failed_history_restore_rolls_back_content_and_keeps_the_history_entry(editing, which):
    editor, layer, _ = editing
    initial = snapshot(layer)
    action(editor, layer)
    edited = snapshot(layer)
    if which == 'redo':
        history(editor, layer, 'undo')
    before = snapshot(layer)
    counts = [len(editor.history['layer'][key]) for key in ('undo', 'redo')]
    layer.fail_writes = 1
    with pytest.raises(RuntimeError, match='native data callback'):
        history(editor, layer, which)
    assert same(snapshot(layer), before)
    assert [len(editor.history['layer'][key]) for key in ('undo', 'redo')] == counts
    assert history(editor, layer, which)
    assert same(snapshot(layer), initial if which == 'undo' else edited)


def test_failed_native_action_restores_content_and_preserves_redo(editing):
    editor, layer, bindings = editing
    action(editor, layer)
    edited = snapshot(layer)
    history(editor, layer, 'undo')
    before = snapshot(layer)

    def fail(target):
        target.data[0, 0] = 99
        target.features.values[0] = 45
        raise RuntimeError('native action failed')
    bindings.paste = fail
    with pytest.raises(RuntimeError, match='native action failed'):
        action(editor, layer)
    assert same(snapshot(layer), before)
    assert not editor.history['layer']['undo']
    assert len(editor.history['layer']['redo']) == 1
    assert history(editor, layer, 'redo')
    assert same(snapshot(layer), edited)


def test_unrestorable_history_failure_requires_stopping_the_private_session(editing):
    editor, layer, _ = editing
    action(editor, layer)
    layer.fail_writes = 2
    with pytest.raises(RuntimeError, match='Stop this session and reopen accepted Data'):
        history(editor, layer, 'undo')
    assert len(editor.history['layer']['undo']) == 1
    assert not editor.history['layer']['redo']


def test_successful_roundtrip_preserves_data_features_styles_and_selection(editing):
    editor, layer, bindings = editing
    before = snapshot(layer)

    def native_edit(target):
        target.data = target.data + 3
        target.features.values[0] = 45
        target.face_color[0, 0] = .5
        target.selected_data = set()
    bindings.paste = native_edit
    assert action(editor, layer)
    after = snapshot(layer)
    assert history(editor, layer, 'undo') and same(snapshot(layer), before)
    assert history(editor, layer, 'redo') and same(snapshot(layer), after)


def choose_mode(editor, layer, bindings):
    def select(target):
        target.mode = 'add'
        target.selected_data = set()
    bindings.activate_points_add_mode = select
    return editor.apply('layer', layer, {'action': 'activate_points_add_mode'}, (100, 100))


def test_mode_selection_is_not_an_extra_edit_between_two_native_edits(editing):
    editor, layer, bindings = editing
    original = snapshot(layer)
    action(editor, layer)
    first = snapshot(layer)
    assert not choose_mode(editor, layer, bindings)
    assert len(editor.history['layer']['undo']) == 1
    action(editor, layer)
    second = snapshot(layer)
    assert len(editor.history['layer']['undo']) == 2
    history(editor, layer, 'undo')
    assert np.array_equal(layer.data, first['data'])
    history(editor, layer, 'undo')
    assert same(snapshot(layer), original)
    history(editor, layer, 'redo')
    history(editor, layer, 'redo')
    assert same(snapshot(layer), second)


def test_mode_selection_preserves_both_adjacent_history_endpoints(editing):
    editor, layer, bindings = editing
    action(editor, layer)
    action(editor, layer)
    final = snapshot(layer)
    history(editor, layer, 'undo')
    assert not choose_mode(editor, layer, bindings)
    book = editor.history['layer']
    assert (len(book['undo']), len(book['redo'])) == (1, 1)
    history(editor, layer, 'undo')
    history(editor, layer, 'redo')
    history(editor, layer, 'redo')
    assert same(snapshot(layer), final)


@pytest.mark.parametrize('change', ['pixels', 'features', 'style', 'selection'])
def test_mode_selection_cannot_reauthorize_stale_history(editing, change):
    editor, layer, bindings = editing
    action(editor, layer)
    if change == 'pixels':
        layer.data[0, 0] = 99
    elif change == 'features':
        layer.features.values[0] = 99
    elif change == 'style':
        layer.face_color[0, 0] = .5
    else:
        layer.selected_data = {1}
    book = copy.deepcopy(editor.history['layer'])
    choose_mode(editor, layer, bindings)
    current = snapshot(layer)
    with pytest.raises(ValueError, match='changed'):
        history(editor, layer, 'undo')
    assert same(snapshot(layer), current)
    assert len(editor.history['layer']['undo']) == len(book['undo'])
    assert same(editor.history['layer']['undo'][-1].after, book['undo'][-1].after)


def test_mode_action_that_changes_native_content_remains_undoable(editing):
    editor, layer, bindings = editing
    before = snapshot(layer)
    def finish_drawing(target):
        target.data += 3
        target.selected_data = set()
    bindings.activate_points_add_mode = finish_drawing
    assert editor.apply('layer', layer, {'action': 'activate_points_add_mode'}, (100, 100))
    history(editor, layer, 'undo')
    assert same(snapshot(layer), before)


@pytest.mark.parametrize('which', ['undo', 'redo'])
def test_mode_normalization_cannot_hide_an_external_selection_change(editing, which):
    editor, layer, bindings = editing
    layer.selected_data = set()
    action(editor, layer)
    if which == 'redo':
        history(editor, layer, 'undo')
    layer.selected_data = {0}
    choose_mode(editor, layer, bindings)
    assert layer.selected_data == set()
    current = snapshot(layer)
    with pytest.raises(ValueError, match='changed'):
        history(editor, layer, which)
    assert same(snapshot(layer), current)
    # A subsequent valid edit can still be undone, but cannot revive the stale entry.
    action(editor, layer)
    history(editor, layer, 'undo')
    if which == 'undo':
        with pytest.raises(ValueError, match='changed'):
            history(editor, layer, 'undo')


def test_mode_cancelling_provisional_geometry_preserves_prior_history(editing):
    editor, layer, bindings = editing
    original = snapshot(layer)
    action(editor, layer)
    accepted = snapshot(layer)
    def start_provisional(target):
        target.data = target.data + 3
        target._is_creating = True
    bindings.paste = start_provisional
    assert not action(editor, layer)
    def cancel_provisional(target):
        target.data = accepted['data'].copy()
        target.selected_data = set()
        target._is_creating = False
    bindings.activate_points_add_mode = cancel_provisional
    assert not editor.apply('layer', layer, {'action': 'activate_points_add_mode'}, (100, 100))
    assert len(editor.history['layer']['undo']) == 1
    assert editor.history['layer']['open'] is None
    history(editor, layer, 'undo')
    assert same(snapshot(layer), original)
    history(editor, layer, 'redo')
    assert np.array_equal(layer.data, accepted['data'])
    assert layer.selected_data == set()
