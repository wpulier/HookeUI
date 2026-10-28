"""A voice session's provider resources and allowance end together (voice/route.py, openai_live.py; Codex's diagnosis of
89e834b5, points 3 and 4): a renewal that fails or raises closes the provider whatever the browser does, a start that
fails or is cancelled after acquiring something closes it before settling, and a created WebRTC session whose sideband
can't attach is ended at the provider. Stand-in sessions and providers: nothing is sent."""
import asyncio
import json

import pytest

from research_worker import api_usage
from orchestrator.voice import route
from tests.test_voice_route import FakeSession, app_with
from tests.waiting import HANG_S, until, until_async


@pytest.fixture
def ledger(tmp_path):
    one = api_usage.UsageLedger(tmp_path / "usage")
    api_usage.configure(one)
    one.set_cap(5.0)
    yield one
    api_usage.configure(None)
    one.close()


class Lingering(FakeSession):
    """A provider session that stays open until it is closed (its events wait)."""

    async def events(self):
        while not self.closed:
            await asyncio.sleep(0.01)
        return
        yield


@pytest.mark.parametrize("failure", ["refused", "raises"])
def test_a_renewal_that_fails_closes_the_provider_whatever_the_browser_does(monkeypatch, ledger, failure):
    client, _ = app_with(monkeypatch)
    monkeypatch.setattr(route, "OpenAILiveSession", Lingering)
    monkeypatch.setattr(api_usage, "RENEW_S", 0.05)

    real, calls = api_usage.VoiceUsage.renew, []

    def renew(self):
        calls.append(1)
        if len(calls) == 1:
            return real(self)  # its first minutes, held as it opens
        if failure == "raises":
            raise RuntimeError("database is locked")
        return False

    monkeypatch.setattr(api_usage.VoiceUsage, "renew", renew)
    with client.websocket_connect("/ws/voice", headers={"origin": "http://127.0.0.1:8770"}) as ws:
        assert json.loads(ws.receive_text())["kind"] == "model"
        said = json.loads(ws.receive_text())
        assert said["kind"] == "error" and "monthly cap" in said["data"]["message"]
    assert Lingering.made[0].closed, "the provider's session is closed by the app, not left to the browser"
    until(lambda: ledger.month()["inFlightUsd"] == 0)  # the route's own ending runs on the server's side of the test client
    assert ledger.month()["inFlightUsd"] == 0, "its hold released when it ended"


class FailsToStart(FakeSession):
    async def start(self, offer_sdp=None):
        self.acquired = True  # a connection opened before what failed
        raise ConnectionError("session.start refused")


def test_a_start_that_fails_after_acquiring_closes_it_before_its_allowance_is_settled(monkeypatch, ledger):
    client, _ = app_with(monkeypatch)
    monkeypatch.setattr(route, "OpenAILiveSession", FailsToStart)
    order = []
    real_finish = api_usage.VoiceUsage.finish

    def finish(self, confirmed=True, why=""):
        order.append("settled" if FailsToStart.made[0].closed else "settled before closing")
        real_finish(self, confirmed, why)

    monkeypatch.setattr(api_usage.VoiceUsage, "finish", finish)
    with client.websocket_connect("/ws/voice", headers={"origin": "http://127.0.0.1:8770"}) as ws:
        said = json.loads(ws.receive_text())
        assert said["kind"] == "error" and "session.start refused" in said["data"]["message"]
    assert FailsToStart.made[0].closed and order == ["settled"]
    assert ledger.month()["inFlightUsd"] == 0


def test_a_created_webrtc_session_whose_sideband_fails_is_ended_at_the_provider():
    from orchestrator.voice.openai_live import OpenAILiveSession
    from orchestrator.voice.base import VoiceConfig

    ended = []

    async def create(**kwargs):
        return {"session": {"id": "sess_1"}, "transport": {"sdp": "v=0 answer"}}

    class Sideband:
        async def __aenter__(self):
            raise ConnectionError("sideband refused")

        async def __aexit__(self, *a):
            return False

    async def hangup(session_id):
        ended.append(session_id)

    session = OpenAILiveSession(VoiceConfig(model="gpt-live-1", system_prompt="p"), link="webrtc", create=create,
                                sideband=lambda session_id: Sideband(), hangup=hangup)
    with pytest.raises(ConnectionError):
        asyncio.run(session.start(offer_sdp="v=0 offer"))
    assert ended == ["sess_1"] and session.answer_sdp is None, "ended, and its answer never reaches the browser"
    asyncio.run(session.close())
    assert ended == ["sess_1"], "ended once"


