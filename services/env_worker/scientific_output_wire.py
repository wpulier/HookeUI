"""Versioned declarations for retained native output layers, never execution state."""
import copy
import math

from .native_regions import layout, DTYPES, IDENTITY
from .scientific_calibration import same_calibration

FORMAT = 'native-layer-output-v1'
RGB_FORMAT = 'native-rgb-output-v1'
POINTS_FORMAT = 'native-points-output-v1'
TRACKS_FORMAT = 'native-tracks-output-v1'
VECTORS_FORMAT = 'native-vectors-output-v1'
SURFACE_FORMAT = 'native-surface-output-v1'
SHAPES_FORMAT = 'native-shapes-output-v1'
COMPOUND = {'surface':SURFACE_FORMAT, 'shapes':SHAPES_FORMAT}

def component_contract(kind):
    if kind == 'surface':
        from . import scientific_surface_data
        return scientific_surface_data
    if kind == 'shapes':
        from . import scientific_shape_data
        return scientific_shape_data
    raise ValueError('This layer has no declared component contract')

STRUCTURED = {'points': POINTS_FORMAT, 'tracks': TRACKS_FORMAT,'vectors':VECTORS_FORMAT, **COMPOUND}
SEMANTICS = {'intensity': 'image', 'probabilities': 'image',
             'class-labels': 'labels', 'instance-labels': 'labels', 'point-set':'points', 'track-coordinates':'tracks','vector-set':'vectors', 'surface-mesh':'surface', 'shape-set':'shapes'}


def selections(value, *, minimum=2):
    """An explicit ordered set, never inferred from layer names or current focus."""
    import re
    if (type(value) is not list or not minimum <= len(value) <= 8
            or any(type(row) is not dict or set(row) != {'layer','meaning'}
                   or type(row['layer']) is not str or not re.fullmatch('[0-9a-f]{32}',row['layer']) for row in value)
            or len({row['layer'] for row in value}) != len(value)):
        raise ValueError(f'Choose {minimum} to eight distinct layers and declare the meaning of each')
    return [{'layer':row['layer'],'meaning':declaration(row['meaning'])} for row in value]


def declaration(value):
    if (type(value) is not dict or set(value) != {'semantic', 'axes'}
            or type(value['semantic']) is not str or value['semantic'] not in SEMANTICS
            or type(value['axes']) is not list or not 2 <= len(value['axes']) <= 8
            or any(type(axis) is not str or not IDENTITY.fullmatch(axis) for axis in value['axes'])
            or len(set(value['axes'])) != len(value['axes'])):
        raise ValueError('Choose the scientific meaning and one distinct axis name per dimension')
    return copy.deepcopy(value)


# Ordinary publication keeps its 1 GiB aggregate allowance. Immutable regional grey
# images declare exact source identities for a separate parent-admitted streamed allowance.
# Other new outputs keep their 64 MiB limit. Accepted layers may be as large as their store allows;
# images open lazily (env_worker.lazy_arrays, H08), and restore refuses unsupported editing sizes.
MAX_NEW_IMAGE_BYTES = 1 << 30
MAX_STREAMED_BYTES = 64 << 30


def transfer_seconds(size):
    from .native_regions import MAX_VIEW_BYTES, VIEW_WINDOW_S
    if type(size) is not int or not 0 <= size <= MAX_STREAMED_BYTES:
        raise ValueError('Streamed publication needs a bounded declared byte count')
    return 60 if size <= MAX_NEW_IMAGE_BYTES else 60 + 2 * math.ceil(size / MAX_VIEW_BYTES) * VIEW_WINDOW_S

MAX_RETAINED_BYTES = 64 << 30
MAX_LEVELS = 16


