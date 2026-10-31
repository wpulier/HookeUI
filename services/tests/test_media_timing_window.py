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
    media_timing.finish(window, out)
    assert not out.passed
    assert out.observations[-1]['elapsedS'] == 5.


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
