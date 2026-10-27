"""Synthetic controlled-pixel admission tests; no native rendering qualification."""
import pytest

from tests import scientific_parity as parity
from tests.test_scientific_parity_guards import approved_pair, approved_pixel_profile as profile


@pytest.mark.parametrize('side', ['hooke', 'reference'])
@pytest.mark.parametrize('defect', ['missing', 'name', 'qt_viewer_dpr', 'widget_dpr', 'pixel_scale', 'physical_size'])
def test_admission_refuses_missing_or_wrong_observed_pixel_profile(monkeypatch, side, defect):
    from tests import scientific_napari_reference
    monkeypatch.setattr(scientific_napari_reference, 'pinned_source_check', lambda report: {'same': 1})
    pair = approved_pair()
    for name in pair:
        pair[name]['pixel_profile'] = profile(name)
    if defect == 'missing':
        del pair[side]['pixel_profile']
    elif defect == 'name':
        pair[side]['pixel_profile']['name'] = 'uncontrolled'
    elif defect == 'physical_size':
        pair[side]['pixel_profile']['physical_size'] = [512, 512]
    else:
        pair[side]['pixel_profile'][defect] = 2.0
    with pytest.raises((AssertionError, KeyError), match='pixel|DPR|physical'):
        parity.admit(pair['reference'], pair['hooke'])


@pytest.mark.parametrize('defect', ['missing', 'wrong_size', 'camera', 'dims', 'output'])
def test_loaded_render_requires_pre_post_viewport_and_unchanged_view(defect):
    import numpy as np
    from tests import scientific_pixel_profile as pixels
    capture = {'before': profile(), 'after': profile(), 'view_before': {'camera': {'zoom': 8}, 'dims': {'point': [0, 0, 0]}},
               'view_after': {'camera': {'zoom': 8}, 'dims': {'point': [0, 0, 0]}}}
    array = np.zeros((256, 256, 4), dtype=np.uint8)
    if defect == 'missing': del capture['after']
    elif defect == 'wrong_size': capture['after']['logical_size'] = capture['after']['physical_size'] = [128, 128]
    elif defect == 'camera': capture['view_after']['camera']['zoom'] = 16
    elif defect == 'dims': capture['view_after']['dims']['point'][0] = 1
    else: array = array.astype(np.float32)
    with pytest.raises((AssertionError, KeyError)):
        pixels.require_pixel_capture(capture, 256, 256, array)


@pytest.mark.parametrize('field,value', [('pixelRatio', 2), ('width', 128), ('height', 128)])
def test_worker_output_must_keep_controlled_physical_viewport(field, value):
    side = parity.HookeSide(None, 2, 2)
    side.frame = {'width': 2, 'height': 2, 'pixelRatio': 1, field: value}
    with pytest.raises(AssertionError, match='pixel'):
        side.pixels(bytes(16))


@pytest.mark.parametrize('field,value', [('qt_platform', 'offscreen'), ('backend', 'CanvasBackendCGL'),
                                        ('qt_scale_factor', None), ('window_dpr', None),
                                        ('native_visible', False), ('native_valid', False)])
def test_reference_refuses_unrealized_or_uncontrolled_cocoa(field, value):
    from tests.scientific_pixel_profile import require_pixel_profile
    observed = {**profile(), field: value}
    with pytest.raises(AssertionError, match='pixel'):
        require_pixel_profile(observed, 'reference', (256, 256))


