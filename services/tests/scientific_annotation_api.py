"""P4 (H07) API recipes: napari's Points and Shapes editing API run in Hooke's actual worker (the Studio's Python panel)
and, verbatim, in the plain pinned-napari reference, each with an independent expectation computed here.

A recipe is code run with `viewer`, `np`, `gesture` (napari's own mouse dispatch with napari's test event; see
tests/scientific_annotation_reference.py) and `actions` (napari's key-binding modules). It makes its own layer, does
one thing and sets `result` to plain JSON. Both sides run the same `PREAMBLE` + recipe, so a difference in `result` is
a difference in behaviour. `expect(result)` is the independent leg: what the recipe must give, from its definition.
"""
from __future__ import annotations

PREAMBLE = r'''
import numpy as np
from dataclasses import dataclass as _dataclass
from napari.utils._proxies import ReadOnlyWrapper as _RO
from napari.utils.interactions import mouse_move_callbacks as _mm, mouse_press_callbacks as _mp, mouse_release_callbacks as _mr
from napari.layers.points import _points_key_bindings as points_actions
from napari.layers.shapes import _shapes_key_bindings as shapes_actions

@_dataclass
class _MouseEvent:  # napari.utils._testsupport.MouseEvent's fields, plus the modifiers napari's callbacks read
    type: str
    position: tuple
    is_dragging: bool = False
    dims_displayed: tuple = (0, 1)
    dims_point: list = None
    view_direction: list = None
    pos: list = (0, 0)
    button: int = None
    handled: bool = False
    modifiers: tuple = ()

def full(world):
    """A layer's world position in the viewer's world: napari aligns layers on their trailing axes, and the leading
    axes are where the viewer is (Hooke's session also holds its bound 4D Labels layer)."""
    d = viewer.dims
    return tuple([float(v) for v in d.point[:d.ndim - len(world)]] + [float(v) for v in world])

def gesture(layer, worlds, *, button=1, modifiers=(), drag=True):
    d = viewer.dims
    worlds = [full(w) for w in worlds]
    # napari's canvas events carry dims_displayed as a list (QtViewer passes list(dims.displayed))
    common = {'dims_displayed': [int(n) for n in d.displayed], 'dims_point': [float(n) for n in d.point],
              'view_direction': None, 'button': button, 'modifiers': tuple(modifiers)}
    # `pos` is the canvas position napari's vispy event carries: the world position on screen, here scaled 1:1.
    canvas = lambda w: np.asarray([float(w[i]) for i in common['dims_displayed']][::-1])
    _mp(layer, _RO(_MouseEvent(type='mouse_press', position=tuple(worlds[0]), pos=canvas(worlds[0]), **common)))
    for world in worlds[1:]:
        viewer.cursor.position = tuple(world)
        _mm(layer, _RO(_MouseEvent(type='mouse_move', is_dragging=drag, position=tuple(world), pos=canvas(world), **common)))
    _mr(layer, _RO(_MouseEvent(type='mouse_release', position=tuple(worlds[-1]), pos=canvas(worlds[-1]), **common)))

def hover(layer, world):
    """The cursor moves over the canvas: napari's viewer updates the layer's value, then the move callbacks run."""
    d = viewer.dims
    viewer.mouse_over_canvas = True  # napari updates a layer's hovered value only while the pointer is over the canvas
    world = full(world)
    viewer.cursor.position = tuple(world)
    viewer.update_status_from_cursor()  # what napari's canvas runs on a pointer move: sets the hovered layer's value
    _mm(layer, _RO(_MouseEvent(type='mouse_move', is_dragging=False, position=tuple(world),
                               pos=np.asarray([world[i] for i in d.displayed][::-1]),
                               dims_displayed=[int(n) for n in d.displayed], dims_point=[float(n) for n in d.point])))

def rows(a):
    return [[float(v) for v in r] for r in np.asarray(getattr(a, 'data', a))]  # a napari Shape holds its vertices in .data

def fresh(kind, name, *args, **kwargs):
    if name in viewer.layers:
        viewer.layers.remove(name)
    return getattr(viewer, 'add_' + kind)(*args, name=name, **kwargs)
'''

POINTS = np_points = [[2.0, 3.0], [10.0, 4.0], [6.0, 12.0], [14.0, 14.0]]


def _points(name='P4 points', extra=''):
    return f"layer = fresh('points', {name!r}, np.array({POINTS!r}), size=3{extra})\n"


