"""Tiny actual ome-zarr-py read over SDK fixture grants; no Qt, worker or UI qualification."""
import hashlib
import importlib.metadata
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
from env_worker.source_files import bind, FORMAT


def run(adapter_path):
    spec = importlib.util.spec_from_file_location('ome_reader_probe', adapter_path)
    adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
    values = [np.array([[0, 255, 256], [1000, 4096, 65535]], dtype='>u2'), np.array([[9, 65535]], dtype='>u2')]
    axes = [{'name': 'y', 'type': 'space', 'unit': 'micrometer'}, {'name': 'x', 'type': 'space', 'unit': 'micrometer'}]
    attrs = {'multiscales': [{'version': '0.4', 'name': 'Pinned values', 'axes': axes,
        'coordinateTransformations': [{'type': 'scale', 'scale': [4, 5]}, {'type': 'translation', 'translation': [7, 11]}],
        'datasets': [{'path': str(i), 'coordinateTransformations': [{'type': 'scale', 'scale': [2*(i+1), 3*(i+1)]},
                        {'type': 'translation', 'translation': [10, 20]}]} for i in range(2)]}]}
    files = {'.zattrs': json.dumps(attrs).encode(), '.zgroup': b'{"zarr_format":2}'}
    for i, data in enumerate(values):
        files[f'{i}/.zarray'] = json.dumps({'zarr_format': 2, 'shape': list(data.shape), 'chunks': list(data.shape),
            'dtype': '>u2', 'compressor': None, 'filters': None, 'order': 'C', 'fill_value': 0}).encode()
        files[f'{i}/0.0'] = data.tobytes()
    entries = []; content = b''
    for name, body in files.items():
        entries.append({'path': name, 'offset': len(content), 'size': len(body)}); content += body
    index = json.dumps({'format': 'source-directory-v1', 'entries': entries}).encode()
    requests = []
    def request(header):
        requests.append(header)
        if header['op'] == 'read-source-directory':
            return {'ok': True, 'kind': 'source-directory-index-v1', 'bytes': len(index)}, index
        at, size = header['offset'], header['length']
        return {'ok': True, 'kind': FORMAT, 'offset': at, 'bytes': size}, content[at:at+size]
    declaration = {'format': FORMAT, 'token': 'fixture-grant', 'name': 'pinned.ome.zarr', 'size': len(content),
        'sha256': hashlib.sha256(content).hexdigest(), 'reference': {'session': 'fixture', 'id': 'source', 'revision': 1, 'component': 'file.source'},
        'directory': {'format': 'source-directory-index-v1', 'bytes': len(index), 'sha256': hashlib.sha256(index).hexdigest()}}
    source = bind({'source': declaration}, request)['source']
    node = adapter.read_images(source)
    observed = [level.compute(scheduler='synchronous') for level in node.data]
    assert len(observed) == len(values)
    for actual, expected in zip(observed, values):
        assert actual.dtype.str == expected.dtype.str
        assert actual.shape == expected.shape and np.array_equal(actual, expected)
    assert node.metadata['axes'] == axes
    assert node.metadata['selectedRootTransforms'] == attrs['multiscales'][0]['coordinateTransformations']
    assert 'qtpy' not in sys.modules and 'PySide6' not in sys.modules
    return {'class': 'actual ome-zarr-py; synthetic SDK byte grants; no Qt/UI', 'python': sys.executable,
        'dependencies': {name: importlib.metadata.version(name) for name in ('ome-zarr', 'zarr', 'numpy', 'dask', 'fsspec', 'numcodecs')},
        'adapterSha256': hashlib.sha256(Path(adapter_path).read_bytes()).hexdigest(),
        'sourceSha256': hashlib.sha256(content).hexdigest(), 'indexSha256': hashlib.sha256(index).hexdigest(),
        'shapes': [list(data.shape) for data in observed], 'dtypes': [data.dtype.str for data in observed],
        'pixelSha256': [hashlib.sha256(data.tobytes()).hexdigest() for data in observed],
        'requests': len(requests), 'maxRequestBytes': max(row.get('length', 0) for row in requests),
        'axes': node.metadata['axes'], 'rootTransforms': node.metadata['selectedRootTransforms']}


if __name__ == '__main__':
    print(json.dumps(run(sys.argv[1]), indent=2))
