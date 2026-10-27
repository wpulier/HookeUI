"""A private Hooke for isolated-profile browser evidence: a real orchestrator, the Studio built from this checkout,
an empty home, and a prepared scientific integration; no camera, no keys, no network.

NAPARI_NATIVE_HOST_PREPARATION names a prepared private review bundle (its `key`, `folder` and the catalog part of
its `environment`), the same preparation the actual-worker tests use. The Studio is rebuilt from the working tree
for every stack and its build identity must equal the backend's, so the page under test is the code under test.
No camera bridge is started (the orchestrator is pointed at a closed port) and every saved file lives in the
stack's own temporary home, which close() removes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / 'services'
STUDIO = ROOT / 'apps/studio'


def preparation():
    location = os.environ.get('NAPARI_NATIVE_HOST_PREPARATION')
    if not location or not Path(location).is_file():
        return None
    return json.loads(Path(location).read_text())


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def build_studio():
    """Rebuild the Studio from this checkout and check its identity equals the backend's."""
    node = shutil.which('node')
    vite = STUDIO / 'node_modules/.bin/vite'
    if node is None or not vite.is_file():
        raise RuntimeError('The Studio cannot be built here: node or apps/studio/node_modules is missing')
    subprocess.run([str(vite), 'build', '--logLevel', 'error'], cwd=STUDIO, check=True, timeout=300,
                   stdout=subprocess.DEVNULL)
    sys.path.insert(0, str(SERVICES))
    from orchestrator.build_info import identity
    built = json.loads((STUDIO / 'dist/build.json').read_text())
    if built != identity():
        raise RuntimeError(f'The Studio build {built} is not the code under test {identity()}')
    return built


class Stack:
    """Orchestrator on a free port with its own home; `url` is the Studio."""

    def __init__(self, prepared, *, build=True):
        self.prepared = prepared
        self.build = build_studio() if build else None
        self.home = Path(tempfile.mkdtemp(prefix='hooke-stack-home-'))
        self.log = self.home / 'orchestrator.log'
        self.process = None
        try:
            self.port, self.bridge_port = free_port(), free_port()
            self.url = f'http://127.0.0.1:{self.port}'
            environment = prepared['environment']
            self.env = {
                'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'TMPDIR': os.environ.get('TMPDIR', '/tmp'),
                'HOME': str(self.home / 'user'), 'MICROMIND_HOME': str(self.home / 'saves'),
                'MICROMIND_ASSETS': str(self.home / 'assets'),
                'MICROMIND_RECORDINGS_DIR': str(self.home / 'saves' / 'recordings'),
                'MICROMIND_CATALOG': environment['MICROMIND_CATALOG'], 'MICROMIND_ENVS': environment['MICROMIND_ENVS'],
                'MICROMIND_KEYCHAIN': 'off', 'MICROMIND_CATALOG_AUTO': '0', 'MICROMIND_PRELOAD': '0',
                'MICROMIND_KEYS_FILE': str(self.home / 'absent-keys'), 'HF_HUB_OFFLINE': '1',
                'MICROMIND_EDITION': 'research', 'PYTHONUTF8': '1'}
            for name in ('user', 'saves', 'assets'):
                (self.home / name).mkdir()
            self.start()
        except BaseException as error:
            try:
                self.close()
            except Exception as cleanup:
                error.add_note(f'Private-stack cleanup also failed: {cleanup}')
            raise

    def start(self):
        with self.log.open('ab') as log:
            self.process = subprocess.Popen(
                [sys.executable, '-m', 'orchestrator.server', '--port', str(self.port),
                 '--bridge-url', f'http://127.0.0.1:{self.bridge_port}', '--no-gpu'],
                # In the test's own process group: a runner that stops the tests' group stops this too.
                cwd=SERVICES, env=self.env, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('The orchestrator exited while starting:\n' + self.log.read_text()[-4000:])
            try:
                with urllib.request.urlopen(self.url + '/api/health', timeout=2) as reply:
                    if reply.status == 200 and self.session():
                        return
            except OSError:
                pass
            time.sleep(.25)
        raise RuntimeError('The orchestrator did not become ready:\n' + self.log.read_text()[-4000:])

    def stop(self):
        if self.process and self.process.poll() is not None:  # it ended before we stopped it: say how
            with self.log.open('a') as log:
                log.write(f'\n[stack] the orchestrator had already exited with {self.process.returncode}\n')
        if self.process and self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)  # the orchestrator stops and reaps its own workers
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)

    def restart(self):
        self.stop()
        self.start()

    def close(self):
        try:
            self.stop()
        finally:
            try:
                keep = os.environ.get('HOOKE_STACK_LOGS')  # where to keep the orchestrator's log and the saves' logs
                if keep:
                    target = Path(keep) / self.home.name
                    target.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(self.log, target / 'orchestrator.log') if self.log.is_file() else None
                    if (self.saves / 'logs').is_dir():
                        shutil.copytree(self.saves / 'logs', target / 'logs', dirs_exist_ok=True)
            finally:
                shutil.rmtree(self.home, ignore_errors=True)

    @property
    def saves(self):
        return self.home / 'saves'

    def session(self):
        """The current Project (day session) id, as the orchestrator recorded it."""
        path = self.saves / 'sessions/preferences.jsonl'
        if not path.is_file():
            return None
        current = None
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get('type') == 'current-session':
                current = row.get('session')
        return current

    def store(self):
        sys.path.insert(0, str(SERVICES))
        from orchestrator.scientific_projects import ProjectStore
        return ProjectStore(self.saves / 'results/projects')

    def seed_scene(self, title, inputs):
        """A saved scene document bound to the prepared integration, with each input array retained.

        Setup only: this places saved data in the Project the way an earlier session would have left it. The
        behaviour under test starts in the browser from Project Data.
        """
        store, sid = self.store(), self.session()
        bundle, operation = self.prepared['key'].split('#')
        runtime = {'bundle': bundle, 'generation': Path(self.prepared['folder']).name, 'operation': operation}
        created = store.create(sid, {'kind': 'scene', 'title': title, 'state': {}, 'resources': [],
                                     'runtimes': [runtime]})
        references = {}
        for port, (values, axes, scientific) in inputs.items():
            chunks = [min(n, 64) for n in values.shape]
            writer = store.array_draft(created.id, sid, component=f'array.{port}', shape=values.shape,
                                       dtype=values.dtype.str, axes=list(axes), chunks=chunks,
                                       scientific=scientific, max_written_bytes=values.nbytes)
            import itertools
            grid = [range(0, n, c) for n, c in zip(values.shape, chunks)]
            for start in itertools.product(*grid):
                index = [s // c for s, c in zip(start, chunks)]
                region = tuple(slice(s, s + c) for s, c in zip(start, chunks))
                writer.write(index, values[region])
            references[port] = writer.finish()
        document = {**created.document, 'resources': list(references.values()),
                    'state': {'scientificView': {'runtime': runtime, 'inputs': references}}}
        return store.save(created.id, sid, document, expected_revision=created.revision)
