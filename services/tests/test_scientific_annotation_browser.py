"""P4 annotation (H07) in a real browser, in an isolated profile, against the actual review worker.

Each test operates the Studio as a person does (Project Data, the Edit section's Labels tools on the preview, Save,
the Python panel for the API) in a fresh Chromium profile served by a private orchestrator, and checks three things
against each other:

- what the Studio shows and the actual worker's native state (napari in Hooke's confined review worker);
- the same edit in a plain pinned-napari reference process (tests/scientific_annotation_reference.py), driven
  through napari's own mouse callbacks at the world positions napari's canvas_to_world gives for the very frame
  pixels the Studio sent (read from the browser's own network log);
- independent expectations from the fixture's definition, computed with numpy (tests/scientific_annotation_fixture.py).

Prepared runtimes are required and never installed here: NAPARI_ANNOTATION_PREPARATION (a prepared review integration
whose factory opens `instance-labels` inputs as editable Labels; its own Python is also the reference's),
HOOKE_BROWSER (a Chromium executable), NAPARI_ARCHIVE and NAPARI_REFERENCE_WHEEL (the approved local artifacts).
Missing runtimes skip; absent or incompatible reference evidence refuses. The acceptance runner reports their
children missing, never passed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time

import numpy as np
import pytest

from tests import browser_cdp, scientific_annotation_fixture as fx, scientific_annotation_reference as reference
from tests.scientific_studio_session import button, labelled
from tests.scientific_studio_stack import Stack

pytestmark = pytest.mark.xdist_group('hooke-studio-browser')
TITLE = 'H07 annotation fixture'
LAYER = 'Annotation labels'  # the prepared integration's name for its bound Labels input
PLANE_STEP = (*fx.PLANE, 0, 0)
LABELS_STATUS = ("(()=>{const t=document.querySelector('[aria-label=\"Label tool\"]');"
                 "return t?t.closest('label').parentElement.querySelector(':scope > p[role=status]')?.textContent||'':''})()")
SETUP = fx.CODE + f"""
base = _annotation_fixture()
layer = viewer.add_labels(base.copy(), name={LAYER!r}, scale={fx.SCALE!r}, translate={fx.TRANSLATE!r})
"""


def preparation():
    location = os.environ.get('NAPARI_ANNOTATION_PREPARATION')
    if not location or not Path(location).is_file():
        return None
    return json.loads(Path(location).read_text())


class Studio:
    def __init__(self, stack, browser, page, session, prepared):
        self.stack, self.browser, self.page, self.session, self.prepared = stack, browser, page, session, prepared
        self.observation = json.loads(self.run(reference.observation_source())['stdout'].strip().splitlines()[-1])

    @property
    def python(self):
        return reference.runtime_python(self.prepared)

    def run(self, code):
        cell = self.session.python(code)
        assert 'error' not in cell, cell  # stderr may carry napari's own deprecation warnings
        return cell

    def reference(self, setup, steps):
        return reference.run(self.python, setup, steps, hooke=self.observation)

    def state(self):
        """The actual worker's native state through the shared P4 snapshot, with the changes from the fixture."""
        code = (reference.SNAPSHOT + fx.CODE + "\nimport json as _json\nprint('HOOKE-STATE' + _json.dumps("
                f"_p4_snapshot(viewer, _annotation_fixture())))")
        text = self.run(code).get('stdout', '')
        assert 'HOOKE-STATE' in text, text
        return json.loads(text.split('HOOKE-STATE', 1)[1].strip().splitlines()[0])

    def labels(self, state):
        return next(row for row in state['layers'] if row['name'] == LAYER)

    def show_plane(self, step=PLANE_STEP):
        """Setup, not the behaviour under test: the edited plane in view, napari's own reset_view framing."""
        self.run(f"viewer.dims.current_step = {tuple(step)!r}\nviewer.reset_view()")
        self.page.click(button('Check current view'))
        self.session.settle()

    def edit_section(self):
        self.session.tab('Edit')
        self.page.wait(f"!!({labelled('Label tool', 'select')}) && !({labelled('Label tool', 'select')}).disabled", 60,
                       'the Labels tools to be available')

    def tool(self, name, *, label=None, brush=None, preserve=None, contiguous=None, dimensions=None):
        """Choose a Labels tool and its settings with the keyboard and mouse, as a person does."""
        page, session = self.page, self.session
        self.edit_section()
        session.choose('Label tool', name)
        if label is not None:
            session.set_number('Label value', label)
        if brush is not None:
            session.set_number('Brush size', brush)
        if dimensions is not None:
            session.choose('Edit dimensions', dimensions)
        for title, wanted in (('Preserve other labels', preserve), ('Fill connected region only', contiguous)):
            if wanted is not None and page.eval(f"({labelled(title, 'input')}).checked") != wanted:
                page.click(labelled(title, 'input'))
        page.key('Tab')  # leave the last field, so a change event has fired before the canvas is used
        # Each changed setting is sent to napari at once (one queued behind another while it applies); the panel says
        # it is no longer busy before the canvas is used.
        page.wait("document.querySelector('.scientific-label-editor')?.getAttribute('aria-busy') === 'false' && "
                  f"!({labelled('Label tool', 'select')}).disabled", 60, 'the Labels settings to apply')
        time.sleep(.2)

    def client(self, state, pixels):
        """Client coordinates of frame pixels (y, x) on the preview canvas."""
        rect = self.page.eval("(()=>{const c=document.querySelector('.scientific-viewport canvas');c.scrollIntoView({block:'center'});"
                              "const r=c.getBoundingClientRect();return [r.left,r.top,r.width,r.height,c.width,c.height]})()")
        left, top, width, height, frame_w, frame_h = rect
        assert [frame_h, frame_w] == state['camera']['canvas'], (rect, state['camera'])
        return [(left + x * width / frame_w, top + y * height / frame_h) for y, x in pixels]

    def pixels(self, state, indices):
        """Frame pixels (y, x) that napari maps to the given data indices on the displayed plane: the camera's own
        world = centre + (pixel - size / 2) / zoom, inverted (orientation down/right, napari's default)."""
        cam = state['camera']
        assert cam['orientation2d'] == ['down', 'right'], cam
        v, h = state['dims']['displayed']
        (cy, cx), zoom, (height, width) = cam['center'][-2:], cam['zoom'], cam['canvas']
        out = []
        for index in indices:
            world = fx.world(index)
            out.append([(world[v] - cy) * zoom + height / 2, (world[h] - cx) * zoom + width / 2])
        return out

    def stroke(self, state, indices, *, steps=6):
        """Press on the first index, drag through the others, release on the last; returns the labels request."""
        page = self.page
        points = self.client(state, self.pixels(state, indices))
        before = len(self.label_requests())
        page.mouse('mouseMoved', *points[0], button='none', clicks=0)
        page.mouse('mousePressed', *points[0])
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            for n in range(1, steps + 1):
                page.mouse('mouseMoved', x0 + (x1 - x0) * n / steps, y0 + (y1 - y0) * n / steps, clicks=0)
        page.mouse('mouseReleased', *points[-1])
        return self.sent(before)

    def click_canvas(self, state, index):
        page = self.page
        (x, y), = self.client(state, self.pixels(state, [index]))
        page.mouse('mouseMoved', x, y, button='none', clicks=0)
        page.mouse('mousePressed', x, y)
        page.mouse('mouseReleased', x, y)

    def history(self, which):
        """Press Undo label edit or Redo label edit; returns its request and outcome."""
        self.edit_section()
        before = len(self.label_requests())
        self.page.click(button(f'{which} label edit'))
        return self.sent(before)

    def undo(self):
        return self.history('Undo')

    def redo(self):
        return self.history('Redo')

    def sent(self, before):
        """The labels request this gesture sent, from the browser's own network log, and its applied status."""
        page = self.page
        page.wait(f"(()=>{{const s={LABELS_STATUS};return !!s&&s!=='Applying label edit…'&&!s.startsWith('Unsent')}})()", 60,
                  'the label edit outcome')
        page.drain()
        sent = self.label_requests()
        assert len(sent) > before, 'the gesture sent no labels request'
        return sent[-1], page.eval(LABELS_STATUS)

    def label_requests(self):
        """Every Labels command the page sent, in order (POST .../request with op 'labels')."""
        self.page.drain()
        return [body['request'] for _, url, body, _ in self.page.requests('/api/scientific-reviews/')
                if url.endswith('/request') and isinstance(body, dict) and isinstance(body.get('request'), dict)
                and body['request'].get('op') == 'labels']


@pytest.fixture
def studio():
    prepared, executable = preparation(), browser_cdp.executable()
    if prepared is None or executable is None:
        pytest.skip('needs NAPARI_ANNOTATION_PREPARATION and HOOKE_BROWSER; not run, so not evidence')
    from tests.scientific_studio_session import StudioSession
    import signal
    import threading

    def stopped(*_):  # a terminated test run still stops its private orchestrator, worker and browser
        raise KeyboardInterrupt('terminated')
    previous = signal.signal(signal.SIGTERM, stopped) if threading.current_thread() is threading.main_thread() else None
    stack = browser = page = None
    try:
        stack = Stack(prepared)
        stack.seed_scene(TITLE, {'labels': (fx.values(), fx.AXES, fx.scientific())})
        browser = browser_cdp.Browser(executable, width=1440, height=900)
        page = browser.page(stack.url + '/')
        page.record_requests()
        session = StudioSession(page)
        session.open_from_project_data(TITLE)
        yield Studio(stack, browser, page, session, prepared)
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



@pytest.fixture(autouse=True)
def idle_panels(request):
    """Setup, not the behaviour under test: each browser test starts with no unsent gesture and both editing panels
    idle. A gesture an earlier failure left open is cancelled as a person's focus loss cancels it (the mouse released,
    another tab, back); a panel still busy after that fails here at once, saying why, instead of stalling every test."""
    if 'studio' not in request.fixturenames:
        yield
        return
    studio = request.getfixturevalue('studio')
    page = studio.page
    statuses = f"[{LABELS_STATUS}, {ANNOTATION_STATUS}]"
    if any(str(text).startswith('Unsent') for text in page.eval(statuses)):
        page.mouse('mouseReleased', 1, 1)
        other = studio.browser.call('Target.createTarget', {'url': 'about:blank'})['targetId']
        studio.browser.call('Target.activateTarget', {'targetId': other})
        page.call('Page.bringToFront')
        studio.browser.call('Target.closeTarget', {'targetId': other})
    idle = ("[...document.querySelectorAll('.scientific-label-editor,.scientific-annotate')]"
            ".every(e=>e.getAttribute('aria-busy')!=='true')")
    try:
        page.wait(idle, 20, 'the editing panels to be idle')
    except Exception as error:
        raise AssertionError(f'An earlier test left an editing panel busy: {page.eval(statuses)}') from error
    yield


def evidence(record_property, **values):
    record_property('evidence', json.dumps(values, sort_keys=True, default=str)[:60000])


def reference_edit(studio, camera, step, code, before=''):
    """The same edit in plain pinned napari, at the worker's axis order, slice and camera; returns its snapshot."""
    report = studio.reference(SETUP, [
        {'name': 'view', 'code': before + reference.same_view(camera, step)},
        {'name': 'edit', 'code': code}])
    assert all(r['error'] is None for r in report['results']), report['results']
    pinned = report['pinned']
    assert pinned['differs'] == [], pinned
    return report['results'][-1]['state'], pinned


