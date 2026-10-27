"""Synthetic native-array boundary tests; not scientific-host or scale qualification."""

import numpy as np
import pytest

from env_worker import native_regions as sdk
from orchestrator.native_regions import NativeRegionInputs, Source


def source(data, reads, *, resource='d1', axes=None):
    axes = axes or tuple('zyx'[-data.ndim:])
    descriptor = {'reference': {'session': 's1', 'id': resource, 'revision': 'a' * 64, 'component': 'array'},
                  'dtype': data.dtype.name, 'shape': list(data.shape), 'axes': list(axes)}
    def read(slices):
        reads.append(slices)
        return np.asarray(data[slices])
    return Source(data.shape, data.dtype.str, axes, descriptor, read)


@pytest.mark.parametrize('dtype', ['<u2', '>u2', '<u8', '>i8', '<f4', '>f8', '|b1'])
def test_nontrivial_native_region_retains_type_endianness_and_values(dtype):
    data = np.arange(3 * 5 * 7).reshape(3, 5, 7).astype(dtype)
    if dtype == '<u8': data += 2**60  # No JSON numeric coercion may truncate object IDs.
    if dtype in ('<f4', '>f8'): data[1, 2, 3] = np.nan
    reads = []
    host = NativeRegionInputs({'image': source(data, reads)})
    resource = sdk.bind(host.specifications(), host.answer)['image']
    result = resource.read([[1, 3], [2, 5], [3, 6]])
    expected = data[1:3, 2:5, 3:6]
    assert result.dtype.str == dtype
    assert result.tobytes() == expected.tobytes()
    assert not result.flags.writeable
    assert reads == [(slice(1, 3), slice(2, 5), slice(3, 6))]
    assert resource.axes == ('z', 'y', 'x')
    assert not hasattr(resource, '__array__')
    assert 'semantic' not in resource.descriptor  # Unknown remains unknown.


def test_logical_source_is_not_materialized_and_bounds_precede_reader(tmp_path):
    # Sparse backing file exercises actual regional I/O. This is a contract test,
    # not the required multi-worker microscopy workload or memory benchmark.
    data = np.memmap(tmp_path / 'array.bin', mode='w+', dtype='<u2', shape=(64, 1024, 1024))
    data[5, 10:14, 11:16] = 512
    reads = []
    host = NativeRegionInputs({'image': source(data, reads)})
    resource = sdk.bind(host.specifications(), host.answer)['image']
    for bounds in ([[0, 64], [0, 1024], [0, 1024]], [[0, 65], [0, 1], [0, 1]],
                   [[False, 1], [0, 1], [0, 1]], [[0, 1], [0, 1]]):
        with pytest.raises(ValueError): resource.read(bounds)
    assert reads == []
    result = resource.read([[5, 6], [10, 14], [11, 16]])
    assert result.shape == (1, 4, 5) and np.all(result == 512)
    assert len(reads) == 1


def test_all_ports_share_budget_and_revocation_does_not_affect_other_grants(monkeypatch):
    data, reads = np.ones((3, 4, 5), dtype='<u2'), []
    sources = {'a': source(data, reads), 'b': source(data, reads, resource='d2')}
    host, unrelated = NativeRegionInputs(sources), NativeRegionInputs(sources)
    resources = sdk.bind(host.specifications(), host.answer)
    monkeypatch.setattr(sdk, 'MAX_READ_BYTES', 3)
    resources['a'].read([[0, 1], [0, 1], [0, 1]])
    with pytest.raises(ValueError, match='allowance'):
        resources['b'].read([[0, 1], [0, 1], [0, 1]])
    spec = host.specifications()['a']
    request = {'op': 'read-native-region', 'token': spec['token'], 'region': [[0, 1]] * 3}
    with pytest.raises(ValueError, match='allowance'): host.answer(request)  # Independent host enforcement.
    host.close()
    with pytest.raises(ValueError, match='closed'): host.answer(request)
    with pytest.raises(ValueError, match='closed'): host.specifications()
    other = sdk.bind(unrelated.specifications(), unrelated.answer)['a']
    assert other.read([[0, 1]] * 3).item() == 1


