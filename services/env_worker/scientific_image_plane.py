"""Image slicing planes use sliced data coordinates, not scientific-data edits."""
import copy
import math

FIELDS = {'depiction', 'position', 'normal', 'thickness'}


def settings(value):
    def finite(v):
        try:
            return type(v) in (int, float) and math.isfinite(v) and abs(v) <= 2**53 - 1
        except OverflowError:
            return False
    def vector(v):
        return type(v) is list and len(v) == 3 and all(finite(n) for n in v)
    if (type(value) is not dict or set(value) != FIELDS
            or type(value['depiction']) is not str or value['depiction'] not in ('volume', 'plane')
            or not vector(value['position']) or not vector(value['normal'])
            or sum(n*n for n in value['normal']) == 0
            or not finite(value['thickness']) or value['thickness'] < 0):
        raise ValueError('Choose volume or plane, finite data coordinates, a nonzero normal and nonnegative thickness.')
    return copy.deepcopy(value)


def snapshot(layer):
    plane = layer.plane
    return settings({'depiction':str(layer.depiction), 'position':[float(n) for n in plane.position],
                     'normal':[float(n) for n in plane.normal], 'thickness':float(plane.thickness)})


def apply(layer, value):
    # Complete validation precedes all setters. Native callbacks run in the
    # confined owning GUI thread; failures after admission stay uncertain.
    layer.plane.position = value['position']
    layer.plane.normal = value['normal']
    layer.plane.thickness = value['thickness']
    layer.depiction = value['depiction']