# recipe id -> (code, expect(result) -> None). Ids name the child they exercise (without the group prefix).
RECIPES = {
    'points-add': (_points() + """
layer.current_size = 7
layer.current_symbol = 'square'
layer.current_face_color = 'red'
layer.current_border_color = 'blue'
layer.current_border_width = 0.25
layer.add(np.array([[5.0, 6.0], [7.0, 8.0]]))
result = {'data': rows(layer.data), 'size': [float(s) for s in layer.size], 'symbol': [str(s) for s in layer.symbol],
          'face': rows(layer.face_color), 'border': rows(layer.border_color),
          'border_width': [float(w) for w in layer.border_width],
          'current': [float(layer.current_size), str(layer.current_symbol), layer.current_face_color,
                      layer.current_border_color, float(layer.current_border_width)]}
""", lambda r: (
        r['data'] == POINTS + [[5.0, 6.0], [7.0, 8.0]] and r['size'][-2:] == [7.0, 7.0] and r['size'][:4] == [3.0] * 4
        and r['symbol'][-2:] == ['square', 'square'] and r['face'][-1] == [1.0, 0.0, 0.0, 1.0]
        and r['border'][-1] == [0.0, 0.0, 1.0, 1.0] and r['border_width'][-2:] == [0.25, 0.25]
        and r['current'] == [7.0, 'square', 'red', 'blue', 0.25])),
    'points-current-properties': (_points(extra=", features={'kind': ['a', 'b', 'a', 'b']}") + """
layer.current_properties = {'kind': np.array(['z'])}
layer.add(np.array([[1.0, 1.0]]))
result = {'kinds': [str(k) for k in layer.features['kind']], 'current': {k: [str(x) for x in v] for k, v in layer.current_properties.items()}}
""", lambda r: r['kinds'] == ['a', 'b', 'a', 'b', 'z'] and r['current'] == {'kind': ['z']}),
    'points-remove': (_points() + """
layer.remove([0, 2])
result = {'data': rows(layer.data)}
""", lambda r: r['data'] == [POINTS[1], POINTS[3]]),
    'points-remove-selected': (_points() + """
layer.selected_data = {1, 3}
selected = sorted(int(i) for i in layer.selected_data)
layer.remove_selected()
result = {'selected': selected, 'data': rows(layer.data), 'after': sorted(int(i) for i in layer.selected_data)}
""", lambda r: r['selected'] == [1, 3] and r['data'] == [POINTS[0], POINTS[2]] and r['after'] == []),
    'points-selected-data': (_points() + """
layer.selected_data = {0, 2}
result = {'selected': sorted(int(i) for i in layer.selected_data)}
""", lambda r: r['selected'] == [0, 2]),
    'points-pop': (_points() + """
popped = layer.pop(1)
result = {'popped': [float(v) for v in popped['data']] if 'data' in popped else [float(v) for v in popped.get('coordinates', [])],
          'keys': sorted(popped), 'data': rows(layer.data)}
""", lambda r: r['popped'] == POINTS[1] and r['data'] == [POINTS[0], *POINTS[2:]]),
    'points-get-point-info': (_points() + """
info = layer.get_point_info(2)
result = {'keys': sorted(info), 'data': [float(v) for v in info.get('data', info.get('coordinates', []))],
          'size': float(np.asarray(info['size']).item()) if 'size' in info else None}
""", lambda r: r['data'] == POINTS[2] and r['size'] == 3.0),
    'points-interaction-box': (_points() + """
box = layer.interaction_box([0, 1])
result = {'box': None if box is None else rows(box)}
""", lambda r: r['box'] is not None and min(p[0] for p in r['box']) <= 2.0 and max(p[0] for p in r['box']) >= 10.0
        and min(p[1] for p in r['box']) <= 3.0 and max(p[1] for p in r['box']) >= 4.0),
    'points-mode': (_points() + """
seen = []
for mode in ('add', 'select', 'pan_zoom', 'transform'):
    layer.mode = mode
    seen.append(str(layer.mode))
result = {'modes': seen}
""", lambda r: r['modes'] == ['add', 'select', 'pan_zoom', 'transform']),
    'points-mode-add': (_points() + """
layer.mode = 'add'
gesture(layer, [layer.data_to_world([8.0, 9.0])])
result = {'mode': str(layer.mode), 'data': rows(layer.data)}
""", lambda r: r['mode'] == 'add' and r['data'] == POINTS + [[8.0, 9.0]]),
    'points-mode-select': (_points() + """
layer.mode = 'select'
gesture(layer, [layer.data_to_world(layer.data[2])])
result = {'mode': str(layer.mode), 'selected': sorted(int(i) for i in layer.selected_data), 'data': rows(layer.data)}
""", lambda r: r['mode'] == 'select' and r['selected'] == [2] and r['data'] == POINTS),
    'mouse-add': (_points() + """
layer.mode = 'add'
gesture(layer, [layer.data_to_world([3.0, 15.0])])
gesture(layer, [layer.data_to_world([12.0, 1.0])])
result = {'data': rows(layer.data)}
""", lambda r: r['data'] == POINTS + [[3.0, 15.0], [12.0, 1.0]]),
    'mouse-select': (_points() + """
layer.mode = 'select'
gesture(layer, [layer.data_to_world(layer.data[0])])
one = sorted(int(i) for i in layer.selected_data)
gesture(layer, [layer.data_to_world(layer.data[3])], modifiers=('Shift',))
two = sorted(int(i) for i in layer.selected_data)
gesture(layer, [layer.data_to_world([0.0, 0.0]), layer.data_to_world([11.0, 13.0])])
box = sorted(int(i) for i in layer.selected_data)
result = {'one': one, 'shift': two, 'box': box, 'data': rows(layer.data)}
""", lambda r: r['one'] == [0] and r['shift'] == [0, 3] and r['box'] == [0, 1, 2] and r['data'] == POINTS),
    'mouse-highlight': (_points() + """
layer.mode = 'select'
hover(layer, layer.data_to_world(layer.data[1]))
over = [int(i) for i in layer._highlight_index]
hover(layer, layer.data_to_world([30.0, 30.0]))
result = {'over': over, 'away': [int(i) for i in layer._highlight_index]}
""", lambda r: r['over'] == [1] and r['away'] == []),
    'activate-points-add-mode': (_points() + """
points_actions.activate_points_add_mode(layer)
result = {'mode': str(layer.mode)}
""", lambda r: r['mode'] == 'add'),
    'activate-points-select-mode': (_points() + """
points_actions.activate_points_select_mode(layer)
result = {'mode': str(layer.mode)}
""", lambda r: r['mode'] == 'select'),
    'select-all-data': (_points() + """
points_actions.select_all_data(layer)
result = {'selected': sorted(int(i) for i in layer.selected_data)}
""", lambda r: r['selected'] == [0, 1, 2, 3]),
    'delete-selected-points': (_points() + """
layer.selected_data = {0, 1}
points_actions.delete_selected_points(layer)
result = {'data': rows(layer.data)}
""", lambda r: r['data'] == POINTS[2:]),
    'copy': (_points() + """
layer.selected_data = {1}
points_actions.copy(layer)
clip = layer._clipboard
result = {'copied': rows(clip['data']) if clip else None, 'data': rows(layer.data)}
""", lambda r: r['copied'] == [POINTS[1]] and r['data'] == POINTS),
    'paste': (_points() + """
layer.selected_data = {1, 2}
points_actions.copy(layer)
points_actions.paste(layer)
result = {'data': rows(layer.data), 'selected': sorted(int(i) for i in layer.selected_data)}
""", lambda r: r['data'] == POINTS + [POINTS[1], POINTS[2]] and r['selected'] == [1, 2, 4, 5]),  # paste adds to the selection
}


