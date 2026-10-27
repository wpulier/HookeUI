"""Read-only native Points hit and feature values; no rounded scientific IDs."""
import re
from .scientific_view_wire import fields, require, vector, integer, bounded

from .scientific_feature_inspection import NUMBER, text, validate_features, feature_rows, feature_value, dtype_label


def reply(value, request):
    fields(value, 'frame layer world data axes units kind hit')
    require(value['frame'] == request['frame'] and value['layer'] == request['layer'] and value['kind'] == 'points')
    for key in ('world', 'data'):
        require(type(value[key]) is list and 2 <= len(value[key]) <= 8)
        vector(value[key], len(value[key]))
    require(len(value['world']) == len(value['data']))
    for key in ('axes', 'units'):
        require(type(value[key]) is list and len(value[key]) == len(value['data'])
                and all(text(s, 100) for s in value[key]))
    hit = value['hit']
    if hit is not None:
        fields(hit, 'index coordinates dtype features selected' + (' style' if type(hit) is dict and 'style' in hit else ''))
        if 'style' in hit:
            from .scientific_geometry_style import element_style
            element_style(hit['style'], 'points')
        require(type(hit['selected']) is bool)
        require(integer(hit['index'], 0, 2**53-1))
        require(type(hit['dtype']) is str and re.fullmatch(r'[<>=|][iuf][1248]', hit['dtype']) is not None)
        require(type(hit['coordinates']) is list and len(hit['coordinates']) == len(value['data'])
                and all(text(s, 64) and re.fullmatch(NUMBER, s) for s in hit['coordinates']))
        validate_features(hit['features'])
    bounded(value, 32 << 10)
    return value



def hit(layer, position):
    import numpy as np
    index = layer.get_value(position)
    if index is None: return None
    if not isinstance(index, (int, np.integer)) or isinstance(index, (bool, np.bool_)) or not 0 <= index < len(layer.data):
        raise ValueError('Native point picking returned an invalid row; refresh the preview')
    index = int(index)
    coordinates = np.asarray(layer.data[index])
    if coordinates.ndim != 1 or not 2 <= len(coordinates) <= 8 or coordinates.dtype.kind not in 'iuf':
        raise ValueError('This point coordinate type needs a native inspection integration')
    features = feature_rows(layer, index)
    from .scientific_geometry_style import element_snapshot
    return {'index': index, 'selected': index in layer.selected_data, 'coordinates': [str(n) for n in coordinates],
            'dtype': coordinates.dtype.str, 'features': features, 'style': element_snapshot(layer, index)}
