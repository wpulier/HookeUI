"""Lazy, chunk-cached native arrays (env_worker/lazy_arrays.py, H08) and the view read budget (native_regions)."""
import numpy as np
import pytest

from env_worker import lazy_arrays as lazy
from env_worker import native_regions as regions


class Source:
    """A native source over a memmap-like array that records every region it serves."""

    def __init__(self, values):
        self.values = values
        self.shape = values.shape
        self.dtype = values.dtype.str if values.dtype.itemsize > 1 else "|" + values.dtype.str[1:]
        self.reads = []

    def read(self, bounds, *, view=False):
        assert view, "lazy reads must say they are view reads"
        self.reads.append(tuple(map(tuple, bounds)))
        return self.values[tuple(slice(a, b) for a, b in bounds)].copy()


def big(shape=(6, 5, 700, 900), dtype="<u2"):
    return np.arange(np.prod(shape), dtype=np.uint64).reshape(shape).astype(dtype)


@pytest.mark.parametrize("key", [
    (2, 3), (Ellipsis, 10, slice(None)), (slice(1, 4), 0, slice(100, 650, 3), slice(None, None, 7)),
    (-1, -2, slice(-300, None), 5), (slice(None), slice(None), 0, 0), (0, 0), np.int64(3),
])
def test_indexing_matches_numpy_and_reads_only_touched_chunks(key):
    values = big()
    source = Source(values)
    array = lazy.LazyNative(source, lazy.ChunkCache())
    got = array[key]
    assert np.array_equal(got, values[key]) and got.dtype == values.dtype
    touched = set(source.reads)
    assert len(touched) == len(source.reads)                      # never the same chunk twice
    for bounds in touched:
        assert all(b - a <= c for (a, b), c in zip(bounds, array.chunks))


def test_the_cache_bounds_resident_bytes_and_serves_repeats_without_reading():
    values = big()
    source = Source(values)
    capacity = 4 << 20
    cache = lazy.ChunkCache(capacity)
    array = lazy.LazyNative(source, cache)
    for t in range(6):
        for z in range(5):
            array[t, z]
            assert cache.stats["residentBytes"] <= capacity
    assert cache.stats["evictions"] > 0 and cache.stats["peakBytes"] <= capacity
    before = len(source.reads)
    array[5, 4]                                                     # the last plane is still resident
    assert len(source.reads) == before and cache.stats["hits"] > 0
    assert array.stats["requests"] == len(source.reads)


def test_whole_array_and_oversized_reads_are_refused_in_words():
    array = lazy.LazyNative(Source(big()), lazy.ChunkCache())
    with pytest.raises(lazy.MaterializationRefused, match="lazily, by slice"):
        np.asarray(array)
    small = lazy.LazyNative(Source(big((3, 40, 50))), lazy.ChunkCache())
    assert np.array_equal(np.asarray(small), big((3, 40, 50)))
    monster = lazy.LazyNative(Source(np.zeros((1, 1, 1), "<u2")), lazy.ChunkCache())
    monster.shape = (400, 1024, 1024)                               # 800 MiB logical: refused before any read
    with pytest.raises(lazy.MaterializationRefused, match="smaller region or a coarser level"):
        monster[...]


@pytest.mark.parametrize("key", [[1, 2], np.array([True, False]), (None, 0), (slice(None, None, -1),)])
def test_unbounded_indexing_is_refused(key):
    with pytest.raises((TypeError, IndexError)):
        lazy.LazyNative(Source(big((4, 30, 40))), lazy.ChunkCache())[key]


def test_multiscale_levels_share_one_cache_and_say_their_downsampling():
    top = big((4, 800, 1000))
    levels = [top, top[:, ::2, ::2].copy(), top[:, ::4, ::4].copy()]
    sources = [Source(v) for v in levels]
    cache = lazy.ChunkCache(2 << 20)
    pyramid = lazy.Multiscale(sources, cache)
    assert [level.shape for level in pyramid] == [v.shape for v in levels]
    assert pyramid.downsample == [(1.0, 1.0, 1.0), (1.0, 2.0, 2.0), (1.0, 4.0, 4.0)]
    assert np.array_equal(pyramid[2][1], levels[2][1]) and np.array_equal(pyramid[0][3, 10:20, 900:], top[3, 10:20, 900:])
    assert cache.stats["residentBytes"] <= 2 << 20 and pyramid.stats["requests"] == sum(len(s.reads) for s in sources)
    with pytest.raises(ValueError, match="never grow"):
        lazy.Multiscale([Source(levels[2]), Source(top)], cache)