def test_annotation_worker_and_reference_use_approved_gui_runtimes(studio, record_property):
    report = studio.reference(SETUP, [{'name': 'open', 'code': 'pass'}])
    evidence(record_property, hooke=studio.observation, reference={key: report[key]
             for key in ('runtime', 'profile', 'pinned')})


# ---- opening and the tools ----------------------------------------------------------------------------------

def test_open_labels_resource_offers_napari_labels_with_their_integer_type(studio, record_property):
    """The saved uint64 labels open as a napari Labels layer at the fixture's calibration, every value intact."""
    state = studio.state()
    row = studio.labels(state)
    report = studio.reference(SETUP, [{'name': 'open', 'code': 'pass'}])
    ref = studio.labels(report['results'][0]['state'])
    evidence(record_property, native=row, reference=ref, napari=report['napari'])
    assert row['type'] == ref['type'] == 'labels' and row['dtype'] == ref['dtype'] == '<u8'
    assert row['shape'] == list(fx.SHAPE) and row['changed_count'] == 0, 'opened exactly the saved values'
    assert row['sha256'] == ref['sha256']
    assert row['scale'] == list(fx.SCALE) == ref['scale'] and row['translate'] == list(fx.TRANSLATE)
    for key in ('mode', 'selected_label', 'background', 'brush_size', 'n_edit_dimensions', 'preserve_labels',
                'contiguous', 'show_selected_label'):
        assert row[key] == ref[key], (key, row[key], ref[key])
    studio.edit_section()
    tools = studio.page.eval(f"[...({labelled('Label tool', 'select')}).options].map(o=>o.value)")
    assert {'paint', 'erase', 'fill', 'polygon', 'pick'} <= set(tools)
    studio.session.tab('View')


@pytest.mark.parametrize('brush', (1, 3, 7))
def test_paint_and_erase_strokes_with_several_brush_sizes_on_a_nonzero_slice(studio, brush, record_property):
    """A drag with Paint and then Erase, at brush sizes 1, 3 and 7, changes exactly what napari's own draw changes."""
    rows = []
    studio.show_plane()
    start = studio.state()
    stroke = [[*fx.PLANE, 8, 20], [*fx.PLANE, 11, 27], [*fx.PLANE, 16, 31]]
    studio.tool('paint', label='42', brush=brush, preserve=False, contiguous=True, dimensions=2)
    request, status = studio.stroke(start, stroke)
    after = studio.state()
    native = studio.labels(after)
    code = (f"layer.mode = 'paint'\nlayer.brush_size = {brush}\nlayer.n_edit_dimensions = 2\n"
            f"layer.preserve_labels = False\nlayer.selected_label = 42\n"
            f"gesture(layer, {reference.worlds(request['points'])})")
    ref, pinned = reference_edit(studio, start['camera'], PLANE_STEP, code)
    ref_row = studio.labels(ref)
    rows.append({'brush': brush, 'request': request, 'status': status, 'native': native['changed_count'],
                 'reference': ref_row['changed_count']})
    assert status.startswith('Label edit applied'), status
    assert native['changed'] == ref_row['changed'], 'the worker changed exactly what napari changes'
    # Independent: every change is on the edited plane, to label 42, and each pressed index itself changed.
    changed = {tuple(c[:4]) for c in native['changed']}
    assert native['changed'] and all(tuple(c[:2]) == fx.PLANE and c[5] == '42' for c in native['changed'])
    assert {tuple(i) for i in stroke} <= changed
    assert native['undo'] == start['layers'][0]['undo'] + 1, 'one undoable gesture'
    # Erase across the middle of the stroke and the box: napari's erase mode writes the background label there.
    painted = studio.state()
    studio.tool('erase', label='42', brush=brush, preserve=False, contiguous=True, dimensions=2)
    erase_request, status = studio.stroke(painted, [[*fx.PLANE, 6, 14], [*fx.PLANE, 12, 24]])
    assert status.startswith('Label edit applied'), status
    erased = studio.labels(studio.state())
    code = (f"layer.mode = 'paint'\nlayer.brush_size = {brush}\nlayer.n_edit_dimensions = 2\n"
            f"layer.preserve_labels = False\nlayer.selected_label = 42\n"
            f"gesture(layer, {reference.worlds(request['points'])})\n"
            f"layer.mode = 'erase'\ngesture(layer, {reference.worlds(erase_request['points'])})\n")
    ref_erased, _ = reference_edit(studio, start['camera'], PLANE_STEP, code)
    assert erased['changed'] == studio.labels(ref_erased)['changed'], 'erase changed exactly what napari\'s erase does'
    assert any(c[5] == '0' and c[4] == str(fx.BOX) for c in erased['changed']), 'the box was erased to background'
    rows[-1]['erased'] = erased['changed_count']
    studio.undo()
    studio.undo()
    assert studio.labels(studio.state())['changed_count'] == 0
    evidence(record_property, rows=rows, pinned=pinned)


def napari_settings(*, mode, label=None, brush=None, preserve=None, contiguous=None, dimensions=None):
    """Reference code: the same Labels settings set on napari's own layer before the gesture."""
    lines = [f"layer.mode = {mode!r}"]
    for name, value in (('brush_size', brush), ('n_edit_dimensions', dimensions), ('preserve_labels', preserve),
                        ('contiguous', contiguous)):
        if value is not None:
            lines.append(f"layer.{name} = {value!r}")
    if label is not None:
        lines.append(f"layer.selected_label = {int(label)}")
    return '\n'.join(lines) + '\n'


def compare_edit(studio, start, request, code, record=None, before=''):
    """The worker's native result and napari's own for the same gesture; returns (native row, reference row)."""
    native = studio.labels(studio.state())
    gesture = f"gesture(layer, {reference.worlds(request['points'])})\n" if request['points'] else ''
    ref, pinned = reference_edit(studio, start['camera'], start['dims']['current_step'], code + gesture, before)
    ref_row = studio.labels(ref)
    if record is not None:
        record.append({'request': request, 'native': native['changed_count'], 'reference': ref_row['changed_count'],
                       'pinned_same': pinned['same']})
    assert native['changed'] == ref_row['changed'], 'the worker changed exactly what napari changes'
    for key in ('selected_label', 'undo', 'redo', 'sha256'):
        assert native[key] == ref_row[key] or key in ('undo', 'redo'), (key, native[key], ref_row[key])
    return native, ref_row


FILL_CASES = [('fill', {'label': '11', 'brush': 1, 'contiguous': True, 'preserve': False}, [[*fx.PLANE, 3, 32]]),
             ('fill', {'label': '11', 'brush': 1, 'contiguous': False, 'preserve': False}, [[*fx.PLANE, 3, 32]]),
             ('paint', {'label': '13', 'brush': 3, 'contiguous': True, 'preserve': True}, [[*fx.PLANE, 8, 2], [*fx.PLANE, 8, 22]]),
             ('paint', {'label': '13', 'brush': 3, 'contiguous': True, 'preserve': False}, [[*fx.PLANE, 8, 2], [*fx.PLANE, 8, 22]])]


@pytest.mark.parametrize('tool,settings,indices', FILL_CASES, ids=('connected-fill', 'global-fill', 'preserve', 'overwrite'))
def test_fill_contiguous_on_and_off_and_paint_with_preservation_on_and_off(studio, tool, settings, indices, record_property):
    """Fill one blob of label 9 with contiguous on (only it changes) and off (both blobs change); paint across the box
    with preserve on (the box keeps its label) and off (it is painted over). Each equals napari's own."""
    rows, base = [], fx.values()[fx.PLANE]
    studio.show_plane()
    start = studio.state()
    studio.tool(tool, dimensions=2, **settings)
    if tool == 'fill':
        before = len(studio.label_requests())
        studio.click_canvas(start, indices[0])
        request, status = studio.sent(before)
    else:
        request, status = studio.stroke(start, indices)
    assert status.startswith('Label edit applied'), status
    native, _ = compare_edit(studio, start, request, napari_settings(
        mode=tool, label=settings['label'], brush=settings['brush'], preserve=settings['preserve'],
        contiguous=settings['contiguous'], dimensions=2), rows)
    changed = {tuple(c[2:4]) for c in native['changed']}
    if tool == 'fill':  # independent: the 4-connected component of label 9 (or every 9) becomes 11
        nine = base == fx.RING
        expected = fx.connected(nine, indices[0][2:]) if settings['contiguous'] else nine
        assert changed == {tuple(map(int, i)) for i in np.argwhere(expected)}
        assert all(c[4] == '9' and c[5] == '11' for c in native['changed'])
    elif settings['preserve']:  # independent: nothing that held another label changed
        assert all(c[4] == '0' for c in native['changed']) and changed
    else:
        assert any(c[4] == str(fx.BOX) for c in native['changed']), 'without preserve the box is painted over'
    studio.undo()
    assert studio.labels(studio.state())['changed_count'] == 0
    evidence(record_property, rows=rows)


def test_polygon_fills_the_displayed_plane_and_pick_reads_any_integer_id(studio, record_property):
    """A polygon clicked corner by corner and finished with Enter fills what napari's paint_polygon fills; Pick on the
    2**60 + 3 object returns that ID exactly (above 2**53, so never through a JavaScript number)."""
    studio.show_plane()
    start = studio.state()
    corners = [[*fx.PLANE, 3, 2], [*fx.PLANE, 3, 14], [*fx.PLANE, 14, 10], [*fx.PLANE, 12, 2]]
    studio.tool('polygon', label=str(fx.BIG + 4), preserve=False)
    before = len(studio.label_requests())
    for corner in corners:
        studio.click_canvas(start, corner)
    studio.page.key('Enter')
    request, status = studio.sent(before)
    assert status.startswith('Label edit applied'), status
    assert request['action'] == 'polygon' and request['label'] == str(fx.BIG + 4) and len(request['points']) == 4
    native = studio.labels(studio.state())
    # napari's own polygon tool: its canvas overlay takes each click (hover, then press) at the world position napari's
    # canvas_to_world gives for the same frame pixel, and Enter runs napari's complete_polygon action.
    code = (napari_settings(mode='polygon', label=fx.BIG + 4, preserve=False) +
            f"for _w in {reference.worlds(request['points'])}:\n    hover(layer, _w)\n    gesture(layer, [_w])\n"
            "from napari.layers.labels import _labels_key_bindings as _k\n_k.complete_polygon(layer)\n")
    ref, pinned = reference_edit(studio, start['camera'], start['dims']['current_step'], code)
    ref_row = studio.labels(ref)
    assert native['changed'] == ref_row['changed']
    # Independent: pixel centres inside the corners' polygon (even-odd), on the edited plane only, to the big ID.
    inside = fx.inside_polygon(fx.SHAPE[2:], [c[2:] for c in corners])
    changed = np.zeros(fx.SHAPE[2:], dtype=bool)
    for c in native['changed']:
        changed[c[2], c[3]] = True
    assert (fx.erode(inside) <= changed).all() and (changed <= fx.dilate(inside)).all(), 'within a pixel of the outline'
    assert all(tuple(c[:2]) == fx.PLANE and c[5] == str(fx.BIG + 4) for c in native['changed'])
    studio.undo()
    # Pick: the selected label becomes the clicked object's ID, exactly.
    studio.tool('pick')
    before = len(studio.label_requests())
    studio.click_canvas(start, [*fx.PLANE, 22, 22])
    request, status = studio.sent(before)
    shown = studio.page.eval(f"({labelled('Label value', 'input')}).value")
    native = studio.labels(studio.state())
    picked, _ = reference_edit(studio, start['camera'], start['dims']['current_step'],
                               "layer.mode = 'pick'\n" + f"gesture(layer, {reference.worlds(request['points'])})\n")
    evidence(record_property, polygon=ref_row['changed_count'], pick=request, shown=shown, pinned=pinned,
             picked=studio.labels(picked)['selected_label'])
    assert shown == str(fx.BIG) and status.startswith('Label action completed'), (shown, status)
    assert native['selected_label'] == studio.labels(picked)['selected_label'] == str(fx.BIG), 'napari\'s pick, exactly'


