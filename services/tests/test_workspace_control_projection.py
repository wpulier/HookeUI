"""Actual accepted Screen projection, with synthetic registered widget observations.

No browser control operation, paint, model or camera acceptance is claimed.
The server registration is separate from all untrusted report fields.
"""
import asyncio
import copy

import pytest

from orchestrator.capture import RUN
from tests.test_screen_workspace import REF, Rig


def control(name, *, cid=None, role='button', editable=False, target='window-a'):
    return {'id': cid or f'{name}:window-a', 'targetId': target, 'role': role,
            'editable': editable, 'enabled': True, 'present': True,
            'stateRevision': 7, 'observedValue': 3.2 if name == 'zoom' else 0.4}


def project(rig, raw):
    assert asyncio.run(rig.ingest(raw))['ok']
    return next(row for row in rig.screen.completion_report(rig.client)['items']
                if row['id'] == 'window-a')


@pytest.mark.parametrize('name', ['zoom', 'pan', 'front'])
def test_accepted_default_effect_control_preserves_registration_and_semantic_role(tmp_path, name):
    rig = Rig(tmp_path)
    rig.accept()
    raw = rig.message()
    supplied = control(name)
    raw['items'][0]['controls'] = [supplied]
    raw['items'][0]['propertyVersions'][name] = 7
    raw['items'][0]['propertyOwners'][name] = 'original-action'
    state = project(rig, raw)['controls'][name]
    assert state['id'] == supplied['id']
    assert state['role'] == name and state['widgetRole'] == 'button'
    assert state['editable'] is False
    assert state['target'] == 'window-a' and state['present'] and state['enabled']
    assert state['stateRevision'] == 7 and state['observedValue'] == supplied['observedValue']
    # The exported record is not mutable authority over the accepted observation.
    state['id'] = 'invented-control'
    assert rig.screen.completion_report(rig.client)['items'][0]['controls'][name]['id'] == supplied['id']


def test_explicit_server_registration_validates_raw_slider_before_semantic_projection(tmp_path):
    rig = Rig(tmp_path)
    cid = 'registered-random-root-horizontal-pan'
    rig.screen.accept_client(rig.client, REF, app_run_id=RUN,
        source_resolver=rig.resolve_source, capture_authority=rig.verify_capture,
        control_names={cid: {'name': 'pan', 'role': 'slider'}})
    raw = rig.message()
    raw['items'][0]['controls'] = [control('pan', cid=cid, role='slider')]
    state = project(rig, raw)['controls']['pan']
    assert state['id'] == cid and state['role'] == 'pan' and state['widgetRole'] == 'slider'
    assert state['editable'] is False and state['stateRevision'] == 7


@pytest.mark.parametrize('change', ['wrong-role', 'editable', 'wrong-target', 'unregistered-id'])
def test_raw_control_fields_cannot_mint_a_trusted_effect_capability(tmp_path, change):
    rig = Rig(tmp_path)
    rig.accept()
    raw = rig.message()
    supplied = control('zoom')
    supplied.update(semanticRole='zoom', verified=True, authority='registered')
    if change == 'wrong-role':
        supplied['role'] = 'slider'
    elif change == 'editable':
        supplied.update(editable=True, observedValue='private editor value')
    elif change == 'wrong-target':
        supplied['targetId'] = 'window-b'
    else:
        supplied['id'] = 'arbitrary-root-zoom'
    raw['items'][0]['controls'] = [supplied]
    assert 'zoom' not in project(rig, raw)['controls']
    assert 'private editor value' not in str(rig.screen.completion_report(rig.client))


def test_missing_editability_fact_remains_unknown_for_completion(tmp_path):
    rig = Rig(tmp_path)
    rig.accept()
    raw = rig.message()
    supplied = control('zoom')
    supplied.pop('editable')
    raw['items'][0]['controls'] = [supplied]
    state = project(rig, raw)['controls']['zoom']
    assert state['id'] == supplied['id'] and state['role'] == 'zoom'
    assert state['editable'] is None
