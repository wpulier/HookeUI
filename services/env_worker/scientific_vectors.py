"""Retained native vector origins, displacements, typed features and appearance."""
from . import scientific_structured_state as state
from . import scientific_layer_colors as colors

SETTINGS=frozenset(('affine','axis_labels','blending','experimental_clipping_planes','metadata','name','opacity',
    'projection_mode','rotate','scale','shear','translate','units','visible','length','edge_width','vector_style',
    'edge_color','edge_color_cycle','edge_colormap','edge_contrast_limits','ndim','features','feature_defaults'))


def validate(value,rows,ndim):
    state.validate(value)
    def dictionary(node,keys):
        if node['type']!='dict' or set(node['value'])!=keys:
            raise ValueError('Vectors need complete native settings and typed features')
        return node['value']
    root=dictionary(value['state'],{'settings','edge'})
    settings=dictionary(root['settings'],SETTINGS)
    if settings['ndim']!={'type':'scalar','value':ndim}:
        raise ValueError('Vector settings must match their coordinate dimensions')
    for key,count in (('features',rows),('feature_defaults',1)):
        table=settings[key]
        if table['type']!='table' or state.length(table['index'])!=count:
            raise ValueError('Vector features must match their vector rows')
    edge=dictionary(root['edge'],colors.FIELDS)['colors']
    if edge['type']!='array' or edge['shape']!=[rows,4]:
        raise ValueError('Vector colors must match their vector rows')
    return value


def check_data(data):
    import numpy as np
    if (type(data) is not np.ndarray or data.ndim!=3 or data.shape[1]!=2 or not 2<=data.shape[2]<=8
            or not 0<=len(data)<=state.MAX_NODES or data.dtype.kind not in 'iuf' or not np.isfinite(data).all()):
        raise ValueError('Vectors need bounded finite rows of an origin and displacement in each dimension')


def snapshot(layer):
    if layer._type_string!='vectors' or len(layer.features.columns)>256:
        raise ValueError('These vector features need a bounded native structured writer; private work was kept')
    check_data(layer.data);colors.admit(layer._edge)
    for name in layer.features:
        column=layer.features[name]
        if str(column.dtype)=='category' and len(column.cat.categories)>state.MAX_NODES:
            raise ValueError('Vector categories exceed the bounded state allowance')
    _,settings,_=layer.as_layer_data_tuple();settings=dict(settings)
    settings.pop('properties',None);settings.pop('property_choices',None)
    settings['units']=tuple(str(unit) for unit in layer.units)
    controls=colors.normalizer()
    for name in settings:
        if name not in ('metadata','features','feature_defaults'):settings[name]=controls(settings[name])
    return validate(state.pack({'settings':settings,'edge':colors.snapshot(layer._edge,controls)}),len(layer.data),layer.ndim)


def restored(data,value):
    check_data(data);validate(value,len(data),data.shape[2])
    from napari.layers import Vectors
    stored=state.unpack(value);settings=stored['settings']
    layer=Vectors(data,**settings)
    layer._edge=colors.restored(stored['edge'])
    # The native constructor wires this before visual subscriptions are attached.
    layer._edge.events.color_mode.connect(layer.events.edge_color_mode)
    if (not layer.features.equals(settings['features']) or not layer.feature_defaults.equals(settings['feature_defaults'])
            or layer.data.tobytes()!=data.tobytes()):
        raise ValueError('The native host changed vector coordinates or typed features; accepted work was kept')
    return layer
