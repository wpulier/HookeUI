"""Real live motion sample identity; synthetic external DOM geometry, no browser proof."""
import asyncio
import copy
import json

import httpx

from orchestrator.brain.toolbox.present import _opening_basis, _placement_observation
from orchestrator.paths import Paths
from orchestrator.server import create_app
from tests.test_closed_presentation_source_metadata import initial, report
from tests.test_native_live_motion_delivery import GRANT, camera_samples


def test_live_sample_advances_without_changing_beside_placement_authority(tmp_path):
    async def go():
        web = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))
        try:
            socket, origin, state, lease = await initial(web, 'live')
            app = web.state.app
            states = {state.id: state}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(web), base_url='http://127.0.0.1:8770') as http:
                async def observed(seq):
                    response = await http.get('/api/presentation-sources/' + lease.token,
                                              headers={GRANT: origin['connectionGrant']})
                    assert response.status_code == 200
                    sample = json.loads(response.headers['X-Micromind-Live-Sample'])
                    message = report(origin, seq, [(state, lease)])
                    row = message['items'][0]
                    row['liveSample'] = sample
                    row['load'].update(state='drawn', drawToken=f'actual-sample-external-paint-{seq}')
                    app.hub.screen.report(message, socket)
                    return app.hub.screen.completion_report(socket)
                before = await observed(2)
                arrangement = _opening_basis({'kind': 'beside', 'reference': state.id}, states, before, [320, 240])
                await camera_samples(web, socket, start=100)
                after = await observed(3)
                assert before['items'][0]['liveSample'] != after['items'][0]['liveSample']
                assert before['items'][0]['drawRef'] != after['items'][0]['drawRef']
                area, projected = _placement_observation(states, after, (state.id,))
                assert area == arrangement[1]
                assert projected[state.id] == arrangement[2]
                for field in ('rect', 'rootId', 'sourceToken'):
                    changed = copy.deepcopy(after)
                    row = changed['items'][0]
                    if field == 'rect': row['rect'][0] += 1
                    else: row[field] += '-different'
                    _, current = _placement_observation(states, changed, (state.id,))
                    assert current[state.id] != arrangement[2], field
        finally:
            await web.state.app.stop()
    asyncio.run(go())
