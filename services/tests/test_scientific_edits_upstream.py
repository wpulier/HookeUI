"""Opt-in pinned napari model execution; no browser qualification.

NAPARI_EDIT_PROOF_ATTEMPT names the audited private source-substitution attempt.
No dependencies are downloaded; absence is a skip and cannot qualify P4.
"""
import hashlib
import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pytest

from env_worker.server import read_message, write_message
from orchestrator import owned_process, sandbox, worker_resources
from orchestrator.native_regions import NativeRegionInputs
from orchestrator.scientific_edits import ViewEdits
from orchestrator.scientific_projects import ProjectStore
from tests.waiting import HANG_S


@pytest.mark.parametrize('runtime_kind', ['source-candidate', 'managed-wheel'])
def test_actual_napari_paint_retains_exact_values_through_confined_edit_pipe(tmp_path, monkeypatch, runtime_kind):
    location = os.environ.get('NAPARI_EDIT_PROOF_ATTEMPT' if runtime_kind == 'source-candidate' else 'NAPARI_REFERENCE_PYTHON')
    if not location:
        pytest.skip('requires the explicitly supplied, audited napari source candidate')
    if sys.platform != 'darwin' or not sandbox.available():
        pytest.fail('requested upstream edit proof requires actual macOS confinement')
    monkeypatch.delenv('MICROMIND_SANDBOX_READ', raising=False)
    if runtime_kind == 'source-candidate':
        attempt = Path(location).absolute()
        config = json.loads((attempt / 'configuration.json').read_bytes())
        assert config['revision'] == 'ebfc4a0a1e5386f41582761204f416fffe6cd0b6'
        runtime = attempt / 'candidate-venv'
        site = runtime / 'lib/python3.12/site-packages'
        assert site == Path(config['site'])
        for relative, digest in config['source_hashes'].items():
            path = site / relative
            assert path.is_relative_to(site) and '..' not in Path(relative).parts and not path.is_symlink()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    else:
        runtime = Path(location).absolute().parent.parent
        site = runtime / 'lib/python3.12/site-packages'
        receipt = json.loads((runtime / 'receipt.json').read_bytes())
        assert receipt['generation'] == runtime.name
    # This pinned source writes 79 icons × two opacities × two themes at import.
    # Its candidate admission needs 512 entries; the generic 256-entry policy is unchanged.
    assert len(list((site / 'napari/resources/icons').glob('*.svg'))) == 79
    executable = runtime / 'bin/python'
    work, bootstrap = tmp_path / 'worker', tmp_path / 'bootstrap'
    work.mkdir()
    bootstrap.mkdir()
    services = Path(__file__).parents[1]
    for package, names in {'env_worker': ['server.py', 'native_edits.py', 'native_regions.py', 'region_reader.py', 'napari_cgl.py'],
                           'gpu_worker': ['semantic_resources.py']}.items():
        folder = bootstrap / package
        folder.mkdir()
        (folder / '__init__.py').touch()
        for name in names:
            shutil.copy2(services / package / name, folder / name)
    child = bootstrap / 'paint.py'
    shutil.copy2(Path(__file__).with_name('native_edit_napari_probe.py'), child)
    store = ProjectStore(tmp_path / 'retained')
    first = store.create('s1', {'kind': 'scene', 'title': 'Labels', 'state': {}, 'resources': [], 'runtimes': []})
    original = np.zeros((6, 24, 28), dtype='<u2')
    original[2, 8, 11], original[5, 22, 27] = 99, 65000
    draft = store.array_draft(first.id, 's1', component='array.labels', shape=original.shape, dtype='<u2',
                              axes=['z', 'y', 'x'], chunks=[1, 12, 14], scientific={'semantic': 'instance-labels'},
                              max_written_bytes=original.nbytes)
    for index in itertools.product(*(range(n) for n in draft.grid)):
        slices = tuple(slice(i * c, (i + 1) * c) for i, c in zip(index, draft.chunks))
        draft.write(index, original[slices])
    reference = draft.finish()
    accepted = store.save(first.id, 's1', {**first.document, 'resources': [reference]}, expected_revision=1)
    reads = NativeRegionInputs({'labels': store.array_source(first.id, 's1', accepted.revision, reference)})
    view = ViewEdits(store, first.id, 's1', accepted.revision, reference)
    frame = view.present(view='fixture-view', layer='labels', context={'evidence': 'synthetic frame receipt'})
    view.rendered(frame)
    edit = view.begin(frame, max_written_bytes=2 * 1 * 12 * 14 * 2)
    grants = {'inputs': reads.specifications(), 'edit': edit.specification()}
    args = ['--managed-reference'] if runtime_kind == 'managed-wheel' else []
    command = sandbox.wrap([str(executable), '-u', str(child), *args],
                           [runtime, sandbox.python_home(executable), bootstrap], [work], gpu=False, children=False)
    env = sandbox.confine({'PATH': '/usr/bin:/bin', 'QT_QPA_PLATFORM': 'offscreen', 'QT_API': 'pyside6',
                           'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'VECLIB_MAXIMUM_THREADS': '1',
                           'NUMBA_NUM_THREADS': '1'}, work)
    process = guard = None
    try:
        with (work / 'stderr.log').open('wb') as errors:
            process = subprocess.Popen(command, cwd=work, env=env, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=errors, start_new_session=True)
            guard = worker_resources.Guard(process, work, worker_resources.Limits(work_entries=512)).start()
            with owned_process.watching(process, HANG_S, 'upstream label edit proof'):
                write_message(process.stdin, grants)
                def admit(header, size):
                    if type(header) is dict and header.get('op') == 'read-native-region':
                        if size:
                            raise ValueError('Native reads do not accept a payload')
                    else:
                        edit.admit_message(header, size)
                for _ in range(4):
                    try:
                        header, body = read_message(process.stdout, max_header=16384, max_payload=1 << 20, admit=admit)
                    except EOFError:
                        guard.check()
                        pytest.fail(f'upstream worker closed its pipe ({process.poll()}): ' + (work / 'stderr.log').read_text())
                    reply, payload = (reads.answer(header, len(body)) if header['op'] == 'read-native-region'
                                      else edit.answer(header, body))
                    write_message(process.stdin, reply, payload)
                process.stdin.close()
                process.wait(timeout=HANG_S)
            assert process.returncode == 0, (work / 'stderr.log').read_text()
            guard.check()
        reopened = ProjectStore(store.root)
        saved = reopened.read(first.id, 's1')
        assert saved.revision == accepted.revision + 1
        current = saved.document['resources'][0]
        result = reopened.array_source(first.id, 's1', saved.revision, current).read(tuple(slice(0, n) for n in original.shape))
        expected = original.copy()
        offsets = [(-1, -1), (-1, 0), (-1, 1), (0, -2), (0, -1), (0, 0), (0, 1), (0, 2),
                   (1, -1), (1, 0), (1, 1)]
        for center in (10, 13):
            for dy, dx in offsets:
                if not expected[2, 8 + dy, center + dx]:
                    expected[2, 8 + dy, center + dx] = 7
        assert np.count_nonzero(expected != original) == 19
        assert result.tobytes() == expected.tobytes()
        before = reopened.array_source(first.id, 's1', accepted.revision, reference).read(tuple(slice(0, n) for n in original.shape))
        assert before.tobytes() == original.tobytes()
    finally:
        reads.close()
        view.close()
        if guard is not None:
            guard.close()
        elif process is not None:
            owned_process.terminate(process)
        if process is not None:
            for stream in (process.stdin, process.stdout):
                stream.close()
