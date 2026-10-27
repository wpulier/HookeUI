"""Generated worker output through segment, evidence conversion and the real memory allowances."""
import asyncio
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from orchestrator.capture import MB, Capture, Memory, Refused
from orchestrator.evidence import encode_labels, evidence_of
from orchestrator.population import segment
from tests.test_population import world


@pytest.mark.parametrize('ids', [(1, 2), (7, 100000)], ids=['dense-ids', 'sparse-ids'])
@pytest.mark.parametrize('priority,cls', [(None, 'analysis'), ('research', 'research')])
def test_label_workspace_is_admitted_before_decode_and_released_for_retry(world, ids, priority, cls):
    memory = world.captures.memory = Memory({'interactive': 8, 'analysis': 8, 'unsaved': 2, 'research': 8})
    labels = np.zeros((256, 256), np.int32)
    labels[1:5, 1:5] = ids[0]
    labels[2:4, 2:4] = 0
    labels[7, 7] = ids[0]
    labels[10:14, 10:14] = ids[1]
    decodes = []

    class ObservedWire(dict):
        def __getitem__(self, key):
            if key == 'zlib':
                decodes.append(dict(memory.reserved))
            return super().__getitem__(key)

    class Finder:
        corrupt = False

        async def ensure_started(self):
            return self

        async def call(self, *args, **kwargs):
            wire = ObservedWire(encode_labels(labels))
            if self.corrupt:
                wire['zlib'] = b'not compressed labels'
            return {'labels': wire, 'count': 2, 'polygons': []}

    world.gpu_heavy = Finder()
    picture = cv2.imencode('.png', np.full((256, 256, 3), 220, np.uint8))[1].tobytes()
    capture = Capture(999, picture, SimpleNamespace(id=999, width=256, height=256, epoch=0, dx=0., dy=0.,
                                                  fov=None, disc=0., wall_at=1000., tracked=True))
    # Another admitted job leaves enough for the ordinary evidence hold, but not extraction's temporary peak.
    occupied = int((7 if cls == 'research' else 7.8) * MB)
    with memory.reserve(cls, occupied, 'another admitted job'):
        with pytest.raises(Refused):
            asyncio.run(segment(world, capture=capture, priority=priority))
        assert not decodes, 'refusal must precede label decompression and compaction'
        assert not evidence_of(world).items
        assert memory.reserved[cls] == occupied
        assert memory.reserved['unsaved'] == 0

    import zlib

    world.gpu_heavy.corrupt = True
    with pytest.raises(zlib.error):
        asyncio.run(segment(world, capture=capture, priority=priority))
    assert all(n == 0 for n in memory.reserved.values()), 'a decoding failure must release temporary and retained holds'
    assert not evidence_of(world).items
    world.gpu_heavy.corrupt = False
    population = asyncio.run(segment(world, capture=capture, priority=priority))
    assert len(decodes) == 2 and all(read[cls] >= labels.size * 2 * np.dtype(encode_labels(labels)['dtype']).itemsize
                                  for read in decodes), 'the raw decoded bytes and their array copy are admitted'
    actual = np.zeros((256, 256), np.int32)
    for member in population.members.values():
        mask = member.mask
        h, w = mask.bits.shape
        actual[mask.y0:mask.y0+h, mask.x0:mask.x0+w][mask.bits] = member.instance
    np.testing.assert_array_equal(actual, labels)
    assert [(m.instance, int(m.mask.bits.sum())) for m in population.members.values()] == [(ids[0], 13), (ids[1], 16)]
    retained = population.hold.reservation.nbytes if cls == 'research' else 0
    assert memory.reserved[cls] == retained, 'temporary extraction capacity is released after conversion'
    assert memory.peak[cls] <= memory.allowances[cls]
    population.hold.release()
    assert all(n == 0 for n in memory.reserved.values())


