"""Points and Shapes styling through the production Studio in a real browser on an isolated profile.

Opt-in (tests/scientific_browser.py): the prepared review bundle, a Chromium and Playwright, plus
NAPARI_REFERENCE_PYTHON for the plain pinned-napari comparison. A researcher's path only: DATA opens the saved
document, Open session starts its private view, the Python section adds the Points or Shapes layer and reads
native state (its API equivalent), the View section's controls change it and Inspect selects. Every observation
is compared with plain pinned napari doing the same through its public API, and with independent values. Renders
are compared with napari in test_scientific_points_shapes_parity.py, which drives the same worker requests.
"""
import numpy as np

from tests.scientific_browser import Studio
from tests.scientific_parity import admit, observation_source, reference_run
import json

from tests.test_scientific_image_labels_browser import operate, section
from tests.test_scientific_points_shapes_parity import (ANCHORS, AREAS, CYCLE, IMAGE_CODE, KINDS, OBSERVE_POINTS, OBSERVE_SHAPES,
                                                        POINTS_CODE, SHAPE, SHAPE_KINDS, SHAPE_VALUES, SHAPES_CODE, SYMBOLS,
                                                        first_appearance, gray)

TITLE = 'H06b fixture: 3D volume for Points and Shapes'
SCENE = IMAGE_CODE + "viewer.add_image(values, name='Native input')\n"


def test_browser_points_border_colormap_and_limits_match_independent_values(tmp_path):
    commands = [f"spots.border_color = 'area'\nspots.border_colormap = 'gray'\nspots.border_contrast_limits = (0, {high})\n"
                "spots.border_color_mode = 'colormap'" for high in (6, 3)]
    reference_report = reference_run(SCENE + POINTS_CODE,
                              [{'apply': "spots = viewer.layers['Spots']", 'observe': OBSERVE_POINTS}]
                              + [{'apply': code, 'observe': OBSERVE_POINTS} for code in commands],
                              width=64, height=64, work=tmp_path / 'reference')
    reference = reference_report['results']
    with studio(tmp_path) as page:
        open_session(page)
        admit(reference_report, json.loads(python(page, observation_source())))
        python(page, POINTS_CODE)
        assert observe(page, OBSERVE_POINTS) == reference[0]['observed']
        for index, high in enumerate((6, 3), start=1):
            operate(page, [('select', 'Border color mode Spots', 'colormap'),
                           ('select', 'Border color feature Spots', 'area'),
                           ('select', 'Border colormap Spots', 'gray'),
                           ('fill', 'Border contrast minimum Spots', '0'),
                           ('fill', 'Border contrast maximum Spots', str(high))])
            observed = observe(page, OBSERVE_POINTS)
            assert observed == reference[index]['observed']
            assert observed['border']['limits'] == [0, high] and observed['border']['mode'] == 'colormap'
            assert np.allclose(observed['border']['colors'], gray(AREAS, 0, high), atol=1e-6)
            section(page, 'View')
            assert page.act('value', label='Border contrast minimum Spots') == '0'
            assert page.act('value', label='Border contrast maximum Spots') == str(high)


class studio(Studio):
    """The isolated Studio; a failing journey keeps a screenshot, the status lines and the Python history beside it."""
    def __init__(self, tmp_path):
        super().__init__(tmp_path / 'studio', {TITLE: {'volume': (np.zeros(SHAPE, dtype=np.uint16), ('z', 'y', 'x'), 'intensity')}})

    def __exit__(self, kind, error, trace):
        if kind is not None and self.driver is not None:
            try:
                self.act('shot', path=str(self.folder / 'failure.png'))
                (self.folder / 'failure.txt').write_text(self.act('eval', js=STATUS) + '\n\n' + self.act('text', max=20000))
            except Exception:  # noqa: BLE001 - the journey's own failure is what is reported
                pass
        return super().__exit__(kind, error, trace)


CELLS = '[aria-label="Python cell history"] section'
CONSOLE_STATUS = "([...document.querySelectorAll('[role=status]')].map(e => e.textContent).find(t => t.startsWith('Running Python') || t.startsWith('Finished') || t.startsWith('Python reported') || t.startsWith('No confirmed')) ?? '')"


