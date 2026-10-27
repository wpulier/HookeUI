"""Controlled H06 physical-pixel comparison, executed unchanged beside the common runtime observer.

This is a declared 1x test profile, not qualification of arbitrary displays. Cocoa uses Qt's documented
QT_SCALE_FACTOR=0.5 on the observed native-2x host; actual ratios must still be 1. Its reference-only
AA_Use96Dpi attribute is set before QApplication, with actual glyph font/features/metrics observed on both
sides. Default-platform typography remains unqualified. No adaptive scaling, image resampling, camera-unit
conversion, VisPy DPI rewrite or tolerance adjustment is permitted.
"""
PIXEL_PROFILE = 'napari-gui-physical-pixels-typography-v2'


def configure_reference_typography():
    """Declare the reference-only Qt glyph rasterization profile before Qt creates its app."""
    from qtpy.QtCore import Qt
    from qtpy.QtWidgets import QApplication
    assert QApplication.instance() is None, 'typography profile must be configured before QApplication'
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_Use96Dpi, True)


def typography_profile(viewer):
    """Observe Qt's actual glyph font using the same upstream resolution as its text atlas."""
    from qtpy.QtCore import Qt
    from qtpy.QtGui import QFontInfo, QFontMetricsF
    from qtpy.QtWidgets import QApplication
    from napari._vispy.utils.qt_font import make_qfont
    # This upstream constructor resolves the 256-point atlas font against the actual application font.
    face = viewer.window._qt_viewer.canvas.font_info().face
    font = make_qfont(face, 256)
    resolved, metrics = QFontInfo(font), QFontMetricsF(font)
    features = {bytes(tag.toString()).decode('ascii'): int(font.featureValue(tag)) for tag in font.featureTags()}
    return {'name': 'qt-glyph-96dpi-v1',
            'use_96_dpi': bool(QApplication.testAttribute(Qt.ApplicationAttribute.AA_Use96Dpi)),
            'font': {'family': resolved.family(), 'features': features, 'pixel_size': resolved.pixelSize(),
                     'point_size': resolved.pointSizeF(), 'height': metrics.height(), 'ascent': metrics.ascent(),
                     'advances': {c: metrics.horizontalAdvance(c) for c in '0123456789abx='}}}


def require_typography(value, side):
    import math
    assert value['name'] == 'qt-glyph-96dpi-v1', 'unknown typography profile'
    assert type(value['use_96_dpi']) is bool, 'missing typography configuration observation'
    if side == 'reference':
        assert value['use_96_dpi'] is True, 'reference typography attribute was not observed'
    font = value['font']
    assert isinstance(font['family'], str) and font['family'], 'missing resolved glyph font'
    assert font['features'].get('tnum') == 1, 'upstream tabular-numeral font initialization missing'
    assert font['pixel_size'] == 341, 'Qt glyph point-to-pixel resolution differs from controlled96DPI'
    assert set(font['advances']) == set('0123456789abx='), 'glyph metrics are incomplete'
    values = [font[key] for key in ('point_size', 'height', 'ascent')] + list(font['advances'].values())
    assert all(type(n) in (int, float) and math.isfinite(n) and n > 0 for n in values), 'invalid glyph font metrics'


def pixel_profile(viewer):
    import os
    from qtpy.QtWidgets import QApplication
    canvas = viewer.window._qt_viewer.canvas
    scene = canvas._scene_canvas
    native = canvas.native
    window = native.window().windowHandle()
    return {'name': PIXEL_PROFILE, 'typography': typography_profile(viewer), 'qt_platform': QApplication.instance().platformName(),
            'qt_scale_factor': os.environ.get('QT_SCALE_FACTOR'), 'backend': type(native).__name__,
            'qt_viewer_dpr': float(viewer.window._qt_viewer.devicePixelRatio()),
            'widget_dpr': float(native.devicePixelRatioF()),
            'window_dpr': float(window.devicePixelRatio()) if window is not None else None,
            'logical_size': [int(n) for n in scene.size], 'physical_size': [int(n) for n in scene.physical_size],
            'pixel_scale': float(scene.pixel_scale), 'native_visible': bool(native.isVisible()),
            'native_valid': bool(native.isValid()) if hasattr(native, 'isValid') else None}


