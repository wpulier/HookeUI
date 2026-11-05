"""Folder matrix capabilities (ADR 0031): discovered from catalog folders, versioned by their own folder,
run in the same confined worker boundary, and a comparison's rule must be a Study's frozen rule."""
import io
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

from gpu_worker import manifests
from orchestrator import matrix_capability_runs as runs
from orchestrator.research import frozen_rules
from orchestrator.research.efforts import Efforts
from orchestrator.scientific_file_intake import FileIntake
from orchestrator.scientific_projects import ProjectStore, InvalidProject
from scientific_compute import catalog

FOLDERS = ('frozen-gate', 'screen-effects', 'transfer-factors', 'vcc-count-emission', 'vcc-entry-check',
           'vcc-fold-score', 'vcc-package')
MODELS = Path(__file__).resolve().parents[2] / 'catalog' / 'models'


@pytest.fixture
def prepared(monkeypatch, tmp_path):
    monkeypatch.setenv('MICROMIND_HEAVY_SLOT', str(tmp_path / 'slot'))
    monkeypatch.setattr(catalog, 'runtime', lambda capability, environ=None: {
        'owner': catalog._runtime_owner(capability), 'prepared': True, 'reason': '', 'backend': 'cpu',
        'evidenceClass': 'synthetic-runtime-admission'})
    monkeypatch.setattr(runs, 'execution_runtime', lambda _: (Path(sys.executable), Path(sys.prefix)))