def python(page, code):
    """Run code in the Studio's Python section and return the new cell's stdout.

    Run code is enabled only while the scientific view is not busy, and every redraw makes it busy for a moment, so
    a click can land as it turns disabled and start nothing (no "Running Python…", no cell). Only then is it clicked
    again: a cell that started is never run twice.
    """
    section(page, 'Python')
    count = page.act('eval', js=f"document.querySelectorAll('{CELLS}').length")
    page.act('fill', label='Python code', value=code)
    for attempt in range(5):
        page.act('click', role='button', name='Run code')
        for _ in range(20):
            started = page.act('eval', js=f"document.querySelectorAll('{CELLS}').length > {count} || {CONSOLE_STATUS}.startsWith('Running Python')")
            if started:
                break
            page.act('wait', ms=100)
        if started:
            break
    else:
        raise AssertionError('Run code started nothing after five clicks: ' + page.act('eval', js=STATUS))
    page.act('waitcount', css=CELLS, count=count, timeout=120000)  # this cell's own record, never an earlier status
    last = page.act('eval', js=f"(() => {{ const cell = [...document.querySelectorAll('{CELLS}')].at(-1); "
                               "const out = cell.querySelector('pre[aria-label=stdout]'); return [out ? out.textContent : '', cell.textContent]; })()")
    if not last[0]:
        assert not any(word in last[1] for word in ('Error', 'Traceback')), last[1][-3000:]
    return last[0]


def observe(page, code):
    return json.loads(python(page, code))


def open_session(page):
    page.act('waitfor', role='button', name='Data', timeout=120000)
    page.act('click', role='button', name='Data')
    page.act('click', role='button', name=TITLE, timeout=60000)
    page.act('click', role='button', name='Open session', timeout=60000)
    page.act('waittext', css='[role=status]', text='Private scientific view open', timeout=240000)


def cycle_text(cycle):
    """Colors as the control shows them (JavaScript's own number text: 1, not 1.0)."""
    return '\n'.join(' '.join(f'{v:g}' for v in color) for color in cycle)


def select_at_center(page, layer, button, center, observation, expected):
    """Center the preview on a data position (Python, the camera's API), then select there through Inspect, and
    wait until napari's own selected_data is the expected set (this selection's effect, never an earlier status)."""
    python(page, f"viewer.camera.center = (viewer.camera.center[0], {center[0]}, {center[1]})")
    section(page, 'Inspect')
    page.act('check', label='Inspect native values', value=True)
    value = page.act('eval', js="[...document.querySelectorAll('select[aria-label=\"Inspect layer\"] option')]"
                                f".find(o => o.textContent === {layer!r}).value")
    page.act('select', label='Inspect layer', value=value)
    kind = 'Point' if layer == 'Spots' else 'Shape'
    for _ in range(20):  # the preview redraws at the new camera after the cell; inspect once it shows a row there
        page.act('click', role='button', name='Inspect preview center')
        page.act('wait', ms=500)
        status = page.act('eval', js=STATUS)
        if f'{kind} row' in status:
            break
        page.act('wait', ms=1000)
    else:
        raise AssertionError(f'Inspect found no {kind.lower()} at the preview center: {status}')
    for _ in range(5):  # a redraw after the inspection is refused in words ("Inspect again"): inspect again, then select
        page.act('click', role='button', name=button, timeout=60000)
        page.act('waitfor', css=f'[role=status]:text-matches("selection updated in this private view|Inspect again|inspect again")',
                 timeout=60000)
        if 'selection updated in this private view' in page.act('eval', js=STATUS):
            break
        page.act('click', role='button', name='Inspect preview center')
        page.act('waittext', css='[role=status]', text=f'{kind} row', timeout=60000)
    else:
        raise AssertionError('the selection was refused five times: ' + page.act('eval', js=STATUS))
    for _ in range(40):
        try:
            if observe(page, observation)['selected'] == expected:
                return
        except AssertionError:  # the session was still busy with the selection
            pass
        page.act('wait', ms=500)
    raise AssertionError(f'{layer}: the selection never became {expected}')


STATUS = "[...document.querySelectorAll('[role=status]')].map(e => e.textContent).join(' | ')"


