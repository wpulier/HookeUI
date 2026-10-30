"""Real Project journals and identity replay; synthetic observation values."""
import json
from types import SimpleNamespace

import pytest
from orchestrator.day.store import SessionStore
from orchestrator.identity import Identity
from orchestrator.project_locations import ProjectLocations
from orchestrator.project_roots import ProjectLocationError, ProjectRoot
from tests.test_project_evidence_routing import project


def identities(day,tmp_path):
    return Identity(tmp_path/'host/identity',today=lambda:'2026-10-29',store=day)


def test_identity_history_survives_move_and_keeps_project_ownership(project,tmp_path):
    day,locations,_,_=project;owner=day.sid
    identity=identities(day,tmp_path)
    identity.make(['p1/c1'],why='User accepted identity')
    identity.same_one('p1/c1','p2/c2',basis='user',confidence=.7)
    identity.observe('p1/c1',{'at':1,'measure':{'areaPx':42}})
    original=identity._path().read_bytes()
    root=locations.project(owner)
    assert not identity.root.exists()
    day.new('Separate Project');identity._current()
    assert identity.individuals()==[]
    identity.make(['p9/c9'])
    locations.close();moved=tmp_path/'Moved';root.path.rename(moved)
    fresh=ProjectLocations(locations.legacy,locations.registry,locations.parent)
    try:
        fresh.registry.register(ProjectRoot.open(moved),replace_inactive=True)
        reopened=SessionStore(fresh.legacy,session=owner,locations=fresh)
        restored=identities(reopened,tmp_path)
        assert restored._path().read_bytes()==original
        assert restored.nodes_of('i1')=={'p1/c1','p2/c2'}
        assert restored.observations['p1/c1'][0]['measure']['areaPx']==42
        assert restored.links[0]['confidence']==.7
        assert restored.make(['p3/c3'])=='i3'  # i2 was merged; its identity must never be reused
    finally:
        fresh.close()


def test_read_only_host_cannot_accept_identity_change(project,tmp_path):
    day,locations,_,_=project
    writer=identities(day,tmp_path);writer.make(['p1/c1'])
    other=ProjectLocations(locations.legacy,locations.registry,locations.parent)
    try:
        # Read a Project through a host handle without a visible session-open write.
        store=SimpleNamespace(sid=day.sid,days=['2026-10-29'],locations=other,listeners=[])
        reader=identities(store,tmp_path)
        before=reader._path().read_bytes()
        assert reader.individuals()==['i1']
        with pytest.raises(ProjectLocationError,match='read-only'):
            reader.observe('p1/c1',{'at':1})
        assert reader.observations=={}
        assert reader._path().read_bytes()==before
    finally:
        other.close()


@pytest.mark.parametrize('change',['closed','missing','file-link','folder-link','replaced'])
def test_failed_write_preserves_accepted_state(project,tmp_path,change):
    day,locations,_,_=project;identity=identities(day,tmp_path)
    identity.make(['p1/c1']);path=identity._path();original=path.read_bytes()
    if change=='closed': locations.close()
    elif change=='missing': path.rename(path.with_name('preserved'))
    elif change=='file-link':
        path.rename(path.with_name('preserved'));path.symlink_to(path.with_name('preserved'))
    elif change=='folder-link':
        folder=path.parent;folder.rename(folder.with_name('preserved'));folder.symlink_to(folder.with_name('preserved'))
    else:
        path.rename(path.with_name('preserved'));path.write_bytes(original)
    with pytest.raises((ProjectLocationError,OSError)):
        identity.observe('p1/c1',{'at':2})
    assert identity.observations=={}


def test_torn_tail_is_retained_but_never_accepted_on_later_append(project,tmp_path):
    day,_,_,_=project;identity=identities(day,tmp_path)
    identity.make(['p1/c1']);path=identity._path()
    tail=json.dumps({'kind':'individual','i':'i99','nodes':['p99/c99'],'at':2,'session':day.sid}).encode()
    with path.open('ab') as f: f.write(tail)
    reader=identities(day,tmp_path);assert reader.individuals()==['i1']
    reader.make(['p2/c2'])
    assert tail+b'\0\n' in path.read_bytes()
    assert identities(day,tmp_path).individuals()==['i1','i2']


