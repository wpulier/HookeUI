"""Hooke prepares Tabs quietly (show false) and sets one up in one action (compose): the user's screen stays where it is,
the receipt is the studio's saved layout, and every Tab action is timed (`tab-action`, bench/tab_speed.py)."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from orchestrator.brain.registry import ToolFailed
from orchestrator.voice.hands import intent_of
from orchestrator.voice.records import Records
from tests.test_workspace_tabs import Rig
from tests.waiting import HANG_S


def answer(rig, command, result):
    """The studio's reply to one workspace-tabs message (workspace-views.ts agentCommand)."""
    assert rig.broker.reply(rig.client, {'id': command['id'], 'session': 'project-a',
                                         'workspacePin': command['workspacePin'], 'result': result})


def tabs(*rows):
    return [{'id': i, 'title': t, 'open': True, 'selected': s, 'kind': 'work', 'omittedItems': 0, 'items': items,
             'specimen': {'state': 'float'}} for i, t, s, items in rows]


async def next_command(rig):
    return await asyncio.wait_for(rig.client.sent.get(), HANG_S)


def logged(rig):
    records = []
    rig.app.agent = SimpleNamespace(log=records.append)
    return records


def test_a_quiet_new_tab_is_made_without_coming_to_the_front_and_said_prepared():
    async def run():
        rig = Rig()
        records = logged(rig)
        task = asyncio.create_task(rig.broker.run(rig.ctx, {'action': 'create', 'title': 'Reference', 'show': False}))
        listing = await next_command(rig)
        assert listing['action'] == {'action': 'list'}
        answer(rig, listing, {'ok': True, 'tabs': tabs(('main', 'Main', True, []))})
        create = await next_command(rig)
        assert create['action'] == {'action': 'create', 'title': 'Reference', 'show': False}
        answer(rig, create, {'ok': True, 'quiet': True, 'made': {'id': 'tab-2', 'title': 'Reference'},
                             'tabs': tabs(('main', 'Main', True, []), ('tab-2', 'Reference', False, []))})
        reply = await task
        assert reply['made']['id'] == 'tab-2' and 'not on screen' in reply['prepared']
        assert rig.client.sent.empty(), 'nothing asked of the screen: a quiet Tab is not waited for there'
        (record,) = [r for r in records if r['action'] == 'create']
        assert record['kind'] == 'tab-action' and record['show'] is False and record['outcome'] == 'ok'
        assert record['totalMs'] >= record['studioMs'] >= 0 and 'listMs' in record
    asyncio.run(run())


def test_a_quiet_arrange_on_a_tab_not_in_view_is_prepared_not_waited_for_on_screen():
    async def run():
        rig = Rig()
        task = asyncio.create_task(rig.broker.run(rig.ctx, {'action': 'arrange', 'tab': 'other', 'show': False}))
        command = await next_command(rig)
        assert command['action'] == {'action': 'arrange', 'tab': 'other', 'show': False}
        answer(rig, command, {'ok': True, 'deferred': True, 'quiet': True,
                              'tabs': tabs(('main', 'Main', True, []), ('other', 'Other', False, []))})
        reply = await asyncio.wait_for(task, HANG_S)
        assert reply['prepared'].startswith('arranged when that Tab is shown')
        assert 'shown' not in reply
    asyncio.run(run())


def test_compose_puts_saved_items_in_one_new_tab_in_one_message():
    async def run():
        rig = Rig()
        kinds = {'tb1': 'dataset-table', 'p1': 'population'}
        rig.app.hub.resources = SimpleNamespace(
            get=lambda rid, session: SimpleNamespace(id=rid, version=3, kind=kinds[rid], title=rid.upper()))
        task = asyncio.create_task(rig.broker.run(rig.ctx, {
            'action': 'compose', 'title': 'Leaves', 'show': False,
            'items': [{'id': 'tb1'}, {'id': 'p1', 'presentation': 'table'}]}))
        listing = await next_command(rig)
        answer(rig, listing, {'ok': True, 'tabs': tabs(('main', 'Main', True, []))})
        compose = await next_command(rig)
        sent = compose['action']
        assert sent['action'] == 'compose' and sent['title'] == 'Leaves' and sent['show'] is False
        assert [(i['resource']['id'], i['resource']['version'], i['presentation']) for i in sent['items']] == \
            [('tb1', 3, 'datatable'), ('p1', 3, 'table')]
        held = [{'placement': 'pl1', 'resource': {'id': 'tb1'}}, {'placement': 'pl2', 'resource': {'id': 'p1'}}]
        answer(rig, compose, {'ok': True, 'made': {'id': 'tab-2', 'title': 'Leaves'},
                              'tabs': tabs(('main', 'Main', True, []), ('tab-2', 'Leaves', False, held))})
        reply = await task
        assert reply['made']['id'] == 'tab-2' and 'not on screen' in reply['prepared']
    asyncio.run(run())


