"""Bounded presentation intent for one exact retained scientific revision."""
from __future__ import annotations

import copy
import math
import re

from . import scientific_text_appearance, scientific_grid, scientific_slices, scientific_point_appearance, scientific_surface_appearance, scientific_path_appearance, scientific_colormap, scientific_image_sampling, scientific_clipping, scientific_image_plane, scientific_image_appearance
from .scientific_view_wire import bounded, fields, integer, number, require, text, vector, geometry, LAYER_TYPES, BLENDING


def preferences(value):
    fields(value, 'schema dimensions camera layers' + (' grid' if type(value) is dict and type(value.get('schema')) is int and value['schema'] >= 12 and 'grid' in value else ''))
    if 'grid' in value: scientific_grid.settings(value['grid'])
    require(type(value['schema']) is int and value['schema'] in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14))
    bounded(value, 64 << 10)
    dims = value['dimensions']
    fields(dims, 'order ndisplay point labels ranges' + (' margins' if value['schema'] >= 11 and type(dims) is dict and 'margins' in dims else ''))
    order = dims['order']
    require(type(order) is list and 2 <= len(order) <= 8 and all(integer(n, 0, 7) for n in order)
            and sorted(order) == list(range(len(order))) and integer(dims['ndisplay'], 2, 3)
            and dims['ndisplay'] <= len(order))
    if 'margins' in dims:
        scientific_slices.margins(dims['margins'], len(order))
    for key in ('point', 'labels', 'ranges'):
        require(type(dims[key]) is list and len(dims[key]) == len(order))
    require(all(text(label) for label in dims['labels']))
    for bounds, point in zip(dims['ranges'], dims['point']):
        vector(bounds, 3)
        require(number(point) and bounds[0] <= point <= bounds[1] and bounds[2] > 0)
    camera = value['camera']
    fields(camera, 'center zoom orientation angles perspective')
    vector(camera['center'], 3)
    vector(camera['angles'], 3)
    require(number(camera['zoom']) and 1e-6 <= camera['zoom'] <= 1e6
            and number(camera['perspective']) and 0 <= camera['perspective'] < 180
            and type(camera['orientation']) is list and len(camera['orientation']) == 3
            and all(type(v) is str and v in options for v, options in zip(camera['orientation'],
                (('towards', 'away'), ('up', 'down'), ('left', 'right')))))
    require(type(value['layers']) is list and len(value['layers']) <= 64)
    seen = set()
    for layer in value['layers']:
        require(type(layer) is dict)
        appearance = (value['schema'] == 2 and layer.get('type') == 'image') or (value['schema'] >= 3 and 'imageAppearance' in layer)
        projection = value['schema'] >= 11 and 'imageProjection' in layer
        text_display = value['schema'] >= 13 and 'textAppearance' in layer
        labels_display = value['schema'] >= 14 and 'labelsAppearance' in layer
        point = value['schema'] >= 10 and 'pointAppearance' in layer
        surface = value['schema'] >= 9 and 'surfaceAppearance' in layer
        path = value['schema'] >= 8 and 'pathAppearance' in layer
        colormap = value['schema'] >= 7 and 'imageColormap' in layer
        sampling = value['schema'] >= 6 and 'imageSampling' in layer
        clipping = value['schema'] >= 5 and 'clippingPlanes' in layer
        plane = value['schema'] >= 4 and 'imagePlane' in layer
        blending = value['schema'] >= 3 and 'blending' in layer
        fields(layer, 'key type visible opacity scale translate geometry' + (' imageAppearance' if appearance else '') + (' blending' if blending else '') + (' imagePlane' if plane else '') + (' clippingPlanes' if clipping else '') + (' imageSampling' if sampling else '') + (' imageColormap' if colormap else '') + (' pathAppearance' if path else '') + (' surfaceAppearance' if surface else '') + (' pointAppearance' if point else '') + (' imageProjection' if projection else '') + (' textAppearance' if text_display else '') + (' labelsAppearance' if labels_display else ''))
        if labels_display:
            from . import scientific_labels_appearance
            require(layer.get('type') == 'labels')
            if scientific_labels_appearance.settings(layer['labelsAppearance'])['autoSource'] == 'other':
                raise ValueError('These automatic label colors were changed in Python. Choose original colors or new colors from a seed before remembering this view.')
        if projection:
            require(layer.get('type') == 'image')
            scientific_slices.projection(layer['imageProjection'])
        if text_display:
            require(layer.get('type') in ('points','shapes') and type(layer.get('scale')) is list)
            scientific_text_appearance.settings(layer['textAppearance'],len(layer['scale']))
        if point:
            require(layer.get('type') == 'points')
            scientific_point_appearance.settings(layer['pointAppearance'])
        if surface:
            require(layer.get('type') == 'surface')
            scientific_surface_appearance.settings(layer['surfaceAppearance'])
        if path:
            scientific_path_appearance.settings(layer['pathAppearance'],layer.get('type'))
        if colormap:
            require(layer.get('type') == 'image')
            scientific_colormap.settings(layer['imageColormap'])
        if sampling:
            require(layer.get('type') == 'image')
            scientific_image_sampling.settings(layer['imageSampling'])
        if clipping:
            scientific_clipping.settings(layer['clippingPlanes'])
        if plane:
            require(layer.get('type') == 'image')
            scientific_image_plane.settings(layer['imagePlane'])
        if blending:
            require(type(layer['blending']) is str and layer['blending'] in BLENDING)
        if appearance:
            require(layer.get('type') == 'image')
            scientific_image_appearance.settings(layer['imageAppearance'])
        require(type(layer['key']) is str and re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', layer['key']) is not None
                and layer['key'] not in seen and type(layer['type']) is str and layer['type'] in LAYER_TYPES
                and type(layer['visible']) is bool and number(layer['opacity']) and 0 <= layer['opacity'] <= 1
                and type(layer['scale']) is list and 2 <= len(layer['scale']) <= len(order))
        seen.add(layer['key'])
        vector(layer['scale'], len(layer['scale']))
        vector(layer['translate'], len(layer['scale']))
        geometry(layer['geometry'], len(layer['scale']))
    return copy.deepcopy(value)


