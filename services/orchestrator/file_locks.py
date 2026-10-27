"""`fcntl` where the OS has it; on Windows, a stand-in whose locks refuse in words (H11, Windows gap G3).

Five modules lock files with `fcntl.flock` (downloads, publish_api, bundle_review, scientific_projects,
scientific_retention). Windows has no `fcntl`, so importing them there failed, and with them the 40 modules that import
them (packaging/windows/import_probe.py). They import `fcntl` from here instead: on macOS and Linux it *is* the
standard module, unchanged. On Windows every module imports, and any attempt to take a lock raises OSError saying
that file locking isn't implemented on Windows yet, so nothing runs unlocked (fail closed). LockFileEx with the same
shared/exclusive/non-blocking meaning is the Windows implementation to write and test on a Windows machine.
"""
from __future__ import annotations

import errno

try:
    import fcntl  # noqa: F401 - re-exported
except ImportError:  # Windows
    class _NoFlock:
        LOCK_SH, LOCK_EX, LOCK_NB, LOCK_UN = 1, 2, 4, 8

        @staticmethod
        def flock(fd, operation):
            raise OSError(errno.ENOSYS, "file locking isn't implemented on Windows yet, so this is not done without it")

    fcntl = _NoFlock()
