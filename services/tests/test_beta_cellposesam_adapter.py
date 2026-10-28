"""The beta Cellpose-SAM adapter's saved run groups; the pinned plugin itself is not imported here."""
import importlib.util
from pathlib import Path

import pytest

ADAPTER = Path(__file__).resolve().parents[2] / 'examples' / 'beta-scientific-tools' / 'cellposesam-panel' / 'adapter.py'


def adapter():
    spec = importlib.util.spec_from_file_location('cellposesam_adapter', ADAPTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Proxy:
    """napari hands plugin functions PublicOnlyProxy layers; wrapt exposes the original as __wrapped__."""
    def __init__(self, layer):
        self.__wrapped__ = layer


def test_groups_recorded_through_the_plugin_proxy_keep_their_bound_layers():
    image, flows, masks = object(), object(), object()
    bindings = {'cellpose_input': image, 'cellpose_result_0': flows, 'cellpose_result_1': masks}
    groups = adapter().saved_groups([[Proxy(flows), Proxy(masks)]], bindings, [image, flows, masks])
    assert groups == [['cellpose_result_0', 'cellpose_result_1']]


def test_removed_or_unbound_layers_are_not_claimed_by_a_saved_group():
    image, flows, masks, stray = object(), object(), object(), object()
    bindings = {'cellpose_input': image, 'cellpose_result_0': flows, 'cellpose_result_1': masks}
    groups = adapter().saved_groups([[Proxy(flows), masks, Proxy(stray)]], bindings, [image, masks, stray])
    assert groups == [['cellpose_result_1']]


class Field:
    def __init__(self, value):
        self.value = value


class Button:
    def __init__(self, enabled):
        self.enabled = enabled


class Panel:
    """The pinned plugin's FunctionGui fields the adapter reads; calling it would run segmentation."""
    def __init__(self, **settings):
        values = {'diameter': '30', 'cellprob_threshold': 0.0, 'flow_threshold': 0.4,
                  'min_size': 15, 'resample_dynamics': False, **settings}
        for name, value in values.items():
            setattr(self, name, Field(value))
        self.call_button = Button(True)
        self.compute_masks_button = Button(True)
        self.runs = []

    def __call__(self, *args, **kwargs):
        self.runs.append((args, kwargs))
        raise AssertionError('Segmentation ran')


def session(next_output, **settings):
    """One open session: the input and four Cellpose outputs from the first run, as the plugin records them."""
    image, *outputs = (object() for _ in range(5))
    bindings = {'cellpose_input': image, **{f'cellpose_result_{n}': layer for n, layer in enumerate(outputs)}}
    panel = Panel(**settings)
    panel.cellpose_layers = [[Proxy(layer) for layer in outputs]]
    return panel, bindings, [image, *outputs], next_output


def binding(module, panel, bindings, current, counter):
    """The host's real tool-document binding around the adapter's own capture and restore."""
    from env_worker.scientific_tool_document import Binding
    def capture():
        return module.capture_state(panel, bindings, current, 'cellpose_input', counter[0])
    def restore(state):
        counter[0] = module.restore_state(panel, state, bindings, 'cellpose_input', counter[0])
    return Binding(schema='hooke.cellposesam.2d', version=1, capture=capture, restore=restore)


def refusal(call):
    """The reason a host binding refused: its own validation, or the adapter's error it wraps."""
    from env_worker.scientific_native_panel import CallbackFailed
    from env_worker.scientific_tool_document import InvalidDocument
    with pytest.raises((InvalidDocument, CallbackFailed)) as refused:
        call()
    return str(refused.value.__cause__ or refused.value)


def reopened(module, saved_bindings):
    """A new session restoring the saved layers under their saved keys; the plugin starts with defaults."""
    bindings = {key: object() for key in saved_bindings}
    panel = Panel()
    counter = [max(int(key.rsplit('_', 1)[1]) + 1 for key in bindings if key.startswith('cellpose_result_'))]
    return panel, bindings, counter


def test_saved_state_reopens_exactly_without_running_segmentation():
    module = adapter()
    panel, bindings, current, next_output = session(4, diameter='17.5', cellprob_threshold=-1e-05,
                                                    flow_threshold=0.85, min_size=40, resample_dynamics=True)
    saved = binding(module, panel, bindings, current, [next_output]).capture()
    assert saved['state']['groups'] == [['cellpose_result_0', 'cellpose_result_1', 'cellpose_result_2', 'cellpose_result_3']]
    assert saved['state']['settings'] == {'diameter': '17.5', 'cellprob_threshold': -1e-05, 'flow_threshold': 0.85,
                                          'min_size': 40, 'resample_dynamics': True}
    new_panel, new_bindings, counter = reopened(module, bindings)
    binding(module, new_panel, new_bindings, list(new_bindings.values()), counter).restore(saved)
    assert {name: getattr(new_panel, name).value for name in module.SETTINGS} == saved['state']['settings']
    assert new_panel.cellpose_layers == [[new_bindings[f'cellpose_result_{n}'] for n in range(4)]]
    assert counter == [4]
    assert new_panel.compute_masks_button.enabled is False and new_panel.runs == []


@pytest.mark.parametrize('next_output', [64, 65, 67, 4096])
def test_an_output_counter_past_64_saves_and_reopens(next_output):
    # Deleted outputs keep advancing the counter, so a long project passes 64 while the
    # host still binds at most 64 layers per session. Whatever saves must reopen.
    module = adapter()
    panel, bindings, current, _ = session(next_output)
    saved = binding(module, panel, bindings, current, [next_output]).capture()
    new_panel, new_bindings, counter = reopened(module, bindings)
    binding(module, new_panel, new_bindings, list(new_bindings.values()), counter).restore(saved)
    assert counter == [next_output] and new_panel.runs == []


def test_capture_and_restore_refuse_the_same_unsupported_counter():
    module = adapter()
    panel, bindings, current, _ = session(2**53)
    assert 'exact JSON value' in refusal(binding(module, panel, bindings, current, [2**53]).capture)
    panel, bindings, current, _ = session(4)
    saved = binding(module, panel, bindings, current, [4]).capture()
    for counter, reason in ((2**53, 'exact JSON value'), (3, 'unsupported'), ('4', 'unsupported'), (4.0, 'unsupported')):
        new_panel, new_bindings, start = reopened(module, bindings)
        state = {**saved, 'state': {**saved['state'], 'nextOutput': counter}}
        assert reason in refusal(lambda: binding(module, new_panel, new_bindings, list(new_bindings.values()), start).restore(state))
        assert new_panel.runs == [] and new_panel.compute_masks_button.enabled is True


def test_capture_refuses_while_segmentation_is_running():
    module = adapter()
    panel, bindings, current, next_output = session(4)
    panel.call_button.enabled = False
    assert 'Wait for segmentation' in refusal(binding(module, panel, bindings, current, [next_output]).capture)


@pytest.mark.parametrize('advertised', [None, 1, True, '2'])
def test_old_host_refuses_coordinate_candidate_before_plugin_import(advertised, monkeypatch):
    from types import SimpleNamespace
    import builtins
    module = adapter()
    context = SimpleNamespace(bind_recipe=lambda **_:None, bind_view_output=lambda **_:None)
    if advertised is not None: context.output_binding_version = advertised
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.split('.')[0] in ('magicgui','qtpy','napari','napari_cellposesam','cellpose'):
            pytest.fail('Imported plugin before refusing unsupported host')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    with pytest.raises(ValueError, match='output.*version 2'):
        module.open_host(context)


@pytest.mark.parametrize('role', ['masks', 'outlines', 'cellprob', 'flows', 'unknown'])
def test_pinned_result_declares_actual_input_after_full_geometry_copy(role):
    from types import SimpleNamespace
    import numpy as np
    module = adapter()
    image = SimpleNamespace(name='Specimen', scale=(2,3), translate=(4,5), rotate=7, shear=(.2,),
        affine=SimpleNamespace(affine_matrix=np.array([[1.,.1,6.],[0.,1.,7.],[0.,0.,1.]])),
        axis_labels=('y','x'), units=('micrometer','micrometer'))
    layer = SimpleNamespace(name=f'Specimen_cp_{role}_0')
    calls = []
    def bind(**call):
        if role != 'unknown':
            assert layer.scale == image.scale and layer.translate == image.translate
            assert layer.rotate == image.rotate and layer.shear == image.shear
            assert np.array_equal(layer.affine, image.affine.affine_matrix)
            assert layer.affine is not image.affine.affine_matrix
            assert layer.axis_labels == image.axis_labels and layer.units == image.units
        calls.append(call)
    module.bind_result(SimpleNamespace(bind_view_output=bind), image, layer, 'result')
    if role == 'unknown':
        assert calls == [{'version':1, 'layer':layer, 'key':'result', 'meaning':None}]
        assert not hasattr(layer, 'scale')
    else:
        assert calls == [{'version':2, 'layer':layer, 'key':'result', 'coordinates_from':image,
            'meaning':{'semantic':'instance-labels' if role in ('masks','outlines') else 'intensity',
                       'axes':['y','x']}}]


class Resource:
    """A saved native input as the host grants it: its meaning, shape and dtype, never its values here."""
    def __init__(self, semantic, shape=(64, 64), dtype='uint8'):
        self.descriptor, self.shape, self.dtype = {'semantic': semantic}, shape, dtype


def opening(inputs):
    """The host's opening context around the adapter's input choice, with its real refusal contract."""
    from types import SimpleNamespace
    from env_worker.scientific_host import Context
    return SimpleNamespace(inputs=inputs, refuse_input=Context(None, {}, None, None).refuse_input)


def test_retained_image_and_its_labels_give_the_panel_the_image():
    # Retain labels for tools keeps the original image beside the corrected labels made from it (population_native);
    # the panel segments the image and leaves the labels in their saved revision.
    image, labels = Resource('intensity'), Resource('instance-labels', dtype='uint16')
    assert adapter().retained_image(opening({'image': image, 'labels': labels})) == ('image', image)


@pytest.mark.parametrize('inputs,says', [
    ({'labels': Resource('instance-labels', dtype='uint16')}, 'this input has none'),
    ({'a': Resource('intensity'), 'b': Resource('intensity')}, 'this input has 2 (a, b)'),
    ({'image': Resource('intensity', shape=(4, 64, 64))}, 'scalar 2D intensity image up to 1 MiB'),
    ({'image': Resource('intensity', shape=(2048, 2048))}, 'scalar 2D intensity image up to 1 MiB'),
])
def test_an_input_the_panel_cannot_segment_is_refused_in_its_own_words(inputs, says):
    from env_worker.scientific_input_refusal import InputRefused
    with pytest.raises(InputRefused) as refused:
        adapter().retained_image(opening(inputs))
    assert says in str(refused.value) and refused.value.next_step.startswith('Open a ')
