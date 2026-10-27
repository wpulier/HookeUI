"""Typed scientific tables on real retained storage; synthetic values, no UI or native-host qualification."""
import copy
import csv
import io
import itertools
import json

import numpy as np
import pytest

from orchestrator import scientific_resource_actions as actions
from orchestrator import scientific_tables as tables
from orchestrator.scientific_edits import ViewEdits
from orchestrator.scientific_projects import Conflict, InvalidProject, ProjectStore
from tests.test_scientific_edits import display


def _array(store, saved, component, data, semantic):
    draft = store.array_draft(saved.id, saved.session, component=component, shape=list(data.shape),
                              dtype=data.dtype.str, axes=['y', 'x'], chunks=[2, 3],
                              scientific={'semantic': semantic}, max_written_bytes=data.nbytes)
    for index in itertools.product(*(range(n) for n in draft.grid)):
        draft.write(index, np.ascontiguousarray(data[index[0] * 2:index[0] * 2 + 2, index[1] * 3:index[1] * 3 + 3]))
    return draft.finish()


@pytest.fixture
def scene(tmp_path):
    """Instance labels and an intensity image, both view inputs of one accepted revision."""
    store = ProjectStore(tmp_path / 'projects')
    first = store.create('s1', {'kind': 'scene', 'title': 'Cells', 'state': {}, 'resources': [], 'runtimes': []})
    labels = np.array([[0, 2**63 + 7, 2**63 + 7, 0, 5, 5], [7, 0, 7, 0, 5, 0],
                       [0, 0, 0, 0, 0, 0], [9, 9, 0, 0, 0, 0]], dtype='>u8')
    image = np.arange(24, dtype='>u2').reshape(4, 6)
    refs = {'labels': _array(store, first, 'array.labels', labels, 'instance-labels'),
            'image': _array(store, first, 'array.image', image, 'intensity')}
    document = first.document
    document['resources'] = list(refs.values())
    document['state']['scientificView'] = {'runtime': {}, 'inputs': refs}
    return store, store.save(first.id, 's1', document, expected_revision=1), refs, labels, image


def objects(keys, area, name='objects', object_space=None, **column):
    declaration = tables.declare(semantic='object-table', key='object_id', object_space=object_space, columns=[
        {'name': 'object_id', 'dtype': keys.dtype.str, 'role': 'key'},
        {'name': 'area', 'dtype': area.dtype.str, 'role': 'measurement', 'unit': 'um^2', **column}])
    return {name: (declaration, {'object_id': keys, 'area': area})}


def lineage(child, parent, dtype='<u8'):
    declaration = tables.declare(semantic='lineage-graph', columns=[
        {'name': 'child', 'dtype': dtype, 'role': 'relation', 'references': {'table': 'objects', 'column': 'object_id'}},
        {'name': 'parent', 'dtype': dtype, 'role': 'relation', 'references': {'table': 'objects', 'column': 'object_id'},
         'missing': {'policy': 'preserve', 'representation': 'sentinel', 'value': 0}}])
    return {'divisions': (declaration, {'child': np.array(child, dtype=dtype), 'parent': np.array(parent, dtype=dtype)})}


KEYS = np.array([2**63 + 7, 5, 7, 9], dtype='>u8')
AREA = np.array([2.5, np.nan, 1e-300, 4.0], dtype='>f8')


def test_published_table_reopens_with_exact_types_bytes_and_missing_values(scene):
    store, saved, refs, *_ = scene
    published = tables.publish(store, saved, objects(KEYS, AREA, missing={'policy': 'preserve', 'representation': 'nan'},
                                                     object_space=refs['labels']))
    table = tables.verify(store, saved.id, 's1', published.revision, 'objects')
    assert table.read('object_id').dtype.str == '>u8' and table.read('object_id').tobytes() == KEYS.tobytes()
    assert table.read('area').tobytes() == AREA.tobytes()  # NaN bit pattern and big-endian order kept
    assert table.page(0, 10) == [[str(2**63 + 7), 2.5], [5, None], [7, 1e-300], [9, 4.0]]
    assert table.schema['object_space'] == refs['labels'] and table.schema['columns'][1]['unit'] == 'um^2'
    rows = list(csv.reader(io.StringIO(b''.join(table.csv()).decode())))
    assert rows == [['object_id', 'area'], [str(2**63 + 7), '2.5'], ['5', ''], ['7', '1e-300'], ['9', '4.0']]
    assert store.read(saved.id, 's1', revision=saved.revision) == saved  # the earlier revision is unchanged