@pytest.mark.parametrize('direction', ['undo', 'redo'])
def test_undo_and_redo_each_gesture_in_order(studio, record_property, direction):
    """Each direction checks three ordered native steps; both fresh-session cases are mandatory.

    Redo first performs all three Undos and verifies the original values before its three Redos.
    Undo's individual snapshots are compared in the other mandatory case, without duplicating those reads here.
    """
    studio.show_plane()
    start = studio.state()
    strokes = [([[*fx.PLANE, 30, 3], [*fx.PLANE, 32, 9]], '21'), ([[*fx.PLANE, 31, 2], [*fx.PLANE, 33, 12]], '22'),
               ([[*fx.PLANE, 34, 4]], '23')]
    sent = []
    for indices, label in strokes:
        studio.tool('paint', label=label, brush=3, preserve=False, contiguous=True, dimensions=2)
        request, _ = studio.stroke(start, indices)
        sent.append(request)
    painted = studio.labels(studio.state())
    undone = []
    for _ in strokes:
        studio.undo()
        if direction == 'undo':
            undone.append(studio.labels(studio.state()))
    restored = undone[-1] if undone else studio.labels(studio.state())
    assert restored['changed_count'] == 0, 'all three Undos restore the original values'
    redone = []
    if direction == 'redo':
        for _ in strokes:
            studio.redo()
            redone.append(studio.labels(studio.state()))
    code = ''.join(napari_settings(mode='paint', label=label, brush=3, preserve=False, dimensions=2) +
                   f"gesture(layer, {reference.worlds(request['points'])})\n"
                   for (_, label), request in zip(strokes, sent))
    steps = [{'name': 'view', 'code': reference.same_view(start['camera'], start['dims']['current_step'])},
             {'name': 'gestures', 'code': code}] + [{'name': 'undo', 'code': 'layer.undo()'}] * 3 + \
            [{'name': 'redo', 'code': 'layer.redo()'}] * 3
    report = studio.reference(SETUP, steps)
    assert all(r['error'] is None for r in report['results']), report['results']
    ref = [studio.labels(r['state']) for r in report['results'][1:]]
    evidence(record_property, direction=direction, native=[r['sha256'] for r in undone + redone],
             reference=[r['sha256'] for r in ref], restored=restored['sha256'])
    assert ref[0]['sha256'] == painted['sha256']
    assert restored['sha256'] == ref[3]['sha256'] and restored['undo'] == ref[3]['undo']
    if direction == 'undo':
        assert [r['sha256'] for r in undone] == [r['sha256'] for r in ref[1:4]], 'each Undo equals napari\'s'
        assert [r['undo'] for r in undone] == [r['undo'] for r in ref[1:4]]
    else:
        assert [r['sha256'] for r in redone] == [r['sha256'] for r in ref[4:7]], 'each Redo equals napari\'s'
        assert redone[-1]['sha256'] == painted['sha256']
        assert [r['redo'] for r in redone] == [r['redo'] for r in ref[4:7]]
    # Each case owns a fresh worker/profile; no extra Undo actions are needed for cleanup.


def test_edit_dimensions_paint_through_hidden_slices_and_on_swapped_displayed_axes(studio, record_property):
    """Edit dimensions 3 paints into neighbouring z slices as napari's n_edit_dimensions does; with y and x swapped
    on screen the same data indices are reached; both equal napari's own."""
    rows = []
    studio.show_plane()
    start = studio.state()
    studio.tool('paint', label='31', brush=3, preserve=False, contiguous=True, dimensions=3)
    request, status = studio.stroke(start, [[*fx.PLANE, 30, 30], [*fx.PLANE, 31, 36]])
    assert status.startswith('Label edit applied'), status
    native, _ = compare_edit(studio, start, request, napari_settings(mode='paint', label='31', brush=3, preserve=False,
                                                                    dimensions=3), rows)
    z_reached = {c[1] for c in native['changed']}
    assert {c[0] for c in native['changed']} == {fx.PLANE[0]} and len(z_reached) > 1, 'reached hidden z slices'
    studio.undo()
    studio.run("viewer.dims.order = (0, 1, 3, 2)")  # setup: x vertical, y horizontal on screen
    studio.show_plane()
    swapped = studio.state()
    assert swapped['dims']['displayed'] == [3, 2]
    studio.tool('paint', label='32', brush=1, preserve=False, contiguous=True, dimensions=2)
    request, _ = studio.stroke(swapped, [[*fx.PLANE, 33, 8], [*fx.PLANE, 33, 14]])
    code = napari_settings(mode='paint', label='32', brush=1, preserve=False, dimensions=2)
    native, _ = compare_edit(studio, swapped, request, code, rows, before="viewer.dims.order = (0, 1, 3, 2)\n")
    assert {(c[2], c[3]) for c in native['changed']} >= {(33, 8), (33, 14)}, 'the same data indices, not swapped'
    studio.undo()
    studio.run("viewer.dims.order = (0, 1, 2, 3)")
    evidence(record_property, rows=rows)


# napari's default Labels keys (napari/utils/shortcuts.py), pressed on the focused preview, and the action each runs.
SHORTCUTS = [('1', 'activate_labels_erase_mode'), ('2', 'activate_labels_paint_mode'), ('3', 'activate_labels_polygon_mode'),
             ('4', 'activate_labels_fill_mode'), ('5', 'activate_labels_picker_mode'), ('6', 'activate_labels_pan_zoom_mode'),
             ('e', 'activate_labels_erase_mode'), ('p', 'activate_labels_paint_mode'), ('f', 'activate_labels_fill_mode'),
             ('l', 'activate_labels_picker_mode'), ('z', 'activate_labels_pan_zoom_mode'),
             ('m', 'new_label'), ('=', 'increase_label_id'), ('=', 'increase_label_id'), ('-', 'decrease_label_id'),
             ('x', 'swap_selected_and_background_labels'), ('x', 'swap_selected_and_background_labels'),
             (']', 'increase_brush_size'), (']', 'increase_brush_size'), ('[', 'decrease_brush_size'),
             ('b', 'toggle_preserve_labels'), ('b', 'toggle_preserve_labels'), ('b', 'toggle_preserve_labels')]
TOOL_KEYS = ('mode', 'selected_label', 'background', 'brush_size', 'n_edit_dimensions', 'preserve_labels', 'contiguous',
             'show_selected_label')


PUNCTUATION = {'[': ('BracketLeft', 219), ']': ('BracketRight', 221), '-': ('Minus', 189), '=': ('Equal', 187)}


def press(page, key):
    """A key as a keyboard sends it (punctuation with its real key code; the driver's generic path guesses it)."""
    if key not in PUNCTUATION:
        return page.key(key)
    code, virtual = PUNCTUATION[key]
    base = {'key': key, 'code': code, 'windowsVirtualKeyCode': virtual, 'modifiers': 0}
    page.call('Input.dispatchKeyEvent', {'type': 'keyDown', 'text': key, 'unmodifiedText': key, **base})
    page.call('Input.dispatchKeyEvent', {'type': 'keyUp', **base})


def focus_preview(studio):
    """Click the visible preview while Inspect is chosen: it takes keyboard focus, edits nothing."""
    studio.edit_section()
    studio.session.choose('Label tool', 'inspect')
    studio.page.wait("document.querySelector('.scientific-label-editor')?.getAttribute('aria-busy') === 'false'", 60, 'the tool change to apply')
    studio.page.click("document.querySelector('.scientific-viewport canvas')")
    studio.page.wait("document.activeElement===document.querySelector('.scientific-viewport canvas')", 10,
                     'the preview to take keyboard focus')


def shown_tool(studio):
    return studio.page.eval(f"""({{tool:({labelled('Label tool', 'select')}).value,label:({labelled('Label value', 'input')}).value,
        brush:({labelled('Brush size', 'input')})?.value ?? null,preserve:({labelled('Preserve other labels', 'input')}).checked}})""")


def napari_bindings(studio, actions):
    """napari's own default shortcuts for these actions (napari.utils.shortcuts.default_shortcuts), from the plain
    reference: the keys the Studio sends must be the keys napari binds."""
    code = ("from napari.utils.shortcuts import default_shortcuts as _d\n"
            f"result = {{a: [str(k) for k in _d.get('napari:' + a, [])] for a in {sorted(set(actions))!r}}}\n")
    report = studio.reference('pass', [{'name': 'bindings', 'code': code}])
    assert report['results'][0]['error'] is None, report['results'][0]
    return report['results'][0]['result']


def napari_key(key):
    """A pressed key as napari names its bindings (letters upper case; the platform modifier is Meta on macOS)."""
    parts = key.split('+')
    names = {'shift': 'Shift', 'mod': 'Meta'}
    return '+'.join([*(names[p] for p in parts[:-1]), parts[-1].upper() if len(parts[-1]) == 1 else parts[-1]])


def test_napari_labels_shortcuts_on_the_preview_run_napari_s_own_actions(studio, record_property):
    """Each of napari's default Labels keys, pressed on the focused preview, changes the worker's native Labels state
    exactly as napari's own action does in the plain reference, and the Labels fields show the result."""
    studio.show_plane()
    studio.tool('paint', label='5', brush=3, preserve=False, contiguous=True, dimensions=2)
    focus_preview(studio)
    native_rows, shown = [], []
    for key, _ in SHORTCUTS:
        press(studio.page, key)
        studio.page.wait("document.querySelector('.scientific-label-editor')?.getAttribute('aria-busy') === 'false'", 60, f'{key} to apply')
        studio.session.tab('View'); studio.session.settle(); studio.edit_section()  # the redrawn frame is settled
        shown.append(shown_tool(studio))
        native_rows.append(studio.labels(studio.state()))
        studio.edit_section()  # back to the preview by keyboard (Tab), so no click lands on it with an editing tool
        studio.page.focus("document.querySelector('.scientific-viewport canvas')")
    steps = [{'name': 'start', 'code': napari_settings(mode='pan_zoom', label='5', brush=3, preserve=False, contiguous=True,
                                                       dimensions=2)}]
    steps += [{'name': action, 'code': f"from napari.layers.labels import _labels_key_bindings as _k\n_k.{action}(layer)"}
              for _, action in SHORTCUTS]
    report = studio.reference(SETUP, steps)
    assert all(r['error'] is None for r in report['results']), report['results']
    reference_rows = [studio.labels(r['state']) for r in report['results'][1:]]
    evidence(record_property, keys=[k for k, _ in SHORTCUTS], native=[{k: r[k] for k in TOOL_KEYS} for r in native_rows],
             reference=[{k: r[k] for k in TOOL_KEYS} for r in reference_rows], shown=shown)
    for (key, action), native, ref, ui in zip(SHORTCUTS, native_rows, reference_rows, shown, strict=True):
        for name in TOOL_KEYS:
            assert native[name] == ref[name], (key, action, name, native[name], ref[name])
        assert native['changed_count'] == 0, 'a shortcut never changes label values'
        assert ui['label'] == native['selected_label']
        # Polygon has no brush width: its UI deliberately hides that control. Native state
        # still matches every reference field above; all other modes expose the exact width.
        if native['mode'] == 'polygon':
            assert ui['brush'] is None
        else:
            assert int(ui['brush']) == int(native['brush_size'])
        assert ui['preserve'] == native['preserve_labels']
        assert ui['tool'] == {'pan_zoom': 'inspect'}.get(native['mode'], native['mode'])
    bindings = napari_bindings(studio, [action for _, action in SHORTCUTS])
    for key, action in SHORTCUTS:
        assert napari_key(key) in bindings[action], (key, action, bindings[action])
    # Independent: new_label is the largest label plus one (2**60 + 4); the brush grew by 2 then shrank by 1.
    assert reference_rows[11]['selected_label'] == str(fx.BIG + 1)
    assert native_rows[19]['brush_size'] == 3 + 1


