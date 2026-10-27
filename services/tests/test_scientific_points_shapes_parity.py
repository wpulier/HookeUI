"""Points and Shapes styling in Hooke's actual review worker against plain pinned napari and independent values.

Opt-in: NAPARI_HOST_PREPARATION (Hooke's prepared review bundle) and NAPARI_REFERENCE_PYTHON (the same pinned
napari, run plain). Without them every case skips and the acceptance runner reports its children missing.
Hooke is driven only through its ordinary worker requests (the frame-bound changes the Studio sends, its
inspection and its Python console); the reference runs napari's public API. Renders use one viewport and an
explicit camera, so each data position lands on a known canvas pixel. Independent values are computed here with
numpy and Python alone: cycle colors by first appearance, gray colormap levels, marker areas, edge widths, the
front shape at an overlap and format strings. This is actual-worker evidence; it is not browser, clean-machine or
researcher qualification.
"""
import json
import os
from pathlib import Path

import numpy as np
import pytest

from tests.scientific_parity import (HookeSide, pinned_source_check, reference_run, runtime_identity, same_render,
                                     screen_of)
from tests.test_native_regions import source
from tests.test_scientific_host_worker import actual_host  # noqa: F401

WIDTH = HEIGHT = 256
ZOOM = 8
CENTER = (11.5, 13.5)  # the middle of a 24 x 28 plane
CAMERA = {'center_yx': CENTER, 'zoom': ZOOM, 'width': WIDTH, 'height': HEIGHT}
SHAPE = (3, 24, 28)
SYMBOLS = ('arrow', 'clobber', 'cross', 'diamond', 'disc', 'hbar', 'ring', 'square', 'star', 'tailed_arrow',
           'triangle_down', 'triangle_up', 'vbar', 'x')
CYCLE = [[1.0, 0.0, 0.0, 1.0], [0.0, 1.0, 0.0, 1.0], [0.0, 0.0, 1.0, 1.0]]
ANCHORS = ('center', 'upper_left', 'upper_right', 'lower_left', 'lower_right')
BIG = 2**60 + 3

IMAGE_CODE = f"""
import numpy as np
values = np.zeros({SHAPE}, dtype=np.uint16)
"""
POINT_ROWS = [(0, 4, 4), (0, 4, 14), (0, 12, 8), (1, 12, 14), (1, 18, 20), (2, 18, 6)]
KINDS = ['b', 'a', 'c', 'a', 'b', 'c']
AREAS = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
POINTS_CODE = f"""
import numpy as np
import pandas as pd
features = pd.DataFrame({{'kind': pd.Categorical({KINDS!r}), 'area': {AREAS!r},
                         'id': np.array([1, 7, {BIG}, 9, 10, 11], dtype=np.uint64)}})
viewer.add_points(np.array({POINT_ROWS!r}, dtype=float), name='Spots', features=features, size=3, symbol='disc',
                  face_color='white', border_width=0, border_color='red')
"""
SHAPE_TYPES = ['rectangle', 'rectangle', 'ellipse', 'line', 'path', 'polygon']
SHAPE_DATA = [
    [[0, 2, 2], [0, 2, 12], [0, 10, 12], [0, 10, 2]],    # A
    [[0, 6, 8], [0, 6, 18], [0, 14, 18], [0, 14, 8]],    # B, over A at y 6-10, x 8-12
    [[0, 14, 2], [0, 14, 10], [0, 22, 10], [0, 22, 2]],  # an ellipse's bounding box
    [[0, 4, 20], [0, 12, 26]],
    [[0, 16, 13], [0, 19, 17], [0, 16, 21]],
    [[0, 19, 22], [0, 23, 27], [0, 23, 21]],
]
SHAPE_KINDS = ['x', 'y', 'x', 'z', 'y', 'x']
SHAPE_VALUES = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
SHAPES_CODE = f"""
import numpy as np
import pandas as pd
outline_features = pd.DataFrame({{'kind': {SHAPE_KINDS!r}, 'v': {SHAPE_VALUES!r}}})
viewer.add_shapes([np.array(s, dtype=float) for s in {SHAPE_DATA!r}], shape_type={SHAPE_TYPES!r}, name='Outlines',
                  features=outline_features, edge_width=0.5, face_color='white', edge_color='red')
"""


def view_code(z):
    return f"""
viewer.dims.order = (0, 1, 2)
viewer.dims.ndisplay = 2
viewer.dims.point = ({float(z)}, 0.0, 0.0)
viewer.camera.center = (viewer.camera.center[0], {CENTER[0]}, {CENTER[1]})
viewer.camera.angles = (0, 0, 0)
viewer.camera.zoom = {ZOOM}
"""


def intensity(values):
    native = source(values, [], axes=('z', 'y', 'x'))
    native.descriptor['semantic'] = 'intensity'
    return native


def canvas(data_yx):
    row, column = screen_of(data_yx, **CAMERA)
    return (row + .5, column + .5)


def hooke_view(hooke, z):
    hooke.render()
    hooke.change('dimensions', {'order': [0, 1, 2], 'ndisplay': 2, 'steps': [z, 0, 0]})
    image = hooke.layer('Native input')
    hooke.change('layer-presentation', {'layer': image['id'], 'visible': False, 'opacity': 1})
    hooke.change('camera', {'center': [hooke.frame['camera']['center'][0], *CENTER], 'zoom': ZOOM, 'angles': [0, 0, 0]})


