"""Read contributed outputs through pinned directory descriptors, never through checked-then-reopened paths.

The caller owns the root and its ancestors. Every relative component below it must be unaliased; the file must
be a regular, single-link file owned by this user. Reads and hashes use the same descriptor and reject a file
that changes during the read. This confines parent reads; it does not sandbox the contributed process.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path


class Invalid(ValueError):
    """An output is missing, aliased, changed, or not an owned regular file."""


def parts(name: str) -> list[str]:
    if not isinstance(name, str) or not name or "\0" in name:
        raise Invalid("output needs a relative file name")
    split = name.split("/")
    if any(p in ("", ".", "..") for p in split):
        raise Invalid("output needs a relative file name without dot segments")
    return split


def _regular(info):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
        raise Invalid("output must be an owned regular file with one link")


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


class Folder:
    """A trusted root held open across execution, or a trusted store root used to reopen a retained output."""
    def __init__(self, root: Path):
        self.root = root
        self.fd = None
        self.check = lambda: None

    def __enter__(self):
        try:
            self.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as exc:
            raise Invalid("output folder is missing or aliases another location") from exc
        return self

    def __exit__(self, *_):
        os.close(self.fd)
        self.fd = None

    @contextmanager
    def _file(self, name):
        names = parts(name)
        directory = os.dup(self.fd)
        fd = None
        try:
            for component in names[:-1]:
                next_dir = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                   dir_fd=directory)
                os.close(directory)
                directory = next_dir
            # NONBLOCK prevents a FIFO from blocking before fstat can reject it.
            fd = os.open(names[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=directory)
            _regular(os.fstat(fd))
            yield fd
        except OSError as exc:
            raise Invalid(f"output {name!r} is missing or not an unaliased regular file") from exc
        finally:
            if fd is not None:
                os.close(fd)
            os.close(directory)

    def _consume(self, name, *, retain=False, expected=None, max_bytes=None):
        self.check()
        with self._file(name) as fd:
            before = os.fstat(fd)
            if max_bytes is not None and before.st_size > max_bytes:
                raise Invalid("output exceeds the allowed read size")
            h, chunks, size = hashlib.sha256(), [], 0
            # Read at most the size observed at open, plus one byte to reject growth. A writer cannot extend this loop.
            while size < before.st_size:
                self.check()
                block = os.read(fd, min(1 << 20, before.st_size - size))
                self.check()
                if not block:
                    break
                size += len(block)
                if max_bytes is not None and size > max_bytes:
                    raise Invalid("output exceeds the allowed read size")
                h.update(block)
                if retain:
                    chunks.append(block)
            self.check()
            extra = os.read(fd, 1)
            self.check()
            if extra:
                raise Invalid("output changed while it was read")
            after = os.fstat(fd)
            _regular(after)
            if _stamp(before) != _stamp(after) or size != before.st_size:
                raise Invalid("output changed while it was read")
            digest = h.hexdigest()
            if expected is not None and digest != expected:
                raise Invalid("output differs from its recorded digest")
            self.check()
            return b"".join(chunks) if retain else {"sha256": digest, "bytes": size}

    def identity(self, name):
        return self._consume(name)

    def read(self, name, *, expected=None, max_bytes=None):
        return self._consume(name, retain=True, expected=expected, max_bytes=max_bytes)

    def write(self, name, data):
        """Replace a parent-owned control file without following a worker-created alias at that name."""
        if len(parts(name)) != 1:
            raise Invalid("control file must be directly in its output folder")
        temporary = f".parent-{secrets.token_hex(16)}"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=self.fd)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            os.rename(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass
