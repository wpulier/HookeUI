# Checks

`scientific_pick_units_probe.py` runs with a prepared integration's Python and `PYTHONPATH=services`.
It uses real napari ViewerModel/Labels without Qt, a canvas, GPU or worker: the retained MT6 mixed-unit
camera/frame-pixel replay must hit and fill the three synthetic stray pixels. Affine/axis-order/orientation,
stale-frame/grid refusal and pixel/incompatible-unit cases qualify the coordinate conversion only;
the actual ordinary-UI correction/reopen remains a separate acceptance step.

The shared `scientific_document_scope.mjs` browser helper discovers a canonical retained title (or an explicit
resource/placement), validates the exact content key, then pins that owner for later binds. Scientific display
headings may include role/revision context. Controls use the observed label and owned launcher within that
document subtree; a globally similar region is never a fallback. Missing, hidden, ambiguous or replaced owners
refuse. Its Node locator regressions are synthetic ownership checks, not browser/native qualification.

Part of the working context the root `AGENTS.md` maps. "Above" and "below" may mean parts it lists in
other files.

## Edit loop and release gate

The owner's October 23 direction removes redundant broad runs from development.
For each edit, select the affected regressions explicitly:

- `make test-python PY_TESTS="tests/test_name.py::case"` (paths relative to `services/`).
- `make test-studio STUDIO_TESTS="src/name.test.ts"` and `make typecheck` for Studio changes.
- `make sim-test SIM_ONLY=actions,correction` only for affected simulation behavior; quiet timing still needs the coordinated slot.
- `make native-test` when native update/restart behavior changes.

The two focused test targets refuse empty selectors. Documentation-only edits need
link/diff review, not Python, models, simulation or a new app build. Do not replace
ownership, retention, admission, recovery or update checks with a cosmetic smoke test.

`make check` remains the frozen integrated candidate gate: full Python, Studio types/
Vitest, native checks where available, and non-cloud simulations. `make check` clears
`SIM_ONLY` so an edit-loop selector cannot silently narrow this gate. `make test` remains
its full non-simulation stage. The `tests` workflow (`make-test`) runs on **every PR into
`dev`** and must pass before it merges, with no bypass; it also runs by dispatch on a frozen
candidate branch (record the resolved SHA). It never runs again on the merge commit: reuse an
unchanged passing run instead of rerunning it locally or after the merge. Repeat only
affected checks after a repair; broad shared-boundary changes can invalidate wider
evidence and require a new combined gate. Applicable actual capability, package and
owner acceptance remain distinct. No checks are silently marked passed by this policy.

This removes duplicate automatic full-suite invocations, not regression coverage.
Keep older cases when they protect current behavior; delete a case only with a named
replacement or evidence that its product contract has been retired. Parts 2–3 fixtures
may still protect shared Part 1 services, so their labels alone do not justify deletion.