def from_frame(frame):
    if any('viewKey' not in row for row in frame['layers']):
        raise ValueError('This view contains temporary layers. The integration must declare their identities before remembering the complete view.')
    return preferences({**({'grid': frame['grid']} if 'grid' in frame else {}), 'schema': 14 if any('labelsAppearance' in row for row in frame['layers']) else 13 if any('textAppearance' in row for row in frame['layers']) else 12 if 'grid' in frame else 11 if 'margins' in frame['dimensions'] or any('imageProjection' in row for row in frame['layers']) else 10 if any('pointAppearance' in row for row in frame['layers']) else 9 if any('surfaceAppearance' in row for row in frame['layers']) else 8 if any('pathAppearance' in row for row in frame['layers']) else 7 if any('imageColormap' in row for row in frame['layers']) else 6 if any('imageSampling' in row for row in frame['layers']) else 5 if any('clippingPlanes' in row for row in frame['layers']) else 4 if any('imagePlane' in row for row in frame['layers']) else 3 if any('blending' in row for row in frame['layers']) else 2 if any('imageAppearance' in row for row in frame['layers']) else 1,
        'dimensions': {key: frame['dimensions'][key] for key in ('order', 'ndisplay', 'point', 'labels', 'ranges', 'margins') if key in frame['dimensions']},
        'camera': frame['camera'],
        'layers': [{'key': row['viewKey'], 'geometry': row['viewGeometry'], **({'labelsAppearance': row['labelsAppearance']} if 'labelsAppearance' in row else {}), **({'imageProjection': row['imageProjection']} if 'imageProjection' in row else {}), **({'textAppearance': row['textAppearance']} if 'textAppearance' in row else {}), **({'pointAppearance': row['pointAppearance']} if 'pointAppearance' in row else {}), **({'surfaceAppearance': row['surfaceAppearance']} if 'surfaceAppearance' in row else {}), **({'pathAppearance': row['pathAppearance']} if 'pathAppearance' in row else {}), **({'imageColormap': row['imageColormap']} if 'imageColormap' in row else {}), **({'imageSampling': row['imageSampling']} if 'imageSampling' in row else {}), **({'clippingPlanes': row['clippingPlanes']} if 'clippingPlanes' in row else {}), **({'imagePlane': row['imagePlane']} if 'imagePlane' in row else {}), **({'blending': row['blending']} if 'blending' in row else {}), **({'imageAppearance': row['imageAppearance']} if 'imageAppearance' in row else {}), **{key: row[key] for key in ('type', 'visible', 'opacity', 'scale', 'translate')}}
                   for row in frame['layers']]})


