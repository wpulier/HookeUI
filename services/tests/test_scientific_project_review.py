"""Real retained storage; private-host integration, not public browser qualification."""
from contextlib import contextmanager
import copy
from pathlib import Path

import numpy as np
import pytest

from env_worker import native_regions
from orchestrator import scientific_project_review as reviews
from orchestrator.native_regions import NativeRegionInputs
from orchestrator.scientific_projects import InvalidProject, ProjectStore
from tests.test_scientific_arrays import fill
from tests.test_scientific_host_worker import actual_host  # opt-in prepared upstream fixture


def saved_view(store, runtime=None, *, session="s1"):
    runtime = runtime or {'bundle': 'mine/host-review', 'generation': 'a' * 16, 'operation': 'viewer'}
    saved = store.create(session, {'kind': 'scene', 'title': 'Scientific input', 'state': {}, 'resources': [], 'runtimes': [runtime]})
    data = np.arange(6 * 24 * 28, dtype='<u2').reshape(6, 24, 28)
    writer = store.array_draft(saved.id, saved.session, component='array.volume', shape=data.shape, dtype=data.dtype.str,
        axes=('z', 'y', 'x'), chunks=(2, 12, 14), scientific={'semantic': 'intensity'}, max_written_bytes=data.nbytes)
    ref = fill(writer, data)
    document = saved.document
    document['resources'] = [ref]
    document['state']['scientificView'] = {'runtime': runtime, 'inputs': {'volume': ref}}
    return store.save(saved.id, saved.session, document, expected_revision=saved.revision), data, ref


def test_saved_inputs_keep_exact_native_values_and_revision_after_head_changes(tmp_path):
    store = ProjectStore(tmp_path)
    saved, values, ref = saved_view(store)
    newer = saved.document
    newer['state'] = {'anotherView': True}
    store.save(saved.id, saved.session, newer, expected_revision=saved.revision)
    resolved, runtime, sources = reviews._resolve(store, saved.id, saved.session, saved.revision)
    grants = NativeRegionInputs(sources)
    try:
        volume = native_regions.bind(grants.specifications(), grants.answer)['volume']
        assert volume.read([[1, 4], [3, 16], [4, 19]]).tobytes() == values[1:4, 3:16, 4:19].tobytes()
        assert volume.descriptor['semantic'] == 'intensity'
        assert resolved.digest == saved.digest and resolved.revision == saved.revision
        runtime['generation'] = 'changed'
        assert store.read(saved.id, saved.session, revision=saved.revision).document['runtimes'][0]['generation'] == 'a' * 16
    finally:
        grants.close()


def test_accepted_edit_reopens_with_updated_named_input_and_preserves_original(tmp_path):
    from orchestrator.scientific_edits import ViewEdits
    store = ProjectStore(tmp_path)
    saved, data, reference = saved_view(store)
    document = saved.document
    document['state']['scientificView']['inputs']['second-view'] = reference
    document['state']['history'] = {'originalInput': reference}
    saved = store.save(saved.id, saved.session, document, expected_revision=saved.revision)
    view = ViewEdits(store, saved.id, saved.session, saved.revision, reference)
    frame = view.present(view='view-one', layer='volume', context={})
    view.rendered(frame)  # synthetic presentation; real storage and input binding
    edit = view.begin(frame, max_written_bytes=2 * 12 * 14 * 2)
    patch = np.full((2, 12, 14), 65000, dtype='<u2')
    edit.write([0, 0, 0], patch)
    accepted = edit.commit()
    _, _, sources = reviews._resolve(ProjectStore(tmp_path), saved.id, saved.session, accepted.revision)
    assert np.array_equal(sources['volume'].read((slice(0, 2), slice(0, 12), slice(0, 14))), patch)
    _, _, original = reviews._resolve(store, saved.id, saved.session, saved.revision)
    assert np.array_equal(original['volume'].read((slice(0, 6), slice(0, 24), slice(0, 28))), data)
    assert accepted.document['state']['scientificView']['inputs']['volume'] == accepted.document['resources'][0]
    assert accepted.document['state']['scientificView']['inputs']['second-view'] == accepted.document['resources'][0]
    assert accepted.document['state']['history'] == {'originalInput': reference}
    assert accepted.document['runtimes'] == saved.document['runtimes']


@pytest.mark.parametrize('change', ['runtime', 'input', 'unknown-field', 'empty', 'name'])
def test_unrecorded_runtime_or_input_cannot_launch_a_worker(tmp_path, monkeypatch, change):
    store = ProjectStore(tmp_path)
    saved, _, ref = saved_view(store)
    doc = saved.document
    spec = copy.deepcopy(doc['state']['scientificView'])
    if change == 'runtime': spec['runtime']['generation'] = 'b' * 16
    if change == 'input': spec['inputs']['volume']['revision'] = 'b' * 64
    if change == 'unknown-field': spec['python'] = 'must not run'
    if change == 'empty': spec['inputs'] = {}
    if change == 'name': spec['inputs'] = {'../path': ref}
    doc['state']['scientificView'] = spec
    bad = store.save(saved.id, saved.session, doc, expected_revision=saved.revision)
    monkeypatch.setattr(reviews.scientific_host, 'review', lambda *args, **kwargs: pytest.fail('must refuse before launch'))
    with pytest.raises(InvalidProject):
        with reviews.review(store, bad.id, bad.session, bad.revision):
            pytest.fail('invalid view admitted')
    assert store.read(saved.id, saved.session, revision=saved.revision).digest == saved.digest


