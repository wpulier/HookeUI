"""P1 dimensions (H04) in a real browser, in an isolated profile, against the actual review worker.

Each test operates the Studio as a person does (Project Data, the View controls, the preview, Inspect, the Python
panel) in a fresh Chromium profile served by a private orchestrator, and checks three things against each other:

- what the Studio shows and the actual worker's native state (napari in Hooke's confined review worker);
- the same steps in a plain pinned-napari reference process (tests/scientific_napari_reference.py);
- independent expected values from the fixture's definition (tests/scientific_dims_fixture.py).

Prepared runtimes are required and never installed here: NAPARI_NATIVE_HOST_PREPARATION (the prepared review
integration, whose own Python is also the reference's) and HOOKE_BROWSER (a Chromium executable). Without them every
test skips, and the acceptance runner reports their children missing, never passed.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
import time

import numpy as np
import pytest

from tests import browser_cdp, scientific_dims_fixture as fx, scientific_napari_reference as reference
from tests.scientific_studio_stack import Stack, preparation

pytestmark = pytest.mark.xdist_group('hooke-studio-browser')
TITLE = 'Asymmetric 5D fixture'
LAYER = 'Native input'  # the prepared integration's name for its bound input layer
CALIBRATE = (f"layer = viewer.layers[{LAYER!r}]\nlayer.scale = {fx.SCALE!r}\nlayer.translate = {fx.TRANSLATE!r}\n"
             f"layer.axis_labels = {fx.AXES!r}\nlayer.units = {fx.UNITS!r}")
RESET = "viewer.dims.ndisplay = 2\nviewer.dims.last_used = 0\nviewer.dims.order = (0, 1, 2, 3, 4)\nviewer.dims.margin_left = (0,) * 5\n" \
        "viewer.dims.margin_right = (0,) * 5\nviewer.layers[0].projection_mode = 'mean'\n"


def start(steps):
    return {'layers': [{'name': LAYER, 'shape': list(fx.SHAPE), 'dtype': '<u2'}], 'steps': steps}


def run_reference(prepared, steps):
    """The pinned napari reference: the fixture with the same calibration, then the given steps."""
    # The same setup the worker gets from Studio.reset (projection, margins, order), so both start alike.
    plan = start([{'name': 'calibrate', 'code': CALIBRATE + '\n' + RESET}, *steps])
    report = reference.run(reference.runtime_python(prepared), plan['layers'], plan['steps'])
    assert all(r['error'] is None for r in report['results']), report['results']
    return report


def inspect_mode(studio, on):
    """Turn Inspect native values on or off with a click (its checkbox in the Inspect tab)."""
    session, page = studio.session, studio.page
    session.tab('Inspect')
    toggle = "document.querySelector('[aria-label=\"Inspect native values\"]')"
    if page.eval(f"({toggle}).checked") != on:
        page.click(toggle)
    session.tab('View')


class Studio:
    def __init__(self, stack, browser, page, session, prepared):
        self.stack, self.browser, self.page, self.session, self.prepared = stack, browser, page, session, prepared
        self.initial = None

    def reset(self, steps):
        """Setup through the Python panel (not the behaviour under test): a known view to start from."""
        code = RESET + f"viewer.dims.current_step = {tuple(steps)!r}\nviewer.reset_view()\n"
        cell = self.session.python(code)
        assert 'error' not in cell, cell
        self.page.click(self.button('Check current view'))
        self.session.settle()
        return code

    @staticmethod
    def button(label):
        from tests.scientific_studio_session import button
        return button(label)


@pytest.fixture
def studio():
    # Each case owns its history/profile: a previous journey must not consume the 128-cell product limit.
    prepared, executable = preparation(), browser_cdp.executable()
    if prepared is None or executable is None:
        pytest.skip('needs NAPARI_NATIVE_HOST_PREPARATION and HOOKE_BROWSER; not run, so not evidence')
    from tests.scientific_studio_session import StudioSession
    import signal
    import threading

    def stopped(*_):  # a terminated test run still stops its private orchestrator, worker and browser
        raise KeyboardInterrupt('terminated')
    previous = signal.signal(signal.SIGTERM, stopped) if threading.current_thread() is threading.main_thread() else None
    stack = browser = page = None
    try:
        stack = Stack(prepared)
        stack.seed_scene(TITLE, {'volume': (fx.values(), fx.AXES, fx.scientific())})
        browser = browser_cdp.Browser(executable, width=1440, height=900)
        page = browser.page(stack.url + '/')
        page.record_requests()
        session = StudioSession(page)
        opened = session.open_from_project_data(TITLE)
        playback_defaults = {'rate': page.eval("document.querySelector('[aria-label=\"Maximum slices per second\"]').value"),
                             'mode': page.eval("document.querySelector('[aria-label=\"Playback mode\"]').value")}
        studio = Studio(stack, browser, page, session, prepared)
        centre = [n // 2 for n in fx.SHAPE]
        studio.initial = {'seconds': opened, 'playback_defaults': playback_defaults, 'ui': session.dims(), 'native': session.native_state([[LAYER, centre]])}
        cell = session.python(CALIBRATE)
        assert 'error' not in cell, cell
        yield studio
    finally:
        try:
            try:
                if browser is not None:
                    browser.close()
            finally:
                if stack is not None:
                    stack.close()
        finally:
            if previous is not None:
                signal.signal(signal.SIGTERM, previous)


def evidence(record_property, **values):
    record_property('evidence', json.dumps(values, sort_keys=True, default=str)[:60000])


def world_of(step, axis):
    return fx.world(step, axis)


def same(a, b, tol=1e-9):
    return all(math.isclose(x, y, rel_tol=0, abs_tol=tol) for x, y in zip(a, b, strict=True))


def compare_dims(native, ref, keys=('ndim', 'ndisplay', 'order', 'displayed', 'not_displayed', 'current_step',
                                    'nsteps', 'axis_labels', 'units', 'last_used', 'rollable')):
    for key in keys:
        assert native[key] == ref[key], (key, native[key], ref[key])
    assert same(native['point'], ref['point']), (native['point'], ref['point'])
    assert native['range'] == ref['range']


# ---- axis position --------------------------------------------------------------------------------------------

def test_open_5d_resource_from_project_data_shows_every_axis(studio, record_property):
    """Opening the saved 5D resource offers one slider per dimension and starts where napari starts."""
    ui, native = studio.initial['ui'], studio.initial['native']
    report = reference.run(reference.runtime_python(studio.prepared), start([])['layers'],
                           [{'name': 'open', 'code': 'pass', 'probes': [[LAYER, [n // 2 for n in fx.SHAPE]]]}])
    ref = report['results'][0]['state']
    pinned = reference.pinned_source_check(report)
    evidence(record_property, ui=ui, native=native, reference=ref, napari=report['napari'], pinned=pinned)
    assert pinned['differs'] == [], pinned  # the reference runs exactly the pinned napari source it loaded
    assert len(ui['axes']) == len(fx.SHAPE) == native['ndim'] == ref['ndim']
    assert native['nsteps'] == list(fx.SHAPE) == ref['nsteps']
    assert [a['max'] for a in ui['axes']] == [n - 1 for n in fx.SHAPE]
    assert [a['hidden'] for a in ui['axes']] == [False, False, False, True, True]
    assert native['displayed'] == [3, 4] == ref['displayed']
    assert native['current_step'] == ref['current_step'] == [a['index'] for a in ui['axes']]
    probe = native['probes'][0]
    assert probe['value'] == ref['probes'][0]['value'] == fx.value_at([n // 2 for n in fx.SHAPE])


def test_each_sliced_axis_moves_by_slider_numeric_entry_and_keyboard(studio, record_property):
    session, page = studio.session, studio.page
    studio.reset((1, 1, 3, 5, 6))
    steps, rows, ref_steps = [1, 1, 3, 5, 6], [], []
    targets = {0: (3, 2), 1: (2, 0), 2: (5, 1)}
    for axis, (dragged, typed) in targets.items():
        for how in ('slider', 'numeric', 'keyboard'):
            if how == 'slider':
                landed = session.drag_slider(axis, dragged)
                assert landed == dragged
                expected = dragged
            elif how == 'numeric':
                session.set_number(f'Slice axis {axis}', typed)
                expected = typed
            else:
                from tests.scientific_studio_session import labelled
                slider = labelled(f'Slice slider axis {axis}', 'input')
                page.focus(slider)  # as Tab reaches it; a click on the track would also move the thumb
                at = page.eval(f"Number(({slider}).value)")
                key = 'ArrowRight' if at < fx.SHAPE[axis] - 1 else 'ArrowLeft'
                expected = at + (1 if key == 'ArrowRight' else -1)
                page.key(key)
                page.wait(f"Number(({slider}).value) === {expected}", 10, 'the slider to take the key')
            status = session.apply()
            assert status.startswith('View changes applied'), status
            steps[axis] = expected
            world = [7.0 + 0.3 * 4, 5.0 + 0.4 * 3]  # displayed y, x probe at data (3, 4)
            probe = [world_of(steps[a], a) for a in range(3)] + [5.0 + 0.4 * 3, 7.0 + 0.3 * 4]
            native = session.native_state([[LAYER, probe]])
            ui = session.dims()
            rows.append({'axis': axis, 'how': how, 'expected': expected, 'ui': ui['axes'][axis], 'native': native})
            ref_steps.append({'name': f'{axis}-{how}', 'code': f'viewer.dims.set_current_step({axis}, {expected})',
                              'probes': [[LAYER, probe]]})
            assert ui['axes'][axis]['index'] == ui['axes'][axis]['slider'] == expected, (axis, how, ui['axes'][axis], expected, status)
            assert native['current_step'][:3] == steps[:3]
            assert math.isclose(native['point'][axis], world_of(expected, axis), abs_tol=1e-9)
            assert native['probes'][0]['value'] == fx.value_at(steps[:3] + [3, 4])
    report = run_reference(studio.prepared, [{'name': 'reset', 'code': 'viewer.dims.current_step = (1, 1, 3, 5, 6)'},
                                            *ref_steps])
    for row, result in zip(rows, report['results'][2:], strict=True):
        compare_dims(row['native'], result['state'])
        assert row['native']['probes'][0]['value'] == result['state']['probes'][0]['value']
    evidence(record_property, rows=rows, reference=[r['state'] for r in report['results']], napari=report['napari'])


def _inspect(studio, x_fraction, y_fraction):
    """Click the 2D preview with Inspect on; returns the parsed status line and the click's frame pixel."""
    from tests.scientific_studio_session import labelled
    page, session = studio.page, studio.session
    canvas = "document.querySelector('.scientific-viewport canvas')"
    rect = page.eval(f"(()=>{{const e={canvas},r=e.getBoundingClientRect();return [r.left,r.top,r.width,r.height,e.width,e.height]}})()")
    # Inspect reads only a drawn, current preview: its Inspect preview center button is enabled exactly then.
    page.wait("!document.querySelector('.scientific-inspection button').disabled", 60, 'a preview ready to inspect')
    page.drain()
    sent = len([r for r in page.requests('/request') if ((r[2] or {}).get('request') or {}).get('op') == 'inspect'])
    for _ in range(3):
        page.click(canvas, offset=((x_fraction - .5) * rect[2], (y_fraction - .5) * rect[3]))
        page.drain()
        if len([r for r in page.requests('/request') if ((r[2] or {}).get('request') or {}).get('op') == 'inspect']) > sent:
            break
        time.sleep(.5)  # the preview redrew under the pointer; click again, as a person would
    page.wait("(()=>{const t=document.querySelector('.scientific-inspection [role=status]')?.textContent||'';"
              "return t.startsWith('Value ') || t.startsWith('No sample')})()", 30, 'the inspected value')
    text = page.eval("document.querySelector('.scientific-inspection [role=status]').textContent")
    match = re.match(r'(?:Value (\S+) · (\S+)|No sample at this position) · (.*)$', text)
    assert match, text
    coordinates = {}
    for part in match.group(3).split(' · '):
        m = re.match(r'(\S+): (\S+) (\S+) \(data (\S+)\)', part)
        if m:
            coordinates[m.group(1)] = (float(m.group(2)), m.group(3), float(m.group(4)))
    # The drawn frame pixel under the pointer (the canvas shows the whole frame stretched over its box).
    pixel = (y_fraction * rect[5], x_fraction * rect[4])
    return {'text': text, 'value': None if match.group(1) is None else float(match.group(1)), 'dtype': match.group(2),
            'coordinates': coordinates, 'click': (x_fraction, y_fraction), 'pixel': pixel, 'frame': rect[4:]}


