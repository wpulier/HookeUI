"""Frozen producing context attached to the same atomic revision as accepted values.

This is a bounded snapshot association, not a claim that arbitrary Python/native
callbacks can be replayed. Before/after content references remain authoritative.
"""
import copy
import hashlib
import json
import re
import time

from .scientific_projects import InvalidProject, _json

MAX_PRODUCER = 32 << 10
UNRECORDED = ('Randomness, the environment and any files or network the code used were not recorded, so running it '
              'again may not reproduce these values. Changes a callback made after its action returned, or that no '
              'event announced, are in the saved values and the change record but are not attributed to an action.')


def frozen(value):
    if value is None:
        return {'kind': 'snapshot', 'details': 'Producing actions were not recorded by this caller'}
    if type(value) is not dict:
        raise InvalidProject('Scientific producer context must be an object')
    body = _json(value)
    if len(body) > MAX_PRODUCER:
        raise InvalidProject('Scientific producer context exceeds its allowance')
    return json.loads(body)


def snapshot(store, project, session, revision, original, output, frame, transaction, producer):
    from .scientific_edits import Frame, ResourceRevision
    if not isinstance(frame, (Frame, ResourceRevision)):
        raise InvalidProject('Scientific publication needs its admitted revision authority')
    rendered = isinstance(frame, Frame)
    base = store.read(project, session, revision=revision)
    return {'format':1, 'base':{'id':project,'session':session,'revision':revision,'digest':base.digest},
            'input':copy.deepcopy(original),'output':copy.deepcopy(output),'transaction':transaction,
            'at':time.time(),'layer':frame.layer,'view':frame.view if rendered else None,
            'frame':json.loads(frame._context) if rendered else None,
            'authority':'rendered-view' if rendered else 'accepted-resource',
            'producer':frozen(producer),'replayable':False,
            'runtimes':copy.deepcopy(base.document['runtimes']),
            'coordinateSpace':copy.deepcopy(base.document['state'].get('coordinateSpace')),
            'changed':_changed(store, original, output, []),'unrecorded':UNRECORDED,
            'meaning':'Exact before/after values and recorded session actions; arbitrary code and callbacks are not guaranteed replayable'}


def attach(document, record, *, output=None):
    if record is None:
        # Earlier provenance stays only while this document still holds the values it describes.
        kept = document['state'].get('hookeEdit')
        if kept is not None and any(change.get('output') not in document['resources'] for change in
                                    (kept['changes'] if kept.get('format') == 2 else [kept])):
            del document['state']['hookeEdit']
    else:
        record = copy.deepcopy(record)
        if output is not None:
            record['output'] = copy.deepcopy(output)
        document['state']['hookeEdit'] = record


KEYS = ('hookeEdit', 'hookeOutput', 'hookeCopy', 'scientificToolChoice', 'cloudSource')
MAX_CHANGED_KEYS = 64


def guard(previous, document):
    """Only owned publication paths write provenance; a document save keeps it exactly as accepted."""
    state = document.get('state') if type(document) is dict else None
    if type(state) is not dict:
        return
    before = {} if previous is None else previous.document['state']
    reserved = ('scientific-workflow-run', 'scientific-workflow-attempt', 'scientific-workflow-late-result', 'workflow-invocation')
    if document.get('kind') in reserved or previous is not None and previous.document.get('kind') in reserved:
        raise InvalidProject('Workflow execution records are written only by the workflow service')
    for key in (*KEYS, 'methodEvidence', 'methodSummary', 'methodAdmission', 'populationSource', 'sourceReader', 'sourceImport', 'researchEffort', 'researchFindings', 'matrixOperation', 'workflowRun', 'workflowAttempt', 'workflowLate', 'workflowOutput', 'workflowInvocation'):
        if state.get(key) != before.get(key):
            raise InvalidProject('Producing provenance is written only when Hooke accepts the work; '
                                 'a document save cannot add, change or remove it')


