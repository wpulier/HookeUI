"""Reference retention/native delivery over actual App owners; synthetic external I/O.

Commons search/fetch and AppStudio's DOM/decoder observations are external stand-ins.
Registry, Runner, Results, Index, PreparedSources, native commands and Screen are real.
Descriptions here are scripted model tool arguments, not an actual model-quality test.
No source identity, delivery grant, completion receipt or ToolRun is manufactured.
"""
import asyncio
import copy
import json
from pathlib import Path

import pytest

from research_worker import safe_fetch
from orchestrator.ticket import issue
from tests.test_workspace_actions_app import (
    WORKSPACE, built, controlled_state, digest, jpeg, open_window, plan, saved_picture, succeeded,
)
from tests.waiting import HANG_S


DESCRIPTION = 'Two guard cells surround the pore; this is a reference example, not the current slide.'
# What the scripted look at the pixels says they show (retrieval.look_at_photo): the picture's description, never the
# caller's DESCRIPTION, which is written before anyone has seen the picture (live 2026-10-29 17:16).
SHOWS = 'a surface view of one stoma between two guard cells, light microscopy'
PAGE = 'https://commons.wikimedia.org/wiki/File:Leaf_pore.jpg'
WEB = {'site': 'Wikimedia Commons', 'page': PAGE, 'title': 'Leaf pore',
       'licence': 'CC BY-SA 4.0', 'author': 'A. Botanist',
       'credit': 'A. Botanist, CC BY-SA 4.0, commons.wikimedia.org'}


@pytest.fixture
def retained_reference(tmp_path, monkeypatch):
    app, studio = built(tmp_path)
    # How a photo is kept and shown, on the web's own path (the default since 2026-10-29): Commons raced beside it
    # is test_the_race_keeps_one_photo_with_its_own_source, below.
    app.hub.settings.reference_search = 'web'
    pixels = jpeg(103)
    external = {'searches': [], 'fetches': [], 'available': True}

    async def search(words, limit=3, client=None):
        external['searches'].append(words)
        return [PAGE] if external['available'] else []

    async def fetch(url, client=None):
        external['fetches'].append(url)
        return safe_fetch.FetchedImage(pixels, 160, 120,
            'https://upload.wikimedia.org/Leaf_pore.jpg', PAGE,
            WEB['title'], WEB['licence'], WEB['author'])

    # Keep the real grounded retrieval, retention and credit path; only its
    # external provider/fetch boundaries are scripted.
    from research_worker import retrieval

    async def lookup(query, **kwargs):
        external['searches'].append(query)
        return [{'title': WEB['title'], 'page': PAGE, 'image': PAGE}] if external['available'] else []

    monkeypatch.setattr(retrieval, 'lookup', lookup)
    monkeypatch.setattr(safe_fetch, 'commons_search', search)
    monkeypatch.setattr(safe_fetch, 'fetch_image', fetch)
    monkeypatch.setattr(safe_fetch, 'check_url', lambda url: None)
    try:
        yield app, studio, pixels, external
    finally:
        app.hub._analysis.shutdown(wait=True)
        app.hub._background.shutdown(wait=True)


async def fetch_reference(app, **extra):
    runs, ctx = await plan(app, [('reference_photo', {'subject': 'leaf pore', **extra})])
    run = runs[0]
    succeeded(run)
    assert run.resource['day'] == app.day.day and run.resource['id'].startswith('d')
    entry = app.hub.resources.get(run.resource['id'], run.resource['day'])
    assert entry is not None and entry.version is None  # no invented version
    return run, ctx, entry


def assert_retained(app, entry, pixels):
    assert entry.media == 'reference'
    assert len(entry.pictures) == 1, 'one fetched image must not be duplicated by display attachment'
    assert app.hub.resources.picture(entry.id, 0, entry.day)[0] == pixels
    assert entry.made_by['web'] == WEB
    assert all(entry.source.get(key) is None for key in ('frame', 'seq', 'boot'))


def test_default_reference_still_shows_and_retains_one_original_photo(retained_reference):
    app, studio, pixels, external = retained_reference

    async def go():
        run, _, entry = await fetch_reference(app)
        assert_retained(app, entry, pixels)
        shown = app.services.presentations.list(WORKSPACE)
        assert len(shown) == 1 and shown[0].source.identity['resource']['id'] == entry.id
        assert shown[0].source.media == 'reference'
        assert run.outcome['status'] == 'confirmed'
        assert entry.id in run.summary and WEB['title'] in run.summary and PAGE in run.summary
        assert entry.summary == run.summary, 'a finished retained photo must not stay labelled still working'
        assert external['searches'] == ['leaf pore\nOriginal request (preserve its relevant constraints): Arrange retained views'] and external['fetches'] == [PAGE]
    asyncio.run(go())


