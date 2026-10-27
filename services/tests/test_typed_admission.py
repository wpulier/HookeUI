"""H01 slice d: typed multi-input admission, one validator, inputs bound at Run, actionable refusals.

A contributed bundle member that takes instance labels and a 16-bit intensity image, proven by its own typed reference
check and run in its real worker process (stand-in installer, the app's Python); inputs are retained native arrays of
real scientific documents.
"""
import asyncio
import io
import itertools
import json

import numpy as np
import pytest

from gpu_worker import manifests as catalog
from orchestrator import admission, bundle_runtime, env_runner, envs, kinds, operations
from orchestrator.scientific_projects import ProjectStore
from tests.test_bundle_manifest import HEAD
from tests.test_env_runner import home, standin_uv  # noqa: F401

LABELS = np.array([[0, 1, 1, 2], [0, 1, 2, 2], [3, 3, 0, 0]], dtype='<u2')
INTENSITY = np.array([[5, 10, 20, 100], [7, 30, 300, 500], [1000, 3000, 9, 9]], dtype='<u2')
MEMBERS = '''
[[members]]
id = "label-means"
name = "Mean intensity per label"
adapter = "adapter:label_means"
inputs = [{ id = "labels", type = "labels.instance.2d" }, { id = "intensity", type = "image.2d.gray16" }]
outputs = [{ id = "table", type = "table.objects" }]
[[members.reference_cases]]
inputs = { labels = "example-labels.npy", intensity = "example-intensity.npy" }
scientific = { inputs = { labels = { axes = ["y", "x"], semantic = "instance-labels" }, intensity = { axes = ["y", "x"], semantic = "intensity" } } }
reference = "reference-label-means.json"
source = "Synthetic labels and 16-bit values; exact per-label means computed independently"
device = "cpu"
tolerance = {atol = 0, rtol = 0, numeric_columns = ["mean"], identity_columns = ["label"]}
'''
ADAPTER = '''
import numpy as np

def label_means(inputs, params):
    labels, intensity = inputs["labels"], inputs["intensity"]
    assert intensity.dtype.str == "<u2" and labels.dtype.str == "<u2"  # exactly as stored: never expanded to RGB
    rows = [[int(n), float(intensity[labels == n].mean())] for n in np.unique(labels) if n]
    return {"columns": [{"name": "label"}, {"name": "mean"}], "rows": rows}
'''


def expected_rows(labels=LABELS, intensity=INTENSITY):
    return [[int(n), float(intensity[labels == n].astype(np.float64).mean())] for n in np.unique(labels) if n]


def contribute(home):
    folder = home / 'repo/models/measure-tools'
    folder.mkdir(exist_ok=True)
    (folder / 'model.toml').write_text(HEAD.replace('image-tools', 'measure-tools').replace('Image tools', 'Measure tools')
                                       + MEMBERS)
    (folder / 'env.lock').write_text('numpy==2.0.0 --hash=sha256:' + 'a' * 64 + '\n')
    (folder / 'adapter.py').write_text(ADAPTER)
    for name, array in (('example-labels.npy', LABELS), ('example-intensity.npy', INTENSITY)):
        body = io.BytesIO()
        np.save(body, array, allow_pickle=False)
        (folder / name).write_bytes(body.getvalue())
    (folder / 'reference-label-means.json').write_text(json.dumps(
        {'columns': [{'name': 'label'}, {'name': 'mean'}], 'rows': expected_rows()}))
    catalog.manifests.cache_clear()
    owner = catalog.get('measure-tools')
    assert owner is not None, catalog.problems()
    return owner


@pytest.fixture
def installed(home, monkeypatch):
    before = dict(operations.OPERATIONS), set(kinds.registered())
    owner = contribute(home)
    standin_uv(home, monkeypatch)
    envs.install(owner)
    env_runner.register()
    yield owner
    operations.OPERATIONS.clear()
    operations.OPERATIONS.update(before[0])
    kinds.registered().clear()
    kinds.registered().update(before[1])


def member(owner):
    return next(m for m in bundle_runtime.expand(env_runner.in_use(owner)) if m.m.key == 'measure-tools#label-means')


