"""H01 slice a3: tables, embeddings and artifacts drafted by Python or a plugin, saved in one transaction.

The real worker-side drafts (env_worker.scientific_family_drafts) and the real parent save
(orchestrator.scientific_family_saves) over the same messages the review worker carries, with a JSON round
trip for the listing; real retained storage. No confined process or napari here
(tests/test_scientific_outputs_upstream.py runs this on the actual worker).
"""
import itertools
import json
from types import SimpleNamespace

import numpy as np
import pytest

from env_worker.scientific_family_drafts import FamilyDrafts
from orchestrator import scientific_artifacts, scientific_embeddings, scientific_families, scientific_tables
from orchestrator.scientific_family_saves import FamilySave, FamilyStaging, drafts
from orchestrator.scientific_host import Failed
from orchestrator.scientific_projects import ProjectStore
from orchestrator.scientific_saves import SaveNotStarted, SaveUnavailable

LABELS = (np.arange(6 * 8) % 4).astype('<u2').reshape(6, 8)


def accepted(tmp_path):
    store = ProjectStore(tmp_path / 'projects')
    first = store.create('s1', {'kind': 'scene', 'title': 'Cells', 'state': {}, 'resources': [], 'runtimes': []})
    draft = store.array_draft(first.id, 's1', component='array.labels', shape=list(LABELS.shape), dtype='<u2',
                              axes=['y', 'x'], chunks=[3, 4], scientific={'semantic': 'instance-labels'},
                              max_written_bytes=LABELS.nbytes)
    for index in itertools.product(*(range(n) for n in draft.grid)):
        draft.write(index, np.ascontiguousarray(LABELS[tuple(slice(i * c, (i + 1) * c) for i, c in zip(index, (3, 4)))]))
    ref = draft.finish()
    document = first.document
    document['resources'] = [ref]
    document['state']['scientificView'] = {'runtime': {}, 'inputs': {'labels': ref}}
    return store, store.save(first.id, 's1', document, expected_revision=1), ref


class Worker:
    """The review worker's two draft calls, carrying the same messages the confined worker sends."""

    def __init__(self, families):
        self.families, self.sent = families, []

    def family_drafts(self):
        return drafts(json.loads(json.dumps(self.families.describe())))

    def stage_families(self, names, staging, *, unchanged=(), base=()):
        def exchange(header, body):
            self.sent.append((header['op'], len(body)))
            staging.admit(header, len(body))
            return staging.answer(header, body)
        try:
            result = self.families.stage(names, exchange, unchanged=unchanged, base=base)
        except ValueError as error:
            staging.refuse({'ok': False, 'code': 'NATIVE_CAPTURE_NOT_STARTED', 'error': str(error)}, b'')
        staging.finish(result, b'')


def session(layer_values=None):
    """Drafts as Python declares them in the console; ``labels`` is bound to a layer holding ``layer_values``."""
    layer = SimpleNamespace(name='cells')
    values = LABELS.copy() if layer_values is None else layer_values
    families = FamilyDrafts({'labels'}, lambda l: 'labels' if l is layer else None,
                            lambda name: values if name == 'labels' else None)
    return families, layer


class Frame:
    """The parts of a DataFrame that napari features use: columns, series and categories."""

    def __init__(self, columns):
        self._columns = columns
        self.columns = list(columns)
        self.to_numpy = None

    def __getitem__(self, name):
        values = self._columns[name]
        if isinstance(values, tuple):  # (codes, categories) as a categorical series
            codes, categories = values
            return SimpleNamespace(dtype='category', cat=SimpleNamespace(codes=SimpleNamespace(to_numpy=lambda: np.array(codes)),
                                                                         categories=categories))
        return SimpleNamespace(dtype=np.asarray(values).dtype, to_numpy=lambda: np.asarray(values))


