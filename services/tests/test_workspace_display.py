"""How Hooke shows what the models made, and knows where everything is (docs/architecture/on-screen.md "The workspace
model", "Placing things", "Web pages and videos"; the live partner spec 4.8, horizontal 2).

Will, 29 Oct: "HOW we display in various ways the data produced by the models is critical and not an afterthought at
all." His display journey (bench/voice_journeys/plans/will-display.json): open the cell results in a new Tab, put the
table beside the live view instead, show the outlines as an overlay, which Tab has the measurements, a YouTube video
about stomata in a window, only the video open in this Tab.

Actual App, Registry, Screen, tabs broker and COMMANDS, with the synthetic studio of test_table_window.py, whose Tab
strip here reports each Tab's specimen and what it holds by kind (workspace-model.ts), and which keeps Tabs, arranges,
keeps only one thing and opens web windows as workspace-views.ts agentCommand and web-window.ts do. No browser, model,
camera, web or worker: the drawing itself is verified separately on the isolated instance."""
import asyncio
import copy
import json

import pytest

from orchestrator import table_window, web_window, workspace_tabs
from orchestrator.brain import context
from orchestrator.commands import COMMANDS
from orchestrator.placement import DEFAULTS, PLACEMENTS, beside_shown, default, kind_of_entry, only_shown
from orchestrator.presentations import PresentationError, SourceRef
from orchestrator.video_search import FoundVideo, NotFound, find
from orchestrator.voice import hands as hands_mod
from orchestrator.voice.hands import intent_of, screen_facts
from orchestrator.web_hosts import embed, framed
from orchestrator.workspace_observation import _legacy, front, tabs_holding
from tests.test_table_window import TableStudio, _row, asked, built as table_built  # noqa: F401

STAGE = [0, 40, 1440, 860]
VIDEO = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'


