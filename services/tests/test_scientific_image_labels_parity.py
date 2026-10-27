"""Image and Labels controls in Hooke's actual review worker against plain pinned napari and independent values.

Opt-in: NAPARI_HOST_PREPARATION (Hooke's prepared review bundle) and NAPARI_REFERENCE_PYTHON (the same pinned
napari, run plain). Without them every case skips and the acceptance runner reports its children missing.
Hooke is driven only through its ordinary worker requests (the frame-bound changes the Studio sends, its
inspection and its Python console); the reference runs napari's public API. Renders use one viewport and
an explicit camera, so each data pixel lands on a known canvas pixel. This is actual-worker evidence; it is
not browser, clean-machine or researcher qualification.
"""
import json
import os
from pathlib import Path

import numpy as np
import pytest

from tests.scientific_parity import (HookeSide, pinned_source_check, pixel_at, reference_run, runtime_identity,
                                     same_render, screen_of)
from tests.test_native_regions import source
from tests.test_scientific_host_worker import actual_host  # noqa: F401

WIDTH = HEIGHT = 256
ZOOM = 8
CENTER = (11.5, 13.5)  # the middle of a 24 x 28 plane
CAMERA = {'center_yx': CENTER, 'zoom': ZOOM, 'width': WIDTH, 'height': HEIGHT}
STEPS = [1, 2, 3, 0, 0]  # t, c and z shown; y and x displayed
SHAPE = (2, 3, 4, 24, 28)
BIG = 2**60 + 3
TOP = 2**64 - 2

IMAGE_CODE = f"""
import numpy as np
values = ((np.arange({int(np.prod(SHAPE))}, dtype=np.uint64) * 7919) % 60000).astype(np.uint16).reshape({SHAPE})
"""
LABELS_CODE = f"""
import numpy as np
import pandas as pd
labels = np.zeros({SHAPE}, dtype=np.uint64)
labels[..., 2:8, 2:10] = 1
labels[..., 16:22, 3:11] = 7
labels[..., 9:15, 11:19] = {BIG}
labels[..., 16:22, 18:26] = {TOP}
features = pd.DataFrame({{'index': np.array([1, 7, {BIG}, {TOP}], dtype=np.uint64), 'area': [48.0, 48.5, 48.25, np.nan],
                         'kind': pd.Categorical(['a', 'b', 'a', 'c'])}})
viewer.add_labels(labels, name='Cells', features=features)
"""
VIEW_CODE = f"""
viewer.dims.order = (0, 1, 2, 3, 4)
viewer.dims.ndisplay = 2
viewer.dims.point = {tuple(float(n) for n in STEPS)}
viewer.camera.center = (viewer.camera.center[0], {CENTER[0]}, {CENTER[1]})
viewer.camera.angles = (0, 0, 0)
viewer.camera.zoom = {ZOOM}
"""


def fixture_values():
    namespace = {}
    exec(IMAGE_CODE, namespace)
    return namespace['values']


def intensity(values):
    """The 5D fixture as a native input the integration may bind, declared as intensity on t, c, z, y, x."""
    native = source(values, [], axes=('t', 'c', 'z', 'y', 'x'))
    native.descriptor['semantic'] = 'intensity'
    return native


def fixture_labels():
    labels = np.zeros(SHAPE, dtype=np.uint64)
    labels[..., 2:8, 2:10] = 1
    labels[..., 16:22, 3:11] = 7
    labels[..., 9:15, 11:19] = BIG
    labels[..., 16:22, 18:26] = TOP
    return labels


def interior(plane, thickness):
    """Independent of napari: a pixel is interior when its 4-neighbourhood maximum equals the minimum over the
    diamond of radius `thickness` (edges mirrored), so no other label lies within that distance."""
    r = thickness + 1
    padded = np.pad(plane, r, mode='symmetric')
    def shifted(dy, dx):
        return padded[r + dy:r + dy + plane.shape[0], r + dx:r + dx + plane.shape[1]]
    near = [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)]
    diamond = [(dy, dx) for dy in range(-thickness, thickness + 1) for dx in range(-thickness, thickness + 1)
               if abs(dy) + abs(dx) <= thickness]
    return np.maximum.reduce([shifted(*o) for o in near]) == np.minimum.reduce([shifted(*o) for o in diamond])


