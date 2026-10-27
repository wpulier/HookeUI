"""Embeddings, artifacts, the family transaction and inventory on real retained storage.

Synthetic values; no UI, native-host or package qualification.
"""
import hashlib
import importlib
import json
import pickle

import numpy as np
import pytest

from orchestrator import scientific_artifacts as artifacts
from orchestrator import scientific_embeddings as embeddings
from orchestrator import scientific_families as families
from orchestrator import scientific_tables as tables
from orchestrator.scientific_edits import ViewEdits
from orchestrator.scientific_projects import Conflict, InvalidProject
from tests.test_scientific_edits import display
from tests.test_scientific_tables import objects, scene  # noqa: F401

KEYS = np.array([11, 12, 13], dtype='<u8')
MATRIX = (np.arange(12).reshape(3, 4) / 7).astype('>f4')
MODEL = pickle.dumps({'weights': [1, 2, 3]})  # stored as bytes; never unpickled by Hooke
ANIMATION = json.dumps({'keyframes': [{'frame': 0, 'camera': {'zoom': 1.0}}, {'frame': 30, 'camera': {'zoom': 2.0}}]}).encode()


def everything(refs, **embedding):
    return dict(
        tables=objects(KEYS, np.ones(3, '<f8'), object_space=refs['labels']),
        embeddings={'shape_features': (embeddings.declare(table='objects', key='object_id', region=refs['image'],
            feature_model={'runtime': {'bundle': 'hooke/features', 'generation': 'g1', 'operation': 'embed'},
                           'recipe': refs['image']}, **embedding), MATRIX)},
        artifacts={'model': (artifacts.declare(semantic='trained-artifact', media_type='application/octet-stream',
                                               file_name='cells.pkl', compatibility={'package': 'cellpose', 'version': '4.0.6'}), MODEL),
                   'movie': (artifacts.declare(semantic='animation-document', media_type='application/json',
                                               file_name='movie.json'), ANIMATION)})


def test_every_family_is_published_in_one_cas_and_reopens_exactly(scene):
    store, saved, refs, *_ = scene
    published = families.publish(store, saved, **everything(refs))
    assert published.revision == saved.revision + 1
    embedding = embeddings.open_embedding(store, saved.id, 's1', published.revision, 'shape_features')
    assert embedding.read().dtype.str == '>f4' and embedding.read().tobytes() == MATRIX.tobytes()
    assert embedding.read(1, 2).tobytes() == MATRIX[1:2].tobytes()
    assert embedding.schema['region'] == refs['image'] and embedding.schema['key'] == 'object_id'
    model = artifacts.open_artifact(store, saved.id, 's1', published.revision, 'model')
    assert b''.join(model.chunks()) == MODEL and model.schema['sha256'] == hashlib.sha256(MODEL).hexdigest()
    assert b''.join(artifacts.open_artifact(store, saved.id, 's1', published.revision, 'movie').chunks()) == ANIMATION
    listed = families.inventory(store, saved.id, 's1', published.revision)['resources']
    assert {(row['family'], row['name']) for row in listed} == {
        ('table', 'objects'), ('embeddings', 'shape_features'), ('artifact', 'model'), ('artifact', 'movie')}
    assert all(row['readable'] for row in listed)


@pytest.mark.parametrize('change, why', [
    ('nan', 'no missing-value form'), ('rows', 'one row per object'), ('one-dimensional', 'two-dimensional'),
    ('integer', 'floating-point'), ('no-table', 'object table in this revision'), ('wrong-key', 'object table'),
    ('bad-model', 'feature_model'), ('bad-json', 'not valid JSON'), ('artifact-name', 'lowercase'),
    ('semantic', 'trained artifact or an animation')])