def record_run(record_property, paths, tolerance):
    """The frozen conditions this run compared under, kept with its JUnit evidence."""
    executable = Path(os.environ['NAPARI_REFERENCE_PYTHON']).absolute()
    record_property('comparison', json.dumps({
        'runtime': runtime_identity(executable), 'pinnedSources': pinned_source_check(executable, paths),
        'viewport': [WIDTH, HEIGHT], 'camera': {'center': CENTER, 'zoom': ZOOM}, 'tolerance': tolerance,
        'cache': 'a fresh Hooke worker and a fresh reference process per run'}, sort_keys=True))


def colors_code(layer):
    return f"""
def colors(side):
    import numpy as np
    layer = viewer.layers[{layer!r}]
    r = lambda a: [[round(float(v), 6) for v in row] for row in np.asarray(a)]
    limits = getattr(layer, side + '_contrast_limits')
    if layer._type_string == 'points':
        manager = getattr(layer, '_' + side)
        prop = None if manager.color_properties is None else manager.color_properties.name
        mapping = {{str(k): [round(float(v), 6) for v in c] for k, c in manager.categorical_colormap.colormap.items()}}
    else:
        prop, mapping = getattr(layer, '_' + side + '_color_property') or None, None
    return {{'mode': str(getattr(layer, side + '_color_mode')), 'colors': r(getattr(layer, side + '_color')), 'property': prop,
            'cycle': r(getattr(layer, side + '_color_cycle')), 'colormap': getattr(layer, side + '_colormap').name,
            'limits': None if limits is None else [float(v) for v in limits], 'mapping': mapping}}
"""


TEXT_CODE = """
def text(layer):
    import numpy as np
    t = layer.text
    constant = getattr(t.color, 'constant', None)
    return {'string': type(t.string).__name__, 'values': [str(v) for v in np.broadcast_to(t.string(layer.features), (len(layer.data),))],
            'color': type(t.color).__name__, 'constant': None if constant is None else [float(v) for v in constant],
            'visible': bool(t.visible), 'size': float(t.size), 'anchor': str(t.anchor), 'rotation': float(t.rotation),
            'translation': np.asarray(t.translation, dtype=float).tolist(), 'blending': str(t.blending), 'scaling': bool(t.scaling)}
"""
OBSERVE_POINTS = colors_code('Spots') + TEXT_CODE + """
import json, hashlib, numpy as np
spots = viewer.layers['Spots']
print(json.dumps({'symbol': [str(s.value) for s in spots.symbol], 'size': [float(v) for v in spots.size],
    'borderWidth': [float(v) for v in spots.border_width], 'relative': bool(spots.border_width_is_relative),
    'face': colors('face'), 'border': colors('border'), 'text': text(spots), 'shading': str(spots.shading),
    'selected': sorted(int(i) for i in spots.selected_data), 'step': [int(v) for v in viewer.dims.current_step],
    'data': hashlib.sha256(np.ascontiguousarray(spots.data).tobytes()).hexdigest(),
    'features': hashlib.sha256(spots.features.to_csv().encode()).hexdigest()}))
"""
OBSERVE_SHAPES = colors_code('Outlines') + TEXT_CODE + """
import json, hashlib, numpy as np
outlines = viewer.layers['Outlines']
print(json.dumps({'edgeWidth': [float(v) for v in outlines.edge_width], 'z': [int(v) for v in outlines.z_index],
    'types': [str(t) for t in outlines.shape_type], 'n': int(outlines.nshapes),
    'face': colors('face'), 'edge': colors('edge'), 'text': text(outlines),
    'selected': sorted(int(i) for i in outlines.selected_data),
    'data': hashlib.sha256(b''.join(np.ascontiguousarray(d).tobytes() for d in outlines.data)).hexdigest(),
    'features': hashlib.sha256(outlines.features.to_csv().encode()).hexdigest()}))
"""


def first_appearance(values, cycle):
    """Independent of napari: each category takes the next cycle color in order of first appearance."""
    order = list(dict.fromkeys(values))
    return [cycle[order.index(v) % len(cycle)] for v in values]


def gray(values, low, high):
    return [[float(np.clip((v - low) / (high - low), 0, 1))] * 3 + [1.0] for v in values]


def marker_area(pixels, data_yx, background, reach=14):
    """Canvas pixels at least half covered by the marker: the antialiasing fringe (1 pixel wide by default) counts
    in proportion to its coverage, so a disc of diameter 16 covers about pi * 8 ** 2 pixels."""
    row, column = screen_of(data_yx, **CAMERA)
    window = pixels[row - reach:row + reach, column - reach:column + reach, :3].astype(int)
    return int((np.abs(window - background).max(axis=2) > 127).sum())


