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


def verify_records(root, *, check=lambda: None):
    store = ProjectStore(document_root(root))
    count = revisions = 0
    try:
        for folder in sorted(store.root.iterdir()):
            check()
            if not PROJECT_ID.fullmatch(folder.name):
                continue
            count += 1
            if count > MAX_DOCUMENTS:
                raise ProjectLocationError('Project record verification exceeds its document allowance')
            latest = store.read(folder.name, root.session)
            revisions += latest.revision
            if revisions > MAX_HISTORY:
                raise ProjectLocationError('Project record verification exceeds its history allowance')
            previous = None
            for number in range(1, latest.revision + 1):
                check()
                saved = store.read(folder.name, root.session, revision=number)
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
    return {'documents': count, 'revisions': revisions}
