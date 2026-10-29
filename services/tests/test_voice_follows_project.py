"""The one-brain voice follows the open Project (bench think-broad-1 and tune-8 rep 1, 2026-10-29).

After a Project switch while Talk was on (switch_session by voice), every show_measurements, tabs and present-close
failed for the rest of Talk with "the studio that asked is not showing this Project's Tabs": the studio's report kept
showing the Project left. The studio half (with Talk on, the opened Project's Tab input was refused, so its Tabs never
opened) is apps/studio/src/session-view.ts ProjectLanding, with its own test. This is the server half: the voice's
binding is the studio connection, not a Project, so its tools follow the Project open now; a tool called while the
studio is still landing the Project is said as waiting, then tried once more when the studio shows the Project's Tabs;
and the voice's brief follows the switch.

Actual App, Registry, Screen and the one-brain voice's own tools (voice/partner_tools.py), with the synthetic studio of
test_table_window.py, which here shows the Project left until it lands the new one (as the real studio does for about a
second, and did for good before the studio fix)."""
import asyncio

from orchestrator.app import App
from orchestrator.paths import Paths
from orchestrator.voice import partner_tools
from orchestrator.voice.partner import Partner
from orchestrator.workspace_observation import ClientRef
from tests.test_table_window import CLIENT, TableStudio


class LandingStudio(TableStudio):
    """The studio, whose Tab strip shows the Project it last landed (not the server's at once)."""

    def __init__(self, app, **kwargs):
        super().__init__(app, **kwargs)
        self.showing = app.day.sid
        self.control = asyncio.Queue()  # what the hub queues for a studio's socket (execution records); unread here

    @property
    def project(self):
        return self.showing

    async def land(self, delay=0.0):
        await asyncio.sleep(delay)
        self.showing = self.app.day.sid
        await self.send_report()

    async def tabs_reply(self, message):
        if message['action']['action'] == 'close':  # workspace-views.ts closes it and the Tab before it comes forward
            self.tabs = [t for t in self.tabs if t['id'] != message['action']['tab']]
            self.active = 'main'
            message = {**message, 'action': {'action': 'list'}}
        await super().tabs_reply(message)


async def voice_with_studio(tmp_path, monkeypatch):
    from orchestrator import tables

    async def read_table(app, eid, session, revision):
        return {'title': 'Nuclei', 'count': 341, 'note': 'areas in pixels', 'columns': [], 'rows': [],
                'session': session, 'revision': revision}

    monkeypatch.setattr(tables, 'read_table', read_table)
    monkeypatch.setattr(partner_tools, 'REPORT_WAIT_S', 0.2)
    monkeypatch.setattr('orchestrator.voice.partner.SNAPSHOT_WAIT_S', 0.05)
    app = App.build('http://127.0.0.1:1', Paths.under(tmp_path), use_gpu=False)
    studio = LandingStudio(app)
    app.accept_workspace(studio, ClientRef(CLIENT['workspaceId'], CLIENT['clientId'], CLIENT['pageEpoch'],
                                          CLIENT['connectionEpoch']))
    app.hub.clients.add(studio)
    await studio.send_report()
    sent = []

    async def send(message):
        sent.append(message)

    # Talk started in this Project: bound to the studio's connection (route.py workspace_transport.voice_client).
    partner = Partner(app.hub, app.brain, None, client=studio, send=send, log=lambda row: None)
    partner._heard[0] = 'show the measurements in a Measurements Tab'
    return app, studio, partner


def test_after_a_project_switch_the_voice_s_tab_tools_work_in_the_project_opened(tmp_path, monkeypatch):
    """The bench's measure, table-tab, close-it and main-tab steps, after Scratch -> Nuclei with Talk on."""
    async def go():
        app, studio, partner = await voice_with_studio(tmp_path, monkeypatch)
        left = app.day.sid
        app.day.new('Nuclei', by='user')  # the voice's switch: the server's Project changes first
        assert app.day.sid != left
        await studio.land(0.0)  # the studio shows the Project opened
        result = await partner.tools.run('show_measurements', {'of': 'd4', 'tab': 'Measurements'})
        assert result['ok'] is True, result
        made = next(t for t in studio.tabs if t['title'] == 'Measurements')
        assert studio.sent('table-window')[0]['session'] == app.day.sid
        result = await partner.tools.run('tabs', {'action': 'close', 'tab': made['id']})
        assert result['ok'] is True, result
        result = await partner.tools.run('tabs', {'action': 'switch', 'tab': 'main'})
        assert result['ok'] is True, result
    asyncio.run(go())


def test_a_tab_tool_called_while_the_studio_is_still_landing_the_project_waits_once_and_is_tried_again(tmp_path,
                                                                                                         monkeypatch):
    """Called during the switch: one plain line of what it waits on (the progress line's facts), then tried again once
    the studio shows the Project's Tabs; never failed for a studio that is a moment behind."""
    async def go():
        app, studio, partner = await voice_with_studio(tmp_path, monkeypatch)
        app.day.new('Nuclei', by='user')
        await studio.send_report()  # still the Project left on its Tab strip
        waits = []
        landing = asyncio.create_task(studio.land(0.4))

        async def watch():
            for _ in range(40):
                facts = partner.tools.running_facts()
                if 'opening' in facts:
                    waits.append(facts)
                    return
                await asyncio.sleep(0.02)
        watching = asyncio.create_task(watch())
        result = await partner.tools.run('show_measurements', {'of': 'd4', 'tab': 'Measurements'})
        await landing
        await watching
        assert result['ok'] is True, result
        assert waits and "Project's Tabs" in waits[0], waits  # what the voice says while it waits: one plain fact
        assert len(studio.sent('table-window')) == 1  # tried again once, done once
    asyncio.run(go())


def test_a_studio_that_never_lands_the_project_is_said_once_not_retried_forever(tmp_path, monkeypatch):
    monkeypatch.setattr(partner_tools, 'LANDING_WAIT_S', 0.3)

    async def go():
        app, studio, partner = await voice_with_studio(tmp_path, monkeypatch)
        app.day.new('Nuclei', by='user')
        await studio.send_report()
        result = await partner.tools.run('tabs', {'action': 'switch', 'tab': 'main'})
        assert result['ok'] is False
        assert 'still' in result['summary'] and 'Project' in result['summary'], result
        assert not studio.sent('workspace-tabs')
    asyncio.run(go())


def test_the_voice_s_brief_follows_the_project_switch(tmp_path, monkeypatch):
    async def go():
        app, studio, partner = await voice_with_studio(tmp_path, monkeypatch)
        briefs = []

        class Session:
            closed = False
            n = 0

            async def set_brief(self, text, revision):
                briefs.append(text)

            async def inject(self, text, **kwargs):
                pass
        session = Session()
        partner.session = session
        session.project_changed = partner.project_changed
        app.brain.voice = session
        app.day.new('Nuclei', by='user')
        for _ in range(50):
            if briefs:
                break
            await asyncio.sleep(0.02)
        assert briefs, 'the brief is refreshed when the Project changes, not at the next key press'
    asyncio.run(go())