def test_relations_and_lineage_are_checked_against_the_key_they_reference(scene):
    store, saved, *_ = scene
    keys = np.array([1, 2, 3, 2**60], dtype='<u8')
    both = {**objects(keys, np.ones(4, '<f8')), **lineage([2, 3, 2**60], [1, 1, 0])}
    published = tables.publish(store, saved, both)
    divisions = tables.verify(store, saved.id, 's1', published.revision, 'divisions')
    assert divisions.page(0, 5) == [[2, 1], [3, 1], [str(2**60), None]]  # beyond 2**53: exact decimal text
    for child, parent, why in (([4], [1], 'does not contain'), ([2], [2], 'own parent')):
        with pytest.raises(InvalidProject, match=why):
            tables.publish(store, published, lineage(child, parent), replace=('divisions',))
    with pytest.raises(InvalidProject, match='type of the key'):
        tables.publish(store, published, lineage([2], [1], dtype='<i8'), replace=('divisions',))
    with pytest.raises(InvalidProject, match='not a declared table key'):
        tables.publish(store, saved, lineage([2], [1]))  # no objects table in that revision
    # A later table can reference a key already accepted in the revision.
    later = tables.publish(store, published, {'more': (lineage([3], [2])['divisions'][0],
                                                      {'child': np.array([3], '<u8'), 'parent': np.array([2], '<u8')})})
    assert tables.verify(store, saved.id, 's1', later.revision, 'more').rows == 1


@pytest.mark.parametrize('change, why', [
    ('repeated-key', 'repeated values'), ('nan-undeclared', 'does not declare NaN'), ('infinite', 'infinite'),
    ('wrong-dtype', 'values of type'), ('short-column', 'same number'), ('vocabulary', 'outside its vocabulary'),
    ('no-key', 'needs the key'), ('float-key', 'keys are integers'), ('nan-on-integer', 'only floating'),
    ('sentinel-type', 'value of the column type'), ('bad-name', 'lowercase'), ('unknown-semantic', 'object, lineage'),
    ('missing-values', 'exactly its declared columns')])
def test_values_that_disagree_with_their_declaration_are_refused_before_anything_is_written(scene, change, why):
    store, saved, *_ = scene
    keys, area = np.array([1, 2], '<u8'), np.array([1., 2.], '<f8')
    column = {}
    if change == 'repeated-key': keys = np.array([1, 1], '<u8')
    elif change == 'nan-undeclared': area = np.array([1., np.nan])
    elif change == 'infinite': area = np.array([1., np.inf])
    elif change == 'wrong-dtype': area = area.astype('>f8')
    elif change == 'short-column': area = area[:1]
    elif change == 'nan-on-integer': column = {'missing': {'policy': 'preserve', 'representation': 'nan'}}; area = np.array([1, 2], '<i4')
    elif change == 'sentinel-type': column = {'missing': {'policy': 'preserve', 'representation': 'sentinel', 'value': 1.5}}; area = np.array([1, 2], '<i4')
    batch = objects(keys, area, **column)
    declaration, values = batch['objects']
    if change == 'wrong-dtype': declaration['columns'][1]['dtype'] = '<f8'
    if change == 'vocabulary':
        declaration['columns'][1].update(dtype='<i4', vocabulary=[{'id': 1, 'name': 'cell'}]); values['area'] = np.array([1, 3], '<i4')
    if change == 'no-key': declaration['key'] = None; declaration['columns'][0]['role'] = 'feature'
    if change == 'float-key': values['object_id'] = np.array([1., 2.], '<f8'); declaration['columns'][0]['dtype'] = '<f8'
    if change == 'bad-name': batch = {'Objects': batch['objects']}
    if change == 'unknown-semantic': declaration['semantic'] = 'spreadsheet'
    if change == 'missing-values': values.pop('area')
    with pytest.raises(InvalidProject, match=why):
        tables.publish(store, saved, batch)
    assert store.read(saved.id, 's1') == saved
    assert not list((store.root / saved.id / 'arrays').glob('*.json')) or all(
        'table' not in path.read_text() for path in (store.root / saved.id / 'arrays').glob('*.json'))


