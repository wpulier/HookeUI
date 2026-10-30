"""Optional descriptor-based macOS copy-on-write; never mutable hard links.

Unsupported volumes fall back to ordinary copying. Permission, storage and I/O
errors propagate. Callers own validation, permissions, syncing and hash checks.
"""
import ctypes
import errno
import os
from pathlib import Path
import sys


def clone(source_fd: int, destination: Path) -> bool:
    if sys.platform != 'darwin':
        return False
    library = ctypes.CDLL(None, use_errno=True)
    function = getattr(library, 'fclonefileat', None)
    if function is None:
        return False
    function.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    function.restype = ctypes.c_int
    parent = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if function(source_fd, parent, os.fsencode(destination.name), 0) == 0:
            return True
        code = ctypes.get_errno()
        if code in (errno.ENOTSUP, errno.EXDEV, errno.ENOSYS):
            return False
        raise OSError(code, os.strerror(code), str(destination))
    finally:
        os.close(parent)
