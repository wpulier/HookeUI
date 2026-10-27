"""Bounded display metadata shared by the scientific worker and its parent.

A valid frame describes a private preview; it grants no Project or edit authority.
"""
from __future__ import annotations

import copy
import json
import math
import re

from . import scientific_grid, scientific_slices, scientific_point_appearance, scientific_surface_appearance, scientific_path_appearance, scientific_colormap, scientific_image_sampling, scientific_clipping, scientific_image_plane, scientific_image_appearance

MAX_BYTES = (128 << 10) - 32
SAFE_INTEGER = 2**53 - 1
BLENDING = ('translucent', 'translucent_no_depth', 'additive', 'minimum', 'opaque', 'multiplicative')
LAYER_TYPES = ('image', 'labels', 'points', 'shapes', 'surface', 'vectors', 'tracks')


def require(condition):
    if not condition:
        raise ValueError('The scientific view returned unsupported or inconsistent metadata')


def fields(value, names):
    require(type(value) is dict and set(value) == set(names.split()))


def token(value, size=32):
    return type(value) is str and re.fullmatch('[a-f0-9]{' + str(size) + '}', value) is not None


def number(value):
    return type(value) in (int, float) and abs(value) <= SAFE_INTEGER and math.isfinite(value)


def integer(value, minimum=0, maximum=SAFE_INTEGER):
    return number(value) and int(value) == value and minimum <= value <= maximum


def text(value):
    return type(value) is str and len(value.encode('utf-16-le', errors='surrogatepass')) // 2 <= 1000


def vector(value, size):
    require(type(value) is list and len(value) == size and all(number(n) for n in value))


def bounded(value, limit):
    try:
        # Match the browser's compact UTF-8 serialization budget.
        require(len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()) <= limit)
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ValueError('Scientific view metadata must be bounded finite JSON') from error


def panel(value):
    fields(value, 'format panel revision controls')
    require(value['format'] == 'scientific-controls-v1' and token(value['panel'])
            and integer(value['revision'], 1) and type(value['controls']) is list and len(value['controls']) <= 128)
    bounded(value, 64 << 10)
    seen = set()
    for row in value['controls']:
        require(type(row) is dict and token(row.get('id')) and row['id'] not in seen)
        seen.add(row['id'])
        require(text(row.get('label')) and text(row.get('help'))
                and type(row.get('enabled')) is bool and type(row.get('visible')) is bool)
        names = 'id label help enabled visible kind'
        kind = row.get('kind')
        if kind == 'button':
            pass
        elif kind in ('label', 'text'):
            names += ' value'
            require(text(row.get('value')))
            if kind == 'text':
                names += ' multiline'
                require(type(row.get('multiline')) is bool)
        elif kind == 'boolean':
            names += ' value'
            require(type(row.get('value')) is bool)
        elif kind in ('integer', 'number'):
            names += ' value min max step'
            require(all(number(row.get(key)) for key in ('value', 'min', 'max'))
                    and row['min'] <= row['value'] <= row['max']
                    and (row.get('step') is None or (number(row['step']) and row['step'] > 0))
                    and (kind != 'integer' or integer(row['value'], -SAFE_INTEGER)))
        elif kind == 'choice':
            names += ' value choices'
            choices = row.get('choices')
            require(type(choices) is list and len(choices) <= 256)
            ids = set()
            for choice in choices:
                fields(choice, 'id label')
                require(token(choice['id']) and choice['id'] not in ids and text(choice['label']))
                ids.add(choice['id'])
            require(row.get('value') is None or (type(row['value']) is str and row['value'] in ids))
        else:
            require(False)
        fields(row, names)
    return value


