"""Bounded discovery transport: named manifest/image blobs only, never a Git fetch.

GitHub trees list byte sizes and blob identities before any content is requested. Local repositories are read with
lazy fetching disabled. Other remote hosts refuse automatic discovery; deliberate channel Get/update remains separate.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import quote, urlparse

import httpx

from . import owned_process
from .channels import CantDo

TREE_BYTES = 2 * 1024 * 1024
MANIFEST_BYTES = 128 * 1024
PICTURE_BYTES = 512 * 1024
TOTAL_BYTES = 8 * 1024 * 1024
MAX_FILES = 512
MAX_PICTURES = 64
SECONDS = 30
PICTURES = {'.png', '.jpg', '.jpeg', '.webp'}


NOTICES = 'maintenance.toml'  # the catalog's revoked versions and unmaintained entries (maintenance/revocations.py)


def _selected(name: str) -> bool:
    parts = PurePosixPath(name).parts
    if parts == (NOTICES,):
        return True
    return len(parts) == 2 and all(p not in ('.', '..') for p in parts) and (
        parts[-1] == 'model.toml' or PurePosixPath(name).suffix.lower() in PICTURES)


def _bounded(rows: list[dict]) -> list[dict]:
    pictures = sum(PurePosixPath(r['path']).suffix.lower() in PICTURES for r in rows)
    if len(rows) > MAX_FILES or pictures > MAX_PICTURES:
        raise CantDo('the automatic preview exceeds its file count limit')
    total = 0
    for row in rows:
        size = row.get('size')
        limit = MANIFEST_BYTES if row['path'].endswith('/model.toml') or row['path'] == NOTICES else PICTURE_BYTES
        if row.get('mode') not in ('100644', '100755') or not isinstance(size, int) or size < 0 or size > limit:
            raise CantDo(f"the automatic preview has an unsafe file or one past its size limit: {row['path']}")
        total += size
    if total > TOTAL_BYTES:
        raise CantDo('the automatic preview exceeds its total byte limit')
    return rows


def _local(repo: Path, *args: str, limit: int) -> bytes:
    env = {k: v for k, v in os.environ.items() if k in ('HOME', 'PATH', 'TMPDIR')}
    env.update(GIT_TERMINAL_PROMPT='0', GIT_NO_LAZY_FETCH='1', GIT_NO_REPLACE_OBJECTS='1')
    process = subprocess.Popen(['git', *args], cwd=repo, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, start_new_session=True)
    try:
        with owned_process.watching(process, SECONDS, 'catalog metadata read'):
            data = process.stdout.read(limit + 1)
            if len(data) > limit:
                raise CantDo('the catalog metadata exceeds its byte limit')
            if process.wait() != 0:
                raise CantDo('the local repository cannot provide the pinned catalog metadata without fetching')
            return data
    finally:
        owned_process.terminate(process)
        process.stdout.close()


def fetch(url: str, commit: str, dest: Path, path: str) -> int:
    if not re.fullmatch(r'[0-9a-f]{40}', commit) or path.startswith('/') or '..' in PurePosixPath(path).parts:
        raise CantDo('the preview needs a pinned commit and an in-repository catalog path')
    if dest.is_dir():
        cached = [p for p in dest.rglob('*') if p.is_file() or p.is_symlink()]
        if any(p.is_symlink() or not _selected(p.relative_to(dest).as_posix()) for p in cached):
            raise CantDo('the cached catalog preview has unsafe files')
        _bounded([{'path': p.relative_to(dest).as_posix(), 'size': p.stat().st_size, 'mode': '100644'} for p in cached])
        return sum(p.name == 'model.toml' for p in cached)
    dest.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + SECONDS
    with httpx.Client(follow_redirects=False, timeout=10) as client:
        def read_http(address: str, limit: int) -> bytes:
            data = bytearray()
            with client.stream('GET', address, headers={'Accept': 'application/vnd.github+json'},
                               timeout=max(.1, min(10, deadline - time.monotonic()))) as response:
                response.raise_for_status()
                if int(response.headers.get('content-length', 0)) > limit:
                    raise CantDo('the catalog response exceeds its byte limit')
                for chunk in response.iter_bytes(chunk_size=16384):
                    if time.monotonic() >= deadline or len(data) + len(chunk) > limit:
                        raise CantDo('the catalog response exceeds its time or byte limit')
                    data.extend(chunk)
            return bytes(data)

        local = Path(url) if Path(url).is_absolute() else None
        try:
            if local is not None:
                listing = _local(local, 'ls-tree', '-rlz', commit, '--', path, limit=TREE_BYTES)
                rows = []
                for line in listing.split(b'\0'):
                    if not line:
                        continue
                    facts, raw_name = line.split(b'\t', 1)
                    mode, kind, sha, size = facts.split()
                    name = raw_name.decode('utf-8')[len(path) + 1:]
                    if _selected(name):
                        rows.append({'path': name, 'mode': mode.decode(), 'sha': sha.decode(), 'size': int(size)})
                read_blob = lambda row: _local(local, 'cat-file', 'blob', row['sha'], limit=row['size'])
            else:
                parsed = urlparse(url)
                parts = parsed.path.strip('/').removesuffix('.git').split('/')
                if parsed.scheme != 'https' or parsed.netloc != 'github.com' or len(parts) != 2 or any(
                        not re.fullmatch(r'[A-Za-z0-9_.-]+', p) for p in parts):
                    raise CantDo('this host has no supported metadata-only discovery transport; use its deliberate catalog update')
                repo = '/'.join(parts)
                tree = json.loads(read_http(f'https://api.github.com/repos/{repo}/git/trees/{commit}?recursive=1', TREE_BYTES))
                if tree.get('truncated') or not isinstance(tree.get('tree'), list):
                    raise CantDo('the catalog tree exceeds the complete metadata listing limit')
                rows = [{**r, 'path': r['path'][len(path) + 1:]} for r in tree['tree'] if isinstance(r, dict)
                        and isinstance(r.get('path'), str) and r['path'].startswith(path + '/')
                        and _selected(r['path'][len(path) + 1:])]
                read_blob = lambda row: read_http(
                    f'https://raw.githubusercontent.com/{repo}/{commit}/{quote(path + "/" + row["path"], safe="/")}', row['size'])
            rows = _bounded(rows)
            count = sum(r['path'].endswith('/model.toml') for r in rows)
            if not count:
                raise CantDo('the pinned catalog contains no preview manifests')
            from .downloads import SPARE

            if shutil.disk_usage(dest.parent).free < sum(r['size'] for r in rows) + SPARE:
                raise CantDo('there is not enough free space for the bounded catalog preview')
            with tempfile.TemporaryDirectory(prefix='.metadata-', dir=dest.parent) as work:
                out = Path(work) / 'out'
                out.mkdir()
                for row in rows:
                    if time.monotonic() >= deadline:
                        raise CantDo('the catalog preview reached its time limit')
                    data = read_blob(row)
                    digest = hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()
                    if len(data) != row['size'] or digest != row.get('sha'):
                        raise CantDo('the catalog preview differs from its pinned blob identity')
                    file = out / row['path']
                    file.parent.mkdir(exist_ok=True)
                    file.write_bytes(data)
                    file.chmod(0o444)
                out.rename(dest)
            return count
        except (httpx.HTTPError, ValueError, OSError, KeyError, owned_process.Stopped) as exc:
            raise CantDo(f'catalog metadata could not be read safely: {exc}') from exc