POINT_STEPS = [
    # name, the controls a researcher sets (operation, accessible name, value), and napari's public API doing the same
    *[(f'symbol-{symbol}', [('select', 'Point symbol Spots', symbol), ('fill', 'Point size Spots', '2')],
       f"spots.symbol = {symbol!r}\nspots.size = 2") for symbol in SYMBOLS],
    ('border-relative', [('fill', 'Border width Spots', '0.25'), ('fill', 'Border color Spots', '1 0 0 1')],
     "spots.border_width = 0.25\nspots.border_color = [1, 0, 0, 1]"),
    ('border-absolute', [('check', 'Border width relative to size Spots', False), ('fill', 'Border width Spots', '0.5')],
     "spots.border_width_is_relative = False\nspots.border_width = 0.5"),
    ('face-cycle', [('select', 'Face color mode Spots', 'cycle'), ('select', 'Face color feature Spots', 'kind'),
                    ('fill', 'Face color cycle Spots', cycle_text(CYCLE))],
     f"spots.face_color = 'kind'\nspots.face_color_cycle = {CYCLE!r}\nspots.face_color_mode = 'cycle'"),
    ('face-colormap', [('select', 'Face color mode Spots', 'colormap'), ('select', 'Face color feature Spots', 'area'),
                       ('select', 'Face colormap Spots', 'gray'), ('fill', 'Face contrast minimum Spots', '0'),
                       ('fill', 'Face contrast maximum Spots', '6')],
     "spots.face_color = 'area'\nspots.face_colormap = 'gray'\nspots.face_contrast_limits = (0.0, 6.0)\nspots.face_color_mode = 'colormap'"),
    ('face-colormap-range', [('select', 'Face colormap Spots', 'viridis'), ('fill', 'Face contrast minimum Spots', ''),
                             ('fill', 'Face contrast maximum Spots', '')],
     "spots.face_color = 'area'\nspots.face_colormap = 'viridis'\nspots.face_contrast_limits = (float(spots.features['area'].min()), float(spots.features['area'].max()))\nspots.face_color_mode = 'colormap'"),
    ('border-cycle', [('select', 'Border color mode Spots', 'cycle'), ('select', 'Border color feature Spots', 'kind'),
                      ('fill', 'Border color cycle Spots', cycle_text(CYCLE[::-1]))],
     f"spots.border_color = 'kind'\nspots.border_color_cycle = {CYCLE[::-1]!r}\nspots.border_color_mode = 'cycle'"),
    ('face-direct', [('select', 'Face color mode Spots', 'direct'), ('fill', 'Face color Spots', '0 0 1 1')],
     "spots.face_color = [0, 0, 1, 1]"),
    ('text-feature', [('select', 'Text from Spots', 'feature'), ('fill', 'Text value Spots', 'kind'), ('fill', 'Text color Spots', '1 1 0 1')],
     "spots.text.string = {'feature': 'kind'}\nspots.text.color = [1, 1, 0, 1]"),
    ('text-format', [('select', 'Text from Spots', 'format'), ('fill', 'Text value Spots', '{kind}: {area:.1f}')],
     "spots.text.string = {'format': '{kind}: {area:.1f}'}"),
    ('text-constant', [('select', 'Text from Spots', 'constant'), ('fill', 'Text value Spots', 'cell'), ('fill', 'Text color Spots', '0 1 1 1')],
     "spots.text.string = {'constant': 'cell'}\nspots.text.color = [0, 1, 1, 1]"),
    *[(f'anchor-{anchor}', [('select', 'Text anchor Spots', anchor)], f"spots.text.anchor = {anchor!r}") for anchor in ANCHORS],
    ('text-display', [('fill', 'Text size Spots', '14'), ('fill', 'Text rotation in degrees Spots', '30'),
                      ('fill', 'Text offset in data coordinates Spots', '1.5'), ('select', 'Text blending Spots', 'additive'),
                      ('check', 'Scale text with zoom Spots', False)],
     "spots.text.size = 14\nspots.text.rotation = 30\nspots.text.translation = 1.5\nspots.text.blending = 'additive'\nspots.text.scaling = False"),
    ('text-hidden', [('check', 'Show feature text Spots', False)], "spots.text.visible = False"),
    ('shading-spherical', [('select', 'Point shading Spots', 'spherical')], "spots.shading = 'spherical'"),
    ('shading-none', [('select', 'Point shading Spots', 'none')], "spots.shading = 'none'"),
]
SELECTION = ("viewer.dims.set_current_step(0, 0)\nspots.selected_data = {1}\n"
             "viewer.dims.set_current_step(0, 1)\nspots.selected_data = set(spots.selected_data) | {3}")


