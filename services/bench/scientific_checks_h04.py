"""H04 (P1 dimensions) executable checks for scientific_acceptance.CHECKS.

Every check here is isolated-profile browser evidence: tests/test_scientific_dims_browser.py drives a fresh Chromium
profile against a private orchestrator and the Studio built from this checkout, operates the actual review worker
through ordinary controls (or the session's Python panel where a child names an API), and compares what is shown and
the worker's native state with a plain pinned-napari process and with independent fixture values. Each child's
procedure, oracle, criteria and Hooke equivalent are in the inventory's manual.json `specifications`.

Without both prepared runtimes the tests skip and these children stay missing (the runner's `requires`).
Kept apart from the runner so a shared comparison harness can take these over unchanged.
"""
from itertools import combinations

BROWSER = 'tests/test_scientific_dims_browser.py::'
REQUIRES = ('NAPARI_NATIVE_HOST_PREPARATION', 'HOOKE_BROWSER', 'NAPARI_ARCHIVE', 'NAPARI_REFERENCE_WHEEL')

OPEN = 'test_open_5d_resource_from_project_data_shows_every_axis'
MOVE = 'test_each_sliced_axis_moves_by_slider_numeric_entry_and_keyboard'
PROBE = tuple(f'test_probe_known_pixels_and_calibrated_coordinates_on_every_displayed_plane[{a}-{b}]'
              for a in range(5) for b in range(5) if a != b)
GO_TO = 'test_go_to_a_world_coordinate_is_napari_set_point'
CANVAS = 'test_off_centre_clicks_map_canvas_to_world_with_each_axis_direction'
# The harness's own check: napari broken inside the worker must make the same comparisons fail. Every child needs it.
SELF_CHECK = 'test_the_comparison_catches_a_worker_whose_napari_behaviour_was_broken'
ORDER = 'test_roll_swap_reorder_and_restore_axes'
DISPLAY = ('test_switch_two_and_three_displayed_dimensions_and_back',) + tuple(
    f'test_each_three_dimensional_axis_combination[{a}-{b}-{c}]' for a, b, c in combinations(range(5), 3))
KEYS = 'test_napari_dimension_shortcuts_step_and_focus_the_keyboard_axis'
LABELS = 'test_axis_labels_and_units_are_napari_s_and_shown_on_every_axis'
DIMS_API = 'test_dims_rollable_range_steps_and_reset_through_python_are_napari_s'
SLAB_STEPS = 'test_slab_steps_and_thickness_through_python_are_napari_s'
SLAB = tuple(f'test_slab_margins_and_every_projection_mode_show_reduced_values[{mode}]'
             for mode in ('none', 'sum', 'mean', 'max', 'min'))
# Explicit parameter nodes prevent a subset of the split journeys from qualifying a child.
PLAY = tuple(f'test_playback_sequence_and_draws_match_napari[{case}]' for case in (
    'once-forward', 'once-restart', 'once-backward', 'loop-forward', 'bounce-forward', 'loop-backward',
    'loop-slow', 'once-slow', 'once-restart-slow')) + (
        'test_playback_defaults_match_napari',
        'test_playback_refuses_invalid_ranges_and_excludes_displayed_axes')