def frame(value):
    fields(value, 'format frame width height pixelRatio derivedDisplay privatePreview dirty layers dimensions camera controls' + (' grid' if type(value) is dict and 'grid' in value else '') + (' layerSelection' if type(value) is dict and 'layerSelection' in value else '') + (' nativePanelAvailable' if type(value) is dict and 'nativePanelAvailable' in value else '') + (' toolDocumentAvailable' if type(value) is dict and 'toolDocumentAvailable' in value else ''))
    if 'nativePanelAvailable' in value: require(type(value['nativePanelAvailable']) is bool)
    if 'toolDocumentAvailable' in value: require(type(value['toolDocumentAvailable']) is bool)
    if 'grid' in value: scientific_grid.settings(value['grid'])
    require(value['format'] == 'scientific-host-v1' and token(value['frame'], 64)
            and integer(value['width'], 1, 1024) and integer(value['height'], 1, 1024)
            and integer(value['pixelRatio'], 1, 1)
            and value['derivedDisplay'] is True and value['privatePreview'] is True and type(value['dirty']) is bool)
    bounded(value, MAX_BYTES)
    dims = value['dimensions']
    fields(dims, 'order displayed ndisplay steps counts labels ranges point' + ''.join(
        f' {name}' for name in ('margins', 'lastUsed', 'rollable', 'units') if type(dims) is dict and name in dims))
    order = dims['order']
    require(type(order) is list and 2 <= len(order) <= 8 and all(integer(n, 0, 7) for n in order)
            and sorted(order) == list(range(len(order))) and integer(dims['ndisplay'], 2, 3)
            and dims['ndisplay'] <= len(order))
    if 'margins' in dims:
        scientific_slices.margins(dims['margins'], len(order))
    require(type(dims['displayed']) is list and all(integer(n) for n in dims['displayed'])
            and dims['displayed'] == order[-int(dims['ndisplay']):])
    for key in ('steps', 'counts', 'labels', 'ranges', 'point'):
        require(type(dims[key]) is list and len(dims[key]) == len(order))
    require(all(text(label) for label in dims['labels']))
    if 'lastUsed' in dims:
        require(integer(dims['lastUsed'], 0, len(order) - 1))
    if 'rollable' in dims:
        require(type(dims['rollable']) is list and len(dims['rollable']) == len(order)
                and all(type(flag) is bool for flag in dims['rollable']))
    if 'units' in dims:
        require(dims['units'] is None or (type(dims['units']) is list and len(dims['units']) == len(order)
                                          and all(text(unit) and len(unit) <= 100 for unit in dims['units'])))
    for bounds, point, step, count in zip(dims['ranges'], dims['point'], dims['steps'], dims['counts']):
        vector(bounds, 3)
        start, stop, spacing = bounds
        require(number(point) and start <= point <= stop and spacing > 0)
        span, position = (stop - start) / spacing, (point - start) / spacing
        require(number(span) and number(position) and integer(count, 1) and integer(step)
                and count == int(span) + 1 and step == round(position))
    camera = value['camera']
    fields(camera, 'center zoom orientation angles perspective')
    vector(camera['center'], 3)
    vector(camera['angles'], 3)
    require(number(camera['zoom']) and camera['zoom'] > 0 and number(camera['perspective'])
            and 0 <= camera['perspective'] < 180 and type(camera['orientation']) is list
            and len(camera['orientation']) == 3
            and all(type(v) is str and v in options for v, options in zip(camera['orientation'],
                (('towards', 'away'), ('up', 'down'), ('left', 'right')))))
    layer_rows(value['layers'], len(order))
    if 'layerSelection' in value:
        selection=value['layerSelection']
        fields(selection,'selected active')
        ids={layer['id'] for layer in value['layers']}
        selected=selection['selected']
        require(type(selected) is list and all(token(identity) and identity in ids for identity in selected)
                and len(selected)==len(set(selected)))
        require(selection['active']==(selected[0] if len(selected)==1 else None))
    panel(value['controls'])
    return copy.deepcopy(value)


def geometry(value, ndim):
    fields(value, 'affine axes units')
    require(type(value['affine']) is list and len(value['affine']) == ndim+1)
    for row in value['affine']: vector(row, ndim+1)
    require(value['affine'][-1] == [0]*ndim+[1])
    for key in ('axes', 'units'):
        require(type(value[key]) is list and len(value[key]) == ndim and all(text(v) for v in value[key]))


