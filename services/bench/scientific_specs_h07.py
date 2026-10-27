"""The H07 (P4) children's specifications for the inventory's manual.json, written from one table.

`python -m bench.scientific_specs_h07` rewrites the H07 records of manual.json `specifications` (and only those) from
scientific_checks_h07.MAPPING and the texts below, so every mapped child states the procedure its tests run, the
three-way oracle, its own criteria and the Hooke path it exercises. A hand-written child that already has a procedure
and oracle gets only criteria and its Hooke equivalent. Then `make scientific-acceptance REGENERATE=1` carries them into
atomic-cases.json.
"""
from __future__ import annotations

import json
from pathlib import Path

from bench import scientific_checks_h07 as checks

MANUAL = Path(__file__).resolve().parents[2] / 'services/bench/specification/manual.json'

SETUP = ("Isolated profile: a new Chromium profile (HOOKE_BROWSER) on a private orchestrator with an empty home, no camera "
         "and the Studio rebuilt from the checkout (its build identity must equal the backend). The P4 fixture (a 3x4x36x44 "
         "uint64 Labels array, t/z/y/x, calibrated 2/1.5/0.5/0.25 with translate 10/-3/5/7, holding a box of label 5, an "
         "object of label 2**60+3 with a hole and two disconnected blobs of label 9 on the plane t=1, z=2, and other "
         "objects on other planes) is a saved scene document of the prepared annotation review integration "
         "(NAPARI_ANNOTATION_PREPARATION), opened from Project Data (Open result, then Open session). ")

ORACLE = ("Three ways: the actual review worker's native result (one shared P4 snapshot read through the Python panel: "
          "every changed voxel with its old and new value, the layer's tool state and history) and what the Studio shows "
          "must equal the same edit in a plain pinned napari Viewer process on the prepared runtime's own Python, driven "
          "through napari's own mouse callbacks and actions at the world positions napari's canvas_to_world gives for the "
          "frame pixels the Studio sent (tests/scientific_annotation_reference.py), and the independent numpy "
          "expectations of the fixture's definition (tests/scientific_annotation_fixture.py). Executed by "
          "tests/test_scientific_annotation_browser.py::{test}, and by the harness self-check "
          "test_the_comparison_catches_a_worker_whose_napari_editing_was_broken, which breaks napari's Labels.paint and "
          "Points.add inside the worker and needs the same comparisons to fail.")

API_ORACLE = ("Three ways: the recipe's result in the actual review worker (run through the Studio's Python panel, printed "
              "as JSON) must equal the same recipe in a plain pinned napari Viewer process on the prepared runtime's own "
              "Python holding the same session layers (tests/scientific_annotation_reference.py), and the result its "
              "definition requires, written independently in tests/scientific_annotation_api.py. Executed by "
              "tests/test_scientific_annotation_browser.py::{test}, and by the harness self-check "
              "test_the_comparison_catches_a_worker_whose_napari_editing_was_broken.")

TESTS = {
    checks.OPEN: ("Open the P4 fixture from Project Data; read the Labels layer's native state through the Python panel "
                  "and the Edit section's Labels tools.",
                  "Project Data > Open result > Open session; Edit > Labels tools"),
    checks.PAINT: ("On the plane t=1, z=2: with Paint, label 42 and brush sizes 1, 3 and 7, drag through data (8,20), "
                   "(11,27), (16,31); then with Erase drag through (6,14), (12,24) across the stroke and the box; Undo both.",
                   "Edit > Label tool Paint / Erase, Label value, Brush size; drag on the preview; Undo label edit"),
    checks.FILL: ("Fill label 9's upper blob with label 11 with Fill connected region only on, then off; paint label 13 "
                  "with brush 3 across the box from (8,2) to (8,22) with Preserve other labels on, then off; Undo each.",
                  "Edit > Label tool Fill / Paint, Fill connected region only, Preserve other labels; click or drag on the preview"),
    checks.POLYGON: ("Polygon: click data corners (3,2), (3,14), (14,10), (12,2) with label 2**60+7 and press Enter; Undo. "
                     "Pick: click the 2**60+3 object.",
                     "Edit > Label tool Polygon (click corners, Enter or Finish polygon) and Pick label; Label value"),
    checks.HISTORY: ("Paint three strokes with labels 21, 22, 23; press Undo label edit three times, then Redo label edit "
                     "three times; Undo all.",
                     "Edit > Undo label edit / Redo label edit (and Cmd/Ctrl+Z, Cmd/Ctrl+Shift+Z on the preview)"),
    checks.DIMENSIONS: ("Paint label 31 with brush 3 and Edit dimensions 3 from (30,30) to (31,36); Undo. Swap y and x on "
                        "screen (dims order 0,1,3,2) and paint label 32 with brush 1 from (33,8) to (33,14); Undo.",
                        "Edit > Edit dimensions, Brush size; drag on the preview with the displayed axes swapped"),
    checks.KEYS: ("Click the preview with the Inspect tool to focus it, then press napari's default Labels keys 1-6, E, P, "
                  "F, L, Z, M, =, =, -, X, X, ], ], [, B, B, B, reading the native tool state and the fields after each.",
                  "Preview keyboard (napari's default Labels bindings) and the Edit section's Labels fields"),
    checks.INTERRUPTED: ("Start a Paint drag and move focus to another browser tab before releasing; start a touch "
                         "stroke and have the system cancel it; click three Polygon corners and press Escape; then take "
                         "the network offline, release a Paint stroke, bring it back and press Check edit outcome.",
                         "Edit > Labels tools on the preview; Escape; Check edit outcome"),
    checks.SAVE: ("Paint label 2**60+14 with brush 3 from (28,5) to (31,14); in Save press Save array changes to "
                  "Annotation labels; read the saved and the original revisions back from the project store; press Stop "
                  "scientific session, then Open session, and read the reopened native layer.",
                  "Edit > Paint; Save > Save array changes; Stop scientific session; Open session"),
    checks.FIELDS: ("Choose Fill, type label 2**60+6, brush 11, Edit dimensions 3, Preserve other labels on and Fill "
                    "connected region only off.",
                    "Edit > Label tool, Label value, Brush size, Edit dimensions, Preserve other labels, Fill connected region only"),
}

