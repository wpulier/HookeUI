"""Offline, explicit preservation of an empty legacy shadow beside a registered Project.

This repairs the prequalification detached-store bug. It never merges identities,
rewrites evidence, deletes files, changes grants or creates a replacement Project.
Stop the host first; the portable writer and namespace locks must be available.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

from .day.ownership import namespace, retained_owner
from .project_repository import verify_owner
from .project_roots import ProjectLocationError, ProjectRegistry, _atomic, writer

LIMIT = 65536


def _journal(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ProjectLocationError('The shadow journal is not a regular file')
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT or not raw.endswith(b'\n'):
        raise ProjectLocationError('The legacy journal is not an empty, complete creation')
    try:
        records = [json.loads(line) for line in raw.splitlines()]
    except ValueError as error:
        raise ProjectLocationError('The legacy journal is unreadable; preserve both locations') from error
    if (len(records) != 2 or any(type(row) is not dict for row in records)
            or records[0].get('type') != 'session-origin'
            or set(records[1]) != {'at', 'type', 'thread', 'n', 'title'}
            or records[1]['type'] != 'thread-opened' or records[1]['thread'] != 't1'
            or records[1]['n'] != 1 or records[1]['title'] != ''):
        raise ProjectLocationError('The legacy Project contains more than empty creation; preserve both and review explicitly')
    return hashlib.sha256(raw).hexdigest()


def inspect(home, session):
    home = Path(home).expanduser().resolve(strict=True)
    registry = ProjectRegistry(home)
    matches = [origin for origin, value in registry.locations().items() if value['session'] == session]
    if len(matches) != 1:
        raise ProjectLocationError('Choose exactly one registered Project handle')
    root = registry.locate(matches[0])
    verify_owner(root)
    legacy = home / 'sessions' / session
    if legacy.is_symlink() or not legacy.is_dir() or {p.name for p in legacy.iterdir()} != {'journal.jsonl'}:
        raise ProjectLocationError('The legacy location contains other material or is unavailable; preserve it for review')
    digest = _journal(legacy / 'journal.jsonl')
    owner = retained_owner(home / 'sessions', session)
    if owner is None or owner['origin']['origin'] == root.origin:
        raise ProjectLocationError('This is not a distinct empty shadow Project')
    return root, {'session': session, 'portableOrigin': root.origin,
        'shadowOrigin': owner['origin']['origin'], 'shadowSha256': digest,
        'shadowLocation': str(legacy), 'portableLocation': str(root.path)}


def preserve_shadow(home, session, expected_digest):
    home = Path(home).expanduser().resolve(strict=True)
    with namespace(home / 'sessions'):
        root, review = inspect(home, session)
        if review['shadowSha256'] != expected_digest:
            raise ProjectLocationError('The shadow journal changed since inspection; nothing was moved')
        with writer(root):
            # Recheck under the writer lease; no active portable host may be bypassed.
            current_root, current = inspect(home, session)
            if current_root != root or current != review:
                raise ProjectLocationError('Project locations changed during recovery')
            recovery = home / 'project-recovery'
            if recovery.is_symlink():
                raise ProjectLocationError('Recovery storage cannot be a link')
            recovery.mkdir(mode=0o700, exist_ok=True)
            destination = recovery / (session + '-' + review['shadowOrigin'])
            destination.mkdir(mode=0o700)  # never overwrite an earlier recovery
            _atomic(destination / 'recovery.json', {**review, 'state': 'prepared',
                'effect': 'Preserve empty legacy shadow; accepted portable research and grants unchanged'})
            Path(review['shadowLocation']).rename(destination / 'original')
            for folder in (home / 'sessions', destination):
                fd = os.open(folder, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            if _journal(destination / 'original/journal.jsonl') != expected_digest:
                raise ProjectLocationError('The preserved journal changed; recovery remains uncertain and all bytes are kept')
            receipt = {**review, 'state': 'preserved', 'recoveryLocation': str(destination)}
            _atomic(destination / 'recovery.json', receipt)
            return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--preserve-empty-shadow', metavar='INSPECTED_SHA256')
    args = parser.parse_args()
    value = (preserve_shadow(args.home, args.session, args.preserve_empty_shadow)
             if args.preserve_empty_shadow else inspect(args.home, args.session)[1])
    print(json.dumps(value, indent=2))


if __name__ == '__main__':
    main()
