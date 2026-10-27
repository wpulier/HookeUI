"""Synthetic declarations/storage and injected proof observations; no native qualification."""
import copy
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from gpu_worker import manifests as catalog
from orchestrator import bundle_admission, bundle_review, bundle_runtime, env_runner, envs, own_trials
from tests.test_bundle_manifest import HEAD
from tests.test_scientific_reader_proof import MEMBER


@pytest.fixture
def reader(tmp_path):
    folder = tmp_path / 'image-tools'; folder.mkdir()
    path = folder / 'model.toml'
    path.write_text(HEAD + MEMBER.replace('"dtype", "axes"', '"dtype", "byte-order", "axes"'))
    owner, errors = catalog.read(path)
    assert owner, errors
    return owner


def test_local_reader_setup_preserves_machine_trust_and_public_refusal(reader, monkeypatch):
    from orchestrator import channels, readiness
    from orchestrator.maintenance import prepare
    assert bundle_admission.scientific(reader) and bundle_admission.local_reader(reader)
    assert bundle_admission.local_problem(reader) is None
    assert 'independent qualification' in bundle_admission.integration_problem(reader)
    monkeypatch.setattr(readiness, 'misfit', lambda *a: '')
    monkeypatch.setattr(channels, 'trusted', lambda *a: True)
    assert prepare._machine(reader, {}) == ''
    class ReachedBuild(Exception): pass
    monkeypatch.setattr(env_runner, 'preserve_active', lambda *a: None)
    monkeypatch.setattr(envs, 'build', lambda *a, **k: (_ for _ in ()).throw(ReachedBuild()))
    with pytest.raises(ReachedBuild): envs.install(reader)
    assert own_trials.marker(reader.key).is_file()
    assert own_trials.pending(reader)
    monkeypatch.setattr(channels, 'trusted', lambda *a: False)
    assert 'allowed' in prepare._machine(reader, {})
    with pytest.raises(envs.Failed, match='allow code'): envs.install(reader)
    monkeypatch.setattr(readiness, 'misfit', lambda *a: 'unsupported machine')
    assert prepare._machine(reader, {}) == 'unsupported machine'
    with pytest.raises(envs.Failed, match='unsupported machine'): envs.install(reader)


@pytest.mark.parametrize('change', ['unchecked', 'nonreader', 'mixed'])
def test_other_scientific_contracts_remain_private(reader, change):
    members = copy.deepcopy(list(reader.members))
    if change in ('unchecked', 'nonreader'): members[0].pop('reader_checks')
    if change == 'nonreader':
        members[0].pop('reader')
    if change == 'mixed':
        members.append({'id':'extra','name':'Extra','adapter':'adapter:extra',
                        'inputs':[{'id':'image','type':'image.2d.rgb8'}],
                        'outputs':[{'id':'result','type':'image.2d.rgb8'}]})
    candidate = replace(reader, members=tuple(members))
    assert bundle_admission.scientific(candidate)
    assert not bundle_admission.local_reader(candidate)
    if change == 'nonreader':
        assert bundle_admission.local_workflow(candidate) and bundle_admission.local_problem(candidate) is None
        assert 'private scientific-host' in bundle_admission.integration_problem(candidate)
    else:
        assert 'private scientific-host' in bundle_admission.local_problem(candidate)


