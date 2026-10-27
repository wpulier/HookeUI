"""Explicit coordinates, real immutable storage and synthetic worker transport; no model/UI claim."""
import copy
import hashlib
from types import SimpleNamespace
import threading

import numpy as np
import pytest

from env_worker.scientific_output_wire import calibration
from orchestrator.scientific_outputs import OutputSave
from orchestrator.scientific_saves import SaveNotStarted
from tests.test_scientific_edits import scene, values
from tests.test_scientific_outputs import attempt, Worker
from tests.scientific_threads import exclude_preexisting_test_harness_threads  # noqa: F401


def proof(data):
    # One tile in these small fixtures; dtype and shape are part of the value identity.
    digest = hashlib.sha256(repr((data.dtype.str, data.shape)).encode())
    digest.update(data.tobytes(order='C'))
    return digest.hexdigest()


def admitted(scene):
    store, saved, ordinary, worker = attempt(scene)
    image = np.arange(35, dtype='uint8').reshape(5, 7)
    coordinates = calibration(worker.info, ordinary.meaning['axes'],
        {'session':saved.session, 'id':saved.id, 'revision':saved.revision})
    draft = store.array_draft(saved.id, saved.session, component='array.image', shape=image.shape,
        dtype=image.dtype.str, axes=ordinary.meaning['axes'], chunks=image.shape,
        scientific={'semantic':'intensity', 'calibration':coordinates}, max_written_bytes=image.nbytes)
    draft.write([0, 0], image); reference = draft.finish()
    body = saved.document; body['resources'].append(reference)
    body['state']['scientificView']['inputs'] = {'image':reference}
    saved = store.save(saved.id, saved.session, body, expected_revision=saved.revision)
    worker.info['coordinatesFrom'] = {'format':'same-input-grid-v1', 'input':'image',
        'reference':reference, 'sha256':proof(image)}
    save = OutputSave(store, saved, ordinary.identity, ordinary.layer, ordinary.meaning)
    return store, saved, save, worker, image, coordinates


def test_explicit_input_grid_keeps_semantics_separate_and_survives_export(scene, tmp_path):
    from orchestrator.scientific_exchange import export, reimport
    from orchestrator.scientific_projects import ProjectStore
    from orchestrator.scientific_project_review import _resolve
    store, saved, save, worker, image, coordinates = admitted(scene)
    before = saved.document
    save.capture(worker); save.publish()
    accepted = store.read(save.target, saved.session)
    _, _, sources = _resolve(store, accepted.id, accepted.session, 1)
    assert sources['output'].descriptor['calibration'] == coordinates
    assert sources['output'].descriptor['semantic'] == 'instance-labels'
    assert sources['output'].dtype == '>u2'
    assert 'coordinatesFrom' not in accepted.document['state']['scientificView']['layers'][0]['metadata']
    relation = accepted.document['state']['hookeOutput']['coordinatesFrom']
    assert relation == worker.info['coordinatesFrom']
    assert accepted.document['state']['hookeOutput']['base']['digest'] == saved.digest
    assert accepted.document['state']['hookeOutput']['runtimes'] == before['runtimes']
    bundle = tmp_path/'output.zip'
    export(store, accepted.id, accepted.session, 1, destination=bundle)
    other = ProjectStore(tmp_path/'other'); reimport(other, bundle)
    _, _, reopened = _resolve(other, accepted.id, accepted.session, 1)
    assert reopened['output'].descriptor == sources['output'].descriptor
    assert other.read(saved.id, saved.session, revision=saved.revision) == saved
    assert values(other, accepted, accepted.document['resources'][0]).tobytes() == worker.data.tobytes()
    assert store.read(saved.id, saved.session).document == before
    save.close()