def compatible(value, frame, *, ranges=None):
    saved, current = preferences(value), from_frame(frame)
    if ranges is not None:
        current['dimensions']['ranges'] = ranges
    if any(saved['dimensions'][key] != current['dimensions'][key] for key in ('labels', 'ranges')):
        raise ValueError('The remembered axes or calibrated ranges no longer match this view. Start with its default view.')
    def identity(row):
        return {key: row[key] for key in ('key', 'type', 'scale', 'translate', 'geometry')}
    if sorted((identity(row) for row in saved['layers']), key=lambda row: row['key']) != sorted(
            (identity(row) for row in current['layers']), key=lambda row: row['key']):
        raise ValueError('The remembered layers or transforms no longer match this integration. Start with its default view.')
    if 'margins' in saved['dimensions'] and 'margins' not in current['dimensions']:
        raise ValueError('This integration cannot restore remembered slice thickness. Start with its default view.')
    if 'margins' not in saved['dimensions'] and 'margins' in current['dimensions']:
        saved['dimensions']['margins'] = copy.deepcopy(current['dimensions']['margins'])
    if 'grid' in saved and 'grid' not in current:
        raise ValueError('This integration cannot restore its remembered grid. Start with its default view.')
    if 'grid' not in saved and 'grid' in current:
        saved['grid'] = copy.deepcopy(current['grid'])
    current_layers = {row['key']: row for row in current['layers']}
    for row in saved['layers']:
        present = current_layers[row['key']]
        if 'imageProjection' in row and 'imageProjection' not in present:
            raise ValueError('This integration cannot restore the remembered image projection. Start with its default view.')
        if 'imageProjection' not in row and 'imageProjection' in present:
            row['imageProjection'] = present['imageProjection']
        if 'labelsAppearance' in row and 'labelsAppearance' not in present:
            raise ValueError('This integration cannot restore the remembered label display. Start with its default view.')
        if 'labelsAppearance' not in row and 'labelsAppearance' in present:
            row['labelsAppearance'] = copy.deepcopy(present['labelsAppearance'])
        if 'textAppearance' in row and 'textAppearance' not in present:
            raise ValueError('This integration cannot restore remembered feature text. Start with its default view.')
        if 'textAppearance' not in row and 'textAppearance' in present:
            row['textAppearance'] = copy.deepcopy(present['textAppearance'])
        if 'pointAppearance' in row and 'pointAppearance' not in present:
            raise ValueError('This integration cannot restore the remembered point display. Start with its default view.')
        if 'pointAppearance' not in row and 'pointAppearance' in present:
            row['pointAppearance'] = copy.deepcopy(present['pointAppearance'])
        if 'surfaceAppearance' in row and 'surfaceAppearance' not in present:
            raise ValueError('This integration cannot restore the remembered surface display. Start with its default view.')
        if 'surfaceAppearance' not in row and 'surfaceAppearance' in present:
            row['surfaceAppearance'] = copy.deepcopy(present['surfaceAppearance'])
        if 'pathAppearance' in row and 'pathAppearance' not in present:
            raise ValueError('This integration cannot restore the remembered path display. Start with its default view.')
        if 'pathAppearance' not in row and 'pathAppearance' in present:
            row['pathAppearance'] = copy.deepcopy(present['pathAppearance'])
        if 'imageColormap' in row and 'imageColormap' not in present:
            raise ValueError('This integration cannot restore the remembered colors. Start with its default view.')
        if 'imageColormap' not in row and 'imageColormap' in present:
            row['imageColormap'] = copy.deepcopy(present['imageColormap'])
        if 'imageSampling' in row and 'imageSampling' not in present:
            raise ValueError('This integration cannot restore the remembered interpolation. Start with its default view.')
        if 'imageSampling' not in row and 'imageSampling' in present:
            row['imageSampling'] = copy.deepcopy(present['imageSampling'])
        if 'clippingPlanes' in row and 'clippingPlanes' not in present:
            raise ValueError('This integration cannot restore the remembered clipping planes. Start with its default view.')
        if 'clippingPlanes' not in row and 'clippingPlanes' in present:
            row['clippingPlanes'] = copy.deepcopy(present['clippingPlanes'])
        if 'imagePlane' in row and 'imagePlane' not in present:
            raise ValueError('This integration cannot restore the remembered image plane. Start with its default view.')
        if 'imagePlane' not in row and 'imagePlane' in present:
            row['imagePlane'] = copy.deepcopy(present['imagePlane'])
        if 'blending' in row and 'blending' not in present:
            raise ValueError('This integration cannot restore the remembered layer blending. Start with its default view.')
        if 'blending' not in row and 'blending' in present:
            row['blending'] = present['blending']
        if 'imageAppearance' in row and 'imageAppearance' not in present:
            raise ValueError('This integration cannot restore the remembered image appearance. Start with its default view.')
        if 'imageAppearance' not in row and 'imageAppearance' in present:
            row['imageAppearance'] = copy.deepcopy(present['imageAppearance'])
    saved['schema'] = current['schema']
    return preferences(saved)