def test_anything_after_the_provider_is_acquired_that_fails_closes_it_and_releases_the_hold(monkeypatch, ledger):
    """Codex's recheck of 73626a0d: the first message to the browser and the session's bookkeeping, after the provider
    started, are owned by the same cleanup."""
    client, _ = app_with(monkeypatch)

    def broken(text):
        raise RuntimeError("the browser went away")

    monkeypatch.setattr(route, "_sha", broken)  # fails right after the first message is sent
    with client.websocket_connect("/ws/voice", headers={"origin": "http://127.0.0.1:8770"}) as ws:
        assert json.loads(ws.receive_text())["kind"] == "model"
    session = FakeSession.made[0]
    until(lambda: session.closed and ledger.month()["inFlightUsd"] == 0)
    assert session.closed and ledger.month()["inFlightUsd"] == 0


class BlockedClose:
    """A provider session whose close waits for the test, and is closed only once it has really finished."""

    def __init__(self, order):
        self.order, self.closing, self.release, self.closed = order, asyncio.Event(), asyncio.Event(), False

    async def close(self):
        self.closing.set()
        await self.release.wait()
        self.closed = True
        self.order.append("closed")


class Meter:
    def __init__(self, order, session):
        self.order, self.session = order, session

    def finish(self, confirmed=True, why=""):
        self.order.append("settled" if self.session.closed else "settled before the provider was closed")


@pytest.mark.parametrize("cancels", [1, 3])
def test_a_cancelled_route_closes_the_provider_before_it_settles_and_never_detaches_its_cleanup(cancels):
    """The browser or the server going away (once, or again) while the provider closes: the route waits for the whole
    bounded cleanup, settles only after the provider is closed, then raises its cancellation (Codex's review,
    2026-10-14)."""
    async def go():
        order = []
        session = BlockedClose(order)
        route_task = asyncio.ensure_future(route.finish_owned(route.close_then_settle([], session, Meter(order, session))))
        await asyncio.wait_for(session.closing.wait(), HANG_S)
        for _ in range(cancels):
            route_task.cancel()
            await asyncio.sleep(0)
        await asyncio.sleep(0.05)
        assert order == [] and not route_task.done(), "nothing settled, and the route still owns its cleanup"
        session.release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(route_task, 5)
        assert order == ["closed", "settled"]
    asyncio.run(go())


def test_a_cleanup_with_nothing_cancelled_returns_after_it_settles():
    async def go():
        order = []
        session = BlockedClose(order)
        session.release.set()
        await asyncio.wait_for(route.finish_owned(route.close_then_settle([], session, Meter(order, session))), 5)
        assert order == ["closed", "settled"]
    asyncio.run(go())


def test_a_session_settles_in_the_ledger_it_held_its_minutes_in(ledger, tmp_path):
    """The app's ledger changing while a session is open (a reconfiguration) never moves its settlement: it releases its
    own hold in its own ledger."""
    session = api_usage.VoiceUsage("gpt-live-1")
    assert ledger.month()["inFlightUsd"] > 0, "its first minutes held as it opened"
    other = api_usage.UsageLedger(tmp_path / "other")
    api_usage.configure(other)
    try:
        session.finish()
    finally:
        api_usage.configure(ledger)
        other.close()
    assert ledger.month()["inFlightUsd"] == 0, "released where it was held"


class CancelledAfterAcquiring(FakeSession):
    """The provider's start acquires a connection, then the route is cancelled (the browser or the server going away)."""

    async def start(self, offer_sdp=None):
        self.acquired = True
        raise asyncio.CancelledError()