def resolve(store, project, session, revision=None, *, resource=None):
    """Explain an accepted revision from retained, digest-checked records only.

    A reference that cannot be read or does not match is reported as a problem. No other
    history that shares the input is ever substituted, and nothing is inferred or replayed.
    An exact accepted resource restricts the explanation to its producing association. Original
    output associations are checked at their introducing revision; a later edit is checked at
    the selected revision and keeps its exact base. Default whole-document diagnostics are unchanged.
    """
    record = store.read(project, session, revision=revision)
    if resource is not None and (type(resource) is not dict or resource not in record.document['resources']):
        raise InvalidProject('This resource is not held by the selected accepted revision')
    state, problems = record.document['state'], []
    produced_at = 0
    population = state.get('populationSource')
    population_recorded = ('populationSource' in state and
                           (type(population) is not dict or population.get('format') != 1 or 'association' in population))
    result = {'format': 1, 'record': _exact_ref(record), 'runtimes': copy.deepcopy(record.document['runtimes']),
              'recorded': any(key in state for key in KEYS) or population_recorded, 'edits': [], 'outputs': None, 'copy': None,
              'problems': problems, 'replayable': False}
    if not result['recorded']:
        result['meaning'] = 'No producing provenance was recorded for this revision.'
        return result
    if 'hookeCopy' in state:
        result['copy'] = _copy(store, record, state['hookeCopy'], problems)
    if 'hookeEdit' in state:
        retained = state['hookeEdit']
        accepted = _accepted_at(store, record, 'hookeEdit', problems)
        changes = retained['changes'] if retained.get('format') == 2 else [retained]
        if resource is not None:
            changes = [change for change in changes if change.get('output') == resource]
        result['edits'] = [_edit(store, record, change, accepted, problems) for change in changes]
        result['acceptedAt'] = None if accepted is None else accepted.revision
        if result['edits'] and accepted is not None:
            produced_at = accepted.revision
    if 'hookeOutput' in state and (resource is None or not result['edits']):
        output = state['hookeOutput']
        rows = output.get('outputs', []) if output.get('format') == 2 else [output]
        selected = resource is None or any(resource in [row.get('output'),
            *((row.get('components') or {}).values())] for row in rows)
        if selected:
            accepted = record if resource is None else _accepted_at(store, record, 'hookeOutput', problems)
            if accepted is not None:
                result['outputs'] = _output(store, accepted, output, problems)
                produced_at = max(produced_at, accepted.revision)
    if 'populationSource' in state and (resource is None or not result['edits']):
        population = state['populationSource']
        # Legacy accepted records remain diagnostics, never an inferred component association.
        if type(population) is not dict or population.get('format') != 1 or 'association' in population:
            accepted = _accepted_at(store, record, 'populationSource', problems)
            if accepted is not None:
                association = _population(store, accepted, population, problems)
                if association is not None and (resource is None or resource == association['output']):
                    result['population'] = association
                    produced_at = max(produced_at, accepted.revision)
    # Selecting a tool preserves older reader/output/edit records. The newer
    # verified selection admits those unchanged values; a later producing edit
    # owns the next lineage step instead. Record presence alone is not chronology.
    if 'scientificToolChoice' in state:
        accepted = _accepted_at(store, record, 'scientificToolChoice', problems)
        if accepted is not None and accepted.revision > produced_at:
            choice = _tool_choice(store, accepted, record, state['scientificToolChoice'], resource, problems)
            if choice is not None:
                result['inputSelection'] = choice
    problems[:] = list(dict.fromkeys(problems))
    if result['outputs'] is not None and not problems:
        result['savedTool'] = _saved_output_tool(store, record, result['outputs'])
    return result


def _saved_output_tool(store, record, output):
    """Display attribution from one verified retained selection, never today's catalog."""
    problems = []
    original = _exact(store, output['base'], problems, 'saved tool source')
    if original is None:
        return None
    origin = original.document['state'].get('scientificToolChoice')
    if origin is None:
        choice = _reader_choice(original)
    else:
        accepted = _accepted_at(store, original, 'scientificToolChoice', problems)
        if accepted is None:
            return None
        choice = _tool_choice(store, accepted, original, origin, None, problems)
    view = record.document['state'].get('scientificView')
    runtime = view.get('runtime') if type(view) is dict else None
    original_view = original.document['state'].get('scientificView')
    if (choice is None or problems or type(original_view) is not dict or runtime is None
            or runtime != original_view.get('runtime')
            or type(output['runtimes']) is not list
            or runtime not in record.document['runtimes'] or runtime not in output['runtimes']):
        return None
    return {'name': choice['name'], 'runtime': copy.deepcopy(runtime), 'source': copy.deepcopy(output['base'])}


