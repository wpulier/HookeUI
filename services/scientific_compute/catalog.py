"""Metadata-only, allowlisted bindings for bundled matrix capabilities."""
from __future__ import annotations

import hashlib
from dataclasses import asdict
from pathlib import Path
import os
import json

IDS = ('matrix-inspect', 'matrix-prepare', 'response-fit', 'response-predict', 'matrix-evaluate', 'response-combine', 'matrix-export', 'matrix-evaluate-cell', 'aggregate-profile-prepare', 'response-effects-merge', 'response-effect-evaluate')
BASE = Path(__file__).resolve().parents[2]


RUNTIME_ID = 'matrix-cpu-runtime'


def runtime(capability, environ=None):
    from gpu_worker import manifests
    from orchestrator import envs
    environ = os.environ if environ is None else environ
    owner_id = 'cell-evaluation-runtime' if capability == 'matrix-evaluate-cell' else RUNTIME_ID
    owner = manifests.manifests()[owner_id]
    lock_hash = hashlib.sha256(envs.lock_file(owner, environ).read_bytes()).hexdigest()
    generation = envs._generation_from_files(owner, environ)
    folder = envs.root(owner, environ) / generation
    result = {'owner': owner_id, 'generation': generation, 'lockSha256': lock_hash,
              'python': owner.env['python'], 'backend': 'cpu', 'memoryLimitBytes': 24 << 30,
              'memoryBoundary': 'observed-process-tree-rss', 'denseWorkingLimitBytes': 512 << 20,
              'prepared': False, 'reason': f'Install {owner.name} from Tools'}
    try:
        receipt_path = folder / envs.RECEIPT
        if folder.is_symlink() or folder.parent.is_symlink() or receipt_path.stat().st_size > 1 << 20:
            return result
        receipt = json.loads(receipt_path.read_text())
        if (receipt.get('model') != owner_id or receipt.get('generation') != generation
                or receipt.get('lockSha256') != lock_hash or receipt.get('python') != owner.env['python']
                or receipt.get('imports') != owner.env['imports'] or not (folder / 'bin/python').is_file()):
            return result
        return {**result, 'prepared': True, 'reason': '',
                'receiptSha256': hashlib.sha256(receipt_path.read_bytes()).hexdigest()}
    except (OSError, ValueError, TypeError):
        return result


def execution_runtime(expected, environ=None):
    from gpu_worker import manifests
    from orchestrator import envs, interpreter_identity
    environ = os.environ if environ is None else environ
    capability = 'matrix-evaluate-cell' if expected.get('owner') == 'cell-evaluation-runtime' else ''
    actual = runtime(capability, environ)
    if not actual['prepared'] or actual != expected:
        raise ValueError('The prepared scientific runtime changed; admit a new attempt')
    owner = manifests.manifests()[actual['owner']]
    folder = envs.root(owner, environ) / actual['generation']
    receipt = json.loads((folder / envs.RECEIPT).read_text())
    executable = folder / 'bin/python'
    interpreter_identity.verify(executable, receipt['interpreter'])
    return executable, folder


def descriptor(capability, environ=None):
    from gpu_worker import manifests
    if capability not in IDS:
        raise ValueError('Unknown bundled matrix capability')
    m = manifests.manifests().get(capability)
    if m is None or m.runner != 'builtin-cpu':
        raise ValueError('The bundled matrix capability declaration is unavailable')
    current = runtime(capability, environ)
    digest = hashlib.sha256()
    for path in (BASE / 'catalog' / 'models' / capability / 'model.toml',
                 Path(__file__).with_name('engine.py'), Path(__file__).with_name('cell_evaluation.py'), Path(__file__).with_name('worker.py'),
                 Path(__file__).with_name('reservation.py'), Path(__file__).with_name('aggregate_profiles.py'),
                 Path(__file__).with_name('bounded_h5.py'), Path(__file__).with_name('effect_evaluation.py'),
                 BASE / 'services' / 'orchestrator' / 'matrix_capability_runs.py', Path(__file__)):
        digest.update(path.name.encode() + b'\0' + path.read_bytes())
    digest.update(json.dumps(current, sort_keys=True).encode())
    reason = current['reason']
    return {'id': capability, 'capability': capability, 'name': m.name,
            'generation': 'matrix-v1.' + digest.hexdigest(), 'inputs': [{'id': p.id, 'type': p.type} for p in m.data_inputs],
            'outputs': [{'id': p.id, 'type': p.type} for p in m.outputs],
            'parameters': [asdict(p) for p in m.params],
            'parameterSchema': {'type': 'object', 'additionalProperties': False, 'properties': {p.id: {
                'type': {'int': 'integer', 'number': 'number', 'string': 'string', 'choice': 'string', 'bool': 'boolean'}[p.type],
                'default': p.default, **({'enum': list(p.options)} if p.options else {}),
                **({'minimum': p.min} if p.min is not None else {}), **({'maximum': p.max} if p.max is not None else {})
            } for p in m.params}, 'required': [p.id for p in m.params if p.default is None and not p.optional]},
            'ready': not reason, 'reason': reason, 'runtime': current,
            'setup': {'model': current['owner'], 'command': 'model-install'}}


def descriptors(environ=None):
    return [{key: value for key, value in descriptor(name, environ).items() if key != 'manifest'} for name in IDS]


def parameters(capability, supplied):
    from gpu_worker import manifests
    m = manifests.manifests()[capability]
    values, problems = manifests.params_given(m, supplied, agent=True)
    if problems:
        raise ValueError('; '.join(problems))
    return values
