"""Native mesh components: vertices, topology and values remain separate arrays.

This writer admits static meshes. Value-series surfaces need a declared mapping
from their extra axes to the scientific coordinate space before publication.
"""
import math

from .native_regions import DTYPES, layout

REQUIRED = frozenset(('vertices', 'faces', 'values'))
OPTIONAL = frozenset(('texture', 'texcoords', 'vertex_colors'))
MAX_BYTES = 64 << 20


def declarations(value):
    if type(value) is not dict or not REQUIRED <= set(value) <= REQUIRED | OPTIONAL:
        raise ValueError('A surface needs its vertices, triangle faces and vertex values together')
    result = {}
    total = 0
    for name, row in value.items():
        if (type(row) is not dict or set(row) != {'shape', 'dtype'}
                or type(row['shape']) is not list or not 1 <= len(row['shape']) <= 8):
            raise ValueError('Every surface component needs its exact native shape and dtype')
        shape, dtype, _ = layout(row['shape'], row['dtype'], [str(i) for i in range(len(row['shape']))])
        if dtype[1] not in 'uif':
            raise ValueError('Surface components require native real numeric values')
        total += math.prod(shape) * DTYPES[dtype]
        result[name] = {'shape': list(shape), 'dtype': dtype}
    vertices, faces, values = (result[name] for name in ('vertices', 'faces', 'values'))
    if len(vertices['shape']) != 2 or not 2 <= vertices['shape'][1] <= 8:
        raise ValueError('Surface vertices need rows of scientific coordinates')
    if len(faces['shape']) != 2 or faces['shape'][1] != 3 or faces['dtype'][1] not in 'iu':
        raise ValueError('Surface faces need three integer vertex indices per triangle')
    count = vertices['shape'][0]
    if values['shape'] != [count]:
        raise ValueError('Save static vertex values here; value-series surfaces need their extra axes declared by a surface integration')
    if 'texcoords' in result and result['texcoords']['shape'] != [count, 2]:
        raise ValueError('Surface texture coordinates must match each vertex')
    if 'vertex_colors' in result and result['vertex_colors']['shape'] not in ([count, 3], [count, 4]):
        raise ValueError('Surface colors must match each vertex')
    if 'texture' in result and (len(result['texture']['shape']) not in (2, 3)
            or len(result['texture']['shape']) == 3 and result['texture']['shape'][-1] not in (3, 4)):
        raise ValueError('Surface textures need a native image with optional RGB or RGBA channels')
    if total > MAX_BYTES:
        raise ValueError('This surface exceeds the bounded native save allowance; a regional mesh writer is required')
    return result


def semantic(name):
    if name not in REQUIRED | OPTIONAL: raise ValueError('Unknown surface component role')
    return 'surface-' + name.replace('_', '-')


def axes(name, ndim):
    fixed = {'vertices': ['vertex', 'coordinate'], 'faces': ['face', 'corner'],
        'values': ['vertex'], 'texcoords': ['vertex', 'texture-coordinate'],
        'vertex_colors': ['vertex', 'color-channel']}
    return fixed[name] if name in fixed else ['texture-y', 'texture-x', 'color-channel'][:ndim]


def describe(layer):
    return {name: {'shape': list(array.shape), 'dtype': array.dtype.str}
            for name, array in components(layer).items()}


def components(layer):
    import numpy as np
    if layer._type_string != 'surface':
        raise ValueError('Choose a native Surface layer')
    result = dict(zip(('vertices', 'faces', 'values'), layer.data))
    for name in OPTIONAL:
        value = getattr(layer, name)
        if value is not None:
            result[name] = value
    if any(type(array) is not np.ndarray for array in result.values()):
        raise ValueError('This surface needs its declared lazy or external component writer')
    declarations({name: {'shape': list(array.shape), 'dtype': array.dtype.str} for name, array in result.items()})
    vertices, faces = result['vertices'], result['faces']
    if not np.isfinite(vertices).all() or np.any(faces < 0) or np.any(faces >= len(vertices)):
        raise ValueError('Surface faces must reference existing finite vertices')
    # Missing/non-finite measured values retain their original scientific meaning.
    # They are not silently replaced with a color or coerced into coordinates.
    return result
