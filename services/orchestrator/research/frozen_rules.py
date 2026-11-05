"""A Study's frozen decision rules (ADR 0031), and the check every comparison step makes against them.

A frozen rule is an exact retained JSON file a Study records, with its time, before the results it
will judge exist. Rows are only appended: a rule is never edited or unfrozen, and the Study's
immutable revision history keeps when each was recorded. A capability input of type
`artifact.frozen-rule` admits only such a file, and only when it was frozen before every other
input of that step was made. The check lives in the shared matrix run (`matrix_capability_runs`),
so a person, a researcher and a connected agent meet the same refusal.
"""
from __future__ import annotations

import json
import time

from ..scientific_projects import InvalidProject, Conflict

PORT_TYPE = 'artifact.frozen-rule'
MAX_RULE_BYTES = 64 << 10
MAX_RULES = 64


def _rule_file(store, session, artifact):
    from ..matrix_capability_runs import _input
    if type(artifact) is not dict or set(artifact) - {'id', 'session', 'revision', 'digest'} or not {'id', 'revision', 'digest'} <= set(artifact):
        raise InvalidProject('Freeze an exact retained file: {id, revision, digest}')
    source, exact = _input(store, session, {k: v for k, v in artifact.items() if k != 'session'}, lambda: None)
    if source.size > MAX_RULE_BYTES:
        raise InvalidProject('A frozen rule is a JSON file of at most 64 KiB')
    try:
        rule = json.loads(source.read(0, source.size))
    except ValueError as error:
        raise InvalidProject('A frozen rule is a JSON document') from error
    if not isinstance(rule, dict) or not isinstance(rule.get('format'), str):
        raise InvalidProject('A frozen rule declares its format')
    return exact, rule


def freeze(efforts, study, session, *, request, name, artifact, attribution):
    if not isinstance(request, str) or not 0 < len(request) <= 128:
        raise InvalidProject('A frozen rule needs a request identity')
    if not isinstance(name, str) or not 0 < len(name) <= 240:
        raise InvalidProject('Name the frozen rule')
    exact, rule = _rule_file(efforts.store, session, artifact)
    row = {'request': request, 'name': name, 'artifact': exact, 'format': rule['format'], **attribution,
           'at': time.time()}

    def change(state):
        rows = state.setdefault('frozenRules', [])
        prior = next((r for r in rows if r['request'] == request), None)
        if prior:
            if {k: v for k, v in prior.items() if k != 'at'} != {k: v for k, v in row.items() if k != 'at'}:
                raise Conflict('This frozen-rule request already belongs to another rule')
            return False
        if any(r['artifact']['id'] == exact['id'] and r['artifact']['revision'] == exact['revision'] for r in rows):
            raise Conflict('This rule revision is already frozen in this Study')
        if len(rows) >= MAX_RULES:
            raise InvalidProject('Frozen rule capacity reached for this Study')
        rows.append(row)
        return True
    return efforts._change(study, session, change)


def frozen_record(store, session, exact):
    """The Study row that froze this exact file revision, or None."""
    from .efforts import KIND, KEY
    for saved in store.list(session):
        if saved.document.get('kind') != KIND:
            continue
        for row in saved.document['state'].get(KEY, {}).get('frozenRules', []):
            a = row['artifact']
            if (a['id'], a['revision'], a['digest'], a['sha256']) == (exact['id'], exact['revision'], exact['digest'], exact['sha256']):
                return {'study': saved.id, 'request': row['request'], 'name': row['name'], 'at': row['at']}
    return None


def require(store, session, port, exact, others):
    """Refuse unless `exact` is a Study's frozen rule, frozen before every other input revision was saved."""
    record = frozen_record(store, session, exact)
    if record is None:
        raise InvalidProject(f'Input {port} must be a rule frozen in a Study before its results exist; '
                             'freeze it with POST studies/<study>/rules, then run this comparison')
    made = {p: store.read(r['id'], session, revision=r['revision']).at for p, r in others.items()}
    earlier = sorted(p for p, at in made.items() if at <= record['at'])
    if earlier:
        raise InvalidProject(f"The rule '{record['name']}' was frozen after {', '.join(earlier)} existed; "
                             'a comparison rule must be frozen before the results it judges')
    return record
