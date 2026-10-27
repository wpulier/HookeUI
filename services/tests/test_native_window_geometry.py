"""TEST-only native viewport geometry through actual App/Registry/Screen.

External AppStudio measurements use literal worked DOM layout examples, not
backend postconditions or a copied product clamp. Native retained-byte decode,
command fences, owner/lifetime/source checks and completion are the real paths.
presentationArea is the proposed optional measured host/visual-viewport
intersection in viewport CSS coordinates. No browser/model quality is inferred.
"""
import asyncio
import copy

import pytest

from orchestrator.commands import COMMANDS
from tests.test_native_requested_goal_binding import close_app, inline_report
from tests.test_workspace_actions_app import (
    WORKSPACE, address, built, output_window, plan, saved_picture, succeeded,
)


ABSENT = object()


class LayoutMeasurements:
    """Only external renderer/report input, retaining actual command history.

    AppStudio still performs the real retained JPEG read/decode and records the
    actual original command fences. This fixture changes its external DOM
    rectangles to independently declared browser layout observations.
    """
    def __init__(self, app, studio, area):
        self.app, self.studio, self.area = app, studio, copy.deepcopy(area)
        self.rects = {}
        self.changed_area = ABSENT
        self.reports = []
        studio.send_report = self.send_report

    async def send_report(self, user=()):
        commands = [m for m in self.studio.messages if m.get('schema') == 'presentation-command-v2']
        op = commands[-1]['op'] if commands else None
        if op in self.rects:
            for iid in commands[-1]['targets']:
                item = self.studio.items[iid]
                outer = list(self.rects[op])
                before = item['rect']
                dx, dy = outer[0] - before[0], outer[1] - before[1]
                item['rect'] = outer
                item['contentRect'] = [outer[0], outer[1] + 30, outer[2], outer[3] - 30]
                item['clipRect'] = list(item['contentRect'])
                transform = item['viewTransform']
                transform['contentOrigin'] = item['contentRect'][:2]
                transform['sourceToWorkspace'][4] += dx
                transform['sourceToWorkspace'][5] += dy
        # A resize/move report can advance sequence legitimately. The known
        # layout area itself changing during that command is a separate fact.
        area = self.changed_area if op == 'move' and self.changed_area is not ABSENT else self.area
        raw = inline_report(self.studio)
        raw['user'] = list(user)
        if area is not ABSENT:
            raw['viewport']['presentationArea'] = copy.deepcopy(area)
        if area is None:
            for item in raw['items']:
                item['visible'] = False
        self.reports.append(copy.deepcopy(raw))
        result = await COMMANDS['screen'](self.app, self.studio, raw)
        assert result['ok'] is True, result  # valid external shape, no bypass


def layout_world(tmp_path, area):
    app, studio = built(tmp_path)
    renderer = LayoutMeasurements(app, studio, area)
    return app, studio, renderer


def expected_rect(run):
    return [row['expected']['rect'] for row in run.outcome['expected'] if row['predicate'] == 'rect']


async def setup_window(app, renderer, *, size=(240, 160), resize_rect=(0, 0, 240, 160)):
    entry, data = saved_picture(app)
    opened, ctx = await plan(app, [('present', {'do': 'open', 'source': address(entry)})])
    succeeded(opened[0])
    iid = output_window(ctx)
    renderer.rects['resize'] = resize_rect
    resized, ctx = await plan(app, [('present', {'do': 'resize', 'instance': {'instanceId': iid},
        'size': {'width': size[0], 'height': size[1]}})])
    return entry, data, iid, opened[0], resized[0], ctx


@pytest.mark.parametrize('area,requested,size,resize_rect,drawn', [
    ([0, 0, 487, 500], [247, -233], [240, 160], [0, 0, 240, 160], [247, 0, 240, 160]),
    ([100, 50, 400, 300], [500, 400], [240, 160], [100, 50, 240, 160], [260, 190, 240, 160]),
    ([100, 50, 400, 300], [-50, -20], [700, 600], [100, 50, 400, 300], [100, 50, 400, 300]),
], ids=['negative-top', 'offset-host-bottom-right', 'oversize-negative-origin'])
def test_actual_native_layout_verifies_fitted_viewport_rect_and_keeps_requested_intent(
        tmp_path, area, requested, size, resize_rect, drawn):
    app, studio, renderer = layout_world(tmp_path, area)

    async def go():
        entry, raw, iid, opened, resized, _ = await setup_window(
            app, renderer, size=size, resize_rect=resize_rect)
        succeeded(resized)
        assert resized.outcome['status'] == 'confirmed'
        assert expected_rect(resized) == [list(resize_rect)]
        source = opened.outputs[0]['source']
        renderer.rects['move'] = drawn
        moved, ctx = await plan(app, [('present', {'do': 'move', 'instance': {'instanceId': iid},
            'position': {'x': requested[0], 'y': requested[1]}})])
        run = moved[0]
        succeeded(run)
        assert run.outcome['status'] == 'confirmed'
        assert expected_rect(run) == [drawn]
        assert run.outcome['observed'][0]['rect'] == drawn
        state = studio.store().get(WORKSPACE, iid)
        assert list(state.geometry_intent.position) == requested
        assert list(state.geometry_intent.size) == size
        assert state.source.to_wire() == source
        assert app.hub.resources.picture(entry.id, 0, entry.day)[0] == raw
        assert ctx.inspection_slots == [] and ctx.admission.used == 0
        accepted = app.hub.screen.observation(studio).wire()
        assert accepted['viewport']['presentationArea'] == area
        assert renderer.reports[-1]['viewport']['presentationArea'] == area

    try:
        asyncio.run(go())
    finally:
        close_app(app)


