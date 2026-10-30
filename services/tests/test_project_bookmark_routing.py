"""Isolated real Project journals and bookmark storage; synthetic camera image."""
import concurrent.futures
import threading
from types import SimpleNamespace

import pytest
from orchestrator.bookmarks import Bookmarks
from orchestrator.day.store import SessionStore
from orchestrator.project_locations import ProjectLocations
from orchestrator.project_roots import ProjectLocationError, ProjectRoot
from orchestrator.resources import Index
from tests.waiting import HANG_S
from tests.test_evidence import view
from tests.test_project_evidence_routing import project


def marks(day, tmp_path):
    return Bookmarks(tmp_path/'host/bookmarks', today=lambda:'2026-10-29', store=day)


def capture():
    c=view()
    from orchestrator.capture import Capture
    return Capture(c.id,c.jpeg,SimpleNamespace(width=160,height=120,epoch=0,dx=3.,dy=4.,disc=0.,wall_at=1000.,
                   fov=SimpleNamespace(cx=80.,cy=60.,radius=50.)))


def test_bookmarks_move_with_owner_and_open_from_inactive_project(project,tmp_path):
    day,locations,_,hub=project
    owner=day.sid
    store=marks(day,tmp_path)
    mark=store.add(capture(),hub=SimpleNamespace(),note='Retained place',session=owner,actor='agent:reviewer')
    store.journal(mark)
    image=store.picture(mark['id'],session=owner)
    assert image
    assert not store.root.exists()
    root=locations.project(owner)
    day.new('Other Project')
    assert store.session_marks(owner,['2026-10-29'])[0]['actor']=='agent:reviewer'
    assert Index(hub,day=day,bookmarks=store).picture(mark['id'],day=owner)[0]==image
    locations.close()
    moved=tmp_path/'Moved bookmarks';root.path.rename(moved)
    fresh=ProjectLocations(locations.legacy,locations.registry,locations.parent)
    try:
        fresh.registry.register(ProjectRoot.open(moved),replace_inactive=True)
        reader=marks(SessionStore(fresh.legacy,session=owner,locations=fresh),tmp_path)
        assert reader.picture(mark['id'])==image
        assert reader.all()[0]['note']=='Retained place'
    finally:
        fresh.close()


def test_delayed_bookmark_keeps_captured_owner(project,tmp_path,monkeypatch):
    from orchestrator import bookmarks
    day,locations,_,_=project
    owner=day.sid;store=marks(day,tmp_path)
    entered=threading.Event();release=threading.Event()
    original=bookmarks._thumbnail
    def delayed(*args):
        entered.set();assert release.wait(HANG_S);return original(*args)
    monkeypatch.setattr(bookmarks,'_thumbnail',delayed)
    with concurrent.futures.ThreadPoolExecutor() as executor:
        pending=executor.submit(store.add,capture(),session=owner,hub=SimpleNamespace())
        assert entered.wait(HANG_S)
        try:
            day.new('Other Project')
        finally:
            release.set()
        mark=pending.result(timeout=HANG_S)
    assert mark['session']==owner
    assert store.all()==[]
    assert store.picture(mark['id'],mark['day'],session=owner)


def test_other_host_can_read_but_not_add_bookmarks(project,tmp_path):
    day,locations,_,_=project;store=marks(day,tmp_path)
    mark=store.add(capture(),hub=SimpleNamespace())
    other=ProjectLocations(locations.legacy,locations.registry,locations.parent)
    try:
        reader=marks(SessionStore(other.legacy,session='2026-10-29',locations=other),tmp_path)
        assert reader.picture(mark['id'],session=day.sid)==store.picture(mark['id'])
        with pytest.raises(ProjectLocationError,match='read-only'):
            reader.add(capture(),session=day.sid)
    finally:
        other.close()


def test_changed_picture_and_replaced_storage_refuse(project,tmp_path):
    day,locations,_,_=project;store=marks(day,tmp_path)
    mark=store.add(capture(),hub=SimpleNamespace())
    folder=store.folder()
    (folder/mark['picture']).write_bytes(b'changed')
    with pytest.raises(ValueError,match='picture changed'):
        store.picture(mark['id'])
    folder.rename(folder.with_name('original'))
    folder.symlink_to(folder.with_name('original'),target_is_directory=True)
    with pytest.raises(ValueError,match='storage changed'):
        store.add(capture(),hub=SimpleNamespace())


def test_failed_publication_never_overwrites_accepted_bookmark(project,tmp_path,monkeypatch):
    from orchestrator.scientific_projects import ProjectStore
    day,locations,_,_=project;store=marks(day,tmp_path)
    first=store.add(capture(),hub=SimpleNamespace(),note='kept')
    original=ProjectStore._write
    def fail_json(path,data,**kwargs):
        if path.suffix=='.json':raise OSError('disk full')
        return original(path,data,**kwargs)
    with monkeypatch.context() as scoped:
        scoped.setattr(ProjectStore,'_write',fail_json)
        with pytest.raises(OSError,match='disk full'):store.add(capture(),hub=SimpleNamespace(),note='not accepted')
    assert [m['id'] for m in store.all()]==[first['id']]
    retry=store.add(capture(),hub=SimpleNamespace(),note='retry')
    assert retry['id']!=first['id']
    assert len(store.all())==2


@pytest.mark.parametrize('change',['close','replace'])
def test_lost_destination_during_picture_write_does_not_publish(project,tmp_path,monkeypatch,change):
    from orchestrator.scientific_projects import ProjectStore
    day,locations,_,_=project;store=marks(day,tmp_path)
    owner=day.sid
    original=ProjectStore._write
    entered=threading.Event();release=threading.Event()
    def blocked(path,data,**kwargs):
        result=original(path,data,**kwargs)
        if path.suffix=='.jpg':
            entered.set();assert release.wait(HANG_S)
        return result
    monkeypatch.setattr(ProjectStore,'_write',blocked)
    with concurrent.futures.ThreadPoolExecutor() as executor:
        pending=executor.submit(store.add,capture(),session=owner,hub=SimpleNamespace())
        assert entered.wait(HANG_S)
        folder=store.folder(session=owner)
        try:
            if change=='close':locations.close()
            else:
                root=folder.parent
                root.rename(root.with_name('original-bookmarks'))
                root.mkdir()
        finally:release.set()
        with pytest.raises(ProjectLocationError):pending.result(timeout=HANG_S)
    original_folder=folder if change=='close' else folder.parent.with_name('original-bookmarks')/folder.name
    assert list(original_folder.glob('*.pending'))
    assert not list(original_folder.glob('*.json'))


def test_post_link_failure_stays_incomplete_on_reopen(project,tmp_path,monkeypatch):
    from orchestrator.scientific_projects import ProjectStore
    day,locations,_,_=project;store=marks(day,tmp_path)
    original=ProjectStore._write
    def failed_receipt(path,data,**kwargs):
        result=original(path,data,**kwargs)
        if path.suffix=='.json':raise OSError('directory sync failed after link')
        return result
    with monkeypatch.context() as scoped:
        scoped.setattr(ProjectStore,'_write',failed_receipt)
        with pytest.raises(OSError):store.add(capture(),hub=SimpleNamespace())
    assert list(store.folder().glob('*.json')) # bytes retained, not accepted
    assert store.all()==[]
    assert marks(day,tmp_path).all()==[]
    assert store.picture('b1') is None
    store.add(capture(),hub=SimpleNamespace(),note='retry')
    assert [m['note'] for m in store.all()]==['retry']
