"""Parent-issued exact reader checks; never workspace or scientific qualification.

Only the runner issues receipts. The seal key stays outside the confined worker's
read grants; accepting caller-supplied success JSON is not a proof producer.
"""
from dataclasses import replace
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
import uuid

from gpu_worker import bundle_contract
from gpu_worker.reader_proof_contract import FORMAT, MAX_INPUT_BYTES
from . import bundle_review, envs


def _key(installed, create=False):
    with bundle_review._directory(installed, create=create) as directory:
        if create:
            try:
                fd=os.open('.reader-seal-key',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd,'wb') as stream:
                    stream.write(secrets.token_bytes(32));stream.flush();os.fsync(stream.fileno())
        fd=os.open('.reader-seal-key',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size!=32:
                raise ValueError('The reader proof issuer key changed')
            return stream.read(33)


def seal(installed, record):
    return {**record,'issuerSeal':hmac.new(_key(installed,True),bundle_review._json(record),hashlib.sha256).hexdigest()}


def authentic(installed, proof):
    try:
        record={key:value for key,value in proof.items() if key!='issuerSeal'}
        expected=hmac.new(_key(installed),bundle_review._json(record),hashlib.sha256).hexdigest()
        if type(proof.get('issuerSeal')) is not str or not hmac.compare_digest(expected,proof['issuerSeal']):return False
        for case in proof['cases']:
            if not bundle_review.descriptors_valid(case.get('artifacts')):return False
            for artifact in case['artifacts'].values():bundle_review.read(installed,artifact)
        return True
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        return False


def runtime_identity():
    from . import scientific_host
    from .paths import SERVICES
    sources=[SERVICES/name for name in (
        'orchestrator/reader_proof.py', 'orchestrator/bundle_runtime.py', 'orchestrator/bundle_review.py',
        'gpu_worker/bundle_contract.py', 'orchestrator/scientific_host.py',
        'orchestrator/prepared_bundles.py', 'orchestrator/interpreter_identity.py',
        'orchestrator/environment_check.py', 'orchestrator/envs.py',
        'orchestrator/scientific_directory_intake.py', 'orchestrator/scientific_file_intake.py',
        'orchestrator/scientific_projects.py', 'orchestrator/scientific_files.py',
        'orchestrator/source_files.py', 'orchestrator/sandbox.py',
        # The parent enforces resource limits, process lifetime and read grants;
        # these implementations are not part of the copied worker bootstrap.
        'orchestrator/worker_resources.py', 'orchestrator/fiji_sandbox.py',
        'orchestrator/owned_process.py', 'orchestrator/prepared_artifacts.py',
        'orchestrator/bounded_files.py', 'orchestrator/generation_leases.py',
        'orchestrator/native_regions.py')]
    sources += [SERVICES/package/name for package,names in scientific_host.BOOTSTRAP.items() for name in names]
    digest=hashlib.sha256()
    for path in sources:
        digest.update(path.name.encode());digest.update(envs._file_sha(path))
    return digest.hexdigest()


def inputs(snapshot, case):
    from .bundle_runtime import _bytes, _pairs
    sizes=[(snapshot/path).stat(follow_symlinks=False).st_size for path in case['files'].values()]
    if not 0<sum(sizes)<=MAX_INPUT_BYTES:
        raise ValueError('Reader checks require at most 8 MiB of exact input files')
    from .prepared_bundles import _regular
    files={name:_regular(snapshot/path,MAX_INPUT_BYTES) for name,path in case['files'].items()}
    if not 0<sum(map(len,files.values()))<=MAX_INPUT_BYTES:
        raise ValueError('Reader checks require at most 8 MiB of exact input files')
    reference=_bytes(snapshot,case['reference'])
    expected=json.loads(reference,object_pairs_hook=_pairs)
    from gpu_worker.reader_proof_contract import observation
    observation(expected)
    identity={name:{'file':case['files'][name],'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()}
              for name,body in files.items()}
    return files,reference,expected,identity


def _observe(member, generation, files, environ):
    """Real prepared admission, immutable Project intake, confined host and owned cleanup."""
    from . import scientific_host
    from .scientific_directory_intake import DirectoryIntake
    from .scientific_projects import ProjectStore
    from .scientific_files import CHUNK_BYTES
    with tempfile.TemporaryDirectory(prefix='hooke-reader-proof-') as root:
        store=ProjectStore(Path(root)/'projects')
        entries=[];offset=0
        for name,body in files.items():
            entries.append({'path':name,'offset':offset,'size':len(body)});offset+=len(body)
        intake=DirectoryIntake(store,'reader-proof',uuid.uuid4().hex,name='reader-check',size=offset,
                               max_bytes=MAX_INPUT_BYTES,entries={'format':'source-directory-v1','entries':entries})
        try:
            for body in files.values():
                for start in range(0,len(body),CHUNK_BYTES):intake.write(body[start:start+CHUNK_BYTES])
            saved=intake.publish()
        finally:intake.close()
        source=store.file_source(saved.id,'reader-proof',saved.revision,saved.document['resources'][0])
        source=replace(source,directory=saved.document['state']['sourceDirectory'])
        with scientific_host.review(member.key,generation,{},environ,files={member.reader['input']:source}) as worker:
            return worker.reader_observation()


def check(installed, environ, *, records=None):
    """Run all exact declared reader cases; no activation or proof-file write here."""
    from . import bundle_runtime
    snapshot=installed.folder/envs.SNAPSHOT/installed.m.id
    records=[] if records is None else records
    for member in bundle_runtime.declarations(installed.m):
        if member.bridge!='scientific-host-v1' or not member.reader_checks:
            raise ValueError('This is a private scientific-host candidate: explicit reader checks are required for every member; interactive workspace and independent qualification remain required')
        view=bundle_contract.view(installed.m,member)
        for case in member.reader_checks['cases']:
            files,reference,expected,identity=inputs(snapshot,case)
            if sum(map(len,files.values()))>view.reader['maximum_bytes']:
                raise ValueError('Reader proof input exceeds the declared reader allowance')
            actual=_observe(view,installed.folder.name,files,environ)
            from gpu_worker.reader_proof_contract import observation
            observation(actual)
            # Typed/dtype/geometry records stay exact; never normalize numeric or axis differences.
            key=bundle_runtime.case_key(member,case)
            matches=bundle_review._json(actual)==bundle_review._json(expected)
            records.append({'case':key,'member':view.key,'source':case['source'],'ok':matches,
                            'inputs':identity,'referenceSha256':hashlib.sha256(reference).hexdigest(),
                            'artifacts':bundle_review.store(installed,key,bundle_review._json(identity),reference,
                                                           actual,'reader',input_kind='json')})
            if not matches:
                raise ValueError(f'{view.name}: reader data, levels, dtype, axes or calibration differ from {case["id"]}; actual/reference artifacts were retained')
            _,current_reference,_,current_identity=inputs(snapshot,case)
            if current_reference!=reference or current_identity!=identity:
                raise ValueError('Reader proof inputs changed during execution')
    return records


def complete(m,folder,proof):
    from types import SimpleNamespace
    from . import bundle_runtime, prepared_bundles, interpreter_identity
    try:
        installed=SimpleNamespace(folder=folder)
        if (proof.get('scope')!=FORMAT or proof.get('fullQualification') is not False
                or proof.get('generation')!=folder.name or proof.get('readerWorkerSha256')!=runtime_identity()
                or not authentic(installed,proof)):return False
        receipt=prepared_bundles._receipt(folder/envs.RECEIPT)
        if envs.prepared_generation(m,receipt)!=folder.name:return False
        interpreter_identity.verify(folder/'bin/python',receipt.get('interpreter'))
        if proof.get('preparationSha256')!=hashlib.sha256(bundle_review._json(receipt)).hexdigest():return False
        wanted=[]
        for member in bundle_runtime.declarations(m):
            if member.bridge!='scientific-host-v1' or not member.reader_checks:return False
            for case in member.reader_checks['cases']:
                _,reference,_,identity=inputs(folder/envs.SNAPSHOT/m.id,case)
                wanted.append((bundle_runtime.case_key(member,case),identity,hashlib.sha256(reference).hexdigest()))
        return len(proof['cases'])==len(wanted) and all(
            row.get('ok') is True and (row.get('case'),row.get('inputs'),row.get('referenceSha256'))==expected
            for row,expected in zip(proof['cases'],wanted))
    except (OSError,ValueError,TypeError,KeyError,AttributeError,envs.Failed):return False


def prove(installed,environ, *, records=None):
    from . import prepared_bundles
    receipt=prepared_bundles._receipt(installed.folder/envs.RECEIPT)
    before=runtime_identity()
    records=check(installed,environ,records=records)
    if runtime_identity()!=before or prepared_bundles._receipt(installed.folder/envs.RECEIPT)!=receipt:
        raise ValueError('Reader proof implementation or preparation changed during its check')
    return seal(installed,{'ok':True,'scope':FORMAT,'fullQualification':False,'generation':installed.folder.name,
                           'readerWorkerSha256':before,'preparationSha256':hashlib.sha256(bundle_review._json(receipt)).hexdigest(),
                           'cases':records,'says':'Exact reader data checks passed; independent and interactive workspace qualification remains required.'})