def _set_order(studio, order):
    """Choose each axis position, then Apply a real change, including a fresh case's default order."""
    from tests.scientific_studio_session import button
    session, page = studio.session, studio.page
    session.settle()
    if not page.eval("document.querySelector('.scientific-navigation details').open"):
        page.click("document.querySelector('.scientific-navigation details summary')")
    names = lambda: page.eval("[...document.querySelectorAll('.scientific-navigation details select')]"
                              ".map(s=>s.getAttribute('aria-label'))")
    if session.dims()['order'] == list(order):
        # A fresh default-plane/default-3D case has no draft: clicking disabled Apply starts nothing.
        # Set up another order through the same UI, then exercise the requested transition normally.
        assert page.eval(f"({button('Apply view changes')}).disabled"), 'a settled unchanged order has no draft'
        session.choose(names()[0], order[1])
        assert session.dims()['order'] != list(order)
        assert session.apply().startswith('View changes applied')
    for position, axis in enumerate(order):
        current = session.dims()['order']
        if current[position] != axis:
            session.choose(names()[position], axis)
    assert session.dims()['order'] == list(order)
    return session.apply()


@pytest.mark.parametrize('vertical,horizontal', [
    pytest.param(a, b, id=f'{a}-{b}') for a in range(5) for b in range(5) if a != b])
