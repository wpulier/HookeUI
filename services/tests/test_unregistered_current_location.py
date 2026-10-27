"""Synthetic consumer contract: equal epochs do not register a new current bitmap.

Actual FrameInfo/Scene/Captures/Identity/place and registered where_is/Ticket readers.
World supplies external JPEGs and declared geometry, not actual MotionTracker or
Hub analysis. No producer-loss, simulator causality, model prose or browser proof.
"""
import asyncio
import copy
import math

import cv2
import numpy as np
import pytest

from orchestrator.brain import toolbox
from orchestrator.brain.registry import ToolContext
from orchestrator.brain.toolbox import Services
from orchestrator.capture import RUN
from orchestrator.fov import FieldOfView
from orchestrator.hub import FrameInfo
from orchestrator.identity import Identity, hhmmss, outline_node, where_words
from orchestrator.scene import SceneObject
from orchestrator.slidemap import place
from orchestrator.ticket import issue, using
from tests.test_apply import World


DAY = '2026-10-09'
FOV = FieldOfView(640.0, 360.0, 300.0, 1.0)


def location_world(*, tracked, outside=False):
    world = World()
    original = FrameInfo(1, 1280, 720, 10.0, -20.0, 7, True, True, FOV,
                         1001.0, seq=111, boot='synthetic-location-bridge')
    world.infos[1] = world.latest_info = original
    obj = world.scene.add(SceneObject(
        id=world.scene.new_id(), kind='highlight', source='click', label='stoma',
        polygons=[[[620, 340], [660, 340], [660, 380], [620, 380]]],
        anchor_frame=original.id, anchor_epoch=original.epoch,
        anchor_offset=(original.dx, original.dy), origin=original.id, created_at=original.wall_at))
    capture = world.captures.take(original.id)
    world.frame(0.0, 0.0)
    last_good = FrameInfo(2, 1280, 720, 610.0 if outside else 60.0, -20.0 if outside else -80.0,
                          7, True, True, FOV, 1002.0, seq=112, boot=original.boot)
    world.infos[last_good.id] = world.latest_info = last_good
    world.frame(0.0, 0.0)
    current = FrameInfo(3, 1280, 720, last_good.dx, last_good.dy,
                        7, tracked, True, FOV, 1003.0, seq=113, boot=original.boot)
    # A new external bitmap with its own frame identity. Explicit false retains
    # the last declared offset/epoch, as _lost_frame can; it is not a measured move.
    pixels = np.full((720, 1280), 91 if tracked else 17, np.uint8)
    cv2.circle(pixels, (640, 360), 100, 180, -1)
    ok, encoded = cv2.imencode('.jpg', pixels)
    assert ok
    world.ring[current.id] = encoded.tobytes()
    world.infos[current.id] = world.latest_info = current
    assert current.id != original.id and current.seq != original.seq
    assert current.id != last_good.id and current.tracked is tracked
    assert (current.dx, current.dy, current.epoch) == (last_good.dx, last_good.dy, last_good.epoch)
    assert world.ring[current.id] != capture.jpeg
    assert current.epoch == obj.anchor_epoch == 7
    assert obj.anchor_frame == capture.id == original.id
    assert obj.anchor_offset == (10.0, -20.0) and capture.jpeg == world.ring[original.id]
    return world, obj, original, current, capture


@pytest.mark.parametrize('outside', [False, True], ids=['in-view', 'out-of-view'])
def test_registered_current_translation_keeps_exact_numeric_place(outside):
    world, obj, _, current, _ = location_world(tracked=True, outside=outside)
    assert current.tracked is True
    found = place(obj, current)
    assert found is not None
    assert (found.dx, found.dy) == ((600.0, 0.0) if outside else (50.0, -60.0))
    assert found.fields == pytest.approx(1.0 if outside else math.hypot(50, 60) / 600)
    assert found.in_view is (not outside)