def test_the_race_keeps_one_photo_with_its_own_source(retained_reference):
    # Option REFERENCE_SEARCH "web+commons": the web and Commons' catalogue are searched at once, and
    # whichever photo passes the check first is the one kept, once, credited to the page it came from.
    app, studio, pixels, external = retained_reference
    app.hub.settings.reference_search = 'web+commons'

    async def go():
        run, _, entry = await fetch_reference(app)
        assert_retained(app, entry, pixels)
        assert 'leaf pore microscope' in external['searches'], 'the catalogue was searched beside the web'
        assert len(external['fetches']) == 1, "one photo fetched and kept, the race's other search called off"
        assert entry.made_by['web']['page'] == PAGE and PAGE in run.summary
        assert len(app.services.presentations.list(WORKSPACE)) == 1
    asyncio.run(go())


def test_retained_reference_window_receipts_name_actual_source(retained_reference):
    app, studio, _, _ = retained_reference

    async def go():
        _, _, entry = await fetch_reference(app, show=False)
        iid, opened, _ = await open_window(app, entry)
        for verb in ('front', 'close'):
            runs, _ = await plan(app, [('present', {'do': verb, 'instance': {'instanceId': iid}})])
            succeeded(runs[0])
            assert entry.id in runs[0].summary
            assert WEB['title'] in runs[0].summary and PAGE in runs[0].summary
        assert entry.id in opened.summary and PAGE in opened.summary

    asyncio.run(go())


def test_second_reference_retains_original_browser_source_identity(retained_reference, monkeypatch):
    from orchestrator.brain.registry import ToolContext
    app, studio, _, _ = retained_reference

    async def go():
        _, _, first = await fetch_reference(app)
        await studio.send_report()
        pin = app.hub.screen.pin(studio, request_id='another-photo', timing='client-send')
        async def fetch(url, client=None):
            return safe_fetch.FetchedImage(jpeg(180), 160, 120,
                'https://upload.wikimedia.org/Other_pore.jpg', PAGE, WEB['title'], WEB['licence'], WEB['author'])
        monkeypatch.setattr(safe_fetch, 'fetch_image', fetch)
        ctx = ToolContext('chat', 2, None, 'another photo beside it', asked=app.day.here(), request_snapshot=pin)
        runs, _ = await plan(app, [('reference_photo', {'subject': 'leaf pore'})], ctx=ctx)
        run = runs[0]
        succeeded(run)
        prior = run.values['referencesAtRequest']
        assert len(prior) == 1 and prior[0]['id'] == first.id and prior[0]['day'] == first.day
        assert prior[0]['page'] == PAGE
        assert 'references already open when requested' in run.summary
        kept = app.hub.resources.get(run.resource['id'], run.resource['day'])
        assert kept.values['referencesAtRequest'] == prior

    asyncio.run(go())


def test_grounded_selection_is_displayed_without_catalogue_or_research(retained_reference, monkeypatch):
    from research_worker import retrieval, safe_fetch

    app, studio, pixels, external = retained_reference
    requested = []

    async def lookup(query, *, images=False, observe=None):
        requested.append((query, images))
        return [{'title': WEB['title'], 'page': PAGE, 'image': PAGE}]

    monkeypatch.setattr(retrieval, 'lookup', lookup)
    monkeypatch.setattr(safe_fetch, 'check_url', lambda url: None)

    async def go():
        run, _, entry = await fetch_reference(app)
        assert_retained(app, entry, pixels)
        assert requested == [('leaf pore\nOriginal request (preserve its relevant constraints): Arrange retained views', True)]
        assert external['searches'] == [] and external['fetches'] == [PAGE]
        assert run.outcome['status'] == 'confirmed'
        assert len(app.services.presentations.list(WORKSPACE)) == 1

    asyncio.run(go())


def test_another_lookup_receives_reference_sources_from_the_request_pin(retained_reference, monkeypatch):
    from orchestrator.brain.registry import ToolContext
    from research_worker import retrieval
    app, studio, pixels, external = retained_reference
    async def go():
        await fetch_reference(app)
        await studio.send_report()
        pin = app.hub.screen.pin(studio, request_id='different-photo', timing='client-send')
        queries = []
        async def lookup(query, **kwargs):
            queries.append(query)
            return []  # no paid provider or new display required to inspect the boundary
        monkeypatch.setattr(retrieval, 'lookup', lookup)
        ctx = ToolContext('chat', 2, None, 'a different one from somewhere else online',
                          asked=app.day.here(), request_snapshot=pin)
        runs, _ = await plan(app, [('reference_photo', {'subject': 'stoma', 'n': 2})], ctx=ctx)
        assert runs[0].ok is False
        assert len(queries) == 2
        assert all(PAGE in q and digest(pixels) in q for q in queries)
        assert all('a different one from somewhere else online' in q for q in queries)
        assert len(app.services.presentations.list(WORKSPACE)) == 1
    asyncio.run(go())