def layer_rows(layers, ndim):
    require(type(layers) is list and len(layers) <= 64)
    seen = set()
    editable_inputs = set()
    view_keys = set()
    for layer in layers:
        names = 'id name type visible opacity scale translate'
        if type(layer) is dict and 'elementCount' in layer:
            names += ' elementCount'
            require(layer.get('type') in ('points','vectors','tracks','shapes') and integer(layer['elementCount']))
        if type(layer) is dict and 'editableInput' in layer:
            names += ' editableInput'
            require(type(layer['editableInput']) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}', layer['editableInput']) is not None
                    and layer.get('type') in ('image', 'labels') and layer['editableInput'] not in editable_inputs)
            editable_inputs.add(layer['editableInput'])
        if type(layer) is dict and 'inputMeaning' in layer:
            from .scientific_output_wire import declaration
            names += ' inputMeaning'
            meaning = declaration(layer['inputMeaning'])
            require('editableInput' in layer and type(layer.get('scale')) is list
                    and len(meaning['axes']) == len(layer['scale'])
                    and meaning['semantic'] in (('intensity', 'probabilities') if layer.get('type') == 'image'
                                               else ('class-labels', 'instance-labels')))
        if type(layer) is dict and 'outputMeaning' in layer:
            from .scientific_output_wire import declaration, SEMANTICS
            names += ' outputMeaning'
            meaning = declaration(layer['outputMeaning'])
            require('viewKey' in layer and 'editableInput' not in layer and 'inputMeaning' not in layer
                    and type(layer.get('scale')) is list and len(meaning['axes']) == len(layer['scale'])
                    and SEMANTICS[meaning['semantic']] == layer.get('type')
                    and (layer.get('type') != 'tracks' or meaning['axes'][0] == 't'))
        if type(layer) is dict and 'viewKey' in layer:
            names += ' viewKey viewGeometry'
            require(type(layer['viewKey']) is str and re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', layer['viewKey']) is not None
                    and layer['viewKey'] not in view_keys)
            view_keys.add(layer['viewKey'])
        if type(layer) is dict and 'imageColormap' in layer:
            names += ' imageColormap colormapChoices'
            require(layer.get('type') == 'image')
            scientific_colormap.settings(layer['imageColormap'])
            scientific_colormap.check_choices(layer.get('colormapChoices'))
        if type(layer) is dict and 'imageProjection' in layer:
            names += ' imageProjection'
            require(layer.get('type') == 'image')
            scientific_slices.projection(layer['imageProjection'])
        if type(layer) is dict and 'textAppearance' in layer:
            from . import scientific_text_appearance
            names += ' textAppearance'
            require(layer.get('type') in ('points','shapes') and type(layer.get('scale')) is list)
            scientific_text_appearance.settings(layer['textAppearance'],len(layer['scale']))
        if type(layer) is dict and 'annotation' in layer:
            from . import scientific_annotate
            names += ' annotation'
            require(layer.get('type') in ('points', 'shapes'))
            try:
                scientific_annotate.check_summary(layer['annotation'])
            except ValueError:
                require(False)
        if type(layer) is dict and 'labelsTool' in layer:
            from . import scientific_labels_tool
            names += ' labelsTool'
            require(layer.get('type') == 'labels' and 'editableInput' in layer)
            try:
                scientific_labels_tool.check(layer['labelsTool'])
            except ValueError:
                require(False)
        if type(layer) is dict and 'pointAppearance' in layer:
            names += ' pointAppearance'
            require(layer.get('type') == 'points')
            scientific_point_appearance.settings(layer['pointAppearance'])
        if type(layer) is dict and any(key in layer for key in ('pointStyle', 'shapeStyle', 'textContent', 'styleChoices')):
            from . import scientific_geometry_style
            names += ''.join(f' {key}' for key in ('pointStyle', 'shapeStyle', 'textContent', 'styleChoices') if key in layer)
            try:
                scientific_geometry_style.described(layer)
            except ValueError:
                require(False)
        if type(layer) is dict and 'surfaceAppearance' in layer:
            names += ' surfaceAppearance'
            require(layer.get('type') == 'surface')
            scientific_surface_appearance.settings(layer['surfaceAppearance'])
        if type(layer) is dict and 'pathAppearance' in layer:
            names += ' pathAppearance'
            scientific_path_appearance.settings(layer['pathAppearance'],layer.get('type'))
        if type(layer) is dict and 'imageSampling' in layer:
            names += ' imageSampling'
            require(layer.get('type') == 'image')
            scientific_image_sampling.settings(layer['imageSampling'])
        if type(layer) is dict and 'clippingPlanes' in layer:
            names += ' clippingPlanes'
            scientific_clipping.settings(layer['clippingPlanes'])
        if type(layer) is dict and 'imagePlane' in layer:
            names += ' imagePlane'
            require(layer.get('type') == 'image')
            scientific_image_plane.settings(layer['imagePlane'])
        if type(layer) is dict and 'imageHistogram' in layer:
            from . import scientific_image_histogram
            names += ' imageHistogram'
            require(layer.get('type') == 'image')
            scientific_image_histogram.described(layer['imageHistogram'])
        if type(layer) is dict and 'labelsAppearance' in layer:
            from . import scientific_labels_appearance
            names += ' labelsAppearance labelsSelectedColor'
            require(layer.get('type') == 'labels')
            scientific_labels_appearance.settings(layer['labelsAppearance'])
            scientific_labels_appearance.check_selected_color(layer.get('labelsSelectedColor'))
        if type(layer) is dict and 'imageAppearance' in layer:
            names += ' imageAppearance'
            require(layer.get('type') == 'image')
            scientific_image_appearance.settings(layer['imageAppearance'])
        if type(layer) is dict and 'blending' in layer:
            names += ' blending'
            require(type(layer['blending']) is str and layer['blending'] in BLENDING)
        fields(layer, names)
        require(token(layer['id']) and layer['id'] not in seen and text(layer['name'])
                and layer['type'] in LAYER_TYPES and type(layer['visible']) is bool
                and number(layer['opacity']) and 0 <= layer['opacity'] <= 1
                and type(layer['scale']) is list and 2 <= len(layer['scale']) <= ndim)
        seen.add(layer['id'])
        vector(layer['scale'], len(layer['scale']))
        vector(layer['translate'], len(layer['scale']))
        if 'viewKey' in layer: geometry(layer['viewGeometry'], len(layer['scale']))


def inventory(value):
    fields(value, 'format layers')
    require(value['format'] == 'scientific-layers-v1')
    bounded(value, MAX_BYTES)
    layer_rows(value['layers'], 8)
    return copy.deepcopy(value)
