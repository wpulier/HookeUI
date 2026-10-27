"""Actual Screen receipts, synthetic independent browsers and controlled show failure."""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator.brain.registry import ToolFailed
from orchestrator.brain.toolbox import present
from orchestrator.capture import RUN
from orchestrator.workspace_observation import ClientRef
from tests.test_legacy_workspace_projection import structural_item, structural_report, structural_rig


def test_reshow_reads_original_browser_not_newer_hidden_peer(tmp_path, monkeypatch):
    async def go():
        rig, _ = structural_rig(tmp_path)
        original = structural_report(rig)
        original['items'] = [structural_item('outlines', 'outlines', {'opacity': 1})]
        rig.screen.report(original, rig.client)
        pin = rig.pin(request_id='original-outline')
        ctx = SimpleNamespace(client=rig.client, request_snapshot=pin, request_id='original-outline', admission=None)
        peer = ClientRef('peer-space', 'peer-client', 'peer-page', 'peer-connection')
        rig.screen.accept_client(rig.other, peer, app_run_id=RUN,
            source_resolver=lambda *_: None, capture_authority=lambda *_: False)
        hidden = structural_report(rig, seq=11)
        hidden['client'] = peer.wire()
        hidden['items'] = [structural_item('outlines', 'outlines', {'opacity': 1})]
        hidden['items'][0].update(workspaceId=peer.workspace_id, visible=False)
        rig.now += .01
        rig.screen.report(hidden, rig.other)
        assert rig.screen.item('outlines')['visible'] is False, 'SETUP: latest peer differs'
        async def unexpected(*_, **__):
            pytest.fail('an already-visible original browser needs no show command')
        monkeypatch.setattr(present, 'act', unexpected)
        assert await present.reshow_outlines(rig.app.services, ctx) == ''
    asyncio.run(go())


@pytest.mark.parametrize('outcome', ['visible', 'hidden', 'missing', 'disconnected', 'unchanged'])
def test_reshow_failure_uses_final_original_receipt_or_explicit_uncertainty(tmp_path, monkeypatch, outcome):
    async def go():
        rig, _ = structural_rig(tmp_path)
        original = structural_report(rig)
        original['items'] = [structural_item('outlines', 'outlines', {'opacity': 1})]
        original['items'][0]['visible'] = False
        rig.screen.report(original, rig.client)
        pin = rig.pin(request_id='original-outline')
        ctx = SimpleNamespace(client=rig.client, request_snapshot=pin, request_id='original-outline', admission=None)
        async def failed_show(services, args, by=None, ctx=None):
            assert ctx is not None and ctx.client is rig.client and ctx.request_snapshot is pin
            if outcome == 'disconnected':
                rig.screen.gone(rig.client)
            elif outcome != 'unchanged':
                rig.now += .1
                final = structural_report(rig, seq=11)
                final['items'] = [] if outcome == 'missing' else [structural_item('outlines', 'outlines', {'opacity': 1}, revision=8)]
                if final['items']:
                    final['items'][0]['visible'] = outcome == 'visible'
                rig.screen.report(final, rig.client)
            raise ToolFailed('original change acknowledgement missing', next='inspect the original view')
        monkeypatch.setattr(present, 'act', failed_show)
        words = await present.reshow_outlines(rig.app.services, ctx)
        if outcome == 'visible':
            assert words == ''  # actual visible receipt, not invented ACK success
        elif outcome == 'hidden':
            assert 'hidden' in words
        else:
            assert 'not confirmed' in words and 'hidden' not in words and 'shown again' not in words
    asyncio.run(go())


def test_outlines_on_in_a_studio_in_the_background_are_left_as_they_are(tmp_path, monkeypatch):
    """Live 2026-10-11 (the studio's page in the background): every item reports not visible, and answers said "the
    workspace reports the outline layer as hidden", then "could not ... the window was hidden". The outlines are on
    in the studio, which paints nothing in the background: nothing is changed, and nothing is said against it."""
    async def go():
        rig, _ = structural_rig(tmp_path)
        original = structural_report(rig)
        original['items'] = [structural_item('outlines', 'outlines', {'opacity': 1})]
        original['viewport']['visible'] = False
        rig.screen.report(original, rig.client)
        pin = rig.pin(request_id='original-outline')
        ctx = SimpleNamespace(client=rig.client, request_snapshot=pin, request_id='original-outline', admission=None)
        async def unexpected(*_, **__):
            pytest.fail('a page that is not visible gets no show command')
        monkeypatch.setattr(present, 'act', unexpected)
        words = await present.reshow_outlines(rig.app.services, ctx)
        assert words == ""
    asyncio.run(go())


def test_reshow_success_requires_real_original_change_confirmation(tmp_path, monkeypatch):
    import json
    from orchestrator.brain.registry import ToolContext

    async def go():
        rig, _ = structural_rig(tmp_path)
        original = structural_report(rig)
        original['items'] = [structural_item('outlines', 'outlines', {'opacity': 1})]
        original['items'][0]['visible'] = False
        rig.screen.report(original, rig.client)
        pin = rig.pin(request_id='original-outline')
        ctx = ToolContext('chat', 1, None, 'highlight the target', client=rig.client,
            request_snapshot=pin, request_id='original-outline')
        sent = []
        def receive(raw, *args, **kwargs):
            message = json.loads(raw)
            sent.append(message)
            assert message['type'] == 'present' and message['id'] == 'outlines'
            final = structural_report(rig, seq=11)
            final['items'] = [structural_item('outlines', 'outlines', {'opacity': 1}, revision=8)]
            final['applied'] = [message['change']]
            def acknowledge():
                rig.now += .1
                rig.screen.report(final, rig.client)
            asyncio.get_running_loop().call_soon(acknowledge)
        monkeypatch.setattr(rig.client, 'offer', receive)
        words = await present.reshow_outlines(rig.app.services, ctx)
        assert words == '; the outlines were hidden, shown again'
        assert len(sent) == 1
        assert rig.screen.completion_report(rig.client)['items'][0]['visible'] is True
    asyncio.run(go())