def test_replacing_needs_consent_keeps_earlier_revisions_and_refuses_orphaned_relations(scene):
    store, saved, *_ = scene
    first = tables.publish(store, saved, {**objects(np.array([1, 2], '<u8'), np.ones(2, '<f8')), **lineage([2], [1])})
    with pytest.raises(InvalidProject, match='replace it explicitly'):
        tables.publish(store, first, objects(np.array([3], '<u8'), np.ones(1, '<f8')))
    with pytest.raises(InvalidProject, match='publish them together'):
        tables.publish(store, first, objects(np.array([3], '<u8'), np.ones(1, '<f8')), replace=('objects',))
    second = tables.publish(store, first, {**objects(np.array([3, 4], '<u8'), np.ones(2, '<f8')), **lineage([4], [3])},
                            replace=('objects', 'divisions'))
    assert tables.verify(store, saved.id, 's1', second.revision, 'objects').page(0, 5) == [[3, 1.0], [4, 1.0]]
    assert tables.verify(store, saved.id, 's1', first.revision, 'objects').page(0, 5) == [[1, 1.0], [2, 1.0]]
    assert len([r for r in second.document['resources'] if 'table.objects' in r['component']]) == 2


def test_stale_revision_conflicts_and_a_lost_acknowledgement_retries_exactly(scene):
    store, saved, *_ = scene
    batch = objects(np.array([1], '<u8'), np.ones(1, '<f8'))
    published = tables.publish(store, saved, batch, transaction='a' * 64)
    assert tables.publish(store, saved, copy.deepcopy(batch), transaction='a' * 64) == published
    with pytest.raises(Conflict):
        tables.publish(store, saved, objects(np.array([2], '<u8'), np.ones(1, '<f8')), transaction='b' * 64)
    assert store.read(saved.id, 's1') == published


@pytest.mark.parametrize('damage', ['chunk', 'schema-dtype', 'schema-unit', 'column-dropped'])
def test_a_changed_column_or_schema_never_reads_as_the_accepted_table(scene, damage):
    store, saved, *_ = scene
    published = tables.publish(store, saved, objects(np.array([1, 2], '<u8'), np.array([1., 2.], '<f8')))
    if damage == 'chunk':
        manifest = next(r for r in published.document['resources'] if r['component'] == 'array.table.objects.area')
        record = json.loads((store.root / saved.id / 'arrays' / f'{manifest["revision"]}.json').read_text())
        chunk = store.root / saved.id / 'arrays' / f'{record["parts"]["0"]}.bin'
        chunk.chmod(0o600); chunk.write_bytes(np.array([1., 3.], '<f8').tobytes())
        with pytest.raises(InvalidProject, match='changed'):
            tables.verify(store, saved.id, 's1', published.revision, 'objects')
        return
    document = published.document
    columns = document['state']['scientificTables']['objects']['columns']
    if damage == 'schema-dtype': columns[1]['dtype'] = '>f8'
    if damage == 'schema-unit': columns[1]['unit'] = 'mm^2'
    if damage == 'column-dropped': document['resources'] = [r for r in document['resources'] if not r['component'].endswith('.area')]
    forged = store.save(saved.id, 's1', document, expected_revision=published.revision)
    with pytest.raises(InvalidProject, match='disagrees|missing its area'):
        tables.verify(store, saved.id, 's1', forged.revision, 'objects')