class DisplayStudio(TableStudio):
    """The studio's Tab strip with each Tab's specimen and holds, its document windows (masks, tables, web windows),
    and the Tab actions placing uses: create (a canvas, no specimen), switch, add, move, arrange (beside), only."""

    def __init__(self, app, *, arranges=True, **kwargs):
        super().__init__(app, **kwargs)
        self.arranges = arranges
        self.specimens = {'main': {'state': 'fill'}}

    def kind_of(self, window):
        return {'masks': 'outlines', 'table': 'table', 'web-page': 'web-page', 'video': 'video'}.get(
            window.get('representation', 'table'), 'document')

    def holds(self, tab):
        out = {}
        for w in self.windows.values():
            if w['tab'] == tab:
                out[self.kind_of(w)] = out.get(self.kind_of(w), 0) + 1
        return out

    def items(self):
        rows = super().items()
        rows[0]['view']['legacy']['workspace']['tabs'] = [
            {'id': t['id'], 'title': t['title'], 'front': t['id'] == self.active,
             'specimen': copy.deepcopy(self.specimens.get(t['id'], {'state': 'hidden'})),
             'kind': 'work', 'holds': self.holds(t['id'])} for t in self.tabs]
        for row in rows:  # the windows say how each is shown (masks, table, web-page, video)
            placement = row['id'].removeprefix('document:')
            window = self.windows.get(placement)
            if window and window.get('representation'):
                row['view']['legacy']['document']['representation'] = window['representation']
                if window['representation'] in ('web-page', 'video'):
                    row['view']['legacy']['title'] = window['title']
        return rows

    def listed(self):
        return [{**t, 'open': True, 'selected': t['id'] == self.active, 'kind': 'work', 'omittedItems': 0,
                 'specimen': self.specimens.get(t['id'], {'state': 'hidden'})['state'],
                 'holds': self.holds(t['id']),
                 'items': [{'placement': p, 'kind': self.kind_of(w), 'title': w['title'],
                            'presentation': w.get('representation', 'table'),
                            'resource': {k: w['resource'][k] for k in ('day', 'id', 'version')}}
                           for p, w in self.windows.items() if w['tab'] == t['id']]} for t in self.tabs]

    async def tabs_reply(self, message):
        action = message['action']
        kind = action['action']
        ok, error = True, None
        if kind == 'create':
            tab = {'id': f'tab-{len(self.tabs)}', 'title': action['title']}
            self.tabs.append(tab)
            self.active = tab['id']
            self.specimens[tab['id']] = {'state': 'hidden'}  # a new Tab is a canvas for data, with no specimen
        elif kind == 'switch':
            self.active = action['tab']
        elif kind == 'add':
            item = action['item']
            placement = f"p{len(self.windows) + 1}"
            self.windows[placement] = {'tab': action['tab'], 'title': item['title'], 'z': 11 + len(self.windows),
                                       'resource': {**item['resource'], 'picture': 0}, 'state': 'rendered',
                                       'generation': 1, 'rect': [40, 80, 420, 380],
                                       'representation': item['presentation']}
        elif kind == 'move':
            self.windows[action['placement']]['tab'] = action['tab']
        elif kind == 'arrange' and self.arranges:
            here = [w for w in self.windows.values() if w['tab'] == action['tab']]
            n = len(here) + (self.specimens.get(action['tab'], {}).get('state') != 'hidden')
            width = 1440 // max(1, n)
            at = 0
            if self.specimens.get(action['tab'], {}).get('state') != 'hidden':
                self.specimens[action['tab']] = {'state': 'float', 'rect': [0, 40, width - 12, 800]}
                at = 1
            for i, w in enumerate(here):
                w['rect'] = [(at + i) * width, 40, width - 12, 800]
        elif kind == 'only':
            keep = set(action['keep'])
            for placement in [p for p, w in self.windows.items() if w['tab'] == action['tab'] and p not in keep]:
                del self.windows[placement]
            self.specimens[action['tab']] = ({'state': 'float', 'rect': [16, 620, 240, 216]} if action['tab'] == 'main'
                                            else {'state': 'hidden'})
        result = {'ok': ok, 'session': self.project, 'studioVisible': self.in_view, 'tabs': self.listed()}
        if error:
            result = {'ok': False, 'error': error}
        await self.send_report()
        await COMMANDS['workspace-tabs-result'](self.app, self, {'type': 'workspace-tabs-result', 'id': message['id'],
                                                                 'session': message['session'],
                                                                 'workspacePin': message['workspacePin'], 'result': result})

    def hear(self, message):
        if isinstance(message, dict) and message.get('type') == 'web-window':
            self.messages.append(copy.deepcopy(message))
            self.later(self.web_reply(message))
            return
        super().hear(message)

    async def web_reply(self, message):
        """web-window.ts openWebWindow: the sandboxed window on the Tab it was asked for."""
        item = message['item']
        placement = f'p{len(self.windows) + 1}'
        if message['tab'] != self.active:
            result = {'ok': False, 'error': 'Another Tab is in front now.'}
        else:
            self.windows[placement] = {'tab': message['tab'], 'title': item['title'], 'z': 50 + len(self.windows),
                                       'resource': {**item['resource'], 'picture': None}, 'state': 'rendered',
                                       'generation': 1, 'rect': [300, 120, 640, 400], 'representation': item['kind']}
            result = {'ok': True, 'placement': placement, 'project': self.project, 'tab': message['tab']}
        await COMMANDS['web-window-result'](self.app, self, {'type': 'web-window-result', 'id': message['id'],
                                                             'session': message['session'],
                                                             'workspacePin': message['workspacePin'], 'result': result})
        self.later(self.send_report())


async def built(tmp_path, monkeypatch, **kwargs):
    import tests.test_table_window as table

    monkeypatch.setattr(table, 'TableStudio', DisplayStudio)
    return await table_built(tmp_path, monkeypatch, **kwargs)


def wire(app, studio):
    return app.hub.screen.observation(studio).wire()


def population(app, monkeypatch=None, title='Leaf epidermis cells'):
    """A population of cells (e3) as outline_cells keeps one: in the evidence and in the index, at revision 0. The
    rest of the app reads it as it reads a real one (show_measurements, tabs add); its pixels are not needed here."""
    from types import SimpleNamespace

    from orchestrator import population as population_mod

    cells = SimpleNamespace(id='e3', revision=0, title=title)
    entry = SimpleNamespace(id='e3', kind='population', version=0, title=title, state='ready', summary='')
    resources_get, evidence_get = app.hub.resources.get, app.hub.evidence.get
    patch = monkeypatch.setattr
    patch(population_mod, 'resolve', lambda hub, ref, ctx=None: cells if ref in ('e3', None, '') else None)
    patch(app.hub.evidence, 'get', lambda eid, *a, **k: cells if eid == 'e3' else evidence_get(eid, *a, **k))
    patch(app.hub.resources, 'get', lambda rid, *a, **k: entry if rid == 'e3' else resources_get(rid, *a, **k))
    return 'e3'


