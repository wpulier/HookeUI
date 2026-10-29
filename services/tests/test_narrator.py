"""Synthetic Line/voice unit inputs, never actual producer or model evidence.

Default app data is silent context. Explicitly canned model Lines exercise exact
once-only speech delivery. Real Narrator queue/timing/session behavior stays in
place; only external voice responses and declared timing inputs are scripted.
Actual operation activity and persistence belong to the separate App tests.
"""
import asyncio
import copy
import json
import time

import pytest

from orchestrator.brain import narrator as N
from orchestrator.brain.narrator import Line, Narrator
from tests.waiting import until_async


class FakeVoice:
    """External delivery recorder; only respond=True can generate canned speech."""

    def __init__(self, speaking=False, speaks_on=("any",), error=None):
        self.closed = False
        self.speaking = speaking
        self.speaks_on = speaks_on
        self.error = error
        self.injected = []
        self._spoke_at = 0.0
        self.speech_tasks = []

    def idle_since(self):
        return 0.0 if self.speaking else 5.0

    def last_speech_at(self):
        return self._spoke_at

    async def inject(self, text, **kwargs):
        self.injected.append({'text': text, 'kwargs': copy.deepcopy(kwargs),
                              'at': time.monotonic(), 'voice': self, 'error': self.error})
        if self.error is not None:
            raise self.error
        if kwargs.get('respond') is True and ("any" in self.speaks_on or
                                              kwargs.get('delegation_id') in self.speaks_on):
            async def speak():
                await asyncio.sleep(0.05)
                self._spoke_at = time.monotonic()
            self.speech_tasks.append(asyncio.ensure_future(speak()))

    def assert_context(self, line, index=0):
        """Assert outside inject; decode the full actual header and bounded body."""
        sent = self.injected[index]
        assert sent['voice'] is self and sent['error'] is None
        assert sent['kwargs'] == {'respond': False, 'source': 'narrator'}  # source: its voice-note record only
        assert sent['kwargs']['respond'] is False
        prefix = 'Context only, do not read aloud: '
        assert sent['text'].startswith(prefix)
        header, wrapped = sent['text'][len(prefix):].split('\n', 1)
        assert json.loads(header) == {'turnId': line.turn_id, 'requestId': line.request_id,
                                     'actionId': line.action_id}
        opening = '<voice-context source="app" untrusted="true">\n'
        assert wrapped.startswith(opening)
        body, suffix = wrapped[len(opening):].split('\n</voice-context>', 1)
        assert body == line.text.replace('<', ' ').replace('>', ' ')[:24576]
        assert suffix == ('\nOmitted context is unknown, not a successful outcome.'
                          if len(line.text) > 24576 else '')
        assert self._spoke_at == 0.0 and not self.speech_tasks

    def assert_model(self, line, index=0):
        sent = self.injected[index]
        assert sent['voice'] is self and sent['error'] is None
        assert sent['text'] == line.text
        assert sent['kwargs'] == {'respond': True, 'delegation_id': line.delegation_id}
        assert sent['kwargs']['respond'] is True


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(N, "CONFIRM_S", 0.4)
    monkeypatch.setattr(N, "MAX_WAIT_S", 0.6)
    monkeypatch.setattr(N, "MIN_GAP_S", 5.0)


async def settle(narrator, seconds=1.5):
    end = time.monotonic() + seconds
    while time.monotonic() < end and (narrator.waiting or (narrator._task and not narrator._task.done())):
        await asyncio.sleep(0.05)
    assert not narrator.waiting, 'real queue must drain before teardown, not through cancellation'
    assert narrator._task is None or narrator._task.done(), 'delivery must reach a real terminal state'
    if narrator._task is not None:
        await narrator._task  # surface actual worker exceptions rather than hiding them


