"""Putting a thing where it is asked, wherever it is now (docs/architecture/on-screen.md "Placing things";
orchestrator/placement.py `place`, `put`; the live partner spec 4.8, horizontals 2 and 3).

Will's voice journeys of 29 Oct on the June 14 leaf footage (reviews/2026-10-29-live-partner/*-c49cc5c), each failure a
test here before its fix:
- will-display: "Show the outlines as an overlay on the live view" sent `present place what=layer:e1 overlay`, refused
  "only outlines and layers are drawn on the specimen; this is a window": a layer's name taken for a window.
- will-research: the kept step "Put the graphic in a new Tab named Graphic as the main item" sent `present open
  placement=only tab=Graphic what=r271381`: `only` kept the researcher's id, no window, and failed "That window is not
  open on this Tab"; then `tabs add o1` said "not saved in this Project", and the user heard "save the image first".
- will-workspace: "Open a new Tab called Reference and put that photo in it as the main item" (the photo open on Main):
  `present place only tab=Reference what=d1` failed the same way, `tabs add d1` was refused, the Tab was left empty.
- will-display: "Now put the table beside the live view instead" opened a second table on Main, the voice said it had
  moved it, and "which Tab has the measurements?" found both.

Actual App, Registry, Screen, tabs broker and COMMANDS with the synthetic studio of test_workspace_display.py (its Tab
strip, document windows and the Tab actions as workspace-views.ts agentCommand does them). The native window a picture
opens in is the one seam: `placement.open_picture` (present open, verified by its own tests) is replaced by the studio
opening that window on the Tab in front."""
import asyncio
import json
from types import SimpleNamespace

from orchestrator import placement
from orchestrator.brain.toolbox import present as present_mod
from orchestrator.voice import hands as hands_mod
from orchestrator.voice.hands import intent_of, screen_facts
from tests.test_workspace_display import asked, built, population, wire

PHOTO = {'title': 'Reference: single stoma', 'z': 30, 'state': 'rendered', 'generation': 1,
         'rect': [700, 80, 420, 380], 'representation': 'image'}


def saved(app, monkeypatch, **entries):
    """Data items by id as the index keeps them (only what placing reads of them)."""
    get = app.hub.resources.get

    def lookup(rid, *a, **k):
        return entries[rid] if rid in entries else get(rid, *a, **k)

    monkeypatch.setattr(app.hub.resources, 'get', lookup)


def photo_entry(studio, eid='d1'):
    return SimpleNamespace(id=eid, kind='result', version=None, title='Reference photo · 22:07',
                           shown='Reference: single stoma', day=studio.project, pictures=[f'/api/resources/{eid}/picture'],
                           source={}, values={}, media='reference', state='ready', summary='')


def opens_pictures(studio, monkeypatch):
    """The studio opening a native picture window on the Tab in front, as present open does; what it was sent."""
    opened = []

    async def open_picture(services, ctx, source, title):
        opened.append({'source': source, 'tab': studio.active, 'title': title})
        key = f'p{len(studio.windows) + 1}'
        studio.windows[key] = {**PHOTO, 'tab': studio.active, 'title': title,
                               'resource': {'day': studio.project, 'id': source['id'], 'version': source['version'],
                                            'picture': source['picture']}}
        await studio.send_report()
        return f'document:{key}'

    monkeypatch.setattr(placement, 'open_picture', open_picture)
    return opened


# --- 1. a layer by any name the screen gives it ------------------------------------------------------------------------

def test_a_layer_named_as_the_screen_names_it_is_drawn_on_the_live_view_never_taken_for_a_window(tmp_path, monkeypatch):
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        population(app, monkeypatch)
        shown = []

        async def act(services, args, by=None, ctx=None):
            shown.append(dict(args))
            return f"{args['what']} is drawn on the live view"

        monkeypatch.setattr(present_mod, 'act', act)
        for name, drawn in (('layer:e3', 'e3'), ('population:e3', 'e3'), ('e3', 'e3'), ('tracks:m2', 'tracks:m2'),
                            ('m2', 'tracks:m2'), ('Layer:E3', 'e3')):
            shown.clear()
            run = await asked(app, studio, 'present', {'what': name, 'do': 'place', 'placement': 'overlay'})
            assert run.ok is True, (name, run.summary)
            assert shown == [{'do': 'show', 'what': drawn}], name
        assert placement.layer_of('layer:e1') == 'e1' and placement.layer_of('document:p1') is None
        # A window is still no overlay, said by what it is.
        run = await asked(app, studio, 'present', {'what': 'document:p1', 'do': 'place', 'placement': 'overlay'})
        assert run.ok is False and 'not outlines or tracks' in run.summary
    asyncio.run(go())