def all_families(families, layer):
    keys = np.array([1, 2, 3], dtype='<u8')
    families.table('objects', {'object_id': keys, 'area': np.array([4., 5.5, 6.])}, key='object_id',
                   units={'area': 'pixel'}, object_space='labels')
    layer.features = Frame({'index': np.array([1, 2, 3], dtype='<i8'), 'kind': ([0, 1, -1], ['round', 'long']),
                            'intensity': np.array([.5, np.nan, .25])})
    families.features('features', layer, missing={'intensity': 'nan'})
    families.embedding('shape_space', np.arange(6, dtype='<f4').reshape(3, 2), table='objects', key='object_id',
                       region='labels')
    families.artifact('classifier', b'{"weights": [1, 2]}', semantic='trained-artifact', media_type='application/json',
                      file_name='classifier.json', compatibility=('scikit-learn', '1.7.2'))
    return keys


def test_python_and_plugin_drafts_publish_together_in_one_revision(tmp_path):
    store, saved, ref = accepted(tmp_path)
    families, layer = session()
    keys = all_families(families, layer)
    worker = Worker(families)
    save = FamilySave(store, saved, 'families-1234567890', ['objects', 'features', 'shape_space', 'classifier'],
                      producer={'kind': 'review'})
    save.capture(worker)
    assert store.read(saved.id, 's1') == saved  # nothing is accepted by capture
    status = save.publish()
    assert status['state'] == 'saved' and status['resource']['version'] == saved.revision + 1
    assert save.publish() == status  # a retry returns the same accepted revision
    revision = status['resource']['version']
    objects = scientific_tables.open_table(store, saved.id, 's1', revision, 'objects')
    assert objects.read('object_id').tobytes() == keys.tobytes() and objects.schema['object_space'] == ref
    assert objects.schema['producer'] == {'kind': 'review', 'family': 'table', 'draft': 'objects', 'by': 'python'}
    features = scientific_tables.open_table(store, saved.id, 's1', revision, 'features')
    kind = next(c for c in features.schema['columns'] if c['name'] == 'kind')
    assert kind['vocabulary'] == [{'id': 0, 'name': 'round'}, {'id': 1, 'name': 'long'}]
    assert kind['missing'] == {'policy': 'preserve', 'representation': 'sentinel', 'value': -1}
    assert features.schema['key'] == 'index' and features.schema['object_space'] == ref
    assert features.schema['producer']['by'] == 'layer-features'
    assert np.isnan(features.read('intensity')[1])
    embedding = scientific_embeddings.open_embedding(store, saved.id, 's1', revision, 'shape_space')
    assert embedding.read().tobytes() == np.arange(6, dtype='<f4').tobytes() and embedding.schema['region'] == ref
    artifact = scientific_artifacts.open_artifact(store, saved.id, 's1', revision, 'classifier')
    assert b''.join(artifact.chunks()) == b'{"weights": [1, 2]}'
    inventory = scientific_families.inventory(store, saved.id, 's1', revision)
    assert {(r['family'], r['name']) for r in inventory['resources'] if r['readable']} >= {
        ('table', 'objects'), ('table', 'features'), ('embeddings', 'shape_space'), ('artifact', 'classifier')}
    assert all(op == 'write-family-part' for op, _ in worker.sent)


def test_a_layer_with_unsaved_changes_cannot_name_its_object_space(tmp_path):
    store, saved, _ = accepted(tmp_path)
    edited = LABELS.copy()
    edited[5, 7] = 2  # LABELS holds 3 there
    families, layer = session(edited)
    families.table('objects', {'object_id': np.array([1, 2, 3], dtype='<u8')}, key='object_id', object_space=layer)
    worker = Worker(families)
    save = FamilySave(store, saved, 'families-1234567890', ['objects'])
    with pytest.raises(SaveNotStarted, match='unsaved changes'):
        save.capture(worker)
    assert not worker.sent and store.read(saved.id, 's1') == saved
    # A draft with no object space needs no accepted input.
    families.table('plain', {'object_id': np.array([1, 2], dtype='<u8'), 'value': np.array([1., 2.])}, key='object_id')
    save = FamilySave(store, saved, 'families-1234567891', ['plain'])
    save.capture(worker)
    assert save.publish()['state'] == 'saved'


