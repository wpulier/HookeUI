"""Admission for the pinned napari Labels history and bounded dimensional gestures."""
from collections import deque
import math
import sys

HISTORY_BYTES = 64 << 20
HISTORY_OBJECTS = 20000
MAX_PAINT_CALLS = 4096
ATOM_ALLOWANCE = 4096


def retained_bytes(roots, *, maximum=HISTORY_BYTES, objects=HISTORY_OBJECTS):
    import numpy as np
    pending, seen, total = [roots], set(), 0
    while pending:
        value = pending.pop()
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        if len(seen) > objects:
            raise ValueError('Edit history has too many objects; save the changes and start a new session')
        charge = sys.getsizeof(value)
        if type(value) is np.ndarray:
            if value.dtype.hasobject:
                raise ValueError('Object arrays cannot enter scientific edit history')
            if value.base is not None:
                if type(value.base) not in (np.ndarray, bytes):
                    raise ValueError('Edit history requires owned native storage')
                pending.append(value.base)
            else:
                charge = max(charge, value.nbytes)
        elif type(value) in (list, tuple, deque) or isinstance(value, tuple) and type(value).__name__ == '_MaskedPaintAtom':
            if len(value) > objects:
                raise ValueError('Edit history has too many objects')
            pending.extend(value)
        elif type(value) is slice:
            pending.extend((value.start, value.stop, value.step))
        elif value is not None and type(value) not in (int, float, bool, bytes, str) and not isinstance(value, (np.integer, np.bool_)):
            raise ValueError('This native edit history needs a qualified adapter')
        total += charge
        if total > maximum:
            raise ValueError('Edit history exceeds its memory allowance; save the changes and start a new session')
    return total


def stroke_calls(points, brush_size, *, maximum=MAX_PAINT_CALLS):
    if (type(brush_size) not in (int, float) or not math.isfinite(brush_size) or brush_size < 1
            or type(points) is not list or not 1 <= len(points) <= 256):
        raise ValueError('Choose a positive brush size and a bounded stroke')
    size, count, previous = None, 1, None
    for point in points:
        if (type(point) not in (tuple, list) or not 2 <= len(point) <= 8 or size is not None and len(point) != size
                or any(type(n) not in (int, float) or not math.isfinite(n) for n in point)):
            raise ValueError('Stroke coordinates must be finite native positions')
        size = len(point)
        if previous is not None:
            steps = 4 * max(abs(a-b) for a,b in zip(previous, point)) / brush_size
            if not math.isfinite(steps) or steps > maximum:
                raise ValueError('Use a shorter stroke or a larger brush')
            count += max(1, round(steps))
        if count > maximum:
            raise ValueError('Use a shorter stroke or a larger brush')
        previous = point
    return count


def paint_bound(layer, brush_size, calls, dimensions=2):
    axes = tuple(layer._slice_input.order[-dimensions:])
    scales = [abs(float(layer.scale[axis])) for axis in axes]
    if any(not math.isfinite(n) or n <= 0 for n in scales):
        raise ValueError('The label brush requires finite, nonzero sampling')
    radius, smallest = math.floor(brush_size / 2) + .5, min(scales)
    box = math.prod(min(layer.data.shape[axis], 2*math.floor(radius/(scale/smallest))+1)
                    for axis, scale in zip(axes, scales))
    return calls * (box * (layer.data.dtype.itemsize+1) + ATOM_ALLOWANCE)


def polygon_region(points):
    """Validate a finite plane polygon; bound its native integer bounding box."""
    if (type(points) is not list or not 3 <= len(points) <= 256
            or any(type(p) not in (list, tuple) or len(p) != 2
                   or any(type(n) not in (int, float) or not math.isfinite(n) for n in p) for p in points)):
        raise ValueError('A polygon needs 3 to 256 finite corners in one displayed plane')
    # Native Labels.paint_polygon truncates data coordinates to integers.
    corners = list(dict.fromkeys(tuple(int(n) for n in p) for p in points))
    if len(corners) < 3:
        raise ValueError('A polygon needs at least three distinct data pixels')
    a, b = corners[:2]
    if not any((b[0]-a[0])*(c[1]-a[1]) != (b[1]-a[1])*(c[0]-a[0]) for c in corners[2:]):
        raise ValueError('A polygon needs corners that enclose a region')
    return math.prod(max(p[axis] for p in corners)-min(p[axis] for p in corners)+1 for axis in (0,1))
