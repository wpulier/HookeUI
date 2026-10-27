"""Ragged Shapes retain typed features and native styling without executable state."""
from . import scientific_shape_data as geometry
from . import scientific_structured_state as state
from . import scientific_layer_colors as colors

SETTINGS=frozenset(('affine','axis_labels','blending','experimental_clipping_planes','metadata','name','opacity',
    'projection_mode','rotate','scale','shear','translate','units','visible','ndim','text','shape_type',
    'z_index','edge_width','face_color','face_color_cycle','face_colormap','face_contrast_limits',
    'edge_color','edge_color_cycle','edge_colormap','edge_contrast_limits','features','feature_defaults'))
COLOR_FIELDS=frozenset(('mode','property','mapping','cycleIndex','current','currentRow'))


def geometry_types(value):
    """Read already validated shape kinds without exposing codec layout to storage."""
    settings=value['state']['value']['settings']['value']
    return tuple(row['value'] for row in settings['shape_type']['value'])



def appearance(root,settings,rows):
    import numpy as np
    def number(node):
        data=colors.numeric(node)
        if data.shape!=() or not np.isfinite(data):raise ValueError('Shape controls require finite scalars')
        return data.item()
    def rgba(node,count):
        data=colors.numeric(node)
        if (data.shape!=count or not np.isfinite(data).all() or np.any(data<0) or np.any(data>1)):
            raise ValueError('Shape colors must be finite RGBA values matching their geometry')
    if number(root['currentWidth'])<0:raise ValueError('Shape widths must be nonnegative')
    for key in ('edge_width','z_index'):
        data=colors.numeric(settings[key])
        if (data.shape!=(rows,) or not np.isfinite(data).all()
                or key=='edge_width' and np.any(data<0)
                or key=='z_index' and np.any(data!=np.floor(data))):
            raise ValueError('Shape widths and ordering must match their geometry rows')
    columns={row['name']:row['values'] for row in settings['features']['columns']}
    for side in ('face','edge'):
        record=root[side]['value'];mode=record['mode'];prop=record['property']
        if mode['type']!='scalar' or mode['value'] not in ('direct','cycle','colormap'):
            raise ValueError('Choose a supported native shape color mode')
        if (prop['type']!='scalar' or type(prop['value']) is not str
                or mode['value']!='direct' and prop['value'] not in columns):
            raise ValueError('Mapped shape colors require an existing feature column')
        if mode['value']=='colormap':
            feature=columns[prop['value']]
            if feature['type']=='category':feature=feature['categories']['value']
            if feature['type']!='array' or feature['dtype'][1] not in 'iuf':
                raise ValueError('Continuous shape colors require a numeric feature column')
        colors.validate_native_colormap(settings[side+'_colormap'])
        cycle=colors.numeric(settings[side+'_color_cycle'])
        if cycle.ndim!=2 or not len(cycle):raise ValueError('Shape color cycles must contain RGBA colors')
        rgba(settings[side+'_color_cycle'],(len(cycle),4))
        index=number(record['cycleIndex'])
        if type(index) is not int or not 0<=index<len(cycle):raise ValueError('Shape color cycle position is invalid')
        current=colors.numeric(record['current'])
        if current.shape not in ((4,),(1,4)):raise ValueError('The current shape color needs one RGBA value')
        rgba(record['current'],current.shape)
        if rows:
            rgba(settings[side+'_color'],(rows,4))
        else:
            # Native Shapes serializes its next-shape color when no geometry
            # exists. It is a default, not the color of an invented shape.
            default=colors.numeric(settings[side+'_color'])
            if default.shape!=current.shape or default.dtype.str!=current.dtype.str or not np.array_equal(default,current):
                raise ValueError('Empty shape colors must match their next-shape defaults')
            rgba(settings[side+'_color'],current.shape)
        linked=record['currentRow']
        if linked!={'type':'scalar','value':None}:
            index=number(linked)
            if type(index) is not int or not 0<=index<rows:
                raise ValueError('The current shape color must reference an existing color row')
            source=colors.numeric(settings[side+'_color'])
            if current.dtype.str!=source.dtype.str or not np.array_equal(current.reshape(4),source[index]):
                raise ValueError('The current shape color disagrees with its linked row')
        mapping=record['mapping']
        if mapping['type']!='list':raise ValueError('Shape color mappings need explicit entries')
        seen=set()
        for pair in mapping['value']:
            if pair['type'] not in ('list','tuple') or len(pair['value'])!=2:
                raise ValueError('Shape color mappings need key and color pairs')
            key,color=pair['value']
            if key['type'] not in ('scalar','integer','float','numpy-scalar'):
                raise ValueError('Shape color mapping keys must be scalar feature values')
            if key['type']=='numpy-scalar' and key['value'].get('shape')!=[]:
                raise ValueError('Shape color mapping keys must be scalar feature values')
            decoded=state.unpack({'format':state.FORMAT,'state':key})
            if decoded in seen:raise ValueError('Shape color mappings cannot repeat a feature value')
            seen.add(decoded)
            rgba(color,(4,))


