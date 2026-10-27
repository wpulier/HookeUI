"""Actual App/Core/Registry/Screen with synthetic external CSS measurements.

The real Studio/FloatingWindow geometry is exercised separately in TypeScript;
these are adapter admission/receipt regressions, not browser pixel evidence.
No action handler, proof comparator or authority owner is replaced.
"""
import asyncio
import copy

import pytest

from tests.test_workspace_actions_app import (
    WORKSPACE, built, controlled_state, open_window, plan, saved_picture, succeeded,
)


@pytest.fixture
def native(tmp_path):
    app, studio = built(tmp_path)
    try:
        yield app, studio
    finally:
        app.hub._analysis.shutdown(wait=True)
        app.hub._background.shutdown(wait=True)


async def pair(app, studio, *, narrow):
    entry, pixels = saved_picture(app)
    moved, _, _ = await open_window(app, entry)
    anchor, _, _ = await open_window(app, entry)
    studio.presentation_area = [0, 0, 487 if narrow else 1200, 600]
    # Actual registered operations establish both original windows, dimensions
    # and owner versions; measurements are never inserted into Core by the test.
    runs, _ = await plan(app, [
        ('present', {'do': 'resize', 'instance': {'instanceId': moved},
                     'size': {'width': 320, 'height': 344.828125}}),
        ('present', {'do': 'move', 'instance': {'instanceId': moved},
                     'position': {'x': 0, 'y': 200}}),
        ('present', {'do': 'resize', 'instance': {'instanceId': anchor},
                     'size': {'width': 180, 'height': 120}}),
        ('present', {'do': 'move', 'instance': {'instanceId': anchor},
                     'position': {'x': 300 if narrow else 20, 'y': 10}}),
    ])
    for run in runs:
        succeeded(run)
        assert run.outcome['status'] == 'confirmed'
    assert studio.items[moved]['rect'] == [0, 200, 320, 344.828125]
    assert studio.items[anchor]['rect'] == [300 if narrow else 20, 10, 180, 120]
    observed = app.hub.screen.completion_report(studio)
    assert observed['viewport']['presentationArea'] == studio.presentation_area
    assert app.hub.resources.picture(entry.id, 0, entry.day)[0] == pixels
    return moved, anchor


def test_feasible_beside_confirms_exact_original_size_and_fixed_reference(native):
    app, studio = native

    async def go():
        moved, anchor = await pair(app, studio, narrow=False)
        original = copy.deepcopy(studio.items[moved])
        fixed = copy.deepcopy(studio.items[anchor])
        fixed_core = controlled_state(studio.store().get(WORKSPACE, anchor))
        runs, _ = await plan(app, [('present', {'do': 'arrange',
            'targets': [{'instanceId': moved}],
            'relation': {'kind': 'beside', 'reference': {'instanceId': anchor},
                         'gapPx': 10, 'align': 'top'}})])
        run = runs[0]
        succeeded(run)
        actual = studio.items[moved]
        assert actual['rect'] == [210, 10, 320, 344.828125]
        assert actual['sourceRef'] == original['sourceRef']
        assert actual['sourceToken'] == original['sourceToken']
        assert actual['lifetimeGeneration'] == original['lifetimeGeneration']
        assert studio.items[anchor] == fixed
        assert controlled_state(studio.store().get(WORKSPACE, anchor)) == fixed_core
        assert run.outcome['status'] == 'confirmed', 'BEHAVIOR: actual beside geometry must be independently confirmed'
        assert not run.outcome.get('failure')
    asyncio.run(go())


@pytest.mark.parametrize('side', ['right', 'left'])
def test_impossible_beside_refuses_before_core_or_renderer_mutation(native, side):
    app, studio = native

    async def go():
        moved, anchor = await pair(app, studio, narrow=True)
        before = {iid: controlled_state(studio.store().get(WORKSPACE, iid)) for iid in (moved, anchor)}
        rendered = {iid: copy.deepcopy(studio.items[iid]) for iid in (moved, anchor)}
        commands = len(studio.rendered_commands)
        relation = {'kind': 'beside', 'reference': {'instanceId': anchor}, 'gapPx': 10, 'align': 'top'}
        if side == 'left':
            relation['side'] = 'left'  # explicit selection; omission retains right-side compatibility
        runs, _ = await plan(app, [('present', {'do': 'arrange',
            'targets': [{'instanceId': moved}], 'relation': relation})])
        run = runs[0]
        assert run.ok is False, 'BEHAVIOR: impossible current-size beside cannot succeed after clamping over its reference'
        assert run.outcome['status'] == 'failed'
        assert run.outcome['failure']['code'] == 'CONSTRAINT_CONFLICT'
        assert run.next and 'resize' in run.next.lower(), 'the model needs a concrete available adjustment'
        # Original measured widths and area belong in bounded factual refusal,
        # rather than a generic unavailable-operation answer or invented size.
        for number in ('320', '180', '487'):
            assert number in run.summary
        assert len(studio.rendered_commands) == commands
        for iid in (moved, anchor):
            assert controlled_state(studio.store().get(WORKSPACE, iid)) == before[iid]
            assert studio.items[iid] == rendered[iid]
    asyncio.run(go())
