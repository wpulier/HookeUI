"""Portability findings (docs/research/2026-10-20-h11-linux-windows-feasibility.md §4.2, §4.3), fixed and kept fixed on
every platform: the refusal's words, the file-digest cache under a coarse clock, and file locks without fcntl."""
import errno
import hashlib
import importlib
import sys
import time
from types import SimpleNamespace

from orchestrator import envs, sandbox


def test_the_refusal_names_this_computer_not_a_mac_elsewhere():
    m = SimpleNamespace(name="Image tools")
    mac = sandbox.refused(m, "darwin")
    assert mac == ("Image tools's code wasn't reviewed, and macOS's sandbox it must run in isn't available on this "
                   "Mac: it is not run without it")
    for platform, os_name in (("linux", "Linux"), ("win32", "Windows")):
        words = sandbox.refused(m, platform)
        assert "Mac" not in words and "macOS" not in words and os_name in words
        assert "wasn't reviewed" in words and "sandbox" in words and "not run" in words


class Stamped:
    """A file whose stat never changes (a coarse clock, a reused inode, the mtime restored) while its bytes do."""

    def __init__(self, data: bytes, ctime_ns: int):
        self.data, self.st = data, SimpleNamespace(st_dev=1, st_ino=7, st_size=len(data), st_mtime_ns=5,
                                                   st_ctime_ns=ctime_ns)

    def stat(self):
        return self.st

    def read_bytes(self):
        return self.data

    def __str__(self):
        return "/same/path"


def test_a_file_replaced_within_one_clock_tick_is_never_served_its_old_digest(monkeypatch):
    monkeypatch.setattr(envs, "_SHAS", {})
    f = Stamped(b"first", ctime_ns=time.time_ns())
    assert envs._file_sha(f) == hashlib.sha256(b"first").digest()
    f.data = b"other"                                   # same size, same stat: only the bytes changed
    assert envs._file_sha(f) == hashlib.sha256(b"other").digest()


def test_an_old_unchanged_file_is_still_read_once(monkeypatch):
    monkeypatch.setattr(envs, "_SHAS", {})
    f = Stamped(b"steady", ctime_ns=time.time_ns() - 10 * envs.RACY_NS)
    first = envs._file_sha(f)
    f.read_bytes = lambda: (_ for _ in ()).throw(AssertionError("read again"))
    assert envs._file_sha(f) == first


def test_without_fcntl_every_lock_refuses_in_words(monkeypatch):
    from orchestrator import file_locks
    monkeypatch.setitem(sys.modules, "fcntl", None)          # as on Windows: the import fails
    standin = importlib.reload(file_locks).fcntl
    try:
        assert standin.__class__.__name__ == "_NoFlock"
        for op in (standin.LOCK_EX, standin.LOCK_SH | standin.LOCK_NB, standin.LOCK_UN):
            try:
                standin.flock(3, op)
            except OSError as e:
                assert e.errno == errno.ENOSYS and "Windows" in str(e)
            else:
                raise AssertionError("a lock was taken without fcntl")
    finally:
        monkeypatch.undo()                                    # the original fcntl module back, then the real re-export
        importlib.reload(file_locks)


def test_with_fcntl_the_lockers_use_the_standard_module():
    if sys.platform == "win32":
        return
    import fcntl
    from orchestrator import bundle_review, downloads, publish_api, scientific_projects, scientific_retention
    for module in (bundle_review, downloads, publish_api, scientific_projects, scientific_retention):
        assert module.fcntl is fcntl
