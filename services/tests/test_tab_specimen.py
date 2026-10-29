"""Each Tab's specimen (the live view) is part of what Hooke knows is on screen and can change
(docs/architecture/on-screen.md "A Tab's specimen").

Live, 29 October: asked "only have the image open in the Stoma Reference tab and close everything else", the Assistant
closed windows and said the Tab held only the reference image, while the live specimen still filled that Tab behind it.
The report did not carry the specimen and no tool could change it. Now the Tab strip reports each Tab's specimen
(filling it, floating with its place, or hidden), the server reads it wherever the screen is told to a model, and
`tabs` action `specimen` floats, fills or hides it, done only once the report shows it.

Actual App, Registry, Screen, tabs broker and COMMANDS, with the synthetic studio of test_table_window.py, whose Tab
strip here also reports each Tab's specimen as workspace-views.ts specimenReport does and answers the specimen action
as its agentCommand does (setSpecimen: Main floats, never hides)."""
import asyncio
import copy
import json

import pytest

from orchestrator import workspace_tabs
from orchestrator.brain import context
from orchestrator.commands import COMMANDS
from orchestrator.voice import hands as hands_mod
from orchestrator.voice.hands import intent_of, screen_facts
from orchestrator.workspace_observation import ObservationError, _legacy, front
from tests.test_table_window import TableStudio, asked, built as table_built  # noqa: F401

CORNER = [16, 620, 240, 216]  # a floating specimen's place (workspace px), as the studio reports it
SHOWN = {'fill': {'state': 'fill'}, 'float': {'state': 'float', 'rect': CORNER}, 'hide': {'state': 'hidden'}}


class SpecimenStudio(TableStudio):
    """The studio's Tab strip with every open Tab's specimen, and the specimen action. `shows`: its report shows the
    change (False: the save is acknowledged, but the Stage never shows it)."""

    def __init__(self, app, *, shows=True, **kwargs):
        super().__init__(app, **kwargs)
        self.shows = shows
        self.specimens = {'main': {'state': 'fill'}}

    def items(self):
        rows = super().items()
        rows[0]['view']['legacy']['workspace']['tabs'] = [
            {'id': t['id'], 'title': t['title'], 'front': t['id'] == self.active,
             'specimen': copy.deepcopy(self.specimens.get(t['id'], {'state': 'fill'}))} for t in self.tabs]
        return rows

    async def tabs_reply(self, message):
        action = message['action']
        if action['action'] == 'create':  # a new Tab opens as a canvas for data, with no specimen (Will, 29 Oct)
            self.specimens[f'tab-{len(self.tabs)}'] = copy.deepcopy(SHOWN['hide'])
        if action['action'] != 'specimen':
            return await super().tabs_reply(message)
        if action['tab'] == 'main' and action['specimen'] == 'hide':
            result = {'ok': False, 'error': 'Main always keeps the specimen: it can float, never hide.'}
        else:
            if self.shows:
                self.specimens[action['tab']] = copy.deepcopy(SHOWN[action['specimen']])
            result = {'ok': True, 'session': self.project, 'studioVisible': self.in_view,
                      'tabs': [{**t, 'open': True, 'selected': t['id'] == self.active, 'kind': 'work', 'omittedItems': 0,
                                'items': []} for t in self.tabs]}
        await self.send_report()
        await COMMANDS['workspace-tabs-result'](self.app, self, {'type': 'workspace-tabs-result', 'id': message['id'],
                                                                 'session': message['session'],
                                                                 'workspacePin': message['workspacePin'], 'result': result})


async def built(tmp_path, monkeypatch, **kwargs):
    import tests.test_table_window as table

    monkeypatch.setattr(table, 'TableStudio', SpecimenStudio)
    app, studio = await table_built(tmp_path, monkeypatch, **kwargs)
    studio.tabs.append({'id': 'tab-1', 'title': 'Stoma Reference'})
    studio.specimens['tab-1'] = {'state': 'fill'}
    await studio.send_report()
    return app, studio


def wire(app, studio):
    return app.hub.screen.observation(studio).wire()


