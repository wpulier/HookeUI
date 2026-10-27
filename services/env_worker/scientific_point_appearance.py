"""Native point rendering controls; per-point scientific values stay untouched."""
import copy
import math

FIELDS = {'shading', 'projection', 'antialiasing', 'canvasLimits'}


def settings(value):
    def finite(n):
        try: return type(n) in (int, float) and math.isfinite(n)
        except OverflowError: return False
    if (type(value) is not dict or set(value) != FIELDS
            or type(value['shading']) is not str or value['shading'] not in ('none', 'spherical')
            or type(value['projection']) is not str or value['projection'] not in ('none', 'all', 'rescale_linear', 'rescale_spherical')
            or not finite(value['antialiasing']) or value['antialiasing'] < 0):
        raise ValueError('Choose native point shading, slice visibility and nonnegative antialiasing')
    limits = value['canvasLimits']
    if (type(limits) is not list or len(limits) != 2 or not all(finite(n) for n in limits)
            or not 0 <= limits[0] <= limits[1]):
        raise ValueError('Point canvas size limits must be finite, nonnegative and in increasing order')
    return copy.deepcopy(value)


def snapshot(layer):
    return settings({'shading': str(layer.shading), 'projection': str(layer.projection_mode),
                     'antialiasing': float(layer.antialiasing),
                     'canvasLimits': [float(n) for n in layer.canvas_size_limits]})


def apply(layer, value):
    layer.shading = value['shading']
    layer.projection_mode = value['projection']
    layer.antialiasing = value['antialiasing']
    layer.canvas_size_limits = tuple(value['canvasLimits'])
