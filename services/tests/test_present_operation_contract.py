"""Synthetic public model-input contract over the actual installed App tools.

Only the external provider and existing external renderer are scripted. Schema
examples describe syntax, not available resources or successful paint. These
tests make no model-quality, browser, live-overlay or whole-request claim.
"""
import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from orchestrator.brain.toolbox import build
from orchestrator.ticket import issue, using
from tests.test_workspace_actions_app import built, plan


LEGACY = {'show', 'hide', 'opacity', 'colour', 'fill', 'edge', 'emphasise',
          'open', 'close', 'move', 'resize', 'front'}
NATIVE_ONLY = {'duplicate', 'split', 'zoom', 'pan', 'arrange', 'beside', 'restore', 'group', 'ungroup'}
PLACE = {'place'}  # do=place with placement: the one placement vocabulary (placement.py), for any target
TRACKS = {'show', 'hide', 'open', 'close', 'opacity', 'colour', 'emphasise'}
MAGNIFIER = {'open', 'close', 'move', 'resize', 'front'}
NATIVE = {'open', 'close', 'duplicate', 'split', 'zoom', 'pan', 'move',
          'resize', 'front', 'arrange', 'beside', 'opacity', 'hide', 'restore', 'group', 'ungroup'}


@pytest.fixture
def native(tmp_path):
    app, studio = built(tmp_path)
    try:
        yield app, studio
    finally:
        app.hub._analysis.shutdown(wait=True)
        app.hub._background.shutdown(wait=True)


def declaration(registry):
    return next(row for row in registry.responses_tools() if row['name'] == 'present')


def json_objects(text):
    """Read whole public JSON examples/maps regardless of whitespace or key order."""
    decoder = json.JSONDecoder()
    rows = []
    for index, char in enumerate(text):
        if char != '{':
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except ValueError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def target_operations(params):
    maps = [row for row in json_objects(params.get('description', ''))
            if 'tracks' in row and 'magnifier' in row]
    assert len(maps) == 1, 'the shared schema needs one compact target-operation map'
    assert all(isinstance(verbs, list) and all(isinstance(verb, str) for verb in verbs)
               for verbs in maps[0].values())
    return {target: set(verbs) for target, verbs in maps[0].items()}


def examples(params):
    rows = params.get('examples')
    assert isinstance(rows, list) and rows, 'operation-specific argument examples are missing'
    assert all(isinstance(row, dict) and isinstance(row.get('do'), str) for row in rows)
    assert all(set(row) <= set(params['properties']) for row in rows)
    return rows


def test_built_function_call_declaration_has_the_authoritative_operation_enum(native):
    app, _ = native
    params = declaration(app.brain.registry)['parameters']
    verbs = params['properties']['do'].get('enum')
    assert isinstance(verbs, list), 'do is currently an unrestricted string rather than an operation enum'
    assert len(verbs) == len(set(verbs))
    assert set(verbs) == LEGACY | NATIVE_ONLY | PLACE
    assert 'value' not in verbs, 'value is an argument to opacity/colour, never an operation'
    assert params['required'] == ['do']


def test_built_declaration_distinguishes_layer_magnifier_and_native_operations(native):
    app, _ = native
    row = declaration(app.brain.registry)
    matrix = target_operations(row['parameters'])
    assert matrix['tracks'] == TRACKS
    assert matrix['magnifier'] == MAGNIFIER
    assert matrix['native_instance'] == NATIVE
    assert not ({'move', 'resize', 'arrange', 'split'} & matrix['tracks'])
    assert 'opacity' in matrix['native_instance'], 'native opacity changes the window, not the source'
    assert 'Change anything on screen' not in row['description'], 'a blanket promise contradicts target-specific support'


def test_actual_schema_examples_separate_opacity_legacy_rect_and_native_layout(native):
    app, _ = native
    rows = examples(declaration(app.brain.registry)['parameters'])
    opacity = [row for row in rows if row.get('do') == 'opacity'
               and str(row.get('what', '')).startswith('tracks:')]
    assert opacity and any(row.get('value') == '50%' for row in opacity)
    assert all(set(row) == {'do', 'what', 'value'} for row in opacity)
    captured = [row for row in rows if row.get('do') == 'resize' and row.get('what') == 'magnifier']
    assert captured and all(len(row.get('rect', [])) == 4 and 'size' not in row for row in captured)
    saved = [row for row in rows if row.get('do') == 'open'
             and isinstance(row.get('source'), dict) and 'id' in row['source']]
    assert saved and all({'day', 'id', 'version', 'picture'} <= set(row['source']) for row in saved)
    arranged = [row for row in rows if row.get('do') == 'arrange']
    assert arranged
    for row in arranged:
        assert 'what' not in row and 'rect' not in row and 'value' not in row
        assert len(row.get('targets', [])) == 1
        ids = [target.get('instanceId') for target in row['targets']]
        relation = row.get('relation', {})
        assert set(relation['reference']) == {'instanceId'}
        ids.append(relation['reference']['instanceId'])
        assert all(isinstance(iid, str) and iid for iid in ids) and len(set(ids)) == 2
        assert relation['kind'] == 'beside' and relation['align'] == 'top'


def test_legacy_only_services_do_not_advertise_native_window_operations(native):
    app, _ = native
    registry = build(replace(app.services, presentations=None, prepared_sources=None, workspace_context=None))
    params = declaration(registry)['parameters']
    assert set(params['properties']['do'].get('enum', [])) == LEGACY | PLACE
    assert 'native_instance' not in target_operations(params)
    assert all(row['do'] not in NATIVE_ONLY and not {'source', 'instance', 'targets'} & set(row)
               for row in examples(params))


