"""The production Studio in a real Chromium, against Hooke on an isolated profile.

Opt-in: NAPARI_HOST_PREPARATION (a prepared review bundle), HOOKE_CHROMIUM (a Chromium executable) and
HOOKE_PLAYWRIGHT (Playwright's index.mjs). Without them a test skips and its children stay missing.

`Studio` builds the Studio from this tree when its build is stale, makes a fresh Hooke home (own assets, an
empty keys file, no camera, no keychain), saves the given scientific documents into today's Project, starts
the real orchestrator on a free port and opens the Studio in a fresh browser profile. Tests then act only
through the page (`act`), as a researcher would. Nothing here touches the live app's home or camera.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import pytest

SERVICES = Path(__file__).resolve().parents[1]
ROOT = SERVICES.parent
DRIVER = Path(__file__).with_name('scientific_browser_driver.mjs')


def requirements():
    names = ('NAPARI_HOST_PREPARATION', 'HOOKE_CHROMIUM', 'HOOKE_PLAYWRIGHT')
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        pytest.skip(f'requires {", ".join(missing)} for an isolated-profile browser run')
    return {name: os.environ[name] for name in names}


def current_studio_build():
    """Build the production Studio when its build does not match this tree; one build at a time."""
    from orchestrator import build_info
    import hashlib
    import tempfile
    lock = Path(tempfile.gettempdir()) / f'hooke-studio-build-{hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]}.lock'
    with lock.open('w') as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        identity = build_info.runtime_identity()
        if not identity['matches']:
            subprocess.run(['npm', 'run', '--silent', 'build'], cwd=ROOT / 'apps/studio', check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=900)
            identity = build_info.runtime_identity()
        assert identity['matches'], 'the Studio build does not match this tree'
        return identity['frontend']


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def today():
    return time.strftime('%Y-%m-%d')


class Studio:
    """One isolated Hooke and one fresh browser profile; closes both."""

    def __init__(self, folder: Path, documents):
        self.folder, self.documents = Path(folder), documents
        self.process = self.driver = None
        self.saved = {}

    def __enter__(self):
        needs = requirements()
        prepared = json.loads(Path(needs['NAPARI_HOST_PREPARATION']).read_text())
        self.prepared = prepared
        self.build = current_studio_build()
        home, assets = self.folder / 'home', self.folder / 'assets'
        for path in (home, assets, self.folder / 'browser'):
            path.mkdir(parents=True, exist_ok=True)
        (self.folder / 'keys.txt').write_text('')
        environment = {key: os.environ[key] for key in ('PATH', 'TMPDIR', 'HOME') if key in os.environ}
        environment.update({key: value for key, value in prepared['environment'].items() if key != 'MICROMIND_ASSETS'})
        environment.update(MICROMIND_HOME=str(home), MICROMIND_ASSETS=str(assets), MICROMIND_KEYS_FILE=str(self.folder / 'keys.txt'),
                           CAMERA_SOURCE_FILE=str(assets / 'camera-source.json'), CAMERA_NO_PHONE='1', MICROMIND_KEYCHAIN='off',
                           MICROMIND_CATALOG_AUTO='0', MICROMIND_LIBRARY_LOOKS='0', HF_HUB_OFFLINE='1', PYTHONUTF8='1',
                           MICROMIND_RECORDINGS_DIR=str(home / 'recordings'))
        self.environment = environment
        self._save_documents(home)
        self.port = free_port()
        log = (self.folder / 'orchestrator.log').open('wb')
        self.process = subprocess.Popen([sys.executable, '-m', 'orchestrator.server', '--port', str(self.port),
                                         '--bridge-url', f'http://127.0.0.1:{free_port()}', '--no-gpu'],
                                        cwd=SERVICES, env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        self._wait_for_health()
        self.url = f'http://127.0.0.1:{self.port}/'
        self.driver = subprocess.Popen(['node', str(DRIVER), needs['HOOKE_PLAYWRIGHT'], needs['HOOKE_CHROMIUM'],
                                        str(self.folder / 'browser'), self.url], cwd=self.folder, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=(self.folder / 'driver.log').open('wb'),
                                       text=True, start_new_session=True)
        assert json.loads(self.driver.stdout.readline())['out'] == 'ready'
        self.act('goto')
        return self

    def _save_documents(self, home):
        from orchestrator.scientific_projects import ProjectStore
        runtime = {'bundle': self.prepared['key'].split('#')[0], 'generation': Path(self.prepared['folder']).name,
                   'operation': self.prepared['key'].split('#')[1]}
        store = ProjectStore(home / 'results/projects', runtime_root=Path(self.environment['MICROMIND_ENVS']))
        for title, arrays in self.documents.items():
            created = store.create(today(), {'kind': 'scene', 'title': title, 'state': {}, 'resources': [], 'runtimes': []})
            inputs = {}
            for name, (values, axes, semantic) in arrays.items():
                draft = store.array_draft(created.id, today(), component=f'array.{name}', shape=values.shape,
                                          dtype=values.dtype.str, axes=list(axes), chunks=list(values.shape),
                                          scientific={'semantic': semantic}, max_written_bytes=values.nbytes)
                draft.write((0,) * values.ndim, values)
                inputs[name] = draft.finish()
            saved = store.save(created.id, today(), {'kind': 'scene', 'title': title, 'resources': list(inputs.values()),
                                                     'runtimes': [runtime], 'state': {'scientificView': {'inputs': inputs, 'runtime': runtime}}},
                               expected_revision=1)
            self.saved[title] = saved

    def _wait_for_health(self):
        import urllib.request
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError('the orchestrator exited: ' + (self.folder / 'orchestrator.log').read_text()[-4000:])
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/api/health', timeout=2) as reply:
                    if reply.status == 200:
                        return
            except OSError:
                time.sleep(.5)
        raise AssertionError('the orchestrator did not answer within 180 s')

    def act(self, op, **fields):
        """One browser step through the page; raises with the page's own words when it fails."""
        self.driver.stdin.write(json.dumps({'op': op, **fields}) + '\n')
        self.driver.stdin.flush()
        answer = json.loads(self.driver.stdout.readline())
        if not answer['ok']:
            raise AssertionError(f'{op} {fields}: {answer["error"]}')
        return answer['out']

    def __exit__(self, *exc):
        if self.driver is not None:
            try:
                self.act('close')
            except Exception:
                pass
            try:
                self.driver.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(self.driver.pid, signal.SIGKILL)
        if self.process is not None and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGINT)
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait()