def _reader_choice(original):
    """A reader workspace names its reader in the guarded sourceReader that Hooke wrote when it accepted the opening
    (guard: a document save cannot add or change it); only while that reader is still the workspace's own runtime."""
    from gpu_worker.bundle_contract import parse_capability_key
    reader = original.document['state'].get('sourceReader')
    view = original.document['state'].get('scientificView')
    if (type(reader) is not dict or reader.get('format') != 1 or type(reader.get('name')) is not str or not reader['name'].strip()
            or type(reader.get('capability')) is not str or type(reader.get('generation')) is not str
            or type(view) is not dict or type(view.get('runtime')) is not dict):
        return None
    try:
        owner, member = parse_capability_key(reader['capability'])
    except (ValueError, TypeError):
        return None
    if view['runtime'] != {'bundle': owner, 'generation': reader['generation'], 'operation': member}:
        return None
    return {'name': reader['name']}


def _exact_ref(record):
    return {'id': record.id, 'session': record.session, 'revision': record.revision, 'digest': record.digest}


def _exact(store, ref, problems, what):
    try:
        found = store.read(ref['id'], ref['session'], revision=ref['revision'])
    except (InvalidProject, OSError, KeyError, TypeError):
        problems.append(f'The {what} could not be read; nothing else was substituted for it')
        return None
    if found.digest != ref.get('digest'):
        problems.append(f'The {what} no longer matches its recorded digest; nothing else was substituted for it')
        return None
    return found


def _accepted_at(store, record, key, problems):
    """The revision that introduced this exact provenance; later saves kept it unchanged.

    None when an earlier revision cannot be read, so the accepting revision is unknown.
    """
    value, accepted = record.document['state'][key], record
    while accepted.revision > 1:
        try:
            earlier = store.read(record.id, record.session, revision=accepted.revision - 1)
        except (InvalidProject, OSError):
            problems.append(f'Revision {accepted.revision - 1} of this document could not be read, so the '
                            'revision that accepted this record is unknown')
            return None
        if earlier.document['state'].get(key) != value:
            break
        accepted = earlier
    return accepted


def _edit(store, record, change, accepted, problems):
    base = change.get('base') or {}
    out = {key: copy.deepcopy(change.get(key)) for key in
           ('base', 'input', 'output', 'at', 'authority', 'layer', 'view', 'frame', 'transaction', 'meaning',
            'runtimes', 'coordinateSpace', 'unrecorded')}
    out['replayable'] = False
    original = _exact(store, base, problems, 'original input revision')
    if original is not None and change.get('input') not in original.document['resources']:
        problems.append('The recorded input is not part of its original revision')
    output = change.get('output')
    if output not in record.document['resources']:
        if type(output) is dict and output.get('id') != record.id and 'hookeCopy' in record.document['state']:
            problems.append('This copy was made before copies kept their record up to date: its record still '
                            'names the values in the document it was copied from, not this copy\'s own')
        else:
            problems.append('This revision no longer holds the values that edit accepted; they were changed later')
    if accepted is not None and base.get('id') == record.id:
        if accepted.parent != base.get('digest'):
            problems.append('These values were not accepted directly on their recorded original revision')
    elif accepted is not None and not (accepted.revision == 1 and 'hookeCopy' in accepted.document['state']):
        problems.append('These values came from another document without a recorded copy')
    measured = _changed(store, change.get('input'), change.get('output'), problems)
    saved = change.get('changed')
    if saved is not None and measured is not None and saved != measured:
        problems.append('The saved change record disagrees with the saved values; the values were measured again')
    out['changed'] = measured if measured is not None or saved is None else saved
    out['changeRecorded'] = saved is not None
    out['producer'] = _producer(store, change.get('producer') or {}, base, problems)
    return out


def _changed(store, before, after, problems):
    """Which retained chunks differ, from immutable chunk digests; not a value-level diff."""
    from .scientific_arrays import _manifest
    if (type(before) is not dict or type(after) is not dict
            or not str(before.get('component', '')).startswith('array.')):
        return None
    try:
        old, new = _manifest(store, before['id'], before), _manifest(store, after['id'], after)
    except (InvalidProject, OSError, KeyError):
        problems.append('The retained values before or after this edit could not be read')
        return None
    layout = ('shape', 'dtype', 'axes', 'chunks')
    if any(old[key] != new[key] for key in layout):
        return {'component': after['component'], 'layout': 'changed', 'granularity': 'array',
                'meaning': 'The array layout changed, so every value is treated as changed.'}
    keys = sorted((k for k in new['parts'] if new['parts'][k] != old['parts'].get(k)),
                  key=lambda k: tuple(map(int, k.split(','))))
    bounds = None
    for key in keys:
        index = tuple(map(int, key.split(',')))
        box = [[n * c, min((n + 1) * c, size)] for n, c, size in zip(index, new['chunks'], new['shape'])]
        bounds = box if bounds is None else [[min(a[0], b[0]), max(a[1], b[1])] for a, b in zip(bounds, box)]
    return {'component': after['component'], 'axes': list(new['axes']), 'granularity': 'chunk',
            'chunks': len(keys), 'of': len(new['parts']), 'keys': keys[:MAX_CHANGED_KEYS],
            'truncated': len(keys) > MAX_CHANGED_KEYS, 'bounds': bounds,
            'meaning': 'Chunks whose retained bytes differ between the recorded input and output; '
                       'values inside a changed chunk may be partly unchanged.'}


