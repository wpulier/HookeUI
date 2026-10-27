"""Labels display and image histogram contracts: the shared wire cases, frames and remembered views.

Synthetic, no napari: these check the bounded metadata both sides share. The behaviour itself is
compared with pinned napari in test_scientific_image_labels_parity.py.
"""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from env_worker import scientific_host, scientific_image_histogram as histogram, scientific_labels_appearance as labels
from env_worker.scientific_view_preferences import compatible, from_frame, preferences
from env_worker.scientific_view_wire import frame as validate_frame
from tests.test_scientific_change_recovery import host

CONTRACT = Path(__file__).resolve().parents[2] / 'contract'
LABELS = json.loads((CONTRACT / 'scientific-labels-appearance-cases.json').read_text())
HISTOGRAM = json.loads((CONTRACT / 'scientific-image-histogram-cases.json').read_text())
FRAME = json.loads((CONTRACT / 'scientific-view-cases.json').read_text())['valid']
VIEW = json.loads((CONTRACT / 'scientific-view-preferences-cases.json').read_text())['valid']


def changed(valid, case):
    value = copy.deepcopy(valid)
    value.update(copy.deepcopy(case.get('patch', {})))
    value.pop(case.get('remove'), None)
    return value


def labels_frame(appearance, *, remembered=True):
    frame = copy.deepcopy(FRAME)
    row = frame['layers'][0]
    row.update(type='labels', labelsAppearance=copy.deepcopy(appearance), labelsSelectedColor=[0.5, 0.25, 1.0, 1.0])
    if remembered:
        row.update(viewKey='raw', viewGeometry=copy.deepcopy(VIEW['layers'][0]['geometry']))
    return frame


@pytest.mark.parametrize('case', LABELS['otherValid'], ids=lambda c: c['name'])
def test_labels_display_accepts_every_valid_shape(case):
    value = changed(LABELS['valid'], case)
    assert labels.settings(value) == value
    assert validate_frame(labels_frame(value, remembered=False))['layers'][0]['labelsAppearance'] == value


@pytest.mark.parametrize('case', LABELS['invalid'], ids=lambda c: c['name'])
def test_labels_display_refuses_before_any_frame_or_change(case):
    value = changed(LABELS['valid'], case)
    with pytest.raises(ValueError):
        labels.settings(value)
    with pytest.raises(ValueError):
        validate_frame(labels_frame(value, remembered=False))


def test_labels_display_belongs_to_labels_layers_only():
    frame = labels_frame(LABELS['valid'], remembered=False)
    frame['layers'][0]['type'] = 'image'
    with pytest.raises(ValueError):
        validate_frame(frame)


@pytest.mark.parametrize('color', [None, [0, 0, 0, 0], [1, 1, 1, 1]])
def test_selected_label_color_travels_with_the_display(color):
    frame = labels_frame(LABELS['valid'], remembered=False)
    frame['layers'][0]['labelsSelectedColor'] = color
    assert validate_frame(frame)['layers'][0]['labelsSelectedColor'] == color


@pytest.mark.parametrize('color', [[1, 0, 0], [2, 0, 0, 1], 'red', 'absent'])
def test_selected_label_color_is_checked(color):
    frame = labels_frame(LABELS['valid'], remembered=False)
    if color == 'absent':
        del frame['layers'][0]['labelsSelectedColor']
    else:
        frame['layers'][0]['labelsSelectedColor'] = color
    with pytest.raises(ValueError):
        validate_frame(frame)


