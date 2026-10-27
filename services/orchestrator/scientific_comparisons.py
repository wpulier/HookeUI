"""Retained alternatives share one accepted input; each owns a separate scene.

This module records preparation and accepted results, never infers completion from
an opened panel. Private execution remains with the existing scientific host.

Each arm keeps its attempts (format v2): a saved result's model, declared settings,
declared device, outputs and timing, or a failure or cancellation with its words.
A failed or cancelled attempt is a new revision beside the successful ones, never a
replacement. Choosing a preferred result is a separate, explicit action and changes
nothing outside this comparison.
"""
import hashlib
import json
import os
import time

from . import scientific_tools
from .scientific_projects import InvalidProject, _json

FORMAT = 'scientific-comparison-v2'
FORMATS = ('scientific-comparison-v1', FORMAT)
ARM_KEYS = {'id', 'capability', 'generation', 'name', 'owner', 'scene'}
ATTEMPT_KEYS = {'arm', 'outcome', 'source', 'at', 'model', 'settings', 'outputs', 'result', 'timing', 'says'}
OUTCOMES = {'succeeded': {'saved-result'}, 'failed': {'preparation', 'researcher'},
            'cancelled': {'preparation', 'researcher'}}
MAX_ATTEMPTS = 36
MAX_SAYS = 1000
MAX_ATTEMPT_BYTES = 16 << 10


def identity(saved):
    return {'id': saved.id, 'session': saved.session, 'revision': saved.revision, 'digest': saved.digest}


def reference(saved):
    return {'id': saved.id, 'session': saved.session, 'revision': saved.revision}


def exact(store, value, session):
    if (type(value) is not dict or set(value) != {'id', 'session', 'revision', 'digest'}
            or value['session'] != session):
        raise InvalidProject('Comparison needs an exact saved input in this Project')
    saved = store.read(value['id'], session, revision=value['revision'])
    if identity(saved) != value:
        raise InvalidProject('The captured comparison input changed; preserve the original comparison')
    return saved


def _number(value):
    return type(value) in (int, float) and value == value and abs(value) < 1e12


def _attempt(store, session, arms, row):
    if (type(row) is not dict or set(row) != ATTEMPT_KEYS or row['outcome'] not in OUTCOMES
            or row['source'] not in OUTCOMES[row['outcome']] or not any(a['id'] == row['arm'] for a in arms)
            or not _number(row['at']) or type(row['says']) is not str or len(row['says']) > MAX_SAYS
            or type(row['model']) is not dict or type(row['timing']) is not dict
            or type(row['outputs']) is not list or not (row['settings'] is None or type(row['settings']) is dict)
            or (row['result'] is None) != (row['outcome'] != 'succeeded')
            or len(_json(row)) > MAX_ATTEMPT_BYTES):
        raise InvalidProject('A saved comparison attempt is unsupported; keep its saved document')
    if row['result'] is not None:
        exact(store, row['result'], session)


def read(store, project, session, revision):
    """A v1 comparison reads as v2 with no attempts and no choice; its saved revision is unchanged."""
    saved = store.read(project, session, revision=revision)
    state = saved.document['state'].get('comparison')
    keys = {'format', 'input', 'arms', 'retained'}
    if (saved.document['kind'] != 'scientific-comparison' or type(state) is not dict or state.get('format') not in FORMATS
            or set(state) != (keys if state['format'] == FORMATS[0] else keys | {'attempts', 'chosen'})
            or type(state['arms']) is not list or not 2 <= len(state['arms']) <= 3
            or type(state['retained']) is not list or len(state['retained']) > 12):
        raise InvalidProject('This comparison version is unsupported; keep its saved document')
    state = {'attempts': [], 'chosen': None, **state, 'format': FORMAT}
    exact(store, state['input'], session)
    for i, arm in enumerate(state['arms']):
        if (type(arm) is not dict or set(arm) != ARM_KEYS
                or arm['id'] != 'abc'[i] or any(type(arm[k]) is not str for k in ('capability','generation','name','owner'))):
            raise InvalidProject('The saved comparison arm is unsupported')
        if arm['scene'] is not None:
            exact(store, arm['scene'], session)
    for row in state['retained']:
        if type(row) is not dict or set(row) != {'arm','result'} or not any(a['id'] == row['arm'] for a in state['arms']):
            raise InvalidProject('A retained comparison result has an unsupported association')
        exact(store, row['result'], session)
    if type(state['attempts']) is not list or len(state['attempts']) > MAX_ATTEMPTS:
        raise InvalidProject('This comparison’s attempts are unsupported; keep its saved document')
    for row in state['attempts']:
        _attempt(store, session, state['arms'], row)
    chosen = state['chosen']
    if chosen is not None and (type(chosen) is not dict or set(chosen) != {'arm', 'result', 'at'} or not _number(chosen['at'])
                               or not any(r['outcome'] == 'succeeded' and r['arm'] == chosen['arm'] and r['result'] == chosen['result']
                                          for r in state['attempts'])):
        raise InvalidProject('The chosen comparison result has an unsupported association')
    return saved, state


