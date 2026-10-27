"""H07 (P4 annotation) executable checks for scientific_acceptance.CHECKS.

Every check here is isolated-profile browser evidence: tests/test_scientific_annotation_browser.py drives a fresh
Chromium profile against a private orchestrator and the Studio built from this checkout, edits through the actual review
worker with the Studio's Labels tools on the preview (or the session's Python panel where a child names an API), and
compares the worker's native result with a plain pinned-napari process doing the same through napari's own mouse
callbacks and actions (tests/scientific_annotation_reference.py), and with independent numpy expectations
(tests/scientific_annotation_fixture.py, tests/scientific_annotation_api.py). Each child's procedure, oracle, criteria
and Hooke equivalent are in the inventory's manual.json `specifications`.

Without prepared runtimes or approved local source/wheel artifacts these children stay missing (the runner's
`requires`). Every child requires the opening, runtime/GUI profile guard and broken-worker negative; each reference
report also validates those pins directly.
"""
BROWSER = 'tests/test_scientific_annotation_browser.py::'
REQUIRES = ('NAPARI_ANNOTATION_PREPARATION', 'HOOKE_BROWSER', 'NAPARI_ARCHIVE', 'NAPARI_REFERENCE_WHEEL')

OPEN = 'test_open_labels_resource_offers_napari_labels_with_their_integer_type'
PAINT = 'test_paint_and_erase_strokes_with_several_brush_sizes_on_a_nonzero_slice'
FILL = 'test_fill_contiguous_on_and_off_and_paint_with_preservation_on_and_off'
POLYGON = 'test_polygon_fills_the_displayed_plane_and_pick_reads_any_integer_id'
HISTORY = 'test_undo_and_redo_each_gesture_in_order'
DIMENSIONS = 'test_edit_dimensions_paint_through_hidden_slices_and_on_swapped_displayed_axes'
KEYS = 'test_napari_labels_shortcuts_on_the_preview_run_napari_s_own_actions'
FIELDS = 'test_labels_fields_set_napari_s_own_layer_settings'
SAVE = 'test_saved_labels_reopen_exactly_and_the_earlier_revision_stays'
INTERRUPTED = ('test_an_unsent_gesture_is_cancelled_by_focus_loss_pointer_cancel_and_escape_and_a_disconnect_applies_'
               'nothing')
# The harness's own check: napari's editing broken inside the worker must make the same comparisons fail.
SELF_CHECK = 'test_the_comparison_catches_a_worker_whose_napari_editing_was_broken'
RUNTIME = 'test_annotation_worker_and_reference_use_approved_gui_runtimes'


def api(recipe):
    return f'test_points_and_shapes_editing_api_in_the_worker_is_napari_s[{recipe}]'