# --- the vocabulary and its defaults -----------------------------------------------------------------------------------

def test_one_placement_vocabulary_with_a_default_for_every_kind_of_output():
    assert PLACEMENTS == ('new-tab', 'beside', 'overlay', 'window', 'replace', 'close', 'only')
    assert default('outlines') == 'overlay' and default('tracks') == 'overlay'
    assert default('table') == 'beside' and default('reference') == 'beside'
    assert default('document') == 'new-tab'
    assert default('video') == 'window' and default('web-page') == 'window'
    assert set(DEFAULTS.values()) <= set(PLACEMENTS)
    assert kind_of_entry('population') == 'outlines' and kind_of_entry('dataset-table') == 'table'
    assert kind_of_entry('result', media='video') == 'video' and kind_of_entry('result', web={'page': 'x'}) == 'reference'


def test_every_tool_that_places_says_the_same_words():
    from orchestrator.brain.toolbox import build, Services  # noqa: F401
    from tests.test_table_window import App, Paths  # noqa: F401

    app = App.build('http://127.0.0.1:1', Paths.under(__import__('pathlib').Path('/tmp/hooke-display-words')),
                    use_gpu=False)
    tools = app.brain.registry.tools
    present = tools['present'].params['properties']['placement']
    assert present['enum'] == list(PLACEMENTS)
    assert set(tools['show_measurements'].params['properties']['placement']['enum']) <= set(PLACEMENTS)
    assert set(tools['show_web'].params['properties']['placement']['enum']) <= set(PLACEMENTS)
    assert 'arrange' in tools['tabs'].params['properties']['action']['enum']
    assert 'only' in tools['tabs'].params['properties']['action']['enum']
    for name in ('present', 'show_measurements', 'show_web', 'tabs'):
        description = tools[name].description.lower()
        for internal in ('workspace report', 'revision clash', 'ask again in a moment'):
            assert internal not in description, (name, internal)


# --- the workspace model: what each Tab holds, by kind ----------------------------------------------------------------

def test_the_tab_strip_says_what_each_tab_holds_and_a_bad_count_drops_only_itself():
    out = _legacy({'schema': 'studio-surface-v1', 'scope': 'dom-observation', 'workspace': {
        'project': 'p', 'tab': 'main', 'tabs': [
            {'id': 'main', 'title': 'Main', 'front': True, 'kind': 'work', 'holds': {'table': 1, 'magnifier': 1}},
            {'id': 't1', 'title': 'Cell results', 'front': False, 'kind': 'bogus',
             'holds': {'outlines': 1, 'spaceship': 3, 'table': -2, 'graph': 'x'}}]}})
    tabs = out['workspace']['tabs']
    assert tabs[0]['holds'] == {'table': 1, 'magnifier': 1} and tabs[0]['kind'] == 'work'
    assert tabs[1]['holds'] == {'outlines': 1} and 'kind' not in tabs[1], 'one bad part never takes down the whole'


def test_which_tab_has_the_measurements_is_read_from_kinds_never_titles(tmp_path, monkeypatch):
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        eid = population(app, monkeypatch)
        studio.tabs.append({'id': 'tab-1', 'title': 'Measurements'})  # a title that says table, holding none
        studio.tabs.append({'id': 'tab-2', 'title': 'Notes'})
        studio.windows['p9'] = {'tab': 'tab-2', 'title': 'Leaf cells', 'z': 12, 'state': 'rendered', 'generation': 1,
                                'rect': [40, 80, 420, 380], 'representation': 'table',
                                'resource': {'day': studio.project, 'id': eid, 'version': None, 'picture': 0}}
        await studio.send_report()
        assert [t['title'] for t in tabs_holding(wire(app, studio), 'table')] == ['Notes']
        run = await asked(app, studio, 'tabs', {'action': 'list', 'kind': 'table'})
        assert run.ok is True, run.summary
        listed = json.loads(run.summary)
        assert listed['holding'] == ['Notes'] and [t['id'] for t in listed['tabs']] == ['tab-2']
        (item,) = listed['tabs'][0]['items']
        assert item['kind'] == 'table' and item['resourceKind'] == 'population', 'its kind as the index keeps it'
        none = json.loads((await asked(app, studio, 'tabs', {'action': 'list', 'kind': 'video'})).summary)
        assert none['tabs'] == [] and none['holding'] == 'no Tab holds a video'
        # The brain and the voice read the same: the Tab in front and every other Tab with what it holds.
        pin = app.hub.screen.pin(studio, request_id='voice-context:holds', timing='server-receive-legacy')
        lines = context._voice_windows(pin)
        assert lines[0] == 'Tab "Main" of this Project, the specimen fills it, behind its windows'
        assert lines[1] == 'Other Tabs: "Measurements" (empty), "Notes" (a table)'
        said = context._screen(app.brain, False, request_snapshot=pin)
        assert 'Other Tabs: "Measurements" (empty), "Notes" (a table).' in said
    asyncio.run(go())


