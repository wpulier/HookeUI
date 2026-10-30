"""Synthetic external WS delivery to the actual simulator Viewer; no researcher or model runs."""
import asyncio
import copy

import pytest

from sim.harness import Viewer
from test_sim_viewer import Connection
from tests.waiting import HANG_S


@pytest.mark.parametrize('fails', [False, True])
def test_scripted_lesson_restores_presentation_setting(fails):
    from sim.harness import standard_layout_fixture

    class SettingsViewer:
        def __init__(self):
            self.results = []
            self.presentation = 'auto'

        async def send(self, message):
            self.presentation = message['settings'].get('presentation', self.presentation)
            self.results.append({'ok': True, 'settings': {'presentation': self.presentation}})

        async def result_for(self, kind, mark):
            return self.results[mark]

    async def check():
        viewer = SettingsViewer()
        @standard_layout_fixture
        async def scenario(stack, active):
            assert active.presentation == 'off'
            if fails:
                raise RuntimeError('scenario failed')
            return 'retained lesson'
        if fails:
            with pytest.raises(RuntimeError, match='scenario failed'):
                await scenario(None, viewer)
        else:
            assert await scenario(None, viewer) == 'retained lesson'
        assert viewer.presentation == 'auto'
    asyncio.run(check())


RUN = {"runId": "original-revision", "run": 1,
       "origin": {"channel": "system", "request_id": "request-A", "action_id": "action-A"}}


def notice(*, run=RUN, result=None, event="completed"):
    return {"type": "app-notice", "producer": {"kind": "research", "id": "r1"},
            "event": event, "at": 100, "day": "2026-10-09", "summary": "activity only",
            "facts": {"researchId": "r1", "status": "done", "run": copy.deepcopy(run),
                      "result": result, "outputs": [{"id": "work-v2", "kind": "html",
                      "path": "outputs/v2.html", "version": 2, "replaces": "work-v1"}],
                      "last": {"mode": "revise", "target": "work-v1", "output": "work-v2", "version": 2}}}


async def receive(monkeypatch, viewer, messages):
    """The barrier is a server result after the packets, consumed by the same real receive loop."""
    connection = Connection()
    monkeypatch.setattr("sim.harness.websockets.connect", lambda *a, **kw: connection)
    receiver = asyncio.create_task(viewer.run())
    try:
        await connection.connected.wait()
        mark = len(viewer.results)
        for message in messages:
            await connection.incoming.put(message)
        await connection.incoming.put({"type": "result", "for": "barrier", "ok": True})
        assert (await viewer.result_for("barrier", mark, timeout=HANG_S))["ok"]
    finally:
        receiver.cancel()
        await asyncio.gather(receiver, return_exceptions=True)


def test_received_research_activity_retains_result_sources_work_and_original_occurrence_without_chat(monkeypatch):
    async def check():
        viewer = Viewer("ws://synthetic-external")
        packet = notice(result={"title": "Observation", "sources": [{"url": "https://example.test/source"}],
                                "refs": ["original-capture"], "limitations": ["scripted fixture"]})
        await receive(monkeypatch, viewer, [packet])
        assert viewer.research_activity("r1", RUN, since=0) == [packet]
        assert not viewer.chats
        assert viewer.research_activity("r1", RUN, since=viewer.notice_count) == []
    asyncio.run(check())


@pytest.mark.parametrize("changed", ["runId", "request_id", "action_id", "unknown-run"])
def test_foreign_or_unknown_original_occurrence_cannot_satisfy_the_research_announcement(monkeypatch, changed):
    async def check():
        viewer = Viewer("ws://synthetic-external")
        wrong = copy.deepcopy(RUN)
        if changed == "runId":
            wrong["runId"] = "later-run"
        elif changed == "unknown-run":
            wrong["runId"] = None
        else:
            wrong["origin"][changed] = "unrelated"
        await receive(monkeypatch, viewer, [notice(run=wrong), notice(event="presentation")])
        assert viewer.research_activity("r1", RUN, since=0) == []
        assert viewer.research_activity("other-researcher", wrong, since=0) == []
    asyncio.run(check())


def test_revised_null_result_does_not_borrow_the_earlier_result_or_lose_new_work(monkeypatch):
    async def check():
        viewer = Viewer("ws://synthetic-external")
        old = {"runId": "initial-run", "run": 0, "origin": {"channel": "chat"}}
        await receive(monkeypatch, viewer, [notice(run=old, result={"summary": "earlier result"})])
        mark = viewer.notice_count
        revised = notice(result=None)
        await receive(monkeypatch, viewer, [revised])
        activity = viewer.research_activity("r1", RUN, since=mark)
        assert activity == [revised]
        assert activity[0]["facts"]["result"] is None
        assert activity[0]["facts"]["outputs"] == [{"id": "work-v2", "kind": "html", "path": "outputs/v2.html",
                                                     "version": 2, "replaces": "work-v1"}]
        assert activity[0]["facts"]["last"]["target"] == "work-v1"
        assert not viewer.chats
    asyncio.run(check())


def test_bounded_activity_retention_does_not_confuse_an_old_cursor_with_new_delivery(monkeypatch):
    async def check():
        viewer = Viewer("ws://synthetic-external")
        await receive(monkeypatch, viewer, [notice() for _ in range(260)])
        assert viewer.notice_count == 260
        assert len(viewer.notices) == 256
        mark = viewer.notice_count
        await receive(monkeypatch, viewer, [notice()])
        assert len(viewer.research_activity("r1", RUN, since=mark)) == 1
        assert viewer.research_activity("r1", RUN, since=viewer.notice_count) == []
    asyncio.run(check())


def test_a_trimmed_original_completion_and_its_retained_duplicate_cannot_certify_exactly_once(monkeypatch):
    async def check():
        viewer = Viewer("ws://synthetic-external")
        original = notice(result={"summary": "original result"})
        unrelated = notice(run={"runId": "unrelated-run", "run": 0, "origin": {"channel": "chat"}})
        await receive(monkeypatch, viewer, [original, *[unrelated for _ in range(256)], original])
        assert viewer.notice_count == 258
        assert len(viewer.notices) == 256
        assert viewer.notices[-1][1] == original, 'the actual suffix is retained, never erased to hide duplicate delivery'
        assert viewer.research_activity("r1", RUN, since=0) == [], 'a missing prefix cannot certify completion cardinality'
        # A later operation starts with complete coverage and remains useful.
        mark = viewer.notice_count
        later_run = {"runId": "later-owned-run", "run": 2,
                     "origin": {"channel": "system", "request_id": "request-B", "action_id": "action-B"}}
        later = notice(run=later_run)
        await receive(monkeypatch, viewer, [later])
        assert viewer.research_activity("r1", later_run, since=mark) == [later]
    asyncio.run(check())
