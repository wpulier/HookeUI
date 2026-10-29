"""Accept one saved population revision as native inputs for the shared scientific tools."""
import copy
import hashlib
import json
import threading
import uuid
from contextlib import contextmanager

from . import execution, particle_exchange
from .capture import captures
from .evidence import evidence_of
from .scientific_file_intake import FileIntake, request_identity
from .scientific_families import write_array
from .scientific_project_api import _owned
from .scientific_projects import InvalidProject, ProjectStore, Conflict
from .ticket import current

OPERATION = 'retain-population-labels-v1'


def descriptor():
    return {'id': OPERATION, 'title': 'Retain labels for tools', 'implementation': 'builtin',
            'input': 'saved-population-revision', 'output': 'native-project-arrays', 'preparation': 'none'}


def describe(hub, day, operation, selection):
    """Shared metadata readiness; run still validates all original bytes and exclusive masks."""
    if operation != OPERATION or type(selection) is not dict or set(selection) != {'id', 'session', 'revision'}:
        raise InvalidProject('Choose the declared operation and exact saved population revision')
    if (any(type(selection[k]) is not str or not selection[k] for k in ('id', 'session'))
            or type(selection['revision']) is not int or selection['revision'] < 0):
        raise InvalidProject('Choose an exact saved population revision')
    store = evidence_of(hub)
    result = store.get(selection['id'])
    if result is None or selection['session'] != day.sid or result.session != day.sid:
        raise InvalidProject('The saved population belongs to another Project or is unavailable')
    with store.exact_revision(result, selection['revision']):
        if result.status != 'saved' or result.path is None:
            raise InvalidProject('Wait for this population revision to be saved before retaining labels')
        image_metadata = result.view.get('sourceImage') or {}
        if (image_metadata.get('dtype'), image_metadata.get('axes'), image_metadata.get('channels'), image_metadata.get('units')) != ('uint8', 'YX', 1, 'pixels'):
            raise InvalidProject('Retain an original single-plane uint8 grayscale imported image in pixel units')
        if (result.scale_id or {}).get('umPerPx'):
            raise InvalidProject('This population has a physical scale; native retention currently supports pixel units only')
        instances = [m.instance for m in result.live()]
        if any(type(n) is not int or not 1 <= n <= 65535 for n in instances) or len(set(instances)) != len(instances):
            raise InvalidProject('Native labels require unique positive uint16 instance IDs; convert explicitly first')
    return {'operation': descriptor(), 'source': copy.deepcopy(selection),
            'readiness': {'ready': True, 'verification': 'metadata-only; pixels, masks and runtime receipt checked at Run',
                          'units': ['pixel', 'pixel'], 'outputs': ['image', 'labels'],
                          'limits': 'Single-plane grayscale, exclusive unscaled masks; no physical-scale conversion'}}


class PopulationIntake(FileIntake):
    def __init__(self, store, session, request, record, raw, gray, labels, mapping, caller, runtimes, acceptance, admission):
        self.record, self.mapping, self.caller = record, mapping, caller
        from .native_picture_inputs import retained_source
        self.native = retained_source(store, record['view']['sourceImage'], session, gray)
        self.admission = copy.deepcopy(admission)
        self.runtimes, self.boundary = runtimes, acceptance
        self.references = {}
        name = 'source.png' if record['view']['sourceImage']['mediaType'] == 'image/png' else 'source.jpg'
        super().__init__(store, session, request, name=name, size=len(raw), max_bytes=particle_exchange.MAX_BYTES)
        try:
            self.write(raw)
            saved = self.private.read(self.target, session)
            space = {'session': session, 'id': self.target, 'revision': 1}
            calibration = {'space': space, 'axes': ['y', 'x'], 'units': ['pixel', 'pixel'],
                           'affine': [[1, 0, 0], [0, 1, 0]]}
            if self.native is not None:
                calibration = copy.deepcopy(self.native[1].get('calibration'))
            for name, values, semantic in [('image', gray, 'intensity'), ('labels', labels, 'instance-labels')]:
                self.references[name] = write_array(self.private, saved, 'array.'+name, values, ['y', 'x'],
                                                     {'semantic': semantic, **({'calibration': calibration} if calibration is not None else {})})
        except BaseException:
            self.close()
            raise

    def acceptance(self):
        return self.boundary()

    def document(self, original):
        record = self.record
        source = {key: copy.deepcopy(record[key]) for key in ('id', 'session', 'revision', 'execution', 'files')}
        association = {'input': self.references['image'], 'output': self.references['labels'],
                       'source': source, 'producer': copy.deepcopy(record['producer'])}
        return {'kind': 'population-labels', 'title': f"Retained labels · {record['id']} · revision {record['revision']}",
                'resources': [original, *self.references.values()], 'runtimes': self.runtimes,
                'state': {'scientificView': {'inputs': self.references}, 'populationSource': {
                    'format': 2, 'operation': OPERATION, 'source': source, 'association': association,
                    'original': original, 'nativeInputs': self.references, 'members': self.mapping, 'producer': record['producer'],
                    'corrections': record['revisions'], 'caller': self.caller, 'admission': self.admission,
                    **({'nativeSource': copy.deepcopy(record['view']['sourceImage'])} if self.native is not None else {}),
                    'scaleBasis': record.get('scaleId'),
                    'runtimeKind': 'fiji' if self.runtimes else 'recorded-producer-only',
                    'meaning': 'Original pixels and retained instance values. Centroids from native measurement '
                               'are mean array indices; particle exchange centroids add 0.5 pixel.'}}}


