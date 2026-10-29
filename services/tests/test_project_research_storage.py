"""Real journals/files with synthetic researcher records; no provider execution."""
import hashlib
import json
from pathlib import Path

import pytest

from orchestrator.day.store import SessionStore
from orchestrator.project_locations import ProjectLocations
from orchestrator.project_roots import ProjectRegistry, ProjectRoot, ProjectLocationError
from orchestrator.research import ResearchManager, ResearchRefused, Session
from orchestrator.research.storage import ResearchStorage
from orchestrator.scientific_projects import ProjectStore
from tests.test_research import FakeHub

DATE = '2026-10-29'


def setup(tmp_path):
    legacy = tmp_path / 'host/sessions'
    SessionStore(legacy, session=DATE)
    locations = ProjectLocations(legacy, ProjectRegistry(tmp_path / 'host'), tmp_path / 'Projects')
    day = SessionStore(legacy, session=DATE, locations=locations)
    sid = day.new('Retained research')
    manager = ResearchManager(FakeHub(), root=tmp_path / 'host/research', today=lambda: DATE, store=day)
    return locations, day, sid, manager


def session(sid, rid='r123'):
    return Session(rid=rid, n=1, brief='Inspect the retained measurements', deliverable='research',
                   effort='quick', engine='gpt-6.1-sol', purpose='research', label='', object_id=None,
                   origin={'channel': 'chat'}, day=DATE, sitting=sid, status='done',
                   result={'title': 'Observed result', 'summary': 'Synthetic retained finding'},
                   outputs=[{'id':'o1', 'version':1, 'kind':'html', 'path':'outputs/report.html'}])


def test_research_history_and_files_follow_project_after_move(tmp_path):
    locations, day, sid, manager = setup(tmp_path)
    try:
        item = session(sid)
        manager._save(item)
        folder = manager.directory(item)
        (folder / 'outputs').mkdir()
        (folder / 'outputs/report.html').write_text('<p>retained result</p>')
        (folder / 'steps.jsonl').write_text('{"text":"Measured retained values"}\n')
        Path(manager.storage.transcript(item, None)).write_text('{"round":1,"text":"bounded trace"}\n')
        original = (folder / 'session.json').read_bytes()
        item.result = {'title':'Observed result', 'summary':'A corrected interpretation'}
        day.new('Another visible Project')
        manager._save(item)
        assert (folder / 'history' / (hashlib.sha256(original).hexdigest()+'.json')).read_bytes() == original
        root = locations.project(sid)
        locations.close()
        moved = tmp_path / 'Moved research'
        root.path.rename(moved)
        fresh = ProjectLocations(locations.legacy, locations.registry, locations.parent)
        try:
            fresh.registry.register(ProjectRoot.open(moved), replace_inactive=True)
            reopened_day = SessionStore(locations.legacy, session=sid, locations=fresh)
            reopened = ResearchManager(FakeHub(), root=manager.root, today=lambda: DATE, store=reopened_day)
            found = reopened.get(item.rid)
            assert found.sitting == sid and found.result['summary'] == 'A corrected interpretation'
            assert reopened.directory(found).is_relative_to(moved)
            assert (reopened.file_root(DATE,item.rid)/DATE/item.rid/'outputs/report.html').read_text() == '<p>retained result</p>'
            assert Path(reopened.storage.transcript(found,None)).read_text().startswith('{"round":1')
            assert (reopened.directory(found)/'history'/(hashlib.sha256(original).hexdigest()+'.json')).read_bytes() == original
            assert not list(manager.root.glob('*/r*/session.json'))
        finally:
            fresh.close()
    finally:
        locations.close()


def test_second_host_can_read_but_not_publish_or_dispatch(tmp_path):
    locations, day, sid, manager = setup(tmp_path)
    try:
        item = session(sid); manager._save(item)
        other_locations = ProjectLocations(locations.legacy, locations.registry, locations.parent)
        try:
            other_day = SessionStore(locations.legacy, session=DATE, locations=other_locations)
            other = ResearchStorage(manager.root, other_day)
            assert list(other.records(DATE,sid))
            with pytest.raises(ProjectLocationError): other.save(item, {'session':sid})
            with pytest.raises(ResearchRefused,match='not qualified'): manager._portable_execution_ready(sid)
            manager._portable_execution_ready(DATE)  # legacy execution unchanged
        finally: other_locations.close()
    finally: locations.close()


@pytest.mark.parametrize('failure', ['sync', 'owner'])
def test_failed_publication_stays_uncertain_after_new_reader(tmp_path, monkeypatch, failure):
    locations, day, sid, manager = setup(tmp_path)
    try:
        item = session(sid); manager._save(item)
        folder = manager.directory(item)
        original = (folder/'session.json').read_bytes()
        sync = ProjectStore._sync_directory
        calls = 0
        def failing(path):
            nonlocal calls
            calls += 1
            if calls == 3:
                if failure == 'sync': raise OSError('sync failure')
                locations.close()
            return sync(path)
        monkeypatch.setattr(ProjectStore,'_sync_directory',staticmethod(failing))
        item.result = {'summary':'new interpretation'}
        with pytest.raises((OSError,ProjectLocationError)): manager._save(item)
        assert (folder/'.publication-pending').exists()
        fresh = ResearchStorage(manager.root, day)
        with pytest.raises(ProjectLocationError,match='incomplete'): fresh.read(folder/'session.json')
        assert (folder/'history'/(hashlib.sha256(original).hexdigest()+'.json')).read_bytes() == original
    finally: locations.close()


