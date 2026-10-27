"""TEST-only native capability composition over App's actual installed owners.

AppStudio replaces only external renderer measurements; retained bytes, source
resolution, Registry, native commands, accepted observations and request pins are
real. The one provider substitute captures actual planner input and returns a
scripted empty plan. No renderer capability manifest or native outcome is made
here. Root alone runs the RED gate; these are synthetic integration assertions,
not browser, physical-camera, provider-quality or runtime acceptance evidence.
"""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from orchestrator.app import App
from orchestrator.brain import context
from orchestrator.brain.toolbox import build
from orchestrator.commands import COMMANDS
from orchestrator.paths import Paths
from orchestrator.workspace_observation import ObservationError
from tests.test_native_requested_goal_binding import inline_report
from tests.test_workspace_actions_app import (
    WORKSPACE, built, jpeg, open_window, output_window, plan, saved_picture,
)


@pytest.fixture
def native(tmp_path):
    app, studio = built(tmp_path)
    try:
        yield app, studio
    finally:
        app.hub._analysis.shutdown(wait=True)
        app.hub._background.shutdown(wait=True)


def pin(app, studio, request='synthetic-capability-request'):
    return app.hub.screen.pin(studio, request_id=request)


def app_facts(text):
    """Read the whole serialized facts supplied to the model, not reply prose."""
    for line in text.splitlines():
        if line.startswith('App facts (') and ': {' in line:
            return json.loads(line.split(': ', 1)[1])
    raise AssertionError('the actual model input contains no complete App facts')


def presentation_flags(text):
    for line in text.splitlines():
        if line.startswith('Presentation: '):
            return {name: json.loads(value) for name, value in
                    (field.split('=', 1) for field in line[14:].split(', '))}
    raise AssertionError('the bounded voice input lost its presentation facts')


def typed(moment):
    return context.scene_table(moment)[0]


def voice(moment):
    return {'note': context.view_note(moment), 'recap': context.voice_recap(moment)}


def assert_native_available(moment):
    assert moment.awareness['presentation']['independent_windows'] is True, (
        'installed native windows were described as unavailable because no item '
        'advertises a fabricated renderer capability manifest')
    assert moment.awareness['presentation']['split'] is True
    model = app_facts(typed(moment))
    assert model['presentation']['independent_windows'] is True
    assert model['presentation']['split'] is True
    for reader, text in voice(moment).items():
        assert len(text) <= {'note': 1800, 'recap': 1900}[reader]
        assert presentation_flags(text) == {'independent_windows': True, 'split': True}


def test_empty_accepted_workspace_advertises_installed_native_tools_without_item_manifest(native):
    app, studio = native
    snapshot = pin(app, studio)
    assert snapshot.observation.wire()['items'] == []
    assert snapshot.observation.wire()['sources'] == []
    # These are the real model-facing declarations and the same catalogue used
    # by the app, not a copied catalogue or a test-created Tool.
    declaration = next(row for row in app.brain.registry.responses_tools() if row['name'] == 'present')
    properties = declaration['parameters']['properties']
    assert declaration['parameters']['required'] == ['do']
    assert properties['do']['type'] == 'string'
    assert properties['source']['type'] == 'object'
    assert properties['instance']['type'] == 'object'
    assert properties['targets']['type'] == 'array'
    installed = [row for row in app.capabilities_wire()['capabilities']
                 if row['actor'] == 'agent' and row.get('how', {}).get('tool') == 'present']
    assert any(row['available'] for row in installed)
    assert app.services.presentations is app.hub.presentations
    assert app.services.prepared_sources is not None
    assert callable(app.services.workspace_context)
    moment = app.brain.moment(request_snapshot=snapshot)
    assert moment.awareness['observations']['workspace_report']['current'] is True
    assert_native_available(moment)
    assert moment.awareness['input'] == {
        'basis': 'context-only', 'camera_pixels': False, 'workspace_pixels': False}
    assert moment.awareness['observations']['workspace_image']['verified'] is False


