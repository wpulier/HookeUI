"""The measurements table on screen tells the truth (docs/architecture/on-screen.md; orchestrator/table_window.py).

The voice log of 2026-10-28 (Part 2.5): the Assistant said the measurements table was up when it was not, made an empty
"Measurements" Tab and said the table was in it, said "the table viewer isn't opening" while the user's own table button
opened it at once, and ended with "I can't see your screen ... quit and reopen the app". `show_measurements` opened a
`#measurements` page (app.open_page) and took the studio's `page-opened` as done; the user's button opens a table
window on the Tab (`openStudyTable`).

Actual App, Registry, Screen, tabs broker and COMMANDS, with one accepted synthetic socket that reports what the
studio's DOM would (its Project Tab strip and its document windows, as workspace-document-surfaces.ts windowSurfaces
does), keeps Tabs as workspace-views.ts agentCommand does, opens the table window when it is sent `table-window`
(agent-table.ts), and answers `open-page` the way main.ts did (shown once the hash is set, whatever the Tab holds). No
browser, model, camera or worker: the drawing itself is verified separately on the isolated instance."""
import asyncio
import copy
import json
import time

import pytest

from orchestrator import table_window
from orchestrator.app import App
from orchestrator.brain.registry import ToolContext
from orchestrator.capture import RUN
from orchestrator.commands import COMMANDS
from orchestrator.paths import Paths
from orchestrator.ticket import issue, using
from orchestrator.workspace_observation import ClientRef
from tests.test_workspace_actions_app import CLIENT, WORKSPACE

TAB_TITLES = True  # the Tab strip says the title of the Tab in front (main.ts)
TABLE = {"title": "Nuclei", "count": 341, "note": "areas in pixels", "columns": [], "rows": []}


def _row(item_id, kind, *, rect, z=None, visible=True, legacy, root=None, generation=1, revision=1):
    row = {'id': item_id, 'rootId': root or f'synthetic-dom:{item_id}', 'workspaceId': CLIENT['workspaceId'],
            'kind': kind, 'lifetimeGeneration': f'synthetic-root:{item_id}', 'sourceGeneration': generation,
            'revision': revision, 'open': True, 'connected': True, 'visible': visible, 'rect': rect,
            'space': 'workspace', 'view': {'legacy': {'schema': 'studio-surface-v1', 'scope': 'dom-observation', **legacy}},
            'propertyVersions': {}, 'propertyOwners': {}, 'controls': [],
            'load': {'state': 'unknown', 'unknown': ['native-load-unobserved']}, 'unknown': ['synthetic-structural-dom-only']}
    if z is not None:
        row['z'] = z
    return row


