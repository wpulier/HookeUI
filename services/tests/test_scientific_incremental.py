"""Incremental storage: only changed chunks are staged, sent, written and hashed (H01 slice c).

Real Project storage, the real parent staging and edit grants, and the real worker-side snapshot and
staging code joined in process through the same wire decoding; no confined process or napari.
"""
import itertools
import sys
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from env_worker import scientific_source_wire as wire
from env_worker.scientific_host import ReviewSession
from env_worker.scientific_layer_exports import EditableLayers
from orchestrator import scientific_arrays
from orchestrator.scientific_edits import ResourceEdits
from orchestrator.scientific_host import Failed, LayerStaging, LayersStaging, base_digests
from orchestrator.scientific_projects import InvalidProject, ProjectStore

MIB = 1 << 20
LAYER = 'a' * 32


@pytest.fixture(autouse=True)
def exclude_preexisting_test_harness_threads(monkeypatch):
    frames = sys._current_frames
    harness = set(frames()) - {threading.get_ident()}
    monkeypatch.setattr(sys, '_current_frames', lambda: {key: value for key, value in frames().items() if key not in harness})


def accepted(tmp_path, shape=(80, 1024, 1024), chunks=(1, 1024, 1024), dtype='|u1', name='labels'):
    """An accepted array larger than the old 64 MiB whole-dataset cap, one MiB per chunk."""
    store = ProjectStore(tmp_path / 'projects')
    first = store.create('s1', {'kind': 'scene', 'title': 'Large', 'state': {}, 'resources': [], 'runtimes': []})
    data = (np.arange(int(np.prod(shape)), dtype=np.uint32) % 251).astype(dtype).reshape(shape)
    draft = store.array_draft(first.id, 's1', component=f'array.{name}', shape=list(shape), dtype=dtype,
                              axes=['z', 'y', 'x'], chunks=list(chunks), scientific={'semantic': 'instance-labels'},
                              max_written_bytes=data.nbytes)
    for index in itertools.product(*(range(n) for n in draft.grid)):
        draft.write(index, np.ascontiguousarray(data[tuple(slice(i * c, (i + 1) * c) for i, c in zip(index, chunks))]))
    ref = draft.finish()
    document = first.document
    document['resources'] = [ref]
    document['state']['scientificView'] = {'runtime': {}, 'inputs': {name: ref}}
    return store, store.save(first.id, 's1', document, expected_revision=1), ref, data


def worker(data, ref):
    """The worker's editable layer bound to the accepted input, as the review host holds it."""
    source = SimpleNamespace(shape=data.shape, dtype=data.dtype.str, axes=('z', 'y', 'x'),
                             descriptor={'semantic': 'instance-labels'}, reference=ref)
    layer = SimpleNamespace(data=data.copy(), _type_string='labels', ndim=3, rgb=False, multiscale=False,
                            axis_labels=('z', 'y', 'x'), units=('pixel',) * 3)
    layer.data_to_world = lambda point: tuple(float(v) for v in point)
    registry = EditableLayers(SimpleNamespace(layers=[layer]), {'labels': source})
    registry.bind(layer=layer, input='labels')
    registry.seal()
    host = object.__new__(ReviewSession)
    host._thread, host._closed, host._frame = threading.get_ident(), False, None
    host._layers, host.editable = [(LAYER, layer)], registry
    host._layer_rows = lambda: None
    return host, layer


def stage(host, edits, *, snapshot='memory', messages=None):
    """One staging exchange: the parent's request and digest payload, decoded as the worker decodes it."""
    staging = LayerStaging(edits[0])
    header, payload = base_digests(edits)
    messages = [] if messages is None else messages
    def exchange(message, body):
        messages.append((message['op'], len(body)))
        return staging.answer(message, body)
    request = wire.decode({'op': 'stage-resource', 'layer': LAYER, 'input': 'labels',
                           'edit': staging.specification, 'snapshot': snapshot, **header}, payload)
    result = host.stage_resource(LAYER, 'labels', request['edit'], exchange, snapshot=snapshot, base=request['_base'][0])
    staging.finish(result, b'')
    return messages


