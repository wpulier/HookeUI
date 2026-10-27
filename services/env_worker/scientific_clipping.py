"""Native clipping-plane intent, independent of scientific array values."""
import copy
import math

# Bound native shader generation independently of the protocol byte budget.
MAX_PLANES = 64


def settings(value):
    def vector(raw):
        return (type(raw) is list and len(raw) == 3 and all(type(n) in (int, float)
                and abs(n) <= 2**53 - 1 and math.isfinite(n) for n in raw))
    if type(value) is not list:
        raise ValueError('Clipping needs a list of planes. Remove a plane to stop clipping there.')
    if len(value) > MAX_PLANES:
        raise ValueError(f'This view supports at most {MAX_PLANES} clipping planes per layer. Remove planes before applying.')
    for plane in value:
        if (type(plane) is not dict or set(plane) != {'position', 'normal', 'enabled'}
                or not vector(plane['position']) or not vector(plane['normal'])
                or sum(n*n for n in plane['normal']) == 0 or type(plane['enabled']) is not bool):
            raise ValueError('Each clipping plane needs finite coordinates, a nonzero normal and an enabled choice.')
    return copy.deepcopy(value)


def snapshot(layer):
    return settings([{'position':[float(n) for n in plane.position],
                      'normal':[float(n) for n in plane.normal], 'enabled':bool(plane.enabled)}
                     for plane in layer.experimental_clipping_planes])


def events(layer):
    planes = getattr(layer, 'experimental_clipping_planes', None)
    # Native EventedList forwards child events, including new planes.
    return [] if planes is None else [planes.events]


def apply(layer, value):
    # The whole list is validated before admission; native setters/callbacks may
    # still fail after starting. The existing owner reports that as uncertain.
    layer.experimental_clipping_planes = value


def same_normals(expected, actual):
    """Allow only native unit-normal renormalization roundoff on restoration."""
    for left, right in zip(expected, actual):
        if all(math.isclose(x,y,rel_tol=1e-12,abs_tol=1e-12) for x,y in zip(left['normal'],right['normal'])):
            right['normal'] = left['normal']
