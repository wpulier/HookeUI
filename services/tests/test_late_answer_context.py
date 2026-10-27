"""Actual App late-answer context, with synthetic provider and retained files.

The file barrier delegates real Index reads. No ToolRun, status, occurrence,
completion or answering callback is manufactured. Passing proves prompt and
publication plumbing, not that an actual model will ground its prose correctly.
"""
import asyncio
import json

import pytest

from tests.test_model_owned_conversation import (
    ExternalFileBarrier, LocalApp, action, eventually, response,
)


PLAN = 'I will read the retained notes and attempt the requested cleanup.'
ANSWER = 'The retained notes were read. Cleanup failed and its dependent operation was skipped.'
MARKER = '<answer-facts source="app" untrusted="true">\n'


@pytest.mark.parametrize('early_reads', [1, 29, 33], ids=['mixed-plan', 'all-32-statuses', 'explicit-omission'])
def test_late_answer_gets_all_bounded_original_statuses_before_optional_details(tmp_path, monkeypatch, early_reads):
    rig = LocalApp(tmp_path, monkeypatch, correction=ANSWER)
    logged = []
    rig.app.agent.taps.append(logged.append)  # observe the actual interaction, never replace logging or receipts
    records = [rig.app.hub.resources.results.add(
        'compare', {}, f'SYNTHETIC_RETAINED_NOTE_{number}',
        values={'detail': f'OPTIONAL_SAVED_DETAIL_{number}_' * 600},
        request='Retain this synthetic note.', thread=rig.app.day.active_id)
        for number in range(early_reads)]
    first = records[0]
    target = rig.app.hub.resources.results.root / first['day'] / (first['id'] + '.json')
    barrier = ExternalFileBarrier(monkeypatch, target)
    actions = [action('resources', id=record['id']) for record in records]
    actions += [action('present', do='hide', what='live'),
                {**action('present', do='close', what='magnifier'), 'after_steps': [early_reads + 1]},
                action('resources', id=rig.record['id'])]
    rig.provider.initial = response(PLAN, actions=actions)

    async def scenario():
        request = asyncio.create_task(rig.ask('Read these retained notes, attempt cleanup and explain the actual results.'))
        try:
            await eventually(barrier.entered.is_set, 'SETUP: original Index read must enter real retained file IO')
            initial = await asyncio.wait_for(asyncio.shield(request), 4)
            assert len(initial['actions']) == early_reads + 3
            assert initial['actions'][0]['ok'] is None and not barrier.release.is_set()
            original = [(run['requestId'], run['actionId']) for run in initial['actions']]
            assert len(set(original)) == len(actions)
            assert not any(row.get('answer') == ANSWER for row in rig.lines())
            ticket = rig.provider.sends[0].ticket
            assert not ticket.closed
            barrier.release.set()
            await eventually(lambda: len(rig.provider.sends) == 3 and ticket.closed,
                             'SETUP: completed original mixed plan must reach actual late provider and settle')
            admission = rig.assert_budget()
            assert admission.used == 3
            assert [send.phase for send in rig.provider.sends] == ['fast', 'query', 'final']
            assert [attempt.phase for attempt in admission.attempts] == ['fast', 'query', 'final']
            assert [attempt.status for attempt in admission.attempts] == ['done', 'done', 'done']
            query = rig.provider.sends[1]
            late = rig.provider.sends[-1]
            assert query.reply_data == late.reply_data == response(ANSWER)
            final = next(row for row in rig.lines() if row.get('answer') == ANSWER)
            assert final['turnId'] == initial['turnId']
            assert (final['requestId'], final['actionId']) == original[-1]
            assert final['tool'] == 'resources' and final['ok'] is True
            assert 'SYNTHETIC_ORIGINAL_REFERENCE_NOTE' in final['summary']

            # Runner settlement and per-operation chat delivery are separate.
            # The existing query-answer interaction retains the actual completed
            # original plan; a stale pending UI row cannot stand in for it.
            completed = [row for row in logged if row.get('kind') == 'query-answer'
                         and row.get('turnId') == initial['turnId']]
            assert len(completed) == 1 and completed[0]['answer'] == ANSWER
            assert len(completed[0]['actions']) == len(actions)
            settled = {(row['requestId'], row['actionId']): row for row in completed[0]['actions']}
            assert list(settled) == original
            assert [settled[pair]['ok'] for pair in original] == [True] * early_reads + [False, False, True]
            cleanup = settled[original[early_reads]]
            skipped = settled[original[early_reads + 1]]
            assert cleanup['summary'] and cleanup.get('next')
            assert 'skipped' in skipped['summary'] and 'dependency' in skipped['summary']
            assert skipped.get('error') is None
            assert all(record['id'] in settled[original[number]]['summary']
                       for number, record in enumerate(records))

            # Decode only after actual public-path prerequisites and retained
            # receipts above. A chopped optional payload is not a valid JSON
            # evidence block and cannot hide the later operation statuses.
            encoded = late.prompt.split(MARKER, 1)[1].split('\n</answer-facts>', 1)[0]
            facts = json.loads(encoded)
            query_encoded = query.prompt.split(MARKER, 1)[1].split('\n</answer-facts>', 1)[0]
            assert json.loads(query_encoded) == facts
            assert 'operationStatuses' in facts, 'MISSING_FEATURE: compact original outcomes precede optional details'
            statuses = facts['operationStatuses']
            assert len(statuses) == min(32, len(actions))
            assert [(row['requestId'], row['actionId']) for row in statuses] == original[:32]
            assert [(row['tool'], row['ok']) for row in statuses] == [
                (settled[pair]['tool'], settled[pair]['ok']) for pair in original[:32]]
            assert facts['omittedOperations'] == max(0, len(actions) - 32)
            assert encoded.index('"operationStatuses"') < encoded.index('"operations"')
            assert len(encoded) <= 24000 and facts['omittedFacts'] >= 0
            assert all(isinstance(row['summary'], str) and len(row['summary']) <= 240 for row in statuses)
            if early_reads < 32:
                assert statuses[early_reads]['summary'] == cleanup['summary'][:240]
                assert statuses[early_reads + 1]['summary'] == skipped['summary'][:240]
                assert statuses[early_reads]['ok'] is False and statuses[early_reads + 1]['ok'] is False
            if early_reads > 1:
                assert facts['omittedFacts'] > 0, 'BEHAVIOR: large original details are explicitly omitted, never implied complete'
            assert facts['inputs']['images'] == [] and facts['limitations'] == []
        finally:
            barrier.release.set()
            await asyncio.gather(request, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        barrier.release.set()
        rig.close()


def test_late_interpretation_is_an_answer_contract_without_an_action_authoring_menu(tmp_path, monkeypatch):
    rig = LocalApp(tmp_path, monkeypatch, query_empty=True)
    actual_ask = rig.provider.ask
    ignored_actions = [action('select', object='o404')]

    async def final_response(images, prompt, **kwargs):
        result = await actual_ask(images, prompt, **kwargs)
        if rig.provider.sends[-1].phase == 'final':
            # An explicit external final packet must remain answer-only even
            # when it supplies an action. No internal result is substituted.
            result.data = response(ANSWER, actions=ignored_actions)
            rig.provider.sends[-1].reply_data = response(ANSWER, actions=ignored_actions)
        return result

    monkeypatch.setattr(rig.provider, 'ask', final_response)
    logged = []
    rig.app.agent.taps.append(logged.append)
    barrier = ExternalFileBarrier(monkeypatch, rig.file)
    async def scenario():
        task = asyncio.create_task(rig.ask())
        try:
            await eventually(barrier.entered.is_set, 'SETUP: actual saved source read must wait at file IO')
            initial = await asyncio.wait_for(asyncio.shield(task), 4)
            ticket = rig.provider.sends[0].ticket
            barrier.release.set()
            await eventually(lambda: len(rig.provider.sends) == 3 and ticket.closed,
                             'SETUP: original query must reach actual late answer provider')
            admission = rig.assert_budget()
            assert admission.used == 3
            first, query, late = rig.provider.sends
            assert [send.phase for send in rig.provider.sends] == ['fast', 'query', 'final']
            assert [(attempt.phase, attempt.status) for attempt in admission.attempts] == [
                ('fast', 'done'), ('query', 'done'), ('final', 'done')]
            assert query.reply_data == response('')
            assert late.reply_data == response(ANSWER, actions=ignored_actions)
            installed_menu = rig.app.brain.registry.prompt_section()
            assert installed_menu and installed_menu in first.prompt
            assert installed_menu in query.prompt
            assert installed_menu not in late.prompt, 'MISSING_FEATURE: answer-only interpretation must not author another action plan'
            assert 'Do not replay actions.' in late.prompt
            assert 'Reply with JSON only' in late.prompt
            assert 'observation_scope' in late.prompt and 'input_refs' in late.prompt
            assert 'SYNTHETIC_ORIGINAL_REFERENCE_NOTE' in late.prompt
            assert any(row.get('answer') == ANSWER and row.get('turnId') == initial['turnId'] for row in rig.lines())
            completed = [row for row in logged if row.get('kind') == 'query-answer'
                         and row.get('turnId') == initial['turnId']]
            assert len(completed) == 1 and completed[0]['answer'] == ANSWER
            assert len(completed[0]['actions']) == 1
            receipt = completed[0]['actions'][0]
            assert receipt['tool'] == 'resources' and receipt['ok'] is True
            assert (receipt['requestId'], receipt['actionId']) == (
                initial['actions'][0]['requestId'], initial['actions'][0]['actionId'])
            for send in (query, late):
                facts = json.loads(send.prompt.split(MARKER, 1)[1].split('\n</answer-facts>', 1)[0])
                assert len(facts['operations']) == 1 and facts['omittedOperations'] == 0
                operation = facts['operations'][0]
                assert (operation['requestId'], operation['actionId'], operation['summary']) == (
                    receipt['requestId'], receipt['actionId'], receipt['summary'])
        finally:
            barrier.release.set()
            await asyncio.gather(task, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        barrier.release.set()
        rig.close()
