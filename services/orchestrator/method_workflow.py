"""Declared native method comparison: exact retained evidence, bounded counts, no producer run."""
import copy
import hashlib
import math
import threading
import uuid

import numpy as np

from . import admission, execution, method_comparison, method_evidence
from .capture import captures
from .scientific_file_intake import request_identity
from .scientific_project_api import _owned
from .scientific_projects import InvalidProject, Conflict, _json
from .ticket import current

OPERATION = 'compare-retained-methods-v1'
WORKING_BYTES = 16 << 20
MAX_MASK_BYTES = 64 << 20


def descriptor():
    return {'id': OPERATION, 'title': 'Compare retained methods', 'implementation':'builtin', 'preparation':'none',
            'input':'exact-native-source-and-two-or-three-arms', 'output':'retained-method-comparison',
            'workingBytes':WORKING_BYTES, 'maskBytes':MAX_MASK_BYTES,
            'method':'Foreground is every nonzero label. Compare area/volume and disagreement against A; never rank accuracy.'}


def _native(store, captured, session):
    return admission.native_source(store, {k:captured[k] for k in ('kind','session','id','revision','component')}, session)[0]


def _sources(store, evidence, session):
    descriptor = evidence['descriptor']
    calibration = descriptor.get('calibration')
    if not calibration or not calibration.get('space'):
        raise InvalidProject('Coordinate identity is missing. Retain masks with the explicit input grid; equal shape does not establish alignment')
    axes = descriptor['axes']
    if set(axes) not in ({'y','x'},{'z','y','x'}):
        raise InvalidProject('Choose a 2D or 3D spatial input; time and channel require an explicit selection')
    sources = {}
    for arm in evidence['arms']:
        if arm['status'] != 'completed':
            continue
        source = _native(store,arm['mask'],session)
        if np.dtype(source.dtype).kind not in 'uib' or math.prod(source.shape)*np.dtype(source.dtype).itemsize > MAX_MASK_BYTES:
            raise InvalidProject('Choose nonnegative integer labels up to 64 MiB per arm; retain a smaller region first')
        sources[arm['id']] = source
    return sources


def describe(store, session, source, arms, *, actor):
    evidence = method_evidence.prepare(store,session,source,arms,actor)
    _sources(store,evidence,session)
    return {'operation':descriptor(), 'readiness':{'available':True,'verification':'metadata only; values checked at Retain',
            'limits':'Explicit shared grid; spatial integer labels up to 64 MiB per arm. Unrecorded histories stay unverified.'},
            'evidence':evidence}