def test_labels_fields_set_napari_s_own_layer_settings(studio, record_property):
    """Typing a label (2**60 + 9), a brush size and the checkboxes sets napari's own Labels properties at once."""
    studio.show_plane()
    studio.tool('fill', label=str(fx.BIG + 6), brush=11, preserve=True, contiguous=False, dimensions=3)
    native = studio.labels(studio.state())
    ref = studio.labels(studio.reference(SETUP, [{'name': 'set', 'code': napari_settings(
        mode='fill', label=fx.BIG + 6, brush=11, preserve=True, contiguous=False, dimensions=3)}])['results'][0]['state'])
    evidence(record_property, native={k: native[k] for k in TOOL_KEYS}, reference={k: ref[k] for k in TOOL_KEYS})
    for name in TOOL_KEYS:
        assert native[name] == ref[name], (name, native[name], ref[name])
    assert native['selected_label'] == str(fx.BIG + 6), 'every digit of an ID above 2**53'
    studio.tool('paint', label='1', brush=3, preserve=False, contiguous=True, dimensions=2)


# ---- the Points and Shapes editing API, through the session's Python panel ----------------------------------------
API_START = "viewer.dims.ndisplay = 2\nviewer.dims.current_step = (1, 2, 0, 0)\n"


@pytest.fixture
def api_results(studio, recipe):
    """One recipe run in its own actual worker (the Studio's Python panel) and once in the
    plain pinned reference; the Python panel's printed JSON is the worker's own result."""
    from tests import scientific_annotation_api as api
    studio.show_plane()
    studio.run(API_START + api.PREAMBLE)
    code = ("import json as _json\n"
            "globals().pop('result', None)\n"
            "try:\n" + f"    exec({api.RECIPES[recipe][0]!r}, globals())\n"
            "    _out = {'result': globals().get('result'), 'error': None}\n"
            "except Exception as _e:\n"
            "    _out = {'result': None, 'error': f'{type(_e).__name__}: {_e}'}\n"
            "print('HOOKE-API' + _json.dumps(_out, default=str))\n")
    text = studio.run(code).get('stdout', '')
    assert 'HOOKE-API' in text, text[-2000:]
    native = json.loads(text.split('HOOKE-API', 1)[1].strip().splitlines()[0])
    report = studio.reference(SETUP + API_START + api.PREAMBLE,
                              [{'name': recipe, 'code': api.RECIPES[recipe][0]}])
    result = report['results'][0]
    return {'native': {recipe: native},
            'reference': {recipe: {'result': result['result'], 'error': result['error']}},
            'pinned': report['pinned'], 'napari': report['napari']}


def _recipe_ids():
    from tests import scientific_annotation_api as api
    return sorted(api.RECIPES)


@pytest.mark.parametrize('recipe', _recipe_ids())
def test_points_and_shapes_editing_api_in_the_worker_is_napari_s(api_results, recipe, record_property):
    """One napari Points or Shapes editing call (or napari's own mouse callback, or key-binding action) run in Hooke's
    worker through the Python panel gives exactly what plain pinned napari gives, and what its definition requires."""
    from tests import scientific_annotation_api as api
    native, ref = api_results['native'][recipe], api_results['reference'][recipe]
    evidence(record_property, recipe=recipe, native=native, reference=ref, napari=api_results['napari'])
    assert api_results['pinned']['differs'] == [], api_results['pinned']
    assert native['error'] is None and ref['error'] is None, (native['error'], ref['error'])
    assert native['result'] == ref['result'], 'the worker gives exactly what plain pinned napari gives'
    assert api.RECIPES[recipe][1](native['result']), 'and what the call is defined to give'


def test_the_comparison_catches_a_worker_whose_napari_editing_was_broken(studio, record_property):
    """The harness itself: break napari's Labels.paint and Points.add inside the actual worker; the same comparisons
    and independent checks must then fail, and pass again once napari is restored."""
    from tests import scientific_annotation_api as api
    broken = ("import napari.layers as _l\n"
              "_l.Labels._hooke_paint, _l.Points._hooke_add = _l.Labels.paint, _l.Points.add\n"
              "_l.Labels.paint = lambda self, coord, new_label, refresh=True: _l.Labels._hooke_paint(self, coord, new_label + 1, refresh)\n"
              "_l.Points.add = lambda self, coords: _l.Points._hooke_add(self, np.asarray(coords)[:-1])\n")
    restore = "import napari.layers as _l\n_l.Labels.paint, _l.Points.add = _l.Labels._hooke_paint, _l.Points._hooke_add\n"
    studio.show_plane()
    start = studio.state()
    stroke = [[*fx.PLANE, 30, 30], [*fx.PLANE, 32, 36]]
    studio.run(broken)
    try:
        studio.tool('paint', label='51', brush=3, preserve=False, contiguous=True, dimensions=2)
        request, _ = studio.stroke(start, stroke)
        native = studio.labels(studio.state())
        code = napari_settings(mode='paint', label='51', brush=3, preserve=False, dimensions=2)
        with pytest.raises(AssertionError):
            compare_edit(studio, start, request, code)
        assert all(c[5] == '52' for c in native['changed']), 'the broken worker painted 52, not the asked 51'
        recipe = 'point-edit.points-add'
        text = studio.run(api.PREAMBLE + api.RECIPES[recipe][0] + "\nimport json as _j\nprint('HOOKE-API' + _j.dumps(result))").get('stdout', '')
        broken_add = json.loads(text.split('HOOKE-API', 1)[1].strip().splitlines()[0])
        assert not api.RECIPES[recipe][1](broken_add), 'the independent check rejects the broken add'
        evidence(record_property, broken_paint=native['changed_count'], broken_add=broken_add)
    finally:
        studio.run(restore)
    studio.undo()
    studio.show_plane()
    start = studio.state()
    studio.tool('paint', label='51', brush=3, preserve=False, contiguous=True, dimensions=2)
    request, _ = studio.stroke(start, stroke)
    compare_edit(studio, start, request, napari_settings(mode='paint', label='51', brush=3, preserve=False, dimensions=2))
    studio.undo()


# ---- interrupted and stale editing --------------------------------------------------------------------------------

@pytest.mark.parametrize('interruption', ['focus', 'pointer-cancel', 'escape', 'disconnect'])
def test_an_unsent_gesture_is_cancelled_by_focus_loss_pointer_cancel_and_escape_and_a_disconnect_applies_nothing(
        studio, record_property, interruption):
    """Each fresh case starts a real gesture, proves cancellation/no replay and recovers through visible controls."""
    page = studio.page
    studio.show_plane()
    start = studio.state()
    sent_before = len(studio.label_requests())
    rows = {}
    studio.tool('polygon' if interruption == 'escape' else 'paint', label='63', brush=3 if interruption != 'escape' else None,
                preserve=False, contiguous=True if interruption != 'escape' else None, dimensions=2 if interruption != 'escape' else None)
    a, b = studio.client(start, studio.pixels(start, [[*fx.PLANE, 30, 30], [*fx.PLANE, 33, 38]]))
    if interruption == 'escape':
        for corner in ([*fx.PLANE, 20, 2], [*fx.PLANE, 20, 12], [*fx.PLANE, 30, 8]):
            studio.click_canvas(start, corner)
        assert page.eval("!document.querySelector('.scientific-viewport canvas + canvas').hidden"), 'the unsent outline shows'
        page.key('Escape')
        rows['escape'] = page.eval(LABELS_STATUS)
        assert page.eval("document.querySelector('.scientific-viewport canvas + canvas').hidden"), 'Escape removed the outline'
        assert page.eval("document.querySelector('.scientific-viewport canvas').checkVisibility()"), 'cancel keeps the editing workspace visible'
    elif interruption == 'pointer-cancel':
        page.call('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': a[0], 'y': a[1], 'id': 1}]})
        page.call('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': b[0], 'y': b[1], 'id': 1}]})
        assert page.eval(f"{LABELS_STATUS}.startsWith('Unsent stroke')"), 'a real touch stroke started'
        page.call('Input.dispatchTouchEvent', {'type': 'touchCancel', 'touchPoints': []})
        rows['touch'] = page.eval(LABELS_STATUS)
        assert 'cancelled' in rows['touch']
    else:
        if interruption == 'disconnect':
            page.call('Network.emulateNetworkConditions', {'offline': True, 'latency': 0, 'downloadThroughput': -1,
                                                           'uploadThroughput': -1})
        try:
            page.mouse('mouseMoved', *a, button='none', clicks=0)
            page.mouse('mousePressed', *a)
            page.mouse('mouseMoved', *b, clicks=0)
            assert page.eval(f"{LABELS_STATUS}.startsWith('Unsent stroke')"), 'a real stroke started'
            if interruption == 'focus':
                other = studio.browser.call('Target.createTarget', {'url': 'about:blank'})['targetId']
                try:
                    studio.browser.call('Target.activateTarget', {'targetId': other})
                    page.wait(f"{LABELS_STATUS}.includes('cancelled')", 20, 'the stroke to cancel when focus left')
                    rows['focus'] = page.eval(LABELS_STATUS)
                finally:
                    page.call('Page.bringToFront')
                    page.mouse('mouseReleased', *b)
                    studio.browser.call('Target.closeTarget', {'targetId': other})
            else:
                page.mouse('mouseReleased', *b)
                page.wait(f"{LABELS_STATUS}.includes('will not be sent again')", 20, 'the lost request to be reported')
                rows['offline'] = page.eval(LABELS_STATUS)
        finally:
            if interruption == 'disconnect':
                page.call('Network.emulateNetworkConditions', {'offline': False, 'latency': 0, 'downloadThroughput': -1,
                                                               'uploadThroughput': -1})
    if interruption == 'disconnect':
        page.click(button('Check edit outcome'))
        page.wait(f"!{LABELS_STATUS}.includes('will not be sent again')", 20, 'the checked outcome')
        rows['checked'] = page.eval(LABELS_STATUS)
        assert len([r for r in studio.label_requests() if r.get('label') == '63']) == 1, 'the lost stroke was never replayed'
    else:
        assert len(studio.label_requests()) == sent_before, 'nothing interrupted was sent'
    # Focus/visibility changes revoke the old paint receipt. Recover through the offered ordinary control.
    page.click(button('Refresh preview'))
    studio.edit_section()  # wait on the visible editor; the View tab's navigation controls are hidden here
    after = studio.labels(studio.state())
    evidence(record_property, interruption=interruption, rows=rows, after=after['changed_count'])
    assert after['changed_count'] == 0 and after['undo'] == start['layers'][0]['undo']
    if interruption == 'escape':
        reset, _ = reference_edit(studio, start['camera'], start['dims']['current_step'],
            napari_settings(mode='polygon', label=63, preserve=False) +
            "for _w in [list(layer.data_to_world(p)) for p in ([1, 2, 20, 2], [1, 2, 20, 12], [1, 2, 30, 8])]:\n"
            "    hover(layer, _w)\n    gesture(layer, [_w])\n"
            "from napari.layers.labels import _labels_key_bindings as _k\n_k.reset_polygon(layer)\n"
            "result = list(layer._overlays['polygon'].points)\n")
        assert studio.labels(reset)['changed_count'] == 0, "napari's reset_polygon also painted nothing"


