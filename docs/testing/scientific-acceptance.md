# Acceptance contract and complete case crosswalk

Read the execution brief and relevant work package before implementing a case. This document defines how to prove completion. It does not claim any case passed.

## 1. Exhaustiveness comes from the pinned reference

The existing 71 cases are grouped requirements, not an exhaustive list of atomic interactions. Preserve every group and expand it. Audit the supplied pinned napari source and pinned dependencies for:

- Viewer/LayerList/Dims/Camera/Grid public APIs, properties, commands and events.
- All seven layer types: public data structures, settings, appearance, transforms, features/text, interactions, selection, supported dimensionality and serialization/export behavior.
- Visible menus, toolbars, context menus, shortcuts, settings, file/reader/writer/sample flows, docks, overlays/value inspection, screenshot/export and plugin management relevant to P1–P8.
- Console/kernel/Jupyter interfaces and viewer/Python synchronization, including callbacks and in-place mutation.
- Extension contributions, generated and dynamic controls, custom widgets, mouse/key callbacks, background work and lifecycle.
- Platform-dependent installation, graphics, runtime dependencies, confinement and cross-OS interchange.

For each public item, record its source symbol/path/hash and supported combinations, the Hooke UI/control/API equivalent, ownership, tests and evidence. Map compound items to separate children: one generic “appearance works” case cannot qualify every blend mode, layer type, transform and dimension. Use interaction-risk combinations for broader combinations while exhaustively covering each required individual item and critical combinations. Have the coverage reviewer justify the sampling design; do not enumerate only combinations that already pass.

Every reference item must map to an implemented equivalent or a source-evidenced upstream limitation. An omission, hardcoded unsupported reply, private integration restriction or unavailable Hooke backend is a missing implementation, not an upstream limitation. User-approved scope changes must retain the original requirement and dated decision; they cannot silently relabel it passed. Extending beyond the reference must not remove required supported behavior.

Known upstream limits to verify and preserve include noneditable multiscale Labels, Shapes editing only with two displayed dimensions, native multiscale volume level behavior and frame dropping during playback. Hooke's additional persistence, provenance, isolation, setup and Project requirements remain mandatory even where native napari has weaker guarantees.

**Coverage exit:** a second reviewer can walk the relevant source inventory and find no unmapped item. The validator rejects an unmapped source item, missing child, duplicate/reused ID, required skip or a group marked qualified while a child remains open. Discovery of an omitted item reopens its family.

## 2. Keep implementation and qualification separate

Use the existing case schema compatibly or migrate it explicitly. At minimum retain:

| Field | Required content |
| --- | --- |
| Identity | Stable group and atomic child ID, requirement, owner/tracker issue, reference revision/symbol/hash and applicable upstream limits. |
| Implementation | Exact code commit, backend path, public UI entry/control, keyboard/API equivalents where applicable and supported platform/device/mode. |
| Procedure | Initial state, realistic input/fixture/hash, exact user actions, expected scientific state and visible outcome, save/export/reopen and negative/recovery steps. |
| Authority | Project, view/placement when relevant, input/resource/layer IDs and revisions, runtime generation, kernel/session epoch and output destination. |
| Oracle | Independent expected values/relations/transforms, actual pinned-upstream comparison and scientific/render tolerances frozen before final runs. |
| Execution | Real automation entry point or reproducible witnessed manual protocol, parameters, raw report, logs/artifacts/hashes, start/end times, errors/interventions and retries. |
| Qualification | Configured, Operational and Usable separately; per-target OS/architecture/device results and exact installer/build identity. |
| Comparison | Frozen performance limits, memory budgets, participant/repetition counts, task order/cache conditions and prespecified no-worse decision method. |
| Review | Evidence class, independent reviewer/authentication, result, limitations and retained failed attempts. |

Implementation states may be missing/partial/implemented; qualification remains not-run/failed/inconclusive/qualified independently. Null parameters mean acceptance is not ready, never “unlimited” or passed. Required skips fail closure. Never copy a status from a different operation, model, device, runtime or app artifact.

The acceptance runner executes the specified checks and produces evidence; a JSON checklist validator alone cannot qualify behavior. Automated status aggregation must follow child results. Browser screenshots support interaction evidence but cannot replace value, resource-identity or persistence checks. A backend response cannot assert a view was presented before the actual frontend receipt.

## 3. Minimum fixture and failure suite

