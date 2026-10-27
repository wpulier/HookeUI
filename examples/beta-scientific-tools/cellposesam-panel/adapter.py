"""Bounded actual-plugin rehearsal, through the public scientific host only.

The pinned upstream plugin owns segmentation, thresholds, shape-based diameter
and asynchronous callbacks. This adapter owns input admission, prepared weights
and the outer scrolling widget. It retains supported 2D settings and native layers; transient flow buffers require a new run after reopen.
"""
from importlib.metadata import version
from pathlib import Path
import os
import numpy as np


def saved_groups(groups, bindings, current):
    """Each run's bound layer keys. napari hands the plugin PublicOnlyProxy layers, so the plugin's
    record holds proxies of the viewer's layers; wrapt exposes the original layer as __wrapped__."""
    def bound(layer):
        layer = getattr(layer, '__wrapped__', layer)
        if not any(item is layer for item in current):
            return None
        return next((key for key, value in bindings.items() if value is layer), None)
    return [[key for key in map(bound, group) if key is not None] for group in groups]


# The device self-check (ticket 62, F-L8-1), the same as services/noncommercial/cellpose_device.py: a GPU that gives
# Cellpose-SAM wrong numbers without raising (seen on a macOS VM's paravirtual GPU: zero cells) must never make a result.
# Before the plugin's first run, its device runs the probe (the bundled CC0 specimen's 128x128 crop, shipped as this
# package's example) and must find the CPU's cells; else float32, else the CPU. The verdict is said in the panel.
PROBE_FILE = Path(__file__).with_name('example-probe-mitosis.png')
PROBE_SHA256 = 'cf19b15ea256fcda5e70aa92cb0eb98c92d229a4589007ae7fc9f95e1acac976'
PROBE_SETTINGS = {'diameter': 30, 'cellprob_threshold': 0.0, 'flow_threshold': 0.4, 'min_size': 15}
# The CPU's cells on the probe: (area px, centroid row, column); the same as cellpose_device.REFERENCE.
REFERENCE = ((79, 13.68, 40.16), (55, 14.45, 50.05), (143, 24.58, 44.41), (112, 66.55, 86.04), (52, 87.0, 124.6), (84, 122.0, 105.5))
CENTROID_PX, AREA_RATIO = 3.0, 0.3
ON_CPU_WORDS = "This Mac's GPU gave wrong results for Cellpose-SAM, so it runs on the CPU, slower"
CHECK_FAILED_WORDS = "The check of this Mac's GPU for Cellpose-SAM couldn't finish, so it runs on the CPU for now, slower"
NO_PICTURE_WORDS = "This Mac's GPU couldn't be checked for Cellpose-SAM: its check picture couldn't be read, so it runs on the CPU, slower"
HALF_UNCHECKED_WORDS = ("This Mac's GPU couldn't be checked for Cellpose-SAM in half precision, so it runs in full precision, "
                        "where its check passed")


def probe_cells(masks):
    masks = np.asarray(masks)
    out = []
    for label in np.unique(masks):
        if label:
            rows, cols = np.nonzero(masks == label)
            out.append((int(rows.size), float(rows.mean()), float(cols.mean())))
    return out


def probe_agrees(masks, reference=REFERENCE):
    found = probe_cells(masks)
    if abs(len(found) - len(reference)) > 1:
        return False, f'found {len(found)} cells on the check picture, where the CPU finds {len(reference)}'
    for area, row, col in reference:
        near = [a for a, r, c in found if (r - row) ** 2 + (c - col) ** 2 <= CENTROID_PX ** 2]
        if not any(abs(a - area) <= AREA_RATIO * area for a in near):
            return False, f'cells the CPU finds were missing or misshapen on the check picture'
    return True, f'found the {len(reference)} cells the CPU finds on the check picture'


