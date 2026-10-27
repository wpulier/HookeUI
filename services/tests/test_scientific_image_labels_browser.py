"""Image and Labels controls through the production Studio in a real browser on an isolated profile.

Opt-in (tests/scientific_browser.py): the prepared review bundle, a Chromium and Playwright, plus
NAPARI_REFERENCE_PYTHON for the plain pinned-napari comparison. A researcher's path only: DATA opens the
saved document, Open session starts its private view, the View section's controls change it, the Python
section reads native state (its API equivalent) and Inspect reads values. Every observation is compared
with plain pinned napari doing the same through its public API, and with independent values. Renders are
compared with napari in test_scientific_image_labels_parity.py, which drives the same worker requests.
"""
import json
import re

import numpy as np

from tests.scientific_browser import Studio
from tests.scientific_parity import admit, observation_source, reference_run
from tests.test_scientific_image_labels_parity import (BIG, DIRECT, IMAGE_CODE, INTERPOLATION, KERNEL, LABELS_CODE, OBSERVE_IMAGE,
                                                       OBSERVE_LABELS, RAMP, STEP, colormap_code, fixture_values)

TITLE = 'H06a fixture: 5D asymmetric index-encoded image'
AXES = ('t', 'c', 'z', 'y', 'x')
SCENE = IMAGE_CODE + "viewer.add_image(values, name='Native input')\n"


def studio(tmp_path):
    return Studio(tmp_path / 'studio', {TITLE: {'volume': (fixture_values(), AXES, 'intensity')}})


def open_session(page):
    page.act('waitfor', role='button', name='Data', timeout=120000)
    page.act('click', role='button', name='Data')
    page.act('click', role='button', name=TITLE, timeout=60000)
    page.act('click', role='button', name='Open session', timeout=60000)
    page.act('waittext', css='[role=status]', text='Private scientific view open', timeout=240000)


def section(page, name):
    page.act('click', role='tab', name=name)


def python(page, code):
    """Run code in the Studio's Python section and return the new cell's stdout."""
    section(page, 'Python')
    cells = '[aria-label="Python cell history"] section'
    before = page.act('eval', js="document.querySelectorAll('pre[aria-label=stdout]').length")
    count = page.act('eval', js=f"document.querySelectorAll('{cells}').length")
    page.act('fill', label='Python code', value=code)
    page.act('click', role='button', name='Run code')
    page.act('waitcount', css=cells, count=count, timeout=120000)  # this cell's own record, never an earlier status
    outputs = page.act('eval', js="[...document.querySelectorAll('pre[aria-label=stdout]')].map(e => e.textContent)")
    if len(outputs) > before:
        return outputs[-1]
    last = page.act('eval', js="[...document.querySelectorAll('[aria-label=\"Python cell history\"] section')].at(-1)?.textContent ?? ''")
    assert not any(word in last for word in ('Error', 'Traceback')), last[-3000:]
    return ''


def observe(page, code):
    return json.loads(python(page, code))


def apply_view(page):
    page.act('click', role='button', name='Apply view changes')
    page.act('waittext', css='[role=status]', text='View changes applied', timeout=120000)


def operate(page, controls):
    """Set each control as a researcher would, then apply once; a button applies by itself."""
    section(page, 'View')
    for op, label, value in controls:
        if op == 'click':
            page.act('click', label=label)
            page.act('waittext', css='[role=status]', text='View changes applied', timeout=120000)
        elif op == 'key':  # a key on the focused preview, routed by the browser as a researcher presses it
            page.act('press', css='.scientific-viewport canvas', key=label)
            page.act('waittext', css='[role=status]', text='View changes applied', timeout=120000)
        else:
            page.act(op, label=label, value=value)
    if controls[-1][0] not in ('click', 'key'):
        for op, label, value in controls:  # the unsent draft still holds every value typed
            if op == 'fill':
                assert page.act('value', label=label) == value, (label, page.act('eval', js=STATE))
        apply_view(page)


STATE = ("[...document.querySelectorAll('[role=status]')].map(e => e.textContent).filter(Boolean).join(' | ')")


