"""Synthetic transport tests for invocation-scoped regional RGB reads."""
import copy
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import numpy as np
import pytest

from env_worker import region_reader as sdk


def spec(shape=(4, 5, 3)):
    return {'token': 'resource-1', 'shape': list(shape), 'dtype': 'uint8',
            'scientific': {'semantic': 'intensity', 'shape': list(shape), 'dtype': 'uint8',
                           'reference': {'session': 's1', 'id': 'd1', 'revision': 0}}}


class Transport:
    def __init__(self):
        self.picture = np.arange(60, dtype=np.uint8).reshape(4, 5, 3)
        self.requests = []

    def __call__(self, header):
        self.requests.append(copy.deepcopy(header))
        chunk = self.picture[tuple(slice(*pair) for pair in header['region'])]
        return {'ok': True, 'kind': 'region', 'shape': list(chunk.shape), 'dtype': 'uint8'}, chunk.tobytes()


def test_nontrivial_region_is_detached_read_only_and_exact():
    transport = Transport()
    resource = sdk.bind({'image': spec()}, transport)['image']
    got = resource.read(((1, 4), (2, 5), (1, 3)))
    np.testing.assert_array_equal(got, transport.picture[1:4, 2:5, 1:3])
    assert got.shape == (3, 3, 2)
    assert got.dtype == np.uint8
    assert not got.flags.writeable
    with pytest.raises(ValueError):
        got.setflags(write=True)
    transport.picture[:] = 0
    assert got.sum() > 0
    assert transport.requests == [{'op': 'read-region', 'token': 'resource-1',
                                   'region': [[1, 4], [2, 5], [1, 3]]}]
    assert resource.shape == (4, 5, 3)
    assert resource.dtype == 'uint8'
    assert not hasattr(resource, '__array__')


def test_metadata_and_reference_are_detached_from_both_directions():
    source = spec()
    resource = sdk.bind({'image': source}, Transport())['image']
    source['shape'][0] = 99
    source['scientific']['reference']['id'] = 'changed'
    resource.descriptor['reference']['id'] = 'changed-again'
    resource.reference['id'] = 'changed-once-more'
    assert resource.descriptor['reference']['id'] == 'd1'
    assert resource.reference == {'session': 's1', 'id': 'd1', 'revision': 0}
    assert resource.shape == (4, 5, 3)
    source = spec()
    source['scientific'] = {}
    assert sdk.bind({'image': source}, Transport())['image'].reference is None


@pytest.mark.parametrize('region', [
    None, {}, [], [(0, 4), (0, 5)], [(0, 4), (0, 5), (0, 3), (0, 1)],
    [(0, 4, 1), (0, 5), (0, 3)], [(True, 4), (0, 5), (0, 3)],
    [(0.0, 4), (0, 5), (0, 3)], [(0, 0), (0, 5), (0, 3)],
    [(-1, 4), (0, 5), (0, 3)], [(0, 5), (0, 5), (0, 3)],
    [(0, 4), (0, 2**200), (0, 3)], [slice(0, 4), (0, 5), (0, 3)],
])
def test_invalid_region_never_reaches_transport(region):
    transport = Transport()
    resource = sdk.bind({'image': spec()}, transport)['image']
    with pytest.raises(ValueError):
        resource.read(region)
    assert transport.requests == []


def test_region_byte_bound_and_pure_slices():
    slices, shape, nbytes = sdk.validate_region(((1, 4), (2, 5), (0, 3)), (4, 5, 3))
    assert slices == (slice(1, 4), slice(2, 5), slice(0, 3))
    assert shape == (3, 3, 3)
    assert nbytes == 27
    with pytest.raises(ValueError, match='byte limit'):
        sdk.validate_region(((0, 1024), (0, 1024), (0, 3)), (1024, 1024, 3))
    assert sdk.validate_region(((0, 1024), (0, 1024), (0, 1)), (1024, 1024, 3))[2] == 1 << 20
    for limit in (True, 0, -1, 2**200, 1.0):
        with pytest.raises(ValueError):
            sdk.validate_region(((0, 1), (0, 1), (0, 1)), (4, 5, 3), limit)


