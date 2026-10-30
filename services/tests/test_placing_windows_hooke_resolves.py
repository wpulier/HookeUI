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