# child id -> the tests whose every case must pass
MAPPING = {
    # Labels tools (P4.label-tools)
    'P4.label-tools.step-1-apply-each-supported-tool-to-known': (PAINT, FILL, POLYGON),
    'P4.label-tools.labels-paint': (PAINT,),
    'P4.label-tools.labels-fill': (FILL,),
    'P4.label-tools.labels-paint-polygon': (POLYGON,),
    'P4.label-tools.labelspolygonoverlay-add-polygon-to-labels': (POLYGON,),
    'P4.label-tools.complete-polygon': (POLYGON,),
    'P4.label-tools.shortcut-napari-complete-polygon': (POLYGON,),
    'P4.label-tools.mouse-draw': (PAINT, FILL),
    'P4.label-tools.mouse-pick': (POLYGON,),
    'P4.label-tools.fixture-precision-semantics': (POLYGON, FIELDS, OPEN, SAVE),
    'P4.label-tools.step-2-inspect-the-accepted-revision': (SAVE,),
    'P4.edit-conflicts.step-1-start-a-gesture-and-lose-focus': (INTERRUPTED,),
    'P4.edit-conflicts.step-2-cancel-a-pointer-and-disconnect': (INTERRUPTED,),
    'P4.edit-conflicts.reset-polygon': (INTERRUPTED,),
    'P4.edit-conflicts.shortcut-napari-reset-polygon': (INTERRUPTED,),
    'P4.label-history.step-3-save-reopen-and-inspect-retained-revisions': (SAVE,),
    'P4.label-tools.labels-selected-label': (FIELDS, POLYGON, KEYS),
    'P4.label-tools.labels-mode': (KEYS,),
    'P4.label-tools.qtlabelscontrols': (FIELDS, KEYS),
    **{f'P4.label-tools.{name}': (KEYS,) for name in (
        'activate-labels-erase-mode', 'activate-labels-fill-mode', 'activate-labels-paint-mode',
        'activate-labels-picker-mode', 'activate-labels-polygon-mode', 'new-label', 'increase-label-id',
        'decrease-label-id', 'swap-selected-and-background-labels', 'labels-swap-selected-and-background-labels',
        'labels-mode-erase', 'labels-mode-fill', 'labels-mode-paint', 'labels-mode-pick', 'labels-mode-polygon',
        'shortcut-napari-activate-labels-erase-mode', 'shortcut-napari-activate-labels-fill-mode',
        'shortcut-napari-activate-labels-paint-mode', 'shortcut-napari-activate-labels-picker-mode',
        'shortcut-napari-activate-labels-polygon-mode', 'shortcut-napari-new-label',
        'shortcut-napari-increase-label-id', 'shortcut-napari-decrease-label-id',
        'shortcut-napari-swap-selected-and-background-labels')},
    # Brush and edit dimensions (P4.label-brush)
    'P4.label-brush.step-1-paint-with-multiple-brush-sizes-and': (PAINT, DIMENSIONS),
    'P4.label-brush.step-2-change-displayed-axes': (DIMENSIONS,),
    'P4.label-brush.step-3-repeat-on-nonzero-slices': (PAINT,),
    'P4.label-brush.labels-brush-size': (PAINT, FIELDS, KEYS),
    'P4.label-brush.labels-n-edit-dimensions': (DIMENSIONS, FIELDS),
    'P4.label-brush.qtbrushsizeslidercontrol': (FIELDS, PAINT),
    'P4.label-brush.qtndimspinboxcontrol': (FIELDS, DIMENSIONS),
    **{f'P4.label-brush.{name}': (KEYS,) for name in (
        'decrease-brush-size', 'increase-brush-size', 'shortcut-napari-decrease-brush-size',
        'shortcut-napari-increase-brush-size')},
    # Connected fill and preservation (P4.label-fill-preserve)
    'P4.label-fill-preserve.step-1-fill-with-contiguous-on-and-off': (FILL,),
    'P4.label-fill-preserve.step-2-paint-with-preservation-on-and-off': (FILL,),
    'P4.label-fill-preserve.step-3-pick-and-swap-selected-background-ids': (POLYGON, KEYS),
    'P4.label-fill-preserve.labels-contiguous': (FILL, FIELDS),
    'P4.label-fill-preserve.labels-preserve-labels': (FILL, FIELDS, KEYS),
    'P4.label-fill-preserve.qtcontiguouscheckboxcontrol': (FIELDS, FILL),
    'P4.label-fill-preserve.qtpreservelabelscheckboxcontrol': (FIELDS, FILL),
    'P4.label-fill-preserve.toggle-preserve-labels': (KEYS,),
    'P4.label-fill-preserve.shortcut-napari-toggle-preserve-labels': (KEYS,),
    # History (P4.label-history)
    'P4.label-history.step-1-perform-gestures': (HISTORY,),
    'P4.label-history.step-2-undo-each-gesture-and-redo': (HISTORY,),
    'P4.label-history.labels-undo': (HISTORY, PAINT),
    'P4.label-history.labels-redo': (HISTORY,),
    'P4.label-history.labels-data-setitem': (PAINT, HISTORY),
    'P4.label-history.labels-block-history': (HISTORY, PAINT),
}

