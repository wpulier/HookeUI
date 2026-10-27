"""Explicit output capture and data-only restoration inside the confined host."""
from contextlib import contextmanager
from types import SimpleNamespace
import math

from . import scientific_output_wire as wire
from .scientific_layer_exports import EditableLayers, geometry, quiescent, single_threaded, values


def _regional_source(data):
    from .lazy_arrays import LazyNative
    from .native_regions import NativeArray
    from .scientific_decoders import DecodedImage
    if (type(data) is LazyNative and (type(data._source) is DecodedImage
            or (type(data._source) is NativeArray and 'reference' in data._source.descriptor))):
        return data._source
    return None


def _regional_reference(source):
    from .native_regions import NativeArray
    from .source_directory import SourceDirectory
    if type(source) is NativeArray:
        return source.reference
    selected = source._source
    if type(selected) is SourceDirectory:
        selected = selected._source
    return selected.reference


def describe(layer):
    import numpy as np
    compound = layer._type_string in wire.COMPOUND
    pyramid = bool(getattr(layer,'multiscale',False))
    extra={}
    if compound:
        parts = wire.component_contract(layer._type_string).describe(layer)
        shape, dtype = parts['vertices']['shape'], parts['vertices']['dtype']
    elif pyramid:
        if (layer._type_string != 'image' or getattr(layer,'rgb',False)
                or not 2 <= len(layer.data) <= wire.MAX_LEVELS):
            raise ValueError('Save supports aligned grey image pyramids with two to sixteen levels')
        levels = tuple(layer.data)
        if any(type(data) is not np.ndarray and _regional_source(data) is None for data in levels):
            raise ValueError('Pyramid levels need native arrays or declared immutable regional readers')
        if sum(data.nbytes for data in levels if type(data) is np.ndarray) > 64 << 20:
            raise ValueError('New in-memory pyramid levels exceed their 64 MiB publication allowance')
        sources = [_regional_source(data) for data in levels]
        if all(source is not None for source in sources):
            extra['regionalSources'] = [_regional_reference(source) for source in sources]
        extra['levels'] = [{'shape':list(data.shape),'dtype':data.dtype.str} for data in levels]
        shape,dtype = list(levels[0].shape),levels[0].dtype.str
    else:
        data = layer.data
        regional = _regional_source(data)
        if (layer._type_string not in ('image','labels',*wire.STRUCTURED)
                or (regional is None and type(data) is not np.ndarray)
                or (regional is not None and (layer._type_string!='image' or getattr(layer,'rgb',False)))):
            raise ValueError('Save this output through its native array integration; lazy layers need their declared writer')
        if regional is None and data.nbytes > 64 << 20:
            raise ValueError('This new in-memory output exceeds its 64 MiB publication allowance')
        if regional is not None:
            extra['regionalSources'] = [_regional_reference(regional)]
        shape, dtype = list(data.shape), data.dtype.str
    basis, axes, units = geometry(layer)
    if layer._type_string in wire.STRUCTURED:
        if compound:
            if layer._type_string=='surface':
                from .scientific_surfaces import snapshot
            else:
                from .scientific_shapes import snapshot
            extra['components'] = parts
        elif layer._type_string=='points':
            from .scientific_points import snapshot
        elif layer._type_string=='tracks':
            from .scientific_tracks import snapshot
        else:
            from .scientific_vectors import snapshot
        extra['state']=snapshot(layer)
    return wire.metadata({'format': wire.RGB_FORMAT if getattr(layer,'rgb',False) else wire.STRUCTURED.get(layer._type_string,wire.FORMAT), 'shape': shape, 'dtype': dtype,
        'name': layer.name, 'type': layer._type_string, 'visible': bool(layer.visible), 'opacity': float(layer.opacity),
        'geometry': {'basis': [list(row) for row in basis], 'axisLabels': list(axes), 'units': list(units)},**extra})