class TableStudio:
    """One accepted socket: its Tab strip and the table windows on its Tabs, as the studio would report them.

    `opens`: it opens the window it is sent (False: it answers but never shows one, the log's empty Tab).
    `answers`: it answers `table-window` at all. `in_view`: the studio is in view (False: in the background).
    `state`: the window's rows ('rendered', 'loading', 'failed'). `covered`: another window lies over the table.
    `report_after`: seconds before it reports what it opened (a slow report)."""

    def __init__(self, app, *, opens=True, answers=True, in_view=True, state='rendered', covered=False,
                 report_after=0.0, fits=True, then_tab=None):
        self.app, self.opens, self.answers, self.in_view, self.state = app, opens, answers, in_view, state
        self.then_tab = then_tab  # the person goes to this Tab right after the window opens
        self.covered, self.report_after, self.fits = covered, report_after, fits
        self.seq, self.messages, self.tasks = 0, [], []
        self.tabs = [{'id': 'main', 'title': 'Main'}]
        self.active = 'main'
        self.windows = {}  # placement -> its window
        self.hash = ''

    @property
    def project(self):
        return self.app.day.sid

    # -- what it reports ----------------------------------------------------------------------------------------------
    def items(self):
        title = next(t['title'] for t in self.tabs if t['id'] == self.active)
        rows = [_row('workspace-tabs', 'panel', rect=[0, 0, 1440, 40], root='synthetic-dom:tabs', revision=self.seq,
                     legacy={'title': 'Project Tabs', 'workspace': {'project': self.project, 'tab': self.active,
                                                                     **({'tabTitle': title} if TAB_TITLES else {})}})]
        for placement, window in self.windows.items():
            shown = window['tab'] == self.active
            rows.append(_row(f'document:{placement}', 'document', rect=window['rect'], z=window['z'], visible=shown,
                             generation=window['generation'], revision=self.seq,
                             legacy={'title': f"{window['title']} · Measurements", 'shows': window['resource']['id'],
                                     'document': {'project': self.project, 'tab': window['tab'], 'placement': placement,
                                                  'resource': window['resource'], 'representation': 'table',
                                                  'state': window['state'], 'focused': False}}))
        if self.covered:
            rows.append(_row('document:note-over', 'document', rect=[100, 80, 900, 700], z=999, revision=self.seq,
                             legacy={'title': 'Note', 'shows': 'n1',
                                     'document': {'project': self.project, 'tab': self.active, 'placement': 'note-over',
                                                  'resource': {'day': self.project, 'id': 'n1', 'version': 1, 'picture': None},
                                                  'representation': 'note', 'state': 'saved-note', 'focused': False}}))
        return rows

    async def send_report(self, delay=0.0):
        if delay:
            await asyncio.sleep(delay)
        self.seq += 1  # every row's revision advances with it (a structural fact changes only with its revision)
        report = {
            'type': 'screen', 'schema': 'screen-observation-v2', 'client': dict(CLIENT), 'appRunId': RUN,
            'seq': self.seq, 'layoutRevision': self.seq, 'capturedMonoMs': time.monotonic() * 1000, 'visible': self.in_view,
            'complete': False, 'omitted': {'items': 1, 'sources': 0, 'targets': 0, 'edges': 0},
            'viewport': {'layoutSize': [1440, 900], 'visualRect': [0, 0, 1440, 900], 'visualScale': 1,
                         'documentScroll': [0, 0], 'dpr': 1, 'visible': self.in_view, 'documentFocused': self.in_view,
                         'activeSurface': 'workspace', 'presentationArea': [0, 40, 1440, 860]},
            'items': self.items(), 'sources': [], 'targets': [], 'edges': [],
            'interaction': {'availability': 'partial', 'unknown': ['synthetic-hit-registration-unavailable']},
            'commandResults': [], 'applied': [], 'user': [],
        }
        reply = await COMMANDS['screen'](self.app, self, report)
        assert reply.get('ok') is True, f'SETUP: structural report refused by actual normalization: {reply}'

    # -- what it is sent ----------------------------------------------------------------------------------------------
    def offer(self, message):
        self.hear(json.loads(message) if isinstance(message, str) else message)

    def later(self, coroutine):
        self.tasks.append(asyncio.get_running_loop().create_task(coroutine))

    def hear(self, message):
        if not isinstance(message, dict):
            return
        self.messages.append(copy.deepcopy(message))
        kind = message.get('type')
        if kind == 'workspace-tabs':
            self.later(self.tabs_reply(message))
        elif kind == 'table-window':
            self.later(self.table_reply(message))
        elif kind == 'open-page':  # as main.ts did: the hash is set, and that alone was "shown"
            self.hash = message['page']
            self.later(COMMANDS['page-opened'](self.app, self, {'type': 'page-opened', 'id': message['id'],
                                                                'page': message['page'], 'shown': True,
                                                                'visible': self.in_view}))
        elif kind == 'screen-ask':
            self.later(self.send_report())

    async def tabs_reply(self, message):
        """workspace-views.ts agentCommand, for the actions this test uses."""
        action = message['action']
        if action['action'] == 'create':
            tab = {'id': f'tab-{len(self.tabs)}', 'title': action['title']}
            self.tabs.append(tab)
            self.active = tab['id']  # addView activates the new Tab
        elif action['action'] == 'switch':
            self.active = action['tab']
        result = {'ok': True, 'session': self.project, 'studioVisible': self.in_view,
                  'tabs': [{**t, 'open': True, 'selected': t['id'] == self.active, 'kind': 'work', 'omittedItems': 0,
                            'items': []} for t in self.tabs]}
        await self.send_report()
        await COMMANDS['workspace-tabs-result'](self.app, self, {'type': 'workspace-tabs-result', 'id': message['id'],
                                                                 'session': message['session'],
                                                                 'workspacePin': message['workspacePin'], 'result': result})

    async def table_reply(self, message):
        """agent-table.ts: the window a person's Open in Measurements opens, on the Tab it was asked for."""
        pin = message['workspacePin']
        assert pin['client'] == CLIENT and pin['id'] == 'workspace-tabs'
        if not self.answers:
            return
        if message['session'] != self.project or message['tab'] != self.active:
            result = {'ok': False, 'error': 'Another Tab is in front now.'}
        else:
            placement = f'p{len(self.windows) + 1}'
            if self.opens:
                top = max([w['z'] for w in self.windows.values()] + [10])
                self.windows[placement] = {'tab': message['tab'], 'title': message['item']['title'], 'z': top + 1,
                                           'resource': {**message['item']['resource'], 'picture': 0},
                                           'state': self.state, 'generation': 1,
                                           'rect': [0, 40, 1440, 860] if message.get('fit') and self.fits else [40, 80, 420, 380]}
            result = {'ok': True, 'placement': placement, 'project': self.project, 'tab': message['tab'],
                      'fitted': bool(message.get('fit') and self.fits)}
        await COMMANDS['table-window-result'](self.app, self, {'type': 'table-window-result', 'id': message['id'],
                                                               'session': message['session'],
                                                               'workspacePin': message['workspacePin'], 'result': result})
        if self.then_tab:
            self.active = self.then_tab
        self.later(self.send_report(self.report_after))

    def sent(self, kind):
        return [m for m in self.messages if m.get('type') == kind]


