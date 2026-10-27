"""Synthetic accepted arrays: metadata previews agree with the actual retained table."""
import json
import numpy as np
import pytest

from orchestrator import scientific_resource_actions as actions, scientific_tables as tables
from orchestrator.scientific_projects import InvalidProject, ProjectStore
from tests.test_scientific_arrays import fill
from tests.test_scientific_resource_actions import counted_reads


def retained(tmp_path, axes, *, semantic='instance-labels', dtype='>u8', calibration=None):
    store = ProjectStore(tmp_path / 'projects')
    original = store.create('s1', {'kind': 'scene', 'title': 'Measurement plan',
                                'state': {}, 'resources': [], 'runtimes': []})
    data = np.full((2,) * len(axes), 7, dtype=dtype)
    data.flat[0] = 0
    scientific = {'semantic': semantic, **({'calibration': calibration} if calibration else {})}
    draft = store.array_draft(original.id, 's1', component='array.labels', shape=data.shape,
                             dtype=data.dtype.str, axes=axes, chunks=[1] * len(axes),
                             scientific=scientific, max_written_bytes=data.nbytes)
    ref = fill(draft, data)
    doc = original.document
    doc['resources'] = [ref]
    doc['state']['scientificView'] = {'inputs': {'labels': ref}}
    return store, store.save(original.id, 's1', doc, expected_revision=1), data


@pytest.mark.parametrize('axes,units,scales,column,factor', [
    (['y', 'x'], ['µm', 'nm'], [.5, 250], 'area_um2', .125),
    (['x', 'z', 'y'], ['µm', 'mm', 'nm'], [.5, .002, 250], 'volume_um3', .25),
])
def test_metadata_plan_matches_published_units_and_method_without_reading_pixels(
        tmp_path, monkeypatch, axes, units, scales, column, factor):
    affine = [[scale if i == j else 0 for j in range(len(axes) + 1)]
              for i, scale in enumerate(scales)]
    calibration = {'space': {'session': 's1', 'id': 'stage', 'revision': 1},
                   'axes': axes, 'units': units, 'affine': affine}
    store, saved, data = retained(tmp_path, axes, calibration=calibration)
    reads = counted_reads(monkeypatch)
    listing = actions.inventory(store, saved.id, 's1', saved.revision)
    assert reads == []
    assert store.read(saved.id, 's1') == saved
    plan = listing['arrays'][0]['measurementPlan']
    assert plan['available'] is True and plan['reason'] == ''
    assert 'same label' in plan['method'] and 'Background 0' in plan['method']
    assert plan['physicalSize'] == {'column': column, 'perElement': factor}
    assert plan['columns'][-1]['unit'] == ('µm²' if len(axes) == 2 else 'µm³')
    measured = actions.measure(store, saved.id, 's1', saved.revision, 'labels')
    declaration = tables.tables(measured.document)['objects']
    assert [{k: actual[k] for k in planned} for actual, planned in
            zip(declaration['columns'], plan['columns'], strict=True)] == plan['columns']
    table = actions.table(store, saved.id, 's1', measured.revision)
    assert table['rows'][0][-1] == pytest.approx((data.size - 1) * factor)
    assert table['source']['digest'] == listing['source']['digest'] == saved.digest
    assert actions.measure(store, saved.id, 's1', saved.revision, 'labels') == measured


@pytest.mark.parametrize('calibration,why', [
    (None, 'no calibration'),
    ({'space': {'session': 's1', 'id': 'stage', 'revision': 1}, 'axes': ['y', 'x'],
      'units': ['µm', 'pixel'], 'affine': [[.5, 0, 0], [0, .25, 0]]}, 'not a length'),
])
def test_uncalibrated_plan_explains_pixel_units_without_inventing_physical_size(tmp_path, calibration, why):
    store, saved, _ = retained(tmp_path, ['y', 'x'], calibration=calibration)
    plan = actions.inventory(store, saved.id, 's1', saved.revision)['arrays'][0]['measurementPlan']
    assert plan['available'] and plan['physicalSize'] is None
    assert why in plan['calibrationReason']
    assert [c.get('unit') for c in plan['columns']] == [None, 'pixel', 'pixel', 'pixel']
    measured = actions.measure(store, saved.id, 's1', saved.revision, 'labels')
    assert actions.table(store, saved.id, 's1', measured.revision)['columns'] == [c['name'] for c in plan['columns']]


