"""Points and Shapes editing on the Studio preview, done by napari itself.

The Studio sends one gesture's pointer and key events in frame pixels (the `annotate` change). Each is dispatched as
napari's own canvas dispatches a vispy event (napari/_vispy/canvas.py `_process_mouse_event`): a vispy MouseEvent at
the canvas position, wrapped in napari's NapariMouseEvent with napari's `canvas_to_world` position, the camera's up
direction and zoom, the displayed dims and the dims point; the cursor moves first; the viewer's mouse callbacks run,
then the active layer's. The Shift key runs napari's own Shapes binding (hold_to_lock_aspect_ratio) as napari's keymap
does. A button or shortcut runs napari's own key-binding action function on the layer.

napari's Points and Shapes keep no undo history, so Hooke keeps one transaction per completed gesture (a shape built
click by click is one transaction, from its first click to its finish): the layer's data and per-element properties
before and after. Undo and Redo restore them exactly.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass

MODES = {'points': ('pan_zoom', 'add', 'select'),
         'shapes': ('pan_zoom', 'select', 'direct', 'vertex_insert', 'vertex_remove', 'add_rectangle', 'add_ellipse',
                    'add_line', 'add_path', 'add_polyline', 'add_polygon', 'add_polygon_lasso')}
ACTIONS = {'points': ('activate_points_add_mode', 'activate_points_select_mode', 'activate_points_pan_zoom_mode', 'copy',
                      'paste', 'select_all_in_slice', 'select_append_all_in_slice', 'select_all_data',
                      'delete_selected_points'),
           'shapes': ('activate_add_rectangle_mode', 'activate_add_ellipse_mode', 'activate_add_line_mode',
                      'activate_add_path_mode', 'activate_add_polyline_mode', 'activate_add_polygon_mode',
                      'activate_add_polygon_lasso_mode', 'activate_direct_mode', 'activate_select_mode',
                      'activate_vertex_insert_mode', 'activate_vertex_remove_mode', 'activate_shapes_pan_zoom_mode',
                      'copy_selected_shapes', 'paste_shape', 'select_shapes_in_slice', 'delete_selected_shapes',
                      'move_shapes_selection_to_front', 'move_shapes_selection_to_back', 'finish_drawing_shape')}
KINDS = ('move', 'press', 'release', 'double', 'key')
MODIFIERS = ('Shift', 'Control', 'Alt', 'Meta')
MAX_EVENTS = 512
MAX_HISTORY = 64


def command(value, width, height):
    """A closed `annotate` value: {layer, mode, events}, {layer, action} or {layer, history}; ValueError otherwise."""
    if type(value) is not dict or set(value) not in ({'layer', 'mode', 'events'}, {'layer', 'action'}, {'layer', 'history'}):
        raise ValueError('An annotation names one Points or Shapes layer and a gesture, a napari action or Undo/Redo')
    if type(value['layer']) is not str or not 1 <= len(value['layer']) <= 64:
        raise ValueError('Choose a Points or Shapes layer')
    if 'history' in value:
        if value['history'] not in ('undo', 'redo'):
            raise ValueError('Choose Undo or Redo')
        return value
    if 'action' in value:
        if not any(value['action'] in names for names in ACTIONS.values()):
            raise ValueError("Choose one of napari's Points or Shapes actions")
        return value
    if not any(value['mode'] in modes for modes in MODES.values()) or value['mode'] == 'pan_zoom':
        raise ValueError('Choose a napari Points or Shapes editing mode')
    events = value['events']
    if type(events) is not list or not 1 <= len(events) <= MAX_EVENTS:
        raise ValueError(f'A gesture has 1 to {MAX_EVENTS} pointer and key events')
    for event in events:
        if type(event) is not dict or event.get('kind') not in KINDS:
            raise ValueError('Each event is a move, press, release, double click or key')
        if event['kind'] == 'key':
            if set(event) != {'kind', 'key', 'down'} or event['key'] != 'Shift' or type(event['down']) is not bool:
                raise ValueError('A key event is Shift pressed or released')
            continue
        if set(event) != {'kind', 'point', 'modifiers'}:
            raise ValueError('A pointer event has its frame point and modifiers')
        point, modifiers = event['point'], event['modifiers']
        if (type(point) is not list or len(point) != 2
                or any(type(n) not in (int, float) or not math.isfinite(n) for n in point)
                or not 0 <= point[0] < height or not 0 <= point[1] < width):
            raise ValueError('The pointer is outside the displayed scientific frame')
        if type(modifiers) is not list or len(set(modifiers)) != len(modifiers) or not set(modifiers) <= set(MODIFIERS):
            raise ValueError('Modifiers are Shift, Control, Alt or Meta')
    return value


def snapshot(layer):
    """The layer's editable content: data and every per-element property napari keeps beside it."""
    import numpy as np
    state = {'features': layer.features.copy(deep=True), 'selected': set(layer.selected_data)}
    if layer._type_string == 'points':
        state.update(data=np.array(layer.data, copy=True), size=np.array(layer.size, copy=True),
                     symbol=np.array(layer.symbol, copy=True), face_color=np.array(layer.face_color, copy=True),
                     border_color=np.array(layer.border_color, copy=True),
                     border_width=np.array(layer.border_width, copy=True), shown=np.array(layer.shown, copy=True))
    else:
        state.update(data=[np.array(shape, copy=True) for shape in layer.data], shape_type=list(layer.shape_type),
                     edge_width=list(layer.edge_width), edge_color=np.array(layer.edge_color, copy=True),
                     face_color=np.array(layer.face_color, copy=True), z_index=list(layer.z_index))
    return state


