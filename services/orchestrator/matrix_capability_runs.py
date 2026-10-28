"""One catalog operation over exact retained Project files, shared by every caller.

The caller owns its workflow attempt or door operation. This adapter never queues,
replays or creates an execution ledger. A completed retry only opens the original
immutable output. A confined process receives only materialized authorized inputs.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from scientific_compute.catalog import IDS, descriptor, descriptors, parameters, execution_runtime
from scientific_compute.reservation import heavy_work
from .scientific_file_intake import FileIntake, request_identity
from .scientific_files import CHUNK_BYTES
from .scientific_projects import InvalidProject, Conflict, _json
from . import sandbox, worker_resources, generation_leases

MAX_INPUT_BYTES = 64 << 30
MAX_RECEIPT_BYTES = 2 << 20


def _input(store, session, ref, check):
    if type(ref) is not dict or not {'id', 'revision', 'digest'} <= set(ref) or set(ref) - {'id', 'session', 'revision', 'digest', 'component'}:
        raise InvalidProject('Choose an exact retained file revision, not a host path')
    if ref.get('session', session) != session:
        raise InvalidProject('This scientific input belongs to another Project')
    saved = store.read(ref['id'], session, revision=ref['revision'])
    if saved.digest != ref['digest']:
        raise InvalidProject('The selected scientific revision digest does not match')
    files = [r for r in saved.document['resources'] if r.get('id') == saved.id and r.get('session') == session
             and r.get('component', '').startswith('file.')]
    if 'component' in ref:
        files = [r for r in files if r['component'] == ref['component']]
    else:
        declared = saved.document['state'].get('sourceFile', {}).get('reference')
        if declared is not None:
            files = [r for r in files if r == declared]
    if len(files) != 1:
        raise InvalidProject('Choose one exact file component from this scientific revision')
    source = store.file_source(saved.id, session, saved.revision, files[0], check=check, verify_content=False)
    if source.size > MAX_INPUT_BYTES:
        raise InvalidProject('The retained file exceeds the admitted input size')
    exact = {'id': saved.id, 'session': session, 'revision': saved.revision, 'digest': saved.digest,
             'component': files[0]['component'], 'sha256': source.sha256, 'bytes': source.size}
    return source, exact


class _Output(FileIntake):
    def __init__(self, *args, invocation, summary, **kwargs):
        self.invocation, self.summary = invocation, summary
        super().__init__(*args, **kwargs)

    def document(self, ref):
        doc = super().document(ref)
        doc['kind'] = 'matrix-result'
        doc['state']['matrixOperation'] = {**self.invocation, 'output': {'reference': ref, 'summary': self.summary}}
        return doc


def _compute(capability, sources, params, work, check, executable, runtime_folder):
    if not sandbox.available():
        raise InvalidProject('The confined scientific process is unavailable on this computer; nothing ran')
    inputs = work / 'inputs'; outputs = work / 'outputs'
    inputs.mkdir(); outputs.mkdir()
    resolved = {}
    for port, source in sources.items():
        destination = inputs / f'{port}{Path(source.name).suffix}'
        digest = hashlib.sha256()
        with destination.open('xb') as stream:
            for offset in range(0, source.size, CHUNK_BYTES):
                check(); part = source.read(offset, min(CHUNK_BYTES, source.size - offset)); digest.update(part); stream.write(part)
        if digest.hexdigest() != source.sha256:
            raise InvalidProject('The retained input content hash does not match its manifest')
        resolved[port] = str(destination)
    request = work / 'request.json'; receipt = outputs / 'receipt.json'
    request.write_bytes(_json({'capability': capability, 'inputs': resolved, 'params': params,
                              'output': str(outputs), 'receipt': str(receipt)}))
    package = Path(__file__).resolve().parents[1] / 'scientific_compute'
    # A minimal environment deliberately excludes provider keys and user-added imports.
    env = sandbox.confine({'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8',
                          'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1', 'VECLIB_MAXIMUM_THREADS': '1'}, outputs)
    cmd = sandbox.wrap([str(executable), str(package / 'worker.py'), str(request)],
                       [package, runtime_folder, sandbox.python_home(executable), inputs, request], [outputs],
                       gpu=False, children=False)
    log = outputs / 'worker.log'
    with log.open('wb') as errors:
        proc = subprocess.Popen(cmd, cwd=outputs, env=env, stdin=subprocess.DEVNULL, stdout=errors,
                                stderr=errors, close_fds=True, start_new_session=True)
        guard = worker_resources.Guard(proc, outputs, worker_resources.Limits(
            memory_bytes=24 << 30, processes=1, file_bytes=64 << 30, work_bytes=128 << 30, work_entries=100000))
        try:
            guard.start()
            while proc.poll() is None:
                guard.check()
                check()
                if log.stat().st_size > MAX_RECEIPT_BYTES:
                    raise InvalidProject('The scientific worker exceeded its diagnostic allowance')
                time.sleep(.05)
            guard.check()
            if proc.returncode != 0:
                message = log.read_bytes()[-4096:].decode('utf-8', errors='replace')
                raise InvalidProject('Scientific capability failed; no result was published: ' + message)
        except BaseException:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait()
            raise
        finally:
            guard.close()
    check()
    if not receipt.is_file() or receipt.is_symlink() or receipt.stat().st_size > MAX_RECEIPT_BYTES:
        raise InvalidProject('The scientific worker did not return a bounded result receipt')
    value = json.loads(receipt.read_text())
    if set(value) != {'file', 'summary'} or type(value['file']) is not str or Path(value['file']).name != value['file']:
        raise InvalidProject('The scientific result receipt is malformed')
    result = outputs / value['file']
    if not result.is_file() or result.is_symlink() or result.stat().st_size > MAX_INPUT_BYTES:
        raise InvalidProject('The scientific output is missing or exceeds its allowance')
    return result, value['summary']


def run_matrix(store, session, capability, generation, inputs, params, request, check=lambda: None):
    current = descriptor(capability)
    if generation != current['generation']:
        raise InvalidProject('The scientific capability generation or runtime changed; admit a new attempt')
    if not current['ready']:
        raise InvalidProject(current['reason'])
    values = parameters(capability, params)
    if type(inputs) is not dict or set(inputs) != {p['id'] for p in current['inputs']}:
        raise InvalidProject('Bind every declared scientific input exactly once')
    sources = {}; exact = {}
    for port, ref in inputs.items():
        sources[port], exact[port] = _input(store, session, ref, check)
    invocation = {'format': 1, 'capability': capability, 'generation': generation, 'runtime': current['runtime'],
                  'inputs': exact, 'params': values}
    target, _ = request_identity(session, request)
    def previous_result():
        if not store._folder(target).exists():
            return None
        previous = store.read(target, session, revision=1)
        old = previous.document['state'].get('matrixOperation', {})
        if {k: v for k, v in old.items() if k != 'output'} != invocation:
            raise Conflict('This scientific request identity belongs to different work')
        _input(store, session, {'id': previous.id, 'revision': 1, 'digest': previous.digest}, check)
        return previous
    previous = previous_result()
    if previous is not None:
        return previous
    store.root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(store.root).free < sum(v.size for v in sources.values()) * 2 + (512 << 20):
        raise InvalidProject('Insufficient disk space to materialize and retain this scientific operation')
    executable, runtime_folder = execution_runtime(current['runtime'])
    with heavy_work(f'{capability}: {request}'), generation_leases.use(runtime_folder), tempfile.TemporaryDirectory(prefix='.matrix-operation-', dir=store.root) as scratch:
        previous = previous_result()
        if previous is not None:
            return previous
        path, summary = _compute(capability, sources, values, Path(scratch), check, executable, runtime_folder)
        check()
        if descriptor(capability)['generation'] != generation:
            raise InvalidProject('Scientific code or runtime changed while executing; result not published')
        intake = _Output(store, session, request, name=path.name, size=path.stat().st_size,
                         max_bytes=MAX_INPUT_BYTES, invocation=invocation, summary=summary)
        try:
            with path.open('rb') as stream:
                while part := stream.read(CHUNK_BYTES):
                    check(); intake.write(part)
            check()
            return intake.publish(lambda: bool(check()))
        finally:
            intake.close()
