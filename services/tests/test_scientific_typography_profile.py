"""Synthetic typography admission; native evidence remains separately required."""
import sys
from types import SimpleNamespace as S

import pytest

from tests import scientific_parity as parity
from tests import scientific_pixel_profile as pixels
from tests.test_scientific_parity_guards import approved_pair, approved_typography as typography


@pytest.mark.parametrize('side', ['hooke', 'reference'])
@pytest.mark.parametrize('defect', ['missing', 'name', 'tnum', 'pixels', 'metrics', 'family'])
def test_admission_requires_observed_compatible_glyph_rasterization(monkeypatch, side, defect):
    from tests import scientific_napari_reference
    monkeypatch.setattr(scientific_napari_reference, 'pinned_source_check', lambda report: {'same': 1})
    pair = approved_pair()
    for name in pair:
        pair[name]['pixel_profile']['typography'] = typography(name)
    value = pair[side]['pixel_profile']['typography']
    if defect == 'missing': del pair[side]['pixel_profile']['typography']
    elif defect == 'name': value['name'] = 'default-platform'
    elif defect == 'tnum': value['font']['features'] = {}
    elif defect == 'pixels': value['font']['pixel_size'] = 256
    elif defect == 'metrics': value['font']['height'] = 301.5
    else: value['font']['family'] = 'another font'
    with pytest.raises((AssertionError, KeyError), match='typography|glyph|font'):
        parity.admit(pair['reference'], pair['hooke'])


def test_reference_enables_font_attribute_before_application_creation(monkeypatch):
    events = []
    app = S(instance=lambda: None, setAttribute=lambda key, value: events.append((key, value)))
    monkeypatch.setitem(sys.modules, 'qtpy.QtWidgets', S(QApplication=app))
    monkeypatch.setitem(sys.modules, 'qtpy.QtCore', S(Qt=S(ApplicationAttribute=S(AA_Use96Dpi='font96'))))
    pixels.configure_reference_typography()
    assert events == [('font96', True)]
    app.instance = lambda: object()
    with pytest.raises(AssertionError, match='before QApplication'):
        pixels.configure_reference_typography()
    assert events == [('font96', True)]


def test_typography_observer_reads_actual_resolved_font_features_and_metrics(monkeypatch):
    seen = []
    tag = S(toString=lambda: b'tnum')
    font = S(featureTags=lambda: [tag], featureValue=lambda supplied: 1)
    resolved = S(family=lambda: 'Observed family', pixelSize=lambda: 341, pointSizeF=lambda: 255.75)
    metrics = S(height=lambda: 400.5, ascent=lambda: 320.25, horizontalAdvance=lambda char: 100. + ord(char))
    def make(face, size):
        seen.append((face, size))
        return font
    def resolve(actual):
        assert actual is font
        return resolved
    def measure(actual):
        assert actual is font
        return metrics
    monkeypatch.setitem(sys.modules, 'napari._vispy.utils.qt_font', S(make_qfont=make))
    monkeypatch.setitem(sys.modules, 'qtpy.QtGui', S(QFontInfo=resolve, QFontMetricsF=measure))
    monkeypatch.setitem(sys.modules, 'qtpy.QtCore', S(Qt=S(ApplicationAttribute=S(AA_Use96Dpi='font96'))))
    monkeypatch.setitem(sys.modules, 'qtpy.QtWidgets', S(QApplication=S(testAttribute=lambda key: key == 'font96')))
    viewer = S(window=S(_qt_viewer=S(canvas=S(font_info=lambda: S(face='actual canvas face')))))
    observed = pixels.typography_profile(viewer)
    assert seen == [('actual canvas face', 256)]
    assert observed['font']['family'] == 'Observed family'
    assert observed['font']['features'] == {'tnum': 1}
    assert observed['font']['height'] == 400.5 and observed['font']['ascent'] == 320.25
    assert observed['font']['advances']['0'] == 148. and observed['font']['advances']['a'] == 197.
    pixels.require_typography(observed, 'reference')


@pytest.mark.parametrize('value', [float('nan'), float('inf'), 0, -1])
def test_matching_invalid_glyph_metrics_do_not_admit(monkeypatch, value):
    from tests import scientific_napari_reference
    monkeypatch.setattr(scientific_napari_reference, 'pinned_source_check', lambda report: {'same': 1})
    pair = approved_pair()
    for side in pair.values():
        side['pixel_profile']['typography']['font']['height'] = value
    with pytest.raises(AssertionError, match='glyph font metrics'):
        parity.admit(pair['reference'], pair['hooke'])


def test_capture_refuses_typography_changes_even_with_unchanged_view():
    import copy
    import numpy as np
    from tests.test_scientific_parity_guards import approved_pixel_profile
    before = approved_pixel_profile()
    after = copy.deepcopy(before)
    after['typography']['font']['height'] += 1
    capture = {'before': before, 'after': after, 'view_before': {'same': True}, 'view_after': {'same': True}}
    with pytest.raises(AssertionError, match='changed typography'):
        pixels.require_pixel_capture(capture, 256, 256, np.zeros((256, 256, 4), dtype=np.uint8))