def test_probe_known_pixels_and_calibrated_coordinates_on_every_displayed_plane(
        studio, record_property, vertical, horizontal):
    session, page = studio.session, studio.page
    steps = (2, 1, 3, 5, 6)
    studio.reset(steps)
    rows = []
    ref_steps = []
    order = [n for n in range(5) if n not in (vertical, horizontal)] + [vertical, horizontal]
    status = _set_order(studio, order)
    assert status.startswith('View changes applied'), (order, status)
    inspect_mode(studio, True)
    session.tab('Inspect')
    sample = _inspect(studio, .5, .5)
    session.tab('View')
    world = [sample['coordinates'][fx.AXES[a]][0] for a in range(5)]
    data = [sample['coordinates'][fx.AXES[a]][2] for a in range(5)]
    index = [round(d) for d in data]
    native = session.native_state([[LAYER, world]])
    # Independent: the preview's centre is the camera's centre (napari's canvas_to_world), within one drawn
    # pixel; world = translate + data * scale on every axis; the value is the voxel's own index; and the sliced
    # axes sit at the chosen steps.
    centre, zoom = native['camera']['center'][-2:], native['camera']['zoom']
    shown = [page.eval(f"Number(document.querySelector('[aria-label=\"Camera center {name}\"]').value)")
             for name in ('vertical', 'horizontal')]
    assert same(shown, centre, 1e-6), (shown, centre)
    assert abs(world[vertical] - centre[0]) <= 1 / zoom and abs(world[horizontal] - centre[1]) <= 1 / zoom, (world, centre, zoom)
    for a in range(5):
        assert math.isclose(world[a], fx.TRANSLATE[a] + data[a] * fx.SCALE[a], abs_tol=1e-6), (a, world, data)
        assert sample['coordinates'][fx.AXES[a]][1] == fx.UNITS[a]
    for a in order[:3]:
        assert index[a] == steps[a]
    assert sample['value'] == fx.value_at(index) == native['probes'][0]['value']
    assert sample['dtype'] == '<u2'
    assert native['displayed'] == [vertical, horizontal]
    rows.append({'order': order, 'sample': sample, 'native': {k: native[k] for k in ('order', 'probes', 'layers')}})
    ref_steps.append({'name': str(order), 'code': f'viewer.dims.order = {tuple(order)!r}', 'probes': [[LAYER, world]]})
    report = run_reference(studio.prepared, [{'name': 'steps', 'code': f'viewer.dims.current_step = {steps!r}'},
                                            *ref_steps])
    for row, result in zip(rows, report['results'][2:], strict=True):
        assert row['native']['probes'] == result['state']['probes']
        assert row['native']['layers'][0]['slice_sha256'] == result['state']['layers'][0]['slice_sha256']
        expected = fx.plane(list(steps), row['order']).astype('<u2')
        assert result['state']['layers'][0]['slice_sha256'] == hashlib.sha256(np.ascontiguousarray(expected).tobytes()).hexdigest()
    evidence(record_property, rows=rows, napari=report['napari'])


def test_off_centre_clicks_map_canvas_to_world_with_each_axis_direction(studio, record_property):
    """napari's canvas_to_world: world = camera centre + (pixel - frame centre) / zoom, vertical down and horizontal
    right on the displayed axes, checked at off-centre points on (y, x) and on the swapped plane (x, y)."""
    session, page = studio.session, studio.page
    studio.reset((2, 1, 3, 5, 6))
    rows = []
    for order in ([0, 1, 2, 3, 4], [0, 1, 2, 4, 3]):
        if order != [0, 1, 2, 3, 4]:
            assert _set_order(studio, order).startswith('View changes applied')
        native = session.native_state()
        centre, zoom = native['camera']['center'][-2:], native['camera']['zoom']
        vertical, horizontal = order[-2:]
        inspect_mode(studio, True)
        session.tab('Inspect')
        for fraction in ((.3, .35), (.7, .4), (.45, .7), (.62, .62)):
            sample = _inspect(studio, *fraction)
            (py, px), (width, height) = sample['pixel'], sample['frame']
            expected = (centre[0] + (py - height / 2) / zoom, centre[1] + (px - width / 2) / zoom)
            shown = (sample['coordinates'][fx.AXES[vertical]][0], sample['coordinates'][fx.AXES[horizontal]][0])
            rows.append({'order': order, 'fraction': fraction, 'pixel': [py, px], 'expected': expected, 'shown': shown,
                         'centre': centre, 'zoom': zoom, 'text': sample['text']})
            # Within one drawn pixel: the click lands somewhere inside a pixel, napari reports that pixel's world.
            assert abs(shown[0] - expected[0]) <= 1 / zoom and abs(shown[1] - expected[1]) <= 1 / zoom, rows[-1]
            # And the direction: right of centre is larger horizontal world, below centre larger vertical world.
            assert (shown[1] > centre[1]) == (px > width / 2) and (shown[0] > centre[0]) == (py > height / 2), rows[-1]
        session.tab('View')
    assert _set_order(studio, [0, 1, 2, 3, 4]).startswith('View changes applied')
    evidence(record_property, rows=rows)


def test_go_to_a_world_coordinate_is_napari_set_point(studio, record_property):
    from tests.scientific_studio_session import button
    session, page = studio.session, studio.page
    studio.reset((0, 1, 3, 5, 6))
    rows = []
    cases = [(0, 16.3), (2, -1.2), (0, 99.0), (1, 1.4)]
    for axis, value in cases:
        session.set_number(f'World coordinate axis {axis}', value)
        session.press(f'Go to world coordinate axis {axis}')
        native = session.native_state()
        rows.append({'axis': axis, 'value': value, 'native': native, 'ui': session.dims()})
    session.set_number('World coordinate axis 0', '')  # an empty coordinate is refused before any request
    page.click(button('Go to world coordinate axis 0'))
    refused = session.status()
    report = run_reference(studio.prepared, [{'name': 'reset', 'code': 'viewer.dims.current_step = (0, 1, 3, 5, 6)'},
                                            *[{'name': f'{a}={v}', 'code': f'viewer.dims.set_point({a}, {v})'}
                                              for a, v in cases]])
    previous = [0, 1, 3, 5, 6]
    for row, result in zip(rows, report['results'][2:], strict=True):
        compare_dims(row['native'], result['state'])
        axis, value = row['axis'], row['value']
        low, high, step = fx.world_range(axis)
        # Independent: napari clips the point to the range. When that moves the slice, napari's slider takes the
        # new slice and puts the point on it; when the slice is unchanged the point keeps the entered coordinate.
        clipped = min(max(value, low), high)
        expected_step = round((clipped - low) / step)
        expected_point = clipped if expected_step == previous[axis] else low + expected_step * step
        previous[axis] = expected_step
        assert row['native']['current_step'][axis] == expected_step
        assert math.isclose(row['native']['point'][axis], expected_point, abs_tol=1e-9)
        assert row['ui']['axes'][axis]['index'] == expected_step
    assert 'Enter a finite world coordinate' in refused
    evidence(record_property, rows=rows, refused=refused, reference=[r['state'] for r in report['results']])


