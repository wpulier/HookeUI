"""Ordinary server/lifespan, isolated profile and synthetic consent; no provider calls."""
import json
import os
from pathlib import Path
import sys

from fastapi.testclient import TestClient
from orchestrator.paths import Paths
from orchestrator.project_locations import ProjectLocations
from orchestrator.project_roots import ProjectRegistry
from orchestrator.server import create_app
from tests.test_agent_door import BASE, HEADER, WINDOW_PUBLIC, connect, bearer
from tests.test_agent_project_grants import OWNER, sign
from tests.test_agent_projects import run
from tests.waiting import until


def main(mode, folder):
    base = Path(folder)
    os.environ['HOOKE_CONFIRM_KEY'] = WINDOW_PUBLIC
    os.environ['HOOKE_STUDIO_TOKEN'] = OWNER['X-Hooke-Studio']
    os.environ.pop('HOOKE_DEV_CONFIRM', None)
    paths = Paths.under(base / 'host')
    locations = ProjectLocations(paths.sessions, ProjectRegistry(paths.home), base / 'Projects')
    http = create_app('http://127.0.0.1:1', use_gpu=False, paths=paths, project_locations=locations)
    built = http.state.app
    with TestClient(http, base_url=BASE) as client:
        if mode == 'create':
            response = client.post('/api/session/new', headers={'Origin': BASE, HEADER: 'new-session'},
                                   json={'name': 'Research from Studio'})
            assert response.status_code == 200, response.text
            sid = response.json()['session']
            connected = connect(client, label='Portable researcher')
            h = bearer(connected['token'])
            value = {'session': sid, 'connection': connected['connection']['id'], 'role': 'contribute'}
            response = client.post('/api/agent-project-access/prepare', headers=OWNER, json=value)
            assert response.status_code == 200, response.text
            response = client.post('/api/agent-project-access/confirm', json=sign(response.json()['choice']))
            assert response.status_code == 200, response.text
            source = base / 'protocol.txt'
            source.write_text('Synthetic protocol: retain evidence and compare exact revisions.')
            imported = run(client, h, 'project.import', {'file': str(source)}, 'server-import', sid)
            ref = imported['outcome']['artifact']
            source.unlink()
            response = client.post('/api/agent/v1/studies', headers=h, json={
                'session': sid, 'requestKey': 'server-study', 'title': 'Retained question',
                'objective': 'Compare exact retained inputs', 'criteria': 'Preserve original evidence',
                'budgetUsd': 0, 'inputs': [{k: ref[k] for k in ('id', 'revision', 'digest')}]})
            assert response.status_code == 200, response.text
            study = response.json()['study']['id']
            receipt = {'headers': h, 'session': sid, 'reference': ref, 'study': study,
                       'root': str(locations.project(sid).path)}
        else:
            receipt = json.loads((base / 'server-receipt.json').read_text())
            h, sid, ref, study = (receipt[k] for k in ('headers', 'session', 'reference', 'study'))
            assert built.day.sid == sid

        until(lambda: built.hub.identity.io.ready)
        response = client.get('/api/resources', params={'day': sid})
        assert response.status_code == 200, response.text
        inventory = response.json()
        assert not inventory['unreadable'], inventory['unreadable']
        entries = {entry['id']: entry for entry in inventory['entries']}
        assert ref['id'] in entries and study in entries
        assert entries[study]['material']['kind'] == 'assignment'
        response = client.get(f'/api/agent/v1/studies/{study}', params={'session': sid}, headers=h)
        assert response.status_code == 200, response.text
        saved = built.hub.resources.projects.read(ref['id'], sid, revision=ref['revision'])
        data = built.hub.resources.projects.file_source(saved.id, sid, saved.revision,
                                                       saved.document['state']['sourceFile']['reference'])
        assert data.read(0, data.size) == b'Synthetic protocol: retain evidence and compare exact revisions.'
        evidence = {'study': response.json(), 'document': saved.document, 'digest': saved.digest,
                    'entries': {key: entries[key] for key in (ref['id'], study)}}
        if mode == 'create':
            receipt['evidence'] = evidence
            (base / 'server-receipt.json').write_text(json.dumps(receipt))
        else:
            assert evidence == receipt['evidence']
    assert locations.closed


if __name__ == '__main__':
    main(*sys.argv[1:])
