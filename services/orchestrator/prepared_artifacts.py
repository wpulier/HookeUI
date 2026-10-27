"""Admit only a prepared manifest's pinned model files for a scientific worker.

Published revisions are immutable. Keep their removal lease until the worker is
reaped; verify bytes with bounded reads before granting any paths. Developer
caches and live catalog aliases are deliberately outside this admission boundary.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat

from gpu_worker import manifests as catalog, model_files
from . import bounded_files, downloads, generation_leases, owned_process

CHUNK_BYTES = 1 << 20


class NotPrepared(ValueError):
    pass


@dataclass(frozen=True)
class Files:
    root: Path
    read_paths: tuple[Path, ...]


def _verify(root, declaration, bound):
    name = declaration.get('path')
    if (type(name) is not str or not name or '\\' in name
            or any(part in ('', '.', '..') for part in name.split('/'))
            or PurePosixPath(name).is_absolute()):
        raise ValueError('Invalid artifact path')
    size = declaration.get('bytes')
    if type(size) is not int or size < 0:
        raise ValueError('Invalid artifact size')
    hashes = []
    for key, pattern, factory in (('sha256', model_files.SHA256, hashlib.sha256),
                                  ('git_sha1', model_files.GIT_SHA1, hashlib.sha1)):
        expected = declaration.get(key)
        if expected is not None:
            if type(expected) is not str or not pattern.fullmatch(expected):
                raise ValueError('Invalid artifact content identity')
            digest = factory()
            if key == 'git_sha1':
                digest.update(f'blob {size}\0'.encode())
            hashes.append((expected, digest))
    if not hashes:
        raise ValueError('Artifact requires a pinned content identity')
    path = root / name
    directory = bounded_files.open_directory(path.parent)
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size != size:
            raise ValueError('Artifact size or file type changed')
        remaining = size
        while remaining:
            bound.check()
            chunk = stream.read(min(CHUNK_BYTES, remaining))
            if not chunk:
                raise ValueError('Artifact is incomplete')
            remaining -= len(chunk)
            for _, digest in hashes:
                digest.update(chunk)
        bound.check()
        after = os.fstat(stream.fileno())
        if (stream.read(1) or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns
                or any(digest.hexdigest() != expected for expected, digest in hashes)):
            raise ValueError('Artifact content changed')
    return path


@contextmanager
def open_files(manifest, environ=os.environ):
    """Yield exact read grants (or None) while preventing ordinary removal."""
    if manifest.files_from:
        raise NotPrepared('Declare pinned model files directly in this package before preparing it')
    if not manifest.files:
        yield None
        return
    with ExitStack() as stack:
        try:
            root = model_files.revision_dir(manifest, environ)
            stack.enter_context(generation_leases.use(root))
            with owned_process.scope() as bound:
                directory = bounded_files.open_directory(root)
                try:
                    stack.enter_context(downloads.Lock(manifest, environ, shared=True))
                    receipt = json.loads(bounded_files.read_at(directory, model_files.RECEIPT, 64 << 10))
                finally:
                    os.close(directory)
                if type(receipt) is not dict or receipt.get('revision') != catalog.revision_of(manifest):
                    raise ValueError('Artifact preparation receipt does not match')
                paths = tuple(_verify(root, item, bound) for item in manifest.files)
                if len(set(paths)) != len(paths):
                    raise ValueError('Duplicate artifact paths')
        except downloads.Failed as error:
            raise NotPrepared(f'{manifest.name}: {error}') from error
        except (OSError, ValueError, generation_leases.Missing) as error:
            raise NotPrepared(f'Prepare {manifest.name} again: its pinned model files are missing or changed') from error
        yield Files(root, paths)
