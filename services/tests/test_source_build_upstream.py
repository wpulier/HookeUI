"""Opt-in actual supplied napari source build, with no dependency download in this test."""
import hashlib
import os
from pathlib import Path
import sys
import zipfile

import pytest

from gpu_worker import manifests
from orchestrator import source_build, sandbox


def test_supplied_napari_source_reproduces_confined_wheel(tmp_path, monkeypatch):
    preparation = os.environ.get('NAPARI_SOURCE_PREPARATION')
    if not preparation:
        pytest.skip('requires the explicitly prepared source archive and pinned build environment')
    if sys.platform != 'darwin' or not sandbox.available():
        pytest.fail('requested upstream source proof requires actual macOS confinement')
    monkeypatch.delenv('MICROMIND_SANDBOX_READ', raising=False)
    preparation = Path(preparation).absolute()
    source = preparation / 'source.zip'
    with zipfile.ZipFile(source) as archive:
        assert archive.comment == b'ebfc4a0a1e5386f41582761204f416fffe6cd0b6'
    builder, problems = manifests.read(preparation / 'builder/builder/model.toml', 'mine')
    assert builder is not None, problems
    result = source_build.build(source,
        sha256='3a29672c608ee6a8e84a488d7c5c122915f5c852757d401736f668bf60c740a6',
        subdirectory='napari-main', builder=builder, destination=tmp_path/'attempt',
        environ={'HOME': str(preparation/'home'), 'MICROMIND_CATALOG': str(preparation/'catalog')})
    assert result.name == 'napari-0.7.1.dev0-py3-none-any.whl'
    assert hashlib.sha256(result.read_bytes()).hexdigest() == 'ea1b1d4681556c3d811a334d60ce3f812e91600081eec74a1874d232fecbaf4f'
