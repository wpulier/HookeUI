"""TEST-ONLY App/COMMANDS/Screen tracers authored on frozen Root 2db7adc8.

Browser records and paint references are synthetic. The existing Rig supplies real
App state, locally retained synthetic JPEGs and its trusted source resolver; actual
canonical ingress, projection and immutable Send pins are exercised. No renderer,
browser, physical paint, live registration, model or requested-goal success is proved.
No completion-only report, fake outcome or proposed production helper is used.
Parent owns the first observed RED run; these tests have not been executed here.
"""
import asyncio
import copy

import pytest

from orchestrator.capture import RUN
from orchestrator.workspace_observation import ObservationError, normalize_observation
from tests.test_screen_workspace import REF, Rig


def test_actual_ingress_preserves_nullable_view_paint_in_items_original_results_and_send_pin(tmp_path):
    rig = Rig(tmp_path)
    rig.accept()
    raw = rig.message()
    raw['commandResults'] = []
    for item, paint in zip(raw['items'], ('synthetic-view-paint-a', None)):
        item.update(viewDrawRef=paint, view={'zoom': 2, 'pan': [0.5, 0.5]})
        item['propertyVersions']['zoom'] = 7
        item['propertyOwners']['zoom'] = 'original-action'
        item['controls'].append({'id': f'zoom:{item["id"]}', 'targetId': item['id'],
            'role': 'button', 'editable': False, 'present': True, 'enabled': True,
            'stateRevision': 7, 'observedValue': 2})
        raw['commandResults'].append({'requestId': 'original-request', 'actionId': 'original-action',
            'commandId': 'original-command', 'client': REF.wire(), 'appRunId': RUN,
            'presentationId': item['id'], 'phase': 'drawn', 'reportSeq': raw['seq'],
            'lifetimeGeneration': item['lifetimeGeneration'], 'sourceGeneration': item['sourceGeneration'],
            'committedRevision': item['revision'], 'committedPropertyVersions': {'zoom': 7},
            'sourceKey': item['sourceKey'], 'sourceToken': item['sourceToken'],
            'payloadBinding': copy.deepcopy(item['payloadBinding']),
            'propertyVersions': copy.deepcopy(item['propertyVersions']),
            'propertyOwners': copy.deepcopy(item['propertyOwners']),
            'drawRef': item['load']['drawToken'], 'viewDrawRef': paint})
    # A raw view reference/verified flag cannot create server live-registration authority.
    raw['liveEvidence'] = [{'presentationId': 'window-a', 'verifiedCount': 160,
        'drawnCount': 160, 'registration': {'state': 'verified', 'basis': 'per-member-pixel-match'}}]
    assert asyncio.run(rig.ingest(raw))['ok'] is True
    normalized = normalize_observation(raw, client=REF, app_run_id=RUN, received_at=rig.now).wire()
    observed = rig.observed().wire()
    projected = rig.screen.completion_report(rig.client)
    pinned = rig.pin(request_id='original-request').wire()
    for index, paint in enumerate(('synthetic-view-paint-a', None)):
        for wire in (normalized, observed, projected, pinned['observation']):
            assert 'viewDrawRef' in wire['items'][index], 'MISSING_FEATURE: canonical nullable view paint is retained'
            assert wire['items'][index]['viewDrawRef'] == paint
            assert wire['commandResults'][index]['viewDrawRef'] == paint
        assert projected['items'][index]['drawRef'] == raw['items'][index]['load']['drawToken']
        assert projected['items'][index]['drawRef'] != paint  # media identity is a separate observation
        assert projected['items'][index]['controls']['zoom']['role'] == 'zoom'
        assert projected['commandResults'][index]['sourceRef'] == rig.sources[raw['items'][index]['sourceKey']]
    assert not projected.get('liveEvidence'), 'raw paint references are not per-member authority'
    assert not pinned['observation'].get('liveEvidence')
    raw['items'][0]['viewDrawRef'] = 'mutated-input'
    projected['items'][0]['viewDrawRef'] = 'mutated-export'
    projected['commandResults'][0]['viewDrawRef'] = 'mutated-result-export'
    assert rig.observed().wire()['items'][0]['viewDrawRef'] == 'synthetic-view-paint-a'
    assert rig.screen.completion_report(rig.client)['commandResults'][0]['viewDrawRef'] == 'synthetic-view-paint-a'
    assert pinned['observation']['items'][0]['viewDrawRef'] == 'synthetic-view-paint-a'


