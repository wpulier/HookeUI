"""Synthetic pre-allocation guards; actual napari text parity remains a runtime gate."""
import pytest

from env_worker import scientific_geometry_style as style
from tests.test_scientific_geometry_style import format_layer


def test_large_repeated_feature_is_refused_before_upstream_encoding(monkeypatch):
    import sys
    layer, calls = format_layer(monkeypatch, ['x' * 100_000] * 64)
    def allocating_encoder(*_):
        raise AssertionError('oversized substitution reached the allocating upstream encoder')
    monkeypatch.setattr(sys.modules['napari.layers.utils.string_encoding'].FormatStringEncoding,
                        '__call__', allocating_encoder)
    with pytest.raises(ValueError, match='does not fit'):
        style.prepare_text(layer, {'string': {'kind': 'format', 'value': '{area}' * 100}, 'color': None})
    assert calls == [], 'oversized substitution reached the allocating upstream encoder'
    assert vars(layer.text) == {'string': 'old', 'color': 'old'}


@pytest.mark.parametrize('expression', ['{area.payload}', '{area[0]}', '{area!r}'])
def test_unknown_value_cannot_invoke_user_formatting(monkeypatch, expression):
    class Unbounded:
        def __repr__(self):
            raise AssertionError('unknown repr must not execute')
    layer, calls = format_layer(monkeypatch, [Unbounded()])
    with pytest.raises(ValueError, match='does not fit'):
        style.prepare_text(layer, {'string': {'kind': 'format', 'value': expression}, 'color': None})
    assert calls == []


@pytest.mark.parametrize('value,expression', [
    ('cells', '{area!r:>12}'), (12, '{area:04d}'), (1.234, '{area:.2f}'),
    (None, '{area!s}'), (True, '{area}'), ('μ', '{area!a}'),
])
def test_bounded_scalar_formats_keep_upstream_values(monkeypatch, value, expression):
    layer, calls = format_layer(monkeypatch, [value])
    apply = style.prepare_text(layer, {'string': {'kind': 'format', 'value': expression}, 'color': None})
    assert calls == [1]
    apply()
    assert layer.text.string(layer.features) == [expression.format(area=value)]


def test_numpy_scalar_and_aggregate_repetition_are_bounded(monkeypatch):
    import numpy as np
    layer, calls = format_layer(monkeypatch, [np.float32(1.25)])
    style.prepare_text(layer, {'string': {'kind': 'format', 'value': '{area:.2f}'}, 'color': None})
    assert calls == [1]
    layer, calls = format_layer(monkeypatch, [np.str_('x' * 600)])
    with pytest.raises(ValueError, match='does not fit'):
        style.prepare_text(layer, {'string': {'kind': 'format', 'value': '{area}{area}'}, 'color': None})
    assert calls == []
