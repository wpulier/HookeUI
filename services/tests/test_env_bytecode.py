"""Preparation compiles bytecode once, without running package code (L6)."""
import os
import sys

from orchestrator import envs


def test_preparation_compiles_bytecode_without_running_package_code(tmp_path):
    d = tmp_path / 'generation'
    site = d / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'
    (site / 'package').mkdir(parents=True)
    (d / 'bin').mkdir()
    (d / 'bin' / 'python').symlink_to(sys.executable)
    (site / 'package' / '__init__.py').write_text('VALUE = 1\n')
    (site / 'package' / 'template.py').write_text('{% for module in modules %}\n')  # shipped invalid on purpose
    ran = tmp_path / 'ran'
    (site / 'evil.pth').write_text(f'import os; open({str(ran)!r}, "w")\n')
    assert not envs.compiled(d)
    envs.compile_bytecode(d, os.environ)
    assert list((site / 'package' / '__pycache__').glob('__init__.*.pyc'))
    assert not list((site / 'package' / '__pycache__').glob('template.*.pyc'))
    assert not ran.exists(), 'no package code runs outside the sandbox while compiling'
    assert envs.compiled(d)


def test_compiled_bytecode_is_what_a_read_only_sandboxed_worker_loads(tmp_path, monkeypatch):
    """The sandboxed worker reads its runtime read-only (no writes, PYTHONDONTWRITEBYTECODE): the bytecode made at
    preparation must be what it loads. Proof: after compiling, the source is changed with its size and time kept, so only
    a loaded .pyc can still give the compiled value."""
    import subprocess
    from pathlib import Path

    import pytest

    from orchestrator import sandbox

    if sys.platform != 'darwin' or not sandbox.available():
        pytest.skip('needs the macOS sandbox')
    monkeypatch.delenv('MICROMIND_SANDBOX_READ', raising=False)  # only what is granted below
    d = tmp_path / 'generation'
    site = d / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'
    for name in ('compiled_mod', 'plain_mod'):
        (site / name).mkdir(parents=True)
        (site / name / '__init__.py').write_text("VALUE = 'compiled'\n")
    (d / 'bin').mkdir()
    (d / 'bin' / 'python').symlink_to(sys.executable)
    envs.compile_bytecode(d, os.environ)
    for cache in (site / 'plain_mod').glob('__pycache__'):  # the control: a package left uncompiled
        for f in cache.iterdir():
            f.unlink()
        cache.rmdir()
    for name in ('compiled_mod', 'plain_mod'):
        source = site / name / '__init__.py'
        before = source.stat()
        source.write_text("VALUE = 'source!!'\n")  # the same size
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    work = tmp_path / 'work'
    work.mkdir()
    executable = Path(sys.executable)
    reads = [d, executable.parent.parent, sandbox.python_home(executable)]
    code = (f'import sys; sys.path.insert(0, {str(site)!r}); import compiled_mod, plain_mod; '
            'print(compiled_mod.VALUE, plain_mod.VALUE)')
    env = sandbox.confine({'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'}, work)
    done = subprocess.run(sandbox.wrap([str(d / 'bin' / 'python'), '-I', '-S', '-c', code], reads, [work]),
                          cwd=work, env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == ['compiled', 'source!!']  # its .pyc was loaded; the uncompiled one read its source
    assert not (site / 'plain_mod' / '__pycache__').exists()  # and nothing was written into the runtime


def test_a_runtime_prepared_before_compiling_is_compiled_once_on_its_next_check(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from orchestrator import environment_check

    d = tmp_path / 'envs' / 'entry' / 'g1'
    d.mkdir(parents=True)
    (d / envs.RECEIPT).write_text('{}')
    m = SimpleNamespace(runner='python-env', name='Entry', key='entry')
    monkeypatch.setattr(envs, 'spec', lambda m: {'python': '3.12'})
    monkeypatch.setattr(envs, '_require_machine', lambda m, probe=False: None)
    monkeypatch.setattr(envs, 'folder', lambda m, environ=None: d)
    monkeypatch.setattr(environment_check, 'refresh', lambda *a, **k: None)
    compiled, said = [], []
    monkeypatch.setattr(envs, 'compile_bytecode', lambda d, environ, stop: (compiled.append(d), (d / envs.COMPILED).write_text('x')))
    assert envs.build(m, say=said.append) == d
    assert envs.build(m, say=said.append) == d
    assert compiled == [d] and said == ['compiling its Python code ahead of time, so its tools start faster']


def _generation(tmp_path):
    d = tmp_path / 'generation'
    site = d / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'
    (site / 'package').mkdir(parents=True)
    (site / 'package' / '__init__.py').write_text('VALUE = 1\n')
    (d / 'bin').mkdir()
    (d / 'bin' / 'python').symlink_to(sys.executable)
    return d, site


def test_an_incomplete_compile_is_never_marked_done(tmp_path):
    import pytest

    d, site = _generation(tmp_path)
    (site / 'package').chmod(0o555)  # its __pycache__ can't be written: a valid source stays uncompiled
    try:
        with pytest.raises(envs.Failed, match='compiling its Python code failed'):
            envs.compile_bytecode(d, os.environ)
    finally:
        (site / 'package').chmod(0o755)
    assert not envs.compiled(d)
    envs.compile_bytecode(d, os.environ)  # tried again, it completes and is marked
    assert envs.compiled(d)


def test_the_compile_runs_at_background_priority(tmp_path, monkeypatch):
    from pathlib import Path

    d, _site = _generation(tmp_path)
    ran = []
    monkeypatch.setattr(envs, '_run', lambda cmd, *a, **k: ran.append(cmd) or '')
    envs.compile_bytecode(d, os.environ)
    if Path(envs.BACKGROUND).exists():
        assert ran[0][:2] == [envs.BACKGROUND, '-b']
    assert ran[0][ran[0].index(str(d / 'bin' / 'python')) + 1:][:2] == ['-I', '-S']


def test_a_failed_repair_never_fails_the_check_and_is_tried_again(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from orchestrator import environment_check

    d = tmp_path / 'envs' / 'entry' / 'g1'
    d.mkdir(parents=True)
    (d / envs.RECEIPT).write_text('{}')
    m = SimpleNamespace(runner='python-env', name='Entry', key='entry')
    monkeypatch.setattr(envs, 'spec', lambda m: {'python': '3.12'})
    monkeypatch.setattr(envs, '_require_machine', lambda m, probe=False: None)
    monkeypatch.setattr(envs, 'folder', lambda m, environ=None: d)
    monkeypatch.setattr(environment_check, 'refresh', lambda *a, **k: None)
    tries = []

    def failing(d, environ, stop):
        tries.append(d)
        raise envs.Failed('compiling its Python code timed out')

    monkeypatch.setattr(envs, 'compile_bytecode', failing)
    said = []
    assert envs.build(m, say=said.append) == d  # the check stands: the runtime was already proven
    assert said[-1].startswith("its Python code wasn't compiled ahead of time this time")
    assert envs.build(m, say=said.append) == d and len(tries) == 2  # not marked, so the next check tries again


def test_bytecode_preparation_never_fans_out_across_the_machine(tmp_path, monkeypatch):
    import concurrent.futures
    import pytest

    _d, site = _generation(tmp_path)
    (site / 'useful.py').write_text('VALUE = 3\n')
    def no_pool(*args, **kwargs):
        raise AssertionError('background preparation must not spawn a compiler per CPU')
    monkeypatch.setattr(concurrent.futures, 'ProcessPoolExecutor', no_pool)
    monkeypatch.setattr(sys, 'argv', ['compile', str(site)])
    with pytest.raises(SystemExit) as stopped:
        exec(envs.COMPILE, {'__name__': '__main__'})
    assert stopped.value.code == 0
    assert list((site / '__pycache__').glob('useful.*.pyc'))
