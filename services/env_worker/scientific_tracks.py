"""Native track rows, typed features and lineage retained as data, never code."""
from . import scientific_structured_state as state
from . import scientific_colormap as palettes

SETTINGS=frozenset(('affine','axis_labels','blending','experimental_clipping_planes','metadata','name','opacity',
    'projection_mode','rotate','scale','shear','translate','units','visible','color_by','colormap','colormaps_dict',
    'tail_width','tail_length','head_length','features','hide_completed_tracks'))
DISPLAY=frozenset(('display_id','display_tail','display_graph'))


def validate(value,rows,ndim):
    state.validate(value)
    def dictionary(node,keys):
        if node['type']!='dict' or set(node['value'])!=keys:
            raise ValueError('Tracks need complete typed features, lineage and native settings')
        return node['value']
    root=dictionary(value['state'],{'settings','graph','display','default_colormap'})
    settings=dictionary(root['settings'],SETTINGS)
    table=settings['features']
    if table['type']!='table' or state.length(table['index'])!=rows:
        raise ValueError('Track feature rows must match their time-coordinate rows')
    display=dictionary(root['display'],DISPLAY)
    if any(v['type']!='scalar' or type(v['value']) is not bool for v in display.values()):
        raise ValueError('Track display flags must be explicit booleans')
    if root['graph']['type']!='list':
        raise ValueError('Tracks require explicit child-to-parent lineage edges')
    if rows==0 and root['graph']['value']:
        raise ValueError('Empty tracks cannot contain lineage edges')
    if rows==0:
        raise ValueError('This pinned native viewer cannot reopen empty Tracks; keep the original result and use a supported viewer integration')
    # Detailed values are decoded and checked in the confined worker, not on the app loop.
    return value


def check_data(data,graph):
    import numpy as np
    if (type(data) is not np.ndarray or data.ndim!=2 or data.shape[1] not in (4,5)
            or data.dtype.kind not in 'iuf' or not 0<=len(data)<=state.MAX_NODES
            or not np.isfinite(data).all()):
        raise ValueError('Tracks need bounded finite ID, time and coordinate rows')
    if len(data)==0:
        if type(graph) is not list or graph:raise ValueError('Empty tracks cannot contain lineage edges')
        return {}
    ids=data[:,0];times=data[:,1]
    # The reference host indexes IDs using a CSR table and rounds time for slicing.
    # Refuse unsafe allocation and lossy uint32 IDs before constructing that table.
    if np.any(ids<0) or np.any(ids!=np.floor(ids)) or np.max(ids)>=2**32 or (int(np.max(ids))+2)*8>(48<<20):
        raise ValueError('Track IDs exceed this native host’s exact identity or bounded index allowance; keep the original and explicitly remap IDs if needed')
    if np.any(times<0) or np.max(times)>1_000_000:
        raise ValueError('Track times exceed this native host’s bounded time-index allowance')
    if len(np.unique(data[:,:2],axis=0))!=len(data):
        raise ValueError('Each track ID and time pair must identify one coordinate row')
    if type(graph) is not list or len(graph)>state.MAX_NODES:
        raise ValueError('Track lineage exceeds its bounded state allowance')
    windows={int(identity):(float(np.min(times[ids==identity])),float(np.max(times[ids==identity]))) for identity in np.unique(ids)}
    seen=set();edges=0
    for row in graph:
        if type(row) not in (list,tuple) or len(row)!=2:
            raise ValueError('Each track lineage row needs a child and its parents')
        child,parents=row
        if type(child) is not int or child not in windows or child in seen or type(parents) not in (list,tuple):
            raise ValueError('Lineage children must be unique existing track IDs')
        seen.add(child);edges+=len(parents)
        if any(type(parent) is not int for parent in parents) or edges>state.MAX_NODES or len(set(parents))!=len(parents):
            raise ValueError('Track lineage must contain bounded distinct parent edges')
        for parent in parents:
            if type(parent) is not int or parent not in windows or parent==child or windows[parent][1]>=windows[child][0]:
                raise ValueError('A parent track must exist and end before its child starts; check track identities and time alignment')
    return dict(graph)


