"""Reproduce tiny synthetic NGFF fixtures/oracles using only Python's standard library.

No adapter, upstream reader, viewer or Hooke observation generates these answers.
The byte digest convention is the shared reader contract: dtype/shape prefix then
C-order native bytes. Geometry is fixed below, independently of adapter transforms.
"""
from pathlib import Path
import hashlib
import json
import struct


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def files():
    result = {'example-zgroup.json': encoded({'zarr_format': 2})}
    grey = [([2, 4, 6], [1000*z + 100*y + x for z in range(2) for y in range(4) for x in range(6)]),
            ([1, 2, 3], [50000 + 100*y + x for y in range(2) for x in range(3)])]
    labels = [([3, 4], [0, 7, 7, 0, 65537, 65537, 0, 9, 0, 0, 9, 9])]
    for name, arrays, dtype, packing, axes, scales, translation, root_scale, root_translation, basis in [
        ('grey', grey, '>u2', '>H', ['z', 'y', 'x'], [[2, .5, .25], [4, 1, .5]], [3, 5, 7],
         [2, 3, 4], [11, 13, 17], [[17., 28., 45.], [21., 28., 45.], [17., 29.5, 45.], [17., 28., 46.]]),
        ('labels', labels, '<u4', '<I', ['y', 'x'], [[.5, .25]], [-2, 10],
         [2, 4], [3, -1], [[-1., 39.], [0., 39.], [-1., 40.]]),
    ]:
        datasets, levels = [], []
        for i, (shape, values) in enumerate(arrays):
            body = b''.join(struct.pack(packing, value) for value in values)
            result[f'example-{name}-{i}.bin'] = body
            result[f'example-{name}-{i}-zarray.json'] = encoded({
                'zarr_format': 2, 'shape': shape, 'chunks': shape, 'dtype': dtype,
                'compressor': None, 'filters': None, 'order': 'C', 'fill_value': 0})
            datasets.append({'path': str(i), 'coordinateTransformations': [
                {'type': 'scale', 'scale': scales[i]}, {'type': 'translation', 'translation': translation}]})
            levels.append({'shape': shape, 'dtype': dtype,
                           'sha256': hashlib.sha256(repr((dtype, tuple(shape))).encode() + body).hexdigest()})
        attrs = {'multiscales': [{'version': '0.4', 'name': f'Synthetic {name} reference',
            'axes': [{'name': axis, 'type': 'space', 'unit': 'micrometer'} for axis in axes],
            'datasets': datasets, 'coordinateTransformations': [
                {'type': 'scale', 'scale': root_scale}, {'type': 'translation', 'translation': root_translation}]}]}
        layer = {'key': 'selected-ome-zarr-0', 'axes': axes, 'units': ['micrometer']*len(axes),
                 'basis': basis, 'levels': levels}
        if name == 'labels':
            attrs['image-label'] = {'version': '0.4'}
            layer['type'] = 'labels'
        result[f'example-{name}-zattrs.json'] = encoded(attrs)
        result[f'reference-{name}.json'] = encoded({
            'format': 'reader-data-contract-v2' if name == 'labels' else 'reader-data-contract-v1', 'layers': [layer]})
    return result


if __name__ == '__main__':
    for name, content in files().items():
        Path(__file__).with_name(name).write_bytes(content)
