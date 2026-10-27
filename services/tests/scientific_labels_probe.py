"""Actual pinned Labels behavior on synthetic arrays in an owned confined worker."""
from pathlib import Path
from types import SimpleNamespace
import sys
sys.path.insert(0, sys.argv[1])
import numpy as np
from napari.layers import Labels
from env_worker.scientific_layer_exports import EditableLayers
from env_worker.scientific_labels import LabelsEditor, Refused
from env_worker import scientific_labels


def setup(values=None, scale=None):
    data = np.zeros((3, 24, 28), dtype=np.uint64) if values is None else values.copy()
    layer = Labels(data, scale=scale)
    source = SimpleNamespace(shape=data.shape, dtype=data.dtype.str, axes=tuple(f'd{axis}' for axis in range(data.ndim)),
        descriptor={'semantic':'instance-labels'}, reference={'id':'test','revision':'a','component':'array.labels'})
    registry = EditableLayers(SimpleNamespace(layers=[layer]), {'labels':source})
    registry.bind(layer=layer, input='labels'); registry.seal()
    return layer, LabelsEditor(layer, registry)


def paint(editor, points, label='7', action='paint', brush=3, preserve=False, contiguous=True):
    return editor.apply(action, points=points, label=label, brush=brush, preserve=preserve, contiguous=contiguous)


def refused(call, text):
    try: call()
    except (ValueError, Refused) as error:
        assert text in str(error), (text,str(error))
    else: raise AssertionError('expected refusal: '+text)


layer, editor = setup()
original = layer.data.copy()
paint(editor, [[1,8,8],[1,8,13]], label='18446744073709551615')
painted = layer.data.copy()
assert painted.max() == np.iinfo(np.uint64).max
assert not painted[0].any() and not painted[2].any()
assert len(layer._undo_history) == 1
editor.apply('undo'); assert np.array_equal(layer.data, original)
editor.apply('redo'); assert np.array_equal(layer.data, painted)
assert editor.apply('pick', points=[[1,8,8]])['label'] == '18446744073709551615'
editor.apply('undo')
original_paint = layer.paint
calls = 0
def fail_after_mutation(*args, **kwargs):
    global calls
    original_paint(*args, **kwargs)
    calls += 1
    if calls == 2: raise ValueError('controlled paint failure')
layer.paint = fail_after_mutation
refused(lambda: paint(editor, [[1,4,4],[1,4,10]]), 'controlled paint failure')
assert np.array_equal(layer.data, original)
layer.paint = original_paint
editor.apply('redo'); assert np.array_equal(layer.data, painted)
editor.apply('undo')
# A no-op must not discard the redo from an earlier accepted gesture.
paint(editor, [[1,3,3]], label='0')
editor.apply('redo'); assert np.array_equal(layer.data, painted)
# Undeclared Python mutations must never be overwritten by manual undo.
layer.data[0,0,0] = 19
refused(lambda: editor.apply('undo'), 'explicitly reset')
assert layer.data[0,0,0] == 19
editor.reset()
refused(lambda: editor.apply('undo'), 'no manual edit')
paint(editor, [[2,4,4]])
editor.apply('undo'); assert layer.data[0,0,0] == 19
# Refusals happen before data changes.
before = layer.data.copy()
refused(lambda: paint(editor, [[1,2,2],[2,2,2]]), 'one displayed plane')
refused(lambda: paint(editor, [[1,2,2]], label='18446744073709551616'), 'integer range')
refused(lambda: paint(editor, [[1,2,2]], brush=257), 'brush')
assert np.array_equal(before, layer.data)
# Native preserve-label and erase semantics, including another label under the brush.
values = np.zeros((3,24,28),dtype=np.uint16)
values[1,8:11,8:11] = 5
values[1,9,10] = 6
layer, editor = setup(values)
paint(editor, [[1,9,9]], label='7', preserve=True, brush=7)
assert layer.data[1,9,9] == 5 and layer.data[1,9,10] == 6
paint(editor, [[1,9,9]], label='5', action='erase', preserve=True, brush=7)
assert layer.data[1,9,9] == 0 and layer.data[1,9,10] == 6
editor.apply('undo'); assert layer.data[1,9,9] == 5
# Contiguous fill changes only the chosen object; whole-plane fill changes both.
values = np.zeros((3,24,28),dtype=np.uint16)
values[1,2:4,2:4] = values[1,15:17,15:17] = 3
layer, editor = setup(values)
paint(editor, [[1,2,2]], label='8', action='fill')
assert np.all(layer.data[1,2:4,2:4] == 8) and np.all(layer.data[1,15:17,15:17] == 3)
editor.apply('undo'); assert np.array_equal(layer.data, values)
paint(editor, [[1,2,2]], label='9', action='fill', contiguous=False)
assert np.all(layer.data[values == 3] == 9)
editor.apply('undo'); assert np.array_equal(layer.data, values)
# Admission is checked before native code executes, at controlled small limits.
limit = scientific_labels.MAX_NEW_HISTORY
scientific_labels.MAX_NEW_HISTORY = 1
refused(lambda: paint(editor, [[1,2,2]]), 'history allowance')
scientific_labels.MAX_NEW_HISTORY = limit
assert np.array_equal(layer.data, values)
# Anisotropic brush must agree with the direct upstream implementation.
layer, editor = setup(scale=(3,2,1))
reference = Labels(np.zeros_like(layer.data), scale=(3,2,1))
reference.n_edit_dimensions = 2; reference.brush_size = 7
reference.paint([1,10,12], 7)
paint(editor, [[1,10,12]], brush=7)
assert np.array_equal(layer.data, reference.data)
# Histories in other layers count toward the same worker allowance.
layer, editor = setup()
other = Labels(np.zeros_like(layer.data))
editor.history = lambda: [scientific_labels.histories(layer), scientific_labels.histories(other)]
paint(editor, [[1,4,4]])
retained = layer.data.copy()
history_limit = scientific_labels.HISTORY_BYTES
other._undo_history.append([np.zeros(5000,dtype=np.uint8)])
scientific_labels.HISTORY_BYTES = scientific_labels.retained_bytes(editor.history()) + 1000
refused(lambda: paint(editor, [[1,2,2]]), 'history allowance')
assert np.array_equal(layer.data,retained)
editor.apply('undo')
assert not layer.data.any()
other._reset_history()
scientific_labels.HISTORY_BYTES = history_limit
paint(editor, [[1,2,2]])
assert layer.data.any()
print('actual Labels: paint/erase/fill/pick, exact uint64, undo/redo, rollback, external-change refusal and admission passed')

