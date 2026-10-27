"""Actual pinned upstream interaction in an owned sandbox, with synthetic native data."""
from pathlib import Path
import sys

sys.path.insert(0, sys.argv[1])
from env_worker import native_regions
from env_worker.server import read_message, write_message
from env_worker.scientific_controls import Stale
from env_worker.scientific_host import ReviewSession, ChangeRefused

output = sys.stdout.buffer
sys.stdout = sys.stderr

def request(header):
    write_message(output, header)
    return read_message(sys.stdin.buffer, max_header=16384, max_payload=1 << 20)

header, payload = read_message(sys.stdin.buffer, max_header=32768, max_payload=0)
edit_specification = header['edit']
resources = native_regions.bind(header['inputs'], request)

import numpy as np
from magicgui import magicgui

data = resources['volume'].read([[0, n] for n in resources['volume'].shape])
original = data.tobytes()

def factory(context):
    assert context.inputs['volume'].reference == resources['volume'].reference
    layer = context.viewer.add_image(data, name='volume', scale=(3, 2, 1), translate=(9, 8, 7), rendering='mip')
    context.bind_editable(layer=layer, input='volume')
    context.bind_view(layer=layer, key='raw-volume')
    from qtpy.QtWidgets import QWidget, QVBoxLayout, QPushButton
    widget = QWidget(); layout = QVBoxLayout(widget)
    button = QPushButton('Native opacity'); layout.addWidget(button)
    button.clicked.connect(lambda: setattr(layer, 'opacity', .35))
    context.bind_native_panel(widget)
    @magicgui(call_button='Apply opacity', opacity={'min': 0., 'max': 1.})
    def control(opacity: float = .4):
        context.viewer.layers['volume'].opacity = opacity
    return control

