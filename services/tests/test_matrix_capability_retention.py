"""Real tiny confined CPU computation and Project retention; no biological claims."""
import io
import json
import threading
import os
import sys
from pathlib import Path
from scientific_compute import catalog as matrix_catalog

import numpy as np
import pytest

from orchestrator import matrix_capability_runs as runs
from orchestrator.scientific_file_intake import FileIntake
from orchestrator.scientific_projects import ProjectStore, InvalidProject, Conflict
from scientific_compute.catalog import descriptor
from tests.test_scientific_project_api import web  # noqa:F401


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    prepared = os.environ.get('HOOKE_MATRIX_TEST_CATALOG')
    if prepared:
        monkeypatch.setenv('MICROMIND_CATALOG', prepared)
    else:
        # Default regression: synthetic runtime admission, actual tiny CPU worker/storage.
        # The explicit prepared-catalog campaign exercises the real setup receipt instead.
        monkeypatch.setattr(matrix_catalog, 'runtime', lambda *a, **k: {
            'owner': 'matrix-cpu-runtime', 'prepared': True, 'reason': '', 'backend': 'cpu', 'evidenceClass': 'synthetic-runtime-admission'})
        monkeypatch.setattr(runs, 'execution_runtime', lambda _: (Path(sys.executable), Path(sys.prefix)))


def kept(store):
    memory = io.BytesIO()
    np.savez_compressed(memory, format=np.array('response-effects-v1'), effects=np.array([[1., -1.]]),
                        genes=np.array(['g1', 'g2']), targets=np.array(['x']), contexts=np.array(['a']))
    body = memory.getvalue()
    intake = FileIntake(store, 's1', 'synthetic-input-012345', name='effects.npz', size=len(body), max_bytes=len(body))
    try:
        intake.write(body); saved = intake.publish()
    finally:
        intake.close()
    return {'id': saved.id, 'session': 's1', 'revision': 1, 'digest': saved.digest}


def arguments(store, ref):
    return dict(store=store, session='s1', capability='response-fit', generation=descriptor('response-fit')['generation'],
                inputs={'effects': ref}, params={'regularization': .1}, request='fitted-request-012345')


def test_confined_fit_retains_exact_inputs_retry_and_changed_intent(tmp_path, monkeypatch):
    monkeypatch.setenv('MICROMIND_HEAVY_SLOT', str(tmp_path / 'slot'))
    store = ProjectStore(tmp_path / 'projects'); ref = kept(store)
    first = runs.run_matrix(**arguments(store, ref))
    record = first.document['state']['matrixOperation']
    assert record['inputs']['effects']['digest'] == ref['digest']
    assert record['output']['summary']['trainingContexts'] == ['a']
    assert record['runtime']['backend'] == 'cpu'
    monkeypatch.setattr(runs, '_compute', lambda *a: pytest.fail('A completed retry must not execute'))
    assert runs.run_matrix(**arguments(store, ref)) == first
    changed = arguments(store, ref); changed['params'] = {'regularization': 1.}
    with pytest.raises(Conflict, match='different work'):
        runs.run_matrix(**changed)
    assert len(store.list('s1')) == 2


def test_confined_effect_transfer_retains_model_reference_and_compact_receipt(tmp_path, monkeypatch):
    from tests.test_response_effect_evaluation import prepared
    monkeypatch.setenv('MICROMIND_HEAVY_SLOT', str(tmp_path/'slot'))
    store=ProjectStore(tmp_path/'projects')
    def reference(saved):
        return {'id':saved.id,'session':'s1','revision':saved.revision,'digest':saved.digest}
    def import_file(path, name):
        content=path.read_bytes()
        intake=FileIntake(store,'s1','effect-test-'+name,name=path.name,size=len(content),max_bytes=len(content))
        try:
            intake.write(content)
            return reference(intake.publish())
        finally:
            intake.close()
    train=import_file(prepared(tmp_path,'train',[[2.,-2.],[4.,0.]],context='training'),'train')
    target=import_file(prepared(tmp_path,'target',[[2.,-1.],[1.,0.]]),'target')
    fitted=runs.run_matrix(store=store,session='s1',capability='response-fit',
        generation=descriptor('response-fit')['generation'],inputs={'effects':train},
        params={'regularization':1},request='effect-test-fit-0001')
    result=runs.run_matrix(store=store,session='s1',capability='response-effect-evaluate',
        generation=descriptor('response-effect-evaluate')['generation'],
        inputs={'model':reference(fitted),'reference':target},params={},request='effect-test-evaluate-0001')
    operation=result.document['state']['matrixOperation']
    assert operation['inputs']['model']['digest']==fitted.digest
    assert operation['inputs']['reference']['digest']==target['digest']
    summary=operation['output']['summary']
    assert summary['overall']['mse']==.5 and summary['competitionScore'] is False
    assert summary['evaluatedPairs']==2 and 'rows' not in summary


