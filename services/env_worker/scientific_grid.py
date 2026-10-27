"""Native layer-grid presentation with viewport admission, never data transforms."""
import copy
import math
from types import SimpleNamespace


def settings(value):
    integer = lambda n: type(n) is int and abs(n) <= 2**53 - 1
    if (type(value) is not dict or set(value) != {'enabled', 'shape', 'stride', 'spacing'}
            or type(value['enabled']) is not bool
            or type(value['shape']) is not list or len(value['shape']) != 2
            or not all(integer(n) and (n == -1 or n > 0) for n in value['shape'])
            or not integer(value['stride']) or value['stride'] == 0
            or type(value['spacing']) not in (int, float)
            or not 0 <= value['spacing'] <= 1500 or not math.isfinite(value['spacing'])):
        raise ValueError('Grid needs positive rows/columns (or -1 for automatic), a nonzero stride, and spacing from 0 to 1500')
    return copy.deepcopy(value)


def snapshot(grid):
    return settings({'enabled': grid.enabled, 'shape': list(grid.shape),
                     'stride': grid.stride, 'spacing': grid.spacing})


def admit(viewer, value, size, *, visibility=None):
    value = settings(value)
    if value['enabled']:
        width, height = size
        if value['shape'][0] > height // 20 or value['shape'][1] > width // 20:
            raise ValueError('This grid would make panels smaller than 20 pixels. Use fewer rows/columns, group layers with Stride, or enlarge the view')
        # Construct a detached native model: calculate native grouping without
        # emitting viewer events or allocating any viewboxes before admission.
        candidate = type(viewer.grid)(**value)
        layers = viewer.layers if visibility is None else [SimpleNamespace(visible=flag) for flag in visibility]
        rows, columns = candidate.actual_shape(layers)
        if rows > height // 20 or columns > width // 20:
            raise ValueError('This grid would make panels smaller than 20 pixels. Use fewer rows/columns, group layers with Stride, or enlarge the view')
        raw = candidate._compute_canvas_spacing_raw(size, layers)
        safe = candidate._compute_canvas_spacing(size, layers)
        if raw > safe:
            value['spacing'] = safe
    return value


def apply(grid, value):
    # Configure while disabled so intermediate shapes cannot allocate a larger
    # grid than the admitted final shape. The caller owns failure recovery.
    grid.enabled = False
    grid.spacing = 0
    grid.shape = tuple(value['shape'])
    grid.stride = value['stride']
    grid.spacing = value['spacing']
    grid.enabled = value['enabled']
