"""Try it prepares a rights-cleared example and binds one proven choice, preserving the user's settings."""
import asyncio

import pytest

from orchestrator import commands, model_checks
from tests.capability_rig import Rig
from tests.waiting import until_async


@pytest.mark.parametrize(('job', 'option', 'capability'), [('embed', 'dinov2-small', 'group-cells'),
                                                        ('cells', 'basic-bright-objects', 'cells')])
def test_local_try_opens_the_existing_form_without_requiring_a_chat_key(tmp_path, monkeypatch, job, option, capability):
    from unittest.mock import AsyncMock
    app = Rig(tmp_path).app
    for key in ('GEMINI_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    chat = AsyncMock(side_effect=AssertionError('Local Try must not invoke chat'))
    monkeypatch.setattr(app.brain, 'turn', chat)
    before = app.hub.settings.to_wire()
    app.hub.checks.record(model_checks.find(job, option), 'works', by='synthetic test')
    prepared = asyncio.run(commands.capability_try(app, None, {'id': job, 'option': option, 'empty': False}))
    assert prepared['action'] == 'configure' and prepared['capability'] == capability
    assert prepared['option'] == option and prepared['asset'] is None
    assert app.hub.settings.to_wire() == before
    chat.assert_not_called()


def test_try_on_an_empty_public_profile_registers_the_verified_example_without_claiming_science(tmp_path):
    app = Rig(tmp_path).app
    app.hub.settings.update({'outline': 'edgetam'})
    app.hub.checks.record(model_checks.find('outline', 'edgetam'), 'works', by='synthetic test')
    async def go():
        return await commands.capability_try(app, None, {'id': 'outline', 'option': 'edgetam', 'empty': True})
    result = asyncio.run(go())
    asset = app.hub.library.get(result['asset']['id'])
    assert asset and asset.path.is_file()
    assert 'Synthetic' in asset.title_now() and 'not scientific qualification' in asset.title_now()
    assert result['job'] == 'outline' and result['option'] == 'edgetam'
    assert app.hub.settings.outline == 'edgetam'
    assert result['action'] == 'click' and result['words']


def test_try_current_view_keeps_the_current_input_and_refuses_an_unproved_choice(tmp_path):
    app = Rig(tmp_path).app
    app.hub.checks.record(model_checks.find('outline', 'edgetam'), 'works', by='synthetic test')
    result = asyncio.run(commands.capability_try(app, None, {'id': 'outline', 'option': 'edgetam', 'empty': False}))
    assert result['asset'] is None
    with pytest.raises(Exception, match='proven|check|ready'):
        asyncio.run(commands.capability_try(app, None, {'id': 'outline', 'option': 'sam2.1-tiny', 'empty': False}))


def test_try_click_runs_exactly_the_proven_alternative_without_changing_defaults(tmp_path):
    app = Rig(tmp_path).app
    app.hub.settings.update({'outline': 'edgetam'})
    app.hub.checks.record(model_checks.find('outline', 'sam2.1-tiny'), 'works', by='synthetic test')
    async def go():
        app._loop = asyncio.get_running_loop()
        prepared = await commands.capability_try(app, None, {'id': 'outline', 'option': 'sam2.1-tiny', 'empty': False})
        msg = {'frameId': app.hub.latest_info.id, 'box': [600, 300, 660, 350], 'capabilityTrial': prepared['token']}
        await commands.click(app, None, msg)
        with pytest.raises(Exception, match='expired|consumed|offer'):
            await commands.click(app, None, msg)
    asyncio.run(go())
    calls = [p for op, p in app.hub.gpu.calls if op == 'segment']
    assert calls and calls[0]['model'] == 'sam2.1-tiny'
    assert app.hub.settings.outline == 'edgetam'


def test_prepared_try_cannot_run_in_a_replacement_session(tmp_path):
    app = Rig(tmp_path).app
    app.hub.checks.record(model_checks.find('outline', 'edgetam'), 'works', by='synthetic test')
    async def go():
        prepared = await commands.capability_try(app, None, {'id': 'outline', 'option': 'edgetam', 'empty': False})
        app.day.new('Another specimen')
        with pytest.raises(Exception, match='conversation|session|offer'):
            await commands.click(app, None, {'frameId': app.hub.latest_info.id, 'x': 25, 'y': 25,
                                             'capabilityTrial': prepared['token']})
    asyncio.run(go())
    assert not app.hub.gpu.calls


def test_try_ask_carries_its_exact_choice_through_the_normal_brain_turn(tmp_path):
    from tests.capability_rig import PLAN

    rig = Rig(tmp_path)
    app = rig.app
    app.hub.settings.update({'cells': 'cellpose-sam'})
    app.hub.checks.record(model_checks.find('cells', 'edgetam-automask'), 'works', by='synthetic test')
    async def go():
        app._loop = asyncio.get_running_loop()
        prepared = await commands.capability_try(app, None, {'id': 'cells', 'option': 'edgetam-automask', 'empty': False})
        token = PLAN.set([{'tool': 'find', 'args': {'what': 'cell', 'by': 'cells'}}])
        try:
            result = await commands.ask(app, None, {'text': prepared['words'], 'frameId': app.hub.latest_info.id,
                                                    'capabilityTrial': prepared['token']})
            assert result['ok']
            await rig.settle([r for r, _ in rig.runs])
        finally:
            PLAN.reset(token)
    asyncio.run(go())
    calls = [(op, p) for worker in rig.workers() for op, p in worker.calls if op in ('cells', 'masks', 'automask')]
    assert calls and any(p.get('model') == 'edgetam-automask' or op in ('masks', 'automask') for op, p in calls), calls
    assert app.hub.settings.cells == 'cellpose-sam'


def test_try_medical_ranking_is_one_click_not_a_changed_default(tmp_path, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'synthetic-test-key')
    rig = Rig(tmp_path)
    app = rig.app
    assert not app.hub.settings.update({'zeroshot': 'off', 'vision': 'gemini-3.8-flash'})  # a Gemini model, on the Google key
    app.hub.checks.record(model_checks.find('zeroshot', 'medsiglip-always'), 'works', by='synthetic test')
    async def go():
        app._loop = asyncio.get_running_loop()
        prepared = await commands.capability_try(app, None, {'id': 'zeroshot', 'option': 'medsiglip-always', 'empty': False})
        await commands.click(app, None, {'frameId': app.hub.latest_info.id, 'box': [600, 300, 660, 350],
                                         'capabilityTrial': prepared['token']})
        # The ordinary click's identification is a tracked late task, not an invented immediate success.
        await until_async(lambda: any(op == 'zeroshot' for w in rig.workers() for op, _ in w.calls))
        assert any(op == 'zeroshot' for w in rig.workers() for op, _ in w.calls)
        await asyncio.sleep(.02)
    asyncio.run(go())
    assert app.hub.settings.zeroshot == 'off'