@pytest.mark.parametrize('snapshot', ['memory', 'files'])
def test_one_changed_chunk_of_an_80_mib_layer_is_the_only_one_sent_written_and_hashed(tmp_path, monkeypatch, snapshot):
    store, saved, ref, data = accepted(tmp_path)
    host, layer = worker(data, ref)
    layer.data[37, 5, 9] = 200  # one value in one chunk
    owner = ResourceEdits(store, saved.id, 's1', saved.revision, ref, layer=LAYER)
    edit = owner.begin(max_written_bytes=data.nbytes)
    if snapshot == 'files':
        monkeypatch.setattr('tempfile.tempdir', str(tmp_path))
    messages = stage(host, [edit], snapshot=snapshot)
    assert messages.count(('write-native-chunk', MIB)) == 1
    assert messages.count(('keep-native-chunk', 0)) == 79 and len(messages) == 80
    hashed = []
    read = scientific_arrays.read_at
    def counting(folder, name, bound):
        if name.endswith('.bin'):
            hashed.append(name)
        return read(folder, name, bound)
    monkeypatch.setattr(scientific_arrays, 'read_at', counting)
    receipt = edit.commit()
    assert len(hashed) == 1  # publication hashed only the new chunk; the 79 inherited ones were checked present
    monkeypatch.setattr(scientific_arrays, 'read_at', read)
    new = next(r for r in receipt.document['resources'] if r['component'] == 'array.labels')
    source = store.array_source(saved.id, 's1', receipt.revision, new)
    for z in (36, 37, 38):  # the changed chunk and its untouched neighbours, a bounded region at a time
        assert np.array_equal(source.read((slice(z, z + 1), slice(0, 1024), slice(0, 1024))), layer.data[z:z + 1])
    old = store.array_source(saved.id, 's1', saved.revision, ref)
    assert old.read((slice(37, 38), slice(5, 6), slice(9, 10))).item() == data[37, 5, 9]  # the earlier revision is unchanged


def test_a_false_unchanged_claim_is_refused_and_nothing_is_accepted(tmp_path):
    store, saved, ref, data = accepted(tmp_path, shape=(4, 8, 8), chunks=(1, 8, 8))
    edit = ResourceEdits(store, saved.id, 's1', saved.revision, ref, layer=LAYER).begin(max_written_bytes=data.nbytes)
    staging = LayerStaging(edit)
    with pytest.raises(InvalidProject, match='exactly its accepted base chunk'):
        staging.answer({'op': 'keep-native-chunk', 'token': edit.identity, 'index': [0, 0, 0], 'sha256': '0' * 64}, b'')
    with pytest.raises(InvalidProject):
        edit.commit()
    assert store.read(saved.id, 's1') == saved


def test_a_kept_chunk_out_of_order_or_with_bytes_is_refused(tmp_path):
    store, saved, ref, data = accepted(tmp_path, shape=(4, 8, 8), chunks=(1, 8, 8))
    edit = ResourceEdits(store, saved.id, 's1', saved.revision, ref, layer=LAYER).begin(max_written_bytes=data.nbytes)
    staging = LayerStaging(edit)
    digest = scientific_arrays._manifest(store, saved.id, ref)['parts']['1,0,0']
    with pytest.raises(Failed, match='exactly once'):
        staging.admit({'op': 'keep-native-chunk', 'token': edit.identity, 'index': [1, 0, 0], 'sha256': digest}, 0)
    first = scientific_arrays._manifest(store, saved.id, ref)['parts']['0,0,0']
    with pytest.raises(InvalidProject, match='carries no bytes'):
        staging.admit({'op': 'keep-native-chunk', 'token': edit.identity, 'index': [0, 0, 0], 'sha256': first}, 64)