async def built(tmp_path, monkeypatch, **kwargs):
    from orchestrator import tables

    async def read_table(app, eid, session, revision):
        return {**TABLE, 'session': session, 'revision': revision}

    monkeypatch.setattr(tables, 'read_table', read_table)
    app = App.build('http://127.0.0.1:1', Paths.under(tmp_path), use_gpu=False)
    studio = TableStudio(app, **kwargs)
    app.accept_workspace(studio, ClientRef(CLIENT['workspaceId'], CLIENT['clientId'], CLIENT['pageEpoch'],
                                          CLIENT['connectionEpoch']))
    app.hub.clients.add(studio)
    await studio.send_report()
    return app, studio


async def asked(app, studio, tool, args, *, wait=True):
    """One request from the studio, pinned on its report at Send, as chat and voice requests are."""
    await studio.send_report()
    ticket = issue(app.hub, 'turn', asked=app.day.here(), channel='chat')
    pin = app.hub.screen.pin(studio, request_id=ticket.admission.request_id, timing='server-receive-legacy')
    ctx = ToolContext('chat', 1, None, 'show the table', asked=ticket.asked)
    ctx.client, ctx.workspace_id = studio, WORKSPACE
    ctx.admission, ctx.request_id, ctx.request_snapshot = ticket.admission, ticket.admission.request_id, pin
    with using(ticket):
        run = app.brain.registry.start(tool, args, ctx)
    if wait:
        try:
            await run.task
            await asyncio.gather(*studio.tasks)
        finally:
            ticket.seal()
    else:
        run.ticket = ticket
    return run


def windows_on(studio, tab):
    return [w for w in studio.windows.values() if w['tab'] == tab]


# --- one surface per thing ---------------------------------------------------------------------------------------------

def test_show_measurements_opens_the_window_the_users_button_opens_never_a_hash_page(tmp_path, monkeypatch):
    """Log 26-31: "I've brought up the measurements table" / "I don't see them". The page-opened answer said shown
    while the Tab held no table; now the studio is sent the same table window its Open in Measurements opens, and it is
    done only once the report shows that window."""
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        run = await asked(app, studio, 'show_measurements', {'of': 'd4'})
        assert not studio.sent('open-page'), 'a #measurements hash page is not the table window'
        (message,) = studio.sent('table-window')
        assert message['session'] == studio.project and message['tab'] == 'main'
        assert message['item']['resource'] == {'day': studio.project, 'id': 'd4', 'version': 0}
        assert run.ok is True, run.summary
        assert windows_on(studio, 'main'), 'the table window is on the Tab'
        assert 'shows' in run.summary and 'Nuclei' in run.summary and '"Main"' in run.summary
        assert 'in front' in run.summary
    asyncio.run(go())


def test_a_table_the_studio_never_shows_is_never_said_to_be_up(tmp_path, monkeypatch):
    """Log 52-63: the Assistant said the table was up three times while it was not. The studio answers, opens nothing:
    not done, and the answer says where the table is saved, never "reopen the app"."""
    monkeypatch.setattr(table_window, 'TABLE_WAIT_S', 0.4)

    async def go():
        app, studio = await built(tmp_path, monkeypatch, opens=False)
        run = await asked(app, studio, 'show_measurements', {'of': 'd4'})
        assert run.ok is False
        said = run.summary.lower()
        assert 'saved in data' in said and 'd4' in said and 'not shown' in said
        for never in ('reopen', 'quit', 'restart', 'refresh', "can't see", 'cannot see', 'open the studio'):
            assert never not in said, never
    asyncio.run(go())