# ---- Points and Shapes editing on the preview ---------------------------------------------------------------------
# The annotated layers are created in the Python panel (setup, not the behaviour under test) with napari's own test
# coordinates: data 0..100 shown at scale 0.1 and translate (5, 7), inside the fixture's view.
SHAPE_SCALE, SHAPE_TRANSLATE = (0.1, 0.1), (5.0, 7.0)
ANNOTATION_STATUS = ("(()=>{const m=document.querySelector('[aria-label=\"Annotation mode\"]');"
                     "return m?m.closest('label').parentElement.querySelector(':scope > p[role=status]')?.textContent||'':''})()")
MODS = {'Shift': 8, 'Alt': 1, 'Control': 2, 'Meta': 4}


def data_world(point):
    return [SHAPE_TRANSLATE[i] + point[i] * SHAPE_SCALE[i] for i in range(2)]


def world_data(world):
    return [(world[i] - SHAPE_TRANSLATE[i]) / SHAPE_SCALE[i] for i in range(2)]


class Annotator:
    """Points and Shapes editing through the Edit section's annotation panel and the preview, as a person does."""

    def __init__(self, studio):
        self.studio, self.page = studio, studio.page
        self.start = None
        self.keys = []  # (the key as napari names it, the napari actions it made the Studio send)

    def requests(self):
        self.page.drain()
        return [body['request']['request']['value'] for _, url, body, _ in self.page.requests('/api/scientific-reviews/')
                if url.endswith('/request') and isinstance(body, dict) and isinstance(body.get('request'), dict)
                and body['request'].get('op') == 'change'
                and isinstance(body['request'].get('request'), dict)
                and body['request']['request'].get('operation') == 'annotate']

    def begin(self, setup, layer):
        """Setup in the Python panel, then the edited plane in view; returns the state before any annotation."""
        studio = self.studio
        # A Labels tool left on by an earlier journey takes the preview's pointer (Points and Shapes stand back), so the
        # Labels layer is returned to napari's pan_zoom first.
        studio.run("for _l in [l for l in viewer.layers if l.name.startswith('P4 ')]:\n    viewer.layers.remove(_l)\n"
                   f"viewer.layers[{LAYER!r}].mode = 'pan_zoom'")
        studio.show_plane()  # the edited plane and napari's framing, then the layer (its own slice, if it sets one)
        # Every case owns a fresh console; its fixture arrays must not borrow imports from another journey.
        studio.run('import numpy as np\n' + setup)
        self.page.click(button('Check current view'))
        studio.session.settle()
        self.start = studio.state()
        self.layer, self.sent_before = layer, len(self.requests())
        studio.edit_section()
        value = self.page.eval(f"[...({labelled('Annotation layer', 'select')}).options].find(o=>o.textContent.startsWith({json.dumps(layer)}))?.value")
        assert value, f'{layer} is offered for annotation'
        studio.session.choose('Annotation layer', value)
        return self.start

    def wait(self):
        self.page.wait(f"(()=>{{const s={ANNOTATION_STATUS};return !s.startsWith('Unsent')&&!({labelled('Annotation mode', 'select')}).disabled"
                       "&&document.querySelector('.scientific-annotate')?.getAttribute('aria-busy')==='false'})()",
                       60, 'the annotation to apply')
        return self.page.eval(ANNOTATION_STATUS)

    def settled(self, before, timeout=20):
        """The operation's own annotate request was sent, and the panel is idle again."""
        deadline = time.monotonic() + timeout
        while len(self.requests()) <= before:
            assert time.monotonic() < deadline, 'the operation sent no annotate request'
            time.sleep(.05)
        return self.wait()

    def mode(self, value):
        self.studio.edit_section()
        if self.page.eval(f"({labelled('Annotation mode', 'select')}).value") == value:
            return self.page.eval(ANNOTATION_STATUS)
        before = len(self.requests())
        self.studio.session.choose('Annotation mode', value)
        return self.settled(before)

    def button(self, title):
        before = len(self.requests())
        self.page.click(button(title))
        return self.settled(before)

    def client(self, data):
        cam = self.start['camera']
        (cy, cx), zoom, (height, width) = cam['center'][-2:], cam['zoom'], cam['canvas']
        world = data_world(data)
        return self.studio.client(self.start, [[(world[0] - cy) * zoom + height / 2, (world[1] - cx) * zoom + width / 2]])[0]

    def key(self, name, modifiers=()):
        before = len(self.requests())
        self.page.focus("document.querySelector('.scientific-viewport canvas')")
        self.page.key(name, modifiers)
        status = self.settled(before)
        pressed = '+'.join([*({'Shift': 'shift', 'Meta': 'mod', 'Control': 'mod'}[m] for m in modifiers), name])
        self.keys.append((napari_key(pressed), [r['action'] for r in self.requests()[before:] if 'action' in r]))
        return status

    def shift(self, down):
        self.page.call('Input.dispatchKeyEvent', {'type': 'rawKeyDown' if down else 'keyUp', 'key': 'Shift',
                                                  'code': 'ShiftLeft', 'windowsVirtualKeyCode': 16,
                                                  'modifiers': MODS['Shift'] if down else 0})

    def drag(self, points, *, shift=False, shift_from=None, steps=4):
        """Press on the first data point, move through the others, release on the last. Shift is held throughout
        (`shift`) or pressed when the drag reaches point `shift_from`."""
        page, clients = self.page, [self.client(p) for p in points]
        held = shift
        if shift:
            self.shift(True)
        mods = lambda: MODS['Shift'] if held else 0
        page.mouse('mouseMoved', *clients[0], button='none', clicks=0, modifiers=mods())
        page.mouse('mousePressed', *clients[0], modifiers=mods())
        for index, ((x0, y0), (x1, y1)) in enumerate(zip(clients, clients[1:]), start=1):
            if shift_from == index - 1 and not held:
                self.shift(True); held = True
            for n in range(1, steps + 1):
                page.mouse('mouseMoved', x0 + (x1 - x0) * n / steps, y0 + (y1 - y0) * n / steps, clicks=0, modifiers=mods())
        before = len(self.requests())
        page.mouse('mouseReleased', *clients[-1], modifiers=mods())
        if held:
            self.shift(False)
        return self.settled(before)

    def click(self, point, *, count=1, modifiers=()):
        page, (x, y) = self.page, self.client(point)
        mask = sum(MODS[m] for m in modifiers)
        page.mouse('mouseMoved', x, y, button='none', clicks=0, modifiers=mask)
        before = len(self.requests())
        page.mouse('mousePressed', x, y, clicks=count, modifiers=mask)
        page.mouse('mouseReleased', x, y, clicks=count, modifiers=mask)
        return self.settled(before)

    def double_click(self, point):
        """Real consecutive mouse clicks, without waiting for the first scientific request."""
        page, (x, y) = self.page, self.client(point)
        page.mouse('mouseMoved', x, y, button='none', clicks=0)
        before = len(self.requests())
        for count in (1, 2):
            page.mouse('mousePressed', x, y, clicks=count)
            page.mouse('mouseReleased', x, y, clicks=count)
        return self.settled(before + 1)  # first click plus either native double-click or queued Finish

    def hover(self, points, steps=3):
        page, clients = self.page, [self.client(p) for p in points]
        for (x0, y0), (x1, y1) in zip(clients, clients[1:]):
            for n in range(1, steps + 1):
                page.mouse('mouseMoved', x0 + (x1 - x0) * n / steps, y0 + (y1 - y0) * n / steps, button='none', clicks=0)

    def finish(self, setup, *, before=''):
        """The worker's native layer and plain napari's after replaying every annotate request the Studio sent."""
        sent = self.requests()[self.sent_before:]
        native = next(row for row in self.studio.state()['layers'] if row['name'] == self.layer)
        report = self.studio.reference(SETUP + setup, [
            {'name': 'view', 'code': before + reference.same_view(self.start['camera'], self.start['dims']['current_step'])},
            {'name': 'replay', 'code': f"replay(viewer.layers[{self.layer!r}], {sent!r})"}])
        assert all(r['error'] is None for r in report['results']), report['results']
        ref = next(row for row in report['results'][-1]['state']['layers'] if row['name'] == self.layer)
        return native, ref, sent, report['pinned']

    def sent_data(self, sent):
        """Every pointer position the Studio sent, as data coordinates by the camera formula (independent of napari)."""
        cam = self.start['camera']
        (cy, cx), zoom, (height, width) = cam['center'][-2:], cam['zoom'], cam['canvas']
        out = []
        for request in sent:
            for event in request.get('events', []):
                if event['kind'] != 'key':
                    y, x = event['point']
                    out.append((event['kind'], world_data([cy + (y - height / 2) / zoom, cx + (x - width / 2) / zoom])))
        return out


def presses(sent):
    """The data positions of the presses (a double click's second press is napari's double-click event)."""
    return [point for kind, point in sent if kind in ('press', 'double')]


def points_of(sent):
    return [point for _, point in sent]


SHAPES_F = ("layer = viewer.add_shapes([np.array([[20.0, 30.0], [20.0, 60.0], [40.0, 60.0], [40.0, 30.0]]), "
            "np.array([[60.0, 20.0], [60.0, 50.0], [80.0, 35.0]])], shape_type=['rectangle', 'polygon'], "
            f"name='P4 shapes', scale={SHAPE_SCALE!r}, translate={SHAPE_TRANSLATE!r})\n")
F = [[[20.0, 30.0], [20.0, 60.0], [40.0, 60.0], [40.0, 30.0]], [[60.0, 20.0], [60.0, 50.0], [80.0, 35.0]]]
V = [[50.0, 10.0], [50.0, 40.0], [70.0, 25.0]]


def near(a, b, tol=1e-3):
    return len(a) == len(b) and all(abs(x - y) <= tol for p, q in zip(a, b) for x, y in zip(p, q))


def box_of(p, q):
    return sorted([(min(p[0], q[0]), min(p[1], q[1])), (min(p[0], q[0]), max(p[1], q[1])),
                   (max(p[0], q[0]), max(p[1], q[1])), (max(p[0], q[0]), min(p[1], q[1]))])


def rounded(shape, digits=3):
    return sorted(tuple(round(v, digits) for v in p) for p in shape)


def extent(shape):
    ys, xs = [p[0] for p in shape], [p[1] for p in shape]
    return max(ys) - min(ys), max(xs) - min(xs)


