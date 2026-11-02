"""Literal reference arithmetic, not catalog activation or scientific accuracy."""
import importlib.util
import numpy as np
from orchestrator.paths import REPO


def test_intensity_reference_and_unchanged_input():
    spec = importlib.util.spec_from_file_location('intensity_proposal', REPO/'examples/research-intensity-diagnostic/adapter.py')
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    image = np.array([[[0, 10, 255], [255, 30, 255]]], dtype=np.uint8)
    before = image.copy()
    result = adapter.run(image, {})
    assert result['rows'] == [['R', 2, 0, 255, 127.5, .5, .5],
                              ['G', 2, 10, 30, 20., 0., 0.],
                              ['B', 2, 255, 255, 255., 0., 1.]]
    np.testing.assert_array_equal(image, before)
    for invalid in (image.astype(float), image[:, :, 0], image[:0]):
        assert 'unsupported' in adapter.run(invalid, {})