# --- the report carries it, and the server reads it --------------------------------------------------------------------

def test_the_tab_strip_reports_each_tabs_specimen_and_every_reader_of_the_screen_gets_it(tmp_path, monkeypatch):
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        studio.specimens['tab-1'] = copy.deepcopy(SHOWN['float'])
        await studio.send_report()
        shown = front(wire(app, studio))
        assert shown['tab'] == 'main' and shown['specimen'] == {'state': 'fill'}, 'the Tab in front holds its specimen'
        facts = screen_facts(wire(app, studio))
        assert [(t['id'], t['specimen']) for t in facts['tabs']] == [('main', {'state': 'fill'}),
                                                                     ('tab-1', {'state': 'float', 'rect': CORNER})]
        pin = app.hub.screen.pin(studio, request_id='voice-context:specimen', timing='server-receive-legacy')
        assert context._voice_windows(pin)[0] == 'Tab "Main" of this Project, the specimen fills it, behind its windows'
        detail = next(line for line in context._voice_workspace(pin) if line.startswith('Workspace front: '))
        assert json.loads(detail.split(': ', 1)[1])['specimen'] == {'state': 'fill'}
        said = context._screen(app.brain, False, request_snapshot=pin)
        assert 'On the Tab in front, the specimen (the live view) fills it, behind its windows.' in said
        studio.active = 'tab-1'
        await studio.send_report()
        pin = app.hub.screen.pin(studio, request_id='voice-context:float', timing='server-receive-legacy')
        assert context._voice_windows(pin)[0] == ('Tab "Stoma Reference" of this Project, the specimen floats in it '
                                                  'as a window (240x216 px at 16,620)')
        studio.specimens['tab-1'] = {'state': 'hidden'}
        await studio.send_report()
        pin = app.hub.screen.pin(studio, request_id='voice-context:hidden', timing='server-receive-legacy')
        assert context._voice_windows(pin)[0] == 'Tab "Stoma Reference" of this Project, the specimen is hidden in it'
    asyncio.run(go())


@pytest.mark.parametrize('specimen', [{'state': 'gone'}, {'state': 'fill', 'rect': [0, 0, 10, 10]},
                                      {'state': 'float', 'rect': [0, 0, -1, 10]}, {'state': 'float', 'where': 'left'}])
def test_a_specimen_outside_its_contract_is_refused_with_the_report(specimen):
    tab = {'id': 'main', 'title': 'Main', 'front': True, 'specimen': specimen}
    with pytest.raises(ObservationError):
        _legacy({'schema': 'studio-surface-v1', 'scope': 'dom-observation',
                 'workspace': {'project': 'p', 'tab': 'main', 'tabs': [tab]}})


def test_an_older_studio_without_the_specimen_is_read_as_not_saying_it():
    out = _legacy({'schema': 'studio-surface-v1', 'scope': 'dom-observation',
                   'workspace': {'project': 'p', 'tab': 'main', 'tabs': [{'id': 'main', 'title': 'Main', 'front': True}]}})
    assert out['workspace']['tabs'] == [{'id': 'main', 'title': 'Main', 'front': True}]
    assert context.specimen_words(None) == ''


# --- the verb, done only on the report ---------------------------------------------------------------------------------

def test_tabs_specimen_hides_it_on_the_named_tab_and_is_done_once_the_report_shows_it(tmp_path, monkeypatch):
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        run = await asked(app, studio, 'tabs', {'action': 'specimen', 'tab': 'tab-1', 'specimen': 'hide'})
        assert run.ok is True, run.summary
        (message,) = studio.sent('workspace-tabs')
        assert message['action'] == {'action': 'specimen', 'tab': 'tab-1', 'specimen': 'hide'}
        assert json.loads(run.summary)['shown'] == {'tab': 'tab-1', 'specimen': {'state': 'hidden'}}
        run = await asked(app, studio, 'tabs', {'action': 'specimen', 'tab': 'tab-1', 'specimen': 'float'})
        assert run.ok is True and json.loads(run.summary)['shown']['specimen'] == {'state': 'float', 'rect': CORNER}
    asyncio.run(go())