def documents(tmp_path, labels=LABELS, intensity=INTENSITY, semantics=('instance-labels', 'intensity')):
    """Two accepted scientific documents holding the inputs as native arrays (an earlier output's kind of resource)."""
    store = ProjectStore(tmp_path / 'projects')
    references = {}
    for name, array, semantic in (('labels', labels, semantics[0]), ('intensity', intensity, semantics[1])):
        first = store.create('s1', {'kind': 'scene', 'title': name, 'state': {}, 'resources': [], 'runtimes': []})
        draft = store.array_draft(first.id, 's1', component=f'array.{name}', shape=list(array.shape), dtype=array.dtype.str,
                                  axes=['y', 'x', 'c'][:array.ndim], chunks=list(array.shape), scientific={'semantic': semantic},
                                  max_written_bytes=array.nbytes)
        for index in itertools.product(*(range(n) for n in draft.grid)):
            draft.write(index, np.ascontiguousarray(array))
        ref = draft.finish()
        saved = store.save(first.id, 's1', {**first.document, 'resources': [ref]}, expected_revision=1)
        references[name] = {'kind': 'array', 'session': 's1', 'id': saved.id, 'revision': saved.revision,
                            'component': f'array.{name}'}
    return store, references


def test_a_typed_member_is_proven_on_its_own_typed_reference_and_offered(installed):
    assert env_runner.in_use(installed) is not None, env_runner.proof(installed)
    proof = env_runner.in_use(installed).proof
    assert proof['ok'] and proof['cases'][0]['inputs'].keys() == {'labels', 'intensity'}
    assert kinds.takes_why(member(installed).m) == ''


def test_two_native_inputs_bound_at_run_reach_the_adapter_unconverted(installed, tmp_path):
    store, references = documents(tmp_path)
    capture = admission.bind(member(installed).m, references, store=store, session='s1')
    assert [row.array.dtype.str for row in capture.typed] == ['<u2', '<u2']
    worker = env_runner.Client(member(installed))
    try:
        out = worker.run_typed(capture.typed, {}, output='table.objects')
    finally:
        worker.stop()
    assert out['rows'] == expected_rows()
    records = [row.record() for row in capture.typed]
    assert [r['reference']['id'] for r in records] == [references['labels']['id'], references['intensity']['id']]


@pytest.mark.parametrize('change,port,words', [
    ('gray8-given-16-bit', 'intensity', 'stores 16-bit nonnegative whole numbers'),
    ('labels-meaning', 'labels', 'holds intensities'),
    ('missing', 'intensity', 'it takes a 16-bit grey image'),
    ('other-session', 'labels', "another session's data"),
    ('unknown-port', 'extra', 'no input of that name'),
    ('stale-revision', 'labels', 'not available'),
    ('rgb-for-labels', 'labels', '3-D'),
])
def test_every_incompatible_input_is_refused_by_port_before_anything_runs(installed, tmp_path, change, port, words):
    labels, intensity, semantics = LABELS, INTENSITY, ('instance-labels', 'intensity')
    if change == 'labels-meaning':
        semantics = ('intensity', 'intensity')
    if change == 'rgb-for-labels':
        labels = np.zeros((3, 4, 3), '|u1')
    store, references = documents(tmp_path, labels, intensity, semantics)
    m = member(installed).m
    if change == 'gray8-given-16-bit':
        m = type('M', (), {'inputs': [p if p.type != 'image.2d.gray16' else type(p)(**{**p.__dict__, 'type': 'image.2d.gray8'})
                                       for p in m.inputs], 'outputs': m.outputs, 'scientific': {}})()
    if change == 'missing':
        references.pop('intensity')
    if change == 'unknown-port':
        references['extra'] = references['labels']
    if change == 'stale-revision':
        references['labels'] = {**references['labels'], 'revision': references['labels']['revision'] + 5}
    if change == 'other-session':  # the review reproduced a run in one session reading another's arrays
        references['labels'] = {**references['labels'], 'session': '2001-01-01'}
    with pytest.raises(admission.Refused) as refused:
        admission.bind(m, references, store=store, session='s1')
    rows = {row['port']: row for row in refused.value.rows}
    assert port in rows and words in rows[port]['message'], refused.value.rows


