"""Actual confined synthetic volume jobs retain their initiating workspace."""
import pytest

from orchestrator import workspace_views
from orchestrator.day import DayStore
from tests.capability_rig import Rig
from tests.test_bundle_actors import command
from tests.test_env_runner import home  # noqa: F401
from tests.test_volume_kinds import on, a_volume, wait  # noqa: F401


@pytest.mark.parametrize('actor', ['action', 'try'])
def test_volume_try_retains_job_origin_and_reopens_its_real_table(on, tmp_path, actor):
    rig = Rig(tmp_path / 'rig')
    rig.app.catalog_changed()
    day, regs = rig.app.day, rig.app.registrations
    volume = regs.add_volume(str(a_volume(on)))
    regs.describe(volume['id'], voxel_um=[2, .5, .5])
    workspace_views.save(day, workspace_views.read(day, schema=2)['layout'], 0)
    origin = {'session': day.sid, 'tab': 'main', 'placementId': 'pending-volume',
              'run': 'volume-run', 'input': {'volume': volume['id']}}
    message = {'type': 'capability-run', 'model': 'spot-counter'} if actor == 'try' else {'type': 'action', 'capability': 'catalog:spot-counter', 'args': {}}
    reply = command(rig, {**message, 'session': day.sid,
        'input': 'volume.3d', 'volume': volume['id'], 'run': 'volume-run', 'workspaceOrigin': origin})
    assert reply.get('ok') is not False, str(reply)
    job = reply['backgroundJob']
    assert job['session'] == day.sid
    completed = wait(regs, job['id'])
    assert completed['state'] == 'done', completed
    assert completed['workspaceOrigin'] == origin
    assert regs.table(job['id'])['rows'][0][5] == '007'
    reopened = DayStore(root=day.root, day=day.sid)
    workspace_views.reconcile_jobs(reopened, regs.records('j', day.sid))
    workspace_views.reconcile_jobs(reopened, regs.records('j', day.sid))
    completions = workspace_views.read(reopened, schema=2)['completions']
    assert len(completions) == 1
    assert completions[0]['origin'] == origin
    assert completions[0]['item']['presentation'] == 'table'
    assert completions[0]['item']['resource']['id'] == job['id']
    regs.stop()
