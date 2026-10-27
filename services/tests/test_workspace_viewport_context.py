"""Original CSS viewport at accepted Send -> actual FAST/ASK model-input seams.

Real App/Screen/pin/Brain/dispatch owners; external reports, camera pixels and
provider replies are synthetic. No geometry command or model quality is proved.
The unchanged RequestRig trace wrappers delegate to the real owners.
"""
import asyncio
import copy
import json

import pytest

from tests.test_workspace_request_observation import OTHER, RequestRig
from tests.waiting import HANG_S


@pytest.mark.parametrize('fast', ['off', 'gemini-3.5-flash'])
def test_original_css_viewport_reaches_model_input_without_borrowing_later_reports(tmp_path, monkeypatch, fast):
    rig = RequestRig(tmp_path, monkeypatch, fast=fast, mode='plan', gate=True)

    async def scenario():
        await rig.camera()
        raw = rig.message(seq=11)
        raw['viewport'].update(layoutSize=[1344.25, 844.5], visualRect=[12.5, 8.25, 1100.5, 700.25],
                               visualScale=1.25, documentScroll=[0, 0], dpr=2)
        assert (await rig.ingest(raw))['ok']
        expected = copy.deepcopy(rig.observed().wire())
        pending = asyncio.create_task(rig.send(screen=copy.deepcopy(raw),
            text='Use the originating viewport facts for positioning the retained windows.'))
        try:
            await asyncio.wait_for(rig.provider.entered.wait(), HANG_S)
            first = rig.provider.sends[0]
            client, request_id, pin = first.pins[-1]
            assert client is rig.client and pin.timing == 'client-send'
            assert request_id == first.admission.request_id
            assert pin.observation.wire() == expected
            assert first.phase == ('fast' if fast != 'off' else 'look')
            assert bool(first.images) is (fast == 'off')

            peer = rig.message(seq=80, ref=OTHER)
            peer['viewport'].update(layoutSize=[2200, 1400], visualRect=[0, 0, 2200, 1400])
            assert (await rig.ingest(peer, rig.other))['ok']
            later = rig.message(seq=12)
            later['viewport'].update(layoutSize=[900, 700], visualRect=[0, 0, 900, 700])
            assert (await rig.ingest(later))['ok']
            raw['viewport']['layoutSize'][0] = 9999  # source dictionary cannot mutate the retained pin
            rig.provider.release.set()
            answer = await asyncio.wait_for(pending, 1)
            assert answer['ok'] and answer['actions'] == []

            moment = first.moments[-1]
            assert moment.request_snapshot is pin
            facts = moment.awareness['observations']
            report = facts['workspace_report']
            assert report['client'] == expected['client'] and report['seq'] == 11
            assert report['provenance'] == 'structured-request-pin' and report['current'] is True
            assert report['viewport']['layoutSize'] == [1344.25, 844.5]
            assert report['viewport']['visualRect'] == [12.5, 8.25, 1100.5, 700.25]
            assert report['placements']['window-a'] == expected['items'][0]['rect']
            assert report['placements']['window-b'] == expected['items'][1]['rect']
            assert facts['camera']['available'] is True
            assert facts['workspace_image']['available'] is False and facts['workspace_image']['verified'] is False
            # Actual provider input must preserve these CSS facts as whole JSON;
            # a Moment-only field or a physical-pixel/dpr substitution is insufficient.
            line = next(line for line in first.prompt.splitlines() if line.startswith('App facts ('))
            supplied = json.loads(line.split(': ', 1)[1])['observations']['workspace_report']
            assert supplied['viewport']['layoutSize'] == [1344.25, 844.5]
            assert supplied['viewport']['visualRect'] == [12.5, 8.25, 1100.5, 700.25]
            assert supplied['client'] == expected['client'] and supplied['seq'] == 11
            assert all(send.admission is first.admission for send in rig.provider.sends)
            assert first.admission.used == len(rig.provider.sends) <= 3
            assert pin.observation.wire() == expected
        finally:
            rig.provider.release.set()
            await asyncio.gather(pending, return_exceptions=True)

    try:
        asyncio.run(scenario())
    finally:
        rig.close()