def require_pixel_profile(value, side, size=None):
    assert side in ('hooke', 'reference')
    assert value['name'] == PIXEL_PROFILE, 'unknown pixel profile'
    require_typography(value['typography'], side)
    platform, backend, factor = (('cocoa', 'CanvasBackendDesktop', '0.5') if side == 'reference'
                                 else ('offscreen', 'CanvasBackendCGL', None))
    assert (value['qt_platform'], value['backend'], value['qt_scale_factor']) == (platform, backend, factor), \
        'unexpected pixel renderer/configuration'
    assert all(value[key] == 1.0 for key in ('qt_viewer_dpr', 'widget_dpr', 'pixel_scale')), \
        f'pixel comparison requires observed DPR 1: {value}'
    assert value['window_dpr'] in (None, 1.0), 'pixel window DPR differs'
    logical, physical = value['logical_size'], value['physical_size']
    assert len(logical) == 2 and all(type(n) is int and n > 0 for n in logical), 'invalid pixel viewport'
    assert physical == logical, 'physical pixel viewport differs from logical viewport'
    if size is not None:
        assert logical == list(size), f'pixel viewport differs from requested size {size}: {value}'
    if side == 'reference':
        assert value['native_visible'] is True and value['native_valid'] is True, f'reference pixel canvas unrealized: {value}'
        assert value['window_dpr'] == 1.0, 'reference pixel window DPR unavailable'


def pixel_view(viewer):
    camera, dims = viewer.scene.camera, viewer.dims
    return {'camera': {'center': [float(n) for n in camera.center], 'angles': [float(n) for n in camera.angles],
                       'zoom': float(camera.zoom), 'perspective': float(camera.perspective),
                       'orientation': [str(n) for n in camera.orientation]},
            'dims': {'order': list(dims.order), 'ndisplay': dims.ndisplay,
                     'point': [float(n) for n in dims.point], 'steps': list(dims.current_step)}}


def _pixel_resize(viewer, width, height, before):
    from qtpy.QtWidgets import QApplication
    canvas = viewer.window._qt_viewer.canvas
    # The shown Qt parent layout otherwise expands this widget during processEvents.
    canvas.native.setFixedSize(width, height)
    canvas.size = (height, width)
    QApplication.processEvents()
    # Native resize can change the camera. Reapply this recipe's exact explicit view after layout.
    camera = viewer.scene.camera
    for name in ('center', 'angles', 'zoom', 'perspective'):
        value = before['camera'][name]
        setattr(camera, name, tuple(value) if isinstance(value, list) else value)
    QApplication.processEvents()
    assert pixel_view(viewer) == before, 'pixel layout changed camera/dimensions'


def realize_pixel_canvas(viewer, width, height):
    from qtpy.QtWidgets import QApplication
    before = pixel_view(viewer)
    # Public QWidget.show on this owned native window; napari Window.show additionally writes first_time.
    viewer.window._qt_viewer.window().show()
    QApplication.processEvents()
    _pixel_resize(viewer, width, height, before)
    observed = pixel_profile(viewer)
    require_pixel_profile(observed, 'reference', (width, height))
    return observed


def require_pixel_capture(value, width, height, pixels):
    import numpy as np
    for stage in ('before', 'after'):
        require_pixel_profile(value[stage], 'reference', (width, height))
    assert value['before']['typography'] == value['after']['typography'], 'capture changed typography profile'
    assert value['view_before'] == value['view_after'], f'pixel capture changed camera/dimensions: {value}'
    assert pixels.dtype == np.uint8 and pixels.shape == (height, width, 4), 'pixel output differs from viewport'


def pixel_render(viewer, width, height):
    before_view = pixel_view(viewer)
    _pixel_resize(viewer, width, height, before_view)
    before = pixel_profile(viewer)
    require_pixel_profile(before, 'reference', (width, height))
    pixels = viewer.screenshot(size=(height, width), canvas_only=True, flash=False)
    after, after_view = pixel_profile(viewer), pixel_view(viewer)
    capture = {'before': before, 'after': after, 'view_before': before_view, 'view_after': after_view}
    require_pixel_capture(capture, width, height, pixels)
    return pixels, capture