@pytest.mark.parametrize('axes,semantic,dtype,why', [
    (['t', 'y', 'x'], 'instance-labels', '>u8', 'Time and channel'),
    (['c', 'y', 'x'], 'instance-labels', '>u8', 'Time and channel'),
    (['y', 'x'], 'class-labels', '>u8', 'class labels'),
    (['y', 'x'], 'intensity', '<f4', 'individual-object labels'),
])
def test_unsupported_input_has_same_actionable_refusal_before_any_values_are_read(
        tmp_path, monkeypatch, axes, semantic, dtype, why):
    store, saved, _ = retained(tmp_path, axes, semantic=semantic, dtype=dtype)
    reads = counted_reads(monkeypatch)
    choice = actions.inventory(store, saved.id, 's1', saved.revision)['arrays'][0]
    plan = choice['measurementPlan']
    assert choice['canMeasure'] is plan['available'] is False
    assert why in plan['reason'] and plan['columns'] == []
    with pytest.raises(InvalidProject) as error:
        actions.measure(store, saved.id, 's1', saved.revision, 'labels')
    assert str(error.value) == plan['reason']
    assert reads == [] and store.read(saved.id, 's1') == saved


def spatial_calibration(scales, units):
    return {'space': {'session': 's1', 'id': 'stage', 'revision': 1},
            'axes': ['y', 'x'], 'units': units,
            'affine': [[scales[0], 0, 0], [0, scales[1], 0]]}


@pytest.mark.parametrize('scales,units', [
    ([1e308, 1e308], ['µm', 'µm']),  # area overflow
    ([1e-308, 1e-308], ['µm', 'µm']),  # area underflow
    ([1e308, 1], ['m', 'µm']),  # conversion overflows before multiplying axes
    ([5e-324, 1], ['nm', 'µm']),  # conversion underflows before multiplying axes
])
def test_unrepresentable_calibration_keeps_valid_counts_and_explains_why(tmp_path, monkeypatch, scales, units):
    store, saved, _ = retained(tmp_path, ['y', 'x'], calibration=spatial_calibration(scales, units))
    reads = counted_reads(monkeypatch)
    listing = actions.inventory(store, saved.id, 's1', saved.revision)
    assert reads == []
    plan = listing['arrays'][0]['measurementPlan']
    assert plan['available'] and plan['physicalSize'] is None
    assert 'positive finite' in plan['calibrationReason']
    json.dumps(listing, allow_nan=False)
    measured = actions.measure(store, saved.id, 's1', saved.revision, 'labels')
    table = actions.table(store, saved.id, 's1', measured.revision)
    assert table['columns'] == ['object_id', 'voxel_count', 'centroid_y_px', 'centroid_x_px']
    assert table['rows'][0][:2] == ['7', 3]
    assert actions.measure(store, saved.id, 's1', saved.revision, 'labels') == measured


def test_object_count_overflow_refuses_before_publication_and_preserves_accepted_revision(tmp_path, monkeypatch):
    store, saved, _ = retained(tmp_path, ['y', 'x'], calibration=spatial_calibration([1e308, 1], ['µm', 'µm']))
    plan = actions.inventory(store, saved.id, 's1', saved.revision)['arrays'][0]['measurementPlan']
    assert plan['physicalSize'] == {'column': 'area_um2', 'perElement': 1e308}
    monkeypatch.setattr(tables, 'publish', lambda *a, **k: pytest.fail('invalid object sizes reached publication'))
    with pytest.raises(InvalidProject, match='Object area.*finite.*calibration'):
        actions.measure(store, saved.id, 's1', saved.revision, 'labels')
    assert store.read(saved.id, 's1') == saved
