"""Retain method arms against exact native Project inputs, without running or ranking them.

This is evidence retention, not a scientific qualification or an area calculation. Recorded
actions/code stay in their original immutable histories. Caller declarations remain attributed
declarations; missing provenance is not invented. The aggregate method_comparison API is separate.
"""
from __future__ import annotations

import json
import math

from env_worker.scientific_calibration import same_calibration
from . import admission, scientific_provenance
from .method_comparison import FACTORS
from .scientific_projects import InvalidProject, _json

FORMAT = 'hooke-method-evidence-v1'
LEGACY_MEANING = ('Exact retained values and recorded producing context; declarations are attributed, '
                  'not independently verified. No inference, area calculation or scientific qualification ran.')
CAPTURE_MEANING = ('Exact retained values and recorded producing context; declarations are attributed, '
                   'not independently verified. This evidence-capture step neither reruns producing methods '
                   'nor assesses scientific qualification; comparison calculations are recorded separately.')
KIND = 'scientific-method-comparison'
MAX_METADATA = 256 << 10
MAX_LINEAGE = 32
TIMING_SCOPE = 'recorded actions and cells; not model inference timing'
POPULATION_HISTORY_LIMIT = ('Population producer attribution is retained; native producing actions '
                            'and inference timing were not recorded')


def _bounded(value):
    body = _json(value)
    if len(body) > MAX_METADATA:
        raise InvalidProject('Method evidence exceeds its metadata allowance')
    return json.loads(body)


def _words(value, what, maximum=4096):
    if type(value) is not str or not 0 < len(value.strip()) <= maximum:
        raise InvalidProject(f'{what} needs 1 to {maximum} characters')
    return value.strip()


def _exact(saved):
    return {'id': saved.id, 'session': saved.session, 'revision': saved.revision, 'digest': saved.digest}


def _record(store, reference, session):
    if (type(reference) is not dict or set(reference) != {'id', 'session', 'revision', 'digest'}
            or reference['session'] != session or type(reference['revision']) is not int
            or reference['revision'] < 1):
        raise InvalidProject('Method evidence needs an exact revision in its own session')
    saved = store.read(reference['id'], session, revision=reference['revision'])
    if reference['digest'] != saved.digest:
        raise InvalidProject('The retained revision digest does not match; nothing was substituted')
    return saved


def _native(store, reference, session):
    if (type(reference) is not dict
            or set(reference) != {'kind', 'session', 'id', 'revision', 'component', 'digest'}
            or reference['kind'] != 'array'):
        raise InvalidProject('Choose an exact native array revision and its digest')
    _, descriptor, captured = admission.native_source(
        store, {key: value for key, value in reference.items() if key != 'digest'}, session)
    if captured['revisionDigest'] != reference['digest']:
        raise InvalidProject('The native revision digest does not match; nothing was substituted')
    return captured, descriptor


def _selection(captured):
    return {**{key: captured[key] for key in ('kind', 'session', 'id', 'revision', 'component')},
            'digest': captured['revisionDigest']}


def _layout(source, mask):
    if any(source.get(key) != mask.get(key) for key in ('shape', 'axes')):
        raise InvalidProject('Each mask must have the frozen input shape and ordered axes')
    a, b = source.get('calibration'), mask.get('calibration')
    if not (a is None and b is None) and not same_calibration(a, b):
        raise InvalidProject('Each mask must have the frozen input calibration and coordinate identity')
    if mask.get('semantic') not in ('instance-labels', 'class-labels'):
        raise InvalidProject('Choose a retained label mask with an explicit semantic role')


def _timing(events):
    seconds = 0.0
    for event in events:
        start, end = event.get('at'), event.get('finishedAt')
        if (event.get('state') != 'completed' or type(start) not in (int, float)
                or type(end) not in (int, float) or not math.isfinite(start)
                or not math.isfinite(end) or end < start):
            seconds = None
            break
        seconds += end - start
        if not math.isfinite(seconds):
            seconds = None
            break
    return {'seconds': seconds if events else None, 'events': len(events), 'scope': TIMING_SCOPE}