def test_browser_points_style_and_selection_reach_native_napari_state(tmp_path):
    reference_report = reference_run(SCENE + POINTS_CODE + "spots = viewer.layers['Spots']\n",
                              [{'apply': '', 'observe': OBSERVE_POINTS}]
                              + [{'apply': code, 'observe': OBSERVE_POINTS} for *_, code in POINT_STEPS]
                              + [{'apply': SELECTION, 'observe': OBSERVE_POINTS}],
                              width=64, height=64, work=tmp_path / 'reference')
    reference = reference_report['results']
    with studio(tmp_path) as page:
        open_session(page)
        admit(reference_report, json.loads(python(page, observation_source())))
        python(page, POINTS_CODE)
        first = observe(page, OBSERVE_POINTS)
        assert first == reference[0]['observed']
        section(page, 'View')
        assert page.act('value', label='Point symbol Spots') == 'disc' and page.act('value', label='Point size Spots') == '3'
        offered = page.act('eval', js="[...document.querySelectorAll('select[aria-label=\"Point symbol Spots\"] option')].map(o => o.value)")
        assert offered == list(SYMBOLS)  # every symbol napari offers, and no other
        for index, (name, controls, _) in enumerate(POINT_STEPS, start=1):
            operate(page, controls)
            for op, label, value in controls:  # each control now shows the accepted native value
                if op in ('fill', 'select') and value != '':
                    assert page.act('value', label=label) == value, (name, label)
                if op == 'check':
                    assert page.act('checked', label=label) is value, (name, label)
            observed = observe(page, OBSERVE_POINTS)
            assert observed == reference[index]['observed'], name
            assert (observed['data'], observed['features']) == (first['data'], first['features'])  # never written
            if name.startswith('symbol-'):
                assert observed['symbol'] == [name.removeprefix('symbol-')] * 6 and observed['size'] == [2.0] * 6
            if name == 'face-cycle':
                assert observed['face']['colors'] == first_appearance(KINDS, CYCLE)
            if name == 'face-colormap':
                assert np.allclose(observed['face']['colors'], gray(AREAS, 0.0, 6.0), atol=1e-6)
            if name == 'face-colormap-range':  # blank limits: napari's data range, now shown in the controls
                assert observed['face']['limits'] == [1.0, 6.0]
                section(page, 'View')
                assert (page.act('value', label='Face contrast minimum Spots'), page.act('value', label='Face contrast maximum Spots')) == ('1', '6')
            if name == 'border-cycle':
                assert observed['border']['colors'] == first_appearance(KINDS, CYCLE[::-1])
            if name == 'text-format':
                assert observed['text']['values'] == [f'{k}: {a:.1f}' for k, a in zip(KINDS, AREAS)]
            if name.startswith('anchor-'):
                assert observed['text']['anchor'] == name.removeprefix('anchor-')
        # Step 3: select a point on slice 0, move to slice 1 and add one there; both stay selected.
        operate(page, [('fill', 'Slice axis 0', '0')])
        select_at_center(page, 'Spots', 'Select this point', (4, 14), OBSERVE_POINTS, [1])
        operate(page, [('fill', 'Slice axis 0', '1')])
        select_at_center(page, 'Spots', 'Add to selection', (12, 14), OBSERVE_POINTS, [1, 3])
        observed = observe(page, OBSERVE_POINTS)
        assert observed == reference[-1]['observed'] and observed['selected'] == [1, 3]
        section(page, 'Inspect')  # inspect again: later cells changed the view, so earlier details were cleared
        page.act('click', role='button', name='Inspect preview center')
        page.act('waittext', css='[role=status]', text='Point row 3', timeout=60000)
        shown = page.act('text', css='section[aria-label="Point features"]')  # Inspect shows that point's own style
        assert 'Point row 3' in shown and 'Marker: x, size 2, border width 0.5; face color 0 0 1 1;' in shown, shown


SHAPE_STEPS = [
    ('edge-width', [('fill', 'Edge width Outlines', '1')], "outlines.edge_width = 1\noutlines.events.edge_width()  # as napari's width control does: the setter emits nothing"),
    ('face-direct', [('fill', 'Face color Outlines', '0 1 0 1')], "outlines.face_color = [0, 1, 0, 1]"),
    ('edge-direct', [('fill', 'Edge color Outlines', '0 0 1 1')], "outlines.edge_color = [0, 0, 1, 1]"),
    ('face-cycle', [('select', 'Face color mode Outlines', 'cycle'), ('select', 'Face color feature Outlines', 'kind'),
                    ('fill', 'Face color cycle Outlines', cycle_text(CYCLE))],
     f"outlines.face_color = 'kind'\noutlines.face_color_cycle = {CYCLE!r}\noutlines.face_color_mode = 'cycle'"),
    ('edge-cycle', [('select', 'Edge color mode Outlines', 'cycle'), ('select', 'Edge color feature Outlines', 'kind'),
                    ('fill', 'Edge color cycle Outlines', cycle_text(CYCLE[::-1]))],
     f"outlines.edge_color = 'kind'\noutlines.edge_color_cycle = {CYCLE[::-1]!r}\noutlines.edge_color_mode = 'cycle'"),
    ('face-colormap', [('select', 'Face color mode Outlines', 'colormap'), ('select', 'Face color feature Outlines', 'v'),
                       ('select', 'Face colormap Outlines', 'gray'), ('fill', 'Face contrast minimum Outlines', '0'),
                       ('fill', 'Face contrast maximum Outlines', '6')],
     "outlines.face_color = 'v'\noutlines.face_colormap = 'gray'\noutlines.face_contrast_limits = (0.0, 6.0)\noutlines.face_color_mode = 'colormap'"),
    ('edge-colormap', [('select', 'Edge color mode Outlines', 'colormap'), ('select', 'Edge color feature Outlines', 'v'),
                       ('select', 'Edge colormap Outlines', 'gray'), ('fill', 'Edge contrast minimum Outlines', '0'),
                       ('fill', 'Edge contrast maximum Outlines', '6')],
     "outlines.edge_color = 'v'\noutlines.edge_colormap = 'gray'\noutlines.edge_contrast_limits = (0.0, 6.0)\noutlines.edge_color_mode = 'colormap'"),
    ('text-format', [('select', 'Text from Outlines', 'format'), ('fill', 'Text value Outlines', '{kind}={v:.0f}'),
                     ('fill', 'Text color Outlines', '1 1 0 1')],
     "outlines.text.string = {'format': '{kind}={v:.0f}'}\noutlines.text.color = [1, 1, 0, 1]"),
]


