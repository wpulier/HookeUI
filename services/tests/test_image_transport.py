"""Synthetic SDK payloads for actual encoded bytes; no network or model calls."""
import asyncio
import base64
from types import SimpleNamespace as NS

import cv2
import numpy as np
import pytest

from orchestrator.brain.llm import FunctionCallingBrain
from orchestrator.brain.registry import Registry
from orchestrator.ticket import issue, using
from orchestrator.brain.dispatch import answer_phase
from tests.fakes import Hub
from tests.test_model_plan_diagnostics import sdk_response, vision


@pytest.mark.parametrize('provider', ['gemini', 'openai'])
@pytest.mark.parametrize('mode', ['vision', 'fc'])
@pytest.mark.parametrize('extension,mime', [('.png','image/png'),('.jpg','image/jpeg')])
def test_provider_payload_matches_original_image_bytes(monkeypatch, provider, mode, extension, mime):
    ok, encoded = cv2.imencode(extension, np.full((2,3), 120, np.uint8))
    assert ok
    image = encoded.tobytes()
    sent = []
    async def create(**kwargs):
        sent.append(kwargs)
        reply = sdk_response(provider, text='{"answer":"synthetic"}')
        if provider == 'gemini':
            reply.candidates[0].content = NS(parts=[NS(text='{"answer":"synthetic"}',thought=False)])
            reply.function_calls = []
        return reply
    async def run():
        request = issue(Hub(), 'turn')
        try:
            with using(request), answer_phase('look'):
                if mode == 'vision':
                    await vision(provider, create).ask([image], 'Synthetic image format check')
                else:
                    monkeypatch.setenv('GEMINI_API_KEY' if provider == 'gemini' else 'OPENAI_API_KEY', 'synthetic-test-key')
                    client = NS(aio=NS(models=NS(generate_content=create))) if provider == 'gemini' else NS(responses=NS(create=create))
                    brain = FunctionCallingBrain()
                    brain._clients[(provider,'synthetic-test-key')] = client
                    await brain.ask(f'{provider}:synthetic@low', image, 'Synthetic image format check', Registry())
        finally: request.seal()
    asyncio.run(run())
    assert len(sent) == 1
    if provider == 'gemini':
        part = sent[0]['contents'][0].inline_data
        assert (part.mime_type, part.data) == (mime, image)
    else:
        url = sent[0]['input'][0]['content'][0]['image_url']
        assert url == f'data:{mime};base64,' + base64.b64encode(image).decode()
