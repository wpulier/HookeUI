"""The Labels tool state of an editable Labels layer: napari's own mode, selected label, brush and preservation.

The Studio's Labels editor shows this snapshot (frame row `labelsTool`) and changes it with the `labels-tool` change:
either explicit settings, or one of napari's own Labels key-binding actions (napari.layers.labels._labels_key_bindings,
run on the layer exactly as napari's keyboard shortcut runs it). Nothing here edits label values: painting stays in
scientific_labels.LabelsEditor, which applies a gesture with the settings it carries.
"""
import re

# napari's registered Labels actions a Studio control or shortcut may run (the default shortcut of each in napari).
ACTIONS = ('activate_labels_erase_mode', 'activate_labels_paint_mode', 'activate_labels_polygon_mode',
           'activate_labels_fill_mode', 'activate_labels_picker_mode', 'activate_labels_pan_zoom_mode',
           'new_label', 'swap_selected_and_background_labels', 'decrease_label_id', 'increase_label_id',
           'decrease_brush_size', 'increase_brush_size', 'toggle_preserve_labels')
MODES = ('pan_zoom', 'paint', 'erase', 'fill', 'pick', 'polygon')
SETTINGS = {'mode', 'selectedLabel', 'brushSize', 'preserve', 'contiguous', 'editDimensions', 'showSelected'}
LABEL = re.compile(r'0|[1-9][0-9]{0,19}')
MAX_BRUSH = 256


def snapshot(layer):
    return {'mode': str(layer.mode), 'selectedLabel': str(int(layer.selected_label)),
            'background': str(int(layer.colormap.background_value)), 'brushSize': int(layer.brush_size),
            'preserve': bool(layer.preserve_labels), 'contiguous': bool(layer.contiguous),
            'editDimensions': int(layer.n_edit_dimensions), 'showSelected': bool(layer.show_selected_label)}


def check(value):
    """A frame row's `labelsTool` snapshot, exactly as snapshot() makes it; raises ValueError otherwise."""
    names = {'mode', 'selectedLabel', 'background', 'brushSize', 'preserve', 'contiguous', 'editDimensions', 'showSelected'}
    if (type(value) is not dict or set(value) != names or value['mode'] not in (*MODES, 'transform')
            or any(type(value[k]) is not str or not LABEL.fullmatch(value[k]) for k in ('selectedLabel', 'background'))
            or type(value['brushSize']) is not int or not 1 <= value['brushSize'] <= 1 << 20
            or type(value['editDimensions']) is not int or not 1 <= value['editDimensions'] <= 32
            or any(type(value[k]) is not bool for k in ('preserve', 'contiguous', 'showSelected'))):
        raise ValueError('The Labels tool state is malformed')
    return value


def command(value):
    """A closed `labels-tool` value: {'layer', 'action'} or {'layer', 'settings'}; raises ValueError otherwise."""
    if type(value) is not dict or set(value) not in ({'layer', 'action'}, {'layer', 'settings'}):
        raise ValueError('A Labels tool change names one layer and either a napari action or its settings')
    if type(value['layer']) is not str or not 1 <= len(value['layer']) <= 64:
        raise ValueError('Choose an editable Labels layer')
    if 'action' in value:
        if value['action'] not in ACTIONS:
            raise ValueError('Choose one of napari\'s Labels actions')
        return value
    settings = value['settings']
    if type(settings) is not dict or not settings or not set(settings) <= SETTINGS:
        raise ValueError('Labels tool settings are its mode, label, brush, preservation, fill and edit dimensions')
    checks = {'mode': lambda v: v in MODES, 'selectedLabel': lambda v: type(v) is str and LABEL.fullmatch(v),
              'brushSize': lambda v: type(v) is int and 1 <= v <= MAX_BRUSH,
              'preserve': lambda v: type(v) is bool, 'contiguous': lambda v: type(v) is bool,
              'editDimensions': lambda v: type(v) is int and 2 <= v <= 8, 'showSelected': lambda v: type(v) is bool}
    for name, item in settings.items():
        if not checks[name](item):
            raise ValueError(f'The Labels tool setting {name} is out of range')
    return value


def admit(layer, value):
    """Refuses, before anything applies, a change this layer cannot take; raises ValueError."""
    import numpy as np
    command(value)
    if layer._type_string != 'labels':
        raise ValueError('Choose a Labels layer')
    settings = value.get('settings', {})
    if 'editDimensions' in settings and settings['editDimensions'] > layer.ndim:
        raise ValueError('Choose edit dimensions within this Labels layer\'s dimensionality')
    if 'selectedLabel' in settings and int(settings['selectedLabel']) > np.iinfo(layer.data.dtype).max:
        raise ValueError('The selected label exceeds this array\'s integer range')


def apply(layer, value):
    """Change the layer's tool state; napari's own action functions run for an action. Data is never changed."""
    from napari.layers.labels import _labels_key_bindings as bindings
    admit(layer, value)
    before = layer.data
    if 'action' in value:
        getattr(bindings, value['action'])(layer)
    else:
        settings = value['settings']
        names = {'mode': 'mode', 'brushSize': 'brush_size', 'preserve': 'preserve_labels', 'contiguous': 'contiguous',
                 'editDimensions': 'n_edit_dimensions', 'showSelected': 'show_selected_label'}
        for name, item in settings.items():
            if name == 'selectedLabel':
                layer.selected_label = int(item)
            else:
                setattr(layer, names[name], item)
    if layer.data is not before:
        raise ValueError('A Labels tool change replaced the layer\'s data')