@pytest.mark.parametrize('change,when', [('input-not-granted', 'capture'), ('unknown-draft', 'capture'),
                                         ('repeated-key', 'publish'), ('embedding-rows', 'publish')])
def test_an_invalid_draft_saves_nothing(tmp_path, change, when):
    store, saved, _ = accepted(tmp_path)
    families, layer = session()
    all_families(families, layer)
    names = ['objects', 'features', 'shape_space', 'classifier']
    if change == 'unknown-draft':
        names.append('absent')
    elif change == 'repeated-key':
        families.table('objects', {'object_id': np.array([1, 1, 3], dtype='<u8')}, key='object_id')
    elif change == 'embedding-rows':
        families.embedding('shape_space', np.zeros((2, 2), '<f4'), table='objects', key='object_id')
    save = FamilySave(store, saved, 'families-1234567890', names,
                      inputs={} if change == 'input-not-granted' else None)
    if when == 'capture':
        with pytest.raises(SaveNotStarted, match='No drafts were saved'):
            save.capture(Worker(families))
    else:
        save.capture(Worker(families))
        with pytest.raises(SaveNotStarted, match='No drafts were saved'):  # refused in one transaction: never started
            save.publish()
    assert store.read(saved.id, 's1') == saved


def test_the_python_side_cannot_name_an_input_it_was_not_granted_or_change_a_draft():
    families, _ = session()
    with pytest.raises(ValueError, match='not an input'):
        families.table('objects', {'object_id': np.array([1], dtype='<u8')}, key='object_id', object_space='other')
    with pytest.raises(ValueError, match='bound to one'):
        families.table('objects', {'object_id': np.array([1], dtype='<u8')}, key='object_id', object_space=object())
    source = np.array([1., 2.])
    families.table('values', {'value': source})
    source[0] = 99  # the draft froze its own copy
    _, arrays = families._drafts['values']
    assert arrays['value'][0] == 1. and not arrays['value'].flags.writeable
    with pytest.raises(ValueError, match='numeric'):
        families.table('names', {'label': np.array(['a', 'b'], dtype=object)})


@pytest.mark.parametrize('forgery', ['order', 'size', 'extra', 'bytes', 'unfinished'])
def test_the_parent_accepts_only_the_declared_parts_in_order(forgery):
    families, _ = session()
    families.table('objects', {'object_id': np.array([1, 2], dtype='<u8'), 'area': np.array([1., 2.])}, key='object_id')
    listed = drafts(families.describe())
    staging = FamilyStaging(listed)
    first = {'op': 'write-family-part', 'draft': 'objects', 'part': 'object_id', 'index': 0, 'count': 1}
    second = {**first, 'part': 'area'}
    with pytest.raises(Failed):
        if forgery == 'order':
            staging.admit(second, 16)
        elif forgery == 'size':
            staging.admit(first, 8)
        elif forgery == 'extra':
            staging.answer(first, np.array([1, 2], '<u8').tobytes())
            staging.answer(second, np.array([1., 2.]).tobytes())
            staging.admit({**second, 'index': 1, 'count': 2}, 16)
        elif forgery == 'bytes':
            staging.answer(first, np.array([1, 3], '<u8').tobytes())
            staging.answer(second, np.array([1., 2.]).tobytes())
            staging.finish({'ok': True, 'staged': ['objects']}, b'')
        else:
            staging.answer(first, np.array([1, 2], '<u8').tobytes())
            staging.finish({'ok': True, 'staged': ['objects']}, b'')


