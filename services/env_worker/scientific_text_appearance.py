"""Native feature-text presentation without rewriting strings, colors or features."""
import copy
import math

FIELDS={'visible','size','scaling','blending','anchor','translation','rotation'}
ANCHORS=('center','upper_left','upper_right','lower_left','lower_right')
BLENDING=('translucent','translucent_no_depth','additive','minimum','multiplicative')


def settings(value, ndim):
    def finite(n):
        try:return type(n) in (int,float) and math.isfinite(n)
        except OverflowError:return False
    if (type(value) is not dict or set(value)!=FIELDS
            or type(value['visible']) is not bool or type(value['scaling']) is not bool
            or not finite(value['size']) or value['size']<=0
            or not finite(value['rotation'])
            or type(value['anchor']) is not str or value['anchor'] not in ANCHORS
            or type(value['blending']) is not str or value['blending'] not in BLENDING):
        raise ValueError('Text display needs a positive size, finite rotation and native anchor/blending choices')
    offset=value['translation']
    if not finite(offset) and not (type(offset) is list and len(offset) in (1,ndim) and all(finite(n) for n in offset)):
        raise ValueError('Text offset needs one finite value or one value per layer axis in data coordinates')
    return copy.deepcopy(value)


def snapshot(layer):
    text=layer.text
    return settings({'visible':bool(text.visible),'size':float(text.size),'scaling':bool(text.scaling),
        'blending':str(text.blending),'anchor':str(text.anchor),'translation':text.translation.tolist(),
        'rotation':float(text.rotation)},layer.ndim)


def apply(layer,value):
    for key,setting in value.items():setattr(layer.text,key,setting)


def events(layer):
    return (layer.text.events,) if layer._type_string in ('points','shapes') else ()
