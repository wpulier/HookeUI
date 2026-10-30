"""Verify every journal-accepted numeric revision without evaluating its method.

The existing table reader owns identity, path and hash meaning. This collector
passes only the captured Project's artifact roots, never another host's tables.
It verifies retained values, not complete source dependency closure or accuracy.
"""
from .project_repository import session_root, verify_owner
from .project_roots import ProjectLocationError
from .project_source_preflight import _journal
from .table_reading import read_accepted

MAX_RECORDS = 100_000


def verify_numeric_records(root, *, check=lambda: None):
    from .datatables import DataTableRefused
    verify_owner(root)
    records = 0

    def visit(event, number):
        nonlocal records
        event_type = event.get('type')
        if event_type not in ('datatable', 'table-derivative'):
            return
        check()
        records += 1
        if records > MAX_RECORDS:
            raise ProjectLocationError('Numeric verification exceeds its history allowance')
        kind = 'dataset-table' if event_type == 'datatable' else event.get('kind')
        if kind not in ('dataset-table', 'analysis', 'graph') or (event_type == 'table-derivative' and kind == 'dataset-table'):
            raise ProjectLocationError('Accepted numeric result has an unsupported kind')
        folder = root.folder('artifacts') / ('tables' if event_type == 'datatable' else 'table-derivatives')
        # Resolve no legacy location; the ordinary reader enforces owner-relative
        # paths and original accepted hashes, even for superseded revisions.
        if folder.is_symlink():
            raise ProjectLocationError('Numeric result storage was replaced by a link')
        read_accepted(folder, event, root.session, kind, check=check)
        check()

    try:
        _journal(session_root(root) / root.session / 'journal.jsonl', check, visit=visit)
    except (OSError, ValueError, KeyError, TypeError, RecursionError, DataTableRefused) as error:
        raise ProjectLocationError(f'Project numeric history failed verification: {error}') from error
    check()
    verify_owner(root)
    return {'verifiedRevisions': records}