def create(store, project, session, revision, choices, request, *, environ=os.environ, check=lambda: None):
    """The same integration may be chosen more than once: each arm keeps its own scene and settings."""
    if (type(request) is not str or len(request) != 32 or any(c not in '0123456789abcdef' for c in request)
            or type(choices) is not list or not 2 <= len(choices) <= 3 or any(type(k) is not str for k in choices)):
        raise InvalidProject('Choose two or three alternatives')
    source = scientific_tools.scene(store, project, session, revision)
    readiness = scientific_tools.comparison_availability(source)
    if not readiness['available']:
        raise InvalidProject(readiness['reason'])
    available = {row['key']: row for row in scientific_tools.discover(store, project, session, revision, environ)['tools']}
    arms = []
    for i, key in enumerate(choices):
        row = available.get(key)
        if row is None or row['generation'] is None:
            raise InvalidProject('An alternative has no pinned runtime; check its integration setup first')
        arms.append({'id': 'abc'[i], 'capability': key, 'generation': row['generation'],
                     'name': row['name'], 'owner': row['owner'], 'scene': None})
    doc = {'kind': 'scientific-comparison', 'title': ('Compare · '+source.document['title'])[:240],
           'resources': [reference(source)], 'runtimes': source.document['runtimes'],
           'state': {'comparison': {'format': FORMAT, 'input': identity(source), 'arms': arms, 'retained': [],
                                    'attempts': [], 'chosen': None}}}
    key = hashlib.sha256(_json({'input': identity(source), 'request': request})).hexdigest()
    check()
    return store.create(session, doc, identity='p'+key[:32])


def _model(arm, environ):
    """What ran: the pinned integration, and the device its manifest declares (the tool reports none)."""
    from gpu_worker import manifests as catalog
    owner = {**catalog.manifests(), **catalog.mine(environ)}.get(arm['owner'])
    runs = getattr(owner, 'runs', None)
    return {'capability': arm['capability'], 'generation': arm['generation'], 'name': arm['name'],
            'device': {'declared': runs, 'by': 'integration manifest'} if type(runs) is str and runs
            else {'declared': None, 'by': 'not declared'}}


def _replayed(store, group, token):
    """A retried action from the same revision returns its saved revision, never a second record."""
    try:
        later = store.read(group.id, group.session, revision=group.revision + 1)
    except InvalidProject:
        return None
    return later if later.transaction == token else None


def _token(group, action):
    return hashlib.sha256(_json({'comparison': group.id, 'revision': group.revision, 'action': action})).hexdigest()


def _save_attempt(store, group, state, row, token, *, check=lambda: None):
    previous = _replayed(store, group, token)
    if previous is not None:
        return previous
    if len(state['attempts']) >= MAX_ATTEMPTS:
        raise InvalidProject('This comparison already keeps 36 attempts; start a new comparison')
    state['attempts'].append(row)
    doc = group.document
    doc['state']['comparison'] = state
    if row['result'] is not None:
        result = {k: row['result'][k] for k in ('id', 'session', 'revision')}
        if result not in doc['resources']:
            doc['resources'].append(result)
    return store.save(group.id, group.session, doc, expected_revision=group.revision, transaction=token, check=check)


def _unsuccessful(arm, outcome, source, says, environ, timing=None):
    return {'arm': arm['id'], 'outcome': outcome, 'source': source, 'at': time.time(),
            'model': _model(arm, environ), 'settings': None, 'outputs': [], 'result': None,
            'timing': timing or {}, 'says': str(says)[:MAX_SAYS]}


