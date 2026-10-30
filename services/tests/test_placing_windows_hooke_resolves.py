"""Placing windows in the workspace: conflicts Hooke made are Hooke's to resolve (the live partner spec 4.8,
horizontals 2 and 3; docs/architecture/on-screen.md "Placing things").

Each test starts from a fault in Will's voice journeys of 29-30 Oct (measure-c4acc90):

- will-leaf, step "reference" ("Pull up a reference image of a stoma."), runs 2 and 3: the reference photo window was
  already open (restored with the Project), the brain called present front on it, and it was never confirmed ("The
  required draw was not confirmed before its deadline."), so the voice said "Ask again in a moment". The studio reported
  the stage's foreground as its own two children, `side` and `study-artifacts`: in the Project workspace every native
  window is mounted inside `#study-artifacts` (main.ts onPresentation), so no window's own root is ever one of the
  stage's foreground roots, and front could never be proved there.
"""
import asyncio
import json

import pytest

from orchestrator import screen as screen_mod
from orchestrator.commands import COMMANDS
from tests.test_workspace_actions_app import (WORKSPACE, built, open_window, plan, saved_picture,
                                               succeeded)


def nested_in_the_project_workspace(monkeypatch, studio):
    """The studio's reports as the live Project workspace sends them (will-leaf run 2, report seq 214): the stage's
    foreground lists only its own children (`side` z 25, `study-artifacts` z 35), incomplete, and the report itself is
    incomplete (items omitted). The windows' own z values are what the studio measured inside #study-artifacts."""
    original = COMMANDS['screen']

    async def screen(app, client, report):
        if client is studio and isinstance(report, dict) and report.get('schema') == 'screen-observation-v2':
            report = {**report, 'complete': False,
                      'foreground': {'scope': 'workspace-windows', 'hostId': 'stage', 'complete': False,
                                     'roots': [{'rootId': 'side', 'z': 25}, {'rootId': 'study-artifacts', 'z': 35}]}}
        return await original(app, client, report)

    monkeypatch.setitem(COMMANDS, 'screen', screen)


def test_front_on_a_window_inside_the_project_workspace_is_confirmed_by_its_tab_order(tmp_path, monkeypatch):
    """will-leaf "reference", runs 2-3: present front on the open reference photo is done once the report shows it
    above every other window on its Tab, never a timeout the user is told to ask again about."""
    monkeypatch.setattr(screen_mod, 'CONFIRM_S', 1.0)
    app, studio = built(tmp_path)
    nested_in_the_project_workspace(monkeypatch, studio)
    entry, _ = saved_picture(app)

    async def go():
        photo, _, _ = await open_window(app, entry)
        other, _, _ = await open_window(app, entry)
        assert studio.items[other]['z'] > studio.items[photo]['z'], 'SETUP: the photo starts behind the other window'
        runs, _ = await plan(app, [('present', {'do': 'front', 'instance': {'instanceId': photo}})])
        succeeded(runs[0])
        assert runs[0].outcome['status'] == 'confirmed', runs[0].outcome
        assert studio.items[photo]['z'] > studio.items[other]['z']
        front = next(e for e in runs[0].outcome['expected'] if e['predicate'] == 'front')
        assert front['expected']['scope'] == 'tab-windows' and front['expected']['above'] == [other]
        assert 'ask again' not in runs[0].summary.lower()
    asyncio.run(go())


def test_front_nested_is_not_confirmed_while_another_window_on_the_tab_is_still_over_it(tmp_path, monkeypatch):
    """The Tab-order proof is a real check: a renderer that never raises the window is not confirmed, and the failure
    is said without handing the user "ask again"."""
    monkeypatch.setattr(screen_mod, 'CONFIRM_S', 0.4)
    app, studio = built(tmp_path)
    nested_in_the_project_workspace(monkeypatch, studio)
    entry, _ = saved_picture(app)

    async def go():
        photo, _, _ = await open_window(app, entry)
        other, _, _ = await open_window(app, entry)
        observe = studio.observe_command

        async def never_raises(message):
            if message.get('op') == 'front':
                message = {**message, 'op': 'noop-front'}  # acknowledged and painted, but the stacking never changes
            await observe(message)

        studio.observe_command = never_raises
        runs, _ = await plan(app, [('present', {'do': 'front', 'instance': {'instanceId': photo}})])
        assert runs[0].ok is False
        assert studio.items[other]['z'] > studio.items[photo]['z']
        assert len([m for m in studio.messages if m.get('op') == 'front']) == 2, 'Hooke sent it once more itself'
        assert 'ask again' not in runs[0].summary.lower(), runs[0].summary
        assert 'sent twice' in runs[0].summary
    asyncio.run(go())


