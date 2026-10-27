"""Contract fixtures, not plugin or complete persistence qualification."""
import copy
import threading

import pytest

from env_worker.scientific_native_panel import CallbackFailed, BoundaryFailed
from env_worker.scientific_tool_document import Binding, InvalidDocument, document, FORMAT, MAX_BYTES


def saved(state=None):
    return {'format': FORMAT, 'schema': 'example.animation', 'version': 1,
            'state': {'frames': [{'name': 'µm plane', 'steps': 15}]} if state is None else state}


def test_capture_and_restore_are_explicit_detached_and_exact():
    state = saved()['state']
    calls = []
    def restore(value):
        calls.append(copy.deepcopy(value))
        state.clear(); state.update(value)
    binding = Binding(schema='example.animation', version=1,
                      capture=lambda: state, restore=restore)
    assert calls == []
    snapshot = binding.capture()
    state['frames'][0]['steps'] = 20
    assert snapshot['state']['frames'][0]['steps'] == 15
    binding.restore(snapshot)
    assert binding.capture() == snapshot
    assert len(calls) == 1
    state['frames'][0]['steps'] = 25
    assert snapshot['state']['frames'][0]['steps'] == 15
    with pytest.raises(InvalidDocument, match='only once'):
        binding.restore(snapshot)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), 2**53, b'bytes', object(), (1, 2), {'x': {1: 'key'}}])
def test_executable_or_lossy_state_is_refused(value):
    with pytest.raises(InvalidDocument):
        document(saved({'value': value}))


def test_envelope_rejects_contributed_objects_without_invoking_equality():
    class ExecutableText(str):
        def __eq__(self, other):
            pytest.fail('Validation invoked contributed equality')
        def __ne__(self, other):
            pytest.fail('Validation invoked contributed equality')
        __hash__ = str.__hash__
    with pytest.raises(InvalidDocument):
        document({**saved(), 'format': ExecutableText(FORMAT)})
    with pytest.raises(InvalidDocument):
        document({ExecutableText('format'): FORMAT, 'schema': 'example.animation',
                  'version': 1, 'state': {}})