# Points and Shapes: each API recipe is its own child; a mode's enum value is the recipe that draws or edits in it.
API_RECIPES = (
    'point-edit.points-add', 'point-edit.points-current-properties', 'point-edit.points-remove',
    'point-edit.points-remove-selected', 'point-edit.points-selected-data', 'point-edit.points-pop',
    'point-edit.points-get-point-info', 'point-edit.points-interaction-box', 'point-edit.points-mode',
    'point-edit.points-mode-add', 'point-edit.points-mode-select', 'point-edit.mouse-add', 'point-edit.mouse-select',
    'point-edit.mouse-highlight', 'point-edit.activate-points-add-mode', 'point-edit.activate-points-select-mode',
    'point-edit.select-all-data', 'point-edit.delete-selected-points', 'point-edit.copy', 'point-edit.paste',
    'point-edit.select-all-in-slice', 'point-edit.select-append-all-in-slice',
    'shape-create.shapes-add', 'shape-create.shapes-add-rectangles', 'shape-create.shapes-add-ellipses',
    'shape-create.shapes-add-lines', 'shape-create.shapes-add-paths', 'shape-create.shapes-add-polygons',
    'shape-create.shapes-current-edge-color', 'shape-create.shapes-current-edge-width',
    'shape-create.shapes-current-face-color', 'shape-create.shapes-current-properties',
    'shape-create.mouse-add-rectangle', 'shape-create.mouse-add-ellipse', 'shape-create.mouse-add-line',
    'shape-create.mouse-add-path-polygon', 'shape-create.mouse-finish-drawing-shape', 'shape-create.finish-drawing-shape',
    *(f'shape-create.activate-add-{m}-mode' for m in ('rectangle', 'ellipse', 'line', 'path', 'polyline', 'polygon',
                                                      'polygon-lasso')),
    'shape-edit.shapes-remove', 'shape-edit.shapes-remove-selected', 'shape-edit.shapes-pop',
    'shape-edit.shapes-get-shape-info', 'shape-edit.shapes-move-to-front', 'shape-edit.shapes-move-to-back',
    'shape-edit.shapes-z-index', 'shape-edit.shapes-selected-data', 'shape-edit.shapes-interaction-box',
    'shape-edit.shapes-get-index-and-intersection', 'shape-edit.shapes-edge-color-mode',
    'shape-edit.shapes-face-color-mode', 'shape-edit.shapes-mode', 'shape-edit.copy-selected-shapes',
    'shape-edit.paste-shape', 'shape-edit.delete-selected-shapes', 'shape-edit.move-shapes-selection-to-front',
    'shape-edit.move-shapes-selection-to-back', 'shape-edit.select-shapes-in-slice',
    *(f'shape-edit.activate-{m}-mode' for m in ('direct', 'select', 'vertex-insert', 'vertex-remove')),
    'shape-edit.mouse-select', 'shape-edit.mouse-move-active-vertex-under-cursor', 'shape-edit.mouse-vertex-insert',
    'shape-edit.mouse-vertex-remove', 'shape-edit.mouse-highlight',
    'label-tools.labelspolygonoverlay-enabled', 'label-tools.labelspolygonoverlay-points',
    'label-tools.labelspolygonoverlay-completion-radius', 'label-tools.labelspolygonoverlay-use-double-click-completion-radius',
    'label-tools.labelsbrushstrokeoverlay-enabled', 'label-tools.labelsbrushstrokeoverlay-active',
    'label-tools.labelsbrushstrokeoverlay-center', 'label-tools.labelsbrushstrokeoverlay-radius',
    'edit-conflicts.labelsbrushstrokeoverlay-abort',
    'shape-edit.selectionboxoverlay-update-from-points', 'shape-edit.selectionboxoverlay-bounds',
    'shape-edit.selectionboxoverlay-handles', 'shape-edit.selectionboxoverlay-selected-handle',
    'shape-create.setting-experimentalsettings-lasso-vertex-distance', 'shape-create.setting-experimentalsettings-rdp-epsilon',
    'shape-create.setting-experimentalsettings-triangulation-backend',
    'shape-create.setting-experimentalsettings-compiled-triangulation',
    'label-tools.setting-experimentalsettings-completion-radius',
    'label-brush.setting-applicationsettings-brush-size-on-mouse-move-modifiers',
    'label-tools.mouse-change-brush-size-on-mouse-move-modifiers',
    'label-tools.labels-show-selected-label', 'label-brush.brushcircleoverlay-size',
    'label-brush.brushcircleoverlay-position-is-frozen')
MAPPING.update({f'P4.{recipe}': (api(recipe),) for recipe in API_RECIPES})
for _child in ('P4.label-history.labels-block-history', 'P4.label-history.labels-data-setitem'):
    MAPPING[_child] = (*MAPPING[_child], api(_child[3:]))
