"""Actual confined Python on synthetic retained data; no model/cloud/scientific quality claim."""
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from orchestrator import sandbox
from orchestrator.research.efforts import Efforts
from orchestrator.research.python import PythonResearch
from orchestrator.scientific_file_intake import FileIntake
from orchestrator.scientific_projects import ProjectStore, InvalidProject
from tests.waiting import until


def study(tmp_path):
    store = ProjectStore(tmp_path / 'projects')
    body = b'x,y\n1,3\n2,5\n3,7\n'
    intake = FileIntake(store, '2026-10-23', 'retained-input-request', name='example.csv', size=len(body), max_bytes=1024)
    try:
        intake.write(body)
        kept = intake.publish()
    finally:
        intake.close()
    efforts = Efforts(store)
    saved = efforts.create(kept.session, request='investigate', title='Small regression',
        objective='Fit and compare', criteria='Squared error', budget_usd=1,
        inputs=[{'id': kept.id, 'revision': kept.revision, 'digest': kept.digest}])
    return efforts, saved, kept


def test_actual_headless_python_reads_retained_input_and_reopens_output_without_execution(tmp_path):
    if not sandbox.available():
        pytest.skip('actual macOS confinement is required')
    efforts, saved, source = study(tmp_path)
    code = '''import csv, json
from pathlib import Path
rows = list(csv.DictReader(next(iter(files.values())).open()))
error = sum((2 * float(r['x']) + 1 - float(r['y'])) ** 2 for r in rows)
Path('metrics.json').write_text(json.dumps({'squared_error': error, 'rows': len(rows)}))
print('Measured', len(rows), 'rows')
'''
    action = PythonResearch(efforts, work_root=tmp_path)
    result = action.execute(saved.id, saved.session, request='fit-1', code=code, outputs=['metrics.json'])
    task = result['tasks']['fit-1']
    assert task['status'] == 'completed', task['result']
    assert task['result']['console']['stdout'] == 'Measured 3 rows\n'
    output = task['result']['outputs'][0]
    retained = efforts.store.read(output['id'], saved.session, revision=output['revision'])
    native = efforts.store.file_source(retained.id, retained.session, retained.revision, retained.document['resources'][0])
    assert json.loads(native.read(0, native.size)) == {'squared_error': 0, 'rows': 3}
    assert output['sha256'] == hashlib.sha256(native.read(0, native.size)).hexdigest()
    assert action.execute(saved.id, saved.session, request='fit-1', code=code, outputs=['metrics.json']) == result
    assert efforts.store.read(source.id, saved.session).digest == source.digest


def test_actual_python_cannot_read_ungranted_file_or_modify_input(tmp_path):
    if not sandbox.available():
        pytest.skip('actual macOS confinement is required')
    efforts, saved, _ = study(tmp_path)
    private = tmp_path / 'private.txt'
    private.write_text('private value must stay outside the worker')
    code = f'''from pathlib import Path
for path in [Path({str(private)!r}), next(iter(files.values()))]:
    try:
        path.write_text('modified')
        print('UNEXPECTED WRITE')
    except PermissionError:
        print('write denied')
try:
    Path({str(private)!r}).read_text()
    print('UNEXPECTED READ')
except PermissionError:
    print('read denied')
'''
    result = PythonResearch(efforts, work_root=tmp_path).execute(saved.id, saved.session, request='isolation', code=code)
    task = result['tasks']['isolation']
    assert task['status'] == 'completed', task['result']
    assert task['result']['console']['stdout'] == 'write denied\nwrite denied\nread denied\n'
    assert private.read_text() == 'private value must stay outside the worker'


def test_cancelled_local_work_keeps_evidence_and_never_runs_code(tmp_path):
    if not sandbox.available():
        pytest.skip('actual macOS confinement is required')
    efforts, saved, _ = study(tmp_path)
    stopping = threading.Event(); stopping.set()
    result = PythonResearch(efforts, work_root=tmp_path).execute(saved.id, saved.session, request='cancelled',
        code="raise RuntimeError('must not run')", stopping=stopping)
    assert result['tasks']['cancelled']['status'] == 'cancelled'
    assert result['tasks']['cancelled']['result']['console'] is None


def test_python_refuses_without_os_confinement_before_reservation(tmp_path, monkeypatch):
    efforts, saved, _ = study(tmp_path)
    monkeypatch.setattr(sandbox, 'available', lambda: False)
    with pytest.raises(InvalidProject, match='confinement'):
        PythonResearch(efforts).execute(saved.id, saved.session, request='no-boundary', code='1+1')
    assert efforts.read(saved.id, saved.session) == saved


def test_malformed_output_selection_refuses_without_reserving_work(tmp_path):
    efforts, saved, _ = study(tmp_path)
    with pytest.raises(InvalidProject):
        PythonResearch(efforts).execute(saved.id, saved.session, request='bad-output', code='1+1', outputs=[[]])
    assert efforts.read(saved.id, saved.session) == saved


def test_durable_stop_interrupts_actual_running_python_and_preserves_cancelled_receipt(tmp_path):
    if not sandbox.available():
        pytest.skip('actual macOS confinement is required')
    efforts, saved, _ = study(tmp_path)
    code = "from pathlib import Path\nimport time\nPath('started').write_text('ready')\nwhile True: time.sleep(0.1)"
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(PythonResearch(efforts, work_root=tmp_path).execute,
            saved.id, saved.session, request='long-run', code=code, seconds=300)
        until(lambda: bool(list(tmp_path.glob('hooke-research-*/work/started'))))
        # A different service instance can stop the retained owner after reconnecting.
        Efforts(ProjectStore(tmp_path / 'projects')).control(saved.id, saved.session, 'stop')
        result = running.result(timeout=60)
    assert result['tasks']['long-run']['status'] == 'cancelled'
    assert result['tasks']['long-run']['result']['outputs'] == []
    assert not list(tmp_path.glob('hooke-research-*'))
