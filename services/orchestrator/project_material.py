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
        collection = state.get('recordCollection')
        if (kind == 'source-directory' and isinstance(collection, dict)
                and collection.get('format') == 'hooke-record-collection-v1'
                and collection.get('scope') == 'whole-record-folder'):
            material.update(kind='recorded-dataset', format='microscopemind-dataset/1')
        media = state.get('librarySource')
        if isinstance(media, dict) and media.get('format') == 1 and media.get('scope') == 'selected-media':
            asset = media.get('asset')
            if isinstance(asset, dict) and asset.get('kind') in ('still', 'recording'):
                material['kind'] = 'image' if asset['kind'] == 'still' else 'recording'
                # A generated picture used as an input keeps its producing origin.
                material['origin'] = ('generated' if asset.get('from') in ('snapshot', 'clearer')
                                      else 'imported' if asset.get('from') in ('added', 'example', 'recording')
                                      else 'unknown')
    elif kind == 'research-effort':
        material.update(kind='assignment', origin='authored', input=True, section='study', category='study-result')
    elif kind == 'project-material-membership':
        material.update(kind='organization', origin='authored', section='history', category='organization', record=True)
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


def _inspections(entries):
    """Index recorded matrix inspections by exact input, never by title or filename."""
    import re
    found = {}
    for entry in entries:
        document = entry.values if isinstance(entry.values, dict) else {}
        state = document.get('state', {})
        operation = state.get('matrixOperation', {}) if isinstance(state, dict) else {}
        if (document.get('kind') != 'matrix-result' or not isinstance(operation, dict)
                or operation.get('format') != 1 or operation.get('capability') != 'matrix-inspect'):
            continue
        inputs, output = operation.get('inputs'), operation.get('output')
        ref = inputs.get('matrix') if isinstance(inputs, dict) else None
        summary = output.get('summary') if isinstance(output, dict) else None
        if not isinstance(ref, dict) or not isinstance(summary, dict):
            continue
        shape = summary.get('shape')
        if (summary.get('format') != 'annotated-matrix-summary-v1'
                or not isinstance(shape, list) or len(shape) != 2
                or any(type(n) is not int or n < 0 for n in shape)
                or ref.get('session') != entry.day
                or not isinstance(ref.get('id'), str) or not re.fullmatch(r'p[a-f0-9]{32}', ref['id'])
                or type(ref.get('revision')) is not int or ref['revision'] < 1
                or not isinstance(ref.get('digest'), str) or not re.fullmatch(r'[a-f0-9]{64}', ref['digest'])):
            continue
        key = (entry.day, ref['id'], ref['revision'], ref['digest'])
        found.setdefault(key, []).append((ref, shape, {
            'id': entry.id, 'session': entry.day, 'revision': entry.version,
            'digest': entry.source['sha256']}))
    return found


def _inspected(material, document, reference, inspections):
    """Enrich display facts from retained reports; never revise source evidence."""
    state = document.get('state', {})
    source = state.get('sourceFile', {}) if isinstance(state, dict) else {}
    if document.get('kind') != 'source-file' or material.get('kind') != 'file' or not isinstance(source, dict):
        return material
    file_ref = source.get('reference', {})
    matches = [(shape, report) for input_ref, shape, report in inspections.get(reference, [])
               if input_ref.get('sha256') == source.get('sha256')
               and type(input_ref.get('bytes')) is int and input_ref['bytes'] == source.get('size')
               and isinstance(file_ref, dict) and isinstance(file_ref.get('component'), str)
               and file_ref['component'].startswith('file.') and input_ref.get('component') == file_ref['component']]
    if not matches:
        return material
    shapes = {tuple(shape) for shape, _ in matches}
    material['inspection'] = {'status': 'reported' if len(shapes) == 1 else 'conflicting',
                              'reports': [report for _, report in matches[:64]], 'total': len(matches)}
    if len(shapes) == 1:
        material.update(kind='matrix', format='AnnData', shape=list(next(iter(shapes))))
    return material


