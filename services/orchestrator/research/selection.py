"""Validate composer targets against retained identities, never against the latest screen."""
from __future__ import annotations

import copy

from .policy import ResearchRefused


def validate(session, value) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or value.get('day') != session.day or value.get('rid') != session.rid:
        raise ResearchRefused('the selected artifact belongs to another researcher or day', next='reopen the saved researcher and select its artifact')
    target = {key: copy.deepcopy(value[key]) for key in
              ('day', 'rid', 'findingVersion', 'findingDigest', 'outputId', 'outputVersion', 'anchor', 'presentationBase') if key in value}
    if target.get('presentationBase') is not None and session.output(target['presentationBase']) is None:
        raise ResearchRefused('the selected presentation base is unavailable', next='reopen its saved presentation')
    if 'findingVersion' in target:
        record = next((row for row in session.finding_revisions if row['version'] == target['findingVersion']), None)
        if record is None or record['digest'] != target.get('findingDigest'):
            raise ResearchRefused('the exact selected findings are unavailable', next='reopen the saved version')
    if 'outputId' in target:
        output = session.output(target['outputId'])
        if output is None or output.get('version', 1) != target.get('outputVersion'):
            raise ResearchRefused('the exact selected output is unavailable', next='reopen the saved version')
        basis = output.get('findingRevision')
        if basis and target.get('findingVersion') and (basis['version'] != target['findingVersion'] or basis['digest'] != target.get('findingDigest')):
            raise ResearchRefused('the selected presentation and findings have different evidence bases', next='reopen the saved presentation')
    if 'anchor' in target and (not isinstance(target['anchor'], str) or len(target['anchor']) > 160):
        raise ResearchRefused('invalid artifact selection', next='select a section from the saved artifact again')
    if target.get('anchor') and target.get('findingVersion'):
        from research_worker.presentation import blocks
        allowed = set(blocks(record['result']))
        allowed.add('evidence-checks')
        for index, chart in enumerate(record['result'].get('charts', [])):
            allowed.update(f'chart:{index}:point:{point}' for point in range(len(chart['rows'])))
        if target.get('outputId'):
            content = session.content_of(session.output(target['outputId'])) or {}
            allowed.update(f"step:{step['id']}" for step in content.get('steps', []))
        if target['anchor'] not in allowed:
            raise ResearchRefused('the selected section is not in these exact findings', next='select a section in the saved version')
    return target


def view(session, target):
    """A detached reader of the exact selected finding/output; no persistence or generation."""
    if not target or target.get('rid') != session.rid:
        return session
    target = validate(session, target)
    selected = copy.copy(session)
    if target.get('findingVersion'):
        record = next(row for row in session.finding_revisions if row['version'] == target['findingVersion'])
        selected.result = copy.deepcopy(record['result'])
        selected.result['findingRevision'] = {key: record[key] for key in ('version', 'digest', 'complete')}
        selected.finding_revisions = [copy.deepcopy(record)]
    if target.get('outputId'):
        selected.outputs = [copy.deepcopy(session.output(target['outputId']))]
    return selected


def require_current(session, target):
    """Edits compare the selected base before queuing; old versions remain readable."""
    if not target or target.get('rid') != session.rid:
        return
    validate(session, target)
    current = (session.result or {}).get('findingRevision', {})
    if target.get('findingVersion') and target['findingVersion'] != current.get('version'):
        raise ResearchRefused('the selected findings are older; no current work was overwritten', next='select the current findings before revising')
    if target.get('outputId') and target['outputId'] not in {o.get('id') for o in session.current_outputs()}:
        raise ResearchRefused('the selected output is older; no current work was overwritten', next='select its current version before revising')