def test_one_wrong_part_publishes_nothing_of_any_family(scene, change, why):
    store, saved, refs, *_ = scene
    parts = everything(refs)
    declaration, matrix = parts['embeddings']['shape_features']
    if change == 'nan': matrix = matrix.copy(); matrix[1, 1] = np.nan
    elif change == 'rows': matrix = matrix[:2]
    elif change == 'one-dimensional': matrix = matrix[0]
    elif change == 'integer': matrix = matrix.astype('<i4')
    elif change == 'no-table': declaration['table'] = 'absent'
    elif change == 'wrong-key': declaration['key'] = 'area'
    elif change == 'bad-model': declaration['feature_model'] = {'runtime': 'cellpose'}
    elif change == 'bad-json': parts['artifacts']['movie'] = (parts['artifacts']['movie'][0], b'{"keyframes": [')
    elif change == 'artifact-name': parts['artifacts'] = {'Model': parts['artifacts']['model']}
    elif change == 'semantic': parts['artifacts']['model'][0]['semantic'] = 'notebook'
    parts['embeddings']['shape_features'] = (declaration, matrix)
    with pytest.raises(InvalidProject, match=why):
        families.publish(store, saved, **parts)
    assert store.read(saved.id, 's1') == saved


def test_replacing_the_table_an_embedding_follows_needs_the_embedding_too(scene):
    store, saved, refs, *_ = scene
    published = families.publish(store, saved, **everything(refs))
    with pytest.raises(InvalidProject, match='publish it again with that table'):
        tables.publish(store, published, objects(KEYS[:2], np.ones(2, '<f8')), replace=('objects',))
    both = families.publish(store, published, tables=objects(KEYS[:2], np.ones(2, '<f8')), replace=('objects', 'shape_features'),
                            embeddings={'shape_features': (embeddings.declare(table='objects', key='object_id'), MATRIX[:2].copy())})
    assert embeddings.open_embedding(store, saved.id, 's1', both.revision, 'shape_features').schema['rows'] == 2
    assert embeddings.open_embedding(store, saved.id, 's1', published.revision, 'shape_features').schema['rows'] == 3


def test_publishing_another_embedding_cannot_carry_an_old_one_over_replaced_keys(scene):
    # Same row count, different objects: only the embedding republished with the table may follow it.
    store, saved, refs, *_ = scene
    published = families.publish(store, saved, **everything(refs))
    other = {'other': (embeddings.declare(table='objects', key='object_id'), MATRIX.copy())}
    with pytest.raises(InvalidProject, match='shape_features follows table objects'):
        families.publish(store, published, tables=objects(KEYS + 100, np.ones(3, '<f8')), embeddings=other,
                         replace=('objects',))
    assert store.read(saved.id, 's1') == published


def test_an_embedding_whose_keys_changed_under_it_does_not_open(scene, monkeypatch):
    # Whoever replaced the table without the check, the stored key digest refuses the stale attribution.
    store, saved, refs, *_ = scene
    published = families.publish(store, saved, **everything(refs))
    monkeypatch.setattr(embeddings, 'check_tables_kept', lambda *a, **k: None)
    forged = tables.publish(store, published, objects(KEYS + 100, np.ones(3, '<f8')), replace=('objects',))
    with pytest.raises(InvalidProject, match='no longer follows the keys of table objects'):
        embeddings.open_embedding(store, saved.id, 's1', forged.revision, 'shape_features')
    listed = {row['name']: row for row in families.inventory(store, saved.id, 's1', forged.revision)['resources']}
    assert listed['shape_features']['readable'] is False
    assert embeddings.open_embedding(store, saved.id, 's1', published.revision, 'shape_features').schema['key_sha256'] == \
        embeddings.key_digest(KEYS)


def test_a_changed_artifact_is_listed_as_unreadable_never_dropped_or_executed(scene):
    store, saved, refs, *_ = scene
    published = families.publish(store, saved, **everything(refs))
    reference = next(r for r in published.document['resources'] if r['component'] == 'file.artifact.model')
    manifest = json.loads((store.root / saved.id / 'files' / f'{reference["revision"]}.json').read_text())
    chunk = store.root / saved.id / 'files' / f'{manifest["chunks"][0]}.bin'
    chunk.chmod(0o600); chunk.write_bytes(b'x' * chunk.stat().st_size)
    with pytest.raises(InvalidProject, match='changed'):
        b''.join(artifacts.open_artifact(store, saved.id, 's1', published.revision, 'model').chunks())
    listed = {row['name']: row for row in families.inventory(store, saved.id, 's1', published.revision)['resources']}
    assert listed['model']['readable'] is False and 'changed' in listed['model']['reason']
    assert listed['movie']['readable'] is True