def test_independently_wrong_drawn_rect_is_not_accepted_as_the_clamped_expectation(tmp_path):
    app, studio, renderer = layout_world(tmp_path, [0, 0, 487, 500])

    async def go():
        _, _, iid, _, resized, _ = await setup_window(app, renderer)
        succeeded(resized)
        renderer.rects['move'] = [200, 0, 240, 160]  # fits area, but is NOT requested x247
        runs, _ = await plan(app, [('present', {'do': 'move', 'instance': {'instanceId': iid},
            'position': {'x': 247, 'y': -233}})])
        run = runs[0]
        assert run.ok is False
        assert expected_rect(run) == [[247, 0, 240, 160]]
        assert run.outcome['error']['code'] == 'GEOMETRY_MISMATCH'
        assert run.outcome['observed'][0]['rect'] == [200, 0, 240, 160]
        assert run.outputs == []

    try:
        asyncio.run(go())
    finally:
        close_app(app)


def test_changed_presentation_area_cannot_confirm_an_original_command_by_rebasing_its_proof(tmp_path):
    app, studio, renderer = layout_world(tmp_path, [0, 0, 487, 500])

    async def go():
        _, _, iid, _, resized, _ = await setup_window(app, renderer)
        succeeded(resized)
        renderer.changed_area = [0, 100, 487, 400]
        renderer.rects['move'] = [247, 100, 240, 160]
        runs, _ = await plan(app, [('present', {'do': 'move', 'instance': {'instanceId': iid},
            'position': {'x': 247, 'y': -233}})])
        run = runs[0]
        assert run.ok is False
        assert expected_rect(run) == [[247, 0, 240, 160]]
        error = run.outcome.get('error') or run.outcome.get('failure')
        assert error['code'] == 'LAYOUT_CHANGED'
        assert run.outputs == []
        assert app.hub.screen.observation(studio).wire()['viewport']['presentationArea'] == [0, 100, 487, 400]
        commands = [m for m in studio.messages if m.get('schema') == 'presentation-command-v2']
        issued = commands[-1]['fence']['afterReportSeq']
        original = next(r for r in renderer.reports if r['seq'] == issued)
        assert original['viewport']['presentationArea'] == [0, 0, 487, 500]

    try:
        asyncio.run(go())
    finally:
        close_app(app)


@pytest.mark.parametrize('area', [ABSENT, None], ids=['legacy-unknown', 'measured-offscreen'])
def test_missing_or_offscreen_presentation_area_never_certifies_exact_native_geometry(tmp_path, area):
    app, studio, renderer = layout_world(tmp_path, [0, 0, 487, 500])

    async def go():
        _, _, iid, _, resized, _ = await setup_window(app, renderer)
        succeeded(resized)
        renderer.area = area
        renderer.rects['move'] = [20, 20, 240, 160]
        runs, _ = await plan(app, [('present', {'do': 'move', 'instance': {'instanceId': iid},
            'position': {'x': 20, 'y': 20}})])
        run = runs[0]
        assert run.outcome['status'] != 'confirmed'
        assert run.outputs == []
        accepted = app.hub.screen.observation(studio).wire()['viewport']
        if area is ABSENT:
            assert 'presentationArea' not in accepted
        else:
            assert accepted.get('presentationArea', ABSENT) is None

    try:
        asyncio.run(go())
    finally:
        close_app(app)


