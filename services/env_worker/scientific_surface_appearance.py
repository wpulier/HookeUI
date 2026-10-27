"""Native mesh presentation, independent of retained vertices and measurements."""
import copy
import math

FIELDS={'shading','contrastLimits','autoContrast','gamma','wireframe','faceNormals','vertexNormals'}


def settings(value):
    def finite(n):
        try:return type(n) in (int,float) and math.isfinite(n)
        except OverflowError:return False
    if type(value) is not dict or set(value)!=FIELDS:
        raise ValueError('Choose complete Surface display settings')
    if (type(value['shading']) is not str or value['shading'] not in ('none','flat','smooth')
            or type(value['autoContrast']) is not bool or not finite(value['gamma']) or value['gamma']<=0):
        raise ValueError('Choose native surface shading and a positive finite gamma')
    limits=value['contrastLimits']
    if type(limits) is not list or len(limits)!=2 or not all(finite(n) for n in limits) or limits[0]>=limits[1]:
        raise ValueError('Enter finite surface contrast limits in increasing order')
    for key in ('wireframe','faceNormals','vertexNormals'):
        row=value[key];fields={'visible','width','color'}|({'length'} if key!='wireframe' else set())
        if (type(row) is not dict or set(row)!=fields or type(row['visible']) is not bool
                or not finite(row['width']) or row['width']<0
                or key!='wireframe' and not finite(row['length'])):
            raise ValueError('Surface lines need a finite nonnegative width and finite normal length')
        color=row['color']
        if type(color) is not list or len(color)!=4 or not all(finite(n) and 0<=n<=1 for n in color):
            raise ValueError('Surface line colors require four RGBA values from zero to one')
    return copy.deepcopy(value)


def snapshot(layer):
    def line(native,normal=False):
        return {'visible':bool(native.visible),'width':float(native.width),
                'color':[float(n) for n in native.color],**({'length':float(native.length)} if normal else {})}
    return settings({'shading':str(layer.shading),'contrastLimits':[float(n) for n in layer.contrast_limits],
        'autoContrast':bool(layer.auto_contrast),'gamma':float(layer.gamma),
        'wireframe':line(layer.wireframe),'faceNormals':line(layer.normals.face,True),
        'vertexNormals':line(layer.normals.vertex,True)})


def apply(layer,value):
    layer.shading=value['shading']
    layer.gamma=value['gamma']
    layer.auto_contrast=False
    layer.contrast_limits=value['contrastLimits']
    layer.auto_contrast=value['autoContrast']
    for key,native in (('wireframe',layer.wireframe),('faceNormals',layer.normals.face),('vertexNormals',layer.normals.vertex)):
        for field,setting in value[key].items():setattr(native,field,setting)


def events(layer):
    return (layer.wireframe.events,layer.normals.face.events,layer.normals.vertex.events) if layer._type_string=='surface' else ()


def retained(value):
    """Admit native saved settings without constructing a GUI or feature table."""
    from . import scientific_structured_state as state
    keys=('shading','contrast_limits','auto_contrast','gamma','wireframe','normals')
    packet={'format':state.FORMAT,'state':{'type':'dict','value':{key:value[key] for key in keys}}}
    state.validate(packet)
    def controls(node):
        kind=node['type']
        if kind in ('scalar','integer','float'):return
        if kind=='array' and node['dtype'][1] in 'iuf':return
        if kind=='numpy-scalar':controls(node['value']);return
        if kind in ('list','tuple','dict'):
            for item in node['value'].values() if kind=='dict' else node['value']:controls(item)
            return
        raise ValueError('Surface display settings require scalar and numeric controls')
    controls(packet['state'])
    native=state.unpack(packet)
    def line(row,normal=False):
        expected={'visible','width','color'}|({'length','mode'} if normal else set())
        if type(row) is not dict or set(row)!=expected:
            raise ValueError('Retained surface lines require complete native controls')
        # Pinned napari discards supplied modes and restores its fixed defaults.
        if normal and (type(row['mode']) is not int or row['mode'] != 1):
            raise ValueError('Retained normals require a native face or vertex mode')
        import numpy as np
        color=row['color']
        if type(color) is np.ndarray:color=color.tolist()
        return {key:color if key=='color' else val for key,val in row.items() if key!='mode'}
    limits=native['contrast_limits']
    if type(limits) is tuple:limits=list(limits)
    normals=native['normals']
    if type(normals) is not dict or set(normals)!={'face','vertex'}:
        raise ValueError('Retained surfaces require both face and vertex normal settings')
    return settings({'shading':native['shading'],'contrastLimits':limits,'autoContrast':native['auto_contrast'],
        'gamma':native['gamma'],'wireframe':line(native['wireframe']),
        'faceNormals':line(normals['face'],True),'vertexNormals':line(normals['vertex'],True)})
