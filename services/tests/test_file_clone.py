"""Native filesystem copy-on-write and explicit fallback/error boundaries."""
import ctypes
import errno
from types import SimpleNamespace

import pytest

from orchestrator import file_clone


def test_native_clone_uses_captured_source_and_has_independent_writes(tmp_path):
    source, moved, copied = (tmp_path / name for name in ('source', 'moved', 'copy'))
    source.write_bytes(b'original retained bytes')
    with source.open('rb') as stream:
        source.rename(moved)
        source.write_bytes(b'replaced path')
        if not file_clone.clone(stream.fileno(), copied):
            pytest.skip('Native clone is unavailable on this platform/filesystem')
    assert copied.read_bytes() == b'original retained bytes'
    assert copied.stat().st_ino != moved.stat().st_ino
    with source.open('rb') as stream, pytest.raises(OSError) as error:
        file_clone.clone(stream.fileno(), copied)
    assert error.value.errno == errno.EEXIST
    assert copied.read_bytes() == b'original retained bytes'
    copied.write_bytes(b'changed working copy')
    assert moved.read_bytes() == b'original retained bytes'
    moved.unlink()
    assert copied.read_bytes() == b'changed working copy'


@pytest.mark.parametrize('code,fallback', [(errno.ENOTSUP, True), (errno.EXDEV, True),
    (errno.ENOSYS, True), (errno.ENOSPC, False), (errno.EIO, False),
    (errno.EACCES, False), (errno.EEXIST, False)])
def test_only_unsupported_clone_errors_fall_back(tmp_path, monkeypatch, code, fallback):
    class Native:
        def __call__(self, *args):
            ctypes.set_errno(code)
            return -1
    monkeypatch.setattr(file_clone.sys, 'platform', 'darwin')
    monkeypatch.setattr(file_clone.ctypes, 'CDLL', lambda *a, **kw: SimpleNamespace(fclonefileat=Native()))
    source = tmp_path / 'source'
    source.write_bytes(b'kept')
    with source.open('rb') as stream:
        if fallback:
            assert file_clone.clone(stream.fileno(), tmp_path / 'copy') is False
        else:
            with pytest.raises(OSError) as error:
                file_clone.clone(stream.fileno(), tmp_path / 'copy')
            assert error.value.errno == code
    assert source.read_bytes() == b'kept' and not (tmp_path / 'copy').exists()


def test_non_native_platform_does_not_load_native_library(tmp_path, monkeypatch):
    monkeypatch.setattr(file_clone.sys, 'platform', 'linux')
    monkeypatch.setattr(file_clone.ctypes, 'CDLL', lambda *a, **kw: pytest.fail('must not load libc'))
    assert file_clone.clone(-1, tmp_path / 'unused') is False