@contextmanager
def _runtime(producer):
    """Lease the actual catalog runtime; retain its exact receipt, never prepare or select a replacement."""
    from gpu_worker import manifests as catalog
    from . import fiji_generations, generation_leases
    implementation = producer.get('implementation')
    manifest = catalog.get(implementation) if type(implementation) is str else None
    if manifest is None or manifest.runner != 'fiji':
        if producer.get('runtime') and (manifest is None or manifest.runner != 'builtin-cpu'):
            raise InvalidProject('This recorded runtime has no supported retention owner; restore its catalog declaration or keep the original population')
        yield []
        return
    runtime = producer.get('runtime', {})
    generation = runtime.get('generation')
    if type(generation) is not int or generation < 1:
        raise InvalidProject('The producing runtime has no exact installed generation')
    folder = fiji_generations.root()/str(generation)
    try:
        with generation_leases.use(folder):
            receipt = json.loads(ProjectStore._read(folder/'receipt.json', 8 << 20))
            digest = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
            if digest != runtime.get('receiptSha256'):
                raise InvalidProject('The producing runtime receipt changed; restore its exact generation')
            yield [{'bundle': 'fiji', 'generation': str(generation), 'operation': implementation}]
    except (generation_leases.Busy, generation_leases.Missing) as exc:
        raise InvalidProject(str(exc)) from exc



async def run(hub, day, operation, selection, *, caller, stopping=None, check_caller=lambda: None):
    if operation != OPERATION or caller not in {'studio', 'agent:chat', 'agent:voice', 'agent:system'}:
        raise InvalidProject('Choose the declared retain-labels operation through an authorized caller')
    if (type(selection) is not dict or set(selection) != {'session', 'id', 'revision', 'request'}
            or any(type(selection[k]) is not str or not selection[k] for k in ('session', 'id', 'request'))
            or type(selection['revision']) is not int or selection['revision'] < 0):
        raise InvalidProject('Choose an exact saved population and its revision')
    selected = copy.deepcopy(selection)
    session, generation = day.sid, day.generation
    store = evidence_of(hub)
    result = store.get(selected['id'])
    ticket = current()
    stopping = stopping if stopping is not None else threading.Event()

    invocation = {'id': uuid.uuid4().hex, 'caller': caller}
    if ticket is not None:
        invocation['ticket'] = ticket.id
        if ticket.admission is not None:
            invocation['request'] = ticket.admission.request_id

    def permission():
        if stopping.is_set():
            raise InvalidProject('Retention stopped; the original population is unchanged')
        if day.sid != session or day.generation != generation or selected['session'] != session:
            raise InvalidProject('The Project changed; return to the original Project')
        if ticket is not None and ticket.superseded('session'):
            raise InvalidProject(ticket.superseded('session'))
        check_caller()

    def check():
        permission()
        if (result is None or store.get(selected['id']) is not result or result.session != session
                or result.revision != selected['revision'] or result.status != 'saved' or result.path is None):
            raise InvalidProject('The saved population or Project changed; select its accepted revision again')
        return False

    permission()
    projects = hub.resources.projects.for_session(session, write=True)
    target, transaction = request_identity(session, selected['request'])

    def retry():
        permission()
        if not projects._folder(target).exists():
            return None
        accepted = projects.read(target, session, revision=1)
        provenance = accepted.document['state'].get('populationSource', {})
        population_reference = provenance.get('source', {})
        if (accepted.transaction != transaction or provenance.get('operation') != OPERATION
                or any(population_reference.get(k) != selected[k] for k in ('id', 'session', 'revision'))):
            raise InvalidProject('This request belongs to another accepted revision; use a new request')
        permission()
        return {'project': accepted, 'operation': descriptor(), 'invocation': invocation, 'reused': True}

    recovered = await _owned(retry, on_cancel=stopping.set)
    if recovered is not None:
        return recovered
    check()
    describe(hub, day, operation, {k: selected[k] for k in ('id', 'session', 'revision')})
    with store.exact_revision(result, selected['revision']):
        check()
        expected = copy.deepcopy({key: value for key, value in store.manifest(result, {}).items()
                                  if key in ('id', 'session', 'revision', 'execution', 'producer', 'view', 'members', 'revisions', 'scaleId')})
    if (expected.get('scaleId') or {}).get('umPerPx'):
        raise InvalidProject('This population has a physical scale; native retention currently supports pixel units only')
    w, h = expected['view'].get('width'), expected['view'].get('height')
    if type(w) is not int or type(h) is not int or min(w, h) < 1 or w*h > particle_exchange.MAX_PIXELS:
        raise InvalidProject('Choose a saved original image within the native population grid limit')

    def work():
        owner = None
        try:
            with captures(hub).memory.reserve('analysis', w*h*96 + particle_exchange.MAX_BYTES*4,
                                             'retaining original population labels'):
                check()
                record, raw, gray, labels, mapping = particle_exchange.snapshot(result.path, expected, preserve_instances=True)
                with _runtime(record['producer']) as runtimes:
                    owner = PopulationIntake(hub.resources.projects, session, selected['request'],
                                             record, raw, gray, labels, mapping, caller, runtimes,
                                             lambda: store.exact_revision(result, selected['revision']), invocation)
                    check()
                    try:
                        saved = owner.publish(check)
                    except Conflict:
                        recovered = retry()
                        if recovered is None:
                            raise
                        return recovered
                return {'project': saved, 'operation': descriptor(), 'invocation': invocation, 'reused': False}
        finally:
            if owner is not None:
                owner.close()

    with execution.request(hub, 'population-native'), execution.span(OPERATION, provider='app', adapter=__name__):
        return await _owned(work, on_cancel=stopping.set)