def run(voice, lines, quieter=False, during=None, *, seconds=1.5, get_voice=None):
    chats = []

    async def go():
        narrator = Narrator(get_voice or (lambda: voice),
                            lambda line, **where: chats.append((copy.deepcopy(line), where)),
                            quieter=lambda: quieter)
        queued = []
        for line in lines:
            narrator.offer(line)
            queued.append(narrator.waiting[-1])  # passive immutable enqueue observation
        if during:
            await during(voice, narrator, tuple(queued))
        await settle(narrator, seconds)
        if voice is not None:
            await asyncio.gather(*voice.speech_tasks)
        return narrator, tuple(queued)

    narrator, queued = asyncio.run(go())
    return narrator, chats, queued


def test_requested_result_is_spoken_on_its_delegation():
    for provenance in ('data', 'model'):
        voice = FakeVoice()
        line = Line('Explicitly canned unit result.', delegation_id='d1', asked=('original-day', 't1'),
                    turn_id=1, request_id='synthetic-r1', action_id='synthetic-a1',
                    **({'provenance': 'model'} if provenance == 'model' else {}))
        assert line.provenance == provenance
        narrator, chats, queued = run(voice, [line])
        assert len(voice.injected) == 1 and not chats
        assert queued[0] is not line and queued[0].voice_owner is voice
        assert queued[0].asked is line.asked and queued[0].delegation_id == 'd1'
        assert line.voice_owner is None  # offer froze a copy, not the caller's value
        if provenance == 'model':
            voice.assert_model(line)
            assert voice.last_speech_at() > voice.injected[0]['at']
        else:
            voice.assert_context(line)
        assert [(how, text) for _at, how, text in narrator.delivered] == [
            ('voice' if provenance == 'model' else 'silent', line.text)]


def test_waits_for_a_pause_before_speaking(monkeypatch):
    monkeypatch.setattr(N, 'MAX_WAIT_S', 5.0)
    for provenance in ('data', 'model'):
        voice = FakeVoice(speaking=True)
        pause = []
        line = Line('Explicitly canned pause result.', delegation_id='d1', turn_id=2,
                    request_id='synthetic-pause', action_id='synthetic-pause-a',
                    **({'provenance': 'model'} if provenance == 'model' else {}))
        assert line.provenance == provenance

        async def stop_talking(v, narrator, queued):
            await asyncio.sleep(0.3)
            assert not v.injected and narrator.waiting == list(queued)
            assert queued[0].voice_owner is v
            pause.append(time.monotonic())
            v.speaking = False

        narrator, chats, queued = run(voice, [line], during=stop_talking)
        assert len(voice.injected) == 1 and not chats
        assert voice.injected[0]['at'] >= pause[0] > queued[0].at
        assert voice.injected[0]['at'] - queued[0].at < N.MAX_WAIT_S
        if provenance == 'model':
            voice.assert_model(line)
        else:
            voice.assert_context(line)
        assert len(narrator.delivered) == 1


def test_requested_lines_are_not_held_forever():
    for provenance in ('data', 'model'):
        voice = FakeVoice(speaking=True)
        line = Line('Explicitly canned overdue result.', delegation_id='d1',
                    **({'provenance': 'model'} if provenance == 'model' else {}))
        assert line.provenance == provenance

        async def before_maximum(v, narrator, queued):
            await asyncio.sleep(0.2)
            assert time.monotonic() - queued[0].at < N.MAX_WAIT_S
            assert not v.injected and narrator.waiting == list(queued)

        narrator, chats, queued = run(voice, [line], during=before_maximum)
        assert voice.speaking and len(voice.injected) == 1 and not chats
        waited = voice.injected[0]['at'] - queued[0].at
        assert N.MAX_WAIT_S <= waited <= N.MAX_WAIT_S + 1.0
        if provenance == 'model':
            voice.assert_model(line)
        else:
            voice.assert_context(line)
        assert len(narrator.delivered) == 1


def test_unspoken_delegation_update_is_retried_as_a_plain_update():
    """Historical name: an unspoken model line now keeps its one original send."""
    voice = FakeVoice(speaks_on=(None,))
    line = Line('Explicitly canned unspoken model result.', delegation_id='old', provenance='model',
                asked=('original-day', 't1'), request_id='synthetic-old', action_id='synthetic-old-a')
    narrator, chats, queued = run(voice, [line])
    assert len(voice.injected) == 1 and not chats
    voice.assert_model(line)
    assert voice.last_speech_at() == 0.0 and not voice.speech_tasks
    assert queued[0].voice_owner is voice and queued[0].asked is line.asked
    assert [(how, text) for _at, how, text in narrator.delivered] == [('unavailable', line.text)]
    assert narrator.delivered[0][0] - voice.injected[0]['at'] >= N.CONFIRM_S