def test_made_today_open_uses_retained_photo_in_requesting_native_workspace(retained_reference):
    from orchestrator.commands import COMMANDS
    app, studio, pixels, external = retained_reference
    async def go():
        _, _, entry = await fetch_reference(app, show=False)
        searches = len(external['searches'])
        result = await COMMANDS['present'](app, studio, {'type': 'present', 'what': entry.id, 'do': 'open'})
        assert result['ok'] is True
        windows = app.services.presentations.list(WORKSPACE)
        assert len(windows) == 1
        assert windows[0].source.identity['resource']['id'] == entry.id
        assert windows[0].source.to_wire()['digest'] == digest(pixels)
        assert len(external['searches']) == searches
        assert len(app.hub.resources.results.entries(entry.day)) == 1
    asyncio.run(go())


def test_hidden_reference_preserves_existing_magnifier_and_native_window(retained_reference):
    app, studio, pixels, _ = retained_reference

    async def go():
        app.services.magnifier.show_picture(jpeg(41), 'Existing captured candidate')
        legacy = copy.deepcopy(app.services.magnifier.wire())
        magnifier_bytes = dict(app.services.magnifier.pictures)
        original, _ = saved_picture(app)
        iid, _, _ = await open_window(app, original)  # useful real accepted source/command control
        before = controlled_state(studio.store().get(WORKSPACE, iid))
        run, _, entry = await fetch_reference(app, show=False, description=DESCRIPTION)
        assert app.services.magnifier.wire() == legacy, 'hidden fetch must not change the existing magnifier'
        assert app.services.magnifier.pictures == magnifier_bytes
        assert controlled_state(studio.store().get(WORKSPACE, iid)) == before
        assert_retained(app, entry, pixels)
        assert run.values['description'] == SHOWS and run.values['describedBy'] == 'gemini-3.8-flash'
        assert run.values['note'] == DESCRIPTION  # the caller's words are its note, not what the picture shows
        assert SHOWS in run.summary and 'Gemini 3.8 Flash' in run.summary
        assert 'showing' not in run.summary.lower(), 'retention alone is not a display receipt'
        assert len(app.hub.resources.results.entries(entry.day)) == 2
        json.dumps(run.wire(full=True))  # private producing bytes never enter public activity
    asyncio.run(go())


def test_exact_hidden_reference_opens_independent_native_windows_with_original_provenance(retained_reference):
    app, studio, pixels, _ = retained_reference

    async def go():
        _, _, entry = await fetch_reference(app, show=False, description=DESCRIPTION)
        assert_retained(app, entry, pixels)
        assert entry.source['web'] == WEB and entry.source['description'] == SHOWS
        first, _, _ = await open_window(app, entry)
        second, _, _ = await open_window(app, entry)
        assert first != second
        for iid in (first, second):
            state = studio.store().get(WORKSPACE, iid)
            source = state.source.to_wire()
            assert source['identity']['resource'] == {
                'day': entry.day, 'id': entry.id, 'version': None, 'picture': 0}
            assert source['media'] == 'reference' and source['digest'] == digest(pixels)
            assert source['provenance']['web'] == WEB
            assert source['provenance']['description'] == SHOWS
            assert source['provenance']['describedBy'] == 'gemini-3.8-flash'
            assert all(source['provenance'].get(key) is None for key in ('frame', 'seq', 'boot'))
            assert studio.items[iid]['load']['state'] == 'drawn'
        assert studio.items[first]['rootId'] != studio.items[second]['rootId']
        assert len(app.hub.resources.results.entries(entry.day)) == 1
        assert not app.services.magnifier.open
    asyncio.run(go())


def test_reference_description_is_bounded_without_replacing_web_credit(retained_reference):
    app, _, pixels, _ = retained_reference

    async def go():
        _, _, entry = await fetch_reference(app, show=False, description=DESCRIPTION + ' x' * 800)
        assert_retained(app, entry, pixels)
        assert entry.source['description'] == SHOWS  # what the look saw, never the caller's caption
        assert entry.source['web'] == WEB
        values = app.hub.resources.results.values(entry.id, entry.day)
        assert values['description'] == SHOWS
        assert values['note'].startswith(DESCRIPTION) and len(values['note']) <= 600
    asyncio.run(go())


def test_unavailable_reference_creates_no_retained_picture_or_display(retained_reference):
    app, _, _, external = retained_reference
    external['available'] = False

    async def go():
        app.services.magnifier.show_picture(jpeg(41), 'Existing captured candidate')
        before = copy.deepcopy(app.services.magnifier.wire())
        runs, _ = await plan(app, [('reference_photo', {'subject': 'leaf pore', 'show': False,
                                                     'description': DESCRIPTION})])
        assert runs[0].ok is False and runs[0].resource is None
        assert 'no reference photo' in runs[0].summary and not external['fetches']
        assert app.hub.resources.results.entries(app.day.day) == []
        assert app.services.magnifier.wire() == before
    asyncio.run(go())


