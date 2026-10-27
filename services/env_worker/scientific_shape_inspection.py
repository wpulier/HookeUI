"""Native read-only shape hit inspection with explicit geometry boundaries."""
import re
import math
from .scientific_view_wire import fields, require, vector, integer, bounded
from .scientific_shape_data import TYPES
from .scientific_feature_inspection import NUMBER, text, validate_features, feature_rows
from .scientific_geometry_style import element_snapshot

MAX_VERTICES = 256


def reply(value, request):
    fields(value, 'frame layer world data axes units kind hit')
    require(value['kind']=='shapes' and value['frame']==request['frame'] and value['layer']==request['layer'])
    for key in ('world','data'):
        require(type(value[key]) is list and 2 <= len(value[key]) <= 8)
        vector(value[key],len(value[key]))
    require(len(value['world'])==len(value['data']))
    for key in ('axes','units'):
        require(type(value[key]) is list and len(value[key])==len(value['data']) and all(text(s,100) for s in value[key]))
    hit=value['hit']
    if hit is not None:
        fields(hit,'index geometry vertices dtype features selected'+(' style' if type(hit) is dict and 'style' in hit else ''))
        if 'style' in hit:
            from .scientific_geometry_style import element_style
            element_style(hit['style'],'shapes')
        require(type(hit['selected']) is bool)
        require(integer(hit['index'],0,2**53-1) and type(hit['geometry']) is str and hit['geometry'] in TYPES)
        require(type(hit['dtype']) is str and re.fullmatch(r'[<>=|][iuf][1248]',hit['dtype']) is not None)
        vertices=hit['vertices']
        require(type(vertices) is list and (3 if hit['geometry']=='polygon' else 2) <= len(vertices) <= MAX_VERTICES)
        require(hit['geometry'] not in ('rectangle','ellipse') or len(vertices)==4)
        require(hit['geometry']!='line' or len(vertices)==2)
        require(all(type(row) is list and len(row)==len(value['data']) and all(text(n,64) and re.fullmatch(NUMBER,n) and math.isfinite(float(n)) for n in row) for row in vertices))
        validate_features(hit['features'])
    bounded(value,64<<10)
    return value


def hit(layer, position):
    import numpy as np
    picked=layer.get_value(position)
    if type(picked) is not tuple or len(picked)!=2:
        raise ValueError('Native shape picking returned an invalid result; refresh the preview')
    index,_handle=picked
    if index is None:return None
    if not isinstance(index,(int,np.integer)) or isinstance(index,(bool,np.bool_)) or not 0 <= index < layer.nshapes:
        raise ValueError('Native shape picking returned an invalid row; refresh the preview')
    index=int(index)
    # Native handle indices may name selection-box handles rather than vertices.
    # Report the chosen geometry itself, never reinterpret a handle as a vertex.
    vertices=layer.data[index]
    geometry=layer.shape_type[index]
    if (type(vertices) is not np.ndarray or vertices.ndim!=2 or not 2 <= vertices.shape[1] <= 8
            or vertices.shape[0] > MAX_VERTICES or vertices.dtype.kind not in 'iuf' or not np.isfinite(vertices).all()):
        raise ValueError('This geometry exceeds the 256-vertex inspection allowance or needs a native integration; saved geometry is unchanged')
    return {'selected':index in layer.selected_data,'index':index,'geometry':geometry,'vertices':[[str(n) for n in row] for row in vertices],
            'dtype':vertices.dtype.str,'features':feature_rows(layer,index),'style':element_snapshot(layer,index)}