# Points in a 3D layer with points on two z planes: "in slice" selects only the current plane's.
SLICED = r'''
layer = fresh('points', 'P4 sliced points', np.array([[0.0, 2.0, 3.0], [0.0, 5.0, 6.0], [1.0, 7.0, 8.0], [1.0, 9.0, 1.0]]), size=2)
viewer.dims.set_point(viewer.dims.ndim - 3, 0.0)  # the z plane of the first two points
'''
RECIPES.update({
    'select-all-in-slice': (SLICED + """
layer.selected_data = set()
points_actions.select_all_in_slice(layer)
first = sorted(int(i) for i in layer.selected_data)
points_actions.select_all_in_slice(layer)
second = sorted(int(i) for i in layer.selected_data)
result = {'first': first, 'second': second}
""", lambda r: r['first'] == [0, 1] and r['second'] == []),
    'select-append-all-in-slice': (SLICED + """
layer.selected_data = {2}
points_actions.select_append_all_in_slice(layer)
result = {'selected': sorted(int(i) for i in layer.selected_data)}
""", lambda r: r['selected'] == [0, 1, 2]),
})

RECIPES = {f'point-edit.{name}': recipe for name, recipe in RECIPES.items()}  # keyed by the child they exercise
POINT_RECIPES = dict(RECIPES)
RECIPES = {}

RECT = [[2.0, 3.0], [2.0, 11.0], [8.0, 11.0], [8.0, 3.0]]
TRI = [[12.0, 2.0], [12.0, 10.0], [18.0, 6.0]]
LINE = [[1.0, 14.0], [9.0, 18.0]]


def _shapes(name='P4 shapes'):
    """A rectangle, a triangle (polygon) and a line, with napari's defaults."""
    return (f"layer = fresh('shapes', {name!r}, [np.array({RECT!r}), np.array({TRI!r}), np.array({LINE!r})], "
            "shape_type=['rectangle', 'polygon', 'line'])\n")


def corners(shape):
    return sorted(map(tuple, shape))


def _box(p, q):
    (a, b), (c, d) = p, q
    return sorted([(min(a, c), min(b, d)), (min(a, c), max(b, d)), (max(a, c), max(b, d)), (max(a, c), min(b, d))])


SHAPE_OBSERVE = ("result = {'data': [rows(s) for s in layer.data], 'type': [str(t) for t in layer.shape_type], "
                 "'selected': sorted(int(i) for i in layer.selected_data), 'z': [int(z) for z in layer.z_index], "
                 "'mode': str(layer.mode)}\n")