# ---- axis order and display mode -------------------------------------------------------------------------------

def test_roll_swap_reorder_and_restore_axes(studio, record_property):
    from tests.scientific_studio_session import button
    session, page = studio.session, studio.page
    studio.reset((2, 1, 3, 5, 6))
    order, rows, ref_steps = [0, 1, 2, 3, 4], [], []
    for label, action in (('Roll axes', 'roll'), ('Roll axes', 'roll'), ('Swap displayed axes', 'transpose'),
                          ('Roll axes', 'roll')):
        session.press(label)
        order = fx.roll(order) if action == 'roll' else fx.transpose(order)
        native = session.native_state()
        rows.append({'action': action, 'expected': order, 'native': native, 'ui': session.dims()})
        ref_steps.append({'name': action, 'code': f"actions.{'roll_axes' if action == 'roll' else 'transpose_axes'}(viewer)"})
        assert native['order'] == order == rows[-1]['ui']['order']
        assert native['displayed'] == order[-2:] and native['not_displayed'] == order[:-2]
    reordered = [4, 2, 0, 3, 1]
    assert _set_order(studio, reordered).startswith('View changes applied')
    rows.append({'action': 'reorder', 'expected': reordered, 'native': session.native_state(), 'ui': session.dims()})
    ref_steps.append({'name': 'reorder', 'code': f'viewer.dims.order = {tuple(reordered)!r}'})
    assert _set_order(studio, [0, 1, 2, 3, 4]).startswith('View changes applied')
    rows.append({'action': 'restore', 'expected': [0, 1, 2, 3, 4], 'native': session.native_state(), 'ui': session.dims()})
    ref_steps.append({'name': 'restore', 'code': 'viewer.dims.order = (0, 1, 2, 3, 4)'})
    report = run_reference(studio.prepared, [{'name': 'reset', 'code': 'viewer.dims.current_step = (2, 1, 3, 5, 6)'},
                                            *ref_steps])
    for row, result in zip(rows, report['results'][2:], strict=True):
        assert row['native']['order'] == row['expected'] == result['state']['order'] == row['ui']['order']
        compare_dims(row['native'], result['state'])
        assert row['native']['layers'][0]['slice_sha256'] == result['state']['layers'][0]['slice_sha256']
    evidence(record_property, rows=rows, reference=[r['state'] for r in report['results']])


def test_switch_two_and_three_displayed_dimensions_and_back(studio, record_property):
    from tests.scientific_studio_session import button
    session, page = studio.session, studio.page
    studio.reset((2, 1, 3, 5, 6))
    rows = []
    session.choose('Displayed dimensions', 3)
    assert session.apply().startswith('View changes applied')
    rows.append(('select-3d', session.native_state(), session.dims()))
    session.press('Toggle 2D/3D')
    rows.append(('toggle-2d', session.native_state(), session.dims()))
    session.press('Toggle 2D/3D')
    rows.append(('toggle-3d', session.native_state(), session.dims()))
    session.choose('Displayed dimensions', 2)
    assert session.apply().startswith('View changes applied')
    rows.append(('select-2d', session.native_state(), session.dims()))
    report = run_reference(studio.prepared, [
        {'name': 'reset', 'code': 'viewer.dims.current_step = (2, 1, 3, 5, 6)'},
        {'name': 'select-3d', 'code': 'viewer.dims.ndisplay = 3'},
        {'name': 'toggle-2d', 'code': 'actions.toggle_ndisplay(viewer)'},
        {'name': 'toggle-3d', 'code': 'actions.toggle_ndisplay(viewer)'},
        {'name': 'select-2d', 'code': 'viewer.dims.ndisplay = 2'}])
    for (name, native, ui), result, ndisplay in zip(rows, report['results'][2:6], (3, 2, 3, 2), strict=True):
        compare_dims(native, result['state'])
        assert native['ndisplay'] == ndisplay and ui['display'] == str(ndisplay)
        assert native['displayed'] == [0, 1, 2, 3, 4][-ndisplay:]
        assert [a['hidden'] for a in ui['axes']] == [a in native['displayed'] for a in range(5)]
    evidence(record_property, rows=rows, reference=[r['state'] for r in report['results']])


@pytest.mark.parametrize('shown', [
    pytest.param(axes, id='-'.join(map(str, axes))) for axes in itertools.combinations(range(5), 3)])
def test_each_three_dimensional_axis_combination(studio, record_property, shown):
    """Every set of three displayed axes, each in its own bounded Studio/reference journey."""
    session = studio.session
    steps = (2, 1, 3, 5, 6)
    studio.reset(steps)
    session.choose('Displayed dimensions', 3)
    assert session.apply().startswith('View changes applied')
    order = [n for n in range(5) if n not in shown] + list(shown)
    assert _set_order(studio, order).startswith('View changes applied')
    native, ui = session.native_state(), session.dims()
    assert native['displayed'] == list(shown) and native['ndisplay'] == 3
    assert [a['hidden'] for a in ui['axes']] == [a in shown for a in range(5)]
    report = run_reference(studio.prepared, [
        {'name': 'reset', 'code': f'viewer.dims.current_step = {steps!r}'},
        {'name': '3d', 'code': 'viewer.dims.ndisplay = 3'},
        {'name': str(order), 'code': f'viewer.dims.order = {tuple(order)!r}'}])
    state = report['results'][3]['state']
    compare_dims(native, state)
    # Independent: the whole volume of the three displayed axes at the sliced steps.
    index = tuple(slice(None) if a in shown else steps[a] for a in range(5))
    kept = [a for a in range(5) if a in shown]
    expected = np.transpose(fx.values()[index], [kept.index(a) for a in shown])
    assert native['layers'][0]['slice_sha256'] == state['layers'][0]['slice_sha256'] == \
        hashlib.sha256(np.ascontiguousarray(expected).tobytes()).hexdigest(), order
    session.choose('Displayed dimensions', 2)
    assert session.apply().startswith('View changes applied')
    assert _set_order(studio, [0, 1, 2, 3, 4]).startswith('View changes applied')
    evidence(record_property, order=order, native=native, ui=ui, reference=state, napari=report['napari'])


# ---- keyboard: napari's dimension shortcuts and actions --------------------------------------------------------