def test_remembered_labels_display_is_schema_14_and_python_colors_cannot_be_remembered():
    frame = labels_frame(LABELS['valid'])
    saved = from_frame(frame)
    assert saved['schema'] == 14 and saved['layers'][0]['labelsAppearance'] == LABELS['valid']
    frame['layers'][0]['labelsAppearance']['contour'] = 0
    assert saved['layers'][0]['labelsAppearance']['contour'] == 2  # detached
    assert compatible(saved, frame) == saved
    older = copy.deepcopy(saved)
    older['schema'] = 13
    with pytest.raises(ValueError):
        preferences(older)  # a schema-13 view never carried label display
    del older['layers'][0]['labelsAppearance']
    assert compatible(older, frame)['layers'][0]['labelsAppearance'] == frame['layers'][0]['labelsAppearance']
    other = labels_frame({**LABELS['valid'], 'colorMode': 'auto', 'autoSource': 'other', 'autoSeed': None})
    with pytest.raises(ValueError, match='changed in Python'):
        from_frame(other)


def fake_labels(dtype='uint16'):
    import numpy as np
    return SimpleNamespace(_type_string='labels', data=np.zeros((4, 30, 40), dtype=dtype), contour=0)


@pytest.mark.parametrize('patch,words', [
    ({'selectedLabel': '65536'}, 'outside this uint16'),
    ({'backgroundValue': '-1'}, 'outside this uint16'),
    ({'contour': 10**6, 'selectedLabel': '1'}, 'choose at most'),
])
def test_labels_display_admission_refuses_against_the_layer_before_changing_it(patch, words):
    layer = fake_labels()
    before = vars(layer).copy()
    with pytest.raises(ValueError, match=words):
        labels.prepare(layer, {**LABELS['valid'], **patch, 'colorMode': 'auto', 'autoSource': 'original', 'autoSeed': None})
    assert vars(layer) == before


def test_contour_limit_bounds_redraw_work_by_the_largest_plane():
    small, large = SimpleNamespace(data=SimpleNamespace(shape=(4, 30, 40))), SimpleNamespace(data=SimpleNamespace(shape=(2, 1024, 1024)))
    for layer in (small, large):
        widest = labels.contour_limit(layer)
        pixels = sorted(layer.data.shape)[-2] * sorted(layer.data.shape)[-1]
        assert (2 * widest ** 2 + 2 * widest + 1) * pixels <= labels.CONTOUR_WORK
        assert (2 * (widest + 1) ** 2 + 2 * (widest + 1) + 1) * pixels > labels.CONTOUR_WORK
    assert labels.contour_limit(small) > labels.contour_limit(large) >= 1


def test_labels_change_refusal_keeps_frame_and_private_work():
    session = host()
    layer = fake_labels()
    session._layers = [('layer', layer)]
    with pytest.raises(scientific_host.ChangeRefused):
        session.change({'frame': 'current', 'operation': 'labels-presentation',
                        'value': {'layer': 'layer', 'settings': {**LABELS['valid'], 'contour': -1}}})
    with pytest.raises(scientific_host.ChangeRefused):
        session.change({'frame': 'current', 'operation': 'labels-presentation',
                        'value': {'layer': 'gone', 'settings': LABELS['valid']}})
    assert session._frame == 'current' and not session.dirty and layer.contour == 0


INSPECT = {'op': 'inspect', 'frame': 'a' * 64, 'layer': 'b' * 32, 'position': [10, 20]}
LABEL_SAMPLE = {'frame': 'a' * 64, 'layer': 'b' * 32, 'world': [10., 20.], 'data': [1., 2.], 'axes': ['y', 'x'],
                'units': ['pixel', 'pixel'], 'level': None, 'projection': 'none', 'dtype': '<u8',
                'values': ['18446744073709551614'],
                'labelFeatures': {'row': 3, 'features': [{'name': 'index', 'dtype': 'uint64', 'kind': 'integer', 'value': '18446744073709551614'},
                                                         {'name': 'area', 'dtype': 'float64', 'kind': 'missing', 'value': None}]}}


