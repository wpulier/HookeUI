"""Comparison recipes for the H00 reference harness. Plain napari only: this text runs twice and is compared.

Hooke's side runs it in a confined review's Python console (``orchestrator.scientific_project_review.review``, op
``python-resources``), where ``viewer`` is the session's own viewer. The reference side runs the same text in a
separate process of the pinned runtime that imports only napari (``tests/scientific_reference_process.py``), on a
hidden ``Viewer`` holding the same volume, with its Qt controls and VisPy observers. ``observe(symbol, viewer)`` makes one child's behaviour happen through its
public trigger and returns what it observed. That is the trigger's return value, every emission of the watched event
(its type, its normalized value, whether it names the instance), the part of the instance's public state that
changed, and any exception. ``tests/test_scientific_reference_comparison.py`` compares the two sides exactly after
``normalize``. Nothing here decides a pass.

Layer cases add a fresh layer to ``viewer`` and remove it afterwards. Dims, camera and layer-list cases use the
viewer's own, and the camera and dims are restored after each case. Both sides run the cases in the same order.
"""
import hashlib as _hashlib
import math as _math
import warnings as _warnings

FLOAT_DIGITS = 12  # frozen before the first comparison run: values agree to 12 significant digits
SMALL = 64  # arrays of at most this many elements are compared element by element, larger ones by digest
PROFILE = 'napari-gui-synchronous-v1'


def comparison_profile(viewer, recipe_sha256):
    """Observe the GUI/configuration actually used; never change it to match the other side.

    Triggers and callbacks run synchronously, without pumping queued Qt events inside an observation.
    The two rendering platforms are recorded separately: Hooke uses its CGL offscreen backend;
    the independent reference uses unmodified napari on Cocoa. This is not a pixel comparison.
    Full settings include experimental.dynamic_layer_controls, whose legacy/dynamic implementations
    emit different colormap events. Unknown or differing settings must fail the comparison guard.
    """
    import json
    import qtpy
    from qtpy.QtWidgets import QApplication
    from napari.settings import get_settings
    return {'name': PROFILE, 'viewer': f'{type(viewer).__module__}.{type(viewer).__qualname__}',
            'ndisplay': viewer.dims.ndisplay, 'qt_api': qtpy.API_NAME,
            'qt_platform': QApplication.instance().platformName(),
            'settings': json.loads(get_settings().model_dump_json()), 'recipe_sha256': recipe_sha256}


