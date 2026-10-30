"""Validate retained scientific history before blessing a folder's current bytes.

Reuse publication/import readers; this is not another record format or a dependency
collector. Host runtimes, grants and executable implementations are never loaded.
"""
from . import scientific_arrays as arrays, scientific_files as files
from .project_repository import document_root
from .project_roots import ProjectLocationError
from .scientific_projects import PROJECT_ID, ProjectStore

MAX_DOCUMENTS = 10_000
MAX_HISTORY = 100_000


def _accepted_history(root, check, counts):
    store = ProjectStore(document_root(root))
    for folder in sorted(store.root.iterdir()):
        check()
        if not PROJECT_ID.fullmatch(folder.name):
            continue
        counts['documents'] += 1
        if counts['documents'] > MAX_DOCUMENTS:
            raise ProjectLocationError('Project record verification exceeds its document allowance')
        latest = store.read(folder.name, root.session)
        counts['revisions'] += latest.revision
        if counts['revisions'] > MAX_HISTORY:
            raise ProjectLocationError('Project record verification exceeds its history allowance')
        for number in range(1, latest.revision + 1):
            check()
            yield store, store.read(folder.name, root.session, revision=number)


def review_dependencies(root, *, check=lambda: None):
    """Review all accepted reference revisions without opening external owners."""
    from .project_record_dependencies import RecordDependencyReview
    review = RecordDependencyReview(ProjectStore(document_root(root)), root.session)
    counts = {'documents': 0, 'revisions': 0}
    for _, saved in _accepted_history(root, check, counts):
        review.inspect(saved, check=check)
    return {**counts, **review.result()}


def verify_records(root, *, check=lambda: None):
    counts = {'documents': 0, 'revisions': 0}
    previous = None
    try:
        for store, saved in _accepted_history(root, check, counts):
            if saved.revision == 1:
                previous = None
            document = saved.document
            # Previously checked chunks are immutable; copy admission also
            # pins all file stamps, catching later changes before publication.
            arrays.validate_references(store, saved.id, root.session, document,
                accepted=arrays.accepted_parts(store, saved.id, root.session, previous),
                check=check, new_bytes=arrays.MAX_LOGICAL_BYTES)
            files.validate_references(store, saved.id, root.session, document,
                accepted=previous['resources'] if previous else (), check=check)
            previous = document
    except (OSError, ValueError) as error:
        if isinstance(error, ProjectLocationError):
            raise
        raise ProjectLocationError(f'Project accepted records failed verification: {error}') from error
    return counts
