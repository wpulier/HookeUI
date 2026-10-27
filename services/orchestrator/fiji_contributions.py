"""Immutable contributed Fiji candidates with receipts separate from reviewed generations."""
from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from gpu_worker import manifests as catalog
from . import bounded_files, channels, fiji_generations as generations, fiji_sandbox, owned_process

MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 10 * 1024 * 1024
LOCK = threading.RLock()
INSTALL_LOCK = threading.Lock()
_LEASES: dict[tuple[str, int], int] = {}


@dataclass(frozen=True)
class Installed:
    m: catalog.Manifest
    proof: dict
    n: int
    folder: Path
    current: bool


def root(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> Path:
    return catalog.catalog_root(environ) / 'fiji-capabilities' / hashlib.sha256(m.key.encode()).hexdigest()


def files(m: catalog.Manifest) -> dict[str, bytes]:
    from .share import ATTRIBUTION

    folder = catalog.entry_folder(m)
    script = m.fiji.get('script', '')
    names = {catalog.MANIFEST, 'README.md', *[str(t['input']) for t in m.tests if t.get('input')]}
    if script:
        names.add(script)
    if m.example:
        names.add(m.example)
    if any(Path(name).name != name or name.startswith('.') for name in names):
        raise generations.Failed('A portable Fiji capability keeps its script and examples beside its manifest')
    try:
        directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            present = os.listdir(directory)
            if any(name.lower().endswith(('.jar', '.class', '.dylib', '.so')) for name in present):
                raise generations.Failed('A Fiji capability cannot install plugins or native code; use staged Fiji setup')
            names.update(name for name in present if name.lower() in ATTRIBUTION)
            out = {name: bounded_files.read_at(directory, name, MAX_FILE) for name in sorted(names)
                   if name in present}
        finally:
            os.close(directory)
    except (OSError, ValueError) as exc:
        raise generations.Failed('The Fiji capability contains an unreadable, linked or oversized file') from exc
    required = {catalog.MANIFEST, *[str(t['input']) for t in m.tests if t.get('input')]}
    if script:
        required.add(script)
    if m.example:
        required.add(m.example)
    if not required <= out.keys() or sum(map(len, out.values())) > MAX_TOTAL:
        raise generations.Failed('The Fiji capability is incomplete or exceeds the portable package limit')
    return out


def digest(content: dict[str, bytes]) -> str:
    return hashlib.sha256(json.dumps({name: hashlib.sha256(data).hexdigest() for name, data in content.items()},
                                     sort_keys=True).encode()).hexdigest()


def runtime_identity(n: int, environ: Mapping[str, str] = os.environ) -> str:
    receipt = generations.receipt(n, environ)
    if not receipt or not generations.fiji_dir(n, environ).is_dir():
        raise generations.Failed('The pinned Fiji generation is no longer installed')
    return hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()


def _json(path: Path) -> dict:
    try:
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            value = json.loads(bounded_files.read_at(directory, path.name, 1024 * 1024))
        finally:
            os.close(directory)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, value: dict) -> None:
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, sort_keys=True))
    tmp.replace(path)


def _stamp(folder: Path) -> tuple:
    paths = [folder, *sorted(folder.iterdir())]
    return tuple((str(p), st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino)
                 for p in paths for st in [p.lstat()])


@functools.lru_cache(maxsize=128)
def _content(folder: str, namespace: str, channel: str, stamp: tuple) -> tuple[catalog.Manifest, str]:
    m, problems = catalog.read(Path(folder) / catalog.MANIFEST, namespace, channel)
    if m is None or problems:
        raise generations.Failed('; '.join(problems) or 'The contributed manifest is invalid')
    return m, digest(files(m))


def content(m: catalog.Manifest) -> tuple[catalog.Manifest, str]:
    return _content(str(m.folder), m.namespace, m.channel, _stamp(Path(m.folder)))