# name, Hooke's operation, the settings patch its control changes, and napari's public API doing the same
POINT_STEPS = [
    *[(f'symbol-{symbol}', 'points-style', {'symbol': symbol, 'size': 2.0}, f"spots.symbol = {symbol!r}\nspots.size = 2")
      for symbol in SYMBOLS],
    ('square-again', 'points-style', {'symbol': 'square', 'size': 2.0}, "spots.symbol = 'square'\nspots.size = 2"),
    ('border-relative', 'points-style', {'borderWidth': 0.25, 'border': {'mode': 'direct', 'color': [1.0, 0.0, 0.0, 1.0]}},
     "spots.border_width = 0.25\nspots.border_color = [1, 0, 0, 1]"),
    ('border-absolute', 'points-style', {'borderWidth': 0.5, 'borderWidthIsRelative': False},
     "spots.border_width_is_relative = False\nspots.border_width = 0.5"),
    ('face-cycle', 'points-style', {'face': {'mode': 'cycle', 'feature': 'kind', 'cycle': CYCLE}},
     f"spots.face_color = 'kind'\nspots.face_color_cycle = {CYCLE!r}\nspots.face_color_mode = 'cycle'"),
    ('face-colormap', 'points-style', {'face': {'mode': 'colormap', 'feature': 'area', 'colormap': 'gray', 'contrastLimits': [0.0, 6.0]}},
     "spots.face_color = 'area'\nspots.face_colormap = 'gray'\nspots.face_contrast_limits = (0.0, 6.0)\nspots.face_color_mode = 'colormap'"),
    ('face-colormap-range', 'points-style', {'face': {'mode': 'colormap', 'feature': 'area', 'colormap': 'viridis', 'contrastLimits': None}},
     "spots.face_color = 'area'\nspots.face_colormap = 'viridis'\nspots.face_contrast_limits = (float(spots.features['area'].min()), float(spots.features['area'].max()))\nspots.face_color_mode = 'colormap'"),
    ('border-cycle', 'points-style', {'border': {'mode': 'cycle', 'feature': 'kind', 'cycle': CYCLE[::-1]}},
     f"spots.border_color = 'kind'\nspots.border_color_cycle = {CYCLE[::-1]!r}\nspots.border_color_mode = 'cycle'"),
    ('face-direct', 'points-style', {'face': {'mode': 'direct', 'color': [0.0, 0.0, 1.0, 1.0]}}, "spots.face_color = [0, 0, 1, 1]"),
    ('text-feature', 'text-content', {'string': {'kind': 'feature', 'value': 'kind'}, 'color': [1.0, 1.0, 0.0, 1.0]},
     "spots.text.string = {'feature': 'kind'}\nspots.text.color = [1, 1, 0, 1]"),
    ('text-format', 'text-content', {'string': {'kind': 'format', 'value': '{kind}: {area:.1f}'}, 'color': [1.0, 1.0, 0.0, 1.0]},
     "spots.text.string = {'format': '{kind}: {area:.1f}'}"),
    ('text-constant', 'text-content', {'string': {'kind': 'constant', 'value': 'cell'}, 'color': [0.0, 1.0, 1.0, 1.0]},
     "spots.text.string = {'constant': 'cell'}\nspots.text.color = [0, 1, 1, 1]"),
    *[(f'anchor-{anchor}', 'text-presentation', {'anchor': anchor}, f"spots.text.anchor = {anchor!r}") for anchor in ANCHORS],
    ('text-display', 'text-presentation', {'size': 14.0, 'rotation': 30.0, 'translation': 1.5, 'blending': 'additive', 'scaling': False},
     "spots.text.size = 14\nspots.text.rotation = 30\nspots.text.translation = 1.5\nspots.text.blending = 'additive'\nspots.text.scaling = False"),
    ('text-hidden', 'text-presentation', {'visible': False}, "spots.text.visible = False"),
    ('shading-spherical', 'point-presentation', {'shading': 'spherical'}, "spots.shading = 'spherical'"),
    ('shading-none', 'point-presentation', {'shading': 'none'}, "spots.shading = 'none'"),
    ('select-z0', 'points-selection', {'step': 0, 'action': 'replace', 'index': 0},
     "viewer.dims.set_current_step(0, 0)\nspots.selected_data = {0}"),
    ('select-z1', 'points-selection', {'step': 1, 'action': 'toggle', 'index': 3},
     "viewer.dims.set_current_step(0, 1)\nspots.selected_data = set(spots.selected_data) | {3}"),
]


def apply_point_step(hooke, spots, operation, patch):
    layer = hooke.layer('Spots')
    if operation == 'points-style':
        return hooke.change(operation, {'layer': spots, 'settings': {**layer['pointStyle'], **patch}})
    if operation == 'text-content':
        return hooke.change(operation, {'layer': spots, 'settings': patch})
    if operation == 'point-presentation':
        return hooke.change(operation, {'layer': spots, 'settings': {**layer['pointAppearance'], **patch}})
    if operation == 'text-presentation':
        return hooke.change(operation, {'layer': spots, 'settings': {**layer['textAppearance'], **patch}})
    hooke.change('dimensions', {'order': [0, 1, 2], 'ndisplay': 2, 'steps': [patch['step'], 0, 0]})
    return hooke.change('points-selection', {'layer': spots, 'action': patch['action'], 'index': patch['index']})