@pytest.mark.parametrize('listing', [
    [{'name': 'Objects'}], [{'family': 'program'}], 'everything',
    [{'producer': {'kind': 'python', 'author': 'claimed'}}], [{'parts': [{'part': 'other', 'bytes': 1, 'sha256': '0' * 64}]}],
])
def test_a_forged_draft_listing_is_refused(listing):
    families, _ = session()
    families.table('objects', {'object_id': np.array([1, 2], dtype='<u8')}, key='object_id')
    real = families.describe()
    value = listing if type(listing) is str else [{**real[0], **listing[0]}]
    with pytest.raises(ValueError):
        drafts(value)


def test_a_newer_revision_during_save_saves_nothing_and_a_new_save_joins_it(tmp_path, monkeypatch):
    store, saved, _ = accepted(tmp_path)
    families, _ = session()
    families.table('plain', {'object_id': np.array([1, 2], dtype='<u8'), 'value': np.array([1., 2.])}, key='object_id')
    save = FamilySave(store, saved, 'families-1234567890', ['plain'])
    save.capture(Worker(families))
    newer = store.save(saved.id, 's1', {**saved.document, 'title': 'Renamed elsewhere'}, expected_revision=saved.revision)
    with pytest.raises(SaveNotStarted, match='newer revision'):
        save.publish()
    assert store.read(saved.id, 's1') == newer
    again = FamilySave(store, newer, 'families-1234567891', ['plain'])
    again.capture(Worker(families))
    status = again.publish()
    assert status['resource']['version'] == newer.revision + 1
    assert 'plain' in scientific_tables.tables(store.read(saved.id, 's1').document)


def test_a_review_lists_and_saves_drafts_through_its_ordinary_session(tmp_path):
    """The review session's own ops: listing, the exact-revision check, one save, and its place among saves."""
    import asyncio
    import uuid
    from tests.test_scientific_reviews import save_rig
    from orchestrator.workspace_observation import ObservationError
    rig, values = save_rig(tmp_path)

    async def go():
        try:
            identity = await rig.manager.open(rig.origin)
            worker = rig.workers[0]
            families = FamilyDrafts({'labels'}, lambda layer: None, lambda name: values if name == 'labels' else None)
            drafting = Worker(families)
            worker.family_drafts, worker.stage_families = drafting.family_drafts, drafting.stage_families
            families.table('objects', {'object_id': np.array([0, 1, 2, 3, 4, 5], dtype='<u8')}, key='object_id',
                           object_space='labels')
            listed = await rig.manager.request(rig.client, identity, {'op': 'family-drafts', 'resource': dict(rig.ref)},
                                               snapshot=rig.pin())
            assert [d['name'] for d in listed['drafts']] == ['objects']
            command = {'op': 'save-families', 'resource': dict(rig.ref), 'request': uuid.uuid4().hex, 'names': ['objects']}
            for bad in ({**command, 'resource': {**rig.ref, 'version': rig.saved.revision + 1}},
                        {**command, 'names': ['objects', 'objects']}, {**command, 'names': []}):
                with pytest.raises(ObservationError):
                    await rig.manager.request(rig.client, identity, bad, snapshot=rig.pin())
            assert not drafting.sent
            reply = await rig.manager.request(rig.client, identity, command, snapshot=rig.pin())
            saved = reply['arraySave']
            assert saved['kind'] == 'families' and saved['state'] == 'saved' and saved['drafts'] == ['objects']
            head = rig.store.read(rig.saved.id, rig.saved.session)
            assert head.revision == rig.saved.revision + 1 and 'objects' in scientific_tables.tables(head.document)
            table = scientific_tables.open_table(rig.store, head.id, head.session, head.revision, 'objects')
            assert table.schema['object_space'] == rig.saved.document['resources'][0]
            assert table.schema['producer']['by'] == 'python'
            # A confirmed save leaves the session open for its other private work.
            await rig.manager.request(rig.client, identity, {'op': 'python-resources', 'resource': dict(rig.ref),
                                                             'cell': uuid.uuid4().hex, 'source': '1+1'}, snapshot=rig.pin())
        finally:
            await rig.manager.close()
    asyncio.run(go())


# Review of #400, round 2: the three a3 defects, reproduced, as regressions.

