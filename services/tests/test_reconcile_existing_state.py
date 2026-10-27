"""Reconciliation facts distinguish existing board state from newly executed work."""
import asyncio

import pytest

from orchestrator.brain.registry import ToolRun
from tests.test_brain import FakeVision, obj
from tests.test_sight import make_brain


@pytest.mark.parametrize('outcome', ['no-new-work', 'failed', 'pending'])
def test_reconciliation_keeps_existing_board_without_claiming_browser_confirmation(outcome):
    brain, hub, agent = make_brain({}, {})
    left = obj(hub, 'left eye', x=100)
    right = obj(hub, 'right eye', x=300)
    agent.fast_model = FakeVision(rewrite={'answer': 'Existing app state retained.',
                                         'observation_scope': 'none', 'input_refs': []})
    runs = [] if outcome == 'no-new-work' else [ToolRun('highlight', {'things': [left.id]},
        ok=False if outcome == 'failed' else None,
        summary='the requesting browser did not confirm the action' if outcome == 'failed' else '')]
    from orchestrator.ticket import issue, using
    request = issue(hub, 'turn')
    try:
        with using(request):
            answer = asyncio.run(brain._reconcile('only his eyes', 'The eyes are already outlined.',
                                                  runs, 0, 0, [], True, force=True))
    finally:
        request.close()
    assert answer == 'Existing app state retained.'
    prompt = agent.fast_model.prompts[0]
    assert left.id in prompt and right.id in prompt
    assert 'new targets in this response, not the total existing outlines' in prompt
    assert 'absence of new work does not mean existing requested objects are absent' in prompt
    assert 'does not by itself confirm what the requesting browser rendered' in prompt
    assert 'You receive no image here' in prompt
    if outcome == 'no-new-work':
        assert '- no new action or outline outcome was recorded in this response' in prompt
        assert '\n- nothing\n' not in prompt
    elif outcome == 'failed':
        assert 'What did not work:\n- p1: highlight: the requesting browser did not confirm the action' in prompt
    else:
        assert 'highlight: still in progress, not done yet' in prompt


def test_new_fast_default_preserves_explicit_user_choices():
    from orchestrator.options import Settings

    settings = Settings()
    assert settings.fast == 'gemini-3.8-flash'
    for value in ('gemini-3.5-flash', 'off'):
        assert settings.update({'fast': value}) == []
        assert settings.fast == value
