"""R02 on the actual confined napari worker: Python and plugin code create each layer type after startup,
the parent accepts it as a new output, and a fresh review reopens it with exactly the values it was saved with.

NAPARI_NATIVE_HOST_PREPARATION names a prepared private review bundle (its key, folder and environment). One
review session per test session creates every output; each is published by OutputSave and reopened in its own
review. Without the preparation every case skips and the acceptance runner reports these children missing.
"""
import fcntl
import json
import os
from pathlib import Path

import numpy as np
import pytest

from tests.shared_run import shared_result

from gpu_worker import manifests as catalog

# What a digest covers: every data array with its dtype and shape, the data-to-world mapping and shape types.
DIGEST = '''
import hashlib as _hashlib, json as _json, numpy as _np
def _digest(layer):
    h = _hashlib.sha256()
    data = layer.data if isinstance(layer.data, (list, tuple)) else [layer.data]
    for part in data:
        part = _np.asarray(part)
        h.update(repr((part.dtype.str, part.shape)).encode()); h.update(_np.ascontiguousarray(part).tobytes())
    # The world mapping, through napari's own transform: restore may record scale and translate as one affine.
    n = layer.ndim
    world = [[float(v) for v in layer.data_to_world(point)] for point in [[0.] * n, *_np.eye(n).tolist()]]
    h.update(repr((world, list(getattr(layer, 'shape_type', [])))).encode())
    return h.hexdigest()
'''
PYTHON = '''
import numpy as np
viewer.add_image((np.arange(42, dtype='<f4').reshape(6, 7) / 41), name='python-image', scale=(2, 3), translate=(4, 5))
viewer.add_labels((np.arange(42).reshape(6, 7) % 5).astype('<u2'), name='python-labels')
viewer.add_points(np.array([[1., 2.], [3.5, 4.], [5., 6.25]]), name='python-points')
viewer.add_shapes([np.array([[0., 0.], [0., 3.], [3., 3.], [3., 0.]]), np.array([[1., 1.], [2., 4.], [4., 2.]])],
                  shape_type=['rectangle', 'polygon'], name='python-shapes')
viewer.add_surface((np.array([[0., 0.], [0., 1.], [1., 1.], [1., 0.]]), np.array([[0, 1, 2], [0, 2, 3]]),
                    np.array([.1, .2, .3, .4])), name='python-surface')
viewer.add_vectors(np.array([[[1., 1.], [2., 0.]], [[3., 3.], [0., 1.5]]]), name='python-vectors')
viewer.add_tracks(np.array([[1, 0, 1., 1.], [1, 1, 2., 2.], [2, 0, 3., 3.], [2, 1, 4., 5.]]), name='python-tracks')
'''
# napari's builtin npe2 plugin: its sample contribution, then its writer and its CSV and OBJ readers.
PLUGIN = '''
import numpy as np
before = len(viewer.layers)
viewer.open_sample('napari', 'balls_2d')
for layer in list(viewer.layers)[before:]:
    layer.name = 'plugin-' + layer._type_string
drawn = viewer.add_shapes([np.array([[0., 0.], [0., 2.], [2., 2.], [2., 0.]])], shape_type='rectangle', name='drawn')
from napari.plugins.io import save_layers
save_layers('drawn-shapes.csv', [drawn], plugin='napari')
viewer.layers.remove(drawn)
viewer.open('drawn-shapes.csv', plugin='napari')[0].name = 'plugin-shapes'
open('mesh.obj', 'w').write('v 0 0 0\\nv 0 1 0\\nv 1 1 0\\nv 1 0 0\\nf 1 2 3\\nf 1 3 4\\n')
viewer.open('mesh.obj', plugin='napari')[0].name = 'plugin-surface'
'''
MEANINGS = {
    'image': {'semantic': 'intensity', 'axes': ['y', 'x']},
    'labels': {'semantic': 'instance-labels', 'axes': ['y', 'x']},
    'points': {'semantic': 'point-set', 'axes': ['y', 'x']},
    'shapes': {'semantic': 'shape-set', 'axes': ['y', 'x']},
    'surface': {'semantic': 'surface-mesh', 'axes': ['y', 'x']},
    'vectors': {'semantic': 'vector-set', 'axes': ['y', 'x']},
    'tracks': {'semantic': 'track-coordinates', 'axes': ['t', 'y', 'x']},
}
CASES = [f'python-{kind}' for kind in MEANINGS] + [f'plugin-{kind}' for kind in ('image', 'labels', 'points', 'shapes')]
SURFACE_PLUGIN = 'plugin-surface'  # napari's OBJ reader makes 3D vertices
CASES.append(SURFACE_PLUGIN)


