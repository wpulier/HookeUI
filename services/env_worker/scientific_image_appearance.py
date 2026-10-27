"""Validated image display settings; no native data or calibration writes."""
import copy
import math

RENDERING = ('translucent', 'additive', 'iso', 'mip', 'minip', 'attenuated_mip', 'average')
FIELDS = {'contrastLimits', 'gamma', 'rendering', 'isoThreshold', 'attenuation', 'autoContrast'}


def settings(value):
    def finite(v):
        try:
            return type(v) in (int, float) and math.isfinite(v)
        except OverflowError:
            return False
    if (type(value) is not dict or set(value) != FIELDS
            or type(value['contrastLimits']) is not list or len(value['contrastLimits']) != 2
            or not all(finite(v) for v in value['contrastLimits'])
            or value['contrastLimits'][0] >= value['contrastLimits'][1]
            or not finite(value['gamma']) or value['gamma'] <= 0
            or type(value['rendering']) is not str or value['rendering'] not in RENDERING
            or not finite(value['isoThreshold']) or not finite(value['attenuation']) or value['attenuation'] < 0
            or type(value['autoContrast']) is not bool):
        raise ValueError('Choose increasing contrast limits, a positive gamma, a volume rendering mode and finite rendering settings.')
    return copy.deepcopy(value)


def snapshot(layer):
    return settings({'contrastLimits': [float(v) for v in layer.contrast_limits], 'gamma': float(layer.gamma),
        'rendering': str(layer.rendering), 'isoThreshold': float(layer.iso_threshold),
        'attenuation': float(layer.attenuation), 'autoContrast': bool(layer.auto_contrast)})


def apply(layer, value):
    # Validation happens before admission. Setter/callback errors after this point
    # remain uncertain mutations and cannot claim that nothing was applied.
    if not value['autoContrast']:
        layer.auto_contrast = False
        layer.contrast_limits = value['contrastLimits']
    layer.gamma = value['gamma']
    layer.rendering = value['rendering']
    layer.iso_threshold = value['isoThreshold']
    layer.attenuation = value['attenuation']
    if value['autoContrast']:
        layer.auto_contrast = True
