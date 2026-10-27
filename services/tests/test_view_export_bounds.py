"""Synthetic dispatch/renderer boundary regressions; no upstream renderer qualification."""
import copy

import numpy as np
import pytest

from orchestrator import view_export
from tests.test_maintenance_scenarios import frame, preview, H, W


def test_dispatch_owns_nested_view_and_region_values():
    shown = frame(1, 2)
    shown['dimensions']['labels'] = ['T', 'Z', 'Y', 'X']
    shown['camera']['center'] = [2., 3.]
    shown['layers'][0]['style'] = {'colors': ['red']}
    shown['projection'] = {'mode': 'mean', 'range': [1, 3]}
    region = {'x': 1, 'y': 2, 'width': 4, 'height': 5}
    dispatch = view_export.capture(shown, scope='selected-region', region=region)
    retained = copy.deepcopy(dispatch)
    shown['dimensions']['labels'][0] = 'changed'
    shown['camera']['center'][0] = 900.
    shown['layers'][0]['style']['colors'].append('blue')
    shown['projection']['range'][0] = 0
    region['x'] = 99
    assert dispatch == retained


@pytest.mark.parametrize('size', [(True, 2), (0, 2), (2, -1), (2, 3, 4), (2.5, 3)])
def test_invalid_requested_size_is_refused_at_capture(size):
    with pytest.raises(view_export.Refused, match='size'):
        view_export.capture(frame(1, 2), scope='current-view', size=size)


def test_size_mismatch_refuses_before_renderer_and_write(tmp_path):
    dispatch = view_export.capture(frame(1, 2), scope='current-view', size=(W * 2, H * 2))
    called = []
    with pytest.raises(view_export.Refused, match='size'):
        view_export.export(dispatch, lambda d: called.append(d) or preview(d), preview(dispatch), tmp_path/'wrong.png',
                           entry='test', generation='g', version='1')
    assert called == [] and not (tmp_path/'wrong.png').exists()
    assert view_export.check(dispatch, preview(dispatch), preview(dispatch))


def test_renderer_cannot_mutate_retained_provenance_or_comparison(tmp_path, monkeypatch):
    monkeypatch.setenv('MICROMIND_CATALOG', str(tmp_path/'catalog'))
    dispatch = view_export.capture(frame(1, 2), scope='current-view', size=(W, H))
    original = copy.deepcopy(dispatch)
    pixels = preview(dispatch)
    def render(given):
        given['camera']['zoom'] = 100
        given['dimensions']['steps'][1] = 0
        return pixels.copy()
    record = view_export.export(dispatch, render, pixels, tmp_path/'view.png', entry='test', generation='g', version='1')
    assert dispatch == original
    assert record['dimensions'] == original['dimensions'] and record['camera'] == original['camera']


def test_dispatch_refuses_nonfinite_view_metadata():
    shown = frame(1, 2); shown['camera']['zoom'] = float('nan')
    with pytest.raises(view_export.Refused, match='metadata'):
        view_export.capture(shown, scope='current-view')
