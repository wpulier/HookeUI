"""Actual pinned magicgui callbacks, run by the confined integration test."""
import importlib.util
import json
import sys
import threading

spec = importlib.util.spec_from_file_location('scientific_controls', sys.argv[1])
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
runtime_spec = importlib.util.spec_from_file_location('napari_cgl', sys.argv[2])
runtime = importlib.util.module_from_spec(runtime_spec)
sys.modules[runtime_spec.name] = runtime
runtime_spec.loader.exec_module(runtime)
runtime._runtime(runtime.REFERENCE_REVISION)

import numpy as np
from magicgui import magicgui, widgets
from napari.components import ViewerModel

viewer = ViewerModel()
first = viewer.add_image(np.zeros((6, 8)), name='first')
second = viewer.add_image(np.ones((6, 8)), name='second')
calls = []

def choices(widget):
    return [(layer.name, layer) for layer in viewer.layers]

@magicgui(call_button='Apply', layer={'choices': choices}, opacity={'min': 0., 'max': 1.})
def control(layer, opacity: float = 1.):
    calls.append(layer.name)
    layer.opacity = opacity

viewer.layers.events.inserted.connect(control.layer.reset_choices)
viewer.layers.events.removed.connect(control.layer.reset_choices)
panel = bridge.Panel(control)

def row(state, label):
    return next(item for item in state['controls'] if item['label'] == label)

def command(state, label, op, **value):
    return {'panel': state['panel'], 'revision': state['revision'], 'control': row(state, label)['id'], 'op': op, **value}

def refused(call, cls):
    try:
        call()
    except cls:
        return
    raise AssertionError('request unexpectedly accepted')

try:
    state = panel.snapshot()
    assert panel.snapshot() == state
    assert all(item['visible'] for item in state['controls'])
    assert 'Image' not in json.dumps(state) and 'array' not in json.dumps(state)
    options = row(state, 'layer')['choices']
    selected = next(item['id'] for item in options if item['label'] == 'second')
    state = panel.dispatch(command(state, 'layer', 'set', value=selected))
    assert control.layer.value is second
    apply_opacity = panel.prepare(command(state, 'opacity', 'set', value=.35))
    assert control.opacity.value == 1. and not calls
    state = apply_opacity()
    refused(apply_opacity, bridge.Stale)
    invoke = command(state, 'Apply', 'invoke')
    state = panel.dispatch(invoke)
    assert second.opacity == .35 and first.opacity == 1 and calls == ['second']
    refused(lambda: panel.dispatch(invoke), bridge.Stale)
    assert calls == ['second']
    before = state
    viewer.layers.remove(second)
    refused(lambda: panel.dispatch(command(before, 'layer', 'set', value=selected)), bridge.Stale)
    state = panel.snapshot()
    assert [item['label'] for item in row(state, 'layer')['choices']] == ['first']
    assert row(before, 'layer')['choices'][0]['id'] == row(state, 'layer')['choices'][0]['id']
    refused(lambda: panel.dispatch(command(state, 'layer', 'set', value=selected)), bridge.Stale)
    refused(lambda: panel.dispatch(command(state, 'opacity', 'set', value=1.1)), bridge.Unsupported)
    refused(lambda: panel.dispatch(command(state, 'opacity', 'set', value=True)), bridge.Unsupported)
    assert control.opacity.value == .35
    control.opacity.visible = False
    state = panel.snapshot()
    assert not row(state, 'opacity')['visible']
    refused(lambda: panel.dispatch(command(state, 'opacity', 'set', value=.4)), bridge.Stale)
    control.opacity.visible = True
    control.opacity.enabled = False
    state = panel.snapshot()
    assert not row(state, 'opacity')['enabled']
    refused(lambda: panel.dispatch(command(state, 'opacity', 'set', value=.4)), bridge.Stale)
    control.opacity.enabled = True
    state = panel.snapshot()
    stale_action = panel.prepare(command(state, 'Apply', 'invoke'))
    panel.invalidate()
    refused(stale_action, bridge.Stale)
    refused(lambda: panel.dispatch(command(state, 'Apply', 'invoke')), bridge.Stale)
    errors = []
    def other_thread():
        try:
            panel.snapshot()
        except bridge.Unsupported:
            errors.append('refused')
        refused(lambda: bridge.Panel(control), bridge.Unsupported)
    thread = threading.Thread(target=other_thread)
    thread.start()
    thread.join()
    assert errors == ['refused']
    @magicgui(call_button='Fail')
    def failing():
        raise RuntimeError('controlled callback failure')
    failed_panel = bridge.Panel(failing)
    failed_state = failed_panel.snapshot()
    failed_request = command(failed_state, 'Fail', 'invoke')
    refused(lambda: failed_panel.dispatch(failed_request), Exception)
    refused(lambda: failed_panel.dispatch(failed_request), bridge.Stale)
    assert failed_panel.snapshot()['revision'] > failed_state['revision']
    failing.close()
    unsupported = widgets.Container(widgets=[widgets.FileEdit()])
    refused(lambda: bridge.Panel(unsupported).snapshot(), bridge.Unsupported)
    unsupported.close()
    for values in [[('one', 1), ('again', 1)], [('one', 1), ('again', True)], [('one', first), ('again', first)]]:
        duplicate = widgets.Container(widgets=[widgets.ComboBox(choices=values)])
        refused(lambda: bridge.Panel(duplicate).snapshot(), bridge.Unsupported)
        duplicate.close()
    @magicgui
    async def asynchronous():
        return 1
    refused(lambda: bridge.Panel(asynchronous), bridge.Unsupported)
    asynchronous.close()
    readonly = widgets.TextEdit(value='retained note')
    readonly.read_only = True
    extra = widgets.Container(widgets=[readonly, widgets.CheckBox(value=False), widgets.LineEdit(value='name')])
    extra_panel = bridge.Panel(extra)
    extra_state = extra_panel.snapshot()
    assert not extra_state['controls'][0]['enabled']
    refused(lambda: extra_panel.dispatch({'panel': extra_state['panel'], 'revision': extra_state['revision'],
                                          'control': extra_state['controls'][0]['id'], 'op': 'set', 'value': 'changed'}), bridge.Stale)
    assert readonly.value == 'retained note'
    extra.close()
    state = panel.dispatch(command(panel.snapshot(), 'Apply', 'invoke'))
    assert calls == ['second', 'first'] and first.opacity == .35
    panel.close()
    refused(panel.snapshot, bridge.Stale)
finally:
    viewer.layers.events.inserted.disconnect(control.layer.reset_choices)
    viewer.layers.events.removed.disconnect(control.layer.reset_choices)
    control.close()
print(json.dumps({'ok': True, 'callbacks': calls, 'scientificValuesUnchanged': bool(np.all(first.data == 0))}))