CRITERIA = {
    checks.OPEN: "The layer is napari Labels with dtype <u8, the saved shape and every value intact, the fixture's "
                 "scale and translate, and napari's default tool state.",
    checks.PAINT: "Each stroke and the erase change exactly the voxels napari's own draw changes, all on t=1, z=2, to "
                  "42 (or the background 0), including each pressed voxel; one undoable gesture each; Undo restores the fixture.",
    checks.FILL: "Contiguous fill changes exactly the clicked 4-connected component of label 9 (global fill both blobs) "
                 "to 11; with preservation only background voxels change; without it the box is painted over; each "
                 "equals napari's fill and draw.",
    checks.POLYGON: "The polygon changes exactly what napari's polygon tool (canvas overlay, complete_polygon) changes, "
                    "within one pixel of the corners' outline, to 2**60+7; Pick sets napari's selected label to "
                    "1152921504606846979 exactly, shown in Label value.",
    checks.HISTORY: "After each Undo and each Redo the array and napari's undo/redo depths equal napari's own history "
                    "after the same gestures; three Undos restore the fixture.",
    checks.DIMENSIONS: "Edit dimensions 3 changes neighbouring z slices of t=1 exactly as napari's n_edit_dimensions does; "
                       "with swapped axes the same data indices change as in napari.",
    checks.KEYS: "After every key the worker's native mode, selected label, background, brush size, edit dimensions, "
                 "preserve, contiguous and show-selected equal napari's own action in the reference; no label value "
                 "changes; the fields show the result (new_label gives 2**60+4).",
    checks.INTERRUPTED: "Nothing interrupted is sent or changes a voxel or the history: focus loss and a cancelled "
                        "pointer cancel the unsent stroke; Escape removes the unsent polygon as napari's reset_polygon "
                        "does (which also paints nothing); the stroke lost offline is reported, never sent again, and "
                        "Check edit outcome confirms it changed nothing.",
    checks.SAVE: "The new revision's labels equal the fixture with exactly the native changes (1152921504606846990 "
                 "kept exactly), the original revision is still the fixture, and the reopened session holds exactly the "
                 "saved values.",
    checks.FIELDS: "napari's own layer then has mode fill, selected label 1152921504606846985 (every digit), brush size "
                   "11, n_edit_dimensions 3, preserve on and contiguous off, equal to napari's.",
}


PROCEDURE_TESTS = {
    'test_shapes_are_created_on_the_preview_exactly_as_napari_creates_them': ('CREATE', 'Shapes creation'),
    'test_shapes_are_edited_on_the_preview_exactly_as_napari_edits_them': ('EDIT', 'Shapes editing'),
    'test_points_are_edited_on_the_preview_exactly_as_napari_edits_them': ('POINTS', 'Points editing'),
}
REPLAY_ORACLE = ("Three ways: the worker's native layer after the Studio's input (the shared P4 snapshot through the Python "
                 "panel) must equal plain pinned napari after replaying exactly the annotate requests the Studio sent "
                 "(read from the browser's network log): napari's own actions, and the pointer and Shift events as real "
                 "vispy events on napari's own canvas at the same canvas size and camera, paced past napari's 5 ms "
                 "mouse-move throttle (tests/scientific_annotation_reference.py replay). And the procedure's independent "
                 "expectation, computed from the pointer positions sent through the camera formula "
                 "(tests/test_scientific_annotation_browser.py). Every key pressed must also be one of napari's own "
                 "default bindings for the action it ran. Executed by tests/test_scientific_annotation_browser.py::{test}, "
                 "and by the harness self-check test_the_comparison_catches_a_worker_whose_napari_editing_was_broken.")
UNDO_ORACLE = ("napari keeps no Points or Shapes history, so the oracle is the worker's own native layer captured after each "
               "edit: each Undo must restore exactly the layer before that edit and each Redo exactly the layer after it. "
               "Executed by tests/test_scientific_annotation_browser.py::{test}.")