def test_empty_tables_keep_their_schema(scene):
    store, saved, *_ = scene
    published = tables.publish(store, saved, objects(np.empty(0, '<u8'), np.empty(0, '>f4')))
    table = tables.verify(store, saved.id, 's1', published.revision, 'objects')
    assert table.rows == 0 and table.read('area').dtype.str == '>f4' and table.page(0, 10) == []
    assert b''.join(table.csv()) == b'object_id,area\r\n'


def test_pages_are_bounded_and_rows_beyond_one_chunk_stay_in_order(scene, monkeypatch):
    store, saved, *_ = scene
    from orchestrator import scientific_families
    monkeypatch.setattr(scientific_families, 'chunks', lambda dtype, shape: [3])
    keys = np.arange(1, 11, dtype='<u8')
    published = tables.publish(store, saved, objects(keys, keys.astype('<f8') / 2))
    table = tables.verify(store, saved.id, 's1', published.revision, 'objects')
    assert [row[0] for row in table.page(2, 5)] == [3, 4, 5, 6, 7]
    assert table.page(9, 1000) == [[10, 5.0]] and table.page(20, 5) == []
    with pytest.raises(InvalidProject, match='up to 1000'):
        table.page(0, 1001)


def test_measure_publishes_a_typed_object_table_and_still_reads_earlier_json_measurements(scene):
    store, saved, refs, labels, _ = scene
    measured = actions.measure(store, saved.id, 's1', saved.revision, 'labels')
    table = tables.verify(store, saved.id, 's1', measured.revision, 'objects')
    assert table.schema['object_space'] == refs['labels'] and table.schema['key'] == 'object_id'
    assert [c['unit'] for c in table.schema['columns']] == [None, 'pixel', 'pixel', 'pixel']
    assert table.page(0, 10)[0] == [5, 3, 0.3333333333333333, 4.333333333333333]
    assert str(2**63 + 7) in [row[0] for row in actions.table(store, saved.id, 's1', measured.revision)['rows']]
    legacy = saved.document
    legacy['state']['scientificMeasurements'] = {
        'format': actions.FORMAT, 'source': {'id': saved.id, 'session': 's1', 'revision': saved.revision,
            'digest': saved.digest, 'input': 'labels', 'reference': refs['labels']},
        'columns': ['object_id', 'voxel_count', 'centroid_y_px', 'centroid_x_px'], 'rows': [['5', 3, 0.3, 4.3]],
        'units': 'array-index pixels', 'background': 0, 'calibration': None}
    old = store.save(saved.id, 's1', legacy, expected_revision=measured.revision)
    assert actions.table(store, saved.id, 's1', old.revision)['rows'] == [['5', 3, 0.3, 4.3]]


@pytest.mark.parametrize('edited', ['image', 'labels'])
def test_copies_keep_tables_and_name_the_measured_labels_honestly(scene, edited):
    store, saved, refs, labels, image = scene
    measured = actions.measure(store, saved.id, 's1', saved.revision, 'labels')
    owner = ViewEdits(store, saved.id, 's1', measured.revision, refs[edited])
    edit = owner.begin(display(owner), max_written_bytes=48)
    edit.write([0, 0], np.full((2, 3), 3, dtype='>u8' if edited == 'labels' else '>u2'))
    store.save(saved.id, 's1', {**measured.document, 'title': 'Concurrent winner'}, expected_revision=measured.revision)
    with pytest.raises(Conflict):
        edit.commit()
    candidate = store.copy_candidate(measured, edit.prepared())
    copied = candidate.publish()
    table = tables.verify(store, copied.id, 's1', copied.revision, 'objects')
    assert table.read('object_id').tobytes() == tables.verify(store, saved.id, 's1', measured.revision, 'objects').read('object_id').tobytes()
    if edited == 'image':  # the measured labels were copied unchanged: the measurements are current
        assert table.schema['object_space'] == copied.document['state']['scientificView']['inputs']['labels']
        assert actions.table(store, copied.id, 's1', copied.revision)['rows'][0][0] == '5'
    else:  # the labels changed in the copy: the measurements belong to the earlier labels
        with pytest.raises(InvalidProject, match='earlier labels'):
            actions.table(store, copied.id, 's1', copied.revision)
    candidate.close(); owner.close()
