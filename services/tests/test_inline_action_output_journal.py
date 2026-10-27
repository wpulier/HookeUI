"""TEST first: actual App action facts survive an absent final model answer.

The existing ArgumentRig supplies synthetic camera JPEGs, external provider replies
and worker masks. App/COMMANDS/Brain/Registry/Runner/Ticket/Conversation/DayStore and
Scene remain real. A provider-only final barrier holds interpretation after the
actual highlight succeeds. Carry uses the actual Hub writer over a later actual
intake frame and explicitly synthetic measured-match output, not CV quality proof.
No fabricated run, output, Asked, Ticket, journal receipt or assistant text is used.
Root alone executes these authored tests; setup failures must be retained separately.
"""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from orchestrator.brain.state import Conversation
from orchestrator.capture import RUN
from orchestrator.day import DayStore
from tests.test_model_target_argument_semantics import ArgumentRig, action, reply
from tests.waiting import HANG_S


MODEL_REPLY = 'The selected synthetic structure has an outline; this does not identify its biology.'


class FinalInterpretation:
    """Script only external final IO; delegate the existing full provider observer."""
    def __init__(self, rig, monkeypatch, *, answer='', held=True):
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.entries = []
        self.answer, self.held = answer, held
        provider = rig.app.agent.vision('gemini-3.5-flash')
        original = provider.ask

        async def ask(images, prompt, **kwargs):
            result = await original(images, prompt, **kwargs)
            entry = rig.entries[-1]
            attempt = entry.attempt
            if entry.answer_scope and attempt is not None and attempt.phase in ('query', 'final'):
                if not self.entries:
                    self.entries.append(entry)
                self.entered.set()
                if self.held:
                    await self.release.wait()
                data = reply()
                data['answer'] = self.answer
                return SimpleNamespace(data=copy.deepcopy(data), latency_ms=result.latency_ms,
                                       model=result.model, error=result.error)
            return result

        monkeypatch.setattr(rig.app.agent, 'vision', lambda _model: SimpleNamespace(ask=ask))


def output_events(events, oid):
    return [event for event in events if event.get('type') == 'turn'
            and any(row.get('id') == oid for row in (event.get('refs') or {}).get('outputRefs', []))]


def assert_original_receipt(events, oid, expected, asked):
    rows = output_events(events, oid)
    assert len(rows) == 1, 'BEHAVIOR: one factual original completion is durable independently of final prose'
    event = rows[0]
    assert event['role'] == 'note', 'BEHAVIOR: application result facts are context, never assistant prose'
    assert event['channel'] == 'chat' and event['thread'] == asked.thread
    assert event['asked'] == {'thread': asked.thread, 'generation': asked.generation, 'day': asked.day, 'session': asked.session}
    assert event['refs']['outputRefs'] == [expected]
    assert event['refs']['objectIds'] == [oid], 'BEHAVIOR: existing exact output-ID utility survives'
    return event