def _palette(cmap):
    if len(cmap.colors)>palettes.MAX_COLORS:
        raise ValueError('The track palette exceeds its bounded color allowance')
    return palettes.snapshot_map(cmap)


def _restore_palette(value):
    from napari.utils.colormaps import Colormap
    value=palettes.settings(value)
    return Colormap(value['colors'],name=value['name'],controls=value['controls'],interpolation=value['interpolation'],
        nan_color=value['nanColor'],high_color=value['highColor'],low_color=value['lowColor'])


def snapshot(layer):
    if layer._type_string!='tracks' or len(layer.data)>state.MAX_NODES or len(layer.features.columns)>256:
        raise ValueError('This track result needs a bounded native structured writer; private work was kept')
    if len(layer.graph)>state.MAX_NODES or sum(len(parents) for parents in layer.graph.values())>state.MAX_NODES:
        raise ValueError('Track lineage exceeds its bounded state allowance')
    import numpy as np
    def identity(value):
        if type(value) not in (int,np.int8,np.int16,np.int32,np.int64,np.uint8,np.uint16,np.uint32,np.uint64):
            raise ValueError('Lineage edges require integer identities; no coercion was applied')
        return int(value)
    graph=[(identity(child),[identity(parent) for parent in parents]) for child,parents in layer.graph.items()]
    check_data(layer.data,graph)
    if len(layer.colormaps_dict)>256:
        raise ValueError('Too many per-feature track palettes for this bounded writer')
    from napari.utils.colormaps.colormap_utils import AVAILABLE_COLORMAPS
    native_maps=[AVAILABLE_COLORMAPS[layer.colormap],*layer.colormaps_dict.values()]
    if sum(len(cmap.colors)*4+len(cmap.controls)+16 for cmap in native_maps)>state.MAX_NODES:
        raise ValueError('Track palettes exceed their complete bounded state allowance')
    default=_palette(native_maps[0])
    maps={key:_palette(cmap) for key,cmap in layer.colormaps_dict.items()}
    _,settings,_=layer.as_layer_data_tuple();settings=dict(settings)
    settings.pop('properties',None);settings.pop('graph',None)
    settings['units']=tuple(str(unit) for unit in layer.units)
    settings['blending']=str(layer.blending);settings['projection_mode']=str(layer.projection_mode)
    settings['colormaps_dict']=maps
    display={key:bool(getattr(layer,key)) for key in DISPLAY}
    return validate(state.pack({'settings':settings,'graph':graph,'display':display,'default_colormap':default}),len(layer.data),layer.ndim)


def restored(data,value):
    validate(value,len(data),data.shape[1]-1)
    stored=state.unpack(value);settings=stored['settings']
    graph=check_data(data,stored['graph'])
    from napari.layers import Tracks
    from napari.utils.colormaps.colormap_utils import AVAILABLE_COLORMAPS
    cmap=_restore_palette(stored['default_colormap'])
    # A shared viewer may already contain another palette under this name. Never
    # overwrite its registry entry or silently recolor another accepted layer.
    if cmap.name in AVAILABLE_COLORMAPS and not AVAILABLE_COLORMAPS[cmap.name].same_colors(cmap):
        raise ValueError('A different track palette already uses this name in the host; reopen this document in a separate session')
    if cmap.name not in AVAILABLE_COLORMAPS: AVAILABLE_COLORMAPS[cmap.name]=cmap
    settings['colormaps_dict']={key:_restore_palette(item) for key,item in settings['colormaps_dict'].items()}
    layer=Tracks(data,graph=graph,**settings)
    for key,value in stored['display'].items():setattr(layer,key,value)
    if not layer.features.equals(settings['features']) or layer.data.tobytes()!=data.tobytes():
        raise ValueError('The native host changed track feature alignment or coordinates; accepted work was kept')
    return layer
