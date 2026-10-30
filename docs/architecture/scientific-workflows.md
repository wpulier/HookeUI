# Reusable scientific workflows

A workflow is a named personal process and a run an execution owned by a
Project. It extends retained Recipes with explicit upgrade; it does not migrate or rewrite historical recipes/runs.
A researcher may create or invoke a workflow. A workflow may contain a bounded evidence-report research step.
Those are separate directions of invocation; an existing researcher cannot launch another paid researcher step.

## Ownership and versioning

Personal definitions use a separate ProjectStore under `workflow-library`, owner `personal`; they contain declared
input requirements, fixed steps and settings, with no implicit bindings to the original Project's data. Upgrading
copies accepted settings and pinned generations only. Definition edits use compare-and-swap and immutable revisions.
Removal is a reversible library revision. Runs retain a frozen definition, exact destination inputs, actor and
separate attempt receipts. Changing a library entry cannot rewrite a run. Cross-Project invocation passes new exact
inputs explicitly. Every transport uses WorkflowService; the Studio's lifetime and selected Project do not own dispatch.

## The first process

Measure bright regions runs the existing local OpenCV method on one retained 8-bit grayscale y/x intensity image,
then pauses for review/correction, measures the accepted labels through native resource actions and retains a CSV
through FileIntake. Inputs are bounded to one million pixels so the method fits the existing analysis memory budget.
Color images need an explicit grayscale conversion first. Tool code, adapter code and OpenCV version determine the
pinned generation. Missing generations refuse; a person explicitly saves a new version to adopt a changed method.
The method identifies bright regions, not biologically validated nuclei. Calibration is carried only through an
explicit same-input-grid association. Measurements and export preserve the exact accepted revision.

Tools with existing scientific controls use their pinned runtime and accepted Recipe settings and stop at
`awaiting-controls`; they do not become unattended merely by entering a graph. The local bright-region adapter is
the first bounded unattended image adapter. Matrix capabilities use their catalog declarations, generation and shared bounded file runner. Other tools refuse unattended execution. Native measurement/export use the
existing typed operations. A changed/incompatible native input refuses before its step dispatch; declared input
requirements are checked before the run starts. Manual input presets cover generic native data, grayscale y/x,
calibrated labels and retained text files. APIs can declare other supported axes and numeric types explicitly.

## Execution and recovery

The run lock serializes durable claims before launch. Each input/step/attempt has its own document; the run summary
does not accumulate results. A batch freezes up to 64 exact inputs, processes them sequentially and resumes without
repeating accepted steps. A graph has at most 12 nodes and no cycles; at most 32 attempts are allowed per node/item.
Independent fixed branches can feed a comparison; the existing method service checks provenance against the original
input and retains method agreement without claiming biological accuracy.

Pause prevents subsequent claims. Stop records intent and blocks subsequent steps; running work remains stopping
until a receipt or reconciliation establishes its outcome. Late outputs remain retained without accepting them.
A read does not restart anything. Reconcile interrupted run changes orphaned running attempts to uncertain, never
replays them, and refuses while this process still owns execution. The user must record observed termination before
retrying uncertain work. Known Study execution identities remain in Efforts for its own collection/reconciliation.
The read-only `observed_status` projection preserves these receipts and separately reports whether this process owns
the driver. Dispatched attempts without a driver say interrupted; an admitted run without a dispatched attempt says
unstarted and requires explicit Resume. `check` and unchanged Start retries never restart a driver. Stop preserves
unknown termination. Awaiting review does not require a driver and remains available after a restart.

Review accepts/rejects a specific result revision. A later correction creates a derived run with unaffected accepted
branches seeded from immutable receipts, and new downstream attempts. The seed snapshot and initialization state
are persisted before seeding; retrying the original correction request finishes initialization after interruption.
The old run stays intact. Generic scientific-document saves cannot forge execution or invocation receipts.

## Researcher direction

Local researchers and connected agents invoke the same workflow service. The graph's historical bounded
report-node declaration remains readable, but standalone HookeUI refuses managed report execution before
allocation. Private provider dispatch is not imported into the core. Retained external tasks keep their original
identity and allowance until the original service reconciles them; an unavailable provider is not cancellation.
An already completed report can still be inspected and reviewed. A tool-enabled researcher drives successive
frozen capability workflows from outside the graph. Paid recursive researcher launch remains refused.