def test_front_says_what_the_tab_in_front_holds_its_overlays_and_magnifiers():
    layer = {'id': 'layer:e3', 'open': True, 'visible': True,
             'view': {'legacy': {'title': 'Leaf cells', 'members': {'count': 142, 'drawn': 142}}}}
    live = {'id': 'w-1', 'open': True, 'visible': True, 'sourceKey': 's1', 'kind': 'presentation'}
    strip = {'id': 'workspace-tabs', 'view': {'legacy': {'workspace': {'project': 'p', 'tab': 'main', 'tabs': [
        {'id': 'main', 'title': 'Main', 'front': True, 'specimen': {'state': 'fill'}, 'holds': {'table': 1}}]}}}}
    wire_ = {'items': [strip, layer, live], 'sources': [{'key': 's1', 'kind': 'live'}],
             'viewport': {'visible': True, 'documentFocused': True}}
    shown = front(wire_)
    assert shown['holds'] == {'table': 1}
    assert shown['overlays'] == [{'id': 'layer:e3', 'kind': 'outlines', 'title': 'Leaf cells', 'members': 142}]
    assert shown['magnifiers'] == ['w-1']
    assert context.holds_words({'table': 1, 'outlines': 1}) == 'a table and cell outlines'
    assert context.holds_words({'image': 2, 'video': 1, 'note': 1}) == '2 images, a video and a note'
    assert context.overlay_words(shown['overlays']) == 'cell outlines (142)'
    hidden = copy.deepcopy(wire_)
    hidden['items'][0]['view']['legacy']['workspace']['tabs'][0]['specimen'] = {'state': 'hidden'}
    assert front(hidden)['overlays'] is None, 'nothing is drawn on a specimen the Tab does not show'


# --- placing the table -------------------------------------------------------------------------------------------------

def test_open_the_cell_results_in_a_new_tab_gives_their_outlines_and_their_table_side_by_side(tmp_path, monkeypatch):
    """will-display step 2: a Tab of their own, a canvas with no specimen, the outlines on their capture and the
    table beside them, like Will's own "Cell outlines" Tab."""
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        eid = population(app, monkeypatch)
        run = await asked(app, studio, 'show_measurements', {'of': eid, 'placement': 'new-tab'})
        assert run.ok is True, run.summary
        tab = next(t for t in studio.tabs if t['title'] == 'Cell results')
        assert studio.active == tab['id'] and studio.specimens[tab['id']] == {'state': 'hidden'}
        shown = sorted(w.get('representation', 'table') for w in studio.windows.values() if w['tab'] == tab['id'])
        assert shown == ['masks', 'table']
        assert 'new Tab "Cell results"' in run.summary and "outlines on their capture" in run.summary
        actions = [m['action']['action'] for m in studio.sent('workspace-tabs')]
        assert actions[:2] == ['list', 'create'] and 'add' in actions and actions[-1] == 'arrange'
    asyncio.run(go())