def test_sync_failure_does_not_claim_an_observation_was_saved(project,tmp_path,monkeypatch):
    from orchestrator import project_events
    day,_,_,_=project;identity=identities(day,tmp_path);identity.make(['p1/c1'])
    def failed(_):raise OSError('simulated full disk')
    monkeypatch.setattr(project_events.os,'fsync',failed)
    with pytest.raises(OSError,match='full disk'): identity.observe('p1/c1',{'at':1})
    assert identity.observations=={}


@pytest.mark.parametrize('failure', ['journal-sync', 'directory-sync', 'acceptance'])
def test_uncertain_write_blocks_retry_and_fresh_replay(project,tmp_path,monkeypatch,failure):
    from orchestrator import project_events
    day,_,_,_=project;identity=identities(day,tmp_path);identity.make(['p1/c1'])
    path=identity._path()
    original_sync=project_events.os.fsync
    original_unlink=type(path).unlink
    wrote=[False]
    def sync(fd):
        # Only fail after the new link is physically present, not marker setup.
        if b'"kind": "link"' in path.read_bytes():
            wrote[0]=True
            if failure=='journal-sync': raise OSError('injected journal sync failure')
            import stat
            if failure=='directory-sync' and stat.S_ISDIR(project_events.os.fstat(fd).st_mode):
                raise OSError('injected directory sync failure')
        return original_sync(fd)
    def unlink(p,*args,**kwargs):
        if failure=='acceptance' and p.name=='pending.json':
            wrote[0]=True
            raise OSError('injected acceptance failure')
        return original_unlink(p,*args,**kwargs)
    monkeypatch.setattr(project_events.os,'fsync',sync)
    monkeypatch.setattr(type(path),'unlink',unlink)
    with pytest.raises(OSError,match='injected'):
        identity.same_one('p1/c1','p2/c2')
    assert wrote[0] and identity.links==[]
    monkeypatch.setattr(project_events.os,'fsync',original_sync)
    monkeypatch.setattr(type(path),'unlink',original_unlink)
    uncertain=path.read_bytes()
    with pytest.raises(ProjectLocationError,match='uncertain'):
        identity.same_one('p1/c1','p2/c2')
    with pytest.raises(ProjectLocationError,match='uncertain'):
        identities(day,tmp_path)
    assert path.read_bytes()==uncertain


def test_portable_history_never_blocks_the_live_event_loop(project,tmp_path):
    # The background lifecycle binding is still a qualification gate. Until it
    # exists, refusal is explicit and precedes any portable history mutation.
    import asyncio
    day,_,_,_=project
    async def check():
        identity=await asyncio.to_thread(identities,day,tmp_path)
        with pytest.raises(ProjectLocationError,match='background execution adapter'):
            identity.observe('p1/c1',{'at':1})
        assert identity.observations=={}
    asyncio.run(check())


def test_reader_checks_uncertainty_after_open_race(project,tmp_path,monkeypatch):
    from orchestrator import project_events
    day,_,_,_=project;identity=identities(day,tmp_path);identity.make(['p1/c1'])
    path=identity._path();original_open=project_events.os.open
    intercepted=[False]
    def open_file(p,flags,*args,**kwargs):
        if str(p)==str(path) and not intercepted[0]:
            intercepted[0]=True
            # Separate host creates uncertainty after reader's initial check,
            # then physically appends before reader captures its first stat.
            path.with_name('pending.json').write_text('{"version":1}')
            with path.open('ab') as f:
                f.write(json.dumps({'kind':'individual','i':'i99','nodes':[],
                    'at':1,'session':day.sid}).encode()+b'\n')
        return original_open(p,flags,*args,**kwargs)
    monkeypatch.setattr(project_events.os,'open',open_file)
    with pytest.raises(ProjectLocationError,match='uncertain'):
        identities(day,tmp_path)
    assert intercepted[0]


def test_pending_first_creation_is_not_replayed_as_empty(project,tmp_path):
    day,_,_,_=project;identity=identities(day,tmp_path)
    path=identity._project_events.path(day,day.sid,'2026-10-29',write=True)
    path.with_name('pending.json').write_text('{"version":1}')
    assert not path.exists()
    with pytest.raises(ProjectLocationError,match='uncertain'):
        identities(day,tmp_path)