def restored(expected, actual):
    """Automatic contrast is derived from the restored slice, not saved intent."""
    wanted, shown = preferences(expected), preferences(actual)
    for value in (wanted, shown):
        for row in value['layers']:
            surface = row.get('surfaceAppearance')
            if surface and surface['autoContrast']:surface.pop('contrastLimits')
            appearance = row.get('imageAppearance')
            if appearance and appearance['autoContrast']:
                appearance.pop('contrastLimits')
    for left, right in zip(wanted['layers'], shown['layers']):
        if 'clippingPlanes' in left and 'clippingPlanes' in right:
            scientific_clipping.same_normals(left['clippingPlanes'], right['clippingPlanes'])
        if 'imagePlane' in left and 'imagePlane' in right:
            a, b = left['imagePlane']['normal'], right['imagePlane']['normal']
            # Native plane setters normalize unit normals again; retain the
            # direction while allowing their floating-point roundoff only.
            if all(math.isclose(x, y, rel_tol=1e-12, abs_tol=1e-12) for x,y in zip(a,b)):
                right['imagePlane']['normal'] = a
    return wanted == shown


def restored_frame_budget(settings, frame):
    """Reserve the entire pending presentation, including native numeric expansion.

    This is a size projection only. The returned placeholders grant no authority
    and must never be sent to a renderer or saved as scientific preferences.
    """
    def expanded(value):
        if type(value) in (int,float): return 'x'*24
        if type(value) is list: return [expanded(item) for item in value]
        if type(value) is dict: return {key:expanded(item) for key,item in value.items()}
        return value
    projected=copy.deepcopy(frame)
    if 'grid' in settings: projected['grid']=expanded(settings['grid'])
    projected['camera']=expanded(settings['camera'])
    projected['dimensions'].update(expanded(settings['dimensions']))
    current={row.get('viewKey'):row for row in projected['layers']}
    for row in settings['layers']:
        current[row['key']].update(expanded({key:value for key,value in row.items() if key not in ('key','geometry')}))
    return projected
