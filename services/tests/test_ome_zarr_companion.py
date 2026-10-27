"""Declared companion and adapter contracts with synthetic viewer/package seams; no GUI qualification."""
import importlib.util
import sys
import tomllib
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from gpu_worker import bundle_contract
from orchestrator import scientific_readers

ROOT = Path(__file__).resolve().parents[2] / 'examples/beta-scientific-tools/ome-zarr-reader'


def test_companion_declares_current_exact_folder_reader_contract():
    source = tomllib.loads((ROOT / 'model.toml').read_text())
    owner = SimpleNamespace(key='mine/ome-zarr-reader', runner=source['runner'], requires=source['requires'])
    member = bundle_contract.parse_members(source['members'], owner)[0]
    folder = {'kind': 'directory', 'name': 'selected.ome.zarr', 'size': 1024,
              'directory': {'entries': [{'path': '.zattrs'}, {'path': '.zgroup'}]}}
    assert scientific_readers.compatibility(member.reader, folder) == ''
    missing = {**folder, 'directory': {'entries': [{'path': '.zattrs'}]}}
    assert '.zgroup' in scientific_readers.compatibility(member.reader, missing)


@pytest.fixture
def adapter(monkeypatch):
    # External packages are isolated synthetic seams; actual package execution has separate gates.
    modules = {'fsspec.spec': {'AbstractFileSystem': object}, 'ome_zarr.io': {'ZarrLocation': object},
               'ome_zarr.format': {'FormatV04': object}, 'ome_zarr.reader': {'Reader': object},
               'zarr.storage': {'FSStore': object}, 'magicgui.widgets': {'Container': lambda **kw: kw}}
    for name, values in modules.items():
        module = ModuleType(name); module.__dict__.update(values); monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location('ome_zarr_companion', ROOT / 'adapter.py')
    loaded = importlib.util.module_from_spec(spec); spec.loader.exec_module(loaded)
    return loaded


class Level:
    def __init__(self, values, *, error=False, output=None):
        self.values, self.error, self.output = values, error, output
        self.shape, self.dtype, self.ndim = values.shape, values.dtype, values.ndim
    def compute(self, **kw):
        if self.error: raise ValueError('upstream decode failed')
        return self.values if self.output is None else self.output


def context():
    layers, bindings = [], []
    def add_image(values, **kwargs):
        layer = SimpleNamespace(data=values, **kwargs); layers.append(layer); return layer
    return SimpleNamespace(files={'source': object()}, viewer=SimpleNamespace(add_image=add_image, layers=layers),
                           bind_view=lambda **kw: bindings.append(kw),
                           refuse_input=lambda reason,recovery: (_ for _ in ()).throw(ValueError(reason+' '+recovery))), layers, bindings


def node(levels, chains=None):
    return SimpleNamespace(data=levels, metadata={'name': 'Pinned specimen',
        'axes': [{'name': 'y', 'type': 'space', 'unit': 'micrometer'},
                 {'name': 'x', 'type': 'space', 'unit': 'micrometer'}],
        'coordinateTransformations': chains or [[{'type': 'scale', 'scale': [2, 3]}] for _ in levels],
        'selectedRootTransforms': [{'type': 'scale', 'scale': [4, 5]}, {'type': 'translation', 'translation': [7, 11]}]})


def test_bad_second_level_cannot_leave_partial_viewer_results(adapter, monkeypatch):
    levels = [Level(np.arange(6, dtype='>u2').reshape(2, 3)), Level(np.ones((1, 2), dtype='>u2'), error=True)]
    monkeypatch.setattr(adapter, 'read_images', lambda _, **kwargs: node(levels, [
        [{'type':'scale','scale':[2,3]}], [{'type':'scale','scale':[4,4.5]}]]))
    owner, layers, bindings = context()
    with pytest.raises(ValueError, match='decode failed'): adapter.open_host(owner)
    assert layers == [] and bindings == []


def test_reader_refuses_an_upstream_dtype_change_before_publication(adapter, monkeypatch):
    values = np.arange(6, dtype='>u2').reshape(2, 3)
    monkeypatch.setattr(adapter, 'read_images', lambda _, **kwargs: node([Level(values, output=values.astype('<u2'))]))
    owner, layers, _ = context()
    with pytest.raises(ValueError, match='dtype|byte order'): adapter.open_host(owner)
    assert layers == []


