"""Bounded native color controls shared by retained Points and Vectors."""
from enum import Enum
from . import scientific_structured_state as state

FIELDS=frozenset(('current_color','color_mode','color_properties','continuous_colormap','contrast_limits','categorical_colormap','colors'))

def normalizer():
    import numpy as np
    budget=state._Budget()
    def controls(item,depth=0):
        budget.visit(depth)
        if type(item).__module__.startswith('napari.') and issubclass(type(item),Enum):
            return item.value
        if type(item).__module__ in ('numpy','napari.utils.color','napari.utils.events.custom_types') and issubclass(type(item),np.ndarray) and item.dtype.kind!='O':
            budget.array(item.nbytes)
            return np.asarray(item)
        if type(item) is np.ndarray and item.dtype.kind=='O':
            if item.size+budget.nodes>state.MAX_NODES:
                raise ValueError('Native color controls exceed the bounded state allowance')
            values=np.empty(item.shape,dtype=object)
            for index in np.ndindex(item.shape): values[index]=controls(item[index],depth+1)
            return values
        if type(item) in (dict,list,tuple) and len(item)+budget.nodes>state.MAX_NODES:
            raise ValueError('Native color controls exceed the bounded state allowance')
        if type(item) is dict: return {k:controls(v,depth+1) for k,v in item.items()}
        if type(item) is list: return [controls(v,depth+1) for v in item]
        if type(item) is tuple: return tuple(controls(v,depth+1) for v in item)
        return item
    return controls

def snapshot(manager,controls):
    value=manager.model_dump()
    palette=manager.categorical_colormap
    value['categorical_colormap']={'colormap':list(palette.colormap.items()),
        'fallback_color':{'values':palette.fallback_color.values,'current_index':palette.fallback_color.current_index}}
    return controls(value)

def admit(manager):
    palette=manager.categorical_colormap
    if len(palette.colormap)>state.MAX_NODES or palette.fallback_color.values.nbytes>state.MAX_ARRAY_BYTES:
        raise ValueError('These colors need a bulk structured writer; the private layer was kept')
    continuous=manager.continuous_colormap
    if continuous.colors.nbytes+continuous.controls.nbytes>state.MAX_ARRAY_BYTES:
        raise ValueError('This continuous palette exceeds the bounded state allowance')
    properties=manager.color_properties
    if properties is not None and len(properties.values)>state.MAX_NODES:
        raise ValueError('These color properties exceed the bounded state allowance')

def restored(record):
    from napari.layers.utils.color_manager import ColorManager
    record['categorical_colormap']['colormap']=dict(record['categorical_colormap']['colormap'])
    return ColorManager(**record)


def numeric(node):
    """Decode only bounded numeric controls; never construct tables in the parent."""
    import numpy as np
    def check(item):
        kind=item['type']
        if kind=='scalar' and type(item['value']) in (int,float):return
        if kind=='array' and item['dtype'][1] in 'iuf':return
        if kind=='numpy-scalar':check(item['value']);return
        if kind in ('list','tuple'):
            for child in item['value']:check(child)
            return
        raise ValueError('Native appearance requires bounded native numeric controls')
    check(node)
    try:return np.asarray(state.unpack({'format':state.FORMAT,'state':node}))
    except (TypeError,ValueError) as error:raise ValueError('Native appearance needs regular numeric controls') from error



def validate_native_colormap(palette):
    """Validate saved native palettes within the existing typed-state allowance."""
    import numpy as np
    def rgba(node,shape):
        data=numeric(node)
        if data.shape!=shape or not np.isfinite(data).all() or np.any(data<0) or np.any(data>1):
            raise ValueError('Native colors require finite RGBA values between zero and one')
    if (palette['type']!='dict' or set(palette['value'])!={'colors','name','interpolation','controls','nan_color','high_color','low_color'}):
        raise ValueError('Native colormaps require complete native color and control arrays')
    cmap=palette['value'];interpolation=cmap['interpolation'];name=cmap['name']
    if (interpolation['type']!='scalar' or interpolation['value'] not in ('linear','zero')
            or name['type']!='scalar' or type(name['value']) is not str):
        raise ValueError('Native colormaps require a native name and interpolation mode')
    palette_colors=numeric(cmap['colors']);controls=numeric(cmap['controls'])
    if palette_colors.ndim!=2 or not len(palette_colors):raise ValueError('Native colormaps need RGBA colors')
    rgba(cmap['colors'],(len(palette_colors),4))
    count=len(palette_colors)+int(interpolation['value']=='zero')
    if (controls.shape!=(count,) or not np.isfinite(controls).all() or controls[0]!=0
            or count>1 and controls[-1]!=1 or np.any(controls[1:]<controls[:-1])):
        raise ValueError('Native colormap controls must match their colors in order from zero to one')
    rgba(cmap['nan_color'],(4,))
    for key in ('high_color','low_color'):
        if cmap[key]!={'type':'scalar','value':None}:rgba(cmap[key],(4,))
