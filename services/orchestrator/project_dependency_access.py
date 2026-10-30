"""Read-only, captured Project dependency inspection for every supported caller.

No cross-Project resolver, path supplied by a caller, copy or execution is admitted.
The exact accepted heads form the basis; new work requires a new inspection.
"""
from .project_record_dependencies import RecordDependencyReview
from .project_roots import ProjectLocationError
from .project_snapshot_integrity import MAX_DOCUMENTS, MAX_HISTORY


def unavailable(session):
    return {'session': session, 'availability': 'unavailable', 'basis': [],
        'documents': 0, 'revisions': 0, 'scope': 'declared-scientific-record-references',
        'referencesChecked': 0, 'resolvedRevisions': 0, 'issues': [], 'completeProject': False,
        'reason': 'This host cannot inspect retained local Project dependencies.'}


def inspect(stores, session, *, check=lambda: None):
    """Inspect an authorized Project without writing or acquiring a writer lease."""
    check()
    store = stores.for_session(session)
    def heads():
        return store.head_references(session, max_documents=MAX_DOCUMENTS,
                                     max_revisions=MAX_HISTORY, check=check)
    basis = heads()
    review = RecordDependencyReview(store, session)
    revisions = 0
    for head in basis:
        for revision in range(1, head['revision'] + 1):
            check()
            review.inspect(store.read(head['id'], session, revision=revision), check=check)
            revisions += 1
    if heads() != basis:
        raise ProjectLocationError('Project records changed during inspection; request a fresh dependency review')
    check()
    return {'session': session, 'availability': 'available', 'basis': basis,
            'documents': len(basis), 'revisions': revisions, **review.result()}
