"""H06b (P3 Points and Shapes) executable checks for scientific_acceptance.CHECKS.

Each child passes only when all of its checks pass with the prepared runtimes present:
- the production-Studio journey in a real Chromium on an isolated profile (tests/test_scientific_points_shapes_browser.py),
  which changes Points or Shapes only through View and Inspect and reads native state in the Python section,
  compared with a plain pinned-napari process and independent values;
- the actual-worker comparison (tests/test_scientific_points_shapes_parity.py): the same changes as frame-bound
  worker requests, observations equal to plain pinned napari, renders equal to the pixel at an explicit camera,
  and independent numpy values;
- refusals before any change, and the harness self-check: with napari's Points.symbol setter and Shapes.move_to_front
  broken inside the actual worker, the same comparisons fail.
Each child's procedure, oracle, criteria and Hooke equivalent are in the inventory's manual.json `specifications`.
Without the runtimes the tests skip and these children stay missing (the runner's `requires`).
"""
BROWSER = 'tests/test_scientific_points_shapes_browser.py::'
PARITY = 'tests/test_scientific_points_shapes_parity.py::'
REQUIRES = ('NAPARI_HOST_PREPARATION', 'NAPARI_REFERENCE_PYTHON', 'HOOKE_CHROMIUM', 'HOOKE_PLAYWRIGHT',
            'NAPARI_ARCHIVE', 'NAPARI_REFERENCE_WHEEL')
RUNTIME = 'tests/test_scientific_image_labels_parity.py::test_h06_worker_and_reference_use_approved_gui_runtimes'
SELF_CHECK = PARITY + 'test_the_comparison_catches_a_worker_whose_napari_behaviour_was_broken'
REFUSED = PARITY + 'test_a_refused_style_leaves_the_actual_layer_unchanged'
TESTS = {
    'points': (BROWSER + 'test_browser_points_style_and_selection_reach_native_napari_state',
               PARITY + 'test_actual_points_style_matches_pinned_napari_and_independent_values', REFUSED, SELF_CHECK),
    'shapes': (BROWSER + 'test_browser_shapes_style_and_z_order_reach_native_napari_state',
               PARITY + 'test_actual_shapes_style_and_z_order_match_pinned_napari_and_independent_values', REFUSED, SELF_CHECK),
}
SYMBOLS = ('arrow', 'clobber', 'cross', 'diamond', 'disc', 'hbar', 'ring', 'square', 'star', 'tailed-arrow', 'triangle-down',
           'triangle-up', 'vbar', 'x')
ANCHORS = ('center', 'upper-left', 'upper-right', 'lower-left', 'lower-right')
CHILDREN = {
    'points': ('points-symbol', *(f'symbol-{s}' for s in SYMBOLS), 'points-size', 'points-border-width',
               'points-border-width-is-relative', 'points-face-color', 'points-face-color-mode', 'points-face-color-cycle',
               'points-face-colormap', 'points-face-contrast-limits', 'points-border-color', 'points-border-color-mode',
               'points-border-color-cycle', 'points-border-colormap', 'points-border-contrast-limits',
               'colormode-direct', 'colormode-cycle', 'colormode-colormap',
               'api-colormanager-color-mode', 'api-colormanager-colors', 'api-colormanager-color-properties',
               'api-colormanager-categorical-colormap', 'api-colormanager-continuous-colormap', 'api-colormanager-contrast-limits',
               'api-colorproperties-name', 'points-shading', 'shading-none', 'shading-spherical',
               *(f'text-anchor-{a}' for a in ANCHORS), 'text-textmanager-string', 'text-textmanager-color',
               'text-textmanager-anchor', 'text-textmanager-size', 'text-textmanager-rotation', 'text-textmanager-translation',
               'text-textmanager-blending', 'text-textmanager-visible', 'text-textmanager-scaling',
               'api-constantstringencoding-constant', 'api-constantstringencoding-encoding-type',
               'api-directstringencoding-feature', 'api-directstringencoding-encoding-type',
               'api-formatstringencoding-format', 'api-formatstringencoding-encoding-type',
               'api-constantcolorencoding-constant', 'api-constantcolorencoding-encoding-type',
               'step-1-exercise-supported-symbols-and-size-border', 'step-2-color-and-label-by-features',
               'step-3-select-points-across-slices'),
    'shapes': ('shapes-edge-width', 'shapes-face-color', 'shapes-face-color-cycle', 'shapes-face-colormap',
               'shapes-face-contrast-limits', 'shapes-edge-color', 'shapes-edge-color-cycle', 'shapes-edge-colormap',
               'shapes-edge-contrast-limits', 'colormode-direct', 'colormode-cycle', 'colormode-colormap',
               'step-1-inspect-each-shape', 'step-2-change-edge-face-width-z-order'),
}


def checks():
    result = {f'P3.{family}.{child}': {'tests': (*TESTS[family], RUNTIME), 'class': 'isolated_profile_browser', 'requires': REQUIRES}
              for family, children in CHILDREN.items() for child in children}
    for family in ('points', 'shapes'):
        for child in CHILDREN[family]:
            result[f'P3.{family}.{child}']['tests'] += (
                PARITY + f'test_constant_native_colormap_renders_and_inspects_without_losing_limits[{family}]',)
    for child in ('points-border-colormap', 'points-border-contrast-limits'):
        result[f'P3.points.{child}']['tests'] += (
            BROWSER + 'test_browser_points_border_colormap_and_limits_match_independent_values',
            PARITY + 'test_actual_points_border_colormap_and_limits_match_independent_values')
    return result
