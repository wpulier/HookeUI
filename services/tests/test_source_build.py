import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import zipfile

import pytest

from orchestrator import source_build as builds, sandbox, generation_leases
from tests.waiting import HANG_S, until


def archive(path, entries):
    with zipfile.ZipFile(path, 'w') as output:
        for name, body in entries.items():
            output.writestr(name, body)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wheel(path):
    contents = {'example.py': b'value = 1\n', 'example-1.dist-info/METADATA': b'Name: example\nVersion: 1\n',
                'example-1.dist-info/WHEEL': b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
    rows = [[name, 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b'=').decode(),
             str(len(body))] for name, body in contents.items()]
    rows.append(['example-1.dist-info/RECORD', '', ''])
    text = io.StringIO()
    csv.writer(text).writerows(rows)
    return archive(path, {**contents, 'example-1.dist-info/RECORD': text.getvalue()})


@pytest.mark.parametrize('name', ['../secret', '/absolute', 'src/../file', 'src//file', 'src/./file',
                                 'C:/file', 'src\\file', 'src/file\x00bad'])
def test_source_paths_are_canonical(name):
    with pytest.raises(builds.Failed):
        builds._path(name)


def test_source_extracts_only_regular_bounded_members(tmp_path, monkeypatch):
    source = tmp_path / 'source.zip'
    archive(source, {'src/pyproject.toml': b'a', 'src/nested/module.py': b'b'})
    builds.unpack(source, tmp_path / 'good')
    assert (tmp_path / 'good/src/nested/module.py').read_bytes() == b'b'
    monkeypatch.setattr(builds, 'MAX_EXPANDED', 1)
    with pytest.raises(builds.Failed, match='expanded'):
        builds.unpack(source, tmp_path / 'bad')
    assert not (tmp_path / 'bad').exists()