@pytest.mark.parametrize('change', ['axes', 'overflow', 'boolean'])
def test_invalid_native_metadata_refuses_before_layers(adapter, monkeypatch, change):
    found = node([Level(np.arange(6, dtype='<u2').reshape(2, 3))])
    if change == 'axes': found.metadata['axes'][1]['name'] = 'y'
    elif change == 'overflow':
        found.metadata['coordinateTransformations'][0][0]['scale'] = [1e308, 1e308]
    else: found.metadata['coordinateTransformations'][0][0]['scale'] = [True, 1]
    monkeypatch.setattr(adapter, 'read_images', lambda _, **kwargs: found)
    owner, layers, _ = context()
    with pytest.raises(ValueError): adapter.open_host(owner)
    assert layers == []


def test_declared_native_values_axes_and_composed_calibration_reach_shared_view(adapter, monkeypatch):
    values = np.array([[0, 255, 256], [1000, 4096, 65535]], dtype='>u2')
    found = node([Level(values)], [[{'type': 'scale', 'scale': [2, 3]},
                                  {'type': 'translation', 'translation': [10, 20]}]])
    monkeypatch.setattr(adapter, 'read_images', lambda _, **kwargs: found)
    owner, layers, bindings = context(); adapter.open_host(owner)
    assert layers[0].data.dtype.str == '>u2' and np.array_equal(layers[0].data, values)
    assert layers[0].axis_labels == ('y', 'x') and layers[0].units == ('micrometer', 'micrometer')
    assert layers[0].scale == [8, 15] and layers[0].translate == [47, 111]
    assert bindings == [{'layer': layers[0], 'key': 'selected-ome-zarr-0'}]


class Selected(dict):
    def open(self, name):
        import io
        stream = io.BytesIO(self[name]); stream.size = len(self[name]); return stream


def selected_metadata(**changes):
    import json
    array = {'zarr_format': 2, 'shape': [2, 3], 'chunks': [2, 3], 'dtype': '>u2',
             'compressor': None, 'filters': None, 'order': 'C', 'fill_value': 0, **changes}
    return Selected({'.zgroup': b'{"zarr_format":2}',
        '.zattrs': json.dumps({'multiscales': [{'version': '0.4', 'axes': [
            {'name': 'y', 'type': 'space'}, {'name': 'x', 'type': 'space'}],
            'datasets': [{'path': '0', 'coordinateTransformations': [{'type': 'scale', 'scale': [1, 1]}]}]}]}).encode(),
        '0/.zarray': json.dumps(array).encode()})


@pytest.mark.parametrize('problem', ['object', 'oversized-chunk', 'path', 'multiple-roots'])
def test_unsafe_storage_declaration_refuses_before_upstream_reader(adapter, monkeypatch, problem):
    import json
    source = selected_metadata(dtype='|O') if problem == 'object' else selected_metadata()
    if problem == 'oversized-chunk': source = selected_metadata(chunks=[1 << 30, 3])
    if problem in ('path', 'multiple-roots'):
        attrs = json.loads(source['.zattrs'])
        if problem == 'path': attrs['multiscales'][0]['datasets'][0]['path'] = '../outside'
        else: attrs['multiscales'].append(attrs['multiscales'][0])
        source['.zattrs'] = json.dumps(attrs).encode()
    calls = []
    monkeypatch.setattr(adapter, 'SelectedLocation', lambda *a, **k: SimpleNamespace(root_attrs=json.loads(source['.zattrs'])))
    monkeypatch.setattr(adapter, 'FSStore', lambda *a, **k: None)
    monkeypatch.setattr(adapter, 'Reader', lambda *a: calls.append(True) or (lambda: [node([Level(np.zeros((2, 3), dtype='>u2'))])]))
    with pytest.raises(ValueError): adapter.read_images(source)
    assert calls == []


from tests.test_scientific_reader_choice import installed  # noqa: F401


def test_actual_companion_discovery_binds_the_accepted_folder_revision(tmp_path, installed):
    from orchestrator.scientific_projects import ProjectStore
    from tests.test_scientific_directories import transfer
    declaration = tomllib.loads((ROOT / 'model.toml').read_text())
    installed('mine/ome-zarr-reader', *declaration['members'], enabled=False)
    store = ProjectStore(tmp_path)
    original = transfer(store, selected_metadata())
    # An exact declaration and prepared environment alone do not establish local consent.
    pending = scientific_readers.discover(store, original.id, original.session, original.revision)
    assert pending['choice']['state'] == 'refused'
    assert 'Review and enable' in pending['readers'][0]['reason']
    with pytest.raises(ValueError, match='accepted checks changed'):
        scientific_readers.bind(store, original.id, original.session, original.revision,
                                pending['readers'][0]['key'], pending['readers'][0]['generation'])
    installed('mine/ome-zarr-reader', *declaration['members'], enabled=True)
    found = scientific_readers.discover(store, original.id, original.session, original.revision)
    assert found['choice']['state'] == 'chosen'
    row = found['readers'][0]
    assert row['canReview'] and row['keeps'] == ['values', 'dtype', 'byte-order', 'axes', 'calibration']
    candidate = scientific_readers.bind(store, original.id, original.session, original.revision, row['key'], row['generation'])
    origin = candidate.document['state']['sourceReader']
    assert origin['source'] == {'id': original.id, 'session': original.session, 'revision': original.revision, 'digest': original.digest}
    assert origin['capability'] == row['key'] and origin['generation'] == row['generation']
    assert candidate.document['state']['scientificView']['directories']['source'] == original.document['state']['sourceDirectory']
    assert store.read(original.id, original.session, revision=original.revision) == original