| Fixture | Required uses |
| --- | --- |
| Asymmetric 5D native arrays | Unequal T/C/Z/Y/X sizes, anisotropic spacing, channel identity, index-encoded values, integer and floating types, nonzero slices, rotation/translation/affine; P1–P4 and Python correctness. |
| Mixed seven-layer scene | Points/features/text, varied shapes, surface topology/values, vectors, division-containing tracks and Image/Labels with matching transforms. Preserve stable identity through edits/reordering/export. |
| Precision and semantics | Large integer label IDs, NaN/missing-value meaning where allowed, probabilities/classes/instances, unknown calibration and explicit conversions. Check JavaScript serialization as well as Python. |
| Actual upstream microscopy workflows | Rights-cleared real images/datasets and independent references for required package modes. Synthetic known values supplement these; they do not establish biological suitability. |
| Realistic large local/remote data | Nontrivial populated chunks, multiple layers and outputs; decoded logical size ≥4× resident budget, budget ≤min(8 GiB,20% RAM); actual read/decode/cache/process/browser/GPU measurements. |
| Multi-actor lifecycle | Two views, two kernels, concurrent manual/plugin updates, Projects/Tabs switched during work, inactive/closed windows, delayed replies, differing bundle generations. |
| Held-out extensions | One stateful widget-heavy napari extension with multi-layer canvas interactions and one non-napari integration, selected outside the implementation team's training examples after SDK freeze. |

For applicable cases inject malformed declarations/messages/outputs, dependency conflict, unsupported input, denied file/network access, missing artifacts, low disk/quota, interrupted writes, hang/crash, cancellation, reconnect, stale revision, failed upgrade/migration, revoked authorization and incomplete cleanup. Assert unrelated jobs, acquisition, saved work and generations survive; owned workers, reservations, callbacks and temporary grants terminate correctly. Verify both failure reporting and corrected retry. Keep the initial failure evidence.

## 4. UI acceptance is part of every capability

Every manual case must specify where a researcher finds the action, what input is selected, what changes while loading/running, how success is visibly confirmed, where the scientific output is kept, and how to recover an error.

Check actual installed production UI rather than a component harness alone:

1. Begin through ordinary Open, Project Data, layer controls, Tools or a declared panel—not through private fixture injection.
2. Operate the control with representative mouse/trackpad and keyboard paths. Verify focus, labels/help, disabled reasons, shortcut conflicts and current selection.
3. Repeat at declared viewport/device-pixel scales; resize during interaction. Check clipping/scrolling and exact screen-to-data mapping.
4. Check preview versus accepted state, busy/error/cancel states and stale/conflict recovery. Preserve drafts and other unsaved layers.
5. Switch Tab/Project, close/reopen the panel/window and restart the app where applicable. Verify background work retains its original input/destination.
6. Inspect exact saved scientific values, features, relations and provenance; export/reimport or cross-OS reopen as applicable.

An API-only implementation cannot close a manual UI child. Invoking Python or asking the AI to perform a missing manual operation is not equivalent UI coverage. Hooke may use a different visual layout from napari, provided capability, discoverability and measured task outcomes satisfy the comparison contract.

## 5. Matched napari comparison

Freeze reference source/dependencies, test data, target hardware, renderer/driver, cache conditions and application versions. Compare scientific correctness, functional completeness and usability as separate outcomes.

For each task, specify numeric/render tolerances and an independently justified no-worse rule before final execution. Include completion success, error/intervention counts, time and recovery where meaningful. Predeclare participants/repetitions, task order/counterbalancing and treatment of inconclusive results. Select counts sufficient for that method; a small convenient pilot is not acceptance by default. Existing product bounds such as live click p95 ≤300 ms still apply even when the native comparator has a different performance profile.

Retain all attempts. Do not erase failures, compare a warm Hooke cache with a cold reference without declaration, omit slow phases, infer p95 from one sample, or expand margins after observing results. Every required task must satisfy its rule; a favorable family average cannot compensate for a failed task. Inconclusive means unqualified.

## 6. Clean-machine and platform qualification

For each advertised target configuration, use two independently reset supported instances. Record OS/image/reset state, installed prerequisites, architecture, physical or virtual hardware, graphics/device/driver, app artifact and all integration/runtime/model identities. A fresh profile on a developer Mac is not a clean-machine run.

Run ordinary install → public discovery/import → explicit preparation/permissions → actual independent operation checks → intended UI workflow → save/export → restart/reopen → uninstall/reinstall or update recovery. Observe downloads and dependencies. Repeat local operation with network denied after preparation. Zero terminal repairs, hidden developer caches/dependencies, undeclared downloads or skipped required interactions are allowed.