@pytest.mark.parametrize('boundary', ['none', 'thread', 'carry', 'new-session'])
def test_actual_app_journals_settled_original_output_before_empty_final(tmp_path, monkeypatch, boundary):
    """Catches output facts gated by final prose, deferred writing or borrowed current origin."""
    async def scenario():
        rig = ArgumentRig(tmp_path, monkeypatch)
        barrier = FinalInterpretation(rig, monkeypatch)
        task = None
        try:
            await rig.prepare()
            original_view = await rig.publish_sight()
            original_thread, original_day = rig.app.day.active_id, rig.app.day.day
            original_clock = rig.app.day.clock()
            task = asyncio.create_task(rig.ask('Highlight the second structure from this original look.',
                                              action('highlight', things=['s2'])))
            await asyncio.wait_for(barrier.entered.wait(), timeout=HANG_S)
            starts = [start for start in rig.started if start.run.tool == 'highlight']
            assert len(starts) == 1, 'SETUP: actual registered highlight must execute once'
            start, run = starts[0], starts[0].run
            assert not task.done() and run.ok is True and len(run.outputs) == 1
            assert len(rig.masks) == 1 and len(rig.app.hub.scene.objects) == 1
            obj = next(iter(rig.app.hub.scene.objects.values()))
            ctx, asked = start.ctx, start.ctx.asked
            assert asked.thread == original_thread and asked.day == original_day
            assert ctx.turn_id > 0 and start.ticket.admission is ctx.admission
            assert start.held is rig.capture and start.ticket.held(rig.capture.id) is rig.capture
            assert ctx.sight_selection.view is ctx.seen_view and ctx.seen_view == original_view
            assert ctx.seen_view is not original_view
            assert run.occurrence is start.occurrence
            assert run.occurrence.request_id == ctx.admission.request_id
            number, steps = run.step
            assert steps.runs[number] is run and steps.occurrences[number] == run.occurrence
            assert steps.outputs[number] is run.outputs
            expected = {'id': obj.id, 'run': RUN, 'frame': rig.capture.id,
                        'epoch': rig.capture.epoch, 'created_at': obj.created_at, 'turn_id': ctx.turn_id}
            born = copy.deepcopy(run.outputs[0].wire())
            assert born == {'kind': 'outline', **expected}
            assert obj.origin_box == original_view.things[1].box and obj.track == 'tracking'
            assert rig.masks[0].payload['jpeg'] == rig.jpeg
            assert rig.masks[0].payload['frame_id'] == rig.capture.id
            assert rig.masks[0].payload['box'] == list(original_view.things[1].box)

            journal = rig.app.day.journal
            if boundary == 'thread':
                newer = rig.app.day.open_thread('A genuinely separate later conversation')
                assert newer.id != original_thread
                assert rig.app.brain.moment().awareness['outputRefs'] == []
            elif boundary == 'carry':
                rig.seq += 1
                await rig.app.hub.source._accept(rig.jpeg, seq=rig.seq,
                                                boot='synthetic-target-argument-camera')
                later = rig.app.hub.latest_info
                assert later.id != rig.capture.id and later.epoch == rig.capture.epoch
                rig.app.hub._apply_reanchor([(obj.id, {'anchor_frame': later.id,
                    'anchor_epoch': later.epoch, 'anchor_offset': (later.dx, later.dy),
                    'track': 'tracking', 'carried': ('re-find', .95)})], later)
                assert obj.anchor_frame == later.id and obj.origin == rig.capture.id
                assert run.outputs[0].wire() == born
            elif boundary == 'new-session':
                rig.app.new_session()  # ADR 0017: the session left keeps its journal; installed App reset listeners
                assert rig.app.day.sid != asked.session and rig.app.day.generation != asked.generation
                assert not rig.app.day.current(asked.routing_generation)
                assert not rig.app.hub.scene.objects
                assert_original_receipt(journal.load(), obj.id, expected, asked)  # the session it was asked in
                assert output_events(rig.app.day.journal.load(), obj.id) == []
            if boundary != 'new-session':
                assert_original_receipt(journal.load(), obj.id, expected, asked)

            # This is before the external final response, not an end-of-turn repair oracle.
            assert len(barrier.entries) == 1 and not task.done()
            assert not any(row.get('role') == 'assistant' for row in rig.app.dialogue.history()['lines'])
            barrier.release.set()
            result = await task
            assert result['ok'] is True and result['answer'] == '' and result['answerStatus'] == 'not-published'
            receipts = [row for row in result['actions'] if row.get('actionId') == run.occurrence.action_id]
            assert len(receipts) == 1 and receipts[0]['ok'] is True
            assert receipts[0]['requestId'] == run.occurrence.request_id
            assert receipts[0]['outputs'] == [born] and result['objectIds'] == [obj.id]
            assert not any(row.get('role') == 'assistant' for row in rig.app.dialogue.history()['lines'])
            rig.assert_provider_accounting(start.ticket)
            if boundary == 'new-session':
                assert_original_receipt(journal.load(), obj.id, expected, asked)
                assert output_events(rig.app.day.journal.load(), obj.id) == []
                assert rig.app.brain.moment().awareness['outputRefs'] == []
            else:
                assert_original_receipt(journal.load(), obj.id, expected, asked)
                reloaded = DayStore(root=rig.app.day.root, day=original_day, clock=lambda: original_clock)
                reloaded.switch(original_thread)
                assert Conversation(reloaded).output_refs() == [expected]
        finally:
            barrier.release.set()
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await rig.close()
    asyncio.run(scenario())