def test_napari_dimension_shortcuts_step_and_focus_the_keyboard_axis(studio, record_property):
    from tests.scientific_studio_session import button
    session, page = studio.session, studio.page
    studio.reset((2, 1, 3, 5, 6))
    inspect_mode(studio, False)
    cell = session.python('viewer.dims.last_used = 0')
    assert 'error' not in cell
    page.click(button('Check current view'))
    session.settle()
    viewport = "document.querySelector('.scientific-viewport')"
    page.click(viewport)
    assert page.eval("document.activeElement === document.querySelector('.scientific-viewport') || "
                     "document.querySelector('.scientific-viewport').contains(document.activeElement)")
    keys = [('ArrowRight', (), 'increment_dims_right'), ('ArrowRight', (), 'increment_dims_right'),
            ('ArrowLeft', (), 'increment_dims_left'), ('ArrowUp', ('Alt',), 'focus_axes_up'),
            ('ArrowRight', (), 'increment_dims_right'), ('ArrowDown', ('Alt',), 'focus_axes_down'),
            ('ArrowDown', ('Alt',), 'focus_axes_down'), ('ArrowLeft', (), 'increment_dims_left')]
    buttons = [('Next slice', 'increment_dims_right'), ('Keyboard axis up', 'focus_axes_up'),
               ('Previous slice', 'increment_dims_left'), ('Keyboard axis down', 'focus_axes_down')]
    rows = []
    # Independent: last_used walks the sliced axes with more than one slice (0, 1, 2); steps clamp to the range.
    last, steps = 0, [2, 1, 3, 5, 6]
    def expect(action):
        nonlocal last
        sliders = [0, 1, 2]
        if action == 'increment_dims_right':
            steps[last] = min(steps[last] + 1, fx.SHAPE[last] - 1)
        elif action == 'increment_dims_left':
            steps[last] = max(steps[last] - 1, 0)
        elif action == 'focus_axes_up':
            last = sliders[(sliders.index(last) + 1) % 3]
        else:
            last = sliders[(sliders.index(last) - 1) % 3]
        return last, list(steps)
    for key, modifiers, action in keys:
        session.press(key=key, modifiers=modifiers, focus=viewport)
        native = session.native_state()
        rows.append({'input': f"{'+'.join(modifiers + (key,))}", 'action': action, 'expected': expect(action),
                     'native': native, 'keyboard': session.dims()['keyboard']})
    for label, action in buttons:
        session.press(label)
        native = session.native_state()
        rows.append({'input': label, 'action': action, 'expected': expect(action), 'native': native,
                     'keyboard': session.dims()['keyboard']})
    report = run_reference(studio.prepared, [
        {'name': 'reset', 'code': 'viewer.dims.current_step = (2, 1, 3, 5, 6)\nviewer.dims.last_used = 0'},
        *[{'name': row['input'], 'code': f"actions.{row['action']}(viewer)"} for row in rows]])
    for row, result in zip(rows, report['results'][2:], strict=True):
        last_used, steps_expected = row['expected']
        assert row['native']['last_used'] == last_used == result['state']['last_used']
        assert row['native']['current_step'] == steps_expected == result['state']['current_step'], (row['input'], row['native']['current_step'], steps_expected, result['state']['current_step'])
        compare_dims(row['native'], result['state'])
        assert row['keyboard'].startswith(f'Keyboard axis: {fx.AXES[last_used]} ({last_used})')
    evidence(record_property, rows=rows, reference=[r['state'] for r in report['results']])


# ---- axis labels and units ---------------------------------------------------------------------------------------

def test_axis_labels_and_units_are_napari_s_and_shown_on_every_axis(studio, record_property):
    session, page = studio.session, studio.page
    studio.reset((2, 1, 3, 5, 6))
    changes = [('dims-labels', "viewer.dims.axis_labels = ('time', 'channel', 'depth', 'row', 'column')",
                ['time', 'channel', 'depth', 'row', 'column'], list(fx.UNITS)),
               ('set-axis-label', "viewer.dims.set_axis_label(2, 'focus')",
                ['time', 'channel', 'focus', 'row', 'column'], list(fx.UNITS)),
               ('dims-units', "registry = viewer.dims.units[0]._REGISTRY  # Dims.units takes pint Units of napari's registry\nviewer.dims.units = tuple(registry.Unit(n) for n in ('minute', 'pixel', 'micrometer', 'micrometer', 'micrometer'))",
                ['time', 'channel', 'focus', 'row', 'column'], ['minute', 'pixel', 'micrometer', 'micrometer', 'micrometer']),
               ('layerlist-units', "viewer.layers.units = ('second', 'pixel', 'micrometer', 'micrometer', 'micrometer')",
                list(fx.AXES), None),
               # A layer change re-derives the viewer's axes from its layers: the layer's own (non-default) labels
               # replace the viewer's (ViewerModel._on_layers_change). napari's world units for mixed layer units
               # are its own conversion, so only the reference decides them (None: no independent value).
               ('layer-units', "viewer.layers[0].units = ('millisecond', 'pixel', 'nanometer', 'micrometer', 'micrometer')",
                list(fx.AXES), None),
               ('layer-labels', f"viewer.layers[0].axis_labels = {fx.AXES!r}\nviewer.layers[0].units = {fx.UNITS!r}",
                list(fx.AXES), list(fx.UNITS))]
    rows = []
    for name, code, labels, units in changes:
        cell = session.python(code)
        assert 'error' not in cell, cell
        page.click(Studio.button('Check current view'))
        session.settle()
        native, ui = session.native_state(), session.dims()
        playback = page.eval("[...document.querySelector('[aria-label=\"Playback axis\"]').options].map(o=>o.textContent)")
        rows.append({'change': name, 'native': native, 'ui': ui, 'playback': playback})
        # Independent: the labels and units set, shown on every axis row, coordinate and choice.
        assert native['axis_labels'] == labels, (name, native['axis_labels'])
        assert units is None or native['units'] == units, (name, native['units'])
        units = native['units']
        for axis in range(5):
            assert ui['axes'][axis]['label'] == f'{labels[axis]} ({axis}) · slice'
            assert ui['coordinates'][axis].startswith(f'{labels[axis]} ({axis}) · ')
            assert f'coordinate {native["point"][axis]:g} {units[axis]}' in ui['coordinates'][axis] or \
                f'coordinate {native["point"][axis]} {units[axis]}' in ui['coordinates'][axis], ui['coordinates'][axis]
        assert playback == [f'{labels[a]} ({a})' for a in (0, 1, 2)]
    report = run_reference(studio.prepared, [{'name': 'steps', 'code': 'viewer.dims.current_step = (2, 1, 3, 5, 6)'},
                                            *[{'name': name, 'code': code} for name, code, _, _ in changes]])
    for row, result in zip(rows, report['results'][2:], strict=True):
        compare_dims(row['native'], result['state'])
        assert row['native']['layerlist_units'] == result['state']['layerlist_units']
        assert row['native']['layerlist_axis_labels'] == result['state']['layerlist_axis_labels']
        assert row['native']['layers'][0]['axis_labels'] == result['state']['layers'][0]['axis_labels']
        assert row['native']['layers'][0]['units'] == result['state']['layers'][0]['units']
    evidence(record_property, rows=rows, reference=[r['state'] for r in report['results']])


# ---- the comparison catches a broken worker -------------------------------------------------------------------

