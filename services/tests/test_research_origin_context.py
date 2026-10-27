"""Original researcher facts at the real Manager -> Moment -> model-input seams.

Retained files and camera pixels are synthetic local fixtures. The provider is
scripted only to record actual FAST/Agent ASK inputs; no returned action runs and
no model quality, browser paint or actual task-selection success is asserted.
"""
import asyncio
import json

import pytest

from orchestrator.brain.toolbox import build
from orchestrator.research import ResearchManager
from tests.fakes import desk
from tests.test_brain_dispatch_budget import admitted, response, world


QUESTION = 'Start a researcher to acquire five new four-second sequences and compare their measured speeds.'


@pytest.mark.parametrize('planner', ['fast', 'camera'])
def test_original_researcher_and_active_conversation_are_distinct_in_actual_planner_input(
        tmp_path, monkeypatch, planner):
    async def script(entry):
        if entry.fast:
            return {'intent': 'request', 'needs_look': False, 'answer': '', 'actions': []}
        return response('')

    brain, hub, providers, _ = world(tmp_path, monkeypatch, script, fast=planner == 'fast')
    store = brain.conversation.store
    original_thread = store.active_id
    retained = tmp_path / 'research' / store.day / 'r-retained'
    retained.mkdir(parents=True)
    (retained / 'session.json').write_text(json.dumps({
        'rid': 'r-retained', 'n': 1, 'purpose': 'research', 'engine': 'gpt-6.1-sol',
        'effort': 'quick', 'deliverable': 'research', 'brief': QUESTION,
        'status': 'done', 'created': 1000, 'finished': 1030,
        'origin': {'thread': original_thread, 'turn_id': 1, 'request_id': 'earlier-request'},
        'result': {'title': 'Earlier incomplete comparison',
                   'summary': 'Only one earlier sequence was measured.',
                   'one_liner': 'The earlier five-sequence comparison was incomplete.'},
    }))
    manager = ResearchManager(hub, root=tmp_path / 'research', today=lambda: store.day)
    original = manager.get('r-retained')
    assert original is not None and original.restored and original._attempt is None
    assert original.origin['thread'] == original_thread and original.day == store.day
    assert original.result['title'] == 'Earlier incomplete comparison'
    active = store.open_thread('Fresh independent acquisition')
    assert active.id != original_thread and not active.turns
    brain.services.research = manager
    brain.services.desk = desk(manager)
    brain.registry = build(brain.services)

    async def go():
        from orchestrator.ticket import issue, using
        if planner == 'fast':
            request = issue(hub, 'turn', asked=brain.conversation.here(), channel='chat')
            try:
                with using(request):
                    decision = await brain.quick(QUESTION, moment=brain.moment())
                assert decision is not None
            finally:
                request.seal()
        else:
            # The real Agent snapshot/encoding/provider path, with actual Moment
            # notes and Registry declarations; no planning/pointing substitute.
            from orchestrator.brain import context
            request = issue(hub, 'turn', asked=brain.conversation.here(), channel='chat')
            try:
                with using(request):
                    view = brain.agent.snapshot()
                    moment = brain.moment()
                    table, selected = context.scene_table(moment)
                    await brain._look(QUESTION, view, option=hub.settings.vision, pointing='box',
                                      notes=(brain.registry.prompt_section(), table, selected))
            finally:
                request.seal()

    asyncio.run(go())
    assert len(providers.sent) == admitted(providers).used == 1
    sent = providers.sent[0]
    assert bool(sent.images) == (planner == 'camera')
    assert QUESTION in sent.prompt and 'start_researcher' in sent.prompt
    assert manager.get(original.rid) is original and len(manager.sessions) == 1
    # Scope facts must travel with this exact retained row, rather than being
    # recoverable only from unrelated day-summary text or a global latest job.
    row = next(line for line in sent.prompt.splitlines() if line.startswith('Researcher 1 ('))
    assert original_thread in row and store.day in row, row
    assert 'restored' in row.lower() and 'unavailable' in row.lower(), row
    assert 'incomplete' in row.lower() and 'done' in row.lower(), row
    active_row = next((line for line in sent.prompt.splitlines()
                       if line.lower().startswith('current conversation:')), '')
    assert active.id in active_row and store.day in active_row, active_row
