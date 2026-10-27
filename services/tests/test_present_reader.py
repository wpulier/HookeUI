"""The Assistant opens things the way a person does: in the Data reader on the Tab they are on (ticket 66, D6;
orchestrator/data_reader.py, docs/architecture/on-screen.md).

Actual App, Registry, Index, Screen and COMMANDS, with one accepted synthetic socket that reports what the studio's
DOM would (its Project Tab strip, and the reader surface while it shows an item, as workspace-document-surfaces.ts
readerSurfaces does) and opens or closes the reader when it is sent a `reader` message (workspace-study.ts). No browser,
model, camera or worker: browser drawing is verified separately on the isolated instance."""
import asyncio
import copy
import json
import time

import pytest

from orchestrator import data_reader
from orchestrator.app import App
from orchestrator.brain.registry import ToolContext, ToolFailed
from orchestrator.capture import RUN
from orchestrator.commands import COMMANDS, run_tool
from orchestrator.paths import Paths
from orchestrator.ticket import issue, using
from orchestrator.workspace_observation import ClientRef
from tests.test_workspace_actions_app import CLIENT, WORKSPACE, research_fixture, saved_picture


class ReaderStudio:
    """One accepted socket: its Tab strip, and its reader on the right edge as it would report it."""

    def __init__(self, app, *, strip=True, state='rendered', shows=True, mode='side'):
        self.app, self.strip, self.state, self.shows = app, strip, state, shows
        self.mode = mode  # 'side': the right edge's reader; 'research': a researcher's Tab's (ticket 66 D3)
        self.seq, self.messages, self.tasks, self.replies = 0, [], [], []
        self.opening = 1  # the Tab strip's opening (a new one when the Project is opened again)
        self.reader = None  # the reader's row while it shows an item
        self.revision = 1
        self.generation = 1

    @property
    def project(self):
        return self.app.hub.resources.session()

    def strip_row(self):
        return {'id': 'workspace-tabs', 'rootId': 'synthetic-dom:tabs', 'workspaceId': CLIENT['workspaceId'],
                'kind': 'panel', 'lifetimeGeneration': 'synthetic-fixed-root:tabs', 'sourceGeneration': self.opening,
                'revision': self.seq, 'open': True, 'connected': True, 'visible': True, 'rect': [0, 0, 1440, 40],
                'space': 'workspace',
                'view': {'legacy': {'schema': 'studio-surface-v1', 'scope': 'dom-observation', 'title': 'Project Tabs',
                                    'workspace': {'project': self.project, 'tab': 'main'}}},
                'propertyVersions': {}, 'propertyOwners': {}, 'controls': [],
                'load': {'state': 'unknown', 'unknown': ['native-load-unobserved']}, 'unknown': ['synthetic-structural-dom-only']}

    def reader_row(self, resource, title):
        self.revision += 1
        root = 'data-reader' if self.mode == 'side' else f'data-reader-{self.mode}'
        return {'id': f'document:reader-{self.mode}', 'rootId': root, 'workspaceId': CLIENT['workspaceId'],
                'kind': 'document', 'lifetimeGeneration': 'synthetic-root:reader', 'sourceGeneration': self.generation,
                'revision': self.revision, 'open': True, 'connected': True, 'visible': True, 'rect': [840, 40, 600, 860],
                'z': 105, 'space': 'workspace',
                'view': {'legacy': {'schema': 'studio-surface-v1', 'scope': 'dom-observation',
                                    'title': f'Reader · {title} · Page', 'shows': resource['id'],
                                    'document': {'project': self.project, 'tab': 'main',
                                                 'placement': f'reader:{self.mode}:main',
                                                 'representation': 'reader' if self.mode == 'side' else f'reader-{self.mode}',
                                                 'resource': {**resource, 'picture': None}, 'state': self.state,
                                                 'focused': False}}},
                'propertyVersions': {}, 'propertyOwners': {}, 'controls': [],
                'load': {'state': 'unknown', 'unknown': ['native-load-unobserved']}, 'unknown': ['synthetic-structural-dom-only']}

    def hear(self, message):
        self.messages.append(copy.deepcopy(message))
        if message.get('type') == 'reader':
            # As workspace-study.ts: only for this Project's opening (the pin), as a Data row's click opens it.
            pin = message['workspacePin']
            assert pin['client'] == CLIENT and pin['id'] == 'workspace-tabs' and message['session'] == self.project
            if pin['sourceGeneration'] != self.opening:
                return
            if message['do'] == 'open' and self.shows:
                self.generation += 1
                self.reader = self.reader_row(message['item']['resource'], message['item'].get('title') or 'Item')
            elif message['do'] == 'close':
                self.reader = None  # the reader's surface leaves the report once it shows nothing (readerSurfaces)
        elif message.get('type') != 'screen-ask':
            return
        self.tasks.append(asyncio.get_running_loop().create_task(self.send_report()))

    def offer(self, message):
        self.hear(json.loads(message))

    async def send_report(self):
        self.seq += 1
        items = ([self.strip_row()] if self.strip else []) + ([copy.deepcopy(self.reader)] if self.reader else [])
        report = {
            'type': 'screen', 'schema': 'screen-observation-v2', 'client': dict(CLIENT), 'appRunId': RUN,
            'seq': self.seq, 'layoutRevision': self.seq, 'capturedMonoMs': time.monotonic() * 1000, 'visible': True,
            'complete': False, 'omitted': {'items': 1, 'sources': 0, 'targets': 0, 'edges': 0},
            'viewport': {'layoutSize': [1440, 900], 'visualRect': [0, 0, 1440, 900], 'visualScale': 1,
                         'documentScroll': [0, 0], 'dpr': 1, 'visible': True, 'documentFocused': True,
                         'activeSurface': 'workspace'},
            'items': items, 'sources': [], 'targets': [], 'edges': [],
            'interaction': {'availability': 'partial', 'unknown': ['synthetic-hit-registration-unavailable']},
            'commandResults': [], 'applied': [], 'user': [],
        }
        reply = await COMMANDS['screen'](self.app, self, report)
        self.replies.append(reply)
        assert reply.get('ok') is True, f'SETUP: structural report refused by actual normalization: {reply}'

    def sent(self, verb):
        return [m for m in self.messages if m.get('type') == 'reader' and m['do'] == verb]