def _meaning(name, kind, metadata):
    if name == SURFACE_PLUGIN:
        return {'semantic': 'surface-mesh', 'axes': ['z', 'y', 'x'][-metadata['components']['vertices']['shape'][1]:]}
    if kind == 'image' and len(metadata['shape']) != 2:
        return {**MEANINGS[kind], 'axes': ['z', 'y', 'x'][-len(metadata['shape']):]}
    return MEANINGS[kind]


def _console(worker, source):
    result, _ = worker.request({'op': 'python-resources', 'source': source})
    console = result['console']
    if console['error']:
        raise AssertionError(console['error'][-4000:])
    return console


def _digests(worker, names=None):
    _console(worker, DIGEST)
    selected = 'viewer.layers' if names is None else f'[l for l in viewer.layers if l.name in {sorted(names)!r}]'
    out = _console(worker, f'print(_json.dumps({{l.name: [l._type_string, _digest(l)] for l in {selected}}}))')
    return json.loads(out['stdout'].strip().splitlines()[-1])


def _run(prepared, root):
    from orchestrator.scientific_outputs import OutputSave
    from orchestrator.scientific_project_review import review
    from orchestrator.scientific_projects import ProjectStore
    from tests.test_scientific_arrays import fill
    store = ProjectStore(root / 'retained')
    bundle, operation = prepared['key'].split('#')
    runtime = {'bundle': bundle, 'generation': Path(prepared['folder']).name, 'operation': operation}
    saved = store.create('s1', {'kind': 'scene', 'title': 'Source', 'state': {}, 'resources': [], 'runtimes': [runtime]})
    data = np.arange(3 * 8 * 9, dtype='<u2').reshape(3, 8, 9)
    draft = store.array_draft(saved.id, 's1', component='array.volume', shape=data.shape, dtype=data.dtype.str,
                              axes=['z', 'y', 'x'], chunks=[1, 8, 9], scientific={'semantic': 'intensity'},
                              max_written_bytes=data.nbytes)
    reference = fill(draft, data)
    document = saved.document
    document['resources'] = [reference]
    document['state']['scientificView'] = {'runtime': runtime, 'inputs': {'volume': reference}}
    saved = store.save(saved.id, 's1', document, expected_revision=1)
    results = {}
    with review(store, saved.id, 's1', saved.revision) as (_, worker):
        _console(worker, PYTHON)
        _console(worker, PLUGIN)
        made = _digests(worker, CASES)
        rows = {row['name']: row for row in worker.request({'op': 'layer-inventory'})[0]['inventory']['layers']}
        for index, name in enumerate(CASES):
            kind, digest = made[name]
            try:
                metadata = worker.describe_output(rows[name]['id'])
                save = OutputSave(store, saved, f'r02-upstream-{index:04d}-0000', rows[name]['id'],
                                  _meaning(name, kind, metadata), producer={'kind': name.split('-')[0]})
                save.capture(worker)
                save.publish()
                save.close()
                results[name] = {'kind': kind, 'digest': digest, 'output': save.target}
            except Exception as error:  # one output's failure is that child's evidence, not the others'
                results[name] = {'kind': kind, 'digest': digest, 'failure': f'{type(error).__name__}: {error}'}
    for name, result in results.items():
        if 'output' not in result:
            continue
        output = store.read(result['output'], 's1')
        provenance = output.document['state']['hookeOutput']
        result['base'] = provenance['base'] == {'id': saved.id, 'session': 's1', 'revision': saved.revision,
                                                'digest': saved.digest}
        result['producer'] = provenance['producer']
        try:
            with review(store, output.id, 's1', 1) as (_, reopened):
                result['reopened'] = _digests(reopened)
        except Exception as error:
            result['failure'] = f'reopen {type(error).__name__}: {error}'
    unchanged = store.read(saved.id, 's1') == saved
    resource_only = _resource_only(store, saved, data)
    return {'cases': results, 'source_unchanged': unchanged, 'resource_only': resource_only,
            'families': _families(store, store.read(saved.id, 's1'))}