@pytest.mark.parametrize('change', ['dirty', 'stale', 'scope', 'shape', 'axes', 'affine', 'units'])
def test_bad_coordinate_relationship_refuses_before_transport_and_publication(scene, change):
    store, saved, save, worker, _, _ = admitted(scene)
    if change == 'dirty': worker.info['coordinatesFrom']['sha256'] = '0'*64
    if change == 'stale': worker.info['coordinatesFrom']['reference']['revision'] = '0'*64
    if change == 'scope': worker.info['coordinatesFrom']['input'] = 'unknown'
    if change == 'shape': worker.info['shape'][0] += 1
    if change == 'axes': save.meaning['axes'].reverse(); save.selections[0]['meaning'] = save.meaning
    if change == 'affine': worker.info['geometry']['basis'][1][0] += .5
    if change == 'units': worker.info['geometry']['units'][0] = 'millimeter'
    before = {row.id for row in store.list(saved.session)}
    with pytest.raises(SaveNotStarted, match='coordinate|input|grid'):
        save.capture(worker)
    assert worker.calls == 0 and {row.id for row in store.list(saved.session)} == before
    assert store.read(saved.id, saved.session) == saved
    assert not list(store.root.glob('.scientific-output-*'))


def session():
    from env_worker.scientific_host import ReviewSession
    from env_worker.scientific_view_bindings import ViewBindings
    from tests.test_scientific_layer_exports import setup
    editable, viewer, image, source = setup('intensity')
    image._type_string = 'image'; image.name = 'Image'; image.visible = True; image.opacity = 1.
    source.reference = {'session':'s1', 'id':'p1', 'component':'array.image', 'revision':'a'*64}
    accepted = image.data.copy()
    source.read = lambda bounds, *, view=False: accepted[tuple(slice(*pair) for pair in bounds)].copy()
    editable.bind(layer=image, input='labels'); editable.seal()
    output = SimpleNamespace(**vars(image)); output._type_string = 'labels'; output.data = image.data.copy()
    output.data_to_world = lambda point: tuple(float(v) + output.offset for v in point)
    viewer.layers.append(output)
    host = object.__new__(ReviewSession)
    host._thread = threading.get_ident(); host._closed = False
    host.editable = editable; host.viewer = viewer; host._frame = None
    host.view_bindings = ViewBindings(viewer); host.view_bindings.bind(layer=image, key='image')
    host.view_bindings.seal(); host._layers = [('i'*32, image), ('a'*32, output)]
    host._layer_rows = lambda: None
    return host, image, output


def bind(host, image, output):
    host.bind_view_output(version=2, layer=output, key='result',
        meaning={'semantic':'instance-labels', 'axes':['y','x']}, coordinates_from=image)


def test_version_two_observes_actual_bound_input_and_retains_version_one():
    host, image, output = session(); bind(host, image, output)
    info = host.describe_output('a'*32)
    assert info['coordinatesFrom'] == {'format':'same-input-grid-v1', 'input':'labels',
        'reference':host.editable.inputs['labels'].reference, 'sha256':proof(image.data)}
    assert host.editable.input_for(output) is None
    host, image, output = session()
    host.bind_view_output(version=1, layer=output, key='legacy')
    assert 'coordinatesFrom' not in host.describe_output('a'*32)


@pytest.mark.parametrize('change', ['dirty', 'geometry', 'units', 'removed', 'output-shape', 'output-axes'])
def test_source_or_output_change_after_declaration_revokes_capture(change):
    host, image, output = session(); bind(host, image, output)
    if change == 'dirty': image.data[0, 0] = 999
    if change == 'geometry': image.offset = 1
    if change == 'units': image.units = ('um', 'um')
    if change == 'removed': host.viewer.layers = [output]
    if change == 'output-shape': output.data = output.data[:, :2]
    if change == 'output-axes': output.axis_labels = ('x', 'y')
    with pytest.raises(ValueError): host.describe_output('a'*32)


def test_unbound_source_cannot_be_claimed_by_equal_shape():
    host, image, output = session()
    with pytest.raises(ValueError, match='bound input'):
        bind(host, SimpleNamespace(**vars(image)), output)
    assert host.view_bindings.key_for(output) is None