async def built(tmp_path, **kwargs):
    app = App.build('http://127.0.0.1:1', Paths.under(tmp_path), use_gpu=False)
    studio = ReaderStudio(app, **kwargs)
    app.accept_workspace(studio, ClientRef(CLIENT['workspaceId'], CLIENT['clientId'], CLIENT['pageEpoch'],
                                          CLIENT['connectionEpoch']))
    original = app.hub.broadcast

    def broadcast(message):
        original(message)
        studio.hear(message)

    app.hub.broadcast = app.hub.screen.broadcast = broadcast
    await studio.send_report()
    return app, studio


async def asked(app, studio, tool, args):
    """One request from the studio, pinned on its report at Send, as chat and voice requests are."""
    await studio.send_report()
    ticket = issue(app.hub, 'turn', asked=app.day.here(), channel='chat')
    try:
        pin = app.hub.screen.pin(studio, request_id=ticket.admission.request_id, timing='server-receive-legacy')
        ctx = ToolContext('chat', 1, None, 'open it', asked=ticket.asked)
        ctx.client, ctx.workspace_id = studio, WORKSPACE
        ctx.admission, ctx.request_id, ctx.request_snapshot = ticket.admission, ticket.admission.request_id, pin
        with using(ticket):
            run = app.brain.registry.start(tool, args, ctx)
            await run.task
            await asyncio.gather(*studio.tasks)
    finally:
        ticket.seal()
    return run


def test_present_open_shows_a_saved_result_in_the_reader_and_is_done_when_the_report_shows_it(tmp_path):
    async def go():
        app, studio = await built(tmp_path)
        entry, _pixels = saved_picture(app)
        run = await asked(app, studio, 'present', {'what': entry.id, 'do': 'open'})
        assert run.ok is True, run.summary
        (message,) = studio.sent('open')
        assert message['item']['resource'] == {'day': entry.day, 'id': entry.id, 'version': entry.version}
        assert message['entry']['id'] == entry.id and message['entry']['kind'] == 'result'  # as Data lists it
        assert 'in the reader' in run.summary and 'not made again' in run.summary
        assert run.resource['id'] == entry.id and run.resource['operation'] == 'reopened'
        # Not the magnifier or a window: nothing else was sent to the studio.
        assert not [m for m in studio.messages if m.get('type') in ('magnifier', 'present')]
        assert app.magnifier.picture_now() is None
        report = app.hub.screen.completion_report(studio)
        assert data_reader.shown(report, data_reader.ref_of(entry)) is not None
    asyncio.run(go())


