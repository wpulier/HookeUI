"""Actual upstream label edits over owned pipes; run only in the test's sandbox."""
import itertools
import sys

import napari
import numpy as np

from env_worker import native_edits, native_regions
from env_worker.server import read_message, write_message

if napari.__version__ != '0.7.1.dev0':
    raise RuntimeError('This probe requires the audited supplied-source candidate')
if sys.argv[1:] == ['--managed-reference']:
    from env_worker import napari_cgl
    napari_cgl._runtime(napari_cgl.REFERENCE_REVISION)

def request(header, body=b''):
    write_message(sys.stdout.buffer, header, body)
    return read_message(sys.stdin.buffer, max_header=16384, max_payload=1 << 20)

declaration, payload = read_message(sys.stdin.buffer, max_header=32768, max_payload=0)
assert not payload
resource = native_regions.bind(declaration['inputs'], request)['labels']
edit = native_edits.bind(declaration['edit'], request)
assert resource.reference == edit.reference
assert resource.shape == (6, 24, 28) and resource.dtype == '<u2'
original = resource.read([[0, n] for n in resource.shape])
labels = napari.layers.Labels(original.copy(), scale=(3, 2, 1))
labels.n_edit_dimensions = 2
labels.brush_size = 5
labels.preserve_labels = True
with labels.block_history():
    labels.paint((2, 8, 10), 7)
    labels.paint((2, 8, 13), 7)
painted = labels.data.copy()
labels.undo()
assert np.array_equal(labels.data, original)
labels.redo()
assert np.array_equal(labels.data, painted)
changed = []
for index in itertools.product(*(range(n) for n in edit.grid)):
    slices = tuple(slice(i * c, min((i + 1) * c, n)) for i, c, n in zip(index, edit.chunks, edit.shape))
    before, after = original[slices], labels.data[slices]
    if before.tobytes() != after.tobytes():
        edit.write(index, after)
        changed.append(index)
assert changed == [(2, 0, 0), (2, 0, 1)]
receipt = edit.commit()
assert receipt['reference'] != resource.reference
