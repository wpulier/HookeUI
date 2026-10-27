"""One source identity for the frontend build and the backend process startup."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from .paths import REPO as ROOT

# A packaged copy (the Mac app, packaging/macos) has no Git: it carries the identity of the commit it was built from,
# the same one its studio build was stamped with. Read first, so a Mac without developer tools never runs git.
PACKAGED = ROOT / 'packaged.json'


def identity():
    if PACKAGED.is_file():
        try:
            built = json.loads(PACKAGED.read_text())['identity']
            return {key: built[key] for key in ('revision', 'dirty', 'sourceDigest')}
        except (OSError, ValueError, KeyError, TypeError):
            return {'revision': None, 'dirty': None, 'sourceDigest': None}

    def git(*args):
        return subprocess.check_output(['git', *args], cwd=ROOT, stderr=subprocess.DEVNULL)
    try:
        revision = git('rev-parse', 'HEAD').decode().strip()
        dirty = bool(git('status', '--porcelain', '--untracked-files=normal'))
        paths = sorted(set(git('ls-files', '-z', '--cached', '--others', '--exclude-standard',
                              'apps/studio', 'services', 'contract', 'scripts', 'Makefile').split(b'\0')) - {b''})
        digest = hashlib.sha256()
        for name in paths:
            path = ROOT / name.decode()
            if not path.is_file():
                digest.update(name + b'\0deleted\0')
                continue
            digest.update(name + b'\0' + hashlib.sha256(path.read_bytes()).digest())
        return {'revision': revision, 'dirty': dirty, 'sourceDigest': digest.hexdigest()}
    except (OSError, subprocess.CalledProcessError):
        return {'revision': None, 'dirty': None, 'sourceDigest': None}


def runtime_identity(backend=None):
    backend = backend if backend is not None else identity()
    try:
        frontend = json.loads((ROOT / 'apps/studio/dist/build.json').read_text())
    except (OSError, ValueError):
        frontend = None
    return {'backend': backend, 'frontend': frontend,
            'matches': bool(backend['revision'] and frontend == backend)}


if __name__ == '__main__':
    print(json.dumps(identity()))