# --- a Tab name already taken (will-workspace "new-tab", run 3) ---------------------------------------------------------

async def in_one_request(app, studio, calls):
    """Several tool calls of one request, as the brain makes them for one hand-off: one ticket, one pin, one context
    (test_table_window.asked, for more than one call)."""
    from orchestrator.brain.registry import ToolContext
    from orchestrator.ticket import issue, using
    from tests.test_workspace_actions_app import WORKSPACE as SPACE

    await studio.send_report()
    ticket = issue(app.hub, 'turn', asked=app.day.here(), channel='chat')
    pin = app.hub.screen.pin(studio, request_id=ticket.admission.request_id, timing='server-receive-legacy')
    ctx = ToolContext('voice', 1, None, 'Open a new tab called Reference and put that photo in it.', asked=ticket.asked)
    ctx.client, ctx.workspace_id = studio, SPACE
    ctx.admission, ctx.request_id, ctx.request_snapshot = ticket.admission, ticket.admission.request_id, pin
    runs = []
    try:
        with using(ticket):
            for tool, args in calls:
                run = app.brain.registry.start(tool, args, ctx)
                await run.task
                await asyncio.gather(*studio.tasks)
                runs.append(run)
    finally:
        ticket.seal()
    return runs


def _reference_tab_already_there(studio):
    """Run 3's Project as runs 1-2 left it: Main, and a Tab called Reference holding the earlier photo's table."""
    studio.tabs.append({'id': 'tab-ref-1', 'title': 'Reference'})
    studio.specimens['tab-ref-1'] = {'state': 'hidden'}


def test_a_new_tab_called_as_one_already_there_is_made_told_apart_and_used_by_the_same_request(tmp_path, monkeypatch):
    """will-workspace run 3: tabs create "Reference" made a second "Reference", then the place in Tab "Reference" was
    refused ("several Tabs are called 'Reference'; ask which one") and the user was asked which. The new Tab is made as
    "Reference 2", the reply says its name, and the same request's next step names "Reference" and gets the new one."""
    from tests.test_workspace_display import built as display_built, population

    async def go():
        app, studio = await display_built(tmp_path, monkeypatch)
        eid = population(app, monkeypatch)
        _reference_tab_already_there(studio)
        made, placed = await in_one_request(app, studio, [
            ('tabs', {'action': 'create', 'title': 'Reference'}),
            ('show_measurements', {'of': eid, 'tab': 'Reference', 'placement': 'only'})])
        assert made.ok is True, made.summary
        reply = json.loads(made.summary)
        assert reply['made']['title'] == 'Reference 2' and reply['made']['asked'] == 'Reference'
        assert 'already in this Project, so the new Tab is called "Reference 2"' in reply['made']['named']
        assert [t['title'] for t in studio.tabs] == ['Main', 'Reference', 'Reference 2']
        assert placed.ok is True, placed.summary
        new = next(t['id'] for t in studio.tabs if t['title'] == 'Reference 2')
        assert studio.active == new
        assert [w['tab'] for w in studio.windows.values()] == [new], 'the table is in the new Tab, not the old one'
        assert 'Reference 2' in placed.summary
        for run in (made, placed):
            assert 'which one' not in run.summary and 'ask again' not in run.summary
    asyncio.run(go())


def test_a_new_tab_placement_with_a_taken_name_makes_a_new_tab_told_apart(tmp_path, monkeypatch):
    """present place … new-tab with tab "Reference" when a Reference Tab is there: a new Tab, "Reference 2", never the
    old one reused for a new Tab."""
    from tests.test_workspace_display import built as display_built, population

    async def go():
        app, studio = await display_built(tmp_path, monkeypatch)
        eid = population(app, monkeypatch)
        _reference_tab_already_there(studio)
        (run,) = await in_one_request(app, studio, [
            ('present', {'do': 'place', 'what': eid, 'placement': 'new-tab', 'tab': 'Reference'})])
        assert run.ok is True, run.summary
        assert [t['title'] for t in studio.tabs] == ['Main', 'Reference', 'Reference 2']
        assert 'new Tab "Reference 2"' in run.summary
    asyncio.run(go())