def test_compose_refuses_a_photo_and_says_how_it_is_placed():
    async def run():
        rig = Rig()
        rig.app.hub.resources = SimpleNamespace(
            get=lambda rid, session: SimpleNamespace(id=rid, version=1, kind='image', title='Stoma'))
        task = asyncio.create_task(rig.broker.run(rig.ctx, {'action': 'compose', 'title': 'Ref',
                                                            'items': [{'id': 'img1'}]}))
        listing = await next_command(rig)
        answer(rig, listing, {'ok': True, 'tabs': tabs(('main', 'Main', True, []))})
        with pytest.raises(ToolFailed, match='not added to a Tab this way') as raised:
            await task
        assert 'present with do place' in raised.value.next
        assert rig.client.sent.empty(), 'nothing is made when one item cannot go in it'
    asyncio.run(run())


@pytest.mark.parametrize('bad', [{'show': 'no'}, {'arrange': 1}])
def test_show_and_arrange_are_true_or_false(bad):
    async def run():
        rig = Rig()
        with pytest.raises(ToolFailed, match='true or false'):
            await rig.broker.run(rig.ctx, {'action': 'compose', 'title': 'X', **bad})
    asyncio.run(run())


def test_a_quiet_tab_action_has_no_hand_and_is_checked_against_the_saved_layout():
    quiet = intent_of('tabs', {'action': 'compose', 'title': 'Leaves', 'show': False})
    assert quiet.ui is False and quiet.check == 'record' and quiet.caption == 'Setting up a Tab quietly'
    shown = intent_of('tabs', {'action': 'compose', 'title': 'Leaves'})
    assert shown.ui is True and shown.check == 'screen'
    # A switch is always shown: it is how a prepared Tab is revealed.
    assert intent_of('tabs', {'action': 'switch', 'tab': 't', 'show': False}).ui is True


def summary(**reply):
    return {'ok': True, 'summary': json.dumps({'ok': True, **reply})}


def test_the_receipt_of_a_quiet_compose_is_what_the_saved_layout_holds():
    intent = intent_of('tabs', {'action': 'compose', 'title': 'Leaves', 'show': False,
                                'items': [{'id': 'tb1'}, {'id': 'p1'}]})
    held = [{'placement': 'pl1', 'resource': {'id': 'tb1'}}, {'placement': 'pl2', 'resource': {'id': 'p1'}}]
    done = Records._tabbed(intent, summary(made={'id': 'tab-2'}, tabs=tabs(('tab-2', 'Leaves', False, held))))
    assert done['result'] == 'verified' and 'holding 2 items; not on screen' in done['said']
    short = Records._tabbed(intent, summary(made={'id': 'tab-2'}, tabs=tabs(('tab-2', 'Leaves', False, held[:1]))))
    assert short['result'] == 'uncertain' and '1 of the 2' in short['error']
    missing = Records._tabbed(intent, summary(tabs=tabs(('main', 'Main', True, []))))
    assert missing['result'] == 'uncertain'


def test_the_receipt_of_a_quiet_arrange_and_specimen():
    arrange = intent_of('tabs', {'action': 'arrange', 'tab': 'other', 'show': False})
    assert Records._tabbed(arrange, summary(deferred=True, tabs=[]))['said'].startswith('Prepared')
    specimen = intent_of('tabs', {'action': 'specimen', 'tab': 'other', 'specimen': 'hide', 'show': False})
    hidden = tabs(('other', 'Other', False, []))
    hidden[0]['specimen'] = {'state': 'hidden'}
    assert Records._tabbed(specimen, summary(tabs=hidden))['result'] == 'verified'
    assert Records._tabbed(specimen, summary(tabs=tabs(('other', 'Other', False, []))))['result'] == 'uncertain'