@pytest.mark.parametrize("path", ["start-fails", "start-cancelled", "after-acquiring", "ends"])
def test_every_way_a_route_ends_closes_then_settles_through_the_owned_cleanup(monkeypatch, ledger, path):
    """The route's own wiring (Codex's review, 2026-10-14): a start that fails or is cancelled after acquiring, a failure
    after the provider is acquired, and an ordinary end each run close-then-settle through finish_owned, closed before
    settled and the hold released. Reverting any of them to a direct close and settle fails here."""

    client, _ = app_with(monkeypatch)
    owned, order = [], []
    real_owned = route.finish_owned

    async def spy(cleanup):
        owned.append(cleanup.cr_code.co_name)
        await real_owned(cleanup)

    monkeypatch.setattr(route, "finish_owned", spy)
    kind = {"start-fails": FailsToStart, "start-cancelled": CancelledAfterAcquiring, "after-acquiring": FakeSession,
            "ends": Lingering}[path]
    monkeypatch.setattr(route, "OpenAILiveSession", kind)
    real_finish = api_usage.VoiceUsage.finish

    def finish(self, confirmed=True, why=""):
        order.append("settled" if kind.made[-1].closed else "settled before closing")
        real_finish(self, confirmed, why)

    monkeypatch.setattr(api_usage.VoiceUsage, "finish", finish)
    if path == "after-acquiring":
        monkeypatch.setattr(route, "_sha", lambda text: (_ for _ in ()).throw(RuntimeError("the browser went away")))
    if path == "ends":
        monkeypatch.setattr(api_usage, "RENEW_S", 0.05)
        real, calls = api_usage.VoiceUsage.renew, []
        monkeypatch.setattr(api_usage.VoiceUsage, "renew",
                            lambda self: (calls.append(1), real(self) if len(calls) == 1 else False)[1])
    with __import__("contextlib").suppress(BaseException):
        with client.websocket_connect("/ws/voice", headers={"origin": "http://127.0.0.1:8770"}) as ws:
            with __import__("contextlib").suppress(BaseException):
                ws.receive_text()
                ws.receive_text()
    until(lambda: order and ledger.month()["inFlightUsd"] == 0)
    assert owned == ["close_then_settle"], f"{path}: the route's own cleanup, owned to its end ({owned})"
    assert order == ["settled"] and ledger.month()["inFlightUsd"] == 0, (path, order)


def _webrtc(hangup=None):
    from orchestrator.voice.openai_live import OpenAILiveSession
    from orchestrator.voice.base import VoiceConfig

    session = OpenAILiveSession(VoiceConfig(model="gpt-live-1", system_prompt="p"), link="webrtc", hangup=hangup)
    session.session_id = "sess_1"  # a call created at the provider, as start() leaves it
    return session


def test_a_failed_hangup_stays_unconfirmed_however_often_it_is_closed(monkeypatch):
    """Its one shutdown's outcome is sticky: a second close (the cap's, then the route's) waits for the same outcome and
    never reads a failed hangup as success (Codex's review of the lifecycle repair)."""
    from orchestrator.voice import openai_live

    monkeypatch.setattr(openai_live, "CLOSE_STEP_S", 0.2)
    calls = []

    async def hangup(session_id):
        calls.append(session_id)
        raise ConnectionError("hangup refused")

    async def go():
        session = _webrtc(hangup)
        await session.close()
        first = session.termination
        await session.close()
        await asyncio.gather(session.close(), session.close())
        return first, session.termination

    first, after = asyncio.run(go())
    assert first.startswith("unconfirmed") and "WebRTC call wasn't hung up" in first
    assert after == first and calls == ["sess_1"], "one shutdown, its outcome kept"


def test_a_webrtc_call_with_no_way_to_hang_up_is_unknown_never_success():
    async def go():
        session = _webrtc(hangup=None)
        await session.close()
        return session.termination
    assert asyncio.run(go()).startswith("unconfirmed")


def test_provider_finalization_survives_later_transport_cleanup_failure():
    """A real session.closed is authoritative; a second hangup is unnecessary."""
    calls, notes = [], []

    async def hangup(session_id):
        calls.append(session_id)
        raise AssertionError("already finalized")

    class BrokenTransport:
        async def __aexit__(self, *args):
            raise ConnectionError("transport already gone")

    async def go():
        session = _webrtc(hangup)
        session.noted = notes.append
        session._cm = BrokenTransport()
        session.handle({"type": "session.closed", "reason": "remote_hangup", "usage": {"seconds": 7.25}})
        await session.close()
        await session.close()
        assert session.termination == "confirmed"
        assert session.usage_seconds == 7.25
        assert session.answer_sdp is None

    asyncio.run(go())
    assert not calls
    assert any(n['kind'] == 'voice-transport-cleanup-incomplete' for n in notes)
    assert not any(n['kind'] == 'voice-termination-unconfirmed' for n in notes)


