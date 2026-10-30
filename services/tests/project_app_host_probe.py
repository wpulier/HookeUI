"""Isolated application process for portability integration tests; synthetic inputs only."""
import asyncio
import json
from pathlib import Path
import sys
import threading
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.waiting import HANG_S
from orchestrator.app import App
from orchestrator.door import api
from orchestrator.origin import studio_origin
from orchestrator.paths import Paths
from orchestrator.project_locations import ProjectLocations
from orchestrator.project_repository import ProjectRepository
from orchestrator.project_roots import ProjectRegistry
from tests.test_agent_door import BASE, HEADER, connect, bearer, WINDOW_PUBLIC
from tests.test_agent_projects import run


def main(mode, folder):
    import os
    os.environ['HOOKE_CONFIRM_KEY'] = WINDOW_PUBLIC
    os.environ.pop('HOOKE_DEV_CONFIRM', None)
    base = Path(folder)
    paths = Paths.under(base / 'host')
    locations = ProjectLocations(paths.sessions, ProjectRegistry(paths.home), base / 'Projects')
    built = App.build('http://127.0.0.1:1', paths, use_gpu=False, project_locations=locations)
    http = FastAPI()
    api.register(http, built, studio_origin, HEADER)
    try:
        with TestClient(http, base_url=BASE) as client:
            if mode == 'create':
                h = bearer(connect(client)['token'])
                original = built.day.sid
                made = run(client, h, 'project.create', {'name':'Portable research'}, 'portable-create')
                sid = made['outcome']['project']['session']
                assert built.day.sid == original
                root = locations.project(sid)
                source = base / 'protocol.txt'
                source.write_text('Compare exact retained evidence. Synthetic integration data.')
                imported = run(client, h, 'project.import', {'file':str(source)}, 'portable-import', sid)
                ref = imported['outcome']['artifact']
                response = client.post('/api/agent/v1/studies', headers=h, json={
                    'session':sid, 'requestKey':'portable-study', 'title':'Retained evidence',
                    'objective':'Compare saved inputs', 'criteria':'Exact historical revisions',
                    'budgetUsd':0, 'inputs':[{k:ref[k] for k in ('id','revision','digest')}]})
                assert response.status_code == 200, response.text
                study = response.json()['study']['id']
                saved = built.hub.resources.projects.read(ref['id'], sid, revision=ref['revision'])
                source.unlink()
                built.day.switch_session(sid)
                response = client.get(f'/api/agent/v1/studies/{study}?session={sid}', headers=h)
                assert response.status_code == 200, response.text
                receipt = {'headers':h, 'session':sid, 'root':str(root.path), 'reference':ref,
                    'document':saved.document, 'study':study, 'evidence':response.json(),
                    'events':ProjectRepository(root).events()}
                (base / 'fixture-receipt.json').write_text(json.dumps(receipt))
                assert not list((paths.results / 'projects').glob('p*'))
            else:
                receipt = json.loads((base / 'fixture-receipt.json').read_text())
                sid, ref = receipt['session'], receipt['reference']
                assert built.day.sid == sid
                response = client.get(f"/api/agent/v1/studies/{receipt['study']}?session={sid}", headers=receipt['headers'])
                assert response.status_code == 200, response.text
                assert response.json() == receipt['evidence']
                saved = built.hub.resources.projects.read(ref['id'], sid, revision=ref['revision'])
                assert saved.document == receipt['document'] and saved.digest == ref['digest']
                data = built.hub.resources.projects.file_source(saved.id, sid, saved.revision,
                    saved.document['state']['sourceFile']['reference'])
                assert data.read(0,data.size) == b'Compare exact retained evidence. Synthetic integration data.'
                assert ProjectRepository(locations.project(sid)).events() == receipt['events']
        # Hold a real daemon evidence save after it acquired the Project folder,
        # exactly where cancellation of an unrelated asyncio task cannot join it.
        from orchestrator import project_evidence
        from tests.test_evidence import population, view, saved as wait_saved
        blocked, released = threading.Event(), threading.Event()
        original_destination = project_evidence.destination
        def delayed_destination(*args):
            destination = original_destination(*args)
            blocked.set()
            assert released.wait(HANG_S)
            return destination
        evidence = built.hub.evidence
        item = population(evidence, view())
        item.session = sid
        evidence.add(item)
        with patch.object(project_evidence, 'destination', delayed_destination):
            evidence.schedule_save(item)
            assert blocked.wait(HANG_S)
            if mode == 'create':
                receipt['events'] = ProjectRepository(root).events()
                (base / 'fixture-receipt.json').write_text(json.dumps(receipt))
            asyncio.run(built.stop())
            assert locations.closed
            locations.close()  # must not undo retirement while a writer thread still exists
            print('stopped-with-lease', flush=True)
            assert sys.stdin.readline().strip() == 'exit'
            released.set()
            # The stopped host refuses later acceptance; original saved research
            # remains readable on restart. Process teardown releases its lease.
            wait_saved(item)
            assert item.status != 'saved'
    except BaseException:
        locations.close()
        raise


if __name__ == '__main__':
    main(*sys.argv[1:])
