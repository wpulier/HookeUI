"""Ragged native shape coordinates with explicit boundaries, never a flat polygon."""
import math

from .native_regions import DTYPES, layout
from .scientific_structured_state import MAX_NODES

MAX_BYTES = 64 << 20
TYPES = frozenset(('rectangle', 'ellipse', 'line', 'path', 'polygon'))


def declarations(value):
    if type(value) is not dict or set(value) != {'vertices', 'offsets'}:
        raise ValueError('Shapes need native vertices and their exact shape boundaries')
    result = {}
    for name, row in value.items():
        if type(row) is not dict or set(row) != {'shape', 'dtype'} or type(row['shape']) is not list:
            raise ValueError('Shape components need exact native shapes and types')
        shape, dtype, _ = layout(row['shape'], row['dtype'], [str(i) for i in range(len(row['shape']))])
        result[name] = {'shape':list(shape), 'dtype':dtype}
    vertices, offsets = result['vertices'], result['offsets']
    if (len(vertices['shape']) != 2 or not 2 <= vertices['shape'][1] <= 8
            or vertices['dtype'][1] not in 'iuf' or len(offsets['shape']) != 1
            or not 1 <= offsets['shape'][0] <= MAX_NODES+1 or offsets['dtype'] != '<u8'):
        raise ValueError('Shapes require coordinate rows and bounded unsigned shape boundaries')
    if ((vertices['shape'][0]==0) != (offsets['shape'][0]==1)
            or vertices['shape'][0]==0 and vertices['dtype']!='<f8'):
        raise ValueError('An empty Shapes set needs no vertices and one zero boundary')
    if sum(math.prod(row['shape'])*DTYPES[row['dtype']] for row in result.values()) > MAX_BYTES:
        raise ValueError('These shapes need a bounded regional geometry writer')
    return result


def axes(name, ndim):
    if name not in ('vertices','offsets'): raise ValueError('Unknown shape component role')
    return ['vertex','coordinate'] if name == 'vertices' else ['shape-boundary']


def semantic(name):
    if name not in ('vertices','offsets'): raise ValueError('Unknown shape component role')
    return 'shape-' + name


def native(layer):
    import numpy as np
    if layer._type_string != 'shapes': raise ValueError('Choose a native Shapes layer')
    data, types = layer.data, layer.shape_type
    if type(data) is not list or not 0 <= len(data) <= MAX_NODES or len(types) != len(data):
        raise ValueError('Shapes require a bounded set of native geometries')
    # A native empty Shapes list has no vertex array or dtype to preserve. Its
    # wire representation is explicitly an empty float64 coordinate matrix; no
    # geometry, numeric value or object identity is fabricated.
    dtype = '<f8' if not data else None
    for array, kind in zip(data, types):
        if (type(array) is not np.ndarray or array.ndim != 2 or array.shape[1] != layer.ndim
                or kind not in TYPES or array.dtype.kind not in 'iuf' or not np.isfinite(array).all()):
            raise ValueError('Shapes need finite native coordinates and explicit geometry types')
        count = len(array)
        if count < (3 if kind=='polygon' else 2) or kind in ('rectangle','ellipse') and count != 4 or kind=='line' and count != 2:
            raise ValueError('A shape has incomplete native geometry')
        if dtype is None: dtype = array.dtype.str
        if array.dtype.str != dtype:
            raise ValueError('Mixed numeric types need an explicit geometry conversion; no coordinates were coerced')
    declarations({'vertices':{'shape':[sum(len(row) for row in data),layer.ndim],'dtype':dtype},
        'offsets':{'shape':[len(data)+1],'dtype':'<u8'}})
    return data


def describe(layer):
    rows = native(layer)
    return {'vertices':{'shape':[sum(len(row) for row in rows),layer.ndim],'dtype':rows[0].dtype.str if rows else '<f8'},
        'offsets':{'shape':[len(rows)+1],'dtype':'<u8'}}


def components(layer):
    import numpy as np
    from .scientific_layer_exports import quiescent
    rows = native(layer)
    for row in rows: quiescent(row)
    offsets = np.empty(len(rows)+1, dtype='<u8'); offsets[0] = 0
    np.cumsum([len(row) for row in rows], out=offsets[1:])
    vertices = np.concatenate(rows) if rows else np.empty((0,layer.ndim),dtype='<f8')
    current = native(layer)
    if len(current) != len(rows) or any(a is not b for a,b in zip(current,rows)):
        raise ValueError('Shapes changed during capture; private work was kept')
    return {'vertices':vertices, 'offsets':offsets}


def boundaries(offsets, types, vertex_count):
    """Validate geometry boundaries in the parent before accepting worker bytes."""
    import numpy as np
    if (type(offsets) is not np.ndarray or offsets.ndim != 1 or offsets.dtype.str != '<u8'
            or not 1 <= len(offsets) <= MAX_NODES+1 or len(types) != len(offsets)-1
            or offsets[0] != 0 or offsets[-1] != vertex_count
            or np.any(offsets[1:] <= offsets[:-1])):
        raise ValueError('Shape boundaries must cover their vertices exactly once')
    for count, kind in zip(np.diff(offsets), types):
        if (kind not in TYPES or count < (3 if kind == 'polygon' else 2)
                or kind in ('rectangle','ellipse') and count != 4 or kind == 'line' and count != 2):
            raise ValueError('Shape boundaries do not match the declared geometry types')


def split(arrays, types):
    import numpy as np
    declarations({name:{'shape':list(row.shape),'dtype':row.dtype.str} for name,row in arrays.items()})
    vertices, offsets = arrays['vertices'], arrays['offsets']
    boundaries(offsets, types, len(vertices))
    if not np.isfinite(vertices).all(): raise ValueError('Shapes require finite vertices')
    rows = [vertices[int(a):int(b)] for a,b in zip(offsets[:-1],offsets[1:])]
    from types import SimpleNamespace
    native(SimpleNamespace(_type_string='shapes',data=rows,shape_type=types,ndim=vertices.shape[1]))
    return rows