@pytest.mark.parametrize('codec', ['zlib', 'gzip', 'blosc', 'unrecognized'])
def test_compressed_chunk_refuses_before_codec_construction_or_decode(adapter, monkeypatch, codec):
    import json
    import zlib
    # The declared chunk is 12 bytes, while the small stored payload expands to
    # 4096. Refusal must precede upstream construction, never measure afterward.
    source = selected_metadata(compressor={'id': codec})
    source['0/0.0'] = zlib.compress(b'\0' * 4096)
    constructed = []
    def forbidden_reader(*args):
        constructed.append(True)
        raise AssertionError('Upstream construction may instantiate an unbounded codec')
    monkeypatch.setattr(adapter, 'SelectedLocation', lambda *a, **k: SimpleNamespace(root_attrs=json.loads(source['.zattrs'])))
    monkeypatch.setattr(adapter, 'FSStore', lambda *a, **k: None)
    monkeypatch.setattr(adapter, 'Reader', forbidden_reader)
    with pytest.raises(ValueError, match='uncompressed'):
        adapter.read_images(source)
    assert constructed == []


@pytest.mark.parametrize('width', [3, 4])
def test_scalar_volume_does_not_become_rgb_when_last_axis_has_three_or_four_values(adapter, monkeypatch, width):
    values = np.arange(31 * 47 * width, dtype='>u2').reshape(31, 47, width)
    found = SimpleNamespace(data=[Level(values)], metadata={'name': 'Scalar volume',
        'axes': [{'name': name, 'type': 'space', 'unit': 'micrometer'} for name in ('z', 'y', 'x')],
        'coordinateTransformations': [[{'type': 'scale', 'scale': [2, 3, 4]}]],
        'selectedRootTransforms': []})
    monkeypatch.setattr(adapter, 'read_images', lambda _, **kwargs: found)
    owner, layers, bindings = context()
    adapter.open_host(owner)
    assert layers[0].rgb is False
    assert layers[0].data.shape == (31, 47, width) and np.array_equal(layers[0].data, values)
    assert layers[0].axis_labels == ('z', 'y', 'x') and layers[0].scale == [2, 3, 4]
    assert bindings == [{'layer': layers[0], 'key': 'selected-ome-zarr-0'}]


def test_compressed_input_uses_explicit_shared_refusal_without_decoding(adapter, monkeypatch):
    from env_worker.scientific_host import Context
    from env_worker.scientific_input_refusal import InputRefused
    source = selected_metadata(compressor={'id': 'zlib', 'level': 1})
    calls = []
    monkeypatch.setattr(adapter, 'Reader', lambda *a: calls.append('reader'))
    owner, layers, bindings = context()
    context_owner = Context(owner.viewer, {}, None, owner.bind_view, files={'source': source})
    with pytest.raises(InputRefused, match='uncompressed NGFF chunks only') as caught:
        adapter.open_host(context_owner)
    assert caught.value.next_step == 'Export an uncompressed copy and keep the original. Bounded compressed input support is not ready.'
    assert not calls and not layers and not bindings