def test_the_view_cannot_stand_in_for_typed_inputs(installed):
    """A typed member is never run on the live view's picture by any actor: there is no picture to convert."""
    m = member(installed).m
    assert admission.typed(m) and kinds.implements(m) is not None
    with pytest.raises(admission.Refused, match='choose one resource'):
        admission.bind(m, None)


@pytest.fixture
def rig(installed, tmp_path):
    from tests.capability_rig import Rig
    made = Rig(tmp_path / 'rig')
    made.app.catalog_changed()
    return made


def app_documents(rig, **changes):
    """The inputs as retained native arrays in the app's own scientific store, in the open session."""
    from orchestrator.resources import resources_of
    store = resources_of(rig.hub).projects
    session = rig.app.day.sid
    references = {}
    for name, array, semantic in (('labels', changes.get('labels', LABELS), 'instance-labels'),
                                  ('intensity', changes.get('intensity', INTENSITY), 'intensity')):
        first = store.create(session, {'kind': 'scene', 'title': name, 'state': {}, 'resources': [], 'runtimes': []})
        draft = store.array_draft(first.id, session, component=f'array.{name}', shape=list(array.shape),
                                  dtype=array.dtype.str, axes=['y', 'x'], chunks=list(array.shape),
                                  scientific={'semantic': semantic}, max_written_bytes=array.nbytes)
        draft.write((0, 0), np.ascontiguousarray(array))
        ref = draft.finish()
        saved = store.save(first.id, session, {**first.document, 'resources': [ref]}, expected_revision=1)
        references[name] = {'kind': 'array', 'session': session, 'id': saved.id, 'revision': saved.revision,
                            'component': f'array.{name}'}
    return references


def kept(rig):
    rows = [row for row in rig.hub.resources.results.entries() if row.get('tool') == 'run_capability']
    return rows, (rig.hub.resources.results.values(rows[-1]['id']) if rows else None)


def test_the_agent_route_binds_each_input_at_run_and_keeps_what_it_used(rig):
    references = app_documents(rig)
    runs = asyncio.run(rig.turn([('run_capability', {'capability': 'measure-tools#label-means', 'inputs': references})],
                                'Measure the mean intensity of each label'))
    run = next(run for run in runs if run.tool == 'run_capability')
    assert run.ok, (run.summary, run.error)
    rows, values = kept(rig)
    assert values['rows'] == expected_rows()
    used = values['inputResources']
    assert [(u['port'], u['reference']['id'], u['dtype']) for u in used] == [
        ('labels', references['labels']['id'], '<u2'), ('intensity', references['intensity']['id'], '<u2')]
    assert values['producer']['inputs'] == used  # what made it names exactly these inputs
    # Said as what it ran on: the bound inputs, never "the view" (ADR 0006).
    assert 'labels (array labels of' in run.summary and 'intensity (array intensity of' in run.summary, run.summary
    assert 'the view' not in run.summary, run.summary


def test_the_agent_route_refuses_the_view_and_incompatible_inputs_in_words(rig):
    runs = asyncio.run(rig.turn([('run_capability', {'capability': 'measure-tools#label-means'})], 'Measure labels'))
    run = next(run for run in runs if run.tool == 'run_capability')
    assert not run.ok and 'choose one resource' in run.summary
    references = app_documents(rig, intensity=INTENSITY.astype('|u1'))
    runs = asyncio.run(rig.turn([('run_capability', {'capability': 'measure-tools#label-means', 'inputs': references})],
                                'Measure labels'))
    run = next(run for run in runs if run.tool == 'run_capability')
    assert not run.ok and 'intensity: it stores 8-bit nonnegative whole numbers' in run.summary
    assert '<u2' not in run.summary and '|u1' not in run.summary and 'image.2d' not in run.summary
    assert kept(rig)[0] == []  # nothing ran, nothing kept


def test_the_researcher_route_binds_the_same_inputs_through_the_same_admission(rig):
    from orchestrator.research.hands import Hands
    from tests.test_research_hands import session
    references = app_documents(rig)
    refused = asyncio.run(Hands(rig.hub).run(session(sitting=rig.app.day.sid), 'capability', 'label means',
                                             capability='measure-tools#label-means'))
    assert not refused['ok'] and 'never runs on the live view' in refused['error']
    reply = asyncio.run(Hands(rig.hub).run(session(sitting=rig.app.day.sid), 'capability', 'label means',
                                           capability='measure-tools#label-means', inputs=references))
    assert reply['ok'], reply
    _rows, values = kept(rig)
    assert values['rows'] == expected_rows() and len(values['inputResources']) == 2


