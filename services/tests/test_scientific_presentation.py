from copy import deepcopy

import pytest

from orchestrator.workspace_observation import ObservationError, normalize_observation
from orchestrator.screen import Screen
from tests.test_workspace_observation import CLIENT, report
from tests.test_legacy_workspace_projection import structural_item


def scientific_report():
    raw = report()
    item = structural_item('document:placement-one', 'document', {'document': {
        'project': '2026-10-16', 'tab': 'main', 'placement': 'placement-one',
        'resource': {'day': '2026-10-16', 'id': 'p' + 'a' * 32, 'version': 3, 'picture': None},
        'representation': 'scientific-review', 'state': 'private-preview', 'focused': True,
    }})
    item['workspaceId'] = CLIENT.workspace_id
    item['scientificPaint'] = {'review': 'review-one', 'frame': 'a' * 64, 'draw': 'draw-one',
                               'width': 32, 'height': 24, 'contentRect': [20, 40, 160, 120]}
    raw.update(items=[item], sources=[], targets=[], edges=[], commandResults=[])
    raw['interaction'] = {'pointer': {'state': 'absent', 'modality': 'none', 'buttons': 0,
                                     'hitPath': [], 'targetCandidates': []}, 'contacts': [],
        'focus': {'presentationId': None, 'controlId': None, 'role': 'none', 'editable': False},
        'lastContentPointer': None, 'lastContentFocus': None, 'selected': [],
        'availability': 'partial', 'unknown': []}
    return raw


def checked(raw, previous=None):
    return normalize_observation(raw, client=CLIENT, app_run_id='run-a', received_at=10,
                                 previous=previous)


def test_private_paint_survives_normalization_without_native_authority():
    raw = scientific_report()
    normalized = checked(raw).wire()
    item = normalized['items'][0]
    assert item['scientificPaint'] == raw['items'][0]['scientificPaint']
    assert normalized['sources'] == [] and normalized['commandResults'] == []
    assert item['load']['state'] == 'unknown' and 'sourceToken' not in item and 'viewDrawRef' not in item
    raw['items'][0]['scientificPaint']['frame'] = 'b' * 64
    assert item['scientificPaint']['frame'] == 'a' * 64


@pytest.mark.parametrize('change', [
    {'frame': 'unknown'}, {'width': 0}, {'height': 1025}, {'width': True},
    {'contentRect': [0, 0, 0, 2]}, {'contentRect': [float('nan'), 0, 2, 2]},
    {'review': ''}, {'draw': 'x' * 257}, {'sourceToken': 'not-a-grant'},
])
def test_malformed_or_unbounded_paint_is_refused(change):
    raw = scientific_report()
    raw['items'][0]['scientificPaint'].update(change)
    with pytest.raises(ObservationError): checked(raw)


@pytest.mark.parametrize('field', ['open', 'connected', 'visible'])
def test_hidden_surface_must_revoke_current_paint(field):
    raw = scientific_report()
    raw['items'][0][field] = False
    with pytest.raises(ObservationError): checked(raw)
    raw['items'][0]['scientificPaint'] = None
    assert checked(raw).wire()['items'][0]['scientificPaint'] is None


@pytest.mark.parametrize('change', [
    {'project': 'another-project'}, {'representation': 'table'},
    {'resource': {'day': '2026-10-16', 'id': 'p' + 'a' * 32, 'version': None, 'picture': None}},
    {'resource': {'day': '2026-10-16', 'id': 'p' + 'a' * 32, 'version': 3, 'picture': 0}},
])
def test_preview_requires_an_exact_scientific_document(change):
    raw = scientific_report()
    raw['items'][0]['view']['legacy']['document'].update(change)
    with pytest.raises(ObservationError): checked(raw)


@pytest.mark.parametrize('field,value', [('sourceToken', 'borrowed'), ('viewDrawRef', 'borrowed'),
                                       ('load', {'state': 'drawn', 'drawToken': 'borrowed'})])
def test_scientific_paint_cannot_grant_native_source_authority(field, value):
    raw = scientific_report()
    raw['items'][0][field] = value
    with pytest.raises(ObservationError): checked(raw)


def test_new_paint_or_revocation_must_advance_observed_revision():
    raw = scientific_report()
    before = checked(raw)
    for paint in [None, {**raw['items'][0]['scientificPaint'], 'frame': 'b' * 64}]:
        changed = deepcopy(raw)
        changed['seq'] += 1
        changed['items'][0]['scientificPaint'] = paint
        with pytest.raises(ObservationError, match='revision'): checked(changed, before)
        changed['items'][0]['revision'] += 1
        assert checked(changed, before).wire()['items'][0]['scientificPaint'] == paint


def test_accepted_screen_pins_exact_paint_and_reacceptance_cannot_inherit_it():
    screen = Screen(clock=lambda: 10.0)
    client = object()

    def unexpected(*args):
        raise AssertionError('Private preview must not resolve a native source or capture')

    def accept():
        screen.accept_client(client, CLIENT, app_run_id='run-a',
                             source_resolver=unexpected, capture_authority=unexpected)

    accept()
    raw = scientific_report()
    pin = screen.pin(client, request_id='scientific-action', inline=raw)
    assert pin.observation.wire()['items'][0]['scientificPaint'] == raw['items'][0]['scientificPaint']
    changed = deepcopy(raw)
    changed['seq'] += 1
    changed['items'][0]['revision'] += 1
    changed['items'][0]['scientificPaint'] = None
    screen.report(changed, client=client)
    assert pin.observation.wire()['items'][0]['scientificPaint'] is not None
    assert screen.pin(client, request_id='next-action').observation.wire()['items'][0]['scientificPaint'] is None
    accept()
    with pytest.raises(ObservationError, match='observation'):
        screen.pin(client, request_id='reconnected-action')