def test_two_tabs_with_one_name_are_never_the_users_to_choose_between(tmp_path, monkeypatch):
    """A Project left with two Tabs called Reference (run 3's own result): "put it in the Reference tab" goes to the one
    in front, else the newest, and is done, never refused with "which one?"."""
    from tests.test_workspace_display import built as display_built, population

    async def go():
        app, studio = await display_built(tmp_path, monkeypatch)
        eid = population(app, monkeypatch)
        _reference_tab_already_there(studio)
        studio.tabs.append({'id': 'tab-ref-2', 'title': 'Reference'})
        studio.specimens['tab-ref-2'] = {'state': 'hidden'}
        (run,) = await in_one_request(app, studio, [
            ('show_measurements', {'of': eid, 'tab': 'Reference', 'placement': 'only'})])
        assert run.ok is True, run.summary
        assert studio.active == 'tab-ref-2', 'the newest of the two, none being in front'
        studio.active = 'tab-ref-1'
        (run,) = await in_one_request(app, studio, [
            ('show_measurements', {'of': eid, 'tab': 'reference', 'placement': 'only'})])
        assert run.ok is True, run.summary
        assert studio.active == 'tab-ref-1', 'the one in front'
    asyncio.run(go())


# --- a photo beside a window with no room beside it (will-workspace "reference-beside", run 3) -------------------------

from tests.test_reference_artifact_presentation import (retained_reference,  # noqa: E402,F401 - its fixtures
                                                         scripted_photo_pixel_check)


def _overlap(a, b):
    width = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    height = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    return width * height


def test_a_photo_beside_a_window_with_no_room_beside_it_gets_room_made_and_is_placed(retained_reference):
    """will-workspace run 3: "Pull up an image from online of a single stoma and put it next to the magnification."
    reference_photo with beside=true refused: "There is not enough space beside this report for its captured view.;
    make room or select the original window", and the voice told the user to make space. The magnification sat wide in
    the middle, with too little room on either side. Hooke moves it over (its size kept when that is enough), then
    places the photo beside it, and says what it moved."""
    from orchestrator.brain.registry import ToolContext
    app, studio, _, _ = retained_reference

    async def go():
        studio.presentation_area = [0, 0, 1200, 800]
        original, _ = saved_picture(app)
        anchor, _, _ = await open_window(app, original)
        for verb, values in (('resize', {'size': {'width': 700, 'height': 420}}),
                             ('move', {'position': {'x': 250, 'y': 300}})):
            changed, _ = await plan(app, [('present', {'do': verb, 'instance': {'instanceId': anchor}, **values})])
            succeeded(changed[0])
        assert studio.items[anchor]['rect'] == [250, 300, 700, 420], 'SETUP: under 240 px free on either side'
        await studio.send_report()
        pin = app.hub.screen.pin(studio, request_id='beside-photo', timing='client-send')
        ctx = ToolContext('voice', 2, None, 'Pull up an image from online of a single stoma and put it next to the '
                          'magnification.', asked=app.day.here(), request_snapshot=pin)
        runs, _ = await plan(app, [('reference_photo', {'subject': 'single stoma', 'beside': True})], ctx=ctx)
        run = runs[0]
        succeeded(run)
        child = next(w for w in app.services.presentations.list(WORKSPACE) if w.id != anchor)
        assert json.loads(child.geometry_intent.relation_json)['reference'] == anchor, 'placed beside it'
        a, c = studio.items[anchor]['rect'], studio.items[child.id]['rect']
        assert _overlap(a, c) == 0
        assert a[2] == 700, 'its size kept: moving it was enough'
        assert c[0] + c[2] <= 1200 and c[1] + c[3] <= 800, 'the photo inside the visible area'
        assert run.outcome['status'] == 'confirmed'
        said = run.summary.lower()
        assert 'moved' in said and 'make room' not in said and 'ask again' not in said, run.summary
    asyncio.run(go())


def test_a_photo_beside_a_window_in_an_area_too_small_for_both_is_said_plainly(retained_reference):
    """A true impossibility (the visible area cannot hold both windows side by side): nothing is moved, the photo is
    kept, and it is said in plain words, with nothing handed to the user to make room or ask again."""
    from orchestrator.brain.registry import ToolContext
    app, studio, _, _ = retained_reference

    async def go():
        original, _ = saved_picture(app)
        anchor, _, _ = await open_window(app, original)
        studio.presentation_area = [0, 0, 390, 500]
        await studio.send_report()
        pin = app.hub.screen.pin(studio, request_id='beside-photo', timing='client-send')
        ctx = ToolContext('voice', 2, None, 'put a photo of a stoma next to it', asked=app.day.here(),
                          request_snapshot=pin)
        runs, _ = await plan(app, [('reference_photo', {'subject': 'single stoma', 'beside': True})], ctx=ctx)
        run = runs[0]
        assert run.ok is False and 'kept' in run.summary
        said = run.summary.lower()
        assert 'make room' not in said and 'ask again' not in said and 'select the original' not in said, run.summary
    asyncio.run(go())
