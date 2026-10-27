"""Offline installed-requirement check; neither operation proof nor optional-extra coverage."""
from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import tempfile

from . import generation_leases, owned_process, sandbox, worker_resources

BOUNDARY = 'installed-requirements-v1'
MAX_OUTPUT = 1 << 20


def receipt():
    return {'boundary': BOUNDARY, 'scope': 'installed distribution requirements', 'ok': True}


def refresh(manifest, folder, environ, *, stop=lambda: False):
    from . import envs, interpreter_identity

    path = folder / envs.RECEIPT
    if path.is_symlink() or path.stat().st_size > 64 << 10:
        raise envs.Failed('The installation receipt is invalid; restore its prepared generation')
    original = path.read_bytes()
    try:
        record = json.loads(original)
    except (ValueError, RecursionError) as exc:
        raise envs.Failed('The installation receipt is invalid; restore its prepared generation') from exc
    if (type(record) is not dict or record.get('model') != manifest.key
            or record.get('generation') != folder.name
            or record.get('lockSha256') != hashlib.sha256(envs.lock_file(manifest, environ).read_bytes()).hexdigest()):
        raise envs.Failed('The installation no longer matches its receipt; restore its prepared generation')
    with generation_leases.use(folder):
        with owned_process.scope(stop=stop, seconds=60):
            identity = record.get('interpreter')
            if identity is None:
                identity = interpreter_identity.recover(folder, record.get('python'), environ)
            interpreter_identity.verify(folder / 'bin/python', identity)
            complete = record.get('dependencyCheck') == receipt() and record['dependencyCheck'].get('ok') is True
            if complete and record.get('interpreter') is not None:
                return
            checked = receipt() if complete else check(manifest, folder, envs.uv(), envs.clean_env(manifest, folder, environ), stop=stop)
        if path.read_bytes() != original:
            raise envs.Failed('The installation receipt changed during checking; review it and retry')
        record['dependencyCheck'] = checked
        record['interpreter'] = identity
        descriptor, temporary = tempfile.mkstemp(prefix='.dependency-receipt-', dir=folder)
        try:
            with os.fdopen(descriptor, 'w') as stream:
                json.dump(record, stream, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)


def check(manifest, folder, tool, environment, *, stop=lambda: False):
    from .envs import Failed

    folder, tool = Path(folder), Path(tool).resolve()
    confined = sandbox.required(manifest)
    if confined and not sandbox.available():
        raise Failed(sandbox.refused(manifest))
    with owned_process.scope(stop=stop, seconds=60), generation_leases.use(folder):
        work = Path(tempfile.mkdtemp(prefix='.dependency-check-', dir=folder))
        command = [str(tool), 'pip', 'check', '--python', str(folder / 'bin/python')]
        environment = {**environment, 'UV_OFFLINE': '1'}
        if confined:
            environment = sandbox.confine(environment, work)
            command = sandbox.wrap(command, [tool, folder, sandbox.python_home(folder / 'bin/python')],
                                   [work], gpu=False, children=True)
        process = guard = None
        try:
            with owned_process.scope():
                pass
            process = subprocess.Popen(command, cwd=work, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       close_fds=True, start_new_session=True)
            if confined:
                guard = worker_resources.Guard(process, work, worker_resources.Limits(
                    memory_bytes=256 << 20, processes=2, file_bytes=1 << 20,
                    work_bytes=16 << 20, work_entries=64)).start()
            with owned_process.watching(process, 60, 'checking installed requirements'):
                total, tail = 0, bytearray()
                while data := process.stdout.read(8192):
                    total += len(data)
                    if total > MAX_OUTPUT:
                        raise Failed('The dependency check exceeded its output allowance')
                    tail.extend(data)
                    del tail[:-8192]
                process.wait()
            if guard is not None:
                guard.close()
                guard.check()
            if process.returncode:
                raise Failed('Installed dependencies are incomplete or incompatible. Correct the pinned lock and retry. '
                             + tail.decode('utf-8', 'replace')[-1200:])
            return receipt()
        except owned_process.Stopped as exc:
            raise Failed(str(exc)) from exc
        finally:
            if guard is not None:
                guard.close()
            elif process is not None and process.poll() is None:
                owned_process.terminate(process)
            if process is not None:
                process.stdout.close()
            shutil.rmtree(work)