def test_hidden_reference_missing_result_receipt_fails_original_run_without_losing_views(retained_reference, monkeypatch):
    app, studio, _, external = retained_reference
    original_open = Path.open
    refused = []
    folder = app.paths.results / app.day.day

    def storage(path, mode='r', *args, **kwargs):
        if path.parent == folder and path.name.startswith('.d2.json.') and path.suffix == '.tmp' and mode == 'wb':
            refused.append(path)
            # An unexpected external storage failure reaches the real keep_result
            # listener; ordinary OSError retains the existing in-memory fallback.
            raise ValueError('synthetic metadata storage unavailable')
        return original_open(path, mode, *args, **kwargs)

    async def go():
        _, _, existing = await fetch_reference(app)  # actual result writer/display positive
        assert existing.id == 'd1'
        iid, _, _ = await open_window(app, existing)
        before = controlled_state(studio.store().get(WORKSPACE, iid))
        legacy = copy.deepcopy(app.services.magnifier.wire())
        pixels = dict(app.services.magnifier.pictures)
        monkeypatch.setattr(Path, 'open', storage)  # exact external file operation only
        ticket = issue(app.hub, 'turn', asked=app.day.here(), channel='chat')
        try:
            runs, ctx = await plan(app, [('reference_photo', {'subject': 'leaf pore', 'show': False,
                                                         'description': DESCRIPTION})], ticket=ticket)
            run = runs[0]
            assert len(refused) == 1 and len(external['fetches']) == 2
            assert ctx.admission is ticket.admission
            assert run.occurrence.request_id == ticket.admission.request_id and run.occurrence.action_id
            assert run.picture_bytes == []  # real Registry finally owns cleanup
            assert run.resource is None
            assert run.ok is False, 'missing actual result receipt must not report retained success'
            assert run.summary and not run.summary.startswith('retained a reference photo')
            assert run.wire()['ok'] is False and 'resource' not in run.wire()
            json.dumps(run.wire(full=True))
            assert app.services.magnifier.wire() == legacy and app.services.magnifier.pictures == pixels
            assert controlled_state(studio.store().get(WORKSPACE, iid)) == before
            assert [row['id'] for row in app.hub.resources.results.entries(existing.day)] == [existing.id]
        finally:
            ticket.close()
    asyncio.run(go())


@pytest.mark.parametrize('invalid', ['oversized', 'mutable'])
def test_hidden_reference_rejects_unbounded_or_mutable_producing_bytes(retained_reference, monkeypatch, invalid):
    app, _, pixels, _ = retained_reference
    offered = pixels + b'\x00' * (10 * 1024 * 1024) if invalid == 'oversized' else bytearray(pixels)
    fetched = []

    async def external_fetch(url, client=None):
        fetched.append(url)
        return safe_fetch.FetchedImage(offered, 160, 120,
            'https://upload.wikimedia.org/Leaf_pore.jpg', PAGE,
            WEB['title'], WEB['licence'], WEB['author'])

    monkeypatch.setattr(safe_fetch, 'fetch_image', external_fetch)

    async def go():
        app.services.magnifier.show_picture(jpeg(41), 'Existing captured candidate')
        before = copy.deepcopy(app.services.magnifier.wire())
        runs, _ = await plan(app, [('reference_photo', {'subject': 'leaf pore', 'show': False})])
        run = runs[0]
        assert fetched == [PAGE]  # actual reference lookup reached the external bytes
        assert run.ok is False and run.resource is None and run.picture_bytes == []
        assert app.hub.resources.results.entries(app.day.day) == []
        assert app.services.magnifier.wire() == before
        json.dumps(run.wire(full=True))
    asyncio.run(go())


def test_same_plan_opens_actual_retained_reference_without_predicting_resource_id(retained_reference):
    app, studio, pixels, external = retained_reference

    async def go():
        # An unrelated older source deliberately occupies the first resource ID.
        saved_picture(app)
        runs, ctx = await plan(app, [
            ('reference_photo', {'subject': 'leaf pore', 'show': False, 'description': DESCRIPTION}),
            ('present', {'do': 'open', 'source': {'step': 1, 'output': 0}}),
        ])
        for run in runs:
            succeeded(run)
        output = runs[0].outputs[0].wire()
        assert output == {'kind': 'source', 'source': {
            'day': app.day.day, 'id': runs[0].resource['id'], 'version': None, 'picture': 0}}
        iid = runs[1].outputs[0]['instanceId']
        source = studio.store().get(WORKSPACE, iid).source.to_wire()
        assert source['identity']['resource'] == output['source']
        assert source['digest'] == digest(pixels)
        assert external['searches'] == ['leaf pore\nOriginal request (preserve its relevant constraints): Arrange retained views']
        assert len(external['fetches']) == 1
        assert not app.services.magnifier.open
    asyncio.run(go())