def test_the_comparison_catches_a_worker_whose_napari_behaviour_was_broken(studio, record_property):
    """The harness itself: break napari inside the actual worker and the same checks must fail, then restore it."""
    session, page = studio.session, studio.page
    studio.reset((2, 1, 3, 5, 6))
    broken = ("import napari.components.dims as _d, napari.layers as _l\n"
              "_d._hooke_roll, _l.Image._hooke_get_value = _d.Dims.roll, _l.Image.get_value\n"
              "_d.Dims.roll = lambda self: None\n"
              "_l.Image.get_value = lambda self, *a, **k: (lambda v: None if v is None else v + 1)(_l.Image._hooke_get_value(self, *a, **k))")
    restore = ("import napari.components.dims as _d, napari.layers as _l\n"
               "_d.Dims.roll, _l.Image.get_value = _d._hooke_roll, _l.Image._hooke_get_value")
    cell = session.python(broken)
    assert 'error' not in cell, cell
    try:
        session.press('Roll axes')
        world = [fx.world(s, a) for a, s in enumerate((2, 1, 3, 5, 6))]
        native = session.native_state([[LAYER, world]])
        report = run_reference(studio.prepared, [{'name': 'steps', 'code': 'viewer.dims.current_step = (2, 1, 3, 5, 6)'},
                                                {'name': 'roll', 'code': 'actions.roll_axes(viewer)', 'probes': [[LAYER, world]]}])
        with pytest.raises(AssertionError):
            compare_dims(native, report['results'][2]['state'])
        assert native['order'] == [0, 1, 2, 3, 4] != fx.roll([0, 1, 2, 3, 4])
        assert native['probes'][0]['value'] == fx.value_at((2, 1, 3, 5, 6)) + 1 != report['results'][2]['state']['probes'][0]['value']
        evidence(record_property, broken=native, reference=report['results'][2]['state'])
    finally:
        cell = session.python(restore)
        assert 'error' not in cell, cell
    session.press('Roll axes')
    assert session.native_state()['order'] == fx.roll([0, 1, 2, 3, 4])


# ---- the Dims API through the session's Python panel ----------------------------------------------------------

def test_dims_rollable_range_steps_and_reset_through_python_are_napari_s(studio, record_property):
    session, page = studio.session, studio.page
    studio.reset((2, 1, 3, 5, 6))
    rows = []
    steps = [
        # Locking t for Roll: Roll then rotates only c and z among the sliced axes (and the displayed y, x).
        ('rollable', 'viewer.dims.rollable = (False, True, True, True, True)', 'ui'),
        ('roll-skips-locked', None, 'Roll axes'),
        ('unlock', 'viewer.dims.rollable = (True,) * 5\nviewer.dims.order = (0, 1, 2, 3, 4)', 'ui'),
        ('set-range', 'viewer.dims.set_range(2, (-3.0, 1.5, 1.5))', 'ui'),
        ('range', 'viewer.dims.range = viewer.dims.range[:2] + ((-3.0, 1.5, 0.75),) + viewer.dims.range[3:]', 'ui'),
        ('nsteps', 'viewer.dims.nsteps = (5, 3, 4, 11, 13)', 'ui'),
        ('reset', 'viewer.dims.reset()', 'ui'),
    ]
    for name, code, how in steps:
        if code is not None:
            try:
                cell = session.python(code)
                assert 'error' not in cell, (name, cell)
                page.click(Studio.button('Check current view'))
                session.settle()
            except browser_cdp.BrowserError as error:
                shown = page.eval("document.querySelector('.scientific-document')?.innerText?.slice(0,800)")
                raise AssertionError(f'{name}: {error}: {shown}') from error
        else:
            session.press(how)
        native, ui = session.native_state(), session.dims()
        rows.append({'step': name, 'native': native, 'ui': ui})
        # What the Studio shows is the worker's napari state on every axis.
        assert ui['order'] == native['order'] and len(ui['axes']) == native['ndim']
        for axis in range(native['ndim']):
            low, high, spacing = native['range'][axis]
            assert ui['axes'][axis]['max'] == native['nsteps'][axis] - 1, (name, axis, ui['axes'][axis], native['nsteps'])
            assert f'range {low:g}–{high:g}' in ui['coordinates'][axis] or f'range {low}–{high}' in ui['coordinates'][axis], \
                (name, ui['coordinates'][axis], native['range'][axis])
            assert ('locked for Roll' in ui['coordinates'][axis]) == (not native['rollable'][axis])
    # Independent: a locked t stays first while Roll rotates the other non-singleton axes; set_range and nsteps
    # give (stop - start) / step + 1 slices; reset gives napari's documented (0, 2, 1) ranges, origin points and order.
    assert rows[1]['native']['order'] == [0, 4, 1, 2, 3]
    assert rows[3]['native']['nsteps'][2] == 4 and rows[4]['native']['nsteps'][2] == 7
    assert rows[5]['native']['range'][2] == [-3.0, 1.5, 1.5]
    assert rows[6]['native']['range'] == [[0.0, 2.0, 1.0]] * 5 and rows[6]['native']['order'] == [0, 1, 2, 3, 4]
    assert rows[6]['native']['point'] == [0.0] * 5 and rows[6]['native']['rollable'] == [True] * 5
    report = run_reference(studio.prepared, [
        {'name': 'steps', 'code': 'viewer.dims.current_step = (2, 1, 3, 5, 6)'},
        *[{'name': name, 'code': code if code is not None else 'actions.roll_axes(viewer)'} for name, code, _ in steps]])
    for row, result in zip(rows, report['results'][2:], strict=True):
        compare_dims(row['native'], result['state'])
        assert row['native']['layers'][0]['slice_sha256'] == result['state']['layers'][0]['slice_sha256']
    evidence(record_property, rows=rows, reference=[r['state'] for r in report['results']])
    # Leave the view as the other tests expect it: a real layer change brings the layer's own ranges back.
    cell = session.python(f"viewer.layers[0].translate = (0,) * 5\n{CALIBRATE}")
    assert 'error' not in cell, cell
    restored = session.native_state()['range']
    assert all(same(restored[a], fx.world_range(a), 1e-9) for a in range(5)), restored


# ---- slab margins and projection --------------------------------------------------------------------------------