def check_device(weights, assign, make):
    """(device, bfloat16, verdict): the plugin's device if its probe answer agrees (as the plugin runs it, then in
    float32), else the CPU. `assign` is cellpose.core.assign_device, `make(device, bf16)` builds the model."""
    import hashlib
    import torch
    from PIL import Image
    device, _gpu = assign(gpu=True)
    cpu = torch.device('cpu')
    if device.type == 'cpu':
        return cpu, False, {'device': 'cpu', 'says': 'Cellpose-SAM runs on the CPU, the reference device.'}
    try:
        picture = np.ascontiguousarray(np.asarray(Image.open(PROBE_FILE)))
        if hashlib.sha256(picture.tobytes()).hexdigest() != PROBE_SHA256:
            raise ValueError('the check picture is not the pinned one')
    except (OSError, ValueError) as exc:
        return cpu, False, {'device': 'cpu', 'says': f'{NO_PICTURE_WORDS} ({exc}).'}
    crashed = []
    for bf16 in (True, False):
        try:
            model = make(device, bf16)
            ok, seen = probe_agrees(model.eval(picture, batch_size=1, **PROBE_SETTINGS)[0])
            del model
        except Exception as exc:  # the device didn't answer: not proven, but not "wrong results" either
            ok, seen = False, f'the check could not finish ({type(exc).__name__})'
            crashed.append(bf16)
        if ok:
            words = f'Checked this Mac\'s GPU for Cellpose-SAM: it {seen}.'
            if not bf16:
                words = (HALF_UNCHECKED_WORDS if True in crashed else
                         "This Mac's GPU gave wrong results for Cellpose-SAM in half precision, so it runs in full precision") + '.'
            return device, bf16, {'device': str(device), 'bfloat16': bf16, 'checked': seen, 'says': words}
    if crashed:
        return cpu, False, {'device': 'cpu', 'checked': seen, 'says': f'{CHECK_FAILED_WORDS} ({seen}).'}
    return cpu, False, {'device': 'cpu', 'checked': seen, 'says': f'{ON_CPU_WORDS} ({seen}).'}


def guard_device(weights):
    """Make the pinned plugin run only on a proven device: its run asks cellpose.core.assign_device for a device and picks
    bfloat16 for any GPU, so both are answered from the verdict, checked once, on the first run (its worker thread)."""
    import threading
    from cellpose import core, models
    # The originals, kept once per process: a second panel in the same process wraps them, never a wrapper.
    if not hasattr(core, '_hooke_originals'):
        core._hooke_originals = (core.assign_device, models.CellposeModel)
    original_assign, original_model = core._hooke_originals
    verdict, lock = {}, threading.Lock()

    def make(device, bf16):
        return original_model(gpu=device.type != 'cpu', device=device, pretrained_model=str(weights), use_bfloat16=bf16)

    def assign_device(*args, **kwargs):
        with lock:
            if not verdict:
                device, bf16, said = check_device(weights, original_assign, make)
                verdict.update(device=device, bf16=bf16, said=said)
        return verdict['device'], verdict['device'].type != 'cpu'

    class ProvenModel(original_model):
        def __init__(self, *args, **kwargs):
            if verdict:
                kwargs['use_bfloat16'] = verdict['bf16']  # the proven precision, whatever the caller chose
            super().__init__(*args, **kwargs)

    # The plugin passes use_bfloat16 only when inspect.signature(models.CellposeModel) lists it: keep the original's.
    import inspect
    ProvenModel.__signature__ = inspect.signature(original_model)

    core.assign_device, models.CellposeModel = assign_device, ProvenModel
    return verdict


SETTINGS = ('diameter', 'cellprob_threshold', 'flow_threshold', 'min_size', 'resample_dynamics')
STATE = {'input', 'settings', 'groups', 'nextOutput', 'recomputeAfterReopen'}


def capture_state(widget, bindings, current, input_key, next_output):
    if not widget.call_button.enabled:
        raise ValueError('Wait for segmentation to finish before saving this tool')
    return {'input': input_key, 'settings': {name: getattr(widget, name).value for name in SETTINGS},
            'groups': saved_groups(getattr(widget, 'cellpose_layers', []), bindings, current),
            'nextOutput': next_output, 'recomputeAfterReopen': 'run-segmentation'}