RECIPES.update({
    'shapes-add': (_shapes() + """
layer.add([np.array([[20.0, 20.0], [26.0, 30.0]])], shape_type='ellipse', edge_width=2.5, edge_color='red', face_color='blue', z_index=4)
""" + SHAPE_OBSERVE + "result['edge'] = rows(layer.edge_color); result['face'] = rows(layer.face_color); result['width'] = [float(w) for w in layer.edge_width]\n",
        # two ellipse rows are its centre and radii (napari's documented form): the box is centre +/- radii
        lambda r: r['type'] == ['rectangle', 'polygon', 'line', 'ellipse'] and corners(r['data'][3]) == _box([-6, -10], [46, 50])
        and r['edge'][3] == [1.0, 0.0, 0.0, 1.0] and r['face'][3] == [0.0, 0.0, 1.0, 1.0] and r['width'][3] == 2.5
        and r['z'][3] == 4 and r['data'][:3] == [RECT, TRI, LINE]),
    'shapes-add-rectangles': (_shapes() + "layer.add_rectangles(np.array([[20.0, 21.0], [24.0, 29.0]]))\n" + SHAPE_OBSERVE,
        lambda r: r['type'][3] == 'rectangle' and corners(r['data'][3]) == _box([20, 21], [24, 29])),
    'shapes-add-ellipses': (_shapes() + "layer.add_ellipses(np.array([[20.0, 21.0], [24.0, 29.0]]))\n" + SHAPE_OBSERVE,
        lambda r: r['type'][3] == 'ellipse' and corners(r['data'][3]) == _box([-4, -8], [44, 50])),  # centre +/- radii
    'shapes-add-lines': (_shapes() + "layer.add_lines(np.array([[20.0, 21.0], [24.0, 29.0]]))\n" + SHAPE_OBSERVE,
        lambda r: r['type'][3] == 'line' and r['data'][3] == [[20.0, 21.0], [24.0, 29.0]]),
    'shapes-add-paths': (_shapes() + "layer.add_paths([np.array([[20.0, 21.0], [24.0, 29.0], [21.0, 33.0]])])\n" + SHAPE_OBSERVE,
        lambda r: r['type'][3] == 'path' and r['data'][3] == [[20.0, 21.0], [24.0, 29.0], [21.0, 33.0]]),
    'shapes-add-polygons': (_shapes() + "layer.add_polygons([np.array([[20.0, 21.0], [24.0, 29.0], [21.0, 33.0]])])\n" + SHAPE_OBSERVE,
        lambda r: r['type'][3] == 'polygon' and r['data'][3] == [[20.0, 21.0], [24.0, 29.0], [21.0, 33.0]]),
    'shapes-remove': (_shapes() + "layer.remove([0, 2])\n" + SHAPE_OBSERVE,
        lambda r: r['data'] == [TRI] and r['type'] == ['polygon']),
    'shapes-remove-selected': (_shapes() + "layer.selected_data = {1}\nlayer.remove_selected()\n" + SHAPE_OBSERVE,
        lambda r: r['data'] == [RECT, LINE] and r['selected'] == []),
    'shapes-pop': (_shapes() + "popped = layer.pop(1)\n" + SHAPE_OBSERVE +
        "result['popped'] = {'keys': sorted(popped), 'data': rows(popped['data']) if 'data' in popped else None, "
        "'type': str(popped.get('shape_type'))}\n",
        lambda r: r['popped']['data'] == TRI and r['popped']['type'] == 'polygon' and r['data'] == [RECT, LINE]),
    'shapes-get-shape-info': (_shapes() + "info = layer.get_shape_info(2)\n"
        "result = {'keys': sorted(info), 'data': rows(info['data']), 'type': str(info['shape_type'])}\n",
        lambda r: r['data'] == LINE and r['type'] == 'line'),
    'shapes-move-to-front': (_shapes() + "layer.selected_data = {0}\nlayer.move_to_front()\n" + SHAPE_OBSERVE,
        lambda r: r['z'][0] > max(r['z'][1:])),
    'shapes-move-to-back': (_shapes() + "layer.selected_data = {2}\nlayer.move_to_back()\n" + SHAPE_OBSERVE,
        lambda r: r['z'][2] < min(r['z'][:2])),
    'shapes-z-index': (_shapes() + "layer.z_index = [3, 1, 2]\n" + SHAPE_OBSERVE + "result['order'] = [int(i) for i in layer._data_view._z_order]\n",
        lambda r: r['z'] == [3, 1, 2] and r['order'] == [1, 2, 0]),  # drawn lowest z first
    'shapes-selected-data': (_shapes() + "layer.selected_data = {0, 2}\n" + SHAPE_OBSERVE,
        lambda r: r['selected'] == [0, 2]),
    'shapes-interaction-box': (_shapes() + "box = layer.interaction_box(0)\nresult = {'box': rows(box)}\n",
        lambda r: min(p[0] for p in r['box']) <= 2.0 and max(p[0] for p in r['box']) >= 8.0
        and min(p[1] for p in r['box']) <= 3.0 and max(p[1] for p in r['box']) >= 11.0),
    'shapes-get-index-and-intersection': ("""
layer = fresh('shapes', 'P4 3D shapes', [np.array([[1.0, 2.0, 3.0], [1.0, 2.0, 11.0], [1.0, 8.0, 11.0], [1.0, 8.0, 3.0]])], shape_type='rectangle')
viewer.dims.ndisplay = 3  # napari's ray pick is for a 3D view
inside = layer.get_index_and_intersection(np.array([0.0, 5.0, 7.0]), np.array([1.0, 0.0, 0.0]), [0, 1, 2])
outside = layer.get_index_and_intersection(np.array([0.0, 30.0, 30.0]), np.array([1.0, 0.0, 0.0]), [0, 1, 2])
result = {'inside': None if inside[0] is None else int(inside[0]),
          'point': None if inside[1] is None else [float(v) for v in inside[1]],
          'outside': None if outside[0] is None else int(outside[0])}
viewer.dims.ndisplay = 2
""", lambda r: r['inside'] == 0 and r['outside'] is None and [round(v, 6) for v in r['point']] == [1.0, 5.0, 7.0]),
    'shapes-current-edge-color': (_shapes() + "layer.current_edge_color = 'green'\nlayer.add_lines(np.array([[20.0, 21.0], [24.0, 29.0]]))\n"
        "result = {'edge': rows(layer.edge_color)[-1], 'current': layer.current_edge_color}\n",
        lambda r: [round(v, 3) for v in r['edge']] == [0.0, 0.502, 0.0, 1.0] and r['current'] == 'green'),  # CSS green #008000
    'shapes-current-edge-width': (_shapes() + "layer.current_edge_width = 3.5\nlayer.add_lines(np.array([[20.0, 21.0], [24.0, 29.0]]))\n"
        "result = {'width': [float(w) for w in layer.edge_width], 'current': float(layer.current_edge_width)}\n",
        lambda r: r['width'][-1] == 3.5 and r['current'] == 3.5),
    'shapes-current-face-color': (_shapes() + "layer.current_face_color = 'magenta'\nlayer.add_rectangles(np.array([[20.0, 21.0], [24.0, 29.0]]))\n"
        "result = {'face': rows(layer.face_color)[-1], 'current': layer.current_face_color}\n",
        lambda r: r['face'] == [1.0, 0.0, 1.0, 1.0] and r['current'] == 'magenta'),
    'shapes-current-properties': ("layer = fresh('shapes', 'P4 shapes', [np.array(%r)], shape_type='rectangle', features={'kind': ['cell']})\n" % RECT +
        "layer.current_properties = {'kind': np.array(['nucleus'])}\nlayer.add_rectangles(np.array([[20.0, 21.0], [24.0, 29.0]]))\n"
        "result = {'kinds': [str(k) for k in layer.features['kind']]}\n",
        lambda r: r['kinds'] == ['cell', 'nucleus']),
    'shapes-edge-color-mode': ("layer = fresh('shapes', 'P4 shapes', [np.array(%r), np.array(%r)], shape_type=['rectangle', 'polygon'], "
        "features={'kind': ['a', 'b']})\n" % (RECT, TRI) +
        "layer.edge_color = 'kind'\nlayer.edge_color_mode = 'cycle'\n"
        "result = {'mode': str(layer.edge_color_mode), 'edge': rows(layer.edge_color)}\n",
        lambda r: r['mode'] == 'cycle' and r['edge'][0] != r['edge'][1]),
    'shapes-face-color-mode': ("layer = fresh('shapes', 'P4 shapes', [np.array(%r), np.array(%r)], shape_type=['rectangle', 'polygon'], "
        "features={'kind': ['a', 'b']})\n" % (RECT, TRI) +
        "layer.face_color = 'kind'\nlayer.face_color_mode = 'cycle'\n"
        "result = {'mode': str(layer.face_color_mode), 'face': rows(layer.face_color)}\n",
        lambda r: r['mode'] == 'cycle' and r['face'][0] != r['face'][1]),
    'shapes-mode': (_shapes() + """
seen = []
for mode in ('select', 'direct', 'vertex_insert', 'vertex_remove', 'add_rectangle', 'add_ellipse', 'add_line', 'add_path',
             'add_polyline', 'add_polygon', 'add_polygon_lasso', 'pan_zoom'):
    layer.mode = mode
    seen.append(str(layer.mode))
result = {'modes': seen}
""", lambda r: r['modes'] == ['select', 'direct', 'vertex_insert', 'vertex_remove', 'add_rectangle', 'add_ellipse', 'add_line',
                             'add_path', 'add_polyline', 'add_polygon', 'add_polygon_lasso', 'pan_zoom']),
    'copy-selected-shapes': (_shapes() + "layer.mode = 'select'\nlayer.selected_data = {1}\nshapes_actions.copy_selected_shapes(layer)\n"
        "result = {'copied': [rows(s) for s in layer._clipboard['data']] if layer._clipboard else None}\n",
        lambda r: r['copied'] is not None and [corners(s) for s in r['copied']] == [corners(TRI)]),
    'paste-shape': (_shapes() + "layer.mode = 'select'\nlayer.selected_data = {1}\nshapes_actions.copy_selected_shapes(layer)\nshapes_actions.paste_shape(layer)\n" + SHAPE_OBSERVE,
        lambda r: len(r['data']) == 4 and corners(r['data'][3]) == corners(TRI) and r['type'][3] == 'polygon' and 3 in r['selected']),
    'delete-selected-shapes': (_shapes() + "layer.selected_data = {0, 1}\nshapes_actions.delete_selected_shapes(layer)\n" + SHAPE_OBSERVE,
        lambda r: r['data'] == [LINE]),
    'move-shapes-selection-to-front': (_shapes() + "layer.selected_data = {0}\nshapes_actions.move_shapes_selection_to_front(layer)\n" + SHAPE_OBSERVE,
        lambda r: r['z'][0] > max(r['z'][1:])),
    'move-shapes-selection-to-back': (_shapes() + "layer.selected_data = {2}\nshapes_actions.move_shapes_selection_to_back(layer)\n" + SHAPE_OBSERVE,
        lambda r: r['z'][2] < min(r['z'][:2])),
    'select-shapes-in-slice': (_shapes() + "layer.selected_data = set()\nshapes_actions.select_shapes_in_slice(layer)\n" + SHAPE_OBSERVE,
        lambda r: r['selected'] == [0, 1, 2]),
})

