"""Pure real-napari models: calibrated picking, no Qt viewer, GPU or native runtime.

Run with a prepared integration's Python and PYTHONPATH=services. The first case
replays the camera and frame pixel retained by the calibrated MT6 failed edit.
"""
import threading
import unittest

import numpy as np
from napari.components import ViewerModel

from env_worker.scientific_host import ReviewSession
from env_worker.scientific_controls import Stale, Unsupported


class PickUnits(unittest.TestCase):
    def host(self, viewer, layer):
        host = object.__new__(ReviewSession)
        host.viewer, host._layers = viewer, [('labels', layer)]
        host._size, host._frame = (780, 604), 'captured-frame'
        host._thread, host._closed = threading.get_ident(), False
        viewer.canvas.size = (604, 780)
        return host

    def test_captured_mixed_units_click_hits_the_three_stray_pixels(self):
        viewer = ViewerModel()
        data = np.zeros((40, 60), dtype='uint16')
        data[4, 5:8] = 9
        layer = viewer.add_labels(data, affine=np.diag([.5, 250., 1.]),
                                  units=('micrometer', 'nanometer'))
        viewer.scene.camera.center = (0., 9749.999999999998, 7375.)
        viewer.scene.camera.zoom = .038
        viewer.scene.camera.orientation = ('towards', 'down', 'right')
        host = self.host(viewer, layer)
        actual = host.data_position('captured-frame', 'labels', [8, 158])
        # Independent camera arithmetic in nm, then .5 um/y pixel and 250 nm/x pixel.
        expected = [(9750. + (8 - 302) / .038) / 500.,
                    (7375. + (158 - 390) / .038) / 250.]
        np.testing.assert_allclose(actual, expected)
        picked = tuple(np.round(actual).astype(int))
        self.assertEqual(picked, (4, 5))
        self.assertEqual(layer.data[picked], 9)
        layer.fill(actual, 0)
        self.assertEqual(int(np.count_nonzero(layer.data)), 0)

    def test_affine_axis_order_and_axis_direction_are_not_replaced_by_units(self):
        viewer = ViewerModel()
        layer = viewer.add_labels(np.zeros((3, 40, 60), dtype='uint16'),
            affine=[[2., 0., 0., 3.], [0., .5, .1, 7.], [0., 0., 250., 11.], [0., 0., 0., 1.]],
            units=('second', 'micrometer', 'nanometer'))
        host = self.host(viewer, layer)
        # A known point transformed independently: time=5s, y=10um, x=2511nm.
        viewer.dims.order = (0, 2, 1)
        viewer.dims.set_point(0, 5.)
        viewer.scene.camera.center = (0., 2504., 9997.)
        viewer.scene.camera.zoom = 1.
        for vertical, horizontal, point in [('down', 'right', [309, 393]),
                                            ('up', 'left', [295, 387])]:
            viewer.scene.camera.orientation = ('towards', vertical, horizontal)
            np.testing.assert_allclose(host.data_position('captured-frame', 'labels', point), [1., 4., 10.])
        with self.assertRaises(Stale):
            host.data_position('old-frame', 'labels', [302, 390])
        with self.assertRaises(ValueError):
            host.data_position('captured-frame', 'labels', [604, 390])
        viewer.canvas.grid.enabled = True
        with self.assertRaises(Unsupported):
            host.data_position('captured-frame', 'labels', [302, 390])

    def test_pixel_and_incompatible_unit_rendering_keep_native_coordinates(self):
        for incompatible in (False, True):
            viewer = ViewerModel()
            layer = viewer.add_labels(np.zeros((40, 60), dtype='uint16'))
            if incompatible:
                viewer.add_image(np.zeros((40, 60)), units=('second', 'meter'))
                self.assertIsNone(viewer.layers.extent.units)
            host = self.host(viewer, layer)
            viewer.scene.camera.center = (0., 4., 5.)
            viewer.scene.camera.zoom = 1.
            np.testing.assert_allclose(host.data_position('captured-frame', 'labels', [302, 390]), [4., 5.])


if __name__ == '__main__':
    unittest.main()
