"""Explicit layer identity and bounded native export; synthetic layers, not viewer qualification.

Existing test-harness threads are excluded here. Newly started mutator threads are
real. Confined upstream tests exercise the unmodified production thread guard."""
from types import SimpleNamespace
import threading
import numpy as np
import pytest

from env_worker.scientific_layer_exports import EditableLayers
from tests.scientific_threads import exclude_preexisting_test_harness_threads  # noqa: F401


def setup(semantic='instance-labels'):
    data = np.arange(24, dtype='>u2').reshape(4, 6)
    source = SimpleNamespace(shape=data.shape, dtype=data.dtype.str, axes=('y', 'x'),
        descriptor={'semantic': semantic}, reference={'id': 'p1', 'component': 'array.labels', 'revision': 'a'})
    layer = SimpleNamespace(data=data, _type_string='labels', ndim=2, rgb=False, multiscale=False,
        axis_labels=('y', 'x'), units=('pixel', 'pixel'), offset=0)
    layer.data_to_world = lambda point: tuple(float(v) + layer.offset for v in point)
    viewer = SimpleNamespace(layers=[layer])
    registry = EditableLayers(viewer, {'labels': source})
    return registry, viewer, layer, source


def test_explicit_identity_survives_names_and_order_but_not_removal():
    registry, viewer, layer, source = setup()
    other = SimpleNamespace(**vars(layer))
    viewer.layers.insert(0, other)
    registry.bind(layer=layer, input='labels')
    registry.seal()
    assert registry.input_for(layer) == 'labels'
    assert registry.input_for(other) is None
    layer.name = 'another name'
    assert registry.capture(layer, 'labels', source.reference).tobytes() == layer.data.tobytes()
    viewer.layers = [other]
    registry.refresh()
    viewer.layers.append(layer)
    assert registry.input_for(layer) is None
    with pytest.raises(ValueError, match='binding'):
        registry.capture(layer, 'labels', source.reference)


def test_registration_is_one_layer_one_declared_input_and_factory_only():
    registry, viewer, layer, _ = setup()
    with pytest.raises(ValueError): registry.bind(layer=layer, input='unknown')
    with pytest.raises(ValueError): registry.bind(layer=SimpleNamespace(**vars(layer)), input='labels')
    registry.bind(layer=layer, input='labels')
    with pytest.raises(ValueError): registry.bind(layer=layer, input='labels')
    registry.seal()
    with pytest.raises(ValueError, match='factory'): registry.bind(layer=layer, input='labels')


@pytest.mark.parametrize('change', ['shape', 'dtype', 'geometry', 'axes', 'units', 'semantic', 'lazy', 'rgb', 'multiscale'])
def test_actual_current_array_and_geometry_must_still_match(change):
    registry, _, layer, source = setup()
    registry.bind(layer=layer, input='labels')
    registry.seal()
    if change == 'shape': layer.data = layer.data[:, :3]
    if change == 'dtype': layer.data = layer.data.astype('float32')
    if change == 'geometry': layer.offset = 1
    if change == 'axes': layer.axis_labels = ('x', 'y')
    if change == 'units': layer.units = ('um', 'um')
    if change == 'semantic': layer._type_string = 'image'
    if change == 'lazy': layer.data = object()
    if change == 'rgb': layer.rgb = True
    if change == 'multiscale': layer.multiscale = True
    with pytest.raises(ValueError): registry.capture(layer, 'labels', source.reference)


def test_capture_is_detached_native_values_and_rejects_target_substitution():
    registry, _, layer, source = setup()
    registry.bind(layer=layer, input='labels'); registry.seal()
    layer.data = layer.data.copy()
    layer.data[1, 2] = 65535
    frozen = registry.capture(layer, 'labels', source.reference)
    assert frozen.dtype.str == '>u2' and frozen[1, 2] == 65535 and not frozen.flags.writeable
    layer.data[1, 2] = 0
    assert frozen[1, 2] == 65535
    with pytest.raises(ValueError): registry.capture(layer, 'labels', {**source.reference, 'component': 'other'})


