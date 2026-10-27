"""The asymmetric calibrated 5D fixture of acceptance.md §3 and its independent expected values.

TCZYX with unequal sizes, anisotropic calibration, a nonzero origin and index-encoded values: every voxel holds its
own flat C-order index, so a value read anywhere names exactly which voxel was shown. The expectations here are
computed from the definition with NumPy, never from napari or Hooke.
"""
import numpy as np

AXES = ('t', 'c', 'z', 'y', 'x')
SHAPE = (5, 3, 7, 11, 13)
SCALE = (2.5, 1.0, 0.75, 0.4, 0.3)
TRANSLATE = (10.0, 0.0, -3.0, 5.0, 7.0)
UNITS = ('second', 'pixel', 'micrometer', 'micrometer', 'micrometer')


def values(dtype='<u2'):
    return np.arange(int(np.prod(SHAPE)), dtype=dtype).reshape(SHAPE)


def scientific():
    """The retained descriptor's meaning (uncalibrated storage; calibration is applied in the session)."""
    return {'semantic': 'intensity'}


def value_at(index):
    """The fixture value at an integer index tuple."""
    return int(np.ravel_multi_index(tuple(int(i) for i in index), SHAPE))


def world(step, axis):
    """World coordinate of an integer step along one axis."""
    return TRANSLATE[axis] + step * SCALE[axis]


def world_range(axis):
    """napari's Dims.range for a layer: (min world, max world, step) of its pixel centres."""
    return (TRANSLATE[axis], TRANSLATE[axis] + (SHAPE[axis] - 1) * SCALE[axis], SCALE[axis])


def roll(order, rollable=None):
    """Dims.roll as napari 0.7 defines it: rotate the rollable, non-singleton axes by one place."""
    rollable = rollable or [True] * len(order)
    movable = [axis for axis in order if rollable[axis] and SHAPE[axis] > 1]
    rotated = movable[-1:] + movable[:-1]
    out, it = [], iter(rotated)
    for axis in order:
        out.append(next(it) if axis in movable else axis)
    return out


def transpose(order):
    out = list(order)
    out[-2], out[-1] = out[-1], out[-2]
    return out


def displayed(order, ndisplay):
    return list(order[-ndisplay:])


def plane(steps, order, ndisplay=2):
    """The displayed 2D plane of raw values for integer steps on the other axes, axes in display order."""
    data = values()
    index = [slice(None) if axis in order[-ndisplay:] else steps[axis] for axis in range(len(SHAPE))]
    view = data[tuple(index)]
    remaining = [axis for axis in range(len(SHAPE)) if axis in order[-ndisplay:]]
    return np.transpose(view, [remaining.index(axis) for axis in order[-ndisplay:]])


def slab(steps, axis, left, right, mode, order, ndisplay=2):
    """A thick slice of `axis` from step-left to step+right (inclusive, clipped) reduced with mode."""
    data = values().astype('float64')
    lo, hi = max(0, steps[axis] - left), min(SHAPE[axis] - 1, steps[axis] + right)
    index = [slice(None) if a in order[-ndisplay:] else (slice(lo, hi + 1) if a == axis else steps[a])
             for a in range(len(SHAPE))]
    view = data[tuple(index)]
    kept = [a for a in range(len(SHAPE)) if a in order[-ndisplay:] or a == axis]
    reduce = {'sum': np.sum, 'mean': np.mean, 'max': np.max, 'min': np.min}[mode]
    reduced = reduce(view, axis=kept.index(axis))
    remaining = [a for a in kept if a != axis]
    return np.transpose(reduced, [remaining.index(a) for a in order[-ndisplay:]])