def prepare_arm(store, project, session, revision, arm_id, *, environ=os.environ, check=lambda: None):
    """Fork this arm's working input. A failure or a stop is kept as this arm's attempt; other arms are untouched."""
    group, state = read(store, project, session, revision)
    arm = next((row for row in state['arms'] if row['id'] == arm_id), None)
    if arm is None:
        raise InvalidProject('Choose an alternative in this comparison')
    if arm['scene'] is not None:
        return group
    source = exact(store, state['input'], session)
    token = hashlib.sha256(_json({'comparison': group.id, 'input': state['input'], 'arm': arm_id,
                                  'capability': arm['capability'], 'generation': arm['generation']})).hexdigest()
    stopped = []
    def owned():
        try:
            check()
        except BaseException:
            stopped.append(True)
            raise
    started = time.monotonic()
    try:
        chosen = scientific_tools.fork(store, source, arm['capability'], arm['generation'], token,
                                       environ=environ, check=owned)
    except (ValueError, OSError, RuntimeError) as error:
        outcome = 'cancelled' if stopped else 'failed'
        says = ('Stopped while preparing this alternative’s working input. ' if stopped else '') + str(error)
        row = _unsuccessful(arm, outcome, 'preparation', says, environ,
                            {'preparationSeconds': round(time.monotonic() - started, 3)})
        try:
            return _save_attempt(store, group, state, row, _token(group, {'prepare': arm_id, 'outcome': outcome}))
        except (ValueError, OSError) as record_error:
            raise InvalidProject(f'{says} The {outcome} attempt could not be recorded: {record_error}') from error
    arm['scene'] = identity(chosen)
    doc = group.document
    doc['state']['comparison'] = state
    doc['resources'].append(reference(chosen))
    doc['runtimes'] = list({tuple(sorted(r.items())): r for r in [*doc['runtimes'], *chosen.document['runtimes']]}.values())
    return store.save(group.id, session, doc, expected_revision=revision, transaction=token, check=check)


def _producing_history(store, result):
    """The private session's saved history that produced this result, when the save names it."""
    producer = result.document['state'].get('hookeOutput', {}).get('producer')
    history = producer.get('history') if type(producer) is dict else None
    if type(history) is not dict or set(history) != {'id', 'session', 'revision', 'digest'}:
        return None
    try:
        saved = exact(store, history, result.session)
    except (InvalidProject, OSError):
        return None
    return saved if saved.document['kind'] == 'scientific-python-history' else None


def timing(store, result):
    """Times Hooke observed, each said for what it measures. The tool reports no compute time of its own.

    controlToSaveSeconds runs from the last plugin-panel action, which may be the Run click or a later change.
    """
    produced = result.document['state'].get('hookeOutput', {})
    saved_at = produced.get('at') if _number(produced.get('at')) else result.at
    out = {'savedAt': saved_at, 'computeSeconds': None,
           'computeSays': 'The tool does not report its own compute time.'}
    history = _producing_history(store, result)
    if history is None:
        out['controls'] = None
        return out
    actions = [row for row in history.document['state'].get('actions', [])
               if type(row) is dict and type(row.get('request')) is dict and row['request'].get('op') == 'native-panel-change'
               and _number(row.get('at')) and row['at'] <= saved_at]
    out['controls'] = len(actions)
    if actions:
        first, last = actions[0]['at'], actions[-1]['at']
        out.update(firstControlAt=first, lastControlAt=last, controlToSaveSeconds=round(saved_at - last, 3),
                   sessionSeconds=round(saved_at - first, 3))
    return out


def settings(result):
    """The integration's declared reproducible settings as the panel held them at Save tool.

    Hooke does not see the Run itself, so a value changed after the run would appear here: the record says
    when it was captured instead of claiming these settings produced the outputs.
    """
    view = result.document['state'].get('scientificView')
    if type(view) is not dict or 'tool' not in view:
        return None
    from env_worker.scientific_tool_document import snapshot, InvalidDocument
    try:
        tool = snapshot(view['tool'], view.get('layers', []))
    except (InvalidDocument, KeyError, TypeError, ValueError):
        return None
    recipe = tool.get('recipe')
    return ({'schema': recipe['schema'], 'version': recipe['version'], 'values': recipe['state'],
             'captured': 'panel-at-save-tool'} if recipe else None)


def outputs(result):
    """Each saved layer: its name, kind, shape, declared meaning and exact stored array."""
    state = result.document['state']
    view, produced = state.get('scientificView', {}), state.get('hookeOutput', {})
    meanings = {}
    for record in produced.get('outputs', []) if type(produced.get('outputs')) is list else []:
        if type(record) is dict and type(record.get('output')) is dict:
            meanings[json.dumps(record['output'], sort_keys=True)] = record.get('meaning')
    rows = []
    for layer in view.get('layers', []) if type(view) is dict else []:
        info = layer.get('metadata', {}) if type(layer) is dict else {}
        stored = view.get('inputs', {}).get(layer.get('input')) if type(layer) is dict else None
        meaning = meanings.get(json.dumps(stored, sort_keys=True)) or (
            produced.get('meaning') if stored is not None and stored == produced.get('output') else None)
        rows.append({'name': str(info.get('name', ''))[:240], 'type': str(info.get('type', ''))[:40],
                     'shape': info.get('shape') if type(info.get('shape')) is list else None,
                     'meaning': meaning, 'stored': stored})
    return rows[:64]


