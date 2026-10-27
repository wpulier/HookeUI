import asyncio
from types import SimpleNamespace

import pytest

from orchestrator import capability_shelf as shelf, catalog_view, commands
from orchestrator.brain.registry import ToolFailed
from tests.test_capability_shelf import App, DOWNLOAD, READY, models, role


def test_stop_reference_cannot_cancel_a_replacement_attempt(models):
    app = App([{'role': role(('a', DOWNLOAD, 'mac'))}])
    turn = shelf.TurnOn(app)
    old = turn.start('cells')['attempt']
    assert turn.cancel('cells', attempt=old)
    new = turn.start('cells')['attempt']
    assert old != new
    assert not turn.cancel('cells', attempt=old)
    assert turn.wire()['cells']['attempt'] == new
    assert turn.cancel('cells', attempt=new)


@pytest.mark.parametrize('change', ['none', 'session', 'choice', 'recipe', 'proof'])
def test_explicit_selection_revalidates_and_is_idempotent(models, monkeypatch, change):
    selected = {'cells': 'e'}
    r = role(('a', READY, 'mac'), current='e')
    app = SimpleNamespace(day=SimpleNamespace(sid='session'),
                          hub=SimpleNamespace(settings=SimpleNamespace(to_wire=lambda: dict(selected))),
                          capabilities_wire=lambda **kw: {'roles': [r], 'models': [{'setups': [
                              {'role': 'cells', 'option': 'a', **catalog_view._setup_plan(r, shelf._options(r)[0])}]}]})
    writes = []
    def update(app, values, by):
        writes.append(dict(values)); selected.update(values); return []
    monkeypatch.setattr(commands, 'change_settings', update)
    candidate = shelf._options(r)[0]
    request = {'session': 'session', 'job': 'cells', 'option': 'a', 'current': 'e', 'recipe': shelf.recipe(candidate)}
    if change == 'session': app.day.sid = 'another'
    if change == 'choice': selected['cells'] = 'b'
    if change == 'recipe': request['recipe'] = 'stale'
    if change == 'proof': r['options'][0]['check'] = None
    async def run():
        if change == 'none':
            assert (await commands.capability_select(app, None, request))['ok']
            assert (await commands.capability_select(app, None, request))['ok']
            assert writes == [{'cells': 'a'}]
        else:
            with pytest.raises(ToolFailed): await commands.capability_select(app, None, request)
            assert not writes
    asyncio.run(run())


@pytest.mark.parametrize('change', ['proof', 'companion', 'candidate', 'session', 'choice'])
def test_selection_rechecks_after_recipe_work(models, monkeypatch, change):
    r = role(('a', READY, 'mac'), current='e')
    selected = {'cells': 'e'}
    def wire(**kw):
        return {'roles': [r], 'models': [{'setups': [
            {'role': 'cells', 'option': 'a', **catalog_view._setup_plan(r, shelf._options(r)[0])}]}]}
    app = SimpleNamespace(day=SimpleNamespace(sid='session'), capabilities_wire=wire,
                          hub=SimpleNamespace(settings=SimpleNamespace(to_wire=lambda: dict(selected))))
    request = {'session': 'session', 'job': 'cells', 'option': 'a', 'current': 'e',
               'recipe': shelf.recipe(shelf._options(r)[0])}
    async def calculate(func, *args):
        result = func(*args)
        if change == 'proof': r['options'][0]['check'] = None
        if change == 'companion': r['options'][0]['readiness'] = DOWNLOAD
        if change == 'candidate': r['options'][0]['runs'] = 'cloud'
        if change == 'session': app.day.sid = 'another'
        if change == 'choice': selected['cells'] = 'b'
        return result
    monkeypatch.setattr(asyncio, 'to_thread', calculate)
    writes = []
    monkeypatch.setattr(commands, 'change_settings', lambda *args, **kw: writes.append(args))
    async def run():
        with pytest.raises(ToolFailed):
            await commands.capability_select(app, None, request)
    asyncio.run(run())
    assert not writes
