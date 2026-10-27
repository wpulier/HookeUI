"""Data-only native Surface state, independent of its bulk mesh components."""
from . import scientific_structured_state as state
from . import scientific_layer_colors as colors
from . import scientific_surface_data as mesh

SETTINGS = frozenset(('affine', 'axis_labels', 'blending', 'experimental_clipping_planes',
    'metadata', 'name', 'opacity', 'projection_mode', 'rotate', 'scale', 'shear', 'translate',
    'units', 'visible', 'auto_contrast', 'colormap', 'contrast_limits', 'gamma', 'shading',
    'features', 'feature_defaults', 'wireframe', 'normals'))


def validate(value, rows):
    state.validate(value)
    root = value['state']
    if root['type'] != 'dict' or set(root['value']) != SETTINGS:
        raise ValueError('A retained surface needs complete native settings and typed features')
    for key, count in (('features', rows), ('feature_defaults', 1)):
        table = root['value'][key]
        if table['type'] != 'table' or state.length(table['index']) != count:
            raise ValueError('Surface features must correspond to their vertices')
    if ({row['name'] for row in root['value']['features']['columns']} != {row['name'] for row in root['value']['feature_defaults']['columns']}):
        raise ValueError('Surface feature defaults must match the feature columns before saving')
    colors.validate_native_colormap(root['value']['colormap'])
    from .scientific_surface_appearance import retained
    retained(root['value'])
    return value


def snapshot(layer):
    arrays = mesh.components(layer)
    if layer.ndim != arrays['vertices'].shape[1]:
        raise ValueError('Surface value axes need an explicit coordinate integration')
    if len(layer.features.columns) > 256:
        raise ValueError('These surface features need their bounded structured writer')
    if layer.colormap.colors.nbytes + layer.colormap.controls.nbytes > state.MAX_ARRAY_BYTES:
        raise ValueError('This surface palette exceeds the bounded state allowance')
    _, settings, _ = layer.as_layer_data_tuple()
    settings = dict(settings)
    for name in mesh.OPTIONAL:
        settings.pop(name)
    settings['units'] = tuple(str(unit) for unit in layer.units)
    controls = colors.normalizer()
    for name in settings:
        if name not in ('metadata', 'features', 'feature_defaults'):
            settings[name] = controls(settings[name])
    return validate(state.pack(settings), len(arrays['vertices']))


def restored(arrays, value):
    from napari.layers import Surface
    validate(value, len(arrays['vertices']))
    settings = state.unpack(value)
    # Native normal setters normalize their argument dictionaries. Unpack gives
    # this construction a private copy; accepted metadata is never modified.
    layer = Surface(tuple(arrays[name] for name in ('vertices', 'faces', 'values')),
        **settings, **{name: arrays[name] for name in mesh.OPTIONAL if name in arrays})
    result = mesh.components(layer)
    if (set(result) != set(arrays) or any(result[name].dtype.str != data.dtype.str
            or result[name].shape != data.shape or result[name].tobytes() != data.tobytes()
            for name, data in arrays.items()) or snapshot(layer) != value):
        raise ValueError('The native host changed surface data or appearance; accepted work was kept')
    return layer
