"""Pinned upstream tests, not browser or clean-machine qualification."""
import os
from pathlib import Path
import shutil
import subprocess
import pytest
from orchestrator import sandbox, owned_process, worker_resources
from tests.waiting import HANG_S


def test_confined_upstream_labels(tmp_path, monkeypatch):
    location = os.environ.get('NAPARI_REFERENCE_PYTHON')
    if not location:
        pytest.skip('requires the prepared pinned napari runtime')
    assert sandbox.available()
    monkeypatch.delenv('MICROMIND_SANDBOX_READ', raising=False)
    executable = Path(location)
    probe = Path(__file__).with_name('scientific_labels_probe.py')
    bootstrap = tmp_path / 'bootstrap'
    package = bootstrap / 'env_worker'
    package.mkdir(parents=True)
    (package / '__init__.py').touch()
    for name in ('scientific_labels.py','scientific_label_history.py','scientific_layer_exports.py'):
        shutil.copy2(probe.parents[1] / 'env_worker' / name, package / name)
    work = tmp_path / 'work'; work.mkdir()
    command = sandbox.wrap([str(executable),'-I','-u',str(probe),str(bootstrap)],
        [executable.parent.parent,sandbox.python_home(executable),probe,bootstrap],[work],children=False)
    environment = sandbox.confine({'PATH':'/usr/bin:/bin','QT_QPA_PLATFORM':'offscreen','QT_API':'pyside6',
        'NAPARI_ASYNC':'0','NAPARI_CONFIG':str(work/'napari.yaml'),'OMP_NUM_THREADS':'1',
        'OPENBLAS_NUM_THREADS':'1','VECLIB_MAXIMUM_THREADS':'1','NUMBA_NUM_THREADS':'1'},work)
    with (work/'output.log').open('wb') as output:
        process = subprocess.Popen(command,cwd=work,env=environment,stdout=output,stderr=subprocess.STDOUT,
            start_new_session=True,close_fds=True)
        guard = worker_resources.Guard(process,work,worker_resources.Limits(memory_bytes=1<<30,work_entries=512)).start()
        try:
            with owned_process.watching(process,HANG_S,'native label edit qualification'):
                process.wait(timeout=HANG_S)
            guard.check()
        finally:
            owned_process.terminate(process); guard.close()
    assert process.returncode == 0, (work/'output.log').read_text()[-12000:]
    assert 'external-change refusal and admission passed' in (work/'output.log').read_text()