def test_actual_app_model_reply_does_not_duplicate_its_original_action_receipt(tmp_path, monkeypatch):
    """Useful model-answer control; catches double qualification on note plus prose rows."""
    async def scenario():
        rig = ArgumentRig(tmp_path, monkeypatch)
        provider = FinalInterpretation(rig, monkeypatch, answer=MODEL_REPLY, held=False)
        try:
            await rig.prepare()
            await rig.publish_sight()
            result = await rig.ask('Highlight the second structure from this original look.',
                                   action('highlight', things=['s2']))
            starts = [start for start in rig.started if start.run.tool == 'highlight']
            assert len(starts) == 1 and starts[0].run.ok is True and len(provider.entries) == 1
            start, run = starts[0], starts[0].run
            assert result['ok'] is True and result['answer'] == MODEL_REPLY
            assert result['answerStatus'] == 'model' and len(run.outputs) == 1
            born = run.outputs[0].wire()
            expected = {key: value for key, value in born.items() if key != 'kind'}
            events = rig.app.day.journal.load()
            assert_original_receipt(events, born['id'], expected, start.ctx.asked)
            assistant = [event for event in events if event.get('type') == 'turn'
                         and event.get('role') == 'assistant']
            assert len(assistant) == 1 and assistant[0]['text'] == MODEL_REPLY
            assert (assistant[0].get('refs') or {}).get('outputRefs', []) == []
            shown = [row['text'] for row in rig.app.dialogue.history()['lines']
                     if row.get('type') == 'chat' and row.get('role') == 'assistant']
            assert shown == [MODEL_REPLY]
            assert rig.app.conversation.output_refs() == [expected]
            rig.assert_provider_accounting(start.ticket)
        finally:
            await rig.close()
    asyncio.run(scenario())


def test_actual_app_ordinary_model_reply_without_action_does_not_invent_outputs(tmp_path, monkeypatch):
    """Existing useful neighbor: a genuine model answer alone cannot create output facts."""
    from orchestrator.commands import COMMANDS

    async def scenario():
        rig = ArgumentRig(tmp_path, monkeypatch)
        provider = FinalInterpretation(rig, monkeypatch, held=False)
        try:
            await rig.prepare()
            await rig.publish_sight()
            rig.initial = reply()
            rig.initial['intent'] = 'question'
            model_text = rig.initial['answer']
            result = await COMMANDS['ask'](rig.app, rig.client,
                {'type': 'ask', 'text': 'Explain what a synthetic outline is.', 'frameId': rig.capture.id})
            assert result['ok'] is True and result['answer'] == model_text
            assert result['answerStatus'] == 'model' and not result['actions'] and not result['objectIds']
            assert not rig.started and not rig.masks and not provider.entries
            assert rig.app.conversation.output_refs() == []
            assert all(not (event.get('refs') or {}).get('outputRefs')
                       for event in rig.app.day.journal.load())
            shown = [row['text'] for row in rig.app.dialogue.history()['lines']
                     if row.get('type') == 'chat' and row.get('role') == 'assistant']
            assert shown == [model_text]
            rig.assert_provider_accounting(rig.sends[0].ticket)
        finally:
            await rig.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('final_answer', [MODEL_REPLY, ''], ids=['model-answer', 'unavailable-answer'])