def run_points(actual_host, tmp_path, check_independent=True):
    from orchestrator import scientific_host as parent
    values = np.zeros(SHAPE, dtype=np.uint16)
    scene = IMAGE_CODE + "viewer.add_image(values, name='Native input')\n" + POINTS_CODE + view_code(0) \
        + "viewer.layers['Native input'].visible = False\nspots = viewer.layers['Spots']\nspots.opacity = 1\n"
    reference_report = reference_run(scene, [{'apply': '', 'observe': OBSERVE_POINTS, 'render': True}]
                              + [{'apply': code, 'observe': OBSERVE_POINTS, 'render': True} for *_, code in POINT_STEPS],
                              width=WIDTH, height=HEIGHT, work=tmp_path / 'reference')
    reference = reference_report['results']
    with parent.review(actual_host['key'], Path(actual_host['folder']).name, {'volume': intensity(values)}) as worker:
        hooke = HookeSide(worker, WIDTH, HEIGHT)
        hooke.admit(reference_report)
        hooke.render()
        hooke.python(POINTS_CODE)
        hooke_view(hooke, 0)
        spots = hooke.layer('Spots')['id']
        _, pixels = hooke.change('layer-presentation', {'layer': spots, 'visible': True, 'opacity': 1})
        background = pixels[0, 0, :3].astype(int)
        first = hooke.observe(OBSERVE_POINTS)
        assert first == reference[0]['observed']
        same_render(pixels, reference[0]['pixels'])
        for index, (name, operation, patch, _) in enumerate(POINT_STEPS, start=1):
            frame, pixels = apply_point_step(hooke, spots, operation, patch)
            row = hooke.layer('Spots')
            observed = hooke.observe(OBSERVE_POINTS)
            assert observed == reference[index]['observed'], name
            assert (observed['data'], observed['features']) == (first['data'], first['features'])  # never written
            same_render(pixels, reference[index]['pixels'])
            if operation == 'points-style':  # the frame describes exactly what napari now holds
                shown = row['pointStyle']
                for key, value in patch.items():
                    if key in ('face', 'border') and value['mode'] == 'colormap' and value['contrastLimits'] is None:
                        assert shown[key]['contrastLimits'] == [min(AREAS), max(AREAS)], name  # napari's data range
                        continue
                    assert shown[key] == value, (name, key)
            if operation == 'text-content':
                assert row['textContent'] == patch, name
            if not check_independent:
                continue
            shown_points = [(i, p) for i, p in enumerate(POINT_ROWS) if p[0] == observed['step'][0]]
            if name.startswith('symbol-') or name == 'square-again':
                symbol = patch['symbol']
                assert observed['symbol'] == [symbol] * 6 and observed['size'] == [2.0] * 6
                for _, (_, y, x) in shown_points:  # a size of 2 data units at zoom 8 is 16 canvas pixels across
                    area = marker_area(pixels, (y, x), background)
                    if symbol == 'square':
                        assert abs(area - 16 * 16) <= 0.15 * 256, (name, area)
                    elif symbol == 'disc':
                        assert abs(area - np.pi * 8 * 8) <= 0.15 * np.pi * 64, (name, area)
                    else:
                        assert 0 < area <= 1.15 * 256, (name, area)
            if name in ('border-relative', 'border-absolute'):
                # A quarter of a 2-unit size, or an absolute 0.5 units: 4 canvas pixels at zoom 8, centered on the
                # square's edge 8 pixels out, so red from 6 to 10 pixels; the face keeps white inside, background beyond.
                assert observed['borderWidth'] == [0.25 if name == 'border-relative' else 0.5] * 6
                assert observed['relative'] is (name == 'border-relative')
                # The point's center falls on a canvas pixel corner: pixel c + k has its center k + 0.5 pixels away, and
                # pixel c - 1 - k too. Sample whole pixels by that distance, in all four directions.
                for _, (_, y, x) in shown_points:
                    row_, column = screen_of((y, x), **CAMERA)
                    for distance, wanted in ((2.5, [255, 255, 255]), (7.5, [255, 0, 0]), (8.5, [255, 0, 0]), (12.5, background)):
                        k = int(distance - .5)
                        for shown in (pixels[row_, column + k, :3], pixels[row_, column - 1 - k, :3],
                                      pixels[row_ + k, column, :3], pixels[row_ - 1 - k, column, :3]):
                            assert np.abs(shown.astype(int) - wanted).max() <= 1, (name, distance, shown)
            if name == 'face-cycle':
                wanted = first_appearance(KINDS, CYCLE)
                assert observed['face']['colors'] == wanted and observed['face']['mode'] == 'cycle'
                for i, (_, y, x) in shown_points:
                    assert np.abs(pixels[screen_of((y, x), **CAMERA)][:3].astype(int) - 255 * np.array(wanted[i][:3])).max() <= 1, name
            if name == 'face-colormap':
                wanted = gray(AREAS, 0.0, 6.0)
                assert np.allclose(observed['face']['colors'], wanted, atol=1e-6) and observed['face']['limits'] == [0.0, 6.0]
                for i, (_, y, x) in shown_points:
                    assert np.abs(pixels[screen_of((y, x), **CAMERA)][:3].astype(int) - 255 * np.array(wanted[i][:3])).max() <= 1, name
            if name == 'face-colormap-range':
                assert observed['face']['limits'] == [1.0, 6.0] and observed['face']['colormap'] == 'viridis'
            if name == 'border-cycle':
                assert observed['border']['colors'] == first_appearance(KINDS, CYCLE[::-1])
            if name == 'face-direct':
                assert observed['face']['colors'] == [[0.0, 0.0, 1.0, 1.0]] * 6 and observed['face']['mode'] == 'direct'
            if name == 'text-feature':
                assert observed['text']['values'] == KINDS and observed['text']['constant'] == [1.0, 1.0, 0.0, 1.0]
            if name == 'text-format':
                assert observed['text']['values'] == [f'{k}: {a:.1f}' for k, a in zip(KINDS, AREAS)]
            if name == 'text-constant':
                assert observed['text']['values'] == ['cell'] * 6 and observed['text']['string'] == 'ConstantStringEncoding'
            if name.startswith('anchor-'):
                assert observed['text']['anchor'] == patch['anchor'] and row['textAppearance']['anchor'] == patch['anchor']
            if name == 'text-display':
                assert (observed['text']['size'], observed['text']['rotation'], observed['text']['translation'],
                        observed['text']['blending'], observed['text']['scaling']) == (14.0, 30.0, 1.5, 'additive', False)
            if name == 'text-hidden':  # no text drawn: the render is the markers alone, as before any text was set
                assert not observed['text']['visible']
                same_render(pixels, reference[[n for n, *_ in POINT_STEPS].index('face-direct') + 1]['pixels'])
            if name == 'select-z1':  # a point chosen on slice 0 stays selected when one on slice 1 is added
                assert observed['selected'] == [0, 3] and observed['step'][0] == 1
        # Inspect reads the exact point under the canvas, with its typed uint64 feature.
        hooke.change('dimensions', {'order': [0, 1, 2], 'ndisplay': 2, 'steps': [0, 0, 0]})
        sample = hooke.inspect(spots, canvas((4, 14)))
        assert sample['hit']['index'] == 1 and {f['name']: f['value'] for f in sample['hit']['features']}['id'] == '7'
        native, style = hooke.observe(OBSERVE_POINTS), sample['hit']['style']  # that point's own values, as napari holds them
        assert (style['symbol'], style['size'], style['borderWidth']) == (native['symbol'][1], native['size'][1], native['borderWidth'][1])
        assert [round(v, 6) for v in style['faceColor']] == native['face']['colors'][1]
        assert [round(v, 6) for v in style['borderColor']] == native['border']['colors'][1]
    assert worker.process.poll() is not None


