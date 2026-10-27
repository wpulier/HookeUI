"""Actual canonical consumer for the Studio legacy-causality TEST-only stdio lane.

The companion is driven by workspace-legacy-causality.test.ts: every report/pin
is serialized by actual Reporter/Session; Changes originate in actual Screen.
Synthetic trusted sockets/clock are platform inputs. No model/App/server/media
or private account is started. Parent runs this only in the accepted combined
consumer tree (e99e or later), never imports a copied normalizer into JS.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import pytest

from orchestrator.screen import Screen, magnifier_arrangement, open_state
from orchestrator.workspace_observation import ClientRef, ObservationError


def app_lifecycle_publications(how):
    """Actual public App/day owner over isolated files and an external passive socket.

    Called without an event loop (or in a TEST worker thread), so the actual
    Memory owner cannot start provider work. No core lifecycle callback is patched.
    """
    from orchestrator.app import App
    from orchestrator.paths import Paths

    class Socket:
        def __init__(self):
            self.messages = []

        def offer(self, message):
            self.messages.append(json.loads(message))

    with tempfile.TemporaryDirectory(prefix="micromind-overlay-lifecycle-") as root:
        app = App.build("http://127.0.0.1:1", Paths.under(Path(root)), use_gpu=False)
        first = app.day.sid
        if how == "switch":
            app.new_session()  # a second session to go back from, before the socket listens
        before = app.day.wire()
        socket = Socket()
        app.hub.clients.add(socket)
        if how == "midnight":
            # The store's public external clock seam: midnight does nothing (ADR 0017).
            next_day = app.day.clock() + 24 * 3600
            app.day.clock = lambda: next_day
            assert app.day.sid == before["id"] and app.day.date != before["id"]
        elif how == "new-session":
            app.new_session()
            assert app.day.sid != before["id"]
        elif how == "switch":
            app.switch_session(first)
            assert app.day.sid == first != before["id"]
        elif how == "thread":
            original = app.day.active_id
            app.day.open_thread("Another conversation")
            app.day.switch(original)
        else:
            raise ValueError("bounded lifecycle lane needs midnight, new-session, switch or thread")
        assert not app.hub.scene.objects and not app.magnifier.open
        return {"before": before, "messages": socket.messages, "after": app.day.wire()}


class BrowserSocket:
    """External accepted transport records actual unicast JSON, without reporting an ACK."""
    def __init__(self, outbound):
        self.outbound = outbound

    def offer(self, message):
        self.outbound.append(json.loads(message))


class BrowserConsumer:
    """Small platform driver; all admission, pin and Change state belongs to Screen."""
    def __init__(self):
        self.now = 10.0
        self.outbound = []
        self.screen = Screen(broadcast=self.outbound.append, clock=lambda: self.now)
        self.sockets = {}
        self.changes = {}  # original public Change objects, not reconstructed associations
        self.pins = {}  # original Screen.pin values with their original request IDs

    @staticmethod
    def no_source(_client, _source):
        raise AssertionError("structural-only browser lane cannot resolve native sources")

    @staticmethod
    def no_capture(*_args):
        raise AssertionError("this lane does not authorize capture")

    async def handle(self, payload):
        self.now += 0.01
        op = payload["op"]
        key = payload.get("socket", "current")
        if op == "app-lifecycle":
            return await asyncio.to_thread(app_lifecycle_publications, payload["how"])
        if op == "accept":
            ready = payload["ready"]
            client = ready["client"]
            socket = BrowserSocket(self.outbound)
            self.sockets[key] = socket
            self.screen.accept_client(socket, ClientRef(client["workspaceId"], client["clientId"],
                client["pageEpoch"], client["connectionEpoch"]), app_run_id=ready["appRunId"],
                source_resolver=self.no_source, capture_authority=self.no_capture)
            return {"accepted": True}
        if op == "gone":
            self.screen.gone(self.sockets.pop(key))
            return {"retired": True}
        if op == "report":
            socket = self.sockets.get(key)
            self.screen.report(payload["wire"], client=socket)
            return {"accepted": True, "wire": self.screen.observation(socket).wire()}
        if op == "pin":
            snapshot = self.screen.pin(self.sockets.get(key), request_id=payload["requestId"],
                                       inline=payload["wire"], timing="client-send")
            self.pins[key] = (snapshot, payload["requestId"])
            return {"accepted": True, "seq": snapshot.observation.wire()["seq"], "timing": snapshot.timing}
        if op == "change":
            change = self.screen.send("magnifier", "magnifier", "move", {"rect": [40, 60, 320, 240]}, "synthetic-test-owner")
            self.changes[change.id] = change
            assert self.outbound[-1] == change.wire()
            return {"wire": change.wire()}
        if op == "change-close":
            change = self.screen.send("magnifier", "magnifier", "close", {"open": False}, "synthetic-test-owner")
            self.changes[change.id] = change
            assert self.outbound[-1] == change.wire()
            return {"wire": change.wire()}
        if op == "change-overlay-hide":
            target, kind = payload["target"], payload["kind"]
            if kind not in ("tracks", "population") or target not in ("tracks:m1", "layer:e1"):
                raise ValueError("bounded overlay lane needs its original measured target")
            snapshot, request_id = self.pins[key]
            change = self.screen.send(target, kind, "hide", {"visible": False}, "synthetic-test-owner",
                                      client=self.sockets[key], snapshot=snapshot, request_id=request_id)
            self.changes[change.id] = change
            assert self.outbound[-1] == change.wire()
            return {"wire": change.wire()}
        if op == "change-entry-open":
            source = payload["source"]  # bounded external publication fact, not a native source grant
            if not isinstance(source, str) or not source or len(source) > 2048:
                raise ValueError("entry-open needs one bounded original picture URL")
            change = self.screen.send("magnifier", "entry", "open",
                {"open": True, "shows": "synthetic-retained-entry", "source": source}, "synthetic-test-owner")
            self.changes[change.id] = change
            assert self.outbound[-1] == change.wire()
            return {"wire": change.wire()}
        if op == "confirm":
            change = self.changes[payload["changeId"]]
            if change.kind in ("tracks", "population") and change.verb == "hide":
                def hidden(report):
                    row = next((item for item in report["items"] if item["id"] == change.target), None)
                    return bool(row and not row.get("visible"))
                return {"confirmed": await self.screen.confirm(change, hidden, timeout=0.01)}
            if change.kind == "entry" and change.verb == "open":
                source = change.set["source"]
                return {"confirmed": await self.screen.confirm(change, magnifier_arrangement(source, None, [source]), timeout=0.01)}
            if change.verb == "close":
                return {"confirmed": await self.screen.confirm(change, open_state(change.target, False), timeout=0.01)}
            def shown(report):
                row = next((item for item in report["items"] if item["id"] == change.target), None)
                return bool(row and row.get("open") and row.get("rect", [])[:2] == [40, 60])
            return {"confirmed": await self.screen.confirm(change, shown, timeout=0.01)}
        raise ValueError("unsupported bounded browser-consumer operation")


async def stdio():
    consumer = BrowserConsumer()
    # Native transport records only; one request at a time, one actual retained Screen.
    for line in sys.stdin:
        if len(line) > 2 * 1024 * 1024:
            raise ValueError("driver input exceeds native report bound")
        try:
            result = await consumer.handle(json.loads(line))
            reply = {"ok": True, **result}
        except ObservationError as error:
            reply = {"ok": False, "code": error.code, "detail": str(error)}
        # Unexpected setup/runtime failures stay failures, not fabricated refusal/acceptance.
        print(json.dumps(reply, allow_nan=False), flush=True)


def test_unregistered_browser_report_does_not_gain_socket_authority():
    """Synthetic negative control only; renderer/consumer scenarios run from JS."""
    consumer = BrowserConsumer()
    try:
        asyncio.run(consumer.handle({"op": "pin", "requestId": "synthetic-question", "wire": {}}))
    except ObservationError as error:
        assert error.code == "CLIENT_MISMATCH"
    else:
        raise AssertionError("unregistered browser gained an accepted workspace")


def test_midnight_publishes_nothing_and_retires_nothing():
    actual = app_lifecycle_publications("midnight")
    assert actual["messages"] == [] and actual["after"]["id"] == actual["before"]["id"]


@pytest.mark.parametrize("how", ["new-session", "switch"])
def test_actual_app_reset_publishes_structural_retirement_before_day(how):
    actual = app_lifecycle_publications(how)
    kinds = [message["type"] for message in actual["messages"]]
    assert "day" in kinds and "thread" in kinds, "SETUP: actual App must publish its new conversation"
    assert kinds.count("session-cleared") == 1, "MISSING_FEATURE: actual reset is not published to installed Studio"
    assert kinds.index("session-cleared") < kinds.index("day")
    reset = actual["messages"][kinds.index("session-cleared")]
    # Which session opened, the one left and why: the studio chooses what the view shows from them (new or returning).
    assert reset == {"type": "session-cleared", "session": actual["after"]["id"], "previous": actual["before"]["id"],
                     "why": "new" if how == "new-session" else "switch"}, \
        "reset is factual lifecycle data, never assistant text or invented IDs"


if __name__ == "__main__":
    if sys.argv[1:] != ["--stdio"]:
        raise SystemExit("use --stdio for the actual JS renderer/Screen companion")
    asyncio.run(stdio())