host = ReviewSession(factory, resources)
try:
    # Native controls can be opened before the image. Queued changes to that
    # same scientific layer must invalidate the panel even with identical pixels.
    from qtpy.QtCore import QTimer
    native, _ = host.render_native_panel(320, 240)
    assert any(events is host.viewer.layers[0].events for events in host._connections), 'Native-first panel omitted its scientific layer subscription'
    row = next(row for row in native['accessibility'] if row['name'] == 'Native opacity')
    x,y,w,h = row['rect']
    QTimer.singleShot(0, lambda: setattr(host.viewer.layers[0], 'opacity', .7))
    try:
        host.change_native_panel({'panel': native['panel'], 'frame': native['frame'],
            'op':'click', 'value':[x+w//2,y+h//2]})
    except ChangeRefused: pass
    else: raise AssertionError('Native-only preview admitted a stale layer context')
    assert host.viewer.layers[0].opacity == .7
    host.viewer.layers[0].opacity = 1
    first, pixels = host.render(256, 192)
    assert len(pixels) == 256 * 192 * 4 and len(set(pixels)) > 10
    assert first['dimensions']['ndisplay'] == 2 and first['privatePreview']
    identity = first['layers'][0]['id']
    assert first['layers'][0]['viewKey'] == 'raw-volume'
    centre = host.data_position(first['frame'], identity, [96, 128])
    expected = host.viewer.layers[0].world_to_data(host.viewer.dims.point[:-2] + host.viewer.camera.center[-2:])
    assert np.allclose(centre, expected), (centre, expected)
    right = host.data_position(first['frame'], identity, [96, 129])
    down = host.data_position(first['frame'], identity, [97, 128])
    assert np.isclose(right[-1] - centre[-1], 1 / first['camera']['zoom'])
    assert np.isclose(down[-2] - centre[-2], 1 / first['camera']['zoom'] / 2)
    button = next(item for item in first['controls']['controls'] if item['kind'] == 'button')
    control_request = {'panel': first['controls']['panel'], 'revision': first['controls']['revision'],
                       'control': button['id'], 'op': 'invoke'}
    change = {'frame': first['frame'], 'operation': 'controls', 'value': control_request}
    host.change(change)
    assert host.viewer.layers[0].opacity == .4
    try:
        host.change(change)
    except ChangeRefused:
        pass
    else:
        raise AssertionError('old frame replayed a callback')
    second, changed = host.render(256, 192)
    assert second['dirty'] and pixels != changed
    host.change({'frame': second['frame'], 'operation': 'dimensions',
                 'value': {'order': [0, 1, 2], 'ndisplay': 3, 'steps': [2, 10, 12]}})
    third, volume = host.render(256, 192)
    assert third['dimensions']['ndisplay'] == 3
    host.change({'frame': third['frame'], 'operation': 'camera',
                 'value': {'center': third['camera']['center'], 'zoom': third['camera']['zoom'], 'angles': [25, 40, 15]}})
    fourth, turned = host.render(256, 192)
    assert volume != turned
    host.viewer.layers[0].opacity = .7
    try:
        host.data_position(fourth['frame'], identity, [96, 128])
    except Stale:
        pass
    else:
        raise AssertionError('changed layer did not revoke its frame')
    fifth, _ = host.render(256, 192)
    host.change({'frame': fifth['frame'], 'operation': 'dimensions',
                 'value': {'order': [2, 0, 1], 'ndisplay': 2, 'steps': [2, 10, 12]}})
    sixth, _ = host.render(256, 192)
    assert sixth['dimensions']['displayed'] == [0, 1]
    from env_worker.scientific_view_preferences import from_frame
    for mode in ('translucent', 'additive', 'iso', 'mip', 'minip', 'attenuated_mip', 'average'):
        appearance = {**sixth['layers'][0]['imageAppearance'], 'gamma': 1.4, 'rendering': mode,
                      'contrastLimits': [30, 2000], 'isoThreshold': 900, 'attenuation': .25}
        host.change({'frame': sixth['frame'], 'operation': 'image-presentation',
                     'value': {'layer': identity, 'settings': appearance}})
        sixth, _ = host.render(256, 192)
        assert sixth['layers'][0]['imageAppearance'] == appearance
        assert data.tobytes() == original
    # Nested plane events must invalidate rendered authority even when Python
    # or a plugin changes the plane directly, outside Hooke's command path.
    host.viewer.layers[0].plane.position = (3, 11, 13)
    try:
        host.change({'frame': sixth['frame'], 'operation': 'image-plane',
                     'value': {'layer': identity, 'settings': sixth['layers'][0]['imagePlane']}})
    except ChangeRefused:
        pass
    else:
        raise AssertionError('a nested image-plane event retained stale frame authority')
    assert tuple(host.viewer.layers[0].plane.position) == (3, 11, 13)
    sixth, _ = host.render(256, 192)
    # Clipping list and nested plane changes have their own upstream emitters.
    host.viewer.layers[0].experimental_clipping_planes = [
        {'position':(2,11,13), 'normal':(1,0,0), 'enabled':True}]
    try:
        host.data_position(sixth['frame'], identity, [96,128])
    except Stale:
        pass
    else:
        raise AssertionError('clipping list changes retained stale frame authority')
    sixth, _ = host.render(256,192)
    host.viewer.layers[0].experimental_clipping_planes[0].enabled = False
    try:
        host.data_position(sixth['frame'], identity, [96,128])
    except Stale:
        pass
    else:
        raise AssertionError('nested clipping changes retained stale frame authority')
    sixth, _ = host.render(256,192)
    remembered = from_frame(sixth)
    assert remembered['schema'] == 12
    assert remembered['dimensions']['margins'] == {'left':[0,0,0], 'right':[0,0,0]}
    assert remembered['layers'][0]['imageProjection'] == 'mean'
    assert remembered['layers'][0]['imageColormap']['name'] == host.viewer.layers[0].colormap.name
    point = host.data_position(sixth['frame'], identity, [96, 128])
    world = list(host.viewer.dims.point)
    world[0], world[1] = host.viewer.camera.center[-2:]
    assert np.allclose(point, host.viewer.layers[0].world_to_data(world))
    shifted = host.data_position(sixth['frame'], identity, [102, 138])
    world[0] += 6 / sixth['camera']['zoom']
    world[1] += 10 / sixth['camera']['zoom']
    assert np.allclose(shifted, host.viewer.layers[0].world_to_data(world))
    host.viewer.window._qt_viewer.canvas.size = (128, 128)
    try:
        host.data_position(sixth['frame'], identity, [96, 128])
    except Stale:
        pass
    else:
        raise AssertionError('resized canvas retained its previous frame')
    latest, _ = host.render(256, 192)
    host.change({'frame':latest['frame'], 'operation':'layer-presentation',
                 'value':{'layer':identity,'visible':False,'opacity':.25}})
    hidden, hidden_pixels = host.render(256,192)
    assert not hidden['layers'][0]['visible'] and hidden['layers'][0]['opacity'] == .25
    host.change({'frame':hidden['frame'], 'operation':'layer-presentation',
                 'value':{'layer':identity,'visible':True,'opacity':.75}})
    visible, visible_pixels = host.render(256,192)
    assert visible['layers'][0]['visible'] and visible['layers'][0]['opacity'] == .75
    assert hidden_pixels != visible_pixels
    host.change({'frame': visible['frame'], 'operation': 'restore-view', 'value': remembered})
    restored, _ = host.render(256, 192)
    assert from_frame(restored) == remembered
    assert data.tobytes() == original
    host.viewer.layers[0].data = data.copy()
    host.viewer.layers[0].data[0, 0, 0] = 65535
    exporting, _ = host.render(256, 192)
    assert exporting['layers'][0]['editableInput'] == 'volume'
    from env_worker.native_edits import FORMAT
    staged = np.empty_like(data)
    specification = edit_specification
    count = 0
    def accept(header, payload):
        global count
        assert header['op'] == 'write-native-chunk'
        index = header['index']
        slices = tuple(slice(i*c, min((i+1)*c,n)) for i,c,n in zip(index, specification['chunks'], data.shape))
        staged[slices] = np.frombuffer(payload, dtype=header['dtype']).reshape(header['shape'])
        count += 1
        write_message(output, header, payload)
        return read_message(sys.stdin.buffer, max_header=16384, max_payload=0)
    result = host.stage_layer(exporting['frame'], identity, 'volume', specification, accept)
    assert result == {'ok': True, 'staged': specification['token']} and count == 24
    expected = data.copy(); expected[0, 0, 0] = 65535
    assert np.array_equal(staged, expected) and data.tobytes() == original
finally:
    host.close()
    host.close()

# Automatic contrast is derived from the current slice, including after reopening.
auto = ReviewSession(factory, resources)
try:
    frame, _ = auto.render(256,192)
    settings = {**frame['layers'][0]['imageAppearance'], 'contrastLimits':[30,2000], 'autoContrast':True}
    auto.change({'frame':frame['frame'],'operation':'image-presentation',
                 'value':{'layer':frame['layers'][0]['id'],'settings':settings}})
    automatic, _ = auto.render(256,192)
    auto_saved = from_frame(automatic)
    assert auto_saved['layers'][0]['imageAppearance']['autoContrast']
    assert auto_saved['layers'][0]['imageAppearance']['contrastLimits'] != [30,2000]
finally:
    auto.close()
auto = ReviewSession(factory, resources)
try:
    frame, _ = auto.render(256,192)
    auto.change({'frame':frame['frame'],'operation':'restore-view','value':auto_saved})
    restored, _ = auto.render(256,192)
    assert from_frame(restored) == auto_saved
    assert auto.viewer.layers[0].data.tobytes() == original
finally:
    auto.close()

# Schema-1 views did not record appearance. A different saved slice must
# recalculate an integration's automatic contrast rather than kill restoration.
def automatic_factory(context):
    panel = factory(context)
    context.viewer.layers[0].auto_contrast = True
    return panel
legacy_host = ReviewSession(automatic_factory, resources)
try:
    first, _ = legacy_host.render(256,192)
    old_view = from_frame(first)
    old_view['schema'] = 1
    old_view.pop('grid')
    old_view['layers'][0].pop('imageAppearance')
    old_view['layers'][0].pop('blending')
    old_view['layers'][0].pop('imagePlane')
    old_view['layers'][0].pop('clippingPlanes')
    old_view['layers'][0].pop('imageSampling')
    old_view['layers'][0].pop('imageColormap')
    old_view['layers'][0].pop('imageProjection')
    old_view['dimensions'].pop('margins')
    old_view['dimensions']['point'][0] = first['dimensions']['ranges'][0][0]
    legacy_host.change({'frame':first['frame'],'operation':'restore-view','value':old_view})
    restored, _ = legacy_host.render(256,192)
    assert restored['dimensions']['point'] == old_view['dimensions']['point']
    assert restored['dimensions']['margins'] == first['dimensions']['margins']
    assert restored['layers'][0]['imageProjection'] == first['layers'][0]['imageProjection']
    assert restored['layers'][0]['imageAppearance']['autoContrast']
    assert restored['layers'][0]['imageAppearance']['contrastLimits'] != first['layers'][0]['imageAppearance']['contrastLimits']
    assert legacy_host.viewer.layers[0].data.tobytes() == original
finally:
    legacy_host.close()

# Mixed-layer stacking uses current worker identities, keeps native values and
# persists through stable view bindings in a fresh viewer.
stack_values = np.arange(24*28, dtype=np.uint16).reshape(24,28)
stack_points = np.array([[3.,4.],[12.,18.]])
def stack_factory(context):
    for name,colour in [('lower','red'),('upper','blue')]:
        layer=context.viewer.add_image(stack_values.copy(),name=name,colormap=colour,blending='opaque')
        context.bind_view(layer=layer,key=name)
    layer=context.viewer.add_points(stack_points.copy(),name='markers',size=3)
    context.bind_view(layer=layer,key='markers')
    @magicgui(call_button='Inspect')
    def control():
        pass
    return control
stack = ReviewSession(stack_factory,{})
try:
    baseline, original_pixels=stack.render(256,192)
    ids=[row['id'] for row in baseline['layers']]
    wanted=[ids[1],ids[0],ids[2]]
    stack.change({'frame':baseline['frame'],'operation':'layer-order','value':wanted})
    ordered,ordered_pixels=stack.render(256,192)
    assert [row['id'] for row in ordered['layers']]==wanted
    assert ordered_pixels!=original_pixels
    from env_worker.scientific_view_preferences import from_frame
    stack_preferences=from_frame(ordered)
    for layer in stack.viewer.layers:
        assert np.array_equal(layer.data,stack_points if layer.name=='markers' else stack_values)
    try:
        stack.change({'frame':baseline['frame'],'operation':'layer-order','value':ids})
    except ChangeRefused:
        pass
    else:
        raise AssertionError('old frame reordered scientific layers')
finally:
    stack.close()
stack = ReviewSession(stack_factory,{})
try:
    baseline,_=stack.render(256,192)
    stack.change({'frame':baseline['frame'],'operation':'restore-view','value':stack_preferences})
    restored,restored_pixels=stack.render(256,192)
    assert from_frame(restored)==stack_preferences and restored_pixels==ordered_pixels
    assert [row['id'] for row in restored['layers']]!=wanted
finally:
    stack.close()

def broken(context):
    context.viewer.add_image(np.ones((8, 8)))
    raise ValueError('controlled factory failure')

try:
    ReviewSession(broken, {})
except ValueError as error:
    assert 'controlled factory failure' in str(error)
else:
    raise AssertionError('failed factory was accepted')
reopened = ReviewSession(factory, resources)
try:
    reopened_frame, _ = reopened.render(256, 192)
    assert reopened_frame['layers'][0]['opacity'] == 1 and not reopened_frame['dirty']
    assert reopened_frame['layers'][0]['viewKey'] == 'raw-volume'
    assert reopened_frame['layers'][0]['id'] != identity
    from env_worker.scientific_view_preferences import from_frame, compatible
    baseline = from_frame(reopened_frame)
    layer = reopened.viewer.layers[0]
    affine = np.eye(layer.ndim+1)
    affine[0, 0] = -1
    affine[0, -1] = sum(layer.extent.world[:, 0])
    before_world = layer.data_to_world((0,)*layer.ndim)
    layer.affine = affine
    reflected, _ = reopened.render(256, 192)
    assert reflected['layers'][0]['scale'] == reopened_frame['layers'][0]['scale']
    assert reflected['layers'][0]['translate'] == reopened_frame['layers'][0]['translate']
    assert reflected['dimensions']['ranges'] == reopened_frame['dimensions']['ranges']
    assert layer.data_to_world((0,)*layer.ndim) != before_world
    try:
        compatible(baseline, reflected)
    except ValueError as error:
        assert 'no longer match' in str(error)
    else:
        raise AssertionError('same-extent reflection incorrectly accepted as matching geometry')
    layer = reopened.viewer.layers.pop(0)
    reopened.viewer.add_layer(layer)
    returned, _ = reopened.render(256, 192)
    assert 'viewKey' not in returned['layers'][0]
    try:
        reopened.view_bindings.bind(layer=layer, key='rebound')
    except ValueError:
        pass
    else:
        raise AssertionError('sealed identity was rebound')
finally:
    reopened.close()

def fiducial(context):
    from magicgui.widgets import Container
    values = np.zeros((40, 50), dtype=np.uint8)
    values[8:11, 33:36] = 255
    context.viewer.add_image(values, interpolation2d='nearest', contrast_limits=(0, 255),
                             scale=(2, 3), translate=(7, 11))
    return Container()

marker = ReviewSession(fiducial, {})
try:
    for orientation in [('down', 'right'), ('up', 'right'), ('down', 'left'), ('up', 'left')]:
        marker.viewer.camera.orientation2d = orientation
        frame, rgba = marker.render(256, 192)
        pixels = np.frombuffer(rgba, dtype=np.uint8).reshape(192, 256, 4)
        ys, xs = np.where(pixels[:, :, 0] > 240)
        assert len(xs) > 0
        found = marker.data_position(frame['frame'], frame['layers'][0]['id'],
                                     [float(ys.mean()) + .5, float(xs.mean()) + .5])
        assert np.allclose(found, [9, 34], atol=.5), (orientation, found)
finally:
    marker.close()

from napari.components import Dims
from env_worker import scientific_view_wire

native_dims = Dims(ndim=2, range=((0, 9.6, 1), (0, 6, 1)), point=(9.6, 2.5))
assert native_dims.nsteps == (10, 7) and native_dims.current_step == (10, 2)
frame['dimensions'] = {'order': list(native_dims.order), 'displayed': list(native_dims.displayed),
    'ndisplay': native_dims.ndisplay, 'steps': list(native_dims.current_step), 'counts': list(native_dims.nsteps),
    'labels': list(native_dims.axis_labels), 'ranges': [list(bounds) for bounds in native_dims.range],
    'point': [float(n) for n in native_dims.point]}
assert scientific_view_wire.frame(frame)['dimensions']['point'] == [9.6, 2.5]
from types import SimpleNamespace
import threading
native_dims = Dims(ndim=3, range=((0, 9.6, 1), (0, 6, 1), (0, 8, 1)), point=(9.6, 2.5, 3.5), order=(2, 0, 1))
probe = object.__new__(ReviewSession)
probe._closed, probe._thread, probe._frame = False, threading.get_ident(), 'fractional'
probe.viewer = SimpleNamespace(dims=native_dims)
probe.panel = SimpleNamespace(invalidate=lambda: None)
probe.change({'frame': 'fractional', 'operation': 'dimensions',
              'value': {'order': [2, 0, 1], 'ndisplay': 2, 'steps': [10, 2, 3]}})
assert native_dims.point == (9.6, 2.5, 3.0) and probe.dirty

from napari.layers import Labels
callback_data = np.zeros((3, 3), dtype=np.uint16)
callback_layer = Labels(callback_data)
def mutate_on_visibility(event):
    callback_data[0, 0] = 7
callback_layer.events.visible.connect(mutate_on_visibility)
probe._frame, probe._layers, probe.dirty = 'callback', [('layer', callback_layer)], False
probe.change({'frame': 'callback', 'operation': 'layer-presentation',
              'value': {'layer': 'layer', 'visible': False, 'opacity': .5}})
assert callback_data[0, 0] == 7 and probe.dirty
# Actual screen-coordinate edits, using the same frame admission as the workspace.
label_data = np.zeros((3, 24, 28), dtype=np.uint16)
label_source = SimpleNamespace(shape=label_data.shape, dtype=label_data.dtype.str, axes=('z','y','x'),
    descriptor={'semantic':'instance-labels'}, reference={'id':'test','revision':'a','component':'array.labels'})
def labels_factory(context):
    from magicgui.widgets import Container
    layer = context.viewer.add_labels(label_data, scale=(3,2,1))
    context.bind_editable(layer=layer,input='labels')
    return Container()
label_host = ReviewSession(labels_factory, {'labels':label_source})
try:
    label_frame, _ = label_host.render(256,192)
    layer_id = label_frame['layers'][0]['id']
    position = label_host.data_position(label_frame['frame'],layer_id,[96,128])
    command = {'op':'labels','request':'gesture-1234567890123456','frame':label_frame['frame'],
        'layer':layer_id,'width':256,'height':192,'action':'paint','points':[[96,128]],
        'label':'7','brush':3,'preserve':False,'contiguous':True}
    outcome = label_host.labels(command)
    assert outcome['state']=='applied' and outcome['changed']
    assert label_data[tuple(round(n) for n in position)] == 7
    try: label_host.labels(command)
    except Stale: pass
    else: raise AssertionError('a stale gesture was replayed')
    label_frame, _ = label_host.render(256,192)
    command = {key:value for key,value in command.items() if key not in ('label','brush','preserve','contiguous')}
    command.update(frame=label_frame['frame'],action='undo',points=[])
    assert label_host.labels(command)['state']=='applied' and not label_data.any()
    label_frame, _ = label_host.render(256,192)
    volume_command = {**command, 'frame':label_frame['frame'], 'action':'paint', 'points':[[96,128]],
                      'label':'7','brush':9,'preserve':False,'contiguous':True,'dimensions':3}
    position = label_host.data_position(label_frame['frame'],layer_id,[96,128])
    from napari.layers import Labels
    reference_labels = Labels(np.zeros_like(label_data),scale=(3,2,1))
    reference_labels.n_edit_dimensions=3; reference_labels.brush_size=9
    reference_labels.paint(position,7)
    assert label_host.labels(volume_command)['state']=='applied'
    assert np.array_equal(label_data,reference_labels.data)
    assert np.count_nonzero(np.any(label_data,axis=(1,2)))>1
    label_frame, _ = label_host.render(256,192)
    command.update(frame=label_frame['frame'])
    assert label_host.labels(command)['state']=='applied' and not label_data.any()
    label_frame, _ = label_host.render(256,192)
    polygon_command = {**command, 'frame':label_frame['frame'], 'action':'polygon',
                       'points':[[65,95],[65,160],[145,95]], 'label':'7','preserve':False}
    positions=[label_host.data_position(label_frame['frame'],layer_id,point) for point in polygon_command['points']]
    reference_labels=Labels(np.zeros_like(label_data),scale=(3,2,1))
    reference_labels.n_edit_dimensions=2
    reference_labels.paint_polygon(positions,7)
    polygon_outcome=label_host.labels(polygon_command)
    assert polygon_outcome['state']=='applied', (polygon_outcome,positions)
    assert label_data.any() and np.array_equal(label_data,reference_labels.data)
    try: label_host.labels(polygon_command)
    except Stale: pass
    else: raise AssertionError('a stale polygon was replayed')
    label_frame, _ = label_host.render(256,192)
    command.update(frame=label_frame['frame'])
    assert label_host.labels(command)['state']=='applied' and not label_data.any()
    label_frame, _ = label_host.render(256,192)
    label_data[0,0,0]=19
    command.update(frame=label_frame['frame'],action='redo')
    outcome = label_host.labels(command)
    assert outcome['state']=='refused' and label_data[0,0,0]==19
    assert label_host.render(256,192)[0]['privatePreview']
finally:
    label_host.close()

write_message(output, {'ok': True, 'frames': 6, 'nativeValuesUnchanged': True})