@pytest.mark.parametrize('reference', [
    {'step': 2, 'output': 0}, {'step': 1, 'output': 7},
    {'step': 1, 'output': 0, 'id': 'd1'},
])
def test_produced_source_reference_never_guesses_missing_or_later_output(retained_reference, reference):
    app, studio, _, _ = retained_reference

    async def go():
        runs, _ = await plan(app, [
            ('reference_photo', {'subject': 'leaf pore', 'show': False}),
            ('present', {'do': 'open', 'source': reference}),
        ])
        succeeded(runs[0])
        assert runs[1].ok is False
        assert not runs[1].outputs
        assert not any(key.startswith('w-') for key in studio.items)
    asyncio.run(go())


def test_presentation_output_cannot_masquerade_as_produced_source(retained_reference):
    app, studio, _, _ = retained_reference

    async def go():
        runs, _ = await plan(app, [
            ('reference_photo', {'subject': 'leaf pore', 'show': False}),
            ('present', {'do': 'open', 'source': {'step': 1, 'output': 0}}),
            ('present', {'do': 'open', 'source': {'step': 2, 'output': 0}}),
        ])
        succeeded(runs[0])
        succeeded(runs[1])
        assert runs[2].ok is False
        assert not runs[2].outputs
        assert len([key for key in studio.items if key.startswith('w-')]) == 1
    asyncio.run(go())


@pytest.fixture(autouse=True)
def scripted_photo_pixel_check(monkeypatch):
    from research_worker import retrieval
    async def look_at_photo(query, jpeg):
        return retrieval.PhotoLook(True, SHOWS)
    monkeypatch.setattr(retrieval, 'look_at_photo', look_at_photo)


def test_made_today_research_reopens_retained_report_without_broadcast(retained_reference, monkeypatch):
    from orchestrator.commands import COMMANDS
    from tests.test_workspace_actions_app import research_fixture
    app, studio, _, _ = retained_reference
    async def go():
        manager, session = research_fixture(app)
        session.result = {'title': 'Retained findings', 'summary': 'Measured observations',
                          'methods': 'Existing capture only', 'observations': ['Three pores']}
        def no_broadcast(*args, **kwargs):
            pytest.fail('retained report must use the requesting workspace, not the shared viewer')
        monkeypatch.setattr(app.presenter, 'show', no_broadcast)
        result = await COMMANDS['present'](app, studio, {'type': 'present', 'what': session.rid, 'do': 'open'})
        assert result['ok'] is True
        windows = app.services.presentations.list(WORKSPACE)
        assert len(windows) == 1
        assert windows[0].source.kind == 'written'
        assert windows[0].source.identity['resource']['id'] == session.rid
        assert session.status == 'done'
    asyncio.run(go())


def test_native_reference_beside_opens_in_initial_composition(retained_reference):
    from orchestrator.brain.registry import ToolContext
    app, studio, _, _ = retained_reference

    async def go():
        original, _ = saved_picture(app)
        anchor, _, _ = await open_window(app, original)
        before = controlled_state(app.services.presentations.get(WORKSPACE, anchor))
        await studio.send_report()
        pin = app.hub.screen.pin(studio, request_id='beside-photo', timing='client-send')
        ctx = ToolContext('chat', 2, None, 'bring up a reference photo beside it',
                          asked=app.day.here(), request_snapshot=pin)
        count = len(studio.messages)
        runs, _ = await plan(app, [('reference_photo', {'subject': 'leaf pore', 'beside': True})], ctx=ctx)
        succeeded(runs[0])
        opened = [w for w in app.services.presentations.list(WORKSPACE) if w.id != anchor]
        assert len(opened) == 1
        assert opened[0].geometry_intent.relation_json is not None, 'MISSING_FEATURE: native reference ignored explicit beside'
        relation = json.loads(opened[0].geometry_intent.relation_json)
        assert relation['kind'] == 'beside'
        assert relation['reference'] == anchor
        assert controlled_state(app.services.presentations.get(WORKSPACE, anchor)) == before
        commands = [m for m in studio.messages[count:] if m.get('schema') == 'presentation-command-v2']
        assert commands and all(m['op'] == 'open' for m in commands)
        assert any(e['predicate'] == 'rect' for e in runs[0].outcome['expected'])
    asyncio.run(go())


