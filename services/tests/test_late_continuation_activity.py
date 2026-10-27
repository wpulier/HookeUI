"""Actual late planning operations must reach the renderer as retained activity.

External provider/renderer/file-read seam is scripted; actual App, Brain, tool
execution and Dialogue messages are reused from the continuation integration rig.
No fabricated ToolRun or application-authored assistant answer.
"""
import asyncio

import pytest

from tests.test_late_result_action_continuation import (
    DONE, REFUSED, eventually, setup_case, start_held,
)


@pytest.mark.parametrize('refused', [False, True])
def test_new_late_operation_is_retained_before_its_model_answer(tmp_path, monkeypatch, refused):
    rig, studio, _, _, barrier, _ = setup_case(tmp_path, monkeypatch, refused=refused)

    async def scenario():
        request, initial, _, ticket = await start_held(rig, studio, barrier)
        original = {(row['requestId'], row['actionId']) for row in initial['actions']}
        try:
            barrier.release.set()
            await eventually(lambda: ticket.closed, 'late request settles')
            events = [row for row in rig.lines() if row.get('type') == 'chat-action'
                      and row.get('turnId') == initial['turnId']]
            answer = next(row for row in events if row.get('answer') == (REFUSED if refused else DONE))
            identity = (answer['requestId'], answer['actionId'])
            assert identity not in original
            activity = [row for row in events[:events.index(answer)]
                        if (row.get('requestId'), row.get('actionId')) == identity and not row.get('answer')]
            assert activity and activity[-1]['ok'] is (not refused)
            assert activity[-1].get('standalone') is True, (
                'a new late operation must be retained, not a transient hint: '
                'the renderer accepts its model answer only for a retained occurrence')
            assert answer['answer'] == rig.provider.sends[-1].reply_data['answer']
        finally:
            barrier.release.set()
            await asyncio.gather(request, return_exceptions=True)

    try:
        asyncio.run(scenario())
    finally:
        barrier.release.set()
        rig.close()