def turned_off(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> bool:
    return (root(m, environ) / 'off.json').exists()


def turn_off(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> None:
    with owned_process.locked(LOCK):
        base = root(m, environ)
        base.mkdir(parents=True, exist_ok=True)
        _write(base / 'off.json', {'at': time.time(), 'request': uuid.uuid4().hex})


def _installed(m: catalog.Manifest, name: str, environ: Mapping[str, str]) -> Installed | None:
    if not isinstance(name, str) or not re.fullmatch('[a-f0-9]{64}', name):
        return None
    folder = root(m, environ) / name
    proof = _json(folder / 'proof.json')
    n = proof.get('generation')
    if proof.get('passed') is not True or type(n) is not int or n < 1 or folder.is_symlink():
        return None
    try:
        if proof.get('boundary') != fiji_sandbox.identity() or proof.get('runtime') != runtime_identity(n, environ):
            return None
        snapshot = folder / 'entry' / m.id
        kept, sha = _content(str(snapshot), m.namespace, m.channel, _stamp(snapshot))
        if kept.key != m.key or kept.runner != 'fiji' or sha != proof.get('content'):
            return None
    except (OSError, ValueError, generations.Failed):
        return None
    try:
        current = content(m)[1] == sha and generations.current(environ) == n
    except (OSError, ValueError, generations.Failed):
        current = False
    return Installed(kept, proof, n, folder, current)


def in_use(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> Installed | None:
    if not m.namespace or not channels.trusted(m, environ) or not fiji_sandbox.available() or turned_off(m, environ):
        return None
    return _installed(m, _json(root(m, environ) / 'active.json').get('candidate', ''), environ)


def install(m: catalog.Manifest, environ: Mapping[str, str] = os.environ, *, say=lambda words: None,
            stop=lambda: False) -> tuple[Path, list[str]]:
    from . import fiji_runner

    if not m.namespace or m.runner != 'fiji' or not m.fiji:
        raise generations.Failed('This is not a contributed Fiji capability')
    if not channels.trusted(m, environ):
        raise generations.Failed('Allow this contributor before checking its Fiji capability')
    if not fiji_sandbox.supported():
        raise generations.Failed('This Mac cannot establish the Fiji sandbox; nothing was run')
    n = generations.current(environ)
    if n is None:
        raise generations.Failed('Set up the managed Fiji before checking this capability')
    off_before = _json(root(m, environ) / 'off.json')
    with owned_process.scope(seconds=generations.STEP_S, stop=stop) as bound, owned_process.locked(INSTALL_LOCK), retain(n, environ):
        content = files(m)
        identity = {'content': digest(content), 'runtime': runtime_identity(n, environ),
                    'boundary': fiji_sandbox.identity(), 'generation': n}
        candidate = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        base = root(m, environ)
        base.mkdir(parents=True, exist_ok=True)
        destination = base / candidate
        try:
            with tempfile.TemporaryDirectory(prefix='.candidate-', dir=base) as tmp:
                folder = Path(tmp) / 'entry' / m.id
                folder.mkdir(parents=True)
                for name, data in content.items():
                    target = folder / name
                    target.write_bytes(data)
                    target.chmod(0o444)
                kept, problems = catalog.read(folder / catalog.MANIFEST, m.namespace, m.channel)
                if kept is None or problems:
                    raise generations.Failed('; '.join(problems) or 'The contributed manifest is invalid')
                say(f'Checking {m.name} in its own sandbox on Fiji generation {n}')
                result = fiji_runner.proof(kept, generations.fiji_dir(n, environ), environ)
                bound.check()
                if result.get('passed') is not True:
                    raise generations.Failed('The confined Fiji proof did not pass')
                if digest(files(kept)) != identity['content'] or digest(files(m)) != identity['content']:
                    raise generations.Failed('The capability changed during its check; retry')
                if runtime_identity(n, environ) != identity['runtime'] or fiji_sandbox.identity() != identity['boundary']:
                    raise generations.Failed('Fiji or its execution boundary changed during this check; retry')
                receipt = {**result, **identity, 'passed': True, 'at': time.time()}
                _write(Path(tmp) / 'proof.json', receipt)
                if not destination.exists():
                    Path(tmp).rename(destination)
                else:
                    previous = _installed(m, candidate, environ)
                    if previous is None or any(previous.proof.get(key) != value for key, value in identity.items()):
                        raise generations.Failed('An incompatible candidate already occupies this identity')
                bound.check()
                if not channels.trusted(m, environ):
                    raise generations.Failed('Permission was withdrawn during this check; nothing was enabled')
                with owned_process.locked(LOCK):
                    bound.check()
                    if _json(base / 'off.json') != off_before:
                        raise generations.Failed('This capability was turned off during setup; turn it on again to continue')
                    _write(base / 'active.json', {'candidate': candidate})
                    (base / 'off.json').unlink(missing_ok=True)
        except Exception as exc:
            _write(base / 'last-failure.json', {'candidate': candidate, 'at': time.time(), 'why': str(exc)[:1000]})
            raise
        return destination, [f'Checked in a private JVM on Fiji generation {n}', result['summary']]


@contextmanager
def retain(n: int, environ: Mapping[str, str] = os.environ):
    key = (str(catalog.catalog_root(environ)), n)
    with owned_process.locked(LOCK):
        runtime_identity(n, environ)
        _LEASES[key] = _LEASES.get(key, 0) + 1
    try:
        yield
    finally:
        with LOCK:
            if _LEASES[key] == 1:
                del _LEASES[key]
            else:
                _LEASES[key] -= 1


def retained_generations(environ: Mapping[str, str] = os.environ) -> set[int]:
    with LOCK:
        out = {n for (catalog_root, n), count in _LEASES.items()
               if count and catalog_root == str(catalog.catalog_root(environ))}
    base = catalog.catalog_root(environ) / 'fiji-capabilities'
    for active in base.glob('*/active.json'):
        name = _json(active).get('candidate', '')
        if isinstance(name, str) and re.fullmatch('[a-f0-9]{64}', name):
            proof = _json(active.parent / name / 'proof.json')
            if proof.get('passed') is True and type(proof.get('generation')) is int:
                out.add(proof['generation'])
    return out