def test_disconnected_polygons_remain_admitted_after_segment_settles(world):
    import sys

    memory = world.captures.memory = Memory({'interactive': 8, 'analysis': 8, 'unsaved': 8, 'research': 8})
    tile = np.zeros((4, 4), np.uint32)
    tile[:3, :3] = 7
    labels = np.tile(tile, (16, 16))

    class Finder:
        async def ensure_started(self):
            return self

        async def call(self, *args, **kwargs):
            return {'labels': encode_labels(labels), 'count': 1, 'polygons': []}

    world.gpu_heavy = Finder()
    picture = cv2.imencode('.png', np.full((64, 64, 3), 220, np.uint8))[1].tobytes()
    capture = Capture(999, picture, SimpleNamespace(id=999, width=64, height=64, epoch=0, dx=0., dy=0.,
                                                  fov=None, disc=0., wall_at=1000., tracked=True))
    population = asyncio.run(segment(world, capture=capture, priority='research'))
    try:
        member, = population.members.values()
        assert member.instance == 7 and len(member.polygons) == 256
        polygon_bytes = sys.getsizeof(member.polygons) + sum(
            sys.getsizeof(poly) + sum(sys.getsizeof(point) + sum(map(sys.getsizeof, point)) for point in poly)
            for poly in member.polygons)
        retained = len(capture.jpeg) + member.mask.bits.nbytes + polygon_bytes
        assert memory.reserved['research'] == population.hold.reservation.nbytes >= retained
        assert all(n == 0 for cls, n in memory.reserved.items() if cls != 'research')
        actual = np.zeros_like(labels)
        mask = member.mask
        h, w = mask.bits.shape
        actual[mask.y0:mask.y0+h, mask.x0:mask.x0+w][mask.bits] = member.instance
        np.testing.assert_array_equal(actual, labels)
    finally:
        population.hold.release()
    assert all(n == 0 for n in memory.reserved.values())


def test_saved_geometry_stays_counted_until_retired_and_is_admitted_on_reopen(world, tmp_path):
    import gc
    import sys
    import weakref

    from orchestrator.day.store import DayStore
    from orchestrator.evidence import Evidence
    from tests.test_evidence import saved

    memory = world.captures.memory = Memory({'interactive': 8, 'analysis': 8, 'unsaved': 8, 'research': 8})
    day = DayStore(root=tmp_path / 'sessions', day='2026-10-07')
    world.execution_day = day
    store = world.evidence = Evidence(world, None, day=day)
    tile = np.zeros((4, 4), np.uint32)
    tile[:3, :3] = 7
    labels = np.tile(tile, (16, 16))

    class Finder:
        async def ensure_started(self):
            return self

        async def call(self, *args, **kwargs):
            return {'labels': encode_labels(labels), 'count': 1, 'polygons': []}

    world.gpu_heavy = Finder()
    picture = cv2.imencode('.png', np.full((64, 64, 3), 220, np.uint8))[1].tobytes()
    capture = Capture(999, picture, SimpleNamespace(id=999, width=64, height=64, epoch=0, dx=0., dy=0.,
                                                  fov=None, disc=0., wall_at=1000., tracked=True))
    population = asyncio.run(segment(world, capture=capture, priority='research'))
    assert population.labels() is not None  # the live layer has also materialized its derived label map
    store.root = tmp_path / 'evidence'
    store.schedule_save(population)
    assert saved(population).status == 'saved'
    assert population.capture is None and population.hold is None
    assert population._labels is None
    assert all(m.mask is None for m in population.members.values())

    def geometry_bytes(p):
        return sum(sys.getsizeof(m.polygons) + sum(
            sys.getsizeof(poly) + sum(sys.getsizeof(point) + sum(map(sys.getsizeof, point)) for point in poly)
            for poly in m.polygons) for m in p.members.values())

    minimum = geometry_bytes(population)
    assert sum(memory.reserved.values()) >= minimum, 'saved resident geometry still needs its capacity'
    kept = dict(memory.reserved)
    cls = next(cls for cls, n in kept.items() if n)
    with pytest.raises(Refused):
        memory.reserve(cls, memory.allowances[cls] - kept[cls] + 1, 'another analysis after saving')
    eid = population.id
    snapshot = population.wire()
    store.items.pop(eid)
    assert memory.reserved == kept, 'a caller still using the population keeps its reservation'
    retired = weakref.ref(population)
    del population
    gc.collect()
    assert retired() is None and all(n == 0 for n in memory.reserved.values())

    with memory.reserve('analysis', memory.allowances['analysis'], 'another analysis while opening history'):
        assert store.read_back() == []
        assert 'memory' in store.unavailable[eid]
        assert memory.reserved['analysis'] == memory.allowances['analysis']
        assert all(n == 0 for cls, n in memory.reserved.items() if cls != 'analysis')
    assert store.read_back() == [eid]
    back = store.get(eid)
    assert back.wire() == snapshot
    assert all(m.mask is None for m in back.members.values()) and back.capture is None
    assert sum(memory.reserved.values()) >= minimum
    store.items.pop(eid)
    del back
    gc.collect()
    assert all(n == 0 for n in memory.reserved.values())