`services/tests/test_golden.py` keeps what every model is told (the brain's
prompts, the fast pass, the voice's notes and recap, the tool list) and what the studio is sent,
byte for byte, for one fixed state (`services/tests/golden/`): a refactor leaves them unchanged,
and a change that is meant is rewritten with `UPDATE_GOLDEN=1` and named in its PR.
`make test` runs the Python tests with pytest-xdist (`SERIAL=1` for one process, `JOBS=` for how many).
Use the current receipt for runtime; the old six-minute estimate is obsolete. Keep
collection the same on every run (parametrize from lists or sorted ids, never a set's order) and keep each test well
under 20 s (split one by case or family). What a loaded Mac breaks rather than the code stays out of the gate
(`tests/conftest.py`): a bound on a measured time ("returns at once") is stated with the `wall_clock` fixture, and a
test that only works within a tiny limit (a child started in 0.15 s) is marked `timing`; `make test-timing` runs both,
one test at a time, with the bounds on. A test waiting for background work (a thread, a task, a queue's worker, an
Event) waits with `tests/waiting.py` (`until`, `until_async`, `HANG_S`): it returns as soon as the state is reached and
fails only past 60 s, a hang (the 1-6 s polls it replaced ran out at load 30-50). The app's background threads run at
normal priority in tests (conftest.py), since a loaded Mac starves macOS's background QoS.
The default catalog is isolated per test, in addition to the collection-time account
isolation. A test of deliberately shared profiles or a prepared runtime may override
`MICROMIND_CATALOG` in its fixture. Do not erase retention associations or raise
production registration limits to make a suite pass. `test_catalog_isolation.py`
checks successive profiles in one subprocess; retention tests cover intentional sharing.
Provider credentials and their app-applied origin marker are also isolated per test.
Cases may install synthetic credentials within their own scope; app-loaded values
must not authorize a later case. `test_credential_isolation.py` exercises that
boundary across successive tests in a fresh pytest process.

The historical readiness comparison explicitly observes optional SAM3 code as available;
its old oracle predates catalog requirements on roles such as outline repair. Separate
missing/present module-discovery fixtures assert the literal setup refusal and ready state
on both outline roles and their catalog cards. They install/import no model and never use
an undeclared package in a developer environment as evidence that a choice is ready.

`test_websocket_lifetime.py` interrupts the real websocket endpoint during its initial snapshot,
with cancellation and a failed send. Another live endpoint must retain its held view; its final
disconnect must clear it. These deterministic lifecycle probes complement the two-Studio TestClient
case; they do not establish the cause of an earlier uninstrumented CI failure or installed-app acceptance.

The `model-load` scenario retains its existing per-phase frame headers/receipt times, raw click times,
before/after bridge counts and orchestrator health samples in `Outcome.observations`, including failed gates.
These expose source sequence gaps, skipped/reconnect counters and analysis tails without extra polling,
JPEG retention or changed workloads/thresholds. They do not identify internal analysis substep cost or
the model subprocess's instantaneous thread count; those are not collected. `test_model_load_observations.py`
checks preservation with scripted transport and a virtual clock, not actual model/performance evidence.

The `media-load` scenario also retains baseline/media frame receipt and analysis
timestamps, chronological click round trips and the existing click-worker execution
timings. It adds no polling, image copies or changed thresholds. These witnesses
distinguish transport/analysis tails from model time when an affected run fails;
one passing isolated run does not erase retained failures or prove a runtime repair.

The maintenance and live-under-load scenarios retain compact frame timing observations after each existing
measurement: phase p50/p95/max, missing/invalid clock counts, and at most 16 worst frames per scenario.
`sim/frame_observations.py` derives intake, analysis, publish and websocket receipt intervals from the actual
`ba/t/an/sent` header fields and harness receive time. These are same-host wall-clock observer boundaries;
analysis includes executor waiting, and receipt is not browser paint. Missing bridge capture time stays unknown.
At most 24 windows are retained (first/baseline plus recent windows), omissions explicit, under 60 KiB.
No additional polling, JPEG retention, scheduling or threshold change is introduced.

The historical entry-digest check preserves the pre-format-2 Fiji contract hash separately
from its intentionally changed shared-operation declaration. Hosted edition counts derive
from the qualified declared capabilities, including that added Fiji operation. Researcher
input schema checks preserve the exact retained-picture branch beside native-array inputs;
no synthetic qualification or relaxed resource admission is implied.

The capability matrix's saved-table rows use the shared make/edit/analyze/graph/read
tools over retained synthetic population measurements, through both chat and voice.
They preserve exact source and table versions and frozen results. The Tab row deliberately
has no originating Studio and must refuse; actual Tab mutation and acknowledgement remain
covered by their separate ownership and browser checks. These rows run no real model.

`test_population_method_lineage.py` drives synthetic model transport through real saved-population retention,
public tool selection, accepted ResourceEdits, shared provenance and MT6 storage. It checks the exact original
image→Labels association, intermediate tool-selection revision, manual history, export/reimport, and explicit
legacy/unrelated/stale/cross-Project/tamper refusals. A large synthetic producer transported through public
retention must remain complete across comparison/export/reimport via its exact bounded attribution reference;
callback-context limits remain enforced. Imported corrupt metadata is a labeled low-level storage
fixture, not a valid ordinary acceptance. No native runtime, ordinary UI or scientific qualification is inferred.

The same public tool-selection/output/provenance seam checks saved tool display attribution:
the exact recorded name survives output publication, while changed runtime, source owner or
selection name cannot borrow a label. Studio tests match the returned runtime/source to the
visible saved view and keep legacy comparison limitations immutable while clarifying their interval.

`test_method_tool_lineage.py` follows public tool selection, native output publication and
MT6 retention/export/reimport with synthetic prepared-member and worker transport boundaries.
It distinguishes a verified selection of the frozen input from later changed inputs/runtime,
untrusted provenance, a different selected revision and absent output-coordinate declaration.
A low-level imported output with valid native references but a forged input fingerprint must
refuse at comparison admission. The original native revision and worker call count remain exact. These checks run no algorithm,
native viewer or ordinary UI and grant no independent scientific qualification.

`test_method_evidence.py` uses real Project storage, output publication, correction history, runtime retention
and bundle reimport with synthetic pixels, manual actions and worker transport. It verifies exact input/calibration
binding, failed/interrupted arms without fake masks, immutable choices, bounded failure and metadata-only retention.
These checks neither execute the recorded Python nor establish a Studio method-comparison journey or full MT6.

`test_scientific_bundle_owners.py` exercises real SessionStore/catalogue, native retained storage and the bundle HTTP
routes with synthetic work. Separate processes exit at publication, commit, cleanup and rollback boundaries; restart
must restore the pre-import state or retain a committed import without changing exact revisions. Other checks cover
same-clock origin collisions, complete multi-owner closure, legacy data-only export, byte-exact native values,
retry idempotence, failed rollback staging, concurrent Project creation and explicit unavailable catalogue responses.
The runtime-retention reader must refuse promptly while publication owns visibility, not deadlock. Input counts hold
visibility across cached and uncached scans; pending reads cannot become zero counts. Actual asyncio-loop checks hold
an HTTP owner import while an admitted journal durably appends, and require New/Open/uncommitted-journal access to
refuse without blocking or changing active identity. Replaced/missing journal admission and memory-only fallback are
also checked. Held-namespace reconstruction tests require durable original-owner turns and tracks while unrelated
creation proceeds, including established journals with torn tails; partial initial journals must still refuse.
Preferences retain their independent journal format. Annotation disk-full injection reaches the admitted descriptor's
actual write and verifies no saved note or annotation. These tests execute no scientific host, browser or model; actual ordinary import/reopen remains separate.

`python -m bench.scientific_acceptance --list` lists the executable R01–R09
components and each mapping's remaining obligations. `--case ID --output NEW_DIR`
records actual JUnit and source hashes. These synthetic component receipts cannot
qualify a UI journey, native platform, independent integration or complete program;
`--require-complete` continues to refuse closure while those obligations are open.

Isolated-profile browser checks (H04 first, `test_scientific_dims_browser.py`) run the Studio as a person does:
`scientific_studio_stack.py` starts a private orchestrator with an empty home, no camera and the Studio rebuilt from
the checkout (its build identity must equal the backend's), `browser_cdp.py` drives a fresh Chromium profile with
trusted input events (page script only reads and finds controls, or moves keyboard focus as Tab would), and
`scientific_studio_session.py` operates the opened session. Each result is compared with the same steps in a plain
pinned-napari Viewer process on the prepared runtime's own Python (`scientific_napari_reference.py`, sharing one
snapshot source with the session's Python panel) and with independent values from the fixture's definition. They need
`NAPARI_NATIVE_HOST_PREPARATION` and `HOOKE_BROWSER`, skip without them (so `make test` never runs them), and use a fresh
stack, review session and browser profile per case. `xdist_group` serializes these cases (the acceptance runner
distributes with `--dist loadgroup`); it does not share their bounded Python histories. Opening the
scientific host is a heavy run: take the slot first.
The Chromium constructor owns partial startup: spawn, endpoint-read or connection failure reaps any child and
removes its private profile; cleanup errors stay attached to the original failure.
The owned browser's websocket close handshake is capped at one second: unread DevTools replies may fill its receive
queue and prevent a graceful handshake. Process termination, bounded wait/kill/reap and private-profile removal
still run even when socket close fails; this changes only test teardown, never scientific or UI timing limits.
Playback runs one case per test, with defaults and invalid-range checks separate. All nine exact case nodes remain mandatory in the H04 mapping;
the three 2fps cases with sub-250ms first-frame assertions carry `timing` and need the quiet timing lane. Skipping
those cases cannot qualify playback. Per-case native/browser durations still require the coordinated run.

P4 annotation (H07, `test_scientific_annotation_browser.py`) uses the same stack, browser and session with its own
prepared integration, `NAPARI_ANNOTATION_PREPARATION`, whose factory opens `instance-labels` inputs as editable Labels.
Its reference (`scientific_annotation_reference.py`) drives napari's own mouse callbacks at the world positions
napari's `canvas_to_world` gives for the frame pixels the Studio actually sent (read from the browser's network log),
and replays the Studio's Points and Shapes requests as real vispy events on napari's own canvas, paced past napari's 5 ms
mouse-move throttle. After matching the camera/canvas, the reference draws one native frame before interaction,
so screen-scale-dependent editing (including Shapes' initial vertex radius) uses the displayed zoom rather than
its startup scale. This preserves exact geometry comparisons; it does not force radii or loosen tolerances. API recipes (`scientific_annotation_api.py`) run verbatim in the worker's Python panel and in the
reference, each with an independent expectation; every key the tests press must also be napari's own default binding
for the action it ran. `test_scientific_annotate_worker.py` checks the same protocol on the actual worker without a
browser.
The Polyline double-click checks distinguish a counted second mouse click from two consecutive clicks while
the first request may still be processing; both must finish the exact three-vertex native path.
Focus, touch cancellation, Escape and disconnect each own a fresh session, assert a gesture actually started,
then use the visible Refresh preview recovery before further editing. A revoked paint cannot certify a touch
cancellation that never started. All four cases remain required by the original conflict acceptance children.
H07 also uses fresh per-case stacks, sessions and profiles, including one API recipe per session. Points/Shapes
fixture setup explicitly imports NumPy in its own console before creating arrays; no preceding journey supplies globals.
The annotation idle observer has a synthetic JavaScript regression for ready, unsent, busy, disabled and missing-panel
states; parsing or readiness alone does not qualify native editing.
The add/move/delete Points journey independently checks all three surviving coordinates in order after deleting
the first original point, in addition to its exact pinned-napari replay comparison.
Paint/erase's three
brush sizes and fill/preservation's four cases are separate mandatory parameter nodes; their exact native and
independent expectations remain required. Ticket 53 records the coordinated native timings; longer sequential
journeys still need further splitting if they exceed the test-duration rule. The three-gesture history journey uses
two mandatory fresh-session cases: three ordered Undo snapshots, and three ordered Redo snapshots after three Undos
restore the exact original. Both compare the same pinned reference history; neither needs post-assertion cleanup. Save/reopen follows
Open saved revision, closes the old stopped window and requires the observed creation request to target that exact
accepted revision with a new review ID. Python observations belong to the visible console, not a hidden old window.
Trusted mouse clicks require checkVisibility, enabled state and a hit-tested intended control (or descendant) at
the actual coordinate, including offsets. A layout box alone does not prove visibility inside closed details. The
harness explicitly opens Session through its visible summary before Stop; it never auto-opens arbitrary details.
Keyboard focus is separate: accepted-revision reopening closes the exact old window with a visible, connected,
enabled title-bar Close control and trusted Enter, avoiding a mouse click through an overlapping new window.
Every H07 mapping requires opening, the approved GUI/runtime guard and the deliberate broken-worker negative.
The common observer runs through the session's existing Python panel and in the independent Cocoa GUI reference.
Every reference report validates the approved archive and generated version wheel, runtime/dependency/source hashes
and actual full GUI settings, with Hooke's declared offscreen/Cocoa platform difference. Unknown or mismatching
profiles refuse; there is no model-only fallback. `NAPARI_ARCHIVE` and `NAPARI_REFERENCE_WHEEL` name the approved
local artifacts. The synthetic annotation adapter and retained Labels fixture cannot certify general scientific
intake, real segmentation correction or the full installed workflow. Native mutable-history atom compatibility,
browser behavior and observed per-case durations remain separate requirements.
`test_scientific_annotation_history.py` also checks tool changes between edits and between Undo/Redo:
selection-only tool changes create no content transaction, preserve both adjacent valid endpoints, and cannot
reauthorize stale content or selection. Native content changed by a tool action still produces an edit.
`test_scientific_annotation_history.py` and `test_scientific_labels_history.py`
inject synthetic native callback failures to check stale Undo/Redo refusal,
complete data/history rollback and fail-closed restoration. Studio component
regressions defer transport responses across failure, layer/session replacement
and close. These fixtures do not qualify native editing, Save/reopen or P4.
Synthetic Labels-history and layer-export cases share `scientific_threads.py` to
exclude threads already owned by pytest/xdist before a case starts. Threads the
case starts still reach the real quiescence refusal; explicit background-work
regressions verify refusal, unchanged data/history and recovery after joining.
The production worker's thread guard is unchanged and native tests do not use
this fixture. Synthetic catalog suppliers must declare real input ports when
testing cards, including each active generation's own input contract.

The capability matrix (`services/tests/test_capability_matrix.py`, the user's Q13 and Q20) runs every capability of
`contract/capabilities.json`, by every actor that can ask for it (chat through `Brain.turn`, the voice's hand-off through
the spoken-request handling, a researcher through its hands' `run`), with every model option of its roles, through the
real app (`App.build`) over stand-in workers and scripted models that answer at once (`tests/capability_rig.py`,
`tests/standin_workers.py`): each row reaches the model it names (the worker op and model asked, the cloud call, the
model a tool asked, what the research process was asked to start), its name resolves through `names.py`, and a choice
that can't run is refused saying why (needs a key, not installed here, not in this edition, done one way) with nothing
run in its place. A capability × actor with no plan fails; a role a path does not reach is declared with why (a
researcher's model is the one it started with; the follower follows on frames, not in a request). Each half stays
under 90 s (a wall-clock bound, checked by `make test-timing`; split by family past that, never trim rows). The real
models are `make capability-check` (above).
The acting bench (`services/bench/acting/`, in the Python tests; `make acting-bench` prints its
table) plays requests through the real brain, spoken-request handling and tools over a stand-in
slide, GPU and models, and scores the board at the end, every "done" the user was told
against the board at that moment (it must never come first), and how soon each thing
happened. The live failures of 2026-10-04 are cases in it; a case marked `xfail` says which PR
fixes it (`docs/design/2026-10-05-acting-layer.md`, ADR 0006). Cases marked `beginner` are also scanned
for words the user never used (model names, ids, the app's machinery): a reported column, never a failure.
With `--models real` it runs the live cases with the real models, in real time, on real pictures whose things
are labelled boxes: settings compared as `--arm`s that take turns, 3 attempts a case, every attempt and prompt kept, a
frozen hash of the cases and scoring, `--report` for the table (`docs/bench/acting.md`). Defaults that change how a
request is answered are chosen there. The maintainers' live cases are their own words and pictures from live
sessions, kept privately: the run takes `live_cases.json` beside `run.py` (ignored by Git) or `--cases FILE`, and
without either says so and skips.
The voice journeys bench (`services/bench/voice_journeys/`, `docs/bench/voice-journeys.md`) measures the live
partner against its acceptance standard for both voices (GPT-Live and the one brain, hold or open): one command
(`bench.voice_journeys.run`) reserves the worst case in the budget, starts an isolated second stack, plays a plan's
recorded words in a real Chromium N times and writes `result.json` and a pass/FAIL scoreboard; `compare` puts the two
voices side by side; `acoustic` replays through the speakers into the microphone. Its scoring and runner are in the
Python tests with synthetic event logs that pass and fail each line (`test_voice_journeys_standard.py`,
`test_voice_journeys_run.py`); the paid runs are the coordinator's.

The testing reference for the end of an effort is the maintainers' Human Interaction Testing Playbook, kept
privately. In short: agents test as the user does, the live app by voice and typing in the user's casual wording,
comparing at each step what was asked, what the workspace shows and what the records prove, then fixing and
retesting. It is a tester's document: never put it into the app's prompts. When and how: the partner plan's "The end
of the effort" (`docs/design/2026-10-07-the-partner.md`).
The user's live cases of 2026-10-07 are crucial acceptance cases there (the partner plan's §5 lists them).
The Part 2.5 journey (`docs/testing/part-2.5-journey.md`) is the public form of that check for one Project across
voice, the manual UI, a researcher and a connected agent; its cases from the 2026-10-28 session are on the benches.

Isolated-profile browser checks (`tests/scientific_browser.py`) build the production Studio from the tree when its
build is stale, start the real orchestrator on a fresh home (own assets, empty keys file, no camera, no keychain,
`--no-gpu`), save the fixture documents and drive a real Chromium in a fresh profile, one JSON line per step
(`scientific_browser_driver.mjs`). They need NAPARI_HOST_PREPARATION, HOOKE_CHROMIUM and HOOKE_PLAYWRIGHT and skip
without them; the acceptance runner then reports their children missing. `tests/scientific_parity.py` runs the same
behaviour in Hooke's actual review worker and in plain pinned napari (NAPARI_REFERENCE_PYTHON) and maps data pixels
to canvas pixels for renders. The independent reference uses unmodified Cocoa GUI in its own home/configuration,
with an owned process, deadline and memory bound; it does not import Hooke's CGL backend or widen the production
worker sandbox. H06a/b compare the common observed runtime/source/wheel and GUI profile through the existing worker
or Studio console before behavioral assertions. Every mapped child requires the approved-runtime check, source
archive and wheel. Shared-backend receipts remain historical. Cocoa pixel comparisons retain their original
tolerances and remain unverified until run; a matching runtime profile alone does not prove rendering parity.
Both are heavy runs: take the slot.
H06's controlled `napari-gui-physical-pixels-typography-v2` profile is a separate pixel prerequisite beside the unchanged
common GUI/runtime profile. Its independent Cocoa process uses Qt's testing setting `QT_SCALE_FACTOR=0.5` on the
observed native-2x host and refuses unless actual Qt/VisPy ratios are exactly 1. The owned native window is shown
through public QWidget methods; its canvas has an explicit fixed size so the parent layout cannot expand it.
Events/layout complete, and the recipe's exact camera is restored after resize;
napari settings must remain unchanged. Before/after captures require matching logical/physical/requested sizes,
unchanged camera/dims and the original uint8 output/tolerances. Worker/Studio console observation checks its
actual pixel profile; returned worker frame dimensions and pixel ratio are checked too. The helper is source-pinned,
and raw reference reports persist before admission. Reference typography sets Qt's `AA_Use96Dpi` before its
QApplication exists; an existing app refuses. Both sides observe the upstream-resolved 256-point glyph font's
actual family, inherited OpenType features, point/pixel size and metrics. Tabular numerals must be initialized,
point-to-pixel resolution must match controlled96DPI, and the font observations must agree exactly. Captures
refuse a changed typography profile. This neither rewrites VisPy DPI nor changes font family, camera, images or
tolerances. The helper remains source-pinned; the common GUI/runtime observer is unchanged. Default Cocoa72DPI
and offscreen96DPI glyph rasterization differ and remain outside this controlled profile's qualification.
Default hidden Cocoa captures were uniformly black; showing
that viewer exposed a separate DPR-2/1 geometry mismatch. Those failures remain evidence, not passes. Controlled
1x native comparisons, arbitrary-DPR/platform rendering and complete H06 acceptance remain open.

H04's reference subprocess validates the approved source archive for every report.
Generated napari version code is checked against bytes of the exact approved local
wheel (`NAPARI_REFERENCE_WHEEL`); no download is performed. Each mapped browser
child includes the opening/reference guard and deliberate broken-worker check.
The shared acceptance runner must require every mapped prerequisite before credit.
Playback browser evidence collects `scientific-frame-drawn` observations emitted
at the existing canvas receipt, separately from requested/fetched images; the event
never grants interaction authority. Synthetic refusal/cleanup tests do not qualify
native comparisons, browser journeys or installed apps.

P2 names its Playwright/Chromium JSON file with `HOOKE_BROWSER_CONFIG`; H04/H07 keep `HOOKE_BROWSER` for the
Chromium executable. Both can be configured in one acceptance process without treating an executable as JSON.
P2 browser evidence requires each child's own case, the approved runtime guard for both sides of every document
(including the deliberately broken worker), and the actual attenuation negative. An early UI failure or absent
negative observation is not a successful negative control. Runtime observations use the existing session console;
reference replay uses the same pinned source list. Acceptance explicitly pins replay/driver/comparison/profile
helpers as well as the complete source-tree identity. These guards do not themselves qualify a browser or native run.

The P2 retained surface uses outward triangle winding. Its browser sequence frames
the mesh and normals through the ordinary camera angle/zoom controls, then shows
and hides contrasting normal baselines through the owned controls. Setup occurs
after the initial checkpoint and remains in the captured/replayed operation list.
The old inward-wound, tightly cropped, black-normal fixture is retained as failed
diagnostic evidence: native state equality cannot substitute for visible changes.
Every existing visibility/length/width/color assertion and the executed negative
remain required. An unsealed fixture probe is not a canonical browser pass.

For an explicitly coordinated quiet scientific acceptance run, use from `services`:
`python -m bench.scientific_acceptance --quiet-timing --jobs 1 --archive /path/to/napari-main.zip --output /private/new-report`.
This disables pytest-xdist and adds `--timing`, enabling `wall_clock` fixture bounds.
The normal default remains four workers without `--timing`: unlike `make test`, the
acceptance runner does not deselect timing-marked cases, so their direct assertions
already execute, while `wall_clock` bounds remain inactive. The quiet flag requires
one job and a new registered-check execution; it cannot relabel `--from` evidence,
`--no-run`, a legacy component, a listing or ledger-only validation. Both jobs and
quiet mode are included in the sealed execution receipt. Opting in does not measure
or establish that the machine is idle: reserve the coordinator's quiet/native slot
first. Every mapped case, source/runtime admission, seal and the existing 1,800-second
timeout remains required and unchanged.

P2 always raises its target through Project Data and binds the exact scientific document's retained placement plus
its matching title/revision inspector dock. Controls, console history, status and preview pixels stay in that scope;
an older restored window is never a first-match fallback. Missing or ambiguous owners fail; clicks are not forced.
For coordinated native runs, set `--basetemp` to a fresh directory under the run's ignored `.scratch` evidence folder
so pytest retention does not remove the shared driver/replay JSON and profiles. Keep failed logs and JUnit alongside it.
P7 call comparisons reuse the H00 actual session and independent GUI reference.
Every call child requires the shared runtime/profile/retained-volume guards plus
an actual-worker `writer-values` negative: a successful Layer.save with corrupted
values must retain a matching successful API return and differ in nonempty file digests
and readback of the known Points values. Exceptions, empty output or a different
return do not satisfy that negative. `scientific_call_probe.py` can run
one call plus that negative in the coordinated native slot; its diagnostic JSON
explicitly grants no mapped completion. It does not replace the canonical complete
comparison run, normal-UI SDK journeys or independent held-out qualification.

The call diagnostic keeps a private, atomically updated receipt at each stage,
including available observations and failure details before re-raising. A retry
uses a fresh directory; previous receipts are never overwritten. An incomplete
or failed receipt grants no mapped completion, just as a passing diagnostic does not.

Bounded acceptance batches use repeatable `--child ID` or `--harness NAME` with
`python -m bench.scientific_acceptance`. Trusted groups are `h04`, `h07`,
`compare-event`, `compare-attribute`, `compare-enum`, `compare-contribution`,
`browser-p2`, and `remaining` (all registrations outside those groups). Each child
executes its full registered test set, including runtime/negative guards; whole-file
registrations remain whole-file runs. Unselected children stay in the full inventory
as missing. Split a large group by registered child IDs rather than raising the
1,800-second process deadline. Quiet/native slot coordination still applies.

Freeze source before running the batches. Repeat `--from ORIGINAL_REPORT_DIR` to
aggregate their locally sealed executions; a single `--from` still reuses one
report. Every original must remain available, from this checkout and unchanged
source, with matching JUnit and runtime configuration file hashes. Failed, skipped,
timed-out, stale or edited input runs refuse aggregation. A child's complete guards
must pass within one original run; they cannot be assembled from partial runs.
Consistent duplicate observations retain both provenances without extra credit;
conflicting runtimes, duplicate input directories and nested aggregates refuse.
Each new execution/output uses an empty directory. Retain failed attempts separately.
The runner sets pytest's `--basetemp` to that execution's fresh `checks/temporary`
directory, retaining raw driver/replay artifacts and profiles beside its JUnit and
log. Never reuse an execution directory: pytest may clear a supplied base directory.

The aggregate is itself sealed, and reuse (including the completion meter) rechecks
all originals. Raw JUnit and older execution-contract receipts cannot be imported
as current evidence. Runtime configuration hashes bind supplied files; the harness's
actual runtime/profile checks still prove package identity. Selection and aggregation
never alter evidence classes, the denominator or external qualification gates.

P2 transfers large observations through bounded, digest-checked chunks in that same
Python console. Initial layer tuples remain opaque JSON text until reference Python
decodes them: JavaScript must not erase float spellings or round native integers.
The independent reference uses a Cocoa GUI Viewer, the observed canvas size and
draw callbacks after public setters, with the common GUI profile at three displayed
dimensions. Every owned session is stopped through its scoped UI control before
closing the browser; failed cleanup stops the sequence and cannot earn credit.
Cleanup opens that document's collapsed Session summary with an ordinary click
before Stop. Locator polling is bounded separately from the overall wait deadline;
missing controls cannot multiply the nominal wait by the locator default timeout.
The actual attenuation negative, exact state/array comparisons and production
console/memory limits remain unchanged. Fixture regressions do not qualify the
new GUI replay or native camera behavior.

`test_scientific_measurement_capability.py` uses synthetic Labels in real accepted Project storage through the
production HTTP endpoint and Registry handler, in both caller orders. It checks descriptor/readiness agreement,
exact native IDs, original producer versus retry invocation, concurrent CAS, cancellation, memory and original
request/session refusal. The corresponding Studio component suite binds the declared operation and accepted input.
These tests do not qualify the ordinary installed workflow, actual Cellpose/Fiji, export/reopen or external agents.

P2 appearance replay records the actual submitted Image/Surface transaction and
binds its layer ID to the successful response's expected name/type. The independent
reference applies all submitted fields, including unchanged defaults, through
napari public setters in transaction order. Replaying only touched controls can
leave different native scalar representations after ordinary JSON submission.
Snapshots retain their original canonical encoding, array dtype and integer checks;
the attenuation negative requires the actual attenuation difference, not an
unrelated state mismatch. Raw submitted appearance transactions remain in driver
receipts for review. No product-setting conversion is added by the harness.

P2 appearance replay captures every owned form value before Apply, including
unchanged hidden fields, and requires exact agreement with the submitted payload.
A product serialization error cannot become the reference expectation. Responses
are scoped to the review opened for the retained document/session/revision; layer
identity is checked again before independent public-setter replay. This form
capture uses the UI's JavaScript number representation; native observation strings,
array dtypes and large integer values retain the existing exact comparison.

`test_population_native.py` uses synthetic model/Fiji subprocess transport with real retained evidence, shared
HTTP/Registry admission, native array/Project persistence and the real generation-removal guard. It checks corrected
instance IDs → shared typed measurement without another producer call, stale/cancelled/unsupported refusal, bounded
NPZ preflight, correction/publication locking and concurrent/lost-ack retry. This is not an actual Fiji-package,
ordinary-browser or independent-user qualification. `population-native.test.ts` covers component selection, receipt
and stale-response behavior; the ordinary UI journey still needs the coordinated runtime lane.

`test_method_workflow.py` checks the declared retained-method operation over real Project storage and synthetic
calibrated masks: exact selection, bounded summary, shared HTTP/Registry admission, missing/failed evidence,
concurrent retry, cancellation/session changes at publication, choice CAS and native bundle reopen. Existing
method-evidence and aggregate tests remain authoritative neighbors. `method-comparison.test.ts` exercises
ordinary component selection, Review/Retain, rationale, stale ownership and request-preserving recovery with
synthetic DOM/HTTP responses. These checks do not establish actual browser, model or scientific qualification.

`test_scientific_reader_proof.py` covers reader proof authority and exact identity,
private worker observation, all-level/aggregate admission, mismatch refusal and
post-promotion rollback with a retained Project. Its lifecycle worker and installer
are stand-ins; the immutable intake, proof seal/store, preparation admission and
maintenance code are real. A closed-bootstrap subprocess observes synthetic arrays
without Qt. These tests do not qualify the actual BDV package, native reader UI,
Huygens data or an independent scientific reference.

`test_animation_slider.py` uses `NAPARI_ANIMATION_HOST_PREPARATION` only in the
coordinated native lane. Each candidate opens its checkout adapter with the actual
pinned Qt widget in a fresh prepared-Python subprocess and private home; no runtime
is installed or modified. Synthetic 32×32 pixels and camera endpoints are explicit.
Capture/Steps/list controls and public slider values exercise actual callbacks,
independent endpoint/intermediate zooms, frame count, unchanged inputs/keyframes
and a caller's pre-existing signal block. This is neither browser keyboard-focus
acceptance nor movie/export qualification. Without the preparation it skips and
provides no native credit; persistent `--basetemp` retains failed observations.

The LayerList comparison recipes use the review session's own `viewer.layers`,
observe exact object relationships/native values and mutation events, and restore
original objects, selection, dimensions and camera after each recipe. The two
mandatory actual-worker negatives return a wrong name lookup and drop an integer
replacement; a crash is not evidence that a successful wrong result is detected.
`test_scientific_layerlist_comparison.py` checks admission with synthetic records;
only the prepared Hooke/reference run can earn those worker-API child results.
Neither path substitutes for ordinary layer controls or explicit Save/reopen.

Collection native state and event payloads are normalized at capture, before they
are nested in case reports. The final observation preserves those JSON leaves;
reapplying the shared depth-limited normalizer would discard native pixels,
dtypes, shapes and digests. Synthetic final-observation regressions require each
of those differences to survive serialization for both collection obligations.
The shared normalization depth/numeric rules and actual-runtime gates are unchanged.

`test_workspace_completion_save.py` uses real journals with synthetic resource references to distinguish
server completion-only additions from conflicting user layouts, removals, opaque/tampered revision
chains, incompatible schema/capacity and lost replies. Studio completion-save component checks retain
newer edits, exact additions and recovery across delayed/failed restoration and Project changes.
These focused checks do not establish an ordinary Fiji restart journey or installed-app acceptance.

`test_population_history_overlay.py` drives public correction and overlay reads on synthetic Three
particles with a stand-in Fiji process. It verifies original/current IDs, contours and measures after
3→2→3→2, original replay after restart, unchanged accepted history, unavailable intermediate/damaged
snapshots, and held-file-read refusal on correction, Project switch, object replacement or cancellation.
This real-storage check does not replace the actual Fiji/browser historical-mask walkthrough.

BDV reader tests include both HDF5 byte orders and signed/unsigned 16-bit storage,
using real selected-folder grants. Full, regional and scalar reads must retain
unsigned BDV values and exact byte order. A private prepared h5py probe separately
checks the pinned runtime; neither establishes a native reader UI or real Huygens
export qualification.

`test_native_tool_continuation.py` uses a synthetic producer with real correction,
native retention, measurement, tool binding and Project storage. It checks unchanged
originals/values/calibration/ancestry, one producer call, exact shared HTTP admission,
retry and stale/foreign/unaccepted inputs. Synthetic Studio coverage reaches the
same discovery control without starting a worker. Actual animation remains separate.

`test_bdv_native_save.py` uses the unmodified public BDV adapter on genuinely single-level HDF5 through
selected-folder grants and the shared native describe/capture path. Exact unsigned pixels and calibration,
aggregate decoded limits before pixel reads, variable-length refusal, failed-read cleanup and unchanged
lazy pyramid behavior use synthetic viewer/container boundaries; these are not native UI or MT5 qualification.

`bdv_layer_protocol.py` is an explicit prepared-runtime probe, not a default pytest test. With the pinned
napari/h5py Python it constructs real HDF5 levels and calls upstream `assert_protocol` and `MultiScaleData`,
refusing pixel reads/materialization during metadata admission, then checking one exact big-endian region.
It launches no viewer or Qt application and does not grant reader proof, pyramid Save or MT5 qualification.

`test_ome_zarr_labels.py` exercises real directory intake and SDK grants with the
public OME-Zarr adapter and synthetic upstream/viewer seams: an explicit standalone
NGFF label root preserves the 40×60, 200-pixel rectangle and .5/.25 µm calibration;
integer Images stay Images and unsupported metadata refuses before upstream construction.
`test_reader_label_proof.py` checks typed observations, exact image/Labels distinction,
nonnegative integer/single-level limits and the parent-issued proof/first-check boundary
with stand-in workers. Neither suite qualifies the actual package or ordinary UI editing.

Release-source checks use an isolated local Git remote to prove Beta eligibility on dev, Stable eligibility only
after main promotion, refresh under narrow fetch mappings, and refusal of deleted remote branches despite stale
tracking refs. Prepare/publish refusal tests make no signing or public writes. They do not qualify a release artifact.

`test_native_picture_inputs.py` uses tiny synthetic native pixels through ordinary
command/workspace admission, the real CPU Bright objects implementation, Population
retention, comparison provenance and export/reimport. It checks exact native identity,
nonunit calibration, settings and unchanged source; wrong owner/revision/dtype or
volume refuse. Reusing one result for two test arms checks lineage only, not actual
two-algorithm scientific or browser/native acceptance.

`test_app_owned_capabilities.py` verifies the Fiji declaration, unchanged adapter
identity and unique listing, missing-proof refusal, and the ordinary native-input
command with synthetic Fiji transport. Actual source pixels, fixed declared defaults
and refusal of an undeclared setting are checked both with stand-in GPU workers and
without any GPU worker, preserving analysis-lane admission; no JVM or upstream Fiji run is
claimed. Real algorithm and packaged qualification remain separate.


`test_scientific_ask.py` uses tiny native arrays, real input admission/Ticket/Brain/Agent paths and synthetic provider
boundaries to check exact capture, ownership/digest refusal, original PNG in json-actions and FC modes, default-fast attachment routing and journal readback, truthful
retained-source claims, cleanup after failure and refusal of unsupported live effects. It makes no model call and
is not ordinary UI, researcher equivalence or physical-camera acceptance.

`test_image_transport.py` checks actual tiny encoded PNG/JPEG bytes at synthetic Gemini/OpenAI SDK boundaries
in both vision and function-calling modes. It establishes payload MIME/byte preservation, not provider acceptance.

`test_scientific_view_export.py` uses tiny synthetic native frames and real PNG/Project-file retention to check exact
region pixels, preserved exports, failed readback, fixed-generation replay requirements and stale/foreign/unsaved
capture refusal. It does not qualify a real renderer, QuPath fix, packaged UI or scientific accuracy; ordinary native
Export-tab acceptance is separate. The Studio companion checks explicit region and pending-fix/false-success UI states.

`test_datatables.py` covers concurrent table allocation, immutable snapshot collision, Project replacement/reload
at the write barrier, reserved IDs across reload, column unit/type refusal and worker-thread transforms.
These synthetic store/HTTP checks do not establish table UI usability or large-table latency.

`test_analysis_math.py` checks synthetic explicit-group analyses against SciPy and independent Welch/Dunn formulas,
paired-key association, scientific units/exclusions and finite JSON. It does not qualify user datasets or statistical
test selection; that choice remains explicit.

`test_table_derivatives.py` uses synthetic table inputs with real journals/snapshots to check exact source hashes,
Freeze/follow/reopen, failed refresh preservation, concurrent IDs, changed owners and damaged/missing saved files.
It establishes store behavior, separately from actual table/analysis/graph UI acceptance.

`test_workspace_research_output.py` checks synthetic retained research through the real journal: new research
kept and never placed (idempotent, each version, no new revision), local-draft merge across kept and placed
completions, an earlier output Tab kept readable with its refusal replaced once, a stale-base save still merging
an earlier output-Tab placement around a kept row, legacy delivery and persisted new mode. Its manager
transport/snapshot are inert; it claims no researcher/model or ordinary-UI acceptance.

`test_comparison_datatables.py` uses synthetic population metadata/rows with real workspace journals and table
publication to check exact comparison selection, Method values, warning/reopen and stale/foreign/input/method
refusals. It does not establish an actual A/B/C UI workflow or scientific independence.

`test_workflows.py` covers synthetic exact references with real Project storage, frozen definitions, sequential batches, named inputs, actor continuation, review/correction, uncertain recovery and shared HTTP/researcher admission. `test_workflow_entrances.py` runs one saved definition on one retained input from the brain's `workflow` tool, the studio's command route, a researcher's hands and the door's Study operation, through the real steps, and checks the same definition, input and result in the one history (and asserts the gap that a studio-saved workflow is not reachable from the door). The capability matrix and voice/chat parity checks discover the actual catalog descriptors and save a frozen definition through the shared registry, with scripted provider transport; those rows execute no scientific model. Managed report execution is explicitly unavailable in standalone core; private provider-deadline qualification remains in its service repository. Workflow Studio tests use synthetic forms/transport. None establishes actual external-agent interoperability, full-size fitting, packaged UI or biological accuracy.

`test_study_workflows.py`, `test_study_door.py` and `test_study_local_researcher.py` cover retained
Study/operation identities, lost admission without replay, stop/restart observation, exact evidence,
connection attribution, Project-switch refusal, original-attempt accounting and late usage receipts.
Local harness tests use synthetic manager receipts and do not call a model provider. Workflow tests
include actual tiny numerical computation where the pinned scientific runtime is prepared; that
is distinct from official benchmark scoring, full-size matrices, biological accuracy, browser use,
two actual agent families and the hosted runtime assembled from its accepted core pin.

`test_matrix_research_capabilities.py`, `test_matrix_capability_retention.py` and
`test_cell_evaluation_capability.py` use tiny synthetic matrices and actual retained files.
Numerical tests use optional installed AnnData/upstream evaluation libraries or explicitly
skip their unavailable cases. Default retention tests substitute only runtime admission;
`HOOKE_MATRIX_TEST_CATALOG` selects an actually prepared isolated catalog and its confined
interpreter instead. Neither establishes full-size inference, biological accuracy, sealed
labels, actual agent reasoning or competition acceptance. See
`docs/architecture/scientific-matrices.md` for the scientific and execution boundaries.
`test_aggregate_profiles.py` checks weighted-mean equivalence to explicit cells,
normalization independent of selected output genes, explicit coverage, legacy
categorical axes, merge compatibility and a prepared-runtime confined operation chain.
The evaluator tests refuse malformed sparse pointer layouts and oversized categorical
or string metadata before upstream decoding. These are synthetic fixtures, not source
data conversion or a biological validation result.

Workspace arrangement geometry checks inject only the server observation clock;
separate before/after-deadline tests preserve confirmation policy. Scientific stack
cleanup tests replace their module clock, never the shared Python time module, so
unrelated writer threads keep their own clocks. Reservation tests exercise queued
ownership and cancellation without asserting scheduling speed.

`test_agent_transport.py` compiles the shared Swift transport and exercises real
loopback HTTP servers with synthetic credentials: redirects never reach another
endpoint in either JSON or streamed-download mode, and direct responses still work.
This is transport qualification, not real Keychain access or a connected-agent journey.

`test_matrix_assembly.py` checks tiny AnnData count assembly, unchanged sources, exact identities/values and
annotation/precision/resource refusals. `test_count_effect_conversion.py` uses analytical compositions to check
pseudocount inversion, normalization subsets, explicit negative projection and legacy interpretation. The optional
prepared-runtime retention probe runs the actual confined concatenation operation with synthetic count inputs;
none establishes full-shape packaging, generator distribution quality or competition performance.
