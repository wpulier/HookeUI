"""Data-only Points state on the pinned scientific host; no callback restoration."""

from . import scientific_structured_state as state
from . import scientific_layer_colors as color_state


SETTINGS=frozenset(('affine','axis_labels','blending','experimental_clipping_planes','metadata','name','opacity',
    'projection_mode','rotate','scale','shear','translate','units','visible','symbol','border_width','border_width_is_relative',
    'face_color','face_color_cycle','face_colormap','face_contrast_limits','border_color','border_color_cycle','border_colormap',
    'border_contrast_limits','text','size','ndim','features','feature_defaults','shading','antialiasing','canvas_size_limits','shown'))
COLORS=color_state.FIELDS


def validate(value, rows, ndim):
    state.validate(value)
    def dictionary(node, keys):
        if node['type']!='dict' or set(node['value'])!=keys:
            raise ValueError('Retained Points require complete native settings and typed features')
        return node['value']
    root=dictionary(value['state'],{'settings','face','border'})
    settings=dictionary(root['settings'],SETTINGS)
    if settings['ndim']!={'type':'scalar','value':ndim}:
        raise ValueError('Point settings must match their coordinate dimensions')
    for key,count in (('features',rows),('feature_defaults',1)):
        table=settings[key]
        if table['type']!='table' or state.length(table['index'])!=count:
            raise ValueError('Typed point features must match their coordinate rows')
    for side in ('face','border'):
        manager=dictionary(root[side],COLORS)
        colors=manager['colors']
        if colors['type']!='array' or colors['shape']!=[rows,4]:
            raise ValueError('Point colors must match their coordinate rows')
    return value


def snapshot(layer):
    """Use the native layer-data contract while retaining typed feature values."""
    if layer._type_string != 'points':
        raise ValueError('This writer requires a native Points layer')
    if len(layer.data)>state.MAX_NODES or len(layer.features.columns)>256:
        raise ValueError('These point features need a bulk structured writer; the private layer was kept')
    # Native model_dump/as_layer_data_tuple may copy unused category mappings.
    # Admit their complete size before invoking either native serializer.
    for manager in (layer._face,layer._border):
        color_state.admit(manager)
    for name in layer.features:
        column=layer.features[name]
        if str(column.dtype)=='category' and len(column.cat.categories)>state.MAX_NODES:
            raise ValueError('These point categories need a bulk structured writer; the private layer was kept')
    _, settings, _ = layer.as_layer_data_tuple()
    settings=dict(settings)
    # These legacy projections are derived from features. Passing them back would
    # override the typed table and erase ordered categorical metadata upstream.
    settings.pop('properties',None);settings.pop('property_choices',None)
    settings['units']=tuple(str(unit) for unit in layer.units)
    # Upstream exposes its enumerated control values as string enums. Persist the
    # declared values, not importable Python types. Arbitrary metadata is untouched.
    controls=color_state.normalizer()
    for name in settings:
        if name not in ('metadata','features','feature_defaults','properties','property_choices'):
            settings[name]=controls(settings[name])
    return validate(state.pack({'settings':settings,'face':color_state.snapshot(layer._face,controls),'border':color_state.snapshot(layer._border,controls)}),len(layer.data),layer.ndim)


def restored(data, value):
    """Only the native Points constructor is available, never a stored class path."""
    from napari.layers import Points
    validate(value,len(data),data.shape[1])
    stored=state.unpack(value)
    if type(stored) is not dict or set(stored)!={'settings','face','border'}:
        raise ValueError('Retained points need complete settings and color mappings')
    settings=stored['settings']
    if type(settings) is not dict or 'data' in settings:
        raise ValueError('Retained point state must contain native settings, never replacement values')
    layer=Points(data,**settings)
    for name in ('face','border'):
        record=stored[name]
        manager=color_state.restored(record)
        # This new layer has not joined a viewer. Bind the complete manager before
        # the visual subscribes to its events; do not overwrite a live manager.
        setattr(layer,'_'+name,manager)
    # A native constructor normalizing controls is expected; losing a feature
    # dtype/value is not. The typed table comparisons cover nullable/category data.
    if not layer.features.equals(settings['features']) or not layer.feature_defaults.equals(settings['feature_defaults']):
        raise ValueError('The native host changed retained feature values or types; the accepted document was kept')
    return layer
