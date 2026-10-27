"""Erase after the background label changes: Hooke's editor beside napari's own eraser, in the pinned runtime.

napari's eraser paints `layer.colormap.background_value` (napari/layers/labels/_labels_mouse_bindings.py:
in Mode.ERASE, new_label = layer.colormap.background_value), so the background label is scientific: erasing
writes it into the data. Run confined by test_scientific_image_labels_parity.py.
"""
import sys
sys.path.insert(0, sys.argv[1])
from types import SimpleNamespace

import numpy as np
from napari.layers import Labels
from napari.utils.colormaps import CyclicLabelColormap
from env_worker.scientific_layer_exports import EditableLayers
from env_worker.scientific_labels import LabelsEditor
from env_worker import scientific_labels_appearance as appearance

values = np.zeros((3, 24, 28), dtype=np.uint16)
values[1, 5:15, 5:15] = 5
values[1, 18:22, 3:9] = 9


def editable(data):
    layer = Labels(data.copy())
    source = SimpleNamespace(shape=data.shape, dtype=data.dtype.str, axes=('z', 'y', 'x'),
                             descriptor={'semantic': 'instance-labels'}, reference={'id': 'test', 'revision': 'a', 'component': 'array.labels'})
    registry = EditableLayers(SimpleNamespace(layers=[layer]), {'labels': source})
    registry.bind(layer=layer, input='labels'); registry.seal()
    return layer, LabelsEditor(layer, registry)


# Hooke: the label display changes the background to 7, then its editor erases a stroke through label 5.
hooke, editor = editable(values)
appearance.prepare(hooke, {**appearance.snapshot(hooke), 'backgroundValue': '7'}).apply()
assert int(hooke.colormap.background_value) == 7
editor.apply('erase', points=[[1, 10, 10], [1, 10, 13]], label='5', brush=3, preserve=False, contiguous=True)

# napari: the same background through its public API, then its eraser's own call along the same stroke.
from napari.layers.labels._labels_utils import interpolate_coordinates
reference = Labels(values.copy())
reference.colormap = CyclicLabelColormap(**{**reference.colormap.model_dump(), 'background_value': 7})
reference.brush_size, reference.n_edit_dimensions, reference.preserve_labels = 3, 2, False
background = reference.colormap.background_value
reference._begin_stroke()
reference.paint([1, 10, 10], background, refresh=False)
for position in interpolate_coordinates([1, 10, 10], [1, 10, 13], 3):
    reference.paint(position, background, refresh=False)
reference._commit_stroke()

assert np.array_equal(hooke.data, reference.data), 'Hooke erase differs from napari'
erased = (hooke.data != values)
# Independent: every changed pixel was label 5 and now holds the new background 7; nothing else moved.
assert erased.any() and set(np.unique(values[erased])) == {5} and set(np.unique(hooke.data[erased])) == {7}
assert not (hooke.data == 0)[1, 10, 10] and hooke.data[1, 10, 10] == 7 and hooke.data[1, 20, 5] == 9
print('erase writes the changed background label, as napari does')