def test_reader_review_requires_exact_current_evidence_and_explicit_acceptance(reader, tmp_path, monkeypatch):
    folder = tmp_path / '0123456789abcdef'; folder.mkdir()
    base = SimpleNamespace(folder=folder)
    artifacts = bundle_review.store(base, 'a'*64, b'{"file":"input"}', b'{"layers":[]}', {'layers':[]}, 'reader', input_kind='json')
    proof = {'ok':True,'scope':'reader-data-contract-v1','fullQualification':False,
             'generation':folder.name,'readerWorkerSha256':'b'*64,'preparationSha256':'c'*64,
             'cases':[{'case':'a'*64,'member':'image-tools#read-folder','source':'Synthetic exact reader oracle',
                       'inputs':{'data.npy':{'sha256':'d'*64,'bytes':12}},'referenceSha256':'e'*64,'artifacts':artifacts}]}
    installed = env_runner.Installed(reader, folder, proof, True)
    monkeypatch.setattr(bundle_runtime, 'proof_complete', lambda *args: True)
    monkeypatch.setattr(catalog, 'get', lambda key: reader)
    monkeypatch.setattr(env_runner, 'in_use', lambda *args: installed)
    own_trials.await_comparison(reader.key, review_kind='scientific-review')
    assert own_trials.pending(reader)
    view = bundle_review.projection(installed)
    assert view['cases'][0]['kind'] == 'reader' and 'device' not in view['cases'][0]
    assert view['cases'][0]['inputs'] == proof['cases'][0]['inputs']
    assert bundle_review.identity(replace(installed,proof={**proof,'at':'later'})) == view['digest']
    bundle_review.accept(reader.key, folder.name, view['digest'])
    assert not own_trials.pending(reader)
    proof['readerWorkerSha256'] = 'f'*64
    assert own_trials.pending(reader)
    with pytest.raises(ValueError, match='changed'): bundle_review.accept(reader.key, folder.name, view['digest'])
    monkeypatch.setattr(bundle_runtime, 'proof_complete', lambda *args: False)
    with pytest.raises(ValueError, match='complete reference checks'): bundle_review.projection(installed)


def test_new_reader_intake_uses_selected_generation_and_refuses_unaccepted_or_stale_choice(reader, tmp_path, monkeypatch):
    from orchestrator import scientific_readers as readers
    from orchestrator.scientific_projects import InvalidProject
    selected = replace(reader, version='old')
    installed = env_runner.Installed(selected,tmp_path/('a'*16),{},False)
    saved = SimpleNamespace(id='source',digest='d'*64)
    source = {'name':'input','kind':'directory','size':12,'directory':{'entries':[{'path':'data.npy'}]}}
    monkeypatch.setattr(readers,'source_document',lambda *a:(saved,source))
    monkeypatch.setattr(catalog,'manifests',lambda:{reader.key:reader})
    monkeypatch.setattr(catalog,'mine',lambda *a:{})
    monkeypatch.setattr(catalog,'get',lambda *a:reader)
    monkeypatch.setattr(envs,'generation',lambda *a:'b'*16)
    monkeypatch.setattr(envs,'root',lambda *a:tmp_path)
    # The newer catalog may remove the reader/check contract and require another machine.
    raw = copy.deepcopy(list(reader.members))
    raw[0].pop('reader_checks'); raw[0].pop('reader')
    newer = replace(reader, members=tuple(raw))
    monkeypatch.setattr(catalog,'manifests',lambda:{reader.key:newer})
    monkeypatch.setattr(catalog,'get',lambda *a:newer)
    monkeypatch.setattr(readers.readiness,'misfit',lambda owner:'' if owner is selected else 'new machine required')
    monkeypatch.setattr(readers.channels,'trusted',lambda *a:True)
    monkeypatch.setattr(env_runner,'in_use',lambda *a:installed)
    pending = [True]
    monkeypatch.setattr(own_trials,'pending',lambda *a:pending[0])
    @contextmanager
    def prepared(*args):
        yield SimpleNamespace(m=SimpleNamespace(owner=selected))
    monkeypatch.setattr(readers.prepared_bundles,'prepared_member',prepared)
    row=readers.discover(None,'source','s1',1)['readers'][0]
    assert row['generation']=='a'*16 and not row['canReview'] and 'Review and enable' in row['reason']
    pending[0]=False
    row=readers.discover(None,'source','s1',1)['readers'][0]
    assert row['generation']=='a'*16 and row['canReview']
    for generation, needs_review in [('b'*16,False),('a'*16,True)]:
        pending[0]=needs_review
        with pytest.raises(InvalidProject,match='accepted checks changed'):
            readers.bind(None,'source','s1',1,'image-tools#read-folder',generation)