def test_actual_empty_workspace_ask_supplies_truthful_native_facts_to_planner(native, monkeypatch):
    app, studio = native
    settings = app.hub.settings
    settings.fast, settings.compare, settings.outcome_lines, settings.speculate = 'off', '', 'off', 'off'
    settings.pointing, settings.closeup = 'box', 'single'
    inputs = []

    async def provider(images, prompt, **kwargs):
        inputs.append((tuple(images), prompt))
        return SimpleNamespace(data={
            'answer': 'Synthetic external provider answer.', 'targets': [], 'actions': [],
            'intent': 'request', 'needs_look': False,
            'observation_scope': 'structural-workspace', 'input_refs': []},
            model='scripted-external-provider', latency_ms=0, error=None)

    monkeypatch.setattr(app.agent, 'vision', lambda _option: SimpleNamespace(ask=provider))

    async def send():
        await COMMANDS['ask'](app, studio, {
            'type': 'ask', 'text': 'What presentation controls are available?',
            'screen': inline_report(studio)})

    asyncio.run(send())
    assert len(inputs) == 1, 'a structural capability question needs only its admitted planner input'
    images, prompt = inputs[0]
    assert images == ()
    facts = app_facts(prompt)
    assert facts['presentation']['independent_windows'] is True
    assert facts['presentation']['split'] is True
    assert facts['observations']['workspace_report']['current'] is True
    assert facts['input']['camera_pixels'] is False and facts['input']['workspace_pixels'] is False


def test_advertised_beside_example_moves_one_window_against_a_distinct_existing_reference(native):
    app, studio = native
    declaration = next(row for row in app.brain.registry.responses_tools() if row['name'] == 'present')
    example = deepcopy(next(row for row in declaration['parameters']['examples'] if row['do'] == 'arrange'))
    assert len(example['targets']) == 1, 'the installed renderer moves one target beside an explicit anchor'
    assert example['relation']['kind'] == 'beside' and example['relation']['align'] == 'top'
    assert set(example['relation']['reference']) == {'instanceId'}
    assert example['relation']['reference'] not in example['targets'], 'the anchor must not move beside itself'
    entry, _ = saved_picture(app)

    async def send():
        anchor, _, _ = await open_window(app, entry)
        moved, _, _ = await open_window(app, entry)
        assert moved != anchor and studio.items[moved]['rootId'] != studio.items[anchor]['rootId']
        before_anchor, before_moved = deepcopy(studio.items[anchor]), deepcopy(studio.items[moved])
        example['targets'] = [{'instanceId': moved}]
        example['relation']['reference'] = {'instanceId': anchor}
        runs, _ = await plan(app, [('present', example)])
        assert runs[0].ok is True and runs[0].outcome['status'] == 'confirmed'
        assert not runs[0].outcome.get('failure')
        assert studio.items[anchor] == before_anchor
        actual = studio.items[moved]
        assert actual['rect'] != before_moved['rect']
        assert actual['rect'][:2] == [before_anchor['rect'][0] + before_anchor['rect'][2] + example['relation']['gapPx'],
                                     before_anchor['rect'][1]]
        assert actual['rect'][2:] == before_moved['rect'][2:]
        assert actual['sourceKey'] == before_moved['sourceKey']
        assert actual['sourceGeneration'] == before_moved['sourceGeneration']
        assert runs[0].outcome['requestId'] == runs[0].occurrence.request_id
        assert runs[0].outcome['actionId'] == runs[0].occurrence.action_id

    asyncio.run(send())


def test_native_instance_context_keeps_actual_source_and_independent_control_targets(native):
    app, studio = native
    entry, _ = saved_picture(app)

    async def arrange():
        original, _, _ = await open_window(app, entry)
        runs, steps = await plan(app, [('present', {'do': 'duplicate', 'what': {'instanceId': original}})])
        assert runs[0].ok is True
        return original, output_window(steps)

    windows = asyncio.run(arrange())
    assert len(set(windows)) == 2
    snapshot = pin(app, studio)
    observed = snapshot.observation.wire()
    native_items = {row['id']: row for row in observed['items']}
    assert set(native_items) == set(windows)
    assert all('capabilities' not in item for item in native_items.values())
    source = observed['sources'][0]
    assert source['identity']['resource'] == {
        'day': entry.day, 'id': entry.id, 'version': entry.version, 'picture': 0}
    moment = app.brain.moment(request_snapshot=snapshot)
    before = deepcopy(moment.awareness)
    assert_native_available(moment)
    projected_sources = app_facts(typed(moment))['observations']['workspace_report']['sources']
    assert any(row.get('key') == source['key'] and row.get('identity', {}).get('resource') ==
               {'day': entry.day, 'id': entry.id, 'version': entry.version, 'picture': 0}
               for row in projected_sources), 'model facts lost the exact retained input revision'
    # Entire source/instance identities must survive each bounded reader. A
    # sourceKey alone does not disclose which retained original it is. Control
    # addresses are the acting brain's: the voice talks about windows and never
    # operates their controls, so for it they are optional detail.
    readers = {'typed': typed(moment), **voice(moment)}
    for reader, text in readers.items():
        for value in (entry.id,) if reader != 'typed' else (source['key'], entry.id, entry.day):
            assert value in text, (reader, 'lost qualified retained source', value)
        for iid in windows:
            item = native_items[iid]
            assert iid in text, (reader, 'lost actual native instance', iid)
            assert item['sourceKey'] == source['key']
            controls = item['controls']
            assert len(controls) == 7  # both pan axes remain independently addressable
            assert all(control['targetId'] == iid and control['present'] and control['enabled']
                       for control in controls)
            if reader == 'typed':
                for control in controls:
                    assert control['id'] in text, (reader, 'lost actual control target', control['id'])
    assert moment.awareness == before