def test_a_stalled_close_is_bounded_and_the_hangup_is_still_attempted(monkeypatch, wall_clock):
    """A sideband close that never returns and delays its own cancellation: the shutdown ends within its steps' bounds,
    the call is still hung up, and the session close is said unconfirmed (Codex's spec review, P1)."""
    from orchestrator.voice import openai_live

    monkeypatch.setattr(openai_live, "CLOSE_STEP_S", 0.1)
    hung = []

    class Stalls:
        async def close(self):
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                await asyncio.sleep(0.3)  # delays its cancellation past the step's bound
                raise

    async def hangup(session_id):
        hung.append(session_id)

    async def go():
        session = _webrtc(hangup)
        session._conn = type("Conn", (), {"session": Stalls()})()
        started = asyncio.get_running_loop().time()
        await session.close()
        return session.termination, asyncio.get_running_loop().time() - started

    termination, took = asyncio.run(go())
    assert hung == ["sess_1"], "hung up although the close before it stalled"
    assert termination == "unconfirmed: its session didn't close", (termination, took)
    wall_clock.within(took, 1.0, "the stalled close")


def test_a_close_that_never_ends_keeps_the_hold_counted_and_the_route_comes_back_on_time(monkeypatch, ledger,
                                                                                         wall_clock):
    """Not confirmed ended: its minutes ahead stay counted against the limit as an unknown cost (the ledger's tracking
    gap), never released as if it surely ended; and the route's cleanup returns at its bounds."""
    monkeypatch.setattr(route, "SHUTDOWN_S", 0.1)

    class NeverCloses:
        closed = False

        async def close(self):
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                await asyncio.sleep(0.2)  # delays its cancellation too
                raise

    async def go():
        meter = api_usage.VoiceUsage("gpt-live-1")
        assert ledger.month()["inFlightUsd"] > 0
        started = asyncio.get_running_loop().time()
        await route.finish_owned(route.close_then_settle([], NeverCloses(), meter))
        return asyncio.get_running_loop().time() - started

    took = asyncio.run(go())
    month = ledger.month()
    wall_clock.within(took, 1.0, "the route")
    assert month["inFlightUsd"] == 0 and month["unknownAllowanceUsd"] > 0, month


def test_repeated_cancellation_while_a_failed_setup_closes_still_lets_the_app_go_of_the_session(monkeypatch, ledger):
    """The route's own endpoint (Codex's standards review): a failure after the provider is acquired, then the route
    cancelled twice while its provider closes. Settled after the close, and the app's own hold on the session (brain.voice,
    its spoken requests) let go even though the cancellation is raised after settling."""
    from types import SimpleNamespace
    from fastapi import FastAPI
    from orchestrator.options import Settings

    order, closed_requests = [], []

    class BlocksOnClose(FakeSession):
        closing = release = None

        async def close(self):
            BlocksOnClose.closing.set()
            await BlocksOnClose.release.wait()
            self.closed = True
            order.append("closed")

    class Requests:
        def __init__(self, *a, **kw):
            self.voice = None
            self.user_line = SimpleNamespace(caption=False, join=" ")
            self.reply_line = SimpleNamespace(caption=False)

        def close(self):
            closed_requests.append(True)

    monkeypatch.setattr(route, "OpenAILiveSession", BlocksOnClose)
    monkeypatch.setattr(route, "SpokenRequests", Requests)
    monkeypatch.setattr(route, "voice_prompt", lambda brain: "prompt")
    monkeypatch.setattr(route, "_sha", lambda text: (_ for _ in ()).throw(RuntimeError("the browser went away")))
    real_finish = api_usage.VoiceUsage.finish

    def finish(self, confirmed=True, why=""):
        order.append("settled" if confirmed else "settled unconfirmed")
        real_finish(self, confirmed, why)

    monkeypatch.setattr(api_usage.VoiceUsage, "finish", finish)
    brain = SimpleNamespace(agent=SimpleNamespace(log=lambda r: None), voice=None, voice_notes=[], sight=None,
                            voice_recap=lambda: "", moment=lambda: None, registry=SimpleNamespace(tools={}))
    app = FastAPI()
    route.add_voice_route(app, SimpleNamespace(settings=Settings(), latest_info=None), brain, dialogue=None)
    endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/ws/voice")

    class Socket:
        headers, query_params, client = {"origin": "http://127.0.0.1:8770"}, {}, None

        async def accept(self): pass
        async def send_text(self, text): pass
        async def send_json(self, value): pass
        async def close(self, code=None): pass

    async def go():
        BlocksOnClose.closing, BlocksOnClose.release = asyncio.Event(), asyncio.Event()
        task = asyncio.ensure_future(endpoint(Socket()))
        await asyncio.wait_for(BlocksOnClose.closing.wait(), HANG_S)
        assert brain.voice is not None, "acquired: the app held it"
        for _ in range(2):
            task.cancel()
            await asyncio.sleep(0)
        await asyncio.sleep(0.05)
        assert order == [] and not task.done()
        BlocksOnClose.release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)

    asyncio.run(go())
    assert order == ["closed", "settled"], order
    assert brain.voice is None and closed_requests, "the app let go of it although the cancellation came after settling"


