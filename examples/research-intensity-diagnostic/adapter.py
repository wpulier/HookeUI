"""Deterministic RGB8 statistics; does not change the measured input."""
import numpy as np


def run(picture, params):
    if (not isinstance(picture, np.ndarray) or picture.dtype != np.uint8
            or picture.ndim != 3 or picture.shape[2] != 3 or picture.size == 0):
        return {'unsupported': 'requires a nonempty uint8 RGB image'}
    rows = []
    for index, channel in enumerate(('R', 'G', 'B')):
        values = picture[:, :, index]
        rows.append([channel, int(values.size), int(values.min()), int(values.max()),
                     float(values.mean()), float(np.count_nonzero(values == 0) / values.size),
                     float(np.count_nonzero(values == 255) / values.size)])
    return {'columns': [
        {'name': 'Channel', 'definition': 'RGB channel as supplied'},
        {'name': 'Pixels', 'unit': 'px'},
        {'name': 'Minimum', 'unit': '0–255'}, {'name': 'Maximum', 'unit': '0–255'},
        {'name': 'Mean', 'unit': '0–255'},
        {'name': 'Zero fraction', 'unit': 'fraction', 'definition': 'Fraction exactly equal to zero'},
        {'name': '255 fraction', 'unit': 'fraction', 'definition': 'Fraction exactly equal to 255'}], 'rows': rows}