def test_foreign_stale_path_and_changed_generation_refuse_before_compute(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / 'projects'); ref = kept(store)
    monkeypatch.setattr(runs, '_compute', lambda *a: pytest.fail('Must refuse before computation'))
    cases = [{'session': 's2'}, {'digest': 'a' * 64}, {'path': '/private/secret'}, {'revision': 2}]
    for changes in cases:
        with pytest.raises(InvalidProject):
            runs.run_matrix(**arguments(store, {**ref, **changes}))
    args = arguments(store, ref); args['generation'] = 'changed'
    with pytest.raises(InvalidProject, match='generation'):
        runs.run_matrix(**args)
    assert len(store.list('s1')) == 1


def test_occupied_machine_slot_admits_no_worker(tmp_path, monkeypatch):
    slot = tmp_path / 'slot'; slot.mkdir(); owner = slot / 'owner'; owner.write_text('Beta verification')
    monkeypatch.setenv('MICROMIND_HEAVY_SLOT', str(slot))
    store = ProjectStore(tmp_path / 'projects'); ref = kept(store)
    monkeypatch.setattr(runs, '_compute', lambda *a: pytest.fail('Occupied slot cannot start a worker'))
    with pytest.raises(RuntimeError, match='occupied'):
        runs.run_matrix(**arguments(store, ref))
    assert owner.read_text() == 'Beta verification' and len(store.list('s1')) == 1


def test_stop_during_materialization_publishes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv('MICROMIND_HEAVY_SLOT', str(tmp_path / 'slot'))
    store = ProjectStore(tmp_path / 'projects'); ref = kept(store)
    calls = 0
    def stop():
        nonlocal calls
        calls += 1
        if calls >= 4:
            raise RuntimeError('stopped')
    with pytest.raises(RuntimeError, match='stopped'):
        runs.run_matrix(**arguments(store, ref), check=stop)
    assert len(store.list('s1')) == 1 and not (tmp_path / 'slot').exists()


def test_retained_matrix_result_downloads_exact_bytes_and_refuses_other_projects(web):
    # Synthetic output bytes through the real output owner, retention and HTTP route.
    client,built=web;store=built.hub.resources.projects;session=built.day.sid
    body=b"retained model bytes"
    owner=runs._Output(store,session,'download-result-0123456789',name='model.npz',size=len(body),
        max_bytes=len(body),invocation={'format':1},summary={'fixture':True})
    try:
        owner.write(body);saved=owner.publish()
    finally:owner.close()
    response=client.get('/api/scientific-files/'+saved.id,params={'session':session,'revision':1})
    assert response.status_code==200,response.text
    assert response.content==body and 'model.npz' in response.headers['content-disposition']
    assert client.get('/api/scientific-files/'+saved.id,params={'session':'2026-10-16.999','revision':1}).status_code!=200
    doc=saved.document;doc['title']='Reviewed model'
    store.save(saved.id,session,doc,expected_revision=1)
    assert client.get('/api/scientific-files/'+saved.id,params={'session':session,'revision':1}).content==body


def test_resources_are_rechecked_after_shared_slot_wait(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    store = ProjectStore(tmp_path / 'projects'); ref = kept(store)
    free = [2 << 30]
    @contextmanager
    def acquired(*args, **kwargs):
        free[0] = 0
        yield
    monkeypatch.setattr(runs, 'heavy_work', acquired)
    monkeypatch.setattr(runs.shutil, 'disk_usage', lambda _: SimpleNamespace(free=free[0]))
    monkeypatch.setattr(runs, '_compute', lambda *a: pytest.fail('Disk changed while waiting'))
    with pytest.raises(InvalidProject, match='after waiting'):
        runs.run_matrix(**arguments(store, ref), wait_seconds=3600)
    assert len(store.list('s1')) == 1
