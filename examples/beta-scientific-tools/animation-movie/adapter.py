"""Private napari-animation integration through the scientific-host interface.

This module is contributed code, not part of Hooke's runtime authority. It uses
the actual upstream widget and interpolation. Only explicit, data-only state is
saved; Python objects and arbitrary class names never cross the document boundary.
Frame selection uses the public sequence interface; Hooke owns managed movie export.
"""
import base64
import copy
from enum import Enum
from itertools import count, product
import math

import numpy as np

MAX_ARRAY_BYTES = 32 << 10
MAX_FRAMES = 128
MAX_MOVIE_FRAMES = 4096


def feature_arrays(index, columns):
    if (type(index) is not np.ndarray or index.ndim != 1
            or any(type(item) is not np.ndarray or item.ndim != 1 or len(item) != len(index) for item in columns)
            or index.nbytes + sum(item.nbytes for item in columns) > MAX_ARRAY_BYTES):
        raise ValueError('Animation feature columns must be bounded arrays matching their index')


def encode(value, depth=0):
    """Closed, bounded representation of the pinned plugin's viewer settings."""
    if depth > 20:
        raise ValueError('Animation settings are too deeply nested to save')
    recurse = lambda item: encode(item, depth + 1)
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError('Animation settings must contain finite numbers')
        return value
    if isinstance(value, Enum) and type(value.value) in (str, int):
        return recurse(value.value)
    if isinstance(value, np.generic):
        return recurse(value.item())
    if isinstance(value, np.ndarray) and type(value) is not np.ndarray:
        from napari.utils.color import ColorArray, ColorValue
        if type(value) in (ColorArray, ColorValue):
            value = np.asarray(value)
    if type(value) is np.ndarray:
        if (value.dtype.kind not in 'biufSU' or value.ndim > 8
                or value.nbytes > MAX_ARRAY_BYTES):
            raise ValueError('This animation setting needs a declared bounded data representation')
        return {'kind': 'array', 'dtype': value.dtype.str, 'shape': list(value.shape),
                'bytes': base64.b64encode(value.tobytes(order='C')).decode('ascii')}
    if type(value) in (list, tuple):
        if len(value) > 4096:
            raise ValueError('Animation settings have too many entries')
        return {'kind': 'tuple' if type(value) is tuple else 'list', 'items': [recurse(item) for item in value]}
    if type(value) is dict:
        if len(value) > 4096:
            raise ValueError('Animation settings have too many entries')
        return {'kind': 'mapping', 'items': [[recurse(key), recurse(item)] for key, item in value.items()]}
    from napari.utils._units import get_unit_registry
    registry = get_unit_registry()
    if isinstance(value, registry.Unit):
        return {'kind': 'unit', 'value': str(value)}
    from napari.utils.colormaps.colormap import Colormap, CyclicLabelColormap, DirectLabelColormap
    for name, cls in (('colormap', Colormap), ('cyclic-labels', CyclicLabelColormap),
                      ('direct-labels', DirectLabelColormap)):
        if type(value) is cls:
            return {'kind': name, 'value': recurse(value.model_dump())}
    import pandas as pd
    if type(value) is pd.DataFrame:
        if len(set(value.columns)) != len(value.columns):
            raise ValueError('Animation feature column names must be unique')
        index = value.index.to_numpy()
        columns = [value[column].to_numpy() for column in value.columns]
        feature_arrays(index, columns)
        return {'kind': 'features', 'columns': recurse(tuple(value.columns)),
                'index': recurse(index), 'values': [recurse(column) for column in columns]}
    raise ValueError(f'Animation state type {type(value).__name__} has no declared persistence mapping')