def _summary(store, evidence, session, check):
    sources = _sources(store,evidence,session)
    counts = {key:0 for key in sources}
    disagreements = {key:[0,0] for key in sources if key != 'a' and 'a' in sources}
    shape = evidence['descriptor']['shape']
    # A row fragment is at most 64K elements, including the three input arrays,
    # bool masks and temporary predicates. Native storage imposes its own chunk bounds.
    from .scientific_arrays import MAX_CHUNKS_PER_READ, MAX_SOURCE_BYTES_PER_READ
    width = 65536
    for source in sources.values():
        if source.chunks:
            chunk_bytes = math.prod(source.chunks)*np.dtype(source.dtype).itemsize
            admitted = min(MAX_CHUNKS_PER_READ, MAX_SOURCE_BYTES_PER_READ//chunk_bytes)
            width = min(width, max(1,(admitted-1)*source.chunks[-1]))
    # A linear index avoids product() materializing potentially huge prefix ranges.
    regions = range(math.prod(shape[:-1])) if sources else ()
    for linear in regions:
        prefix = []
        for length in reversed(shape[:-1]):
            linear, coordinate = divmod(linear, length)
            prefix.append(coordinate)
        prefix.reverse()
        for start in range(0,shape[-1],width):
            check()
            region = tuple(slice(i,i+1) for i in prefix)+(slice(start,min(start+width,shape[-1])),)
            masks = {}
            for key,source in sources.items():
                check()
                value = source.read(region)
                if (type(value) is not np.ndarray or value.dtype.str != source.dtype
                        or value.shape != tuple(s.stop-s.start for s in region)):
                    raise InvalidProject('A retained mask changed its native layout')
                if np.any(value < 0):
                    raise InvalidProject('Negative labels need an explicit conversion; originals remain unchanged')
                masks[key] = value != 0
                counts[key] += int(np.count_nonzero(masks[key]))
            for key,pair in disagreements.items():
                pair[0] += int(np.count_nonzero(masks['a'] & ~masks[key]))
                pair[1] += int(np.count_nonzero(masks[key] & ~masks['a']))
    check()
    arms = [{**arm,'corrections':[{'history':r} for r in arm['histories']]} for arm in evidence['arms']]
    return method_comparison.from_counts(evidence['source'], evidence['descriptor'].get('calibration'),
                                         arms,counts,disagreements,evidence['descriptor']['axes'])


def read(store, project, session, revision, *, check=lambda:None):
    check()
    evidence = method_evidence.read(store,project,session,revision)
    saved = store.read(project,session,revision=revision)
    summary = _summary(store,evidence,session,check)
    if saved.document['state'].get('methodSummary') != summary:
        raise InvalidProject('The retained summary does not match its exact masks; keep the original evidence and create a new comparison')
    return {'evidence':evidence,'summary':{**summary,'chosen':copy.deepcopy(evidence['chosen'])},'operation':descriptor()}


async def preview(hub, day, operation, selection, *, caller, stopping=None, check_caller=lambda:None):
    if (operation != OPERATION or caller not in {'studio','agent:chat','agent:voice','agent:system'}
            or type(selection) is not dict or set(selection) != {'session','source','arms','request'}):
        raise InvalidProject('Choose the declared comparison and exact source/arms')
    if len(_json(selection)) > method_evidence.MAX_METADATA:
        raise InvalidProject('Method evidence exceeds its metadata allowance')
    selected = copy.deepcopy(selection)
    session,generation = day.sid,getattr(day,'generation',None)
    ticket=current();stop=stopping if stopping is not None else threading.Event()
    def check():
        if stop.is_set():raise InvalidProject('Comparison preview stopped')
        if selected['session'] != session or day.sid != session or getattr(day,'generation',None) != generation:
            raise InvalidProject('The Project changed; return to the original comparison')
        if ticket is not None and ticket.superseded('session'):raise InvalidProject(ticket.superseded('session'))
        check_caller()
    def work():
        check()
        result=describe(hub.resources.projects,session,selected['source'],selected['arms'],actor=caller)
        check()
        return result
    return await _owned(work,on_cancel=stop.set)


async def run(hub, day, operation, action, request, *, caller, stopping=None, check_caller=lambda:None):
    if operation != OPERATION or caller not in {'studio','agent:chat','agent:voice','agent:system'}:
        raise InvalidProject('Choose the declared comparison through an authorized caller')
    required = {'session','source','arms','request'} if action == 'retain' else {'session','project','revision','arm','rationale'}
    if action not in {'retain','choose'} or type(request) is not dict or set(request) != required:
        raise InvalidProject('Choose exact retained method evidence and the declared action')
    if len(_json(request)) > method_evidence.MAX_METADATA:
        raise InvalidProject('Method evidence exceeds its metadata allowance')
    selected = copy.deepcopy(request)
    session,generation = day.sid,getattr(day,'generation',None)
    ticket = current(); stopping = stopping if stopping is not None else threading.Event()
    invocation = {'id':uuid.uuid4().hex,'caller':caller}
    if ticket is not None:
        invocation['ticket'] = ticket.id
        if ticket.admission is not None:invocation['request'] = ticket.admission.request_id
    def check():
        if stopping.is_set():raise InvalidProject('Comparison stopped; accepted work remains unchanged')
        if selected['session'] != session or day.sid != session or getattr(day,'generation',None) != generation:
            raise InvalidProject('The Project changed; return to the original Project before comparing')
        if ticket is not None and ticket.superseded('session'):raise InvalidProject(ticket.superseded('session'))
        check_caller()
    store = hub.resources.projects
    def work():
        check()
        if action == 'choose':
            original = store.read(selected['project'],session,revision=selected['revision'])
            read(store,original.id,session,original.revision,check=check)
            def choice_retry():
                check()
                latest = store.read(original.id,session)
                if latest.revision == original.revision:return None
                if latest.revision == original.revision+1 and latest.parent == original.digest:
                    choice = read(store,latest.id,session,latest.revision,check=check)['evidence']['chosen']
                    if (choice is not None and choice['arm'] == selected['arm']
                            and type(selected['rationale']) is str and choice['rationale'] == selected['rationale'].strip()):
                        check()
                        return {'project':latest,'operation':descriptor(),'invocation':invocation,'reused':True}
                raise Conflict('This comparison changed; reopen its accepted choice before choosing again')
            previous = choice_retry()
            if previous is not None:return previous
            check()
            try:
                saved = method_evidence.choose(store,original.id,session,original.revision,selected['arm'],
                    selected['rationale'],actor=caller,check=check,admission=invocation)
            except Conflict:
                previous = choice_retry()
                if previous is None:raise
                return previous
            return {'project':saved,'operation':descriptor(),'invocation':invocation,'reused':False}
        target,_ = request_identity(session,selected['request'])
        digest = hashlib.sha256(_json(selected)).hexdigest()
        def retry():
            check()
            if not (store._folder(target)/'head.json').exists():return None
            saved = store.read(target,session,revision=1)
            receipt = saved.document['state'].get('methodAdmission',{})
            if receipt.get('operation') != OPERATION or receipt.get('selectionSha256') != digest:
                raise Conflict('This request already belongs to another comparison; keep it and start a new request')
            read(store,target,session,1,check=check)
            check()
            return {'project':saved,'operation':descriptor(),'invocation':invocation,'reused':True}
        previous = retry()
        if previous is not None:return previous
        evidence = describe(store,session,selected['source'],selected['arms'],actor=caller)['evidence']
        summary = _summary(store,evidence,session,check)
        document = {'kind':method_evidence.KIND,'title':'Method comparison','resources':[],
                    'runtimes':evidence['runtimes'],'state':{'methodEvidence':evidence,'methodSummary':summary,
                    'methodAdmission':{'operation':OPERATION,'selectionSha256':digest,'invocation':invocation}}}
        check()
        try:saved = store.create(session,document,identity=target,check=check)
        except Conflict:
            previous = retry()
            if previous is None:raise
            return previous
        return {'project':saved,'operation':descriptor(),'invocation':invocation,'reused':False}
    check()
    with execution.request(hub,'method-comparison'), execution.span(OPERATION,provider='app',adapter=__name__):
        with captures(hub).memory.reserve('analysis',WORKING_BYTES,'comparing retained method masks'):
            return await _owned(work,on_cancel=stopping.set)
