"""Reader proof admission with synthetic layers and stand-in prepared workers; no native qualification."""
import copy
import json
from types import SimpleNamespace

import numpy as np
import pytest

from gpu_worker import bundle_contract, manifests as catalog
from tests.test_bundle_manifest import HEAD
from tests.scientific_threads import exclude_preexisting_test_harness_threads  # noqa:F401

MEMBER = '''
[[members]]
id = "read-folder"
name = "Read folder"
adapter = "adapter:open_host"
bridge = "scientific-host-v1"
reader = {format=3, kind="directory", input="source", patterns=["*"], entries=["*.npy"], maximum_bytes=8388608, keeps=["values", "dtype", "axes", "calibration"]}
inputs = [{id="project", type="project.scientific"}]
outputs = [{id="result", type="project.scientific"}]
reader_checks = {format=1, cases=[{id="tiny", files={"data.npy"="example.npy"}, reference="reference-reader.json", source="Synthetic uint16 data; no biological oracle"}]}
'''


def test_reader_checks_are_an_explicit_versioned_directory_contract(tmp_path):
    path=tmp_path/'image-tools/model.toml';path.parent.mkdir();path.write_text(HEAD+MEMBER)
    owner,problems=catalog.read(path)
    assert owner is not None, problems
    member=bundle_contract.parse_members(list(owner.members),owner)[0]
    assert member.reader_checks['format']==1
    assert member.reader_checks['cases'][0]['files']=={'data.npy':'example.npy'}


def test_reader_observes_every_level_and_refuses_aggregate_before_reading():
    from env_worker.scientific_reader_probe import observe
    data=np.arange(24,dtype='>u2').reshape(2,3,4)
    layer=SimpleNamespace(_type_string='image',rgb=False,multiscale=True,data=[data,data[:,::2,::2]],
        ndim=3,axis_labels=('z','y','x'),units=('um','um','um'),data_to_world=lambda point:tuple(v*s for v,s in zip(point,(2.,.5,.5))))
    host=SimpleNamespace(viewer=SimpleNamespace(layers=[layer]),view_bindings=SimpleNamespace(key_for=lambda _: 'stack'))
    actual=observe(host)
    assert [v['shape'] for v in actual['layers'][0]['levels']]==[[2,3,4],[2,2,2]]
    assert [v['dtype'] for v in actual['layers'][0]['levels']]==['>u2','>u2']
    import hashlib
    assert actual['layers'][0]['levels'][0]['sha256']==hashlib.sha256(repr((data.dtype.str,data.shape)).encode()+data.tobytes()).hexdigest()
    assert actual['layers'][0]['basis']==[[0.,0.,0.],[2.,0.,0.],[0.,.5,0.],[0.,0.,.5]]
    class Huge:
        shape=(2,8192,8192);dtype=np.dtype('uint16')
        def __getitem__(self,key):pytest.fail('Read oversized data before aggregate admission')
    layer.data=[Huge()]
    with pytest.raises(ValueError,match='64 MiB'):observe(host)


def test_reader_proof_receipt_requires_parent_seal_and_exact_retained_artifacts(tmp_path):
    from orchestrator import reader_proof, bundle_review
    installed=SimpleNamespace(folder=tmp_path/'0123456789abcdef')
    installed.folder.mkdir()
    artifacts=bundle_review.store(installed,'a'*64,b'{"input":1}',b'{"expected":1}',{'actual':1},'reader',input_kind='json')
    record={'scope':'reader-data-contract-v1','generation':installed.folder.name,'cases':[{'artifacts':artifacts}]}
    assert not reader_proof.authentic(installed,record)
    signed=reader_proof.seal(installed,record)
    assert reader_proof.authentic(installed,signed)
    changed=copy.deepcopy(signed);changed['cases'][0]['extra']='unobserved'
    assert not reader_proof.authentic(installed,changed)
    target=bundle_review.folder(installed)/artifacts['actual']['sha256'];target.write_text('changed')
    assert not reader_proof.authentic(installed,signed)

from tests.test_env_runner import home, standin_uv  # noqa:F401