def test_slab_steps_and_thickness_through_python_are_napari_s(studio, record_property):
    session, page = studio.session, studio.page
    studio.reset((2, 1, 3, 5, 6))
    changes = [('margin-left-step', 'viewer.dims.margin_left_step = (0, 0, 2, 0, 0)', [0, 0, 1.5, 0, 0], [0] * 5),
               ('margin-right-step', 'viewer.dims.margin_right_step = (1, 0, 1, 0, 0)', [0, 0, 1.5, 0, 0], [2.5, 0, .75, 0, 0]),
               ('thickness', 'viewer.dims.thickness = (5.0, 0, 3.0, 0, 0)', [2.5, 0, 1.5, 0, 0], [2.5, 0, 1.5, 0, 0]),
               ('thickness-step', 'viewer.dims.thickness_step = (0, 0, 4, 0, 0)', [0, 0, 1.5, 0, 0], [0, 0, 1.5, 0, 0])]
    rows = []
    for name, code, left, right in changes:
        cell = session.python(code)
        assert 'error' not in cell, (name, cell)
        page.click(Studio.button('Check current view'))
        session.settle()
        native = session.native_state()
        shown = {side: [page.eval(f"Number(document.querySelector('[aria-label=\"Slice margin {side} {fx.AXES[a]} ({a})\"]').value)")
                        for a in range(5)] for side in ('before', 'after')}
        rows.append({'change': name, 'native': native, 'shown': shown})
        # Independent: steps are world margins / scale (t 2.5, z 0.75); thickness is split equally on both sides,
        # thickness_step is halved (whole steps) on both sides.
        assert same(native['margin_left'], left) and same(native['margin_right'], right), (name, native['margin_left'], native['margin_right'])
        assert same(shown['before'], left) and same(shown['after'], right), (name, shown)
        assert native['thickness'] == [a + b for a, b in zip(native['margin_left'], native['margin_right'])]
    report = run_reference(studio.prepared, [{'name': 'steps', 'code': 'viewer.dims.current_step = (2, 1, 3, 5, 6)'},
                                            *[{'name': name, 'code': code} for name, code, _, _ in changes]])
    for row, result in zip(rows, report['results'][2:], strict=True):
        compare_dims(row['native'], result['state'])
        for key in ('margin_left', 'margin_right', 'thickness', 'margin_left_step', 'margin_right_step'):
            assert row['native'][key] == result['state'][key], (row['change'], key)
    evidence(record_property, rows=rows, reference=[r['state'] for r in report['results']])
    cell = session.python('viewer.dims.margin_left = (0,) * 5\nviewer.dims.margin_right = (0,) * 5')
    assert 'error' not in cell, cell


@pytest.mark.parametrize('mode', ['none', 'sum', 'mean', 'max', 'min'])
def test_slab_margins_and_every_projection_mode_show_reduced_values(studio, record_property, mode):
    session, page = studio.session, studio.page
    steps = (2, 1, 3, 5, 6)
    studio.reset(steps)
    # Mean was reached from sum in the original journey. Keep a real mode change in this isolated case.
    starting_mode = 'sum' if mode == 'mean' else 'mean'
    if mode == 'mean':
        session.choose(f'Image slice projection {LAYER}', starting_mode)
        assert session.apply().startswith('View changes applied')
    session.set_number('Slice margin before z (2)', 0.75)
    session.set_number('Slice margin after z (2)', 1.5)
    assert session.apply().startswith('View changes applied')
    rows, ref_steps = [], [{'name': 'margins', 'code': 'viewer.dims.margin_left = (0, 0, 0.75, 0, 0)\n'
                                                       'viewer.dims.margin_right = (0, 0, 1.5, 0, 0)'}]
    session.choose(f'Image slice projection {LAYER}', mode)
    assert session.apply().startswith('View changes applied')
    inspect_mode(studio, True)
    session.tab('Inspect')
    sample = _inspect(studio, .5, .5)
    session.tab('View')
    world = [sample['coordinates'][fx.AXES[a]][0] for a in range(5)]
    native = session.native_state([[LAYER, world]])
    rows.append({'mode': mode, 'sample': sample, 'native': native})
    ref_steps.append({'name': mode, 'code': f"viewer.layers[0].projection_mode = {mode!r}", 'probes': [[LAYER, world]]})
    y, x = (round(sample['coordinates'][fx.AXES[a]][2]) for a in (3, 4))
    if mode == 'none':
        expected = fx.value_at([*steps[:3], y, x])
        plane = fx.plane(list(steps), [0, 1, 2, 3, 4]).astype('float64')
    else:
        plane = fx.slab(list(steps), 2, 1, 2, mode, [0, 1, 2, 3, 4])
        expected = plane[y, x]
    assert math.isclose(sample['value'], expected, rel_tol=1e-6), (mode, sample, expected)
    assert math.isclose(native['layers'][0]['slice_sum'], float(plane.sum()), rel_tol=1e-6), (mode, native['layers'][0])
    assert native['layers'][0]['projection_mode'] == mode
    assert native['margin_left'][2] == .75 and native['margin_right'][2] == 1.5
    report = run_reference(studio.prepared, [{'name': 'steps', 'code': f'viewer.dims.current_step = {steps!r}\n'
                                            f'viewer.layers[0].projection_mode = {starting_mode!r}'},
                                            *ref_steps])
    for row, result in zip(rows, report['results'][3:], strict=True):
        state = result['state']
        assert row['native']['layers'][0]['slice_sha256'] == state['layers'][0]['slice_sha256']
        assert row['native']['probes'][0]['value'] == state['probes'][0]['value']
        compare_dims(row['native'], state)
    evidence(record_property, rows=rows, reference=[r['state'] for r in report['results']])


# ---- playback -----------------------------------------------------------------------------------------------------

STATUS = "[...document.querySelectorAll('.scientific-navigation section [role=status]')].map(e=>e.textContent).join(' ')"
NAPARI_MODE = {'once': 'once', 'loop': 'loop', 'bounce': 'back_and_forth'}


def _play(studio, *, axis, mode, direction, first, last, rate, stop_after=None):
    """Set the playback controls, press Play, and return what was requested, drawn and shown, with timings."""
    from tests.scientific_studio_session import button
    session, page = studio.session, studio.page
    session.choose('Playback axis', axis)
    session.choose('Playback direction', direction)
    session.choose('Playback mode', mode)
    session.set_number('First slice', first)
    session.set_number('Last slice', last)
    session.set_number('Maximum slices per second', rate)
    page.drain()
    sent = len(page.requests('/request'))
    page.eval('''(()=>{
      if(window.__hookePaintListener)document.removeEventListener('scientific-frame-drawn',window.__hookePaintListener,true);
      window.__hookePaintReceipts=[];
      window.__hookePaintListener=e=>window.__hookePaintReceipts.push({frame:e.detail.frame,draw:e.detail.draw,at:performance.now()});
      document.addEventListener('scientific-frame-drawn',window.__hookePaintListener,true);
    })()''')
    steps = lambda: [r for r in page.requests('/request')[sent:] if _step(r, axis) is not None]
    page.wait(f"!({button('Play slices')}).disabled", 60, 'Play slices to be available')
    page.click(button('Play slices'))
    clicked, first_after, during = time.monotonic(), None, None
    while time.monotonic() - clicked < 60:
        page.drain()
        if first_after is None and steps():
            first_after = time.monotonic() - clicked
            # QtDims.is_playing as Hooke shows it: while playing, Stop is enabled and Play disabled.
            during = {'play_disabled': page.eval(f"({button('Play slices')}).disabled"),
                      'stop_enabled': page.eval(f"!({button('Stop playback')}).disabled")}
        if stop_after is not None and len(steps()) >= stop_after:
            page.click(button('Stop playback'))
            break
        if stop_after is None and page.eval(f"({button('Stop playback')}).disabled") and first_after is not None:
            break
        time.sleep(.01)
    page.wait(f"({button('Stop playback')}).disabled === true", 60, 'playback to end')
    time.sleep(1.0)
    page.drain()
    requested = steps()
    fetched = {r[1].rsplit('/', 1)[-1] for r in page.requests('/frames/')}
    receipts = page.eval('window.__hookePaintReceipts')
    drawn = []
    for request in requested:
        reply = page.response_json(request[3])
        frame = reply['frame']
        drawn.append({'step': _step(request, axis), 'frame': frame['frame'], 'shown_step': frame['dimensions']['steps'][axis],
                      'pixels_fetched': frame['frame'] in fetched,
                      'paint_receipts': [r for r in receipts if r['frame'] == frame['frame']]})
    after = {'play_disabled': page.eval(f"({button('Play slices')}).disabled"),
             'stop_enabled': page.eval(f"!({button('Stop playback')}).disabled")}
    return {'shown': [d['step'] for d in drawn], 'times': [r[0] for r in requested], 'first_after_s': first_after,
            'drawn': drawn, 'status': page.eval(STATUS), 'during': during, 'after': after}


