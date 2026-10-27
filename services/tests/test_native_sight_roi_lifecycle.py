"""TEST-only lifecycle controls over the actual Sight/native ROI workers at 8a51.

Synthetic JPEG ingress and canned external Sight replies reuse the seven-case
Rig unchanged. Only external codecs receive a barrier/failure here. Real App,
Capture/Hold/Memory, Ticket, Registry, preparation pool/Job/Future and native
publication own execution. Retaining a real failed Future is deliberate: room
must not be released while its exception still owns decoded pixel buffers.
These are local synthetic backend controls, not browser or model evidence.
"""
import asyncio
import copy
import gc
import sys
import threading
import traceback
import weakref

import numpy as np
import pytest
from PIL import Image

from orchestrator import sight as sight_module
from orchestrator.brain.registry import ToolContext
from orchestrator.capture import captures
from orchestrator.ticket import issue, using
from tests.test_comparison_retention import until
from tests.test_native_requested_goal_binding import close_app, inline_report
from tests.test_native_sight_roi import Rig
from tests.test_presentation_delivery import held
from tests.test_workspace_actions_app import WORKSPACE
from tests.waiting import HANG_S


@pytest.fixture
def rig(tmp_path, monkeypatch):
    instance = Rig(tmp_path, monkeypatch)
    yield instance
    instance.app.sight.stop()
    instance.app.services.presentations.reset_all()
    instance.app.services.prepared_sources._pool.shutdown(wait=True)
    close_app(instance.app)


class CodecBarrier:
    """Only weak pixel observations; the actual CPU stack owns the buffers."""
    def __init__(self, fail):
        self.fail = fail
        self.entered, self.release = threading.Event(), threading.Event()
        self.pixels = []
        self.phase = 'waiting for codec entry'

    def wait(self, pixels):
        self.pixels.append(weakref.ref(pixels))
        self.entered.set()
        if not self.release.wait(HANG_S):
            stack = ''.join(traceback.format_stack(sys._current_frames()[threading.main_thread().ident]))
            raise AssertionError(f'SETUP: external codec barrier was not released during {self.phase}\n{stack}')
        if self.fail:
            raise OSError('synthetic external codec failure')

    def sight_encoder(self, monkeypatch):
        encode = sight_module.encode_crop

        def external(image, crop, **kwargs):
            self.wait(image)  # the actual Capture.bgr(), never a substitute decode
            return encode(image, crop, **kwargs)

        monkeypatch.setattr(sight_module, 'encode_crop', external)

    def native_encoder(self, monkeypatch):
        save = Image.Image.save

        def external(image, stream, *args, **kwargs):
            # A codec-local pixel view owns its byte buffer. Closing the caller's
            # PIL Image cannot free this deeper frame after a codec raises.
            pixels = np.frombuffer(image.tobytes(), np.uint8)
            self.wait(pixels)
            return save(image, stream, *args, **kwargs)

        monkeypatch.setattr(Image.Image, 'save', external)

    def freed(self):
        gc.collect()
        assert self.pixels, 'SETUP: the real worker never entered the external codec'
        assert all(ref() is None for ref in self.pixels), (
            'LIFECYCLE: a settled failed Future retains codec pixels after counted room was released')


def sight_preparation(task):
    """Read the real shielded wrapper and decode Hold; install neither."""
    coroutine = task.get_coro()
    while coroutine is not None:
        frame = getattr(coroutine, 'cr_frame', None)
        if (frame is not None and frame.f_code.co_name == '_look_held'
                and frame.f_code.co_filename.endswith('/orchestrator/sight.py')):
            future = frame.f_locals['future']
            assert asyncio.isfuture(future)
            return future, frame.f_locals['decode']
        coroutine = getattr(coroutine, 'cr_await', getattr(coroutine, 'gi_yieldfrom', None))
    raise AssertionError('SETUP: no actual Sight preparation Future at the codec barrier')


async def newer_camera(rig, original, scale):
    rig.app.hub.scale.set_objective(10, via='synthetic later operator')
    newer = await rig.feed(replacement=True)
    assert newer.id != original.id
    assert newer.scale['objective'] == 10
    assert original.scale == scale and scale['objective'] == 40