# child id -> the tests whose every case must pass
MAPPING = {
    'P1.axis-position.step-1-open-the-5d-resource': (OPEN,),
    'P1.axis-position.qtdims-nsliders': (OPEN,),
    'P1.axis-position.step-2-move-each-non-displayed-axis-by': (MOVE,),
    'P1.axis-position.dims-current-step': (MOVE,),
    'P1.axis-position.dims-set-current-step': (MOVE,),
    'P1.axis-position.step-3-probe-known-pixels-at-each-position': (MOVE, *PROBE),
    'P1.axis-position.dims-point': (GO_TO, MOVE),
    'P1.axis-position.dims-set-point': (GO_TO,),
    'P1.axis-position.dims-last-used': (KEYS,),
    'P1.axis-position.increment-dims-left': (KEYS,),
    'P1.axis-position.increment-dims-right': (KEYS,),
    'P1.axis-position.focus-axes-up': (KEYS,),
    'P1.axis-position.focus-axes-down': (KEYS,),
    'P1.axis-position.shortcut-napari-increment-dims-left': (KEYS,),
    'P1.axis-position.shortcut-napari-increment-dims-right': (KEYS,),
    'P1.axis-position.shortcut-napari-focus-axes-up': (KEYS,),
    'P1.axis-position.shortcut-napari-focus-axes-down': (KEYS,),
    'P1.axis-order.step-1-reorder-axes': (ORDER,),
    'P1.axis-order.step-2-transpose-and-roll': (ORDER,),
    'P1.axis-order.step-3-select-each-supported-display-plane': (*PROBE, *DISPLAY),
    'P1.axis-order.step-4-restore-the-original-order': (ORDER,),
    'P1.axis-order.dims-order': (ORDER,),
    'P1.axis-order.dims-roll': (ORDER,),
    'P1.axis-order.roll-axes': (ORDER,),
    'P1.axis-order.dims-transpose': (ORDER,),
    'P1.axis-order.transpose-axes': (ORDER,),
    'P1.axis-order.dims-displayed': (ORDER, *DISPLAY),
    'P1.axis-order.dims-not-displayed': (ORDER, *DISPLAY),
    'P1.axis-order.dims-axis-labels': (LABELS,),
    'P1.axis-order.dims-rollable': (DIMS_API,),
    'P1.axis-position.dims-range': (DIMS_API,),
    'P1.axis-position.dims-set-range': (DIMS_API,),
    'P1.axis-position.dims-nsteps': (DIMS_API, OPEN),
    'P1.axis-position.dims-reset': (DIMS_API,),
    'P1.slab-projection.dims-margin-left-step': (SLAB_STEPS,),
    'P1.slab-projection.dims-margin-right-step': (SLAB_STEPS,),
    'P1.slab-projection.dims-thickness': (SLAB_STEPS,),
    'P1.slab-projection.dims-thickness-step': (SLAB_STEPS,),
    'P1.axis-order.dims-set-axis-label': (LABELS,),
    'P1.display-mode.dims-ndisplay': DISPLAY,
    'P1.display-mode.toggle-ndisplay': DISPLAY,
    'P1.display-mode.step-2-switch-2d-to-3d-and-back': DISPLAY,
    'P1.calibrated-coordinates.step-1-select-coordinates-on-each-displayed-plane': PROBE,
    'P1.calibrated-coordinates.step-2-change-displayed-axes': (*PROBE, ORDER),
    'P1.calibrated-coordinates.step-3-inspect-values-and-world-coordinates': PROBE,
    'P1.calibrated-coordinates.layer-get-value': PROBE,
    'P1.calibrated-coordinates.layer-data-to-world': PROBE,
    'P1.calibrated-coordinates.layer-world-to-data': PROBE,
    'P1.calibrated-coordinates.viewermodel-canvas-to-world': (CANVAS,),
    'P1.calibrated-coordinates.dims-units': (LABELS,),
    'P1.calibrated-coordinates.layer-units': (LABELS,),
    'P1.calibrated-coordinates.layerlist-units': (LABELS,),
    'P1.calibrated-coordinates.layer-axis-labels': (LABELS,),
    'P1.slab-projection.dims-margin-left': SLAB,
    'P1.slab-projection.dims-margin-right': SLAB,
    'P1.slab-projection.imageprojectionmode-none': SLAB,
    'P1.slab-projection.imageprojectionmode-sum': SLAB,
    'P1.slab-projection.imageprojectionmode-mean': SLAB,
    'P1.slab-projection.imageprojectionmode-max': SLAB,
    'P1.slab-projection.imageprojectionmode-min': SLAB,
    'P1.slab-projection.qtprojectionmodecontrol': SLAB,
    'P1.slab-projection.step-1-change-margins-and-projection-mode': SLAB,
    'P1.slab-projection.step-2-read-projected-values': SLAB,
    'P1.playback.loopmode-once': PLAY,
    'P1.playback.loopmode-loop': PLAY,
    'P1.playback.loopmode-back-and-forth': PLAY,
    'P1.playback.qtdims-play': PLAY,
    'P1.playback.qtdims-stop': PLAY,
    'P1.playback.qtdims-is-playing': PLAY,
    'P1.playback.qtdimsliderwidget-fps': PLAY,
    'P1.playback.qtdimsliderwidget-loop-mode': PLAY,
    'P1.playback.qtdimsliderwidget-frame-range': PLAY,
    'P1.playback.animationthread-advance': PLAY,
    'P1.playback.animationthread-work': PLAY,
    'P1.playback.animationthread-finish': PLAY,
    'P1.playback.animationthread-set-fps': PLAY,
    'P1.playback.animationthread-set-frame-range': PLAY,
    'P1.playback.step-1-play-each-supported-loop-mode-and': PLAY,
    'P1.playback.step-2-stop-during-playback': PLAY,
    'P1.playback.step-3-attempt-an-invalid-range-and-a': PLAY,
    'P1.playback.upstream-limit-frame-dropping': PLAY,
}


def checks():
    return {child: {'tests': tuple(BROWSER + test for test in dict.fromkeys((*tests, OPEN, SELF_CHECK))), 'class': 'isolated_profile_browser',
                    'requires': REQUIRES} for child, tests in MAPPING.items()}
