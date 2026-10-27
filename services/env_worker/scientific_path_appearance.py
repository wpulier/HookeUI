"""Native Vector/Track presentation, separate from coordinates and lineage."""
import copy
import math
from types import SimpleNamespace

FIELDS = {
    'vectors': {'kind','length','width','style','projection'},
    'tracks': {'kind','width','tail','head','showIds','showTail','showGraph','hideCompleted'},
}
NATIVE = {
    'vectors': {'length':'length','width':'edge_width','style':'vector_style','projection':'projection_mode'},
    'tracks': {'width':'tail_width','tail':'tail_length','head':'head_length','showIds':'display_id',
               'showTail':'display_tail','showGraph':'display_graph','hideCompleted':'hide_completed_tracks'},
}


def settings(value, kind=None):
    def finite(n):
        return type(n) in (int,float) and abs(n)<=2**53-1 and math.isfinite(n)
    if (type(value) is not dict or type(value.get('kind')) is not str or value['kind'] not in FIELDS
            or set(value)!=FIELDS[value['kind']] or kind is not None and value['kind']!=kind):
        raise ValueError('Choose a current Vector or Track layer and its complete display settings')
    if not finite(value['width']) or value['width']<0:
        raise ValueError('Enter a finite, nonnegative line width')
    if value['kind']=='vectors':
        if (not finite(value['length']) or value['style'] not in ('line','triangle','arrow')
                or value['projection'] not in ('none','all','fade')):
            raise ValueError('Choose a vector style, projection and finite display length multiplier')
    elif (not .5<=value['width']<=20
            or any(type(value[key]) is not int or not 0<=value[key]<=2**53-1 for key in ('tail','head'))
            or any(type(value[key]) is not bool for key in ('showIds','showTail','showGraph','hideCompleted'))):
        raise ValueError('Track width must be 0.5–20 pixels; past/future lengths must be nonnegative whole time steps')
    return copy.deepcopy(value)


def snapshot(layer):
    kind=layer._type_string
    row={'kind':kind,**{key:getattr(layer,native) for key,native in NATIVE[kind].items()}}
    row['width']=float(row['width'])
    if kind=='vectors':
        row.update(length=float(row['length']),style=str(row['style']),projection=str(row['projection']))
    else:
        row.update(tail=int(row['tail']),head=int(row['head']))
    return settings(row,kind)


def apply(layer,value):
    for key,native in NATIVE[value['kind']].items():
        setattr(layer,native,value[key])


def projected_ranges(viewer,bindings,value):
    """Ask pinned napari for extents without changing a live layer or firing events.

    Vector display length changes its extent. A shallow read-only projection uses
    native geometry/units aggregation; only its private length and extent cache
    differ. Data, transforms and event objects are never written through it.
    """
    wanted={row['key']:row.get('pathAppearance') for row in value['layers']}
    layers=[]
    for layer in viewer.layers:
        appearance=wanted.get(bindings.key_for(layer))
        if appearance and appearance['kind']=='vectors' and layer._type_string=='vectors' and appearance['length']!=layer.length:
            # Layer.__copy__ reconstructs and copies data. Bypass that hook: this
            # unobserved shell is used only for the native extent property.
            projected=object.__new__(type(layer))
            object.__setattr__(projected,'__dict__',layer.__dict__.copy())
            projected.__dict__.pop('extent',None)
            projected._length=appearance['length']
            layers.append(projected)
        else:
            layers.append(layer)
    extent=viewer.layers.get_extent(layers)
    ranges=[[float(a),float(b),float(step)] for a,b,step in zip(extent.world[0],extent.world[1],extent.step)]
    if any(not math.isfinite(n) or abs(n)>2**53-1 for row in ranges for n in row):
        raise ValueError('This display length exceeds the scientific view coordinate allowance; use a smaller multiplier')
    if any(step<=0 or b<a or not math.isfinite((b-a)/step) or (b-a)/step>=2**53-1 for a,b,step in ranges):
        raise ValueError('This display length exceeds the scientific slice-count allowance; use a smaller multiplier')
    return ranges


def admit(viewer,layer,value):
    if layer._type_string=='tracks':
        if value['width']>layer._max_width:
            raise ValueError('This native Track layer cannot display that line width')
    elif value['length']!=layer.length:
        projected_ranges(viewer,SimpleNamespace(key_for=lambda candidate:'target' if candidate is layer else None),
                         {'layers':[{'key':'target','pathAppearance':value}]})


def refresh_ranges(viewer):
    # Pinned napari invalidates Vector extents on length changes but does not
    # refresh Dims.range. A truthful frame must describe the displayed endpoints,
    # including changes made through Python; otherwise remember/reopen disagrees.
    if any(layer._type_string=='vectors' for layer in viewer.layers):
        viewer.dims.range=viewer.layers._ranges