def test_never_spoken_falls_back_to_chat():
    """Historical name: absent speech or failed delivery creates no chat prose."""
    for error in (None, RuntimeError('explicitly canned external delivery failure')):
        voice = FakeVoice(speaks_on=(), error=error)
        line = Line('Explicitly canned model result.', rid='r1', delegation_id='d1', provenance='model',
                    asked=('original-day', 't1'), turn_id=3,
                    request_id='synthetic-unavailable', action_id='synthetic-unavailable-a')
        narrator, chats, queued = run(voice, [line])
        assert len(voice.injected) == 1 and not chats
        assert voice.injected[0]['text'] == line.text
        assert voice.injected[0]['kwargs'] == {'respond': True, 'delegation_id': 'd1'}
        assert voice.injected[0]['error'] is error
        assert voice.injected[0]['voice'] is voice
        assert queued[0].voice_owner is voice and queued[0].rid == 'r1'
        assert queued[0].asked is line.asked and queued[0].request_id == line.request_id
        assert voice.last_speech_at() == 0.0 and not voice.speech_tasks
        assert [(how, text) for _at, how, text in narrator.delivered] == [('unavailable', line.text)]


def test_without_voice_lines_go_to_the_chat():
    """Historical name: unavailable original sessions retire without fallback."""
    lines = [Line('App requested data.', asked=('original-day', 't1')),
             Line('App unrequested data.', 'suggestion', asked=('original-day', 't2')),
             Line('Explicitly canned model result.', delegation_id='original', provenance='model')]
    narrator, chats, queued = run(None, lines)
    assert not chats and all(line.voice_owner is None for line in queued)
    assert [(how, text) for _at, how, text in narrator.delivered] == [
        ('unavailable', line.text) for line in lines]

    for change in ('close', 'replace'):
        original, replacement = FakeVoice(speaking=True), FakeVoice()
        installed = [original]

        async def retire_original(v, narrator, offered):
            assert all(line.voice_owner is original for line in offered)
            assert not original.injected and narrator.waiting == list(offered)
            if change == 'close':
                original.closed = True
            else:
                installed[0] = replacement

        narrator, chats, queued = run(original, lines, during=retire_original,
                                      get_voice=lambda: installed[0])
        assert not chats and not original.injected and not replacement.injected
        assert all(line.voice_owner is original for line in queued)
        assert [line.asked for line in queued] == [line.asked for line in lines]
        assert [(how, text) for _at, how, text in narrator.delivered] == [
            ('unavailable', line.text) for line in lines]


def test_unasked_lines_are_merged_and_rate_limited(monkeypatch):
    """Distinct data values retain the real gap; expired unasked data stays silent."""
    voice = FakeVoice()
    lines = [Line('App confirmation one.', 'confirmation', turn_id=4,
                  request_id='synthetic-one', action_id='synthetic-one-a'),
             Line('App suggestion two.', 'suggestion', turn_id=5,
                  request_id='synthetic-two', action_id='synthetic-two-a')]
    first_stamp = []

    async def before_next(v, narrator, queued):
        await until_async(lambda: v.injected)
        assert len(v.injected) == 1
        first_stamp.append(narrator.last_unrequested)
        await asyncio.sleep(0.25)
        assert time.monotonic() - first_stamp[0] < N.MIN_GAP_S
        assert len(v.injected) == 1 and narrator.waiting == [queued[1]]

    narrator, chats, queued = run(voice, lines, during=before_next, seconds=N.MIN_GAP_S + 1.5)
    assert len(voice.injected) == 2 and not chats
    for index, line in enumerate(lines):
        voice.assert_context(line, index)
        assert queued[index].voice_owner is voice
    assert narrator.last_unrequested - first_stamp[0] >= N.MIN_GAP_S
    assert voice.injected[1]['at'] - voice.injected[0]['at'] >= N.MIN_GAP_S - 0.05
    assert [(how, text) for _at, how, text in narrator.delivered] == [('silent', line.text) for line in lines]

    # Declared unit timing input, not a changed queue or invented completion.
    monkeypatch.setattr(N, 'EXPIRES_S', 0.25)
    busy = FakeVoice(speaking=True)
    expired = Line('App unrequested data that expires.', 'suggestion')
    narrator, chats, queued = run(busy, [expired])
    assert time.monotonic() - queued[0].at >= N.EXPIRES_S
    assert not chats and not busy.injected and not narrator.delivered
    assert busy.last_speech_at() == 0.0


