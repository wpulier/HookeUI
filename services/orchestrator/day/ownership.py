"""Journal-held Project origins and bounded partial-work transfer descriptions.

An origin is minted only when a journal is first written, never on export or when opening a legacy journal.
Imported work retains that origin; it does not restore conversations, assets or permissions not in the bundle.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import uuid

from ..file_locks import fcntl
from .sid import is_sid

MAX_LINE = 65536
MAX_JOURNAL = 16 << 20
_held = threading.local()


@contextmanager
def namespace(root):
    """Admit journal creation/import without blocking live callers; reentrant on the same thread."""
    root = Path(root).resolve()
    held = getattr(_held, 'roots', None)
    if held is None:
        held = _held.roots = set()
    if root in held:
        yield
        return
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root / '.owners.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            from ..scientific_import_transaction import ImportPending
            raise ImportPending('A Project import or creation is in progress; retry after it completes') from error
        held.add(root)
        yield
    finally:
        held.discard(root)
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def origin(sid, at):
    return {'type': 'session-origin', 'session': sid, 'origin': uuid.uuid4().hex, 'at': at}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def validate(value, sid):
    if type(value) is not dict or set(value) != {'format', 'session', 'origin', 'originDigest', 'metadata'} \
            or type(value['format']) is not int or value['format'] != 1 or not is_sid(sid) or value['session'] != sid:
        raise ValueError('Invalid Project owner envelope')
    first, meta = value['origin'], value['metadata']
    if (type(first) is not dict or set(first) != {'type', 'session', 'origin', 'at'}
            or first['type'] != 'session-origin' or first['session'] != sid
            or type(first['origin']) is not str or re.fullmatch('[0-9a-f]{32}', first['origin']) is None
            or type(first['at']) not in (int, float) or not math.isfinite(first['at'])
            or hashlib.sha256(encode(first)).hexdigest() != value['originDigest']):
        raise ValueError('Invalid Project origin identity')
    if (type(meta) is not dict or set(meta) != {'name', 'named', 'created', 'localOnly'}
            or type(meta['name']) is not str or len(meta['name']) > 80
            or type(meta['named']) is not bool or type(meta['localOnly']) is not bool
            or type(meta['created']) not in (int, float) or not math.isfinite(meta['created'])):
        raise ValueError('Invalid Project owner metadata')
    return value


def snapshot(root, sid):
    """Read bounded real journal identity; None means legacy/missing, never a fabricated owner."""
    with namespace(root):
        from ..scientific_import_transaction import require_visible
        require_visible(root)
        return _snapshot(root, sid)


def retained_owner(root, sid, *, allowance=None, check=lambda: None):
    """Read journal ownership for ordinary retained work, without export envelope ceilings.

    Stream events so cumulative history does not need to fit a transfer bundle.
    The visibility guard still prevents observing a half-published import.
    """
    from ..scientific_import_transaction import reading
    with reading(root):
        return _snapshot(root, sid, transfer=False, allowance=allowance, check=check)


def _snapshot(root, sid, *, transfer=True, allowance=None, check=lambda: None):
    if allowance is not None and (type(allowance) is not tuple or len(allowance) != 2
            or any(type(n) is not int or n <= 0 for n in allowance)):
        raise ValueError("Invalid Project owner read allowance")
    if not is_sid(sid):
        raise ValueError('Invalid Project owner session')
    folder = Path(root) / sid
    if folder.is_symlink():
        raise ValueError('Project owner folder cannot be a symlink')
    try:
        fd = os.open(folder / 'journal.jsonl', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    first, used = None, 0
    meta = {'name': '', 'named': False, 'created': 0.0, 'localOnly': False}
    with os.fdopen(fd, 'rb') as stream:
        import stat
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Project owner journal must be a regular file')
        limits = (MAX_JOURNAL, MAX_LINE) if transfer else allowance
        if limits and os.fstat(stream.fileno()).st_size > limits[0]:
            raise ValueError('Project owner journal exceeds its read allowance')
        while True:
            check()
            raw = stream.readline(limits[1] + 1 if limits else -1)
            if not raw:
                break
            used += len(raw)
            if limits and (len(raw) > limits[1] or used > limits[0]):
                raise ValueError('Project owner journal exceeds its read allowance')
            if not raw.endswith(b'\n'):
                break  # ordinary concurrent append may have an incomplete final line
            try:
                event = json.loads(raw)
            except (ValueError, UnicodeError):
                continue  # journals preserve damaged historical lines
            if type(event) is not dict:
                continue
            kind = event.get('type')
            if kind == 'session-origin':
                if first is not None:
                    raise ValueError('Project journal has multiple origins')
                first = event
                meta['created'] = event.get('at')
            elif kind == 'session-imported':
                imported = validate(event.get('owner'), sid)
                if first != imported['origin']:
                    raise ValueError('Imported Project origin changed')
                meta = dict(imported['metadata'])
            elif kind == 'session-started':
                meta.update(name=str(event.get('name') or '')[:80], named=bool(event.get('name')) and event.get('by') == 'user',
                            created=event.get('created', meta['created']), localOnly=event.get('localOnly') is True)
            elif kind == 'session-renamed':
                meta.update(name=str(event.get('name') or '')[:80], named=bool(event.get('name')) and not event.get('auto'))
    if first is None:
        return None
    return validate({'format': 1, 'session': sid, 'origin': first,
                     'originDigest': hashlib.sha256(encode(first)).hexdigest(), 'metadata': meta}, sid)


def journal_bytes(owner):
    """Only the actual origin and explicit partial import; no invented historical conversation."""
    return encode(owner['origin']) + b'\n' + encode({'type': 'session-imported', 'at': owner['origin']['at'],
                                                     'owner': owner, 'scope': 'scientific-bundle'}) + b'\n'