@pytest.mark.parametrize('storage', ['files', 'memory'])
@pytest.mark.parametrize('mutate_during_freeze', [False, True])
def test_coordinate_source_is_checked_after_freeze_before_any_transport(monkeypatch, storage, mutate_during_freeze):
    from contextlib import contextmanager
    from env_worker import scientific_outputs
    from env_worker.native_edits import OUTPUT_FORMAT
    host, image, output = session(); bind(host, image, output)
    expected = host.describe_output('a'*32)
    spec = {'format':OUTPUT_FORMAT, 'token':'parent-grant', 'shape':[4,6],
            'dtype':'>u2', 'axes':['y','x'], 'chunks':[2,3]}
    original = scientific_outputs.capture
    @contextmanager
    def capture(*args):
        with original(*args) as chunks:
            if mutate_during_freeze: image.data[0, 0] = 999
            yield chunks
    monkeypatch.setattr(scientific_outputs, 'capture', capture)
    received = []
    def exchange(header, body):
        received.append(body)
        # The accepted output is already frozen; later input edits do not rewrite it.
        image.data[:] = 0; output.data[:] = 0
        return {'ok':True, 'kind':OUTPUT_FORMAT, 'index':header['index'], 'bytes':len(body)}, b''
    if mutate_during_freeze:
        with pytest.raises(ValueError): host.stage_output('a'*32, expected,
            {'semantic':'instance-labels','axes':['y','x']}, spec, exchange, snapshot=storage)
        assert received == []
    else:
        original_data = output.data.copy()
        host.stage_output('a'*32, expected, {'semantic':'instance-labels','axes':['y','x']},
                          spec, exchange, snapshot=storage)
        assert b''.join(received) == b''.join(original_data[y:y+2,x:x+3].tobytes()
                                            for y in (0,2) for x in (0,3))


def test_tool_group_retains_explicit_source_per_output_and_removes_live_bindings(scene):
    from tests.test_scientific_tool_save import ToolWorker
    from orchestrator.scientific_project_review import _resolve
    store, saved, ordinary, output, image, coordinates = admitted(scene)
    input_worker = Worker(); input_worker.data = image
    input_worker.info = {**copy.deepcopy(output.info), 'type':'image', 'dtype':'|u1', 'name':'Source'}
    input_worker.info.pop('coordinatesFrom')
    parts = {'b'*32:input_worker, 'a'*32:output}
    selections = [{'layer':'b'*32,'meaning':{'semantic':'intensity','axes':['z','x']}},
                  {'layer':'a'*32,'meaning':ordinary.meaning}]
    class Tool:
        def request(self, request):
            return {'inventory':{'layers':[{'id':'b'*32,'editableInput':'image'}, {'id':'a'*32}]}}, b''
        def describe_output(self, key, *, tool): return parts[key].describe_output(key)
        def stage_outputs(self, rows, *, tool):
            assert tool
            for row in rows:
                parts[row['layer']].stage_output(row['layer'],row['metadata'],row['meaning'],row['grants']['output'])
            return {'document':ToolWorker().state, 'bindings':{'source':rows[0]['names']['output'],
                                                             'result':rows[1]['names']['output']}}
    save = OutputSave(store, saved, ordinary.identity, 'b'*32, selections[0]['meaning'],
                      selections=selections, tool=True)
    save.capture(Tool()); save.publish()
    accepted = store.read(save.target, saved.session)
    _, _, sources = _resolve(store, accepted.id, accepted.session, 1)
    assert all(source.descriptor['calibration'] == coordinates for source in sources.values())
    assert sources['layer1_output'].descriptor['semantic'] == 'intensity'
    assert sources['layer2_output'].descriptor['semantic'] == 'instance-labels'
    records = accepted.document['state']['hookeOutput']['outputs']
    assert 'coordinatesFrom' not in records[0]
    assert records[1]['coordinatesFrom'] == output.info['coordinatesFrom']
    assert all('coordinatesFrom' not in row['metadata'] for row in accepted.document['state']['scientificView']['layers'])
    assert store.read(saved.id, saved.session) == saved
    save.close()