def restore_state(widget, state, bindings, input_key, next_output):
    """Apply saved settings and run groups to a new panel; segmentation never runs here.

    nextOutput only names future lifetime keys and never decreases, so it may exceed the
    host's 64 bound identities per session; the host enforces those limits itself."""
    if (type(state) is not dict or set(state) != STATE
            or state['input'] != input_key or type(state['settings']) is not dict
            or set(state['settings']) != set(SETTINGS)
            or state['recomputeAfterReopen'] != 'run-segmentation'
            or type(state['nextOutput']) is not int or state['nextOutput'] < next_output
            or type(state['groups']) is not list
            or any(type(group) is not list or any(key not in bindings for key in group) for group in state['groups'])):
        raise ValueError('This saved Cellpose state is unsupported; retain the original document')
    for name, value in state['settings'].items():
        getattr(widget, name).value = value
    widget.cellpose_layers = [[bindings[key] for key in group] for group in state['groups']]
    widget.compute_masks_button.enabled = False
    return state['nextOutput']


def bind_result(context, image, layer, key):
    """Only the pinned plugin's known outputs declare the actual input pixel grid."""
    role = layer.name.removeprefix(image.name + '_cp_').rsplit('_', 1)[0]
    if not layer.name.startswith(image.name + '_cp_') or role not in {'flows', 'cellprob', 'outlines', 'masks'}:
        context.bind_view_output(version=1, layer=layer, key=key, meaning=None)
        return
    # The pinned plugin creates these outputs in the input grid. Declare only
    # after copying its complete geometry; the shared host verifies the claim.
    layer.scale = image.scale
    layer.translate = image.translate
    layer.rotate = image.rotate
    layer.shear = image.shear
    layer.affine = image.affine.affine_matrix.copy()
    layer.axis_labels = image.axis_labels
    layer.units = image.units
    context.bind_view_output(version=2, layer=layer, key=key, coordinates_from=image,
        meaning={'semantic':'instance-labels' if role in {'outlines', 'masks'} else 'intensity',
                 'axes':list(image.axis_labels)})