def canvas(data_yx):
    row, column = screen_of(data_yx, **CAMERA)
    return (row + .5, column + .5)


def hooke_view(hooke, *, image_visible):
    hooke.render()
    hooke.change('dimensions', {'order': [0, 1, 2, 3, 4], 'ndisplay': 2, 'steps': STEPS})
    image = hooke.layer('Native input')
    hooke.change('layer-presentation', {'layer': image['id'], 'visible': image_visible, 'opacity': 1})
    hooke.change('camera', {'center': [hooke.frame['camera']['center'][0], *CENTER], 'zoom': ZOOM, 'angles': [0, 0, 0]})


def record_run(record_property, paths, tolerance):
    """The frozen conditions this run compared under, kept with its JUnit evidence."""
    executable = Path(os.environ['NAPARI_REFERENCE_PYTHON']).absolute()
    record_property('comparison', json.dumps({
        'runtime': runtime_identity(executable), 'pinnedSources': pinned_source_check(executable, paths),
        'viewport': [WIDTH, HEIGHT], 'camera': {'center': CENTER, 'zoom': ZOOM}, 'tolerance': tolerance,
        'cache': 'a fresh Hooke worker and a fresh reference process per run'}, sort_keys=True))


OBSERVE_LABELS = """
import json, hashlib, numpy as np
cells = viewer.layers['Cells']
cmap = cells.colormap
colors = {str(v): None if cells.get_color(v) is None else [round(float(n), 6) for n in cells.get_color(v)]
          for v in (0, 1, 7, %d, %d)}
print(json.dumps({'contour': int(cells.contour), 'selected': str(int(cells.selected_label)),
    'showSelected': bool(cells.show_selected_label), 'useSelection': bool(cmap.use_selection), 'selection': str(int(cmap.selection)),
    'kind': type(cmap).__name__, 'background': str(int(cmap.background_value)),
    'colors': hashlib.sha256(np.ascontiguousarray(cmap.colors).tobytes()).hexdigest(), 'count': len(cmap.colors),
    'mapped': colors, 'rendering': str(cells.rendering), 'gradient': str(cells.iso_gradient_mode),
    'ndisplay': int(viewer.dims.ndisplay),
    'data': hashlib.sha256(np.ascontiguousarray(cells.data).tobytes()).hexdigest(), 'dtype': cells.data.dtype.str}))
""" % (BIG, TOP)

DIRECT = {None: [0, 0, 1, 1], 7: [1, 0, 0, 1], 1: [0, 1, 0, 1]}  # pinned napari without numba colors direct labels up to 2**16
LABEL_STEPS = [
    # name, the settings Hooke's control changes, and the same change through napari's public API
    ('contour', {'contour': 1}, "cells.contour = 1"),
    ('selected', {'contour': 0, 'selectedLabel': str(BIG), 'showSelectedLabel': True},
     f"cells.contour = 0\ncells.selected_label = {BIG}\ncells.show_selected_label = True"),
    ('seeded', {'showSelectedLabel': False, 'autoSource': 'seeded', 'autoSeed': 12345},
     "cells.show_selected_label = False\ncells.new_colormap(12345)"),
    ('background', {'backgroundValue': '7'},
     "from napari.utils.colormaps import CyclicLabelColormap\n"
     "cells.colormap = CyclicLabelColormap(**{**cells.colormap.model_dump(), 'background_value': 7})"),
    ('direct', {'colorMode': 'direct', 'autoSource': 'original', 'autoSeed': None, 'backgroundValue': '0',
                'directColors': [{'label': None if key is None else str(key), 'color': color} for key, color in DIRECT.items()]},
     f"from napari.utils.colormaps import DirectLabelColormap\ncells.colormap = DirectLabelColormap(color_dict={DIRECT!r}, background_value=0)\n"
     "cells.show_selected_label = False  # the control applies the whole display; this setter syncs the selection"),
    ('direct-selected', {'selectedLabel': '7', 'showSelectedLabel': True},
     "cells.selected_label = 7\ncells.show_selected_label = True"),
    ('rendering', {'rendering': 'translucent', 'isoGradientMode': 'smooth'},
     "cells.rendering = 'translucent'\ncells.iso_gradient_mode = 'smooth'\nviewer.dims.ndisplay = 3"),
]