def test_the_studio_action_route_runs_the_same_admission(rig):
    from tests.test_bundle_actors import command
    references = app_documents(rig)
    reply = command(rig, {'type': 'action', 'capability': 'catalog:measure-tools#label-means', 'args': {},
                          'inputs': references})
    assert reply.get('ok') is not False, reply
    _rows, values = kept(rig)
    assert values['rows'] == expected_rows() and values['inputResources'][0]['port'] == 'labels'
    refused = command(rig, {'type': 'action', 'capability': 'catalog:measure-tools#label-means', 'args': {},
                            'inputs': {'labels': references['labels']}})
    assert refused.get('ok') is False and 'intensity' in str(refused), refused


def authorized(tmp_path, name, array):
    import hashlib
    body = io.BytesIO()
    np.save(body, array, allow_pickle=False)
    path = tmp_path / f'{name}.npy'
    path.write_bytes(body.getvalue())
    return {'id': f'input-{name}', 'path': str(path), 'sha256': hashlib.sha256(body.getvalue()).hexdigest(),
            'descriptor': {'axes': ['y', 'x']}}


def test_the_headless_route_takes_one_authorized_file_per_input_and_the_same_admission(installed, tmp_path):
    from orchestrator.capability_runs import Refused as HeadlessRefused, ResourceHost, kept_result, run_on_resource
    sources = {'labels': authorized(tmp_path, 'labels', LABELS), 'intensity': authorized(tmp_path, 'intensity', INTENSITY)}
    host = ResourceHost(tmp_path / 'host')
    got = asyncio.run(run_on_resource(host, 'measure-tools#label-means', sources))
    _entry, values, _picture = kept_result(host, got['result'], got['session'])
    assert values['rows'] == expected_rows()
    assert [(r['port'], r['reference']['input']) for r in values['inputResources']] == [
        ('labels', 'input-labels'), ('intensity', 'input-intensity')]
    tampered = dict(sources, intensity={**sources['intensity'], 'sha256': '0' * 64})
    with pytest.raises(HeadlessRefused, match='intensity: the input changed after it was authorized'):
        asyncio.run(run_on_resource(host, 'measure-tools#label-means', tampered))
    narrowed = dict(sources, intensity=authorized(tmp_path, 'narrow', INTENSITY.astype('|u1')))
    with pytest.raises(HeadlessRefused, match=r'intensity: it stores 8-bit nonnegative whole numbers'):
        asyncio.run(run_on_resource(host, 'measure-tools#label-means', narrowed))


def test_each_input_lists_the_retained_arrays_it_takes_and_says_why_not_the_others(rig, installed):
    from orchestrator.resources import resources_of
    references = app_documents(rig)
    listed = admission.choices(resources_of(rig.hub), rig.app.day.sid, member(installed).m)
    by_port = {row['port']: {c['reference']['id']: c for c in row['choices']} for row in listed}
    labels, intensity = references['labels']['id'], references['intensity']['id']
    assert by_port['labels'][labels]['fits'] and not by_port['labels'][intensity]['fits']
    assert 'holds intensities' in by_port['labels'][intensity]['says']
    assert by_port['intensity'][intensity]['fits'] and not by_port['intensity'][labels]['fits']
    assert 'holds instance labels' in by_port['intensity'][labels]['says']


def test_another_sessions_arrays_are_refused_on_every_route(rig):
    """Review of #412: one session ran another session's arrays. Each reference must be in the actor's own session."""
    references = app_documents(rig)
    foreign = {name: {**ref, 'session': '2001-01-01'} for name, ref in references.items()}
    runs = asyncio.run(rig.turn([('run_capability', {'capability': 'measure-tools#label-means', 'inputs': foreign})],
                                'Measure labels'))
    run = next(run for run in runs if run.tool == 'run_capability')
    assert not run.ok and "another session's data" in run.summary, run.summary
    assert kept(rig)[0] == []