def test_actual_points_style_matches_pinned_napari_and_independent_values(actual_host, tmp_path, record_property):
    record_run(record_property, ['src/napari/layers/points/points.py', 'src/napari/layers/points/_points_constants.py',
                                 'src/napari/layers/utils/color_manager.py', 'src/napari/layers/utils/text_manager.py',
                                 'src/napari/layers/utils/string_encoding.py'],
               {'render': 0, 'values': 'exact', 'color': 1, 'marker area': 0.15})
    run_points(actual_host, tmp_path)


def test_actual_points_border_colormap_and_limits_match_independent_values(actual_host, tmp_path, record_property):
    """A dedicated border oracle: enter a gray map, then narrow its range and observe saturation."""
    from orchestrator import scientific_host as parent
    record_run(record_property, ['src/napari/layers/points/points.py', 'src/napari/layers/utils/color_manager.py',
                                 'src/napari/layers/utils/color_manager_utils.py'], {'render': 0, 'color': 1})
    scene = IMAGE_CODE + "viewer.add_image(values, name='Native input')\n" + POINTS_CODE + view_code(0) \
        + "viewer.layers['Native input'].visible = False\n"
    commands = [f"spots.border_color = 'area'\nspots.border_colormap = 'gray'\nspots.border_contrast_limits = (0, {high})\n"
                "spots.border_color_mode = 'colormap'\nspots.symbol = 'square'\nspots.size = 2\n"
                "spots.border_width_is_relative = False\nspots.border_width = 0.5" for high in (6, 3)]
    reference_report = reference_run(scene, [{'apply': "spots = viewer.layers['Spots']", 'observe': OBSERVE_POINTS}]
                              + [{'apply': code, 'observe': OBSERVE_POINTS, 'render': True} for code in commands],
                              width=WIDTH, height=HEIGHT, work=tmp_path / 'reference')
    reference = reference_report['results']
    values = np.zeros(SHAPE, dtype=np.uint16)
    with parent.review(actual_host['key'], Path(actual_host['folder']).name, {'volume': intensity(values)}) as worker:
        hooke = HookeSide(worker, WIDTH, HEIGHT)
        hooke.admit(reference_report)
        hooke.render()
        hooke.python(POINTS_CODE)
        hooke_view(hooke, 0)
        spots = hooke.layer('Spots')['id']
        for index, high in enumerate((6, 3), start=1):
            settings = {**hooke.layer('Spots')['pointStyle'], 'symbol': 'square', 'size': 2,
                        'borderWidthIsRelative': False, 'borderWidth': 0.5,
                        'border': {'mode': 'colormap', 'feature': 'area', 'colormap': 'gray', 'contrastLimits': [0, high]}}
            _, pixels = hooke.change('points-style', {'layer': spots, 'settings': settings})
            observed = hooke.observe(OBSERVE_POINTS)
            assert observed == reference[index]['observed']
            same_render(pixels, reference[index]['pixels'])
            wanted = gray(AREAS, 0, high)
            assert observed['border']['limits'] == [0, high] and observed['border']['mode'] == 'colormap'
            assert np.allclose(observed['border']['colors'], wanted, atol=1e-6)
            for i, (z, y, x) in enumerate(POINT_ROWS):
                if z == 0:
                    row, column = screen_of((y, x), **CAMERA)
                    assert np.abs(pixels[row, column + 7, :3].astype(int) - 255 * np.array(wanted[i][:3])).max() <= 1
            inspected = hooke.inspect(spots, canvas((4, 14)))
            assert np.allclose(inspected['hit']['style']['borderColor'], wanted[1], atol=1e-6)
    assert worker.process.poll() is not None