ANNOTATE_SETUP = ("The annotated Points or Shapes layer is created in the session's Python panel (setup) with napari's own "
                  "test coordinates (data 0-100 at scale 0.1, translate 5/7, inside the view): for Shapes the fixture F, a "
                  "rectangle (20,30)-(40,60) and a triangle (60,20),(60,50),(80,35), or the procedure's own shapes. ")


def procedure_text(test):
    base, row = test.split('[', 1)
    row = row.rstrip(']')
    if base == 'test_each_completed_points_or_shapes_gesture_is_one_undoable_transaction':
        return (f"In the Edit section's annotation panel on the {row} layer, make three edits on the preview, then press "
                "Undo annotation edit three times and Redo annotation edit three times, reading the native layer after "
                "each.", UNDO_ORACLE.format(test=test), f"{row.capitalize()}: each Undo restores exactly the native layer "
                "before its edit and each Redo exactly the layer after it.")
    table, title = PROCEDURE_TESTS[base]
    return (f"In the Edit section's annotation panel choose the layer and its napari mode, then operate procedure {row} "
            f"of tests/test_scientific_annotation_browser.py {table}: real pointer presses, drags, clicks and double "
            "clicks on the preview, Shift held where the procedure says, and napari's keys on the focused preview or the "
            "panel's buttons.", REPLAY_ORACLE.format(test=test),
            f"{title} procedure {row}: the worker's layer (vertices, shape types, selection, z-order, mode) equals plain "
            "napari's replay exactly, and its stated independent expectation holds.")


def specification(child, tests, manual):
    # Some children require both ordinary controls and an API observation. Keep
    # every mapped obligation instead of classifying the entire row by its first test.
    parts = [_specification(child, (test,), manual) for test in tests]
    fields = ('criteria', 'hooke_equivalent') if manual.get(child, {}).get('procedure') and manual[child].get('oracle') else (
        'procedure', 'oracle', 'criteria', 'hooke_equivalent')
    return {'id': child, **{field: ' '.join(dict.fromkeys(part[field] for part in parts if field in part))
                           for field in fields}}


def _specification(child, tests, manual):
    procedure_tests = [t for t in tests if t.split('[', 1)[0] in PROCEDURE_TESTS
                       or t.startswith('test_each_completed_points_or_shapes_gesture_is_one_undoable_transaction')]
    if procedure_tests:
        texts = [procedure_text(t) for t in procedure_tests]
        text = {'id': child, 'criteria': ' '.join(c for _, _, c in texts),
                'hooke_equivalent': "Edit > the annotation panel (Points and Shapes layer, napari mode, its buttons, Undo "
                                    "and Redo annotation edit) and pointer input and napari's keys on the preview"}
        if not (manual.get(child, {}).get('procedure') and manual.get(child, {}).get('oracle')):
            text['procedure'] = SETUP + ANNOTATE_SETUP + ' Then: ' + ' '.join(p for p, _, _ in texts)
            text['oracle'] = ' '.join(dict.fromkeys(o for _, o, _ in texts))
        return text
    if tests[0].startswith('test_points_and_shapes_editing_api_in_the_worker_is_napari_s['):
        recipe = tests[0].split('[', 1)[1].rstrip(']')
        return {'id': child,
                'procedure': SETUP + f"Then in the Studio's Python panel run recipe {recipe} of "
                                     "tests/scientific_annotation_api.py: it makes its own Points or Shapes layer beside "
                                     "the session's layers, does one call, napari mouse callback or napari action, and "
                                     "prints its result.",
                'oracle': API_ORACLE.format(test=tests[0]),
                'criteria': f"Recipe {recipe}'s result in the worker equals plain pinned napari's and its stated "
                            "expectation" + ('' if len(tests) == 1 else
                                             f" (and the mode recipe {tests[1].split('[', 1)[1].rstrip(']')}).") + '.',
                'hooke_equivalent': "The session's Python panel (napari's API on the session's viewer)"}
    text = {'id': child,
            'criteria': ' '.join(CRITERIA[t] for t in tests) +
                        f" Executed in an isolated profile against the actual worker and pinned napari by "
                        f"tests/test_scientific_annotation_browser.py::{', '.join(tests)}.",
            'hooke_equivalent': '; '.join(dict.fromkeys(TESTS[t][1] for t in tests))}
    if not (manual.get(child, {}).get('procedure') and manual.get(child, {}).get('oracle')):
        text['procedure'] = SETUP + ' Then: ' + ' '.join(TESTS[t][0] for t in tests)
        text['oracle'] = ORACLE.format(test=', '.join(tests))
    return text


def main():
    data = json.loads(MANUAL.read_text())
    manual = {c['id']: c for c in data['children']}
    kept = {ident: fields for ident, fields in data.get('specifications', {}).items() if not ident.startswith('P4.')}
    ours = [specification(child, tests, manual) for child, tests in checks.MAPPING.items()]
    data['specifications'] = {**kept, **{row['id']: {key: value for key, value in row.items() if key != 'id'} for row in ours}}
    MANUAL.write_text(json.dumps(data, indent=1, ensure_ascii=False) + '\n')
    print(f'{len(ours)} H07 specifications written')


if __name__ == '__main__':
    main()