def retain(store, project, session, revision, arm_id, result_id, result_revision, *, environ=os.environ, check=lambda: None):
    """Keep one saved result as this arm's succeeded attempt, with what produced it."""
    group, state = read(store, project, session, revision)
    arm = next((row for row in state['arms'] if row['id'] == arm_id), None)
    if arm is None or arm['scene'] is None:
        raise InvalidProject('Open this alternative before retaining its result')
    result = store.read(result_id, session, revision=result_revision)
    produced = result.document['state'].get('hookeOutput', {})
    if type(produced) is not dict or produced.get('base') != arm['scene']:
        raise InvalidProject('This result was not saved from the selected comparison input and alternative')
    row = {'arm': arm_id, 'result': identity(result)}
    if row in state['retained'] and any(a['result'] == row['result'] for a in state['attempts']):
        return group
    if row not in state['retained']:
        if len(state['retained']) >= 12:
            raise InvalidProject('This comparison already retains twelve results; start a new comparison')
        state['retained'].append(row)
    attempt = {'arm': arm_id, 'outcome': 'succeeded', 'source': 'saved-result', 'at': time.time(),
               'model': _model(arm, environ), 'settings': settings(result), 'outputs': outputs(result),
               'result': identity(result), 'timing': timing(store, result), 'says': ''}
    check()
    return _save_attempt(store, group, state, attempt, _token(group, {'retain': row}), check=check)


def settle(store, project, session, revision, arm_id, outcome, says, request, *, environ=os.environ, check=lambda: None):
    """The researcher's own record that an arm failed or was cancelled; it is shown as theirs."""
    group, state = read(store, project, session, revision)
    arm = next((row for row in state['arms'] if row['id'] == arm_id), None)
    if arm is None:
        raise InvalidProject('Choose an alternative in this comparison')
    if (outcome not in ('failed', 'cancelled') or type(says) is not str or len(says) > MAX_SAYS
            or type(request) is not str or len(request) != 32 or any(c not in '0123456789abcdef' for c in request)):
        raise InvalidProject('Record a failure or a cancellation with what happened, up to 1000 characters')
    if outcome == 'failed' and not says.strip():
        raise InvalidProject('Say what failed, for example the message the tool showed')
    if any(r['timing'].get('request') == request for r in state['attempts']):
        return group
    row = _unsuccessful(arm, outcome, 'researcher', says.strip(), environ, {'request': request})
    check()
    return _save_attempt(store, group, state, row, _token(group, {'settle': request}), check=check)


def choose(store, project, session, revision, arm_id, result_id, result_revision, *, check=lambda: None):
    """Record the preferred result here only; None clears it. No tool, default or future run changes."""
    group, state = read(store, project, session, revision)
    if arm_id is None:
        chosen = None
    else:
        match = next((r for r in state['attempts'] if r['outcome'] == 'succeeded' and r['arm'] == arm_id
                      and r['result']['id'] == result_id and r['result']['revision'] == result_revision), None)
        if match is None:
            raise InvalidProject('Choose a result this comparison kept as succeeded')
        chosen = {'arm': arm_id, 'result': match['result'], 'at': time.time()}
    if (state['chosen'] or {}).get('result') == (chosen or {}).get('result'):
        return group
    token = _token(group, {'choose': chosen and chosen['result']})
    previous = _replayed(store, group, token)
    if previous is not None:
        return previous
    state['chosen'] = chosen
    doc = group.document
    doc['state']['comparison'] = state
    check()
    return store.save(project, session, doc, expected_revision=revision, transaction=token, check=check)


def outcomes(store, project, session, revision):
    group, state = read(store, project, session, revision)
    rows = []
    # ProjectStore owns the inventory and includes only this session. Never infer
    # a completed operation from a preview or a worker's acknowledgement.
    for saved in store.list(session):
        produced = saved.document['state'].get('hookeOutput', {})
        if type(produced) is not dict:
            continue
        arm = next((a for a in state['arms'] if a['scene'] is not None and produced.get('base') == a['scene']), None)
        if arm is not None:
            rows.append({'arm': arm['id'], 'result': identity(saved), 'title': saved.document['title'],
                         'savedAt': saved.at, 'configuration': configuration(saved), 'retained': {'arm': arm['id'], 'result': identity(saved)} in state['retained']})
        if len(rows) == 48:
            break
    return rows


def configuration(saved):
    """A matching input is not proof that the producing settings were captured."""
    view = saved.document['state'].get('scientificView')
    if type(view) is not dict or 'tool' not in view:
        return 'not-retained'
    from env_worker.scientific_tool_document import snapshot, InvalidDocument
    try:
        snapshot(view['tool'], view.get('layers', []))
    except (InvalidDocument, KeyError, TypeError, ValueError):
        return 'not-retained'
    return 'retained-tool-state'
