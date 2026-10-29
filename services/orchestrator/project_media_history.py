"""Capture a selected Library asset's retained metadata history, never the whole Library.

Called on the intake worker. The Library lock binds its in-memory metadata to a
bounded journal read; publication checks that capture again under the same lock.
Historical author strings remain evidence, not connection credentials or grants.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat

from .library import Asset, Library
from .project_roots import ProjectLocationError

MAX_JOURNAL = 64 << 20
MAX_HISTORY = 4 << 20
MAX_LINE = 64 << 10


def _stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@dataclass(frozen=True)
class LibraryCapture:
    library: Library
    asset: Asset
    history: dict
    path: Path
    stamp: tuple

    @contextmanager
    def acceptance(self):
        with self.library.lock:
            if (self.library.path != self.path or _stamp(self.path.lstat()) != self.stamp
                    or self.library.key_of(self.asset.id) != self.asset.key
                    or self.library.edits(self.asset.id) != self.asset.user):
                raise ProjectLocationError('Library metadata changed during intake; keep the source and retry')
            yield


def capture(library, identity):
    from .project_media import _background
    _background()
    with library.lock:
        asset = library.get(identity)
        if asset is None or library.path is None:
            raise ProjectLocationError('Choose a retained Library source with readable metadata history')
        asset = deepcopy(asset)
        path = library.path
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        rows, kept, size = [], 0, 0
        with os.fdopen(descriptor, 'rb') as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_JOURNAL:
                raise ProjectLocationError('Library history exceeds the bounded collection limit')
            while True:
                line = source.readline(MAX_LINE + 1)
                if not line:
                    break
                size += len(line)
                if size > MAX_JOURNAL or len(line) > MAX_LINE or not line.endswith(b'\n'):
                    raise ProjectLocationError('Library history is incomplete or exceeds the bounded collection limit')
                try:
                    row = json.loads(line)
                except ValueError as error:
                    raise ProjectLocationError('Library history contains an unreadable record') from error
                if not isinstance(row, dict):
                    raise ProjectLocationError('Library history contains an invalid record')
                selected = row.get('id') == asset.id
                shared = (asset.group == 'example' and isinstance(row.get('looked-specimen'), dict)
                          and str(row.get('specimen', '')).lower() == asset.specimen.lower())
                if selected or shared:
                    if selected and 'key' in row and row['key'] != asset.key:
                        raise ProjectLocationError('Library history contains a conflicting source identity')
                    kept += len(line)
                    if kept > MAX_HISTORY:
                        raise ProjectLocationError('Selected media history exceeds the retained document limit')
                    rows.append(row)
            if (_stamp(os.fstat(source.fileno())) != _stamp(before)
                    or _stamp(path.lstat()) != _stamp(before)):
                raise ProjectLocationError('Library history changed while reading; retry intake')
        if not any(row.get('id') == asset.id and row.get('key') == asset.key for row in rows):
            raise ProjectLocationError('Library history does not establish this source identity')
        # Identity is scoped to these retained records: unrelated Library edits
        # must not change a completed intake's request/content identity.
        encoded = json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()
        history = {'format': 1, 'scope': 'selected-library-metadata', 'records': rows,
                   'sha256': hashlib.sha256(encoded).hexdigest()}
        return LibraryCapture(library, asset, history, path, _stamp(before))