@pytest.mark.parametrize('change', ['move', 'close', 'ambiguous', 'no-room', 'foreign-pin'])
def test_native_beside_failure_retains_photo_without_rearranging_existing_windows(retained_reference, change):
    from orchestrator.brain.registry import ToolContext
    from orchestrator.workspace_observation import ClientRef
    from tests.test_workspace_actions_app import AppStudio
    app, studio, pixels, _ = retained_reference

    async def go():
        original, _ = saved_picture(app)
        anchor, _, _ = await open_window(app, original)
        if change == 'ambiguous':
            second, _ = saved_picture(app, value=140)
            await open_window(app, second)
        if change == 'no-room':
            studio.presentation_area = [0, 0, 390, 500]
        await studio.send_report()
        pin = app.hub.screen.pin(studio, request_id='beside-photo', timing='client-send')
        if change == 'foreign-pin':
            peer = AppStudio(app)
            peer.client_ref = {**studio.client_ref, 'clientId': 'peer-client', 'pageEpoch': 'peer-page', 'connectionEpoch': 'peer-socket'}
            app.accept_workspace(peer, ClientRef(WORKSPACE, 'peer-client', 'peer-page', 'peer-socket'))
            await peer.send_report()
            pin = app.hub.screen.pin(peer, request_id='peer-photo', timing='client-send')
        if change in ('move', 'close'):
            args = {'do': change, 'instance': anchor}
            if change == 'move':
                args['position'] = {'x': 230, 'y': 80}
            changed, _ = await plan(app, [('present', args)])
            succeeded(changed[0])
        before = [controlled_state(w) for w in app.services.presentations.list(WORKSPACE)]
        ctx = ToolContext('chat', 2, None, 'bring up a reference photo beside it',
                          asked=app.day.here(), request_snapshot=pin)
        runs, _ = await plan(app, [('reference_photo', {'subject': 'leaf pore', 'beside': True})], ctx=ctx)
        run = runs[0]
        assert run.ok is False and 'retained' in run.summary
        assert [controlled_state(w) for w in app.services.presentations.list(WORKSPACE)] == before
        assert run.resource
        entry = app.hub.resources.get(run.resource['id'], run.resource['day'])
        assert_retained(app, entry, pixels)
        assert any(o.get('kind') == 'source' for o in run.outputs)
        assert not any(o.get('kind') == 'presentation' for o in run.outputs)
    asyncio.run(go())


def test_native_reference_beside_keeps_anchor_across_web_lookup(retained_reference, monkeypatch):
    import threading
    from research_worker import retrieval
    from orchestrator.brain.registry import ToolContext
    app, studio, _, _ = retained_reference
    entered, release = threading.Event(), threading.Event()
    lookup = retrieval.lookup

    async def gated(*args, **kwargs):
        entered.set()
        assert await asyncio.to_thread(release.wait, HANG_S)
        return await lookup(*args, **kwargs)
    monkeypatch.setattr(retrieval, 'lookup', gated)

    async def go():
        original, _ = saved_picture(app)
        anchor, _, _ = await open_window(app, original)
        await studio.send_report()
        pin = app.hub.screen.pin(studio, request_id='beside-photo', timing='client-send')
        ctx = ToolContext('chat', 2, None, 'bring up a reference photo beside it',
                          asked=app.day.here(), request_snapshot=pin)
        request = asyncio.create_task(plan(app, [('reference_photo', {'subject': 'leaf pore', 'beside': True})], ctx=ctx))
        try:
            assert await asyncio.to_thread(entered.wait, HANG_S)
            other, _ = saved_picture(app, value=130)
            newer, _, _ = await open_window(app, other)
            release.set()
            runs, _ = await request
            succeeded(runs[0])
            child = next(w for w in app.services.presentations.list(WORKSPACE) if w.id not in (anchor, newer))
            assert json.loads(child.geometry_intent.relation_json)['reference'] == anchor
        finally:
            release.set()
            await request
    asyncio.run(go())


def test_native_beside_ignores_anchor_repaint_during_source_prepare(retained_reference, monkeypatch):
    """Real prepare/admission, external redraw only; geometry/source owners stay exact."""
    from orchestrator.brain.registry import ToolContext
    app, studio, _, _ = retained_reference

    async def go():
        original, _ = saved_picture(app)
        anchor, _, _ = await open_window(app, original)
        await studio.send_report()
        pin = app.hub.screen.pin(studio, request_id='beside-photo', timing='client-send')
        ctx = ToolContext('chat', 2, None, 'bring up a reference photo beside it',
                          asked=app.day.here(), request_snapshot=pin)
        before = controlled_state(app.services.presentations.get(WORKSPACE, anchor))
        prepare = app.services.prepared_sources.prepare
        async def prepare_across_repaint(*args, **kwargs):
            lease = await prepare(*args, **kwargs)
            row = studio.items[anchor]
            row['load']['drawToken'] = row['drawRef'] = 'next-paint-same-source'
            row['viewDrawRef'] = 'next-view-paint-same-geometry'
            studio.command_results = []
            await studio.send_report()
            return lease
        monkeypatch.setattr(app.services.prepared_sources, 'prepare', prepare_across_repaint)
        runs, _ = await plan(app, [('reference_photo', {'subject': 'leaf pore', 'beside': True})], ctx=ctx)
        succeeded(runs[0])
        assert controlled_state(app.services.presentations.get(WORKSPACE, anchor)) == before
        child = next(w for w in app.services.presentations.list(WORKSPACE) if w.id != anchor)
        assert json.loads(child.geometry_intent.relation_json)['reference'] == anchor
        assert runs[0].outcome['status'] == 'confirmed'
    asyncio.run(go())