def test_an_open_the_studio_never_shows_in_the_reader_is_said_not_done(tmp_path, monkeypatch):
    monkeypatch.setattr(data_reader, 'READER_WAIT_S', 0.4)

    async def go():
        app, studio = await built(tmp_path, shows=False)
        entry, _pixels = saved_picture(app)
        run = await asked(app, studio, 'present', {'what': entry.id, 'do': 'open'})
        assert run.ok is False and 'not open in the reader' in run.summary
        assert studio.sent('open') and run.resource is None
    asyncio.run(go())


def test_content_that_could_not_be_drawn_is_a_failure_and_content_still_loading_is_said(tmp_path, monkeypatch):
    monkeypatch.setattr(data_reader, 'READER_WAIT_S', 0.4)

    async def go():
        app, studio = await built(tmp_path, state='failed')
        entry, _pixels = saved_picture(app)
        run = await asked(app, studio, 'present', {'what': entry.id, 'do': 'open'})
        assert run.ok is False and 'could not be drawn' in run.summary
        studio.state, studio.reader = 'loading', None
        run = await asked(app, studio, 'present', {'what': entry.id, 'do': 'open'})
        assert run.ok is True and 'still loading' in run.summary
    asyncio.run(go())


def test_a_studio_without_the_reader_is_shown_things_as_before(tmp_path, monkeypatch):
    """No Tab strip in the report: no reader message; the kind's older presentation (screen.WITHOUT_READER) is used."""
    async def go():
        app, studio = await built(tmp_path, strip=False)
        entry, _pixels = saved_picture(app)
        run = await asked(app, studio, 'present', {'what': entry.id, 'do': 'open'})
        assert not studio.sent('open')
        assert 'in the reader' not in (run.summary or '')
    asyncio.run(go())


def test_the_reader_is_closed_by_name_and_by_close_that_and_done_once_it_shows_nothing(tmp_path):
    async def go():
        app, studio = await built(tmp_path)
        entry, _pixels = saved_picture(app)
        assert (await asked(app, studio, 'present', {'what': entry.id, 'do': 'open'})).ok
        run = await asked(app, studio, 'present', {'what': entry.id, 'do': 'close'})
        assert run.ok is True, run.summary
        assert 'closed the reader' in run.summary and studio.reader is None
        (closed,) = studio.sent('close')
        assert closed['item']['resource'] == {'day': entry.day, 'id': entry.id, 'version': entry.version}
        # "Close that": the reader is the window in front.
        assert (await asked(app, studio, 'present', {'what': entry.id, 'do': 'open'})).ok
        run = await asked(app, studio, 'present', {'what': 'that', 'do': 'close'})
        assert run.ok is True and studio.reader is None, run.summary
        # It does not move: said, nothing sent.
        assert (await asked(app, studio, 'present', {'what': entry.id, 'do': 'open'})).ok
        run = await asked(app, studio, 'present', {'what': 'document:reader-side', 'do': 'move', 'rect': [0, 0, 300, 300]})
        assert run.ok is False and 'sits on the right' in run.summary and studio.reader is not None
    asyncio.run(go())


def test_a_project_opened_again_while_it_opens_refuses_rather_than_open_it_there(tmp_path, monkeypatch):
    monkeypatch.setattr(data_reader, 'READER_WAIT_S', 0.4)

    async def go():
        app, studio = await built(tmp_path, shows=False)
        entry, _pixels = saved_picture(app)
        original = studio.hear

        def reopened(message):
            if message.get('type') == 'reader':
                studio.opening += 1  # the workspace was opened again before the studio acted on it, and says so
                studio.tasks.append(asyncio.get_running_loop().create_task(studio.send_report()))
            original(message)

        studio.hear = reopened
        run = await asked(app, studio, 'present', {'what': entry.id, 'do': 'open'})
        assert run.ok is False and 'opened again' in run.summary
        assert studio.reader is None
    asyncio.run(go())