FAMILIES = '''
import numpy as np
volume = inputs['volume'].read([[0, n] for n in inputs['volume'].shape])
families.table('objects', {'object_id': np.arange(1, 4, dtype='<u8'),
                           'plane_mean': volume.reshape(volume.shape[0], -1).mean(axis=1)},
               key='object_id', units={'plane_mean': 'intensity'}, object_space='volume')
scored = viewer.add_points(np.array([[1., 1.], [2., 2.]]), features={'index': [7, 9], 'score': [.5, .25]}, name='scored')
families.features('scores', scored)
families.embedding('space', np.arange(6, dtype='<f4').reshape(3, 2), table='objects', key='object_id', region='volume')
families.artifact('movie', '{"keyframes": []}', semantic='animation-document', media_type='application/json',
                  file_name='movie.json')
'''


def _families(store, head):
    """H01 a3: Python and a layer's features drafted in the console, saved together through the actual worker."""
    from orchestrator import scientific_artifacts, scientific_embeddings, scientific_tables
    from orchestrator.scientific_family_saves import FamilySave
    from orchestrator.scientific_project_review import review
    from orchestrator.scientific_saves import SaveNotStarted
    out = {}
    with review(store, head.id, 's1', head.revision) as (_, worker):
        _console(worker, FAMILIES)
        out['listed'] = [d['name'] for d in worker.family_drafts()]
        save = FamilySave(store, head, 'families-upstream-0001', ['objects', 'scores', 'space', 'movie'])
        save.capture(worker)
        status = save.publish()
        revision = status['resource']['version']
        objects = scientific_tables.open_table(store, head.id, 's1', revision, 'objects')
        volume = store.array_source(head.id, 's1', head.revision, head.document['state']['scientificView']['inputs']['volume'])
        values = volume.read(tuple(slice(0, n) for n in volume.shape))
        out['objects'] = (objects.read('plane_mean').tobytes() == values.reshape(values.shape[0], -1).mean(axis=1).tobytes()
                          and objects.schema['object_space'] == head.document['state']['scientificView']['inputs']['volume'])
        scores = scientific_tables.open_table(store, head.id, 's1', revision, 'scores')
        out['scores'] = (scores.read('index').tolist() == [7, 9] and scores.read('score').tolist() == [.5, .25]
                         and scores.schema['producer']['by'] == 'layer-features')
        space = scientific_embeddings.open_embedding(store, head.id, 's1', revision, 'space')
        out['space'] = space.read().tobytes() == np.arange(6, dtype='<f4').tobytes()
        movie = scientific_artifacts.open_artifact(store, head.id, 's1', revision, 'movie')
        out['movie'] = b''.join(movie.chunks()) == b'{"keyframes": []}'
        out['one_revision'] = revision == head.revision + 1
        # The same session, after its layer changes without a save: a draft naming that layer's input is refused.
        _console(worker, "import numpy as np\nchanged = np.array(viewer.layers[0].data)\nchanged[0, 0, 0] += 1\n"
                         "viewer.layers[0].data = changed\n"
                         "families.table('stale', {'object_id': np.arange(1, 3, dtype='<u8')}, key='object_id', "
                         "object_space=viewer.layers[0])")
        latest = store.read(head.id, 's1')
        try:
            FamilySave(store, latest, 'families-upstream-0002', ['stale']).capture(worker)
            out['refused'] = None
        except SaveNotStarted as error:
            out['refused'] = str(error)
        out['unchanged_after_refusal'] = store.read(head.id, 's1') == latest
    return out