def test_fingerprint_reads_bounded_tiles_and_keeps_dtype_and_shape_exact():
    from env_worker.scientific_output_coordinates import fingerprint
    data = np.arange(900_000, dtype='>u2').reshape(900,1000)
    reads = []
    def read(region):
        result = data[region]; reads.append(result.nbytes)
        return result
    digest = fingerprint(data.shape, data.dtype.str, read)
    assert len(reads) > 1 and max(reads) <= 1 << 20 and sum(reads) == data.nbytes
    assert digest != fingerprint(data.shape, '<u2', lambda region:data[region].astype('<u2'))
    assert digest != fingerprint((1000,900), data.dtype.str, lambda region:data.reshape(1000,900)[region])
    with pytest.raises(ValueError, match='64 MiB'):
        fingerprint((8192,8192), '>u2', lambda _:pytest.fail('Read before admission'))


def test_retained_metadata_cannot_recreate_live_source_binding(scene):
    from env_worker.scientific_output_wire import scene as validate_scene
    _, _, _, worker, _, _ = admitted(scene)
    with pytest.raises(ValueError, match='live coordinate-source'):
        validate_scene([{'input':'output','metadata':worker.info}], {'output':object()})


def test_coordinate_attribution_is_explained_after_reopen(scene):
    from orchestrator.scientific_provenance import resolve
    store, saved, save, worker, _, _ = admitted(scene)
    save.capture(worker); save.publish()
    result = resolve(store, save.target, saved.session, 1)
    assert result['outputs']['made'][0]['coordinatesFrom'] == worker.info['coordinatesFrom']
    assert result['outputs']['base']['digest'] == saved.digest
    assert result['outputs']['runtimes'] == saved.document['runtimes']
    save.close()


@pytest.mark.parametrize('version, source, meaning', [(1, True, True), (2, False, True), (2, True, False)])
def test_versioned_coordinate_declarations_cannot_silently_drop_required_fields(version, source, meaning):
    host, image, output = session()
    with pytest.raises(ValueError):
        host.bind_view_output(version=version, layer=output, key='result',
            meaning={'semantic':'instance-labels','axes':['y','x']} if meaning else None,
            coordinates_from=image if source else None)
    assert host.view_bindings.key_for(output) is None


def test_group_source_mutation_during_later_freeze_sends_no_outputs(monkeypatch):
    from contextlib import contextmanager
    from env_worker import scientific_outputs
    from env_worker.native_edits import OUTPUT_FORMAT
    host, image, first = session(); bind(host, image, first)
    second = SimpleNamespace(**vars(first)); second.data = first.data.copy()
    second.data_to_world = lambda point: tuple(float(v) + second.offset for v in point)
    host.viewer.layers.append(second); host._layers.append(('b'*32, second))
    meaning = {'semantic':'instance-labels', 'axes':['y','x']}
    host.bind_view_output(version=2, layer=second, key='second', meaning=meaning, coordinates_from=image)
    rows = [{'layer':key, 'metadata':host.describe_output(key), 'meaning':meaning,
             'components':[{'name':'output', 'edit':{'format':OUTPUT_FORMAT, 'token':key,
                 'shape':[4,6], 'dtype':'>u2', 'axes':['y','x'], 'chunks':[2,3]}}]}
            for key in ('a'*32, 'b'*32)]
    original = scientific_outputs.capture
    @contextmanager
    def capture(viewer, layer, *args):
        with original(viewer, layer, *args) as chunks:
            if layer is second: image.data[0,0] = 999
            yield chunks
    monkeypatch.setattr(scientific_outputs, 'capture', capture)
    with pytest.raises(ValueError):
        host.stage_outputs(rows, lambda *_:pytest.fail('Transferred before every coordinate source was checked'))


def test_context_advertises_output_binding_version_before_package_execution():
    from env_worker.scientific_host import Context
    context = Context(viewer=None, inputs={}, bind_editable=None, bind_view=None)
    assert context.output_binding_version == 2