def same(a, b, *, selection=True):
    import numpy as np
    if (set(a) != set(b) or (selection and a['selected'] != b['selected'])
            or not a['features'].equals(b['features'])):
        return False
    for key in set(a) - {'features', 'selected'}:
        x, y = a[key], b[key]
        if key == 'data' and type(x) is list:
            if len(x) != len(y) or any(p.shape != q.shape or not np.array_equal(p, q) for p, q in zip(x, y)):
                return False
        elif not np.array_equal(np.asarray(x, dtype=object), np.asarray(y, dtype=object)):
            return False
    return True


def restore(layer, state):
    """Put back exactly a snapshot's content (napari's own setters; no events are suppressed)."""
    state = copy.deepcopy(state)  # setters must never lend retained history to mutable layer data
    layer.selected_data = set()
    if layer._type_string == 'points':
        layer.data = state['data']
        layer.size, layer.symbol = state['size'], state['symbol']
        layer.face_color, layer.border_color = state['face_color'], state['border_color']
        layer.border_width, layer.shown = state['border_width'], state['shown']
    else:
        layer.remove(list(range(len(layer.data))))
        if state['data']:
            layer.add(state['data'], shape_type=state['shape_type'], edge_width=state['edge_width'],
                      edge_color=state['edge_color'], face_color=state['face_color'], z_index=state['z_index'])
    layer.features = state['features'].copy(deep=True)
    layer.selected_data = set(state['selected'])


class RollbackFailed(RuntimeError):
    pass


def restore_checked(layer, state):
    restore(layer, state)
    if not same(snapshot(layer), state):
        raise RuntimeError('A native callback changed the restored annotation')


def rollback(layer, state):
    try:
        restore_checked(layer, state)
    except BaseException as error:
        raise RollbackFailed('The private edit could not be restored. Stop this session and reopen accepted Data.') from error


@dataclass
class Edit:
    before: dict
    after: dict
    stale: bool = False