def test_the_table_beside_the_live_view_instead_moves_it_and_is_done_once_they_are_side_by_side(tmp_path, monkeypatch):
    """will-display step 3, after step 2: the table moves to Main and sits beside the live view."""
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        eid = population(app, monkeypatch)
        assert (await asked(app, studio, 'show_measurements', {'of': eid, 'placement': 'new-tab'})).ok
        run = await asked(app, studio, 'show_measurements', {'of': eid, 'placement': 'beside', 'tab': 'Main',
                                                             'instead': True})
        assert run.ok is True, run.summary
        tables = [w for w in studio.windows.values() if w.get('representation', 'table') == 'table']
        assert [w['tab'] for w in tables] == ['main'], 'moved, not copied'
        assert studio.specimens['main']['state'] == 'float'
        assert 'beside the live view' in run.summary and 'moved there' in run.summary
        ok, _ = beside_shown(wire(app, studio), 'main', ['document:' + next(
            p for p, w in studio.windows.items() if w.get('representation', 'table') == 'table')])
        assert ok
    asyncio.run(go())


def test_a_table_never_shown_side_by_side_is_not_said_to_be_beside(tmp_path, monkeypatch):
    monkeypatch.setattr(table_window, 'TABLE_WAIT_S', 0.4)

    async def go():
        app, studio = await built(tmp_path, monkeypatch, arranges=False)
        eid = population(app, monkeypatch)
        run = await asked(app, studio, 'show_measurements', {'of': eid})
        assert run.ok is False
        assert 'not side by side' in run.summary.lower() and 'saved in data' in run.summary.lower()
    asyncio.run(go())


def test_a_table_is_not_an_overlay_its_outlines_are(tmp_path, monkeypatch):
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        eid = population(app, monkeypatch)
        run = await asked(app, studio, 'show_measurements', {'of': eid, 'placement': 'overlay'})
        assert run.ok is False and 'outlines are' in run.summary
        assert not studio.sent('table-window')
    asyncio.run(go())


# --- only, on a Tab ---------------------------------------------------------------------------------------------------

def test_only_closes_everything_else_and_puts_the_specimen_away_done_on_the_report(tmp_path, monkeypatch):
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        for p, title in (('p1', 'Leaf cells'), ('p2', 'Stomata video')):
            studio.windows[p] = {'tab': 'main', 'title': title, 'z': 12, 'state': 'rendered', 'generation': 1,
                                 'rect': [40, 80, 420, 380], 'representation': 'table',
                                 'resource': {'day': studio.project, 'id': f'd{p[1]}', 'version': 0, 'picture': 0}}
        await studio.send_report()
        run = await asked(app, studio, 'tabs', {'action': 'only', 'tab': 'Main', 'keep': ['document:p2']})
        assert run.ok is True, run.summary
        assert studio.sent('workspace-tabs')[-1]['action']['tab'] == 'main', 'a Tab named by its title, sent by its id'
        assert list(studio.windows) == ['p2'] and studio.specimens['main']['state'] == 'float', 'Main floats, never hides'
        assert json.loads(run.summary)['shown']['windows'] == ['document:p2']
        intent = intent_of('tabs', {'action': 'only', 'tab': 'main', 'keep': ['document:p2']})
        facts = screen_facts(wire(app, studio))
        assert hands_mod.expected(intent, facts) is True
        assert hands_mod.said(intent, {'result': 'verified'}, facts, None) == (
            'Done: only that is open in the Main Tab; the live view floats small.')
    asyncio.run(go())


def test_only_that_the_report_never_shows_is_not_done(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_tabs, 'PLACED_WAIT_S', 0.3)

    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        studio.windows['p1'] = {'tab': 'main', 'title': 'Leaf cells', 'z': 12, 'state': 'rendered', 'generation': 1,
                                'rect': [40, 80, 420, 380], 'representation': 'table',
                                'resource': {'day': studio.project, 'id': 'd1', 'version': 0, 'picture': 0}}
        studio.windows['p2'] = copy.deepcopy(studio.windows['p1'])
        await studio.send_report()
        original = studio.tabs_reply

        async def ignores_only(message):  # the save is acknowledged, but nothing closes
            if message['action']['action'] == 'only':
                message = {**message, 'action': {**message['action'], 'action': 'list'}}
            await original(message)

        studio.tabs_reply = ignores_only
        run = await asked(app, studio, 'tabs', {'action': 'only', 'tab': 'main', 'keep': ['p2']})
        assert run.ok is False and 'still open there' in run.summary and 'still fills it' in run.summary
        assert only_shown(wire(app, studio), 'main', ['p2'], home=True)[0] is False
    asyncio.run(go())


# --- web pages and videos ---------------------------------------------------------------------------------------------