def test_actual_labels_display_matches_pinned_napari_and_independent_values(actual_host, tmp_path, record_property):
    from orchestrator import scientific_host as parent
    prepared, values, labels = actual_host, fixture_values(), fixture_labels()
    plane = labels[1, 2, 3]
    record_run(record_property, ['src/napari/layers/labels/labels.py', 'src/napari/utils/colormaps/colormap.py',
                                 'src/napari/utils/colormaps/colormap_utils.py', 'src/napari/layers/labels/_labels_utils.py'],
               {'render': 0, 'values': 'exact', 'color': 1})
    scene = (IMAGE_CODE + "viewer.add_image(values, name='Native input')\n" + LABELS_CODE + VIEW_CODE
             + "viewer.layers['Native input'].visible = False\ncells = viewer.layers['Cells']\ncells.opacity = 1\n")
    reference_report = reference_run(scene, [{'apply': '', 'observe': OBSERVE_LABELS, 'render': True}]
                              + [{'apply': code, 'observe': OBSERVE_LABELS, 'render': True} for *_, code in LABEL_STEPS],
                              width=WIDTH, height=HEIGHT, work=tmp_path / 'reference')
    reference = reference_report['results']
    with parent.review(prepared['key'], Path(prepared['folder']).name,
                       {'volume': intensity(values)}) as worker:
        hooke = HookeSide(worker, WIDTH, HEIGHT)
        hooke.admit(reference_report)
        hooke.render()
        hooke.python(LABELS_CODE)
        hooke_view(hooke, image_visible=False)
        cells = hooke.layer('Cells')['id']
        _, pixels = hooke.change('layer-presentation', {'layer': cells, 'visible': True, 'opacity': 1})
        background = pixels[0, 0, :3].astype(int)
        first = hooke.observe(OBSERVE_LABELS)
        assert first == reference[0]['observed']
        assert first['dtype'] == '<u8'
        same_render(pixels, reference[0]['pixels'])
        for index, (name, patch, _) in enumerate(LABEL_STEPS, start=1):
            settings = {**hooke.layer('Cells')['labelsAppearance'], **patch}
            frame, pixels = hooke.change('labels-presentation', {'layer': cells, 'settings': settings})
            shown = hooke.layer('Cells')['labelsAppearance']
            assert {key: shown[key] for key in patch if key != 'directColors'} == {key: value for key, value in patch.items() if key != 'directColors'}
            if name == 'rendering':
                frame, pixels = hooke.change('dimensions', {'order': [0, 1, 2, 3, 4], 'ndisplay': 3, 'steps': frame['dimensions']['steps']})
            observed = hooke.observe(OBSERVE_LABELS)
            assert observed == reference[index]['observed'], name
            assert observed['data'] == first['data']  # identifiers and values never change
            swatch = hooke.layer('Cells')['labelsSelectedColor']  # napari's get_color for the selected label
            assert (None if swatch is None else [round(n, 6) for n in swatch]) == observed['mapped'][observed['selected']], name
            same_render(pixels, reference[index]['pixels'])
            if name == 'direct':  # pinned napari without numba cannot draw a direct table naming 2**60+3: refused first
                large = {**settings, 'directColors': [*settings['directColors'], {'label': str(BIG), 'color': [1, 1, 0, 1]}]}
                with pytest.raises(parent.ChangeRefused, match='numba'):
                    hooke.change('labels-presentation', {'layer': cells, 'settings': large})
                assert hooke.observe(OBSERVE_LABELS) == observed and worker.process.poll() is None
            if name == 'rendering':
                continue
            colors = {(y, x): pixel_at(pixels, (y, x), **CAMERA) for y in range(24) for x in range(28)}
            for (y, x), color in colors.items():
                label = int(plane[y, x])
                hidden = (color == background).all()
                if name == 'contour':
                    assert hidden == (label == 0 or bool(interior(plane, 1)[y, x])), (name, y, x)
                elif name == 'selected':
                    assert hidden == (label != BIG), (name, y, x)
                elif name in ('seeded', 'background'):
                    assert hidden == (label == (7 if name == 'background' else 0)), (name, y, x)
                elif name == 'direct':
                    wanted = background if label == 0 else 255 * np.array(DIRECT.get(label, DIRECT[None])[:3])
                    assert np.abs(color - wanted).max() <= 1, (name, y, x, label, color)
                elif name == 'direct-selected':
                    wanted = 255 * np.array(DIRECT[7][:3]) if label == 7 else background
                    assert np.abs(color - wanted).max() <= 1, (name, y, x, label, color)
        # Inspection reads the exact uint64 identifier and that label's own native feature row.
        frame, _ = hooke.change('dimensions', {'order': [0, 1, 2, 3, 4], 'ndisplay': 2, 'steps': STEPS})
        sample = hooke.inspect(cells, canvas((12, 14)))
        assert sample['values'] == [str(BIG)]
        assert sample['labelFeatures']['row'] == 2
        assert {f['name']: (f['kind'], f['value']) for f in sample['labelFeatures']['features']} == {
            'index': ('integer', str(BIG)), 'area': ('float', '48.25'), 'kind': ('string', 'a')}
        hooke.python("viewer.layers['Cells'].features = viewer.layers['Cells'].features.iloc[:2]")
        hooke.render()
        missing = hooke.inspect(cells, canvas((12, 14)))
        assert missing['values'] == [str(BIG)] and missing['labelFeatures'] == {'row': None, 'features': []}
    assert worker.process.poll() is not None