@pytest.mark.parametrize('mode', ['failure', 'canceled-failure', 'canceled', 'stale'])
def test_sight_cpu_settlement_and_failed_future_buffer_ownership(rig, monkeypatch, mode):
    async def go():
        app = rig.app
        original = await rig.feed()
        scale = copy.deepcopy(original.scale)
        memory = captures(app.hub).memory
        baseline = held(memory)
        codec = CodecBarrier(fail=mode in {'failure', 'canceled-failure'})
        codec.sight_encoder(monkeypatch)
        pending = asyncio.create_task(app.sight.look())
        future = None
        try:
            await until(codec.entered.is_set, 'SETUP: actual Sight CPU encoder did not start')
            codec.phase = 'checking Sight buffer ownership'
            future, decode = sight_preparation(pending)
            reservation = decode.reservation
            assert decode.capture is original and decode.kind == 'look'
            assert not decode.released and not reservation.released
            assert not future.done() and held(memory) > baseline
            assert original._covered['bgr'] > 0 and original._bgr is codec.pixels[0]()
            codec.phase = 'accepting replacement camera frame'
            await newer_camera(rig, original, scale)
            codec.phase = 'checking cancellation or stale publication'
            if mode.startswith('canceled'):
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                # Canceled asyncio await is not actual CPU settlement.
                assert not future.done() and not future.cancelled()
                assert not decode.released and not reservation.released
                assert held(memory) >= baseline + reservation.nbytes
                assert original._covered['bgr'] > 0 and original._bgr is codec.pixels[0]()
                assert app.sight.view is None and app.sight._hold is None
            elif mode == 'stale':
                app.sight.stop()  # actual publication generation invalidation
                assert not future.done() and held(memory) > baseline
            codec.release.set()
            if mode == 'failure':
                with pytest.raises(OSError, match='synthetic external codec failure'):
                    await pending
            elif mode == 'stale':
                assert await pending is None
            await until(lambda: future.done() and held(memory) == baseline,
                        'Sight released counted room before/after the wrong settlement')
            assert not future.cancelled()
            if codec.fail:
                assert isinstance(future.exception(), OSError)
            else:
                assert future.exception() is None
            assert original._covered['bgr'] == 0 and original._bgr is None
            assert decode.released and reservation.released
            assert original.scale == scale
            assert app.sight.view is None and app.sight._hold is None
            if mode.startswith('canceled') or codec.fail:
                assert not rig.sight_sends
            else:
                assert len(rig.sight_sends) == 1
            # Keep the actual Future alive through collection and the assertion.
            codec.freed()
            assert future.done()
        finally:
            codec.release.set()
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            if future is not None:
                await until(future.done, 'SETUP cleanup: actual Sight CPU did not settle')

    asyncio.run(go())


def test_sight_preparation_done_before_it_is_awaited_is_released_when_the_look_returns(rig):
    """CPython 3.13.12+ (gh-105836) hands back an executor Future already done when the work beat
    its wrapping (the macos-15 runner): the await then never yields to the done callback."""
    async def go():
        app = rig.app
        await rig.feed()
        loop = asyncio.get_running_loop()
        settled = []

        def ran(executor, work, *args):
            assert executor is None and work.__qualname__.endswith('_look_held.<locals>.prepare')
            done = loop.create_future()
            done.set_result(work(*args))  # the real decode and encodes, finished before the await
            settled.append(done)
            return done

        loop.run_in_executor = ran
        try:
            view, original = await rig.observe()
        finally:
            del loop.run_in_executor
        assert settled and settled[0].done()
        assert app.sight.view is view and app.sight._hold.capture is original
        # Only the published Sight view's own room is left: the look's decode was let go before it returned.
        assert held(captures(app.hub).memory) == app.sight._hold.reservation.nbytes
        assert original._covered['bgr'] == 0 and original._bgr is None

    asyncio.run(go())


