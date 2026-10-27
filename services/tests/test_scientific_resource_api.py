"""Production HTTP admission; scientific calculations are tested on real storage separately."""
import pytest
from tests.test_scientific_project_api import web,ORIGIN,create  # noqa: F401
from orchestrator import scientific_resource_actions as actions
from orchestrator import scientific_measurement as measurement


def test_measurement_refuses_foreign_origin_and_changed_project(web,monkeypatch):
    client,built=web
    monkeypatch.setattr(actions,'measure',lambda *a,**k:pytest.fail('unadmitted measurement'))
    url='/api/scientific-projects/p'+'a'*32+'/measure'
    assert client.post(url,json={}).status_code==403
    assert client.post(url,headers={'Origin':ORIGIN,'X-MicroscopeMind':'measure-scientific-objects'},
        json={'operation':measurement.OPERATION,'session':'2020-01-01','revision':1,'input':'labels'}).status_code==409


def test_measurement_captures_exact_revision_and_input(web,monkeypatch):
    client,built=web;saved=create(client,built).json()['project'];calls=[]
    async def measure(hub,day,operation,selection,**kwargs):
        assert operation==measurement.OPERATION and kwargs['caller']=='studio'
        calls.append((selection['project'],selection['session'],selection['revision'],selection['input']))
        return {'project':hub.resources.projects.read(selection['project'],selection['session'],revision=selection['revision'])}
    monkeypatch.setattr(measurement,'run',measure)
    response=client.post('/api/scientific-projects/'+saved['id']+'/measure',
        headers={'Origin':ORIGIN,'X-MicroscopeMind':'measure-scientific-objects'},
        json={'operation':measurement.OPERATION,'session':built.day.sid,'revision':1,'input':'labels'})
    assert response.status_code==200 and calls==[(saved['id'],built.day.sid,1,'labels')]


@pytest.mark.parametrize('cancelled',[False,True])
def test_export_cleans_up_when_response_start_fails(cancelled):
    import asyncio
    import io
    from starlette.requests import ClientDisconnect
    from orchestrator.scientific_resource_api import NativeExportResponse
    stream=io.BytesIO(b'native scientific data')
    class Reservation:
        released=0
        def release(self):self.released+=1
    room=Reservation()
    response=NativeExportResponse(stream,room,2)
    async def receive():
        await asyncio.Event().wait()
    async def send(message):
        assert message['type']=='http.response.start'
        if cancelled:raise asyncio.CancelledError()
        raise OSError('connection closed before body')
    async def run():
        with pytest.raises(asyncio.CancelledError if cancelled else ClientDisconnect):
            await response({'type':'http','asgi':{'spec_version':'2.4'}},receive,send)
    asyncio.run(run())
    assert stream.closed and room.released==1


def test_native_export_names_the_input_so_two_arrays_never_share_a_file_name():
    import io
    from orchestrator.scientific_resource_api import NativeExportResponse
    class Reservation:
        def release(self):pass
    names={NativeExportResponse(io.BytesIO(),Reservation(),3,key).headers['content-disposition'] for key in ('layer4_output','layer5_output')}
    assert names=={'attachment; filename="scientific-array-revision-3-layer4_output.zip"',
                   'attachment; filename="scientific-array-revision-3-layer5_output.zip"'}
    hostile=NativeExportResponse(io.BytesIO(),Reservation(),3,'../a "b"\n').headers['content-disposition']
    assert hostile=='attachment; filename="scientific-array-revision-3-a-b.zip"'


def test_csv_names_its_labels_and_refuses_measurements_of_earlier_labels(web,monkeypatch):
    client,built=web;saved=create(client,built).json()['project']
    url='/api/scientific-projects/'+saved['id']+'/measurements.csv?session='+built.day.sid+'&revision=3'
    table={'format':actions.FORMAT,'source':{'input':'layer5_output'},'columns':['object_id','voxel_count','centroid_y_px','centroid_x_px'],'rows':[['6',84,122.,105.5]]}
    monkeypatch.setattr(actions,'table',lambda *a:table)
    response=client.get(url)
    assert response.status_code==200 and response.text=='object_id,voxel_count,centroid_y_px,centroid_x_px\r\n6,84,122.0,105.5\r\n'
    assert response.headers['content-disposition']=='attachment; filename="objects-revision-3-layer5_output.csv"'
    def stale(*a):raise actions.InvalidProject('These measurements belong to earlier labels (saved revision 2).')
    monkeypatch.setattr(actions,'table',stale)
    refused=client.get(url)
    assert refused.status_code==400 and 'earlier labels' in refused.json()['says'] and not refused.headers['content-type'].startswith('text/csv')


def test_tables_list_and_page_typed_columns_of_an_exact_revision(web):
    import numpy as np
    from orchestrator import scientific_tables as tables
    client,built=web;made=create(client,built).json()['project']
    store=built.hub.resources.projects;saved=store.read(made['id'],made['session'],revision=1)
    declaration=tables.declare(semantic='object-table',key='object_id',columns=[
        {'name':'object_id','dtype':'<u8','role':'key'},
        {'name':'kind','dtype':'<i2','role':'feature','vocabulary':[{'id':1,'name':'round'},{'id':2,'name':'long'}]}])
    published=tables.publish(store,saved,{'objects':(declaration,{'object_id':np.array([3,2**60],'<u8'),'kind':np.array([2,1],'<i2')})})
    base='/api/scientific-projects/'+saved.id+'/tables'
    query=f'?session={saved.session}&revision={published.revision}'
    listed=client.get(base+query).json()
    assert listed['ok'] and [t['name'] for t in listed['tables']]==['objects']
    assert 'component' not in listed['tables'][0]['columns'][0]
    page=client.get(base+'/objects'+query+'&offset=1&limit=1').json()
    assert page['rows']==[[str(2**60),1]] and page['table']['columns'][1]['vocabulary'][0]['name']=='round'
    assert client.get(base+'/objects'+f'?session={saved.session}&revision=1').status_code==400
    assert client.get(base+'/objects'+query+'&limit=5000').status_code==400