for _mode in ('rectangle', 'ellipse', 'line', 'path', 'polyline', 'polygon', 'polygon_lasso'):
    RECIPES[f'activate-add-{_mode.replace("_", "-")}-mode'] = (
        _shapes() + f"shapes_actions.activate_add_{_mode}_mode(layer)\nresult = {{'mode': str(layer.mode)}}\n",
        (lambda m: lambda r: r['mode'] == f'add_{m}')(_mode))
for _mode in ('direct', 'select', 'vertex_insert', 'vertex_remove'):
    RECIPES[f'activate-{_mode.replace("_", "-")}-mode'] = (
        _shapes() + f"shapes_actions.activate_{_mode}_mode(layer)\nresult = {{'mode': str(layer.mode)}}\n",
        (lambda m: lambda r: r['mode'] == m)(_mode))

W = "layer.data_to_world"  # data -> world for the shapes layer (identity here)
RECIPES.update({
    'mouse-add-rectangle': ("layer = fresh('shapes', 'P4 drawn', ndim=2)\nlayer.mode = 'add_rectangle'\n"
        f"gesture(layer, [{W}([4.0, 5.0]), {W}([6.0, 8.0]), {W}([10.0, 13.0])])\n" + SHAPE_OBSERVE,
        lambda r: r['type'] == ['rectangle'] and corners(r['data'][0]) == _box([4, 5], [10, 13])),
    'mouse-add-ellipse': ("layer = fresh('shapes', 'P4 drawn', ndim=2)\nlayer.mode = 'add_ellipse'\n"
        f"gesture(layer, [{W}([4.0, 5.0]), {W}([6.0, 8.0]), {W}([10.0, 13.0])])\n" + SHAPE_OBSERVE,
        lambda r: r['type'] == ['ellipse'] and corners(r['data'][0]) == _box([4, 5], [10, 13])),
    'mouse-add-line': ("layer = fresh('shapes', 'P4 drawn', ndim=2)\nlayer.mode = 'add_line'\n"
        f"gesture(layer, [{W}([4.0, 5.0]), {W}([6.0, 8.0]), {W}([10.0, 13.0])])\n" + SHAPE_OBSERVE,
        lambda r: r['type'] == ['line'] and r['data'][0] == [[4.0, 5.0], [10.0, 13.0]]),
    'mouse-add-path-polygon': ("layer = fresh('shapes', 'P4 drawn', ndim=2)\nlayer.mode = 'add_polygon'\n"
        "for p in ([2.0, 2.0], [2.0, 12.0], [10.0, 7.0]):\n    hover(layer, layer.data_to_world(p))\n    gesture(layer, [layer.data_to_world(p)])\n"
        "shapes_actions.finish_drawing_shape(layer)\n" + SHAPE_OBSERVE,
        lambda r: r['type'] == ['polygon'] and r['data'][0] == [[2.0, 2.0], [2.0, 12.0], [10.0, 7.0]]),
    'mouse-finish-drawing-shape': ("layer = fresh('shapes', 'P4 drawn', ndim=2)\nlayer.mode = 'add_polyline'\n"
        "for p in ([2.0, 2.0], [6.0, 9.0], [3.0, 14.0]):\n    hover(layer, layer.data_to_world(p))\n    gesture(layer, [layer.data_to_world(p)])\n"
        "from napari.utils.interactions import mouse_double_click_callbacks as _dc\n"
        "_dc(layer, _RO(_MouseEvent(type='mouse_double_click', position=full(layer.data_to_world([3.0, 14.0])), "
        "pos=np.asarray([14.0, 3.0]), dims_displayed=[int(n) for n in viewer.dims.displayed], dims_point=list(viewer.dims.point))))\n" + SHAPE_OBSERVE,
        lambda r: r['type'] == ['path'] and r['data'][0] == [[2.0, 2.0], [6.0, 9.0], [3.0, 14.0]]),  # double-click finishes
    'finish-drawing-shape': ("layer = fresh('shapes', 'P4 drawn', ndim=2)\nlayer.mode = 'add_polyline'\n"
        "for p in ([2.0, 2.0], [6.0, 9.0], [3.0, 14.0]):\n    hover(layer, layer.data_to_world(p))\n    gesture(layer, [layer.data_to_world(p)])\n"
        "shapes_actions.finish_drawing_shape(layer)\n" + SHAPE_OBSERVE,
        lambda r: len(r['data']) == 1 and r['data'][0] == [[2.0, 2.0], [6.0, 9.0], [3.0, 14.0]]),
    'mouse-select': (_shapes() + "layer.mode = 'select'\n"
        f"gesture(layer, [{W}([5.0, 7.0])])\none = sorted(int(i) for i in layer.selected_data)\n"
        f"gesture(layer, [{W}([14.0, 6.0])], modifiers=('Shift',))\ntwo = sorted(int(i) for i in layer.selected_data)\n"
        # a drag is many moves from the press point: napari starts the selection box at the first one
        f"gesture(layer, [{W}([0.0, 0.0]), {W}([0.5, 0.5]), {W}([10.0, 6.0]), {W}([20.0, 12.0])])\n"
        "box = sorted(int(i) for i in layer.selected_data)\n"
        "result = {'one': one, 'shift': two, 'box': box}\n",
        lambda r: r['one'] == [0] and r['shift'] == [0, 1] and r['box'] == [0, 1]),
    'mouse-move-active-vertex-under-cursor': (_shapes() + "layer.mode = 'direct'\n"
        f"gesture(layer, [{W}([5.0, 7.0])])\n"  # select the rectangle
        f"gesture(layer, [{W}([2.0, 3.0]), {W}([0.0, 1.0]), {W}([-1.0, 0.0])])\n" + SHAPE_OBSERVE,
        lambda r: [-1.0, 0.0] in r['data'][0] and [2.0, 3.0] not in r['data'][0] and r['data'][1:] == [TRI, LINE]),
    'mouse-vertex-insert': (_shapes() + "layer.mode = 'select'\n"
        f"gesture(layer, [{W}([15.0, 4.0])])\nlayer.mode = 'vertex_insert'\n"  # select the triangle
        f"gesture(layer, [{W}([12.0, 6.0])])\n" + SHAPE_OBSERVE,
        lambda r: len(r['data'][1]) == 4 and [12.0, 6.0] in r['data'][1] and all(v in r['data'][1] for v in TRI)),
    'mouse-vertex-remove': ("layer = fresh('shapes', 'P4 shapes', [np.array([[2.0, 2.0], [2.0, 12.0], [10.0, 12.0], [10.0, 2.0], [6.0, 0.0]])], shape_type='polygon')\n"
        "layer.mode = 'select'\n" f"gesture(layer, [{W}([6.0, 6.0])])\nlayer.mode = 'vertex_remove'\n"
        f"gesture(layer, [{W}([6.0, 0.0])])\n" + SHAPE_OBSERVE,
        lambda r: r['data'][0] == [[2.0, 2.0], [2.0, 12.0], [10.0, 12.0], [10.0, 2.0]]),
    'mouse-highlight': (_shapes() + "layer.mode = 'select'\n" f"hover(layer, {W}([5.0, 7.0]))\n"
        "over = None if layer._value is None or layer._value[0] is None else int(layer._value[0])\n"
        f"hover(layer, {W}([30.0, 30.0]))\n"
        "result = {'over': over, 'away': None if layer._value is None or layer._value[0] is None else int(layer._value[0])}\n",
        lambda r: r['over'] == 0 and r['away'] is None),
})