def test_quieter_speaks_only_what_was_asked_for():
    for provenance in ('data', 'model'):
        voice = FakeVoice()
        asked = Line('Explicitly canned requested unit value.', delegation_id='d1',
                     **({'provenance': 'model'} if provenance == 'model' else {}))
        assert asked.provenance == provenance
        unasked = Line('App suggestion.', 'suggestion')
        narrator, chats, queued = run(voice, [asked, unasked], quieter=True)
        assert len(voice.injected) == 1 and not chats
        assert all(line.voice_owner is voice for line in queued)
        if provenance == 'model':
            voice.assert_model(asked)
        else:
            voice.assert_context(asked)
        assert [(how, text) for _at, how, text in narrator.delivered] == [
            ('unavailable', unasked.text), ('voice' if provenance == 'model' else 'silent', asked.text)]


def test_waiting_results_go_out_as_one():
    """Historical name: each value is delivered once on its own origin."""
    for provenance in ('data', 'model'):
        voice = FakeVoice()
        lines = [Line(text, delegation_id=delegation, asked=('original-day', thread),
                      turn_id=turn, request_id=request, action_id=action,
                      **({'provenance': 'model'} if provenance == 'model' else {}))
                 for text, delegation, thread, turn, request, action in (
                     ('First explicitly canned unit value.', 'd1', 't1', 6, 'synthetic-r6', 'synthetic-a6'),
                     ('Second explicitly canned unit value.', 'd2', 't2', 7, 'synthetic-r7', 'synthetic-a7'))]
        assert all(line.provenance == provenance for line in lines)
        narrator, chats, queued = run(voice, lines)
        assert len(voice.injected) == 2 and not chats
        for index, line in enumerate(lines):
            assert queued[index].voice_owner is voice and queued[index].asked is line.asked
            assert queued[index].delegation_id == line.delegation_id
            if provenance == 'model':
                voice.assert_model(line, index)
            else:
                voice.assert_context(line, index)
        assert [(how, text) for _at, how, text in narrator.delivered] == [
            ('voice' if provenance == 'model' else 'silent', line.text) for line in lines]


def test_a_failure_does_not_wait_for_a_pause():
    for provenance in ('data', 'model'):
        voice = FakeVoice(speaking=True)
        line = Line('Explicitly canned failure: the slide moved.', delivery='interrupt', delegation_id='d1',
                    **({'provenance': 'model'} if provenance == 'model' else {}))
        assert line.provenance == provenance

        async def while_still_busy(v, narrator, queued):
            await asyncio.sleep(0.2)
            assert v.speaking and len(v.injected) == 1
            assert v.injected[0]['at'] - queued[0].at < N.MAX_WAIT_S

        narrator, chats, queued = run(voice, [line], during=while_still_busy)
        assert len(voice.injected) == 1 and not chats and queued[0].voice_owner is voice
        if provenance == 'model':
            voice.assert_model(line)
        else:
            voice.assert_context(line)
        assert len(narrator.delivered) == 1


def test_silent_lines_are_told_not_said():
    for provenance in ('data', 'model'):
        voice = FakeVoice(speaking=True)
        line = Line('App or explicitly canned model information <source>.' + 'Z' * 25000,
                    'suggestion', delivery='silent', delegation_id='d1', turn_id=8,
                    request_id='synthetic-silent', action_id='synthetic-silent-a',
                    **({'provenance': 'model'} if provenance == 'model' else {}))
        assert line.provenance == provenance
        narrator, chats, queued = run(voice, [line])
        assert len(voice.injected) == 1 and not chats and voice.speaking
        voice.assert_context(line)
        assert queued[0].voice_owner is voice and queued[0].delegation_id == 'd1'
        assert [(how, text) for _at, how, text in narrator.delivered] == [('silent', line.text)]