def _producer(store, producer, base, problems):
    if producer.get('kind') in (None, 'snapshot'):
        return {'recorded': False, 'details': producer.get('details') or 'Producing actions were not recorded'}
    if producer.get('kind') != 'scientific-session':
        return {'recorded': True, 'verified': False, 'kind': producer.get('kind'), 'record': copy.deepcopy(producer),
                'details': 'Its producer recorded this as given. It names no saved history Hooke can check.'}
    out = {'recorded': True, 'verified': True, 'kind': producer['kind'], 'kernel': producer.get('kernel'),
           'actor': producer.get('actor'), 'origin': copy.deepcopy(producer.get('origin')),
           'history': copy.deepcopy(producer.get('history')), 'cells': None, 'actions': None}
    if producer.get('history') is None:
        out['details'] = 'No code or manual action had been saved in this session when the values were accepted'
        return out
    history = _exact(store, producer['history'], problems, 'producing history')
    if history is None:
        return out
    state = history.document.get('state') or {}
    if history.document.get('kind') != 'scientific-python-history' or state.get('input') != {
            key: base.get(key) for key in ('session', 'id', 'revision', 'digest')}:
        problems.append('The recorded history belongs to a different input; nothing else was substituted for it')
        return out
    if state.get('kernel') != producer.get('kernel'):
        problems.append('The recorded history belongs to a different kernel; nothing else was substituted for it')
        return out
    out['cells'] = copy.deepcopy(state.get('cells', []))
    out['actions'] = copy.deepcopy(state.get('actions', []))
    return out


def _output(store, record, provenance, problems):
    base = provenance.get('base') or {}
    original = _exact(store, base, problems, 'originating revision')
    if provenance.get('format') == 2:
        made = [{**{key: copy.deepcopy(row.get(key)) for key in ('layer', 'output', 'meaning', 'components')},
                 **({'coordinatesFrom':copy.deepcopy(row['coordinatesFrom'])} if 'coordinatesFrom' in row else {})}
                for row in provenance.get('outputs', [])]
    else:
        made = [{'layer': None, 'output': copy.deepcopy(provenance.get('output')),
                 'meaning': copy.deepcopy(provenance.get('meaning')),
                 'components': copy.deepcopy(provenance.get('components')),
                 **({'coordinatesFrom':copy.deepcopy(provenance['coordinatesFrom'])} if 'coordinatesFrom' in provenance else {})}]
    held = record.document['resources']
    coordinate_digests = {}
    for row in made:
        if 'coordinatesFrom' in row:
            from env_worker.scientific_output_coordinates import declaration, fingerprint
            try:
                relation = declaration(row['coordinatesFrom'])
                inputs = original.document['state']['scientificView']['inputs'] if original is not None else {}
                reference = relation['reference']
                if (inputs.get(relation['input']) != reference or original is None
                        or reference not in original.document['resources'] or reference not in provenance.get('inputs', [])):
                    raise InvalidProject('Output coordinates name a different admitted input')
                key = _json(reference)
                if key not in coordinate_digests:
                    source = store.array_source(original.id, original.session, original.revision, reference)
                    coordinate_digests[key] = fingerprint(source.shape, source.dtype, source.read)
                if coordinate_digests[key] != relation['sha256']:
                    raise InvalidProject('Output input fingerprint disagrees with the exact accepted native values')
            except (InvalidProject, ValueError, KeyError, TypeError, OSError) as error:
                problems.append('The retained output input association is invalid: ' + str(error))
        refs = [row['output'], *((row['components'] or {}).values())]
        if any(ref not in held for ref in refs):
            problems.append('This revision no longer holds every output that was accepted; they were changed later')
            break
    accepted = _accepted_at(store, record, 'hookeOutput', problems)
    if accepted is not None and accepted.revision != 1:
        problems.append('This output provenance was not accepted with the document it describes')
    return {'base': copy.deepcopy(base), 'inputs': copy.deepcopy(provenance.get('inputs')), 'made': made,
            'at': provenance.get('at'), 'transaction': provenance.get('transaction'), 'replayable': False,
            'runtimes': copy.deepcopy(provenance.get('runtimes')), 'unrecorded': provenance.get('unrecorded'),
            'coordinateMeaning': provenance.get('coordinateMeaning'),
            'producer': _producer(store, provenance.get('producer') or {}, base, problems)}