@pytest.mark.parametrize('kind', ['symlink', 'duplicate', 'case', 'fifo'])
def test_source_rejects_linked_or_ambiguous_members(tmp_path, kind):
    source = tmp_path / 'source.zip'
    with zipfile.ZipFile(source, 'w') as out:
        item = zipfile.ZipInfo('src/a')
        if kind in ('symlink', 'fifo'):
            item.external_attr = ((stat.S_IFLNK if kind == 'symlink' else stat.S_IFIFO) | 0o777) << 16
        out.writestr(item, b'outside')
        if kind in ('duplicate', 'case'):
            out.writestr('src/a' if kind == 'duplicate' else 'SRC/A', b'again')
    with pytest.raises(builds.Failed):
        builds.unpack(source, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


def test_wheel_requires_complete_exact_record(tmp_path):
    valid = tmp_path / 'example-1-py3-none-any.whl'
    wheel(valid)
    builds.check_wheel(valid)
    with zipfile.ZipFile(valid) as source:
        contents = {name: source.read(name) for name in source.namelist()}
    for name, change in [('modified', {'example.py': b'value = 2\n'}), ('missing', {'new.py': b'pass'}),
                         ('record', {'example-1.dist-info/RECORD': 'wrong,,\n'})]:
        path = tmp_path / name / 'example-1-py3-none-any.whl'
        path.parent.mkdir()
        archive(path, {**contents, **change})
        with pytest.raises(builds.Failed):
            builds.check_wheel(path)


@pytest.fixture
def builder(tmp_path, monkeypatch):
    monkeypatch.delenv('MICROMIND_SANDBOX_READ', raising=False)
    folder = Path(sys.prefix)
    manifest = SimpleNamespace(namespace='mine', env={'python': 'test'}, members=(), key='mine/builder')
    lock = tmp_path / 'env.lock'
    lock.write_text('test-builder')
    receipt = {'model': manifest.key, 'generation': folder.name,
               'lockSha256': hashlib.sha256(lock.read_bytes()).hexdigest()}
    original = Path.read_bytes
    monkeypatch.setattr(Path, 'read_bytes', lambda path: json.dumps(receipt).encode()
                        if path == folder / builds.envs.RECEIPT else original(path))
    monkeypatch.setattr(builds.envs, 'folder', lambda *args: folder)
    monkeypatch.setattr(builds.envs, 'lock_file', lambda *args: lock)
    return manifest


def build_fixture(tmp_path, builder, backend):
    source = tmp_path / 'source.zip'
    digest = archive(source, {'src/pyproject.toml': '[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n',
                              'src/backend.py': backend})
    return builds.build(source, sha256=digest, subdirectory='src', builder=builder,
                        destination=tmp_path / 'attempt')


@pytest.mark.skipif(sys.platform != 'darwin' or not sandbox.available(), reason='actual macOS confinement required')
def test_actual_build_hook_cannot_read_write_network_or_fork(tmp_path, builder, monkeypatch):
    terminate = builds.owned_process.terminate
    def live_only(process):
        assert process.poll() is None, 'must not signal an already reaped worker group'
        return terminate(process)
    monkeypatch.setattr(builds.owned_process, 'terminate', live_only)
    template = tmp_path / 'template.whl'
    wheel(template)
    encoded = base64.b64encode(template.read_bytes()).decode()
    protected = tmp_path / 'protected'
    protected.write_text('private canary')
    target = tmp_path / 'unowned'
    backend = f'''
import base64, os, socket
from pathlib import Path
def get_requires_for_build_wheel(config_settings=None):
    return []
def build_wheel(wheel_directory, config_settings=None):
    for action in (lambda: Path({str(protected)!r}).read_bytes(),
                   lambda: Path({str(target)!r}).write_text('bad'),
                   lambda: socket.create_connection(('127.0.0.1', 9)), os.fork):
        try:
            action()
        except OSError:
            pass
        else:
            raise RuntimeError('sandbox allowed forbidden operation')
    name = 'example-1-py3-none-any.whl'
    (Path(wheel_directory)/name).write_bytes(base64.b64decode({encoded!r}))
    return name
'''
    result = build_fixture(tmp_path, builder, backend)
    builds.check_wheel(result)
    assert not target.exists()
    receipt = json.loads((result.parent / 'build.json').read_bytes())
    assert receipt['ok'] and receipt['wheelSha256'] == hashlib.sha256(result.read_bytes()).hexdigest()
    assert result.stat().st_mode & 0o222 == 0


@pytest.mark.skipif(sys.platform != 'darwin' or not sandbox.available(), reason='actual macOS confinement required')
@pytest.mark.parametrize('backend,reason', [
    ('def get_requires_for_build_wheel(config_settings=None): return ["missing_hooke_build_requirement_123==1"]', 'Missing build dependency'),
    ('def get_requires_for_build_wheel(config_settings=None): return ["x @ https://example.com/x.whl"]', 'cannot fetch'),
    ('def build_wheel(wheel_directory, config_settings=None): raise RuntimeError("fixture hook failure")', 'fixture hook failure'),
    ('from pathlib import Path\ndef build_wheel(wheel_directory, config_settings=None):\n p=Path(wheel_directory);p.rmdir();p.symlink_to("/etc");return "fake.whl"', 'replaced its output|link or special file'),
])
def test_hook_failures_remain_private_and_actionable(tmp_path, builder, backend, reason):
    with pytest.raises(builds.Failed, match=reason):
        build_fixture(tmp_path, builder, backend)
    receipt = json.loads((tmp_path / 'attempt/build.json').read_bytes())
    assert not receipt['ok'] and __import__('re').search(reason, receipt['why'])
    assert not list((tmp_path / 'attempt').glob('*.whl'))


def test_hash_mismatch_runs_no_hook(tmp_path, builder, monkeypatch):
    source = tmp_path / 'source.zip'
    archive(source, {'src/pyproject.toml': 'bad'})
    monkeypatch.setattr(builds.subprocess, 'Popen', lambda *a, **k: pytest.fail('hook ran'))
    with pytest.raises(builds.Failed, match='SHA256'):
        builds.build(source, sha256='a' * 64, subdirectory='src', builder=builder, destination=tmp_path / 'attempt')


def test_refuses_test_sandbox_override_before_preparation(tmp_path, builder, monkeypatch):
    monkeypatch.setenv('MICROMIND_SANDBOX_READ', str(tmp_path))
    with pytest.raises(builds.Failed, match='unmodified'):
        builds.build(tmp_path / 'source', sha256='a' * 64, subdirectory='src', builder=builder,
                     destination=tmp_path / 'attempt')
    assert not (tmp_path / 'attempt').exists()


@pytest.mark.skipif(sys.platform != 'darwin' or not sandbox.available(), reason='actual macOS confinement required')
def test_log_failure_reaps_before_environment_retirement(tmp_path, builder, monkeypatch):
    observed, processes = [], []
    popen = builds.subprocess.Popen
    folder = builds.envs.folder(builder)

    def start(*args, **kwargs):
        process = popen(*args, **kwargs)
        if str(args[0][0]) != sandbox.EXEC:
            return process
        processes.append(process)
        def retire():
            with generation_leases.withdraw(folder):
                observed.append(process.poll())
        generation_leases.when_idle(folder, retire)
        assert observed == []
        return process

    monkeypatch.setattr(builds.subprocess, 'Popen', start)
    monkeypatch.setattr(builds, 'MAX_LOG', 16384)
    backend = 'def build_wheel(wheel_directory, config_settings=None):\n while True: print("x"*8192, flush=True)\n'
    with pytest.raises(builds.Failed, match='log allowance'):
        build_fixture(tmp_path, builder, backend)
    assert len(observed) == 1 and observed[0] is not None
    assert processes[0].poll() is not None


def test_already_cancelled_build_starts_nothing(tmp_path, builder, monkeypatch):
    monkeypatch.setattr(builds.subprocess, 'Popen', lambda *a, **k: pytest.fail('cancelled build ran'))
    with pytest.raises(builds.owned_process.Stopped, match='cancelled'):
        builds.build(tmp_path/'absent.zip', sha256='a'*64, subdirectory='src', builder=builder,
                     destination=tmp_path/'attempt', stop=lambda: True)
    assert not (tmp_path/'attempt').exists()


@pytest.mark.skipif(sys.platform != 'darwin' or not sandbox.available(), reason='actual macOS confinement required')
def test_cancel_running_build_releases_only_its_owned_process_and_keeps_retry_possible(tmp_path, builder):
    source = tmp_path/'source.zip'
    digest = archive(source, {'src/pyproject.toml': '[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n',
                              'src/backend.py': 'from pathlib import Path\nimport time\ndef build_wheel(wheel_directory, config_settings=None):\n Path("started").write_text("yes")\n while True: time.sleep(1)\n'})
    stop = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(builds.build, source, sha256=digest, subdirectory='src', builder=builder,
                             destination=tmp_path/'attempt', stop=stop.is_set)
        until(lambda: (tmp_path/'attempt/work/source/src/started').exists() or future.done())
        stop.set()
        with pytest.raises(builds.Failed, match='cancelled'):
            future.result(timeout=HANG_S)
    with generation_leases.withdraw(builds.envs.folder(builder)):
        pass
    assert not json.loads((tmp_path/'attempt/build.json').read_text())['ok']


def test_cli_prepares_only_in_new_private_destination(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv('MICROMIND_SANDBOX_READ', raising=False)
    monkeypatch.setattr(sandbox, 'available', lambda: True)
    builder = tmp_path/'builder'
    builder.mkdir()
    (builder/'model.toml').write_text('''id="builder"
name="Build tools"
runs="this-mac"
version="1"
licence=["builder"]
kind="tool"
what="Prepare build tools"
component={what="Test wrapper", licence="MIT", url="https://example.com", tier="open"}
env={python="3.12.11", lock="env.lock", imports=["packaging"]}
''')
    (builder/'env.lock').write_text('pinned build tools fixture')
    source = tmp_path/'source.zip'
    digest = archive(source, {'src/pyproject.toml': '[build-system]\nrequires=[]\n'})
    live = tmp_path/'live'
    live.mkdir()
    (live/'retained').write_text('untouched')
    monkeypatch.setenv('MICROMIND_CATALOG', str(live))
    calls = []
    def prepare(manifest, environ, say):
        calls.append(environ)
        assert manifest.namespace == 'mine'
        assert Path(manifest.folder).is_relative_to(tmp_path/'attempt')
        assert environ == {'HOME': str(tmp_path/'attempt/home'), 'MICROMIND_CATALOG': str(tmp_path/'attempt/catalog')}
    monkeypatch.setattr(builds.envs, 'build', prepare)
    monkeypatch.setattr(builds, 'build', lambda *a, **k: tmp_path/'attempt/result/example.whl')
    argv = [str(source), '--sha256', digest, '--subdirectory', 'src', '--builder', str(builder), '--into', str(tmp_path/'attempt')]
    assert builds.main(argv) == 0
    assert len(calls) == 1 and 'Private wheel prepared' in capsys.readouterr().out
    assert (tmp_path/'attempt/builder/builder/env.lock').read_text() == 'pinned build tools fixture'
    assert list(live.iterdir()) == [live/'retained']
    assert builds.main(argv) == 2
    assert len(calls) == 1
    argv[2] = 'a'*64
    argv[-1] = str(tmp_path/'bad-hash')
    assert builds.main(argv) == 2
    assert len(calls) == 1