def test_a_specimen_change_the_report_never_shows_is_not_done(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_tabs, 'SPECIMEN_WAIT_S', 0.3)

    async def go():
        app, studio = await built(tmp_path, monkeypatch, shows=False)
        run = await asked(app, studio, 'tabs', {'action': 'specimen', 'tab': 'tab-1', 'specimen': 'hide'})
        assert run.ok is False
        assert 'does not show it' in run.summary and 'shows it fill' in run.summary
    asyncio.run(go())


def test_main_keeps_its_specimen_it_floats_but_never_hides(tmp_path, monkeypatch):
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        run = await asked(app, studio, 'tabs', {'action': 'specimen', 'tab': 'main', 'specimen': 'hide'})
        assert run.ok is False and 'never hide' in run.summary
        assert studio.specimens['main'] == {'state': 'fill'}
        run = await asked(app, studio, 'tabs', {'action': 'specimen', 'tab': 'main', 'specimen': 'float'})
        assert run.ok is True, run.summary
    asyncio.run(go())


@pytest.mark.parametrize('args', [{'action': 'specimen', 'tab': 'tab-1'},
                                  {'action': 'specimen', 'tab': 'tab-1', 'specimen': 'gone'},
                                  {'action': 'specimen', 'tab': 'tab-1', 'specimen': {'state': 'hidden'}},
                                  {'action': 'switch', 'tab': 'tab-1', 'specimen': 'hide'},
                                  {'action': 'specimen', 'specimen': 'hide'}])
def test_the_specimen_verb_takes_only_its_own_words(tmp_path, monkeypatch, args):
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        run = await asked(app, studio, 'tabs', args)
        assert run.ok is False
        assert not studio.sent('workspace-tabs'), 'nothing is sent for a call outside the verb'
    asyncio.run(go())


def test_a_new_tab_the_assistant_makes_reports_its_specimen_floating_small(tmp_path, monkeypatch):
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        run = await asked(app, studio, 'tabs', {'action': 'create', 'title': 'Reference'})
        assert run.ok is True, run.summary
        assert front(wire(app, studio))['specimen'] == {'state': 'float', 'rect': CORNER}
    asyncio.run(go())


# --- Hands: the voice's receipt ----------------------------------------------------------------------------------------

def _report(**specimens):
    return {"seq": 3, "items": [{"id": "workspace-tabs", "open": True, "view": {"legacy": {"workspace": {
        "project": "p", "tab": "main", "tabTitle": "Main",
        "tabs": [{"id": t, "title": "Stoma Reference" if t == "t1" else t.title(), "front": t == "main",
                  **({"specimen": s} if s else {})} for t, s in specimens.items()]}}}}]}


def test_hands_verifies_the_specimen_from_the_report_and_says_it():
    intent = intent_of("tabs", {"action": "specimen", "tab": "t1", "specimen": "hide"})
    assert intent.ids == ["t1"] and intent.target == "tab:t1" and intent.caption == "Hiding the specimen"
    assert hands_mod.expected(intent, screen_facts(_report(main={"state": "fill"}, t1={"state": "hidden"}))) is True
    assert hands_mod.expected(intent, screen_facts(_report(main={"state": "fill"}, t1={"state": "fill"}))) is False
    assert hands_mod.expected(intent, screen_facts(_report(main=None, t1=None))) is None, 'a report that does not say'
    facts = screen_facts(_report(main={"state": "fill"}, t1={"state": "hidden"}))
    assert hands_mod.said(intent, {"result": "verified"}, facts, None) == "Done: the specimen is hidden in Stoma Reference."
    floating = intent_of("tabs", {"action": "specimen", "tab": "main", "specimen": "float"})
    assert hands_mod.expected(floating, screen_facts(_report(main={"state": "float", "rect": CORNER}))) is True
    unknown = intent_of("tabs", {"action": "specimen", "tab": "Notes", "specimen": "hide"})
    assert hands_mod.known(unknown, facts) is False, 'a Tab nothing on screen knows is asked about, never guessed'