def metadata(value, *, retained=False):
    fields = {'format', 'shape', 'dtype', 'name', 'type', 'geometry', 'visible', 'opacity'}
    if type(value) is dict and 'levels' in value: fields.add('levels')
    if type(value) is dict and 'regionalSources' in value:
        from gpu_worker.semantic_resources import parse_reference
        refs = value['regionalSources']
        if (value.get('format') != FORMAT or value.get('type') != 'image'
                or type(refs) is not list or not 1 <= len(refs) <= MAX_LEVELS):
            raise ValueError('Streamed images require their immutable regional source identities')
        for ref in refs:
            if parse_reference(ref) != ref or not ref.get('component', '').startswith(('array.', 'file.')):
                raise ValueError('A streamed source must name an exact retained component')
        fields.add('regionalSources')
    if type(value) is dict and 'coordinatesFrom' in value:
        from .scientific_output_coordinates import declaration as coordinate_declaration
        coordinate_declaration(value['coordinatesFrom'])
        fields.add('coordinatesFrom')
    structured = type(value) is dict and value.get('format') in STRUCTURED.values()
    if structured: fields.add('state')
    if type(value) is dict and value.get('format') in COMPOUND.values(): fields.add('components')
    if type(value) is not dict or set(value) != fields or value['format'] not in (FORMAT,RGB_FORMAT,*STRUCTURED.values()):
        raise ValueError('A new scientific layer needs complete versioned metadata')
    shape, dtype, _ = layout(value['shape'], value['dtype'], [str(i) for i in range(len(value['shape']))])
    limit = (MAX_STREAMED_BYTES if 'regionalSources' in value else
             MAX_RETAINED_BYTES if retained and value['format']==FORMAT else
             MAX_NEW_IMAGE_BYTES if value['format']==FORMAT and value['type']=='image' else 64 << 20)
    if (not 2 <= len(shape) <= 8 or (not structured and any(n==0 for n in shape)) or math.prod(shape) * DTYPES[dtype] > limit
            or type(value['name']) is not str or not 0 < len(value['name']) <= 240
            or value['type'] not in (tuple(kind for kind,format in STRUCTURED.items() if format==value['format']) if structured else ('image', 'labels')) or type(value['visible']) is not bool
            or type(value['opacity']) not in (int, float) or not 0 <= value['opacity'] <= 1):
        raise ValueError('This native output exceeds its supported data or presentation limits')
    if value['format'] == RGB_FORMAT and (value['type'] != 'image' or len(shape) < 3 or shape[-1] not in (3,4)):
        raise ValueError('RGB images require three or four explicit color components after the spatial axes')
    if 'levels' in value:
        levels = value['levels']
        if (value['format'] != FORMAT or value['type'] != 'image' or type(levels) is not list
                or not 2 <= len(levels) <= MAX_LEVELS):
            raise ValueError('Native pyramids require two to sixteen aligned grey image levels')
        previous = shape; total = 0
        for index, level in enumerate(levels):
            if type(level) is not dict or set(level) != {'shape','dtype'}:
                raise ValueError('Every pyramid level needs its exact shape and dtype')
            dims, kind, _ = layout(level['shape'],level['dtype'],[str(i) for i in range(len(shape))])
            if (len(dims) != len(shape) or kind != dtype or any(n <= 0 or n > p for n,p in zip(dims,previous))
                    or index == 0 and dims != shape):
                raise ValueError('Pyramid levels keep their dtype and dimensions, finest first')
            previous = dims; total += math.prod(dims)*DTYPES[kind]
        if total > limit:
            raise ValueError('The pyramid exceeds its aggregate native publication allowance')
    if structured:
        n=coordinate_dimensions(value) if len(shape)==(3 if value['type']=='vectors' else 2) else 0
        if value['type']=='vectors' and shape[1]!=2:
            raise ValueError('Vectors require a separate origin and displacement component')
        if not 2<=n<=8 or (value['type']=='tracks' and n not in (3,4)):
            raise ValueError('Structured coordinates require rows with their declared coordinate values')
        if value['type'] in COMPOUND:
            components = component_contract(value['type']).declarations(value['components'])
            if components['vertices'] != {'shape':list(shape),'dtype':dtype}:
                raise ValueError('Surface coordinates must match their declared vertex component')
            if value['type']=='surface':
                from .scientific_surfaces import validate as validate_surface
                validate_surface(value['state'], shape[0])
            else:
                from .scientific_shapes import validate as validate_shapes
                validate_shapes(value['state'], components['offsets']['shape'][0]-1, n)
        elif value['type']=='points':
            from .scientific_points import validate
        elif value['type']=='tracks':
            from .scientific_tracks import validate
        else:
            from .scientific_vectors import validate
        if value['type'] not in COMPOUND: validate(value['state'],shape[0],n)
    geometry = value['geometry']; n = coordinate_dimensions(value)
    if (type(geometry) is not dict or set(geometry) != {'basis', 'axisLabels', 'units'}
            or type(geometry['basis']) is not list or len(geometry['basis']) != n+1
            or any(type(row) is not list or len(row) != n or
                   any(type(v) not in (int,float) or not math.isfinite(v) for v in row) for row in geometry['basis'])
            or any(type(geometry[key]) is not list or len(geometry[key]) != n
                   or any(type(v) is not str or not 0 < len(v) <= 128 for v in geometry[key])
                   for key in ('axisLabels','units'))):
        raise ValueError('The scientific output has invalid coordinate transforms or units')
    return copy.deepcopy(value)


