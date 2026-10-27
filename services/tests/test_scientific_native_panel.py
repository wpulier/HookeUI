"""Native Qt component evidence; public panel/SDK qualification stays separate."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from orchestrator import owned_process, sandbox, worker_resources
from tests.waiting import HANG_S


@pytest.mark.parametrize('case',['all','gestures'])
def test_actual_confined_native_panel(tmp_path, monkeypatch, case):
    location = os.environ.get('NAPARI_REFERENCE_PYTHON')
    if not location:
        pytest.skip('requires the pinned scientific runtime; not public UI qualification')
    assert sandbox.available(), 'native panel evidence requires actual confinement'
    monkeypatch.delenv('MICROMIND_SANDBOX_READ', raising=False)
    executable = Path(location).absolute()
    probe = Path(__file__).with_name('scientific_native_panel_probe.py')
    root = probe.parents[1]
    bridge = root / 'env_worker/scientific_native_panel.py'
    runtime = root / 'env_worker/napari_cgl.py'
    classifier = root / 'env_worker/scientific_callback_errors.py'
    wire = root / 'env_worker/scientific_native_panel_wire.py'
    protected = tmp_path / 'private-marker'
    protected.write_text('Must not be readable by the contributed panel')
    work = tmp_path / 'work'
    work.mkdir()
    command = sandbox.wrap([str(executable), '-I', '-u', str(probe), str(bridge), str(runtime),
                            str(protected), str(classifier), str(wire), case],
                           [executable.parent.parent, sandbox.python_home(executable), probe, bridge, runtime, classifier, wire],
                           [work], gpu=False, children=False)
    environment = sandbox.confine({'PATH': '/usr/bin:/bin', 'QT_QPA_PLATFORM': 'offscreen',
                                  'QT_API': 'pyside6', 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
                                  'VECLIB_MAXIMUM_THREADS': '1', 'NUMBA_NUM_THREADS': '1'}, work)
    process = guard = None
    try:
        with (work / 'output').open('wb') as output:
            process = subprocess.Popen(command, cwd=work, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            guard = worker_resources.Guard(process, work, worker_resources.Limits(memory_bytes=1 << 30,
                processes=1, file_bytes=1 << 20, work_bytes=64 << 20, work_entries=512)).start()
            with owned_process.watching(process, HANG_S, 'native scientific panel'):
                process.wait(timeout=HANG_S)
            guard.close()
            guard.check()
        result = (work / 'output').read_text()
        assert process.returncode == 0, result
        value = json.loads(result.splitlines()[-1])
        for field in ('ok', 'nestedTabs', 'nativeAccessibilityNamesValuesAndFocus',
                      'syntheticInMemoryArrayUnchanged', 'nativeContextInvalidated',
                      'paintAndQueuedFailureRecovery', 'fileAndNetworkDenied', 'keyboardNavigation', 'dragWheelAndCancellation', 'failedGestureStopsBeforeRelease', 'lifecycleErrorsCaptured'):
            assert value[field] is True
        assert value['projectSaveReopen'] == 'not exercised'
    finally:
        if guard is not None:
            guard.close()
        elif process is not None and process.poll() is None:
            owned_process.terminate(process)
    assert process.poll() is not None