# Explicit volume editing agrees with native napari, including reordered 4D axes.
from napari.components import Dims
for dimensions, shape, order, point in (
    (3, (5, 24, 28), (0,1,2), [2,10,12]),
    (3, (3,5,24,28), (1,0,2,3), [1,2,10,12]),
):
    for action in ('paint','erase','fill'):
        initial = np.zeros(shape,dtype=np.uint16)
        initial[...,8:13,10:15] = 5
        scale = (2,) + (1,)*(len(shape)-1)
        layer, editor = setup(initial, scale)
        reference = Labels(initial.copy(),scale=scale)
        dims = Dims(ndim=len(shape),order=order)
        layer._slice_dims(dims); reference._slice_dims(dims)
        reference.n_edit_dimensions=dimensions
        reference.brush_size=7
        reference.preserve_labels=False
        reference.contiguous=True
        target=0 if action=='erase' else 9
        getattr(reference,'fill' if action=='fill' else 'paint')(point,target)
        editor.apply(action,points=[point],label='9',brush=7,preserve=False,contiguous=True,dimensions=dimensions)
        assert np.array_equal(layer.data,reference.data), (action,shape)
        changed = layer.data.copy()
        assert not np.array_equal(changed,initial)
        if len(shape)==4:
            assert np.array_equal(changed[:,0],initial[:,0])
            assert np.array_equal(changed[:,4],initial[:,4])
        editor.apply('undo'); assert np.array_equal(layer.data,initial)
        editor.apply('redo'); assert np.array_equal(layer.data,changed)
        before=layer.data.copy()
        refused(lambda: editor.apply('paint',points=[point],label='7',brush=3,preserve=False,contiguous=True,dimensions=len(shape)+1), 'dimensionality')
        assert np.array_equal(before,layer.data)
# Fill admission covers hidden dimensions too, before any upstream mutation.
layer, editor = setup(np.zeros((5,24,28),dtype=np.uint16))
limit=scientific_labels.MAX_FILL_PIXELS
scientific_labels.MAX_FILL_PIXELS=24*28
refused(lambda: editor.apply('fill',points=[[2,8,8]],label='7',brush=3,preserve=False,contiguous=True,dimensions=3), 'region allowance')
assert not layer.data.any()
scientific_labels.MAX_FILL_PIXELS=limit
print('actual Labels: explicit 3D paint/erase/fill, reordered 4D axes, exact undo/redo and volume admission passed')

