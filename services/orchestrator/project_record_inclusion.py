"""Owner-reviewed whole Record inclusion; no caller paths or agent Library access."""
import re

from .datasets.store import ID
from .project_record_collection import review_record, collect_record
from .project_roots import ProjectLocationError
from .scientific_files import CHUNK_BYTES, MAX_CHUNKS

MAX_BYTES = CHUNK_BYTES * MAX_CHUNKS


def prepare(day, stores, datasets, session, identity, *, check=lambda: None):
    locations = getattr(day, 'locations', None)
    owner = locations.project(session) if locations is not None else None
    if owner is None:
        raise ProjectLocationError('Open a Project folder before collecting a recorded dataset')
    if not isinstance(identity, str) or not ID.fullmatch(identity):
        raise ProjectLocationError('Choose a recorded dataset from this Hooke host')
    # The adapter opens every path component without following symlinks. Its root
    # is host-owned, not a path or identity carried in an imported Project.
    if datasets.is_symlink():
        raise ProjectLocationError('The recordings folder is a link; recover its regular location first')
    review = review_record(datasets / identity, max_bytes=MAX_BYTES, check=check)
    value = review.wire()
    if value['dataset']['id'] != identity:
        raise ProjectLocationError('The recording summary does not match the selected dataset')
    if locations.project(session) != owner:
        raise ProjectLocationError('The destination Project changed; review inclusion again')
    return review, {'session': session, 'origin': owner.origin, 'dataset': identity,
        'title': value['dataset'].get('name') or identity, 'scope': value['scope'],
        'reviewDigest': value['reviewDigest'], 'files': len(value['members']),
        'bytes': value['bytes'], 'requiredFreeBytes': value['requiredFreeBytes'],
        'captureCheck': value['dataset']['endCheck'],
        'reason': 'Includes the whole recorded dataset, including material from other Projects. External references are not collected.'}


def include(day, stores, datasets, session, identity, digest, operation, *, check=lambda: None):
    if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
        raise ProjectLocationError('Review the recording before including it')
    if not isinstance(operation, str) or not re.fullmatch('op_[a-f0-9]{16,32}', operation):
        raise ProjectLocationError('Record inclusion needs its admitted operation identity')
    review, plan = prepare(day, stores, datasets, session, identity, check=check)
    if plan['reviewDigest'] != digest:
        raise ProjectLocationError('The recording changed since review; review its whole contents again')
    def stopping():
        check()
        return False
    ref = collect_record(stores, session, review, reviewed_digest=digest,
        request=operation, max_bytes=MAX_BYTES, producer={'actor': 'user', 'operation': operation}, stopping=stopping)
    return {'session': session, 'origin': ref.origin, 'dataset': identity,
        'reviewDigest': digest, 'scope': 'whole-record-folder',
        'reference': {'session': ref.session, 'id': ref.document, 'revision': ref.revision, 'digest': ref.digest},
        'files': plan['files'], 'bytes': plan['bytes'], 'externalReferences': 'not-collected'}
