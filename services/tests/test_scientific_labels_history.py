"""Synthetic native failures for Labels transactions, not native qualification."""
from collections import deque
from types import SimpleNamespace
import sys
import threading

import numpy as np
import pytest

from env_worker.scientific_labels import LabelsEditor, Refused, RollbackFailed, history_identity, history_copy, same_history
from tests.scientific_threads import exclude_preexisting_test_harness_threads  # noqa: F401
from tests.waiting import HANG_S


class NativeLabels:
    _type_string = 'labels'
    visible = True
    ndim = 2
    axis_labels = ('y', 'x')
    units = ('um', 'um')
    scale = (1, 1)
    _slice_input = SimpleNamespace(ndisplay=2, order=(0, 1))
    colormap = SimpleNamespace(background_value=0)

    def __init__(self):
        self.data = np.zeros((8, 8), dtype=np.uint64)
        self.brush_size, self.n_edit_dimensions = 1, 2
        self.preserve_labels, self.contiguous = False, True
        self.selected_label, self._prev_selected_label = 1, 0
        self._reset_history()

    def _reset_history(self):
        self._undo_history, self._redo_history = deque(maxlen=20), deque(maxlen=20)
        self._staged_history, self._block_history = [], False

    def data_to_world(self, point):
        return point

    def _begin_stroke(self):
        self._block_history = True

    def paint(self, point, value, refresh=False):
        index = tuple(int(n) for n in point)
        self._staged_history.append((index, np.array(self.data[index]), value))
        self.data[index] = value

    def _commit_stroke(self):
        self._undo_history.append(self._staged_history)
        self._redo_history.clear()
        self._staged_history, self._block_history = [], False

    def undo(self):
        atoms = self._undo_history.pop()
        for index, before, _after in reversed(atoms):
            self.data[index] = before
        self._redo_history.append(atoms)

    def redo(self):
        atoms = self._redo_history.pop()
        for index, _before, after in atoms:
            self.data[index] = after
        self._undo_history.append(atoms)

    def refresh(self):
        pass


@pytest.fixture
def editing(monkeypatch):
    monkeypatch.setitem(sys.modules, 'napari.layers.labels._labels_utils',
                        SimpleNamespace(interpolate_coordinates=lambda *_args: ()))
    layer = NativeLabels()
    accepted = layer.data.copy()
    registry = SimpleNamespace(input_for=lambda _layer: 'labels', inputs={'labels': SimpleNamespace(reference='accepted')},
                               capture=lambda target, _name, _reference: target.data.copy())
    editor = LabelsEditor(layer, registry)
    editor.apply('paint', points=[[2, 3]], label='18446744073709551615', brush=1, preserve=False, contiguous=True)
    return editor, layer, accepted


@pytest.mark.parametrize('which', ['undo', 'redo'])
@pytest.mark.parametrize('external', ['pixels', 'array', 'history'])
def test_labels_refuse_stale_undo_and_redo_without_overwriting_current_work(editing, which, external):
    editor, layer, accepted = editing
    if which == 'redo':
        editor.apply('undo')
    if external == 'pixels':
        layer.data[0, 0] = 29
    elif external == 'array':
        layer.data = layer.data.copy()
    else:
        layer._undo_history.append([((0, 0), np.array(0, dtype=np.uint64), 29)])
    before, identity = layer.data.copy(), history_identity(layer)
    with pytest.raises(Refused, match='explicitly reset'):
        editor.apply(which)
    np.testing.assert_array_equal(layer.data, before)
    assert history_identity(layer) == identity
    assert not accepted.any()


@pytest.mark.parametrize('which', ['undo', 'redo'])
def test_partial_native_history_failure_preserves_pixels_and_both_stacks(editing, which):
    editor, layer, accepted = editing
    if which == 'redo':
        editor.apply('undo')
    before = layer.data.copy()
    counts = (len(layer._undo_history), len(layer._redo_history), len(editor.undo), len(editor.redo))
    native = getattr(layer, which)

    def fail():
        native()
        raise ValueError('native callback failed after pop')
    setattr(layer, which, fail)
    with pytest.raises(ValueError, match='after pop'):
        editor.apply(which)
    np.testing.assert_array_equal(layer.data, before)
    assert (len(layer._undo_history), len(layer._redo_history), len(editor.undo), len(editor.redo)) == counts
    setattr(layer, which, native)
    assert editor.apply(which)['changed']
    assert not accepted.any()
    if which == 'redo':
        assert layer.data[2, 3] == np.iinfo(np.uint64).max
    else:
        np.testing.assert_array_equal(layer.data, accepted)


def test_mutated_native_atom_is_restored_after_callback_failure(editing):
    editor, layer, accepted = editing
    native = layer.undo

    def fail():
        layer._undo_history[-1][0][1][...] = 19
        native()
        raise ValueError('native callback mutated retained atom')
    layer.undo = fail
    with pytest.raises(ValueError, match='mutated retained atom'):
        editor.apply('undo')
    layer.undo = native
    assert editor.apply('undo')['changed']
    np.testing.assert_array_equal(layer.data, accepted)


def test_array_replacement_during_native_history_fails_closed(editing):
    editor, layer, accepted = editing

    def replace():
        layer.data = layer.data.copy()
    layer.undo = replace
    with pytest.raises(RollbackFailed, match='Stop this session and reopen accepted Data'):
        editor.apply('undo')
    assert not accepted.any()
    with pytest.raises(Refused, match='explicitly reset'):
        editor.apply('undo')


def test_history_mutation_during_rollback_refresh_fails_closed(editing):
    editor, layer, _ = editing

    def fail():
        raise ValueError('controlled undo failure')

    def corrupt_restoration():
        layer._undo_history[-1][0][1][...] = 19
    layer.undo, layer.refresh = fail, corrupt_restoration
    with pytest.raises(RollbackFailed, match='Stop this session and reopen accepted Data'):
        editor.apply('undo')


def test_labels_history_still_refuses_background_work_started_by_the_case(editing):
    editor, layer, accepted = editing
    before, history = layer.data.copy(), history_copy(layer)
    started, stop = threading.Event(), threading.Event()

    def background():
        started.set()
        stop.wait(HANG_S)

    worker = threading.Thread(target=background)
    worker.start()
    try:
        assert started.wait(HANG_S)
        with pytest.raises(ValueError, match='Stop background Python work'):
            editor.apply('undo')
        np.testing.assert_array_equal(layer.data, before)
        assert same_history(history_copy(layer), history)
        assert not accepted.any()
    finally:
        stop.set()
        worker.join(HANG_S)
        assert not worker.is_alive()
    assert editor.apply('undo')['changed']
    np.testing.assert_array_equal(layer.data, accepted)
