"""Actual confined Qt factories use the shared declaration, with synthetic data."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from env_worker.server import read_message
from orchestrator import owned_process, sandbox, scientific_host, worker_resources
from tests.waiting import HANG_S


def test_actual_label_and_point_consumers_declare_outputs_without_host_edits(tmp_path, monkeypatch):
    location = os.environ.get('NAPARI_NATIVE_HOST_PREPARATION')
    if not location:
        pytest.skip('requires independently prepared confined native runtime')
    prepared = json.loads(Path(location).read_text())
    executable = Path(prepared['folder']) / 'bin/python'
    assert sandbox.available()
    monkeypatch.delenv('MICROMIND_SANDBOX_READ', raising=False)
    work = tmp_path / 'work'; work.mkdir()
    bootstrap = tmp_path / 'bootstrap'; scientific_host._bootstrap(bootstrap)
    probe = Path(__file__).with_name('scientific_output_meanings_probe.py')
    command = sandbox.wrap([str(executable), '-I', '-u', str(probe), str(bootstrap)],
        [executable.parent.parent, sandbox.python_home(executable), probe, bootstrap],
        [work], gpu=True, children=False)
    environment = sandbox.confine({'PATH':'/usr/bin:/bin','QT_QPA_PLATFORM':'offscreen',
        'QT_API':'pyside6','NAPARI_ASYNC':'0','NAPARI_CONFIG':str(work/'napari.yaml'),
        'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','VECLIB_MAXIMUM_THREADS':'1','NUMBA_NUM_THREADS':'1'},work)
    process = guard = None
    try:
        with (work/'stderr.log').open('wb') as errors:
            process = subprocess.Popen(command,cwd=work,env=environment,stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,stderr=errors,start_new_session=True,close_fds=True)
            guard = worker_resources.Guard(process,work,worker_resources.Limits(memory_bytes=1<<30,work_entries=512)).start()
            with owned_process.watching(process,HANG_S,'declared output consumers'):
                try:
                    header, body = read_message(process.stdout,max_header=1024,max_payload=0)
                except EOFError:
                    pytest.fail((work/'stderr.log').read_text()[-8000:])
                assert header == {'ok':True,'consumers':['labels','points']} and not body
                process.wait(timeout=HANG_S)
            guard.close();guard.check()
        assert process.returncode == 0,(work/'stderr.log').read_text()
    finally:
        if guard is not None:guard.close()
        elif process is not None:owned_process.terminate(process)
        if process is not None:
            process.stdin.close();process.stdout.close()