def array_parts(info):
    """Native storage components; the finest pyramid level keeps the existing output identity."""
    return info.get('components', {('output' if i == 0 else f'level{i}'): level
        for i,level in enumerate(info.get('levels',[{'shape':info['shape'],'dtype':info['dtype']}]))})


def level_metadata(info, index):
    """Napari's aligned pyramid geometry: one origin, shape-ratio-scaled basis vectors."""
    level = copy.deepcopy(info); levels = level.pop('levels')
    level.update(levels[index])
    basis = info['geometry']['basis']; origin = basis[0]
    ratios = [fine/coarse for fine,coarse in zip(info['shape'],level['shape'])]
    level['geometry']['basis'] = [list(origin), *[
        [origin[j]+(vector[j]-origin[j])*ratios[i] for j in range(len(origin))]
        for i,vector in enumerate(basis[1:])]]
    return metadata(level, retained=True)


def compatible(info, meaning, *, retained=False):
    info, meaning = metadata(info, retained=retained), declaration(meaning)
    if SEMANTICS[meaning['semantic']] != info['type'] or len(meaning['axes']) != coordinate_dimensions(info):
        raise ValueError('The declared meaning and axes do not match this native layer')
    if info['format'] == RGB_FORMAT and (meaning['semantic'] != 'intensity' or 'color' in meaning['axes']):
        raise ValueError('RGB outputs require image intensity and spatial axes distinct from the color component')
    if info['type']=='tracks' and meaning['axes'][0]!='t':
        raise ValueError('Track coordinate columns begin with time: declare t before spatial axes')
    if info['type'] == 'labels' and info['dtype'][1] not in ('u', 'i'):
        raise ValueError('Scientific labels require integer values')
    return info, meaning


def scene(value, inputs):
    if type(value) is not list or not 0 < len(value) <= 8:
        raise ValueError('Retained layers need their declared native inputs')
    seen = set(); result = []
    for row in value:
        if type(row) is not dict or 'metadata' not in row:
            raise ValueError('Retained layers need complete native metadata')
        info = metadata(row['metadata'], retained=True)
        if 'coordinatesFrom' in info:
            raise ValueError('Retained layers cannot restore a live coordinate-source binding')
        compound = info['type'] in COMPOUND
        pyramid = 'levels' in row
        if set(row) != ({'input','metadata','components'} if compound else
                        {'input','metadata','levels'} if pyramid else {'input','metadata'}):
            raise ValueError('Retained layers need exactly their declared component bindings')
        if pyramid and (info['type'] != 'image' or info['format'] != FORMAT or type(row['levels']) is not list
                        or not 2 <= len(row['levels']) <= MAX_LEVELS or row['levels'][0] != row['input']):
            raise ValueError('A multiscale image lists 2 to 16 accepted levels, finest first, starting with its input')
        if 'levels' in info and (not pyramid or len(row['levels']) != len(info['levels'])):
            raise ValueError('The saved pyramid must retain every declared native level')
        names = (row.get('components') if compound else
                 {f'level{i}': name for i, name in enumerate(row['levels'])} if pyramid else {'output':row['input']})
        if (type(names) is not dict or compound and (set(names) != set(info['components'])
                or names.get('vertices') != row['input'])
                or any(type(name) is not str or name not in inputs or name in seen for name in names.values())
                or len(set(names.values())) != len(names)):
            raise ValueError('Retained layers must bind distinct accepted inputs')
        seen.update(names.values()); result.append(copy.deepcopy(row))
    if seen != set(inputs):
        raise ValueError('Retained layers must account for every declared native input')
    return result


def calibration(info, axes, space):
    info=metadata(info, retained=True);basis=info['geometry']['basis'];n=len(axes)
    if len(axes) != len(basis)-1:
        raise ValueError('Scientific axes must match the layer coordinate dimensions')
    layout(info['shape'],info['dtype'],storage_axes(info,axes))
    return {'space':copy.deepcopy(space),'axes':list(axes),'units':list(info['geometry']['units']),
            'affine':[[*(basis[j+1][i]-basis[0][i] for j in range(n)),basis[0][i]] for i in range(n)]}


def coordinate_dimensions(info):
    if info['format'] == RGB_FORMAT:return len(info['shape'])-1
    if info['type']=='vectors':return info['shape'][2]
    return info['shape'][1]-(info['type']=='tracks') if info['type'] in STRUCTURED else len(info['shape'])