def test_actual_view_paint_ingress_refuses_bound_replay_and_wrong_origin_atomically(tmp_path):
    rig = Rig(tmp_path)
    rig.accept()
    raw = rig.message()
    item = raw['items'][0]
    item['viewDrawRef'] = 'synthetic-original-view-paint'
    raw['commandResults'] = [{'requestId': 'original-request', 'actionId': 'original-action',
        'commandId': 'original-command', 'client': REF.wire(), 'appRunId': RUN,
        'presentationId': item['id'], 'phase': 'drawn', 'reportSeq': raw['seq'],
        'lifetimeGeneration': item['lifetimeGeneration'], 'sourceGeneration': item['sourceGeneration'],
        'committedRevision': item['revision'], 'committedPropertyVersions': {'rect': 2},
        'sourceKey': item['sourceKey'], 'sourceToken': item['sourceToken'],
        'propertyVersions': copy.deepcopy(item['propertyVersions']),
        'propertyOwners': copy.deepcopy(item['propertyOwners']),
        'viewDrawRef': item['viewDrawRef'], 'drawRef': item['load']['drawToken']}]
    assert asyncio.run(rig.ingest(raw))['ok'] is True
    before = rig.observed().wire()
    pinned = rig.pin(request_id='original-request').wire()
    received = rig.observed().received_at
    for change, code in [('item-bound', 'INVALID_REPORT'), ('result-bound', 'INVALID_REPORT'),
                         ('older-sequence', 'REPORT_REPLAYED'), ('same-sequence-new-paint', 'REPORT_REPLAYED'),
                         ('wrong-report-origin', 'CLIENT_MISMATCH'), ('wrong-result-origin', 'CLIENT_MISMATCH')]:
        bad = copy.deepcopy(raw)
        bad['seq'] = 11
        if change == 'item-bound':
            bad['items'][0]['viewDrawRef'] = 'p' * 257
        elif change == 'result-bound':
            bad['commandResults'][0]['viewDrawRef'] = 'p' * 257
        elif change == 'older-sequence':
            bad['seq'] = bad['commandResults'][0]['reportSeq'] = 9
        elif change == 'same-sequence-new-paint':
            bad['seq'] = raw['seq']
            bad['items'][0]['viewDrawRef'] = 'different-paint-at-the-same-sequence'
        elif change == 'wrong-report-origin':
            bad['client']['pageEpoch'] = 'another-page'
        else:
            bad['commandResults'][0]['client']['connectionEpoch'] = 'another-socket'
        rig.now += 1
        result = asyncio.run(rig.ingest(bad))
        assert result.get('ok') is False and result.get('code') == code, change
        assert rig.observed().wire() == before, change
        assert rig.observed().received_at == received, change
        assert rig.pin(request_id='original-request').wire()['observation'] == pinned['observation'], change


def test_current_view_changes_preserve_original_source_lifetime_versions_occurrence_and_snapshot(tmp_path):
    rig = Rig(tmp_path)
    rig.accept()
    raw = rig.message()
    item = raw['items'][0]
    item['viewDrawRef'] = 'synthetic-original-view-paint'
    item['propertyVersions']['zoom'] = 7
    item['propertyOwners']['zoom'] = 'original-action'
    original = {'requestId': 'original-request', 'actionId': 'original-action',
        'commandId': 'original-command', 'client': REF.wire(), 'appRunId': RUN,
        'presentationId': item['id'], 'phase': 'drawn', 'reportSeq': raw['seq'],
        'lifetimeGeneration': item['lifetimeGeneration'], 'sourceGeneration': item['sourceGeneration'],
        'committedRevision': item['revision'], 'committedPropertyVersions': {'zoom': 7},
        'sourceKey': item['sourceKey'], 'sourceToken': item['sourceToken'],
        'propertyVersions': copy.deepcopy(item['propertyVersions']),
        'propertyOwners': copy.deepcopy(item['propertyOwners']),
        'viewDrawRef': item['viewDrawRef'], 'drawRef': item['load']['drawToken']}
    raw['commandResults'] = [copy.deepcopy(original)]
    assert asyncio.run(rig.ingest(raw))['ok'] is True
    snapshot = rig.pin(request_id='original-request')
    frozen = snapshot.wire()
    for seq, refusal in ((11, 'OWNERSHIP_STALE'), (12, 'TARGET_GONE')):
        later = copy.deepcopy(raw)
        later['seq'] = seq
        current = later['items'][0]
        current['viewDrawRef'] = f'synthetic-user-view-paint-{seq}'
        current['revision'] += seq - 10
        current['propertyVersions']['zoom'] = seq - 3
        current['propertyOwners']['zoom'] = 'user'
        if seq == 12:
            current['lifetimeGeneration'] = 'replacement-lifetime'
            current['sourceGeneration'] = 2
            current['sourceKey'] = 'source-b'
            current['sourceToken'] = 'served:source-b'
            current['payloadBinding'] = copy.deepcopy(later['items'][1]['payloadBinding'])
            current['load'].update(sourceKey='source-b', sourceGeneration=2, drawToken='replacement-media-draw')
            later['targets'][0]['sourceKey'] = 'source-b'
        # Synthetic Runtime result shape: old occurrence/fences survive, while its
        # superseded view has no current matching paint. No old success is fabricated.
        result = later['commandResults'][0]
        result.update(phase='unknown', reportSeq=seq, viewDrawRef=None, drawRef=None,
            propertyVersions=copy.deepcopy(current['propertyVersions']),
            propertyOwners=copy.deepcopy(current['propertyOwners']))
        result['sourceRef'] = copy.deepcopy(rig.sources['source-b'])  # raw override must be discarded
        rig.now += 0.1
        assert asyncio.run(rig.ingest(later))['ok'] is True
        projected = rig.screen.completion_report(rig.client)
        retained = projected['commandResults'][0]
        for field in ('requestId', 'actionId', 'commandId', 'presentationId', 'appRunId',
                      'sourceKey', 'sourceToken', 'sourceGeneration', 'lifetimeGeneration',
                      'committedRevision', 'committedPropertyVersions'):
            assert retained[field] == original[field], field
        assert retained['sourceRef'] == rig.sources['source-a']
        assert retained['observedPropertyVersions']['zoom'] == current['propertyVersions']['zoom']
        assert retained['observedPropertyOwners']['zoom'] == 'user'
        assert retained['phase'] == 'unknown' and retained['viewDrawRef'] is None and retained['drawRef'] is None
        assert projected['items'][0]['viewDrawRef'] == current['viewDrawRef']
        with pytest.raises(ObservationError) as refused:
            rig.screen.target(snapshot, 'target-a', properties=('zoom',))
        assert refused.value.code == refusal
        assert snapshot.wire() == frozen
        assert frozen['observation']['items'][0]['viewDrawRef'] == 'synthetic-original-view-paint'
        assert frozen['observation']['items'][0]['propertyOwners']['zoom'] == 'original-action'
        assert frozen['observation']['commandResults'][0] == original
