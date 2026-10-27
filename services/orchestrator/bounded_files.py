"""Bounded regular-file reads relative to a directory the caller already holds open.

Callers own their allowlists, directory traversal and aggregate size limits. This
primitive opens one leaf without following links or blocking on special files.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path


def open_directory(path: Path) -> int:
    """Hold a directory after rejecting links in every path component."""
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(path).absolute().parts[1:]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def read_at(directory: int, name: str, maximum: int) -> bytes:
    """Read one regular leaf, enforcing the bound before and during the read."""
    if name in ("", ".", "..") or "/" in name or "\\" in name:
        raise ValueError("expected one file name")
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError("file is not regular or is too big")
        data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError("file is too big")
        return data
