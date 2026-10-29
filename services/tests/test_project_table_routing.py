"""Real journals and numeric snapshots in isolated Projects; synthetic source measurements."""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator.analysis_math import analyze
from orchestrator.datatables import DataTables
from orchestrator.day.store import SessionStore
from orchestrator.project_locations import ProjectLocations
from orchestrator.project_roots import ProjectLocationError, ProjectRegistry, ProjectRoot
from orchestrator.table_derivatives import TableDerivatives
from orchestrator.table_reading import read_accepted


@pytest.fixture
def project(tmp_path, monkeypatch):
    legacy = tmp_path / 'host/sessions'
    SessionStore(legacy, session='2026-10-29')
    locations = ProjectLocations(legacy, ProjectRegistry(tmp_path / 'host'), tmp_path / 'Projects')
    day = SessionStore(legacy, session='2026-10-29', locations=locations)
    app = SimpleNamespace(day=day, hub=SimpleNamespace())
    tables = DataTables(app, tmp_path / 'host/tables')
    derivatives = TableDerivatives(tables, tmp_path / 'host/derived', {'analysis': analyze})
    app.hub.datatables, app.hub.table_derivatives = tables, derivatives
    async def measured(app, eid, session, revision):
        return {'id':eid,'kind':'result-table','title':eid,'revision':0,
                'columns':[{'key':'value','type':'number','unit':'µm'},{'key':'condition','type':'text'}],
                'rows':[{'value':v,'condition':eid} for v in ([1.,2.,3.] if eid=='d1' else [4.,5.,6.])]}
    monkeypatch.setattr('orchestrator.tables.read_table', measured)
    try:
        yield app, locations, tables, derivatives
    finally:
        locations.close()


def test_all_table_and_analysis_revisions_move_with_original_project(project,tmp_path):
    app,locations,tables,derivatives=project
    sid=app.day.new('Measured research')
    async def run():
        first=await tables.create(['d1','d2'],mode='frozen')
        old=tables.read(first['id'],1)
        second=await tables.edit(first['id'],1,[{'op':'title','title':'Retained measurements'}])
        result=await derivatives.create('analysis',first['id'],2,
            {'method':'welch','value':'value','group':'condition','groups':['d1','d2']},mode='frozen')
        return first,second,old,result,derivatives.read(result['id'])
    first,second,old,result,analysis=asyncio.run(run())
    root=locations.project(sid)
    assert tables.path_of(first,sid).is_relative_to(root.path)
    assert derivatives.path_of(result,sid).is_relative_to(root.path)
    assert not tables.root.exists() and not derivatives.root.exists()
    app.day.new('Unrelated research')
    assert read_accepted(tables.root_for(sid),first,sid,'dataset-table')['snapshot']==old
    assert read_accepted(derivatives.root_for(sid),result,sid,'analysis')['snapshot']==analysis
    locations.close()
    moved=tmp_path/'Moved research';root.path.rename(moved)
    fresh=ProjectLocations(locations.legacy,locations.registry,locations.parent)
    try:
        fresh.registry.register(ProjectRoot.open(moved),replace_inactive=True)
        app.day=SessionStore(fresh.legacy,session=sid,locations=fresh)
        reopened=DataTables(app,tables.root)
        reader=TableDerivatives(reopened,derivatives.root,{})  # no installed evaluator needed
        assert reopened.read(first['id'],1)==old
        assert reopened.version(first['id'],2)['snapshot']['sha256']==second['snapshot']['sha256']
        assert reader.read(result['id'])==analysis
    finally:
        fresh.close()


def test_second_host_reads_accepted_numeric_data_but_cannot_write(project):
    app,locations,tables,derivatives=project
    sid=app.day.new('Writer owns research')
    first=asyncio.run(tables.create(['d1'],mode='frozen'))
    other=ProjectLocations(locations.legacy,locations.registry,locations.parent)
    try:
        day=SessionStore(other.legacy,session='2026-10-29',locations=other)
        reader=DataTables(SimpleNamespace(day=day),tables.root)
        assert read_accepted(reader.root_for(sid),first,sid,'dataset-table')['snapshot']==tables.read(first['id'])
        with pytest.raises(ProjectLocationError,match='read-only'):
            reader._write({**first,'version':2},tables.read(first['id']),sid)
    finally:
        other.close()


@pytest.mark.parametrize('change',['closed','missing','replaced'])
def test_unavailable_original_destination_refuses_late_publication(project,change):
    app,locations,tables,_=project
    sid=app.day.new('Research')
    first=asyncio.run(tables.create(['d1'],mode='frozen'))
    snapshot=tables.read(first['id'])
    folder=tables.root_for(sid)
    if change=='closed':locations.close()
    else:
        folder.rename(folder.with_name('preserved-tables'))
        if change=='replaced':folder.mkdir()
    with pytest.raises(ProjectLocationError):
        tables._write({**first,'version':2},snapshot,sid)
    assert not tables.root.exists()


def test_symlinked_numeric_destination_never_writes_elsewhere(project,tmp_path):
    app,locations,tables,_=project
    sid=app.day.new('Research')
    folder=tables.root_for(sid,write=True)
    outside=tmp_path/'outside';outside.mkdir()
    (folder/sid).symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='replaced'):
        asyncio.run(tables.create(['d1'],mode='frozen'))
    assert not list(outside.iterdir()) and not app.day.datatables
