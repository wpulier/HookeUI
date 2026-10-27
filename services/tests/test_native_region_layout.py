"""Real retained storage, synthetic arrays; no native renderer or performance claim."""
from dataclasses import replace

import numpy as np
import pytest

from env_worker import native_regions as wire
from orchestrator.native_regions import NativeRegionInputs
from orchestrator.scientific_projects import InvalidProject, ProjectStore
from tests.test_scientific_arrays import accept, draft, fill, project
from tests.test_native_regions import source


def test_retained_plane_chunks_restore_without_amplified_source_reads(tmp_path):
    data = (np.arange(9 * 1024 * 1024, dtype=np.uint32) % 251).astype('|u1').reshape(9, 1024, 1024)
    store = ProjectStore(tmp_path)
    owner = project(store)
    ref = fill(draft(store, owner, data, chunks=(1, 1024, 1024)), data)
    saved = accept(store, owner, ref)
    actual = store.array_source(saved.id, saved.session, saved.revision, ref)
    reads = []
    def read(parts):
        reads.append(parts)
        return actual.read(parts)
    host = NativeRegionInputs({'labels': replace(actual, read=read)})
    remote = wire.bind(host.specifications(), host.answer)['labels']
    # An arbitrary narrow box across nine planes still exceeds the unchanged 8MiB decoder bound.
    with pytest.raises(InvalidProject, match='source-read budget'):
        remote.read([[0, 9], [0, 64], [0, 128]], view=True)
    reads.clear()
    restored = np.empty(data.shape, dtype=data.dtype)
    for region in remote.regions():
        restored[tuple(slice(*part) for part in region)] = remote.read(region, view=True)
    assert np.array_equal(restored, data)
    assert len(reads) == 9
    assert all(s[0].stop - s[0].start == 1 for s in reads)
    assert sum(np.prod([s.stop-s.start for s in parts]) for parts in reads) == data.size
    assert store.read(saved.id, saved.session) == saved


@pytest.mark.parametrize('chunks', [None, (2, 3, 4), (9, 9, 9), (1, 1, 1)])
def test_regional_partition_preserves_every_value_once(chunks):
    data = np.arange(3*5*7, dtype='>u2').reshape(3, 5, 7)
    host = NativeRegionInputs({'x': replace(source(data, []), chunks=chunks)})
    remote = wire.bind(host.specifications(), host.answer)['x']
    coverage = np.zeros(data.shape, dtype=np.uint8)
    restored = np.empty_like(data)
    for bounds in remote.regions():
        parts = tuple(slice(*part) for part in bounds)
        coverage[parts] += 1
        restored[parts] = remote.read(bounds)
    assert np.all(coverage == 1)
    assert restored.dtype == data.dtype and restored.tobytes() == data.tobytes()


def test_large_storage_chunk_is_subdivided_without_crossing_its_boundary():
    data = np.arange(3*800*1024, dtype='<u2').reshape(3,800,1024)
    host = NativeRegionInputs({'x': replace(source(data, []), chunks=(1,800,1024))})
    remote = wire.bind(host.specifications(), host.answer)['x']
    assert all(np.prod([b-a for a,b in region])*2 <= wire.MAX_REGION_BYTES for region in remote.regions())
    assert sum(np.prod([b-a for a,b in region]) for region in remote.regions()) == data.size


@pytest.mark.parametrize('chunks', [[], [True,2,3], [0,2,3], [-1,2,3], [1,2], [1,2,2**53], [1.0,2,3], None])
def test_invalid_optional_chunk_metadata_is_refused(chunks):
    host = NativeRegionInputs({'x':source(np.zeros((2,3,4),dtype='|u1'),[])})
    spec = host.specifications()['x'] | {'chunks':chunks}
    with pytest.raises(ValueError): wire.bind({'x':spec},lambda _:pytest.fail('read before validation'))


def test_empty_source_has_no_regions():
    host = NativeRegionInputs({'x':replace(source(np.empty((0,3),dtype='|u1'),[]),chunks=(1,3))})
    remote=wire.bind(host.specifications(),host.answer)['x']
    assert list(remote.regions()) == []


def test_small_chunks_coalesce_within_decoder_and_existing_request_budgets():
    # Synthetic decoder with the stricter current source's 64-chunk bound.
    # Full 64MiB display must not need 16384 requests for 4KiB stored chunks.
    data = np.zeros((64,1024,1024), dtype='|u1')
    original = source(data, [])
    calls = []
    def read(parts):
        touched = np.prod([(s.stop-1)//c-s.start//c+1 for s,c in zip(parts,(1,64,64))])
        assert touched <= 64
        calls.append(parts)
        return data[parts]
    host = NativeRegionInputs({'x':replace(original, read=read, chunks=(1,64,64))})
    remote = wire.bind(host.specifications(),host.answer)['x']
    host._budget.clock = lambda:0
    remote._budget.clock = lambda:0
    total = sum(remote.read(region,view=True).nbytes for region in remote.regions())
    assert total == data.nbytes
    assert len(calls) <= 1024


def test_first_region_does_not_pool_the_logical_dimension():
    import tracemalloc
    data = np.zeros((2_000_000,2),dtype='|u1')
    host = NativeRegionInputs({'x':replace(source(data, []),chunks=(1,1))})
    remote=wire.bind(host.specifications(),host.answer)['x']
    tracemalloc.start()
    try:
        first=next(remote.regions())
        _,peak=tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert first[0][0] == first[1][0] == 0
    assert peak < 1 << 20, f'First region pooled {peak} bytes before reading any values'