@pytest.mark.parametrize('kind', ['points', 'shapes'])
def test_constant_native_colormap_renders_and_inspects_without_losing_limits(actual_host, tmp_path, kind):
    from orchestrator import scientific_host as parent
    points = kind == 'points'
    name, feature = ('Spots', 'area') if points else ('Outlines', 'v')
    code = (POINTS_CODE if points else SHAPES_CODE).replace(repr(AREAS if points else SHAPE_VALUES), repr([7.0] * 6))
    code += (f"layer = viewer.layers[{name!r}]\nlayer.face_color = {feature!r}\n"
             "layer.face_colormap = 'gray'\nlayer.face_contrast_limits = (7.0, 7.0)\nlayer.face_color_mode = 'colormap'\nlayer.opacity = 1\n")
    observe_code = OBSERVE_POINTS if points else OBSERVE_SHAPES
    change = 'layer.size = 4' if points else 'layer.edge_width = 1\nlayer.events.edge_width()'
    scene = IMAGE_CODE + "viewer.add_image(values, name='Native input')\n" + code + view_code(0) \
        + "viewer.layers['Native input'].visible = False\n"
    reference_report = reference_run(scene, [{'apply': change, 'observe': observe_code, 'render': True}],
                              width=WIDTH, height=HEIGHT, work=tmp_path / 'reference')
    reference = reference_report['results'][0]
    with parent.review(actual_host['key'], Path(actual_host['folder']).name,
                       {'volume': intensity(np.zeros(SHAPE, dtype=np.uint16))}) as worker:
        hooke = HookeSide(worker, WIDTH, HEIGHT)
        hooke.admit(reference_report)
        hooke.render()
        hooke.python(code)
        hooke_view(hooke, 0)
        row = hooke.layer(name)
        key = 'pointStyle' if points else 'shapeStyle'
        assert row[key]['face']['contrastLimits'] == [7.0, 7.0]
        _, pixels = hooke.change('points-style' if points else 'shapes-style',
                                 {'layer': row['id'], 'settings': {**row[key], **({'size': 4} if points else {'edgeWidth': 1})}})
        native = hooke.observe(observe_code)
        assert native == reference['observed']
        same_render(pixels, reference['pixels'])
        assert native['face']['limits'] == [7.0, 7.0]
        # Pinned napari's np.interp(v, (v,v), (0,1)) selects the upper gray endpoint: opaque white.
        assert native['face']['colors'] == [[1.0, 1.0, 1.0, 1.0]] * 6
        inspected = hooke.inspect(row['id'], canvas((4, 14) if points else (4, 6)))
        assert inspected['hit']['style']['faceColor'] == [1.0, 1.0, 1.0, 1.0]
    assert worker.process.poll() is not None


SHAPE_STEPS = [
    ('edge-width', 'shapes-style', {'edgeWidth': 1.0}, "outlines.edge_width = 1\noutlines.events.edge_width()  # as napari's width control does: the setter emits nothing"),
    ('face-direct', 'shapes-style', {'face': {'mode': 'direct', 'color': [0.0, 1.0, 0.0, 1.0]}}, "outlines.face_color = [0, 1, 0, 1]"),
    ('edge-direct', 'shapes-style', {'edge': {'mode': 'direct', 'color': [0.0, 0.0, 1.0, 1.0]}}, "outlines.edge_color = [0, 0, 1, 1]"),
    ('face-cycle', 'shapes-style', {'face': {'mode': 'cycle', 'feature': 'kind', 'cycle': CYCLE}},
     f"outlines.face_color = 'kind'\noutlines.face_color_cycle = {CYCLE!r}\noutlines.face_color_mode = 'cycle'"),
    ('edge-cycle', 'shapes-style', {'edge': {'mode': 'cycle', 'feature': 'kind', 'cycle': CYCLE[::-1]}},
     f"outlines.edge_color = 'kind'\noutlines.edge_color_cycle = {CYCLE[::-1]!r}\noutlines.edge_color_mode = 'cycle'"),
    ('face-colormap', 'shapes-style', {'face': {'mode': 'colormap', 'feature': 'v', 'colormap': 'gray', 'contrastLimits': [0.0, 6.0]}},
     "outlines.face_color = 'v'\noutlines.face_colormap = 'gray'\noutlines.face_contrast_limits = (0.0, 6.0)\noutlines.face_color_mode = 'colormap'"),
    ('edge-colormap', 'shapes-style', {'edge': {'mode': 'colormap', 'feature': 'v', 'colormap': 'gray', 'contrastLimits': [0.0, 6.0]}},
     "outlines.edge_color = 'v'\noutlines.edge_colormap = 'gray'\noutlines.edge_contrast_limits = (0.0, 6.0)\noutlines.edge_color_mode = 'colormap'"),
    ('select-a', 'shapes-selection', {'action': 'replace', 'index': 0}, "outlines.selected_data = {0}"),
    ('front', 'shapes-order', 'front', "outlines.move_to_front()"),
    ('back', 'shapes-order', 'back', "outlines.move_to_back()"),
    ('text-format', 'text-content', {'string': {'kind': 'format', 'value': '{kind}={v:.0f}'}, 'color': [1.0, 1.0, 0.0, 1.0]},
     "outlines.text.string = {'format': '{kind}={v:.0f}'}\noutlines.text.color = [1, 1, 0, 1]"),
]