def test_new_outputs_have_no_base_so_every_chunk_is_sent(tmp_path):
    from orchestrator.scientific_outputs import OutputGrant
    store, saved, ref, data = accepted(tmp_path, shape=(2, 8, 8), chunks=(1, 8, 8))
    draft = store.array_draft(saved.id, 's1', component='array.out', shape=[2, 8, 8], dtype='|u1', axes=['z', 'y', 'x'],
                              chunks=[1, 8, 8], scientific={'semantic': 'instance-labels'}, max_written_bytes=128)
    grant = OutputGrant(draft)
    assert base_digests([grant]) == ({}, b'')
    with pytest.raises(InvalidProject):
        grant.admit_message({'op': 'keep-native-chunk', 'token': grant.identity, 'index': [0, 0, 0], 'sha256': '0' * 64}, 0)


def test_inherited_chunks_must_be_present_and_their_bytes_are_checked_on_every_read(tmp_path):
    store, saved, ref, data = accepted(tmp_path, shape=(4, 8, 8), chunks=(1, 8, 8))
    folder = store.root / saved.id / 'arrays'
    parts = scientific_arrays._manifest(store, saved.id, ref)['parts']
    def publish(change):
        draft = store.array_edit(saved.id, 's1', saved.revision, ref, max_written_bytes=64)
        draft.write((0, 0, 0), np.full((1, 8, 8), change, dtype='|u1'))
        new = draft.finish()
        document = saved.document
        document['resources'] = [new]
        document['state']['scientificView']['inputs']['labels'] = new
        return store.save(saved.id, 's1', document, expected_revision=saved.revision), new
    inherited = folder / (parts['2,0,0'] + '.bin')
    body = inherited.read_bytes()
    inherited.chmod(0o600)
    inherited.write_bytes(body[:-1])  # a changed size is refused at publication
    with pytest.raises(InvalidProject, match='changed size'):
        publish(9)
    inherited.write_bytes(bytes([body[0] ^ 1]) + body[1:])  # same size: accepted, then refused when read
    receipt, new = publish(9)
    source = store.array_source(saved.id, 's1', receipt.revision, new)
    with pytest.raises(InvalidProject, match='changed'):
        source.read((slice(2, 3), slice(0, 8), slice(0, 8)))
    inherited.unlink()  # a missing chunk is refused at publication
    with pytest.raises(OSError):
        store.save(saved.id, 's1', receipt.document, expected_revision=receipt.revision)


def test_budgets_bound_new_bytes_and_logical_size_not_a_whole_array_cap(tmp_path, monkeypatch):
    store, saved, ref, data = accepted(tmp_path, shape=(4, 8, 8), chunks=(1, 8, 8))
    monkeypatch.setattr(scientific_arrays, 'MAX_NEW_BYTES', 64)  # one chunk of new bytes per publication
    draft = store.array_edit(saved.id, 's1', saved.revision, ref, max_written_bytes=128)
    draft.write((0, 0, 0), np.full((1, 8, 8), 1, dtype='|u1'))
    draft.write((1, 0, 0), np.full((1, 8, 8), 1, dtype='|u1'))
    two = draft.finish()
    document = saved.document
    document['resources'] = [two]
    with pytest.raises(InvalidProject, match='save allowance'):
        store.save(saved.id, 's1', document, expected_revision=saved.revision)
    assert scientific_arrays.MAX_LOGICAL_BYTES == 65536 * MIB
    big = store.array_draft(saved.id, 's1', component='array.big', shape=[2048, 1024, 1024], dtype='|u1',
                            axes=['z', 'y', 'x'], chunks=[1, 1024, 1024], scientific={}, max_written_bytes=MIB)
    assert big.grid == (2048, 1, 1)  # a 2 GiB component is declarable; only written chunks cost work