def validate(value,rows,ndim):
    state.validate(value)
    def dictionary(node,keys):
        if node['type']!='dict' or set(node['value'])!=keys:
            raise ValueError('Shapes need complete native settings, boundaries and typed features')
        return node['value']
    root=dictionary(value['state'],{'settings','face','edge','currentWidth'})
    settings=dictionary(root['settings'],SETTINGS)
    if settings['ndim']!={'type':'scalar','value':ndim}:
        raise ValueError('Shape settings must match their coordinate dimensions')
    for key,count in (('features',rows),('feature_defaults',1)):
        table=settings[key]
        if table['type']!='table' or state.length(table['index'])!=count:
            raise ValueError('Shape features must match their geometry rows')
    if ({row['name'] for row in settings['features']['columns']}
            != {row['name'] for row in settings['feature_defaults']['columns']}):
        raise ValueError('Shape feature defaults must match the feature columns. Set defaults for newly added columns before saving; private values were kept')
    for side in ('face','edge'): dictionary(root[side],COLOR_FIELDS)
    kinds=settings['shape_type']
    if kinds['type']!='list' or len(kinds['value'])!=rows or any(
            kind.get('type')!='scalar' or kind.get('value') not in geometry.TYPES for kind in kinds['value']):
        raise ValueError('Every shape requires its own native geometry type')
    appearance(root,settings,rows)
    return value


def snapshot(layer):
    import numpy as np
    rows=geometry.native(layer)
    if len(layer.features.columns)>256:
        raise ValueError('These shape features need a bounded structured writer')
    for side in ('face','edge'):
        cycle=getattr(layer,'_'+side+'_color_cycle')
        palette=getattr(layer,side+'_colormap')
        mapping=getattr(layer,side+'_color_cycle_map')
        if (len(mapping)>state.MAX_NODES or cycle.values.nbytes>state.MAX_ARRAY_BYTES
                or palette.colors.nbytes+palette.controls.nbytes>state.MAX_ARRAY_BYTES):
            raise ValueError('These shape colors exceed the bounded state allowance')
    _,settings,_=layer.as_layer_data_tuple();settings=dict(settings)
    settings.pop('properties',None);settings.pop('property_choices',None)
    settings['units']=tuple(str(unit) for unit in layer.units)
    controls=colors.normalizer()
    for name in settings:
        if name not in ('metadata','features','feature_defaults'):settings[name]=controls(settings[name])
    record={'settings':settings,'currentWidth':float(layer.current_edge_width)}
    for side in ('face','edge'):
        current=getattr(layer,'_current_'+side+'_color')
        palette=getattr(layer,side+'_color')
        linked=None
        # Native Shapes may keep the current color as a view into a shape row.
        # Preserve that relationship, since refresh_colors updates it in place.
        if np.shares_memory(current,palette):
            for index,row in enumerate(palette):
                if (current.ctypes.data==row.ctypes.data and current.flags.c_contiguous
                        and current.dtype==row.dtype and current.size==4):
                    linked=index;break
            if linked is None:raise ValueError('This current shape color needs an explicit native alias integration')
        record[side]=controls({'mode':str(getattr(layer,side+'_color_mode')),
            'property':getattr(layer,'_'+side+'_color_property'),
            'mapping':list(getattr(layer,side+'_color_cycle_map').items()),
            'cycleIndex':getattr(layer,'_'+side+'_color_cycle').current_index,
            'current':current,'currentRow':linked})
    return validate(state.pack(record),len(rows),layer.ndim)


def restored(arrays,value):
    from napari.layers import Shapes
    from napari.layers.shapes._shapes_constants import ColorMode
    validate(value,len(arrays['offsets'])-1,arrays['vertices'].shape[1])
    stored=state.unpack(value);settings=stored['settings']
    rows=geometry.split(arrays,settings['shape_type'])
    layer=Shapes(rows,**settings)
    for side in ('face','edge'):
        record=stored[side]
        # ShapeList allocates different color dtypes when rebuilding its 2D
        # and 3D meshes. Its public setters assign into that existing storage,
        # so establish the accepted dtype first, then synchronize mesh colors
        # through the native setter. Never reinterpret the retained colors.
        if rows:
            setattr(layer._data_view,'_'+side+'_color',settings[side+'_color'].copy())
            setattr(layer._data_view,side+'_color',settings[side+'_color'])
        setattr(layer,'_'+side+'_color_mode',ColorMode(record['mode']))
        setattr(layer,'_'+side+'_color_property',record['property'])
        setattr(layer,side+'_color_cycle_map',dict(record['mapping']))
        getattr(layer,'_'+side+'_color_cycle').current_index=record['cycleIndex']
        current=record['current']
        if record['currentRow'] is not None:
            row=record['currentRow'];palette=getattr(layer,side+'_color')
            current=palette[row] if current.ndim==1 else palette[row:row+1]
        setattr(layer,'_current_'+side+'_color',current)
    layer._current_edge_width=stored['currentWidth']
    actual=geometry.native(layer)
    if (len(actual)!=len(rows) or any(a.dtype.str!=b.dtype.str or a.shape!=b.shape or a.tobytes()!=b.tobytes()
            for a,b in zip(actual,rows)) or snapshot(layer)!=value):
        raise ValueError('The native host changed shape geometry, features or styling; accepted work was kept')
    return layer