def test_native_position_declares_closed_required_numeric_css_coordinates(native):
    app, _ = native
    position = declaration(app.brain.registry)['parameters']['properties']['position']
    assert position == {
        'type': 'object',
        'properties': {'x': {'type': 'number'}, 'y': {'type': 'number'}},
        'required': ['x', 'y'],
        'description': 'native move only: {x,y} viewport CSS pixels (the reported DOM rectangle space), fitted within the accepted viewport.presentationArea; never rect',
        'additionalProperties': False,
    }


@pytest.mark.parametrize('layout', ['flat', 'families', 'compact'])
def test_json_action_menu_retains_enum_examples_and_target_capabilities(native, layout):
    app, _ = native
    params = declaration(app.brain.registry)['parameters']
    rendered = app.brain.registry.prompt_section(layout)
    assert set(params['properties']['do'].get('enum', [])) == LEGACY | NATIVE_ONLY | PLACE
    assert all(verb in rendered for verb in LEGACY | NATIVE_ONLY | PLACE)
    supplied = json_objects(rendered)
    assert all(row in supplied for row in examples(params)), 'the text menu dropped the legal argument examples'
    matrix = target_operations(params)
    assert any(isinstance(row.get('tracks'), list) and
               {target: set(verbs) for target, verbs in row.items()} == matrix
               for row in supplied), 'compact layout lost the layer/window capability distinction'


def test_real_fast_dispatch_receives_the_same_examples_and_target_facts(native, monkeypatch):
    app, studio = native
    app.hub.settings.fast = 'gemini-3.5-flash'
    inputs = []

    async def provider(images, prompt, **kwargs):
        inputs.append((tuple(images), prompt))
        return SimpleNamespace(data={'intent': 'question', 'needs_look': False,
                                    'answer': 'Synthetic model text.', 'actions': [],
                                    'observation_scope': 'structural-workspace', 'input_refs': []},
                               model='scripted-external-provider', latency_ms=0, error=None)

    monkeypatch.setattr(app.agent, 'vision', lambda option: SimpleNamespace(ask=provider))

    async def request():
        snapshot = app.hub.screen.pin(studio, request_id='synthetic-presentation-syntax')
        moment = app.brain.moment(request_snapshot=snapshot)
        ticket = issue(app.hub, 'turn', asked=app.day.here(), channel='chat')
        try:
            with using(ticket):
                result = await app.brain.quick('What display controls are installed?', moment=moment)
            assert result is not None and result.actions == []
            assert ticket.admission.used == 1
            assert [attempt.phase for attempt in ticket.admission.attempts] == ['fast']
        finally:
            ticket.close()

    asyncio.run(request())
    assert len(inputs) == 1 and inputs[0][0] == (), 'syntax guidance adds no image or model round'
    params = declaration(app.brain.registry)['parameters']
    supplied = json_objects(inputs[0][1])
    assert all(row in supplied for row in examples(params))
    matrix = target_operations(params)
    assert any(isinstance(row.get('tracks'), list) and
               {target: set(verbs) for target, verbs in row.items()} == matrix for row in supplied)


@pytest.mark.parametrize('args', [
    {'do': 'value', 'what': 'tracks:m3', 'value': '50%'},
    {'do': 'open', 'what': 'tracks:m3', 'value': '50%'},
])
def test_real_registered_malformed_calls_are_refused_without_intent_conversion(native, args):
    app, studio = native
    before = len(studio.messages)
    runs, _ = asyncio.run(plan(app, [('present', args)]))
    assert len(runs) == 1 and runs[0].ok is False
    assert runs[0].args == args
    assert runs[0].occurrence is not None
    assert not any(message.get('commandId') or message.get('type') == 'present'
                   for message in studio.messages[before:]), 'a malformed call must not be converted into a guessed effect'
    assert runs[0].summary, 'the original failed occurrence must retain its reason'


def test_beside_schema_does_not_invent_an_explicit_right_preference(native):
    app, _ = native
    relation = declaration(app.brain.registry)['parameters']['properties']['relation']
    side = relation['properties']['side']
    assert 'default' not in side
    assert 'explicit side never falls back' in side['description']
    assert 'omit side to try right then left' in relation['description']


def test_photo_and_present_contracts_expose_initial_beside_fit(native):
    app, _ = native
    photo = next(row for row in app.brain.registry.responses_tools() if row['name'] == 'reference_photo')
    assert 'beside=true' in photo['description']
    assert 'before its first paint' in photo['description']
    assert 'original requesting browser' in photo['description']
    assert 'show=false' in photo['description'] and 'relation' in photo['description']
    assert 'initial fit' in photo['parameters']['properties']['beside']['description']
    present = declaration(app.brain.registry)
    params = present['parameters']
    initial = [row for row in examples(params) if row['do'] == 'open' and 'relation' in row]
    assert initial and any(row['source'] == {'step': 1, 'output': 0} for row in initial)
    assert all(row['relation']['kind'] == 'beside' and 'instanceId' in row['relation']['reference']
               and 'targets' not in row and 'instance' not in row for row in initial)
    assert 'optional title/instance/relation' in params['description']
    relation = params['properties']['relation']['description']
    assert 'open' in relation and 'before its first paint' in relation
    assert 'arrange' in relation and 'Keeps current measured sizes' in relation
    assert 'explicit side left or right is honored' in relation
