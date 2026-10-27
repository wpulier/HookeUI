"""Structured coordinate metadata and real retained transport, not viewer qualification."""
import copy

import numpy as np
import pytest

from gpu_worker.semantic_resources import Contract, InvalidDescriptor, SameAs, parse_descriptor, validate
from env_worker import native_regions as sdk
from orchestrator.native_regions import NativeRegionInputs
from orchestrator.scientific_projects import ProjectStore


def coordinates():
    return {'axis': 'coordinate', 'columns': [1, 3, 2], 'calibration': {
        'space': {'session': 's1', 'id': 'acquisition', 'revision': 4},
        'axes': ['z', 'y', 'x'], 'units': ['um', 'um', 'um'],
        'affine': [[2, 0, 0, 10], [0, .5, 0, 20], [0, 0, .25, 30]]}}


def descriptor():
    return {'semantic': 'point-set', 'shape': [7, 4], 'axes': ['object', 'coordinate'],
            'dtype': 'float64', 'coordinate_columns': coordinates()}


def test_storage_axes_and_value_coordinates_are_distinct_and_never_inferred():
    original = descriptor()
    assert parse_descriptor(original).wire() == original
    empty = {**original, 'shape': [0, 4]}
    assert parse_descriptor(empty).wire() == empty  # valid metadata; not a storage-support claim
    legacy = {key: value for key, value in original.items() if key != 'coordinate_columns'}
    mismatch, = validate(legacy, Contract(known=('coordinate_columns',)))
    assert mismatch.code == 'unknown'
    assert 'coordinate_columns' not in parse_descriptor(legacy).wire()
    # Moving this transform onto array indices would falsely calibrate rows/columns.
    with pytest.raises(InvalidDescriptor, match='calibrated axes must belong'):
        parse_descriptor({**legacy, 'calibration': coordinates()['calibration']})


@pytest.mark.parametrize('mutate,field', [
    (lambda d: d['coordinate_columns'].__setitem__('axis', 'absent'), 'coordinate_columns.axis'),
    (lambda d: d['coordinate_columns'].__setitem__('columns', [1, 4, 2]), 'coordinate_columns.columns'),
    (lambda d: d['coordinate_columns'].__setitem__('columns', [1, 1, 2]), 'coordinate_columns.columns'),
    (lambda d: d['coordinate_columns'].__setitem__('columns', [1, True, 2]), 'coordinate_columns.columns[1]'),
    (lambda d: d['coordinate_columns'].__setitem__('columns', [1, -1, 2]), 'coordinate_columns.columns[1]'),
    (lambda d: d['coordinate_columns'].__setitem__('columns', [1, 2]), 'coordinate_columns.columns'),
    (lambda d: d['coordinate_columns'].__setitem__('columns', []), 'coordinate_columns.columns'),
    (lambda d: d['coordinate_columns']['calibration'].__setitem__('axes', ['z', 'y', 'y']), 'coordinate_columns.calibration.axes'),
    (lambda d: d['coordinate_columns']['calibration']['affine'][0].__setitem__(3, float('nan')), 'coordinate_columns.calibration.affine[0][3]'),
    (lambda d: d['coordinate_columns']['calibration']['affine'][0].__setitem__(3, 10**400), 'coordinate_columns.calibration.affine[0][3]'),
    (lambda d: d['coordinate_columns']['calibration']['space'].__setitem__('revision', 'latest'), 'coordinate_columns.calibration.space.revision'),
])
def test_malformed_coordinates_are_refused_by_the_shared_validator(mutate, field):
    value = descriptor(); mutate(value)
    failure, = validate(value, Contract())
    assert failure.code == 'invalid-descriptor' and failure.field == field


@pytest.mark.parametrize('mutate,field', [
    (lambda c: c.__setitem__('columns', [3, 1, 2]), 'coordinate_columns.columns[0]'),
    (lambda c: c['calibration']['space'].__setitem__('revision', 5), 'coordinate_columns.calibration.space.revision'),
    (lambda c: c['calibration']['units'].__setitem__(0, 'nm'), 'coordinate_columns.calibration.units[0]'),
    (lambda c: c['calibration']['affine'][1].__setitem__(3, 21), 'coordinate_columns.calibration.affine[1][3]'),
])
def test_same_shape_does_not_allow_changed_geometry_on_inputs_or_outputs(mutate, field):
    peer, actual = descriptor(), descriptor(); mutate(actual['coordinate_columns'])
    contract = Contract(same_as=(SameAs('source', ('coordinate_columns',)),))
    for resource in ('input.points', 'output.points'):
        failure, = validate(actual, contract, peers={'source': peer}, resource=resource)
        assert failure.resource == resource and failure.field == field
        assert 'explicit' in failure.next
    assert validate(peer, contract, peers={'source': peer}) == ()


