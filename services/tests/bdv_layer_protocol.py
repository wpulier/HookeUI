"""Actual pinned napari/HDF5 protocol probe; no viewer, Qt application or qualification.

Run with the prepared reader's Python. This is a bounded integration diagnostic,
not a replacement for confined reader proof, ordinary UI or multiscale Save.
"""
from pathlib import Path
import hashlib
import importlib.metadata
import importlib.util
import io
import json
from unittest.mock import patch


def check():
    import h5py
    import numpy as np
    import napari.layers._data_protocols as protocols
    import napari.layers._multiscale_data as multiscale
    assert importlib.metadata.version('napari') == '0.7.1.dev0'
    assert importlib.metadata.version('h5py') == '3.16.0'
    source = Path(__file__).resolve().parents[2] / 'examples/beta-scientific-tools/bdv-hdf5-reader/bdv.py'
    spec = importlib.util.spec_from_file_location('bdv_protocol_candidate', source)
    bdv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bdv)
    expected = (np.arange(8 * 12 * 16).reshape(8, 12, 16) + 40000).astype('>u2')
    stream = io.BytesIO()
    with h5py.File(stream, 'w') as file:
        file.create_dataset('fine', data=expected.view('>i2'), chunks=(2, 4, 4))
        file.create_dataset('coarse', data=expected[::2, ::2, ::2].view('>i2'), chunks=True)
        def no_pixels(*_args, **_kwargs):
            raise AssertionError('Protocol/size metadata attempted to read or materialize pixels')
        with patch.object(h5py.Dataset, '__getitem__', no_pixels), patch.object(bdv.Level, '__array__', no_pixels):
            fine, coarse = [bdv.Level(file[name], True) for name in ('fine', 'coarse')]
            # The actual pinned upstream assertion, not an imitation of its protocol.
            protocols.assert_protocol(fine)
            protocols.assert_protocol(coarse)
            pyramid = multiscale.MultiScaleData([fine, coarse])
            assert pyramid[0] is fine and pyramid[1] is coarse
            assert (fine.size, coarse.size) == (1536, 192)
            assert (fine.ndim, fine.shape, fine.dtype.str) == (3, (8, 12, 16), '>u2')
        reads = []
        original = h5py.Dataset.__getitem__
        def read(dataset, key):
            reads.append(key)
            return original(dataset, key)
        region = (slice(1, 4), slice(3, 8), slice(5, 11))
        with patch.object(h5py.Dataset, '__getitem__', read), patch.object(bdv.Level, '__array__', no_pixels):
            actual = fine[region]
        assert reads == [region]
        assert actual.dtype.str == '>u2' and actual.shape == (3, 5, 6)
        assert actual.tobytes() == expected[region].tobytes()
    return {'classification': 'actual pinned napari protocol and HDF5; no viewer/Qt app, worker, proof or qualification',
            'protocol': 'LayerDataProtocol and MultiScaleData', 'metadataPixelReads': 0,
            'sizes': [1536, 192], 'regionReads': 1, 'dtype': actual.dtype.str,
            'regionShape': list(actual.shape), 'regionSha256': hashlib.sha256(actual.tobytes()).hexdigest(),
            'sourceSha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'pinnedSources': {str(module.__file__): hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                              for module in (protocols, multiscale)}}


if __name__ == '__main__':
    print(json.dumps(check(), indent=2))