def _resource_only(store, saved, data):
    """R04: code computes on the undisplayed input and its result is published, with no render and no frame receipt."""
    from orchestrator.scientific_edits import ResourceEdits
    from orchestrator.scientific_project_review import review
    reference = saved.document['resources'][0]
    owner = ResourceEdits(store, saved.id, 's1', saved.revision, reference, layer='volume')
    requests = []
    try:
        with review(store, saved.id, 's1', saved.revision) as (_, worker):
            request = worker.request
            worker.request = lambda r: requests.append(r['op']) or request(r)
            rows = worker.request({'op': 'layer-inventory'})[0]['inventory']['layers']
            layer = next(row for row in rows if row.get('editableInput') == 'volume')
            # Computed from the bound input itself, not from anything rendered.
            _console(worker, "import numpy as np\nsource = inputs['volume']\n"
                             "total = int(source.read([[0, n] for n in source.shape]).astype(np.int64).sum())\n"
                             "changed = np.array(viewer.layers[0].data)\nchanged[0, 0, 0] = total % 65536\n"
                             "viewer.layers[0].data = changed")
            edit = owner.begin(max_written_bytes=data.nbytes)
            worker.stage_resource(layer['id'], 'volume', edit)
            accepted = edit.commit()
    finally:
        owner.close()
    values = store.array_source(saved.id, 's1', accepted.revision, accepted.document['resources'][0])
    values = values.read(tuple(slice(0, n) for n in data.shape))
    expected = data.copy()
    expected[0, 0, 0] = int(data.astype(np.int64).sum()) % 65536
    before = store.array_source(saved.id, 's1', saved.revision, reference).read(tuple(slice(0, n) for n in data.shape))
    return {'exact': values.tobytes() == expected.tobytes(), 'earlier_unchanged': before.tobytes() == data.tobytes(),
            'frame': accepted.document['state']['hookeEdit']['frame'], 'requests': requests}


@pytest.fixture(scope='session')
def outputs(tmp_path_factory):
    location = os.environ.get('NAPARI_NATIVE_HOST_PREPARATION')
    if not location:
        pytest.skip('requires a prepared private review bundle (NAPARI_NATIVE_HOST_PREPARATION)')
    prepared = json.loads(Path(location).read_text())
    shared = shared_result(tmp_path_factory, 'r02-upstream')
    with open(shared.with_suffix('.lock'), 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not shared.exists():
            saved = {key: os.environ.get(key) for key in prepared['environment']}
            os.environ.update(prepared['environment'])
            os.environ.pop('MICROMIND_SANDBOX_READ', None)
            catalog.manifests.cache_clear()
            try:
                shared.write_text(json.dumps(_run(prepared, tmp_path_factory.mktemp('r02'))))
            finally:
                for key, value in saved.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
                catalog.manifests.cache_clear()
    return json.loads(shared.read_text())


@pytest.mark.parametrize('name', CASES)
def test_created_output_is_accepted_and_reopens_with_exact_values(name, outputs):
    result = outputs['cases'][name]
    assert 'failure' not in result, result['failure']
    assert result['kind'] == name.split('-', 1)[1]
    assert result['base'] and result['producer'] == {'kind': name.split('-')[0]}
    assert result['reopened'] == {name: [result['kind'], result['digest']]}
    assert outputs['source_unchanged']


def test_resource_only_code_publishes_from_undisplayed_input(outputs):
    result = outputs['resource_only']
    assert result['exact'] and result['earlier_unchanged']
    assert 'render' not in result['requests'] and 'change' not in result['requests']


def test_resource_only_publication_invents_no_frame_receipt(outputs):
    result = outputs['resource_only']
    assert result['frame'] is None and 'render' not in result['requests']


def test_python_and_layer_feature_drafts_publish_together_on_the_actual_worker(outputs):
    result = outputs['families']
    assert result['listed'] == ['objects', 'scores', 'space', 'movie']
    assert result['objects'] and result['scores'] and result['space'] and result['movie'] and result['one_revision']


def test_a_draft_naming_a_layer_with_unsaved_changes_is_refused_on_the_actual_worker(outputs):
    result = outputs['families']
    assert result['refused'] is not None and 'unsaved changes' in result['refused']
    assert result['unchanged_after_refusal']