def test_every_input_is_checked_from_its_metadata_before_any_value_is_read(installed, tmp_path, monkeypatch):
    """Review of #412: inputs were read whole before the layout and total size were checked."""
    from types import SimpleNamespace
    store, references = documents(tmp_path)
    reads = []
    original = ProjectStore.array_source

    def counted(self, *args, **kwargs):  # every value read goes through the source's read
        source = original(self, *args, **kwargs)
        return SimpleNamespace(shape=source.shape, dtype=source.dtype, descriptor=source.descriptor,
                               read=lambda *a, **k: reads.append(1) or source.read(*a, **k))
    monkeypatch.setattr(ProjectStore, 'array_source', counted)
    monkeypatch.setattr(admission, 'MAX_BYTES', 8)
    with pytest.raises(admission.Refused, match='MiB together'):
        admission.bind(member(installed).m, references, store=store, session='s1')
    narrowed = documents(tmp_path / 'narrow', intensity=INTENSITY.astype('|u1'))
    monkeypatch.setattr(admission, 'MAX_BYTES', 256 << 20)
    with pytest.raises(admission.Refused, match='stores 8-bit'):
        admission.bind(member(installed).m, narrowed[1], store=narrowed[0], session='s1')
    assert reads == []  # neither refusal read a value


def test_a_typed_capability_with_a_points_output_is_refused_before_it_runs():
    """Review of #412: a points output from typed inputs used the run's input id as a view frame. Points and pictures
    are kept on the view they were found on; a typed run has no view (ticket 47: typed outputs are open)."""
    port = type('P', (), {'id': 'labels', 'type': 'labels.instance.2d', 'widget': False})()
    for output in ('points.2d', 'image.2d.rgb8'):
        m = type('M', (), {'inputs': [port], 'outputs': [type('O', (), {'type': output})()], 'scientific': {}})()
        assert 'table, text or data' in admission.why_not(m)
        with pytest.raises(admission.Refused, match='table, text or data'):
            admission.bind(m, {}, session='s1')


def test_one_definition_of_typed():
    from gpu_worker import bundle_contract
    import inspect
    assert 'bundle_contract' in inspect.getsource(admission.typed)
    assert bundle_contract.TYPED_INPUTS == tuple(admission.TYPES)


@pytest.mark.parametrize('changed_stamp', [True, False])
def test_npy_changed_during_payload_read_is_refused_before_numpy_allocation(tmp_path, monkeypatch, changed_stamp):
    """Synthetic file-read race: a tiny preflight must not admit a replacement giant header.

    The unchanged-stamp case injects inconsistent bytes at the I/O boundary to
    require validation of the actual buffer as well as filesystem observations.
    NumPy allocation is intercepted; this never allocates the declared volume.
    """
    from types import SimpleNamespace
    from unittest.mock import Mock
    from orchestrator import typed_files

    m = SimpleNamespace(inputs=[SimpleNamespace(id='image', type='image.2d.gray16', widget=None)],
                        outputs=[SimpleNamespace(type='table.objects')], scientific={})
    path = tmp_path / 'example.npy'
    np.save(path, np.zeros((2, 3), dtype='<u2'))
    original = path.read_bytes()
    header = io.BytesIO()
    np.lib.format.write_array_header_1_0(header, {'descr': '<u2', 'fortran_order': False, 'shape': (1, 2**34)})
    replacement = header.getvalue() + original[len(header.getvalue()):]
    assert len(replacement) == len(original)
    fdopen = typed_files.os.fdopen

    class ChangedRead:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def read(self, size=-1):
            if size == len(original) + 1:
                if changed_stamp:
                    path.write_bytes(replacement)
                return replacement
            return self.stream.read(size)

    monkeypatch.setattr(typed_files.os, 'fdopen', lambda *a, **kw: ChangedRead(fdopen(*a, **kw)))
    load = Mock(side_effect=AssertionError('changed bytes reached NumPy allocation'))
    monkeypatch.setattr(np, 'load', load)
    with pytest.raises(admission.Refused):
        typed_files.load(m, {'image': {'path': path, 'descriptor': {'axes': ['y', 'x']},
                                      'reference': {'kind': 'example'}}})
    load.assert_not_called()