@pytest.mark.parametrize('change', [
    {'dtype': 'object'}, {'dtype': 'uint16'}, {'dtype': '=u2'}, {'shape': [1, True, 1]},
    {'axes': ['z', 'z', 'x']}, {'axes': ['z', 'x']}, {'format': 'unknown'}, {'path': '/private'},
    {'token': ''}, {'scientific': {}},
])
def test_bad_specs_never_contact_transport(change):
    host = NativeRegionInputs({'image': source(np.zeros((2, 3, 4), dtype='<u2'), [])})
    spec = host.specifications()['image'] | change
    with pytest.raises(ValueError): sdk.bind({'image': spec}, lambda _: pytest.fail('transport called'))


def test_immutable_metadata_and_grants_cannot_redirect_reads():
    reads = []
    host = NativeRegionInputs({'image': source(np.ones((2, 3, 4), dtype='<u2'), reads)})
    specs = host.specifications()
    bound = sdk.bind(specs, host.answer)['image']
    specs['image']['scientific']['reference']['id'] = 'other'
    bound.reference['id'] = 'another'
    assert bound.reference['id'] == 'd1'
    request = {'op': 'read-native-region', 'token': host.specifications()['image']['token'], 'region': [[0, 1]] * 3}
    for altered in (request | {'token': 'forged'}, request | {'path': '/private'}, request | {'region': [[0, 3]] * 3}):
        with pytest.raises(ValueError): host.answer(altered)
    with pytest.raises(ValueError): host.answer(request, 1)
    assert reads == []
    assert bound.read([[0, 1]] * 3).item() == 1


@pytest.mark.parametrize('bad', ['shape', 'dtype', 'lazy'])
def test_misbehaving_source_is_refused_without_coercing_or_changing_another_source(bad):
    data = np.zeros((2, 3, 4), dtype='<u2')
    valid = source(data, [])
    class Lazy:
        def __array__(self): pytest.fail('unbounded lazy coercion')
    wrong = {'shape': np.zeros((1, 2, 1), dtype='<u2'), 'dtype': np.zeros((1, 1, 1), dtype='<u8'), 'lazy': Lazy()}[bad]
    broken = Source(valid.shape, valid.dtype, valid.axes, valid.descriptor, lambda _: wrong)
    host = NativeRegionInputs({'bad': broken, 'good': valid})
    resources = sdk.bind(host.specifications(), host.answer)
    with pytest.raises(ValueError): resources['bad'].read([[0, 1]] * 3)
    assert resources['good'].read([[0, 1]] * 3).item() == 0


def test_reply_cannot_change_dtype_shape_payload_or_revision_claim():
    host = NativeRegionInputs({'image': source(np.ones((2, 3, 4), dtype='<u2'), [])})
    specs = host.specifications()
    for change in ({'dtype': '>u2'}, {'shape': [True, 1, 1]}, {'reference': {'id': 'forged'}}):
        def altered(request):
            header, payload = host.answer(request)
            return header | change, payload
        with pytest.raises(ValueError): sdk.bind(specs, altered)['image'].read([[0, 1]] * 3)
    assert sdk.bind(specs, host.answer)['image'].read([[0, 1]] * 3).item() == 1


def test_close_revokes_a_blocked_read_and_queued_reads_before_publication():
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from tests.waiting import HANG_S

    entered, release = threading.Event(), threading.Event()
    data = np.ones((2, 3, 4), dtype='<u2')
    original = source(data, [])
    calls = []
    def blocked(slices):
        calls.append(slices)
        entered.set()
        assert release.wait(HANG_S)
        return data[slices]
    host = NativeRegionInputs({'image': Source(original.shape, original.dtype, original.axes, original.descriptor, blocked)})
    request = {'op': 'read-native-region', 'token': host.specifications()['image']['token'], 'region': [[0, 1]] * 3}
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = pool.submit(host.answer, request)
        try:
            assert entered.wait(HANG_S)
            queued = pool.submit(host.answer, request)
            pool.submit(host.close).result(timeout=HANG_S)
            assert not pending.done()
        finally:
            release.set()
        for result in (pending, queued):
            with pytest.raises(ValueError, match='closed'): result.result(timeout=HANG_S)
    assert len(calls) == 1