def test_a_lost_acknowledgement_retries_exactly_and_a_stale_revision_conflicts(scene):
    store, saved, refs, *_ = scene
    first = families.publish(store, saved, **everything(refs), transaction='c' * 64)
    assert families.publish(store, saved, **everything(refs), transaction='c' * 64) == first
    with pytest.raises(Conflict):
        families.publish(store, saved, artifacts=everything(refs)['artifacts'], transaction='d' * 64)


@pytest.mark.parametrize('edited', ['image', 'labels'])
def test_copies_keep_every_family_and_their_references(scene, edited):
    store, saved, refs, *_ = scene
    published = families.publish(store, saved, **everything(refs))
    owner = ViewEdits(store, saved.id, 's1', published.revision, refs[edited])
    edit = owner.begin(display(owner), max_written_bytes=48)
    edit.write([0, 0], np.full((2, 3), 3, dtype='>u8' if edited == 'labels' else '>u2'))
    store.save(saved.id, 's1', {**published.document, 'title': 'Concurrent winner'}, expected_revision=published.revision)
    with pytest.raises(Conflict):
        edit.commit()
    copied = store.copy_candidate(published, edit.prepared()).publish()
    embedding = embeddings.open_embedding(store, copied.id, 's1', copied.revision, 'shape_features')
    assert embedding.read().tobytes() == MATRIX.tobytes()
    if edited == 'labels':  # the region's bytes were copied unchanged with it
        assert embedding.schema['region']['id'] == copied.id
    else:  # an edit replaced the region in the copy: it keeps naming the bytes it was computed from
        assert embedding.schema['region'] == refs['image']
    assert b''.join(artifacts.open_artifact(store, copied.id, 's1', copied.revision, 'model').chunks()) == MODEL
    assert all(row['readable'] for row in families.inventory(store, copied.id, 's1', copied.revision)['resources'])
    owner.close()


def test_the_registry_names_every_family_of_the_work_package_and_its_opener():
    named = {row['family'] for row in families.FAMILIES}
    assert {'image', 'labels', 'points', 'shapes', 'surface', 'vectors', 'tracks', 'table', 'embeddings',
            'artifact', 'source-file'} == named
    semantics = {s for row in families.FAMILIES for s in row['semantics']}
    assert {'intensity', 'probabilities', 'class-labels', 'instance-labels', 'object-table', 'lineage-graph',
            'embeddings', 'trained-artifact', 'animation-document'} <= semantics
    for row in families.FAMILIES:
        importlib.import_module('orchestrator.' + row['opens'])
    from env_worker.scientific_output_wire import SEMANTICS
    assert set(SEMANTICS) <= semantics  # every native layer semantic has its family


def test_resources_and_artifact_download_through_the_api(web):
    from tests.test_scientific_project_api import create
    client, built = web
    made = create(client, built).json()['project']
    store = built.hub.resources.projects
    saved = store.read(made['id'], made['session'], revision=1)
    model = (artifacts.declare(semantic='trained-artifact', media_type='application/octet-stream', file_name='cells.pkl'), MODEL)
    published = families.publish(store, saved, artifacts={'model': model})
    query = f'?session={saved.session}&revision={published.revision}'
    listed = client.get(f'/api/scientific-projects/{saved.id}/resources{query}').json()
    assert listed['ok'] and listed['resources'][0]['family'] == 'artifact'
    download = client.get(f'/api/scientific-projects/{saved.id}/artifacts/model{query}')
    assert download.status_code == 200 and download.content == MODEL
    assert download.headers['content-type'] == 'application/octet-stream'
    assert download.headers['x-content-sha256'] == hashlib.sha256(MODEL).hexdigest()
    assert download.headers['content-disposition'] == 'attachment; filename="cells.pkl"'
    assert client.get(f'/api/scientific-projects/{saved.id}/artifacts/absent{query}').status_code == 400


from tests.test_scientific_project_api import web  # noqa: E402,F401
