"""Synthetic native input through real admission/Brain; no live model, UI or device qualification."""
import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from orchestrator import commands, scientific_ask
from orchestrator.brain.registry import ToolFailed, ToolContext, Tool, Registry, schema
from orchestrator.ticket import issue, using
from tests.standin_workers import build_app
from tests.test_native_picture_inputs import input_at


def selected(built):
    owner, pixels, reference = input_at(built)
    request = issue(built.hub, 'turn', asked=built.conversation.here(), channel='chat')
    request.workspace_origin = {'session': owner.session, 'tab': 'main'}
    return request, pixels, {'reference': reference, 'digest': owner.digest, 'tab': 'main'}


@pytest.mark.parametrize('change', ['foreign', 'digest', 'tab', 'unsupported'])
def test_invalid_saved_attachment_never_enters_answer_and_releases(tmp_path, change):
    built = build_app(tmp_path/'home')
    request, pixels, value = selected(built)
    if change == 'foreign': value['reference']['session'] = '2026-01-01.1'
    if change == 'digest': value['digest'] = '0' * 64
    if change == 'tab': value['tab'] = 'another-tab'
    if change == 'unsupported': value['reference']['component'] = 'array.absent'
    entered = []
    async def go():
        try:
            async with scientific_ask.admitted(built.hub, value, request):
                entered.append('provider')
        finally:
            request.seal()
    with pytest.raises(ToolFailed): asyncio.run(go())
    assert entered == []
    assert request.closed and all(hold.released for hold in request.holds)


def test_native_ask_uses_held_pixels_without_camera_snapshot_and_releases_on_failure(tmp_path, monkeypatch):
    built = build_app(tmp_path/'home')
    request, pixels, value = selected(built)
    seen = []
    assert built.hub.settings.fast != 'off'
    built.hub.settings.compare = ''
    built.hub.settings.pointing = 'box'
    built.hub.settings.outcome_lines = 'off'
    def camera(*args):
        pytest.fail('a native attachment must never fall back to the camera snapshot')
    monkeypatch.setattr(built.agent, 'snapshot', camera)
    async def no_fast(*args, **kwargs):
        pytest.fail('explicit saved pixels must not use the notes-only fast planner')
    monkeypatch.setattr(built.brain, 'quick', no_fast)
    async def stop_at_provider(question, view, **kwargs):
        capture, image, crop = view
        assert np.array_equal(image[:, :, 0], pixels)
        assert request.held(capture.id) is capture
        assert capture.source_image['resource'] == value['reference']
        assert capture.source_image['nativeSource']['revisionDigest'] == value['digest']
        assert 'Explicit typed-Ask attachment' in kwargs['notes'][1]
        assert 'No live outlines' not in kwargs['notes'][1] or not built.hub.scene.objects
        seen.append(capture)
        raise RuntimeError('synthetic provider failure')
    monkeypatch.setattr(built.brain, '_look', stop_at_provider)
    async def go():
        try:
            async with scientific_ask.admitted(built.hub, value, request) as capture:
                await built.brain.turn('Describe this saved input', asked=request.asked, request_ticket=request, input_capture=capture)
        finally:
            request.seal()
    with pytest.raises(RuntimeError, match='synthetic provider failure'): asyncio.run(go())
    assert len(seen) == 1
    assert request.closed and all(hold.released for hold in request.holds)


def test_native_scope_refuses_undeclared_effect_before_handler(tmp_path):
    built = build_app(tmp_path/'home')
    request, pixels, value = selected(built)
    called = []
    async def live_effect(ctx, args): called.append('live'); return 'changed'
    registry = Registry()
    registry.add(Tool('live_effect', 'Synthetic camera effect', schema({}), live_effect))
    async def go():
        try:
            async with scientific_ask.admitted(built.hub, value, request) as capture:
                with using(request):
                    ctx = ToolContext('chat', 1, capture.id, scientific_input=capture)
                    run = registry.start('live_effect', {}, ctx)
                    await run.task
                    assert run.ok is False and 'does not support' in run.summary
        finally: request.seal()
    asyncio.run(go())
    assert called == []

@pytest.mark.parametrize('mode', ['json-actions', 'fc'])
def test_shared_agent_look_keeps_mode_history_exact_png_and_retained_claim(tmp_path, monkeypatch, mode):
    from orchestrator.marks import fov_crop
    from orchestrator.vision import VisionResult
    from orchestrator.brain.claims import observation_limit
    built = build_app(tmp_path/'home')
    request, pixels, value = selected(built)
    seen = []
    result = {'answer': 'Synthetic answer', 'targets': [], 'actions': [],
              'observation_scope': 'retained-source', 'input_refs': [1]}
    async def vision(images, prompt, **kwargs):
        seen.append((images, prompt, mode)); return VisionResult(result, 1., built.hub.settings.vision)
    async def fc(model, image, prompt, registry):
        seen.append(([image], prompt, mode)); return result, {'model': model, 'latency_ms': 1.}
    monkeypatch.setattr(built.agent, 'vision', lambda _model: SimpleNamespace(ask=vision))
    monkeypatch.setattr(built.agent.fc, 'ask', fc)
    monkeypatch.setattr(built.agent, '_history_text', lambda: 'Previously asked about saved cells.')
    async def go():
        try:
            async with scientific_ask.admitted(built.hub, value, request) as capture:
                with using(request):
                    image = capture.bgr()
                    output, _ = await built.agent.look('Describe the input', (capture, image, fov_crop(None, image.shape[1], image.shape[0])),
                         option=built.hub.settings.vision, mode=mode, notes=('Tools', 'Saved context', 'None'), registry=built.brain.registry)
                    assert seen[0][0] == [capture.analysis_bytes]
                    assert value['digest'] in seen[0][1] and 'Previously asked' in seen[0][1]
                    assert observation_limit(output, images=[(capture.analysis_bytes, scientific_ask.facts(capture))]) is None
                    assert observation_limit({**output, 'observation_scope':'specimen-camera'}, images=[(capture.analysis_bytes, scientific_ask.facts(capture))])
        finally: request.seal()
    asyncio.run(go())