def test_only_an_allowed_hosts_framed_address_is_a_web_source():
    shown = embed(VIDEO)
    assert shown['url'] == 'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ' and shown['site'] == 'youtube.com'
    assert embed('https://youtu.be/dQw4w9WgXcQ')['url'] == shown['url']
    assert embed('https://vimeo.com/76979871')['url'] == 'https://player.vimeo.com/video/76979871'
    assert embed('https://en.wikipedia.org/wiki/Stoma')['kind'] == 'web-page'
    for refused in ('https://evil.example/watch?v=dQw4w9WgXcQ', 'https://www.youtube.com/@channel',
                    'javascript:alert(1)', 'https://en.wikipedia.org:8443/wiki/Stoma', 'http://en.wikipedia.org/wiki/x',
                    'https://user:pw@www.youtube.com/watch?v=dQw4w9WgXcQ', 'https://www.youtube.com/watch?v=short'):
        assert embed(refused) is None, refused
    assert framed(shown['url'], 'video') and not framed(VIDEO, 'video'), 'the frame loads the embed only'

    def source(kind, url, basis='web'):
        return {'key': 'web:x', 'kind': kind, 'media': 'external-reference', 'availability': 'temporary',
                'identity': {'url': url}, 'provenance': {'basis': basis}}

    assert SourceRef.from_wire(source('video', shown['url'])).kind == 'video'
    assert SourceRef.from_wire(source('web-page', 'https://en.wikipedia.org/wiki/Stoma')).kind == 'web-page'
    for kind, url in (('web-page', 'https://evil.example/wiki/Stoma'), ('video', VIDEO),
                      ('video', 'https://player.vimeo.com/video/abc'), ('web-page', 'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ')):
        with pytest.raises(PresentationError):
            SourceRef.from_wire(source(kind, url))
    # An app-delivered video (no web basis) is unchanged.
    assert SourceRef.from_wire(source('video', '/x', basis='retained')).kind == 'video'


def test_a_video_found_is_checked_before_it_is_shown_the_model_judges_the_code_checks():
    async def go():
        async def lookup(query, *, videos, observe=None):
            assert videos is True
            return [{'title': 'Stomata', 'page': 'https://example.com/stomata-video', 'description': 'not a video page'},
                    {'title': 'Gone', 'page': 'https://www.youtube.com/watch?v=AAAAAAAAAAA', 'description': 'removed'},
                    {'title': 'Stomata opening', 'page': VIDEO, 'description': 'guard cells open and close'}]

        async def check(page, site):
            return None if 'AAAA' in page else {'title': 'How stomata open and close', 'channel': 'Plant Bio'}

        judged = []

        async def judge(request, candidates):
            judged.append([c['title'] for c in candidates])
            return type('V', (), {'choice': 0, 'why': 'its title is about stomata opening'})()

        found = await find('stomata', lookup=lookup, judge=judge, check=check)
        assert judged == [['How stomata open and close']], 'only video pages the site confirms are judged'
        assert found.title == 'How stomata open and close' and found.channel == 'Plant Bio', 'the site\'s own title'
        assert found.url == 'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ'

        async def none(request, candidates):
            return type('V', (), {'choice': 5, 'why': 'none is about stomata'})()

        with pytest.raises(NotFound, match='none of the videos found'):
            await find('stomata', lookup=lookup, judge=none, check=check)

        async def nothing(query, *, videos, observe=None):
            return [{'title': 'x', 'page': 'https://example.com/x'}]

        with pytest.raises(NotFound, match='no video page'):
            await find('stomata', lookup=nothing, judge=judge, check=check)
    asyncio.run(go())