Qualify native Windows/Linux execution locally, not a browser connection to macOS. Physical-device claims need actual device evidence; VMs qualify only the paths they exercise. Confirm platform confinement and process cleanup without unrestricted fallbacks. Exchange Projects between all three OS families and repeat after dependency updates/removal.

If access is unavailable, record the exact target/case and needed input, complete independent work and report that qualification remains open. No alternate evidence class can silently close it.

## 7. Review regression cases added by this handoff

These complement the existing 71 groups and must become executable children or stable new IDs in the authoritative ledger.

| ID | Owner | Required regression |
| --- | --- | --- |
| R01.producing-history | H02 | Two kernels plus manual editing share a base; after save/restart/export, every accepted result resolves its exact producing history, settings and base, including conflict copies. |
| R02.new-layer-publication | H01/H09 | Python and a plugin each create new Image/Labels/Points/Shapes/Surface/Vectors/Tracks outputs; accept and reopen valid outputs with exact data/relations. No startup-only binding restriction. |
| R03.multi-layer-drafts | H01/H07 | Edit multiple layers, save one, fail another, resolve a conflict and accept the remainder; all drafts remain available and no partial save appears complete. |
| R04.resource-only-code | H02/H09 | Compute and publish using exact undisplayed resources with no fake camera/render receipt; stale resource revisions are refused. |
| R05.empty-install-intake | H03 | Empty installed app opens ordinary native datasets and creates scientific documents through public controls, without fixture injection or shell setup. |
| R06.native-composition | H01/H03/H12 | Native and structured multiple inputs flow through operation → retained output → next operation for manual/agent/researcher/recipe paths; no RGB conversion or source retargeting. |
| R07.direct-3d | H05 | Real gestures orbit/pan/zoom and perform valid 3D picking at multiple DPI/viewport sizes; late frames cannot misplace selections or edits. |
| R08.specialist-widget | H10 | A custom stateful panel with actual canvas drag events, live multi-layer choices and asynchronous callbacks works through SDK lifecycle/reconnect without duplicate actions. |
| R09.truthful-closure | H00/H14/H15 | Missing child/UI path, stale artifact, null criterion, required skip, failed simulation or unauthenticated admission evidence prevents completion/readiness. |

## 8. Evidence delivery and final sign-off

Evidence classes remain distinct: source inspection; synthetic component/rendering tests; actual isolated backend/native worker; production-path browser; installed clean machine; physical device; independent contributor; researcher usability. Record the class for each assertion.

At final artifact freeze, rerun required repository gates and acceptance cases. Changed code/dependencies invalidate affected evidence; determine impact explicitly. Package mode/device qualification never inherits unrelated proof. Keep unrelated scientific claims outside the scope actually measured.

Deliver a generated index with exact artifact hashes, all atomic cases, grouped P1–P8/Q/W rollups, commands, raw results, reference comparisons, participant/device evidence, failures/corrections and review dispositions. Protect credentials and private datasets while making evidence inspectable to authorized reviewers. Separate installation/functional proof from biological or assay validation.

Final closure requires no mandatory missing, failed, skipped, inconclusive, stale or merely source-inspected item. A known baseline failure is still an open gate under this handoff. An intermediate Mac milestone may be delivered with accurate limits; it is not full parity.

## 9. Crosswalk of all 71 existing required groups

Generated from the implementation branch's `parity-cases.json` during handoff preparation. Titles/IDs are preserved; this table is an ownership crosswalk, not a second mutable status ledger. Use the original procedures/oracles plus the expanded children above. Ownership supplements in H01–H03, H13–H15 apply across rows.