def test_declared_capability_defaults_to_attachment_and_keeps_its_native_identity(tmp_path):
    from orchestrator.evidence import evidence_of
    built = build_app(tmp_path/'home')
    request, pixels, value = selected(built)
    from orchestrator import workspace_views
    workspace_views.save(built.day, workspace_views.read(built.day, schema=4)['layout'], 0)
    request.workspace_origin.update({'placementId':'native-ask-test', 'run':request.id, 'input':{'resource':value['reference']}})
    request.workspace_store = lambda: built.day
    async def go():
        try:
            async with scientific_ask.admitted(built.hub, value, request) as capture:
                with using(request):
                    ctx = ToolContext('chat', 1, capture.id, scientific_input=capture, asked=request.asked)
                    run = built.brain.registry.start('run_capability', {'capability':'basic-bright-objects', 'params':{'sigma':.5,'min_area':3}}, ctx)
                    await run.task
                    assert run.ok is True, run.error
                    population = next(iter(evidence_of(built.hub).items.values()))
                    assert population.producer['capturedNativeInput']['nativeSource'] == capture.source_image['nativeSource']
        finally: request.seal()
    asyncio.run(go())

@pytest.mark.parametrize('attached', [True, False])
def test_saved_input_specimen_does_not_rename_live_stage_but_camera_guess_still_applies(tmp_path, monkeypatch, attached):
    from orchestrator.marks import fov_crop
    built = build_app(tmp_path/'home')
    request, pixels, value = selected(built)
    built.hub.settings.fast = 'off'; built.hub.settings.compare = ''; built.hub.settings.pointing = 'box'
    built.hub.settings.outcome_lines = 'off'
    before = dict(built.hub.scene.context), built.day.active.subject
    async def reply(*args, **kwargs):
        return {'answer':'Synthetic saved-image finding', 'specimen':'synthetic cultured cells', 'targets':[], 'actions':[]}, {'model':built.hub.settings.vision,'latency_ms':1.}
    monkeypatch.setattr(built.brain, '_look', reply)
    async def go():
        try:
            async with scientific_ask.admitted(built.hub, value, request) as capture:
                fields = {'input_capture':capture} if attached else {}
                if not attached:
                    # A synthetic existing-camera view, solely to exercise the unchanged subject branch.
                    del request.scientific_input
                    image = capture.bgr()
                    monkeypatch.setattr(built.agent, 'snapshot', lambda frame=None:(capture,image,fov_crop(None,image.shape[1],image.shape[0])))
                await built.brain.turn('Describe this image', asked=request.asked, request_ticket=request, **fields)
        finally: request.seal()
    asyncio.run(go())
    if attached:
        assert (dict(built.hub.scene.context), built.day.active.subject) == before
    else:
        assert built.hub.scene.context['specimen'] == built.day.active.subject == 'synthetic cultured cells'


def test_saved_input_turn_retains_exact_manifest_after_journal_reload(tmp_path):
    from orchestrator.day.store import SessionStore
    built = build_app(tmp_path/'home')
    request, _, value = selected(built)
    async def go():
        try:
            async with scientific_ask.admitted(built.hub, value, request) as capture:
                facts = scientific_ask.facts(capture)
                turn = built.conversation.add('user', 'chat', 'Describe saved data', asked=request.asked, scientificInput=facts)
                reloaded = SessionStore(built.day.root, session=built.day.sid)
                restored = next(t for t in reloaded.active.turns if t.id == turn.id)
                assert restored.refs['scientificInput'] == facts
        finally: request.seal()
    asyncio.run(go())


def test_camera_keeps_default_fast_planner(tmp_path, monkeypatch):
    built = build_app(tmp_path/'home')
    assert built.hub.settings.fast != 'off'
    seen = []
    async def fast(*args, **kwargs):
        seen.append('fast')
        raise RuntimeError('synthetic fast boundary')
    monkeypatch.setattr(built.brain, 'quick', fast)
    request, _, value = selected(built)
    async def go():
        from orchestrator.marks import fov_crop
        try:
            async with scientific_ask.admitted(built.hub, value, request) as capture:
                del request.scientific_input
                image = capture.bgr()
                monkeypatch.setattr(built.agent, 'snapshot', lambda frame=None:(capture,image,fov_crop(None,image.shape[1],image.shape[0])))
                await built.brain.turn('Describe the current view', asked=request.asked, request_ticket=request)
        finally: request.seal()
    with pytest.raises(RuntimeError, match='synthetic fast boundary'):
        asyncio.run(go())
    assert seen == ['fast']