def _lineage(store, captured, source, session):
    """Follow accepted edit/output associations only, never matching by shape or a caller's claim."""
    current = _record(store, {key: value for key, value in _selection(captured).items()
                              if key not in ('kind', 'component')}, session)
    resource, records, histories, limitations, events, runtimes = captured['resource'], [], [], [], {}, []
    associations, selections = [], []
    for _ in range(MAX_LINEAGE):
        ref = _exact(current)
        if ref in records:
            raise InvalidProject('Method evidence has a cyclic producing lineage')
        records.append(ref)
        for runtime in current.document['runtimes']:
            if runtime not in runtimes:
                runtimes.append(runtime)
        if ref == {key: value for key, value in _selection(source).items() if key not in ('kind', 'component')}:
            if resource != source['resource']:
                provenance = scientific_provenance.resolve(store, current.id, session, current.revision, resource=resource)
                if provenance['problems']:
                    raise InvalidProject('; '.join(provenance['problems']))
                association = provenance.get('population')
                if (association is None or association['base'] != ref or association['output'] != resource
                        or association['input'] != source['resource']):
                    raise InvalidProject('The mask came from a different frozen input component')
                # This verified terminal edge stays in one exact revision. It is not
                # another traversal step or an exception to document-cycle refusal.
                associations.append(association)
                limitations.append(POPULATION_HISTORY_LIMIT)
            if not histories:
                limitations.append('No producing actions were recorded for this arm')
            break
        provenance = scientific_provenance.resolve(store, current.id, session, current.revision, resource=resource)
        if provenance['problems']:
            raise InvalidProject('; '.join(provenance['problems']))
        changes = [edit for edit in provenance['edits'] if edit['output'] == resource]
        choice = provenance.get('inputSelection')
        if choice is not None and choice['input'] == resource:
            # A validated selection is the newer identity-preserving edge. Its
            # input can itself be a reader/tool output with older provenance.
            selections.append(choice)
            current = _record(store, choice['base'], session)
            continue
        output = provenance['outputs']
        if not changes and output is not None and any(resource in [row['output'],
                *((row['components'] or {}).values())] for row in output['made']):
            rows = [row for row in output['made'] if resource in [row['output'], *((row['components'] or {}).values())]]
            if len(rows) != 1:
                raise InvalidProject('The mask has ambiguous output associations')
            relation = rows[0].get('coordinatesFrom')
            direct = output['base'] == {key: value for key, value in _selection(source).items()
                                       if key not in ('kind', 'component')}
            if direct:
                if source['resource'] not in output['inputs'] or (relation is not None and relation['reference'] != source['resource']):
                    raise InvalidProject('The output was produced from a different frozen input component')
                reference = source['resource']
            else:
                if relation is None:
                    raise InvalidProject('The output was produced from a different frozen input revision without an explicit input association')
                reference = relation['reference']
                selected = _record(store, output['base'], session)
                admission = scientific_provenance.resolve(store, selected.id, session, selected.revision,
                                                          resource=reference)
                if admission['problems']:
                    raise InvalidProject('; '.join(admission['problems']))
                choice = admission.get('inputSelection')
                frozen_input = {key: value for key, value in _selection(source).items()
                                if key not in ('kind', 'component')}
                if choice is None or choice['base'] != frozen_input or choice['input'] != source['resource']:
                    raise InvalidProject('The output has no verified tool selection from this frozen input')
            changes = [{**output, 'input': reference}]
        association = provenance.get('population')
        if not changes and association is not None and association['output'] == resource:
            if (association['base'] != {key: value for key, value in _selection(source).items()
                                       if key not in ('kind', 'component')}
                    or association['input'] != source['resource']):
                raise InvalidProject('The population was produced from a different frozen input revision')
            associations.append(association)
            limitations.append(POPULATION_HISTORY_LIMIT)
            changes = [association]
        if not changes:
            if len(records) > 1:
                raise InvalidProject('The recorded lineage ends at a different frozen input revision')
            limitations.append('The producing input and actions were not recorded; a shared input is unverified')
            break
        if len(changes) != 1:
            raise InvalidProject('The mask has ambiguous producing records')
        change = changes[0]
        producer = change['producer']
        history = producer.get('history')
        if producer.get('verified') and history is not None:
            _record(store, history, session)
            if history not in histories:
                histories.append(history)
            for collection in ('cells', 'actions'):
                for row in producer[collection]:
                    # A newer history includes earlier actions; count each recorded event once.
                    events.setdefault((producer['kernel'], collection, row['id']), row)
        else:
            limitations.append('Producing code or manual actions were not recorded in a verified history')
        current = _record(store, change['base'], session)
        resource = change['input']
    else:
        raise InvalidProject('Method evidence exceeds its producing lineage allowance')
    timing = _timing(list(events.values()))
    if events and timing['seconds'] is None:
        limitations.append('Some recorded actions have no confirmed terminal timing')
    return {'lineage': records, 'histories': histories, 'runtimes': runtimes,
            **({'producingAssociations': associations} if associations else {}),
            **({'inputSelections': selections} if selections else {}), 'timing': timing,
            'limitations': list(dict.fromkeys(limitations))}