LABEL_STEPS = [
    # name, the controls a researcher sets (operation, accessible name, value), and napari's public API doing the same
    ('contour', [('fill', 'Contour width Cells', '1')], "cells.contour = 1"),
    ('selected', [('fill', 'Contour width Cells', '0'), ('fill', 'Selected label Cells', str(BIG)),
                  ('check', 'Show only the selected label Cells', True)],
     f"cells.contour = 0\ncells.selected_label = {BIG}\ncells.show_selected_label = True"),
    ('seeded', [('check', 'Show only the selected label Cells', False), ('select', 'Label colors Cells', 'seeded'),
                ('fill', 'Color seed Cells', '12345')],
     "cells.show_selected_label = False\ncells.new_colormap(12345)"),
    ('background', [('fill', 'Background label Cells', '7')],
     "from napari.utils.colormaps import CyclicLabelColormap\n"
     "cells.colormap = CyclicLabelColormap(**{**cells.colormap.model_dump(), 'background_value': 7})"),
    ('direct', [('select', 'Label colors Cells', 'direct'), ('fill', 'Background label Cells', '0'),
                ('fill', 'Direct colors Cells', 'other: 0 0 1 1\n7: 1 0 0 1\n1: 0 1 0 1')],
     f"from napari.utils.colormaps import DirectLabelColormap\ncells.colormap = DirectLabelColormap(color_dict={DIRECT!r}, background_value=0)\n"
     "cells.show_selected_label = False  # the control applies the whole display; this setter syncs the selection"),
    ('direct-selected', [('fill', 'Selected label Cells', '7'), ('check', 'Show only the selected label Cells', True)],
     "cells.selected_label = 7\ncells.show_selected_label = True"),
    # Isosurface shading is offered only with isosurface rendering, as in napari's controls.
    ('gradient', [('select', 'Isosurface shading Cells', 'smooth')], "cells.iso_gradient_mode = 'smooth'"),
    ('rendering', [('select', '3D rendering Cells', 'translucent')], "cells.rendering = 'translucent'"),
    ('3d', [('select', 'Displayed dimensions', '3')], "viewer.dims.ndisplay = 3"),
    ('2d', [('select', 'Displayed dimensions', '2')], "viewer.dims.ndisplay = 2"),
]


def test_browser_labels_display_reaches_native_napari_state(tmp_path):
    reference_report = reference_run(SCENE + LABELS_CODE + "cells = viewer.layers['Cells']\n",
                              [{'apply': '', 'observe': OBSERVE_LABELS}]
                              + [{'apply': code, 'observe': OBSERVE_LABELS} for *_, code in LABEL_STEPS],
                              width=64, height=64, work=tmp_path / 'reference')
    reference = reference_report['results']
    with studio(tmp_path) as page:
        open_session(page)
        admit(reference_report, json.loads(python(page, observation_source())))
        python(page, LABELS_CODE)
        first = observe(page, OBSERVE_LABELS)
        assert first == reference[0]['observed']
        section(page, 'View')
        assert page.act('value', label='Contour width Cells') == '0'
        assert page.act('value', label='Selected label Cells') == first['selected']
        for index, (name, controls, _) in enumerate(LABEL_STEPS, start=1):
            operate(page, controls)
            for op, label, value in controls:  # each control now shows the accepted native value
                if op == 'fill' and label != 'Direct colors Cells':
                    assert page.act('value', label=label) == value, (name, label)
                if op == 'check':
                    assert page.act('checked', label=label) is value, (name, label)
                if op == 'select':
                    assert page.act('value', label=label) == value, (name, label)
            if name == 'direct':  # the table shows the default ("other") color and the background napari added
                table = page.act('value', label='Direct colors Cells')
                assert 'other: 0 0 1 1' in table and '0: 0 0 0 0' in table and '1: 0 1 0 1' in table
            observed = observe(page, OBSERVE_LABELS)
            assert observed == reference[index]['observed'], name
            assert observed['data'] == first['data'] and observed['dtype'] == '<u8'  # identifiers never change
            section(page, 'View')  # the label control's color box shows napari's get_color for the selected label
            shown = page.act('eval', js="document.querySelector('.scientific-label-swatch').dataset.color")
            wanted = observed['mapped'][observed['selected']]
            assert (None if shown == 'background' else [round(float(n), 6) for n in shown.split()]) == wanted, name
        # Inspect reads the exact uint64 label at the preview center and that label's own native feature row.
        section(page, 'Inspect')
        page.act('check', label='Inspect native values', value=True)
        cells = page.act('eval', js="[...document.querySelectorAll('select[aria-label=\"Inspect layer\"] option')].find(o => o.textContent === 'Cells').value")
        page.act('select', label='Inspect layer', value=cells)
        page.act('click', role='button', name='Inspect preview center')
        page.act('waittext', css='[role=status]', text=f'Value {BIG}', timeout=60000)
        table = page.act('text', css='section[aria-label="Label features"]')
        assert f'Label {BIG} · feature row 2' in table and '48.25' in table and 'uint64' in table