def test_large_image_opens_as_regions_without_whole_upstream_compute(adapter, monkeypatch):
    from env_worker.scientific_host import Context
    from env_worker.source_files import SourceFile, Budget, FORMAT
    requests=[]
    source=SourceFile({'format':FORMAT,'token':'selected','name':'image.zarr','size':1,
        'sha256':'a'*64,'regional':True,'reference':{'session':'s1','id':'source','revision':1,'component':'file.source'}},
        lambda _:pytest.fail('Synthetic decoder has no byte reads'),Budget())
    class Large:
        shape=(32768,32768);dtype=np.dtype('>u2');ndim=2
        def compute(self,**kw):pytest.fail('Whole image decoded at open')
        def __getitem__(self,slices):
            requests.append(slices)
            shape=tuple(s.stop-s.start for s in slices)
            return Level(np.full(shape,257,dtype=self.dtype))
    monkeypatch.setattr(adapter,'read_images',lambda _,**kwargs:node([Large()]))
    owner,layers,bindings=context()
    owner.files={'source':source}
    sdk=Context(owner.viewer,{},None,owner.bind_view,owner.files)
    adapter.open_host(sdk)
    assert requests==[] and len(layers)==1
    observed=layers[0].data[3:5,7:10]
    assert observed.shape==(2,3) and observed.dtype.str=='>u2' and (observed==257).all()
    assert requests and all((s[0].stop-s[0].start)*(s[1].stop-s[1].start)*2<=1<<20 for s in requests)


def test_regional_decoder_rejects_missing_authority_and_malformed_regions(monkeypatch):
    from env_worker.scientific_host import Context
    from env_worker.source_files import SourceFile, Budget, FORMAT
    declaration={'format':FORMAT,'token':'selected','name':'image.raw','size':1,'sha256':'a'*64,
        'reference':{'session':'s1','id':'source','revision':1,'component':'file.source'}}
    source=SourceFile(declaration,lambda _:None,Budget())
    ctx=Context(None,{},None,None,{'source':source})
    settings=dict(input='source',shape=(4096,4096),dtype='>u2',axes=('y','x'))
    with pytest.raises(ValueError,match='declare regional'):ctx.decode_image(**settings,decode=lambda _:None)
    source=SourceFile({**declaration,'regional':True},lambda _:None,Budget())
    ctx=Context(None,{},None,None,{'source':source})
    calls=[];buffer=np.ones((2,3),dtype='>u2')
    lazy=ctx.decode_image(**settings,decode=lambda s:calls.append(s) or buffer)
    with pytest.raises(ValueError,match='byte allowance'):lazy._source.read([[0,4096],[0,4096]],view=True)
    assert calls==[]
    with pytest.raises(ValueError,match='region shape'):lazy._source.read([[0,2],[0,2]],view=True)
    exact=lazy._source.read([[0,2],[0,3]],view=True)
    buffer[:]=9
    assert (exact==1).all() and not exact.flags.writeable
    from env_worker import lazy_arrays
    # Model Python reusing an object address after a removed layer is collected.
    monkeypatch.setattr(lazy_arrays,'id',lambda _:1,raising=False)
    for value in (3,7):
        current=ctx.decode_image(input='source',shape=(2,3),dtype='>u2',axes=('y','x'),
            decode=lambda slices,value=value:np.full((2,3),value,dtype='>u2'))
        assert (current[:,:]==value).all()
    source.close()
    with pytest.raises(ValueError,match='closed'):lazy._source.read([[0,2],[0,3]],view=True)


def test_aligned_pyramid_is_one_native_output_with_all_values(adapter, monkeypatch):
    from env_worker import scientific_outputs
    arrays = [np.arange(24, dtype='>u2').reshape(4, 6),
              (100 + np.arange(6)).astype('>u2').reshape(2, 3)]
    found = node([Level(value) for value in arrays], [
        [{'type': 'scale', 'scale': [2, 3]}], [{'type': 'scale', 'scale': [4, 6]}]])
    monkeypatch.setattr(adapter, 'read_images', lambda _, **kwargs: found)
    owner, layers, bindings = context(); adapter.open_host(owner)
    assert len(layers) == 1 and bindings == [{'layer': layers[0], 'key': 'selected-ome-zarr-0'}]
    layer = layers[0]
    assert layer.multiscale and not layer.rgb and layer.name == 'Pinned specimen'
    for actual, expected in zip(layer.data, arrays):
        assert actual.dtype.str == expected.dtype.str and actual.tobytes() == expected.tobytes()
    assert layer.scale == [8, 15] and layer.translate == [7, 11]
    layer._type_string = 'image'; layer.ndim = 2; layer.opacity = 1.
    layer.data_to_world = lambda p: tuple(a*s+t for a,s,t in zip(p, layer.scale, layer.translate))
    info = scientific_outputs.describe(layer)
    assert info['levels'] == [{'shape': [4, 6], 'dtype': '>u2'}, {'shape': [2, 3], 'dtype': '>u2'}]
    assert info['geometry']['basis'] == [[7., 11.], [15., 11.], [7., 26.]]