def test_too_many_changed_bytes_for_memory_are_refused_before_any_transport(tmp_path, monkeypatch):
    store, saved, ref, data = accepted(tmp_path, shape=(4, 8, 8), chunks=(1, 8, 8))
    host, layer = worker(data, ref)
    layer.data[:] = 1
    monkeypatch.setattr('env_worker.scientific_layer_exports.MEMORY_SNAPSHOT_BYTES', 64)
    edit = ResourceEdits(store, saved.id, 's1', saved.revision, ref, layer=LAYER).begin(max_written_bytes=data.nbytes)
    messages = []
    with pytest.raises(ValueError, match='too much to freeze'):
        stage(host, [edit], messages=messages)
    assert messages == []


def test_several_layers_each_send_only_their_changed_chunks(tmp_path):
    host, first, other, edits = two_layer_host(tmp_path)
    first.data[3, 0, 0] = 99
    sent = []
    stage_both(host, edits, sent)
    assert sent.count('write-native-chunk') == 1 and sent.count('keep-native-chunk') == 7


def two_layer_host(tmp_path):
    store, saved, ref, data = accepted(tmp_path, shape=(4, 8, 8), chunks=(1, 8, 8))
    second = store.array_draft(saved.id, 's1', component='array.mask', shape=[4, 8, 8], dtype='|u1', axes=['z', 'y', 'x'],
                               chunks=[1, 8, 8], scientific={'semantic': 'instance-labels'}, max_written_bytes=256)
    for z in range(4):
        second.write((z, 0, 0), np.ascontiguousarray(data[z:z + 1]))
    mask = second.finish()
    document = saved.document
    document['resources'].append(mask)
    document['state']['scientificView']['inputs']['mask'] = mask
    saved = store.save(saved.id, 's1', document, expected_revision=saved.revision)
    host, first = worker(data, ref)
    source = SimpleNamespace(shape=data.shape, dtype=data.dtype.str, axes=('z', 'y', 'x'),
                             descriptor={'semantic': 'instance-labels'}, reference=mask)
    other = SimpleNamespace(**{**vars(first), 'data': data.copy()})
    host.editable.inputs['mask'] = source
    host.editable._sealed = False
    host.editable.viewer.layers.append(other)
    host.editable.bind(layer=other, input='mask')
    host._layers = [('a' * 32, first), ('b' * 32, other)]
    edits = [ResourceEdits(store, saved.id, 's1', saved.revision, r, layer=n).begin(max_written_bytes=256)
             for r, n in ((ref, 'a' * 32), (mask, 'b' * 32))]
    return host, first, other, edits


def stage_both(host, edits, sent):
    staging = LayersStaging(edits)
    header, payload = base_digests(edits)
    request = wire.decode({'op': 'stage-resources', 'snapshot': 'memory', **header,
                           'layers': [{'layer': 'a' * 32, 'input': 'labels', 'edit': staging.layers[0].specification},
                                      {'layer': 'b' * 32, 'input': 'mask', 'edit': staging.layers[1].specification}]}, payload)
    def exchange(message, body):
        sent.append(message['op'])
        return staging.answer(message, body)
    staging.finish(host.stage_resources(request['layers'], exchange, snapshot='memory', base=request['_base']), b'')


def test_the_memory_budget_is_shared_by_every_layer_of_one_save(tmp_path, monkeypatch):
    # Each layer's changes fit the budget alone; together they exceed it, so the save is refused unsent.
    host, first, other, edits = two_layer_host(tmp_path)
    first.data[0] = 1; other.data[0] = 1  # one 64-byte chunk changed in each layer
    monkeypatch.setattr('env_worker.scientific_layer_exports.MEMORY_SNAPSHOT_BYTES', 100)
    sent = []
    with pytest.raises(ValueError, match='layers in this save changed'):
        stage_both(host, edits, sent)
    assert sent == []


