"""Observed pinning for the existing GPU worker, without model-specific adapters.

A prepared image supplies installed packages and pinned catalog weights. Admission
hashes the actual files before starting its original GpuClient process. The worker
uses the same interpreter and explicit catalog model, never an alternate package.
"""
import hashlib
import importlib.metadata
import platform
import json

from orchestrator.scientific_projects import InvalidProject as CloudRefused


def observe(manifest, *, environ=None):
    import os
    from gpu_worker import model_files
    from orchestrator.capability_identity import generation_of
    environ = os.environ if environ is None else environ
    if manifest.runner != 'gpu-worker' or model_files.standin(environ):
        raise CloudRefused('The admitted GPU runtime requires actual pinned model files')
    weights = []
    for item in model_files.files(manifest):
        if not item.get('sha256'):
            raise CloudRefused('The GPU qualification requires SHA256-pinned model files')
        path = model_files.model_file(manifest.key, item['path'], environ)
        sha, count = hashlib.sha256(), 0
        with path.open('rb') as stream:
            while block := stream.read(4 << 20):
                sha.update(block); count += len(block)
        if sha.hexdigest() != item['sha256'] or count != item['bytes']:
            raise CloudRefused('The model weights differ from the catalog admission')
        weights.append({'path': item['path'], 'sha256': sha.hexdigest(), 'size': count})
    packages = sorted((dist.metadata['Name'], dist.version) for dist in importlib.metadata.distributions()
                      if dist.metadata['Name'])
    environment = {'runner': 'gpu-worker', 'python': platform.python_version(),
                   'packagesSha256': hashlib.sha256(json.dumps(packages, sort_keys=True, separators=(',', ':')).encode()).hexdigest(), 'weights': weights}
    return generation_of(manifest), environment


async def prepare(host, manifest, generation, environment):
    import asyncio
    from orchestrator.gpu_client import GpuClient
    actual, observed = await asyncio.to_thread(observe, manifest)
    if actual != generation or observed != environment:
        raise CloudRefused('The prepared GPU runtime differs from the admitted generation or environment')
    client = GpuClient('micromind-gpu-heavy', warm=False)
    try:
        await client.ensure_started()
        if not str(client.device).startswith('cuda'):
            raise CloudRefused('The admitted GPU job did not observe a CUDA device; no CPU substitution')
    except BaseException:
        await asyncio.to_thread(client.stop)
        raise
    host.gpu_heavy = client
    return client