# Each Shapes recipe belongs to its group: creating shapes (shape-create) or editing them (shape-edit).
CREATE = {'shapes-add', 'shapes-add-rectangles', 'shapes-add-ellipses', 'shapes-add-lines', 'shapes-add-paths',
          'shapes-add-polygons', 'shapes-current-edge-color', 'shapes-current-edge-width', 'shapes-current-face-color',
          'shapes-current-properties', 'mouse-add-rectangle', 'mouse-add-ellipse', 'mouse-add-line',
          'mouse-add-path-polygon', 'mouse-finish-drawing-shape', 'finish-drawing-shape'}
RECIPES = {**POINT_RECIPES, **{(f'shape-create.{name}' if name in CREATE or name.startswith('activate-add-')
                                else f'shape-edit.{name}'): recipe for name, recipe in RECIPES.items()}}


# Labels overlays and settings (napari's own models beside the Labels tools): one 40x40 labels layer per recipe.
LABELS = "layer = fresh('labels', 'P4 overlay labels', np.zeros((40, 40), dtype=np.int32))\nlayer.selected_label = 1\n"
W2 = "layer.data_to_world"
LABEL_RECIPES = {
    'label-tools.labelspolygonoverlay-enabled': (LABELS + """
seen = []
for mode in ('polygon', 'paint', 'polygon', 'pan_zoom'):
    layer.mode = mode
    seen.append(bool(layer._overlays['polygon'].enabled))
result = {'enabled': seen}
""", lambda r: r['enabled'] == [True, False, True, False]),
    'label-tools.labelspolygonoverlay-points': (LABELS + f"""
layer.mode = 'polygon'
for p in ([5.0, 5.0], [5.0, 20.0], [20.0, 12.0]):
    hover(layer, {W2}(p))
    gesture(layer, [{W2}(p)])
result = {{'points': [[round(float(v), 3) for v in p] for p in layer._overlays['polygon'].points]}}
""", lambda r: r['points'][:3] == [[5.5, 5.5], [5.5, 20.5], [20.5, 12.5]] and len(r['points']) == 4),
    'label-tools.labelspolygonoverlay-completion-radius': (LABELS + """
ov = layer._overlays['polygon']
default = ov.completion_radius
ov.completion_radius = 35
result = {'default': default, 'set': ov.completion_radius}
""", lambda r: r['default'] == 20 and r['set'] == 35),
    'label-tools.labelspolygonoverlay-use-double-click-completion-radius': (LABELS + """
ov = layer._overlays['polygon']
default = bool(ov.use_double_click_completion_radius)
ov.use_double_click_completion_radius = True
result = {'default': default, 'set': bool(ov.use_double_click_completion_radius)}
""", lambda r: r['default'] is False and r['set'] is True),
    'label-tools.labelsbrushstrokeoverlay-enabled': (LABELS + """
seen = []
for mode in ('paint', 'fill', 'paint', 'erase'):
    layer.mode = mode
    seen.append(bool(layer._overlays['brush_stroke'].enabled))
result = {'enabled': seen}
""", lambda r: r['enabled'] == [True, False, True, False]),
    'label-tools.labelsbrushstrokeoverlay-active': (LABELS + f"""
layer.mode = 'paint'
layer.brush_size = 10
before = bool(layer._overlays['brush_stroke'].active)
gesture(layer, [{W2}([15.0, 15.0])], button=2)
ov = layer._overlays['brush_stroke']
result = {{'before': before, 'after': bool(ov.active), 'center': [round(float(v), 3) for v in ov.center][-2:],
          'radius': round(float(ov.radius), 3), 'staged': int(np.asarray(layer.data)[15, 15])}}
""", lambda r: r['before'] is False and r['after'] is True and r['center'] == [15.0, 15.0] and r['radius'] == 18.0
        and r['staged'] == 1),
    'edit-conflicts.labelsbrushstrokeoverlay-abort': (LABELS + f"""
layer.mode = 'paint'
layer.brush_size = 10
gesture(layer, [{W2}([15.0, 15.0])], button=2)
for p in ([15.0, 16.0], [16.0, 16.0]):
    hover(layer, {W2}(p))
undo_before = len(layer._undo_history)
layer._overlays['brush_stroke'].abort(layer)
result = {{'active': bool(layer._overlays['brush_stroke'].active), 'nonzero': int(np.count_nonzero(layer.data)),
          'undo': len(layer._undo_history) - undo_before, 'staged': len(layer._staged_history)}}
""", lambda r: r['active'] is False and r['nonzero'] == 0 and r['undo'] == 0 and r['staged'] == 0),
}
LABEL_RECIPES['label-tools.labelsbrushstrokeoverlay-center'] = LABEL_RECIPES['label-tools.labelsbrushstrokeoverlay-active']
LABEL_RECIPES['label-tools.labelsbrushstrokeoverlay-radius'] = LABEL_RECIPES['label-tools.labelsbrushstrokeoverlay-active']
RECIPES.update(LABEL_RECIPES)