def apply_shape_step(hooke, outlines, operation, patch):
    if operation == 'shapes-style':
        return hooke.change(operation, {'layer': outlines, 'settings': {**hooke.layer('Outlines')['shapeStyle'], **patch}})
    if operation == 'shapes-order':
        return hooke.change(operation, {'layer': outlines, 'action': patch})
    if operation == 'shapes-selection':
        return hooke.change(operation, {'layer': outlines, **patch})
    return hooke.change(operation, {'layer': outlines, 'settings': patch})


def run_shapes(actual_host, tmp_path, check_independent=True):
    from orchestrator import scientific_host as parent
    values = np.zeros(SHAPE, dtype=np.uint16)
    scene = IMAGE_CODE + "viewer.add_image(values, name='Native input')\n" + SHAPES_CODE + view_code(0) \
        + "viewer.layers['Native input'].visible = False\noutlines = viewer.layers['Outlines']\noutlines.opacity = 1\n"
    reference_report = reference_run(scene, [{'apply': '', 'observe': OBSERVE_SHAPES, 'render': True}]
                              + [{'apply': code, 'observe': OBSERVE_SHAPES, 'render': True} for *_, code in SHAPE_STEPS],
                              width=WIDTH, height=HEIGHT, work=tmp_path / 'reference')
    reference = reference_report['results']
    overlap = screen_of((8, 10), **CAMERA)
    with parent.review(actual_host['key'], Path(actual_host['folder']).name, {'volume': intensity(values)}) as worker:
        hooke = HookeSide(worker, WIDTH, HEIGHT)
        hooke.admit(reference_report)
        hooke.render()
        hooke.python(SHAPES_CODE)
        hooke_view(hooke, 0)
        outlines = hooke.layer('Outlines')['id']
        _, pixels = hooke.change('layer-presentation', {'layer': outlines, 'visible': True, 'opacity': 1})
        first = hooke.observe(OBSERVE_SHAPES)
        assert first == reference[0]['observed'] and first['types'] == SHAPE_TYPES and first['n'] == 6
        same_render(pixels, reference[0]['pixels'])
        # Step 1: inspect each shape at a point inside it; Hooke names that shape and its features.
        for index, (y, x) in enumerate([(4, 4), (12, 16), (18, 6), (8, 23), (17.5, 15), (22, 23)]):
            hit = hooke.inspect(outlines, canvas((y, x)))['hit']
            assert hit['index'] == index and hit['geometry'] == SHAPE_TYPES[index], (index, hit)
            assert {f['name']: f['value'] for f in hit['features']}['kind'] == SHAPE_KINDS[index]
            assert hit['style'] == {'edgeWidth': 0.5, 'faceColor': [1.0, 1.0, 1.0, 1.0], 'edgeColor': [1.0, 0.0, 0.0, 1.0], 'z': 0}
        for index, (name, operation, patch, _) in enumerate(SHAPE_STEPS, start=1):
            frame, pixels = apply_shape_step(hooke, outlines, operation, patch)
            observed = hooke.observe(OBSERVE_SHAPES)
            assert observed == reference[index]['observed'], name
            assert (observed['data'], observed['features']) == (first['data'], first['features'])
            same_render(pixels, reference[index]['pixels'])
            if operation == 'shapes-style':
                shown = hooke.layer('Outlines')['shapeStyle']
                assert all(shown[key] == value for key, value in patch.items()), name
            if not check_independent:
                continue
            if name == 'edge-width':  # one data unit at zoom 8: about 8 red canvas pixels across A's left edge
                row_, left = screen_of((6, 2), **CAMERA)
                line = pixels[row_, left - 12:left + 12, :3].astype(int)
                red = int((np.abs(line - [255, 0, 0]).max(axis=1) <= 40).sum())
                assert observed['edgeWidth'] == [1.0] * 6 and abs(red - 8) <= 2, (name, red)
            if name == 'face-direct':
                assert np.abs(pixels[screen_of((4, 4), **CAMERA)][:3].astype(int) - [0, 255, 0]).max() <= 1
            if name == 'face-cycle':
                assert observed['face']['colors'] == first_appearance(SHAPE_KINDS, CYCLE)
            if name == 'edge-cycle':
                assert observed['edge']['colors'] == first_appearance(SHAPE_KINDS, CYCLE[::-1])
            if name == 'face-colormap':
                wanted = gray(SHAPE_VALUES, 0.0, 6.0)
                assert np.allclose(observed['face']['colors'], wanted, atol=1e-6)
                assert np.abs(pixels[screen_of((4, 4), **CAMERA)][:3].astype(int) - 255 * np.array(wanted[0][:3])).max() <= 1
                assert np.abs(pixels[overlap][:3].astype(int) - 255 * np.array(wanted[1][:3])).max() <= 1  # B drawn over A
            if name == 'edge-colormap':
                assert np.allclose(observed['edge']['colors'], gray(SHAPE_VALUES, 0.0, 6.0), atol=1e-6) and observed['edge']['limits'] == [0.0, 6.0]
                # napari refreshes both sides here: the face colormap now spans the data range of v.
                assert np.allclose(observed['face']['colors'], gray(SHAPE_VALUES, 1.0, 6.0), atol=1e-6)
                assert hooke.layer('Outlines')['shapeStyle']['face']['contrastLimits'] == [1.0, 6.0]  # and the frame says so
            if name == 'front':  # A, selected, is now drawn over B where they overlap
                assert observed['selected'] == [0] and observed['z'][0] > max(observed['z'][1:])
                assert hooke.inspect(outlines, canvas((4, 4)))['hit']['style']['z'] == observed['z'][0]
                # Faces as napari now holds them (setting the edge color by feature also refreshes the face colormap
                # over the data range, in napari as in Hooke): the overlap shows A's.
                assert np.abs(pixels[overlap][:3].astype(int) - 255 * np.array(observed['face']['colors'][0][:3])).max() <= 1
            if name == 'back':
                assert observed['z'][0] < min(observed['z'][1:])
                assert np.abs(pixels[overlap][:3].astype(int) - 255 * np.array(observed['face']['colors'][1][:3])).max() <= 1
                assert observed['face']['colors'][0] != observed['face']['colors'][1]  # so the two cases differ
            if name == 'text-format':
                assert observed['text']['values'] == [f'{k}={v:.0f}' for k, v in zip(SHAPE_KINDS, SHAPE_VALUES)]
        with pytest.raises(parent.ChangeRefused, match='Select one or more shapes'):  # nothing selected: refused first
            hooke.change('shapes-selection', {'layer': outlines, 'action': 'clear', 'index': None})
            hooke.change('shapes-order', {'layer': outlines, 'action': 'front'})
        assert hooke.observe(OBSERVE_SHAPES)['z'] == observed['z']
    assert worker.process.poll() is not None