| Existing ID | Required behavior | Primary work package |
| --- | --- | --- |
| `P1.axis-position` | Navigate depth, time, and channels | H04 |
| `P1.axis-order` | Reorder and select displayed axes | H04 |
| `P1.display-mode` | Switch between two and three displayed dimensions | H04 |
| `P1.calibrated-coordinates` | Display calibrated coordinates | H04 |
| `P1.playback` | Play a dimension forward and backward | H04 |
| `P1.slab-projection` | Inspect a finite slab | H04 |
| `P2.camera` | Control a three-dimensional camera | H05 |
| `P2.volume-modes` | Render all supported volume modes | H05 |
| `P2.volume-parameters` | Adjust volume appearance | H05 |
| `P2.clipping-planes` | Position image planes and clipping planes | H05 |
| `P2.surface-render` | Inspect meshes and surface appearance | H05 |
| `P2.mixed-picking` | Pick in mixed three-dimensional scenes | H05 |
| `P3.lifecycle` | Manage general layers | H06 |
| `P3.transforms` | Transform each layer type | H06 |
| `P3.common-appearance` | Adjust shared layer appearance | H06 |
| `P3.image` | Use Image layers | H06 |
| `P3.labels` | Use Labels layers | H06 |
| `P3.points` | Use Points layers | H06 |
| `P3.shapes` | Use Shapes layers | H06 |
| `P3.surface` | Use Surface layers | H06 |
| `P3.vectors` | Use Vectors layers | H06 |
| `P3.tracks` | Use Tracks layers and division graphs | H06 |
| `P3.out-of-slice` | Control out-of-slice display | H06 |
| `P3.grid-linked-views` | Inspect grids and linked views | H06 |
| `P4.label-tools` | Paint, erase, fill, pick, and polygon-label | H07 |
| `P4.label-brush` | Edit labels across dimensions | H07 |
| `P4.label-fill-preserve` | Control connected fill and label preservation | H07 |
| `P4.label-history` | Undo and redo label transactions | H07 |
| `P4.point-edit` | Add and edit points | H07 |
| `P4.shape-create` | Create all supported shapes | H07 |
| `P4.shape-edit` | Edit shape geometry and ordering | H07 |
| `P4.edit-conflicts` | Reject stale or interrupted editing | H07 |
| `P5.lazy-open` | Open native arrays without eager materialization | H08 |
| `P5.multiscale` | Navigate multiscale images | H08 |
| `P5.volume-level` | Render supported multiscale volumes | H08 |
| `P5.fast-navigation` | Keep navigation current under load | H08 |
| `P5.remote-recovery` | Recover from remote data failures | H08 |
| `P5.incremental-workflow` | View, edit, process, and export large data | H08 |
| `P6.console-session` | Use a persistent scientific console | H09 |
| `P6.arrays` | Compute with scientific arrays | H09 |
| `P6.bidirectional` | Synchronize UI and Python edits | H09 |
| `P6.callbacks` | Run viewer callbacks safely | H09 |
| `P6.interrupt` | Interrupt and restart the kernel | H09 |
| `P6.scripts-notebooks` | Use scripts and Jupyter | H09 |
| `P7.readers` | Register readers through the SDK | H10 |
| `P7.writers` | Register exporters through the SDK | H10 |
| `P7.samples-commands` | Register samples and commands | H10 |
| `P7.forms` | Represent function controls | H10 |
| `P7.dynamic-controls` | Resolve dependent controls in the worker | H10 |
| `P7.background` | Retain background plugin results | H10 |
| `P7.custom-panel` | Use specialist interactive panels | H10 |
| `P7.panel-lifecycle` | Preserve panels through lifecycle changes | H10 |
| `P7.held-out` | Integrate independently after SDK freeze | H10 |
| `P8.install` | Install natively on three operating systems | H11 |
| `P8.execution` | Run scientific workflows on each platform | H11 |
| `P8.confinement` | Confine graphics and worker processes | H11 |
| `P8.exchange` | Exchange Projects between operating systems | H11 |
| `P8.updates` | Preserve work through app and package updates | H11 |
| `Q.clean-preparation` | Qualify clean-machine setup | H14 |
| `Q.operation-proof` | Qualify the actual operation | H14 |
| `Q.usable-workflow` | Qualify intended usability | H14 |
| `Q.catalog-admission` | Admit only independently qualified packages | H14 |
| `Q.failure-isolation` | Preserve unrelated work during failures | H14 |
| `W.cellposesam` | Use actual napari-cellposesam integration | H12 |
| `W.ome-zarr` | Use actual napari-ome-zarr integration | H12 |
| `W.animation` | Use actual napari-animation integration | H12 |
| `W.connected` | Compose the required packages | H12 |
| `W.semantic-refusal` | Reject scientifically incompatible connections | H12 |
| `W.projects` | Preserve trained scientific projects | H12 |
| `W.relations-calibration` | Preserve relationships and changed calibration | H12 |
| `W.partial-batches` | Retain partial batch and recipe outcomes | H12 |

Total: **71 existing groups**, plus **9 review regression cases**. All require atomic coverage and evidence; this handoff marks none completed.