OBSERVE_IMAGE = """
import json, hashlib, numpy as np
image = viewer.layers['Native input']
model = image.histogram
cmap = image.colormap
color = lambda value: None if value is None else [float(n) for n in value]
print(json.dumps({'limits': [float(n) for n in image.contrast_limits], 'range': [float(n) for n in image.contrast_limits_range],
    'gamma': float(image.gamma), 'auto': bool(image.auto_contrast), 'colormap': cmap.name, 'choices': list(image.colormaps),
    'colors': [color(row) for row in cmap.colors] if len(cmap.colors) <= 8 else hashlib.sha256(np.ascontiguousarray(cmap.colors).tobytes()).hexdigest(),
    'controls': [float(n) for n in cmap.controls], 'cmapInterpolation': str(cmap.interpolation),
    'nan': color(cmap.nan_color), 'high': color(cmap.high_color), 'low': color(cmap.low_color),
    'interpolation': str(image.interpolation2d), 'kernel': image.custom_interpolation_kernel_2d.tolist(),
    'enabled': bool(model.enabled), 'bins': int(model.bins), 'mode': str(model.mode), 'log': bool(model.log_scale),
    'samples': int(model.max_samples),
    'counts': [float(n) for n in model.counts] if model.enabled else None,
    'edges': [float(n) for n in model._bin_edges] if model.enabled else None,
    'step': [int(n) for n in viewer.dims.current_step], 'dtype': image.dtype.str if hasattr(image.dtype, 'str') else str(image.dtype),
    'data': hashlib.sha256(np.ascontiguousarray(image.data).tobytes()).hexdigest()}))
"""
HISTOGRAM = {'enabled': True, 'bins': 64, 'mode': 'canvas', 'logScale': False, 'maxSamples': 1000000}
RAMP = {'name': 'h06a-ramp', 'colors': [[0, 0, 0, 1], [1, .5, 0, 1]], 'controls': [0, 1], 'interpolation': 'linear',
        'nanColor': [1, 0, 1, 1], 'highColor': [0, 1, 0, 1], 'lowColor': [0, 0, 1, 1]}
STEP = {**RAMP, 'name': 'h06a-step', 'colors': [[1, 0, 0, 1], [0, 0, 1, 1]], 'controls': [0, .5, 1], 'interpolation': 'zero'}
INTERPOLATION = ('bessel', 'cubic', 'linear', 'blackman', 'catrom', 'gaussian', 'hamming', 'hanning', 'hermite', 'kaiser',
                 'lanczos', 'mitchell', 'spline16', 'spline36', 'nearest')
KERNEL = [[0.0, 1.0, 0.0], [1.0, 4.0, 1.0], [0.0, 1.0, 0.0]]


def colormap_code(value):
    return ("from napari.utils.colormaps import Colormap\n"
            f"image.colormap = Colormap({value['colors']!r}, name={value['name']!r}, controls={value['controls']!r}, "
            f"interpolation={value['interpolation']!r}, nan_color={value['nanColor']!r}, high_color={value['highColor']!r}, "
            f"low_color={value['lowColor']!r})")


