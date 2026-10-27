"""A bounded, headless scientific Python action with exact retained file inputs.

Reuses the scientific console, OS sandbox, process-tree guard and immutable file
intake. No package installation, arbitrary host paths, camera or viewer is granted.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time

from env_worker.scientific_console import source, reply
from .. import owned_process, sandbox, worker_resources
from ..paths import SERVICES
from ..scientific_file_intake import FileIntake
from ..scientific_projects import InvalidProject, ProjectStore
from .efforts import KEY, summary

WORKER = SERVICES / 'env_worker' / 'research_python_worker.py'
MAX_INPUT = 64 << 20
MAX_OUTPUT = 16 << 20
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}')


class PythonResearch:
    def __init__(self, efforts, *, python=None, work_root=None):
        self.efforts = efforts
        self.python = Path(python or sys.executable)
        self.work_root = work_root

    def execute(self, effort, session, *, request, code, outputs=(), seconds=60, stopping=None):
        source(code)
        if len(code.encode()) > 16 << 10:
            raise InvalidProject('This bounded Python action accepts at most 16 KiB of code')
        if (type(seconds) is not int or not 1 <= seconds <= 300 or not isinstance(outputs, (list, tuple))
                or len(outputs) > 8
                or any(type(name) is not str or NAME.fullmatch(name) is None or name == 'console.json' for name in outputs)
                or len(set(outputs)) != len(outputs)):
            raise InvalidProject('Choose up to eight output file names and a Python job limit of 1–300 seconds')
        if not sandbox.available():
            raise InvalidProject('The required OS confinement is unavailable; Python was not run')
        stopping = stopping or threading.Event()
        saved = self.efforts.read(effort, session)
        original = saved.document['state'][KEY]
        specification = {'operation': 'python', 'source': code, 'sourceSha256': hashlib.sha256(code.encode()).hexdigest(),
                         'inputs': original['inputs'], 'outputs': list(outputs), 'seconds': seconds,
                         'python': str(self.python.resolve()), 'confinement': 'macos-sandbox-exec'}
        reserved = self.efforts.reserve(effort, session, request=request, specification=specification, maximum_usd=0)
        previous = reserved.document['state'][KEY]['tasks'][request]
        if previous['status'] in ('completed', 'failed', 'cancelled'):
            return summary(reserved)
        self.efforts.claim(effort, session, request)
        # Reaping and output validation belong to this synchronous execution owner.
        # A process crash leaves launching/running in the durable record; it never reruns on read.
        execution = {'provider': 'local-confined-python', 'task': request,
                     'sourceSha256': specification['sourceSha256']}
        self.efforts.started(effort, session, request, execution)
        result = {'execution': execution, 'outputs': [], 'console': None}
        status = 'failed'
        started = time.monotonic()
        cancellation_seen = threading.Event()

        def should_stop():
            if stopping.is_set():
                cancellation_seen.set()
                return True
            try:
                current = self.efforts.read(effort, session).document['state'][KEY]
            except (OSError, ValueError):
                raise owned_process.Stopped('Cannot verify the research effort; local execution stopped') from None
            if current['status'] == 'stopped' or current['tasks'][request]['status'] == 'cancel_requested':
                cancellation_seen.set()
                return True
            return False

        try:
            with owned_process.scope(seconds, stop=should_stop) as bound:
                with tempfile.TemporaryDirectory(prefix='hooke-research-', dir=self.work_root) as folder:
                    root = Path(folder)
                    inputs, work = root / 'inputs', root / 'work'
                    inputs.mkdir(); work.mkdir()
                    files = self._inputs(original['inputs'], session, inputs, bound.check)
                    query = root / 'request.json'
                    query.write_text(json.dumps({'source': code, 'files': files}))
                    cmd = [str(self.python), '-I', '-B', str(WORKER), str(SERVICES), str(query)]
                    cmd = sandbox.wrap(cmd, [self.python.parent.parent, sandbox.python_home(self.python),
                                             SERVICES / 'env_worker', WORKER, query, inputs], [work], gpu=False, children=False)
                    env = sandbox.confine({'PATH': '/usr/bin:/bin', 'OPENBLAS_NUM_THREADS': '1',
                                           'OMP_NUM_THREADS': '1', 'VECLIB_MAXIMUM_THREADS': '1'}, work)
                    process = subprocess.Popen(cmd, cwd=work, env=env, stdin=subprocess.DEVNULL,
                                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                               start_new_session=True, close_fds=True)
                    guard = worker_resources.Guard(process, work, worker_resources.Limits(
                        memory_bytes=1 << 30, processes=1, file_bytes=MAX_OUTPUT,
                        work_bytes=32 << 20, work_entries=64))
                    try:
                        guard.start()
                        with owned_process.watching(process, seconds, 'Research Python'):
                            diagnostic = owned_process.drained(process, keep=8192)
                        guard.check()
                    finally:
                        guard.close()
                    bound.check()
                    if process.returncode != 0:
                        result['problem'] = 'The confined Python process failed'
                        result['diagnostic'] = diagnostic
                    else:
                        result['console'] = reply(json.loads(ProjectStore._read(work / 'console.json', 128 << 10)))
                        for field in ('stdout', 'stderr', 'result', 'error'):
                            value = result['console'][field]
                            if len(value.encode()) > 4000:
                                result['console'][field] = value.encode()[:4000].decode('utf-8', errors='ignore')
                                result['console']['truncated'] = True
                        if not result['console']['error']:
                            self._outputs(work, outputs, effort, session, request, bound.check, result['outputs'])
                            status = 'completed'
        except owned_process.Stopped as error:
            status = 'cancelled' if cancellation_seen.is_set() else 'failed'
            result['problem'] = str(error)
        except (OSError, ValueError) as error:
            result['problem'] = str(error)[:2000]
        result['elapsedSeconds'] = round(time.monotonic() - started, 4)
        # Parent-side Stop can arrive while output validation finishes. Never publish success after it.
        current = self.efforts.read(effort, session).document['state'][KEY]['tasks'][request]
        if current['status'] == 'cancel_requested':
            status = 'cancelled'
            result['problem'] = 'Stopped; local execution has ended'
        return summary(self.efforts.finish(effort, session, request, status=status, result=result, actual_usd=0))

    def _inputs(self, selected, session, destination, check):
        found, total = {}, 0
        for selection in selected:
            check()
            saved = self.efforts.store.read(selection['id'], session, revision=selection['revision'])
            if saved.digest != selection['digest']:
                raise InvalidProject('The admitted research input changed')
            for ref in saved.document['resources']:
                if not str(ref.get('component', '')).startswith('file.'):
                    continue
                data = self.efforts.store.file_source(saved.id, session, saved.revision, ref,
                                                       check=check, verify_content=False)
                total += data.size
                if total > MAX_INPUT or len(found) >= 32:
                    raise InvalidProject('This local Python action accepts at most 64 MiB across 32 retained files')
                key = f'{saved.id}/{ref["component"]}'
                path = destination / f'{len(found)}-{data.name}'
                with path.open('xb') as stream:
                    for offset in range(0, data.size, 1 << 20):
                        check()
                        stream.write(data.read(offset, min(1 << 20, data.size - offset)))
                path.chmod(0o400)
                found[key] = str(path)
        return found

    def _outputs(self, work, names, effort, session, request, check, results):
        # Worker is reaped before parent reads; no child can exchange files during retention.
        candidates, total = [], 0
        for name in names:
            check()
            body = ProjectStore._read(work / name, MAX_OUTPUT)
            total += len(body)
            if total > MAX_OUTPUT:
                raise InvalidProject('Research outputs exceed the 16 MiB local action allowance')
            candidates.append((name, body))
        for name, body in candidates:
            key = hashlib.sha256(f'{effort}/{request}/{name}'.encode()).hexdigest()
            intake = FileIntake(self.efforts.store, session, key, name=name, size=len(body), max_bytes=MAX_OUTPUT)
            try:
                for offset in range(0, len(body), 1 << 20):
                    check(); intake.write(body[offset:offset + (1 << 20)])
                def stopping():
                    check()
                    return False
                kept = intake.publish(stopping=stopping)
                results.append({'id': kept.id, 'session': kept.session, 'revision': kept.revision,
                                'digest': kept.digest, 'name': name, 'sha256': hashlib.sha256(body).hexdigest()})
            finally:
                intake.close()
        return results