@pytest.mark.parametrize('drawn,confirmed', [
    ([167, 0, 320, 224], True),
    ([140, 0, 320, 224], False),
], ids=['original-default-size-fitted', 'wrong-drawn-default-size-position'])
def test_move_default_size_window_uses_original_accepted_dimensions_before_ack(
        tmp_path, drawn, confirmed):
    """Real open then move, without resize; original external DOM size is the basis."""
    app, studio, renderer = layout_world(tmp_path, [0, 0, 487, 500])
    renderer.rects['open'] = [0, 0, 320, 224]  # independent external default window measurement

    async def go():
        entry, raw = saved_picture(app)
        opened, ctx = await plan(app, [('present', {'do': 'open', 'source': address(entry)})])
        succeeded(opened[0])
        iid = output_window(ctx)
        before = app.hub.screen.observation(studio).wire()
        original = next(item for item in before['items'] if item['id'] == iid)
        assert original['rect'] == [0, 0, 320, 224]
        assert before['viewport']['presentationArea'] == [0, 0, 487, 500]
        state = studio.store().get(WORKSPACE, iid)
        assert state.geometry_intent.size is None  # no hidden resize or declared dimensions
        source = state.source.to_wire()

        renderer.rects['move'] = drawn
        moved, ctx = await plan(app, [('present', {'do': 'move', 'instance': {'instanceId': iid},
            'position': {'x': 247, 'y': -233}})])
        run = moved[0]
        # 487px area minus original 320px width leaves x167; negative y fits to0.
        assert expected_rect(run) == [[167, 0, 320, 224]]
        assert run.outcome['observed'][0]['rect'] == drawn
        if confirmed:
            succeeded(run)
            assert run.outcome['status'] == 'confirmed'
        else:
            assert run.ok is False and run.outcome['error']['code'] == 'GEOMETRY_MISMATCH'
            assert run.outputs == []
        commands = [m for m in studio.messages if m.get('schema') == 'presentation-command-v2']
        assert [command['op'] for command in commands] == ['open', 'move']
        issued_seq = commands[-1]['fence']['afterReportSeq']
        # plan publishes a fresh initiating report before its actual command.
        issuing = next(report for report in renderer.reports if report['seq'] == issued_seq)
        assert issued_seq > before['seq']
        assert next(item for item in issuing['items'] if item['id'] == iid)['rect'] == [0, 0, 320, 224]
        assert issuing['viewport']['presentationArea'] == [0, 0, 487, 500]
        state = studio.store().get(WORKSPACE, iid)
        assert state.geometry_intent.size is None
        assert list(state.geometry_intent.position) == [247, -233]
        assert state.source.to_wire() == source
        assert app.hub.resources.picture(entry.id, 0, entry.day)[0] == raw
        assert ctx.inspection_slots == [] and ctx.admission.used == 0

    try:
        asyncio.run(go())
    finally:
        close_app(app)


def test_resize_of_an_auto_placed_window_keeps_where_the_studio_placed_it(tmp_path):
    """Live 2026-10-11 16:22-16:43: 5 resizes of just-opened windows refused, GEOMETRY_MISMATCH.

    The studio places a new window away from its object (placeAway), never at 0,0, and resizes it
    in place. The admitted geometry kept the never-set position (0,0), so the drawn rect never
    matched. A window never placed by a move is resized where it was observed.
    """
    app, studio, renderer = layout_world(tmp_path, [0, 0, 1000, 700])
    renderer.rects['open'] = [600, 40, 320, 224]  # where the studio's own placement put it

    async def go():
        entry, _ = saved_picture(app)
        opened, ctx = await plan(app, [('present', {'do': 'open', 'source': address(entry)})])
        succeeded(opened[0])
        iid = output_window(ctx)
        assert tuple(studio.store().get(WORKSPACE, iid).geometry_intent.position) == (0.0, 0.0)
        renderer.rects['resize'] = [600, 40, 240, 160]  # resized in place
        resized, _ = await plan(app, [('present', {'do': 'resize', 'instance': {'instanceId': iid},
            'size': {'width': 240, 'height': 160}})])
        run = resized[0]
        assert expected_rect(run) == [[600, 40, 240, 160]]
        succeeded(run)
        assert run.outcome['status'] == 'confirmed'
        state = studio.store().get(WORKSPACE, iid)
        assert list(state.geometry_intent.position) == [600, 40]
        assert list(state.geometry_intent.size) == [240, 160]

    try:
        asyncio.run(go())
    finally:
        close_app(app)


@pytest.mark.parametrize('value', [1, 0, 0.5], ids=['opaque', 'transparent', 'between'])
def test_opacity_at_the_sliders_ends_confirms_with_the_studios_json_numbers(tmp_path, value):
    """Live 2026-10-11 16:42:35: the opacity slider at an end was refused (CONTROL_UNAVAILABLE, "The displayed window
    state differs from the request."). The server expects 1.0 or 0.0; the browser's JSON says 1 or 0."""
    app, studio, renderer = layout_world(tmp_path, [0, 0, 1000, 700])
    measured = renderer.send_report

    async def browser_numbers(user=()):
        for item in studio.items.values():
            view = item.get('view') or {}
            opacity = view.get('opacity')
            if isinstance(opacity, float) and opacity.is_integer():
                view['opacity'] = int(opacity)  # JSON.stringify(1.0) is "1": an int once parsed
            if opacity == 0:
                item['visible'] = False  # workspace-windows.ts viewVisible: a window at opacity 0 is not visible
        await measured(user)
    studio.send_report = browser_numbers

    async def go():
        entry, _ = saved_picture(app)
        opened, ctx = await plan(app, [('present', {'do': 'open', 'source': address(entry)})])
        succeeded(opened[0])
        iid = output_window(ctx)
        if value != 0.5:
            faded, _ = await plan(app, [('present', {'do': 'opacity', 'instance': {'instanceId': iid}, 'value': '0.5'})])
            succeeded(faded[0])
        runs, _ = await plan(app, [('present', {'do': 'opacity', 'instance': {'instanceId': iid}, 'value': str(value)})])
        run = runs[0]
        assert run.outcome['status'] == 'confirmed', run.outcome.get('error')
        succeeded(run)

    try:
        asyncio.run(go())
    finally:
        close_app(app)
