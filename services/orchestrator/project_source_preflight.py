"""Identify historical Library-source dependencies without consulting another host.

Only journal source events and producer-owned retained Library bindings participate.
This is one collection preflight, not complete journal/media dependency closure.
"""
import hashlib
import json
import os
import stat

from .library import is_asset_id
from .project_repository import document_root, session_root, verify_owner
from .project_roots import ProjectLocationError
from .project_snapshot_integrity import MAX_DOCUMENTS, MAX_HISTORY
from .scientific_projects import ProjectStore

MAX_JOURNAL_BYTES = 256 << 20
MAX_LINE_BYTES = 8 << 20
MAX_EVENTS = 1_000_000
MAX_SOURCES = 10_000
MAX_BINDINGS = 100_000


def _stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _journal(root, check):
    path = session_root(root) / root.session / 'journal.jsonl'
    ids, count, digest = set(), 0, hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_JOURNAL_BYTES:
            raise ProjectLocationError('Project source review exceeds its journal allowance')
        remaining = before.st_size
        while remaining:
            check()
            line = stream.readline(min(MAX_LINE_BYTES + 1, remaining))
            if not line or not line.endswith(b'\n') or len(line) > MAX_LINE_BYTES:
                raise ProjectLocationError('Project journal has an incomplete or oversized event; preserve it before collection')
            remaining -= len(line)
            digest.update(line)
            count += 1
            if count > MAX_EVENTS:
                raise ProjectLocationError('Project source review exceeds its event allowance')
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError, RecursionError) as error:
                raise ProjectLocationError('Project journal contains an unreadable event; collection cannot certify it') from error
            if type(event) is not dict:
                raise ProjectLocationError('Project journal contains an unsupported event')
            if event.get('type') != 'source' or event.get('asset') == 'live':
                continue
            identity = event.get('asset')
            if not is_asset_id(identity):
                raise ProjectLocationError('A journal source event has an unsupported Library identity')
            ids.add(identity)
            if len(ids) > MAX_SOURCES:
                raise ProjectLocationError('Project source review exceeds its media allowance')
        check()
        if stream.read(1) or _stamp(os.fstat(stream.fileno())) != _stamp(before) or _stamp(path.lstat()) != _stamp(before):
            raise ProjectLocationError('Project journal changed during source review; retry after its work settles')
    return ids, {'events': count, 'bytes': before.st_size, 'sha256': digest.hexdigest()}, before


def review_sources(root, *, check=lambda: None):
    """Report retained choices and missing inclusion, keeping removed history too.

    A retained binding is not evidence of complete Library history or verified media
    bytes. Snapshot integrity and explicit whole-Record collection remain separate.
    """
    check()
    identities, basis, journal_stamp = _journal(root, check)
    verify_owner(root)
    store = ProjectStore(document_root(root))
    def read_heads():
        return store.head_references(root.session, max_documents=MAX_DOCUMENTS,
                                     max_revisions=MAX_HISTORY, check=check)
    heads = read_heads()
    choices = {identity: [] for identity in sorted(identities)}
    bindings = 0
    for head in heads:
        for revision in range(1, head['revision'] + 1):
            check()
            saved = store.read(head['id'], root.session, revision=revision)
            metadata = saved.document['state'].get('librarySource')
            if type(metadata) is not dict or type(metadata.get('asset')) is not dict:
                continue
            identity = metadata['asset'].get('id')
            if type(identity) is not str or identity not in choices:
                continue
            bindings += 1
            if bindings > MAX_BINDINGS:
                raise ProjectLocationError('Project source review exceeds its retained binding allowance')
            valid = (metadata.get('format') == 1 and metadata.get('projectOrigin') == root.origin
                     and metadata.get('scope') == 'selected-media' and saved.document['kind'] == 'source-file')
            if valid:
                source = saved.document['state'].get('sourceFile')
                try:
                    store.file_source(saved.id, root.session, saved.revision,
                        source.get('reference') if type(source) is dict else None,
                        verify_content=False)
                except (ValueError, OSError):
                    valid = False
                check()
            choices[identity].append({'reference': {'id': saved.id, 'session': saved.session,
                'revision': saved.revision, 'digest': saved.digest},
                'status': 'retained-binding' if valid else 'invalid-retained-binding'})
    check()
    if heads != read_heads():
        raise ProjectLocationError('Project records changed during source review; retry after its work settles')
    path = session_root(root) / root.session / 'journal.jsonl'
    if _stamp(path.lstat()) != _stamp(journal_stamp):
        raise ProjectLocationError('Project journal changed during source review; retry after its work settles')
    verify_owner(root)
    return {'scope': 'journal-library-source-events', 'journal': basis,
        'sources': [{'asset': identity, 'status': 'retained-choices' if any(
            item['status'] == 'retained-binding' for item in items) else 'inclusion-required',
            'choices': items} for identity, items in choices.items()],
        'completeProject': False,
        'reason': 'This reviews historical source events only. Automatically joined Library media, shared Record datasets, other journal references and extension dependencies still require collection review.'}