IMAGE_STEPS = [
    # name, Hooke's operation, the settings its control changes, and napari's public API doing the same
    ('appearance', 'image-presentation', {'contrastLimits': [1000.0, 50000.0], 'gamma': 0.5},
     "image.contrast_limits = (1000, 50000)\nimage.gamma = 0.5"),
    ('colormap', 'image-colormap', {'name': 'viridis'}, "image.colormap = 'viridis'"),
    ('custom-linear', 'image-colormap', RAMP, colormap_code(RAMP)),
    ('custom-zero', 'image-colormap', STEP, colormap_code(STEP)),
    *[(f'interpolation-{mode}', 'image-sampling', {'interpolation2d': mode}, f"image.interpolation2d = {mode!r}") for mode in INTERPOLATION],
    ('interpolation-custom', 'image-sampling', {'interpolation2d': 'custom', 'kernel2d': KERNEL},
     f"import numpy as np\nimage.custom_interpolation_kernel_2d = np.asarray({KERNEL!r}, dtype=np.float32)\nimage.interpolation2d = 'custom'"),
    ('histogram', 'image-histogram', HISTOGRAM, "image.histogram.bins = 64\nimage.histogram.enabled = True"),
    ('full', 'image-histogram', {**HISTOGRAM, 'mode': 'full'}, "image.histogram.mode = 'full'"),
    ('samples', 'image-histogram', {**HISTOGRAM, 'mode': 'full', 'maxSamples': 5000}, "image.histogram.max_samples = 5000"),
    ('log', 'image-histogram', {**HISTOGRAM, 'mode': 'full', 'maxSamples': 5000, 'logScale': True}, "image.histogram.log_scale = True"),
    ('reset-histogram', 'image-contrast', {'action': 'reset-histogram'}, "image.histogram.reset()"),
    ('auto-once', 'image-contrast', {'action': 'auto-once'}, "image.reset_contrast_limits()"),
    ('set-range', 'image-contrast', {'action': 'set-range', 'range': [0.0, 40000.0]}, "image.contrast_limits_range = (0, 40000)"),
    # napari's contrast popup reset, as its source does it (napari/_qt/layer_controls/widgets/qt_contrast_limits.py, reset()).
    ('autoscale-range', 'image-contrast', {'action': 'autoscale-range'},
     "image.reset_contrast_limits()\nimage.contrast_limits_range = image.contrast_limits"),
    ('reset-range', 'image-contrast', {'action': 'reset-range'}, "image.reset_contrast_limits_range()"),
    ('continuous', 'image-presentation', {'autoContrast': True}, "image.auto_contrast = True"),
]