SETTINGS = "from napari.settings import get_settings as _settings\n"
LASSO = ("layer = fresh('shapes', 'P4 lasso', ndim=2)\nlayer.mode = 'add_polygon_lasso'\n"
         "gesture(layer, [layer.data_to_world(p) for p in [[10.0, 10.0]] + [[10.0 + 0.5 * i, 10.0 + i] for i in range(1, 30)]"
         " + [[25.0, 40.0 - i] for i in range(0, 30)] + [[11.0, 11.0]]])\n")
SETTING_RECIPES = {
    'shape-edit.selectionboxoverlay-update-from-points': ("""
layer = fresh('shapes', 'P4 box', ndim=2)
ov = layer._overlays['selection_box']
ov.update_from_points(np.array([[2.0, 3.0], [10.0, 4.0], [6.0, 12.0]]))
result = {'bounds': [[float(v) for v in p] for p in ov.bounds]}
""", lambda r: r['bounds'] == [[2.0, 10.0], [3.0, 12.0]]),  # (min, max) of each coordinate, as its code computes
    # napari keeps a selection box model on every layer and draws it from these fields; nothing in napari moves it on
    # its own, so the behaviour is the model's: what is set is what its bounds, handles and events say.
    'shape-edit.selectionboxoverlay-bounds': ("""
layer = fresh('shapes', 'P4 box', ndim=2)
ov = layer._overlays['selection_box']
seen = []
ov.events.bounds.connect(lambda e: seen.append([[float(v) for v in p] for p in getattr(e, 'value', e)]))
default = [[float(v) for v in p] for p in ov.bounds]
ov.bounds = ((1.0, 4.0), (2.0, 9.0))
result = {'default': default, 'bounds': [[float(v) for v in p] for p in ov.bounds], 'events': seen}
""", lambda r: r['default'] == [[0.0, 0.0], [0.0, 0.0]] and r['bounds'] == [[1.0, 4.0], [2.0, 9.0]] and r['events'] == [r['bounds']]),
    'shape-edit.selectionboxoverlay-handles': ("""
layer = fresh('shapes', 'P4 box', ndim=2)
ov = layer._overlays['selection_box']
seen = []
ov.events.handles.connect(lambda e: seen.append(bool(getattr(e, 'value', e))))
default = bool(ov.handles)
ov.handles = True
ov.handles = False
result = {'default': default, 'events': seen}
""", lambda r: r['default'] is False and r['events'] == [True, False]),
    'shape-edit.selectionboxoverlay-selected-handle': ("""
layer = fresh('shapes', 'P4 box', ndim=2)
ov = layer._overlays['selection_box']
from napari.components.overlays.interaction_box import InteractionBoxHandle
default = ov.selected_handle
ov.selected_handle = InteractionBoxHandle.TOP_LEFT
result = {'default': None if default is None else str(default), 'set': str(ov.selected_handle)}
""", lambda r: r['default'] is None and r['set'] != 'None'),
    'shape-create.setting-experimentalsettings-lasso-vertex-distance': (SETTINGS + """
counts = []
_settings().experimental.rdp_epsilon = 0.0  # no simplification: the threshold alone decides which samples stay
for distance in (1, 10, 40):
    _settings().experimental.lasso_vertex_distance = distance
""" + "\n".join("    " + line for line in LASSO.splitlines()) + """
    counts.append(len(layer.data[-1]) if len(layer.data) else 0)
_settings().experimental.lasso_vertex_distance = 10
_settings().experimental.rdp_epsilon = 0.5
result = {'counts': counts}
""", lambda r: r['counts'][0] >= r['counts'][1] >= r['counts'][2] and r['counts'][0] > r['counts'][2]),
    'shape-create.setting-experimentalsettings-rdp-epsilon': (SETTINGS + """
counts = []
for epsilon in (0.0, 0.5, 5.0):
    _settings().experimental.rdp_epsilon = epsilon
    _settings().experimental.lasso_vertex_distance = 1
""" + "\n".join("    " + line for line in LASSO.splitlines()) + """
    counts.append(len(layer.data[-1]) if len(layer.data) else 0)
_settings().experimental.rdp_epsilon = 0.5
_settings().experimental.lasso_vertex_distance = 10
result = {'counts': counts}
""", lambda r: r['counts'][0] >= r['counts'][1] >= r['counts'][2] and r['counts'][0] > r['counts'][2]),
    'shape-create.setting-experimentalsettings-triangulation-backend': (SETTINGS + f"""
before = str(_settings().experimental.triangulation_backend)
choices = [str(c) for c in type(_settings().experimental.triangulation_backend)]
_settings().experimental.triangulation_backend = 'pure_python'
layer = fresh('shapes', 'P4 tri', [np.array({TRI!r})], shape_type='polygon')
after = str(_settings().experimental.triangulation_backend)
_settings().experimental.triangulation_backend = before
result = {{'after': after, 'choices': choices, 'data': [rows(s) for s in layer.data]}}
""", lambda r: r['after'] == 'Pure python' and 'Pure python' in r['choices'] and r['data'] == [TRI]),
    'shape-create.setting-experimentalsettings-compiled-triangulation': (SETTINGS + f"""
default = bool(_settings().experimental.compiled_triangulation)
_settings().experimental.compiled_triangulation = True
layer = fresh('shapes', 'P4 tri', [np.array({TRI!r})], shape_type='polygon')
data = [rows(s) for s in layer.data]
_settings().experimental.compiled_triangulation = default
result = {{'default': default, 'data': data}}
""", lambda r: r['default'] is False and r['data'] == [TRI]),
    'label-tools.setting-experimentalsettings-completion-radius': (SETTINGS + """
layer = fresh('labels', 'P4 overlay labels', np.zeros((40, 40), dtype=np.int32))
ov = layer._overlays['polygon']
default = (_settings().experimental.completion_radius, ov.completion_radius, bool(ov.use_double_click_completion_radius))
_settings().experimental.completion_radius = 30
changed = (ov.completion_radius, bool(ov.use_double_click_completion_radius))
_settings().experimental.completion_radius = -1
result = {'default': list(default), 'changed': list(changed)}
""", lambda r: r['default'][0] == -1 and r['changed'] == [30, True]),
    'label-brush.setting-applicationsettings-brush-size-on-mouse-move-modifiers': (SETTINGS + """
from napari.layers.labels import _labels_mouse_bindings as _m
default = (str(_settings().application.brush_size_on_mouse_move_modifiers), tuple(_m.BRUSH_SIZE_ON_MOUSE_MOVE_MODIFIERS_PARTS))
_settings().application.brush_size_on_mouse_move_modifiers = 'Control'
changed = tuple(_m.BRUSH_SIZE_ON_MOUSE_MOVE_MODIFIERS_PARTS)
_settings().application.brush_size_on_mouse_move_modifiers = default[0]
result = {'default': [default[0], list(default[1])], 'changed': list(changed)}
""", lambda r: r['default'][1] == ['Alt'] and r['changed'] == ['Control']),
    'label-tools.mouse-change-brush-size-on-mouse-move-modifiers': ("""
from napari.layers.labels import _labels_mouse_bindings as _m
before = tuple(_m.BRUSH_SIZE_ON_MOUSE_MOVE_MODIFIERS_PARTS)
_m.change_brush_size_on_mouse_move_modifiers(('Shift', 'Alt'))
after = tuple(_m.BRUSH_SIZE_ON_MOUSE_MOVE_MODIFIERS_PARTS)
_m.change_brush_size_on_mouse_move_modifiers(before)
result = {'after': list(after), 'restored': list(_m.BRUSH_SIZE_ON_MOUSE_MOVE_MODIFIERS_PARTS) == list(before)}
""", lambda r: r['after'] == ['Shift', 'Alt'] and r['restored'] is True),
}
RECIPES.update(SETTING_RECIPES)