def open_host(context):
    supported = getattr(context, 'output_binding_version', 1)
    if type(supported) is not int or supported != 2 or not callable(getattr(context, 'bind_view_output', None)):
        raise ValueError('Update Hooke to support output bindings version 2 before opening this candidate')
    if not callable(getattr(context, "bind_recipe", None)):
        raise ValueError("Update Hooke to support versioned recipe settings before opening this candidate")
    from magicgui.widgets import Container
    from qtpy.QtWidgets import QLabel, QVBoxLayout, QWidget
    from napari.layers import Image
    from qtpy.QtWidgets import QScrollArea
    from napari_cellposesam._dock_widget import widget_wrapper

    if version('napari-cellposesam') != '0.1.1' or version('cellpose') != '4.2.1.1':
        raise ValueError('This candidate needs its pinned Cellpose-SAM runtime')
    if context.restored_layers is None:
        if len(context.inputs) != 1:
            raise ValueError('Select one retained 2D intensity image for this rehearsal')
        key, resource = next(iter(context.inputs.items()))
        if (resource.descriptor.get('semantic') != 'intensity' or len(resource.shape) != 2
                or np.prod(resource.shape) * np.dtype(resource.dtype).itemsize > 1 << 20):
            raise ValueError('This rehearsal takes one scalar 2D intensity image up to 1 MiB')
        values = resource.read([[0, n] for n in resource.shape])
        kwargs = {'axis_labels': resource.axes}
        calibration = resource.descriptor.get('calibration')
        if calibration is not None:
            affine = np.eye(3); affine[:2] = calibration['affine']
            kwargs.update(affine=affine, units=calibration['units'])
        image = context.viewer.add_image(values, name=key, **kwargs)
        context.bind_editable(layer=image, input=key)
        context.bind_view(layer=image, key='cellpose_input')
        bindings = {'cellpose_input': image}
    else:
        bindings = dict(context.restored_layers)
        images = [layer for key,layer in bindings.items() if not key.startswith('cellpose_result_') and isinstance(layer, Image)]
        if (len(images) != 1 or images[0].ndim != 2 or images[0].rgb
                or type(images[0].data) is not np.ndarray or images[0].data.nbytes > 1 << 20):
            raise ValueError('Select a scene with one scalar 2D image up to 1 MiB; other modes remain unqualified')
        image = images[0]

    input_key = next(key for key,layer in bindings.items() if layer is image)
    next_output = max([int(key.rsplit('_',1)[1])+1 for key in bindings if key.startswith('cellpose_result_')] or [0])
    weights = Path(os.environ['MICROMIND_ENTRY_FILES']) / 'cpsam_v2'
    if not weights.is_file():
        raise ValueError('Prepare this candidate’s declared Cellpose-SAM weights before opening it')
    verdict = guard_device(weights)
    widget = widget_wrapper()
    widget.viewer.value = context.viewer
    # magicgui resets choices when Qt reparents a container. A callable is its
    # public persistent choice provider; a one-time list restores old defaults.
    widget.image_layer.choices = lambda _: [image]
    widget.image_layer.value = image
    widget.image_layer.enabled = False
    widget.model_type.choices = lambda _: [('Prepared Cellpose-SAM v2', 'custom')]
    widget.model_type.value = 'custom'
    widget.model_type.enabled = False
    widget.custom_model.value = weights
    widget.custom_model.visible = False
    widget.clear_previous_segmentations.value = False
    widget.clear_previous_segmentations.enabled = False
    widget.clear_previous_segmentations.tooltip = 'Earlier results remain available for comparison and explicit removal.'
    widget.process_3D.enabled = False
    widget.stitch_threshold_3D.enabled = False
    widget.call_button.text = 'Run segmentation'

    def preserve_geometry(event):
        nonlocal next_output
        layer = event.value
        key = f'cellpose_result_{next_output}'
        bind_result(context, image, layer, key)
        bindings[key] = layer
        next_output += 1

    context.viewer.layers.events.inserted.connect(preserve_geometry)
    panel = QScrollArea()
    panel.setWidgetResizable(True)
    body = QWidget(); layout = QVBoxLayout(body)
    notice = QLabel('Save tool retains the image, outputs and settings. After reopening, run segmentation to enable threshold recomputation; transient flow buffers are not saved.')
    notice.setWordWrap(True); layout.addWidget(notice)
    device_line = QLabel("This Mac's GPU is checked against a known picture before the first run's result.")
    device_line.setWordWrap(True); layout.addWidget(device_line); layout.addWidget(widget.native)
    from qtpy.QtCore import QTimer
    shown = QTimer(panel)  # the verdict is made on the plugin's worker thread; shown from the GUI thread

    def show_verdict():
        if verdict.get('said'):
            device_line.setText(verdict['said']['says'])
            shown.stop()
    shown.timeout.connect(show_verdict); shown.start(500)
    panel.setWidget(body)
    panel.setAccessibleName('Cellpose-SAM segmentation controls')
    context.bind_native_panel(panel)
    def capture():
        return capture_state(widget, bindings, list(context.viewer.layers), input_key, next_output)
    def restore(state):
        nonlocal next_output
        next_output = restore_state(widget, state, bindings, input_key, next_output)
    context.bind_document(schema='hooke.cellposesam.2d',version=1,capture=capture,restore=restore)
    def recipe_settings():
        if not widget.call_button.enabled:
            raise ValueError('Wait for segmentation before capturing its recipe settings')
        return {name:getattr(widget,name).value for name in SETTINGS}
    def configure_recipe(state):
        if type(state) is not dict or set(state)!=set(SETTINGS):
            raise ValueError('This recipe needs the five supported 2D segmentation settings')
        for name,value in state.items():getattr(widget,name).value=value
    context.bind_recipe(schema='hooke.cellposesam.2d.settings',version=1,
                        capture=recipe_settings,configure=configure_recipe)
    return Container()