# --- 2. only keeps the window it opened --------------------------------------------------------------------------------

def test_open_with_only_keeps_the_window_it_opened_never_the_id_it_was_asked_by(tmp_path, monkeypatch):
    """will-research step 2: a researcher's picture into the Graphic Tab as its main item."""
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        studio.tabs.append({'id': 'tab-1', 'title': 'Graphic'})
        studio.active = 'tab-1'
        studio.specimens['tab-1'] = {'state': 'float', 'rect': [16, 620, 240, 216]}
        studio.windows['p1'] = {'tab': 'tab-1', 'title': 'Old note', 'z': 12, 'state': 'rendered', 'generation': 1,
                                'rect': [40, 80, 420, 380], 'representation': 'table',
                                'resource': {'day': studio.project, 'id': 'd9', 'version': 0, 'picture': 0}}
        work = SimpleNamespace(id='r271381', kind='research', version=None, title="Researcher 1's work", shown='',
                               day=studio.project, pictures=[], source={}, values={}, media=None, state='done',
                               summary='')
        saved(app, monkeypatch, r271381=work)
        source = {'day': studio.project, 'id': 'r271381', 'version': 1, 'picture': 0, 'outputId': 'o1'}
        monkeypatch.setattr(placement, 'picture_source', lambda services, entry: source if entry is work else None)
        opened = opens_pictures(studio, monkeypatch)
        await studio.send_report()
        run = await asked(app, studio, 'present', {'do': 'open', 'placement': 'only', 'tab': 'Graphic',
                                                   'what': 'r271381'})
        assert run.ok is True, run.summary
        assert opened == [{'source': source, 'tab': 'tab-1', 'title': "Researcher 1's work"}], \
            'opened from its exact retained output, on the Tab named'
        only = [m['action'] for m in studio.sent('workspace-tabs') if m['action']['action'] == 'only']
        assert only == [{'action': 'only', 'tab': 'tab-1', 'keep': ['p2']}], 'the window it opened, not r271381'
        assert list(studio.windows) == ['p2'] and studio.specimens['tab-1'] == {'state': 'hidden'}
        assert 'the only window in Tab "Graphic"' in run.summary and 'opened there from its saved work' in run.summary
        for internal in ('save it', 'save the', 'not saved', 'version', 'List the Tabs'):
            assert internal not in run.summary, internal
        # Hands reads the same: the window opened is the one kept, never r271381 (which no window is called).
        intent = intent_of('present', {'do': 'open', 'placement': 'only', 'tab': 'Graphic', 'what': 'r271381'})
        assert intent.caption == 'Closing the rest'
        facts = screen_facts(wire(app, studio))
        assert hands_mod.expected(intent, facts, {'outputs': [{'kind': 'presentation', 'instanceId': 'document:p2'}]})
        assert hands_mod.expected(intent, facts, {}) is True, 'the window showing r271381 is the one kept'
        other = intent_of('present', {'do': 'open', 'placement': 'only', 'tab': 'Graphic', 'what': 'r999'})
        assert hands_mod.expected(other, facts, {}) is None, 'no window by that name: its receipt decides'
    asyncio.run(go())


def test_a_researchers_picture_opens_from_its_exact_retained_output_never_a_copy(tmp_path, monkeypatch):
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        images = [{'id': 'o1', 'kind': 'image', 'version': 1, 'path': 'outputs/o1.png'},
                  {'id': 'o2', 'kind': 'html', 'version': 1, 'path': 'outputs/o2.html'},
                  {'id': 'o3', 'kind': 'image', 'version': 2, 'path': 'outputs/o3.png'}]
        session = SimpleNamespace(rid='r7', day='2026-10-29', current_outputs=lambda: images)
        manager = SimpleNamespace(get=lambda rid: session if rid == 'r7' else None)
        monkeypatch.setattr(app.hub.resources, 'research', manager)
        research = SimpleNamespace(id='r7', kind='research', source={}, pictures=[])
        figure = SimpleNamespace(id='r7:o3', kind='figure', source={'researcher': 'r7', 'outputId': 'o3', 'line': 'o3'},
                                 pictures=['/x'])
        services = app.brain.services
        assert placement.picture_source(services, research) == {
            'day': '2026-10-29', 'id': 'r7', 'version': 1, 'picture': 0, 'outputId': 'o1'}, 'its first picture'
        assert placement.picture_source(services, figure) == {
            'day': '2026-10-29', 'id': 'r7', 'version': 2, 'picture': 1, 'outputId': 'o3'}
        photo = photo_entry(studio)
        assert placement.picture_source(services, photo) == {'day': studio.project, 'id': 'd1', 'version': None,
                                                             'picture': 0}
    asyncio.run(go())