def load(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(f'folder_{name.replace("-", "_")}', MODELS / name / 'adapter.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_folder_capabilities_are_discovered_beside_engine_operations():
    ids = catalog.ids()
    assert ids[:len(catalog.IDS)] == catalog.IDS
    assert set(FOLDERS) <= set(ids[len(catalog.IDS):])
    for name in FOLDERS:
        d = catalog.descriptor(name)
        assert d['generation'].startswith('matrix-folder-v1.')
        assert 'implementation' not in d and str(MODELS) not in json.dumps(d)
        assert catalog.implementation(name) == {'folder': str(MODELS / name), 'adapter': 'adapter:run'}
        m = manifests.manifests()[name]
        assert [p['id'] for p in d['inputs']] == [p.id for p in m.data_inputs]
    assert catalog.descriptor('vcc-fold-score')['runtime']['owner'] == 'cell-evaluation-runtime'
    assert catalog.descriptor('vcc-package')['runtime']['owner'] == 'vcc-packaging-runtime'
    assert {d['id'] for d in catalog.descriptors()} >= set(FOLDERS)


def test_a_folder_generation_reads_its_folder_not_engine(monkeypatch):
    read = []
    original = Path.read_bytes
    def recording(self):
        read.append(self.name)
        return original(self)
    monkeypatch.setattr(Path, 'read_bytes', recording)
    catalog.descriptor('transfer-factors')
    assert 'engine.py' not in read and 'matrix_capability_runs.py' not in read and 'catalog.py' not in read
    assert {'adapter.py', 'model.toml', 'folder_worker.py', 'resource_profile.py'} <= set(read)


def test_user_and_channel_entries_never_become_matrix_capabilities(monkeypatch):
    from dataclasses import replace
    real = manifests.manifests()
    for change in ({'namespace': 'mine'}, {'channel': 'community'}, {'adapter': ''}, {'runner': 'python-env'},
                   {'folder': '/tmp/elsewhere/transfer-factors'}):
        fake = dict(real, **{'transfer-factors': replace(real['transfer-factors'], **change)})
        monkeypatch.setattr(manifests, 'manifests', lambda fake=fake: fake)
        assert 'transfer-factors' not in catalog.ids()
        with pytest.raises(ValueError):
            catalog.descriptor('transfer-factors')


def test_transfer_factors_shrinks_shifts_and_weights_by_strength(tmp_path):
    (tmp_path / 'genes.csv').write_text('gene_name\ng0\ng1\ng2\n')
    (tmp_path / 'perts.csv').write_text('target_gene\nA\nB\nZ\n')
    L = np.array([[1.0, -1.0], [0.0, 2.0]], dtype=np.float32)
    np.savez(tmp_path / 'effects.npz', targets=np.array(['A', 'B']), L=L, cells=np.array([50.0, 150.0]),
             axis_idx=np.array([0, 2]), strength=np.array([100.0, np.nan]), strength_cells=np.array([400.0, np.nan]),
             axis_length=np.array(3))
    run = load('transfer-factors').run
    params = {'a': 1.0, 's': 0.5, 'rank': 0, 'k': 50.0, 'clip_low': -5.0, 'clip_high': 2.0, 'strength_weight': 'none',
              'strength_half': 100.0, 'strength_scale': 400.0, 'contexts': 'D,E'}
    inputs = {'effects': tmp_path / 'effects.npz', 'genes': tmp_path / 'genes.csv', 'perts': tmp_path / 'perts.csv'}
    (out := tmp_path / 'plain').mkdir()
    path, summary = run(inputs, params, out, lambda: None)
    f = np.load(path)
    assert sorted(f.files) == ['D', 'E'] and summary['covered'] == 2
    mu = L.astype(np.float64).mean(0)
    lhat = mu + (L - mu) * np.array([[.5], [.75]])
    m = lhat.mean(0)
    expected = np.exp(np.array([m + .5 * (lhat[0] - m), m + .5 * (lhat[1] - m), m]))
    assert np.allclose(f['D'][:, [0, 2]], expected, rtol=1e-6) and (f['D'][:, 1] == 1).all()
    (out := tmp_path / 'weighted').mkdir()
    path, summary = run(inputs, {**params, 'strength_weight': 'saturating'}, out, lambda: None)
    w = np.load(path)['D']
    # A has n = 100*400/400 = 100 -> half the shift; B and Z (no strength) take the median, also 100.
    zero = np.exp(np.array([.5 * (lhat[0] - m), .5 * (lhat[1] - m), 0 * m]))
    assert np.allclose(w[:, [0, 2]], np.sqrt(expected * zero), rtol=1e-5)
    assert summary['strength']['medianFilled'] == 2


def score(tmp_path, name, pds, mse, weak):
    value = {'format': 'vcc-fold-score-v1', 'preset': 'vcc2026', 'versions': {'cell-eval2': '0.20.0'},
             'aggregate': {'pds_cosine': pds, 'expr_mse_unbiased_capped_norm': mse},
             'perTarget': {'pds_cosine': {'T1': weak, 'T2': pds}}}
    (tmp_path / name).write_text(json.dumps(value))
    return tmp_path / name


RULE = {'format': 'frozen-gate-v1', 'name': 'challenger must not lose', 'baseline': 'base', 'challenger': 'new',
        'metrics': {'pds_cosine': {'direction': 'higher', 'span': 0.5},
                    'expr_mse_unbiased_capped_norm': {'direction': 'lower', 'span': 1.0}},
        'subsets': {'weak': ['T1']},
        'conditions': [{'measure': 'overall', 'min': 0.0}, {'measure': 'subset', 'subset': 'weak', 'min': 0.02},
                       {'measure': 'each-metric', 'min': -0.1}]}


def test_frozen_gate_applies_its_rule(tmp_path):
    (tmp_path / 'rule.json').write_text(json.dumps(RULE))
    run = load('frozen-gate').run
    inputs = {'baseline': score(tmp_path, 'b.json', .6, .9, .4), 'challenger': score(tmp_path, 'c.json', .7, .95, .45),
              'rule': tmp_path / 'rule.json'}
    _, decision = run(inputs, {}, tmp_path, lambda: None)
    assert decision['scaledGain'] == pytest.approx({'pds_cosine': .2, 'expr_mse_unbiased_capped_norm': -.05})
    assert decision['subsets']['weak'] == pytest.approx(.1)
    assert decision['passed'] and decision['decision'] == 'new'
    inputs['challenger'] = score(tmp_path, 'c2.json', .7, 1.2, .45)
    _, decision = run(inputs, {}, tmp_path, lambda: None)
    assert not decision['passed'] and decision['decision'] == 'base'
    assert [c['holds'] for c in decision['conditions']] == [False, True, False]


def keep(store, name, body, request):
    intake = FileIntake(store, 's1', request, name=name, size=len(body), max_bytes=len(body))
    try:
        intake.write(body)
        saved = intake.publish()
    finally:
        intake.close()
    return {'id': saved.id, 'session': 's1', 'revision': saved.revision, 'digest': saved.digest}


def study(store):
    return Efforts(store).create('s1', request='study-request-0001', title='Gate', objective='Decide', criteria='Rule',
                                 budget_usd=0).id


def gate(store, refs, request):
    return runs.run_matrix(store=store, session='s1', capability='frozen-gate',
                           generation=catalog.descriptor('frozen-gate')['generation'],
                           inputs=refs, params={}, request=request)


def test_a_comparison_runs_only_on_a_rule_frozen_before_its_results(tmp_path, prepared):
    store = ProjectStore(tmp_path / 'projects')
    effort = study(store)
    by = {'by': 'user:local', 'byLabel': 'Researcher'}
    rule = keep(store, 'rule.json', json.dumps(RULE).encode(), 'rule-file-000001')
    frozen_rules.freeze(Efforts(store), effort, 's1', request='freeze-0001', name='C3 rule', artifact=rule, attribution=by)
    base = keep(store, 'b.json', score(tmp_path, 'b.json', .6, .9, .4).read_bytes(), 'score-base-00001')
    new = keep(store, 'c.json', score(tmp_path, 'c.json', .7, .95, .45).read_bytes(), 'score-new-000001')
    result = gate(store, {'baseline': base, 'challenger': new, 'rule': rule}, 'gate-request-0001')
    operation = result.document['state']['matrixOperation']
    assert operation['frozenRules']['rule']['study'] == effort
    assert operation['output']['summary']['passed'] is True
    assert operation['generation'].startswith('matrix-folder-v1.')
    # A rule frozen after the scores existed judges nothing.
    late = keep(store, 'late.json', json.dumps({**RULE, 'name': 'late'}).encode(), 'rule-file-000002')
    frozen_rules.freeze(Efforts(store), effort, 's1', request='freeze-0002', name='late', artifact=late, attribution=by)
    with pytest.raises(InvalidProject, match='frozen after baseline, challenger existed'):
        gate(store, {'baseline': base, 'challenger': new, 'rule': late}, 'gate-request-0002')
    # An unfrozen file is not a rule.
    loose = keep(store, 'loose.json', json.dumps({**RULE, 'name': 'loose'}).encode(), 'rule-file-000003')
    with pytest.raises(InvalidProject, match='frozen in a Study'):
        gate(store, {'baseline': base, 'challenger': new, 'rule': loose}, 'gate-request-0003')


def test_frozen_rules_are_append_only(tmp_path):
    store = ProjectStore(tmp_path / 'projects')
    effort = study(store)
    by = {'by': 'user:local', 'byLabel': 'Researcher'}
    rule = keep(store, 'rule.json', json.dumps(RULE).encode(), 'rule-file-000001')
    first = frozen_rules.freeze(Efforts(store), effort, 's1', request='freeze-0001', name='C3 rule', artifact=rule, attribution=by)
    again = frozen_rules.freeze(Efforts(store), effort, 's1', request='freeze-0001', name='C3 rule', artifact=rule, attribution=by)
    assert again.revision == first.revision
    from orchestrator.scientific_projects import Conflict
    with pytest.raises(Conflict):
        frozen_rules.freeze(Efforts(store), effort, 's1', request='freeze-0001', name='renamed', artifact=rule, attribution=by)
    with pytest.raises(Conflict, match='already frozen'):
        frozen_rules.freeze(Efforts(store), effort, 's1', request='freeze-0002', name='again', artifact=rule, attribution=by)
    not_json = keep(store, 'rule.txt', b'not json', 'rule-file-000004')
    with pytest.raises(InvalidProject, match='JSON'):
        frozen_rules.freeze(Efforts(store), effort, 's1', request='freeze-0003', name='bad', artifact=not_json, attribution=by)


def test_the_folder_worker_cannot_import_the_engine(tmp_path):
    import subprocess
    folder = tmp_path / 'probe'
    folder.mkdir()
    (folder / 'adapter.py').write_text(
        'def run(inputs, params, output, check):\n'
        '    try:\n        import engine\n        reach = "engine"\n    except ImportError:\n        reach = "none"\n'
        '    (output / "r.json").write_text("{}")\n    return output / "r.json", {"reach": reach}\n')
    (tmp_path / 'out').mkdir()
    request = {'capability': 'probe', 'inputs': {}, 'params': {}, 'output': str(tmp_path / 'out'),
               'receipt': str(tmp_path / 'out' / 'receipt.json'), 'folder': str(folder), 'adapter': 'adapter:run'}
    (tmp_path / 'request.json').write_text(json.dumps(request))
    subprocess.run([sys.executable, str(Path(catalog.__file__).with_name('folder_worker.py')), str(tmp_path / 'request.json')],
                   check=True, cwd=tmp_path / 'out')
    receipt = json.loads((tmp_path / 'out' / 'receipt.json').read_text())
    assert receipt['summary']['reach'] == 'none'
