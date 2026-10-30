"""Verify journal-accepted microscopy saves through the existing evidence readers.

All historical saves matter, not only the latest population index. This checks
accepted native evidence, not complete Project dependency closure.
"""
from types import SimpleNamespace

from .project_evidence import resolve_entry
from .project_repository import session_root, verify_owner
from .project_roots import ProjectLocationError
from .project_source_preflight import _journal

MAX_SAVES = 100_000
MAX_EVIDENCE_FILE = 64 << 20


class _CapturedLocation:
    def __init__(self, root):
        self.root = root

    def project(self, session):
        if session != self.root.session:
            raise ProjectLocationError('Native evidence belongs to another Project')
        return self.root


def verify_native_records(root, *, check=lambda: None):
    from .evidence import Evidence, OPTIONAL_FILES
    from .project_folder_copy import _stream
    verify_owner(root)
    day = SimpleNamespace(locations=_CapturedLocation(root))
    saves, checked = 0, set()

    def visit(event, number):
        nonlocal saves
        if event.get('type') != 'evidence-saved':
            return
        check()
        saves += 1
        if saves > MAX_SAVES:
            raise ProjectLocationError('Native evidence verification exceeds its history allowance')
        folder = resolve_entry(day, root.session, event)
        identity = (event['path'], event.get('manifestSha256'), event.get('id'), event.get('revision'))
        if identity in checked:
            return
        manifest = Evidence.checked_manifest(folder, max_bytes=MAX_EVIDENCE_FILE)
        if (manifest.get('id') != event.get('id') or manifest.get('session') != root.session
                or type(event.get('revision')) is not int
                or manifest.get('revision') != event['revision']):
            raise ProjectLocationError('Native evidence does not match its accepted Project and revision')
        # Normal reading can tolerate missing enrichment; a complete byte snapshot
        # must preserve every file the accepted manifest actually declares.
        for name, digest in manifest['files'].items():
            check()
            if name in OPTIONAL_FILES and _stream(folder / name, check=check)[1] != digest:
                raise ProjectLocationError('Accepted native evidence enrichment is missing or changed')
        resolve_entry(day, root.session, event)  # manifest must still match the journal pin
        checked.add(identity)

    try:
        _journal(session_root(root) / root.session / 'journal.jsonl', check, visit=visit)
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
        raise ProjectLocationError(f'Project native evidence failed verification: {error}') from error
    check()
    verify_owner(root)
    return {'savedEvents': saves, 'verifiedSaves': len(checked)}