def _on_a_back_tab(studio, monkeypatch, anchor, *, front_title='Reference'):
    """The studio once the person's next request put a new Tab in front (live 2026-10-29 18:54:35): the anchor stays
    open on the Main Tab, laid out nowhere (display:none, a 0x0 rect, not visible), and the Tab strip says which Tab is
    in front. Only the studio's DOM facts change; the Core's window is untouched."""
    from orchestrator.commands import COMMANDS

    row = studio.items[anchor]
    row.update(visible=False, rect=[0, 0, 0, 0], contentRect=[0, 0, 0, 0], clipRect=[0, 0, 0, 0],
               drawRef=None, viewDrawRef=None, visibility='unknown')
    row['load'] = {**row['load'], 'state': 'loaded', 'drawToken': None}
    row['view'] = {**row['view'], 'arrangement': {'project': 'project-a', 'tab': 'main', 'placement': 'p-anchor',
                                                  'focused': False}}
    screen = COMMANDS['screen']

    async def with_strip(app, client, report):
        report = {**report, 'items': [*report['items'], {
            'id': 'workspace-tabs', 'rootId': 'tabs-root', 'workspaceId': WORKSPACE, 'kind': 'panel',
            'lifetimeGeneration': 'tabs-lifetime', 'sourceGeneration': 1, 'load': {'state': 'unknown'},
            'revision': report['seq'], 'open': True, 'connected': True, 'visible': True,
            'view': {'legacy': {'schema': 'studio-surface-v1', 'scope': 'dom-observation', 'workspace': {
                'project': 'project-a', 'tab': 'reference', 'tabTitle': front_title,
                'tabs': [{'id': 'main', 'title': 'Main', 'front': False},
                         {'id': 'reference', 'title': front_title, 'front': True}]}}}}]}
        return await screen(app, client, report)
    monkeypatch.setitem(COMMANDS, 'screen', with_strip)


def test_a_photo_asked_for_beside_a_window_now_on_a_back_tab_opens_where_the_person_is_and_says_why(
        retained_reference, monkeypatch):
    """Live 2026-10-29 18:53:56-18:54:43: "pull up an image from online of a single stoma and put it next to the
    magnification", then, while the web lookup ran (42 s), "open a new tab called Reference". When the photo came the
    magnifier was open on the Main Tab, laid out nowhere; the fence lumped that with a changed window ("beside needs both
    exact current window rectangles and owners"), so the photo was kept as d1 and never shown, and the person was told to
    make room. Nobody changed the magnifier: the photo opens where the person is, and the receipt says why it is not
    beside it, from the report."""
    import threading
    from research_worker import retrieval
    from orchestrator.brain.registry import ToolContext
    app, studio, pixels, _ = retained_reference
    entered, release = threading.Event(), threading.Event()
    lookup = retrieval.lookup

    async def gated(*args, **kwargs):
        entered.set()
        assert await asyncio.to_thread(release.wait, HANG_S)
        return await lookup(*args, **kwargs)
    monkeypatch.setattr(retrieval, 'lookup', gated)

    async def go():
        original, _ = saved_picture(app)
        anchor, _, _ = await open_window(app, original)
        await studio.send_report()
        pin = app.hub.screen.pin(studio, request_id='beside-photo', timing='client-send')
        ctx = ToolContext('chat', 2, None, 'pull up an image from online and put it next to the magnification',
                          asked=app.day.here(), request_snapshot=pin)
        before = controlled_state(app.services.presentations.get(WORKSPACE, anchor))
        request = asyncio.create_task(plan(app, [('reference_photo', {'subject': 'leaf pore', 'beside': True})], ctx=ctx))
        try:
            assert await asyncio.to_thread(entered.wait, HANG_S)
            _on_a_back_tab(studio, monkeypatch, anchor)
            await studio.send_report()
            release.set()
            runs, _ = await request
        finally:
            release.set()
        run = runs[0]
        succeeded(run)
        assert run.outcome['status'] == 'confirmed'
        assert controlled_state(app.services.presentations.get(WORKSPACE, anchor)) == before, 'the anchor is untouched'
        child = next(w for w in app.services.presentations.list(WORKSPACE) if w.id != anchor)
        assert child.open and child.geometry_intent.relation_json is None, 'opened plainly, never against a 0x0 rect'
        assert f'not beside window {anchor}' in run.summary
        assert '"Main" Tab' in run.summary and '"Reference" Tab is in front' in run.summary
        assert 'ask again' not in run.summary and 'make room' not in run.summary
        assert any(o.get('kind') == 'presentation' for o in run.outputs)
        entry = app.hub.resources.get(run.resource['id'], run.resource['day'])
        assert_retained(app, entry, pixels)
    asyncio.run(go())


