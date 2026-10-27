"""Parent-observed local control/Save/reopen receipts, not scientific accuracy.

Only the owned scientific worker calls this observer. Receipts stay in the existing
private generation proof/review store; there is no endpoint accepting success JSON.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace

from gpu_worker import bundle_contract
from . import bundle_admission, bundle_review, envs, reader_proof
from .file_locks import fcntl

FORMAT = 'local-interactive-workflow-v1'
WORDS = ('Local controls, Save tool and reopening were observed. Scientific accuracy, '
         'export fidelity and independent qualification are not established by this check.')


def runtime_identity():
    from .paths import SERVICES
    digest = hashlib.sha256(reader_proof.runtime_identity().encode())
    for name in ('local_workflow.py', 'scientific_reviews.py', 'scientific_history.py',
                 'scientific_outputs.py', 'scientific_tools.py', 'scientific_project_review.py'):
        digest.update(name.encode()); digest.update(envs._file_sha(SERVICES/'orchestrator'/name))
    return digest.hexdigest()


def _identity(m, folder):
    from . import prepared_bundles, interpreter_identity
    receipt = prepared_bundles._receipt(folder/envs.RECEIPT)
    if envs.prepared_generation(m, receipt) != folder.name:
        raise ValueError('The local workflow preparation changed')
    interpreter_identity.verify(folder/'bin/python', receipt.get('interpreter'))
    return {'generation': folder.name, 'workflowHostSha256': runtime_identity(),
            'preparationSha256': hashlib.sha256(bundle_review._json(receipt)).hexdigest(),
            'declarationSha256': envs._file_sha(folder/envs.SNAPSHOT/m.id/'model.toml').hex()}


def _read(folder, suffix):
    from .prepared_bundles import _regular
    try:
        value = json.loads(_regular(folder.with_name(folder.name+suffix), bundle_review.MAX_FILE))
        return value if type(value) is dict else {}
    except (OSError, ValueError):
        return {}


def _write(folder, suffix, record):
    target = folder.with_name(folder.name+suffix)
    descriptor, temporary = tempfile.mkstemp(prefix=target.name+'.', dir=target.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(bundle_review._json(record)); stream.flush(); os.fsync(stream.fileno())
        Path(temporary).replace(target)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def _locked(installed):
    # Separate from the artifact writer's lock: the transition also writes artifacts.
    with bundle_review._directory(installed, create=True) as directory:
        fd = os.open('.workflow-lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK, 0o600, dir_fd=directory)
        try:
            import stat
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError('The workflow evidence lock is not a regular file')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)


def _reference(saved):
    return {'id': saved.id, 'session': saved.session, 'revision': saved.revision, 'digest': saved.digest}


def _members(m):
    return [bundle_contract.capability_key(m.key, member.id)
            for member in bundle_contract.parse_members(list(m.members), m)]


def _matches(installed, proof, identity):
    return (proof.get('scope') == FORMAT and proof.get('fullQualification') is False
            and all(proof.get(key) == value for key, value in identity.items())
            and reader_proof.authentic(installed, proof))


def complete(m, folder, proof):
    try:
        if not bundle_admission.local_workflow(m) or proof.get('ok') is not True:
            return False
        installed = SimpleNamespace(folder=folder)
        if not _matches(installed, proof, _identity(m, folder)):
            return False
        members = _members(m)
        rows = proof['cases']
        return (len(rows) == len(members) and [row['member'] for row in rows] == members
                and all(row.get('ok') is True and row.get('reopened') == row['saved']
                        and row.get('controls') and row.get('toolSha256') for row in rows))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, envs.Failed):
        return False


class Observation:
    """One real opened worker, with its input captured before any interaction."""
    def __init__(self, worker, saved, *, stopping=lambda:False):
        self.worker, self.saved = worker, saved
        self.prepared = worker.prepared
        self.m, self.folder = self.prepared.m.owner, self.prepared.folder
        self.installed = SimpleNamespace(folder=self.folder)
        self.identity = _identity(self.m, self.folder)
        self.key = self.prepared.m.key
        self.reopen_attempted = False
        self.stopping = stopping

    def _current(self):
        if self.stopping():
            raise ValueError('This local workflow observation was stopped')
        self.worker._allowed()
        if self.identity != _identity(self.m, self.folder):
            raise ValueError('The tool or host changed during this local workflow; review a fresh session')

    def _record(self):
        for suffix in ('.proof.json', '.proof-attempt.json'):
            proof = _read(self.folder, suffix)
            if _matches(self.installed, proof, self.identity):
                return proof
        return {'scope': FORMAT, 'fullQualification': False, **self.identity, 'ok': False, 'cases': []}

    def saved_tool(self, result, history):
        """Called only after the owner's Save tool has a confirmed Project receipt."""
        from . import env_runner, own_trials
        self._current()
        if history.pending is not None or history.record is None:
            return
        actions = history.document['state']['actions']
        controls = [row['id'] for row in actions if row['state'] == 'completed'
                    and (row['request'].get('op') == 'native-panel-change'
                         or row['request'].get('op') == 'change' and row['request'].get('request', {}).get('operation') == 'controls')]
        if not controls:
            return
        view = result.document['state'].get('scientificView', {})
        expected = {'bundle': self.m.key, 'generation': self.folder.name,
                    'operation': self.prepared.m.member.id}
        producer = result.document['state'].get('hookeOutput', {}).get('producer', {})
        if (view.get('runtime') != expected or not view.get('tool') or not view.get('layers')
                or producer.get('history') != _reference(history.record)
                or result.document['state']['hookeOutput'].get('base') != _reference(self.saved)):
            raise ValueError('The saved tool does not match this completed local workflow')
        env_runner.preserve_active(self.m, self.worker.environ)
        own_trials.await_comparison(self.m.key, self.worker.environ, review_kind='local-workflow')
        with _locked(self.installed):
            proof = self._record()
            if any(row['member'] == self.key and row.get('ok') for row in proof['cases']):
                return  # a later use never silently changes an accepted evidence identity
            case = hashlib.sha256(bundle_review._json({'member':self.key, 'saved':_reference(result)})).hexdigest()
            output = {'saved':_reference(result), 'view':view, 'resources':result.document['resources']}
            artifacts = bundle_review.store(self.installed, case,
                bundle_review._json({'input':_reference(self.saved), 'resources':self.saved.document['resources']}),
                bundle_review._json(output), {'state':'saved; reopen this exact result to complete the local observation'},
                'workflow', input_kind='json')
            row = {'case':case, 'member':self.key, 'source':'Actual local controls, Save tool and reopen; no scientific oracle',
                   'ok':False, 'input':_reference(self.saved), 'saved':_reference(result),
                   'history':_reference(history.record), 'controls':controls,
                   'toolSha256':hashlib.sha256(bundle_review._json(view['tool'])).hexdigest(), 'artifacts':artifacts}
            proof['cases'] = [item for item in proof['cases'] if item['member'] != self.key] + [row]
            proof.pop('issuerSeal', None)
            self._current()
            _write(self.folder, '.proof-attempt.json', reader_proof.seal(self.installed, proof))

    def rendered(self, history):
        """First render after a fresh restore; no intervening local controls or code."""
        if self.reopen_attempted:
            return
        self.reopen_attempted = True
        if history.document['state']['actions'] or history.document['state']['cells']:
            return
        view = self.saved.document['state'].get('scientificView', {})
        if self.worker.retained_tool is None or self.worker.retained_tool != view.get('tool'):
            return
        self._current()
        with _locked(self.installed):
            proof = self._record()
            row = next((item for item in proof['cases'] if item['member'] == self.key
                        and item['saved'] == _reference(self.saved) and not item.get('ok')), None)
            if row is None:
                return
            if row['toolSha256'] != hashlib.sha256(bundle_review._json(view['tool'])).hexdigest():
                raise ValueError('Reopened tool state differs from the saved local observation')
            output = {'reopened':_reference(self.saved), 'toolSha256':row['toolSha256'],
                      'observed':'restored exact retained layers and tool state, then rendered before interaction'}
            row['artifacts'] = bundle_review.store(self.installed, row['case'],
                bundle_review.read(self.installed, row['artifacts']['input']),
                bundle_review.read(self.installed, row['artifacts']['reference']), output, 'workflow', input_kind='json')
            row.update(ok=True, reopened=_reference(self.saved))
            members = _members(self.m)
            proof['cases'].sort(key=lambda item:members.index(item['member']))
            proof['ok'] = [item['member'] for item in proof['cases']] == members and all(item['ok'] for item in proof['cases'])
            proof.update(says=WORDS, at=time.strftime('%Y-%m-%dT%H:%M:%S'))
            proof.pop('issuerSeal', None)
            self._current()
            _write(self.folder, '.proof.json' if proof['ok'] else '.proof-attempt.json', reader_proof.seal(self.installed, proof))


def observe(worker, saved, *, stopping=lambda:False):
    from .scientific_host import ReviewWorker
    if not isinstance(worker, ReviewWorker):
        return None
    if not bundle_admission.local_workflow(worker.prepared.m.owner):
        return None
    return Observation(worker, saved, stopping=stopping)