class Annotations:
    """One review session's Points and Shapes editing: napari's dispatch and Hooke's per-gesture history."""

    def __init__(self, viewer):
        self.viewer = viewer
        self.history = {}  # layer identity -> {'undo': [Edit(...)], 'redo': [...], 'open': before|None}
        self._held = {}  # layer identity -> napari's running Shift key-binding generator

    def check(self, identity, layer, value):
        """Refuse before anything is applied (the change is then not applied at all)."""
        if layer._type_string not in MODES:
            raise ValueError('Choose a Points or Shapes layer')
        if not layer.visible or self.viewer.dims.ndisplay != 2 or self.viewer.grid.enabled:
            raise ValueError('Points and Shapes are edited in a single 2D view of a visible layer')
        if 'action' in value and value['action'] not in ACTIONS[layer._type_string]:
            raise ValueError(f"That napari action is not one of the {layer._type_string} layer's")
        if 'mode' in value and value['mode'] not in MODES[layer._type_string]:
            raise ValueError(f"That mode is not one of the {layer._type_string} layer's")
        if 'history' in value:
            book = self.history.get(identity) or {'undo': [], 'redo': []}
            if getattr(layer, '_is_creating', False):
                raise ValueError('Finish or cancel the shape being drawn first')
            if not book[value['history']]:
                raise ValueError(f"There is no {layer._type_string} edit to {value['history']}")
            edit = book[value['history']][-1]
            expected = edit.after if value['history'] == 'undo' else edit.before
            if edit.stale or not same(snapshot(layer), expected):
                raise ValueError('Another tool changed this annotation. Keep the current work; its earlier Undo/Redo is no longer applicable.')

    def apply(self, identity, layer, value, size):
        self.check(identity, layer, value)
        book = self.history.setdefault(identity, {'undo': [], 'redo': [], 'open': None})
        if 'history' in value:
            return self._history(layer, book, value['history'])
        current = snapshot(layer)
        before = book['open'] if book['open'] is not None else current
        try:
            self.viewer.layers.selection.active = layer  # napari's canvas dispatches to the active layer
            if 'action' in value:
                self._action(layer, value['action'])
            else:
                if str(layer.mode) != value['mode']:
                    layer.mode = value['mode']
                self._dispatch(identity, layer, value['events'], size)
            creating = bool(getattr(layer, '_is_creating', False))
            after = snapshot(layer)
        except BaseException:
            rollback(layer, current)
            raise
        book['open'] = before if creating else None  # a shape still being drawn: its transaction stays open
        if (not creating and value.get('action', '').startswith('activate_')
                and same(before, after, selection=False)):
            self._mode_selection(book, before, after)
            return False
        if not creating and not same(before, after):
            book['undo'] = (book['undo'] + [Edit(before, after)])[-MAX_HISTORY:]
            book['redo'] = []
            return True
        return False

    @staticmethod
    def _mode_selection(book, before, after):
        """Tool choice is not an edit. Carry its selection only across still-current endpoints."""
        for name, endpoint in (('undo', 'after'), ('redo', 'before')):
            if not book[name]:
                continue
            edit = book[name][-1]
            expected = getattr(edit, endpoint)
            if same(expected, before):
                setattr(edit, endpoint, {**expected, 'selected': set(after['selected'])})
            else:
                # Clearing selection must not make a formerly stale endpoint applicable again.
                edit.stale = True

    def _history(self, layer, book, which):
        source, target = (book['undo'], book['redo']) if which == 'undo' else (book['redo'], book['undo'])
        if not source:
            raise ValueError(f'There is no {layer._type_string} edit to {which}')
        edit = source[-1]
        current = snapshot(layer)
        try:
            restore_checked(layer, edit.before if which == 'undo' else edit.after)
        except BaseException:
            rollback(layer, current)
            raise
        source.pop()  # history moves only after the complete native restoration succeeds
        target.append(edit)
        return True

    def _action(self, layer, name):
        if layer._type_string == 'points':
            from napari.layers.points import _points_key_bindings as bindings
        else:
            from napari.layers.shapes import _shapes_key_bindings as bindings
        getattr(bindings, name)(layer)

    def _dispatch(self, identity, layer, events, size):
        import numpy as np
        from napari._vispy.mouse_event import NapariMouseEvent
        from napari.utils._proxies import ReadOnlyWrapper
        from napari.utils.interactions import (mouse_double_click_callbacks, mouse_move_callbacks,
                                               mouse_press_callbacks, mouse_release_callbacks)
        from vispy.app.canvas import MouseEvent
        from vispy.util import keys
        callbacks = {'move': mouse_move_callbacks, 'press': mouse_press_callbacks,
                     'release': mouse_release_callbacks, 'double': mouse_double_click_callbacks}
        named = {'Shift': keys.SHIFT, 'Control': keys.CONTROL, 'Alt': keys.ALT, 'Meta': keys.META}
        viewer, (width, height) = self.viewer, size
        viewer.mouse_over_canvas = True
        press = last = None
        try:
            for event in events:
                if event['kind'] == 'key':
                    self._shift(identity, layer, event['down'])
                    continue
                y, x = event['point']
                kind = {'move': 'mouse_move', 'press': 'mouse_press', 'release': 'mouse_release',
                        'double': 'mouse_double_click'}[event['kind']]
                button = 1 if kind != 'mouse_move' or press is not None else None
                vispy = MouseEvent(type=kind, pos=(float(x), float(y)), button=button,
                                   buttons=[1] if press is not None or kind == 'mouse_press' else [],
                                   modifiers=tuple(named[m] for m in event['modifiers']),
                                   press_event=press if kind in ('mouse_move', 'mouse_release') else None, last_event=last)
                position = viewer.canvas_to_world((float(y), float(x)))
                napari_event = NapariMouseEvent(
                    event=vispy, view_direction=None,
                    up_direction=viewer.scene.camera.calculate_nd_up_direction(viewer.dims.ndim, viewer.dims.displayed),
                    camera_zoom=viewer.scene.camera.zoom, position=position, dims_displayed=list(viewer.dims.displayed),
                    dims_point=list(viewer.dims.point), viewbox=(0, 0))
                viewer.cursor._view_direction = None
                viewer.cursor.position = napari_event.position
                wrapped = ReadOnlyWrapper(napari_event, exceptions=('handled',))
                callbacks[event['kind']](viewer, wrapped)
                callbacks[event['kind']](layer, wrapped)
                last = vispy
                press = vispy if kind == 'mouse_press' else (None if kind == 'mouse_release' else press)
        finally:
            if identity in self._held:  # failures must release napari's Shift binding too
                self._shift(identity, layer, False)



    def _shift(self, identity, layer, down):
        if layer._type_string != 'shapes':
            return  # napari binds Shift alone only for Shapes (hold_to_lock_aspect_ratio)
        from napari.layers.shapes._shapes_key_bindings import hold_to_lock_aspect_ratio
        if down and identity not in self._held:
            generator = hold_to_lock_aspect_ratio(layer)
            next(generator)
            self._held[identity] = generator
        elif not down and identity in self._held:
            generator = self._held.pop(identity)
            try:
                next(generator)
            except StopIteration:
                pass

    def forget(self, identity):
        self.history.pop(identity, None)
        self._held.pop(identity, None)


def summary(layer, book):
    """What the Studio shows of a Points or Shapes layer's editing: napari's mode and selection, Hooke's history."""
    return {'mode': str(layer.mode), 'selected': len(layer.selected_data),
            'creating': bool(getattr(layer, '_is_creating', False)),
            'undo': len(book['undo']) if book else 0, 'redo': len(book['redo']) if book else 0}


def check_summary(value):
    if (type(value) is not dict or set(value) != {'mode', 'selected', 'creating', 'undo', 'redo'}
            or value['mode'] not in (*MODES['shapes'], *MODES['points'], 'transform')
            or any(type(value[k]) is not int or not 0 <= value[k] <= 1 << 30 for k in ('selected', 'undo', 'redo'))
            or type(value['creating']) is not bool):
        raise ValueError('The annotation state is malformed')
    return copy.deepcopy(value)
