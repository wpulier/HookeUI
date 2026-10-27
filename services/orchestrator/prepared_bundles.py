"""Lease an exact prepared candidate for private review, never ordinary execution.

This is preparation evidence, not operation proof, selection or qualification. The
review host still owns resource authorization, runtime confinement and admission.
Neither browser paths nor display observations choose what executable runs.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

from gpu_worker import bundle_contract, manifests as catalog
from . import channels, env_observations, envs, environment_check, generation_leases, interpreter_identity, licences, paths, readiness

GENERATION = re.compile(r'[0-9a-f]{16}')
MAX_ENTRY_BYTES = 256 << 20
MAX_ENTRY_FILES = 16_384


class NotPrepared(ValueError):
    pass


@dataclass(frozen=True)
class PreparedMember:
    m: bundle_contract.CapabilityView
    folder: Path
    entry: Path
    python_home: Path

    @property
    def python(self):
        return self.folder / 'bin/python'


def _regular(path, limit):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit or path.is_symlink():
            raise NotPrepared('The prepared snapshot needs bounded regular files; prepare it again')
        body = stream.read(limit + 1)
    if len(body) > limit:
        raise NotPrepared('The prepared snapshot exceeds its file allowance; prepare it again')
    return body


def _tree(entry):
    count, total = 0, 0
    for parent, directories, files in os.walk(entry, followlinks=False):
        for name in [*directories, *files]:
            path = Path(parent) / name
            info = path.lstat()
            count += 1
            if count > MAX_ENTRY_FILES or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise NotPrepared('The prepared entry has unsupported files or too many entries')
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
                if info.st_size > 64 << 20 or total > MAX_ENTRY_BYTES:
                    raise NotPrepared('The prepared entry exceeds its review byte allowance')


def _receipt(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise NotPrepared('The preparation receipt repeats a field; prepare it again')
            result[key] = value
        return result
    value = json.loads(_regular(path, 64 << 10), object_pairs_hook=pairs)
    if type(value) is not dict:
        raise NotPrepared('The preparation receipt is invalid; prepare it again')
    return value


def _rights(m, environ):
    allowed = licences.EDITIONS[licences.edition()]
    for identity in licences.model_uses(m):
        if m.component and identity in (m.id, m.key):
            tier = m.component['tier']
        else:
            try:
                tier = licences.component(identity, environ).tier
            except KeyError as exc:
                raise NotPrepared('This candidate has unknown rights; review its licence declaration') from exc
        if tier not in allowed:
            raise NotPrepared('This candidate is not permitted in this edition; review its licence declaration')


def _lock(entry, name):
    if (type(name) is not str or not name or '\\' in name or '\x00' in name
            or PurePosixPath(name).is_absolute() or str(PurePosixPath(name)) != name
            or any(part in ('.', '..') for part in PurePosixPath(name).parts)):
        raise NotPrepared('The candidate lock must be a canonical file inside its prepared entry')
    return _regular(entry / name, 8 << 20)


@contextmanager
def prepared_member(key: str, generation: str, environ=os.environ):
    """Yield one snapshot member while its runtime is retained; do not activate it.

Must run off the application loop. The caller launches and reaps any review process
inside this context. No receipt, proof, choice or catalog entry is written here.
"""
    owner_key, member_id = bundle_contract.parse_capability_key(key)
    if type(generation) is not str or GENERATION.fullmatch(generation) is None:
        raise NotPrepared('Choose the exact prepared generation before reviewing this capability')
    owner = catalog.get(owner_key)
    if owner is None or owner.runner != 'python-env' or not owner.members:
        raise NotPrepared('This bundle is no longer in the catalog; add its integration before reviewing it')
    if not channels.trusted(owner, environ):
        raise NotPrepared('Allow code from this community catalog before reviewing its candidate')
    base = paths.envs(environ).resolve()
    root = envs.root(owner, environ)
    folder = root / generation
    if (root.is_symlink() or folder.is_symlink() or root.resolve().parent != base
            or folder.resolve().parent != root.resolve()):
        raise NotPrepared('The candidate must be in its own managed runtime directory; prepare it again')
    with generation_leases.use(folder):
        with env_observations.live():
            try:
                entry = folder / envs.SNAPSHOT / owner.id
                if any(path.is_symlink() for path in (folder / envs.SNAPSHOT, entry, folder / 'bin')):
                    raise NotPrepared('The prepared candidate contains redirected directories; prepare it again')
                _tree(entry)
                _regular(entry / catalog.MANIFEST, 1 << 20)
                kept, problems = catalog.read(entry / catalog.MANIFEST, owner.namespace, owner.channel)
                if kept is None or kept.key != owner_key or kept.runner != 'python-env' or not kept.members:
                    raise NotPrepared('The installed bundle declaration is invalid; prepare the integration again')
                _rights(kept, environ)
                if why := readiness.misfit(kept):
                    raise NotPrepared(why)
                receipt = _receipt(folder / envs.RECEIPT)
                check = receipt.get('dependencyCheck')
                expected_check = environment_check.receipt()
                lock = _lock(entry, envs.spec(kept)['lock'])
                if (receipt.get('model') != owner_key or receipt.get('generation') != generation
                        or receipt.get('lockSha256') != hashlib.sha256(lock).hexdigest()
                        or type(check) is not dict or set(check) != set(expected_check)
                        or check != expected_check or check.get('ok') is not True
                        or envs.prepared_generation(kept, receipt, environ) != generation):
                    raise NotPrepared('This candidate no longer matches its preparation receipt; prepare it again')
                python_home = interpreter_identity.verify(folder / 'bin/python', receipt.get('interpreter'))
                member = next((item for item in bundle_contract.parse_members(list(kept.members), kept)
                               if item.id == member_id), None)
                if member is None:
                    raise NotPrepared('This operation is not in that prepared generation; choose its declared operation')
                adapter = entry.joinpath(*member.adapter.partition(':')[0].split('.')).with_suffix('.py')
                _regular(adapter, 64 << 20)
                prepared = PreparedMember(bundle_contract.view(kept, member), folder, entry, python_home)
            except (OSError, ValueError, RecursionError, envs.Failed) as exc:
                if isinstance(exc, NotPrepared):
                    raise
                raise NotPrepared('The prepared candidate is incomplete; prepare its integration again') from exc
        yield prepared