def _unsuccessful(store, arm, source, session):
    outcome = arm.get('outcome')
    if (type(outcome) is not dict or set(outcome) not in ({'says'}, {'says', 'history', 'event'})
            or 'mask' in arm):
        raise InvalidProject('A failed or cancelled arm needs an outcome, not an accepted mask')
    outcome = {**outcome, 'says': _words(outcome['says'], 'The outcome')}
    result = {'outcome': outcome, 'lineage': [], 'histories': [], 'runtimes': [], 'observed': None,
              'timing': _timing([]),
              'limitations': ['This arm has no accepted mask; no area or disagreement was calculated']}
    if 'history' not in outcome:
        result['limitations'].append('The outcome is declared by the actor; no terminal history was recorded')
        return result
    history = _record(store, outcome['history'], session)
    state = history.document['state']
    if history.document['kind'] != 'scientific-python-history' or state.get('input') != {
            key: value for key, value in _selection(source).items() if key not in ('kind', 'component')}:
        raise InvalidProject('The outcome history belongs to a different frozen input revision')
    event = outcome['event']
    if (type(event) is not dict or set(event) != {'kind', 'id'}
            or event['kind'] not in ('cell', 'action') or type(event['id']) is not str):
        raise InvalidProject('Choose the exact recorded terminal event')
    rows = [row for row in state.get('cells' if event['kind'] == 'cell' else 'actions', [])
            if row.get('id') == event['id']]
    if len(rows) != 1 or rows[0].get('state') not in ('completed', 'interrupted', 'not-dispatched'):
        raise InvalidProject('The terminal event is missing or its outcome is still unconfirmed')
    row = rows[0]
    failed = (bool((row.get('transcript') or {}).get('error'))
              or bool((row.get('outcome') or {}).get('error'))
              or ((row.get('outcome') or {}).get('edit') or {}).get('state') == 'refused')
    cancelled = (row['state'] in ('interrupted', 'not-dispatched')
                 or ((row.get('outcome') or {}).get('edit') or {}).get('state') == 'cancelled')
    if not (failed if arm['status'] == 'failed' else cancelled):
        raise InvalidProject('The terminal event does not support the declared outcome')
    result.update(histories=[outcome['history']], runtimes=history.document['runtimes'], timing=_timing([row]),
                  observed={key: value for key, value in row.items() if key not in ('source', 'request', 'context')})
    if row['state'] == 'interrupted':
        result['limitations'].append('The request may have run; interruption does not confirm its private outcome')
    return result


def prepare(store, session, source, arms, actor):
    actor = _words(actor, 'The declaring actor', 128)
    source, descriptor = _native(store, source, session)
    if (type(arms) is not list or not 2 <= len(arms) <= 3 or any(type(arm) is not dict for arm in arms)
            or [arm.get('id') for arm in arms] != list('abc'[:len(arms)])):
        raise InvalidProject('Compare two or three declared arms, A, B and optionally C')
    rows = []
    runtimes = store.read(source['id'], session, revision=source['revision']).document['runtimes']
    for arm in arms:
        if (set(arm) - {'id', 'factor', 'says', 'status', 'mask', 'declaration', 'outcome'}
                or arm.get('factor') not in FACTORS or arm.get('status') not in ('completed', 'failed', 'cancelled')):
            raise InvalidProject('Declare each arm factor and its completed, failed or cancelled status')
        declaration = arm.get('declaration', {})
        if type(declaration) is not dict:
            raise InvalidProject('Method declarations must be an object; omitted facts remain unrecorded')
        if arm['status'] == 'completed':
            if 'outcome' in arm:
                raise InvalidProject('A completed arm retains its producing history, not a replacement outcome')
            mask, mask_descriptor = _native(store, arm.get('mask'), session)
            _layout(descriptor, mask_descriptor)
            trace = {'mask': mask, **_lineage(store, mask, source, session)}
        else:
            trace = _unsuccessful(store, arm, source, session)
        for runtime in trace['runtimes']:
            if runtime not in runtimes:
                runtimes.append(runtime)
        rows.append({'id': arm['id'], 'factor': arm['factor'], 'says': _words(arm.get('says'), 'The method'),
                     'status': arm['status'], 'declaration': declaration,
                     'declaredBy': actor, **trace})
    return _bounded({'format': FORMAT, 'source': source, 'descriptor': descriptor, 'arms': rows,
                     'actor': actor, 'runtimes': runtimes, 'ranked': False, 'qualification': 'not-assessed',
                     'evidenceComplete': all(not row['limitations'] for row in rows), 'chosen': None,
                     'meaning': CAPTURE_MEANING})