def test_actual_image_controls_and_histogram_match_pinned_napari_and_independent_values(actual_host, tmp_path, record_property):
    from orchestrator import scientific_host as parent
    prepared, values = actual_host, fixture_values()
    plane = values[1, 2, 3].astype(float)
    record_run(record_property, ['src/napari/layers/image/image.py', 'src/napari/layers/intensity_mixin.py',
                                 'src/napari/components/histogram.py', 'src/napari/utils/colormaps/colormap.py'],
               {'render': 0, 'values': 'exact', 'gray level': 1, 'log counts': 1e-6})
    scene = IMAGE_CODE + "viewer.add_image(values, name='Native input')\n" + VIEW_CODE + "image = viewer.layers['Native input']\n"
    reference_report = reference_run(scene, [{'apply': '', 'observe': OBSERVE_IMAGE, 'render': True}]
                              + [{'apply': code, 'observe': OBSERVE_IMAGE, 'render': True} for *_, code in IMAGE_STEPS],
                              width=WIDTH, height=HEIGHT, work=tmp_path / 'reference')
    reference = reference_report['results']
    with parent.review(prepared['key'], Path(prepared['folder']).name,
                       {'volume': intensity(values)}) as worker:
        hooke = HookeSide(worker, WIDTH, HEIGHT)
        hooke.admit(reference_report)
        hooke_view(hooke, image_visible=True)
        image = hooke.layer('Native input')['id']
        first = hooke.observe(OBSERVE_IMAGE)
        assert first == reference[0]['observed']
        for index, (name, operation, patch, _) in enumerate(IMAGE_STEPS, start=1):
            current = hooke.layer('Native input')
            settings = ({**current['imageAppearance'], **patch} if operation == 'image-presentation'
                        else {**current['imageSampling'], **patch} if operation == 'image-sampling'
                        else {**current['imageHistogram'], **patch} if operation == 'image-histogram' else patch)
            if operation == 'image-histogram':
                settings = {key: settings[key] for key in HISTOGRAM}
            frame, pixels = hooke.change(operation, {'layer': image, 'settings': settings})
            observed = hooke.observe(OBSERVE_IMAGE)
            assert observed == reference[index]['observed'], name
            assert observed['data'] == first['data']  # native values never change
            same_render(pixels, reference[index]['pixels'])
            row = hooke.layer('Native input')['imageHistogram']
            levels = np.clip((plane - 1000) / 49000, 0, 1) ** 0.5  # the shader's normalised value for these limits
            for y in range(24 if name in ('appearance', 'custom-linear', 'custom-zero') else 0):
                for x in range(28):
                    shown, v, g = pixel_at(pixels, (y, x), **CAMERA), plane[y, x], levels[y, x]
                    if name == 'appearance':
                        assert np.abs(shown - 255 * g).max() <= 1, (name, y, x)
                    elif v < 1000 or v > 50000:  # beyond the limits: the colormap's low and high colors
                        assert np.abs(shown - 255 * np.array((RAMP['lowColor'] if v < 1000 else RAMP['highColor'])[:3])).max() <= 1, (name, y, x)
                    elif name == 'custom-linear':
                        assert np.abs(shown - 255 * g * np.array([1, .5, 0])).max() <= 2, (name, y, x)
                    elif abs(g - .5) > .02:  # a zero (step) colormap: the first color below the middle control
                        assert np.abs(shown - 255 * np.array(STEP['colors'][0 if g < .5 else 1][:3])).max() <= 1, (name, y, x)
            if name.startswith('interpolation-'):
                assert observed['interpolation'] == name.removeprefix('interpolation-')
            if name == 'interpolation-custom':
                assert observed['kernel'] == KERNEL
            if name in ('custom-linear', 'custom-zero'):
                wanted = RAMP if name == 'custom-linear' else STEP
                assert (observed['colors'], observed['controls'], observed['cmapInterpolation'], observed['nan'], observed['high'],
                        observed['low']) == (wanted['colors'], wanted['controls'], wanted['interpolation'], wanted['nanColor'],
                                             wanted['highColor'], wanted['lowColor'])
            if name in ('histogram', 'full', 'samples', 'log'):
                data = plane if name == 'histogram' else values.astype(float)
                counts, edges = np.histogram(data, bins=64, range=tuple(row['range']))
                if name == 'samples':
                    assert sum(row['counts']) == 5000 and all(n <= c for n, c in zip(row['counts'], counts))
                elif name == 'log':
                    assert abs(sum(10 ** np.array(row['counts']) - 1) - 5000) < 1
                else:
                    assert np.allclose(row['counts'], counts, rtol=0, atol=0) and sum(counts) == data.size
                assert np.array_equal(np.array(row['binEdges'], dtype=np.float32), edges.astype(np.float32))
                assert row['counts'] == observed['counts'] and row['binEdges'] == observed['edges']
            if name == 'reset-histogram':
                assert (observed['enabled'], observed['bins'], observed['mode'], observed['log'], observed['samples']) == (False, 256, 'canvas', False, 1000000)
                assert row['enabled'] is False and row['counts'] is None
            if name == 'continuous':
                assert observed['auto'] and observed['limits'] == [float(plane.min()), float(plane.max())]
            if name == 'auto-once':
                assert observed['limits'] == [float(plane.min()), float(plane.max())]
            if name == 'set-range':
                assert observed['range'] == row['range'] == [0.0, 40000.0]
            if name == 'autoscale-range':  # the shown slice's own range, then the slider narrowed to it
                assert observed['limits'] == [float(plane.min()), float(plane.max())] == observed['range'] == row['range']
            if name == 'reset-range':
                assert observed['range'] == row['range'] == [0.0, 65535.0]
        # Value inspection reads the exact native value under a canvas position.
        sample = hooke.inspect(image, canvas((12, 14)))
        assert sample['values'] == [str(values[1, 2, 3, 12, 14])] and sample['dtype'] == '<u2'
        assert 'labelFeatures' not in sample
    assert worker.process.poll() is not None