IMAGE_STEPS = [
    ('appearance', [('fill', 'Contrast minimum Native input', '1000'), ('fill', 'Contrast maximum Native input', '50000'),
                    ('fill', 'Gamma Native input', '0.5')], "image.contrast_limits = (1000, 50000)\nimage.gamma = 0.5"),
    ('shortcut', [('key', 'c', None)], "image.reset_contrast_limits()"),
    ('appearance-again', [('fill', 'Contrast minimum Native input', '1000'), ('fill', 'Contrast maximum Native input', '50000')],
     "image.contrast_limits = (1000, 50000)"),
    ('colormap', [('select', 'Color map Native input', 'name:viridis')], "image.colormap = 'viridis'"),
    ('custom-linear', [('select', 'Color map Native input', 'custom'), ('fill', 'Custom color map Native input', json.dumps(RAMP))],
     colormap_code(RAMP)),
    ('custom-zero', [('select', 'Color map Native input', 'custom'), ('fill', 'Custom color map Native input', json.dumps(STEP))],
     colormap_code(STEP)),
    *[(f'interpolation-{mode}', [('select', '2D interpolation Native input', mode)], f"image.interpolation2d = {mode!r}")
      for mode in INTERPOLATION],
    ('interpolation-custom', [('select', '2D interpolation Native input', 'custom'), ('fill', 'Custom 2D kernel Native input', json.dumps(KERNEL))],
     f"import numpy as np\nimage.custom_interpolation_kernel_2d = np.asarray({KERNEL!r}, dtype=np.float32)\nimage.interpolation2d = 'custom'"),
    ('channel', [('fill', 'Slice axis 1', '0')], "viewer.dims.set_current_step(1, 0)"),
    ('histogram', [('check', 'Show histogram Native input', True), ('fill', 'Histogram bins Native input', '64')],
     "image.histogram.bins = 64\nimage.histogram.enabled = True"),
    ('full', [('select', 'Histogram values from Native input', 'full')], "image.histogram.mode = 'full'"),
    ('samples', [('fill', 'Histogram sample limit Native input', '5000')], "image.histogram.max_samples = 5000"),
    ('log', [('check', 'Logarithmic counts Native input', True)], "image.histogram.log_scale = True"),
    ('reset-histogram', [('click', 'Reset histogram Native input', None)], "image.histogram.reset()"),
    ('auto-once', [('click', 'Auto-contrast once Native input', None)], "image.reset_contrast_limits()"),
    ('set-range', [('fill', 'Contrast range minimum Native input', '0'), ('fill', 'Contrast range maximum Native input', '40000')],
     "image.contrast_limits_range = (0, 40000)"),
    ('autoscale-range', [('click', 'Autoscale contrast range to data Native input', None)],
     "image.reset_contrast_limits()\nimage.contrast_limits_range = image.contrast_limits"),  # napari's popup reset()
    ('reset-range', [('click', 'Full contrast range Native input', None)], "image.reset_contrast_limits_range()"),
    ('continuous', [('check', 'Automatic contrast Native input', True)], "image.auto_contrast = True"),
]


