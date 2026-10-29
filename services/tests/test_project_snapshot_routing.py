"""Real isolated Project journals and snapshot files; synthetic image payloads."""
from types import SimpleNamespace

import pytest

from orchestrator.day.store import SessionStore
from orchestrator.project_locations import ProjectLocations
from orchestrator.project_roots import ProjectRoot, ProjectLocationError
from orchestrator.project_snapshots import Snapshots
from orchestrator.resources import Index
from orchestrator.scientific_projects import ProjectStore
from tests.test_project_evidence_routing import project

DATE='2026-10-29'
NAME='snapshot-120000-0123456789ab-f1'


def save(day, tmp_path, name=NAME):
    store=Snapshots(tmp_path/'host/snapshots', day)
    facts={'session':day.sid,'wallAt':1000.,'frameId':1,'overlays':True,'actor':'you'}
    store.save(day.sid,DATE,name,facts,b'annotated picture',b'clean picture')
    return store


def test_snapshot_and_clean_source_move_with_owner(project,tmp_path):
    day,locations,_,hub=project;owner=day.sid
    store=save(day,tmp_path)
    root=locations.project(owner)
    day.new('Another Project')
    index=Index(hub,day=day,snapshots=store.storage.legacy)
    assert index.picture(NAME,day=owner)==(b'annotated picture','image/png')
    assert index._snapshots(owner)[0].made_by['actor']=='you'
    assert not store.storage.legacy.exists()
    locations.close()
    moved=tmp_path/'Moved snapshots';root.path.rename(moved)
    fresh=ProjectLocations(locations.legacy,locations.registry,locations.parent)
    try:
        fresh.registry.register(ProjectRoot.open(moved),replace_inactive=True)
        reopened=SessionStore(fresh.legacy,session=owner,locations=fresh)
        reader=Snapshots(store.storage.legacy,reopened)
        assert reader.picture(owner,DATE,NAME)==b'annotated picture'
        assert reader.picture(owner,DATE,NAME,clean=True)==b'clean picture'
    finally:fresh.close()


def test_second_host_reads_without_writer_and_cannot_publish(project,tmp_path):
    day,locations,_,_=project;store=save(day,tmp_path)
    other=ProjectLocations(locations.legacy,locations.registry,locations.parent)
    try:
        reader=Snapshots(store.storage.legacy,SessionStore(other.legacy,session=DATE,locations=other))
        assert reader.picture(day.sid,DATE,NAME)==b'annotated picture'
        with pytest.raises(ProjectLocationError,match='read-only'):
            reader.save(day.sid,DATE,NAME,{'session':day.sid},b'x')
    finally:other.close()


def test_interrupted_snapshot_is_not_accepted_after_reopen(project,tmp_path,monkeypatch):
    day,_,_,hub=project;store=Snapshots(tmp_path/'host/snapshots',day)
    original=ProjectStore._write
    def failing(path,data,**options):
        result=original(path,data,**options)
        if path.suffix=='.json':raise OSError('publication failed')
        return result
    with monkeypatch.context() as patch:
        patch.setattr(ProjectStore,'_write',failing)
        with pytest.raises(OSError):store.save(day.sid,DATE,NAME,{'session':day.sid},b'picture')
    reader=Snapshots(store.storage.legacy,day)
    assert reader.read(day.sid,DATE,NAME) is None
    assert Index(hub,day=day,snapshots=store.storage.legacy)._snapshots(day.sid)==[]
    save(day,tmp_path,NAME.replace('f1','f2'))
    assert reader.picture(day.sid,DATE,NAME.replace('f1','f2'))==b'annotated picture'


def test_changed_bytes_and_replaced_clean_folder_refuse(project,tmp_path):
    day,_,_,_=project;store=save(day,tmp_path)
    folder=store.folder(day.sid,DATE)
    (folder/f'{NAME}.png').write_bytes(b'changed')
    with pytest.raises(ProjectLocationError,match='changed'):store.picture(day.sid,DATE,NAME)
    (folder/'clean').rename(folder/'prior-clean')
    (folder/'clean').symlink_to(folder/'prior-clean',target_is_directory=True)
    with pytest.raises(ProjectLocationError,match='replaced'):store.picture(day.sid,DATE,NAME,clean=True)


def test_closed_host_during_encoding_cannot_accept_snapshot(project,tmp_path):
    day,locations,_,_=project;owner=day.sid
    store=Snapshots(tmp_path/'host/snapshots',day)
    admitted=store.folder(owner,DATE,write=True)
    locations.close()
    with pytest.raises(ProjectLocationError):
        store.save(owner,DATE,NAME,{'session':owner},b'picture',admitted=admitted)
    assert not (admitted/f'{NAME}.json').exists()


def test_owner_selection_does_not_retarget_late_snapshot(project,tmp_path):
    day,_,_,_=project;owner=day.sid
    store=Snapshots(tmp_path/'host/snapshots',day)
    admitted=store.folder(owner,DATE,write=True)
    day.new('Later Project')
    store.save(owner,DATE,NAME,{'session':owner},b'picture',admitted=admitted)
    assert store.read(day.sid,DATE,NAME) is None
    assert store.picture(owner,DATE,NAME)==b'picture'