def test_unknown_meaning_and_oversized_arrays_cannot_be_bound(monkeypatch):
    registry, _, layer, _ = setup('unknown')
    with pytest.raises(ValueError, match='meaning'): registry.bind(layer=layer, input='labels')
    registry, _, layer, _ = setup()
    monkeypatch.setattr('env_worker.scientific_layer_exports.MAX_BYTES', 32)
    with pytest.raises(ValueError, match='512 MiB'): registry.bind(layer=layer, input='labels')


@pytest.mark.parametrize('snapshot', ['files','memory'])
def test_stage_exports_every_chunk_without_granting_publication(snapshot):
    import threading
    from env_worker.scientific_host import ReviewSession
    from env_worker.native_edits import FORMAT
    registry, viewer, layer, source = setup()
    source.reference = {'session': 's1', 'id': 'p1', 'component': 'array.labels', 'revision': 'a' * 64}
    registry.bind(layer=layer, input='labels'); registry.seal()
    host = object.__new__(ReviewSession)
    host._thread, host._closed, host._frame = threading.get_ident(), False, 'frame'
    host._layers, host.editable = [('layer', layer)], registry
    spec = {'format': FORMAT, 'token': 'parent-grant', 'shape': [4, 6], 'dtype': '>u2', 'axes': ['y', 'x'],
            'chunks': [2, 3], 'reference': source.reference, 'projectRevision': 2}
    received = []
    def exchange(header, payload):
        assert header['op'] == 'write-native-chunk'
        received.append((header['index'], np.frombuffer(payload, dtype=header['dtype']).reshape(header['shape']).copy()))
        layer.data[:] = 0  # Export remains the snapshot, even when callbacks mutate the private layer.
        return {'ok': True, 'kind': FORMAT, 'index': header['index'], 'bytes': len(payload)}, b''
    result = host.stage_layer('frame', 'layer', 'labels', spec, exchange, snapshot=snapshot)
    assert result == {'ok': True, 'staged': 'parent-grant'} and host._frame is None
    restored = np.empty((4, 6), dtype='>u2')
    for (row, col), chunk in received: restored[row*2:row*2+2, col*3:col*3+3] = chunk
    assert np.array_equal(restored, np.arange(24).reshape(4, 6))
    assert len(received) == 4
    with pytest.raises(ValueError, match='current'):
        host.stage_layer('frame', 'layer', 'labels', spec, exchange)


def test_independent_python_mutation_must_stop_before_capture():
    import threading
    registry, _, layer, source = setup()
    registry.bind(layer=layer, input='labels'); registry.seal()
    started, stop = threading.Event(), threading.Event()
    def mutate():
        started.set()
        while not stop.wait(.001): layer.data[:] = 7
    worker = threading.Thread(target=mutate)
    worker.start()
    try:
        assert started.wait(2)
        with pytest.raises(ValueError, match='background'):
            registry.capture(layer, 'labels', source.reference)
    finally:
        stop.set(); worker.join(timeout=2)
    assert not worker.is_alive()
    assert np.array_equal(registry.capture(layer, 'labels', source.reference), layer.data)


def test_externally_shared_mutable_storage_needs_an_explicit_snapshot_adapter():
    registry, _, layer, source = setup()
    registry.bind(layer=layer, input='labels'); registry.seal()
    layer.data = np.ndarray(source.shape, dtype=source.dtype, buffer=bytearray(48))
    with pytest.raises(ValueError, match='owned'):
        registry.capture(layer, 'labels', source.reference)