def prepared_reader(home,monkeypatch,version='1',*,expected=None):
    from orchestrator import envs
    folder=home/'repo/models/image-tools';folder.mkdir(exist_ok=True)
    (folder/'model.toml').write_text(HEAD.replace('version = "1"',f'version = "{version}"')+MEMBER)
    (folder/'env.lock').write_text('numpy==2.0.0 --hash=sha256:'+'a'*64+'\n')
    (folder/'adapter.py').write_text('def open_host(context): pass\n')
    np.save(folder/'example.npy',np.arange(24,dtype='>u2').reshape(2,3,4))
    expected=expected or {'format':'reader-data-contract-v1','layers':[{'key':'stack','axes':['z','y','x'],'units':['micrometer']*3,
        'basis':[[0.,0.,0.],[2.,0.,0.],[0.,.5,0.],[0.,0.,.5]],'levels':[{'shape':[2,3,4],'dtype':'>u2','sha256':'b'*64}]}]}
    (folder/'reference-reader.json').write_text(json.dumps(expected))
    catalog.manifests.cache_clear();owner=catalog.get('image-tools');assert owner is not None,catalog.problems()
    standin_uv(home,monkeypatch)
    return owner,envs.build(owner),expected


def test_exact_reader_proof_is_not_workspace_qualification_and_replays_first_check(home,monkeypatch):
    from orchestrator import bundle_runtime,env_runner,reader_proof,prepared_bundles
    from orchestrator.maintenance import promote
    owner,folder,expected=prepared_reader(home,monkeypatch)
    calls=[]
    def observe(member,generation,files,environ):
        with prepared_bundles.prepared_member(member.key,generation,environ):
            calls.append((member.key,generation,files))
        return copy.deepcopy(expected)
    monkeypatch.setattr(reader_proof,'_observe',observe)
    proof=env_runner.prove(owner)
    assert proof['ok'],proof
    assert proof['fullQualification'] is False and proof['scope']=='reader-data-contract-v1'
    assert len(calls)==1 and calls[0][0]=='image-tools#read-folder'
    kept=env_runner.generation(owner,folder.name)
    assert kept is not None and bundle_runtime.proof_complete(kept.m,folder,proof)
    assert promote.first_check(owner,folder.name)=='' and len(calls)==2
    counterfeit=copy.deepcopy(proof);counterfeit['fullQualification']=True
    assert not bundle_runtime.proof_complete(kept.m,folder,counterfeit)
    for key in ('cases','issuerSeal'):
        partial=copy.deepcopy(proof);partial.pop(key)
        assert not bundle_runtime.proof_complete(kept.m,folder,partial)
    (folder/'entry/image-tools/example.npy').write_bytes(b'changed')
    assert not bundle_runtime.proof_complete(kept.m,folder,proof)
    assert 'ready and proven' in promote.first_check(owner,folder.name)


def test_reader_candidate_failure_and_first_check_rollback_preserve_exact_project(home,monkeypatch):
    from orchestrator import env_runner,envs,reader_proof,scientific_host,prepared_bundles,scientific_retention
    from orchestrator.maintenance import promote,policy
    from orchestrator.scientific_projects import ProjectStore
    from contextlib import contextmanager
    owner,v1,expected=prepared_reader(home,monkeypatch)
    observations=[];fail=False
    @contextmanager
    def review(key,generation,sources,environ,*,files):
        assert not sources and set(files)=={'source'}
        assert files['source'].directory['entries'][0]['path']=='data.npy'
        assert files['source'].read(0,6)==b'\x93NUMPY'
        with prepared_bundles.prepared_member(key,generation,environ):
            def observe():
                observations.append(generation)
                if fail:raise ValueError('reader failed during first check')
                return copy.deepcopy(expected)
            yield SimpleNamespace(reader_observation=observe)
    monkeypatch.setattr(scientific_host,'review',review)
    assert env_runner.prove(owner)['ok']
    promote.promote(owner,v1.name,by='user')
    store=ProjectStore(home/'reader-projects',runtime_root=v1.parent.parent)
    saved=store.create('s1',{'kind':'scene','title':'Original reader work','state':{'chosenLevel':0},'resources':[],
        'runtimes':[{'bundle':owner.key,'generation':v1.name,'operation':'read-folder'}]})
    candidate,v2,_=prepared_reader(home,monkeypatch,'2')
    assert env_runner.prove(candidate)['ok']
    policy.record(candidate,'auto',by='user',how='screen')
    fail=True
    decision=promote.promote(candidate,v2.name,by='maintenance:auto')
    assert decision['action']=='rollback' and decision['to']==v1.name
    assert env_runner.selected(candidate).folder==v1
    assert 'first check' in policy.get(candidate.key)['held']
    reopened=ProjectStore(store.root,runtime_root=v1.parent.parent).read(saved.id,'s1',revision=1)
    assert reopened==saved and reopened.document['runtimes'][0]['generation']==v1.name
    with pytest.raises(scientific_retention.Retained):
        scientific_retention.require_removable(v1.parent.parent,models={owner.key},generations={v1.name})
    broken,v3,_=prepared_reader(home,monkeypatch,'3')
    result=env_runner.prove(broken)
    assert not result['ok'] and 'first check' in result['why']
    with pytest.raises(promote.Failed):promote.promote(broken,v3.name,by='user')
    assert env_runner.selected(broken).folder==v1 and store.read(saved.id,'s1')==saved


