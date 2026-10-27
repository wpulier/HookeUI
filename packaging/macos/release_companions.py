"""Exact optional release assets. This validates bytes, never tool qualification."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import urllib.request

MANIFEST = 'companions.json'
MAX_ASSET = 128 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024


def regular(path: Path, maximum: int) -> bytes:
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError('Companion must be a bounded regular file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            body = stream.read(maximum + 1)
        if len(body) != info.st_size or len(body) > maximum:
            raise ValueError('Companion changed while reading')
        return body
    finally:
        os.close(fd)


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def parse(body: bytes) -> dict:
    value = json.loads(body)
    if (not isinstance(value, dict) or set(value) != {'format','sourceCommit','assets'}
            or type(value['format']) is not int or value['format'] != 1
            or not isinstance(value['sourceCommit'], str) or not re.fullmatch('[0-9a-f]{40}', value['sourceCommit'])
            or not isinstance(value['assets'], list) or not 1 <= len(value['assets']) <= 8):
        raise ValueError('Invalid companion manifest')
    names = set(); total = 0
    for row in value['assets']:
        if (not isinstance(row, dict) or set(row) != {'name','sha256','bytes'}
                or not isinstance(row['name'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,180}\.zip', row['name'])
                or row['name'].casefold() in names or '..' in row['name']
                or not isinstance(row['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', row['sha256'])
                or type(row['bytes']) is not int or not 0 < row['bytes'] <= MAX_ASSET):
            raise ValueError('Invalid companion asset name, size or hash')
        names.add(row['name'].casefold()); total += row['bytes']
    if total > MAX_TOTAL:
        raise ValueError('Companion assets exceed the total bound')
    return value


def checked(path: Path, row: dict) -> bytes:
    body = regular(path, row['bytes'])
    if len(body) != row['bytes'] or digest(body) != row['sha256']:
        raise ValueError('Companion asset differs from its approved bytes: ' + row['name'])
    return body


def stage(manifest: Path, out: Path) -> dict:
    """Copy into a new candidate directory; input changes cannot change the candidate."""
    if manifest.parent.is_symlink():
        raise ValueError('Companion directory must not be a symlink')
    body = regular(manifest, 64 * 1024); value = parse(body)
    destination = out / 'companions'
    if destination.exists() or destination.is_symlink():
        raise ValueError('Companion candidate already exists')
    with tempfile.TemporaryDirectory(prefix='.companions-', dir=out) as tmp:
        folder = Path(tmp) / 'checked'; folder.mkdir()
        for row in value['assets']:
            path = folder / row['name']; path.write_bytes(checked(manifest.parent / row['name'], row)); path.chmod(0o444)
        (folder / MANIFEST).write_bytes(body); (folder / MANIFEST).chmod(0o444)
        folder.rename(destination)
    destination.chmod(0o555)
    return {'manifest': MANIFEST, 'sha256': digest(body), 'sourceCommit': value['sourceCommit']}


def verify(out: Path, binding: dict | None, approval: str | None) -> dict | None:
    folder = out / 'companions'
    if binding is None:
        if approval or folder.exists() or folder.is_symlink():
            raise ValueError('Unbound companions or unexpected companion approval')
        return None
    if (not isinstance(binding, dict) or set(binding) != {'manifest','sha256','sourceCommit'}
            or binding['manifest'] != MANIFEST or not approval or approval != binding['sha256']):
        raise ValueError('Explicit companion manifest approval is required')
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError('Missing or linked companion candidate directory')
    body = regular(folder / MANIFEST, 64 * 1024); value = parse(body)
    if digest(body) != approval or value['sourceCommit'] != binding['sourceCommit']:
        raise ValueError('Companion manifest changed after preparation')
    if set(os.listdir(folder)) != {MANIFEST, *(row['name'] for row in value['assets'])}:
        raise ValueError('Companion candidate contains missing or unlisted assets')
    for row in value['assets']:
        checked(folder / row['name'], row)
    return value


def promotion(beta: dict | None, candidate: dict | None) -> None:
    if beta != candidate:
        raise ValueError('Stable must preserve the exact Beta companion manifest and assets; provide its matching --companions manifest')


def expected(out: Path, binding: dict) -> list[dict]:
    value = verify(out, binding, binding['sha256'])
    return [{'name': MANIFEST, 'bytes': (out/'companions'/MANIFEST).stat().st_size, 'sha256': binding['sha256']}, *value['assets']]


def verify_download(out: Path, binding: dict, downloaded: Path) -> None:
    for row in expected(out, binding):
        checked(downloaded / row['name'], row)


def verify_remote(out: Path, binding: dict, base: str, *, fetch=urllib.request.urlopen) -> None:
    if not re.fullmatch(r'https://github.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/releases/download/[A-Za-z0-9_.-]+', base):
        raise ValueError('Companion verification requires the canonical release URL')
    for row in expected(out, binding):
        with fetch(base + '/' + row['name'], timeout=30) as response:
            body = response.read(row['bytes'] + 1)
        if len(body) != row['bytes'] or digest(body) != row['sha256']:
            raise ValueError('Published companion bytes differ: ' + row['name'])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['paths','downloaded','anonymous'])
    parser.add_argument('out', type=Path)
    parser.add_argument('--approval')
    parser.add_argument('--download-dir', type=Path)
    parser.add_argument('--url')
    args = parser.parse_args()
    receipt = json.loads(regular(args.out/'release.json', 1024 * 1024))
    intent_path = args.out/'release-intent.json'
    binding = receipt.get('companions')
    if intent_path.exists() and json.loads(regular(intent_path, 1024 * 1024)).get('companions') != binding:
        raise ValueError('Companion release intent differs from candidate')
    value = verify(args.out, binding, args.approval)
    if value is None:
        return
    if not intent_path.exists():
        raise ValueError('Companion publication requires its prepared release intent')
    if args.action == 'paths':
        for row in expected(args.out, binding):
            print(args.out/'companions'/row['name'])
    elif args.action == 'downloaded':
        verify_download(args.out, binding, args.download_dir)
    else:
        verify_remote(args.out, binding, args.url)


if __name__ == '__main__':
    main()
