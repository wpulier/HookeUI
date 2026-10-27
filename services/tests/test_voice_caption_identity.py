"""Production speech lines: one journal identity, timed full-duplex captions."""
import pytest
from orchestrator.brain.speech import SpokenLine
from orchestrator.voice.openai_live import OpenAILiveSession
from orchestrator.voice.base import VoiceConfig


class Journal:
    def __init__(self):
        self.rows = {}
    def here(self):
        return 'origin'
    def show(self, line, **kwargs):
        self.rows[line['lineId']] = dict(line)
    stream = show


def test_captions_keep_repeated_words_and_group_by_speech_time_not_delivery_delay():
    journal = Journal()
    line = SpokenLine('assistant', journal, '', lambda: 'origin', lambda *args: None)
    line.caption = True
    line.add('Hello', span={'startMs': 100, 'endMs': 300, 'at': 1, 'speechSession': 's'})
    identity = line.id
    line.add(' hello.', span={'startMs': 300, 'endMs': 600, 'at': 1.2, 'speechSession': 's'})
    assert line.id == identity
    assert journal.rows[identity]['text'] == 'Hello hello.'
    assert journal.rows[identity]['echo'] is False
    line.add('New answer.', span={'startMs': 3000, 'endMs': 4000, 'at': 4, 'speechSession': 's'})
    assert line.id != identity
    assert len(journal.rows) == 2
    line.close()
    assert journal.rows[identity]['at'] == 1


def test_duplicate_provider_event_id_is_ignored_but_repeated_words_are_not():
    session = OpenAILiveSession(VoiceConfig(model='gpt-live-1'))
    fragment = {'type': 'session.input_transcript.delta', 'event_id': 'one', 'delta': 'hello', 'start_ms': 0, 'end_ms': 100}
    session.handle(fragment)
    session.handle(fragment)
    session.handle({**fragment, 'event_id': 'two', 'delta': ' hello', 'start_ms': 100, 'end_ms': 200})
    session._finish_heard()
    events = list(session._queue._queue)
    assert len(events) == 3
    assert events[-1].text == 'hello hello'
    assert events[0].data['delta'] == 'hello'


def test_quiet_caption_suffix_is_saved_without_splitting_identity(monkeypatch):
    import asyncio
    async def run():
        journal = Journal()
        updates = []
        saved = asyncio.Event()
        monkeypatch.setattr("orchestrator.brain.speech.REFILE_S", 0.01)
        journal.stream = lambda *args, **kwargs: None
        line = SpokenLine('assistant', journal, '', lambda: 'origin', lambda *args: None)
        line.caption = True
        line.updated = lambda *args: (updates.append(args), saved.set())
        line.add('A', span={'startMs': 0, 'endMs': 100, 'at': 1, 'speechSession': 's'})
        identity = line.id
        line.add(' complete answer.', span={'startMs': 100, 'endMs': 200})
        saved.clear()
        await asyncio.wait_for(saved.wait(), timeout=2)
        assert line.id == identity
        assert journal.rows[identity]['text'] == 'A complete answer.'
        assert updates[-1] == (identity, 'A complete answer.', 'origin')
        line.close()
    asyncio.run(run())


def test_spoken_answer_is_in_shared_memory_before_talk_ends(tmp_path):
    from tests.test_speech import spoken
    requests, brain, _, _, day, _, _ = spoken(tmp_path, {"intent": "filler", "needs_look": False})
    requests.reply_line.caption = True
    requests.said('The count is ', span={'startMs': 0, 'endMs': 100})
    requests.said('42.', span={'startMs': 100, 'endMs': 200})
    requests.reply_done()
    identity = requests.reply_line.id
    replies = [turn for turn in brain.conversation.turns if turn.role == 'assistant']
    assert len(replies) == 1 and replies[0].text == 'The count is 42.'
    brain.conversation.add('user', 'typed', 'Explain that count.')
    requests.said(' With two touching cells.', span={'startMs': 200, 'endMs': 400})
    requests.reply_done()
    assert requests.reply_line.id == identity
    assert replies[0].text == 'The count is 42. With two touching cells.'
    requests.close()
    assert len([turn for turn in brain.conversation.turns if turn.role == 'assistant']) == 1


def test_caption_checkpoint_after_project_switch_stays_in_original_project(tmp_path):
    from tests.test_speech import spoken
    requests, brain, _, _, day, _, _ = spoken(tmp_path, {"intent": "filler", "needs_look": False})
    origin = day.sid
    requests.reply_line.caption = True
    requests.said('Original answer', span={'startMs': 0, 'endMs': 100})
    day.new('Other experiment')
    requests.said(' completed.', span={'startMs': 100, 'endMs': 200})
    requests.reply_done()
    assert not [turn for turn in day.active.turns if turn.role == 'assistant']
    kept = day.elsewhere(origin)
    assert kept.active.turns[-1].text == 'Original answer completed.'
    requests.close()


def test_immediate_live_reply_keeps_question_before_answer_in_shared_history(tmp_path):
    import asyncio
    from tests.test_speech import spoken
    async def run():
        requests, brain, _, _, day, _, _ = spoken(tmp_path, {"intent": "filler", "needs_look": False})
        requests.user_line.caption = requests.reply_line.caption = True
        requests.user_line.add('Hello', span={'startMs': 0, 'endMs': 100})
        requests.heard('Hello', (0, 100))
        requests.said('Hello back.', span={'startMs': 150, 'endMs': 250})
        assert [(t.role, t.text) for t in brain.conversation.turns][:2] == [('user', 'Hello'), ('assistant', 'Hello back.')]
        await requests.pending
        requests.reply_done()
        assert [(t.role, t.text) for t in brain.conversation.turns][:2] == [('user', 'Hello'), ('assistant', 'Hello back.')]
        assert len([t for t in brain.conversation.turns if t.role == 'user']) == 1
        requests.close()
    asyncio.run(run())


def test_long_notification_stages_exact_facts_then_requests_speech_once():
    import asyncio
    import json
    from types import SimpleNamespace
    from orchestrator.voice.openai_live import append_context

    async def run():
        wire = []
        class Part:
            def __init__(self, kind):
                self.kind = kind
            async def append(self, **arguments):
                wire.append(SimpleNamespace(kind=self.kind, args=arguments))
        text = ('<untrusted>λ=488nm; 細胞 "sample"\n' * 80) + '</untrusted>'
        await append_context(Part('commentary'), text, staging=Part('thinking'), delegation_id='original-task')
        assert [row.kind for row in wire].count('commentary') == 1
        assert wire[-1].kind == 'commentary'
        assert all(len(row.args['content'].encode('utf-8')) <= 500 for row in wire)
        assert all(row.args['delegation_id'] == 'original-task' for row in wire)
        fragments = [json.loads(row.args['content']) for row in wire[:-1]]
        assert [item['part'] for item in fragments] == list(range(len(fragments)))
        assert ''.join(item['text'] for item in fragments) == text
        request = json.loads(wire[-1].args['content'])
        assert {item['live_context'] for item in fragments} == {request['live_context']}
    asyncio.run(run())