def attach_usage(entries, store=None) -> list[str]:
    """Join exact Study inputs. Bad memberships never hide unrelated material."""
    import re
    materials, problems = {}, []
    inspections = _inspections(entries)
    for entry in entries:
        if entry.kind == 'project' and isinstance(entry.values, dict):
            entry.material = _inspected(describe(entry.values), entry.values,
                (entry.day, entry.id, entry.version, entry.source.get("sha256")), inspections)
            entry.material['uses'] = []
            materials[entry.id] = entry
    for study in materials.values():
        if study.values.get('kind') != 'research-effort':
            continue
        effort = study.values.get('state', {}).get('researchEffort', {})
        refs = effort.get('inputs', []) if isinstance(effort, dict) else None
        if not isinstance(refs, list) or len(refs) > 64:
            problems.append(f'Study {study.id}: input membership is unreadable')
            continue
        for ref in refs:
            if (not isinstance(ref, dict) or not isinstance(ref.get('id'), str)
                    or type(ref.get('revision')) is not int or ref['revision'] < 1
                    or not isinstance(ref.get('digest'), str)
                    or not re.fullmatch(r'[a-f0-9]{64}', ref['digest'])):
                problems.append(f'Study {study.id}: an input reference is invalid')
                continue
            entry = materials.get(ref['id'])
            if entry is None:
                problems.append(f'Study {study.id}: input {ref["id"]} is unavailable')
                continue
            current = entry.version == ref['revision'] and entry.source.get('sha256') == ref['digest']
            use = {'study': study.id, 'revision': ref['revision'], 'digest': ref['digest'],
                   'title': entry.title, 'available': current, 'material': {k: v for k, v in entry.material.items() if k != 'uses'} if current else describe({})}
            if not current and store is not None:
                try:
                    old = store.read(entry.id, entry.day, revision=ref['revision'])
                    if old.digest != ref['digest']:
                        raise ValueError('digest mismatch')
                    use.update(title=old.document['title'], available=True, material=_inspected(
                        describe(old.document), old.document, (old.session, old.id, old.revision, old.digest), inspections))
                except (OSError, ValueError, KeyError):
                    problems.append(f'Study {study.id}: input {entry.id} revision {ref["revision"]} is unavailable')
            entry.material['uses'].append(use)
            if current:
                entry.material['input'] = True
    from .project_material_edit import record, membership_id
    for membership in materials.values():
        if membership.values.get('kind') != 'project-material-membership':
            continue
        try:
            value = record(membership.values['state']['materialMembership'], session=membership.day)
            ref = value['reference']
            if membership.id != membership_id(ref):
                raise ValueError('organization identity mismatch')
            entry = materials.get(ref['id'])
            if entry is None:
                raise ValueError('organized material unavailable')
            organization = {k: value[k] for k in ('role', 'input', 'by')}
            organization['revision'] = membership.version
            current = entry.version == ref['revision'] and entry.source.get('sha256') == ref['digest']
            if current:
                entry.material['organization'] = organization
                entry.material['input'] = value['input'] or any(u['revision'] == entry.version for u in entry.material['uses'])
            for use in entry.material['uses']:
                if use['revision'] == ref['revision'] and use['digest'] == ref['digest']:
                    use['material']['organization'] = organization
            # Explicitly selected historical material remains visible even outside a Study.
            if not current and value['input'] and not any(u['revision'] == ref['revision'] and u['digest'] == ref['digest'] for u in entry.material['uses']):
                if store is None:
                    raise ValueError('historical organization needs retained storage')
                old = store.read(entry.id, entry.day, revision=ref['revision'])
                if old.digest != ref['digest']:
                    raise ValueError('organized revision digest mismatch')
                old_material = _inspected(describe(old.document), old.document,
                    (old.session, old.id, old.revision, old.digest), inspections)
                old_material.update(organization=organization, input=True)
                entry.material['uses'].append({'membership': membership.id, 'revision': ref['revision'],
                    'digest': ref['digest'], 'title': old.document['title'], 'available': True, 'material': old_material})
        except (ValueError, OSError, KeyError):
            problems.append(f'Material organization {membership.id} is unavailable')
    return problems