def test_a_studio_that_never_answers_is_a_timeout_that_says_where_the_table_is(tmp_path, monkeypatch):
    monkeypatch.setattr(table_window, 'TABLE_WAIT_S', 0.4)

    async def go():
        app, studio = await built(tmp_path, monkeypatch, answers=False)
        run = await asked(app, studio, 'show_measurements', {'of': 'd4'})
        assert run.ok is False and 'saved in data as d4' in run.summary.lower()
        assert 'done' not in run.summary.lower() and 'reopen' not in run.summary.lower()
    asyncio.run(go())


def test_rows_that_could_not_be_read_there_are_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(table_window, 'TABLE_WAIT_S', 0.4)

    async def go():
        app, studio = await built(tmp_path, monkeypatch, state='failed')
        run = await asked(app, studio, 'show_measurements', {'of': 'd4'})
        assert run.ok is False and 'could not be read' in run.summary and 'saved in Data' in run.summary
    asyncio.run(go())


def test_a_table_under_another_window_is_said_to_be_behind_it_not_up(tmp_path, monkeypatch):
    """"In front" is what the report shows: nothing over the window. Behind is said only because it was observed."""
    monkeypatch.setattr(table_window, 'TABLE_WAIT_S', 0.4)

    async def go():
        app, studio = await built(tmp_path, monkeypatch, covered=True)
        run = await asked(app, studio, 'show_measurements', {'of': 'd4'})
        assert run.ok is False and 'behind' in run.summary and 'Note' in run.summary
    asyncio.run(go())


# --- open X in Tab Y (new if needed), fitted, in front: one action ----------------------------------------------------

def test_a_new_tab_called_measurements_with_the_table_in_it_is_one_action_confirmed_by_the_report(tmp_path, monkeypatch):
    """Log 48-60 and 74: "what I would have wanted ... a tab called measurements and it had this table already fit in
    it perfectly". One action makes the Tab, puts the table window in it fitted and in front, and is done only when the
    report shows that window in that Tab with that exact Project and revision."""
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        run = await asked(app, studio, 'show_measurements', {'of': 'd4', 'tab': 'Measurements'})
        assert run.ok is True, run.summary
        (create,) = [m for m in studio.sent('workspace-tabs') if m['action']['action'] == 'create']
        assert create['action']['title'] == 'Measurements'
        made = next(t for t in studio.tabs if t['title'] == 'Measurements')
        (message,) = studio.sent('table-window')
        assert message['tab'] == made['id'] and message['fit'] is True
        (window,) = windows_on(studio, made['id'])
        assert window['resource']['id'] == 'd4' and window['resource']['version'] == 0
        said = run.summary
        assert '"Measurements"' in said and 'new Tab' in said and 'fitted' in said and 'in front' in said
        # Saved, computed, displayed, in front and accepted are separate facts.
        for fact in ('saved: in Data as d4', 'computed: 341 rows', 'displayed: yes', 'in front: yes', 'accepted: not asked'):
            assert fact in said, fact
    asyncio.run(go())


def test_an_existing_tab_is_used_by_its_title_and_put_in_front(tmp_path, monkeypatch):
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        studio.tabs.append({'id': 'tab-results', 'title': 'Measurements'})
        run = await asked(app, studio, 'show_measurements', {'of': 'd4', 'tab': 'measurements'})
        assert run.ok is True, run.summary
        actions = [m['action']['action'] for m in studio.sent('workspace-tabs')]
        assert 'create' not in actions and 'switch' in actions
        assert windows_on(studio, 'tab-results') and studio.active == 'tab-results'
    asyncio.run(go())


def test_the_log_s_empty_measurements_tab_is_never_claimed(tmp_path, monkeypatch):
    """Log 56-57: "you created a tab called measurements, but you didn't open the table of the data". The Tab is made,
    the table never lands in it: the answer says the Tab exists and is empty of it, and where the table is saved."""
    monkeypatch.setattr(table_window, 'TABLE_WAIT_S', 0.4)

    async def go():
        app, studio = await built(tmp_path, monkeypatch, opens=False)
        run = await asked(app, studio, 'show_measurements', {'of': 'd4', 'tab': 'Measurements'})
        assert run.ok is False
        assert any(t['title'] == 'Measurements' for t in studio.tabs)
        assert '"Measurements"' in run.summary and 'saved in Data as d4' in run.summary and 'not shown' in run.summary
    asyncio.run(go())