## Experiments: live models and this app's researchers as steps

Part 2.5, W5 (`orchestrator/experiments.py`, `orchestrator/experiment_steps.py`; Will, 29 Oct: "I want to run an
experiment comparing Cellpose and micro-sam on this footage and have a researcher audit it"). Each piece is an option
beside the steps above; none of them changes.

- **A frame as an input** (`experiment_steps.keep_frame`): a frame of the live view kept as a scene whose one intensity
  array is its grey plane on its own grid (pixel units, the scene as its coordinate space), the camera's JPEG kept as a
  source file beside it and the view's facts (`population.view_facts`: size, disc, epoch, offset, the bridge's numbers,
  the run). Retried with the same request, the same frame comes back.
- **A live model as a tool step**: capability `model:<operation>/<impl>` (`cells` today), pinned by its generation (the
  implementation, its catalog entry and version: a model that changed is refused before it runs), one parameter
  `policy` (raw or route). Readiness asks `operations.availability` (edition, key, installed, worker). It runs on the
  event loop through `population.segment` at research priority (the GPU worker serves it only when nothing of the app's
  own waits), made for the experiment, its cells kept in Data as a population; its exclusive label map is published as
  the step's `instance-labels` output on the frame's grid (`BuiltinOutputSave`, as bright regions), so the
  `measure` and `compare` steps read it unchanged. The output keeps what ran (`experimentModelRun`: the runner's
  execution, what ran, time, reuse, the population and its count and boxes). A Project switched away from refuses it
  ("open this experiment's Project to go on"), never files cells in another Project.
- **A researcher step through this app's researchers** (provider `local-researcher`: question, criteria, a quick or
  standard `effort`, an allowance of at least that effort's cap, no tools, a report). Readiness needs the research
  manager and its model's key. It starts one researcher (`ResearchManager.start`, `confirmed`: the plan the user
  approved admits it; no camera snapshot; origin `experiment`), its brief the question and criteria with the records to
  read by id. The attempt waits `awaiting-research`; the driver collects it: done or partial completes the step with a
  `research-findings` document (`accepted` false: not reviewed by anyone), a failure or stop fails it with its words, a
  researcher that is not loaded is `uncertain` (nothing replayed). The managed provider stays refused in the core.
  Study workflows still refuse researcher nodes (their allowance is not qualified there).
- **The experiment** is two frozen runs: the models' (a frame per item: each model, its measurement, the comparison)
  and, when audited, the audit's (one item: the run's record as a JSON source file, then the researcher step). The
  comparison table is built from the attempts' outputs only (`Experiments._comparison`); the results are one document.

## File capabilities and named inputs

`source-file` is an accepted exact retained file, not a path or an in-memory table. A tool declares its pinned
catalog capability, generation, parameters and automatic mode. Optional `settings.bindings` maps declared input
ports to dependencies. `input` remains the current item in a sequential batch. A dependency `input:<name>` selects
an explicit named input supplied on Start/Validate through `bindings`; these references are verified and frozen
in the Project run. Definitions therefore remain reusable without embedding another Project's data. Changed
named inputs under the same request identity refuse, and cross-Project references never become read authority.

The shared `matrix_capability_runs.run_matrix` handles bounded materialization, computation and retained outputs.
It owns no workflow attempts. Matrix bytes do not pass through the 48-column table or document JSON paths.
The manual capability action constructs a one-node frozen workflow and uses the same admission, dispatch and
recovery. Readiness says when a pinned runtime is missing; a manifest alone does not make a method executable.
The bounded adapters, prepared dependencies and upstream evaluator are described in
[Scientific matrices](scientific-matrices.md); the workflow layer does not implement their numerical algorithms.

## Attribution