def create(store, session, source, arms, *, actor):
    """Validate everything before atomic publication; original inputs/arms never change.

    Source and completed masks name {kind: array, session, id, revision, component, digest}.
    Arms declare id (a/b/c), factor, says, status, optional attributed declaration, and either
    a completed mask or outcome {says, optional history + event {kind: cell/action, id}}.
    The authenticated caller supplies actor separately. No software/model declaration is
    inferred for manual methods; retained histories carry what was actually recorded.
    """
    request = _bounded({'source': source, 'arms': arms, 'actor': _words(actor, 'The declaring actor', 128)})
    evidence = prepare(store, session, **request)
    return store.create(session, {'kind': KIND, 'title': 'Method comparison', 'resources': [],
                                 'runtimes': evidence['runtimes'], 'state': {'methodEvidence': evidence}})


def read(store, project, session, revision):
    """Revalidate exact accepted dependencies on reopen. Missing inputs are never replaced by latest."""
    if type(revision) is not int or revision < 1:
        raise InvalidProject('Select an exact accepted method comparison revision')
    saved = store.read(project, session, revision=revision)
    evidence = saved.document['state'].get('methodEvidence')
    if saved.document['kind'] != KIND or type(evidence) is not dict or evidence.get('format') != FORMAT:
        raise InvalidProject('This revision is not retained method evidence')
    evidence = _bounded(evidence)
    try:
        arms = [{**{key: row[key] for key in ('id', 'factor', 'says', 'status', 'declaration')},
                 **({'mask': _selection(row['mask'])} if row['status'] == 'completed'
                    else {'outcome': row['outcome']})} for row in evidence['arms']]
        source, actor = _selection(evidence['source']), evidence['actor']
    except (KeyError, TypeError) as error:
        raise InvalidProject('The retained method evidence has an invalid record structure') from error
    expected = prepare(store, session, source, arms, actor)
    # Exact legacy text remains part of its immutable record; no arbitrary metadata is ignored.
    if evidence.get('meaning') == LEGACY_MEANING:
        expected['meaning'] = LEGACY_MEANING
    if evidence.get('chosen') is not None:
        choice = evidence['chosen']
        if type(choice) is not dict or set(choice) != {'arm', 'rationale', 'by'}:
            raise InvalidProject('The retained method choice is invalid')
        if not any(row['id'] == choice['arm'] and row['status'] == 'completed' for row in expected['arms']):
            raise InvalidProject('Choose an arm with an accepted mask')
        expected['chosen'] = {'arm': choice['arm'], 'rationale': _words(choice['rationale'], 'The rationale'),
                              'by': _words(choice['by'], 'The choosing actor', 128)}
    if evidence != expected or saved.document['runtimes'] != expected['runtimes']:
        raise InvalidProject('The retained method evidence disagrees with its exact producing records')
    return evidence


def choose(store, project, session, revision, arm, rationale, *, actor, check=lambda:None, admission=None):
    """Accept a single choice with rationale by CAS; alternatives and prior revisions remain immutable."""
    evidence = read(store, project, session, revision)
    if evidence['chosen'] is not None:
        raise InvalidProject('This method revision is already chosen; preserve its original rationale')
    if not any(row['id'] == arm and row['status'] == 'completed' for row in evidence['arms']):
        raise InvalidProject('Choose an arm with an accepted mask')
    evidence['chosen'] = {'arm': arm, 'rationale': _words(rationale, 'The rationale'),
                          'by': _words(actor, 'The choosing actor', 128)}
    document = store.read(project, session, revision=revision).document
    document['state']['methodEvidence'] = _bounded(evidence)
    if admission is not None:
        document['state']['methodAdmission'] = {**document['state'].get('methodAdmission', {}), 'choice': _bounded(admission)}
    return store.save(project, session, document, expected_revision=revision, check=check)
