"""Deliberate public GitHub catalog reads, without a system Git installation.

Fetch only the requested catalog subtree at a full commit, bound its complete listing and
contents, verify each Git blob identity, then publish once. Nothing is installed or run.
Automatic previews retain catalog_metadata's smaller, metadata-only transport.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import time
import unicodedata
from urllib.parse import quote, urlparse

import httpx

from .channels import CantDo

TREE_BYTES = 4 * 1024 * 1024
FILE_BYTES = 8 * 1024 * 1024
TOTAL_BYTES = 64 * 1024 * 1024
MAX_FILES = 4096
SECONDS = 120
SHA = re.compile(r'[0-9a-f]{40}')


def _failure(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 404:
            return 'This public catalog or pinned revision could not be found. Check the address; private repositories need a shared capability file. Then retry.'
        if status in (403, 429):
            return 'GitHub refused or temporarily limited this public catalog request. Wait and retry; nothing was installed.'
        if 300 <= status < 400:
            return 'This catalog address redirects elsewhere. Use its current public GitHub repository address, then retry.'
    return 'The public GitHub catalog could not be read safely. Check the address, connection and storage, then retry.'


def repository(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.netloc != 'github.com':
        return None
    parts = parsed.path.strip('/').removesuffix('.git').split('/')
    if parsed.query or parsed.fragment or len(parts) != 2 or any(
            not re.fullmatch(r'[A-Za-z0-9_.-]+', p) or p in ('.', '..') for p in parts):
        raise CantDo('use the public GitHub repository address, without query parameters or a page path')
    return '/'.join(parts)


def _read(client: httpx.Client, url: str, limit: int, deadline: float) -> bytes:
    if time.monotonic() >= deadline:
        raise CantDo('the catalog download reached its time limit; try again')
    data = bytearray()
    with client.stream('GET', url, timeout=max(.1, min(10, deadline - time.monotonic()))) as response:
        response.raise_for_status()
        if int(response.headers.get('content-length', 0)) > limit:
            raise CantDo('the catalog response exceeds its byte limit')
        for chunk in response.iter_bytes(chunk_size=16384):
            if time.monotonic() >= deadline or len(data) + len(chunk) > limit:
                raise CantDo('the catalog response exceeds its time or byte limit')
            data.extend(chunk)
    return bytes(data)


def newest(repo: str) -> str:
    try:
        with httpx.Client(follow_redirects=False, trust_env=False) as client:
            result = json.loads(_read(client, f'https://api.github.com/repos/{repo}/commits/HEAD',
                                      TREE_BYTES, time.monotonic() + SECONDS))
        sha = result.get('sha') if isinstance(result, dict) else None
        if not isinstance(sha, str) or not SHA.fullmatch(sha):
            raise CantDo('GitHub did not return a full commit for this catalog')
        return sha
    except (httpx.HTTPError, ValueError, OSError) as exc:
        raise CantDo(_failure(exc)) from exc


def _path(value: str) -> bool:
    return (bool(value) and not value.startswith('/') and '\\' not in value
            and all(p not in ('', '.', '..') and not any(ord(c) < 32 for c in p) for p in value.split('/')))


def _files(tree: object, path: str) -> list[dict]:
    if not isinstance(tree, dict) or tree.get('truncated') or not isinstance(tree.get('tree'), list):
        raise CantDo('the catalog needs a complete file listing within its size limit')
    rows, names, spellings, total = [], set(), {}, 0
    for row in tree['tree']:
        if not isinstance(row, dict) or not isinstance(row.get('path'), str):
            raise CantDo('the catalog file listing is malformed')
        name = row['path']
        if not name.startswith(path + '/'):
            continue
        name = name[len(path) + 1:]
        normalized = unicodedata.normalize('NFC', name).casefold()
        if not _path(name) or normalized in names:
            raise CantDo('the catalog has a noncanonical or ambiguous path')
        names.add(normalized)
        parts = name.split('/')
        for i in range(1, len(parts) + 1):
            prefix = '/'.join(parts[:i])
            key = unicodedata.normalize('NFC', prefix).casefold()
            if key in spellings and spellings[key] != prefix:
                raise CantDo('the catalog has ambiguous folder or file spellings')
            spellings[key] = prefix
        if row.get('type') == 'tree' and row.get('mode') == '040000':
            continue
        size, sha = row.get('size'), row.get('sha')
        if (row.get('type') != 'blob' or row.get('mode') not in ('100644', '100755')
                or type(size) is not int or not 0 <= size <= FILE_BYTES
                or not isinstance(sha, str) or not SHA.fullmatch(sha)):
            raise CantDo('the catalog contains an unsafe file, link, submodule or file beyond its size limit')
        total += size
        rows.append({**row, 'path': name})
        if total > TOTAL_BYTES or len(rows) > MAX_FILES:
            raise CantDo('the catalog exceeds its total byte or file count limit')
    files = {unicodedata.normalize('NFC', row['path']).casefold() for row in rows}
    if any('/'.join(PurePosixPath(name).parts[:i]).casefold() in files
           for name in files for i in range(1, len(PurePosixPath(name).parts))):
        raise CantDo('the catalog has a file where another file requires a folder')
    if not any(len(row['path'].split('/')) == 2 and row['path'].endswith('/model.toml') for row in rows):
        raise CantDo('the pinned catalog contains no manifests')
    return rows


def fetch(repo: str, commit: str, dest: Path, path: str) -> int:
    if not SHA.fullmatch(commit) or not _path(path):
        raise CantDo('the catalog needs a pinned commit and a canonical in-repository folder')
    if dest.is_dir() and not dest.is_symlink():
        return sum(1 for _ in dest.glob('*/model.toml'))
    dest.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + SECONDS
    try:
        with httpx.Client(follow_redirects=False, trust_env=False) as client:
            tree = json.loads(_read(client, f'https://api.github.com/repos/{repo}/git/trees/{commit}?recursive=1',
                                    TREE_BYTES, deadline))
            rows = _files(tree, path)
            from .downloads import SPARE
            if shutil.disk_usage(dest.parent).free < sum(row['size'] for row in rows) + SPARE:
                raise CantDo('there is not enough free space for this catalog; free some space and retry')
            with tempfile.TemporaryDirectory(prefix='.catalog-', dir=dest.parent) as temporary:
                out = Path(temporary) / 'out'
                out.mkdir()
                for row in rows:
                    name = row['path']
                    data = _read(client, f'https://raw.githubusercontent.com/{repo}/{commit}/'
                                 + quote(path + '/' + name, safe='/'), row['size'], deadline)
                    digest = hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()
                    if len(data) != row['size'] or digest != row['sha']:
                        raise CantDo('the catalog file differs from its pinned blob identity; retry the download')
                    target = out / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    target.chmod(0o444)
                out.rename(dest)
        return sum(1 for row in rows if len(row['path'].split('/')) == 2 and row['path'].endswith('/model.toml'))
    except (httpx.HTTPError, ValueError, OSError) as exc:
        raise CantDo(_failure(exc)) from exc
