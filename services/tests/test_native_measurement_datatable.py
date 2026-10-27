"""Actual native storage → existing editable tables/analysis/graph, synthetic labels only."""
import asyncio
from types import SimpleNamespace
import numpy as np
import pytest
from orchestrator.scientific_projects import ProjectStore
from orchestrator.scientific_families import write_array
from orchestrator import scientific_resource_actions as actions
from orchestrator.day.store import DayStore
from orchestrator.datatables import DataTables, DataTableRefused
from orchestrator.table_derivatives import TableDerivatives
from orchestrator.analysis_math import analyze
from orchestrator.graph_math import graph


def test_exact_native_measurements_continue_to_edit_analysis_graph_and_reopen(tmp_path,monkeypatch):
    day=DayStore(root=tmp_path/'sessions')
    store=ProjectStore(tmp_path/'projects')
    source=store.create(day.sid,{'kind':'scene','title':'Corrected synthetic cells','resources':[], 'runtimes':[], 'state':{}})
    values=np.array([[0,2**63+7,2**63+7],[7,19,19]],dtype='<u8')
    reference=write_array(store,source,'array.labels',values,['y','x'],{'semantic':'instance-labels'})
    doc=source.document;doc['resources']=[reference];doc['state']['scientificView']={'inputs':{'labels':reference}}
    source=store.save(source.id,source.session,doc,expected_revision=1)
    measured=actions.measure(store,source.id,source.session,source.revision,'labels')
    monkeypatch.setattr(actions,'measure',lambda *a,**k: pytest.fail('Continuation must not measure or run segmentation again'))
    app=SimpleNamespace(day=day,hub=SimpleNamespace(resources=SimpleNamespace(projects=store)))
    tables=DataTables(app,tmp_path/'tables');app.hub.datatables=tables
    derived=TableDerivatives(tables,tmp_path/'derived',{'analysis':analyze,'graph':graph})
    async def run():
        with pytest.raises(DataTableRefused,match='exact saved revision'):
            await tables.create([{'id':measured.id,'revision':measured.revision}])
        made=await tables.create([{'id':measured.id,'revision':measured.revision}],mode='frozen')
        source_record=made['sources'][0]
        assert source_record['revision']==measured.revision
        assert source_record['nativeSource']['digest']==measured.digest
        assert source_record['nativeSource']['labels']['revision']==source.revision
        schema=source_record['nativeSource']['schema']
        assert schema['key']=='object_id' and schema['object_space']==reference
        assert next(c for c in schema['columns'] if c['name']=='voxel_count')['unit']=='pixel'
        assert all(c['description'] for c in schema['columns'])
        snapshot=tables.read(made['id'],1)
        assert [row['member'] for row in snapshot['rows']]==['7','19',str(2**63+7)]
        assert [row['voxel_count'] for row in snapshot['rows']]==[1,2,2]
        edited=await tables.edit(made['id'],1,[{'op':'add-column','label':'Condition','type':'category'}])
        assert edited['sources']==made['sources']
        with pytest.raises(DataTableRefused,match='selected revision'):
            await tables.edit(made['id'],2,[{'op':'mode','mode':'follow'}])
        analysis=await derived.create('analysis',made['id'],2,{'method':'pearson','x':'centroid_y_px','y':'centroid_x_px'},mode='frozen')
        plotted=await derived.create('graph',made['id'],2,{'kind':'histogram','value':'voxel_count','bins':2},mode='frozen')
        assert derived.read(analysis['id'])['source']['version']==2
        assert derived.read(plotted['id'])['result']['groups'][0]['n']==3
        app.day=DayStore(root=day.root,day=day.sid,clock=day.clock)
        reopened=DataTables(app,tables.root)
        restored=TableDerivatives(reopened,derived.root,{})
        assert reopened.read(made['id'],1)==snapshot
        assert restored.read(plotted['id'])['source']==derived.read(plotted['id'])['source']
        assert store.read(source.id,source.session,revision=source.revision).digest==source.digest
        app.day=DayStore(root=tmp_path/'other')
        app.day.sid='different-project'
        with pytest.raises(DataTableRefused):
            await reopened.create([{'id':measured.id,'revision':measured.revision}],mode='frozen')
    asyncio.run(run())