@pytest.mark.parametrize('damage', ['digest', 'count', 'op'])
def test_base_digests_travel_bound_to_their_request(damage):
    payload = bytes(32)
    header = {'op': 'stage-resource', 'baseDigests': {'bytes': 32, 'sha256': __import__('hashlib').sha256(payload).hexdigest(),
                                                       'layers': [2]}}
    if damage == 'digest': payload = bytes([1]) + payload[1:]
    if damage == 'count': header['baseDigests']['layers'] = [3]
    if damage == 'op': header['op'] = 'render'
    with pytest.raises(ValueError):
        wire.decode(header, payload)


def test_the_review_worker_exchange_accepts_kept_chunks_from_its_worker(tmp_path, monkeypatch):
    """The parent's real exchange loop and the worker's staging over real pipes, framed as the server frames them.

    Regression: the loop passed only written chunks to staging, so the first kept chunk failed every save of an
    edited layer on the actual worker.
    """
    import io
    import os
    from env_worker.server import read_message, write_message
    from orchestrator import scientific_host
    store, saved, ref, data = accepted(tmp_path, shape=(4, 8, 8), chunks=(1, 8, 8))
    host, layer = worker(data, ref)
    layer.data[2, 3, 4] = 200
    owner = ResourceEdits(store, saved.id, 's1', saved.revision, ref, layer=LAYER)
    edit = owner.begin(max_written_bytes=data.nbytes)
    worker_in, parent_out = os.pipe()
    parent_in, worker_out = os.pipe()
    incoming, output = os.fdopen(worker_in, 'rb'), os.fdopen(worker_out, 'wb')
    sent, failures = [], []
    # The parent's threads live in another process on the actual worker: its quiescence check sees only its own.
    frames, worker_thread = sys._current_frames, []
    monkeypatch.setattr(sys, '_current_frames', lambda: {k: v for k, v in frames().items() if k in worker_thread})

    def serve():  # one stage-resource request, as scientific_host_server serves it
        try:
            host._thread = threading.get_ident()
            worker_thread.append(host._thread)
            header, payload = read_message(incoming, max_header=1 << 20, max_payload=1 << 20)
            request = wire.decode(header, payload)
            def native_write(message, body):
                sent.append(message['op'])
                write_message(output, message, body)
                return read_message(incoming, max_header=16 << 10, max_payload=0)
            result = host.stage_resource(request['layer'], request['input'], request['edit'], native_write,
                                         snapshot=request['snapshot'], base=request['_base'][0])
            write_message(output, result)
        except BaseException as error:
            failures.append(error)
        finally:
            output.close()

    review = object.__new__(scientific_host.ReviewWorker)
    review._lock, review._stopped, review._interrupt_lock = threading.Lock(), threading.Event(), threading.Lock()
    review._interrupt_fd = review._python_token = None
    review._closed, review._errors, review.work = False, io.BytesIO(), tmp_path
    review.grants, review.guard = SimpleNamespace(close=lambda: None), SimpleNamespace(check=lambda: None, close=lambda: None)
    review.process = SimpleNamespace(stdin=os.fdopen(parent_out, 'wb'), stdout=os.fdopen(parent_in, 'rb'),
                                     poll=lambda: None)
    monkeypatch.setattr(review, '_allowed', lambda: None, raising=False)
    monkeypatch.setattr(review, 'close', lambda: None, raising=False)  # no process to reap here
    thread = threading.Thread(target=serve)
    thread.start()
    try:
        review.stage_resource(LAYER, 'labels', edit)
    finally:
        review.process.stdin.close()  # a worker still waiting for a reply ends instead of hanging
        thread.join(timeout=60)
        review.process.stdout.close()
        incoming.close()
        if failures:
            raise failures[0]  # the worker side's own failure explains a parent-side end of input
    assert sent.count('keep-native-chunk') == 3 and sent.count('write-native-chunk') == 1
    result = edit.commit()
    values = store.array_source(saved.id, 's1', result.revision, result.document['resources'][0])
    assert values.read(tuple(slice(0, n) for n in data.shape)).tobytes() == layer.data.tobytes()