def test_research_identity_collision_never_substitutes_another_project(tmp_path):
    locations, day, sid, manager = setup(tmp_path)
    try:
        first=session(sid); manager._save(first)
        manager.sessions[first.rid] = first
        second_sid = day.new('Second Project')
        second=session(second_sid); manager._save(second)
        with pytest.raises(ProjectLocationError,match='ambiguous'):
            manager._load_session(second_sid)
        with pytest.raises(ProjectLocationError,match='ambiguous'):
            manager.file_root(DATE,first.rid)
        assert manager.get(first.rid) is first
    finally: locations.close()


def test_closed_or_replaced_folder_rejects_late_save(tmp_path):
    locations, day, sid, manager = setup(tmp_path)
    item=session(sid); manager._save(item)
    folder=manager.directory(item)
    folder.rename(folder.with_name('old'))
    folder.mkdir()
    with pytest.raises(ProjectLocationError,match='changed'): manager._save(item)
    locations.close()
    with pytest.raises(ProjectLocationError,match='closed'): manager._save(item)


def test_restoring_interrupted_work_never_replays_or_writes_from_readonly_host(tmp_path):
    locations, day, sid, manager = setup(tmp_path)
    try:
        item=session(sid); item.status='running'
        item.interaction_jobs=[{'state':'dispatched'}]
        manager._save(item)
        path=manager.directory(item)/'session.json'; before=path.read_bytes()
        reader_locations=ProjectLocations(locations.legacy,locations.registry,locations.parent)
        try:
            reader_day=SessionStore(locations.legacy,session=DATE,locations=reader_locations)
            reader=ResearchManager(FakeHub(),root=manager.root,today=lambda:DATE,store=reader_day)
            reader._load_session(sid)
            restored=reader.get(item.rid)
            assert restored.status=='interrupted'
            assert restored.interaction_jobs[0]['state']=='interrupted'
            assert path.read_bytes()==before and reader._worker is None
        finally: reader_locations.close()
    finally: locations.close()


def test_concurrent_publishers_cannot_share_the_pending_marker(tmp_path, monkeypatch):
    locations, day, sid, manager = setup(tmp_path)
    try:
        item=session(sid); manager._save(item)
        folder=manager.directory(item); before=(folder/'session.json').read_bytes()
        other=ResearchStorage(manager.root,day)
        write=ProjectStore._write
        attempted=[]
        def interleave(path, body, **options):
            if path.name == '.publication-pending':
                with pytest.raises(ProjectLocationError,match='being saved'):
                    other.save(item, {'session':sid,'rid':item.rid,'other':'must not publish'})
                attempted.append(True)
            return write(path,body,**options)
        monkeypatch.setattr(ProjectStore,'_write',staticmethod(interleave))
        item.result={'summary':'first accepted update'}; manager._save(item)
        assert attempted == [True]
        assert manager.storage.read(folder/'session.json')['result']['summary']=='first accepted update'
        assert (folder/'history'/(hashlib.sha256(before).hexdigest()+'.json')).read_bytes()==before
        assert not (folder/'.publication-pending').exists()
    finally: locations.close()


def test_cross_date_startup_collision_refuses_before_inserting(tmp_path):
    locations, day, sid, manager = setup(tmp_path)
    try:
        first=session(sid); first.day='2026-10-28'; manager._save(first)
        # Explicitly include the earlier day in the scoped restoration.
        other_sid=day.new('Other Project'); second=session(other_sid); second.status='running'; manager._save(second)
        original=manager._dates_of
        manager._dates_of=lambda selected: ['2026-10-28'] if selected==sid else original(selected)
        manager._loaded.discard(sid)
        with pytest.raises(ProjectLocationError,match='ambiguous'): manager._load_session(sid,start=True)
        assert not manager.sessions
    finally: locations.close()


def test_offline_project_and_pending_peer_do_not_hide_healthy_research(tmp_path):
    locations, day, sid, manager = setup(tmp_path)
    try:
        good=session(sid); manager._save(good)
        bad=session(sid,'r999'); manager._save(bad)
        (manager.directory(bad)/'.publication-pending').write_text('interrupted')
        offline_sid=day.new('Offline Project'); offline=locations.project(offline_sid)
        offline.path.rename(tmp_path/'Temporarily offline')
        reader=ResearchManager(FakeHub(),root=manager.root,today=lambda:DATE,session=lambda:DATE,store=day)
        reader._load_session(sid)
        assert reader.get(good.rid).result==good.result
        assert reader.file_root(DATE,good.rid)==manager.directory(good).parent.parent
        unavailable=reader.list_wire()['unavailable']
        assert any(row['session']==offline_sid for row in unavailable)
        assert any(row['rid']==bad.rid for row in unavailable)
        with pytest.raises(ProjectLocationError,match='unavailable'): reader.file_root(DATE,bad.rid)
    finally: locations.close()


def test_step_read_is_bounded_nofollow_and_preserves_trace(tmp_path):
    locations, day, sid, manager=setup(tmp_path)
    try:
        item=session(sid); manager._save(item)
        path=manager.directory(item)/'steps.jsonl'
        path.write_text(''.join(json.dumps({'text':str(n)})+'\n' for n in range(225)))
        before=path.read_bytes(); result=manager.storage.steps(item)
        assert result['stepsTotal']==225 and len(result['steps'])==200
        assert result['steps'][0]['text']=='25' and path.read_bytes()==before
        target=path.with_name('original-steps.jsonl'); path.rename(target); path.symlink_to(target)
        with pytest.raises(OSError): manager.storage.steps(item)
        path.unlink(); path.write_text('x'*65537)
        with pytest.raises(ProjectLocationError,match='bounded'): manager.storage.steps(item)
    finally: locations.close()