def test_staging_memory_depends_on_chunks_not_full_array():
    import tracemalloc
    from env_worker.native_edits import bind, FORMAT
    registry, _, layer, source = setup()
    layer.data = np.arange(8 * 1024 * 1024, dtype='>u2').reshape(4096, 2048)
    source.shape, source.dtype = layer.data.shape, layer.data.dtype.str
    source.reference = {'session':'s1','id':'p1','component':'array.labels','revision':'a'*64}
    registry.bind(layer=layer, input='labels'); registry.seal()
    grant = bind({'format':FORMAT,'token':'owned','shape':list(source.shape),'dtype':source.dtype,
                  'axes':['y','x'],'chunks':[256,2048],'reference':source.reference,'projectRevision':1},
                 lambda *_: None)
    tracemalloc.start()
    try:
        with registry.staged_chunks(layer, 'labels', grant) as chunks:
            layer.data[:] = 0
            count = 0
            for index, values in chunks:
                assert values.nbytes == 1 << 20 and not values.flags.writeable
                assert values.flat[1] == 1
                count += 1
            assert count == 16
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 6 << 20, f'Staging copied too much at once: {peak} bytes'


@pytest.mark.parametrize('phase', ['read', 'consumer'])
def test_snapshot_file_is_closed_on_failure(monkeypatch, phase):
    import tempfile
    from env_worker.native_edits import bind, FORMAT
    registry, _, layer, source = setup()
    source.reference = {'session':'s1','id':'p1','component':'array.labels','revision':'a'*64}
    registry.bind(layer=layer, input='labels'); registry.seal()
    grant = bind({'format':FORMAT,'token':'owned','shape':[4,6],'dtype':source.dtype,
                  'axes':['y','x'],'chunks':[2,3],'reference':source.reference,'projectRevision':1},
                 lambda *_: None)
    from pathlib import Path
    import io
    create, open_file = tempfile.TemporaryDirectory, Path.open
    directories, opened = [], []
    def directory(**kwargs):
        result = create(**kwargs)
        directories.append(Path(result.name))
        return result
    def open_chunk(path, mode='r', *args, **kwargs):
        if path.parent not in directories: return open_file(path, mode, *args, **kwargs)
        if phase == 'write' and mode == 'xb': raise OSError('Controlled full disk')
        stream = io.BytesIO(b'') if phase == 'read' and mode == 'rb' else open_file(path, mode, *args, **kwargs)
        opened.append(stream)
        return stream
    monkeypatch.setattr('env_worker.scientific_layer_exports.tempfile.TemporaryDirectory', directory)
    monkeypatch.setattr(Path, 'open', open_chunk)
    with pytest.raises(OSError):
        with registry.staged_chunks(layer, 'labels', grant) as chunks:
            assert all(path.is_file() for path in directories[0].iterdir())
            for _ in chunks:
                if phase == 'consumer': raise OSError('Controlled disconnected parent')
    assert directories and all(not path.exists() for path in directories)
    assert all(stream.closed for stream in opened)
    assert np.array_equal(layer.data, np.arange(24).reshape(4,6))


def test_small_native_chunks_pack_into_bounded_visible_files(tmp_path, monkeypatch):
    from env_worker.scientific_layer_exports import _SnapshotFiles
    monkeypatch.setattr(_SnapshotFiles, 'FILE_BYTES', 16)
    stream = _SnapshotFiles(tmp_path)
    try:
        # Read/write boundaries deliberately differ and cross a physical file.
        for i in range(48): stream.write(bytes([i]))
        stream.close()
        assert sorted(p.stat().st_size for p in tmp_path.iterdir()) == [16,16,16]
        assert b''.join(stream.read(6) for _ in range(8)) == bytes(range(48))
        assert list(tmp_path.iterdir()) == []
        with pytest.raises(OSError,match='incomplete'): stream.read(1)
    finally:
        stream.close()


