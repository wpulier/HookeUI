"""Actual ask/context over accepted structural reports with unavailable geometry.

Only external pixels and answer-provider responses are synthetic. Existing
App/Screen/Brain/pin/dispatch owners are unchanged; this does not certify model
quality or actual browser pixels. The real browser hit KeyError('rect') when
the Studio's closed surfaces correctly omitted their unmeasured rectangles.
"""
import asyncio
import copy

import pytest

from orchestrator.brain import context
from tests.test_workspace_request_observation import PLAN, RequestRig


@pytest.mark.parametrize('fast', ['off', 'gemini-3.5-flash'])
@pytest.mark.parametrize('state', ['hidden', 'closed', 'disconnected'])
def test_ordinary_request_keeps_camera_and_source_facts_when_surface_geometry_is_unknown(
        tmp_path, monkeypatch, fast, state):
    rig = RequestRig(tmp_path, monkeypatch, fast=fast, mode='plan')

    async def scenario():
        await rig.camera()
        raw = rig.message(seq=11)
        absent = raw['items'][1]
        absent['visible'] = False
        if state == 'closed':
            absent['open'] = False
        elif state == 'disconnected':
            absent['connected'] = False
        for key in ('rect', 'contentRect', 'clipRect', 'viewTransform'):
            absent.pop(key)
        accepted = await rig.ingest(raw)
        assert accepted['ok'] is True, accepted
        original = copy.deepcopy(rig.observed().wire())
        assert 'rect' not in original['items'][1]
        assert original['items'][1]['sourceKey'] == 'source-b'
        assert original['items'][0]['rect'] == raw['items'][0]['rect']

        answer = await rig.send(screen=copy.deepcopy(raw), text='highlight the other stoma')
        assert answer['ok'] and answer['answer'] == PLAN
        assert rig.provider.sends, 'the actual request must reach external provider dispatch'
        _, request_id, pin = rig.pins[-1]
        assert pin.observation.wire() == original
        assert request_id == rig.provider.sends[0].admission.request_id
        moment = rig.moments[0]
        facts = moment.awareness['observations']
        assert facts['camera']['available'] is True
        assert facts['camera']['provenance'] == 'raw-camera'
        assert facts['workspace_image']['available'] is False
        assert facts['workspace_image']['verified'] is False
        assert facts['workspace_report']['placements']['window-a'] == raw['items'][0]['rect']
        assert facts['workspace_report']['placements']['window-b'] is None
        assert moment.request_snapshot is pin
        assert moment.request_snapshot.observation.wire()['items'][1] == original['items'][1]
        assert all(send.admission is rig.provider.sends[0].admission for send in rig.provider.sends)
        assert rig.provider.sends[0].admission.used == len(rig.provider.sends) <= 3
        # All renderers consume the same retained Moment; no application reply
        # or phantom rectangle is inserted to compensate for unavailable input.
        assert context.scene_table(moment)
        assert context.fast_fields(moment)
        assert context.view_note(moment)
        assert answer['actions'] == []

    try:
        asyncio.run(scenario())
    finally:
        rig.close()