def normalize(value, depth=0):
    """A JSON value both sides can compare exactly."""
    import enum
    import numpy as np
    if depth > 8:
        return f'<{type(value).__name__}>'
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, enum.Enum):
        return normalize(value.value, depth + 1)
    if isinstance(value, (int, np.integer)) and not isinstance(value, (bool, np.bool_)):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, (float, np.floating)):
        value = float(value)
        return 'nan' if _math.isnan(value) else repr(float(f'{value:.{FLOAT_DIGITS}g}'))
    if isinstance(value, np.ndarray):
        if value.dtype.kind == 'O' or value.size <= SMALL:
            return {'dtype': value.dtype.str, 'shape': list(value.shape),
                    'values': normalize(value.tolist(), depth + 1)}
        return {'dtype': value.dtype.str, 'shape': list(value.shape),
                'sha256': _hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest()}
    if isinstance(value, dict):
        return {str(k): normalize(v, depth + 1) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [normalize(v, depth + 1) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((normalize(v, depth + 1) for v in value), key=repr)
    if type(value).__name__ == 'DataFrame':
        return {'columns': [str(c) for c in value.columns], 'dtypes': [str(t) for t in value.dtypes],
                'values': normalize({str(c): value[c].to_numpy() for c in value.columns}, depth + 1)}
    if hasattr(value, 'dict') and callable(value.dict):  # an evented or pydantic model
        try:
            return normalize(value.dict(), depth + 1)
        except Exception:  # noqa: BLE001 - an unreadable model is compared by its type only
            pass
    if type(value).__module__.startswith('pint'):
        return str(value)
    if hasattr(value, 'shape') and hasattr(value, 'dtype'):  # an array-like such as a dask or zarr array
        return normalize(np.asarray(value), depth + 1)
    return f'<{type(value).__name__}>'


def _state(x):
    """The instance's public state, normalized; a layer's data by digest."""
    import numpy as np
    from napari.components import LayerList
    from napari.layers import Layer
    if isinstance(x, Layer):
        state = {k: v for k, v in x._get_state().items() if k != 'data'}
        data = x.data if isinstance(x.data, (list, tuple)) else [x.data]
        state['data'] = [normalize(np.asarray(part)) for part in data]
        for extra in ('mode', 'selected_data', 'features', 'feature_defaults', 'current_size', 'selected_label'):
            if hasattr(x, extra):
                state[extra] = getattr(x, extra)
        return normalize(state)
    if isinstance(x, LayerList):
        return {'names': [layer.name for layer in x], 'selection': sorted(layer.name for layer in x.selection),
                'active': None if x.selection.active is None else x.selection.active.name}
    if hasattr(x, 'dict'):
        return normalize(x.dict())
    return {}


def _delta(before, after):
    keys = sorted(set(before) | set(after))
    return {k: after.get(k, '<absent>') for k in keys if before.get(k, '<absent>') != after.get(k, '<absent>')}


def _make(owner):
    """A fresh, real upstream instance of each layer owner, made through the public API."""
    import numpy as np
    from napari import layers
    image = lambda: layers.Image(np.zeros((4, 5, 6), 'float32'), name='case-image')
    return {
        'Layer': image, 'ScalarFieldBase': image, 'IntensityVisualizationMixin': image,
        'Points': lambda: layers.Points(np.array([[1., 1.], [2., 3.]]), name='case-points'),
        'Labels': lambda: layers.Labels(np.zeros((5, 6), 'uint8'), name='case-labels'),
        'Shapes': lambda: layers.Shapes([np.array([[0., 0.], [0., 2.], [2., 2.], [2., 0.]])], shape_type='rectangle',
                                        name='case-shapes'),
        'Surface': lambda: layers.Surface((np.array([[0., 0.], [0., 1.], [1., 1.]]), np.array([[0, 1, 2]])),
                                          name='case-surface'),
        'Vectors': lambda: layers.Vectors(np.zeros((2, 2, 2)), name='case-vectors'),
        'Tracks': lambda: layers.Tracks(np.array([[0, 0, 1., 1.], [0, 1, 2., 2.]]), name='case-tracks'),
    }[owner]()


def _target(owner, viewer):
    """The instance a case acts on, and how to put the viewer back afterwards."""
    if owner in ('Dims', 'Camera'):
        part = viewer.dims if owner == 'Dims' else viewer.camera
        saved, names = part.dict(), [layer.name for layer in viewer.layers]

        def restore():
            for layer in [layer for layer in viewer.layers if layer.name not in names]:
                viewer.layers.remove(layer)
            with _warnings.catch_warnings():
                _warnings.simplefilter('ignore')
                part.update(saved)
        return part, restore
    if owner == 'LayerList':
        names = [layer.name for layer in viewer.layers]

        def restore():  # cases add their own layers to the list; those go again
            for layer in [layer for layer in viewer.layers if layer.name not in names]:
                viewer.layers.remove(layer)
        return viewer.layers, restore
    layer = viewer.add_layer(_make(owner))

    def remove():
        if layer in viewer.layers:
            viewer.layers.remove(layer)
    return layer, remove


# Events: the change that makes upstream emit each one. AUTO changes a plain value attribute of the same name.
AUTO = 'auto'
EVENTS = {
    **{f'Camera.events.{n}': AUTO for n in ('angles', 'center', 'mouse_pan', 'mouse_zoom', 'perspective', 'synced', 'zoom')},
    'Camera.events.orientation': "x.orientation = ('away', 'up', 'left')",
    **{f'Dims.events.{n}': AUTO for n in ('margin_left', 'margin_right', 'ndisplay')},
    # last_used names a non-displayed axis: a 4D layer gives the viewer a second one to move to.
    'Dims.events.last_used': "viewer.add_image(np.zeros((2, 3, 4, 5), 'float32'), name='case-4d')\n"
                             'x.last_used = 1 if x.last_used != 1 else 0',
    # A viewer's dims follow its layers: a layer with more dimensions is the change that sets ndim.
    'Dims.events.ndim': "viewer.add_image(np.zeros((2, 3, 4, 5), 'float32'), name='case-4d')",
    'Dims.events.axis_labels': "x.axis_labels = ('row', 'column')",
    'Dims.events.order': 'x.order = (1, 0)',
    'Dims.events.point': 'x.range = ((0, 10, 1), (0, 10, 1)); x.point = (3, 4)',
    'Dims.events.range': 'x.range = ((0, 10, 1), (0, 20, 2))',
    'Dims.events.rollable': 'x.rollable = (False, True)',
    'Dims.events.units': "import pint; unit = pint.get_application_registry().Unit('micrometer'); x.units = (unit, unit)",
    **{f'IntensityVisualizationMixin.events.{n}': AUTO for n in ('auto_contrast', 'gamma')},
    'IntensityVisualizationMixin.events.colormap': "x.colormap = 'magma'",
    'IntensityVisualizationMixin.events.contrast_limits': 'x.contrast_limits = (0.1, 0.5)',
    'IntensityVisualizationMixin.events.contrast_limits_range': 'x.contrast_limits_range = (0., 2.)',
    **{f'Labels.events.{n}': AUTO for n in ('brush_size', 'contiguous', 'contour', 'n_edit_dimensions',
                                             'preserve_labels', 'selected_label', 'show_selected_label')},
    'Labels.events.colormap': 'from napari.utils.colormaps import label_colormap; x.colormap = label_colormap(5)',
    'Labels.events.features': "x.features = {'index': [0, 1], 'size': [1., 2.]}",
    'Labels.events.iso_gradient_mode': "x.iso_gradient_mode = 'smooth'",
    'Labels.events.labels_update': 'x.paint((1, 1), 2)',
    'Labels.events.paint': 'x.paint((1, 1), 2)',
    'Labels.events.properties': "x.properties = {'index': np.array([0, 1]), 'size': np.array([1., 2.])}",
    **{f'Layer.events.{n}': AUTO for n in ('cursor_size', 'editable', 'mouse_pan', 'mouse_zoom', 'opacity', 'scale_factor',
                                            'visible')},
    'Layer.events.affine': 'x.affine = np.diag([2., 2., 2., 1.])',
    'Layer.events.axis_labels': "x.axis_labels = ('plane', 'row', 'column')",
    'Layer.events.blending': "x.blending = 'additive'",
    'Layer.events.cursor': "x.cursor = 'cross'",
    'Layer.events.data': "x.data = np.ones((4, 5, 6), 'float32')",
    'Layer.events.help': "x.help = 'Hooke check'",
    'Layer.events.locked': 'x.locked = True',
    'Layer.events.metadata': "x.metadata = {'hooke': 1}",
    'Layer.events.mode': "x.mode = 'transform'",
    'Layer.events.name': "x.name = 'renamed'",
    'Layer.events.projection_mode': "x.projection_mode = 'max'",
    # zyx: rotate the displayed y/x plane. The original z/y quarter-turn projects to a
    # singular 2D VisPy transform; it is retained separately in LIMITATIONS below.
    'Layer.events.rotate': 'x.rotate = [[1., 0., 0.], [0., 0., -1.], [0., 1., 0.]]',
    'Layer.events.scale': 'x.scale = (2., 3., 4.)',
    'Layer.events.set_data': "x.data = np.ones((4, 5, 6), 'float32')",
    'Layer.events.shear': 'x.shear = (0.1, 0.2, 0.3)',
    'Layer.events.thumbnail': 'x.refresh()',
    'Layer.events.translate': 'x.translate = (1., 2., 3.)',
    'Layer.events.units': "x.units = ('um', 'um', 'um')",
    # clear() would remove the session's own layers: batched_update is the public batch it uses.
    **{f'LayerList.events.{n}': "from napari.layers import Image\nwith x.batched_update(): x.append(Image(np.zeros((2, 2)), name='a'))"
       for n in ('begin_batch', 'end_batch')},
    'LayerList.events.renamed': "from napari.layers import Image; x.append(Image(np.zeros((2, 2)), name='a')); x[0].name = 'b'",
    'LayerList.events.units': "from napari.layers import Image; x.append(Image(np.zeros((2, 2)), name='a')); x.units = None",
    **{f'Points.events.{n}': AUTO for n in ('antialiasing', 'border_width_is_relative', 'canvas_size_limits',
                                             'n_dimensional', 'out_of_slice_display')},
    'Points.events.border_color': "x.border_color = 'red'",
    'Points.events.border_width': 'x.border_width = 0.3',
    'Points.events.current_border_color': "x.current_border_color = 'blue'",
    'Points.events.current_border_width': 'x.current_border_width = 0.4',
    'Points.events.current_face_color': "x.current_face_color = 'green'",
    'Points.events.current_properties': "x.features = {'v': [1, 2]}; x.current_properties = {'v': np.array([7])}",
    'Points.events.current_size': 'x.current_size = 3',
    'Points.events.current_symbol': "x.current_symbol = 'square'",
    'Points.events.face_color': "x.face_color = 'red'",
    'Points.events.feature_defaults': "x.features = {'v': [1, 2]}; x.feature_defaults = {'v': 9}",
    'Points.events.features': "x.features = {'v': [1, 2]}",
    'Points.events.highlight': 'x.selected_data = {0}',
    'Points.events.properties': "x.properties = {'v': np.array([1, 2])}",
    'Points.events.shading': "x.shading = 'spherical'",
    'Points.events.size': 'x.selected_data = {0}; x.current_size = 5',
    'Points.events.symbol': "x.symbol = 'square'",
    **{f'ScalarFieldBase.events.{n}': AUTO for n in ('attenuation', 'iso_threshold')},
    'ScalarFieldBase.events.custom_interpolation_kernel_2d': 'x.custom_interpolation_kernel_2d = np.ones((3, 3)) / 9',
    'ScalarFieldBase.events.depiction': "x.depiction = 'plane'",
    'ScalarFieldBase.events.interpolation2d': "x.interpolation2d = 'linear'",
    'ScalarFieldBase.events.interpolation3d': "x.interpolation3d = 'linear'",
    'ScalarFieldBase.events.plane': "x.plane = {'position': (1., 2., 3.), 'normal': (1., 0., 0.)}",
    'ScalarFieldBase.events.rendering': "x.rendering = 'translucent'",
    'Shapes.events.current_edge_color': "x.current_edge_color = 'red'",
    'Shapes.events.current_face_color': "x.current_face_color = 'blue'",
    'Shapes.events.current_properties': "x.features = {'v': [1]}; x.current_properties = {'v': np.array([7])}",
    'Shapes.events.edge_color': "x.edge_color = 'red'",
    'Shapes.events.edge_width': 'x.current_edge_width = 3',
    'Shapes.events.face_color': "x.face_color = 'green'",
    'Shapes.events.feature_defaults': "x.features = {'v': [1]}; x.feature_defaults = {'v': 9}",
    'Shapes.events.features': "x.features = {'v': [1]}",
    'Shapes.events.highlight': 'x.selected_data = {0}',
    'Shapes.events.properties': "x.properties = {'v': np.array([1])}",
    'Surface.events.feature_defaults': "x.features = {'v': [1., 2., 3.]}; x.feature_defaults = {'v': 9.}",
    'Surface.events.features': "x.features = {'v': [1., 2., 3.]}",
    'Surface.events.normals': "x.normals = {'face': {'visible': True}}",
    'Surface.events.shading': "x.shading = 'smooth'",
    'Surface.events.texcoords': 'x.texcoords = np.zeros((3, 2))',
    'Surface.events.wireframe': 'x.wireframe.visible = True',
    **{f'Tracks.events.{n}': AUTO for n in ('display_graph', 'display_id', 'display_tail', 'head_length',
                                             'hide_completed_tracks', 'tail_length', 'tail_width')},
    'Tracks.events.color_by': "x.features = {'v': [1., 2.]}; x.color_by = 'v'",
    'Tracks.events.colormap': "x.colormap = 'viridis'",
    'Tracks.events.properties': "x.properties = {'v': np.array([1., 2.])}",
    'Tracks.events.rebuild_graph': 'x.graph = {}',
    'Tracks.events.rebuild_tracks': 'x.data = np.array([[0, 0, 1., 1.], [0, 1, 3., 3.]])',
    **{f'Vectors.events.{n}': AUTO for n in ('edge_width', 'length', 'out_of_slice_display')},
    'Vectors.events.edge_color': "x.edge_color = 'red'",
    'Vectors.events.edge_color_mode': "x.properties = {'v': np.array([0., 1.])}; x.edge_color_mode = 'colormap'",
    'Vectors.events.feature_defaults': "x.features = {'v': [1, 2]}; x.feature_defaults = {'v': 9}",
    'Vectors.events.features': "x.features = {'v': [1, 2]}",
    'Vectors.events.properties': "x.properties = {'v': np.array([1, 2])}",
    'Vectors.events.vector_style': "x.vector_style = 'arrow'",
}


def _changed(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value * .5 + .25
    if isinstance(value, tuple) and value and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
        return tuple(v + 1 for v in value)
    raise AssertionError(f'{value!r} has no plain changed value')


LIMITATIONS = {
    'out-of-plane-rotation-2d': ('Layer.events.rotate',
                               'x.rotate = [[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]]'),
}


def _event(symbol, viewer, recipe=None):
    import numpy as np
    owner, _, name = symbol.split('.')
    x, restore = _target(owner, viewer)
    try:
        emitter, seen = getattr(x.events, name), []

        def callback(event):  # an event names its source only while it is being delivered
            payload = {k: v for k, v in vars(event).items() if not k.startswith('_')}
            seen.append({'type': event.type, 'payload': normalize(payload), 'source': event.source is x})
        emitter.connect(callback)
        before = _state(x)
        try:
            recipe = EVENTS[symbol] if recipe is None else recipe
            with _warnings.catch_warnings():
                _warnings.simplefilter('ignore')
                if recipe == AUTO:
                    setattr(x, name, _changed(getattr(x, name)))
                else:
                    exec(recipe, {'np': np, 'x': x, 'viewer': viewer})
        finally:
            emitter.disconnect(callback)
        return {'events': seen, 'delta': _delta(before, _state(x))}
    finally:
        restore()




















def _layer_data(viewer):
    import numpy as np
    x, restore = _target('Layer', viewer)
    try:
        before, new = _state(x), np.ones((7, 5, 6), 'float32')
        x.data = new
        return {'same_object': x.data is new, 'extent': normalize(x.extent.data), 'delta': _delta(before, _state(x))}
    finally:
        restore()


def _layer_metadata(viewer):
    x, restore = _target('Layer', viewer)
    try:
        before = _state(x)
        x.metadata = {'hooke': 1}
        return {'metadata': normalize(x.metadata), 'delta': _delta(before, _state(x))}
    finally:
        restore()




def _model_events(viewer):
    model, restore = _target('Dims', viewer)
    try:
        seen = []
        callback = lambda event: seen.append(normalize(event.value))
        model.events.ndisplay.connect(callback)
        model.ndisplay = 2 if model.ndisplay == 3 else 3
        model.events.ndisplay.disconnect(callback)
        return {'fields': sorted(model.events.emitters), 'seen': seen}
    finally:
        restore()


def _selection(field, viewer):
    import numpy as np
    from napari.layers import Image
    layers, restore = _target('LayerList', viewer)
    kept = list(layers.selection)
    try:
        first, second = Image(np.zeros((2, 2)), name='a'), Image(np.zeros((2, 2)), name='b')
        layers.extend([first, second])
        if field == 'selection':
            layers.selection.select_only(first)
        else:
            layers.selection.active = second
        return _state(layers)
    finally:
        restore()
        layers.selection.update(kept)






def _keymap_provider(viewer):
    layer, restore = _target('Points', viewer)
    try:
        def action(layer):
            return None
        layer.bind_key('Shift-H', action, overwrite=True)
        held = [str(key) for key, value in layer.keymap.items()
                if value is action or getattr(value, '__wrapped__', None) is action]
        return {'bound': held}
    finally:
        restore()




def _mouse_callbacks(provider, field, viewer):
    def callback(layer, event):
        return None
    if provider == 'MousemapProvider':
        owner, restore = _target('Points', viewer)
    else:
        owner, restore = viewer, lambda: None
    try:
        before = list(getattr(owner, field))
        getattr(owner, field).append(callback)
        kept = callback in getattr(owner, field)
        getattr(owner, field).remove(callback)
        return {'kept': kept, 'count_change': len(getattr(owner, field)) - len(before)}
    finally:
        restore()


def _raises(fn):
    try:
        fn()
    except (AttributeError, TypeError, ValueError):
        return True
    return False


def _viewer_field(field, viewer):
    value = getattr(viewer, field)
    expected = {'dims': 'Dims', 'layers': 'LayerList'}[field]
    if type(value).__name__ != expected or not _raises(lambda: setattr(viewer, field, type(value)())):
        raise AssertionError(f'ViewerModel.{field} is not its frozen {expected}')
    return {field: expected}




def _actions(member, viewer):
    import numpy as np
    layer, restore = _target('Points', viewer)
    try:
        seen = []
        layer.events.data.connect(lambda event: seen.append(str(getattr(event.action, 'value', event.action))))
        layer.add([[4., 4.]])
        layer.selected_data = {0}
        layer.remove_selected()
        layer.data = np.array([[5., 5.]])
        return {'reported': seen, 'has': member.lower() in seen}
    finally:
        restore()






def _layers(added):
    import numpy as np
    return [[layer._type_string, normalize(layer.data if not isinstance(layer.data, (list, tuple))
                                           else [np.asarray(p) for p in layer.data])] for layer in added]


def _opened(viewer, open_):
    before = list(viewer.layers)
    with _warnings.catch_warnings():
        _warnings.simplefilter('ignore')
        open_()
    added = [layer for layer in viewer.layers if layer not in before]
    try:
        return _layers(added)
    finally:
        for layer in added:
            viewer.layers.remove(layer)


def _reader(command, viewer):
    import numpy as np
    from pathlib import Path
    if command == 'napari.get_obj_reader':
        Path('reader.obj').write_text('v 0 0 0\nv 0 1 0\nv 1 1 0\nf 1 2 3\n')
        return {'layers': _opened(viewer, lambda: viewer.open('reader.obj', plugin='napari'))}
    if command == 'napari.get_py_reader':
        Path('reader.py').write_text('import builtins\nbuiltins.hooke_reader_runs = getattr(builtins, "hooke_reader_runs", 0) + 1\n')
        import builtins
        before = getattr(builtins, 'hooke_reader_runs', 0)
        layers = _opened(viewer, lambda: viewer.open('reader.py', plugin='napari'))
        return {'layers': layers, 'ran': getattr(builtins, 'hooke_reader_runs', 0) - before}
    np.save('reader.npy', np.arange(12, dtype='<u2').reshape(3, 4))
    return {'layers': _opened(viewer, lambda: viewer.open('reader.npy', plugin='napari'))}


def _writer(command, display_name, viewer):
    """Write through napari's writer, then read every written file back through napari's own reader."""
    import numpy as np
    from pathlib import Path
    from napari import layers
    from napari.plugins.io import save_layers
    made = {'napari.write_image': lambda: layers.Image(np.arange(12, dtype='uint8').reshape(3, 4), name='image'),
            'napari.write_labels': lambda: layers.Labels(np.arange(12, dtype='uint8').reshape(3, 4), name='labels'),
            'napari.write_points': lambda: layers.Points(np.array([[1., 2.]]), name='points'),
            'napari.write_shapes': lambda: layers.Shapes([np.array([[0., 0.], [0., 2.], [2., 2.]])], shape_type='polygon',
                                                         name='shapes')}
    # The layers are the session's: added to its viewer, saved from there, then removed.
    chosen = ([made['napari.write_image'](), made['napari.write_points']()] if command == 'napari.write_directory'
              else [made[command]()])
    chosen = [viewer.add_layer(layer) for layer in chosen]
    try:
        if command == 'napari.write_directory':
            written = save_layers('saved_folder', chosen, plugin='napari')
        else:
            suffix = {'lossless': '.tif', 'lossy': '.jpg', 'labels': '.tif', 'points': '.csv', 'shapes': '.csv'}[display_name]
            written = save_layers(f'saved_{display_name}{suffix}', chosen, plugin='napari')
    finally:
        for layer in chosen:
            if layer in viewer.layers:
                viewer.layers.remove(layer)
    files = sorted(str(p) for w in written for p in ([*Path(w).iterdir()] if Path(w).is_dir() else [Path(w)]))
    back = {Path(f).name: _opened(viewer, lambda f=f: viewer.open(f, plugin='napari')) for f in files}
    return {'written': [Path(w).name for w in written], 'read_back': back}


def _sample(key, viewer):
    return {'layers': _opened(viewer, lambda: viewer.open_sample('napari', key))}




ATTRIBUTES = {  # each acts on the session's own viewer or a layer added to it
    'Layer.data': _layer_data, 'Layer.metadata': _layer_metadata, 'EventedModel.events': _model_events,
    'KeymapProvider.keymap': _keymap_provider,
    'Selectable.selection': lambda v: _selection('selection', v), 'Selection.active': lambda v: _selection('active', v),
    'ViewerModel.dims': lambda v: _viewer_field('dims', v), 'ViewerModel.layers': lambda v: _viewer_field('layers', v),
    **{f'{provider}.{field}': (lambda v, provider=provider, field=field: _mouse_callbacks(provider, field, v))
       for provider in ('MousemapProvider', 'MousemapProviderPydantic')
       for field in ('mouse_double_click_callbacks', 'mouse_drag_callbacks', 'mouse_move_callbacks', 'mouse_wheel_callbacks')},
}
ENUMS = {  # reported by a points layer added to the session's viewer
    **{f'ActionType.{m}': (lambda v, m=m: _actions(m, v)) for m in ('ADDED', 'ADDING', 'CHANGED', 'CHANGING', 'REMOVED', 'REMOVING')},
}
# Samples whose data ships with the runtime or is computed. The others download on first use, which a confined
# worker cannot, and diffusion_4d needs numba, which this runtime lacks: their children stay missing.
SAMPLES = ('astronaut', 'balls_2d', 'balls_3d', 'binary_blobs', 'binary_blobs_3D', 'brick', 'camera', 'cat', 'checkerboard',
           'clock', 'coffee', 'coins', 'colorwheel', 'grass', 'gravel', 'horse', 'hubble_deep_field',
           'immunohistochemistry', 'microaneurysms', 'moon', 'page', 'retina', 'rocket', 'shepp_logan_phantom', 'text')
CONTRIBUTIONS = {
    **{f'readers:{c}': (lambda v, c=c: _reader(c, v)) for c in ('napari.get_obj_reader', 'napari.get_py_reader', 'napari.get_reader')},
    'writers:napari.write_directory:image*,labels*,points*,shapes*:Save to Folder':
        lambda v: _writer('napari.write_directory', 'Save to Folder', v),
    'writers:napari.write_image:image:lossless': lambda v: _writer('napari.write_image', 'lossless', v),
    'writers:napari.write_image:image:lossy': lambda v: _writer('napari.write_image', 'lossy', v),
    'writers:napari.write_labels:labels:labels': lambda v: _writer('napari.write_labels', 'labels', v),
    'writers:napari.write_points:points:points': lambda v: _writer('napari.write_points', 'points', v),
    'writers:napari.write_shapes:shapes:shapes': lambda v: _writer('napari.write_shapes', 'shapes', v),
    **{f'sample_data:napari.data.{k}:{k}': (lambda v, k=k: _sample(k, v)) for k in SAMPLES},
}
# H10 (P7) calls, each acting on the session's own viewer, layers held by it, or the session's own plugin registry
# (review of #416 and #428: a call on an object the case built for itself compares napari with napari and has no
# harness). The observation is the normalized return, the files written (by digest) and what the session's viewer
# reads back from them, and the layers added. Paths under the working directory read as <cwd>.

def _digests(names):
    from pathlib import Path
    out = {}
    for name in sorted(names):
        path = Path(name)
        files = sorted(p for p in path.rglob('*') if p.is_file()) if path.is_dir() else [path]
        for file in files:
            out[str(file)] = _hashlib.sha256(file.read_bytes()).hexdigest()
    return out


def _held(viewer, kind):
    """A layer of this kind added to the session's viewer, and how to take it out again."""
    import numpy as np
    from napari import layers
    made = {'image': lambda: layers.Image(np.arange(12, dtype='uint8').reshape(3, 4), name='held-image'),
            'labels': lambda: layers.Labels(np.arange(12, dtype='uint8').reshape(3, 4), name='held-labels'),
            'points': lambda: layers.Points(np.array([[1., 2.], [3., 4.]]), name='held-points'),
            'shapes': lambda: layers.Shapes([np.array([[0., 0.], [0., 2.], [2., 2.]])], shape_type='polygon',
                                            name='held-shapes')}[kind]()
    layer = viewer.add_layer(made)

    def release():
        if layer in viewer.layers:
            viewer.layers.remove(layer)
    return layer, release


def _holding(viewer, kinds, action):
    held = [_held(viewer, kind) for kind in kinds]
    try:
        return action([layer for layer, _ in held])
    finally:
        for _, release in held:
            release()


def _written_and_read(viewer, names, returned):
    """What was written, and what the session's own viewer opens from each file (through the session's plugins)."""
    from pathlib import Path
    files = _digests(names)
    back = {}
    for name in files:
        if Path(name).suffix in ('.tif', '.csv', '.png'):
            back[name] = _opened(viewer, lambda name=name: viewer.open(name, plugin='napari'))
    return {'returned': normalize(returned), 'files': files, 'read_back': back}


def _write_kind(viewer, kind):
    from napari_builtins.io import _write
    name = f'w-{kind}' + ('.csv' if kind in ('points', 'shapes') else '.tif')
    returned = _holding(viewer, [kind], lambda held: getattr(_write, f'napari_write_{kind}')(
        name, *held[0].as_layer_data_tuple()[:2]))
    return _written_and_read(viewer, [name], returned)


def _input_file():
    import numpy as np
    np.save('case.npy', np.arange(12, dtype='<u2').reshape(3, 4))
    return 'case.npy'


def _npe2():
    from napari.plugins import _npe2
    return _npe2


# Two runtime obligations; the two typing overload declarations share __getitem__.
LAYERLIST_PROOFS = {
    'LayerList.__getitem__': ('index', 'negative-index', 'name', 'renamed-name', 'slice', 'reverse', 'empty', 'missing-name', 'missing-index'),
    'LayerList.__setitem__': ('integer', 'same-object', 'slice', 'extended-slice', 'duplicate', 'wrong-type', 'wrong-length'),
}


def validate_layerlist_observation(symbol, report):
    """Positive anchors prevent two equally absent behaviors receiving comparison credit."""
    assert set(report) == {'observed'}, 'collection recipe must complete'
    observed = report['observed']
    keys = set(LAYERLIST_PROOFS[symbol])
    assert set(observed) == {'proven', 'cases', 'restored'}, 'incomplete collection observation'
    assert observed['restored'] is True, 'collection recipe did not restore retained state'
    assert set(observed['cases']) == keys and set(observed['proven']) == keys, 'missing collection operation'
    assert all(value is True for value in observed['proven'].values()), 'collection behavior was not demonstrated'


def _layerlist_case(viewer, symbol):
    """Act on the session collection, retaining native data and exact object relationships."""
    import numpy as np
    from napari.layers import Image
    from napari.components import LayerList
    layers = viewer.layers
    original, selected, active = list(layers), list(layers.selection), layers.selection.active
    records, proven = {}, {}
    result = {'cases': records, 'proven': proven, 'restored': False}
    dims, camera = viewer.dims.dict(), viewer.camera.dict()
    def image(letter, offset):
        return Image(np.arange(6, dtype='<u2').reshape(2, 3) + offset,
                     name='__h00_list_' + letter, scale=(2, 3), translate=(5, -7))
    held = [image('a', 10), image('b', 20), image('c', 30)]
    replacements = [image('d', 40), image('e', 50), image('f', 60)]
    names = {layer: layer.name for layer in original + held + replacements}
    if len(set(names.values())) != len(names):
        raise ValueError('Collection fixture names must not collide with retained input')
    def identity(layer):
        return names.get(layer, '<unexpected-layer>')
    def state():
        return {'layers': [[identity(layer), _state(layer)] for layer in layers],
                'selected': sorted(identity(layer) for layer in layers.selection),
                'active': None if layers.selection.active is None else identity(layers.selection.active)}
    original_state = state()
    def reject(key, operation, error_type):
        before = state()
        try:
            operation()
        except Exception as error:  # record the actual expected refusal, never convert it into success
            caught = type(error).__name__
        else:
            caught = None
        after = state()
        records[key] = {'error': caught, 'before': before, 'after': after}
        proven[key] = caught == error_type and before == after
    try:
        layers.extend(held)
        start = len(original)
        if symbol == 'LayerList.__getitem__':
            def lookup(key, index, expected, container=False):
                result = layers[index]
                got = list(result) if container else [result]
                records[key] = {'returned': [[identity(layer), _state(layer)] for layer in got],
                                'container': type(result).__name__, 'state': state()}
                proven[key] = (len(got) == len(expected) and all(a is b for a, b in zip(got, expected))
                               and (not container or (isinstance(result, LayerList) and result is not layers)))
            lookup('index', start, [held[0]])
            lookup('negative-index', -1, [held[-1]])
            lookup('name', held[0].name, [held[0]])
            held[1].name = '__h00_list_renamed'
            lookup('renamed-name', held[1].name, [held[1]])
            lookup('slice', slice(start, start + 3, 2), [held[0], held[2]], True)
            lookup('reverse', slice(None, None, -1), list(reversed(original + held)), True)
            lookup('empty', slice(start, start), [], True)
            reject('missing-name', lambda: layers['__h00_no_such_layer'], 'KeyError')
            reject('missing-index', lambda: layers[len(original) + 100], 'IndexError')
        else:
            emissions = []
            def emitted(event):
                payload = {'type': event.type, 'source_is_collection': event.source is layers}
                for field in ('index', 'new_index', 'value', 'old_value'):
                    if hasattr(event, field):
                        value = getattr(event, field)
                        if field in ('value', 'old_value'):
                            value = [identity(v) for v in value] if isinstance(value, (list, tuple)) else identity(value)
                        payload[field] = normalize(value)
                emissions.append(payload)
            layers.events.connect(emitted)
            try:
                def replace(key, index, value, expected, changed=True):
                    emissions.clear()
                    before = state()
                    layers[index] = value
                    actual = list(layers)
                    records[key] = {'before': before, 'after': state(), 'events': list(emissions)}
                    proven[key] = (len(actual) == len(expected) and all(a is b for a, b in zip(actual, expected))
                                   and (bool(emissions) if changed else not emissions))
                replace('integer', start, replacements[0], original + [replacements[0], *held[1:]])
                replace('same-object', start, replacements[0], original + [replacements[0], *held[1:]], False)
                replace('slice', slice(start, start + 2), replacements[1:], original + replacements[1:] + [held[2]])
                replace('extended-slice', slice(start, start + 3, 2), [held[0], held[1]], original + [held[0], replacements[2], held[1]])
                reject('duplicate', lambda: layers.__setitem__(start, held[1]), 'ValueError')
                reject('wrong-type', lambda: layers.__setitem__(start, 123), 'TypeError')
                reject('wrong-length', lambda: layers.__setitem__(slice(start, start + 3, 2), [replacements[0]]), 'ValueError')
            finally:
                layers.events.disconnect(emitted)
        return result
    finally:
        # Restore the session's exact original objects and order, not reconstructed copies.
        layers.clear()
        layers.extend(original)
        layers.selection.clear()
        layers.selection.update(selected)
        layers.selection.active = active
        viewer.dims.update(dims)
        viewer.camera.update(camera)
        result['restored'] = (state() == original_state and normalize(viewer.dims.dict()) == normalize(dims)
                              and normalize(viewer.camera.dict()) == normalize(camera))


LAYERLIST = {symbol: (lambda viewer, symbol=symbol: _layerlist_case(viewer, symbol)) for symbol in LAYERLIST_PROOFS}


CALLS = {
    # The session's viewer and the layers it holds
    'ViewerModel.open': lambda v: {'layers': _opened(v, lambda: v.open(_input_file(), plugin='napari'))},
    'ViewerModel.open_sample': lambda v: {'layers': _opened(v, lambda: v.open_sample('napari', 'balls_2d'))},
    'Layer.as_layer_data_tuple': lambda v: _holding(v, ['points'], lambda held: {'returned': normalize(held[0].as_layer_data_tuple())}),
    'Layer.create': lambda v: _holding(v, ['shapes'], lambda held: _created(v, held[0])),
    'Layer.save': lambda v: _written_and_read(v, ['w-layer.csv'], _holding(v, ['points'], lambda held: held[0].save('w-layer.csv', plugin='napari'))),
    'LayerList.save': lambda v: _written_and_read(v, ['w-list.csv'], _holding(v, ['points'], lambda held: _save_selected(v, held, 'w-list.csv'))),
    # Writers fed from layers the session's viewer holds, read back through that viewer
    **{f'napari_write_{kind}': (lambda v, kind=kind: _write_kind(v, kind)) for kind in ('image', 'labels', 'points', 'shapes')},
    'write_layer_data_with_plugins': lambda v: _written_and_read(v, ['w-folder'], _holding(v, ['image', 'points'], lambda held: __import__(
        'napari_builtins.io._write', fromlist=['x']).write_layer_data_with_plugins('w-folder', [layer.as_layer_data_tuple() for layer in held]))),
    'save_layers': lambda v: _written_and_read(v, ['w-save.tif'], _holding(v, ['image'], lambda held: __import__(
        'napari.plugins.io', fromlist=['x']).save_layers('w-save.tif', held, plugin='napari'))),
    'write_layers': lambda v: _written_and_read(v, ['w-npe2.tif'], _holding(v, ['image'], lambda held: _npe2().write_layers(
        'w-npe2.tif', held, plugin_name='napari'))),
    'file_extensions_string_for_layers': lambda v: _holding(v, ['points'], lambda held: {'returned': normalize(
        _npe2().file_extensions_string_for_layers(held))}),
    # The session's own plugin registry
    'read_data_with_plugins': lambda v: {'returned': normalize(__import__('napari.plugins.io', fromlist=['x']).read_data_with_plugins(
        [_input_file()], plugin='napari'))},
    'read': lambda v: {'returned': normalize(_npe2().read([_input_file()], plugin='napari', stack=False))},
    'get_readers': lambda v: {'returned': normalize(_npe2().get_readers(_input_file()))},
    'iter_manifests': lambda v: {'returned': sorted(m.name for m in _npe2().iter_manifests())},
    'widget_iterator': lambda v: {'returned': normalize(list(_npe2().widget_iterator()))},
    'sample_iterator': lambda v: {'returned': normalize([[name, sorted(samples)] for name, samples in _npe2().sample_iterator()])},
    'get_sample_data': lambda v: {'returned': normalize(_npe2().get_sample_data('napari', 'balls_2d')[0]())},
    'get_filename_patterns_for_reader': lambda v: {'returned': normalize(__import__(
        'napari.plugins.utils', fromlist=['x']).get_filename_patterns_for_reader('napari'))},
}


def _created(viewer, held):
    """A layer napari recreates from a held layer's layer-data tuple, added to the session's viewer beside it."""
    from napari.layers import Layer
    made = viewer.add_layer(Layer.create(*held.as_layer_data_tuple()))
    try:
        return {'state': _state(made), 'same_as_source': _state(made) == {**_state(held), 'name': _state(made).get('name')}}
    finally:
        viewer.layers.remove(made)


def _save_selected(viewer, held, name):
    kept = list(viewer.layers.selection)
    viewer.layers.selection.clear()
    viewer.layers.selection.update(held)
    try:
        return viewer.layers.save(name, selected=True, plugin='napari')
    finally:
        viewer.layers.selection.clear()
        viewer.layers.selection.update(kept)


def _paths(value, roots):
    if isinstance(value, str):
        for root in roots:
            value = value.replace(root, '<cwd>')
        return value
    if isinstance(value, list):
        return [_paths(v, roots) for v in value]
    if isinstance(value, dict):
        return {_paths(k, roots): _paths(v, roots) for k, v in value.items()}
    return value


TEMPLATES = {'compare-event': EVENTS, 'compare-attribute': ATTRIBUTES, 'compare-enum': ENUMS,
             'compare-contribution': CONTRIBUTIONS, 'compare-call': CALLS, 'compare-layerlist': LAYERLIST}


def recipes():
    """Every symbol each template has a recipe for: the bench rules list exactly these."""
    return {name: sorted(table) for name, table in TEMPLATES.items()}


def _enum_value(symbol, path):
    import importlib
    from pathlib import Path
    parts = Path(path).with_suffix('').parts
    parts = parts[1:-1] if parts[-1] == '__init__' else parts[1:]
    owner, leaf = symbol.split('.')
    member = getattr(getattr(importlib.import_module('.'.join(parts)), owner), leaf)
    return normalize(getattr(member, 'value', member))


def _listed(symbol):
    from npe2 import PluginManager
    kind, rest = symbol.split(':', 1)
    contributions = getattr(PluginManager.instance().get_manifest('napari').contributions, kind) or []
    return any(c.command == rest.split(':', 1)[0] for c in contributions)


def observe(template, symbol, viewer, path=None):
    """One case on this side: what happened, never a verdict."""
    try:
        with _warnings.catch_warnings():
            _warnings.simplefilter('ignore')
            if template == 'compare-event':
                return {'observed': _event(symbol, viewer)}
            if template == 'upstream-limitation':
                if viewer.dims.ndisplay != 2:
                    raise ValueError('The retained limitation requires a 2D view')
                event, recipe = LIMITATIONS[symbol]
                return {'observed': _event(event, viewer, recipe)}
            if template == 'compare-enum':
                return {'value': _enum_value(symbol, path), 'observed': ENUMS[symbol](viewer)}
            if template == 'compare-contribution':
                return {'listed': _listed(symbol), 'observed': CONTRIBUTIONS[symbol](viewer)}
            if template == 'compare-layerlist':
                # The bounded recipe normalizes native state/event leaves where captured.
                # Re-normalizing its enclosing report would erase deeper pixels and digests.
                return {'observed': LAYERLIST[symbol](viewer)}
            if template == 'compare-call':
                import os
                roots = sorted({os.getcwd(), os.path.realpath(os.getcwd())}, key=len, reverse=True)
                return {'observed': _paths(normalize(CALLS[symbol](viewer)), roots)}
            return {'observed': normalize(ATTRIBUTES[symbol](viewer))}
    except Exception as error:  # noqa: BLE001 - the exception is the observation
        return {'error': f'{type(error).__name__}: {str(error)[:300]}'}


def wheel_origin_hash(origin):
    """Read the prepared artifact's recorded digest; contradictory origins are not evidence."""
    from urllib.parse import parse_qs, urlsplit
    try:
        archive = origin.get('archive_info', {})
        digest = archive.get('hashes', {}).get('sha256')
        hashes = ([digest] if digest is not None else [])
        hashes += parse_qs(urlsplit(origin.get('url', '')).fragment, keep_blank_values=True).get('sha256', [])
        legacy = archive.get('hash')
        if legacy is not None:
            if not isinstance(legacy, str) or not legacy.startswith('sha256='):
                return None
            hashes.append(legacy.removeprefix('sha256='))
        return hashes[0] if hashes and all(isinstance(h, str) and h and h == hashes[0] for h in hashes) else None
    except (AttributeError, TypeError, ValueError):
        return None


def runtime(source_paths=()):
    """The whole runtime this side ran in: every installed distribution's RECORD digest, and napari's own files."""
    import json
    import sys
    from importlib import metadata
    from pathlib import Path
    import napari
    distributions = []
    for dist in metadata.distributions():
        record = dist.read_text('RECORD') or ''
        distributions.append([dist.metadata['Name'], dist.version, _hashlib.sha256(record.encode()).hexdigest()])
    files = _hashlib.sha256()
    root = Path(napari.__file__).parent
    for path in sorted(p for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts):
        files.update(str(path.relative_to(root)).encode() + b'\0' + _hashlib.sha256(path.read_bytes()).digest())
    listed = json.dumps(sorted(distributions)).encode()
    sources = {}
    for name in source_paths:
        path = root.parent / name.removeprefix('src/')
        sources[name] = _hashlib.sha256(path.read_bytes()).hexdigest()
    origin = json.loads(metadata.distribution('napari').read_text('direct_url.json') or '{}')
    wheel = wheel_origin_hash(origin)
    return {'python': sys.version.split()[0], 'napari': napari.__version__, 'napari_files': files.hexdigest(),
            'distributions': len(distributions), 'distributions_sha256': _hashlib.sha256(listed).hexdigest(),
            'versions': {name.lower().replace('_', '-'): version for name, version, _ in distributions},
            'napari_wheel': wheel,
            'pinned_sources_sha256': _hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()}



# The production-path browser harness reads the session's state through Studio's own Python console with these,
# and a reference process rebuilds the same layers from ``initial`` and replays the controls' upstream equivalents.

def raw(value):
    """A value as JSON that napari's own constructors and setters take back."""
    import enum
    import numpy as np
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(k): raw(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [raw(v) for v in value]
    if hasattr(value, 'dict') and callable(value.dict):
        return raw(value.dict())
    if type(value).__module__.startswith('pint'):
        return str(value)
    if type(value).__name__ == 'DataFrame':
        return {str(c): raw(value[c].to_numpy()) for c in value.columns}
    return value


def initial(names, viewer, with_data=()):
    """Each named layer as napari would recreate it (its layer-data tuple), the camera and the displayed dimensions."""
    import numpy as np
    out = {'layers': {}, 'camera': raw(viewer.camera.dict()), 'ndisplay': viewer.dims.ndisplay}
    for name in names:
        data, state, kind = viewer.layers[name].as_layer_data_tuple()
        parts = data if isinstance(data, (list, tuple)) else [data]
        kept = [{'dtype': np.asarray(part).dtype.str, 'values': raw(np.asarray(part))} for part in parts]
        out['layers'][name] = {'type': kind, 'state': raw(state), **({'data': kept} if name in with_data else {})}
    return out


def snapshot(names, viewer):
    """The compared state: each named layer's public state (data by digest) and the camera."""
    return {**{name: _state(viewer.layers[name]) for name in names}, 'camera': normalize(viewer.camera.dict())}


def apply_equivalent(op, viewer):
    """One control's upstream equivalent, through napari's public setters: set, update (evented parts) or camera."""
    kind = op[0]
    if kind == 'camera':
        # Match the public control transaction: orientation changes the camera
        # coordinate basis before angles are set; a model update batches differently.
        for name in ('orientation', 'perspective', 'center', 'angles', 'zoom'):
            setattr(viewer.camera, name, op[1][name])
        return
    if kind in ('image-presentation', 'surface-presentation'):
        # Replay the complete submitted transaction with independent napari
        # public setters. Unedited defaults were submitted too; replaying only
        # the touched control leaves a different native state on the reference.
        _, name, value = op
        layer = viewer.layers[name]
        if kind == 'image-presentation':
            if set(value) != {'contrastLimits', 'gamma', 'rendering', 'isoThreshold', 'attenuation', 'autoContrast'}:
                raise ValueError('Unknown image appearance fields in reference transaction')
            if not value['autoContrast']:
                layer.auto_contrast = False
                layer.contrast_limits = value['contrastLimits']
            layer.gamma = value['gamma']
            layer.rendering = value['rendering']
            layer.iso_threshold = value['isoThreshold']
            layer.attenuation = value['attenuation']
            if value['autoContrast']:
                layer.auto_contrast = True
        else:
            if set(value) != {'shading', 'gamma', 'contrastLimits', 'autoContrast', 'wireframe', 'faceNormals', 'vertexNormals'}:
                raise ValueError('Unknown surface appearance fields in reference transaction')
            layer.shading = value['shading']
            layer.gamma = value['gamma']
            layer.auto_contrast = False
            layer.contrast_limits = value['contrastLimits']
            layer.auto_contrast = value['autoContrast']
            for key, native in (('wireframe', layer.wireframe), ('faceNormals', layer.normals.face),
                                ('vertexNormals', layer.normals.vertex)):
                for field, setting in value[key].items():
                    setattr(native, field, setting)
        return
    if kind == 'ndisplay':
        viewer.dims.ndisplay = op[1]
        return
    _, name, path, value = op
    owner = viewer.layers[name]
    *parents, leaf = path.split('.')
    for part in parents:
        owner = getattr(owner, part)
    if kind == 'set':
        setattr(owner, leaf, value)
    else:
        getattr(owner, leaf).update(value)