# --- 3. one way to put X in Tab T --------------------------------------------------------------------------------------

def test_a_photo_open_on_main_put_in_a_new_named_tab_as_its_main_item(tmp_path, monkeypatch):
    """will-workspace step 4: "Open a new Tab called Reference and put that photo in it as the main item"."""
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        studio.windows['p1'] = {**PHOTO, 'tab': 'main',
                                'resource': {'day': studio.project, 'id': 'd1', 'version': None, 'picture': 0}}
        saved(app, monkeypatch, d1=photo_entry(studio))
        opened = opens_pictures(studio, monkeypatch)
        await studio.send_report()
        # Moved there: the model judged the user wants it there rather than on Main.
        run = await asked(app, studio, 'present', {'do': 'place', 'placement': 'only', 'tab': 'Reference',
                                                   'what': 'd1', 'instead': True})
        assert run.ok is True, run.summary
        tab = next(t['id'] for t in studio.tabs if t['title'] == 'Reference')
        assert studio.active == tab and [w['tab'] for w in studio.windows.values()] == [tab], 'moved, not copied'
        assert not opened, 'a window already open is moved, never opened again'
        moves = [m['action'] for m in studio.sent('workspace-tabs') if m['action']['action'] == 'move']
        assert moves == [{'action': 'move', 'tab': tab, 'from': 'main', 'placement': 'p1'}]
        assert 'the only window in Tab "Reference"' in run.summary and 'moved there from Tab "Main"' in run.summary
    asyncio.run(go())


def test_put_in_a_tab_without_instead_opens_it_there_too_and_says_where_else_it_is(tmp_path, monkeypatch):
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        studio.windows['p1'] = {**PHOTO, 'tab': 'main',
                                'resource': {'day': studio.project, 'id': 'd1', 'version': None, 'picture': 0}}
        saved(app, monkeypatch, d1=photo_entry(studio))
        opened = opens_pictures(studio, monkeypatch)
        await studio.send_report()
        run = await asked(app, studio, 'present', {'do': 'place', 'placement': 'only', 'tab': 'Reference', 'what': 'd1'})
        assert run.ok is True, run.summary
        tab = next(t['id'] for t in studio.tabs if t['title'] == 'Reference')
        assert [o['tab'] for o in opened] == [tab] and opened[0]['source']['id'] == 'd1'
        assert sorted(w['tab'] for w in studio.windows.values()) == sorted(['main', tab])
        assert 'also still open in Tab "Main"' in run.summary, 'every claim from the Tabs as they are'
    asyncio.run(go())


def test_a_saved_picture_not_open_anywhere_is_opened_in_the_tab_named(tmp_path, monkeypatch):
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        studio.tabs.append({'id': 'tab-1', 'title': 'Reference'})
        saved(app, monkeypatch, d1=photo_entry(studio))
        opened = opens_pictures(studio, monkeypatch)
        await studio.send_report()
        run = await asked(app, studio, 'present', {'do': 'place', 'placement': 'beside', 'tab': 'Reference',
                                                   'what': 'd1'})
        assert run.ok is True, run.summary
        assert studio.active == 'tab-1' and [o['tab'] for o in opened] == ['tab-1']
        assert 'side by side with what is open in Tab "Reference"' in run.summary
        assert 'also still open' not in run.summary
    asyncio.run(go())