def test_view_reads_are_bounded_per_window_not_over_the_invocations_life():
    now = [0.0]
    budget = regions.Budget(clock=lambda: now[0])
    for _ in range(3):                                              # far past the 64 MiB lifetime operation cap
        for _ in range(regions.MAX_VIEW_BYTES // regions.MAX_REGION_BYTES):
            budget.admit(regions.MAX_REGION_BYTES, view=True)
        with pytest.raises(ValueError, match="wait a moment"):
            budget.admit(regions.MAX_REGION_BYTES, view=True)
        now[0] += regions.VIEW_WINDOW_S
    assert budget.bytes == 0 and budget.requests == 0               # view reads never spend the operation allowance
    for _ in range(regions.MAX_READ_BYTES // regions.MAX_REGION_BYTES):
        budget.admit(regions.MAX_REGION_BYTES)
    with pytest.raises(ValueError, match="exhausted"):
        budget.admit(1)


def test_the_parent_admits_view_reads_per_window_and_keeps_the_operation_cap():
    from orchestrator.native_regions import NativeRegionInputs
    from tests.test_native_regions import source
    values = np.zeros((128, 1024, 512), dtype="<u2")                # 128 MiB, a plane per MiB region
    host = NativeRegionInputs({"image": source(values, [])})
    token = host.specifications()["image"]["token"]
    ask = lambda z, **extra: host.answer({"op": "read-native-region", "token": token,
                                          "region": [[z, z + 1], [0, 1024], [0, 512]], **extra})
    for z in range(100):                                            # 100 MiB of view reads: past the 64 MiB cap
        ask(z, view=True)
    for z in range(64):
        ask(z)
    with pytest.raises(ValueError, match="exhausted"):
        ask(64)
    with pytest.raises(ValueError, match="valid payload-free"):
        ask(0, view=False)


def test_a_multiscale_row_lists_its_levels_finest_first_from_its_input():
    from types import SimpleNamespace
    from env_worker import scientific_output_wire as wire
    meta = {'format': 'native-layer-output-v1', 'shape': [4, 64, 64], 'dtype': '<u2', 'name': 'p', 'type': 'image',
            'visible': True, 'opacity': 1., 'geometry': {'basis': [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
                                                          'axisLabels': ['z', 'y', 'x'], 'units': ['pixel'] * 3}}
    inputs = {name: SimpleNamespace() for name in ('a', 'b', 'c')}
    assert wire.scene([{'input': 'a', 'metadata': meta, 'levels': ['a', 'b', 'c']}], inputs)[0]['levels'] == ['a', 'b', 'c']
    for levels in (['b', 'a', 'c'], ['a'], ['a', 'b', 'b'], 'abc'):
        with pytest.raises(ValueError):
            wire.scene([{'input': 'a', 'metadata': meta, 'levels': levels}], inputs)
    with pytest.raises(ValueError, match="multiscale image"):
        wire.scene([{'input': 'a', 'metadata': {**meta, 'type': 'labels'}, 'levels': ['a', 'b', 'c']}], inputs)


def test_retained_layers_may_be_large_while_new_outputs_stay_within_64_mib():
    from env_worker import scientific_output_wire as wire
    big = {'format': 'native-layer-output-v1', 'shape': [80, 1024, 1024], 'dtype': '|u1', 'name': 'labels',
           'type': 'labels', 'visible': True, 'opacity': 1.,
           'geometry': {'basis': [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], 'axisLabels': ['z', 'y', 'x'],
                        'units': ['pixel'] * 3}}
    meaning = {'semantic': 'instance-labels', 'axes': ['z', 'y', 'x']}
    assert wire.compatible(big, meaning, retained=True)[0]['shape'] == [80, 1024, 1024]
    with pytest.raises(ValueError, match="exceeds its supported"):
        wire.compatible(big, meaning)


def test_immutable_save_pacing_keeps_navigation_limits_and_checks_cancellation(monkeypatch):
    now=[0.];checks=[]
    budget=regions.Budget(clock=lambda:now[0])
    budget.admit(regions.MAX_VIEW_BYTES,view=True)
    with pytest.raises(ValueError,match='faster than its allowance'):budget.admit(1,view=True)
    def sleep(seconds):
        assert 0 < seconds <= .05
        now[0]+=seconds
    monkeypatch.setattr(regions.time,'sleep',sleep)
    with regions.paced_view_reads(lambda:checks.append(now[0])):
        budget.admit(1,view=True)
    assert now[0]>=regions.VIEW_WINDOW_S and len(checks)>2
    assert budget.requests==0 and budget.bytes==0 and sum(n for _,n in budget.views)==1
    budget.admit(regions.MAX_VIEW_BYTES-1,view=True)
    def stopped():raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'):
        with regions.paced_view_reads(stopped):budget.admit(1,view=True)
    assert sum(n for _,n in budget.views)==regions.MAX_VIEW_BYTES
    with pytest.raises(ValueError,match='faster than its allowance'):budget.admit(1,view=True)