MAPPING.update({
    'P4.shape-create.shapes-mode-add-rectangle': (api('shape-create.mouse-add-rectangle'), api('shape-edit.shapes-mode')),
    'P4.shape-create.shapes-mode-add-ellipse': (api('shape-create.mouse-add-ellipse'), api('shape-edit.shapes-mode')),
    'P4.shape-create.shapes-mode-add-line': (api('shape-create.mouse-add-line'), api('shape-edit.shapes-mode')),
    'P4.shape-create.shapes-mode-add-polygon': (api('shape-create.mouse-add-path-polygon'), api('shape-edit.shapes-mode')),
    'P4.shape-create.shapes-mode-add-polyline': (api('shape-create.mouse-finish-drawing-shape'),
                                                 api('shape-create.finish-drawing-shape'), api('shape-edit.shapes-mode')),
    'P4.shape-edit.shapes-mode-select': (api('shape-edit.mouse-select'), api('shape-edit.shapes-mode')),
    'P4.shape-edit.shapes-mode-direct': (api('shape-edit.mouse-move-active-vertex-under-cursor'), api('shape-edit.shapes-mode')),
    'P4.shape-edit.shapes-mode-vertex-insert': (api('shape-edit.mouse-vertex-insert'), api('shape-edit.shapes-mode')),
    'P4.shape-edit.shapes-mode-vertex-remove': (api('shape-edit.mouse-vertex-remove'), api('shape-edit.shapes-mode')),
})


def checks():
    bounded = {INTERRUPTED: tuple(f'{INTERRUPTED}[{case}]' for case in ('focus', 'pointer-cancel', 'escape', 'disconnect')),
               HISTORY: tuple(f'{HISTORY}[{direction}]' for direction in ('undo', 'redo')),
               PAINT: tuple(f'{PAINT}[{brush}]' for brush in (1, 3, 7)),
               FILL: tuple(f'{FILL}[{case}]' for case in ('connected-fill', 'global-fill', 'preserve', 'overwrite'))}
    return {child: {'tests': tuple(dict.fromkeys(BROWSER + case
                    for test in (*tests, OPEN, RUNTIME, SELF_CHECK) for case in bounded.get(test, (test,)))),
                    'class': 'isolated_profile_browser', 'requires': REQUIRES}
            for child, tests in MAPPING.items()}


# Points and Shapes editing on the preview (the annotation panel): each procedure is its own parametrized case.
def create(row):
    return f'test_shapes_are_created_on_the_preview_exactly_as_napari_creates_them[{row}]'


def edit(row):
    return f'test_shapes_are_edited_on_the_preview_exactly_as_napari_edits_them[{row}]'


def points(row):
    return f'test_points_are_edited_on_the_preview_exactly_as_napari_edits_them[{row}]'


def undo(kind):
    return f'test_each_completed_points_or_shapes_gesture_is_one_undoable_transaction[{kind}]'


DIRECTIONS = ('top', 'bottom', 'left', 'right', 'top-left', 'top-right', 'bottom-left', 'bottom-right')
CREATE_ROWS = ('rectangle', 'ellipse', 'line', 'polygon', 'polyline', 'path-drag', 'path-click-move', 'lasso-drag',
               'lasso-click-move', 'line-angle-lock', 'finish-explicit', 'finish-shortcut', 'polygon-finish-on-mode-change',
               'polyline-finish-on-mode-change', 'polygon-invalid-finish', 'polyline-invalid-finish',
               'delete-during-creation', 'rectangle-mid-drag-lock', 'ellipse-mid-drag-lock',
               *(f'{shape}-locked-drag-{d}' for shape in ('rectangle', 'ellipse') for d in DIRECTIONS))
EDIT_ROWS = ('select-click', 'shift-toggle', 'marquee-containing-select', 'marquee-partial-select', 'marquee-empty-select',
             'marquee-containing-direct', 'marquee-partial-direct', 'marquee-empty-direct', 'translate', 'rotate',
             'rotate-shift-snap', 'resize-corner', 'resize-edge', 'resize-rotated-box', 'resize-degenerate-box',
             'direct-click', 'direct-rectangle-to-polygon', 'drag-shared-polygon-endpoint', 'insert-line-to-path',
             'insert-open-path-start', 'insert-open-path-end', 'insert-rectangle-to-polygon', 'remove-minimal-line',
             'remove-minimal-polygon', 'remove-rectangle-to-polygon', 'front', 'back', 'copy-paste-features-text-colors',
             'select-slice', 'upstream-limit-ellipse-direct-unsupported')