@pytest.mark.parametrize('ids,shape', [((1, 2), (256, 256)), ((7, 100000), (256, 256)), (None, None),
                                    ((1, 2), (720, 1280)), ((7, 4000000000), (720, 1280)),
                                    ('islands', (256, 256)), ('islands', (512, 512)), ('output-refusal', (256, 256))],
                         ids=['dense-ids', 'sparse-ids', 'disconnected-256', '720p', '720p-large-native-id',
                              'many-contours-one-id', 'oversized-contour-topology', 'polygon-output-refusal'])
def test_measured_label_conversion_peak_fits_its_admitted_capacity(ids, shape, record_property):
    """A fresh controlled process excludes unrelated test threads/imports from the allocation measurement."""
    import json
    import subprocess
    import sys

    measured = subprocess.run([sys.executable, '-c', '''
import json, sys, tracemalloc
from types import SimpleNamespace
import numpy as np
from orchestrator.capture import Capture, Captures, Memory, Refused
from orchestrator.evidence import Evidence, encode_labels, label_workspace_bytes
from orchestrator.population import members_from
ids = json.loads(sys.argv[1])
shape = json.loads(sys.argv[2])
members_from({'labels': encode_labels(np.ones((2, 2), np.int32))}, Evidence(None, None))
if isinstance(ids, str):
    tile = np.zeros((4, 4), np.uint32)
    tile[:3, :3] = 7
    labels = np.tile(tile, (shape[0] // 4, shape[1] // 4))
elif ids is None:
    labels = np.tile(np.arange(1, 257, dtype=np.int32).reshape(16, 16), (4, 4))
else:
    labels = np.zeros(shape, np.uint32)
    labels[1:5, 1:5] = ids[0]
    labels[10:14, 10:14] = ids[1]
wire = encode_labels(labels)
memory = Memory({'research': 8 if ids == 'output-refusal' else 24})
store = Captures(SimpleNamespace(), memory)
capture = Capture(1, b'', SimpleNamespace(width=labels.shape[1], height=labels.shape[0]))
members, refused, called = {}, False, []
from gpu_worker import worker
real_polygons = worker.mask_to_polygons
def traced_polygons(*a, **k):
    called.append(sum(memory.reserved.values()))
    return real_polygons(*a, **k)
worker.mask_to_polygons = traced_polygons
with store.hold(capture, 'research', 'evidence', 'evidence being made') as retained:
    def admit(nbytes):
        retained.reservation.grow(max(0, nbytes - retained.reservation.nbytes), 'retaining instance masks')
    with memory.reserve('research', label_workspace_bytes(wire, distinct=0), 'label extraction') as working:
        base = label_workspace_bytes(wire, distinct=0)
        def admit_workspace(distinct):
            global base
            base = label_workspace_bytes(wire, distinct=distinct)
            working.grow(max(0, base - working.nbytes))
        def admit_contours(nbytes):
            working.grow(max(0, base + nbytes - working.nbytes))
        tracemalloc.start()
        try:
            members = members_from({'labels': wire}, Evidence(None, None), admit_masks=admit,
                                   admit_workspace=admit_workspace, admit_contours=admit_contours)
        except Refused:
            refused = True
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        admitted = sum(memory.reserved.values())
    retained_bytes = sum(memory.reserved.values())
print(json.dumps({'peakBytes': peak, 'admittedBytes': admitted, 'releasedBytes': sum(memory.reserved.values()),
                  'retainedBytes': retained_bytes, 'currentBytes': current, 'refused': refused,
                  'contourCalls': len(called), 'maskBytes': sum(m.mask.bits.nbytes for m in members.values()),
                  'instances': [[m.instance, int(m.mask.bits.sum())] for m in members.values()],
                  'polygons': sum(len(m.polygons) for m in members.values())}))
''', json.dumps(ids), json.dumps(shape)], text=True, capture_output=True, timeout=30, check=True)
    result = json.loads(measured.stdout)
    record_property('labelMemory', json.dumps(result))
    assert result['peakBytes'] <= result['admittedBytes'], result
    assert result['maskBytes'] <= result['retainedBytes'], result
    assert result['releasedBytes'] == 0
    assert result['currentBytes'] <= result['retainedBytes'], 'derived polygons remain admitted after scratch is released'
    if ids == 'output-refusal':
        assert result['refused'] and result['contourCalls'] == 1, 'polygon growth must refuse within its retained allowance'
    elif ids == 'islands':
        if shape[0] == 512:
            assert result['refused'] and not result['contourCalls'], 'oversized topology refuses before OpenCV allocation'
        else:
            assert not result['refused']
            n = (shape[0] // 4) * (shape[1] // 4)
            assert result['instances'] == [[7, n * 9]]
            assert result['polygons'] == n, 'every disconnected 3x3 component keeps its boundary'
    elif ids is None:
        assert result['maskBytes'] == 614656
        assert result['instances'] == [[i, 16] for i in range(1, 257)]
    else:
        assert result['instances'] == [[ids[0], 16], [ids[1], 16]]


def disconnected_input(world, size, *, sparse=False):
    side = 8 if size == 16 else 16
    labels = np.tile(np.arange(1, side * side + 1, dtype=np.int32).reshape(side, side), (size // side, size // side))
    if sparse:
        labels[labels == side * side] = 100000

    class Finder:
        async def ensure_started(self):
            return self

        async def call(self, *args, **kwargs):
            return {'labels': encode_labels(labels), 'count': side * side, 'polygons': []}

    world.gpu_heavy = Finder()
    picture = cv2.imencode('.png', np.full((size, size, 3), 220, np.uint8))[1].tobytes()
    capture = Capture(999, picture, SimpleNamespace(id=999, width=size, height=size, epoch=0, dx=0., dy=0.,
                                                  fov=None, disc=0., wall_at=1000., tracked=True))
    return capture, labels


@pytest.mark.parametrize('size,mask_bytes', [(16, 5184), (64, 614656)])
@pytest.mark.parametrize('sparse', [False, True], ids=['dense-ids', 'sparse-ids'])
@pytest.mark.parametrize('priority,cls', [(None, 'unsaved'), ('research', 'research')])
def test_disconnected_mask_storage_stays_admitted_after_workspace_release(world, size, mask_bytes, sparse,
                                                                         priority, cls, record_property):
    memory = world.captures.memory = Memory({'interactive': 4, 'analysis': 4, 'unsaved': 4, 'research': 4})
    capture, labels = disconnected_input(world, size, sparse=sparse)
    population = asyncio.run(segment(world, capture=capture, priority=priority))
    try:
        actual_bytes = sum(m.mask.bits.nbytes for m in population.members.values())
        assert population.hold.reservation.nbytes >= actual_bytes + 1024 * len(population.members), (
            'settling evidence must retain its instance records as well as mask storage')
        assert actual_bytes == mask_bytes
        required = actual_bytes + len(capture.jpeg)
        record_property('retainedMaskBytes', actual_bytes)
        record_property('retainedHoldBytes', population.hold.reservation.nbytes)
        assert population.hold.reservation.nbytes >= required, 'retained arrays need their own capacity after workspace release'
        assert memory.reserved[cls] >= required
        assert all(n == 0 for other, n in memory.reserved.items() if other != cls)
        actual = np.zeros_like(labels)
        for member in population.members.values():
            mask = member.mask
            h, w = mask.bits.shape
            actual[mask.y0:mask.y0+h, mask.x0:mask.x0+w][mask.bits] = member.instance
        np.testing.assert_array_equal(actual, labels)
    finally:
        population.hold.release()
    assert all(n == 0 for n in memory.reserved.values())


@pytest.mark.parametrize('sparse', [False, True], ids=['dense-ids', 'sparse-ids'])
@pytest.mark.parametrize('priority,cls', [(None, 'unsaved'), ('research', 'research')])
@pytest.mark.parametrize('failure', [MemoryError, asyncio.CancelledError], ids=['allocation-failure', 'cancelled'])
def test_mask_growth_refuses_before_materialization_and_allocation_failure_releases_it(world, monkeypatch, sparse,
                                                                                     priority, cls, failure):
    from orchestrator import evidence

    # The successful retry now also admits 256 measurement rows and their revision-zero copies.
    # Keep the same 0.4 MiB free during the pre-materialization refusal probe.
    memory = world.captures.memory = Memory({'interactive': 4, 'analysis': 4, 'unsaved': 4, 'research': 4})
    capture, labels = disconnected_input(world, 64, sparse=sparse)
    made = []
    fail = False
    real_mask = evidence.Mask

    def observed_mask(*args, **kwargs):
        made.append(memory.reserved[cls])
        assert memory.reserved[cls] >= 614656 + len(capture.jpeg), 'all mask rectangles need capacity before materialization'
        if fail:
            raise failure('controlled output-allocation failure')
        return real_mask(*args, **kwargs)

    monkeypatch.setattr(evidence, 'Mask', observed_mask)
    occupied = int(3.6 * MB)
    # Workspace and the ordinary evidence hold fit; the aggregate disconnected rectangles do not.
    with memory.reserve(cls, occupied, 'another admitted job'):
        with pytest.raises(Refused):
            asyncio.run(segment(world, capture=capture, priority=priority))
        assert not made, 'no output mask may be materialized before aggregate retained capacity is granted'
        assert memory.reserved[cls] == occupied
        assert sum(memory.reserved.values()) == occupied
        assert memory.refused[cls] == 1
        assert not evidence_of(world).items

    fail = True
    with pytest.raises(failure, match='controlled output-allocation failure'):
        asyncio.run(segment(world, capture=capture, priority=priority))
    assert all(n == 0 for n in memory.reserved.values())
    assert not evidence_of(world).items

    fail = False
    made.clear()
    population = asyncio.run(segment(world, capture=capture, priority=priority))
    try:
        assert len(made) == len(population.members) == 256
        assert population.hold.reservation.nbytes >= 614656 + len(capture.jpeg)
        assert sum(m.mask.bits.nbytes for m in population.members.values()) == 614656
        actual = np.zeros_like(labels)
        for member in population.members.values():
            mask = member.mask
            h, w = mask.bits.shape
            actual[mask.y0:mask.y0+h, mask.x0:mask.x0+w][mask.bits] = member.instance
        np.testing.assert_array_equal(actual, labels)
    finally:
        population.hold.release()
    assert all(n == 0 for n in memory.reserved.values())


@pytest.mark.parametrize('sparse', [False, True], ids=['dense-ids', 'sparse-ids'])
def test_bounds_planning_fits_workspace_even_when_every_pixel_has_its_own_id(sparse, record_property):
    """Probe stops at the retained-admission boundary, before any per-instance output is allocated."""
    import json
    import subprocess
    import sys

    measured = subprocess.run([sys.executable, '-c', '''
import json, sys, tracemalloc
from types import SimpleNamespace
import numpy as np
from orchestrator.capture import Capture, Captures, Memory
from orchestrator.evidence import Evidence, encode_labels, label_workspace_bytes
from orchestrator.population import members_from
members_from({'labels': encode_labels(np.ones((2, 2), np.int32))}, Evidence(None, None))
labels = np.arange(1, 4097, dtype=np.int32).reshape(64, 64)
if sys.argv[1] == 'True': labels[-1, -1] = 100000
wire = encode_labels(labels)
memory = Memory({'analysis': 1, 'unsaved': 1})
store = Captures(SimpleNamespace(), memory)
capture = Capture(1, b'', SimpleNamespace(width=64, height=64))
result = {}
class PlannedOnly(Exception): pass
def admit(nbytes):
    _, peak = tracemalloc.get_traced_memory()
    result.update(plannedMaskBytes=nbytes, planningPeakBytes=peak, admittedWorkspaceBytes=memory.reserved['analysis'])
    raise PlannedOnly()
with store.hold(capture, 'unsaved', 'evidence', 'evidence being made'):
    with memory.reserve('analysis', label_workspace_bytes(wire), 'label bounds planning'):
        tracemalloc.start()
        try:
            members_from({'labels': wire}, Evidence(None, None), admit_masks=admit)
        except PlannedOnly:
            pass
        finally:
            tracemalloc.stop()
result['releasedBytes'] = sum(memory.reserved.values())
print(json.dumps(result))
''', str(sparse)], text=True, capture_output=True, timeout=30, check=True)
    result = json.loads(measured.stdout)
    record_property('boundsPlanning', json.dumps(result))
    assert result['plannedMaskBytes'] == 4096 * (1 + 1024), 'masks and their retained instance records'
    assert result['planningPeakBytes'] <= result['admittedWorkspaceBytes'], result
    assert result['releasedBytes'] == 0