@pytest.mark.parametrize("cap", [5.0, None])
def test_an_unconfirmed_end_shows_as_unknown_cost_with_or_without_a_cap(tmp_path, cap):
    """Cap or no cap, an end not confirmed reaches Spending's existing unknown-cost tracking: a charge of unknown cost
    beside its estimate (and, under a cap, its minutes ahead kept counted), never a clean final estimate alone."""
    ledger = api_usage.UsageLedger(tmp_path / "usage")
    api_usage.configure(ledger)
    try:
        if cap is not None:
            ledger.set_cap(cap)
        session = api_usage.VoiceUsage("gpt-live-1")
        session.finish(False, "its close didn't end within 10 s")
        month = ledger.month()
        assert month["unknownCalls"] >= 1, month
        assert month["inFlightUsd"] == 0
        if cap is not None:
            assert month["unknownAllowanceUsd"] > 0, "its minutes ahead stay counted against the limit"
    finally:
        api_usage.configure(None)
        ledger.close()


def test_a_confirmed_end_adds_no_unknown_cost(tmp_path):
    ledger = api_usage.UsageLedger(tmp_path / "usage")
    api_usage.configure(ledger)
    try:
        api_usage.VoiceUsage("gpt-live-1").finish()
        assert ledger.month()["unknownCalls"] == 0
    finally:
        api_usage.configure(None)
        ledger.close()


class Closes:
    closed = False

    async def close(self):
        self.closed = True


@pytest.mark.parametrize("late", [False, True])
def test_a_settlement_that_fails_is_a_tracking_gap_never_silent(monkeypatch, ledger, late):
    """Its outcome is read, in time or after its bound: a failure counts in Spending's existing tracking gap (the
    ledger's recording errors), never a silently kept hold (Codex's re-review of 1ec7e180)."""
    import threading
    monkeypatch.setattr(route, "SETTLE_S", 0.05)
    gate = threading.Event()

    class Meter:
        ledger = None

        def finish(self, confirmed=True, why=""):
            if late:
                gate.wait(5)
            raise RuntimeError("database is locked")

    meter = Meter()
    meter.ledger = ledger

    async def go():
        await route.finish_owned(route.close_then_settle([], Closes(), meter))
        if late:
            assert ledger.failures == 0, "not yet: it ends after its bound"
            gate.set()
            await until_async(lambda: ledger.failures)

    asyncio.run(go())
    assert ledger.failures == 1


def test_end_talk_settles_final_provider_usage_after_event_pump_has_stopped():
    from test_voice_openai import make
    async def run():
        session, conn, _ = make()
        await session.start()
        async def close():
            await conn.inbox.put({"type": "session.closed", "reason": "close_requested", "usage": {"seconds": 9.25}})
        conn.session.close = close
        order = []
        class FinalMeter:
            def update(self, seconds):
                order.append(("usage", seconds))
            def finish(self, confirmed, why):
                order.append(("finish", confirmed, why))
        await route.close_then_settle([], session, FinalMeter())
        assert order == [("usage", 9.25), ("finish", True, "confirmed")]
        assert session._closed_ack.is_set()
    asyncio.run(run())
