"""Prepared real Qt/adapter regression. No dependency installation or ordinary-UI claim."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from orchestrator import owned_process


@pytest.mark.parametrize('candidate', ['scientific-animation', 'beta-scientific-tools/animation-movie'])
def test_actual_adapter_slider_applies_camera_after_steps_and_selection(candidate, tmp_path):
    location = os.environ.get('NAPARI_ANIMATION_HOST_PREPARATION')
    if not location:
        pytest.skip('requires coordinated prepared actual animation runtime; never installed by tests')
    prepared = json.loads(Path(location).read_text())
    runtime = Path(prepared['folder'])
    receipt = runtime / 'receipt.json'
    interpreter = runtime / 'bin/python'
    assert receipt.is_file() and interpreter.is_file()
    root = Path(__file__).resolve().parents[2]
    adapter = root / 'examples' / candidate / 'adapter.py'
    helper = Path(__file__).with_name('animation_slider_probe.py')
    for name in ('home', 'tmp', 'assets', 'catalog'):
        (tmp_path / name).mkdir()
    keys = tmp_path / 'empty-keys'; keys.write_text('')
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(tmp_path / 'home'), 'TMPDIR': str(tmp_path / 'tmp'),
           'QT_QPA_PLATFORM': 'cocoa' if sys.platform == 'darwin' else 'offscreen',
           'PYTHONDONTWRITEBYTECODE': '1', 'MICROMIND_HOME': str(tmp_path / 'home'),
           'MICROMIND_ASSETS': str(tmp_path / 'assets'), 'MICROMIND_CATALOG': str(tmp_path / 'catalog'),
           'MICROMIND_KEYS_FILE': str(keys), 'MICROMIND_KEYCHAIN': 'off', 'MICROMIND_CATALOG_AUTO': '0',
           'MICROMIND_REFUSE_NETWORK': '1', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
           'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1', 'VECLIB_MAXIMUM_THREADS': '1'}
    evidence = {'preparation': str(Path(location).resolve()),
                'preparationSha256': hashlib.sha256(Path(location).read_bytes()).hexdigest(),
                'receiptSha256': hashlib.sha256(receipt.read_bytes()).hexdigest(),
                'adapterSha256': hashlib.sha256(adapter.read_bytes()).hexdigest(),
                'helperSha256': hashlib.sha256(helper.read_bytes()).hexdigest()}
    (tmp_path / 'inputs.json').write_text(json.dumps(evidence, indent=2) + '\n')
    process = None
    output = 'Child did not produce captured output.'
    try:
        process = subprocess.Popen([str(interpreter), '-I', '-B', str(helper), str(adapter), str(tmp_path / 'result.json')],
                                   env=env, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        with owned_process.watching(process, 60, 'animation slider regression'):
            output = owned_process.drained(process)
        assert process.returncode == 0, output
        result = json.loads((tmp_path / 'result.json').read_text())
        assert result['passed'] and result['originalUnchanged'] and result['previousBlockedPreserved']
        assert result['adapterSha256'] == evidence['adapterSha256']
    finally:
        try:
            if process is not None:
                owned_process.terminate(process)
        finally:
            (tmp_path / 'child.log').write_text(output)
