"""Real HTTP/journal/snapshot chain with synthetic measured rows, no model or UI claim."""
import asyncio
import io
import zipfile

from fastapi.testclient import TestClient

from orchestrator.paths import Paths
from orchestrator.server import create_app


def test_table_analysis_graph_workbook_share_exact_saved_values(tmp_path, monkeypatch):
    async def measurements(app, eid, session, revision):
        return {'id': eid, 'kind': 'result-table', 'title': eid, 'revision': 0,
            'columns': [{'key': 'value', 'type': 'number', 'label': 'Area', 'unit': 'µm²'},
                        {'key': 'condition', 'type': 'text', 'label': 'Condition'}],
            'rows': [{'value': value, 'condition': eid} for value in ([1., 2., 3.] if eid == 'd1' else [4., 5., 6.])]}
    monkeypatch.setattr('orchestrator.tables.read_table', measurements)
    original = __import__('orchestrator.analysis_math', fromlist=['analyze']).analyze
    calls = []
    def evaluate(*args, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise AssertionError('statistics ran on the live event loop')
        calls.append('computed')
        return original(*args, **kwargs)
    monkeypatch.setattr('orchestrator.analysis_math.analyze', evaluate)
    app = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))
    app.state.app.day.open('2026-10-22.3')
    assert app.state.app.day.date != app.state.app.day.sid
    client = TestClient(app)
    header = {'X-MicroscopeMind': 'datatable'}
    session = client.get('/api/datatables').json()['session']
    table = client.post('/api/datatables', headers=header, json={'session': session, 'sources': ['d1', 'd2']}).json()
    body = {'session': session, 'kind': 'analysis', 'table': {'id': table['id'], 'version': 1},
            'spec': {'method': 'welch', 'value': 'value', 'group': 'condition'}, 'mode': 'frozen'}
    assert client.post('/api/table-derivatives', json=body).status_code == 403
    assert client.post('/api/table-derivatives', headers=header, json={**body, 'session': 'elsewhere'}).status_code == 409
    made = client.post('/api/table-derivatives', headers=header, json=body)
    assert made.status_code == 200, made.text
    analysis = made.json()
    assert analysis['result']['effect']['value'] == -3 and calls == ['computed']
    spec = {'kind': 'bar', 'value': 'value', 'group': 'condition', 'error': 'sem',
            'analysis': {'id': analysis['id'], 'version': 1}}
    response = client.post('/api/table-derivatives', headers=header, json={**body, 'kind': 'graph', 'spec': spec})
    assert response.status_code == 200, response.text
    graph = response.json()
    assert graph['source'] == analysis['source']
    assert graph['result']['groups'][0]['summary']['mean'] == 2
    assert graph['result']['annotations'][0]['p'] == analysis['result']['p']
    for saved in (analysis, graph):
        read = client.get('/api/table-derivatives/' + saved['id'], params={'session': session, 'version': 1})
        assert read.json()['result'] == saved['result']
    exported = client.get(f"/api/datatables/{table['id']}.xlsx", params={'session': session, 'version': 1})
    assert exported.status_code == 200, exported.text[:500] if exported.status_code != 200 else ''
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        data = archive.read('xl/worksheets/sheet1.xml').decode()
        result = archive.read('xl/worksheets/sheet2.xml').decode()
        assert 'µm' not in data  # units remain declared separately, not mixed into numeric cells
        assert '<v>6.0</v>' in data and 'welch' in result and '<v>-3.0</v>' in result
    assert client.get(f"/api/datatables/{table['id']}.xlsx", params={'session': '2026-10-22', 'version': 1}).status_code == 409
    assert calls == ['computed']  # GET and export never recompute
    assert client.get(f"/api/datatables/{table['id']}.xlsx", params={'session': session}).status_code == 400
    assert client.get('/api/table-derivatives/' + graph['id'], params={'session': 'elsewhere', 'version': 1}).status_code == 409
    changed = client.patch('/api/table-derivatives/' + graph['id'], headers=header,
                          json={'session': session, 'version': 1, 'mode': 'follow'})
    assert changed.status_code == 200 and changed.json()['version'] == 2
    stale = client.patch('/api/table-derivatives/' + graph['id'], headers=header,
                        json={'session': session, 'version': 1, 'mode': 'frozen'})
    assert stale.status_code == 409