def test_disconnected_native_renderer_is_distinct_from_current_empty_workspace(native):
    app, studio = native
    snapshot = pin(app, studio)
    captured = app.brain.moment(request_snapshot=snapshot)
    app.hub.screen.gone(studio)
    with pytest.raises(ObservationError) as refused:
        app.workspace_context(studio, request_id='after-disconnect', owner='after-disconnect-action')
    assert refused.value.code == 'CLIENT_MISMATCH'
    with pytest.raises(ObservationError):
        pin(app, studio, 'after-disconnect')
    now = app.brain.moment()
    assert now.awareness['observations']['workspace_report']['current'] is False
    assert now.awareness['presentation']['independent_windows'] is False
    assert now.awareness['presentation']['split'] is False
    # A captured Send remains detached history; it cannot gain later items.
    assert captured.request_snapshot.observation.wire()['items'] == []
    assert app.services.presentations.list(WORKSPACE) == ()


def test_registry_without_native_owner_does_not_upgrade_legacy_present_to_independent_windows(native):
    app, studio = native
    # Use the supported Services/build composition seam for a legacy-only tool
    # installation. No replacement handler, owner, Scene or capability wire.
    legacy = replace(app.services, presentations=None, prepared_sources=None, workspace_context=None)
    app.brain.services = legacy
    app.brain.registry = build(legacy)
    assert 'present' in app.brain.registry.tools
    moment = app.brain.moment(request_snapshot=pin(app, studio))
    assert moment.awareness['observations']['workspace_report']['current'] is True
    assert moment.awareness['presentation']['independent_windows'] is False
    assert moment.awareness['presentation']['split'] is False
    for text in voice(moment).values():
        assert presentation_flags(text) == {'independent_windows': False, 'split': False}


def test_default_camera_and_legacy_paired_pictures_remain_accurate_without_workspace(tmp_path):
    app = App.build('http://127.0.0.1:1', Paths.under(tmp_path), use_gpu=False)

    async def camera_and_pair():
        await app.hub.source._accept(jpeg(width=640, height=640), seq=1,
                                     boot='synthetic-capability-camera')
        app.services.magnifier.show_picture(jpeg(60), 'Synthetic first picture')
        app.services.magnifier.show_picture(jpeg(150), 'Synthetic second picture', beside=True)

    try:
        asyncio.run(camera_and_pair())
        moment = app.brain.moment()
        camera = moment.awareness['observations']['camera']
        assert camera['available'] is True and camera['current'] is True
        assert camera['provenance'] == 'raw-camera'
        assert camera['boot'] == 'synthetic-capability-camera' and camera['seq'] == 1
        assert moment.awareness['observations']['workspace_report']['available'] is False
        assert moment.awareness['input']['camera_pixels'] is False
        assert moment.awareness['input']['workspace_pixels'] is False
        assert moment.awareness['presentation']['independent_windows'] is False
        assert moment.awareness['presentation']['split'] is False
        assert app.services.magnifier.shown_beside() is not None
        assert 'in the one window' in moment.screen
        assert 'in the one window' in moment.screen_voice
        assert app.services.presentations.list(WORKSPACE) == ()
    finally:
        app.hub._analysis.shutdown(wait=True)
        app.hub._background.shutdown(wait=True)