LOCKED = tuple(create(f'{shape}-locked-drag-{d}') for shape in ('rectangle', 'ellipse') for d in DIRECTIONS)
MAPPING.update({f'P4.shape-create.{row}': (create(row),) for row in CREATE_ROWS})
MAPPING.update({f'P4.shape-edit.{row}': (edit(row),) for row in EDIT_ROWS})
MAPPING.update({
    'P4.shape-create.polyline': (create('polyline'), create('polyline-fast-double')),
    'P4.shape-create.step-1-create-each-supported-shape': tuple(create(r) for r in (
        'rectangle', 'ellipse', 'line', 'polygon', 'polyline', 'polyline-fast-double', 'path-drag', 'lasso-drag')),
    'P4.shape-create.step-2-complete-and-cancel-in-progress-shapes': tuple(create(r) for r in (
        'finish-explicit', 'finish-shortcut', 'polygon-invalid-finish', 'polyline-invalid-finish', 'delete-during-creation')),
    'P4.shape-create.hold-to-lock-aspect-ratio': (*LOCKED, create('rectangle-mid-drag-lock'), create('ellipse-mid-drag-lock'),
                                                  create('line-angle-lock')),
    'P4.shape-create.qtshapescontrols': (create('rectangle'), create('finish-explicit'), edit('front'), edit('back')),
    'P4.shape-create.mouse-add-path-polygon-lasso': (create('lasso-drag'), create('lasso-click-move')),
    'P4.shape-create.mouse-add-vertex-to-path': (create('polygon'), create('polyline'), create('path-click-move')),
    'P4.shape-create.mouse-initiate-polygon-draw': (create('polygon'), create('polyline')),
    'P4.shape-create.mouse-polygon-creating': (create('polygon-finish-on-mode-change'), create('polyline-finish-on-mode-change')),
    'P4.shape-create.shapes-mode-add-path': (create('path-drag'), create('path-click-move')),
    'P4.shape-create.shapes-mode-add-polygon-lasso': (create('lasso-drag'), create('lasso-click-move')),
    'P4.shape-create.shortcut-napari-finish-drawing-shape': (create('finish-shortcut'), create('polygon-invalid-finish')),
    **{f'P4.shape-create.shortcut-napari-activate-add-{m}-mode': (edit('shortcuts'),) for m in (
        'rectangle', 'ellipse', 'line', 'path', 'polyline', 'polygon', 'polygon-lasso')},
    **{f'P4.shape-edit.shortcut-napari-{name}': (edit('shortcuts'),) for name in (
        'activate-direct-mode', 'activate-select-mode', 'activate-vertex-insert-mode', 'activate-vertex-remove-mode',
        'move-shapes-selection-to-front', 'move-shapes-selection-to-back')},
    'P4.shape-edit.shortcut-napari-copy-selected-shapes': (edit('copy-paste-features-text-colors'),),
    'P4.shape-edit.shortcut-napari-paste-shape': (edit('copy-paste-features-text-colors'),),
    'P4.shape-edit.shortcut-napari-select-shapes-in-slice': (edit('select-slice'),),
    'P4.shape-edit.shortcut-napari-delete-selected-shapes': (create('delete-during-creation'),),
    'P4.shape-edit.step-1-move-rotate-resize-insert-remove-vertices': tuple(edit(r) for r in (
        'translate', 'rotate', 'resize-corner', 'insert-rectangle-to-polygon', 'remove-rectangle-to-polygon', 'front', 'back')),
    'P4.shape-edit.step-2-undo-and-redo': (undo('shapes'),),
    'P4.point-edit.step-1-add-select-move-and-delete-points': (points('step-1-add-select-move-and-delete-points'),),
    'P4.point-edit.step-2-undo-and-redo-accepted-edits': (undo('points'),),
    'P4.point-edit.qtpointscontrols': (points('step-1-add-select-move-and-delete-points'), points('shortcuts')),
    **{f'P4.point-edit.shortcut-napari-{name}': (points('shortcuts'),) for name in (
        'activate-points-add-mode', 'activate-points-select-mode', 'select-all-in-slice', 'select-append-all-in-slice',
        'select-all-data', 'delete-selected-points')},
})
