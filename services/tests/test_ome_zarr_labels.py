"""Real folder intake and public adapter; synthetic upstream/viewer, no GUI qualification."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from tests.test_ome_zarr_companion import adapter, Level, node, selected_metadata  # noqa:F401
from tests.test_scientific_directories import transfer, granted
from orchestrator.scientific_projects import ProjectStore


def specimen(*, labels=True, dtype='>u2'):
    values = np.zeros((40, 60), dtype=dtype)
    values[10:20, 20:40] = 1
    files = selected_metadata(shape=[40, 60], chunks=[40, 60], dtype=dtype)
    attrs = json.loads(files['.zattrs'])
    if labels: attrs['image-label'] = {'version': '0.4'}
    image = attrs['multiscales'][0]
    image['name'] = 'Synthetic calibrated rectangle A'
    image['axes'] = [{'name': name, 'type': 'space', 'unit': 'micrometer'} for name in ('y', 'x')]
    image['datasets'][0]['coordinateTransformations'] = [{'type': 'scale', 'scale': [.5, .25]}]
    files['.zattrs'] = json.dumps(attrs).encode()
    files['0/0.0'] = values.tobytes()
    return files, values


def upstream(adapter, monkeypatch, files, values):
    calls = []
    monkeypatch.setattr(adapter, 'SelectedLocation', lambda *a, **k: SimpleNamespace(root_attrs=json.loads(files['.zattrs'])))
    monkeypatch.setattr(adapter, 'FSStore', lambda *a, **k: None)
    monkeypatch.setattr(adapter, 'Reader', lambda *a: calls.append('reader') or (lambda: [node([Level(values)])]))
    return calls


def viewer_context(source):
    layers, bindings = [], []
    def add(kind, values, **settings):
        layer = SimpleNamespace(_type_string=kind, data=values, **settings)
        layers.append(layer)
        return layer
    def refuse(reason, next_step):
        raise ValueError(reason + ' ' + next_step)
    return SimpleNamespace(files={'source': source}, viewer=SimpleNamespace(
        add_image=lambda values, **kw: add('image', values, **kw),
        add_labels=lambda values, **kw: add('labels', values, **kw)),
        bind_view=lambda **kw: bindings.append(kw), refuse_input=refuse), layers, bindings


@pytest.mark.parametrize('labels', [True, False])
def test_public_intake_and_adapter_preserve_explicit_semantics_and_calibrated_rectangle(tmp_path, adapter, monkeypatch, labels):
    files, expected = specimen(labels=labels)
    store = ProjectStore(tmp_path)
    accepted = transfer(store, files)
    grants, source = granted(store, accepted)
    calls = upstream(adapter, monkeypatch, files, expected)
    owner, layers, bindings = viewer_context(source)
    try: adapter.open_host(owner)
    finally: grants.close()
    assert calls == ['reader']
    assert len(layers) == 1 and layers[0]._type_string == ('labels' if labels else 'image')
    layer = layers[0]
    assert layer.data.dtype.str == '>u2' and np.array_equal(layer.data, expected)
    assert layer.axis_labels == ('y', 'x') and layer.units == ('micrometer', 'micrometer')
    assert layer.scale == [.5, .25] and layer.translate == [0., 0.]
    assert np.count_nonzero(layer.data) == 200
    assert np.count_nonzero(layer.data) * np.prod(layer.scale) == 25.
    assert bindings == [{'layer': layer, 'key': 'selected-ome-zarr-0'}]
    assert store.read(accepted.id, accepted.session, revision=1) == accepted
    if labels: assert not hasattr(layer, 'rgb')
    else: assert layer.rgb is False


@pytest.mark.parametrize('problem', ['metadata', 'version', 'linked', 'colors', 'properties', 'unknown',
    'multiscale', 'channel', 'order', 'transform', 'float', 'boolean', 'nested-labels', 'nested-directory'])
def test_incompatible_labels_refuse_before_upstream_construction(adapter, monkeypatch, problem):
    files, values = specimen(dtype='<f4' if problem == 'float' else '|b1' if problem == 'boolean' else '>u2')
    attrs = json.loads(files['.zattrs']); declaration = attrs['image-label']; image = attrs['multiscales'][0]
    if problem == 'metadata': attrs['image-label'] = []
    if problem == 'version': declaration['version'] = '0.3'
    if problem == 'linked': declaration['source'] = {'image': '../../'}
    if problem == 'colors': declaration['colors'] = [{'label-value': 1, 'rgba': [255, 0, 0, 255]}]
    if problem == 'properties': declaration['properties'] = [{'label-value': 1, 'name': 'cell'}]
    if problem == 'unknown': declaration['invented'] = True
    if problem == 'multiscale': image['datasets'].append({'path': '1'}); files['1/.zarray'] = files['0/.zarray']
    if problem == 'channel': image['axes'][0]['type'] = 'channel'
    if problem == 'order': image['axes'].reverse()
    if problem == 'transform': image['datasets'][0]['coordinateTransformations'] = []
    if problem == 'nested-labels': attrs['labels'] = ['other']
    if problem == 'nested-directory': files['labels/.zattrs'] = b'{"labels":["other"]}'
    files['.zattrs'] = json.dumps(attrs).encode()
    calls = upstream(adapter, monkeypatch, files, values)
    owner, layers, bindings = viewer_context(files)
    with pytest.raises(ValueError, match='label|Label'): adapter.open_host(owner)
    assert not calls and not layers and not bindings


def test_negative_label_ids_refuse_before_publication(adapter, monkeypatch):
    files, values = specimen(dtype='<i2'); values[0, 0] = -1
    upstream(adapter, monkeypatch, files, values)
    owner, layers, bindings = viewer_context(files)
    with pytest.raises(ValueError, match='nonnegative'): adapter.open_host(owner)
    assert not layers and not bindings


def test_single_volume_labels_preserve_composed_spatial_calibration(adapter, monkeypatch):
    values = np.arange(24, dtype='<u2').reshape(2, 3, 4)
    files = selected_metadata(shape=[2, 3, 4], chunks=[2, 3, 4], dtype='<u2')
    attrs = json.loads(files['.zattrs']); attrs['image-label'] = {'version': '0.4'}
    image = attrs['multiscales'][0]
    image['axes'] = [{'name': name, 'type': 'space', 'unit': 'micrometer'} for name in ('z', 'y', 'x')]
    image['datasets'][0]['coordinateTransformations'] = [
        {'type': 'scale', 'scale': [2, .5, .25]}, {'type': 'translation', 'translation': [1, 2, 3]}]
    image['coordinateTransformations'] = [
        {'type': 'scale', 'scale': [3, 4, 5]}, {'type': 'translation', 'translation': [6, 7, 8]}]
    files['.zattrs'] = json.dumps(attrs).encode()
    upstream(adapter, monkeypatch, files, values)
    owner, layers, _ = viewer_context(files); adapter.open_host(owner)
    assert len(layers) == 1 and layers[0]._type_string == 'labels'
    assert np.array_equal(layers[0].data, values) and layers[0].data.dtype.str == '<u2'
    assert layers[0].axis_labels == ('z', 'y', 'x') and layers[0].units == ('micrometer',)*3
    assert layers[0].scale == [6, 2, 1.25] and layers[0].translate == [9, 15, 23]