@pytest.mark.parametrize('revision', [None, True, 0, -1, '2'])
def test_review_never_substitutes_latest_for_an_unknown_revision(tmp_path, revision):
    store = ProjectStore(tmp_path)
    saved, _, _ = saved_view(store)
    with pytest.raises(InvalidProject, match='exact'):
        reviews._resolve(store, saved.id, saved.session, revision)
    with pytest.raises(InvalidProject, match='session'):
        reviews._resolve(store, saved.id, 'other', saved.revision)


def test_review_owns_context_cleanup_and_never_publishes_private_changes(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path)
    saved, _, _ = saved_view(store)
    events = []
    @contextmanager
    def worker(key, generation, sources, environ):
        events.append(('open', key, generation, tuple(sources)))
        try: yield object()
        finally: events.append(('closed',))
    monkeypatch.setattr(reviews.scientific_host, 'review', worker)
    with pytest.raises(RuntimeError, match='private error'):
        with reviews.review(store, saved.id, saved.session, saved.revision) as (origin, _):
            assert origin.digest == saved.digest
            raise RuntimeError('private error')
    assert events == [('open', 'mine/host-review#viewer', 'a' * 16, ('volume',)), ('closed',)]
    assert store.read(saved.id, saved.session).digest == saved.digest


def test_memory_refusal_precedes_saved_document_reads(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path)
    saved, _, _ = saved_view(store)
    monkeypatch.setattr(store, 'read', lambda *args, **kwargs: pytest.fail('must admit metadata memory before reading'))
    @contextmanager
    def refused(amount):
        assert amount == reviews.SAVE_WORKING_BYTES / (1 << 20)
        raise RuntimeError('controlled memory refusal')
        yield  # pragma: no cover
    monkeypatch.setattr(reviews.jobs.MEMORY, 'reserve', refused)
    with pytest.raises(RuntimeError, match='memory refusal'):
        with reviews.review(store, saved.id, saved.session, saved.revision):
            pytest.fail('refused review must not open')


def test_host_start_failure_releases_metadata_allowance_without_changing_saved_work(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path)
    saved, _, _ = saved_view(store)
    events = []
    @contextmanager
    def reserve(amount):
        events.append('reserved')
        try: yield
        finally: events.append('released')
    @contextmanager
    def refused(*args, **kwargs):
        events.append('start-refused')
        raise RuntimeError('controlled host start failure')
        yield  # pragma: no cover
    monkeypatch.setattr(reviews.jobs.MEMORY, 'reserve', reserve)
    monkeypatch.setattr(reviews.scientific_host, 'review', refused)
    with pytest.raises(RuntimeError, match='host start failure'):
        with reviews.review(store, saved.id, saved.session, saved.revision):
            pytest.fail('failed worker must not open')
    assert events == ['reserved', 'start-refused', 'released']
    assert store.read(saved.id, saved.session).digest == saved.digest


def test_actual_host_uses_saved_native_document_and_preserves_it_after_python(tmp_path, actual_host):
    prepared = actual_host
    store = ProjectStore(tmp_path)
    saved, values, ref = saved_view(store, {'bundle': 'mine/host-review',
        'generation': Path(prepared['folder']).name, 'operation': 'viewer'})
    with reviews.review(store, saved.id, saved.session, saved.revision) as (origin, worker):
        shown, pixels = worker.request({'op': 'render', 'width': 256, 'height': 192})
        assert origin.digest == saved.digest and len(set(pixels)) > 10
        reply, _ = worker.request({'op': 'python', 'frame': shown['frame']['frame'],
            'source': 'viewer.layers[0].opacity = .2\nint(viewer.layers[0].data.sum())'})
        assert reply['console']['result'] == str(int(values.sum())) and not reply['console']['error']
    assert worker.process.poll() is not None and not worker.work.exists()
    reopened = ProjectStore(tmp_path).read(saved.id, saved.session)
    assert reopened.digest == saved.digest and reopened.document['resources'] == [ref]


def test_file_resolution_checks_stop_between_sources_and_bounds_aggregate_metadata(monkeypatch):
    from types import SimpleNamespace
    calls=[]
    def file_source(project,session,revision,reference,*,check):
        check();calls.append(reference);return SimpleNamespace(chunks=('hash',)*10)
    store=SimpleNamespace(file_source=file_source)
    monkeypatch.setattr(reviews,'FILE_METADATA_BYTES',5700)
    with pytest.raises(InvalidProject,match='metadata allowance'):
        reviews._files(store,'project','session',1,{'first':1,'second':2},lambda:None)
    assert calls==[1,2]
    calls.clear()
    def stopped():
        if calls:raise RuntimeError('controlled Stop')
    with pytest.raises(RuntimeError,match='controlled Stop'):
        reviews._files(store,'project','session',1,{'first':1,'second':2},stopped)
    assert calls==[1]