def test_a_report_behind_the_window_a_photo_goes_beside_is_asked_again_never_refused(retained_reference, monkeypatch):
    """The studio's last report, when the photo came, did not show the anchor (a report taken without it, as one with an
    ended grant is), while the app's window was unchanged and still open. That is the report being behind, Hooke's to
    resolve: it asks the studio for a report now, looks again once, and places the photo beside the anchor."""
    import threading
    from research_worker import retrieval
    from orchestrator.brain.registry import ToolContext
    app, studio, _, _ = retained_reference
    entered, release = threading.Event(), threading.Event()
    lookup = retrieval.lookup

    async def gated(*args, **kwargs):
        entered.set()
        assert await asyncio.to_thread(release.wait, HANG_S)
        return await lookup(*args, **kwargs)
    monkeypatch.setattr(retrieval, 'lookup', gated)

    async def go():
        original, _ = saved_picture(app)
        anchor, _, _ = await open_window(app, original)
        await studio.send_report()
        pin = app.hub.screen.pin(studio, request_id='beside-photo', timing='client-send')
        ctx = ToolContext('chat', 2, None, 'pull up an image from online and put it next to the magnification',
                          asked=app.day.here(), request_snapshot=pin)
        asked = sum(m.get('type') == 'screen-ask' for m in studio.messages)
        request = asyncio.create_task(plan(app, [('reference_photo', {'subject': 'leaf pore', 'beside': True})], ctx=ctx))
        try:
            assert await asyncio.to_thread(entered.wait, HANG_S)
            row = studio.items.pop(anchor)
            await studio.send_report()  # the report that is behind: the anchor left out of it
            studio.items[anchor] = row
            assert all(i['id'] != anchor for i in app.hub.screen.observation(studio).wire()['items'])
            release.set()
            runs, _ = await request
        finally:
            release.set()
        succeeded(runs[0])
        assert runs[0].outcome['status'] == 'confirmed'
        assert sum(m.get('type') == 'screen-ask' for m in studio.messages) > asked, 'Hooke asked the studio again'
        child = next(w for w in app.services.presentations.list(WORKSPACE) if w.id != anchor)
        assert json.loads(child.geometry_intent.relation_json)['reference'] == anchor, 'placed beside, as asked'
        assert 'not beside' not in runs[0].summary
    asyncio.run(go())


def test_what_a_window_is_to_a_placement_is_said_by_the_check_that_failed(retained_reference, monkeypatch):
    """The placement fence names why a window cannot anchor, from the report and the app: laid out in a studio in the
    background (nothing is painted there) it still anchors, as completion counts it shown; open on a Tab not in front
    it is "hidden", with where it is; the report behind the app "disagrees" (asked again before it is said); closed in
    the app it is "gone". The old lump reason ("beside needs both exact current window rectangles and owners") said
    none of these."""
    import dataclasses
    from orchestrator.brain.toolbox.present import _placement_observation
    from orchestrator.presentations import PresentationError
    app, studio, _, _ = retained_reference

    def why(report):
        try:
            _placement_observation(states, report, (anchor,))
        except PresentationError as exc:
            return exc.placement, exc.why
        return None

    async def go():
        nonlocal states, anchor
        original, _ = saved_picture(app)
        anchor, _, _ = await open_window(app, original)
        states = {anchor: app.services.presentations.get(WORKSPACE, anchor)}
        await studio.send_report()
        assert why(app.hub.screen.completion_report(studio)) is None
        studio.visible = False
        await studio.send_report()
        background = app.hub.screen.completion_report(studio)
        assert next(i for i in background['items'] if i['id'] == anchor)['visible'] is False
        assert why(background) is None, 'laid out in a studio in the background: still there to be beside'
        studio.visible = True
        behind = app.hub.screen.completion_report(studio)
        behind['items'] = [{**i, 'propertyOwners': {**i['propertyOwners'], 'rect': 'someone-else'}}
                           if i['id'] == anchor else i for i in behind['items']]
        assert why(behind) == ('disagrees', "the studio's report has another owner of its rect than the app")
        _on_a_back_tab(studio, monkeypatch, anchor)
        await studio.send_report()
        assert why(app.hub.screen.completion_report(studio)) == (
            'hidden', 'it is on the "Main" Tab, and the "Reference" Tab is in front')
        states[anchor] = dataclasses.replace(states[anchor], open=False)
        assert why(app.hub.screen.completion_report(studio)) == ('gone', 'it was closed')

    states, anchor = {}, None
    asyncio.run(go())
