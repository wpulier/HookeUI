"""General presentation recovery and complete researcher comparisons, without live providers."""
import asyncio

import pytest

from orchestrator.brain.registry import Tool, ToolFailed, schema
from orchestrator.research import Session
from tests.test_brain import make
from tests.test_research_content import researcher


@pytest.mark.parametrize('repair', [True, False])
def test_failed_effect_can_be_corrected_by_the_model_without_replaying_success(repair):
    attempted, contexts = [], []

    async def effect(ctx, args):
        contexts.append(ctx)
        attempted.append(args['value'])
        if args['value'] == 'invalid':
            raise ToolFailed('invalid value; supply a numeric opacity')
        return f"applied {args['value']}"

    next_actions = [{'tool': 'style_layer', 'args': {'value': '0.5'}}] if repair else []
    brain, _, agent = make([
        {'answer': 'Applying.', 'actions': [
            {'tool': 'style_layer', 'args': {'value': 'visible'}},
            {'tool': 'style_layer', 'args': {'value': 'invalid'}}]},
        {'answer': 'Correcting the opacity.' if repair else 'The opacity attempt failed.', 'actions': next_actions},
    ], [Tool('style_layer', 'Set a layer style.', schema({}), effect)])
    agent.fast_model.rewrite = {'answer': 'The corrected opacity is applied.' if repair else 'The opacity attempt failed.'}
    result = asyncio.run(brain.turn('Show this layer and set its opacity.'))
    assert len(agent.calls) == 2, 'An effect refusal must reach action-aware model continuation while capacity remains'
    assert attempted == ['visible', 'invalid'] + (['0.5'] if repair else [])
    assert 'invalid value; supply a numeric opacity' in agent.calls[1]['question']
    assert 'applied visible' in agent.calls[1]['question']
    assert all(ctx is contexts[0] for ctx in contexts)
    assert result['calls'] <= 3
    assert [a['ok'] for a in result['actions']] == [True, False] + ([True] if repair else [])
    assert len({a['actionId'] for a in result['actions']}) == len(result['actions'])


def comparison():
    return {'title': 'Independent sequences', 'columns': ['Sequence', 'Average speed (px/s)', 'Tracks', 'Limitation'],
            'rows': [[f'm{i}', f'{10 + i}.25', str(100 + i), 'No calibrated physical scale.'] for i in range(1, 6)]}


def test_researcher_table_survives_finish_and_can_be_read_as_a_complete_part(tmp_path):
    r, _ = researcher(tmp_path, {'effort': 'standard'})
    table = comparison()
    asyncio.run(r.tool_finish({'title': 'Comparison', 'summary': 'Five independent sequences.',
                              'one_liner': 'Five measured sequences.', 'significance': 'low', 'tables': [table]}))
    assert r.result.get('tables') == [table], 'A comparison cannot be cut into the 320-character detail slot'
    s = Session(rid='r9', day='2026-10-09', n=9, brief='Compare sequences', deliverable='', effort='standard',
                engine='fake', purpose='research', label='', object_id=None, origin={}, status='done', result=r.result)
    rendered = s.readable('tables')
    assert table['title'] in rendered and table['columns'][1] in rendered
    for row in table['rows']:
        assert ' | '.join(row) in rendered


@pytest.mark.parametrize('part', ['tables', 'summary', None])
def test_table_read_keeps_missing_evidence_partial_and_scale_qualifiers(tmp_path, part):
    r, _ = researcher(tmp_path, {'effort': 'standard'})
    table = comparison()
    table['rows'] *= 8  # a valid full table exceeds the bounded model excerpt
    asyncio.run(r.tool_finish({'title': 'Comparison', 'tables': [table],
                              'limitations': ['No physical scale; these are tracks, not a count of sperm.']}))
    r.result.update(partial=True, evidence=[{'id': 'm5', 'found': False}])
    s = Session(rid='r9', day='2026-10-09', n=9, brief='Compare sequences', deliverable='', effort='standard',
                engine='fake', purpose='research', label='', object_id=None, origin={}, status='partial', result=r.result)
    text = s.readable(part)
    assert 'No physical scale' in text
    assert 'm5 (not found' in text
    assert 'stopped before writing up' in text
    assert text.index('No physical scale') < text.index('Table:')


@pytest.mark.parametrize('table', [
    {'title': 'Ragged', 'columns': ['A', 'B'], 'rows': [['only one']]},
    {'title': 'Oversized', 'columns': ['A'], 'rows': [['x']] * 51},
    {'title': 'Oversized cell', 'columns': ['A'], 'rows': [['x' * 401]]},
])
def test_invalid_table_is_rejected_without_silently_losing_rows(tmp_path, table):
    r, _ = researcher(tmp_path, {'effort': 'standard'})
    reply, _ = asyncio.run(r.tool_finish({'title': 'Comparison', 'tables': [table]}))
    assert reply.get('error') and r.result is None