DIRECTIONS = {'top': (-1, 0), 'bottom': (1, 0), 'left': (0, -1), 'right': (0, 1), 'top-left': (-1, -1),
              'top-right': (-1, 1), 'bottom-left': (1, -1), 'bottom-right': (1, 1)}


def _create(mode, ops, check):
    return {'setup': SHAPES_F, 'layer': 'P4 shapes', 'mode': mode, 'ops': ops, 'check': check}


def _unchanged_f(native):
    return [[list(map(float, p)) for p in s] for s in native['data'][:2]] == F


CREATE = {
    'rectangle': _create('add_rectangle', [('drag', [[20, 30], [30, 45], [40, 60]])],
        lambda n, d: n['shape_type'][-1] == 'rectangle' and rounded(n['data'][-1]) == rounded(box_of(presses(d)[0], points_of(d)[-1]))
        and n['selected'] == [2] and _unchanged_f(n)),
    'ellipse': _create('add_ellipse', [('drag', [[20, 30], [30, 45], [40, 60]])],
        lambda n, d: n['shape_type'][-1] == 'ellipse' and rounded(n['data'][-1]) == rounded(box_of(presses(d)[0], points_of(d)[-1]))
        and len(n['data'][-1]) == 4 and _unchanged_f(n)),
    'line': _create('add_line', [('drag', [[20, 30], [30, 45], [40, 60]])],
        lambda n, d: n['shape_type'][-1] == 'line' and near(n['data'][-1], [presses(d)[0], points_of(d)[-1]]) and _unchanged_f(n)),
    'polygon': _create('add_polygon', [('click', p) for p in V] + [('key', 'Enter')],
        lambda n, d: n['shape_type'][-1] == 'polygon' and near(n['data'][-1], presses(d)[:3]) and _unchanged_f(n)),
    'polyline': _create('add_polyline', [('click', V[0]), ('click', V[1]), ('click', V[2]), ('double', V[2])],
        lambda n, d: n['shape_type'][-1] == 'path' and near(n['data'][-1], presses(d)[:3]) and _unchanged_f(n)),
    'polyline-fast-double': _create('add_polyline', [('click', V[0]), ('click', V[1]), ('double-click', V[2])],
        lambda n, d: n['shape_type'][-1] == 'path' and near(n['data'][-1], presses(d)[:3]) and _unchanged_f(n)),
    'path-drag': _create('add_path', [('drag', [[50, 10], [55, 20], [60, 30], [65, 45]])],
        lambda n, d: n['shape_type'][-1] == 'path' and near(n['data'][-1][:1], presses(d)[:1]) and len(n['data'][-1]) >= 3
        and all(any(near([v], [p]) for p in points_of(d)) for v in n['data'][-1]) and _unchanged_f(n)),
    'path-click-move': _create('add_path', [('click', [50, 10]), ('hover', [[50, 10], [55, 20], [60, 30], [65, 45]]),
                                            ('click', [65, 45])],
        lambda n, d: n['shape_type'][-1] == 'path' and near(n['data'][-1][:1], presses(d)[:1]) and _unchanged_f(n)),
    'lasso-drag': _create('add_polygon_lasso', [('drag', [[50, 10], [50, 40], [75, 40], [75, 10], [52, 12]])],
        lambda n, d: n['shape_type'][-1] == 'polygon' and len(n['data'][-1]) >= 3
        and all(any(near([v], [p]) for p in points_of(d)) for v in n['data'][-1]) and _unchanged_f(n)),
    'lasso-click-move': _create('add_polygon_lasso', [('click', [50, 10]), ('hover', [[50, 10], [50, 40], [75, 40], [75, 10]]),
                                                     ('click', [52, 12])],
        lambda n, d: n['shape_type'][-1] == 'polygon' and len(n['data'][-1]) >= 3 and _unchanged_f(n)),
    'line-angle-lock': _create('add_line', [('drag', [[20, 30], [30, 41], [40, 60]], {'shift': True})],
        lambda n, d: n['shape_type'][-1] == 'line' and (lambda dy, dx: abs(abs(dy) - abs(dx)) < 1e-3 or abs(dy) < 1e-3 or abs(dx) < 1e-3)(
            n['data'][-1][1][0] - n['data'][-1][0][0], n['data'][-1][1][1] - n['data'][-1][0][1])),
    'finish-explicit': _create('add_polygon', [('click', p) for p in V] + [('button', 'Finish shape')],
        lambda n, d: n['shape_type'][-1] == 'polygon' and len(n['data']) == 3 and near(n['data'][-1], presses(d)[:3])),
    'finish-shortcut': _create('add_polygon', [('click', p) for p in V] + [('key', 'Escape')],
        lambda n, d: n['shape_type'][-1] == 'polygon' and len(n['data']) == 3 and near(n['data'][-1], presses(d)[:3])),
    'polygon-finish-on-mode-change': _create('add_polygon', [('click', p) for p in V] + [('hover', [V[2], [70, 70]]), ('mode', 'select')],
        lambda n, d: n['shape_type'][-1] == 'polygon' and near(n['data'][-1], presses(d)[:3])),
    'polyline-finish-on-mode-change': _create('add_polyline', [('click', p) for p in V] + [('hover', [V[2], [70, 70]]), ('mode', 'select')],
        lambda n, d: n['shape_type'][-1] == 'path' and near(n['data'][-1], presses(d)[:3])),
    'polygon-invalid-finish': _create('add_polygon', [('click', [20, 30]), ('click', [10, 50]), ('click', [10, 50]), ('key', 'Enter')],
        lambda n, d: len(n['data']) == 2 and _unchanged_f(n)),
    'polyline-invalid-finish': _create('add_polyline', [('click', [20, 30]), ('click', [20, 30]), ('key', 'Enter')],
        lambda n, d: len(n['data']) == 2 and _unchanged_f(n) and n['selected'] == []),
    'delete-during-creation': _create('add_polygon', [('click', V[0]), ('click', V[1]), ('key', 'Delete')],
        lambda n, d: _unchanged_f(n)),
    **{f'rectangle-locked-drag-{name}': _create('add_rectangle', [('drag', [[50, 50], [50 + 8 * dy, 50 + 8 * dx], [50 + 20 * dy, 50 + 20 * dx]], {'shift': True})],
        lambda n, d: n['shape_type'][-1] == 'rectangle' and abs(extent(n['data'][-1])[0] - extent(n['data'][-1])[1]) < 1e-3)
       for name, (dy, dx) in DIRECTIONS.items()},
    **{f'ellipse-locked-drag-{name}': _create('add_ellipse', [('drag', [[50, 50], [50 + 8 * dy, 50 + 8 * dx], [50 + 20 * dy, 50 + 20 * dx]], {'shift': True})],
        lambda n, d: n['shape_type'][-1] == 'ellipse' and abs(extent(n['data'][-1])[0] - extent(n['data'][-1])[1]) < 1e-3)
       for name, (dy, dx) in DIRECTIONS.items()},
    'rectangle-mid-drag-lock': _create('add_rectangle', [('drag', [[10, 10], [25, 18], [40, 25], [40, 60]], {'shift_from': 2})],
        lambda n, d: n['shape_type'][-1] == 'rectangle'),
    'ellipse-mid-drag-lock': _create('add_ellipse', [('drag', [[10, 10], [25, 18], [40, 25], [40, 60]], {'shift_from': 2})],
        lambda n, d: n['shape_type'][-1] == 'ellipse'),
}


def operate(annotator, ops):
    reads = []
    for op in ops:
        kind, arg, extra = op[0], (op[1] if len(op) > 1 else None), (op[2] if len(op) > 2 else {})
        if kind in ('read', 'read-mode', 'read-z'):  # the native selection, mode or z-order now (the Python panel)
            row = next(r for r in annotator.studio.state()['layers'] if r['name'] == annotator.layer)
            reads.append(row['selected'] if kind == 'read' else row['mode'] if kind == 'read-mode' else row['z_index'])
            annotator.studio.edit_section()
            continue
        if kind == 'handle':  # drag one of napari's interaction-box handles of the current selection to `extra`
            text = annotator.studio.run(f"import json as _j\nprint('HB' + _j.dumps([float(v) for v in viewer.layers[{annotator.layer!r}]._selected_box[{arg}]]))").get('stdout', '')
            annotator.studio.edit_section()
            start = json.loads(text.split('HB', 1)[1].strip().splitlines()[0])
            target = extra['to'](start)
            annotator.drag([start, [(a + b) / 2 for a, b in zip(start, target)], target], shift=extra.get('shift', False))
            continue
        if kind == 'drag':
            annotator.drag(arg, **extra)
        elif kind == 'click':
            annotator.click(arg)
        elif kind == 'double-click':
            annotator.double_click(arg)
        elif kind == 'double':
            annotator.click(arg, count=2)
        elif kind == 'hover':
            annotator.hover(arg)
        elif kind == 'key':
            annotator.key(arg, extra.get('modifiers', ()))
        elif kind == 'button':
            annotator.button(arg)
        elif kind == 'mode':
            annotator.mode(arg)
        elif kind == 'shift-click':
            annotator.click(arg, modifiers=('Shift',))
    return reads


def run_annotation(studio, case, record_property):
    annotator = Annotator(studio)
    annotator.begin(case['setup'], case['layer'])
    annotator.mode(case['mode'])
    reads = operate(annotator, case['ops'])
    native, ref, sent, pinned = annotator.finish(case['setup'])
    native = {**native, 'reads': reads}
    evidence(record_property, native=native, reference=ref, sent=sent, pinned=pinned, keys=annotator.keys)
    assert pinned['differs'] == [], pinned
    if annotator.keys:  # every key pressed ran an action napari itself binds to that key
        bindings = napari_bindings(studio, [a for _, actions in annotator.keys for a in actions])
        for key, actions in annotator.keys:
            assert actions and all(key in bindings[a] for a in actions), (key, actions, bindings)
    for key in ('data', 'shape_type', 'selected', 'z_index', 'mode', 'size', 'symbol'):
        if key in ref:
            assert native[key] == ref[key], (key, native[key], ref[key])
    return native, annotator.sent_data(sent)


@pytest.mark.parametrize('procedure', sorted(CREATE))
def test_shapes_are_created_on_the_preview_exactly_as_napari_creates_them(studio, procedure, record_property):
    """One Shapes creation procedure, operated with the annotation panel and real pointer and keyboard input on the
    preview: the worker's shapes equal plain napari replaying the same input through its own canvas, and the
    procedure's independent geometry (from the pointer positions sent)."""
    case = CREATE[procedure]
    native, sent = run_annotation(studio, case, record_property)
    assert case['check'](native, sent), (native['data'][-1:], sent)


def _edit(mode, ops, check, setup=SHAPES_F, layer='P4 shapes'):
    return {'setup': setup, 'layer': layer, 'mode': mode, 'ops': ops, 'check': check}


def _shapes(rows):
    shapes = ', '.join(f"np.array({data!r})" for data, _ in rows)
    types = [kind for _, kind in rows]
    return (f"layer = viewer.add_shapes([{shapes}], shape_type={types!r}, name='P4 shapes', scale={SHAPE_SCALE!r}, "
            f"translate={SHAPE_TRANSLATE!r})\n")


def _moved(a, b, delta, tol=1e-3):
    return near([[p[0] + delta[0], p[1] + delta[1]] for p in a], b, tol)


