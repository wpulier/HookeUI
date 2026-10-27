"""Content identity shared by discovery and the adapter that actually executes an entry."""
from __future__ import annotations

import hashlib


def generation_of(m) -> str:
    """A capability's exact generation: its pinned files' revision and a digest of everything in its entry folder (the
    manifest, its adapter, its environment lock, its tests and examples), so a change to any code that runs makes a
    new generation. The pinned weights are named by the revision, never read here."""
    from gpu_worker import manifests as catalog
    folder = catalog.entry_folder(m)
    digest = hashlib.sha256()
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        rel = path.relative_to(folder)
        if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            continue  # caches and system files aren't the entry
        digest.update(rel.as_posix().encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return f"{catalog.short_revision(m)}.{digest.hexdigest()[:16]}"


def check_generation(m, params: dict) -> dict:
    """Bind the selected installed entry before its code runs. Unconstrained local calls keep their old path."""
    expected = params.get("expectedGeneration")
    if expected is None:
        return {}
    from .operations import Unavailable

    actual = generation_of(m)
    if actual != expected:
        raise Unavailable("The installed capability generation changed after admission; nothing ran.",
                          next="review the current tool and submit a new job")
    return {"entryGeneration": actual}


def supports_qualification(runner: str) -> bool:
    """Only adapters that check the selected generation before invoking its code."""
    return runner in ("python-env", "fiji")


def environment_of(installed, environ=None) -> dict:
    """The selected prepared environment, including its lock/proof receipt, never the mutable current pointer."""
    import json
    import os
    from . import envs, fiji_generations
    environ = os.environ if environ is None else environ
    owner = installed.owner if hasattr(installed, 'owner') else installed
    if owner.m.runner == 'python-env':
        receipt = json.loads((owner.folder / envs.RECEIPT).read_text())
        generation = owner.folder.name
        if not receipt.get('lockSha256') or receipt.get('generation') != generation:
            raise ValueError('the selected Python environment has no matching generation/lock receipt')
    elif owner.m.runner == 'fiji' and not owner.m.namespace:
        receipt = fiji_generations.receipt(owner.n, environ)
        generation = f'fiji-{owner.n}'
        if not receipt:
            raise ValueError('the selected Fiji environment has no receipt')
    else:
        raise ValueError('this runner cannot bind a qualified environment')
    return {'runner': owner.m.runner, 'generation': generation,
            'receiptSha256': hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()}


def check_environment(installed, params: dict) -> dict:
    import json
    expected = params.get('expectedEnvironment')
    if isinstance(expected, str):
        expected = json.loads(expected)
    if expected is None:
        return {}
    from .operations import Unavailable
    try:
        actual = environment_of(installed)
    except (OSError, ValueError, TypeError) as error:
        raise Unavailable('The qualified runtime identity is unavailable; nothing ran.',
                          next='restore the qualified environment') from error
    if actual != expected:
        raise Unavailable('The selected runtime changed after admission; nothing ran.',
                          next='review the current tool and submit a new job')
    return {'environment': actual}