def _regional_chunks(source, grant, verify):
    from itertools import product
    for index in product(*(range(n) for n in grant.grid)):
        verify()
        bounds = [[i*c,min((i+1)*c,n)] for i,c,n in zip(index,grant.chunks,grant.shape)]
        from .native_regions import paced_view_reads
        with paced_view_reads(verify):
            block = source.read(bounds,view=True)
        verify()
        yield index,block
    verify()


@contextmanager
def capture_pyramid(layer, expected, meaning, specifications, exchange, storage):
    """Freeze mutable levels together; stream only immutable regional levels."""
    from contextlib import ExitStack
    from .native_edits import bind_output
    from .scientific_layer_exports import snapshot_chunks
    expected,meaning = wire.compatible(expected,meaning)
    if storage not in ('files','memory'):
        raise ValueError('Choose a supported native snapshot storage')
    if 'levels' not in expected or describe(layer) != expected:
        raise ValueError('The pyramid changed before capture; inspect and save again')
    data = layer.data; levels = tuple(data)
    sources = tuple(_regional_source(level) for level in levels)
    parts = wire.array_parts(expected)
    if (type(specifications) is not list or len(specifications) != len(parts)
            or any(type(row) is not dict or set(row) != {'name','edit'} for row in specifications)
            or [row['name'] for row in specifications] != sorted(parts)):
        raise ValueError('A pyramid requires its exact ordered level grants')
    def verify():
        single_threaded()
        if (layer.data is not data or len(layer.data) != len(levels)
                or any(current is not original for current,original in zip(layer.data,levels))
                or any(_regional_source(level) is not source for level,source in zip(levels,sources))
                or describe(layer) != expected):
            raise ValueError('The pyramid changed during capture; private work was kept')
    with ExitStack() as frozen:
        snapshots = []
        for row in specifications:
            index = 0 if row['name']=='output' else int(row['name'][5:])
            array,source = levels[index],sources[index]
            grant = bind_output(row['edit'],exchange)
            if (grant.shape != tuple(array.shape) or grant.dtype != array.dtype.str
                    or tuple(grant.axes) != tuple(meaning['axes'])):
                raise ValueError('A pyramid level does not match its exact native grant')
            verify()
            chunks = (_regional_chunks(source,grant,verify) if source is not None else
                      frozen.enter_context(snapshot_chunks(array,grant,verify,storage=storage)))
            snapshots.append((grant,chunks))
        verify()
        yield snapshots
        verify()


