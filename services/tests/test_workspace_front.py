"""What is in front, as the studio's report shows it (workspace_observation.front; docs/architecture/on-screen.md): the
Tab of the Project, the window highest on it and whether the studio is in view. The brain's `tabs list` and the voice's
window lines say it, observed and never guessed (voice log 2026-10-28: "I can see the measurements table window is open
in your project. But if you're not seeing it, we can bring it to the front")."""
import asyncio
import json

from orchestrator.brain import context
from orchestrator.workspace_observation import front
from tests.test_table_window import TableStudio, asked, built  # noqa: F401 - the same synthetic studio


def _window(studio, placement, tab, z, rect=(40, 80, 420, 380)):
    studio.windows[placement] = {'tab': tab, 'title': f'Table {placement}', 'z': z, 'generation': 1, 'state': 'rendered',
                                 'resource': {'day': studio.project, 'id': f'd{len(studio.windows) + 1}', 'version': 0,
                                              'picture': 0}, 'rect': list(rect)}


def test_front_is_the_highest_window_on_the_tab_in_front_with_the_tab_s_title(tmp_path, monkeypatch):
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        studio.tabs.append({'id': 'tab-1', 'title': 'Measurements'})
        _window(studio, 'p1', 'main', 11)
        _window(studio, 'p2', 'main', 14)
        _window(studio, 'p3', 'tab-1', 40)  # on another Tab: laid out there, never in front here
        await studio.send_report()
        shown = front(app.hub.screen.observation(studio).wire())
        assert shown['project'] == studio.project and shown['tab'] == 'main' and shown['tabTitle'] == 'Main'
        assert shown['window'] == 'document:p2' and 'Table p2' in shown['title']
        assert shown['studioVisible'] is True
        studio.active = 'tab-1'
        studio.in_view = False
        await studio.send_report()
        shown = front(app.hub.screen.observation(studio).wire())
        assert (shown['tab'], shown['tabTitle'], shown['window']) == ('tab-1', 'Measurements', 'document:p3')
        assert shown['studioVisible'] is False  # observed: the studio was not in view
    asyncio.run(go())


def test_a_report_without_the_tab_strip_says_no_tab_rather_than_guessing_one():
    shown = front({'items': [], 'viewport': {'visible': True, 'documentFocused': False}})
    assert shown == {'project': None, 'tab': None, 'tabTitle': None, 'window': None, 'title': None, 'specimen': None,
                     'studioVisible': True, 'studioFocused': False}


def test_the_voice_hears_the_tab_and_which_window_is_in_front_in_a_few_words(tmp_path, monkeypatch):
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        _window(studio, 'p1', 'main', 11)
        _window(studio, 'p2', 'main', 14)
        await studio.send_report()
        pin = app.hub.screen.pin(studio, request_id='voice-context:front', timing='server-receive-legacy')
        lines = context._voice_windows(pin)
        assert lines[0] == 'Tab "Main" of this Project'
        assert [line for line in lines if line.endswith(', in front')] == [next(l for l in lines if l.startswith('document:p2'))]
        detail = [line for line in context._voice_workspace(pin) if line.startswith('Workspace front: ')]
        assert json.loads(detail[0].split(': ', 1)[1]) == {'tab': 'main', 'window': 'document:p2', 'studioVisible': True,
                                                           'studioFocused': True}
        # Tiny: the Tab line and the marker are all the voice gains.
        added = len(lines[0]) + len(', in front')
        assert added <= 40
        studio.in_view = False
        await studio.send_report()
        pin = app.hub.screen.pin(studio, request_id='voice-context:away', timing='server-receive-legacy')
        assert context._voice_windows(pin)[0] == 'Tab "Main" of this Project, the studio not in view'
    asyncio.run(go())


def test_tabs_list_says_what_is_in_front_as_the_report_shows_it(tmp_path, monkeypatch):
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        _window(studio, 'p1', 'main', 12)
        run = await asked(app, studio, 'tabs', {'action': 'list'})
        assert run.ok is True, run.summary
        listed = json.loads(run.summary)
        assert listed['front']['tab'] == 'main' and listed['front']['tabTitle'] == 'Main'
        assert listed['front']['window'] == 'document:p1' and listed['front']['studioVisible'] is True
    asyncio.run(go())
