"""Real owner journals/files and job runner, with a declared synthetic registration adapter."""
import hashlib
import json

import pytest

from orchestrator.day.store import SessionStore
from orchestrator.project_locations import ProjectLocations
from orchestrator.project_roots import ProjectRoot, ProjectLocationError
from orchestrator.registrations import Registrations
from tests.test_project_evidence_routing import project
from tests.test_registrations import home, volume_file, wait

DATE = '2026-10-29'


def service(day, tmp_path):
    return Registrations(tmp_path/'host/jobs', store=day, today=lambda: DATE)


def test_volume_metadata_job_and_outputs_move_with_project(project, home):
    day, locations, _, _ = project
    owner = day.sid
    regs = service(day, home)
    original = volume_file(home, 'a brain, voxel sizes stated')
    volume = regs.add_volume(str(original))
    first = (regs.folder(volume)/'record.json').read_bytes()
    regs.describe(volume['id'], orientation='psl')
    done = wait(regs, regs.start(volume['id'])['id'])
    assert done['state'] == 'done'
    assert regs.picture(done['id']) == b'picture'
    original.unlink()
    root = locations.project(owner)
    day.new('Another Project')
    assert regs.picture(done['id'], owner) == b'picture'
    assert not regs.root.exists()
    locations.close()
    moved = home/'Moved volumes'; root.path.rename(moved)
    fresh = ProjectLocations(locations.legacy, locations.registry, locations.parent)
    try:
        fresh.registry.register(ProjectRoot.open(moved), replace_inactive=True)
        reopened = SessionStore(fresh.legacy, session=owner, locations=fresh)
        reader = service(reopened, home)
        assert reader.picture(done['id']) == b'picture'
        assert reader.picture(volume['id']).startswith(b'png:')
        history = reader.folder(volume)/'history'/(hashlib.sha256(first).hexdigest()+'.json')
        assert history.read_bytes() == first
        assert (reader.folder(volume)/volume['file']).read_text() == 'a brain, voxel sizes stated'
        assert reader.interrupted() == []
    finally:
        fresh.close()


def test_second_host_cannot_reconcile_or_change_another_hosts_work(project, home):
    day, locations, _, _ = project
    regs = service(day, home)
    v = regs.add_volume(str(volume_file(home, 'a brain, voxel sizes stated')))
    jid, folder = regs._new_id('j', day.sid)
    record = {'session':day.sid, 'date':DATE, 'id':jid, 'state':'running', 'kind':'job'}
    regs._write(record)
    other = ProjectLocations(locations.legacy, locations.registry, locations.parent)
    try:
        reader = service(SessionStore(other.legacy, session=DATE, locations=other), home)
        assert reader.interrupted() == []
        assert reader.get(jid, day.sid)['state'] == 'running'
        with pytest.raises(ProjectLocationError, match='read-only'):
            reader.describe(v['id'], orientation='psl', session=day.sid)
        with pytest.raises(ProjectLocationError, match='read-only'):
            reader.cancel(jid, day.sid)
    finally:
        other.close()


def test_reopen_reconciles_owned_running_job_without_replay(project, home):
    day, locations, _, _ = project
    regs = service(day, home)
    jid, folder = regs._new_id('j', day.sid)
    record = {'session':day.sid, 'date':DATE, 'id':jid, 'state':'running', 'kind':'job'}
    regs._write(record)
    reader = service(day, home)
    assert reader.interrupted()[0]['state'] == 'interrupted'
    assert reader.get(jid)['state'] == 'interrupted'
    assert reader.interrupted() == []
    assert not reader._threads
    assert any(json.loads(p.read_bytes())['state'] == 'running' for p in (folder/'history').glob('*.json'))


@pytest.mark.parametrize('change', ['closed', 'replaced', 'symlink'])
def test_late_volume_publication_refuses_changed_owner(project, home, monkeypatch, change):
    day, locations, _, _ = project
    regs = service(day, home)
    original_write = regs._write
    def change_before_publish(record, *args):
        if change == 'closed':
            locations.close()
        else:
            folder = regs.folder(record)
            folder.rename(folder.with_name('preserved'))
            if change == 'replaced':folder.mkdir()
            else:folder.symlink_to(folder.with_name('preserved'), target_is_directory=True)
        return original_write(record, *args)
    monkeypatch.setattr(regs, '_write', change_before_publish)
    with pytest.raises(ProjectLocationError):
        regs.add_volume(str(volume_file(home, 'a brain, voxel sizes stated')))