def test_byte_depth_cycle_and_unicode_limits_are_enforced_before_copying():
    cyclic = {}; cyclic['self'] = cyclic
    nested = {}; cursor = nested
    for _ in range(25):
        cursor['next'] = {}; cursor = cursor['next']
    for state in (cyclic, nested, {'text': 'µ' * (MAX_BYTES // 2)}, {'text': '\ud800'},
                  {'rows': [0] * 17_000}):
        with pytest.raises(InvalidDocument): document(saved(state))


def test_schema_mismatch_does_not_call_restore_and_roundtrip_loss_is_refused():
    calls = []
    binding = Binding(schema='example.animation', version=1, capture=lambda: {}, restore=calls.append)
    with pytest.raises(InvalidDocument, match='schema version'):
        binding.restore({**saved(), 'version': 2})
    assert not calls
    with pytest.raises(InvalidDocument, match='differs'):
        binding.restore(saved())
    assert len(calls) == 1
    with pytest.raises(InvalidDocument, match='only once'):
        binding.restore(saved())


def test_callback_failure_has_no_automatic_retry_or_false_snapshot():
    calls = []
    def broken(state):
        calls.append(state)
        raise ValueError('Partial private state')
    binding = Binding(schema='example.animation', version=1, capture=lambda: {}, restore=broken)
    with pytest.raises(CallbackFailed): binding.restore(saved())
    with pytest.raises(InvalidDocument, match='only once'): binding.restore(saved())
    assert len(calls) == 1
    def fatal(): raise MemoryError('Controlled failure without exhaustion')
    binding = Binding(schema='example.animation', version=1, capture=fatal, restore=lambda _: None)
    with pytest.raises(BoundaryFailed): binding.capture()


@pytest.mark.parametrize('changed', [True, 1.0])
def test_roundtrip_cannot_silently_change_json_scalar_types(changed):
    binding = Binding(schema='example.animation', version=1,
                      capture=lambda: {'selected': changed}, restore=lambda _: None)
    with pytest.raises(InvalidDocument, match='differs'):
        binding.restore(saved({'selected': 1}))


def test_closed_and_cross_thread_callbacks_are_not_invoked():
    calls = []; errors = []
    binding = Binding(schema='example.animation', version=1, capture=lambda: calls.append(1), restore=lambda _: None)
    def other():
        try: binding.capture()
        except InvalidDocument as error: errors.append(error)
    thread = threading.Thread(target=other); thread.start(); thread.join()
    assert len(errors) == 1 and not calls
    binding.close()
    with pytest.raises(InvalidDocument): binding.capture()
    assert not calls


def test_host_document_binding_is_explicit_once_and_only_during_factory():
    from tests.test_scientific_native_host import host
    from env_worker.scientific_host import ChangeRefused
    session = host(); session.tool_document = None
    arguments = dict(schema='example.animation', version=1, capture=lambda: {}, restore=lambda _: None)
    with pytest.raises(ChangeRefused): session.bind_document(**arguments)
    session._binding_contributions = True
    session.bind_document(**arguments)
    assert session.tool_document.capture() == saved({})
    with pytest.raises(ChangeRefused): session.bind_document(**arguments)


@pytest.mark.parametrize('change', ['add', 'remove', 'reorder', 'rebind'])
def test_capture_callback_cannot_change_complete_layer_membership_or_bindings(change, monkeypatch):
    from types import SimpleNamespace
    from tests.test_scientific_native_host import host
    session = host(); session._files = {}
    session.viewer = object()
    monkeypatch.setattr('env_worker.scientific_tool_document.scientific_identity', lambda _: [])
    first, second, added = object(), object(), object()
    session._layers = [('a'*32, first), ('b'*32, second)]
    keys = {first: 'first', second: 'second', added: 'third'}
    session._layer_rows = lambda: None
    session.view_bindings = SimpleNamespace(key_for=keys.get)
    def capture():
        if change == 'add': session._layers.append(('c'*32, added))
        elif change == 'remove': session._layers.pop()
        elif change == 'reorder': session._layers.reverse()
        else: keys[first] = 'rebound'
        return {}
    session.tool_document = Binding(schema='example.animation', version=1, capture=capture, restore=lambda _: None)
    session.native_panel = SimpleNamespace(current=object(), invalidate=lambda: setattr(session.native_panel, 'current', None))
    rows = [{'layer': letter*32, 'input': f'layer{index}', 'metadata': {},
             'meaning': {'semantic': 'intensity', 'axes': ['y','x']}, 'components': []}
            for index, letter in enumerate('ab')]
    with pytest.raises((InvalidDocument, ValueError)):
        session.stage_outputs(rows, lambda *_: pytest.fail('No bytes may be sent'), tool=True)
    assert session._frame is None and session.native_panel.current is None


def test_recipe_settings_are_data_only_and_do_not_replace_tool_state():
    from env_worker.scientific_tool_document import snapshot
    layers = [{'input': 'image'}]
    original = {'document': saved({'selection': 'image'}), 'bindings': {'image_layer': 'image'},
                'recipe': saved({'threshold': 0.4})}
    captured = snapshot(original, layers)
    original['recipe']['state']['threshold'] = 0.8
    assert captured['recipe']['state']['threshold'] == 0.4
    assert captured['document']['state'] == {'selection': 'image'}
    with pytest.raises(InvalidDocument):
        snapshot({**captured, 'recipe': saved({'execute': object()})}, layers)


def _reopened_rgb_tool(monkeypatch):
    """A reopened tool whose retained RGB layer is stored with a trailing color axis."""
    from contextlib import contextmanager
    from types import SimpleNamespace
    from tests.test_scientific_native_host import host
    session = host(); session._files = {}
    session.viewer = object()
    monkeypatch.setattr('env_worker.scientific_tool_document.scientific_identity', lambda _: [])
    class Layer:
        rgb = True
    flows = Layer()
    session._layers = [('a'*32, flows)]
    session._layer_rows = lambda: None
    session.view_bindings = SimpleNamespace(key_for={flows: 'cellpose_result_0'}.get, check_meaning=lambda *_: None)
    source = SimpleNamespace(descriptor={'semantic': 'intensity'}, axes=('y', 'x', 'color'))
    session.editable = SimpleNamespace(input_for=lambda layer: 'layer1_output' if layer is flows else None,
                                       inputs={'layer1_output': source})
    session.describe_tool_layer = lambda layer: {'type': 'image'}
    session.tool_document = Binding(schema='example.animation', version=1, capture=lambda: {}, restore=lambda _: None)
    captured = []
    @contextmanager
    def capture(viewer, layer, metadata, meaning, grant, snapshot):
        captured.append(meaning)
        yield []
    monkeypatch.setattr('env_worker.scientific_outputs.capture', capture)
    monkeypatch.setattr('env_worker.native_edits.bind_output', lambda edit, exchange: SimpleNamespace(write=lambda *_: None))
    return session, captured


def test_reopened_rgb_tool_layer_saves_with_its_displayed_coordinate_axes(monkeypatch):
    session, captured = _reopened_rgb_tool(monkeypatch)
    meaning = {'semantic': 'intensity', 'axes': ['y', 'x']}
    rows = [{'layer': 'a'*32, 'input': 'layer1_output', 'metadata': {'type': 'image'}, 'meaning': meaning,
             'components': [{'name': 'output', 'edit': {'token': 't'*32}}]}]
    result = session.stage_outputs(rows, lambda *_: None, tool=True)
    assert captured == [meaning]
    assert result['tool']['bindings'] == {'cellpose_result_0': 'layer1_output'}


def test_reopened_rgb_tool_layer_still_refuses_a_changed_meaning(monkeypatch):
    session, captured = _reopened_rgb_tool(monkeypatch)
    for meaning in ({'semantic': 'intensity', 'axes': ['y', 'x', 'color']}, {'semantic': 'intensity', 'axes': ['x', 'y']}):
        rows = [{'layer': 'a'*32, 'input': 'layer1_output', 'metadata': {'type': 'image'}, 'meaning': meaning,
                 'components': [{'name': 'output', 'edit': {'token': 't'*32}}]}]
        with pytest.raises(ValueError):
            session.stage_outputs(rows, lambda *_: pytest.fail('No bytes may be sent'), tool=True)
    assert captured == []