def _copy(store, record, lineage, problems):
    steps = lineage.get('history', []) if lineage.get('format') == 2 and 'history' in lineage else [lineage]
    for step in steps:
        _exact(store, step.get('source') or {}, problems, 'revision this copy was made from')
    accepted = _accepted_at(store, record, 'hookeCopy', problems)
    if accepted is not None and accepted.revision != 1:
        problems.append('This copy record was not accepted with the copy it describes')
    return {'conflictCopy': True, 'steps': copy.deepcopy(steps),
            'meaning': 'Saved as a separate copy because its original changed first; the original was not altered.'}


def _population_producer(record, producer):
    # An already accepted source record can contain complete per-object geometry.
    # Keep small legacy responses stable; reference larger attribution in its exact
    # immutable document instead of duplicating it into each bounded comparison arm.
    body = _json(producer)
    attribution = {'recorded': True, 'verified': False, 'kind': 'retained-population',
                   'details': 'Captured producer attribution; no verified native action history or inference timing'}
    if len(body) <= MAX_PRODUCER:
        attribution['record'] = json.loads(body)
    else:
        attribution['recordReference'] = {'document': _exact_ref(record),
            'path': ['state', 'populationSource', 'producer'], 'encoding': 'canonical-json',
            'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body)}
        attribution['details'] += '; complete attribution is retained in the referenced accepted document'
    return attribution


def _population(store, record, provenance, problems):
    """Validate the image→Labels edge emitted by atomic native population acceptance.

    Identity comes from the protected introducing record, not equal pixels or shape.
    Legacy associations check manifests only. An explicitly captured native source
    additionally checks at most one megapixel from each saved image under a bounded
    reservation. Neither path manufactures a scientific-session action history.
    """
    try:
        if (type(provenance) is not dict or provenance.get('format') != 2
                or record.revision != 1 or record.document['kind'] != 'population-labels'
                or provenance.get('operation') != 'retain-population-labels-v1'):
            raise InvalidProject('The population association was not accepted with its native document')
        source, producer = provenance['source'], provenance['producer']
        inputs, association = provenance['nativeInputs'], provenance['association']
        if (type(source) is not dict or set(source) != {'id', 'session', 'revision', 'execution', 'files'}
                or source['session'] != record.session or type(source['revision']) is not int or source['revision'] < 0
                or any(type(source[key]) is not str or not source[key] for key in ('id', 'execution'))
                or type(producer) is not dict or not producer
                or type(inputs) is not dict or set(inputs) != {'image', 'labels'}
                or association != {'input': inputs['image'], 'output': inputs['labels'],
                                   'source': source, 'producer': producer}):
            raise InvalidProject('The population association disagrees with its captured source or producer')
        references = [provenance['original'], inputs['image'], inputs['labels']]
        if any(type(ref) is not dict or ref.get('id') != record.id or ref.get('session') != record.session
               or ref not in record.document['resources'] for ref in references):
            raise InvalidProject('The population association names an unavailable or differently owned component')
        if inputs['image'] == inputs['labels']:
            raise InvalidProject('The population association must distinguish original image and labels')
        original = store.file_source(record.id, record.session, record.revision, provenance['original'],
                                     verify_content=False)
        if source['files']['source.image'] != original.sha256:
            raise InvalidProject('The population association does not retain its original source file')
        image, labels = [store.array_source(record.id, record.session, record.revision, inputs[key])
                         for key in ('image', 'labels')]
        space = {'session': record.session, 'id': record.id, 'revision': 1}
        calibration = {'space': space, 'axes': ['y', 'x'], 'units': ['pixel', 'pixel'],
                       'affine': [[1, 0, 0], [0, 1, 0]]}
        native = provenance.get('nativeSource')
        base, original_input = _exact_ref(record), inputs['image']
        if native is not None:
            from . import native_picture_inputs, jobs
            # The protected acceptance records exact source identity, independently checked
            # against both stored pixel sets; equal images from another owner are not a link.
            if image.dtype != '|u1' or len(image.shape) != 2 or image.shape[0] * image.shape[1] > native_picture_inputs.MAX_PIXELS:
                raise InvalidProject('The retained native picture exceeds its supported layout')
            with jobs.MEMORY.reserve(4):
                pixels = image.read(tuple(slice(0, n) for n in image.shape))
                captured, descriptor = native_picture_inputs.retained_source(store, native, record.session, pixels)
            if producer.get('capturedNativeInput') != native:
                raise InvalidProject('The population producer does not name this captured native input')
            base = {key: captured[key] for key in ('id', 'session', 'revision')}
            base['digest'] = captured['revisionDigest']
            original_input = captured['resource']
            calibration = descriptor.get('calibration')
        if (image.shape != labels.shape or image.axes != ('y', 'x') or labels.axes != image.axes
                or image.dtype != '|u1' or labels.dtype != '<u2'
                or image.descriptor.get('semantic') != 'intensity'
                or labels.descriptor.get('semantic') != 'instance-labels'
                or any(value.descriptor.get('calibration') != calibration for value in (image, labels))):
            raise InvalidProject('The population association has inconsistent native roles or coordinate identity')
        return {'base': base, 'input': copy.deepcopy(original_input),
                'output': copy.deepcopy(inputs['labels']), 'source': copy.deepcopy(source),
                'producer': _population_producer(record, producer),
                'replayable': False}
    except (InvalidProject, OSError, KeyError, TypeError, ValueError) as error:
        problems.append('The retained population producing association is invalid: ' + str(error))
        return None


def _tool_choice(store, record, current, origin, resource, problems):
    """An owned selection preserves an admitted input; it is not a producing action or copy."""
    from gpu_worker.bundle_contract import parse_capability_key
    try:
        if (type(origin) is not dict or set(origin) != {'format', 'source', 'capability', 'generation', 'name'}
                or type(origin['format']) is not int or origin['format'] != 1 or type(origin['name']) is not str or not origin['name']
                or type(origin['generation']) is not str
                or not re.fullmatch('[0-9a-f]{16}', origin['generation'])):
            raise InvalidProject('The tool selection has an unsupported captured identity')
        base = origin['source']
        if (type(base) is not dict or set(base) != {'id', 'session', 'revision', 'digest'}
                or base['id'] != record.id or base['session'] != record.session
                or type(base['revision']) is not int or base['revision'] != record.revision - 1):
            raise InvalidProject('The tool selection is not from the previous revision of this document')
        source = _exact(store, base, problems, 'selected tool input revision')
        if source is None:
            return None
        if record.parent != source.digest or record.transaction != hashlib.sha256(_json(origin)).hexdigest():
            raise InvalidProject('The tool selection was not accepted by its captured selection transaction')
        owner, member = parse_capability_key(origin['capability'])
        runtime = {'bundle': owner, 'generation': origin['generation'], 'operation': member}
        view, before = record.document['state']['scientificView'], source.document['state']['scientificView']
        inputs = view['inputs']
        now = current.document['state']['scientificView']
        if (type(inputs) is not dict or type(now) is not dict
                or now.get('runtime') != runtime or now.get('inputs') != inputs
                or any(ref not in current.document['resources'] for ref in inputs.values())):
            raise InvalidProject('The selected tool no longer holds its exact admitted inputs and runtime')
        if (record.document['kind'] != 'scene' or view.get('runtime') != runtime
                or runtime not in record.document['runtimes'] or type(inputs) is not dict
                or not inputs or inputs != before['inputs']
                or any(ref not in source.document['resources'] or ref not in record.document['resources']
                       for ref in inputs.values())):
            raise InvalidProject('The tool selection did not preserve its exact admitted native inputs')
        # Whole-document diagnostics list the choice; a lineage step must select
        # one exact resource that this choice actually admitted, never another array.
        if resource is not None and resource not in inputs.values():
            return None
        return {'base': _exact_ref(source), 'acceptedAt': _exact_ref(record),
                'input': copy.deepcopy(resource), 'capability': origin['capability'],
                'generation': origin['generation'], 'name': origin['name'],
                'meaning': 'Explicit tool selection preserved this accepted input; selection did not execute the tool'}
    except (InvalidProject, ValueError, KeyError, TypeError, OSError) as error:
        problems.append('The retained tool selection is invalid: ' + str(error))
        return None