def test_reader_member_semantics_and_permissions_require_maintenance_review(tmp_path):
    from dataclasses import replace
    from orchestrator.maintenance import compat
    path=tmp_path/'image-tools/model.toml';path.parent.mkdir();path.write_text(HEAD+MEMBER)
    owner,problems=catalog.read(path);assert owner,problems
    changed=copy.deepcopy(list(owner.members));changed[0]['reader']['maximum_bytes']=4096
    assert not compat.compare(owner,replace(owner,members=tuple(changed)))['compatible']
    changed=copy.deepcopy(list(owner.members));changed[0]['reader_checks']['cases'][0]['source']='New source attribution'
    assert compat.compare(owner,replace(owner,members=tuple(changed)))['compatible']


@pytest.mark.parametrize('field,value', [('dtype','<u2'),('sha256','c'*64),('shape',[4,3,2])])
def test_exact_reader_comparison_rejects_value_dtype_or_shape_mismatch(home,monkeypatch,field,value):
    from orchestrator import reader_proof,env_runner
    owner,folder,expected=prepared_reader(home,monkeypatch)
    changed=copy.deepcopy(expected);changed['layers'][0]['levels'][0][field]=value
    monkeypatch.setattr(reader_proof,'_observe',lambda *args:changed)
    result=env_runner.prove(owner)
    assert not result['ok'] and 'differ' in result['why']
    assert result['cases'][0]['ok'] is False and set(result['cases'][0]['artifacts'])=={'input','reference','actual'}
    assert env_runner.generation(owner,folder.name) is None
    assert (folder.with_name(folder.name+'.review')).is_dir()


def test_changed_worker_or_generation_invalidates_reader_proof(home,monkeypatch):
    from orchestrator import reader_proof,env_runner,bundle_runtime
    owner,folder,expected=prepared_reader(home,monkeypatch)
    monkeypatch.setattr(reader_proof,'_observe',lambda *args:copy.deepcopy(expected))
    proof=env_runner.prove(owner);assert proof['ok']
    kept=env_runner.generation(owner,folder.name)
    assert not bundle_runtime.proof_complete(kept.m,folder.with_name('0'*16),proof)
    monkeypatch.setattr(reader_proof,'runtime_identity',lambda:'0'*64)
    assert not bundle_runtime.proof_complete(kept.m,folder,proof)


@pytest.mark.parametrize('dependency', [
    'worker_resources.py', 'fiji_sandbox.py', 'owned_process.py',
    'prepared_artifacts.py', 'bounded_files.py', 'generation_leases.py', 'native_regions.py',
])
def test_changed_confinement_dependency_invalidates_prior_reader_proof(home,monkeypatch,dependency):
    from orchestrator import reader_proof,env_runner,bundle_runtime,envs
    from orchestrator.paths import SERVICES
    owner,folder,expected=prepared_reader(home,monkeypatch)
    monkeypatch.setattr(reader_proof,'_observe',lambda *args:copy.deepcopy(expected))
    proof=env_runner.prove(owner);assert proof['ok']
    kept=env_runner.generation(owner,folder.name)
    assert kept is not None and bundle_runtime.proof_complete(kept.m,folder,proof)
    target=SERVICES/'orchestrator'/dependency
    original=envs._file_sha
    def changed(path):
        return b'changed confinement implementation' if path==target else original(path)
    monkeypatch.setattr(envs,'_file_sha',changed)
    assert not bundle_runtime.proof_complete(kept.m,folder,proof)
    assert env_runner.generation(owner,folder.name) is None


