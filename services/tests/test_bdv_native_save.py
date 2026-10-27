"""Real selected-folder/HDF5 and shared native capture; synthetic UI, no Qt qualification."""
import importlib.util
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from env_worker.native_edits import OUTPUT_FORMAT, bind_output
from env_worker.scientific_outputs import capture, describe
from tests.scientific_threads import exclude_preexisting_test_harness_threads  # noqa: F401
from tests.test_bdv_reader import EXAMPLE, FACTORS, folder, h5, stack, xml


@pytest.fixture
def adapter(monkeypatch):
    callbacks = []
    container = SimpleNamespace(native=SimpleNamespace(destroyed=SimpleNamespace(connect=callbacks.append)))
    monkeypatch.setitem(sys.modules, 'magicgui.widgets', SimpleNamespace(Container=lambda **_: container))
    spec = importlib.util.spec_from_file_location('bdv_public_adapter', EXAMPLE / 'adapter.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    for callback in callbacks:
        callback()


class Viewer:
    def __init__(self):
        self.layers = []

    def add_image(self, data, **settings):
        layer = SimpleNamespace(data=data, _type_string='image', ndim=3, visible=True, opacity=1.,
                                **{'rgb':False, **settings})
        layer.data_to_world = lambda point: tuple(float(x*s) for x,s in zip(point, layer.scale))
        self.layers.append(layer)
        return layer


def context(source):
    return SimpleNamespace(files={'source':source}, viewer=Viewer(), bind_view=lambda **_: None)


def test_single_level_public_reader_can_capture_exact_native_values_and_geometry(tmp_path, adapter):
    grants, source = folder(tmp_path, {'dataset.xml':xml(), 'dataset.h5':h5(levels=FACTORS[:1])})
    try:
        ctx = context(source)
        adapter.open_host(ctx)
        assert len(ctx.viewer.layers) == 2
        for t,layer in enumerate(ctx.viewer.layers):
            info = describe(layer)
            assert info['dtype'] == '<u2' and info['shape'] == [8,24,32]
            assert info['geometry'] == {'basis':[[0.,0.,0.],[1.,0.,0.],[0.,.25,0.],[0.,0.,.25]],
                                        'axisLabels':['z','y','x'], 'units':['µm']*3}
            expected = stack(t)
            actual = np.empty_like(expected)
            def write(header, payload):
                index = header['index']; shape = header['shape']
                slices = tuple(slice(i*c,i*c+n) for i,c,n in zip(index,(2,12,16),shape))
                actual[slices] = np.frombuffer(payload,dtype=info['dtype']).reshape(shape)
                return {'ok':True,'kind':OUTPUT_FORMAT,'index':index,'bytes':len(payload)},b''
            grant = bind_output({'format':OUTPUT_FORMAT,'token':'parent-grant','shape':info['shape'],
                'dtype':info['dtype'],'axes':['z','y','x'],'chunks':[2,12,16]},write)
            with capture(ctx.viewer,layer,info,{'semantic':'intensity','axes':['z','y','x']},grant,'files') as chunks:
                for index,chunk in chunks:
                    grant.write(index,chunk)
            assert actual.tobytes() == expected.tobytes()
    finally:
        grants.close()


def track_open(adapter, monkeypatch):
    opened = []
    original = adapter.bdv.open_levels
    def record(*args, **kwargs):
        close, levels = original(*args, **kwargs)
        opened.extend(one['data']._dataset for pyramid in levels.values() for one in pyramid)
        return close, levels
    monkeypatch.setattr(adapter.bdv, 'open_levels', record)
    return opened


def test_aggregate_decoded_limit_refuses_before_pixels_or_layers(tmp_path, adapter, monkeypatch):
    import h5py
    import io
    body = io.BytesIO()
    # Two sparse logical arrays are individually below 64 MiB but exceed it
    # together. The file stays tiny; no large pixel array is allocated by this test.
    with h5py.File(body, 'w') as file:
        file['s00/resolutions'] = [[1.,1.,1.]]
        for t in (0,1):
            file.create_dataset(f't{t:05d}/s00/0/cells',shape=(1,4096,4097),dtype='i2',chunks=(1,1,1))
    grants, source = folder(tmp_path, {'dataset.xml':xml().replace(b'32 24 8',b'4097 4096 1'),
                                      'dataset.h5':body.getvalue()})
    opened = track_open(adapter, monkeypatch)
    monkeypatch.setattr(adapter.bdv.Level, '__getitem__', lambda *_: pytest.fail('Pixels decoded before aggregate admission'))
    ctx = context(source)
    refusals = []
    def refuse(reason, recovery):
        refusals.append((reason,recovery))
        raise ValueError(reason)
    ctx.refuse_input = refuse
    try:
        with pytest.raises(ValueError, match='64 MiB decoded allowance'):
            adapter.open_host(ctx)
        assert not ctx.viewer.layers
        assert len(refusals) == 1 and 'smaller single-resolution export' in refusals[0][1]
        assert len(opened) == 2 and all(not data.id.valid for data in opened)
    finally:
        grants.close()


def test_failed_second_decode_closes_source_without_partial_layers(tmp_path, adapter, monkeypatch):
    grants, source = folder(tmp_path, {'dataset.xml':xml(), 'dataset.h5':h5(levels=FACTORS[:1])})
    opened = track_open(adapter, monkeypatch)
    original = adapter.bdv.Level.__getitem__
    reads = []
    def read(level, key):
        reads.append(level)
        if len(reads) == 2:
            raise OSError('Selected source read refused')
        return original(level,key)
    monkeypatch.setattr(adapter.bdv.Level, '__getitem__', read)
    ctx = context(source)
    try:
        with pytest.raises(OSError, match='Selected source read refused'):
            adapter.open_host(ctx)
        assert len(reads) == 2 and not ctx.viewer.layers
        assert all(not data.id.valid for data in opened)
    finally:
        grants.close()


def test_pyramid_keeps_lazy_values_and_does_not_claim_native_save(tmp_path, adapter, monkeypatch):
    grants, source = folder(tmp_path, {'dataset.xml':xml(), 'dataset.h5':h5()})
    monkeypatch.setattr(adapter.bdv.Level, '__getitem__', lambda *_: pytest.fail('Pyramid was eagerly decoded'))
    ctx = context(source)
    try:
        adapter.open_host(ctx)
        assert len(ctx.viewer.layers) == 2
        for layer in ctx.viewer.layers:
            assert layer.multiscale and not layer.rgb and len(layer.data) == 3
            with pytest.raises(ValueError, match='Pyramid levels need native arrays or declared immutable regional readers'):
                describe(layer)
    finally:
        grants.close()


def test_variable_length_pixels_refuse_before_size_estimate_or_decode(tmp_path, adapter, monkeypatch):
    import h5py
    import io
    body = io.BytesIO()
    with h5py.File(body,'w') as file:
        file['s00/resolutions'] = [[1.,1.,1.]]
        file.create_dataset('t00000/s00/0/cells',shape=(1,1,1),dtype=h5py.string_dtype())
    grants, source = folder(tmp_path, {'dataset.xml':xml(timepoints=1).replace(b'32 24 8',b'1 1 1'),
                                      'dataset.h5':body.getvalue()})
    opened = track_open(adapter, monkeypatch)
    monkeypatch.setattr(adapter.bdv.Level,'__getitem__',lambda *_: pytest.fail('Variable-length pixels decoded'))
    ctx = context(source)
    def refuse(reason, recovery):
        raise ValueError(reason)
    ctx.refuse_input = refuse
    try:
        with pytest.raises(ValueError,match='fixed-width numeric'):
            adapter.open_host(ctx)
        assert not ctx.viewer.layers and all(not data.id.valid for data in opened)
    finally:
        grants.close()