LINE_SETUP = _shapes([([[30.0, 10.0], [30.0, 50.0]], 'line')])
PATH_SETUP = _shapes([([[30.0, 10.0], [40.0, 30.0], [30.0, 50.0]], 'path')])
TRIANGLE_SETUP = _shapes([([[40.0, 10.0], [40.0, 40.0], [70.0, 10.0]], 'polygon')])
STACK_SETUP = ("layer = viewer.add_shapes([np.array([[20.0, 20.0], [20.0, 50.0], [50.0, 50.0], [50.0, 20.0]]), "
               "np.array([[30.0, 30.0], [30.0, 60.0], [60.0, 60.0], [60.0, 30.0]]), "
               "np.array([[40.0, 40.0], [40.0, 70.0], [70.0, 70.0], [70.0, 40.0]])], shape_type='rectangle', "
               f"z_index=[2, 7, 4], name='P4 shapes', scale={SHAPE_SCALE!r}, translate={SHAPE_TRANSLATE!r})\n")
FEATURES_SETUP = ("layer = viewer.add_shapes([np.array([[20.0, 30.0], [20.0, 60.0], [40.0, 60.0], [40.0, 30.0]]), "
                  "np.array([[60.0, 20.0], [60.0, 50.0], [80.0, 35.0]])], shape_type=['rectangle', 'polygon'], "
                  "features={'kind': ['nucleus', 'cell'], 'area': [600.0, 450.0]}, text={'string': '{kind}'}, "
                  "edge_color=['red', 'blue'], face_color=['yellow', 'green'], name='P4 shapes', "
                  f"scale={SHAPE_SCALE!r}, translate={SHAPE_TRANSLATE!r})\n")
SLICED_SETUP = ("layer = viewer.add_shapes([np.array([[0.0, 20.0, 10.0], [0.0, 20.0, 50.0]]), "
                "np.array([[1.0, 40.0, 10.0], [1.0, 40.0, 50.0]]), np.array([[0.0, 60.0, 10.0], [0.0, 60.0, 50.0]])], "
                f"shape_type='line', name='P4 shapes', scale=(1.5, *{SHAPE_SCALE!r}), translate=(-3.0, *{SHAPE_TRANSLATE!r}))\n"
                "viewer.dims.set_point(1, -3.0)\n")
ELLIPSE_SETUP = _shapes([([[20.0, 30.0], [20.0, 60.0], [40.0, 60.0], [40.0, 30.0]], 'ellipse')])

EDIT = {
    'select-click': _edit('select', [('click', [20, 30])],
        lambda n, d: n['selected'] == [0] and _unchanged_f(n)),
    'shift-toggle': _edit('select', [('shift-click', [30, 45]), ('read',), ('shift-click', [70, 35]), ('read',),
                                     ('shift-click', [30, 45]), ('read',), ('shift-click', [70, 35]), ('read',)],
        lambda n, d: n['reads'] == [[0], [0, 1], [1], []] and _unchanged_f(n)),
    'marquee-containing-select': _edit('select', [('drag', [[5, 5], [6, 6], [50, 40], [95, 75]])],
        lambda n, d: n['selected'] == [0, 1] and _unchanged_f(n)),
    'marquee-partial-select': _edit('select', [('drag', [[5, 5], [6, 6], [20, 40], [30, 75]])],
        lambda n, d: set(n['selected']) <= {0} and _unchanged_f(n)),
    'marquee-empty-select': _edit('select', [('click', [30, 45]), ('drag', [[85, 80], [86, 81], [90, 85], [95, 95]])],
        lambda n, d: n['selected'] == [] and _unchanged_f(n)),
    'marquee-containing-direct': _edit('direct', [('drag', [[5, 5], [6, 6], [50, 40], [95, 75]])],
        lambda n, d: n['selected'] == [0, 1] and _unchanged_f(n)),
    'marquee-partial-direct': _edit('direct', [('drag', [[5, 5], [6, 6], [20, 40], [30, 75]])],
        lambda n, d: set(n['selected']) <= {0} and _unchanged_f(n)),
    'marquee-empty-direct': _edit('direct', [('click', [30, 45]), ('drag', [[85, 80], [86, 81], [90, 85], [95, 95]])],
        lambda n, d: n['selected'] == [] and _unchanged_f(n)),
    'translate': _edit('select', [('drag', [[30, 45], [35, 47], [40, 50]])],
        lambda n, d: _moved(F[0], n['data'][0], [points_of(d)[-1][i] - presses(d)[0][i] for i in range(2)])
        and n['data'][1] == F[1]),
    'rotate': _edit('select', [('click', [70, 35]), ('handle', 9, {'to': lambda h: [2 * 70 - h[0], h[1]]})],
        lambda n, d: n['shape_type'] == ['rectangle', 'polygon'] and n['data'][0] == F[0]
        and near([[sum(p[0] for p in n['data'][1]) / 3, sum(p[1] for p in n['data'][1]) / 3]],
                 [[sum(p[0] for p in F[1]) / 3, sum(p[1] for p in F[1]) / 3]], 0.5)),
    'rotate-shift-snap': _edit('select', [('click', [70, 35]), ('handle', 9, {'to': lambda h: [h[0] + 3, h[1] + 25], 'shift': True})],
        lambda n, d: n['data'][0] == F[0] and len(n['data'][1]) == 3),
    'resize-corner': _edit('select', [('click', [30, 45]), ('handle', 4, {'to': lambda h: [h[0] + 10, h[1] + 10]})],
        lambda n, d: near([min(n['data'][0])], [[20.0, 30.0]], 1e-3) and extent(n['data'][0])[0] > 20 and extent(n['data'][0])[1] > 30),
    'resize-edge': _edit('select', [('click', [30, 45]), ('handle', 5, {'to': lambda h: [h[0], h[1] + 10]})],
        lambda n, d: abs(extent(n['data'][0])[0] - 20) < 1e-3 and extent(n['data'][0])[1] > 30),
    'resize-rotated-box': _edit('select', [('click', [30, 45]), ('handle', 9, {'to': lambda h: [h[0] + 5, h[1] + 12]}),
                                           ('handle', 4, {'to': lambda h: [h[0] + 6, h[1] + 6]})],
        lambda n, d: n['shape_type'][0] == 'rectangle' and len(n['data'][0]) == 4 and n['data'][1] == F[1]),
    'resize-degenerate-box': _edit('select', [('click', [30, 30]), ('handle', 4, {'to': lambda h: [h[0] + 10, h[1] + 10]})],
        lambda n, d: all(v == v for p in n['data'][0] for v in p), setup=LINE_SETUP),
    'direct-click': _edit('direct', [('click', [20, 45])],
        lambda n, d: n['selected'] == [0] and _unchanged_f(n)),
    'direct-rectangle-to-polygon': _edit('direct', [('click', [20, 45]), ('drag', [[20, 30], [10, 15], [0, 0]])],
        lambda n, d: n['shape_type'][0] == 'polygon' and near([n['data'][0][0]], [points_of(d)[-1]])
        and n['data'][0][1:] == F[0][1:] and n['data'][1] == F[1]),
    'drag-shared-polygon-endpoint': _edit('direct', [('click', [65, 30]), ('drag', [[60, 20], [57, 17], [55, 15]])],
        lambda n, d: n['shape_type'][1] == 'polygon' and len(n['data'][1]) == 3 and n['data'][0] == F[0]),
    'insert-line-to-path': _edit('select', [('click', [30, 30]), ('mode', 'vertex_insert'), ('click', [32, 30])],
        lambda n, d: n['shape_type'] == ['path'] and len(n['data'][0]) == 3
        and n['data'][0][0] == [30.0, 10.0] and n['data'][0][-1] == [30.0, 50.0], setup=LINE_SETUP),
    'insert-open-path-start': _edit('select', [('click', [35, 20]), ('mode', 'vertex_insert'), ('click', [30, 6])],
        lambda n, d: n['shape_type'] == ['path'] and len(n['data'][0]) == 4, setup=PATH_SETUP),
    'insert-open-path-end': _edit('select', [('click', [35, 20]), ('mode', 'vertex_insert'), ('click', [30, 54])],
        lambda n, d: n['shape_type'] == ['path'] and len(n['data'][0]) == 4, setup=PATH_SETUP),
    'insert-rectangle-to-polygon': _edit('select', [('click', [30, 45]), ('mode', 'vertex_insert'), ('click', [20, 45])],
        lambda n, d: n['shape_type'][0] == 'polygon' and len(n['data'][0]) == 5
        and all(v in n['data'][0] for v in F[0]) and n['data'][1] == F[1]),
    'remove-minimal-line': _edit('select', [('click', [30, 30]), ('mode', 'vertex_remove'), ('click', [30, 10])],
        lambda n, d: n['data'] == [] and n['selected'] == [], setup=LINE_SETUP),
    'remove-minimal-polygon': _edit('select', [('click', [48, 18]), ('mode', 'vertex_remove'), ('click', [40, 10])],
        lambda n, d: n['data'] == [] and n['selected'] == [], setup=TRIANGLE_SETUP),
    'remove-rectangle-to-polygon': _edit('select', [('click', [30, 45]), ('mode', 'vertex_remove'), ('click', [20, 30])],
        lambda n, d: n['shape_type'][0] == 'polygon' and n['data'][0] == F[0][1:] and n['data'][1] == F[1]),
    'front': _edit('select', [('click', [25, 25]), ('button', 'Bring to front')],
        lambda n, d: n['z_index'] == [8, 7, 4], setup=STACK_SETUP),
    'back': _edit('select', [('click', [55, 55]), ('button', 'Send to back')],
        lambda n, d: n['z_index'][1] == 1 and n['z_index'][0] == 2 and n['z_index'][2] == 4, setup=STACK_SETUP),
    'copy-paste-features-text-colors': _edit('select', [('click', [30, 45]), ('key', 'c', {'modifiers': ('Meta',)}),
                                                        ('key', 'v', {'modifiers': ('Meta',)})],
        lambda n, d: len(n['data']) == 3 and rounded(n['data'][2]) == rounded(F[0]), setup=FEATURES_SETUP),
    'shortcuts': _edit('select', [op for key, mods in (('r', ()), ('e', ()), ('l', ()), ('t', ()), ('l', ('Shift',)),
                                                        ('p', ()), ('p', ('Shift',)), ('d', ()), ('s', ()), ('i', ()),
                                                        ('x', ()), ('4', ()), ('5', ()), ('2', ()), ('1', ()))
                                  for op in (('key', key, {'modifiers': mods}), ('read-mode',))]
                       + [('key', 's'), ('click', [30, 45]), ('key', 'f'), ('read-z',), ('key', 'b'), ('read-z',)],
        lambda n, d: n['reads'] == ['add_rectangle', 'add_ellipse', 'add_line', 'add_path', 'add_polyline', 'add_polygon',
                                    'add_polygon_lasso', 'direct', 'select', 'vertex_insert', 'vertex_remove', 'direct',
                                    'select', 'vertex_insert', 'vertex_remove', [1, 0], [-1, 0]] and _unchanged_f(n)),
    'select-slice': _edit('select', [('key', 'a'), ('read',), ('key', 'a'), ('read',)],
        lambda n, d: n['reads'] == [[0, 2], []], setup=SLICED_SETUP),
    'upstream-limit-ellipse-direct-unsupported': _edit('direct', [('click', [30, 45]), ('drag', [[20, 30], [15, 20], [10, 10]])],
        lambda n, d: n['shape_type'] == ['ellipse'] and len(n['data'][0]) == 4, setup=ELLIPSE_SETUP),
}


