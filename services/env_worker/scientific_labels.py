"""Atomic, bounded manual edits using the pinned upstream Labels implementation.

This worker-local adapter does not authorize a screen gesture or publish data. Its
caller must admit the exact rendered frame, resolve every coordinate before mutation,
and retain the existing explicit Save boundary.
"""
from collections import deque
import copy
import hashlib
import math
import re

from .scientific_label_history import HISTORY_BYTES, ATOM_ALLOWANCE, retained_bytes, stroke_calls, paint_bound, polygon_region
from .scientific_layer_exports import geometry, quiescent

MAX_FILL_PIXELS = 1 << 20
MAX_NEW_HISTORY = 16 << 20


class Refused(ValueError):
    pass


class RollbackFailed(RuntimeError):
    pass


def digest(data):
    import numpy as np
    if type(data) is not np.ndarray:
        from .lazy_labels import LazyLabels
        if type(data) is LazyLabels:return data.fingerprint()
    return hashlib.sha256(np.ascontiguousarray(data).data).hexdigest()


def histories(layer):
    return layer._undo_history, layer._redo_history, layer._staged_history


def history_copy(layer):
    # Native atoms contain mutable index/value arrays. Retaining only their list
    # leaves the rollback copy writable by the very callback being rolled back.
    return copy.deepcopy((*histories(layer), layer._block_history))


def same_history(left, right):
    import numpy as np
    if type(left) is not type(right):
        return False
    if isinstance(left, np.ndarray):
        return left.dtype == right.dtype and left.shape == right.shape and np.array_equal(left, right)
    if isinstance(left, (list, tuple, deque)):
        return (not isinstance(left, deque) or left.maxlen == right.maxlen) and len(left) == len(right) and all(
            same_history(a, b) for a, b in zip(left, right))
    return bool(left == right)


def history_identity(layer):
    return tuple(tuple(id(atom) for atom in item) for item in layer._undo_history), tuple(
        tuple(id(atom) for atom in item) for item in layer._redo_history)


def label_value(value, data):
    import numpy as np
    if type(value) is not str or re.fullmatch(r'0|[1-9][0-9]{0,19}', value) is None or data.dtype.kind not in 'iu':
        raise Refused('Choose a nonnegative whole label within this array’s integer range')
    result = int(value)
    if result > np.iinfo(data.dtype).max:
        raise Refused('The selected label exceeds this array’s integer range')
    return result


def polygon_tool_points(clicks, displayed):
    """The vertices napari's own polygon tool hands to paint_polygon for these clicks (data coordinates).

    napari's canvas overlay (napari/_vispy/overlays/labels_polygon.py) moves each click to the centre of its pixel
    (floor(coordinate + 0.5) + 0.5 on the displayed axes), skips a click on the previous vertex, and keeps the pointer's
    own position plus 1e-3 as a trailing cursor vertex; Enter (complete_polygon) paints all of them. The Studio sends the
    clicks, and the pointer rests on the last one when the polygon is finished.
    """
    import numpy as np
    vertices = []
    for click in clicks:
        exact = np.array(click, dtype=float)
        exact[list(displayed)] += 0.5
        centre = exact.copy()
        centre[list(displayed)] = np.floor(centre[list(displayed)]) + 0.5
        previous = np.array(vertices[-2]) if len(vertices) > 1 else None
        if previous is None or np.linalg.norm(centre - previous) > 0:
            vertices = vertices[:-1] + [centre.tolist(), (exact + 1e-3).tolist()]
    return vertices