def test_metadata_history_failure_preserves_original(project, home, monkeypatch):
    from orchestrator.scientific_projects import ProjectStore
    day, _, _, _ = project
    regs = service(day, home)
    v = regs.add_volume(str(volume_file(home, 'a brain, voxel sizes stated')))
    first = (regs.folder(v)/'record.json').read_bytes()
    def fail(*args, **kwargs):raise OSError('disk full')
    monkeypatch.setattr(ProjectStore, '_write', fail)
    with pytest.raises(OSError, match='disk full'):regs.describe(v['id'], orientation='psl')
    assert (regs.folder(v)/'record.json').read_bytes() == first


def test_same_job_id_in_two_projects_has_separate_cancel_identity(project, home):
    day, _, _, _ = project
    first = day.sid
    regs = service(day, home)
    jid, _ = regs._new_id('j', first)
    regs._write({'session':first, 'date':DATE, 'id':jid, 'state':'running'})
    day.new('Second owner')
    second = day.sid
    # Independent Projects can legitimately contain the same historical local ID.
    other = service(day, home)
    other_id, _ = other._new_id('j', second)
    assert other_id == jid
    other._write({'session':second, 'date':DATE, 'id':jid, 'state':'running'})
    regs.cancel(jid, first)
    assert regs._stop == {(first,jid):True}
    regs.cancel(jid, second)
    assert regs._stop == {(first,jid):True,(second,jid):True}


@pytest.mark.parametrize('boundary', ['input', 'date', 'root', 'inspection'])
def test_dependency_sync_failure_does_not_accept_volume(project, home, monkeypatch, boundary):
    import os
    import stat
    from orchestrator.scientific_projects import ProjectStore
    from orchestrator.job_outputs import Invalid
    day, _, _, _ = project
    regs = service(day, home)
    original_sync = ProjectStore._sync_directory
    original_fsync = os.fsync
    def directory(path):
        if ((boundary == 'inspection' and path.name == 'inspect') or
                (boundary == 'date' and path.name == DATE) or
                (boundary == 'root' and path.name == 'volumes')):
            raise OSError('dependency sync failed')
        original_sync(path)
    def fsync(fd):
        # Input file has not yet been replaced by metadata or a temporary history file.
        if boundary == 'input' and stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError('dependency sync failed')
        original_fsync(fd)
    monkeypatch.setattr(ProjectStore, '_sync_directory', directory)
    monkeypatch.setattr(os, 'fsync', fsync)
    with pytest.raises((OSError, Invalid), match='dependency sync failed'):
        regs.add_volume(str(volume_file(home, 'a brain, voxel sizes stated')))
    assert regs.records('v') == []


def test_failure_resource_report_does_not_write_after_lease_loss(project, home):
    day, locations, _, _ = project
    regs = service(day, home)
    jid, folder = regs._new_id('j', day.sid)
    (folder/'out').mkdir()
    resource = folder/'out/resources.json'
    resource.write_text('original resource evidence')
    record = {'session':day.sid, 'date':DATE, 'id':jid, 'resources':{'outcome':'failed'}}
    locations.close()
    regs._failed_resources(record)
    assert resource.read_text() == 'original resource evidence'
    assert 'closed' in record['resources']['persistenceError']


def test_invalid_metadata_publication_reports_failure_without_stuck_worker(project, home, monkeypatch):
    import time
    from orchestrator.scientific_projects import InvalidProject
    day, _, _, _ = project
    regs = service(day, home)
    jid, _ = regs._new_id('j', day.sid)
    record = {'session':day.sid, 'date':DATE, 'id':jid, 'state':'running', 'resources':{}}
    regs._write(record)
    def failed(*args, **kwargs):raise InvalidProject('record replaced')
    monkeypatch.setattr(regs, '_write', failed)
    regs._complete(record, {'state':'failed','error':'execution failed'}, None, time.monotonic())
    assert record['state'] == 'failed'
    assert record['persistence']['saved'] is False
    assert regs.get(jid)['state'] == 'failed'


def test_rejected_dependency_finishes_failed_instead_of_running(project, home):
    import time
    from types import SimpleNamespace
    day, _, _, _ = project
    regs = service(day, home)
    jid, _ = regs._new_id('j', day.sid)
    record = {'session':day.sid, 'date':DATE, 'id':jid, 'state':'running', 'resources':{}}
    regs._write(record)
    regs._complete(record, {'state':'done', 'outputs':{'picture':{'path':'out/missing.png','sha256':'a'*64}}},
                   SimpleNamespace(check=lambda:None), time.monotonic())
    assert record['state'] == 'failed'
    assert 'output' in record['error']
    assert regs.get(jid)['state'] == 'failed'