def test_erase_writes_the_changed_background_label_as_napari(tmp_path):
    """The background label is scientific: after the label display changes it, Hooke's eraser writes it, as napari's does."""
    import shutil
    import subprocess
    from orchestrator import owned_process, sandbox, worker_resources
    from tests.scientific_parity import reference_python
    executable = reference_python()
    assert sandbox.available()
    probe = Path(__file__).with_name('scientific_labels_background_probe.py')
    bootstrap, work = tmp_path / 'bootstrap', tmp_path / 'work'
    (bootstrap / 'env_worker').mkdir(parents=True); work.mkdir()
    (bootstrap / 'env_worker/__init__.py').touch()
    for name in ('scientific_labels.py', 'scientific_label_history.py', 'scientific_layer_exports.py', 'scientific_labels_appearance.py'):
        shutil.copy2(probe.parents[1] / 'env_worker' / name, bootstrap / 'env_worker' / name)
    command = sandbox.wrap([str(executable), '-I', '-u', str(probe), str(bootstrap)],
                           [executable.parent.parent, sandbox.python_home(executable), probe, bootstrap], [work], children=False)
    environment = sandbox.confine({'PATH': '/usr/bin:/bin', 'QT_QPA_PLATFORM': 'offscreen', 'QT_API': 'pyside6', 'NAPARI_ASYNC': '0',
                                   'NAPARI_CONFIG': str(work / 'napari.yaml'), 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
                                   'VECLIB_MAXIMUM_THREADS': '1', 'NUMBA_NUM_THREADS': '1'}, work)
    with (work / 'output.log').open('wb') as output:
        process = subprocess.Popen(command, cwd=work, env=environment, stdout=output, stderr=subprocess.STDOUT,
                                   start_new_session=True, close_fds=True)
        guard = worker_resources.Guard(process, work, worker_resources.Limits(memory_bytes=1 << 30, work_entries=512)).start()
        try:
            with owned_process.watching(process, 300, 'labels background erase parity'):
                process.wait(timeout=300)
            guard.check()
        finally:
            owned_process.terminate(process); guard.close()
    log = (work / 'output.log').read_text()
    assert process.returncode == 0 and 'erase writes the changed background label, as napari does' in log, log[-6000:]


MISSING_CODE = f"""
import numpy as np
values = (np.arange({int(np.prod(SHAPE))}, dtype=np.float32).reshape({SHAPE}) % 997) / 997
values[..., 4:9, 6:12] = np.nan
"""
MISSING = {**RAMP, 'name': 'h06a-missing', 'nanColor': [1, 0, 1, 1]}
TYPED_NAN = [round(255 * n) for n in MISSING['nanColor']]  # the typed nan_color as the thumbnail's uint8 RGBA
# Where nan_color changes napari's output: Colormap.map and the layer thumbnail (napari/layers/image/image.py,
# _update_thumbnail) both color a NaN with it. The thumbnail is hashed whole, and its pixels in the typed color listed.
OBSERVE_NAN = f"""
import json, hashlib, numpy as np
image = viewer.layers['Native input']
thumbnail = np.ascontiguousarray(image.thumbnail)
print(json.dumps({{'mapped': [[float(n) for n in row] for row in image.colormap.map(np.array([np.nan, 0.5], np.float32))],
    'thumbnail': [list(thumbnail.shape), str(thumbnail.dtype), hashlib.sha256(thumbnail.tobytes()).hexdigest()],
    'typed': np.argwhere(np.all(thumbnail == {TYPED_NAN!r}, axis=-1)).tolist()}}))
"""


