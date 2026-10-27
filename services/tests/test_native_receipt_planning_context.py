"""Actual native receipts in the next App planning call; synthetic external IO.

App/COMMANDS/Brain/Registry, request admission, PreparedSources, native Core,
completion comparison and journals are real. The existing AppStudio replaces
only renderer/transport observations; LocalApp scripts the external provider
and camera JPEG. No ToolRun, receipt, ACK, source or completion is constructed.
This proves context plumbing, not browser pixels or actual model planning.
"""
import asyncio
import copy
import json

from orchestrator.workspace_observation import ClientRef
from tests.test_late_answer_context import MARKER
from tests.test_model_owned_conversation import (
    ExternalFileBarrier, LocalApp, RETAINED, action, eventually, response,
)
from tests.test_workspace_actions_app import AppStudio, CLIENT, WORKSPACE, address, saved_picture


PLAN = 'I will arrange the two retained pictures and read the requested records.'
ANSWER = 'The retained records and the actual operation receipts are available.'


def native_transport(rig):
    """Attach the existing external renderer to the actual local App owner."""
    studio = AppStudio(rig.app)
    rig.app.accept_workspace(studio, ClientRef(
        CLIENT['workspaceId'], CLIENT['clientId'], CLIENT['pageEpoch'], CLIENT['connectionEpoch']))
    original = rig.app.hub.broadcast

    def broadcast(message):
        original(message)
        studio.hear(message)

    rig.app.hub.broadcast = broadcast
    rig.app.hub.screen.broadcast = broadcast
    rig.client = studio  # COMMANDS.ask pins this actually accepted original socket
    return studio


def node_count(value):
    """Measure the actual receipt tree; no truncation or projection algorithm."""
    if isinstance(value, dict):
        return 1 + sum(node_count(child) for child in value.values())
    if isinstance(value, list):
        return 1 + sum(node_count(child) for child in value)
    return 1


def facts_from(send):
    encoded = send.prompt.split(MARKER, 1)[1].split('\n</answer-facts>', 1)[0]
    assert len(encoded) <= 24000, 'BEHAVIOR: planning context must retain the existing character bound'
    return json.loads(encoded)


def assert_original_journals(rig, original, captured, turn_id, initial_actions):
    """Check actual on-disk receipts independently from prompt projection."""
    interaction = [json.loads(line) for line in rig.app.agent.interaction_log.read_text().splitlines()]
    completed = [row for row in interaction if row.get('kind') == 'query-answer'
                 and row.get('turnId') == turn_id]
    assert len(completed) == 1
    full = completed[0]['actions']
    assert [(row['requestId'], row['actionId']) for row in full] == original
    assert full == captured, 'BEHAVIOR: compacting a prompt must not change the original full interaction receipts'
    journal = rig.app.day.journal.load()
    rows = [event['line'] for event in journal if event.get('type') == 'line'
            and event['line'].get('turnId') == turn_id]
    activity = [action for row in rows for action in row.get('actions', [])]
    activity += [row for row in rows if row.get('type') == 'chat-action' and row.get('standalone') is True]
    first = {}
    for row in activity:
        identity = (row['requestId'], row['actionId'])
        assert identity in original, 'BEHAVIOR: late updates cannot invent another operation identity'
        first.setdefault(identity, row)
    # Completion and the final model answer update the original occurrence;
    # their journal revisions are not additional operations or initial receipts.
    assert list(first) == original
    initial = list(first.values())
    assert any(row.get('type') == 'chat' and row.get('role') == 'assistant'
                   and row.get('text') == PLAN for row in rows)
    for saved, returned, receipt in zip(initial, initial_actions, full):
        for key, value in returned.items():
            assert saved[key] == value, 'BEHAVIOR: initial activity journal preserves the actual returned receipt'
        if receipt['tool'] == 'present':
            assert saved['outcome'] == receipt['outcome']
            assert 'omittedTrace' not in saved['outcome']
            assert saved['outcome']['observed'] and saved['outcome']['receipts']
    return full