def test_a_youtube_video_about_stomata_opens_in_a_window_kept_in_data_and_done_on_the_report(tmp_path, monkeypatch):
    """will-display step 6, then step 7: only the video open in this Tab."""
    async def go():
        app, studio = await built(tmp_path, monkeypatch)

        async def find_video(subject):
            assert subject == 'stomata'
            return FoundVideo(kind='video', url='https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ', page=VIDEO,
                              site='youtube.com', id='dQw4w9WgXcQ', title='How stomata open and close',
                              channel='Plant Bio', description='', why='its title is about stomata')

        app.brain.services.find_video = find_video
        run = await asked(app, studio, 'show_web', {'video': 'stomata'})
        assert run.ok is True, run.summary
        (message,) = studio.sent('web-window')
        assert message['item']['url'] == 'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ'
        assert message['item']['page'] == VIDEO and message['item']['kind'] == 'video'
        entry = app.hub.resources.get(message['item']['resource']['id'], studio.project)
        assert entry is not None and entry.kind == 'result', 'kept in Data as the Project\'s own entry'
        assert 'in a window in Tab "Main", in front' in run.summary and VIDEO in run.summary
        assert 'Plant Bio' in run.summary and 'checked against what was asked' in run.summary
        placement = next(p for p, w in studio.windows.items() if w.get('representation') == 'video')
        # Hands: verified once the report shows its window.
        intent = intent_of('show_web', {'video': 'stomata'})
        assert intent.kind == 'open' and intent.check == 'record' and intent.background
        from orchestrator.voice.records import Records

        records = Records.__new__(Records)  # its record check reads only the receipt and the report
        receipt = records._webbed({'ok': True, 'summary': run.summary}, screen_facts(wire(app, studio)))
        assert receipt['result'] == 'verified' and receipt['pointAt'] == [f'document:{placement}']
        gone = copy.deepcopy(wire(app, studio))
        gone['items'] = [i for i in gone['items'] if i['id'] != f'document:{placement}']
        assert records._webbed({'ok': True, 'summary': run.summary}, screen_facts(gone))['result'] == 'uncertain'
        # "Only have the video open in this tab and close everything else."
        studio.windows['p99'] = {'tab': 'main', 'title': 'Leaf cells', 'z': 12, 'state': 'rendered', 'generation': 1,
                                 'rect': [40, 80, 420, 380], 'representation': 'table',
                                 'resource': {'day': studio.project, 'id': 'd1', 'version': 0, 'picture': 0}}
        await studio.send_report()
        run = await asked(app, studio, 'tabs', {'action': 'only', 'tab': 'main', 'keep': [f'document:{placement}']})
        assert run.ok is True, run.summary
        assert list(studio.windows) == [placement]
    asyncio.run(go())


def test_a_site_hooke_does_not_frame_is_said_with_its_link_and_nothing_is_sent(tmp_path, monkeypatch):
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        run = await asked(app, studio, 'show_web', {'url': 'https://example.com/stomata'})
        assert run.ok is False and 'not one Hooke shows inside the app' in run.summary
        assert 'https://example.com/stomata' in run.summary
        assert not studio.sent('web-window')
    asyncio.run(go())


def test_a_web_window_the_studio_never_shows_is_not_done(tmp_path, monkeypatch):
    monkeypatch.setattr(web_window, 'WEB_WAIT_S', 0.3)

    async def go():
        app, studio = await built(tmp_path, monkeypatch)

        async def find_video(subject):
            return FoundVideo(kind='video', url='https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ', page=VIDEO,
                              site='youtube.com', id='dQw4w9WgXcQ', title='Stomata', channel='', description='', why='')

        app.brain.services.find_video = find_video

        async def answers_but_shows_nothing(message):
            await COMMANDS['web-window-result'](studio.app, studio, {
                'type': 'web-window-result', 'id': message['id'], 'session': message['session'],
                'workspacePin': message['workspacePin'],
                'result': {'ok': True, 'placement': 'p7', 'project': studio.project, 'tab': message['tab']}})

        studio.web_reply = answers_but_shows_nothing
        run = await asked(app, studio, 'show_web', {'video': 'stomata'})
        assert run.ok is False and 'not shown' in run.summary and VIDEO in run.summary and 'kept in Data' in run.summary
    asyncio.run(go())


def test_present_place_puts_outlines_on_the_live_view_or_in_their_own_tab(tmp_path, monkeypatch):
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        eid = population(app, monkeypatch)
        intent = intent_of('present', {'do': 'place', 'what': eid, 'placement': 'overlay'})
        assert intent.caption == 'Drawing it on the view'
        run = await asked(app, studio, 'present', {'do': 'place', 'what': eid, 'placement': 'new-tab'})
        assert run.ok is True, run.summary
        assert any(t['title'] == 'Cell results' for t in studio.tabs)
        run = await asked(app, studio, 'present', {'do': 'place', 'what': 'document:p1', 'placement': 'overlay'})
        assert run.ok is False and 'only outlines and layers' in run.summary
    asyncio.run(go())
