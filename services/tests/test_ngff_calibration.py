"""Synthetic accepted-folder calibration; no native renderer or large-data qualification."""
import json

import pytest

from bench import h14_fixtures
from orchestrator import ngff_sources as ngff
from tests.ngff_fixtures import selected


def capture(tmp_path, *, datasets=None, root_transforms=None, levels=1):
    folder = tmp_path / 'calibrated.zarr'
    h14_fixtures.write_ome_zarr(folder, (2, 4, 6), levels=levels)
    path = folder / '.zattrs'
    attrs = json.loads(path.read_text())
    ms = attrs['multiscales'][0]
    if datasets is not None:
        for row, transforms in zip(ms['datasets'], datasets):
            row['coordinateTransformations'] = transforms
    if root_transforms is not None:
        ms['coordinateTransformations'] = root_transforms
    path.write_text(json.dumps(attrs))
    store, saved = selected(folder, tmp_path / 'projects')
    return ngff.SelectedStore(store, saved.id, saved.session, saved.revision)


def test_dataset_then_root_transforms_preserve_full_native_calibration(tmp_path):
    store = capture(tmp_path,
        datasets=[[{'type': 'scale', 'scale': [2, 2, 2]}, {'type': 'translation', 'translation': [3, 4, 5]}]],
        root_transforms=[{'type': 'scale', 'scale': [3, 4, 5]}, {'type': 'translation', 'translation': [28, 66, 128]}])
    pyramid = ngff.open_multiscale(store)
    source = pyramid.sources()[0]
    assert source.descriptor['calibration']['affine'] == [[6, 0, 0, 37], [0, 8, 0, 82], [0, 0, 10, 153]]
    assert pyramid.metadata()['geometry']['basis'] == [[37, 82, 153], [43, 82, 153], [37, 90, 153], [37, 82, 163]]


@pytest.mark.parametrize('transform', [
    {'type': 'affine', 'affine': [[1, 0], [0, 1]]},
    {'type': 'scale', 'scale': [1, True, 1]},
    {'type': 'translation', 'translation': [0, float('inf'), 0]},
])
def test_unsupported_or_invalid_transform_refuses_before_sources(tmp_path, transform):
    with pytest.raises(ngff.NgffError, match='transform|scale|translation'):
        ngff.open_multiscale(capture(tmp_path, root_transforms=[transform]))


def test_misaligned_pyramid_is_refused_instead_of_inventing_level_coordinates(tmp_path):
    transforms = [[{'type': 'scale', 'scale': [1, 1, 1]}],
                  [{'type': 'scale', 'scale': [1, 2, 2]}, {'type': 'translation', 'translation': [0, 1, 0]}]]
    with pytest.raises(ngff.NgffError, match='level.*transform|transform.*level'):
        ngff.open_multiscale(capture(tmp_path, datasets=transforms, levels=2))


def test_aligned_pyramid_keeps_each_levels_calibration_in_shared_scene(tmp_path):
    from env_worker.scientific_output_wire import retained_scene
    transforms = [[{'type': 'scale', 'scale': [2, 3, 4]}],
                  [{'type': 'scale', 'scale': [2, 6, 8]}]]
    pyramid = ngff.open_multiscale(capture(tmp_path, datasets=transforms, levels=2,
        root_transforms=[{'type': 'translation', 'translation': [7, 11, 13]}]))
    sources = dict(zip(('fine', 'coarse'), pyramid.sources()))
    assert sources['coarse'].descriptor['calibration']['affine'] == [[2, 0, 0, 7], [0, 6, 0, 11], [0, 0, 8, 13]]
    rows = [{'input': 'fine', 'metadata': pyramid.metadata(), 'levels': ['fine', 'coarse']}]
    assert retained_scene(rows, sources) == rows


@pytest.mark.parametrize('transforms', [
    [{'type': 'scale', 'scale': [1e308, 1, 1]}, {'type': 'scale', 'scale': [1e308, 1, 1]}],
    [{'type': 'translation', 'translation': [1e308, 0, 0]}],
    [{'type': 'scale', 'scale': [10**400, 1, 1]}],
])
def test_unrepresentable_calibration_is_refused(tmp_path, transforms):
    with pytest.raises(ngff.NgffError, match='transform|coordinate'):
        ngff.open_multiscale(capture(tmp_path, root_transforms=transforms)).sources()