def decode(value, depth=0):
    """Restore only known setting types; never import a name from saved input."""
    if depth > 20:
        raise ValueError('Animation settings are too deeply nested to reopen')
    recurse = lambda item: decode(item, depth + 1)
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) is not dict or type(value.get('kind')) is not str:
        raise ValueError('Invalid animation setting')
    kind = value['kind']
    expected = {'array': {'dtype', 'shape', 'bytes'}, 'tuple': {'items'}, 'list': {'items'},
                'mapping': {'items'}, 'unit': {'value'}, 'colormap': {'value'},
                'cyclic-labels': {'value'}, 'direct-labels': {'value'},
                'features': {'columns', 'index', 'values'}}
    if kind not in expected or set(value) != expected[kind] | {'kind'}:
        raise ValueError('This animation setting is unsupported or incomplete')
    if kind == 'array':
        if (type(value['dtype']) is not str or len(value['dtype']) > 12
                or type(value['shape']) is not list or len(value['shape']) > 8
                or any(type(n) is not int or not 0 <= n <= MAX_ARRAY_BYTES for n in value['shape'])
                or type(value['bytes']) is not str or len(value['bytes']) > 4 * (MAX_ARRAY_BYTES + 2) // 3):
            raise ValueError('Invalid animation array setting')
        dtype = np.dtype(value['dtype'])
        size = math.prod(value['shape']) * dtype.itemsize
        if dtype.kind not in 'biufSU' or not dtype.itemsize or size > MAX_ARRAY_BYTES:
            raise ValueError('Animation array settings exceed their declared allowance')
        raw = base64.b64decode(value['bytes'], validate=True)
        if len(raw) != size:
            raise ValueError('Animation array setting has incomplete values')
        return np.frombuffer(raw, dtype=dtype).reshape(value['shape']).copy()
    if kind in ('tuple', 'list', 'mapping'):
        items = value['items']
        if type(items) is not list or len(items) > 4096:
            raise ValueError('Invalid animation setting entries')
        if kind == 'mapping':
            result = {}
            for pair in items:
                if type(pair) is not list or len(pair) != 2:
                    raise ValueError('Invalid animation mapping entry')
                key = recurse(pair[0])
                if key in result:
                    raise ValueError('Animation setting contains duplicate keys')
                result[key] = recurse(pair[1])
            return result
        result = [recurse(item) for item in items]
        return tuple(result) if kind == 'tuple' else result
    if kind == 'unit':
        if type(value['value']) is not str or len(value['value']) > 128:
            raise ValueError('Invalid animation unit')
        from napari.utils._units import get_unit_registry
        return get_unit_registry().Unit(value['value'])
    if kind == 'features':
        import pandas as pd
        columns, index = recurse(value['columns']), recurse(value['index'])
        rows = value['values']
        if (type(columns) is not tuple or len(set(columns)) != len(columns)
                or type(index) is not np.ndarray or index.ndim != 1
                or type(rows) is not list or len(rows) != len(columns)):
            raise ValueError('Invalid animation feature table')
        data = [recurse(item) for item in rows]
        feature_arrays(index, data)
        return pd.DataFrame(dict(zip(columns, data)), index=index)
    from napari.utils.colormaps.colormap import Colormap, CyclicLabelColormap, DirectLabelColormap
    cls = {'colormap': Colormap, 'cyclic-labels': CyclicLabelColormap, 'direct-labels': DirectLabelColormap}[kind]
    settings = recurse(value['value'])
    if type(settings) is not dict:
        raise ValueError('Invalid animation color settings')
    return cls(**settings)


def bind_animation_document(context, widget):
    """Explicit integration-owned persistence; no host-specific dispatch branch."""
    from napari_animation.easing import Easing
    from napari_animation.key_frame import KeyFrame
    from napari_animation.viewer_state import ViewerState
    from napari_animation.utils import make_thumbnail
    viewer, animation = context.viewer, widget.animation

    def viewer_state(value):
        return encode({'camera': value.camera, 'dims': value.dims, 'layers': value.layers})

    def read_state(value):
        decoded = decode(value)
        if type(decoded) is not dict or set(decoded) != {'camera', 'dims', 'layers'}:
            raise ValueError('The animation needs complete camera, dimensions and layer settings')
        if set(decoded['layers']) != {layer.name for layer in viewer.layers}:
            raise ValueError('Animation layer names changed; restore matching layers before reopening')
        return ViewerState(**decoded)

    def capture():
        frames = list(animation.key_frames)
        if len(frames) > MAX_FRAMES or len(animation._frames) > MAX_MOVIE_FRAMES:
            raise ValueError('The animation exceeds this integration\'s declared keyframe or movie allowance')
        for frame in frames:
            if (type(frame.steps) is not int or not 1 <= frame.steps <= MAX_MOVIE_FRAMES
                    or not isinstance(frame.ease, Easing) or type(frame.name) is not str or len(frame.name) > 256):
                raise ValueError('Use a supported easing and a positive bounded frame duration')
            if set(frame.viewer_state.layers) != {layer.name for layer in viewer.layers}:
                raise ValueError('A keyframe refers to renamed or removed layers; correct it before saving')
        counter = next(copy.copy(animation._keyframe_counter))
        if type(counter) is not int or not 0 <= counter <= 2**31 - 1:
            raise ValueError('The animation name counter exceeds its declared allowance')
        return {'keyframes': [{'name': frame.name, 'steps': frame.steps, 'ease': frame.ease.name,
                               'view': viewer_state(frame.viewer_state)} for frame in frames],
                'current': viewer_state(ViewerState.from_viewer(viewer)),
                'active': next((i for i, frame in enumerate(frames) if frame is animation.key_frames.selection.active), None),
                'selected': [i for i, frame in enumerate(frames) if frame in animation.key_frames.selection],
                'counter': counter,
                'controls': {'steps': widget.frameWidget.stepsSpinBox.value(),
                             'ease': widget.frameWidget.get_easing_func().name,
                             'preview': widget.animationSlider.value()}}

    def restore(saved):
        if type(saved) is not dict or set(saved) != {'keyframes', 'current', 'active', 'selected', 'counter', 'controls'}:
            raise ValueError('The saved animation is incomplete')
        rows = saved['keyframes']
        if (type(rows) is not list or len(rows) > MAX_FRAMES or type(saved['counter']) is not int
                or not 0 <= saved['counter'] <= 2**31 - 1 or type(saved['selected']) is not list
                or any(type(i) is not int or not 0 <= i < len(rows) for i in saved['selected'])
                or len(set(saved['selected'])) != len(saved['selected'])
                or saved['active'] is not None and (type(saved['active']) is not int or not 0 <= saved['active'] < len(rows))):
            raise ValueError('The saved animation selection or size is invalid')
        prepared = []
        for row in rows:
            if (type(row) is not dict or set(row) != {'name', 'steps', 'ease', 'view'}
                    or type(row['name']) is not str or len(row['name']) > 256
                    or type(row['steps']) is not int or not 1 <= row['steps'] <= MAX_MOVIE_FRAMES
                    or type(row['ease']) is not str or row['ease'] not in Easing.__members__):
                raise ValueError('A saved keyframe is invalid')
            prepared.append((row, read_state(row['view'])))
        if sum(row['steps'] for row in rows[1:]) + bool(rows) > MAX_MOVIE_FRAMES:
            raise ValueError('The saved animation exceeds its movie allowance')
        controls = saved['controls']
        if (type(controls) is not dict or set(controls) != {'steps', 'ease', 'preview'}
                or type(controls['steps']) is not int or not 1 <= controls['steps'] <= 100000
                or type(controls['ease']) is not str or controls['ease'] not in Easing.__members__
                or type(controls['preview']) is not int or not 0 <= controls['preview'] < max(1, sum(row['steps'] for row in rows[1:]) + bool(rows))):
            raise ValueError('The saved animation controls are invalid')
        current = read_state(saved['current'])
        animation.key_frames.clear()
        try:
            for row, state in prepared:
                thumbnail = make_thumbnail(state.render(viewer))
                frame = KeyFrame(state, thumbnail, steps=row['steps'], ease=Easing[row['ease']], name=row['name'])
                animation.key_frames.append(frame)
            # Let upstream update the native list's selection and active controls.
            # Its preview callback may move the view; the saved current view is
            # restored below, independently from the selected keyframe.
            animation.key_frames.selection.clear()
            animation.key_frames.selection.update(animation.key_frames[i] for i in saved['selected'])
            animation.key_frames.selection.active = (animation.key_frames[saved['active']]
                                                     if saved['active'] is not None else None)
            animation._keyframe_counter = count(saved['counter'])
            # Sync controls without replaying callbacks or changing keyframes.
            from qtpy.QtCore import QSignalBlocker
            for control, value, setter in (
                    (widget.frameWidget.stepsSpinBox, controls['steps'], 'setValue'),
                    (widget.frameWidget.easeComboBox, controls['ease'].lower(), 'setCurrentText'),
                    (widget.animationSlider, controls['preview'], 'setValue')):
                blocker = QSignalBlocker(control)
                try: getattr(control, setter)(value)
                finally: blocker.unblock()
            animation._frames._current_index = controls['preview']
        finally:
            current.apply(viewer)

    context.bind_document(schema='hooke.napari-animation-0.0.10', version=1, capture=capture, restore=restore)
    # Recipe camera motion applies to the new accepted scene. Layer appearance
    # stays with that scene instead of importing another result's display state.
    def recipe_capture():
        state=capture()
        def motion(value):
            decoded=decode(value)
            return encode({'camera':decoded['camera'],'dims':decoded['dims']})
        state['current']=motion(state['current'])
        for frame in state['keyframes']:frame['view']=motion(frame['view'])
        return state
    def recipe_configure(state):
        state=copy.deepcopy(state)
        layers=ViewerState.from_viewer(viewer).layers
        def scene_motion(value):
            decoded=decode(value)
            if type(decoded) is not dict or set(decoded)!={'camera','dims'}:
                raise ValueError('Animation recipe requires declared camera and dimensions')
            return encode({**decoded,'layers':layers})
        state['current']=scene_motion(state['current'])
        for frame in state['keyframes']:frame['view']=scene_motion(frame['view'])
        restore(state)
    context.bind_recipe(schema='hooke.napari-animation.camera-recipe',version=1,
                        capture=recipe_capture,configure=recipe_configure)



def admit_sources(inputs):
    """Refuse an over-budget composition before allocating its first array."""
    if sum(math.prod(resource.shape) * np.dtype(resource.dtype).itemsize
           for resource in inputs.values()) > 64 << 20:
        raise ValueError('Animation inputs exceed this candidate\'s 64 MiB allowance')
    for resource in inputs.values():
        descriptor = resource.descriptor
        if descriptor.get('semantic') not in ('intensity', 'instance-labels', 'class-labels'):
            raise ValueError('This candidate requires declared intensity or label resources')
        calibration = descriptor.get('calibration')
        if calibration is not None and calibration['axes'] != list(resource.axes):
            raise ValueError('This animation candidate needs calibration covering the ordered input axes')


def open_host(context):
    if not callable(getattr(context, "bind_recipe", None)):
        raise ValueError("Update Hooke to support versioned recipe settings before opening this candidate")
    """Use actual upstream controls against explicitly retained native resources."""
    from importlib.metadata import version
    from magicgui.widgets import Container
    from napari_animation._qt import AnimationWidget
    if version('napari-animation') != '0.0.10':
        raise ValueError('This private integration requires napari-animation 0.0.10')
    if context.restored_layers is None:
        admit_sources(context.inputs)
        for key, resource in context.inputs.items():
            semantic = resource.descriptor.get('semantic')
            data = np.empty(resource.shape, dtype=resource.dtype)
            chunks = list(resource.shape)
            while math.prod(chunks) * data.itemsize > 1 << 20:
                axis = max(range(len(chunks)), key=chunks.__getitem__)
                chunks[axis] = max(1, chunks[axis] // 2)
            for starts in product(*(range(0, n, chunk) for n, chunk in zip(resource.shape, chunks))):
                bounds = [[start, min(start + chunk, n)] for start, chunk, n in zip(starts, chunks, resource.shape)]
                data[tuple(slice(*pair) for pair in bounds)] = resource.read(bounds)
            kwargs = {'axis_labels': resource.axes}
            calibration = resource.descriptor.get('calibration')
            if calibration is not None:
                affine = np.eye(data.ndim + 1)
                affine[:data.ndim] = calibration['affine']
                kwargs.update(affine=affine, units=calibration['units'])
            add = context.viewer.add_image if semantic == 'intensity' else context.viewer.add_labels
            layer = add(data, name=key, **kwargs)
            context.bind_editable(layer=layer, input=key)
            context.bind_view(layer=layer, key=key)
    class PlaybackWidget(AnimationWidget):
        def _nframes_changed(self, event):
            # Upstream 0.0.10 leaves the slider blocked after a frame-count change.
            # Own only this update's block; preserve an outer caller's prior state.
            from qtpy.QtCore import QSignalBlocker
            has_frames = bool(event.value)
            self.animationSlider.setEnabled(has_frames)
            blocker = QSignalBlocker(self.animationSlider)
            try:
                self.animationSlider.setMaximum(event.value - 1 if has_frames else 0)
            finally:
                blocker.unblock()

    widget = PlaybackWidget(context.viewer)
    # The upstream file dialog has no scoped resource destination in this host.
    # Keep that unfinished action visible; never launch an external desktop UI.
    widget.saveButton.clicked.disconnect()
    widget.saveButton.setText('Export the saved movie from Hooke’s Export section')
    widget.saveButton.setToolTip('Save tool and all layers preserves editable keyframes. Open the saved document and choose Export saved movie in its Export section.')
    def refuse_export():
        raise ValueError('Export the saved movie from Hooke’s Export section; save the editable tool document in Hooke meanwhile')
    widget.saveButton.clicked.connect(refuse_export)
    bind_animation_document(context, widget)
    context.bind_sequence(version=1, name='napari-animation',
                          count=lambda: len(widget.animation._frames),
                          select=widget.animation.set_movie_frame_index)
    context.bind_native_panel(widget)
    # Public Python inspection has the real upstream animation object.
    context.viewer._hooke_animation = widget.animation
    return Container()