@pytest.mark.parametrize('phase', ['directory', 'write'])
def test_temporary_storage_failure_falls_back_before_transport(monkeypatch, tmp_path, phase):
    from env_worker import scientific_layer_exports as exports
    from env_worker.native_edits import bind, FORMAT
    registry, _, layer, source = setup()
    source.reference = {'session':'s1','id':'p1','component':'array.labels','revision':'a'*64}
    registry.bind(layer=layer, input='labels'); registry.seal()
    grant = bind({'format':FORMAT,'token':'owned','shape':[4,6],'dtype':source.dtype,
                  'axes':['y','x'],'chunks':[2,3],'reference':source.reference,'projectRevision':1}, lambda *_: None)
    monkeypatch.setattr(exports.tempfile, 'tempdir', str(tmp_path))
    def fail(*args, **kwargs): raise OSError('Controlled temporary volume full')
    if phase == 'directory': monkeypatch.setattr(exports.tempfile, 'TemporaryDirectory', fail)
    else: monkeypatch.setattr(exports._SnapshotFiles, 'write', fail)
    restored = np.empty_like(layer.data)
    with registry.staged_chunks(layer, 'labels', grant) as chunks:
        assert list(tmp_path.iterdir()) == []
        layer.data[:] = 99
        for (row, col), values in chunks:
            restored[row*2:row*2+2, col*3:col*3+3] = values
    assert np.array_equal(restored, np.arange(24).reshape(4,6))
    assert np.all(layer.data == 99)  # Export did not roll back private work.

@pytest.mark.parametrize('snapshot', ['files', 'memory'])
def test_all_layer_snapshots_are_frozen_before_first_transport(snapshot):
    from env_worker.scientific_host import ReviewSession
    from env_worker.native_edits import FORMAT
    registry, viewer, first, source = setup()
    source.reference = {'session':'s1','id':'p1','component':'array.labels','revision':'a'*64}
    second = SimpleNamespace(**vars(first)); second.data = (first.data + 100).astype(first.data.dtype)
    second_source = SimpleNamespace(**vars(source)); second_source.reference = {**source.reference,'component':'array.second'}
    registry.inputs['second'] = second_source
    viewer.layers.append(second)
    registry.bind(layer=first,input='labels'); registry.bind(layer=second,input='second'); registry.seal()
    host = object.__new__(ReviewSession)
    host._thread,host._closed,host._frame=threading.get_ident(),False,None
    host._layers,host.editable=[('first',first),('second',second)],registry
    host._layer_rows=lambda: []
    rows=[]
    for name, layer, source in [('labels','first',source),('second','second',second_source)]:
        spec={'format':FORMAT,'token':name,'shape':[4,6],'dtype':'>u2','axes':['y','x'],
              'chunks':[2,3],'reference':source.reference,'projectRevision':2}
        rows.append({'layer':layer,'input':name,'edit':spec})
    received={'labels':[], 'second':[]}
    def exchange(header,payload):
        received[header['token']].append(np.frombuffer(payload,dtype=header['dtype']).copy())
        first.data[:]=0; second.data[:]=0
        return {'ok':True,'kind':FORMAT,'index':header['index'],'bytes':len(payload)},b''
    result=host.stage_resources(rows,exchange,snapshot=snapshot)
    assert result == {'ok':True,'staged':['labels','second']}
    assert sorted(np.concatenate(received['labels']).tolist()) == list(range(24))
    assert sorted(np.concatenate(received['second']).tolist()) == list(range(100,124))



@pytest.mark.parametrize('family',['points','tracks','vectors'])
def test_structured_layers_cannot_enter_array_only_editable_bindings(family):
    from types import SimpleNamespace
    from env_worker.scientific_layer_exports import EditableLayers
    layer=SimpleNamespace(_type_string=family,data=np.zeros((3,2)))
    source=SimpleNamespace(shape=(3,2),dtype='<f8',axes=('object','coordinate'),
        descriptor={'semantic':'point-set'},reference={'id':'points'})
    registry=EditableLayers(SimpleNamespace(layers=[layer]),{'points':source})
    with pytest.raises(ValueError,match='meaning'):
        registry.bind(layer=layer,input='points')
    assert registry.input_for(layer) is None
