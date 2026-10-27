"""Bounded exact-value inspection of the displayed native image/label slice."""
import re
from .scientific_view_wire import fields, require, token, vector, integer, bounded


def command(value):
    fields(value, 'op frame layer position')
    require(value['op'] == 'inspect' and token(value['frame'], 64) and token(value['layer']))
    vector(value['position'], 2)
    return value


def reply(value, request):
    if type(value) is dict and value.get('kind') == 'shapes':
        from .scientific_shape_inspection import reply as shape_reply
        return shape_reply(value, request)
    if type(value) is dict and value.get('kind') == 'points':
        from .scientific_point_inspection import reply as point_reply
        return point_reply(value, request)
    fields(value, 'frame layer world data axes units level projection dtype values'
           + (' labelFeatures' if type(value) is dict and 'labelFeatures' in value else ''))
    require(value['frame'] == request['frame'] and value['layer'] == request['layer'])
    for key in ('world', 'data'):
        require(type(value[key]) is list and 2 <= len(value[key]) <= 8)
        vector(value[key], len(value[key]))
    require(len(value['world']) == len(value['data']))
    for key in ('axes', 'units'):
        require(type(value[key]) is list and len(value[key]) == len(value['data'])
                and all(type(s) is str and len(s) <= 100 for s in value[key]))
    require(value['level'] is None or integer(value['level'], 0, 63))
    require(value['projection'] in ('none', 'sum', 'mean', 'max', 'min'))
    require(type(value['dtype']) is str and re.fullmatch(r'[<>=|][biuf][1248]', value['dtype']) is not None)
    values = value['values']
    require(values is None or (type(values) is list and len(values) in (1,3,4)
            and all(type(s) is str and len(s) <= 64 and re.fullmatch(r'(?:-?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?|nan|inf|-inf|True|False)', s) for s in values)))
    if 'labelFeatures' in value:
        from .scientific_feature_inspection import validate_features
        found = value['labelFeatures']
        fields(found, 'row features')
        require(values is not None and len(values) == 1 and re.fullmatch(r'-?[0-9]+', values[0]) is not None
                and (found['row'] is None or integer(found['row'], 0, 2**53 - 1)))
        validate_features(found['features'])
        require(found['row'] is not None or found['features'] == [])
        bounded(value, 32 << 10)
    else:
        bounded(value, 4096)
    return value


def label_features(layer, label):
    """The native feature row napari shows for one label: its 'index' column, else the row number."""
    from .scientific_feature_inspection import text, feature_value, dtype_label, validate_features
    table = layer.features
    if table.shape[1] == 0:
        return None
    if not table.columns.is_unique or len(table.columns) > 256:
        raise ValueError('Label features need unique column names within the inspection allowance')
    row = layer._label_index.get(label)
    if row is None:
        return {'row': None, 'features': []}
    features = []
    for name in table.columns:
        if not text(name, 256): raise ValueError('Features need bounded text column names')
        column = table[name]
        kind, content = feature_value(column.iloc[row])
        features.append({'name': name, 'dtype': dtype_label(column.dtype), 'kind': kind, 'value': content})
    return {'row': int(row), 'features': validate_features(features)}


def inspect(session, request):
    import numpy as np
    command(request)
    position = session.data_position(request['frame'], request['layer'], request['position'])
    layer = next(layer for identity, layer in session._layers if identity == request['layer'])
    if layer._type_string not in ('image', 'labels', 'points', 'shapes') or not layer.visible:
        raise ValueError('Choose a visible image, Labels, Points or Shapes layer in a single 2D view')
    world = [float(n) for n in layer.data_to_world(position)]
    if not layer.loaded:
        raise ValueError('This scientific slice is still loading. Wait for its completed preview')
    if layer._type_string in ('points', 'shapes'):
        if layer._type_string == 'points':
            from .scientific_point_inspection import hit
        else:
            from .scientific_shape_inspection import hit
        result = {'frame':request['frame'], 'layer':request['layer'], 'world':world,
                  'data':position, 'axes':[str(n) for n in layer.axis_labels],
                  'units':[str(n) for n in layer.units], 'kind':layer._type_string, 'hit':hit(layer, position)}
        session._current(request['frame'])
        return reply(result, request)
    level = int(layer.data_level) if layer.multiscale else None
    # Native empty slices contain a 1x1 zero placeholder, not scientific data.
    value = None if layer._slice.empty else layer.get_value(position)
    if layer.multiscale and value is not None:
        level, value = value
        level = int(level)
    # get_value reads the native rendered slice: projection, RGB and multiscale
    # behavior are upstream's, never a sample from display RGBA pixels.
    raw = layer._slice.image.raw
    dtype = np.dtype(raw.dtype)
    if dtype.kind not in 'biuf' or dtype.itemsize not in (1,2,4,8):
        raise ValueError('This native value type needs an inspection integration')
    values = None
    if value is not None:
        sample = np.asarray(value)
        if sample.size not in (1,3,4) or sample.ndim > 1:
            raise ValueError('This native sample exceeds the scalar/RGB inspection contract')
        values = [str(v.item()) for v in sample.reshape(-1)]
    result = {'frame':request['frame'], 'layer':request['layer'], 'world':world,
              'data':position, 'axes':[str(n) for n in layer.axis_labels],
              'units':[str(n) for n in layer.units], 'level':level,
              'projection':str(layer.projection_mode) if layer._type_string == 'image' else 'none',
              'dtype':dtype.str, 'values':values}
    if layer._type_string == 'labels' and values is not None and len(values) == 1:
        found = label_features(layer, int(np.asarray(value).item()))
        if found is not None:
            result['labelFeatures'] = found
    session._current(request['frame'])
    return reply(result, request)
