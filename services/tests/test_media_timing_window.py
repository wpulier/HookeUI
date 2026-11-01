"""Synthetic source-window admission; real throughput is qualified by the sim."""
import asyncio
from types import SimpleNamespace

import pytest

from sim import media_timing
from sim.harness import Outcome


class Stack:
    start_frame = 2650

    def __init__(self, *, total=6683, fps=30., observed=2654, seek_ok=True):
        self.status = dict(totalFrames=total, fps=fps, latestSourceFrame=observed, worldFrame=observed)
        self.status.update(video='/synthetic/recording.mp4', captureStarts=[dict(at=1., sourceFrame=2650)])
        self.seek_ok = seek_ok
        self.seeks = []

    async def sim_status(self):
        return dict(self.status)

    async def seek(self, frame):
        self.seeks.append(frame)
        return self.seek_ok


def test_late_prior_scenario_cannot_choose_the_media_timing_window():
    stack = Stack()
    out = Outcome('synthetic')
    window = asyncio.run(media_timing.prepare(stack, out))
    assert stack.seeks == [2650]
    assert window['sourceFrame'] == 2654
    assert out.passed


def test_requested_start_near_end_uses_beginning_before_measurement():
    stack = Stack(observed=4)
    stack.start_frame = 6353
    out = Outcome('synthetic')
    assert asyncio.run(media_timing.prepare(stack, out))['sourceFrame'] == 4
    assert stack.seeks == [0]


@pytest.mark.parametrize('kwargs', [dict(total=300), dict(fps=0), dict(fps=float('nan')),
                                   dict(seek_ok=False), dict(observed=6650)])
def test_unqualified_source_refuses_without_sampling_until_fast(kwargs):
    stack = Stack(**kwargs)
    out = Outcome('synthetic')
    assert asyncio.run(media_timing.prepare(stack, out)) is None
    assert not out.passed
    assert len(stack.seeks) <= 1


def test_crossing_loop_boundary_stays_a_failure(monkeypatch):
    monkeypatch.setattr(media_timing, 'time', SimpleNamespace(monotonic=lambda: 105.))
    out = Outcome('synthetic')
    window = dict(started=100., sourceFrame=6600, worldFrame=6602, totalFrames=6683, fps=30.)
    stack = Stack(observed=6660)
    window['identity'] = media_timing.source_identity(stack.status)
    asyncio.run(media_timing.finish(stack, window, out))
    assert not out.passed
    assert out.observations[-1]['elapsedS'] == 5.


@pytest.mark.parametrize('change', ['capture', 'source', 'wrap', 'missing'])
def test_unexpected_ending_state_fails_even_inside_elapsed_bound(monkeypatch, change):
    monkeypatch.setattr(media_timing, 'time', SimpleNamespace(monotonic=lambda: 101.))
    stack = Stack()
    window = dict(started=100., sourceFrame=2654, worldFrame=2654, totalFrames=6683,
                  fps=30., identity=media_timing.source_identity(stack.status))
    if change == 'capture': stack.status['captureStarts'] = [dict(at=100., sourceFrame=2654)]
    if change == 'source': stack.status['video'] = '/synthetic/another.mp4'
    if change == 'wrap': stack.status.update(latestSourceFrame=10, worldFrame=11)
    if change == 'missing': stack.status.pop('captureStarts')
    out = Outcome('synthetic')
    asyncio.run(media_timing.finish(stack, window, out))
    assert not out.passed


def test_status_delivery_delay_is_counted_conservatively(monkeypatch):
    clock = [100.]
    monkeypatch.setattr(media_timing, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    stack = Stack()
    original = stack.sim_status
    calls = [0]

    async def delayed_status():
        calls[0] += 1
        if calls[0] == 2: clock[0] += 2.
        return await original()

    stack.sim_status = delayed_status
    window = asyncio.run(media_timing.prepare(stack, Outcome('synthetic')))
    assert window['started'] == 100.
    assert clock[0] == 102.


def test_media_scenario_does_not_start_work_after_source_refusal(monkeypatch):
    from sim import harness

    async def ready(*args, **kwargs):
        return True

    async def refuse(stack, out):
        out.check('source window', False, 'short recording')
        return None

    monkeypatch.setattr(harness, 'prepare_timing', ready)
    monkeypatch.setattr(media_timing, 'prepare', refuse)
    # No viewer or HTTP transport is supplied: admission must stop before using either.
    out = asyncio.run(harness.scenario_media_load(SimpleNamespace(), None))
    assert not out.passed