def test_label_feature_rows_keep_exact_identifiers_and_refuse_malformed_rows():
    from env_worker.scientific_inspection import reply
    assert reply(copy.deepcopy(LABEL_SAMPLE), INSPECT) == LABEL_SAMPLE
    assert reply({**LABEL_SAMPLE, 'labelFeatures': {'row': None, 'features': []}}, INSPECT)['labelFeatures']['row'] is None
    for patch in ({'labelFeatures': {'row': None, 'features': LABEL_SAMPLE['labelFeatures']['features']}},
                  {'labelFeatures': {'row': -1, 'features': []}}, {'labelFeatures': {'row': 1}},
                  {'values': None}, {'values': ['1.5']}, {'values': ['1', '2', '3']},
                  {'labelFeatures': {'row': 1, 'features': [{'name': 'id', 'dtype': 'uint64', 'kind': 'integer', 'value': 2**64 - 2}]}}):
        with pytest.raises(ValueError):
            reply({**copy.deepcopy(LABEL_SAMPLE), **patch}, INSPECT)


def histogram_frame(value):
    frame = copy.deepcopy(FRAME)
    frame['layers'][0]['imageHistogram'] = copy.deepcopy(value)
    return frame


@pytest.mark.parametrize('case', HISTOGRAM['otherValid'], ids=lambda c: c['name'])
def test_histogram_accepts_every_valid_shape(case):
    value = changed(HISTOGRAM['valid'], case)
    assert histogram.described(value) == value
    assert validate_frame(histogram_frame(value))['layers'][0]['imageHistogram'] == value


@pytest.mark.parametrize('case', HISTOGRAM['invalid'], ids=lambda c: c['name'])
def test_histogram_refuses_unsupported_metadata(case):
    value = changed(HISTOGRAM['valid'], case)
    with pytest.raises(ValueError):
        histogram.described(value)
    with pytest.raises(ValueError):
        validate_frame(histogram_frame(value))


@pytest.mark.parametrize('value', HISTOGRAM['contrast']['valid'])
def test_contrast_actions(value):
    assert histogram.contrast(value) == value


@pytest.mark.parametrize('value', HISTOGRAM['contrast']['invalid'])
def test_contrast_actions_refused(value):
    with pytest.raises(ValueError):
        histogram.contrast(value)


def test_histogram_is_not_remembered_and_stays_on_image_layers():
    frame = histogram_frame(HISTOGRAM['valid'])
    frame['layers'][0].update(viewKey='raw', viewGeometry=copy.deepcopy(VIEW['layers'][0]['geometry']))
    assert 'imageHistogram' not in from_frame(frame)['layers'][0]
    frame['layers'][0]['type'] = 'labels'
    with pytest.raises(ValueError):
        validate_frame(frame)


def test_histogram_frame_budget_is_reserved_before_enabling():
    frame = copy.deepcopy(FRAME)
    frame['layers'] = [dict(copy.deepcopy(FRAME['layers'][0]), id=f'{n:032x}') for n in range(1, 13)]
    for row in frame['layers'][:-1]:
        row['imageHistogram'] = {**HISTOGRAM['valid'], 'bins': 512, 'binEdges': [1.2345678901234567e17] * 513,
                                 'counts': [123456.0] * 512}
    with pytest.raises(ValueError):
        histogram.reserve(frame, frame['layers'][-1]['id'], 512)
    histogram.reserve(copy.deepcopy(FRAME), FRAME['layers'][0]['id'], 512)


def test_histogram_change_refusal_keeps_frame():
    session = host()
    layer = SimpleNamespace(_type_string='image')
    session._layers = [('layer', layer)]
    for operation, settings in (('image-histogram', {'enabled': True, 'bins': 0, 'mode': 'canvas', 'logScale': False, 'maxSamples': 1}),
                                ('image-contrast', {'action': 'set-range', 'range': [2, 1]})):
        with pytest.raises(scientific_host.ChangeRefused):
            session.change({'frame': 'current', 'operation': operation, 'value': {'layer': 'layer', 'settings': settings}})
    assert session._frame == 'current' and not session.dirty
