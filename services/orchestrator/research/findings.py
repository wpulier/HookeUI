"""Scientific revisions, independent of editable presentation outputs.

The manager saves these with the existing atomic session record. A revision holds
the entire result and exact evidence reads; presentation retries never create one.
Older summary-only work stays explicitly incomplete rather than being expanded.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time


def payload_digest(payload: dict) -> str:
    """The existing retained-finding identity, shared by saving and verification."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def retain(revisions: list[dict], result: dict, working: list[dict], run: dict | None, *, missing: int = 0) -> dict:
    evidence = [{key: row[key] for key in ('id', 'how', 'version', 'contentRevision', 'kind', 'run') if key in row}
                for row in working]
    document = {key: copy.deepcopy(value) for key, value in result.items() if key != 'findingRevision'}
    payload = {'result': document, 'evidence': evidence, 'missingEvidenceReads': missing}
    digest = payload_digest(payload)
    if revisions and revisions[-1]['digest'] == digest:
        return revisions[-1]
    revision = {'version': len(revisions) + 1, 'digest': digest, 'at': time.time(),
                'complete': bool(document.get('report')), 'run': copy.deepcopy(run), **payload}
    revisions.append(revision)
    return revision


def reference(revision: dict) -> dict:
    return {key: revision[key] for key in ('version', 'digest', 'complete')}
