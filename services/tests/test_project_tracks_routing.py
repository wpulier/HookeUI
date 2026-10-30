"""Real retained track snapshots and journals, with synthetic points/image bytes."""
import copy
from types import SimpleNamespace

import numpy as np
import pytest

from orchestrator.day.store import SessionStore
from orchestrator.motiontracks import Tracks, TracksStore
from orchestrator.project_evidence import resolve
from orchestrator.project_locations import ProjectLocations
from orchestrator.project_roots import ProjectRoot, ProjectLocationError
from orchestrator.resources import Index
from tests.test_evidence import view
from tests.test_motion_tracks import wait_saved
from tests.test_project_evidence_routing import project


def make(day, evidence, hub):
    store = TracksStore(hub, evidence.root, day=day)
    hub.tracks_store = store
    capture = view()
    item = Tracks(store.new_id(), 'measured', 'motion', 'synthetic', {'execution':'motion-test'}, capture.id,
                  {'width':160,'height':120,'epoch':0}, None, {'from':1000.,'to':1001.,'seconds':1.},
                  {'unit':'px/s'}, {}, [{'id':'track1','values':{'net':2.},'segments':[{'epoch':0,'start':1000.,'end':1001.,'n':2}]}],
                  [[np.array([[1.,2.,1000.,1.],[3.,4.,1001.,2.]])]],
                  day=day.date, session=day.sid, capture=capture, picture=capture.jpeg)
    store.add(item); store.schedule_save(item)
    assert wait_saved(item) == 'saved', item.status_why
    return store, item


def test_track_saves_move_and_reopen_without_recomputing_motion(project, tmp_path):
    day, locations, evidence, hub = project
    owner = day.sid
    store, item = make(day,evidence,hub)
    first = copy.deepcopy(day.tracks[item.id])
    original = store.points(item)[0][0].copy()
    original_image = store.picture(item)
    store.schedule_save(item)
    assert wait_saved(item) == 'saved', item.status_why
    assert day.tracks[item.id]['path'] != first['path']
    assert resolve(store,owner,first).exists()
    root = locations.project(owner)
    day.new('Different view')
    index = Index(hub,day=day)
    assert index.captured_manifest(item.id,owner)[0]['schema'] == 'tracks/1'
    assert index.picture(item.id,day=owner)[0] == original_image
    locations.close()
    moved=tmp_path/'Moved tracks'; root.path.rename(moved)
    fresh=ProjectLocations(locations.legacy,locations.registry,locations.parent)
    try:
        fresh.registry.register(ProjectRoot.open(moved),replace_inactive=True)
        reopened=SessionStore(fresh.legacy,session=owner,locations=fresh)
        reader=TracksStore(SimpleNamespace(),store.root,day=reopened)
        back=reader.get(item.id)
        assert back and back.path.is_relative_to(moved)
        assert np.array_equal(reader.points(back)[0][0],original)
        assert reader.picture(back)==original_image
        assert resolve(reader,owner,first).is_relative_to(moved)
        assert reader.new_id()!=item.id
    finally:
        fresh.close()


def test_damaged_track_archive_cannot_be_blessed_by_resave(project):
    day,_,evidence,hub=project
    store,item=make(day,evidence,hub)
    accepted=copy.deepcopy(day.tracks[item.id])
    np.savez_compressed(item.path/'tracks.npz', **{'track1.s0':np.zeros((2,4))})
    store.schedule_save(item)
    assert wait_saved(item)=='failed'
    assert day.tracks[item.id]==accepted
    assert 'Previous evidence bytes changed' in item.status_why


@pytest.mark.parametrize('closed', [True,False])
def test_tracks_require_original_host_writer(project,closed):
    day,locations,evidence,hub=project
    store,item=make(day,evidence,hub)
    accepted=copy.deepcopy(day.tracks[item.id])
    if closed:
        locations.close()
        store.schedule_save(item)
        assert wait_saved(item)=='failed'
    else:
        other=ProjectLocations(locations.legacy,locations.registry,locations.parent)
        try:
            reader=TracksStore(SimpleNamespace(),store.root,day=SessionStore(other.legacy,session='2026-10-29',locations=other))
            assert resolve(reader,day.sid,accepted)==item.path
            with pytest.raises(ProjectLocationError,match='read-only'):
                reader.root_for(day.sid,write=True)
        finally:
            other.close()
    assert day.tracks[item.id]==accepted


def test_workspace_recovers_project_relative_track_completion(project):
    from orchestrator import workspace_views as w
    from tests.test_workspace_recovery import migrated
    day,_,evidence,hub=project
    w.save(day,migrated(day),0)
    origin={'session':day.sid,'tab':'main','placementId':'motion-place','run':'motion-run'}
    w.admit(day,origin);w.link_execution(day,origin,'motion-test')
    _,item=make(day,evidence,hub)
    w.reconcile(day)
    rows=w.read(day,schema=2)['completions']
    assert len(rows)==1 and rows[0]['item']['resource']['id']==item.id
    assert rows[0]['item']['presentation']=='table'


def test_late_read_failure_keeps_other_tracks_available(project, monkeypatch):
    from pathlib import Path
    import sys
    day,_,evidence,hub=project
    _,first=make(day,evidence,hub)
    _,second=make(day,evidence,hub)
    original=Path.lstat
    def missing_at_admission(path):
        if path==first.path/'manifest.json' and sys._getframe(1).f_code.co_name=='read_back':
            raise FileNotFoundError('storage disappeared before admission')
        return original(path)
    monkeypatch.setattr(Path,'lstat',missing_at_admission)
    reader=TracksStore(SimpleNamespace(),evidence.root,day=day)
    assert reader.get(first.id) is None
    assert reader.get(second.id) is not None
    assert any(first.id in problem and 'storage disappeared' in problem for problem in reader.problems)


def test_pending_track_save_keeps_original_project_when_workspace_switches(project, monkeypatch):
    import threading
    from orchestrator import project_evidence
    from tests.waiting import HANG_S
    day,locations,evidence,hub=project
    owner=day.sid
    store,item=make(day,evidence,hub)
    entered,release=threading.Event(),threading.Event()
    original=project_evidence.sync_publication
    def paused(store,item,folder):
        original(store,item,folder)
        entered.set()
        assert release.wait(HANG_S)
    monkeypatch.setattr(project_evidence,'sync_publication',paused)
    store.schedule_save(item)
    try:
        assert entered.wait(HANG_S)
        other=day.new('Another workspace')
    finally:
        release.set()
    assert wait_saved(item)=='saved',item.status_why
    assert not day.tracks
    old=day.elsewhere(owner)
    assert item.id in old.tracks and item.path.is_relative_to(locations.project(owner).path)
    assert not item.path.is_relative_to(locations.project(other).path)
