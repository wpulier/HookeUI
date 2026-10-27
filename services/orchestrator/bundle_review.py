"""Retained reference evidence and explicit acceptance of one proven bundle generation."""
from __future__ import annotations

from contextlib import contextmanager
import copy
from .file_locks import fcntl  # the standard module, or Windows' refusing stand-in
import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from pathlib import Path

MAX_FILE = 8 << 20
MAX_GENERATION = 64 << 20
MAX_FILES = 4096
SHA = re.compile(r'[0-9a-f]{64}')


def descriptors_valid(value):
    return (isinstance(value, dict) and set(value) == {'input', 'reference', 'actual'}
            and all(isinstance(item, dict) and set(item) == {'kind', 'sha256', 'bytes'}
                    and item['kind'] in ('image', 'json')
                    and isinstance(item['sha256'], str) and SHA.fullmatch(item['sha256'])
                    and type(item['bytes']) is int and 0 < item['bytes'] <= MAX_FILE
                    for item in value.values()))


def folder(installed):
    return installed.folder.with_name(installed.folder.name + '.review')


@contextmanager
def _directory(installed, *, create=False):
    path = folder(installed)
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield fd
    finally:
        os.close(fd)


def _read_at(fd, descriptor):
    if not descriptors_valid({name: descriptor for name in ('input', 'reference', 'actual')}):
        raise ValueError('The saved reference evidence descriptor is invalid; run the checks again')
    source = os.open(descriptor['sha256'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(source, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != descriptor['bytes']:
            raise ValueError('The saved reference evidence changed; run the checks again')
        body = stream.read(MAX_FILE + 1)
    if len(body) != descriptor['bytes'] or hashlib.sha256(body).hexdigest() != descriptor['sha256']:
        raise ValueError('The saved reference evidence changed; run the checks again')
    return body


def read(installed, descriptor):
    try:
        with _directory(installed) as fd:
            return _read_at(fd, descriptor)
    except OSError as exc:
        raise ValueError('The saved reference evidence is unavailable; run the checks again') from exc


def _json(value):
    body = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(body) > MAX_FILE:
        raise ValueError('The reference evidence exceeds the 8 MiB file limit')
    return body


def store(installed, case_key, input_bytes, reference_bytes, actual: dict, output_type: str, *, input_kind='image'):
    if not isinstance(case_key, str) or not SHA.fullmatch(case_key):
        raise ValueError('The reference check identity is invalid')
    image = output_type == 'image.2d.rgb8'
    if image:
        import numpy as np
        from PIL import Image
        picture = actual.get('picture')
        if (not isinstance(picture, np.ndarray) or picture.dtype != np.uint8 or picture.ndim != 3
                or picture.shape[2] != 3 or not 0 < picture.shape[0] * picture.shape[1] <= 1 << 20):
            raise ValueError('The reference result must be a bounded RGB8 picture')
        encoded = io.BytesIO()
        Image.fromarray(picture).save(encoded, format='PNG')
        actual_bytes = encoded.getvalue()
    else:
        actual_bytes = _json(actual)
    data = {'input': (input_kind, input_bytes), 'reference': ('image' if image else 'json', reference_bytes),
            'actual': ('image' if image else 'json', actual_bytes)}
    if any(not isinstance(body, bytes) or not 0 < len(body) <= MAX_FILE for _, body in data.values()):
        raise ValueError('The reference evidence exceeds the 8 MiB file limit')
    descriptors = {name: {'kind': kind, 'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body)}
                   for name, (kind, body) in data.items()}
    with _directory(installed, create=True) as fd:
        lock = os.open('.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
        try:
            if not stat.S_ISREG(os.fstat(lock).st_mode):
                raise ValueError('The reference evidence store is not a regular directory')
            fcntl.flock(lock, fcntl.LOCK_EX)
            entries = os.listdir(fd)
            if len(entries) > MAX_FILES:
                raise ValueError('The saved reference evidence has too many files')
            total = 0
            for name in entries:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError('The saved reference evidence contains an unsafe file')
                total += info.st_size
            missing, repair = {}, set()
            for name, (_, body) in data.items():
                descriptor = descriptors[name]
                if descriptor['sha256'] in entries:
                    try:
                        _read_at(fd, descriptor)
                    except ValueError:
                        missing[descriptor['sha256']] = body
                        repair.add(descriptor['sha256'])
                else:
                    missing[descriptor['sha256']] = body
            additional = sum(map(len, missing.values()))
            if total + additional > MAX_GENERATION or len(entries) + len(missing) > MAX_FILES:
                raise ValueError('The saved reference evidence reached its 64 MiB generation limit')
            from .downloads import SPARE
            if shutil.disk_usage(folder(installed)).free < additional + SPARE:
                raise ValueError('There is not enough free space to retain the reference evidence')
            for digest, body in missing.items():
                temporary = '.pending-' + uuid.uuid4().hex
                target = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                try:
                    with os.fdopen(target, 'wb') as stream:
                        stream.write(body)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if digest in repair:
                        os.replace(temporary, digest, src_dir_fd=fd, dst_dir_fd=fd)
                    else:
                        os.link(temporary, digest, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=fd)
                    except FileNotFoundError:
                        pass
        finally:
            os.close(lock)
    return descriptors


def identity(installed):
    proof = installed.proof
    value = {'generation': installed.folder.name,
             **{key: proof.get(key) for key in ('scope', 'boundary', 'workerSha256', 'deviceBoundary', 'cases', 'examples')}}
    if proof.get('scope') == 'reader-data-contract-v1':
        value.update({key: proof.get(key) for key in ('readerWorkerSha256', 'preparationSha256', 'fullQualification')})
    if proof.get('scope') == 'local-interactive-workflow-v1':
        value.update({key: proof.get(key) for key in ('workflowHostSha256', 'preparationSha256', 'declarationSha256', 'fullQualification')})
    return hashlib.sha256(_json(value)).hexdigest()


def projection(installed):
    from . import bundle_runtime
    if not bundle_runtime.proof_complete(installed.m, installed.folder, installed.proof):
        raise ValueError('This package needs complete reference checks before review')
    cases = []
    reader = installed.proof.get('scope') == 'reader-data-contract-v1'
    workflow = installed.proof.get('scope') == 'local-interactive-workflow-v1'
    for case in installed.proof['cases']:
        if not descriptors_valid(case.get('artifacts')):
            raise ValueError('This package needs retained reference evidence; run the checks again')
        for descriptor in case['artifacts'].values():
            read(installed, descriptor)
        cases.append(copy.deepcopy({key: case[key] for key in ('case', 'member', 'operation', 'device', 'parameters',
                                                              'source', 'tolerance', 'metrics', 'artifacts') if key in case}))
        if reader:
            cases[-1]['kind'] = 'reader'
            cases[-1]['inputs'] = copy.deepcopy(case['inputs'])
            cases[-1]['referenceSha256'] = case['referenceSha256']
        if workflow:
            cases[-1].update(kind='workflow', inputs=copy.deepcopy(case['input']),
                            saved=copy.deepcopy(case['saved']), reopened=copy.deepcopy(case['reopened']))
    return {'generation': installed.folder.name, 'digest': identity(installed), 'cases': cases,
            'says': ('Local controls, Save tool and reopen were observed. Review the retained input, saved state and reopened result before enabling. Scientific accuracy and export fidelity remain separate.' if workflow else
                     'These checks compare the package with its declared references. Review the examples before enabling it; scientific suitability remains yours to assess.')}


def accept(model, generation, digest, environ=os.environ, still_current=lambda: True):
    from gpu_worker import manifests as catalog
    from . import env_runner, own_trials
    if (not isinstance(model, str) or not re.fullmatch(r'(?:[a-z0-9_-]+/)?[a-z0-9_-]+', model)
            or len(model) > 256 or not isinstance(generation, str) or not re.fullmatch(r'[0-9a-f]{16}', generation)
            or not isinstance(digest, str) or not SHA.fullmatch(digest)):
        raise ValueError('The package or checked version is invalid or changed; reopen its result review')
    owner = catalog.get(model) or catalog.mine(environ).get(model)
    if owner is None or not owner.members or model != owner.key:
        raise ValueError('Choose a package with completed reference checks')
    installed = env_runner.in_use(owner, environ)
    if installed is not None and installed.folder.name != generation:
        from .maintenance import workflow
        return workflow.accept(owner, generation, digest, environ, still_current)
    if installed is None:
        raise ValueError('Complete the package checks before enabling it')
    reviewed = projection(installed)
    if generation != reviewed['generation'] or digest != reviewed['digest']:
        raise ValueError('The package or reference results changed; review them again')
    current = env_runner.in_use(owner, environ)
    if (current is None or current.folder != installed.folder or identity(current) != digest
            or not still_current()):
        raise ValueError('The session or package changed; review it again before enabling it')
    path = own_trials.marker(model, environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        previous = json.loads(path.read_text())
    except (OSError, ValueError):
        previous = {}
    record = {'model': model, 'trial': previous.get('trial'), 'adoptedDigest': digest,
              'reviewKind': 'local-workflow' if installed.proof.get('scope') == 'local-interactive-workflow-v1' else 'bundle-reference', 'generation': generation}
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(record, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        if not still_current():
            raise ValueError('The session changed; review it again before enabling it')
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {'model': model, 'generation': generation,
            'says': f'{installed.m.name} is now available. Its results remain yours to judge.'}
