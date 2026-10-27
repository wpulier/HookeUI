import os
from pathlib import Path
import subprocess

import pytest

from env_worker.scientific_controls import Unsupported, _number, _same, _text
from orchestrator import owned_process, sandbox, worker_resources
from tests.waiting import HANG_S


@pytest.mark.parametrize('value', [True, None, '1', float('inf'), float('nan'), 10**1000, 2**53])
def test_controls_reject_nonfinite_or_inexact_numbers(value):
    with pytest.raises(Unsupported):
        _number(value)


def test_choices_never_compare_arbitrary_python_objects():
    class Choice:
        def __eq__(self, other):
            raise AssertionError('must not run arbitrary equality')
    first, other = Choice(), Choice()
    assert _same(first, first) and not _same(first, other)
    assert not _same(True, 1) and _same('layer', 'layer')
    with pytest.raises(Unsupported):
        _text('x' * 1001)


def test_actual_confined_dynamic_controls(tmp_path, monkeypatch):
    location = os.environ.get('NAPARI_REFERENCE_PYTHON')
    if not location:
        pytest.skip('requires the prepared pinned scientific runtime; not UI qualification')
    assert sandbox.available(), 'the requested actual callback probe requires macOS confinement'
    monkeypatch.delenv('MICROMIND_SANDBOX_READ', raising=False)
    executable = Path(location).absolute()
    probe = Path(__file__).with_name('scientific_controls_probe.py')
    bridge = probe.parents[1] / 'env_worker/scientific_controls.py'
    runtime = probe.parents[1] / 'env_worker/napari_cgl.py'
    work = tmp_path / 'work'
    work.mkdir()
    command = sandbox.wrap([str(executable), '-I', '-u', str(probe), str(bridge), str(runtime)],
                           [executable.parent.parent, sandbox.python_home(executable), probe, bridge, runtime],
                           [work], gpu=False, children=False)
    environment = sandbox.confine({'PATH': '/usr/bin:/bin', 'QT_QPA_PLATFORM': 'offscreen',
                                    'QT_API': 'pyside6', 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
                                    'VECLIB_MAXIMUM_THREADS': '1', 'NUMBA_NUM_THREADS': '1'}, work)
    process = guard = None
    try:
        with (work / 'output').open('wb') as output:
            process = subprocess.Popen(command, cwd=work, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            guard = worker_resources.Guard(process, work, worker_resources.Limits(work_entries=512)).start()
            with owned_process.watching(process, HANG_S, 'scientific control callbacks'):
                process.wait(timeout=HANG_S)
            guard.close()
            guard.check()
        result = (work / 'output').read_text()
        assert process.returncode == 0, result
        import json
        assert json.loads(result.splitlines()[-1]) == {'ok': True, 'callbacks': ['second', 'first'],
                                                      'scientificValuesUnchanged': True}
    finally:
        if guard is not None:
            guard.close()
        elif process is not None and process.poll() is None:
            owned_process.terminate(process)