@pytest.fixture
def owned_viewer(monkeypatch):
    """Small Qt/VisPy stand-in with resize/show camera effects; never imports native Qt."""
    import sys
    from types import SimpleNamespace as S
    import numpy as np
    from tests import scientific_pixel_profile as pixels
    from tests.test_scientific_parity_guards import approved_typography
    monkeypatch.setattr(pixels, 'typography_profile', lambda viewer: approved_typography())
    calls = []
    camera = S(center=(0., 11.5, 13.5), angles=(0., 0., 0.), zoom=8., perspective=0.,
               orientation=('towards', 'down', 'right'))
    dims = S(order=(0, 1, 2), ndisplay=2, point=(0., 0., 0.), current_step=(0, 0, 0))
    scene = S(size=(800, 600), physical_size=(800, 600), pixel_scale=1.)
    state = {'shown': False, 'valid': False, 'fixed': None}
    def show():
        calls.append('show')
        state['shown'] = True
        camera.center = (0., 30., 40.)
    window = S(show=show, devicePixelRatio=lambda: 1.)
    window.windowHandle = lambda: window if state['shown'] else None
    def fixed(width, height):
        calls.append(('fixed', (width, height)))
        state['fixed'] = (width, height)
    native = type('CanvasBackendDesktop', (), {
        'setFixedSize': lambda self, width, height: fixed(width, height),
        'window': lambda self: window, 'devicePixelRatioF': lambda self: 1.,
        'isVisible': lambda self: state['shown'], 'isValid': lambda self: state['valid']})()
    class Canvas:
        _scene_canvas = scene
        @property
        def size(self): return scene.size[::-1]
        @size.setter
        def size(self, size):
            calls.append(('resize', size))
            scene.size = scene.physical_size = tuple(size[::-1])
            camera.zoom = 12.
    canvas = Canvas()
    canvas.native = native
    qt_viewer = S(canvas=canvas, window=lambda: window, devicePixelRatio=lambda: 1.)
    def events():
        calls.append('events')
        state['valid'] = state['shown']
        if getattr(viewer, 'layout_expands', False):
            scene.size = scene.physical_size = state['fixed'] or (778, 603)
    monkeypatch.setitem(sys.modules, 'qtpy.QtWidgets', S(QApplication=S(
        processEvents=events, instance=lambda: S(platformName=lambda: 'cocoa'))))
    monkeypatch.setenv('QT_SCALE_FACTOR', '0.5')
    viewer = S(scene=S(camera=camera), dims=dims, window=S(_qt_viewer=qt_viewer))
    def screenshot(*, size, canvas_only, flash):
        calls.append(('screenshot', size, camera.zoom))
        return np.zeros((*size, 4), dtype=np.uint8)
    viewer.screenshot = screenshot
    return viewer, calls


def test_realization_uses_owned_window_then_resize_and_restores_exact_view(owned_viewer):
    from tests import scientific_pixel_profile as pixels
    viewer, calls = owned_viewer
    before = pixels.pixel_view(viewer)
    observed = pixels.realize_pixel_canvas(viewer, 256, 128)
    assert calls == ['show', 'events', ('fixed', (256, 128)), ('resize', (128, 256)), 'events', 'events']
    assert pixels.pixel_view(viewer) == before
    assert observed['logical_size'] == observed['physical_size'] == [256, 128]
    assert observed['native_visible'] and observed['native_valid']
    rendered, capture = pixels.pixel_render(viewer, 256, 128)
    assert rendered.shape == (128, 256, 4)
    assert capture['view_before'] == capture['view_after'] == before
    assert calls[-1] == ('screenshot', (128, 256), 8.)


def test_actual_capture_refuses_native_screenshot_camera_mutation(owned_viewer):
    from tests import scientific_pixel_profile as pixels
    viewer, _ = owned_viewer
    pixels.realize_pixel_canvas(viewer, 256, 256)
    original = viewer.screenshot
    def changed(**kwargs):
        result = original(**kwargs)
        viewer.scene.camera.zoom = 9.
        return result
    viewer.screenshot = changed
    with pytest.raises(AssertionError, match='changed camera'):
        pixels.pixel_render(viewer, 256, 256)


def test_owned_parent_layout_cannot_expand_the_requested_pixel_viewport(owned_viewer):
    from tests import scientific_pixel_profile as pixels
    viewer, calls = owned_viewer
    viewer.layout_expands = True
    before = pixels.pixel_view(viewer)
    observed = pixels.realize_pixel_canvas(viewer, 256, 128)
    assert observed['logical_size'] == observed['physical_size'] == [256, 128]
    assert pixels.pixel_view(viewer) == before
    image, capture = pixels.pixel_render(viewer, 256, 128)
    assert image.shape == (128, 256, 4)
    assert capture['before']['physical_size'] == capture['after']['physical_size'] == [256, 128]
    assert capture['view_before'] == capture['view_after'] == before