def test_browser_shapes_style_and_z_order_reach_native_napari_state(tmp_path):
    order = ["outlines.selected_data = {0}\noutlines.move_to_front()", "outlines.move_to_back()"]
    reference_report = reference_run(SCENE + SHAPES_CODE + "outlines = viewer.layers['Outlines']\n",
                              [{'apply': '', 'observe': OBSERVE_SHAPES}]
                              + [{'apply': code, 'observe': OBSERVE_SHAPES} for *_, code in SHAPE_STEPS]
                              + [{'apply': code, 'observe': OBSERVE_SHAPES} for code in order],
                              width=64, height=64, work=tmp_path / 'reference')
    reference = reference_report['results']
    with studio(tmp_path) as page:
        open_session(page)
        admit(reference_report, json.loads(python(page, observation_source())))
        python(page, SHAPES_CODE)
        first = observe(page, OBSERVE_SHAPES)
        assert first == reference[0]['observed']
        section(page, 'View')
        assert page.act('value', label='Edge width Outlines') == '0.5'
        for index, (name, controls, _) in enumerate(SHAPE_STEPS, start=1):
            operate(page, controls)
            observed = observe(page, OBSERVE_SHAPES)
            assert observed == reference[index]['observed'], name
            assert (observed['data'], observed['features']) == (first['data'], first['features'])
            if name == 'face-cycle':
                assert observed['face']['colors'] == first_appearance(SHAPE_KINDS, CYCLE)
            if name == 'edge-cycle':
                assert observed['edge']['colors'] == first_appearance(SHAPE_KINDS, CYCLE[::-1])
            if name == 'face-colormap':
                assert np.allclose(observed['face']['colors'], gray(SHAPE_VALUES, 0.0, 6.0), atol=1e-6)
            if name == 'edge-colormap':
                assert np.allclose(observed['edge']['colors'], gray(SHAPE_VALUES, 0.0, 6.0), atol=1e-6)
            if name == 'text-format':
                assert observed['text']['values'] == [f'{k}={v:.0f}' for k, v in zip(SHAPE_KINDS, SHAPE_VALUES)]
        # Z-order: select shape A in Inspect, then move it to the front and to the back.
        operate(page, [('fill', 'Slice axis 0', '0')])  # every shape lies on slice 0
        select_at_center(page, 'Outlines', 'Select this shape', (4, 4), OBSERVE_SHAPES, [0])
        section(page, 'Inspect')
        page.act('click', role='button', name='Inspect preview center')
        page.act('waittext', css='[role=status]', text='Shape row 0', timeout=60000)
        shown = page.act('text', css='section[aria-label="Shape features"]')
        assert 'Shape row 0 · rectangle' in shown and 'Edge width 1;' in shown and 'z-order 0.' in shown, shown
        operate(page, [('click', 'Move selected shapes to front Outlines', None)])
        observed = observe(page, OBSERVE_SHAPES)
        assert observed == reference[-2]['observed'] and observed['z'][0] > max(observed['z'][1:]) and observed['selected'] == [0]
        operate(page, [('click', 'Move selected shapes to back Outlines', None)])
        observed = observe(page, OBSERVE_SHAPES)
        assert observed == reference[-1]['observed'] and observed['z'][0] < min(observed['z'][1:])