def test_tabs_add_of_a_photo_says_where_it_goes_never_that_it_is_unsaved(tmp_path, monkeypatch):
    """will-workspace and will-research: `tabs add d1` and `tabs add o1` said "not saved in this Project", and the user
    was told to save it first. A saved photo has no version to name; it is placed, not added; an unknown id is said so."""
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        saved(app, monkeypatch, d1=photo_entry(studio))
        run = await asked(app, studio, 'tabs', {'action': 'add', 'tab': 'main', 'resource': {'id': 'd1'}})
        assert run.ok is False and 'not saved' not in run.summary
        assert 'present with do place, what d1, tab' in run.summary
        run = await asked(app, studio, 'tabs', {'action': 'add', 'tab': 'main', 'resource': {'id': 'o9'}})
        assert run.ok is False and "o9 is not in this Project's Data" in run.summary
        assert 'save' not in run.summary.lower()
    asyncio.run(go())


# --- 4. instead moves; otherwise the answer says where else it is ------------------------------------------------------

def test_the_table_put_beside_the_live_view_without_instead_says_it_is_still_in_the_other_tab(tmp_path, monkeypatch):
    """will-display step 3 as the model sent it (no instead): a second table on Main, and the answer says the first is
    still in "Cell results", so "I moved it" is never said from words alone."""
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        eid = population(app, monkeypatch)
        assert (await asked(app, studio, 'show_measurements', {'of': eid, 'placement': 'new-tab'})).ok
        run = await asked(app, studio, 'show_measurements', {'of': eid, 'tab': 'main', 'placement': 'beside'})
        assert run.ok is True, run.summary
        tables = [w['tab'] for w in studio.windows.values() if w.get('representation', 'table') == 'table']
        assert sorted(tables) == sorted(['main', next(t['id'] for t in studio.tabs if t['title'] == 'Cell results')])
        assert 'also still open in Tab "Cell results"' in run.summary and 'instead moves it' in run.summary
        # With instead, the one in "Cell results" moves: one table, on Main.
        studio.windows = {p: w for p, w in studio.windows.items() if w['tab'] != 'main'}
        await studio.send_report()
        run = await asked(app, studio, 'present', {'do': 'place', 'what': 'layer:e3', 'placement': 'beside',
                                                   'tab': 'Main', 'instead': True})
        assert run.ok is True, run.summary
        tables = [w['tab'] for w in studio.windows.values() if w.get('representation', 'table') == 'table']
        assert tables == ['main'] and 'moved there' in run.summary and 'also still open' not in run.summary
        listed = json.loads((await asked(app, studio, 'tabs', {'action': 'list', 'kind': 'table'})).summary)
        assert listed['holding'] == ['Main'], 'which Tab has the measurements: one'
    asyncio.run(go())


def test_the_words_let_the_model_decide_move_or_also(tmp_path, monkeypatch):
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        tools = app.brain.registry.tools
        for name in ('present', 'show_measurements'):
            words = tools[name].params['properties']['instead']['description']
            assert 'rather than' in words and tools[name].params['properties']['instead']['type'] == 'boolean'
        assert 'wherever it is now' in tools['present'].description
        assert wire(app, studio) is not None
    asyncio.run(go())


def test_two_photos_put_in_one_new_tab_share_it_side_by_side(tmp_path, monkeypatch):
    """Will, 30 Oct: a Tab made for something has no feed; one thing fills it, two or more share it. The first photo put
    in a new Tab fills it (only); the second put in that same Tab shares it (arranged), never closing the first."""
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        saved(app, monkeypatch, d1=photo_entry(studio, 'd1'), d2=photo_entry(studio, 'd2'))
        opens_pictures(studio, monkeypatch)
        await studio.send_report()
        run = await asked(app, studio, 'present', {'do': 'place', 'placement': 'new-tab', 'tab': 'Plant cells',
                                                   'what': 'd1'})
        assert run.ok is True, run.summary
        tab = next(t['id'] for t in studio.tabs if t['title'] == 'Plant cells')
        assert 'it fills the Tab, with no live feed there' in run.summary
        assert studio.sent('workspace-tabs')[-1]['action']['action'] == 'only'
        run = await asked(app, studio, 'present', {'do': 'place', 'placement': 'new-tab', 'tab': tab, 'what': 'd2'})
        assert run.ok is True, run.summary
        assert [t['title'] for t in studio.tabs].count('Plant cells') == 1, 'one Tab for both, never a Tab each'
        shown = sorted(w['resource']['id'] for w in studio.windows.values() if w['tab'] == tab)
        assert shown == ['d1', 'd2'], 'the second shares the Tab; the first is not closed'
        assert studio.sent('workspace-tabs')[-1]['action']['action'] == 'arrange'
        assert 'side by side with what is open in Tab "Plant cells"' in run.summary
    asyncio.run(go())
