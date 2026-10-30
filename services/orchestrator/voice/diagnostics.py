"""Bounded voice boundary observations in the existing Activity/execution stores.

No audio, transcripts, provider errors, URLs or browser-supplied text are accepted.
Client observations are explicitly labelled; only server events establish provider facts.
"""
from __future__ import annotations

import time
from uuid import uuid4
from .. import activity, execution

CLIENT_STAGES = {
    'capture': 'Microphone audio is reaching Hooke',
    'signal': 'The microphone detected sound',
    'playback': 'The browser started response playback',
    'playback-blocked': 'Response playback was blocked; check audio output and start Talk again',
    'capture-stalled': 'Microphone audio stopped; check the selected microphone and start Talk again',
    'response-wait': 'No voice response yet; inspect the connection or end Talk and retry explicitly',
}
SERVER_STAGES = {
    'connecting': 'Connecting to the voice provider',
    'ready': 'The voice provider acknowledged the session',
    'audio-received': 'Microphone audio reached the voice service',
    'input_transcript': 'The provider returned a speech transcript',
    'output_transcript': 'The provider returned response text',
    'audio': 'The provider returned response audio',
    'error': 'The voice provider reported a failure; inspect the conversation and provider access',
    'provider-credit': 'Voice needs API credit. The provider refused this session; add credit in your provider account, then start Talk again',
    'provider-access': 'Voice provider access was refused. Check the saved key and model permission in Models, then start Talk again',
    'provider-rate-limit': 'The voice provider is rate limited. Wait for its limit to reset, then start Talk again',
    'response-failed': 'Hooke could not deliver the voice response. Your conversation is kept; start Talk again and copy diagnostics if it repeats',
    'closed': 'The voice connection closed',
    'ended': 'Talk ended; delegated work keeps its own Stop control',
}
FAILURES = {'error', 'provider-credit', 'provider-access', 'provider-rate-limit', 'response-failed', 'playback-blocked', 'capture-stalled', 'response-wait'}

class VoiceDiagnostics:
    def __init__(self, hub, asked, model):
        self.hub, self.asked, self.model = hub, asked, model
        self.id = 'voice-' + uuid4().hex
        self.started = time.monotonic()
        self.seen = set()
        self.lines = []
        self.failure = None

    def provider_error(self, data):
        """Known provider refusals become fixed recovery text; arbitrary text is never retained.

        Older adapters only supplied message. The exact observed credit-refusal prefix
        supports those records without copying the provider's URL or account details.
        """
        data = data if isinstance(data, dict) else {}
        code = data.get('code')
        message = data.get('message')
        from research_worker.api_usage import CREDIT_CODES  # the one mapping of why a model could not be used

        if code in CREDIT_CODES or (isinstance(message, str) and message.startswith('You have no credits remaining.')):
            stage = 'provider-credit'
        elif code in ('invalid_api_key', 'permission_denied', 'model_not_found'):
            stage = 'provider-access'
        elif code == 'rate_limit_exceeded':
            stage = 'provider-rate-limit'
        else:
            stage = 'error'
        self.record(stage)

    def record(self, stage, *, client=False):
        if not isinstance(stage, str):
            return
        words = (CLIENT_STAGES if client else SERVER_STAGES).get(stage)
        key = ('client' if client else 'server', stage)
        if words is None or key in self.seen:
            return
        self.seen.add(key)  # fixed vocabulary: bounded even for a long/malicious session
        elapsed = round((time.monotonic() - self.started) * 1000)
        source = 'Browser reported' if client else 'Service observed'
        self.lines.append(f'{elapsed} ms · {source}: {words}')
        if stage in FAILURES and (self.failure is None or self.failure == SERVER_STAGES['error']):
            self.failure = words
        log = getattr(self.hub, 'execution', None)
        if log is not None:
            with log.request('voice-diagnostic', day=getattr(self.asked, 'day', ''),
                             session=getattr(self.asked, 'session', ''), thread=getattr(self.asked, 'thread', ''),
                             generation=getattr(self.asked, 'generation', 0)) as receipt:
                with execution.span('voice-boundary', phase=stage, model=self.model, provider='openai',
                                    sessionId=self.id, scope=source, adapterMs=elapsed):
                    if stage in FAILURES:
                        execution.finish('failed')
                        receipt.finish('failed')
        activity.activity(self.hub, 'model', self.failure or words, id=self.id, asked=self.asked,
                          state='failed' if self.failure else 'done' if stage in ('ended', 'closed') else 'running',
                          ref={'model': self.model}, details={'lines': self.lines[-8:],
                          'ids': {'voiceSession': self.id, 'model': self.model}})