@pytest.mark.parametrize('problem', ['scale', 'origin', 'dtype', 'order', 'duplicate-level'])
def test_unrepresentable_pyramid_refuses_before_pixels_or_layer_publication(adapter, monkeypatch, problem):
    arrays = [np.empty((4, 6), dtype='>u2'), np.empty((2, 3), dtype='>u2')]
    if problem == 'dtype': arrays[1] = arrays[1].astype('<u2')
    if problem == 'order': arrays.reverse()
    if problem == 'duplicate-level': arrays[1] = arrays[0]
    found = node([Level(array, error=True) for array in arrays], [
        [{'type': 'scale', 'scale': [2, 3]}], [{'type': 'scale', 'scale': [4, 6]}]])
    if problem == 'duplicate-level': found.metadata['coordinateTransformations'][1][0]['scale'] = [2, 3]
    if problem == 'scale': found.metadata['coordinateTransformations'][1][0]['scale'] = [4, 5]
    if problem == 'origin': found.metadata['coordinateTransformations'][1].append(
        {'type': 'translation', 'translation': [1, 0]})
    monkeypatch.setattr(adapter, 'read_images', lambda _, **kwargs: found)
    owner, layers, bindings = context()
    with pytest.raises(ValueError, match='aligned.*keep the original'): adapter.open_host(owner)
    assert not layers and not bindings


def test_regional_pyramid_uses_distinct_declared_decoders_without_eager_pixels(adapter, monkeypatch):
    from env_worker.scientific_host import Context
    from env_worker.source_files import SourceFile, Budget, FORMAT
    requests = []
    source = SourceFile({'format': FORMAT, 'token': 'selected', 'name': 'pyramid.zarr', 'size': 1,
        'sha256': 'a'*64, 'regional': True,
        'reference': {'session': 's1', 'id': 'source', 'revision': 1, 'component': 'file.source'}},
        lambda _: pytest.fail('Synthetic decoder has no byte reads'), Budget())
    class Large:
        dtype=np.dtype('>u2'); ndim=2
        def __init__(self, size, value): self.shape=(size, size); self.value=value
        def compute(self, **kw): pytest.fail('Whole pyramid level decoded at open')
        def __getitem__(self, slices):
            requests.append((self.value, slices))
            return Level(np.full(tuple(s.stop-s.start for s in slices), self.value, dtype=self.dtype))
    found = node([Large(8192, 257), Large(4096, 513)], [
        [{'type': 'scale', 'scale': [2, 3]}], [{'type': 'scale', 'scale': [4, 6]}]])
    monkeypatch.setattr(adapter, 'read_images', lambda _, **kwargs: found)
    owner, layers, bindings = context()
    sdk = Context(owner.viewer, {}, None, owner.bind_view, {'source': source})
    adapter.open_host(sdk)
    assert not requests and len(layers) == 1 and layers[0].multiscale
    for level, value in zip(layers[0].data, (257, 513)):
        observed = level[3:5, 7:10]
        assert observed.shape == (2, 3) and observed.dtype.str == '>u2' and (observed == value).all()
    assert {value for value, _ in requests} == {257, 513}
    source.close()


@pytest.mark.parametrize('capability', [None, {'version':2,'maximum_bytes':64<<30},
                                      {'version':True,'maximum_bytes':64<<30}])
def test_larger_reader_input_refuses_older_host_before_upstream_construction(adapter, monkeypatch, capability):
    source=selected_metadata(shape=[32768,32768],chunks=[512,512])
    owner,layers,bindings=context();owner.files={'source':source}
    if capability is not None:owner.streamed_image_publication=capability
    monkeypatch.setattr(adapter,'SelectedLocation',lambda *a,**k:pytest.fail('Unsupported host constructed upstream reader'))
    with pytest.raises(ValueError,match='Update to a host with streamed image publication'):
        adapter.open_host(owner)
    assert not layers and not bindings


def test_reader_negotiates_exact_host_byte_bound_before_decode(adapter, monkeypatch):
    source=selected_metadata(shape=[32768,32768],chunks=[512,512])
    declared,layouts,labels=adapter.declared_image(source,maximum_image_bytes=2<<30)
    assert layouts==[((32768,32768),'>u2')] and not labels
    with pytest.raises(ValueError,match='retention allowance'):
        adapter.declared_image(source,maximum_image_bytes=(2<<30)-1)
    owner,_,_=context();owner.streamed_image_publication={'version':1,'maximum_bytes':2<<30}
    captured=[]
    def inspect_limit(source,**kwargs):
        captured.append(kwargs['maximum_image_bytes'])
        raise ValueError('stop after negotiated declaration')
    monkeypatch.setattr(adapter,'read_images',inspect_limit)
    with pytest.raises(ValueError,match='stop after negotiated declaration'):adapter.open_host(owner)
    assert captured==[2<<30]