def test_browser_image_controls_reach_native_napari_state(tmp_path):
    values = fixture_values()
    reference_report = reference_run(SCENE + "image = viewer.layers['Native input']\n",
                              [{'apply': '', 'observe': OBSERVE_IMAGE}]
                              + [{'apply': code, 'observe': OBSERVE_IMAGE} for *_, code in IMAGE_STEPS],
                              width=64, height=64, work=tmp_path / 'reference')
    reference = reference_report['results']
    with studio(tmp_path) as page:
        open_session(page)
        admit(reference_report, json.loads(python(page, observation_source())))
        first = observe(page, OBSERVE_IMAGE)
        assert first == reference[0]['observed']
        section(page, 'View')
        offered = page.act('eval', js="[...document.querySelectorAll('select[aria-label=\"Color map Native input\"] option')]"
                                      ".map(o => o.value).filter(v => v.startsWith('name:')).map(v => v.slice(5))")
        assert offered == first['choices']  # every colormap napari offers this layer, in its order
        for index, (name, controls, _) in enumerate(IMAGE_STEPS, start=1):
            operate(page, controls)
            observed = observe(page, OBSERVE_IMAGE)
            assert observed == reference[index]['observed'], name
            assert observed['data'] == first['data']  # native values never change
            plane = values[tuple(observed['step'][:3])].astype(float)
            if name.startswith('interpolation-'):
                assert observed['interpolation'] == name.removeprefix('interpolation-')
                assert page.act('value', label='2D interpolation Native input') == observed['interpolation']
            if name == 'interpolation-custom':
                assert observed['kernel'] == KERNEL
            if name in ('custom-linear', 'custom-zero'):
                wanted = RAMP if name == 'custom-linear' else STEP
                assert (observed['colors'], observed['controls'], observed['cmapInterpolation'], observed['nan'], observed['high'],
                        observed['low']) == (wanted['colors'], wanted['controls'], wanted['interpolation'], wanted['nanColor'],
                                             wanted['highColor'], wanted['lowColor'])
            if name == 'channel':
                assert observed['step'][1] == 0 and observed['step'][0] == first['step'][0]
            if name in ('histogram', 'full', 'samples', 'log'):
                data = plane if name == 'histogram' else values.astype(float)
                counts, _ = np.histogram(data, bins=64, range=tuple(observed['range']))
                if name == 'samples':
                    assert sum(observed['counts']) == 5000
                elif name == 'log':
                    assert abs(sum(10 ** np.array(observed['counts']) - 1) - 5000) < 1
                else:
                    assert observed['counts'] == [float(n) for n in counts]
                caption = page.act('text', css='fieldset:has(legend:text-is("Native input · histogram"))')
                assert '64 bins from ' in caption and ('log10(count + 1)' in caption) == (name == 'log')
            if name == 'reset-histogram':
                assert (observed['enabled'], observed['bins'], observed['mode'], observed['log'], observed['samples']) == (False, 256, 'canvas', False, 1000000)
            if name in ('auto-once', 'continuous', 'shortcut'):
                assert observed['limits'] == [float(plane.min()), float(plane.max())]
            if name == 'autoscale-range':
                assert observed['limits'] == [float(plane.min()), float(plane.max())] == observed['range']
            if name == 'reset-range':
                assert observed['range'] == [0.0, 65535.0]
            if name == 'appearance':
                assert observed['limits'] == [1000.0, 50000.0] and observed['gamma'] == 0.5
        # Inspect reads the exact native value and its dtype at the preview center of the shown slice.
        section(page, 'Inspect')
        page.act('check', label='Inspect native values', value=True)
        page.act('click', role='button', name='Inspect preview center')
        page.act('waittext', css='[role=status]', text='Value ', timeout=60000)
        status = page.act('eval', js="[...document.querySelectorAll('[role=status]')].map(e => e.textContent).find(t => t.startsWith('Value '))")
        y, x = (round(float(n)) for n in re.findall(r'\(data (-?[0-9.e+-]+)\)', status)[-2:])
        assert status.startswith(f'Value {values[tuple(observed["step"][:3])][y, x]} · <u2'), status