MORE_LABELS = {
    'label-tools.labels-show-selected-label': (LABELS + """
layer.data[5:10, 5:10] = 3
layer.selected_label = 3
default = bool(layer.show_selected_label)
layer.show_selected_label = True
on = (bool(layer.colormap.use_selection), int(layer.colormap.selection))
layer.show_selected_label = False
off = bool(layer.colormap.use_selection)
result = {'default': default, 'on': list(on), 'off': off}
""", lambda r: r['default'] is False and r['on'] == [True, 3] and r['off'] is False),
    'label-history.labels-block-history': (LABELS + """
before = len(layer._undo_history)
with layer.block_history():
    layer.paint((5, 5), 2, refresh=False)
    layer.paint((20, 20), 4, refresh=False)
one = len(layer._undo_history) - before
layer.undo()
result = {'items': one, 'after_undo': int(np.count_nonzero(layer.data))}
""", lambda r: r['items'] == 1 and r['after_undo'] == 0),
    'label-history.labels-data-setitem': (LABELS + """
before = len(layer._undo_history)
layer.data_setitem((np.array([3, 4, 5]), np.array([7, 8, 9])), 6)
values = [int(v) for v in np.asarray(layer.data)[[3, 4, 5], [7, 8, 9]]]
items = len(layer._undo_history) - before
layer.undo()
result = {'values': values, 'items': items, 'after_undo': int(np.count_nonzero(layer.data))}
""", lambda r: r['values'] == [6, 6, 6] and r['items'] == 1 and r['after_undo'] == 0),
    # napari's canvas draws the brush circle from the cursor (napari/_vispy/canvas.py _on_cursor): its size is the
    # cursor size scaled by the camera zoom, and the frozen cursor (brush resizing) freezes its position.
    'label-brush.brushcircleoverlay-size': (LABELS + """
viewer.layers.selection.active = layer
layer.mode = 'paint'
layer.brush_size = 10
viewer.scene.camera.zoom = 2.0
ov = viewer.canvas.overlays['_brush_circle']
result = {'size': int(ov.size), 'cursor': str(viewer.cursor.style), 'cursor_size': float(viewer.cursor.size),
          'scaled': bool(viewer.cursor.scaled), 'zoom': float(viewer.scene.camera.zoom)}
""", lambda r: r['cursor'] == 'circle' and r['size'] == int(r['cursor_size'] * (r['zoom'] if r['scaled'] else 1))),
    'label-brush.brushcircleoverlay-position-is-frozen': (LABELS + """
viewer.layers.selection.active = layer
layer.mode = 'paint'
layer.brush_size = 10
viewer.scene.camera.zoom = 2.0
ov = viewer.canvas.overlays['_brush_circle']
viewer.cursor.style = 'circle_frozen'
frozen = bool(ov.position_is_frozen)
viewer.cursor.style = 'circle'
result = {'frozen': frozen, 'after': bool(ov.position_is_frozen)}
""", lambda r: r['frozen'] is True and r['after'] is False),
}
RECIPES.update(MORE_LABELS)