def test_a_measurements_table_is_prepared_quietly_in_a_new_tab_while_the_screen_stays_on_main(tmp_path, monkeypatch):
    """show_measurements show false: "get a tab ready with the cell measurements, but don't switch to it yet" (the
    will-quiet-tabs journey). The Tab is made quietly and holds the table in its saved layout; Main stays in front."""
    from tests.test_placing_windows_hooke_resolves import in_one_request
    from tests.test_workspace_display import built as display_built, population

    async def go():
        app, studio = await display_built(tmp_path, monkeypatch)
        eid = population(app, monkeypatch)
        (run,) = await in_one_request(app, studio, [
            ('show_measurements', {'of': eid, 'tab': 'Measurements', 'show': False})])
        assert run.ok is True, run.summary
        assert run.summary.startswith('Prepared: ') and 'not on screen until that Tab is shown' in run.summary
        new = next(t['id'] for t in studio.tabs if t['title'] == 'Measurements')
        assert studio.active == 'main', 'the screen stays on Main'
        held = [w for w in studio.windows.values() if w['tab'] == new]
        assert [w['resource']['id'] for w in held] == [eid] and held[0]['representation'] == 'table'
        assert not [w for w in studio.windows.values() if w['tab'] == 'main'], 'nothing opened on the Tab in front'
    asyncio.run(go())


def test_a_quiet_table_on_the_tab_in_front_is_simply_shown(tmp_path, monkeypatch):
    from tests.test_placing_windows_hooke_resolves import in_one_request
    from tests.test_workspace_display import built as display_built, population

    async def go():
        app, studio = await display_built(tmp_path, monkeypatch)
        eid = population(app, monkeypatch)
        (run,) = await in_one_request(app, studio, [('show_measurements', {'of': eid, 'tab': 'Main', 'show': False})])
        assert run.ok is True, run.summary
        assert not run.summary.startswith('Prepared'), 'Main is in front: the table is shown there, not prepared'
        assert studio.windows
    asyncio.run(go())


def test_a_quiet_table_has_no_hand_and_its_receipt_is_what_the_tool_prepared():
    intent = intent_of('show_measurements', {'of': 'e3', 'tab': 'Measurements', 'show': False})
    assert intent.ui is False and intent.check == 'record' and intent.caption == 'Preparing the table quietly'
    prepared = {'ok': True, 'summary': 'Prepared: the measurements are in the Tab "Measurements", not on screen until '
                                       'that Tab is shown. Facts: saved: in Data as e3'}
    receipt = asyncio.run(Records(None).check(intent, prepared, None, {}))
    assert receipt['result'] == 'verified' and 'Facts' not in receipt['said']


def test_a_closed_tab_is_reopened_quietly_and_said_back_among_the_open_tabs():
    """How Hooke undoes a close (Lane B's tidy) without moving the person's screen: reopen with show false."""
    async def run():
        rig = Rig()
        task = asyncio.create_task(rig.broker.run(rig.ctx, {'action': 'reopen', 'tab': 'other', 'show': False}))
        command = await next_command(rig)
        assert command['action'] == {'action': 'reopen', 'tab': 'other', 'show': False}
        answer(rig, command, {'ok': True, 'quiet': True,
                              'tabs': tabs(('main', 'Main', True, []), ('other', 'Other', False, []))})
        reply = await asyncio.wait_for(task, HANG_S)
        assert reply['prepared'].startswith('back among the open Tabs') and 'shown' not in reply
    asyncio.run(run())


def test_a_quiet_reopen_the_studio_did_not_do_is_said_plainly():
    async def run():
        rig = Rig()
        task = asyncio.create_task(rig.broker.run(rig.ctx, {'action': 'reopen', 'tab': 'other', 'show': False}))
        command = await next_command(rig)
        closed = tabs(('main', 'Main', True, []), ('other', 'Other', False, []))
        closed[1]['open'] = False
        answer(rig, command, {'ok': True, 'quiet': True, 'tabs': closed})
        with pytest.raises(ToolFailed, match='not reopened'):
            await asyncio.wait_for(task, HANG_S)
    asyncio.run(run())


def test_the_receipt_of_a_reopen():
    quiet = intent_of('tabs', {'action': 'reopen', 'tab': 'other', 'show': False})
    assert quiet.ui is False and quiet.caption == 'Reopening the Tab quietly'
    assert intent_of('tabs', {'action': 'reopen', 'tab': 'other'}).ui is True
    back = Records._tabbed(quiet, summary(tabs=tabs(('other', 'Other', False, []))))
    assert back['result'] == 'verified' and back['said'].startswith('The Tab "Other" is open again')
    still = tabs(('other', 'Other', False, []))
    still[0]['open'] = False
    assert Records._tabbed(quiet, summary(tabs=still))['result'] == 'uncertain'