def test_another_tab_brought_forward_before_it_showed_is_said_so(tmp_path, monkeypatch):
    monkeypatch.setattr(table_window, 'TABLE_WAIT_S', 0.4)

    async def go():
        app, studio = await built(tmp_path, monkeypatch, then_tab='main')
        run = await asked(app, studio, 'show_measurements', {'of': 'd4', 'tab': 'Measurements'})
        assert run.ok is False and 'another Tab came to the front' in run.summary and 'saved in Data as d4' in run.summary
    asyncio.run(go())


# --- the edge cases: slow, in the background, timed out ----------------------------------------------------------------

def test_a_slow_report_is_waited_for_past_the_answer_and_its_outcome_said_when_it_arrives(tmp_path, monkeypatch):
    """Acknowledge now, say the outcome when it arrives: the run is still working when the answer is written (its
    inline wait), never failed, and done once the slow report shows the table."""
    async def go():
        app, studio = await built(tmp_path, monkeypatch, report_after=0.6)
        tool = app.brain.registry.tools['show_measurements']
        assert tool.inline_ms / 1000 < table_window.TABLE_WAIT_S  # a slow report goes late, it does not fail
        # As a window: the default (beside) arranges the Tab, and that answer's own report shows it sooner here.
        run = await asked(app, studio, 'show_measurements', {'of': 'd4', 'placement': 'window'}, wait=False)
        await asyncio.sleep(0.3)
        assert run.ok is None, 'still working while the report is on its way'
        await run.task
        await asyncio.gather(*studio.tasks)
        run.ticket.seal()
        assert run.ok is True and 'shows' in run.summary
    asyncio.run(go())


def test_a_studio_in_the_background_is_waited_for_and_said_so_only_as_observed(tmp_path, monkeypatch):
    async def go():
        app, studio = await built(tmp_path, monkeypatch, in_view=False)
        run = await asked(app, studio, 'show_measurements', {'of': 'd4'}, wait=False)
        await asyncio.sleep(0.4)
        assert run.ok is None and 'in view' in (run.progress or '')
        studio.in_view = True
        await studio.send_report()
        await run.task
        await asyncio.gather(*studio.tasks)
        run.ticket.seal()
        assert run.ok is True and 'shows' in run.summary
    asyncio.run(go())


def test_a_studio_that_stays_in_the_background_is_placed_not_shown_and_never_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(table_window, 'BACKGROUND_WAIT_S', 0.5)

    async def go():
        app, studio = await built(tmp_path, monkeypatch, in_view=False)
        run = await asked(app, studio, 'show_measurements', {'of': 'd4', 'tab': 'Measurements'})
        assert run.ok is True, run.summary
        said = run.summary
        assert 'not in view' in said and 'displayed: not yet' in said and 'saved: in Data as d4' in said
        assert not said.startswith('the studio shows') and said.startswith('placed')
    asyncio.run(go())


def test_a_studio_without_the_project_workspace_is_shown_the_page_as_before(tmp_path, monkeypatch):
    """No accepted studio (an older one, a test's fixture): the Measurements page, as before; and a Tab named there is
    refused with where the table is saved."""
    from orchestrator.brain.registry import Registry
    from orchestrator.brain.toolbox.tables import add_table_tools
    from types import SimpleNamespace

    async def go():
        from orchestrator import tables

        async def read_table(app, eid, session, revision):
            return {**TABLE, 'session': session, 'revision': revision}

        monkeypatch.setattr(tables, 'read_table', read_table)
        app = App.build('http://127.0.0.1:1', Paths.under(tmp_path), use_gpu=False)
        opened = []

        async def open_page(client, page):
            opened.append(page)
            return {'shown': True, 'visible': True}

        registry = Registry()
        add_table_tools(registry, SimpleNamespace(hub=app.hub, day=app.day, open_page=open_page, workspace_tabs=None))
        ctx = ToolContext('chat', 0, None, asked=app.day.here())
        run = registry.start('show_measurements', {'of': 'd4'}, ctx)
        await run.task
        assert run.ok and opened and opened[0].startswith('#measurements/d4')
        run = registry.start('show_measurements', {'of': 'd4', 'tab': 'Measurements'}, ctx)
        await run.task
        assert run.ok is False and 'saved in Data as d4' in run.summary
    asyncio.run(go())