def thumbnail_nan_pixels(plane, side=32):
    """Independent of napari: the thumbnail positions of a plane's NaN under nearest-neighbour scaling to fit a
    side x side thumbnail, centred (napari's thumbnail geometry, computed here with scipy alone)."""
    from scipy import ndimage
    factor = min(side / plane.shape[0], side / plane.shape[1])
    shape = np.clip(np.multiply(factor, plane.shape), 1, side)
    mask = ndimage.zoom(np.isnan(plane).astype(np.uint8), tuple(shape / plane.shape), order=0, prefilter=False).astype(bool)
    mask = np.pad(mask, [((side - n) // 2, (side - n + 1) // 2) for n in mask.shape])
    return np.argwhere(mask).tolist()


def test_actual_missing_values_keep_nan_color_and_render_as_napari(actual_host, tmp_path, record_property):
    """A floating image with NaN: nan_color is read and changed as napari does, and Full range is not offered.
    In this runtime's offscreen GL, napari's image shader draws a NaN as a value below the limits (the low color
    when the colormap has one, else its first color), and Hooke's render matches it pixel for pixel. nan_color is
    kept and used where napari uses it: Colormap.map and the layer thumbnail color every NaN with the typed
    nan_color, on both sides, at the positions computed independently."""
    from orchestrator import scientific_host as parent
    namespace = {}
    exec(MISSING_CODE, namespace)
    values = namespace['values']
    plane = values[1, 2, 3]
    record_run(record_property, ['src/napari/layers/image/image.py', 'src/napari/utils/colormaps/colormap.py'],
               {'render': 0, 'nan color': 1, 'ramp': 2, 'thumbnail': 'exact'})
    scene = MISSING_CODE + "viewer.add_image(values, name='Native input')\n" + VIEW_CODE + "image = viewer.layers['Native input']\n"
    reference_report = reference_run(scene, [{'apply': "image.contrast_limits = (0.0, 1.0)\n" + colormap_code(MISSING),
                                       'observe': OBSERVE_IMAGE, 'render': True},
                                      {'apply': '', 'observe': OBSERVE_NAN, 'render': False}],
                              width=WIDTH, height=HEIGHT, work=tmp_path / 'reference')
    reference = reference_report['results']
    with parent.review(actual_host['key'], Path(actual_host['folder']).name, {'volume': intensity(values)}) as worker:
        hooke = HookeSide(worker, WIDTH, HEIGHT)
        hooke.admit(reference_report)
        hooke_view(hooke, image_visible=True)
        image = hooke.layer('Native input')
        assert image['imageHistogram']['integer'] is False
        with pytest.raises(parent.ChangeRefused, match='integer images'):
            hooke.change('image-contrast', {'layer': image['id'], 'settings': {'action': 'reset-range'}})
        hooke.change('image-presentation', {'layer': image['id'], 'settings': {**image['imageAppearance'], 'contrastLimits': [0.0, 1.0]}})
        _, pixels = hooke.change('image-colormap', {'layer': image['id'], 'settings': MISSING})
        observed = hooke.observe(OBSERVE_IMAGE)
        assert observed == reference[0]['observed'] and observed['nan'] == [1.0, 0.0, 1.0, 1.0]
        same_render(pixels, reference[0]['pixels'])
        assert np.isnan(plane).sum() == 30
        for y in range(24):
            for x in range(28):
                shown = pixel_at(pixels, (y, x), **CAMERA)
                if np.isnan(plane[y, x]) or plane[y, x] <= 0:  # this runtime's offscreen GL: a NaN draws the low color
                    assert np.abs(shown - [0, 0, 255]).max() <= 1, (y, x, shown)
                else:
                    assert np.abs(shown - 255 * plane[y, x] * np.array([1, .5, 0])).max() <= 2, (y, x, shown)
        # Where nan_color changes the output, both sides use the typed one.
        kept = hooke.observe(OBSERVE_NAN)
        assert kept == reference[1]['observed']
        assert kept['mapped'] == [MISSING['nanColor'], [.5, .25, 0.0, 1.0]]
        assert kept['thumbnail'][:2] == [[32, 32, 4], 'uint8']
        assert kept['typed'] == thumbnail_nan_pixels(plane) and len(kept['typed']) == 42
    assert worker.process.poll() is not None


def test_h06_worker_and_reference_use_approved_gui_runtimes(actual_host, tmp_path, record_property):
    """Mandatory H06 admission: observe the existing worker console and an independent Cocoa Viewer."""
    from orchestrator import scientific_host as parent
    reference = reference_run(IMAGE_CODE + "viewer.add_image(values, name='Native input')\n", [],
                              width=64, height=64, work=tmp_path / 'reference')
    with parent.review(actual_host['key'], Path(actual_host['folder']).name,
                       {'volume': intensity(fixture_values())}) as worker:
        HookeSide(worker, 64, 64).admit(reference)
    record_property('approved_reference_runtime', json.dumps(
        {key: reference[key] for key in ('runtime', 'profile', 'pixel_profile', 'pinned')}, sort_keys=True))
