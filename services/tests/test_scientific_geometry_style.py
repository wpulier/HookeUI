"""Points and Shapes styling: complete settings refused before any change, and the frame's description checked.

The actual-worker comparison with plain pinned napari is in test_scientific_points_shapes_parity.py and the
production-Studio journeys in test_scientific_points_shapes_browser.py; this file needs no napari.
"""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from env_worker import scientific_geometry_style as style, scientific_host, scientific_view_wire as wire
from tests.test_scientific_change_recovery import host

CASES = json.loads((Path(__file__).resolve().parents[2] / 'contract/scientific-geometry-style-cases.json').read_text())
VIEW = json.loads((Path(__file__).resolve().parents[2] / 'contract/scientific-view-cases.json').read_text())
CHOICES = {'features': [['kind', 'categorical'], ['area', 'numeric']], 'colormaps': ['gray', 'viridis']}


def refused(operation, kind, value):
    session = host()
    layer = SimpleNamespace(_type_string=kind)
    session._layers = [('layer', layer)]
    with pytest.raises(scientific_host.ChangeRefused):
        session.change({'frame': 'current', 'operation': operation, 'value': value})
    assert vars(layer) == {'_type_string': kind} and session._frame == 'current' and not session.dirty


@pytest.mark.parametrize('case', CASES['invalidPoints'], ids=lambda case: case['name'])
def test_invalid_marker_style_changes_nothing(case):
    refused('points-style', 'points', {'layer': 'layer', 'settings': {**CASES['points'], **case['patch']}})


@pytest.mark.parametrize('case', CASES['invalidShapes'], ids=lambda case: case['name'])
def test_invalid_shape_style_changes_nothing(case):
    refused('shapes-style', 'shapes', {'layer': 'layer', 'settings': {**CASES['shapes'], **case['patch']}})


@pytest.mark.parametrize('case', CASES['invalidText'], ids=lambda case: case['name'])
def test_invalid_text_content_changes_nothing(case):
    refused('text-content', 'points', {'layer': 'layer', 'settings': {**CASES['text'], **case['patch']}})


@pytest.mark.parametrize('operation,kind,value', [
    ('points-style', 'shapes', {'layer': 'layer', 'settings': CASES['points']}),
    ('shapes-style', 'points', {'layer': 'layer', 'settings': CASES['shapes']}),
    ('text-content', 'image', {'layer': 'layer', 'settings': CASES['text']}),
    ('shapes-order', 'shapes', {'layer': 'layer', 'action': 'top'}),
    ('shapes-order', 'points', {'layer': 'layer', 'action': 'front'}),
    ('points-style', 'points', {'layer': 'gone', 'settings': CASES['points']}),
    ('points-style', 'points', {'layer': 'layer', 'settings': CASES['points'], 'extra': 1}),
])
def test_a_style_change_needs_its_own_current_layer_kind(operation, kind, value):
    refused(operation, kind, value)


def test_valid_settings_round_trip_exactly():
    assert style.point_settings(CASES['points']) == CASES['points']
    assert style.shape_settings(CASES['shapes']) == CASES['shapes']
    assert style.text_settings(CASES['text']) == CASES['text']
    assert style.order('front') == 'front' and style.order('back') == 'back'
    for value in (float('nan'), float('inf'), 10**400):
        with pytest.raises(ValueError):
            style.point_settings({**CASES['points'], 'size': value})


def frame_with(**fields):
    value = copy.deepcopy(VIEW['valid'])
    value['layers'][0].update(fields)
    return value


def test_the_frame_carries_style_only_for_its_layer_kind():
    points = frame_with(type='points', pointStyle=CASES['points'], textContent=CASES['text'], styleChoices=CHOICES)
    assert wire.frame(points)['layers'][0]['pointStyle'] == CASES['points']
    shapes = frame_with(type='shapes', shapeStyle=CASES['shapes'], textContent={'string': {'kind': 'other', 'value': ''}, 'color': None},
                        styleChoices=CHOICES)
    assert wire.frame(shapes)['layers'][0]['textContent']['string']['kind'] == 'other'
    long_cycle = {**CASES['points'], 'face': {'mode': 'cycle', 'feature': 'kind', 'cycle': None}}  # too long to edit here
    assert wire.frame(frame_with(type='points', pointStyle=long_cycle, styleChoices=CHOICES))
    for bad in (frame_with(type='image', pointStyle=CASES['points']), frame_with(type='points', pointStyle=CASES['points']), frame_with(type='points', shapeStyle=CASES['shapes']),
                frame_with(type='points', pointStyle={**CASES['points'], 'symbol': 'circle'}),
                frame_with(type='points', styleChoices={**CHOICES, 'features': [['x' * 129, 'numeric']]}),
                frame_with(type='points', textContent={'string': {'kind': 'other', 'value': 'x'}, 'color': None})):
        with pytest.raises(ValueError):
            wire.frame(bad)