@pytest.mark.parametrize('outside', [False, True], ids=['retained-near-offset', 'retained-far-offset'])
def test_explicit_unregistered_current_frame_cannot_certify_place(outside):
    world, obj, _, current, _ = location_world(tracked=False, outside=outside)
    assert current.tracked is False and current.epoch == obj.anchor_epoch
    assert place(obj, current) is None
    assert obj.anchor_frame == 1 and obj.anchor_offset == (10.0, -20.0)


def observed_identity(world, obj, original, tmp_path, mode):
    identity = Identity(tmp_path / 'identity', today=lambda: DAY, hub=world)
    node = outline_node(obj.id)
    individual = identity.make([node], why='synthetic location consumer')
    identity.observe(node, {
        'at': original.wall_at, 'frame': original.id, 'run': RUN, 'boot': original.boot,
        'seq': original.seq, 'epoch': original.epoch, 'slide': [630.0, 380.0],
        'producer': 'synthetic original outline', 'measure': {'areaPx': 1600.0},
        'basis': {'frame': [1280, 720], 'discPx': 300.0, 'umPerPx': None, 'scale': None}})
    if mode == 'out-of-view':
        world.scene.update(obj.id, track='out_of_view')
    elif mode == 'history-only':
        assert world.scene.remove(obj.id)
        assert identity.outline(node) is None
    else:
        assert obj.track == 'tracking'
    assert identity.nodes_of(individual) == {node}
    assert identity.lost == {}  # No epoch departure/loss time or own-pixel failure was observed.
    return identity, individual


@pytest.mark.parametrize('mode', ['tracking', 'out-of-view', 'history-only'])
def test_registered_identity_keeps_useful_extant_and_dated_history_location(tmp_path, mode):
    world, obj, original, current, _ = location_world(tracked=True, outside=mode == 'out-of-view')
    identity, individual = observed_identity(world, obj, original, tmp_path, mode)
    history = copy.deepcopy(identity.history(individual))
    words = where_words(world, identity, individual)
    if mode == 'out-of-view':
        assert words.startswith('about 1.0 field to the right of the view')
        assert 'out of view' in words
    else:
        assert words.startswith('in view now')
    if mode == 'history-only':
        assert hhmmss(original.wall_at) in words and 'not checked by its own pixels' in words
    else:
        assert "slide's measured movement" in words
    assert current.tracked is True and identity.history(individual) == history


@pytest.mark.parametrize('mode', ['tracking', 'out-of-view', 'history-only'])
def test_unregistered_identity_cannot_borrow_same_epoch_or_history_offsets(tmp_path, mode):
    world, obj, original, current, capture = location_world(tracked=False, outside=mode == 'out-of-view')
    identity, individual = observed_identity(world, obj, original, tmp_path, mode)
    history = copy.deepcopy(identity.history(individual))
    path = tmp_path / 'identity' / DAY / 'events.jsonl'
    journal = path.read_bytes()
    words = where_words(world, identity, individual)
    assert current.tracked is False and current.epoch == original.epoch
    assert not words.startswith('in view now') and not words.startswith('about 1.0 field')
    assert "can't tell" in words or 'not known' in words
    assert 'continuity was lost at' not in words and 'jumped' not in words and 'moved too fast' not in words
    assert 'own pixels have not matched' not in words
    if mode == 'history-only':
        assert hhmmss(original.wall_at) in words  # The dated observation still has utility.
    assert identity.history(individual) == history and path.read_bytes() == journal
    assert identity.lost == {} and obj.lost_since is None
    assert capture.id == original.id and capture.jpeg == world.ring[original.id]