def test_reader_producer_closed_bootstrap_imports_and_observes(tmp_path):
    import subprocess,sys
    from orchestrator.scientific_host import _bootstrap
    bootstrap=tmp_path/'bootstrap';_bootstrap(bootstrap)
    code='''import sys
from types import SimpleNamespace
sys.path.insert(0,sys.argv[1])
import numpy as np
from env_worker.scientific_reader_probe import observe
layer=SimpleNamespace(_type_string='image',rgb=False,multiscale=False,data=np.array([[2**64-1]],dtype='uint64'),ndim=2,
axis_labels=('y','x'),units=('um','um'),data_to_world=lambda point:tuple(float(v) for v in point))
host=SimpleNamespace(viewer=SimpleNamespace(layers=[layer]),view_bindings=SimpleNamespace(key_for=lambda _: 'exact'))
assert observe(host)['layers'][0]['levels'][0]['dtype']=='<u8'
'''
    result=subprocess.run([sys.executable,'-I','-B','-c',code,str(bootstrap)],cwd=tmp_path,
                          capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr


def test_reader_private_observation_is_not_a_browser_request():
    from orchestrator.scientific_host import ReviewWorker,Failed
    worker=object.__new__(ReviewWorker)
    with pytest.raises(Failed,match='declared scientific view interaction'):
        worker.request({'op':'reader-proof'})


def test_reader_input_aggregate_refuses_before_worker_or_file_read(home,monkeypatch):
    from orchestrator import reader_proof,env_runner
    owner,folder,_=prepared_reader(home,monkeypatch)
    with (folder/'entry/image-tools/example.npy').open('wb') as stream:stream.truncate(9<<20)
    monkeypatch.setattr(reader_proof,'_observe',lambda *args:pytest.fail('Oversized reader input reached host'))
    result=env_runner.prove(owner)
    assert not result['ok'] and '8 MiB' in result['why']


def test_reader_changed_input_during_observation_cannot_issue_proof(home,monkeypatch):
    from orchestrator import reader_proof,env_runner
    owner,folder,expected=prepared_reader(home,monkeypatch)
    def observe(*args):
        (folder/'entry/image-tools/example.npy').write_bytes(b'changed after admission')
        return expected
    monkeypatch.setattr(reader_proof,'_observe',observe)
    result=env_runner.prove(owner)
    assert not result['ok'] and 'changed during' in result['why']
    assert env_runner.generation(owner,folder.name) is None


def test_reader_observer_does_not_weaken_background_thread_refusal():
    import threading
    from env_worker.scientific_reader_probe import observe
    layer=SimpleNamespace(_type_string='image',rgb=False,multiscale=False,data=np.ones((2,3),dtype='uint16'),
        ndim=2,axis_labels=('y','x'),units=('um','um'),data_to_world=lambda p:tuple(float(n) for n in p))
    host=SimpleNamespace(viewer=SimpleNamespace(layers=[layer]),view_bindings=SimpleNamespace(key_for=lambda _: 'stack'))
    release=threading.Event();thread=threading.Thread(target=release.wait);thread.start()
    try:
        with pytest.raises(ValueError,match='stable snapshot'):observe(host)
    finally:release.set();thread.join(timeout=5)
    assert not thread.is_alive() and observe(host)['layers'][0]['levels'][0]['shape']==[2,3]


def test_reader_package_keeps_only_declared_proof_files_and_cannot_claim_qualification(home,monkeypatch,tmp_path):
    from orchestrator import share,publish
    owner,_,_=prepared_reader(home,monkeypatch)
    entry=catalog.entry_folder(owner)
    (entry/'private-neighbor.txt').write_text('never included')
    package=tmp_path/'reader.observationui.zip';package.write_bytes(share.pack(owner))
    shared,_,files=share._read(package)
    assert {'example.npy','reference-reader.json'}<=files.keys()
    assert 'private-neighbor.txt' not in files and shared['proof']=={}
    assert any('private scientific' in reason for reason in publish.problems(owner,proven=True))
