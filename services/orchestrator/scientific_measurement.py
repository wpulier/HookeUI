"""One built-in native measurement admission/publication path for Studio and Registry.

operations.Runner owns model implementations, captures and model Result storage. This
operation instead consumes accepted Project arrays and atomically appends a typed table
with Project CAS. It shares native-input resolution, capture memory, request tickets,
owned cancellation, execution tracing and Registry receipts; no model environment exists.
"""
import copy
import threading
import uuid

from . import execution, scientific_resource_actions as actions, scientific_tables as tables
from .capture import captures
from .scientific_project_api import _owned, Refused
from .scientific_projects import Conflict, InvalidProject
from .ticket import current

OPERATION = actions.MEASUREMENTS
CALLERS = {'studio', 'agent:chat', 'agent:voice', 'agent:system', 'agent:researcher'}


def descriptor():
    return {'id': OPERATION, 'title': 'Measure saved objects', 'implementation': 'builtin',
            'input': {'kind': 'array', 'semantic': 'instance-labels', 'revision': 'exact-accepted'},
            'output': {'kind': 'table', 'semantic': 'object-table', 'publication': 'project-revision'},
            'preparation': 'none', 'workingBytes': actions.WORKING_BYTES}


def _selection(operation, selection, session):
    if operation != OPERATION:
        raise InvalidProject('Choose the declared Measure saved objects operation')
    if (type(selection) is not dict or set(selection) != {'project', 'session', 'revision', 'input'}
            or any(type(selection[k]) is not str or not selection[k] for k in ('project', 'session', 'input'))
            or type(selection['revision']) is not int or selection['revision'] < 1):
        raise InvalidProject('Choose an exact saved Project revision and labels input')
    if selection['session'] != session:
        raise Refused('The active Project changed.', 409, next='return to the original Project before measuring')
    return copy.deepcopy(selection)


def describe(store, operation, selection, session):
    selected = _selection(operation, selection, session)
    saved, reference, source = actions.selected(store, selected['project'], session, selected['revision'], selected['input'])
    return {'operation': descriptor(), 'readiness': actions.measurement_plan(source),
            'source': {'id': saved.id, 'session': session, 'revision': saved.revision,
                       'digest': saved.digest, 'input': selected['input'], 'reference': reference}}


async def run(hub, day, operation, selection, *, caller, stopping=None, check_caller=lambda: None, researcher=None):
    """Caller identity is supplied by trusted transports, never accepted from request JSON.

    A retry may return only the immediately succeeding, matching completed publication.
    Its original producer stays intact; the new invocation is reported separately.
    """
    if caller not in CALLERS:
        raise InvalidProject('Unknown measurement caller')
    if caller == 'agent:researcher':
        if (type(researcher) is not dict or set(researcher) != {'id', 'run', 'runId'}
                or type(researcher['id']) is not str or not 0 < len(researcher['id']) <= 128
                or type(researcher['runId']) is not str or not 0 < len(researcher['runId']) <= 128
                or type(researcher['run']) is not int or researcher['run'] < 0):
            raise InvalidProject('Researcher measurements need their original run identity')
    elif researcher is not None:
        raise InvalidProject('Only the research transport supplies a researcher identity')
    session = day.sid
    selected = _selection(operation, selection, session)  # detach before the first await
    stopping = stopping if stopping is not None else threading.Event()
    ticket = current()
    generation = getattr(day, 'generation', None)
    invocation = {'id': uuid.uuid4().hex, 'caller': caller}
    if researcher is not None:
        invocation['researcher'] = copy.deepcopy(researcher)
    if ticket is not None:
        invocation['ticket'] = ticket.id
        if ticket.admission is not None:
            invocation['request'] = ticket.admission.request_id

    def check():
        if stopping.is_set():
            raise InvalidProject('Measurement stopped; accepted arrays remain unchanged')
        if day.sid != session or getattr(day, 'generation', None) != generation:
            raise Refused('The active Project changed.', 409, next='return to the original Project before measuring')
        if ticket is not None and ticket.superseded('session'):
            raise InvalidProject(ticket.superseded('session'))
        check_caller()

    store = hub.resources.projects
    def work():
        check()
        prepared = describe(store, operation, selected, session)
        if not prepared['readiness']['available']:
            raise InvalidProject(prepared['readiness']['reason'])
        latest = store.read(selected['project'], session)
        if latest.revision != selected['revision']:
            # No execution retry silently retargets current labels or overwrites other work.
            if (latest.revision == selected['revision'] + 1
                    and latest.transaction == actions.measurement_transaction(prepared['source'])):
                pointer = latest.document['state'].get('scientificMeasurements', {})
                if type(pointer) is dict and pointer.get('source') == prepared['source'] and pointer.get('format') == OPERATION:
                    table = tables.open_table(store, latest.id, session, latest.revision, actions.TABLE)
                    producer = table.schema['producer']
                    if (type(producer) is dict and producer.get('operation') == OPERATION and producer.get('source') == prepared['source']
                            and type(producer.get('admission')) is dict
                            and producer['admission'].get('caller') in CALLERS):
                        actions.table(store, latest.id, session, latest.revision)  # validate accepted source/table binding
                        check()
                        return {'project': latest, 'producer': producer, 'invocation': invocation,
                                'operation': prepared['operation'], 'reused': True}
            raise Conflict('The saved revision changed; reopen it and select the accepted labels again')
        check()
        saved = actions.measure(store, selected['project'], session, selected['revision'], selected['input'],
                                check=check, admission=invocation)
        producer = tables.open_table(store, saved.id, session, saved.revision, actions.TABLE).schema['producer']
        return {'project': saved, 'producer': producer, 'invocation': invocation,
                'operation': prepared['operation'], 'reused': False}

    check()
    with execution.request(hub, 'scientific-measurement'), execution.span(OPERATION, provider='app', adapter=__name__):
        with captures(hub).memory.reserve('analysis', actions.WORKING_BYTES, 'measuring retained objects'):
            return await _owned(work, on_cancel=stopping.set)
