"""A deliberately narrow, local diagnostic export; no chat, grants or source URLs."""
from __future__ import annotations

import hashlib
import uuid


def redacted(detail: dict) -> dict:
    salt = uuid.uuid4().hex
    def alias(value):
        return hashlib.sha256((salt + str(value)).encode()).hexdigest()[:16] if value else None
    root = detail.get('root') or {}
    steps = []
    for row in detail.get('steps', []):
        # Explicit fields prevent future receipt additions exposing user content.
        step = {key: row[key] for key in ('operation', 'outcome', 'ms', 'provider', 'model', 'reportedModel',
                'phase', 'adapterMs', 'inputTokens', 'outputTokens', 'outputChars', 'candidateCount', 'groundingCount',
                'parseOk', 'finishReason', 'responseStatus') if key in row}
        step.update(id=alias(row.get('id')), parent=alias(row.get('parent')))
        if row.get('operation') == 'voice-boundary':
            step['scope'] = row.get('scope') if row.get('scope') in ('Browser reported', 'Service observed') else 'unknown'
        if row.get('sessionId'):
            step['sessionId'] = alias(row['sessionId'])
        steps.append(step)
    context = detail.get('context') or {}
    build = context.get('build') or {}
    return {'schema': 1, 'redacted': True, 'status': detail.get('status'),
            'root': {**{k: root[k] for k in ('kind', 'at', 'outcome', 'incomplete') if k in root}, 'id': alias(root.get('id'))},
            'build': {k: build[k] for k in ('revision', 'dirty', 'sourceDigest') if k in build},
            'steps': steps, 'interrupted': bool(detail.get('interrupted')),
            'omitted': ['original words', 'notes', 'browser identities', 'source identities and URLs',
                        'recordings', 'settings', 'raw transport and credentials']}
