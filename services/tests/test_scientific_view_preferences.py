import copy
import json
from pathlib import Path

import pytest

from env_worker.scientific_view_preferences import preferences, from_frame, compatible

CONTRACT = Path(__file__).resolve().parents[2] / 'contract'
CASES = json.loads((CONTRACT / 'scientific-view-preferences-cases.json').read_text())
FRAME = json.loads((CONTRACT / 'scientific-view-cases.json').read_text())['valid']


def test_applied_frame_snapshot_is_detached_and_requires_declared_identity():
    frame = copy.deepcopy(FRAME)
    with pytest.raises(ValueError, match='temporary layers'):
        from_frame(frame)
    frame['layers'][0]['viewKey'] = 'raw'
    frame['layers'][0]['viewGeometry'] = copy.deepcopy(CASES['valid']['layers'][0]['geometry'])
    saved = from_frame(frame)
    assert saved == CASES['valid']
    frame['layers'][0]['opacity'] = .25
    assert saved['layers'][0]['opacity'] == 1
    assert compatible(saved, frame) == saved
    frame['layers'][0]['name'] = 'Renamed layer'
    frame['layers'][0]['id'] = '2' * 32
    assert compatible(saved, frame) == saved


@pytest.mark.parametrize('case', CASES['otherValid'], ids=lambda c:c['name'])
def test_native_exact_preferences(case):
    assert preferences(case['value']) == case['value']


@pytest.mark.parametrize('case', CASES['invalid'], ids=lambda c:c['name'])
def test_preferences_refuse_unsupported_values(case):
    value = copy.deepcopy(CASES['valid'])
    target = value
    for key in case['path'][:-1]:
        target = target[key]
    target[case['path'][-1]] = case['value']
    with pytest.raises(ValueError):
        preferences(value)


@pytest.mark.parametrize('change', ['axes', 'range', 'transform', 'key', 'type', 'affine', 'units'])
def test_changed_integration_never_guesses_a_layer_or_coordinate(change):
    frame = copy.deepcopy(FRAME)
    frame['layers'][0]['viewKey'] = 'raw'
    frame['layers'][0]['viewGeometry'] = copy.deepcopy(CASES['valid']['layers'][0]['geometry'])
    saved = from_frame(frame)
    if change == 'axes': frame['dimensions']['labels'][0] = 'T'
    if change == 'range': frame['dimensions']['ranges'][0][1] += 1
    if change == 'transform': frame['layers'][0]['scale'][0] *= 2
    if change == 'key': frame['layers'][0]['viewKey'] = 'new'
    if change == 'type': frame['layers'][0]['type'] = 'labels'
    if change == 'affine': frame['layers'][0]['viewGeometry']['affine'][0][0] *= -1
    if change == 'units': frame['layers'][0]['viewGeometry']['units'][0] = 'micrometer'
    with pytest.raises(ValueError, match='no longer match'):
        compatible(saved, frame)