def test_actual_shapes_style_and_z_order_match_pinned_napari_and_independent_values(actual_host, tmp_path, record_property):
    record_run(record_property, ['src/napari/layers/shapes/shapes.py', 'src/napari/layers/shapes/_shapes_constants.py',
                                 'src/napari/layers/utils/text_manager.py', 'src/napari/layers/utils/string_encoding.py'],
               {'render': 0, 'values': 'exact', 'color': 1, 'edge pixels': 2})
    run_shapes(actual_host, tmp_path)


def test_a_refused_style_leaves_the_actual_layer_unchanged(actual_host):
    """A colormap over categories, an unknown feature and a format naming no feature are refused before any change."""
    from orchestrator import scientific_host as parent
    values = np.zeros(SHAPE, dtype=np.uint16)
    with parent.review(actual_host['key'], Path(actual_host['folder']).name, {'volume': intensity(values)}) as worker:
        hooke = HookeSide(worker, WIDTH, HEIGHT)
        hooke.render()
        hooke.python(POINTS_CODE)
        hooke.render()
        spots = hooke.layer('Spots')
        before = hooke.observe(OBSERVE_POINTS)
        for operation, settings, words in [
                ('points-style', {**spots['pointStyle'], 'face': {'mode': 'colormap', 'feature': 'kind', 'colormap': 'gray', 'contrastLimits': None}}, 'numeric'),
                ('points-style', {**spots['pointStyle'], 'face': {'mode': 'cycle', 'feature': 'nope', 'cycle': CYCLE}}, 'no feature'),
                ('points-style', {**spots['pointStyle'], 'face': {'mode': 'colormap', 'feature': 'area', 'colormap': 'no-such-map', 'contrastLimits': None}}, 'no colormap'),
                ('points-style', {**spots['pointStyle'], 'borderWidth': 2.0, 'borderWidthIsRelative': True}, 'fraction'),
                ('text-content', {'string': {'kind': 'format', 'value': '{missing}'}, 'color': None}, 'does not fit')]:
            with pytest.raises(parent.ChangeRefused, match=words):
                hooke.change(operation, {'layer': spots['id'], 'settings': settings})
            assert hooke.observe(OBSERVE_POINTS) == before and worker.process.poll() is None
    assert worker.process.poll() is not None


BREAK = ("import napari.layers as _l\n"
         "_symbol = _l.Points.symbol\n"
         "_l.Points.symbol = property(_symbol.fget, lambda self, value: _symbol.fset(self, 'disc'))\n"
         "_l.Shapes.move_to_front = lambda self: None\n")


def test_the_comparison_catches_a_worker_whose_napari_behaviour_was_broken(actual_host, tmp_path, monkeypatch):
    """The harness itself: with napari's Points.symbol setter always drawing discs and Shapes.move_to_front a no-op
    inside the actual worker, the same comparisons must fail, each at its own step."""
    original = HookeSide.python

    def broken(self, source):
        if not getattr(self, '_broken', False):
            self._broken = True
            original(self, BREAK)
        return original(self, source)
    monkeypatch.setattr(HookeSide, 'python', broken)
    with pytest.raises(AssertionError, match='symbol-arrow'):
        run_points(actual_host, tmp_path / 'points', check_independent=True)
    with pytest.raises(AssertionError, match='front'):
        run_shapes(actual_host, tmp_path / 'shapes', check_independent=True)