@pytest.mark.parametrize('procedure', sorted(EDIT))
def test_shapes_are_edited_on_the_preview_exactly_as_napari_edits_them(studio, procedure, record_property):
    """One Shapes editing procedure (select, marquee, move, rotate, resize, direct vertices, insert and remove
    vertices, order, copy and paste, select in slice), operated on the preview: the worker's shapes equal plain napari
    replaying the same input through its own canvas, and the procedure's independent expectation."""
    case = EDIT[procedure]
    native, sent = run_annotation(studio, case, record_property)
    assert case['check'](native, sent), (native['data'], native['selected'], native.get('reads'), native['z_index'])


POINTS_SETUP = (f"layer = viewer.add_points(np.array([[20.0, 30.0], [60.0, 20.0]]), size=4, name='P4 points', "
                f"scale={SHAPE_SCALE!r}, translate={SHAPE_TRANSLATE!r})\n")
POINTS_SLICED = ("layer = viewer.add_points(np.array([[0.0, 20.0, 30.0], [0.0, 60.0, 20.0], [1.0, 40.0, 40.0]]), size=4, "
                 f"name='P4 points', scale=(1.5, *{SHAPE_SCALE!r}), translate=(-3.0, *{SHAPE_TRANSLATE!r}))\n"
                 "viewer.dims.set_point(1, -3.0)\n")
POINTS = {
    'step-1-add-select-move-and-delete-points': {
        'setup': POINTS_SETUP, 'layer': 'P4 points', 'mode': 'add',
        'ops': [('click', [40, 50]), ('click', [80, 60]), ('mode', 'select'), ('click', [40, 50]),
                ('drag', [[40, 50], [45, 55], [50, 60]]), ('click', [20, 30]), ('key', 'Delete')],
        # Deleting the first original point shifts the surviving original to row zero.
        'check': lambda n, d: near(n['data'], [[60.0, 20.0], [50.0, 60.0], [80.0, 60.0]])},
    'shortcuts': {
        'setup': POINTS_SLICED, 'layer': 'P4 points', 'mode': 'select',
        'ops': [('key', 'p'), ('read',), ('key', 's'), ('key', 'a'), ('read',), ('key', 'a'), ('read',),
                ('key', 'a', {'modifiers': ('Shift',)}), ('read',), ('key', 'a', {'modifiers': ('Meta',)}), ('read',),
                ('key', 'Backspace'), ('read',)],
        'check': lambda n, d: n['reads'][1:5] == [[0, 1], [], [0, 1], [0, 1, 2]] and n['data'] == []},
}


@pytest.mark.parametrize('procedure', sorted(POINTS))
def test_points_are_edited_on_the_preview_exactly_as_napari_edits_them(studio, procedure, record_property):
    """Points added, selected, moved and deleted on the preview, and napari's default Points keys: the worker's points
    equal plain napari replaying the same input, and the independent expectation."""
    case = POINTS[procedure]
    native, sent = run_annotation(studio, case, record_property)
    assert case['check'](native, sent), (native['data'], native['selected'], native.get('reads'))


@pytest.mark.parametrize('kind', ['points', 'shapes'])
def test_each_completed_points_or_shapes_gesture_is_one_undoable_transaction(studio, kind, record_property):
    """napari keeps no Points or Shapes history, so Hooke keeps one transaction per completed gesture: after three
    edits, each Undo restores exactly the native layer as it was before that edit, and each Redo exactly as it was after.
    The oracle is the worker's own native states captured after each edit (napari has no equivalent to compare)."""
    annotator = Annotator(studio)
    if kind == 'points':
        annotator.begin(POINTS_SETUP, 'P4 points')
        annotator.mode('add')
        edits = [lambda: annotator.click([40, 50]), lambda: annotator.click([80, 60]),
                 lambda: (annotator.mode('select'), annotator.drag([[40, 50], [45, 55], [50, 60]]))]
    else:
        annotator.begin(SHAPES_F, 'P4 shapes')
        annotator.mode('add_rectangle')
        edits = [lambda: annotator.drag([[50, 10], [55, 20], [60, 30]]),
                 lambda: (annotator.mode('add_polygon'), [annotator.click(p) for p in V], annotator.key('Enter')),
                 lambda: (annotator.mode('select'), annotator.drag([[30, 45], [35, 47], [40, 50]]))]

    def layer():
        row = next(r for r in studio.state()['layers'] if r['name'] == annotator.layer)
        studio.edit_section()
        return {k: row[k] for k in ('data', 'shape_type', 'size', 'symbol') if k in row}
    states = [layer()]
    for edit in edits:
        edit()
        states.append(layer())
    assert all(a != b for a, b in zip(states, states[1:])), 'each edit changed the layer'
    undone, redone = [], []
    for _ in edits:
        annotator.button('Undo annotation edit')
        undone.append(layer())
    for _ in edits:
        annotator.button('Redo annotation edit')
        redone.append(layer())
    evidence(record_property, states=states, undone=undone, redone=redone)
    assert undone == states[-2::-1], 'each Undo restores exactly the layer before that edit'
    assert redone == states[1:], 'each Redo restores exactly the layer after that edit'


# ---- saving and reopening the explicitly accepted revision -----------------------------------------------

def _saved_labels(studio, revision=None):
    """The labels array of a saved revision, read back from the project store (independent of the worker)."""
    store, sid = studio.stack.store(), studio.stack.session()
    document = next(r for r in store.list(sid) if r.document.get('title') == TITLE)
    current = store.read(document.id, sid, revision=revision)
    reference_ = current.document['state']['scientificView']['inputs']['labels']
    values = store.array_source(document.id, sid, current.revision, reference_).read(tuple(slice(0, n) for n in fx.SHAPE))
    return current.revision, np.asarray(values)


def _opening_requests(page):
    """Observed creation requests only; inventory reads and attaching old sessions cannot prove reopening."""
    page.drain()
    return [row for row in page.requests('/api/scientific-reviews')
            if row[1].endswith('/api/scientific-reviews') and isinstance(row[2], dict)]


def _accepted_reopening(previous, current, accepted, response):
    """Fail closed on the actual request/response identities, independently of a plausible native snapshot."""
    old_request, old_response = previous
    assert current['session'] == old_request['session'], 'reopened another Project'
    assert current['document'] == old_request['document'], 'reopened another document'
    assert current['revision'] == accepted > old_request['revision'], 'did not open the accepted revision'
    review = response.get('review')
    assert isinstance(review, str) and review and review != old_response.get('review'), 'did not open a new review'
    return review


def test_saved_labels_reopen_exactly_and_the_earlier_revision_stays(studio, record_property):
    """Save array changes keeps the edited labels as a new revision with every voxel exact (an ID above 2**53
    included) and the original revision unchanged; Open saved revision starts a new session with exactly those values."""
    page = studio.page
    studio.show_plane()
    start = studio.state()
    first_revision, original = _saved_labels(studio)
    opening, = _opening_requests(page)
    previous = (opening[2], page.response_json(opening[3]))
    studio.tool('paint', label=str(fx.BIG + 11), brush=3, preserve=False, contiguous=True, dimensions=2)
    request, status = studio.stroke(start, [[*fx.PLANE, 28, 5], [*fx.PLANE, 31, 14]])
    assert status.startswith('Label edit applied'), status
    edited = studio.labels(studio.state())
    studio.session.tab('Save')
    page.click(button(f'Save array changes to {LAYER}'))
    page.wait("document.body.innerText.includes('Array changes saved')", 120, 'the saved revision')
    revision, saved = _saved_labels(studio)
    expected = fx.values()
    for change in edited['changed']:
        expected[tuple(change[:4])] = np.uint64(int(change[5]))
    _, kept = _saved_labels(studio, first_revision)
    assert revision > first_revision and np.array_equal(saved, expected), 'the saved revision is exactly the edited labels'
    assert np.array_equal(kept, original) and np.array_equal(original, fx.values()), 'the original revision stays'
    assert int(saved.max()) == fx.BIG + 11, 'an ID above 2**53, exactly'
    # Stop retains this window's original revision and the accepted-save receipt. Open session on that
    # window would reopen the original; follow the receipt's ordinary control instead.
    studio.session.tab('View')
    session_summary = "[...document.querySelectorAll('.scientific-toolbar details > summary')].find(e=>e.checkVisibility()&&e.textContent.trim()==='Session')"
    if not page.eval(f"({session_summary}).parentElement.open"):
        page.click(session_summary)
    page.wait(f"({button('Stop scientific session')}).checkVisibility()", 30, 'the disclosed Stop control')
    page.click(button('Stop scientific session'))
    page.wait(f"({button('Stop scientific session')}).disabled", 120, 'the old session to stop')
    studio.session.tab('Save')
    before = len(_opening_requests(page))
    page.click(button(f'Open saved revision {revision}'))
    roots = "[...document.querySelectorAll('[data-study-retained]')]"
    def window(version):
        resource = {'id': opening[2]['document'], 'day': opening[2]['session'], 'version': version}
        return (f"{roots}.find(e=>{{const r=JSON.parse(e.dataset.studyRetained).resource;"
                f"const wanted={json.dumps(resource)};return r?.id===wanted.id&&r.day===wanted.day&&r.version===wanted.version}})")
    old_window, new_window = window(first_revision), window(revision)
    page.wait(f"({new_window})?.dataset.displayState==='rendered'", 120, 'the accepted revision window')
    # A new window may overlap the old title bar. Use the ordinary keyboard Close control on the exact
    # old revision, rather than sending a mouse click through the new window. Focus changes no UI values.
    old_close = f"({old_window}).querySelector(':scope > header button[aria-label^=Close]')"
    page.wait(f"(()=>{{const e={old_close};return e?.isConnected&&e.checkVisibility()&&!e.disabled}})()",
              30, 'the old revision Close control to accept keyboard focus')
    page.focus(old_close)
    page.key('Enter')
    page.wait(f"({old_window}).hidden", 30, 'the old revision window to close')
    start = f"[...({new_window}).querySelectorAll('button')].find(e=>e.textContent.trim()==='Open session')"
    page.click(start)
    page.wait(f"!({start}).disabled && ![...({new_window}).querySelectorAll('button')].find(e=>e.textContent.trim()==='Stop scientific session').disabled",
              240, 'the accepted revision session to open')
    current, = _opening_requests(page)[before:]
    response = page.response_json(current[3])
    # Record the discriminating observation before waiting for interactive controls or reading native data.
    fields = ('session', 'document', 'revision')
    evidence(record_property, original={key: previous[0][key] for key in fields},
             opened={key: current[2][key] for key in fields},
             previous_review=previous[1].get('review'), review=response.get('review'),
             visible_revisions=page.eval("[...document.querySelectorAll('.scientific-revision')].filter(e=>e.offsetParent).map(e=>e.textContent)"))
    review = _accepted_reopening(previous, current[2], revision, response)
    studio.session.tab('View'); studio.session.settle()
    reopened = studio.labels(studio.state())
    _, kept_after_reopen = _saved_labels(studio, first_revision)
    assert np.array_equal(kept_after_reopen, original), 'opening accepted data must leave the earlier revision unchanged'
    evidence(record_property, revision=revision, review=review, changed=edited['changed_count'], reopened=reopened['changed_count'])
    assert reopened['sha256'] == edited['sha256'] and reopened['changed'] == edited['changed'], 'reopened exactly as saved'