The transport supplies an authenticated `agent:ac_<id>` actor, a local `researcher:r<id>` identity, or the existing
local user/chat/voice actor. Execution never switches behavior based on an agent product name. The original run actor is retained.
Each new definition records `createdBy` and `lastChange: {by, action, revision}`. Edits, removal and restoration
preserve the creator and attribute their own immutable revision; duplication, upgrade, capture and a direct capability's
frozen definition attribute the new definition to that caller. A legacy definition with no recorded creator stays
unattributed when edited. A same-content create retry returns the original revision and original author, even when
another authorized caller retries it; changed content conflicts. Stale edits still refuse rather than overwrite.
Reviews, controls and recovery record their acting identity; later attempts inherit the actor that continued
the run. A correction-derived run records its new actor and original run association. Unchanged request retries
return the original result without rewriting authorship. These records establish provenance, not authorization;
the caller's door grant and Project access must be checked by the transport.

## Interfaces and UI

`/api/workflows/command` and the brain's `workflow` tool use one service. Discovery and run-status routes are read-only.
Mutating commands carry an exact version, explicit Project where relevant, trusted transport actor and stable request
identity for create/start/review/continuation. Library edits use exact revision compare-and-swap. Research workers route
through their original dispatched attempt and existing operation budget, and cannot accept reviews or create paid recursion.
Study-bound researchers read evidence and admit work through the same tool's `study-*` actions; they cannot bypass
their original Study with direct workflow admission or publish claims in another Study.

The Workflows library supports name/search/create/edit/duplicate/remove/restore. React Flow is a lazy Studio island;
connected steps and ordered steps share one definition and inspector. The first method exposes smoothing, minimum
region size and connectivity as named controls; research steps expose question, criteria, time and spend limits.
Other captured tools retain an advanced settings object. The single-capability form is generated from catalog input ports and parameters; it selects exact retained Project revisions and shows unavailable-runtime reasons. React/React DOM and React Flow carry their MIT
notices in installed packages and are recorded in Hooke's component register. No n8n source is included.
The existing Study rail shows workflow progress and exact result links, with a route to each retained run. Both the
Study form and workflow editor expose every named input binding; no matrix bytes are transported by those controls.
Catalog cards for reviewed file adapters link to that retained-file form instead of offering a live-picture action.
Their legacy live-view registry refusal names the correct route; only the workflow descriptor's runtime check supplies
execution readiness. Agent discovery uses `find_workflows(capabilities=true)` to obtain the current contracts and generation.

## Dependency provenance

React, React DOM and React Flow are ordinary npm dependencies with exact resolved versions and integrity hashes in
`apps/studio/package-lock.json`; their upstream MIT notices stay with the installed packages. Reproduce the Studio
environment with the repository's normal locked install. Development-only links to another checkout's dependency
directory are not shipped artifacts or an independent clean-install qualification. Scientific CPU dependencies are
prepared through the existing catalog environment service, with their own lockfiles, generation receipts and licenses.
Neither workflow definitions nor a connected agent may select arbitrary imports or host executable paths.

## Verification boundary

`services/tests/test_workflows.py` covers storage/ownership, pinned admission, claims, duplicate starts, pause/stop,
uncertainty, batch, legacy upgrade, derived initialization, branch comparison and real worker-command transport.
Its real-image case uses the repository human-mitosis specimen and independent NumPy reference, a second explicitly
shifted image in another Project, native correction with synthetic display authority, measurement/export and reopening.
This is deterministic image processing and native storage evidence, not actual-model or biological validation.
Browser, packaged Beta, live provider and independent returning-researcher evidence must be reported separately.
Manual/Fiji task timings and independent adoption remain evaluation work; code completion does not establish superiority.

Computed file choices include their recorded capability and a stable identity suffix
so distinct outputs called `effects.npz` can be selected deliberately. Display labels
do not rename saved documents or substitute for their exact revision/digest bindings.

Built-in bright-region output uses the shared bounded atomic native publication path without
borrowing an external viewer runtime from the input. Its versioned algorithm producer remains
in provenance. Runtime-free retained image/label documents can be copied for measurement;
external worker output capture still requires its declared retained runtime. The same workflow
therefore accepts native images retained from saved populations, including corrected revisions.

Correcting a native workflow result advances its named scientific input reference
with the accepted array resource in the same atomic publication. Review, measurement
and export then read that exact corrected revision; the prior revision stays intact.