def test_an_inspected_element_style_is_complete_and_valid():
    point = {'symbol': 'star', 'size': 2.0, 'borderWidth': 0.5, 'faceColor': [0.0, 0.0, 1.0, 1.0], 'borderColor': [1.0, 0.0, 0.0, 1.0]}
    shape = {'edgeWidth': 1.0, 'faceColor': [0.0, 1.0, 0.0, 1.0], 'edgeColor': [0.0, 0.0, 1.0, 1.0], 'z': -1}
    assert style.element_style(point, 'points') == point and style.element_style(shape, 'shapes') == shape
    for bad, kind in (({**point, 'symbol': 'circle'}, 'points'), ({**point, 'faceColor': [2, 0, 0, 1]}, 'points'),
                      ({k: v for k, v in point.items() if k != 'size'}, 'points'), ({**shape, 'z': 0.5}, 'shapes'),
                      (point, 'shapes')):
        with pytest.raises(ValueError):
            style.element_style(bad, kind)


@pytest.mark.parametrize('kind,key,base,side', [('points', 'pointStyle', 'points', 'border'),
                                            ('shapes', 'shapeStyle', 'shapes', 'face')])
def test_constant_feature_colormap_survives_frame_and_style_round_trip(kind, key, base, side):
    constant = {'mode': 'colormap', 'feature': 'area', 'colormap': 'gray', 'contrastLimits': [7.0, 7.0]}
    settings = {**CASES[base], side: constant}
    frame = wire.frame(frame_with(type=kind, **{key: settings}, styleChoices=CHOICES))
    assert frame['layers'][0][key][side] == constant
    # Editing another field must preserve this valid native color state unchanged.
    validate = style.point_settings if kind == 'points' else style.shape_settings
    assert validate(settings)[side] == constant


def format_layer(monkeypatch, values):
    import sys
    from types import ModuleType
    # A fixture at napari's encoding boundary; the actual pinned encoding remains a separate runtime gate.
    module = ModuleType('napari.layers.utils.string_encoding')
    calls = []
    class Encoding:
        def __init__(self, **settings):
            self.settings = settings
        def __call__(self, features):
            calls.append(len(features))
            return [self.settings['format'].format(**row) for row in features.to_dict('records')]
    module.ConstantStringEncoding = module.DirectStringEncoding = module.FormatStringEncoding = Encoding
    monkeypatch.setitem(sys.modules, module.__name__, module)
    class Features(list):
        columns = ['area']
        @property
        def iloc(self):
            return self
        def __getitem__(self, selection):
            return Features(super().__getitem__(selection))
        def to_dict(self, _):
            return [{'area': value} for value in self]
    previous = SimpleNamespace(string='old', color='old')
    layer = SimpleNamespace(features=Features(values), text=previous)
    return layer, calls


def test_a_later_incompatible_feature_refuses_text_before_mutation(monkeypatch):
    layer, calls = format_layer(monkeypatch, [1.0] * 128 + ['incompatible'])
    previous = layer.text
    with pytest.raises(ValueError, match='does not fit'):
        style.prepare_text(layer, {'string': {'kind': 'format', 'value': '{area:.1f}'}, 'color': None})
    assert layer.text is previous and vars(previous) == {'string': 'old', 'color': 'old'}
    assert calls == [64, 64]  # refuse the incompatible final row before upstream allocation


@pytest.mark.parametrize('problem', ['rows', 'width', 'dynamic-width', 'output'])
def test_text_format_budget_refuses_before_mutation(monkeypatch, problem):
    layer, calls = format_layer(monkeypatch, [1.0] * 129)
    value = '{area:.1f}'
    if problem == 'rows':
        monkeypatch.setattr(style, 'MAX_FORMAT_ROWS', 128)
    elif problem == 'width':
        value = '{area:1000000000}'
    elif problem == 'dynamic-width':
        value = '{area:{area}}'
    else:
        monkeypatch.setattr(style, 'MAX_FORMAT_CHARS', 2)
    with pytest.raises(ValueError, match='does not fit'):
        style.prepare_text(layer, {'string': {'kind': 'format', 'value': value}, 'color': None})
    assert vars(layer.text) == {'string': 'old', 'color': 'old'}
    assert calls == []


def test_every_feature_is_validated_in_bounded_batches_before_applying_text(monkeypatch):
    layer, calls = format_layer(monkeypatch, [1.0] * 129)
    apply = style.prepare_text(layer, {'string': {'kind': 'format', 'value': '{area:.1f}'}, 'color': None})
    assert calls == [64, 64, 1] and layer.text.string == 'old'
    apply()
    assert layer.text.string.settings == {'format': '{area:.1f}'}