def start_original_roi(rig, original):
    """Public Registry entry, using the real original request and accepted pin."""
    app = rig.app
    ticket = issue(app.hub, 'turn', asked=app.day.here(), channel='chat')
    with using(ticket):
        selection = app.sight.select(ticket)
        assert selection.capture is original and ticket.held(original.id) is original
        pin = app.hub.screen.pin(rig.studio, request_id=ticket.admission.request_id,
                                 inline=inline_report(rig.studio))
        ctx = ToolContext('chat', 1, original.id, 'Open the original second Sight region.',
                          asked=ticket.asked, step=1, seen_view=selection.view,
                          sight_selection=selection, admission=ticket.admission,
                          client=rig.studio, workspace_id=WORKSPACE,
                          request_snapshot=pin, request_id=ticket.admission.request_id)
        run = app.brain.registry.start('present', {'do': 'open', 'source': {'sight': 's2'}}, ctx)
        assert run.task is not None and run.occurrence.request_id == ticket.admission.request_id
        ticket.track(run.task)
        ticket.seal()
    return ticket, run


@pytest.mark.parametrize('mode', ['failure', 'canceled-failure', 'canceled', 'stale'])
def test_native_roi_cpu_settlement_and_failed_future_buffer_ownership(rig, monkeypatch, mode):
    async def go():
        app = rig.app
        await rig.feed()
        view, original = await rig.observe()
        scale = copy.deepcopy(original.scale)
        store = app.services.prepared_sources
        memory = captures(app.hub).memory
        baseline = held(memory)  # the actual published Sight JPEG stays held
        initial_items = copy.deepcopy(rig.studio.items)
        codec = CodecBarrier(fail=mode in {'failure', 'canceled-failure'})
        codec.native_encoder(monkeypatch)
        ticket, run = start_original_roi(rig, original)
        future = None
        try:
            await until(codec.entered.is_set, 'SETUP: actual native ROI CPU encoder did not start')
            codec.phase = 'checking native ROI buffer ownership'
            assert len(store._jobs) == 1 and store._pending == 1
            future, job = next(iter(store._jobs.items()))
            reservation, capture_hold = job.reservation, job.capture_hold
            assert future.running() and not future.done()
            assert reservation is not None and not reservation.released
            assert capture_hold.capture is original and not capture_hold.released
            assert original.id == view.frame_id and original.scale == scale
            assert held(memory) >= baseline + reservation.nbytes
            codec.phase = 'accepting replacement camera frame'
            await newer_camera(rig, original, scale)
            codec.phase = 'checking cancellation or stale publication'
            if mode.startswith('canceled'):
                run.task.cancel()  # the actual Registry task awaiting preparation
                with pytest.raises(asyncio.CancelledError):
                    await run.task
                await until(lambda: ticket.closed, 'actual canceled request did not close')
                assert job.stopped.is_set()
            elif mode == 'stale':
                ticket.cancel('synthetic original request became stale')
            if mode != 'failure':
                assert not future.done() and not future.cancelled()
                assert job.reservation is reservation and not reservation.released
                assert job.capture_hold is capture_hold and not capture_hold.released
                assert capture_hold.capture is original and original.scale == scale
                assert held(memory) >= baseline + reservation.nbytes
            assert not store._pins and not store._sources
            assert not run.outputs and not rig.studio.rendered_commands
            codec.release.set()
            if not mode.startswith('canceled'):
                await run.task
                assert run.ok is False
                code = 'REQUEST_STALE' if mode == 'stale' else 'SOURCE_UNAVAILABLE'
                assert run.outcome['failure']['code'] == code
            await until(lambda: future.done() and not store._jobs and store._pending == 0
                        and ticket.closed and held(memory) == baseline,
                        'native ROI did not settle its actual Job/Hold/reservation')
            assert not future.cancelled() and future.exception() is not None
            assert job.reservation is None and reservation.released
            assert job.capture_hold is None and capture_hold.released
            assert original.scale == scale
            assert app.sight.view is view and app.sight._hold.capture is original
            assert not store._pins and not store._sources
            assert not run.outputs and not rig.studio.rendered_commands
            assert rig.studio.items == initial_items
            codec.freed()  # concurrent Future stays alive; no exception cleanup by TEST
            assert future.done()
        finally:
            codec.release.set()
            if not run.task.done():
                run.task.cancel()
            await asyncio.gather(run.task, return_exceptions=True)
            if future is not None:
                await until(lambda: future.done() and not store._jobs and store._pending == 0,
                            'SETUP cleanup: actual native ROI CPU did not settle')
            ticket.close()

    asyncio.run(go())