class LabelsEditor:
    def __init__(self, layer, editable, *, history=None):
        self.layer, self.editable = layer, editable
        self.history = history or (lambda: histories(layer))
        self.undo, self.redo = [], []
        self._data()
        self.expected = digest(layer.data)
        self.array = layer.data
        self.identity = history_identity(layer)
        self.invalid = bool(layer._undo_history or layer._redo_history or layer._staged_history or layer._block_history)

    def _data(self):
        layer = self.layer
        if layer._type_string != 'labels' or not layer.visible or layer._slice_input.ndisplay != 2:
            raise Refused('Choose a visible Labels layer in a single 2D view')
        name = self.editable.input_for(layer)
        if name is None:
            raise Refused('This layer has no declared editable scientific input')
        data = self.editable.capture(layer, name, self.editable.inputs[name].reference)
        if not layer.data.flags.writeable:
            raise Refused('This Labels array is read-only')
        return data

    def _check(self):
        layer = self.layer
        if layer.data is not self.array or digest(layer.data) != self.expected or history_identity(layer) != self.identity:
            self.invalid = True
        if self.invalid:
            raise Refused('Python or another tool changed this layer. Inspect it and explicitly reset manual edit history before editing.')
        if layer._block_history or layer._staged_history:
            raise Refused('Finish the existing native stroke before editing')
        retained_bytes(self.history(), maximum=HISTORY_BYTES)

    def reset(self):
        self._data()
        self.layer._reset_history()
        self.undo.clear(); self.redo.clear()
        self.array, self.expected, self.identity = self.layer.data, digest(self.layer.data), history_identity(self.layer)
        self.invalid = False

    def apply(self, action, *, points=(), label=None, brush=None, preserve=None, contiguous=None, dimensions=None):
        import numpy as np
        from napari.layers.labels._labels_utils import interpolate_coordinates
        if action not in ('paint', 'erase', 'fill', 'polygon', 'pick', 'undo', 'redo'):
            raise Refused('Choose a supported Labels action')
        if action in ('undo', 'redo'):
            if points or any(v is not None for v in (label, brush, preserve, contiguous, dimensions)):
                raise Refused('Undo and Redo do not accept brush settings')
        elif action == 'pick':
            if len(points) != 1 or any(v is not None for v in (label, brush, preserve, contiguous, dimensions)):
                raise Refused('Pick needs one current point and no brush settings')
        elif action == 'polygon':
            if (any(v is not None for v in (brush, contiguous, dimensions)) or type(preserve) is not bool
                    or type(points) is not list or not 3 <= len(points) <= 256):
                raise Refused('A polygon needs 3 to 256 corners and a preserve-label choice; it edits only the displayed plane')
        elif (type(brush) is not int or not 1 <= brush <= 256 or type(preserve) is not bool or type(contiguous) is not bool
              or type(points) is not list or not 1 <= len(points) <= 256 or action == 'fill' and len(points) != 1):
            raise Refused('Choose a brush from 1 to 256 and a bounded complete gesture')
        before = self._data()
        self._check()
        layer, data = self.layer, self.layer.data
        dimensions = 2 if dimensions is None else dimensions
        if type(dimensions) is not int or not 2 <= dimensions <= min(8, data.ndim):
            raise Refused('Choose editing dimensions between 2 and this Labels array’s dimensionality')
        native_label = label_value(label, data) if action in ('paint', 'erase', 'fill', 'polygon') else None
        for point in points:
            if (type(point) not in (tuple,list) or len(point) != data.ndim
                    or any(type(n) not in (int,float) or not math.isfinite(n) or not 0 <= round(n) < size for n,size in zip(point,data.shape))):
                raise Refused('The gesture is outside the selected Labels array; inspect its current plane')
        if action == 'polygon':
            region = polygon_region([[point[axis] for axis in layer._slice_input.order[-2:]] for point in points]) \
                + sum(data.shape[axis] for axis in layer._slice_input.order[-2:]) + 1  # its pixel-centred, cursor-ended form
            points = polygon_tool_points(points, layer._slice_input.order[-2:])
        hidden = set(range(data.ndim)) - set(layer._slice_input.order[-2:])
        if points and any(round(point[axis]) != round(points[0][axis]) for point in points for axis in hidden):
            raise Refused('A single gesture must stay in one displayed plane')
        if action == 'pick':  # napari's pick: the clicked value becomes the layer's selected label
            value = int(data[tuple(round(n) for n in points[0])])
            label_value(str(value), data)
            layer.selected_label = value
            return {'label': str(value), 'changed': False}
        if action in ('undo','redo') and not getattr(self,action):
            raise Refused(f'There is no manual edit to {action}')
        if action in ('paint','erase','fill','polygon'):
            if len(layer._undo_history) >= layer._undo_history.maxlen:
                raise Refused('Manual history is full. Save changes and start a new session before editing further.')
            if action == 'polygon':
                if region > MAX_FILL_PIXELS:
                    raise Refused('This polygon exceeds the bounded editor’s region allowance; draw a smaller polygon')
                estimate = region*(data.dtype.itemsize+1)+ATOM_ALLOWANCE
            elif action == 'fill':
                plane = math.prod(data.shape[axis] for axis in layer._slice_input.order[-dimensions:])
                if plane > MAX_FILL_PIXELS:
                    raise Refused('This fill exceeds the bounded editor’s region allowance; use fewer editing dimensions or a qualified regional editor')
                estimate = plane*(data.dtype.itemsize+1)+ATOM_ALLOWANCE
            else:
                estimate = paint_bound(layer, brush, stroke_calls(points, brush), dimensions)
            if estimate > MAX_NEW_HISTORY or retained_bytes(self.history(), maximum=HISTORY_BYTES)+estimate > HISTORY_BYTES:
                raise Refused('This edit exceeds its history allowance. Use a shorter stroke or smaller brush; saved Data is unchanged.')
        original_history = history_copy(layer)
        settings = {key: getattr(layer,key) for key in ('brush_size','n_edit_dimensions','preserve_labels','contiguous','selected_label','_prev_selected_label')}
        original_geometry = geometry(layer)
        previous_undo, previous_redo = list(self.undo), list(self.redo)
        before_digest = self.expected
        try:
            if action in ('undo','redo'):
                mark = getattr(self,action)[-1]
                getattr(layer,action)()
                expected = mark[0] if action == 'undo' else mark[1]
                if digest(data) != expected:
                    raise Refused('A callback changed native undo results; this edit was not applied')
                getattr(self,action).pop()
                getattr(self,'redo' if action == 'undo' else 'undo').append(mark)
            else:
                layer.n_edit_dimensions = dimensions
                layer.preserve_labels = preserve
                if action != 'polygon':
                    layer.brush_size, layer.contiguous = brush, contiguous
                layer.selected_label = native_label
                target = int(layer.colormap.background_value) if action == 'erase' else native_label
                label_value(str(target), data)
                layer._begin_stroke()
                if action == 'polygon':
                    layer.paint_polygon(points, target)
                elif action == 'fill':
                    layer.fill(points[0], target, refresh=False)
                else:
                    layer.paint(points[0], target, refresh=False)
                    for previous, point in zip(points,points[1:]):
                        for position in interpolate_coordinates(previous,point,brush):
                            layer.paint(position,target,refresh=False)
                layer._commit_stroke()
                layer.refresh()
                changed = digest(data) != before_digest
                if changed:
                    if len(layer._undo_history) != len(original_history[0])+1:
                        raise Refused('The native tool did not produce one undoable gesture')
                    self.undo.append((before_digest,digest(data))); self.redo.clear()
                elif history_identity(layer) != self.identity:
                    raise Refused('A callback changed native history without a matching edit')
            quiescent(data)
            if layer.data is not data or geometry(layer) != original_geometry:
                raise Refused('The layer or scientific coordinates changed during this edit')
            retained_bytes(self.history(), maximum=HISTORY_BYTES)
            self.expected, self.identity = digest(data), history_identity(layer)
            return {'label': str(layer.selected_label), 'changed': self.expected != before_digest}
        except BaseException as error:
            try:
                if layer.data is not data: raise RuntimeError('The target array was replaced')
                if type(data) is not np.ndarray:data.restore(before)
                else:data[...] = before
                layer._undo_history, layer._redo_history, layer._staged_history, layer._block_history = copy.deepcopy(original_history)
                for key,value in settings.items(): setattr(layer,key,value)
                self.undo, self.redo = previous_undo, previous_redo
                layer.refresh()
                if layer.data is not data or digest(data) != before_digest or geometry(layer) != original_geometry:
                    raise RuntimeError('Restored data or coordinates did not match')
                if not same_history((*histories(layer), layer._block_history), original_history):
                    raise RuntimeError('Restored native history did not match')
                self.expected, self.identity = before_digest, history_identity(layer)
            except BaseException as rollback:
                self.invalid = True
                raise RollbackFailed('The private edit could not be restored. Stop this session and reopen accepted Data.') from rollback
            raise error