def _step(request, axis):
    body = request[2] or {}
    change = (body.get('request') or {}).get('request') or {}
    if change.get('operation') != 'dimensions':
        return None
    return change['value']['steps'][axis]


def test_playback_defaults_match_napari(studio, record_property):
    # Captured immediately after the ordinary-UI open, before any case changes the controls.
    defaults = studio.initial['playback_defaults']
    assert defaults == {'rate': '10', 'mode': 'loop'}
    evidence(record_property, defaults=defaults)


@pytest.mark.parametrize('mode,direction,start_at,first,last,rate,stop_after,expected,at_once', [
    pytest.param('once', '1', 1, 1, 3, 10, None, [2, 3], False, id='once-forward'),
    pytest.param('once', '1', 3, 1, 3, 10, None, [1, 2, 3], True, id='once-restart'),
    pytest.param('once', '-1', 3, 1, 3, 10, None, [2, 1], False, id='once-backward'),
    pytest.param('loop', '1', 1, 1, 3, 10, 7, [2, 3, 1, 2, 3, 1, 2], True, id='loop-forward'),
    pytest.param('bounce', '1', 1, 1, 3, 10, 7, [2, 3, 2, 1, 2, 3, 2], True, id='bounce-forward'),
    pytest.param('loop', '-1', 3, 1, 3, 10, 5, [2, 1, 3, 2, 1], True, id='loop-backward'),
    # Sub-250ms first-frame distinctions need the quiet timing lane, not the parallel gate.
    pytest.param('loop', '1', 1, 1, 3, 2, 3, [2, 3, 1], True, id='loop-slow', marks=pytest.mark.timing),
    pytest.param('once', '1', 1, 1, 4, 2, None, [2, 3, 4], False, id='once-slow', marks=pytest.mark.timing),
    pytest.param('once', '1', 4, 1, 4, 2, None, [1, 2, 3, 4], True, id='once-restart-slow', marks=pytest.mark.timing),
])
def test_playback_sequence_and_draws_match_napari(
        studio, record_property, mode, direction, start_at, first, last, rate, stop_after, expected, at_once):
    """One bounded Studio/reference journey; expected sequences are independent AnimationThread rules."""
    session = studio.session
    studio.reset((start_at, 1, 3, 5, 6))
    played = _play(studio, axis=0, mode=mode, direction=direction, first=first, last=last, rate=rate,
                   stop_after=stop_after)
    native = session.native_state()
    played.update(case=[mode, direction, start_at, first, last, rate, stop_after], expected=expected,
                  native_step=native['current_step'][0])
    shown, drawn = played['shown'], played['drawn']
    assert shown[:len(expected)] == expected, played
    if stop_after is None:
        assert shown == expected and 'Reached the end' in played['status'], played
    else:
        assert len(shown) - stop_after <= 1, played  # the one request already sent when Stop was pressed
        assert 'Playback stopped' in played['status'], played
    # Nothing dropped and nothing claimed: every requested slice came back as a frame showing exactly that
    # slice, its pixels were fetched, and its actual canvas receipt was observed.
    assert all(d['shown_step'] == d['step'] and d['pixels_fetched'] and len(d['paint_receipts']) == 1 for d in drawn), drawn
    assert len({d['paint_receipts'][0]['draw'] for d in drawn}) == len(drawn), drawn
    said = re.search(r'Shown (\d+) slices?, every one drawn, at ([\d.]+) per second', played['status'])
    assert said and int(said.group(1)) == len(drawn), played['status']
    assert native['current_step'][0] == shown[-1]
    # is_playing: Play disabled and Stop enabled while playing; the reverse afterwards.
    assert played['during'] == {'play_disabled': True, 'stop_enabled': True}, played
    assert played['after'] == {'play_disabled': False, 'stop_enabled': False}, played
    # AnimationThread.work: the first slice at once (well inside one interval) or after one interval.
    interval = 1 / rate
    if rate == 2:
        assert (played['first_after_s'] < interval / 2) == at_once, played
        gaps = np.diff(played['times'])
        assert gaps.min() >= .9 * interval, played  # at most the requested rate, never faster
    report = run_reference(studio.prepared, [
        {'name': f'{mode} {direction} from {start_at} at {rate}', 'code': reference.PLAYBACK.format(
            axis=0, start=(start_at, 1, 3, 5, 6), first=first, last=last, fps=rate * int(direction),
            mode=NAPARI_MODE[mode], limit=(stop_after or len(expected)) + 2)}])
    napari = report['results'][1]['result']
    played['napari'] = napari
    assert napari['frames'][:len(expected)] == expected == shown[:len(expected)], played
    if stop_after is None:
        assert napari['frames'] == shown, played
    assert napari['was_playing'] and not napari['playing_after_stop']
    if rate == 2:
        assert (napari['after_play_s'][0] < 1 / rate / 2) == at_once, played
    evidence(record_property, rows=[played], napari=report['napari'])


def test_playback_refuses_invalid_ranges_and_excludes_displayed_axes(studio, record_property):
    from tests.scientific_studio_session import button
    session, page = studio.session, studio.page
    # Invalid range: nothing is requested and the reason is said. Displayed axes are never offered.
    studio.reset((1, 1, 3, 5, 6))
    session.choose('Playback axis', 0)
    session.set_number('First slice', 3)
    session.set_number('Last slice', 1)
    page.drain()
    sent = len(page.requests('/request'))
    page.eval('''(()=>{
      if(window.__hookePaintListener)document.removeEventListener('scientific-frame-drawn',window.__hookePaintListener,true);
      window.__hookePaintReceipts=[];
      window.__hookePaintListener=e=>window.__hookePaintReceipts.push({frame:e.detail.frame,draw:e.detail.draw,at:performance.now()});
      document.addEventListener('scientific-frame-drawn',window.__hookePaintListener,true);
    })()''')
    page.click(button('Play slices'))
    time.sleep(1)
    page.drain()
    refused = page.eval(STATUS)
    offered = page.eval("[...document.querySelector('[aria-label=\"Playback axis\"]').options].map(o=>Number(o.value))")
    moved = [r for r in page.requests('/request')[sent:] if _step(r, 0) is not None]  # no slice was requested
    assert not moved and 'Choose at least two slices' in refused, (moved, refused)
    assert offered == [0, 1, 2]
    evidence(record_property, invalid=refused, offered=offered)