def test_verbose_native_receipts_keep_later_original_outputs_args_and_failures_in_planning(tmp_path, monkeypatch):
    rig = LocalApp(tmp_path, monkeypatch, correction=ANSWER)
    studio = native_transport(rig)
    first, first_pixels = saved_picture(rig.app, 40)
    second, second_pixels = saved_picture(rig.app, 190)
    barrier = ExternalFileBarrier(monkeypatch, rig.file)
    finished = []
    rig.app.brain.registry.finished.append(
        lambda run, ctx: finished.append((run, ctx, copy.deepcopy(run.wire(full=True)))))
    a, b = {'step': 1, 'output': 0}, {'step': 4, 'output': 0}
    actions = [
        action('present', do='open', source=address(first)),
        action('present', do='resize', what=a, size={'width': 300, 'height': 240}),
        action('present', do='move', what=a, position={'x': 10, 'y': 10}),
        action('present', do='open', source=address(second)),
        action('present', do='resize', what=b, size={'width': 300, 'height': 240}),
        action('present', do='move', what=b, position={'x': 340, 'y': 10}),
        action('present', do='resize', what=a, size={'width': 310, 'height': 250}),
        action('present', do='move', what=a, position={'x': 20, 'y': 20}),
        action('present', do='resize', what=b, size={'width': 310, 'height': 250}),
        action('present', do='move', what=b, position={'x': 350, 'y': 20}),
        action('resources', order='sideways'),  # real declared-order refusal, not a fabricated failure
        action('resources', id=first.id),
        action('resources', id=second.id),
        action('resources', id=rig.record['id']),
    ]
    rig.provider.initial = response(PLAN, actions=actions)

    async def scenario():
        await studio.send_report()
        assert rig.app.hub.screen.observation(studio).wire()['client'] == CLIENT
        original_asked = rig.app.day.here()
        request = asyncio.create_task(rig.ask('Arrange these retained pictures and explain the actual records and any failures.'))
        try:
            await eventually(barrier.entered.is_set, 'SETUP: last original read must reach actual retained file IO')
            initial = await asyncio.wait_for(asyncio.shield(request), 4)
            assert initial['ok'] is True and initial['answer'] == 'I will arrange the two retained pictures and read the requested records.'
            assert len(initial['actions']) == len(actions) == 14
            assert initial['actions'][-1]['ok'] is None and not barrier.release.is_set()
            original = [(row['requestId'], row['actionId']) for row in initial['actions']]
            assert len(set(original)) == 14
            assert [row['ok'] for row in initial['actions'][:13]] == [True] * 10 + [False, True, True]
            ticket = rig.provider.sends[0].ticket
            assert ticket is not None and not ticket.closed
            barrier.release.set()
            await eventually(lambda: len(rig.provider.sends) == 3 and ticket.closed,
                             'SETUP: actual original plan must reach query/final interpretation and settle')
            admission = rig.assert_budget()
            assert admission.used == 3
            assert [send.phase for send in rig.provider.sends] == ['fast', 'query', 'final']
            assert [(attempt.phase, attempt.status) for attempt in admission.attempts] == [
                ('fast', 'done'), ('query', 'done'), ('final', 'done')]
            owned = [(run, ctx, wire) for run, ctx, wire in finished
                     if run.occurrence.request_id == admission.request_id]
            assert len(owned) == 14
            assert all(ctx.asked == original_asked and ctx.turn_id == initial['turnId'] for _, ctx, _ in owned)
            captured = [wire for _, _, wire in owned]
            assert [(row['requestId'], row['actionId']) for row in captured] == original
            assert [row['args'] for row in captured] == [row['args'] for row in actions]
            assert [row['ok'] for row in captured] == [True] * 10 + [False, True, True, True]
            assert all(row['outcome']['status'] == 'confirmed' for row in captured[:10])
            assert sum(node_count(row['outcome']) for row in captured[:10]) > 2048, (
                'SETUP: actual native completion traces must exceed the existing optional node allowance')
            assert captured[10]['summary'] and captured[10]['next']
            assert RETAINED in captured[-1]['summary']
            assert rig.app.hub.resources.picture(first.id, 0, first.day)[0] == first_pixels
            assert rig.app.hub.resources.picture(second.id, 0, second.day)[0] == second_pixels
            full = assert_original_journals(rig, original, captured, initial['turnId'], initial['actions'])
            assert all(run.wire(full=True) == wire for run, _, wire in owned)
            windows = [full[index]['outputs'][0] for index in (0, 3)]
            assert windows[0]['instanceId'] != windows[1]['instanceId']
            for output, entry in zip(windows, (first, second)):
                assert output['workspaceId'] == WORKSPACE
                assert output['source']['identity']['resource']['id'] == entry.id
                assert studio.items[output['instanceId']]['sourceRef'] == output['source']

            # The assertions below observe the real next planning send. Full
            # receipts above must remain intact even if only their trace is omitted here.
            query, final = rig.provider.sends[1:]
            facts = facts_from(query)
            assert facts_from(final) == facts
            assert [(row['requestId'], row['actionId']) for row in facts['operationStatuses']] == original
            assert facts['omittedOperations'] == 0 and facts['inputs']['images'] == []
            operations = facts['operations']
            assert len(operations) == 14 and all(not row.get('omitted') for row in operations), (
                'MISSING_FEATURE: early native traces must not erase later original planning operations', operations)
            assert [(row['requestId'], row['actionId']) for row in operations] == original
            assert [row['args'] for row in operations] == [row['args'] for row in actions], (
                'MISSING_FEATURE: next planning needs the actual original attempted arguments')
            for index in (0, 3):
                assert operations[index]['outputs'] == full[index]['outputs']
            for projected, receipt in zip(operations[:10], full[:10]):
                for key in ('status', 'error', 'unmatched'):
                    if key in receipt['outcome']:
                        assert projected['outcome'][key] == receipt['outcome'][key]
            assert operations[10]['ok'] is False and operations[10]['summary'] == full[10]['summary']
            assert operations[10]['next'] == full[10]['next']
            assert operations[-1]['ok'] is True and operations[-1]['summary'] == full[-1]['summary']
            assert any(row['outcome'].get('omittedTrace', 0) > 0 for row in operations[:10]), (
                'MISSING_FEATURE: omitted native trace must be explicitly counted, never implied complete')
            assert facts['omittedFacts'] > 0
        finally:
            barrier.release.set()
            await asyncio.gather(request, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        barrier.release.set()
        rig.close()


def test_small_actual_receipt_keeps_existing_wire_facts_without_omission(tmp_path, monkeypatch):
    rig = LocalApp(tmp_path, monkeypatch)
    finished = []
    rig.app.brain.registry.finished.append(lambda run, ctx: finished.append(run))

    async def scenario():
        result = await rig.ask()
        assert result['ok'] is True
        admission = rig.assert_budget()
        owned = [run for run in finished if run.occurrence.request_id == admission.request_id]
        assert len(owned) == 1 and owned[0].ok is True
        receipt = copy.deepcopy(owned[0].wire())
        query = next(send for send in rig.provider.sends if send.phase == 'query')
        assert RETAINED in receipt['summary'] and RETAINED in query.prompt
        facts = facts_from(query)
        assert facts['omittedFacts'] == facts['omittedOperations'] == 0
        assert len(facts['operations']) == 1
        for key, value in receipt.items():
            assert facts['operations'][0][key] == value
        assert owned[0].wire() == receipt
        assert not any(row.get('outcome', {}).get('omittedTrace') for row in facts['operations'])
    try:
        asyncio.run(scenario())
    finally:
        rig.close()