def test_snapshot_tool_uses_portable_storage_with_real_drawing(project,tmp_path):
    import cv2
    import numpy as np
    from orchestrator.brain.toolbox import Services, build
    from orchestrator.magnifier import Magnifier
    from tests.test_seeing import make_hub, feed, call
    day,_,_,_=project
    hub=make_hub();feed(hub,1)
    legacy=tmp_path/'host/snapshots'
    registry=build(Services(hub=hub,day=day,magnifier=Magnifier(hub),snapshots=legacy))
    run=call(registry,'snapshot',{})
    assert run.ok,run.summary
    index=Index(hub,day=day,snapshots=legacy)
    entries=index._snapshots(day.sid)
    assert len(entries)==1
    image=index.picture(entries[0].id,day=day.sid)[0]
    assert cv2.imdecode(np.frombuffer(image,np.uint8),cv2.IMREAD_COLOR) is not None
    assert index.snapshot_store.picture(day.sid,entries[0].date,entries[0].id,clean=True)!=image
    assert not legacy.exists()


def test_replaced_date_directory_during_publication_is_refused(project,tmp_path):
    day,_,_,_=project;store=Snapshots(tmp_path/'host/snapshots',day)
    admitted=store.folder(day.sid,DATE,write=True)
    admitted.rename(admitted.with_name('original-date'));admitted.mkdir()
    with pytest.raises(ProjectLocationError,match='replaced'):
        store.save(day.sid,DATE,NAME,{'session':day.sid},b'picture',admitted=admitted)
    assert not list(admitted.iterdir())


def test_conflicting_resave_preserves_accepted_visibility(project,tmp_path):
    day,_,_,hub=project;store=save(day,tmp_path)
    with pytest.raises(ProjectLocationError,match='already exists'):
        store.save(day.sid,DATE,NAME,{'session':day.sid},b'different')
    reader=Snapshots(store.storage.legacy,day)
    assert reader.picture(day.sid,DATE,NAME)==b'annotated picture'
    assert len(Index(hub,day=day,snapshots=store.storage.legacy)._snapshots(day.sid))==1


@pytest.mark.parametrize('failure', ['directory-sync', 'parent-sync', 'acceptance'])
def test_failed_completion_stays_unaccepted_after_host_reopen(project,tmp_path,monkeypatch,failure):
    from pathlib import Path
    day,locations,_,hub=project;owner=day.sid
    store=Snapshots(tmp_path/'host/snapshots',day)
    folder=store.folder(owner,DATE,write=True)
    original_sync=ProjectStore._sync_directory
    original_unlink=Path.unlink
    def sync(path):
        completed=(folder/f'{NAME}.json').exists()
        if completed and ((failure=='directory-sync' and path==folder) or
                          (failure=='parent-sync' and path==folder.parent)):
            raise OSError('disk lost after writing snapshot')
        return original_sync(path)
    def unlink(path,*args,**kwargs):
        if failure=='acceptance' and path==folder/f'{NAME}.pending':
            raise OSError('cannot accept snapshot')
        return original_unlink(path,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(ProjectStore,'_sync_directory',sync)
        patch.setattr(Path,'unlink',unlink)
        with pytest.raises(OSError):store.save(owner,DATE,NAME,{'session':owner},b'picture')
    assert (folder/f'{NAME}.png').read_bytes()==b'picture'
    assert (folder/f'{NAME}.pending').exists()
    locations.close()
    fresh=ProjectLocations(locations.legacy,locations.registry,locations.parent)
    try:
        reopened=SessionStore(fresh.legacy,session=owner,locations=fresh)
        reader=Snapshots(store.storage.legacy,reopened)
        assert reader.read(owner,DATE,NAME) is None
        assert Index(hub,day=reopened,snapshots=store.storage.legacy)._snapshots(owner)==[]
        with pytest.raises(ProjectLocationError,match='already exists'):
            reader.save(owner,DATE,NAME,{'session':owner},b'retry')
        assert (folder/f'{NAME}.png').read_bytes()==b'picture'
    finally:fresh.close()


def test_no_fallible_storage_work_follows_snapshot_acceptance(project,tmp_path,monkeypatch):
    day,_,_,_=project;store=Snapshots(tmp_path/'host/snapshots',day)
    folder=store.folder(day.sid,DATE,write=True)
    original_sync=ProjectStore._sync_directory
    def sync(path):
        if (folder/f'{NAME}.json').exists() and not (folder/f'{NAME}.pending').exists():
            raise OSError('storage unavailable after acceptance')
        return original_sync(path)
    with monkeypatch.context() as patch:
        patch.setattr(ProjectStore,'_sync_directory',sync)
        store.save(day.sid,DATE,NAME,{'session':day.sid},b'picture')
    assert Snapshots(store.storage.legacy,day).picture(day.sid,DATE,NAME)==b'picture'