@contextmanager
def capture(viewer, layer, expected, meaning, grant, storage):
    expected, meaning = wire.compatible(expected, meaning)
    if describe(layer) != expected:
        raise ValueError('The new layer changed before capture; inspect it and save again')
    if (source := _regional_source(layer.data)) is not None:
        if storage not in ('files','memory'):
            raise ValueError('Choose a supported native snapshot storage')
        data = layer.data
        if (grant.shape != source.shape or grant.dtype != source.dtype
                or tuple(grant.axes) != tuple(wire.storage_axes(expected,meaning['axes']))):
            raise ValueError('The lazy image does not match its exact native write grant')
        def unchanged():
            single_threaded()
            if layer.data is not data or _regional_source(data) is not source or describe(layer) != expected:
                raise ValueError('The lazy image changed during capture; inspect and save again')
        unchanged()
        yield _regional_chunks(source,grant,unchanged)
        return
    if expected['type'] in wire.STRUCTURED:
        from .scientific_layer_exports import snapshot_chunks
        data=layer.data
        if grant.axes!=tuple(wire.storage_axes(expected,meaning['axes'])):
            raise ValueError('Structured output requires explicit coordinate-column storage')
        def verify():
            if layer.data is not data or describe(layer)!=expected:
                raise ValueError('The structured data or features changed during capture; inspect and save again')
        with snapshot_chunks(data,grant,verify,storage=storage) as chunks:
            yield chunks
        return
    source = SimpleNamespace(shape=tuple(expected['shape']), dtype=expected['dtype'], axes=tuple(wire.storage_axes(expected,meaning['axes'])),
                             descriptor={**meaning, **({'channels':wire.rgb_channels(expected)} if expected['format']==wire.RGB_FORMAT else {})}, reference=None)
    # Check scalar meaning before the first external transfer. This is an array
    # contract check, not evidence of biological accuracy or model suitability.
    import numpy as np
    data = values(layer, source); quiescent(data)
    if meaning['semantic'] in ('class-labels','instance-labels','probabilities'):
        for start in range(0,data.size,max(1,(1 << 20)//data.itemsize)):
            part = data.flat[start:start+max(1,(1 << 20)//data.itemsize)]
            if np.any(part < 0) or (meaning['semantic']=='probabilities' and
                    (not np.isfinite(part).all() or np.any(part > 1))):
                raise ValueError('Labels must be nonnegative and probabilities must be finite values between zero and one')
    registry = EditableLayers(viewer, {'output': source})
    registry.bind(layer=layer, input='output'); registry.seal()
    try:
        with registry.staged_chunks(layer, 'output', grant, storage=storage) as chunks:
            yield chunks
    finally:
        registry.close()


LAZY_ABOVE_BYTES = 64 << 20   # a grey image larger than this opens lazily
EAGER_BYTES = 512 << 20       # retained layers read whole at open, together (the editable allowance)
LAZY_CACHE_BYTES = 256 << 20  # chunk cache of the lazy layers, within the worker's 1 GiB allowance


def restore(viewer, inputs, layers, editable, bindings, *, keys=None):
    """Load accepted values only. No user code or creating plugin callback is replayed."""
    import numpy as np
    from .native_regions import DTYPES
    layers = wire.retained_scene(layers, inputs)
    by_input = {name: key for key, name in keys.items()} if keys is not None else {}
    from .lazy_arrays import ChunkCache, LazyNative, Multiscale
    from .lazy_labels import LazyLabels,DirtyBudget
    size = lambda source: math.prod(source.shape) * DTYPES[source.dtype]
    # H08. A grey image over 64 MiB, and every multiscale image, is shown lazily: read by slice through a bounded
    # chunk cache, never whole, and not bound as editable. Labels above the existing eager allowance use
    # regional copy-on-write storage. Smaller Labels, small images and geometry keep their eager behavior.
    lazy_labels={row['input'] for row in layers if row['metadata']['type']=='labels'
                 and size(inputs[row['input']])>EAGER_BYTES}
    lazy = {row['input'] for row in layers if row['metadata']['type'] == 'image'
            and row['metadata']['format'] != wire.RGB_FORMAT and size(inputs[row['input']]) > LAZY_ABOVE_BYTES}
    levels = {name for row in layers for name in row.get('levels', ())}     # a multiscale image is always lazy
    lazy |= levels | lazy_labels
    eager = sum(size(source) for name, source in inputs.items() if name not in lazy)
    if eager > EAGER_BYTES:
        big = sorted((size(inputs[row['input']]), row['metadata']['name']) for row in layers if row['input'] not in lazy)
        raise ValueError(f'The layers this view loads whole need {eager / 2**20:.0f} MiB, over the '
                         f'{EAGER_BYTES >> 20} MiB the worker holds for editing (largest: {big[-1][1]}); '
                         'open fewer eager layers together')
    cache = ChunkCache(LAZY_CACHE_BYTES) if lazy else None
    label_budget=DirtyBudget()
    from magicgui.widgets import Container
    def read(source):
        data = np.empty(source.shape, dtype=source.dtype)
        for region in source.regions():
            data[tuple(slice(*bounds) for bounds in region)] = source.read(region, view=True)
        return data
    lazy_images = []
    for row in layers:
        source = inputs[row['input']]; info = row['metadata']
        if 'levels' in row:
            data = Multiscale([inputs[name] for name in row['levels']], cache, ident=row['input'])
        elif row['input'] in lazy_labels:
            data=LazyLabels(source,cache,label_budget,ident=row['input'])
        else:
            data = LazyNative(source, cache, ident=row['input']) if row['input'] in lazy else read(source)
        if info['type'] in wire.COMPOUND:
            if info['type']=='surface':
                from .scientific_surfaces import restored
            else:
                from .scientific_shapes import restored
            arrays = {name: data if binding == row['input'] else read(inputs[binding])
                      for name,binding in row['components'].items()}
            layer = restored(arrays, info['state'])
            if describe(layer) != info:
                raise ValueError('Retained geometry coordinates or settings changed during reconstruction')
            viewer.add_layer(layer); bindings.bind(layer=layer,key=by_input.get(row['input'], row['input']))
            continue
        if info['type'] in wire.STRUCTURED:
            if info['type']=='points':
                from .scientific_points import restored
            elif info['type']=='tracks':
                from .scientific_tracks import restored
            else:
                from .scientific_vectors import restored
            layer=restored(data,info['state'])
            if describe(layer)!=info:
                raise ValueError('Retained structured features or coordinates changed during reconstruction')
            viewer.add_layer(layer)
            bindings.bind(layer=layer,key=by_input.get(row['input'], row['input']))
            continue
        geo = info['geometry']; basis = np.asarray(geo['basis']); n = wire.coordinate_dimensions(info)
        affine = np.eye(n+1); affine[:n,:n] = (basis[1:] - basis[0]).T; affine[:n,n] = basis[0]
        kwargs = dict(name=info['name'], axis_labels=geo['axisLabels'], units=geo['units'],
                      affine=affine, visible=info['visible'], opacity=info['opacity'])
        layer = (viewer.add_labels(data, **kwargs) if info['type']=='labels' else
                 viewer.add_image(data, rgb=info['format']==wire.RGB_FORMAT, **({'multiscale': True} if 'levels' in row else {}), **kwargs))
        if info['type'] == 'image' and row['input'] in lazy:
            lazy_images.append(layer)
        if row['input'] not in lazy or row['input'] in lazy_labels:
            editable.bind(layer=layer, input=row['input'])
        bindings.bind(layer=layer, key=by_input.get(row['input'], row['input']))
    # Lazy images start with napari's placeholder contrast rather than a full-data
    # scan. Once all layers have established the displayed slice, use the same
    # bounded native operation as Auto-contrast once. Remembered imageAppearance
    # is applied later by restore-view and remains authoritative.
    for layer in lazy_images:
        layer.reset_contrast_limits(mode='slice')
    return Container(widgets=[])


@contextmanager
def capture_components(layer, expected, meaning, specifications, exchange, storage):
    from contextlib import ExitStack
    from .native_edits import bind_output
    from .scientific_layer_exports import snapshot_chunks
    expected, meaning = wire.compatible(expected, meaning)
    if expected['type'] not in wire.COMPOUND or describe(layer) != expected:
        raise ValueError('The geometry changed before capture; inspect it and save again')
    contract = wire.component_contract(expected['type'])
    originals = contract.native(layer) if expected['type']=='shapes' else None
    data = contract.components(layer)
    if (type(specifications) is not list or len(specifications) != len(data)
            or any(type(row) is not dict or set(row) != {'name','edit'} for row in specifications)
            or [row['name'] for row in specifications] != sorted(data)):
        raise ValueError('A geometry snapshot requires its exact ordered component grants')
    def verify():
        if originals is not None:
            current = contract.native(layer)
            changed = len(current)!=len(originals) or any(a is not b for a,b in zip(current,originals))
        else:
            current = contract.components(layer)
            changed = any(current[name] is not array for name,array in data.items())
        if changed or describe(layer) != expected:
            raise ValueError('The geometry components changed during capture; private work was kept')
    with ExitStack() as frozen:
        snapshots = []
        for row in specifications:
            name = row['name']; grant = bind_output(row['edit'], exchange); array = data[name]
            if (grant.shape != array.shape or grant.dtype != array.dtype.str
                    or grant.axes != tuple(contract.axes(name,array.ndim))):
                raise ValueError('The geometry component does not match its exact native grant')
            chunks = frozen.enter_context(snapshot_chunks(array,grant,verify,storage=storage))
            snapshots.append((grant,chunks))
        verify()
        # Every component is frozen before the first parent transport callback.
        yield snapshots