def coordinate_indices(info):
    if info['type']=='vectors':return list(range(info['shape'][2]))
    return list(range(int(info['type']=='tracks'),info['shape'][1]))


def storage_axes(info, axes):
    if info['format'] == RGB_FORMAT:return [*axes,'color']
    if info['type'] in COMPOUND: return ['vertex','coordinate']
    if info['type']=='points': return ['object','coordinate']
    if info['type']=='tracks': return ['observation','field']
    if info['type']=='vectors': return ['vector','component','coordinate']
    return list(axes)


def scientific(info, axes, space):
    transform=calibration(info,axes,space)
    if info['type'] in STRUCTURED:
        columns={'axis':storage_axes(info,axes)[-1],'columns':coordinate_indices(info),'calibration':transform}
        if info['type']=='vectors':columns['components']={'axis':'component','origin':0,'displacement':1}
        return {'coordinate_columns':columns}
    return {'calibration':transform, **({'channels':rgb_channels(info)} if info['format']==RGB_FORMAT else {})}


def rgb_channels(info):
    return {'axis':'color','ids':['red','green','blue','alpha'][:info['shape'][-1]]}


def retained(info, source):
    info=metadata(info, retained=True)
    if info['type'] in STRUCTURED:
        columns=source.descriptor.get('coordinate_columns')
        if type(columns) is not dict or type(columns.get('calibration')) is not dict:
            raise ValueError('Retained structured layers need their declared coordinate columns')
        declared=columns['calibration']; axes=declared.get('axes')
        if source.axes!=tuple(storage_axes(info,axes)) or columns['axis']!=source.axes[-1] or columns['columns']!=coordinate_indices(info):
            raise ValueError('Retained structured storage must match its declared coordinate columns')
        expected_components={'axis':'component','origin':0,'displacement':1} if info['type']=='vectors' else None
        if columns.get('components')!=expected_components:
            raise ValueError('Retained vector origins and displacements must have explicit distinct roles')
    else:
        declared=source.descriptor.get('calibration'); axes=list(source.axes)
        if info['format']==RGB_FORMAT:
            if (not axes or axes[-1]!='color' or source.descriptor.get('channels')!=rgb_channels(info)):
                raise ValueError('Retained RGB layers require exact color component identities')
            axes=axes[:-1]
    semantic = source.descriptor.get('semantic')
    if info['type'] in COMPOUND:
        if semantic != component_contract(info['type']).semantic('vertices'):
            raise ValueError('Compound geometry requires its declared vertex component')
        semantic = 'surface-mesh' if info['type']=='surface' else 'shape-set'
    compatible(info,{'semantic':semantic,'axes':axes}, retained=True)
    if (tuple(info['shape']) != source.shape or info['dtype'] != source.dtype or type(declared) is not dict
            or not same_calibration(calibration(info,axes,declared.get('space')), declared)):
        raise ValueError('Retained layer coordinates must match the accepted scientific resource calibration')
    return info


def retained_scene(rows, sources):
    rows = scene(rows, sources)
    for row in rows:
        info = retained(row['metadata'], sources[row['input']])
        if 'levels' in info:
            for index,name in enumerate(row['levels']):
                retained(level_metadata(info,index),sources[name])
                if (sources[name].descriptor['calibration']['space'] !=
                        sources[row['input']].descriptor['calibration']['space']):
                    raise ValueError('Aligned pyramid levels must retain one coordinate space')
        for finer, coarser in zip(row.get('levels', []), row.get('levels', [])[1:]):
            f, c = sources[finer], sources[coarser]
            if (c.axes != f.axes or c.dtype != f.dtype or len(c.shape) != len(f.shape)
                    or any(m > n for m, n in zip(c.shape, f.shape))
                    or c.descriptor.get('semantic') != f.descriptor.get('semantic')):
                raise ValueError('Multiscale levels keep their axes, dtype and meaning, and never grow')
        if info['type'] in COMPOUND:
            contract = component_contract(info['type'])
            for name, binding in row['components'].items():
                source = sources[binding]; expected = info['components'][name]
                if (source.shape != tuple(expected['shape']) or source.dtype != expected['dtype']
                        or source.axes != tuple(contract.axes(name,len(source.shape)))
                        or source.descriptor.get('semantic') != contract.semantic(name)):
                    raise ValueError('Retained geometry components must match their exact roles, shapes and dtypes')
    return rows