def test_value_coordinate_contract_can_be_declared_without_fixing_storage_shape():
    contract = Contract(exact={'coordinate_columns': coordinates()})
    assert validate(descriptor(), contract) == ()
    detached = contract.wire()
    detached['exact']['coordinate_columns']['columns'][0] = 100
    assert contract.wire()['exact']['coordinate_columns']['columns'] == [1, 3, 2]


@pytest.mark.parametrize('dtype', ['<f8', '>f8'])
def test_accepted_coordinate_values_and_mapping_survive_store_restart_and_worker_transport(tmp_path, dtype):
    store = ProjectStore(tmp_path)
    owner = store.create('s1', {'kind': 'scene', 'title': 'Points', 'state': {}, 'resources': [], 'runtimes': []})
    data = np.array([[101, 1.25, -9.5, 12], [102, 3.5, 7.25, 0]], dtype=dtype)
    scientific = {'semantic': 'point-set', 'coordinate_columns': coordinates()}
    writer = store.array_draft(owner.id, owner.session, component='array.points', shape=data.shape,
        dtype=data.dtype.str, axes=['object', 'coordinate'], chunks=[1, 4], scientific=scientific,
        max_written_bytes=data.nbytes)
    writer.write([0, 0], data[:1]); writer.write([1, 0], data[1:])
    reference = writer.finish()
    document = owner.document; document['resources'] = [reference]
    accepted = store.save(owner.id, owner.session, document, expected_revision=owner.revision)
    store = ProjectStore(tmp_path)
    source = store.array_source(owner.id, owner.session, accepted.revision, reference)
    grant = NativeRegionInputs({'points': source})
    remote = sdk.bind(grant.specifications(), grant.answer)['points']
    assert remote.read([[0, 2], [0, 4]]).tobytes() == data.tobytes()
    assert remote.descriptor['coordinate_columns'] == scientific['coordinate_columns']
    assert remote.axes == ('object', 'coordinate') and remote.dtype == dtype
    assert store.read(owner.id, owner.session, revision=1).document['resources'] == []
    # Returned metadata cannot mutate the accepted document or subsequent readers.
    remote.descriptor['coordinate_columns']['columns'].reverse()
    assert source.descriptor['coordinate_columns']['columns'] == [1, 3, 2]


def vector_descriptor():
    value=descriptor();value.update(semantic='vector-set',shape=[7,2,4],axes=['vector','component','coordinate'])
    value['coordinate_columns']['components']={'axis':'component','origin':0,'displacement':1}
    return value


def test_vector_geometry_explicitly_distinguishes_origins_from_directions():
    value=vector_descriptor();assert parse_descriptor(value).wire()==value
    transform=np.array(value['coordinate_columns']['calibration']['affine'])
    origin=np.array([1.,2.,3.]);direction=np.array([2.,0.,-1.])
    np.testing.assert_array_equal(transform[:,:3]@origin+transform[:,3],[12,21,30.75])
    np.testing.assert_array_equal(transform[:,:3]@direction,[4,0,-.25])
    legacy=copy.deepcopy(value);del legacy['coordinate_columns']['components']
    assert 'components' not in parse_descriptor(legacy).wire()['coordinate_columns']
    # Same-shaped point data cannot inherit vector component behavior.
    failure,=validate(legacy,Contract(exact={'coordinate_columns':value['coordinate_columns']}))
    assert failure.code=='mismatch'
    assert failure.field=='coordinate_columns.components'


@pytest.mark.parametrize('change',[
    {'axis':'coordinate','origin':0,'displacement':1},
    {'axis':'absent','origin':0,'displacement':1},
    {'axis':'component','origin':0,'displacement':0},
    {'axis':'component','origin':0,'displacement':2},
    {'axis':'component','origin':False,'displacement':1},
    {'axis':'component','origin':-1,'displacement':1},
])
def test_invalid_vector_component_roles_refuse(change):
    value=vector_descriptor();value['coordinate_columns']['components']=change
    with pytest.raises(InvalidDescriptor):parse_descriptor(value)
