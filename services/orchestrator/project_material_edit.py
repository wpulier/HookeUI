"""Project-use metadata refers to exact evidence without revising that evidence.

Membership histories are separate accepted records. A group is explicit context,
not a scientific classification or producing provenance.
"""
import hashlib
import json

from .scientific_comparisons import exact, identity
from .scientific_projects import InvalidProject, Conflict

KIND = 'project-material-membership'
RESERVED = {'research-effort', 'workflow-invocation', KIND}


def membership_id(reference):
    return 'p' + hashlib.sha256(json.dumps(reference, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:32]


def organize(store, session, reference, *, role, selected, expected_revision, actor, request, check=lambda: None):
    if not isinstance(role, str) or len(role) > 80 or any(ord(c) < 32 for c in role):
        raise InvalidProject('Use a material group of at most 80 characters')
    if type(selected) is not bool or not isinstance(actor, str) or not actor or len(actor) > 128:
        raise InvalidProject('Material organization needs an actor and an input choice')
    if type(expected_revision) is not int or expected_revision < 0:
        raise InvalidProject('Read current material organization before saving')
    if not isinstance(request, str) or not 1 <= len(request) <= 128:
        raise InvalidProject('Material organization needs a request identity')
    check()
    original = exact(store, reference, session)
    kind = original.document['kind']
    if kind in RESERVED or kind.startswith('scientific-workflow-'):
        raise InvalidProject('Organize the Study’s material; assignments and workflow history keep their own controls')
    annotation = {'reference': identity(original), 'role': role.strip(), 'input': selected, 'by': actor}
    transaction = hashlib.sha256(json.dumps(['material', actor, request, session], separators=(',', ':')).encode()).hexdigest()
    document = {'kind': KIND, 'title': f"Organization · {original.document['title']}"[:240],
                'state': {'materialMembership': annotation, 'request': transaction}, 'resources': [], 'runtimes': []}
    record_id = membership_id(reference)
    check()
    if expected_revision == 0:
        try:
            return store.create(session, document, identity=record_id, check=check)
        except Conflict:
            current = store.read(record_id, session)
            if current.document == document:
                return current
            raise Conflict('Material organization changed. Refresh before saving your choices.') from None
    return store.save(record_id, session, document, expected_revision=expected_revision,
                      transaction=transaction, check=check)


def record(value, *, session):
    """Read only the shared membership shape; malformed records cannot label another Project."""
    if not isinstance(value, dict) or set(value) != {'reference', 'role', 'input', 'by'}:
        raise InvalidProject('Material organization is unreadable')
    ref = value['reference']
    from .door.schema import check
    check(ref, 'artifact-reference')
    if (ref['session'] != session or not isinstance(value['role'], str) or len(value['role']) > 80
            or type(value['input']) is not bool or not isinstance(value['by'], str) or not 0 < len(value['by']) <= 128):
        raise InvalidProject('Material organization does not match this Project')
    return value
