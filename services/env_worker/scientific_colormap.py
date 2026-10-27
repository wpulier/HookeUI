"""Native intensity color maps, retained by value rather than global registry name."""
import copy
import math

MAX_COLORS = 1024


def name(value):
    return type(value) is str and 0 < len(value.encode('utf-16-le',errors='surrogatepass'))//2 <= 240


def settings(value):
    def unit(v):
        return type(v) in (int,float) and 0 <= v <= 1 and math.isfinite(v)
    def color(v):
        return type(v) is list and len(v)==4 and all(unit(n) for n in v)
    fields={'name','colors','controls','interpolation','nanColor','highColor','lowColor'}
    if (type(value) is not dict or set(value)!=fields
            or not name(value['name'])
            or type(value['interpolation']) is not str or value['interpolation'] not in ('linear','zero')
            or type(value['colors']) is not list or not 1<=len(value['colors'])<=MAX_COLORS
            or not all(color(row) for row in value['colors'])
            or type(value['controls']) is not list
            or len(value['controls'])!=len(value['colors'])+int(value['interpolation']=='zero')
            or not all(unit(n) for n in value['controls'])
            or value['controls'][0]!=0 or (len(value['controls'])>1 and value['controls'][-1]!=1)
            or value['controls']!=sorted(value['controls'])
            or not color(value['nanColor'])
            or any(value[key] is not None and not color(value[key]) for key in ('highColor','lowColor'))):
        raise ValueError('Use 1–1024 RGBA colors between 0 and 1, ordered control positions from 0 to 1, and linear or stepped color interpolation. Stepped colors need one extra control position.')
    return copy.deepcopy(value)


def snapshot_map(cmap):
    return settings({'name':cmap.name,'colors':cmap.colors.tolist(),'controls':cmap.controls.tolist(),
        'interpolation':str(cmap.interpolation),'nanColor':cmap.nan_color.tolist(),
        'highColor':None if cmap.high_color is None else cmap.high_color.tolist(),
        'lowColor':None if cmap.low_color is None else cmap.low_color.tolist()})


def choices(layer):
    # The native registry contains the palettes actually available in this host.
    return list(layer.colormaps)


def check_choices(value):
    if (type(value) is not list or len(value)>1024 or any(not name(n) for n in value)
            or len(set(value))!=len(value)):
        raise ValueError('The image color choices exceed their supported metadata allowance')
    return value


def prepare(layer, value, *, registry=None):
    if type(value) is dict and set(value)=={'name'}:
        if type(value['name']) is not str or value['name'] not in choices(layer):
            raise ValueError('Choose a color map available in this scientific host')
        from napari.utils.colormaps import ensure_colormap
        value=snapshot_map(ensure_colormap(value['name']))
    value=settings(value)
    from napari.utils.colormaps import Colormap
    # Construct and normalize before mutation, including float32 control rounding.
    cmap=Colormap(value['colors'],name=value['name'],controls=value['controls'],interpolation=value['interpolation'],
                 nan_color=value['nanColor'],high_color=value['highColor'],low_color=value['lowColor'])
    from napari.utils.colormaps.colormap_utils import AVAILABLE_COLORMAPS, increment_name
    registry=AVAILABLE_COLORMAPS if registry is None else registry
    if cmap.name in registry and not registry[cmap.name].same_colors(cmap):
        cmap.name=increment_name(cmap.name,registry)
    return cmap,snapshot_map(cmap)


def reserve(frame, maps):
    """New native palette names appear on every image, so budget the whole frame."""
    from . import scientific_view_wire as wire
    projected=copy.deepcopy(frame)
    for row in projected['layers']:
        if 'imageColormap' not in row:
            continue
        if row['id'] in maps:
            row['imageColormap']=snapshot_map(maps[row['id']])
        for cmap in maps.values():
            if cmap.name not in row['colormapChoices']:
                row['colormapChoices'].append(cmap.name)
        check_choices(row['colormapChoices'])
    wire.bounded(projected,wire.MAX_BYTES-256)
