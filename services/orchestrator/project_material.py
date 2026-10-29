"""Metadata-only material projection shared by the resource index and agent state.

No content reads, filesystem inference or execution. Declared formats are facts from
accepted documents, not a guess from a title. Use never changes an artifact's origin.
"""
from __future__ import annotations


def describe(document: dict) -> dict:
    kind = document.get('kind', '')
    state = document.get('state', {})
    state = state if isinstance(state, dict) else {}
    file = state.get('sourceFile', {})
    file = file if isinstance(file, dict) else {}
    operation = state.get('matrixOperation', {})
    operation = operation if isinstance(operation, dict) else {}
    output = operation.get('output', {})
    summary = output.get('summary', {}) if isinstance(output, dict) else {}
    summary = summary if isinstance(summary, dict) else {}
    material = {'kind': 'file', 'origin': 'unknown', 'input': False,
                'section': 'other', 'category': 'other', 'record': False}
    if kind in ('source-file', 'source-directory'):
        material.update(kind='folder' if kind == 'source-directory' else 'file',
                        origin='imported', input=True, section='files', category='import')
    elif kind == 'research-effort':
        material.update(kind='assignment', origin='authored', input=True, section='study', category='study-result')
    elif kind.startswith('scientific-workflow-'):
        material.update(kind='workflow', origin='generated', section='history', category='workflow', record=True)
        attempt = state.get('workflowAttempt', {})
        if isinstance(attempt, dict) and isinstance(attempt.get('run'), str):
            material['run'] = attempt['run']
    elif kind == 'matrix-result':
        material.update(kind='analysis', origin='generated', section='analysis', category='analysis')
        if summary.get('format') == 'response-model-v1':
            material.update(kind='model', section='model')
    elif isinstance(state.get('scientificMeasurements'), dict):
        material.update(kind='table', origin='generated', section='analysis', category='table')
    elif kind in ('population-labels', 'retained-population'):
        material.update(kind='labels', origin='generated', section='analysis', category='outline-layer')
    elif kind in ('scientific-comparison', 'scientific-method-comparison'):
        material.update(kind='analysis', origin='generated', section='analysis', category='analysis')
    if isinstance(summary.get('format'), str):
        material['format'] = summary['format'][:120]
    if type(file.get('size')) is int and file['size'] >= 0:
        material['bytes'] = file['size']
    shape = summary.get('shape')
    if isinstance(shape, list) and len(shape) <= 8 and all(type(n) is int and n >= 0 for n in shape):
        material['shape'] = list(shape)
    # These are exact accepted dependencies, not proof of content verification now.
    material['dependencies'] = document.get('resources', [])
    return material


def attach_usage(entries) -> None:
    """Join exact Study input memberships without changing their historical origin.

    Older pinned revisions remain listed in uses; only an exact current revision
    becomes an input row. Reading an older input stays the Study's exact-ref path.
    """
    materials = {}
    for entry in entries:
        if entry.kind == 'project' and isinstance(entry.values, dict):
            entry.material = describe(entry.values)
            entry.material['uses'] = []
            materials[entry.id] = entry
    for study in materials.values():
        if study.values.get('kind') != 'research-effort':
            continue
        effort = study.values.get('state', {}).get('researchEffort', {})
        if not isinstance(effort, dict):
            continue
        for ref in effort.get('inputs', []):
            if not isinstance(ref, dict):
                continue
            entry = materials.get(ref.get('id'))
            if entry is None:
                continue
            use = {'study': study.id, 'revision': ref.get('revision'), 'digest': ref.get('digest')}
            entry.material['uses'].append(use)
            if entry.version == ref.get('revision') and entry.source.get('sha256') == ref.get('digest'):
                entry.material['input'] = True
