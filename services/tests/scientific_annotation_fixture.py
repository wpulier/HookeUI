"""The P4 (H07) annotation fixture: one 4D uint64 Labels array with known objects, an anisotropic calibration and
independent expectations computed here with numpy only.

`CODE` builds the array. It is executed verbatim by the test process (to seed the saved document) and by the plain
pinned-napari reference process, so both start from the same bytes. The objects sit on nonzero slices so a gesture
on the wrong plane shows up, and one of them carries an ID above 2**53, which no JavaScript number holds exactly.
"""
from __future__ import annotations

import numpy as np

AXES = ('t', 'z', 'y', 'x')
SHAPE = (3, 4, 36, 44)
SCALE = (2.0, 1.5, 0.5, 0.25)  # anisotropic: a y step is twice an x step
TRANSLATE = (10.0, -3.0, 5.0, 7.0)
UNITS = ('second', 'micrometer', 'micrometer', 'micrometer')
PLANE = (1, 2)  # (t, z) of the edited plane
BIG = 2 ** 60 + 3  # a label above 2**53
RING = 9  # two disconnected blobs of one label on the edited plane
BOX = 5

CODE = r'''
import numpy as _np
def _annotation_fixture():
    data = _np.zeros((3, 4, 36, 44), dtype=_np.uint64)
    data[1, 2, 4:12, 6:18] = 5                                   # a box
    data[1, 2, 20:30, 20:34] = 1152921504606846979               # 2**60 + 3, with a hole
    data[1, 2, 24:26, 26:28] = 0
    data[1, 2, 2:6, 30:36] = 9                                   # label 9: two disconnected blobs
    data[1, 2, 14:18, 36:42] = 9
    data[0, 2, 4:12, 6:18] = 7                                   # the same box one time step earlier
    data[2, 1, 10:20, 10:20] = 3                                 # and another plane entirely
    return data
'''


def values():
    namespace = {}
    exec(CODE, namespace)
    data = namespace['_annotation_fixture']()
    assert data.shape == SHAPE and data.dtype == np.uint64 and int(data.max()) == BIG
    return data


def scientific():
    """The retained descriptor: instance labels with their own diagonal calibration (the integration applies it)."""
    return {'semantic': 'instance-labels',
            'calibration': {'space': {'session': 'h07-fixture', 'id': 'h07-fixture-space', 'revision': 1}, 'axes': list(AXES), 'units': list(UNITS),
                            'affine': [[SCALE[i] if j == i else 0.0 for j in range(4)] + [TRANSLATE[i]]
                                       for i in range(4)]}}


def world(index):
    """World coordinates of a data index (napari's data_to_world for a diagonal affine)."""
    return [TRANSLATE[i] + float(index[i]) * SCALE[i] for i in range(len(index))]


def changed(before, after):
    """Every voxel that differs: sorted (index..., old, new), the independent record of an edit."""
    where = np.argwhere(before != after)
    return [[*map(int, idx), int(before[tuple(idx)]), int(after[tuple(idx)])] for idx in where]


def connected(mask, seed):
    """4-connected component of `mask` containing `seed` (2D), by breadth-first search; no napari or scipy."""
    height, width = mask.shape
    seen, todo = np.zeros_like(mask, dtype=bool), [tuple(seed)]
    while todo:
        y, x = todo.pop()
        if not (0 <= y < height and 0 <= x < width) or seen[y, x] or not mask[y, x]:
            continue
        seen[y, x] = True
        todo += [(y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)]
    return seen


def inside_polygon(shape, corners):
    """Pixels whose centre is inside a polygon of (row, col) corners, by the even-odd crossing rule."""
    rows, cols = np.mgrid[:shape[0], :shape[1]]
    inside = np.zeros(shape, dtype=bool)
    n = len(corners)
    for i in range(n):
        (y0, x0), (y1, x1) = corners[i], corners[(i + 1) % n]
        crosses = ((y0 > rows) != (y1 > rows))
        with np.errstate(divide='ignore', invalid='ignore'):
            at = (x1 - x0) * (rows - y0) / (y1 - y0) + x0
        inside ^= crosses & (cols < at)
    return inside


def dilate(mask):
    """Grown by one pixel (4-neighbourhood)."""
    out = mask.copy()
    out[1:] |= mask[:-1]; out[:-1] |= mask[1:]; out[:, 1:] |= mask[:, :-1]; out[:, :-1] |= mask[:, 1:]
    return out


def erode(mask):
    """Shrunk by one pixel: only pixels whose four neighbours are all in the mask."""
    return ~dilate(~mask)
