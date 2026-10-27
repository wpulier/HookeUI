"""A researcher's work the user asked for opens beside the view, and the chat says it is open only once
the studio's report shows the viewer open on that researcher (flow 7; M1.5b re-check N5, announcer.py).
Historical unowned reports are retained as negative controls; accepted-browser reports exercise owned delivery below."""
import asyncio
from datetime import datetime
from types import SimpleNamespace

from orchestrator.day import DayStore
from orchestrator.research import ResearchManager, Session
from orchestrator.screen import Screen
from tests.fakes import announcer
from tests.test_research import FakeHub


def session(tmp_path) -> Session:
    return Session(rid="r3", n=3, brief="look into stomata", deliverable="", effort="quick", engine="fake",
                   purpose="research", label="stomata", object_id=None, origin={"channel": "chat"}, day="2026-10-07",
                   status="done", root=tmp_path, result={"title": "Stomata", "summary": "Pores in leaves.", "one_liner": "Pores."})


class Studio:
    """Reports its viewer on `shows` (or on another researcher), and answers the server's ask for a report."""

    def __init__(self, screen: Screen, shows: str | None):
        self.screen, self.shows = screen, shows

    def hear(self, message: dict) -> None:
        if message.get("type") == "screen-ask":
            asyncio.get_running_loop().call_soon(self.report)

    def report(self) -> None:
        open_ = self.shows not in (None, "closed")
        items = [{"id": "viewer", "kind": "viewer", "open": open_, "shows": self.shows if open_ else None}]
        self.screen.report({"items": items, "applied": []}, client=self)


def announced(tmp_path, studio_shows, *, with_studio=True, stale=None):
    screen = Screen()
    studio = Studio(screen, studio_shows) if with_studio else None
    screen.broadcast = (lambda message: studio.hear(message)) if studio else (lambda message: None)
    shown = []

    def show(rid):
        shown.append(rid)
        if studio is not None and studio_shows == "follows":
            studio.shows = rid  # the studio opens the viewer on what it is told to show

    hub = FakeHub()
    hub.screen, hub.broadcast = screen, screen.broadcast
    presenter = SimpleNamespace(show=show, hub=hub)
    day = DayStore(root=tmp_path / 'sessions', day='2026-10-07',
                   clock=lambda: datetime(2026, 10, 7, 12).timestamp())
    manager = ResearchManager(hub, root=tmp_path / 'research', today=lambda: day.day)
    original = session(tmp_path)
    manager.sessions[original.rid] = original
    a = announcer(tmp_path, manager, presenter=presenter, day=day)

    async def go():
        if studio is not None:
            studio.report()  # a studio showing the view
            if stale is not None:
                studio.shows = stale  # it changed since, and has not said so yet
        await a.announcer.research_finished(original)

    asyncio.run(go())
    return shown, a


def presentation(a):
    completed, = [row for row in a.lines if row.get('event') == 'completed']
    assert completed['facts']['result']['title'] == 'Stomata'
    assert completed['facts']['result']['summary'] == 'Pores in leaves.'
    notice, = [row for row in a.lines if row.get('event') == 'presentation']
    facts = notice['facts']['presentation']
    assert facts['researchId'] == 'r3' and facts['contentVerified'] is False
    assert all(row['type'] == 'app-notice' for row in a.lines)
    return facts['report']


def test_it_is_said_open_only_once_the_studio_shows_the_viewer_on_that_researcher(tmp_path):
    shown, a = announced(tmp_path / "a", "follows")
    assert shown == [] and presentation(a) == 'retained in Researchers; original browser unavailable'


def test_a_viewer_kept_on_another_researcher_is_not_called_open(tmp_path, monkeypatch):
    import orchestrator.screen as screen_module

    monkeypatch.setattr(screen_module, "CONFIRM_S", 0.3)
    shown, a = announced(tmp_path / "b", "r1")  # the viewer stays on Researcher 1's work
    assert shown == [] and presentation(a) == 'retained in Researchers; original browser unavailable'


def test_without_a_studio_nothing_is_claimed_about_the_screen(tmp_path):
    shown, a = announced(tmp_path / "c", None, with_studio=False)
    assert shown == [] and presentation(a) == 'retained in Researchers; original browser unavailable'


def test_a_report_from_before_it_was_shown_never_counts(tmp_path, monkeypatch):
    """The last report said the viewer was open on this researcher (an earlier opening); it was closed
    since, and the studio does not open it again: only a report made after the show counts."""
    import orchestrator.screen as screen_module

    monkeypatch.setattr(screen_module, "CONFIRM_S", 0.3)
    shown, a = announced(tmp_path / "d", "r3")
    assert shown == [] and presentation(a) == 'retained in Researchers; original browser unavailable'
    shown, a = announced(tmp_path / "e", "r3", stale="closed")
    assert shown == [] and presentation(a) == 'retained in Researchers; original browser unavailable'


def test_delayed_research_opens_only_its_accepted_browser_and_needs_its_fresh_ack(tmp_path):
    from orchestrator.research.run import Delivery
    from tests.test_legacy_workspace_projection import structural_report, row
    from tests.test_screen_workspace import Rig, REF
    from tests.test_structural_change_origin import PEER, packets
    from tests.test_workspace_actions_app import research_fixture

    async def go():
        rig = Rig(tmp_path)
        rig.app.accept_workspace(rig.client, REF)
        rig.app.accept_workspace(rig.other, PEER)
        _, saved = research_fixture(rig.app)
        first = structural_report(rig)
        await rig.ingest(first)
        pin = rig.screen.pin(rig.client, request_id='research-original')
        saved._delivery = Delivery(asked=rig.app.day.here(), day=saved.day,
            client=rig.client, request_snapshot=pin, request_id='research-original')
        assert 'open its work when ready' in await rig.app.announcer._open_beside(saved)
        assert not any(p.get('type') in ('presenter', 'present') for p in packets(rig.client))
        rig.app.presenter.for_client(rig.client).show(saved.rid)
        packets(rig.client); packets(rig.other)
        task = asyncio.create_task(rig.app.announcer._open_beside(saved))
        await asyncio.sleep(0)
        original = packets(rig.client)
        peer = packets(rig.other)
        assert not any(p.get('type') == 'presenter' for p in original)  # no selection or quiz reset
        assert not any(p.get('type') in ('presenter', 'present') for p in peer)
        change = next(p for p in original if p.get('type') == 'present')
        assert not task.done()
        ack = structural_report(rig, seq=11)
        row(ack, 'viewer')['view']['legacy']['shows'] = saved.rid
        from orchestrator.presenter import items, first_shown
        row(ack, 'viewer')['view']['legacy']['work'] = items(saved)[first_shown(saved)]['work']
        row(ack, 'viewer')['view']['legacy']['workReady'] = True
        row(ack, 'viewer')['revision'] += 1
        row(ack, 'viewer')['sourceGeneration'] += 1
        ack['applied'] = [change['change']]
        reply = await rig.ingest(ack)
        assert reply['ok'], reply
        assert await task == 'viewer rendered the exact retained work'
        rig.screen.gone(rig.client)
        assert 'ownership expired' in await rig.app.announcer._open_beside(saved)
        assert not any(p.get('type') in ('presenter', 'present') for p in packets(rig.other))
    asyncio.run(go())