async def registered_where(world, obj, original, current, ref=None):
    measurements = []
    registry = toolbox.build(Services(hub=world, log=measurements.append))
    assert registry.tools['where_is'].kind == 'query'
    ticket = issue(world, 'turn', channel='chat')
    try:
        ticket.hold(world.captures.take(original.id))
        ticket.hold(world.captures.take(current.id))
        context = ToolContext('chat', 19, current.id, 'Where is the original stoma?')
        with using(ticket):
            run = registry.start('where_is', {'object': obj.id if ref is None else ref}, context)
            ticket.track(run.task)
            await run.task
        assert run.ok is True and run.error is None, run.summary
        assert context.admission is ticket.admission and ticket.admission.used == 0
        assert ticket.held(original.id).jpeg == world.ring[original.id]
        assert ticket.held(current.id).jpeg == world.ring[current.id]
        rows = [row for row in measurements if row.get('kind') == 'measurement' and row.get('what') == 'place']
        assert len(rows) == 1
        record = rows[0]
        assert record['frameId'] == current.id and record['turnId'] == 19
        assert record['measuredOn'] == {'frameId': current.id, 'seq': current.seq,
                                        'boot': current.boot, 'wallAt': current.wall_at}
        assert run.values['place']['places'] == record['places']
        assert run.values['place']['measuredOn'] == record['measuredOn']
        return run, record
    finally:
        ticket.close()


@pytest.mark.parametrize('outside', [False, True], ids=['in-view', 'out-of-view'])
def test_real_where_is_receipt_keeps_registered_numeric_measurement(outside):
    world, obj, original, current, _ = location_world(tracked=True, outside=outside)
    run, record = asyncio.run(registered_where(world, obj, original, current))
    assert record['places'] == [{
        'objectId': obj.id, 'label': 'stoma', 'known': True,
        'dxPx': 600.0 if outside else 50.0, 'dyPx': 0.0 if outside else -60.0,
        'fields': 1.0 if outside else 0.13, 'inView': not outside, 'um': None}]
    assert run.values['place']['places'][0]['known'] is True


@pytest.mark.parametrize('outside', [False, True], ids=['retained-near-offset', 'retained-far-offset'])
def test_real_where_is_receipt_marks_unregistered_location_unknown_without_numbers(outside):
    world, obj, original, current, _ = location_world(tracked=False, outside=outside)
    run, record = asyncio.run(registered_where(world, obj, original, current))
    assert record['places'] == [{'objectId': obj.id, 'label': 'stoma', 'known': False}]
    assert set(run.values['place']['places'][0]) == {'objectId', 'label', 'known'}
    assert 'jumped' not in run.summary and 'moved too fast' not in run.summary
    assert obj.anchor_frame == original.id and obj.anchor_epoch == current.epoch


def test_same_name_summary_and_measurements_share_one_bounded_known_first_cohort():
    world, obj, original, current, _ = location_world(tracked=True)
    added = []
    for n in range(4):
        added.append(world.scene.add(SceneObject(
            id=world.scene.new_id(), kind='highlight', source='click', label='stoma',
            polygons=[[[620, 340], [660, 340], [660, 380], [620, 380]]],
            anchor_frame=original.id, anchor_epoch=99, anchor_offset=(10.0, -20.0),
            origin=original.id, created_at=1004.0 + n)))
    assert len(world.scene.objects) == 5 and current.tracked is True
    assert place(obj, current) is not None and all(place(other, current) is None for other in added)
    run, record = asyncio.run(registered_where(world, obj, original, current, ref='stoma'))
    expected = [obj.id, added[3].id, added[2].id, added[1].id]
    assert [row['objectId'] for row in record['places']] == expected
    assert [row['known'] for row in record['places']] == [True, False, False, False]
    assert all(f'({object_id})' in run.summary for object_id in expected)
    assert f'({added[0].id})' not in run.summary and '1 more' in run.summary
    assert record['places'][0]['dxPx'] == 50.0 and record['places'][0]['dyPx'] == -60.0
    assert record['how'] != 'current-relative positions unavailable'
    assert set(world.scene.objects) == {obj.id, *(other.id for other in added)}