# Every offered dimensional count runs upstream; unused leading axes stay untouched.
for dimensions in range(2,9):
    initial=np.zeros((3,)*8,dtype=np.uint16)
    layer, editor=setup(initial)
    reference=Labels(initial.copy())
    reference.n_edit_dimensions=dimensions; reference.brush_size=3
    point=[1]*8
    reference.paint(point,7)
    editor.apply('paint',points=[point],label='7',brush=3,preserve=False,contiguous=True,dimensions=dimensions)
    assert np.array_equal(layer.data,reference.data), dimensions
    editor.apply('undo'); assert np.array_equal(layer.data,initial)
    reference=Labels(initial.copy()); reference.n_edit_dimensions=dimensions
    reference.fill(point,7)
    editor.apply('fill',points=[point],label='7',brush=3,preserve=False,contiguous=True,dimensions=dimensions)
    assert np.array_equal(layer.data,reference.data), dimensions
    editor.apply('undo'); assert np.array_equal(layer.data,initial)
print('actual Labels: all offered editing dimensions 2 through 8 match upstream paint and fill')

# Polygon is a single native undoable edit. The expected triangle is independent
# of napari's own reference call and includes its edges.
layer, editor = setup()
points = [[1,2,2],[1,2,6],[1,6,2]]
expected = np.zeros_like(layer.data)
for y in range(2,7):
    for x in range(2,7):
        if y+x <= 8: expected[1,y,x] = 7
assert np.count_nonzero(expected) == 15
editor.apply('polygon',points=points,label='7',preserve=False)
assert np.array_equal(layer.data,expected)
assert len(layer._undo_history) == 1
editor.apply('undo'); assert not layer.data.any()
editor.apply('redo'); assert np.array_equal(layer.data,expected)
editor.apply('undo')
# The displayed axes need not be the last two array axes.
layer._slice_dims(Dims(ndim=3,order=(1,2,0)))
points = [[0,1,2],[0,1,6],[2,1,2]]
reference = Labels(np.zeros_like(layer.data)); reference._slice_dims(Dims(ndim=3,order=(1,2,0)))
reference.n_edit_dimensions=2; reference.paint_polygon(points,7)
editor.apply('polygon',points=points,label='7',preserve=False)
assert np.array_equal(layer.data,reference.data)
assert not layer.data[:,0].any() and not layer.data[:,2:].any()
editor.apply('undo'); assert not layer.data.any()
editor.apply('redo'); assert np.array_equal(layer.data,reference.data)
# Preserve existing labels; rollback a callback failure without destroying redo.
values=np.zeros((3,24,28),dtype=np.uint16); values[1,3,3]=9
layer,editor=setup(values); points=[[1,2,2],[1,2,6],[1,6,2]]
editor.apply('polygon',points=points,label='7',preserve=True)
assert layer.data[1,3,3] == 9 and np.count_nonzero(layer.data==7) == 14
changed=layer.data.copy(); editor.apply('undo')
original_polygon=layer.paint_polygon
def fail_polygon(*args,**kwargs):
    original_polygon(*args,**kwargs)
    raise ValueError('controlled polygon failure')
layer.paint_polygon=fail_polygon
refused(lambda: editor.apply('polygon',points=points,label='7',preserve=False), 'controlled polygon failure')
assert np.array_equal(layer.data,values)
layer.paint_polygon=original_polygon
editor.apply('redo'); assert np.array_equal(layer.data,changed)
editor.apply('undo')
for settings in ({'dimensions':3},{'brush':3},{'contiguous':True}):
    refused(lambda: editor.apply('polygon',points=points,label='7',preserve=False,**settings), 'polygon')
refused(lambda: editor.apply('polygon',points=[[1,2,2]]*3,label='7',preserve=False), 'polygon')
limit=scientific_labels.MAX_FILL_PIXELS; scientific_labels.MAX_FILL_PIXELS=1
refused(lambda: editor.apply('polygon',points=points,label='7',preserve=False), 'region allowance')
scientific_labels.MAX_FILL_PIXELS=limit
limit=scientific_labels.MAX_NEW_HISTORY; scientific_labels.MAX_NEW_HISTORY=1
refused(lambda: editor.apply('polygon',points=points,label='7',preserve=False), 'history allowance')
scientific_labels.MAX_NEW_HISTORY=limit
assert np.array_equal(layer.data,values)
print('actual Labels: exact polygon pixels, reordered axes, preserve, undo/redo, rollback and refusal passed')