def test_actual_app_direct_look_target_retains_original_facts_before_final(tmp_path, monkeypatch, final_answer):
    """A real unregistered point producer has facts even without a final model reply. Its outline drawn and nothing
    to tell beyond the look's answer, that answer is kept when the final reply is empty (T5: a request whose steps ran
    never ends with no words)."""
    from orchestrator.commands import COMMANDS

    async def scenario():
        rig = ArgumentRig(tmp_path, monkeypatch)
        barrier = FinalInterpretation(rig, monkeypatch, answer=final_answer)
        task = None
        try:
            await rig.prepare()
            rig.app.hub.settings.fast = 'off'
            rig.initial = reply()
            rig.initial['observation_scope'], rig.initial['input_refs'] = 'specimen-camera', ['camera']
            rig.initial['targets'] = [{'box_2d': [620, 620, 740, 740],
                                      'label': 'synthetic direct look target', 'confidence': .95}]
            original_thread = rig.app.day.active_id
            task = asyncio.create_task(COMMANDS['ask'](rig.app, rig.client,
                {'type': 'ask', 'text': 'Point out the synthetic right structure.',
                 'frameId': rig.capture.id}))
            await asyncio.wait_for(barrier.entered.wait(), timeout=HANG_S)
            assert not task.done() and not rig.started, 'SETUP: direct Agent.point, no fabricated tool occurrence'
            assert len(rig.masks) == 1 and len(rig.app.hub.scene.objects) == 1
            obj = next(iter(rig.app.hub.scene.objects.values()))
            ticket = rig.sends[0].ticket
            user = next(turn for turn in rig.app.day.threads[original_thread].turns if turn.role == 'user')
            asked = rig.app.conversation.asked_of(user)
            assert obj.source.startswith('ai:') and obj.track == 'tracking'
            assert obj.origin == rig.capture.id and obj.producing_basis.frame == rig.capture.id
            assert obj.producing_basis.run == RUN and obj.producing_basis.epoch == rig.capture.epoch
            assert rig.masks[0].ticket is ticket and ticket.held(rig.capture.id) is rig.capture
            assert rig.masks[0].payload['jpeg'] == rig.jpeg
            assert rig.masks[0].payload['frame_id'] == rig.capture.id
            expected = {'id': obj.id, 'run': RUN, 'frame': rig.capture.id,
                        'epoch': rig.capture.epoch, 'created_at': obj.created_at, 'turn_id': user.id}

            # Real later intake/carry and a different thread while the final external
            # provider waits: neither can lend source birth or receipt ownership.
            await rig.app.hub.source._accept(rig.jpeg, seq=rig.seq + 1,
                                            boot='synthetic-target-argument-camera')
            later = rig.app.hub.latest_info
            assert later.id != rig.capture.id and later.epoch == rig.capture.epoch
            rig.app.hub._apply_reanchor([(obj.id, {'anchor_frame': later.id,
                'anchor_epoch': later.epoch, 'anchor_offset': (later.dx, later.dy),
                'track': 'tracking', 'carried': ('re-find', .95)})], later)
            assert obj.anchor_frame == later.id and obj.origin == rig.capture.id
            newer = rig.app.day.open_thread('An unrelated conversation while the direct point is interpreted')
            assert newer.id != asked.thread and rig.app.brain.moment().awareness['outputRefs'] == []
            event = assert_original_receipt(rig.app.day.journal.load(), obj.id, expected, asked)
            assert not event['refs'].get('requestId') and not event['refs'].get('actionId')
            assert not any(row.get('role') == 'assistant' for row in rig.app.dialogue.history()['lines'])

            barrier.release.set()
            result = await task
            said = final_answer or rig.initial['answer']  # the look's own answer, kept (T5)
            assert result['ok'] is True and result['answer'] == said
            assert result['answerStatus'] == 'model'
            assert result['turnId'] == user.id and result['objectIds'] == [obj.id] and result['actions'] == []
            assert_original_receipt(rig.app.day.journal.load(), obj.id, expected, asked)
            assert rig.app.brain.moment().awareness['outputRefs'] == []
            rig.app.day.switch(asked.thread)
            current = rig.app.brain.moment().awareness['outputRefs']
            assert len(current) == 1 and current[0]['available'] is True
            assert all(current[0][key] == value for key, value in expected.items())
            original_lines = rig.app.dialogue.history()['lines']
            assert [row['text'] for row in original_lines if row.get('role') == 'assistant'] == [said]
            rig.assert_provider_accounting(ticket)
        finally:
            barrier.release.set()
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await rig.close()
    asyncio.run(scenario())