def test_a_tool_run_no_studio_sent_opens_in_the_reader_of_the_studio_whose_report_counts(tmp_path):
    """The sim's `tool` command (run_tool, no client): the studio that counts, as present's other changes address it."""
    async def go():
        app, studio = await built(tmp_path)
        entry, _pixels = saved_picture(app)
        reply = await run_tool(app, 'present', {'what': entry.id, 'do': 'open'})
        await asyncio.gather(*studio.tasks)
        assert reply['ok'] is True and 'in the reader' in reply['summary'], reply
        assert studio.sent('open') and studio.reader['view']['legacy']['shows'] == entry.id
    asyncio.run(go())


def test_show_result_opens_a_researchers_work_in_the_reader(tmp_path):
    async def go():
        app, studio = await built(tmp_path)
        _manager, session = research_fixture(app)
        run = await asked(app, studio, 'show_result', {'researcher': session.rid})
        assert run.ok is True, run.summary
        (message,) = studio.sent('open')
        from orchestrator.presenter import first_shown, items
        output = items(session)[first_shown(session)]  # the one show_result opens first: its newest picture
        assert output['kind'] == 'image'
        assert message['item']['resource']['id'] == f"{session.rid}:{output['id']}"  # its picture, as Data lists it
        assert message['entry']['kind'] == 'figure' and 'in the reader' in run.summary
        # close_result closes the reader showing a researcher's work.
        run = await asked(app, studio, 'close_result', {})
        assert run.ok is True and studio.reader is None, run.summary
    asyncio.run(go())


def test_every_kind_the_reader_shows_says_how_a_studio_without_it_shows_it():
    from orchestrator.produces import KINDS, role_of
    from orchestrator.screen import PRESENTED_AS, READER, TAKES, WITHOUT_READER, presented

    for kind in KINDS:
        shown_as, why = presented(kind, reader=False)
        assert shown_as != READER and ((shown_as in TAKES) if shown_as else bool(why)), kind
        if PRESENTED_AS[kind][0] == READER:
            assert role_of(kind) is not None, f"{kind} opens in the reader but Data does not list it"
            assert kind in WITHOUT_READER, kind
    # Outlines and tracks stay layers (show draws them on the picture); the kinds Data does not list are as before.
    assert PRESENTED_AS['population'][0] == 'population' and PRESENTED_AS['tracks'][0] == 'tracks'
    assert PRESENTED_AS['member'][0] == PRESENTED_AS['job'][0] == 'magnifier'


def test_cell_outlines_open_in_the_reader_and_show_on_the_picture(tmp_path, monkeypatch):
    """Open puts outlines in the reader, as their Data row does; show stays the layer drawn on the picture (the reader's
    Show on picture), never the reader; close closes the reader while it shows them."""
    from orchestrator import screen
    from tests.test_workspace_actions_app import comparison_fixture

    monkeypatch.setattr(screen, 'CONFIRM_S', 0.3)  # this fixture reports no layer root: the layer's wait ends sooner

    async def go():
        app, studio = await built(tmp_path)
        population, _ids, _originals = comparison_fixture(app)
        run = await asked(app, studio, 'present', {'what': population.id, 'do': 'open'})
        assert run.ok is True, run.summary
        (message,) = studio.sent('open')
        assert message['item']['resource']['id'] == population.id and message['entry']['kind'] == 'population'
        await asked(app, studio, 'present', {'what': population.id, 'do': 'show'})
        assert len(studio.sent('open')) == 1  # shown on the picture: no reader message
        run = await asked(app, studio, 'present', {'what': population.id, 'do': 'close'})
        assert run.ok is True and studio.reader is None and 'closed the reader' in run.summary, run.summary
    asyncio.run(go())


def test_an_open_on_a_researchers_tab_is_done_when_its_reader_shows_it_and_is_not_closed_alone(tmp_path):
    """On a researcher's Tab the studio opens a thing in that Tab's reader (ticket 66 D3), reported as
    `document:reader-research`: the open is done when it shows it, and that reader is part of its Tab."""
    async def go():
        app, studio = await built(tmp_path, mode='research')
        entry, _pixels = saved_picture(app)
        run = await asked(app, studio, 'present', {'what': entry.id, 'do': 'open'})
        assert run.ok is True, run.summary
        report = app.hub.screen.completion_report(studio)
        item = data_reader.shown(report, data_reader.ref_of(entry))
        assert item is not None and item['id'] == 'document:reader-research'
        with pytest.raises(ToolFailed, match="part of its Tab"):
            await data_reader.close_in_reader(studio, item, lambda: None)
    asyncio.run(go())