@pytest.mark.parametrize('change', [
    {'shape': [0, 5, 3]}, {'shape': [True, 5, 3]}, {'shape': [4.0, 5, 3]},
    {'shape': [4, 5]}, {'shape': [4, 5, 4]}, {'shape': [1025, 1024, 3]},
    {'shape': [2**200, 1, 3]}, {'dtype': 'float32'}, {'token': ''}, {'token': 3},
    {'token': 'x' * 257}, {'path': '/private/data'}, {'scientific': None},
    {'scientific': {'dtype': 'float32'}}, {'scientific': {'shape': [3, 5, 3]}},
    {'scientific': {'shape': [True, 5, 3]}},
    {'scientific': {'value': float('nan')}}, {'scientific': {'value': object()}},
    {'scientific': {'value': 'x' * (128 << 10)}},
])
def test_malformed_spec_refused_before_transport(change):
    transport = Transport()
    source = spec()
    source.update(change)
    with pytest.raises(ValueError):
        sdk.bind({'image': source}, transport)
    assert transport.requests == []


@pytest.mark.parametrize('specs', [None, [], {}, {'': spec()}, {True: spec()}, {'image': {}},
                                   {'image': {'token': 'r'}}, {'x' * 129: spec()}])
def test_invalid_bind_map(specs):
    with pytest.raises(ValueError):
        sdk.bind(specs, Transport())


@pytest.mark.parametrize('change,payload', [
    ({'ok': False}, b'\x00'), ({'ok': 1}, b'\x00'), ({'kind': 'picture'}, b'\x00'),
    ({'shape': [1, 1, 2]}, b'\x00'), ({'shape': [True, 1, 1]}, b'\x00'),
    ({'shape': (1, 1, 1)}, b'\x00'), ({'dtype': 'float32'}, b'\x00'),
    ({'path': '/tmp/untrusted'}, b'\x00'), ({}, b''), ({}, b'\x00\x00'),
    ({}, bytearray(b'\x00')), ({}, memoryview(b'\x00')),
])
def test_forged_or_inexact_response_refused(change, payload):
    header = {'ok': True, 'kind': 'region', 'shape': [1, 1, 1], 'dtype': 'uint8'}
    header.update(change)
    resource = sdk.bind({'image': spec()}, lambda _: (header, payload))['image']
    with pytest.raises(ValueError):
        resource.read(((0, 1), (0, 1), (0, 1)))


@pytest.mark.parametrize('response', [None, {}, [], (None, b''), ([], b''), ({},), ({}, b'', b'')])
def test_malformed_response_envelope(response):
    resource = sdk.bind({'image': spec()}, lambda _: response)['image']
    with pytest.raises(ValueError):
        resource.read(((0, 1), (0, 1), (0, 1)))


def test_request_budget_shared_between_resources(monkeypatch):
    monkeypatch.setattr(sdk, 'MAX_READ_REQUESTS', 2)
    transport = Transport()
    resources = sdk.bind({'a': spec(), 'b': spec()}, transport)
    resources['a'].read(((0, 1), (0, 1), (0, 1)))
    resources['b'].read(((0, 1), (0, 1), (0, 1)))
    with pytest.raises(ValueError, match='budget'):
        resources['a'].read(((0, 1), (0, 1), (0, 1)))
    assert len(transport.requests) == 2


def test_byte_budget_shared_and_failed_requests_still_count(monkeypatch):
    monkeypatch.setattr(sdk, 'MAX_READ_BYTES', 3)
    requests = []

    def failed(header):
        requests.append(header)
        raise EOFError('transport closed')

    resources = sdk.bind({'a': spec(), 'b': spec()}, failed)
    with pytest.raises(EOFError):
        resources['a'].read(((0, 1), (0, 1), (0, 3)))
    with pytest.raises(ValueError, match='budget'):
        resources['b'].read(((0, 1), (0, 1), (0, 1)))
    assert len(requests) == 1


def test_resources_share_serial_transport_across_threads():
    barrier = threading.Barrier(8)
    transport_lock = threading.Lock()
    transport = Transport()

    def request(header):
        assert transport_lock.acquire(blocking=False), 'request/response exchanges overlapped'
        try:
            time.sleep(0.002)
            return transport(header)
        finally:
            transport_lock.release()

    resources = sdk.bind({'a': spec(), 'b': spec()}, request)

    def read(i):
        barrier.wait(timeout=10)
        return resources['a' if i % 2 else 'b'].read(((0, 1), (0, 1), (0, 3)))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(read, range(8)))
    assert len(transport.requests) == 8
    for result in results:
        np.testing.assert_array_equal(result, transport.picture[:1, :1, :3])