def test_saving_the_same_drafts_twice_is_refused_and_leaves_the_session_usable(tmp_path):
    import asyncio
    import uuid
    from tests.test_scientific_reviews import save_rig
    rig, values = save_rig(tmp_path)
    out = {}

    async def go():
        try:
            identity = await rig.manager.open(rig.origin)
            worker = rig.workers[0]
            families = FamilyDrafts({'labels'}, lambda layer: None, lambda name: values if name == 'labels' else None)
            drafting = Worker(families)
            worker.family_drafts, worker.stage_families = drafting.family_drafts, drafting.stage_families
            families.table('objects', {'object_id': np.arange(6, dtype='<u8')}, key='object_id')
            command = lambda: {'op': 'save-families', 'resource': dict(rig.ref), 'request': uuid.uuid4().hex,
                               'names': ['objects']}
            out['first'] = (await rig.manager.request(rig.client, identity, command(), snapshot=rig.pin()))['arraySave']['state']
            try:
                await rig.manager.request(rig.client, identity, command(), snapshot=rig.pin())
                out['second'] = 'saved'
            except Exception as error:  # noqa: BLE001 - the refusal is what is checked
                out['second'] = f'{type(error).__name__}: {error}'
            await rig.manager.request(rig.client, identity, {'op': 'python-resources', 'resource': dict(rig.ref),
                                                             'cell': uuid.uuid4().hex, 'source': '1+1'}, snapshot=rig.pin())
            out['after'] = 'usable'
        finally:
            await rig.manager.close()
    asyncio.run(go())
    assert out['first'] == 'saved' and out['second'] != 'saved' and out['after'] == 'usable', out


def test_a_refused_publication_is_not_started(tmp_path, monkeypatch):
    from orchestrator.scientific_projects import InvalidProject
    store, saved, _ = accepted(tmp_path)
    families, _ = session()
    families.table('objects', {'object_id': np.array([1, 2], dtype='<u8')}, key='object_id')
    save = FamilySave(store, saved, 'families-1234567890', ['objects'])
    save.capture(Worker(families))

    def refuse(*args, **kwargs):
        raise InvalidProject('A table named objects already exists')
    monkeypatch.setattr(scientific_families, 'publish', refuse)
    with pytest.raises(SaveNotStarted, match='No drafts were saved'):
        save.publish()
    assert save.closed and store.read(saved.id, saved.session) == saved


def test_a_draft_declared_from_unsaved_values_is_refused_even_after_they_revert(tmp_path):
    store, saved, _ = accepted(tmp_path)
    current = {'values': LABELS.copy()}
    current['values'][5, 7] = 2
    layer = object()
    families = FamilyDrafts({'labels'}, lambda l: 'labels' if l is layer else None, lambda name: current['values'])
    families.table('objects', {'object_id': np.array([1, 2, 3], dtype='<u8')}, key='object_id', object_space=layer)
    current['values'] = LABELS.copy()  # back to the accepted values after the declaration
    worker = Worker(families)
    with pytest.raises(SaveNotStarted, match='changed after draft objects was declared'):
        FamilySave(store, saved, 'families-1234567890', ['objects']).capture(worker)
    assert not worker.sent and store.read(saved.id, saved.session) == saved


def test_every_draft_hash_is_verified_before_anything_is_sent(tmp_path):
    store, saved, _ = accepted(tmp_path)
    families, _ = session()
    families.table('a', {'object_id': np.array([1, 2], dtype='<u8')}, key='object_id')
    families.table('b', {'object_id': np.array([1, 2], dtype='<u8')}, key='object_id')
    changed = families._drafts['b'][1]['object_id']
    changed.setflags(write=True)
    changed[0] = 9
    worker = Worker(families)
    with pytest.raises(SaveNotStarted, match='Draft b changed after it was declared'):
        FamilySave(store, saved, 'families-1234567890', ['a', 'b']).capture(worker)
    assert not worker.sent