def test_a_result_queued_before_the_user_spoke_may_already_be_covered():
    for provenance in ('data', 'model'):
        voice = FakeVoice(speaking=True)
        line = Line('Explicitly canned original value, without a new prefix.', delegation_id='d1',
                    asked=('original-day', 't1'), turn_id=9,
                    request_id='synthetic-before-speech', action_id='synthetic-before-speech-a',
                    **({'provenance': 'model'} if provenance == 'model' else {}))
        assert line.provenance == provenance
        heard = []

        async def user_speaks(v, narrator, queued):
            await asyncio.sleep(0.05)
            assert not v.injected and narrator.waiting == list(queued)
            narrator.user_spoke()
            heard.append(narrator.last_heard)
            assert heard[0] > queued[0].at
            v.speaking = False

        narrator, chats, queued = run(voice, [line], during=user_speaks)
        assert len(voice.injected) == 1 and not chats
        assert voice.injected[0]['at'] >= heard[0]
        assert queued[0].voice_owner is voice and queued[0].asked is line.asked
        if provenance == 'model':
            voice.assert_model(line)
        else:
            voice.assert_context(line)
        assert [(how, text) for _at, how, text in narrator.delivered] == [
            ('voice' if provenance == 'model' else 'silent', line.text)]


def test_a_line_that_no_longer_holds_when_spoken_is_told_not_said():
    # ADR 0016's waits: "still working" was said after the work had finished (live 2026-10-11 16:18:40), and a late
    # outcome must not be said over a newer question. `still` is read at speaking time.
    voice = FakeVoice()
    held = Line('photo is still being done.', delegation_id='d1', turn_id=1, request_id='r1', action_id='a1',
                provenance='model', notify=True, still=lambda: False)
    narrator, chats, queued = run(voice, [held])
    assert len(voice.injected) == 1 and voice.injected[0]['kwargs']['respond'] is False
    assert [how for _at, how, _text in narrator.delivered] == ['silent']
    voice = FakeVoice()
    holds = Line('Explicitly canned unit result.', delegation_id='d1', turn_id=1, request_id='r1', action_id='a1',
                 provenance='model', still=lambda: True)
    narrator, chats, queued = run(voice, [holds])
    assert voice.injected[0]['kwargs']['respond'] is True


def test_one_outcome_is_said_once_and_a_model_answer_waiting_with_its_facts_is_the_one_said():
    """Live 2026-10-28 (one outline said three times): a late step's raw facts and the model's own answer about the
    same step are one outcome (its identity, Line.outcomes). Both queued while the voice was busy: the answer is said,
    the facts told. Once one of them was said (narrator.Said), the other is told."""
    voice = FakeVoice(speaking=True)
    facts = Line('{"outline_cells": "341 nuclei"}', delegation_id='d1', notify=True, request_id='r1', action_id='a1',
                 outcomes=frozenset({'action:a1'}))
    answer = Line('I have outlined 341 nuclei.', delegation_id='d1', provenance='model', request_id='r1',
                  action_id='a1', outcomes=frozenset({'action:a1'}))

    async def quiet_later(v, narrator, queued):
        await asyncio.sleep(0.1)
        v.speaking = False

    narrator, chats, _ = run(voice, [facts, answer], during=quiet_later)
    respond = [sent for sent in voice.injected if sent['kwargs'].get('respond')]
    assert [sent['text'] for sent in respond] == ['I have outlined 341 nuclei.']
    from orchestrator.brain.narrator import Said

    said = Said()
    voice = FakeVoice()
    voice.said = said
    narrator, chats, _ = run(voice, [facts])
    narrator, chats, _ = run(voice, [answer])
    assert [sent['kwargs'].get('respond') for sent in voice.injected] == [True, False]
