"""Installer-owned interpreter identity, checked before deriving a sandbox read grant."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

BOUNDARY = 'prepared-interpreter-v1'


def capture(executable):
    from .envs import Failed
    try:
        target = Path(executable).resolve(strict=True)
        if target.parent.name != 'bin':
            raise Failed('The prepared Python is not inside its runtime bin directory')
        descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 64 << 20:
                raise Failed('The prepared Python is not a bounded regular executable')
            digest, size = hashlib.sha256(), 0
            while body := stream.read(1 << 20):
                size += len(body)
                if size > 64 << 20:
                    raise Failed('The prepared Python exceeds its executable allowance')
                digest.update(body)
        return {'boundary': BOUNDARY, 'executable': str(target), 'home': str(target.parent.parent),
                'sha256': digest.hexdigest()}
    except (OSError, RuntimeError) as exc:
        raise Failed('The managed Python is missing or redirected; restore its prepared runtime') from exc


def verify(executable, record):
    from .envs import Failed
    if type(record) is not dict or record != capture(executable):
        raise Failed('The managed Python changed after preparation; restore its prepared runtime')
    return Path(record['home'])


def recover(folder, version, environ):
    """Old receipts can acquire identity only from the trusted installer's managed lookup."""
    from . import envs
    if type(version) is not str or re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[a-z0-9.+-]{0,32})', version) is None:
        raise envs.Failed('The older runtime has no recorded Python version; restore its preparation')
    environment = envs.clean_env(environ=environ)
    environment['UV_OFFLINE'] = '1'
    with tempfile.TemporaryDirectory(prefix='.interpreter-check-', dir=folder) as work:
        found = envs._run([envs.uv(), 'python', 'find', '--managed-python', '--no-project', '--no-config',
                          '--no-python-downloads', '--offline', version], environment,
                         lambda: False, 'finding its managed Python', cwd=work).strip()
    if not found or '\n' in found or len(found) > 4096 or not Path(found).is_absolute():
        raise envs.Failed('The managed installer could not identify this older Python; restore its preparation')
    identity = capture(found)
    verify(folder / 'bin/python', identity)
    return identity
