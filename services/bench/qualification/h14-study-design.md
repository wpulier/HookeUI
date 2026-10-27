# H14 pre-registered study: napari versus Hooke

**Frozen on 2026-10-20.** The SHA-256 of this file is recorded in `h14-study-design.sha256` next to it and in
[ticket 60](../../../planning/open-source-successor/implementation/issues/60-hooke-h14.md). It cannot hold its own
hash. Any edit after the freeze changes that hash. Such an edit is allowed only as a dated amendment in §11, made
before the binding freeze (§3.3), which comes before any final-study data, and only in the directions §11 permits.

This document satisfies `Q.usable-workflow.step-1` (freeze task criteria, tolerances, margins, participant counts and
repetitions), [acceptance.md §5](../acceptance.md#5-matched-napari-comparison), the H14 bullets in
[work-packages.md](../work-packages.md#h14--independent-qualification-and-researcher-usability) and the
Q.usable-workflow procedure (§9). The other Q procedures are frozen separately in [q-procedures.md](q-procedures.md).
This document does not say anything has passed. Nothing in it has been run. Its pilot has not been run either.

Owner: lane-qualification (Claude session, implementation side). Under H14 the owner cannot sign off qualification.
The independent qualification reviewer, moderators and researchers are external gates (§10).

## 1. Question and claim

For each required researcher task (§4), the question is whether Hooke is no worse than pinned napari for fresh
intended researchers on the reference configuration (§3). "No worse" is judged on unassisted correct completion and
on time, under the rules in §6. The claim is made task by task. A task that fails or is inconclusive is unqualified.
A good average across tasks or a family cannot make up for it.

What it can establish: usability equivalence on the macOS reference configuration only, for the stated population and
tasks. What it cannot establish: biological or assay validity; usability on Windows or Linux (each OS needs its own
separately frozen replication study); usability for researchers who don't use Python (§2.1); or any functional
parity case. Those need their own evidence class.

## 2. Participants

### 2.1 Eligibility

- Works with biological microscopy images (graduate student, postdoc, technician, staff scientist, imaging-core staff
  or PI), and has analysed such images at least monthly over the past 6 months.
- Can read and edit a 10-line Python/NumPy script, judged by a 2-minute screener item that is scored before enrolment.
  This matches napari's intended population. The limitation is stated in §1.
- Has never used Hooke, HookeUI, ObservationUI or MicroscopeMind, and has not seen a demonstration of them.
- Is not employed by, and has not contributed to, Hooke or napari. Has not taken part in the pilot.
- Normal or corrected vision. Participants who use assistive technology are welcome. Their sessions are analysed with
  everyone else's and also reported separately.

### 2.2 Counts

- **Final study: 250 completers** (Amendment 3, Will's decision). Each one finishes both sessions, so every task
  has 250 paired attempts. They are randomised in blocks of 4 to the 4 sequences of §5: 62 full blocks and one final
  half block, whose two sequences come from the same seeded permutation. That gives 62 or 63 per sequence.
- **Repetitions:** one attempt per task per app per participant, on matched variants (§4.2). There are no retries:
  a participant who gives up or reaches the cap has a failed attempt.
- **Strata.** Randomisation is stratified by napari use: *regular* (used it at least monthly in the last 6 months)
  and *naive* (all others). Quotas: at least 48 naive and at least 36 regular.
- **Sites.** At least 3 institutions. No single lab supplies more than 15% and no single institution more than 40%.
- **Replacement.** If a participant withdraws, or an attempt is lost for a reason unrelated to either app (§7.2), the
  next recruit in the same stratum takes the same sequence. A lost attempt is recorded and kept, never deleted.
- **Pilot: 8 participants**, 2 per sequence, recruited separately and never reused (§8).

### 2.3 Power, and why 250

There is one decision per task and every task must pass (§6). Each test is one-sided at α = 0.05. Because a pass
requires every component of every task to pass, this is an intersection–union test (Berger, 1982). The chance of a
false claim of no-worse stays at or below 0.05 for every task, and for the whole set, with no further adjustment. The
cost is power: the probability of passing everything when Hooke is truly as good as napari is the product of the
per-task powers.

The first freeze's table ([`h14_power_simulation.py`](h14_power_simulation.py)) simulated H2 on a normal D with SD
0.6. But §6 defines H2 on the **composite** time, where every failure scores ln(2 × cap), and failures in one app
but not the other widen D.

[`proposals/h14_power_composite.py`](proposals/h14_power_composite.py) keeps that table's assumptions:
- equal true rates of 0.90 for unassisted correct completion;
- a discordant-pair rate of 0.10 to 0.16;
- an SD of 0.6 of the log time ratio among successes;
- times at a median of a third of the cap.

It builds D from paired attempts with the composite, which gives an SD of D of 0.80–0.92. Its output is
[`proposals/h14_power_composite.out.txt`](proposals/h14_power_composite.out.txt). Seeded, it gives these pass
probabilities for H2 and H3:

| Completers | Per task (H2 and H3) | 15 comparison tasks |
| --- | --- | --- |
| 120 (first freeze) | 0.85–0.92 | 0.09–0.28 |
| 200 | 0.96–0.99 | 0.54–0.81 |
| **250** | **0.987–0.997** | **0.82–0.96** |
| 300 | 0.995–0.999 | 0.93–0.99 |

So 250 gives at least about 80% probability of passing H2 and H3 on every comparison task when Hooke is truly as
good. That is why Will set N = 250 (Amendment 3).

**What these figures leave out**, each of which lowers the true probability of passing everything:
- H1a and H1b are not simulated. They pass only if Hooke has no defect that needs developer repair or produces a
  silent wrong result. They are about the app, not sampling.
- The required agreement of the bootstrap with the t-interval (H2), and of the mixed models with the primary
  analysis (§7.4), is not simulated. Any disagreement makes a task unqualified.
- The 7 Hooke-only tasks are not in the table. At 250 their rule needs at least 211 unassisted correct completions
  (84.4%). If each task's true rate is 0.90, all 7 pass with probability about 0.99; at 0.88, about 0.77; at 0.86,
  about 0.20.

**How N may change.** N = 250 is Will's decision of 2026-10-21 (Amendment 3). From here there are two routes, and
no others:
1. The pilot re-estimates the discordance rate and the SD of D. If 250 then gives under 80% all-task power, the pilot
   raises N (§8, item 4). The pilot never lowers N.
2. Separately from the pilot, Will may make one funding amendment that lowers N, before the binding freeze (§11). It
   must state the power it accepts. It never changes a margin, an α or a rule, so the chance of a false claim does
   not change. The only effect is that more tasks may come out inconclusive, which means unqualified.

### 2.4 Consent, privacy and compensation

Participants give written informed consent and are screen- and audio-recorded. The recordings are stored with
participant codes only and are kept as evidence for authorised reviewers (acceptance.md §8). An institutional ethics
review or exemption is obtained before the pilot. The proposed compensation is USD 60 per hour, including the
screener and travel time, about USD 360 per completer. That is about USD 93,000 for 250 completers plus the 8 pilot
participants, excluding moderators and sites. **The amount and the budget are Will's decision (§10).**

## 3. Reference configuration (bound before the final study, §3.3)

### 3.1 Applications

- **napari:** the pinned reference revision `ebfc4a0a1e5386f41582761204f416fffe6cd0b6`, which is the archive
  SHA-256 `3a29672c608ee6a8e84a488d7c5c122915f5c852757d401736f668bf60c740a6` that the H00 inventory uses. It is
  installed from the prepared wheel SHA-256 `ea1b1d4681556c3d811a334d60ce3f812e91600081eec74a1874d232fecbaf4f`
  with PySide6 6.12.0, vispy 0.17.0, PyOpenGL 3.1.10, QtPy 2.4.3 and numpy 2.5.4 (the set `napari_cgl.PINNED`
  uses). Plugins: napari-ome-zarr, napari-cellposesam and napari-animation at the exact versions H12 pins, plus
  napari's built-in console. Each runs in its own locked environment, launched from a desktop shortcut with
  napari's ordinary Qt window. In T0 the participant installs napari by following napari's own installation page at
  the pinned version, with the pinned wheel file provided in Downloads, then adds the plugin through napari's plugin
  manager.
- **Hooke:** the exact signed, notarized release-candidate artifact (DMG SHA-256), its bundle, environment and model
  identities, and the matching catalog entries for OME-Zarr reading, Cellpose-SAM and animation. It is installed
  through the ordinary DMG and Move to Applications. No developer build, no terminal and no `make start`.
- The **held-out extension** (T14) is the napari extension selected for H10 after the SDK freeze: the same pinned
  version in napari, and its Hooke integration through the frozen SDK.

### 3.2 Hardware and environment

The same hardware is used for both apps and at every site. Its exact model identifiers go in the binding manifest:

- Apple Silicon MacBook Pro (one model, one chip and one memory size at every site, at least 24 GB), on mains
  power. The macOS build is frozen and automatic updates, Spotlight indexing of the data folder and Time Machine are
  off during sessions.
- One external 27-inch display at 2560×1440 effective (2× backing), the laptop lid closed, a wired 3-button mouse
  with a wheel, and a wired US-layout keyboard. Participants may use the laptop trackpad only if both apps get it.
- Wired network, which the sessions use only for T0 and T13. A logging forward proxy records every host contacted
  ([q-procedures.md](q-procedures.md) §2, step 3).
- Screen recording at 30 fps (both apps' windows), audio, and each app's own logs. Hooke's interaction log records
  click latency. The live-view bound of click p95 ≤ 300 ms is recorded for H15 and is not part of this study's
  decision.

### 3.3 Binding manifest

Before the first final-study session, the qualification reviewer fills `h14-binding-manifest.json` with every
identity above and hashes it: the artifact hashes, plugin and environment locks, the macOS build, the hardware model
identifiers, the dataset hashes (§4.2), the time caps after the pilot (§8), the analysis script's hash (§7.5), the
moderator script hash, and N. That file is then frozen too. A null field means the study cannot start. Changing any
bound identity after a final session has run invalidates every session run on the old identity for the tasks it
affects. Those sessions are kept and reported but not pooled.

## 4. Tasks

### 4.1 Comparison tasks (napari and Hooke)

Each participant does every comparison task once in each app, on matched dataset variants (§4.2). Instructions state
the goal in the researcher's terms ("find…", "fix…", "measure…") and never name a menu, a control or a shortcut.
**Done** means the participant says "done" and the app's state or saved output is then scored. The time cap is per
attempt. The caps are the values the pilot may only raise (§8).

| ID | Task (goal given to the participant) | Data | Start state | Oracle and tolerance | Cap |
| --- | --- | --- | --- | --- | --- |
| T0 | Install the app and get it ready to segment nuclei with Cellpose-SAM | none (download) | Fresh macOS user account with neither app's profile or caches, on the reference machine ([q-procedures.md](q-procedures.md) §2) | The app is installed through its ordinary installer. The segmentation package is prepared, with its receipt. A 64×64 check image segments to the reference labels (object F1 = 1.0 at IoU 0.5). Every download host is declared. | 25 min |
| T1 | Go to time *t*, channel *named C*, and the depth holding the brightest marked object; report its value and its calibrated position | asymmetric 5D (T6 C3 Z21 Y256 X320, anisotropic spacing, index-encoded uint16) | file open in the app | Value exact. Position within ±0.5 voxel in each axis, in calibrated units. Axis order also changed when asked: the displayed axes equal the target. | 6 min |
| T2 | Open the named sample or command, then show a 5-plane maximum slab around *z*, set the given contrast limits and colormap on two channels and hide the third | same 5D, plus one built-in sample | empty app | Layer state: contrast limits within ±1% of range, colormap and visibility exact, slab thickness and position exact. | 6 min |
| T3 | Show the volume in 3D as maximum projection, then isosurface at threshold *v*; place a clipping plane at depth *d*; report the ID of the object under the marked point | labels and image volume, 3 objects deliberately occluding each other | file open, 2D | Render mode exact, threshold within ±2%, plane within ±1 voxel. Picked ID exact. The pick must be made in 3D (a 2D pick isn't counted). | 8 min |
| T4 | In the mixed scene, report the parent track of the marked cell after its division, the value on the marked surface vertex and the marked vector's length; make points out of slice visible at half size; show the layers as a grid | mixed seven-layer scene with matched transforms | scene open | Parent ID, vertex value and vector length exact (length to 1e-6 relative). Out-of-slice and grid state exact. | 8 min |
| T5 | Open the large dataset, go to the marked region at full resolution and report the value at the marked pixel | multiscale OME-Zarr, decoded logical size at least 4× the resident budget, read through the OME-Zarr reader | OS file cache purged before each app's attempt (cold) | Value exact, from level 0 (verified against the stored chunk). | 8 min |
| T6 | Put a point on each of the 10 marked cell centres; draw a rectangle and a polygon around the two marked regions; move the polygon behind the rectangle | single 2D image | file open | Point count exact, each within 3 px of a centre. Shape IoU ≥ 0.85 against the reference. z-order exact. | 8 min |
| T7 | Fix the labels: split the marked merged nucleus into two, delete the marked false object, fill the marked hole, and undo one deliberate mistake the instructions ask for | 2D labels on BBBC039-class nuclei | labels open | Each split part's IoU ≥ 0.80 against the reference correction. The deleted object is absent. The hole is filled (exact pixels). Pixels outside the three edit regions are unchanged, exactly. The undo leaves no trace. | 10 min |
| T8 | Segment the nuclei with Cellpose-SAM using the given diameter and channel | BBBC039-class nuclei image (CC0) | image open, package prepared | Labels object-level F1 ≥ 0.98 at IoU 0.5 against the reference run with the same model, settings and device. | 10 min |
| T9 | Measure each nucleus's area (µm²) and mean intensity and export the table | the reference labels for T8's image, provided beside it, so a T8 failure does not carry over | labels open in the same app session | Every row matches an independent regionprops computation: area exact, mean intensity within 1e-6 relative, units µm². The file exists with one row per object. | 8 min |
| T10 | Make a movie: three keyframes (the given views and time points), 15 steps between them, exported | 3D+t image | file open | Keyframe count and order exact. Frame count exact. SSIM ≥ 0.95 at each keyframe against the reference render. | 10 min |
| T11 | Erase the marked label, save everything, quit the app, reopen it and change the second marked label | this session's T8–T10 work | nothing saved since T8 | Saved labels and table byte-exact against the pre-quit state. They reopen exactly, and the second change is the only difference after reopening. | 6 min |
| T12 | In the app's Python console: add the maximum projection of the image as a new layer; change label 7 to 9 in place and see it update; stop the provided long loop and keep working | 3D image and labels | console closed | New layer array exact. Label change visible in the viewer, with the array exact. The kernel is alive after the interrupt and earlier variables are kept. | 10 min |
| T13 | Keep working on the remote dataset after its connection drops | remote OME-Zarr served by the site's local server; the moderator's script cuts it at a fixed point | remote dataset open | The participant notices the failure, reconnects or retries through the app, and reports the value at the marked pixel (exact). Labels from T7 are intact. | 8 min |
| T14 | The held-out extension's own task, written into the binding manifest when the extension is chosen (H10) | extension's own data | stated then | Stated then. Must be numeric or structural, with no subjective criterion. | stated then |

### 4.2 Data and matched variants

Each comparison task has two variants, D1 and D2. They match by construction: the same shape, dtype, object count
(±10%), object-size distribution and contrast, with different positions and values. Each participant does one variant
per app (§5). The pilot checks that the median time on D1 and D2 differs by less than 10% per task (§8). Fixtures come
from the H00/H12 fixture suite (acceptance.md §3). Real microscopy uses rights-cleared sets with ground truth:
BBBC039 (CC0) for T7–T9, and a CC BY 4.0 IDR OME-Zarr image for T13. Large and remote data are generated
deterministically from real tiles by a script whose hash is bound. Every dataset file's SHA-256 is in the binding
manifest. Before each attempt the files are copied from a read-only master and their hashes checked.

### 4.3 Hooke-only tasks (absolute criteria)

The pinned napari source offers no ordinary researcher action for these, so there is nothing to compare. The
independent coverage reviewer must confirm this against the pinned source before the binding freeze. If they find a
napari equivalent, the task moves to §4.1 by amendment.

| ID | Task | Groups | Criterion |
| --- | --- | --- | --- |
| U1 | Another actor changed the labels while you were editing. Keep your work and resolve it | P4.edit-conflicts | Both versions are kept; the chosen version is accepted; nothing else changes |
| U2 | Use this probability map as intensity in a measurement (the app should refuse), then find a valid input | W.semantic-refusal | The refusal is understood (the participant states why) and the valid input is used |
| U3 | Apply the provided trained model to new data, update it, then roll back | W.projects | Outputs match the reference; the rollback restores the earlier generation exactly |
| U4 | Retry only the failed items of a partly failed batch | W.partial-batches | Exact successful outputs are kept; only the failed items rerun |
| U5 | Update an installed package, notice what changed, and go back to the previous version | P8.updates | The candidate is reviewed, then the previous generation is active again; saved work is unchanged |
| U6 | Open a Project made on another computer that needs a package you don't have | P8.exchange | The package is prepared through the offered path; the data reopen exactly; nothing is deleted |
| U7 | Find out whether a catalog entry is qualified, and what it is missing | Q.catalog-admission | The participant reports the status and missing requirement that the record states |

Rule for each U task: hard rules H1a and H1b (§6), and unassisted correct completion with a one-sided 95%
Clopper–Pearson lower bound ≥ 0.80 over the 250 completers.

### 4.4 Coverage of the 71 groups' `qualify-usable` children

Every group's `.qualify-usable` child maps to at least one task. P8.execution and P8.confinement are covered by
running every Hooke attempt in the installed, confined app on the reference configuration, which is the only target
this study qualifies.

| Groups | Task |
| --- | --- |
| P1.axis-position, P1.axis-order, P1.calibrated-coordinates, P1.playback | T1 (playback: the participant plays to *t* and stops) |
| P1.slab-projection, P3.common-appearance, P3.image, P3.lifecycle, P7.samples-commands | T2 |
| P1.display-mode, P2.camera, P2.volume-modes, P2.volume-parameters, P2.clipping-planes, P2.mixed-picking | T3 |
| P2.surface-render, P3.surface, P3.vectors, P3.tracks, P3.out-of-slice, P3.transforms, P3.grid-linked-views | T4 |
| P5.lazy-open, P5.multiscale, P5.volume-level, P5.fast-navigation, P7.readers, W.ome-zarr | T5 |
| P3.points, P3.shapes, P4.point-edit, P4.shape-create, P4.shape-edit | T6 |
| P3.labels, P4.label-tools, P4.label-brush, P4.label-fill-preserve, P4.label-history | T7 |
| W.cellposesam, P7.forms, P7.dynamic-controls, P7.background, Q.operation-proof | T8 |
| P7.writers, W.relations-calibration, P5.incremental-workflow | T9 |
| W.animation, P7.custom-panel | T10 |
| P7.panel-lifecycle, W.connected (T8→T9→T10→T11 in one app session), Q.failure-isolation (T13) | T11, T13 |
| P6.console-session, P6.arrays, P6.bidirectional, P6.callbacks, P6.interrupt, P6.scripts-notebooks | T12 |
| P5.remote-recovery | T13 |
| P7.held-out | T14 |
| P8.install, Q.clean-preparation | T0 |
| P4.edit-conflicts, W.semantic-refusal, W.projects, W.partial-batches, P8.updates, P8.exchange, Q.catalog-admission | U1–U7 |
| P8.execution, P8.confinement, Q.usable-workflow | the whole study |

W.connected's Hooke attempts run T8, T9, T10 and T11 in one application session, keeping each result in the same
Project. Each task still starts from its own provided input, so an earlier failure never changes a later task's
score. The napari attempts run the same steps in one napari session through napari's own files. T5 (open OME-Zarr)
and T7 (manual correction) cover the chain's other steps in session 1. The full single-session chain is a
functional W.connected case (H12), not this study's.

## 5. Order, sessions and cache conditions

- **Sequences.** With N = napari and H = Hooke: S1 = session 1 N→H, session 2 H→N, D1 first; S2 = S1 with D2
  first; S3 = session 1 H→N, session 2 N→H, D1 first; S4 = S3 with D2 first. The first app in a session uses the
  first-listed variant. Allocation is concealed: a sealed list generated from a fixed seed is held by the reviewer.
- **Session 1** (about 2 h 15 min): consent check; the scripted 8-minute orientation video for the first app; T0,
  then T1–T7 in that app; a break; the same for the second app. **Session 2** (about 2 h 15 min, 1–7 days later):
  T8–T14 per app in the same pattern, then U1–U7 in Hooke, then questionnaires. Each app's orientation video is
  the same length and style, shows only general navigation, and no task step.
- **Task order** within an app block is fixed (T0…T7, T8…T14), because T8–T11 run as one chain.
- **Cache conditions, declared and the same for both apps:**
  - T0: cold. A fresh macOS user account with no profile, cache or runtime of either app. The download network is
    on.
  - T1–T4 and T6–T14: prepared-warm. Both apps are installed and prepared in that account (T0's result, or the
    reviewer's preparation if T0 failed, recorded), launched once and quit before the block. The data are copied
    locally just before the attempt.
  - T5: cold OS file cache. `sudo purge` is run by the moderator just before each app's attempt.
  - T11 includes the app restart in its time.
  - T13 uses the site's local data server on the wired network, the same for both apps.

## 6. Decision rules (per task, all must hold)

Definitions, per attempt:
- **Unassisted correct completion (UCC):** done within the cap; result within the task's tolerance; no intervention
  above level 1 (§7.1).
- **Composite time Y:** `ln(min(time, cap))` for a UCC attempt; `ln(2 × cap)` for any other attempt. Every failure
  counts as worse than any success.
- **Paired difference:** D = Y(Hooke) − Y(napari) per participant.

For each comparison task:

- **H1a (no developer repair):** no Hooke attempt has a level-4 intervention (§7.1). One occurrence fails the task.
- **H1b (never a silent wrong result):** no Hooke attempt ends with an app-caused wrong accepted scientific result,
  meaning the app showed success, or showed a state, that differs from what it computed or saved, given the
  participant's recorded actions. One occurrence fails the task. Two reviewers adjudicate from the video and logs,
  independently. A disagreement counts as app-caused.
- **H2 (time, non-inferiority):** the one-sided 95% upper confidence bound of mean D is below ln(1.25). This is the
  t-interval on the 250 paired D, with a BCa bootstrap (10,000 resamples, seed 20261020) as the required
  sensitivity check. Both must pass.
- **H3 (completion, non-inferiority):** the Tango score lower bound (one-sided 95%) of p(UCC, Hooke) − p(UCC, napari)
  is above −0.15.
- **Inconclusive** (a bound crosses its margin) means unqualified. **Disagreement** between the primary analysis and
  the mixed-model sensitivity analysis (§7.4) on pass or fail also means unqualified.

For each Hooke-only task (§4.3): H1a, H1b and the Clopper–Pearson rule.

**Why these margins.** They are judgments, fixed before any data, and are stated so a reviewer can reject them before
the binding freeze:
- **1.25 on time** borrows the 0.80–1.25 convention of paired equivalence testing. A task that takes a researcher at
  most a quarter longer, with no more failures, does not stop them doing the work.
- **15 percentage points on completion** keeps Hooke's completion above 75% when napari's is 90%. A tighter margin
  costs participants roughly as the inverse square: 10 points needs about 2.3 times the N of §2.3. The H2 time rule
  also penalises extra failures, because a failure scores worse than any success, so a Hooke that fails more often
  has to pass both rules.
- **H1a and H1b are absolute**, because the contract forbids developer repair and the project's rule forbids claiming
  what did not happen.

The margins come from those rules and the contract, not from expected results.

## 7. Measurement and analysis

### 7.1 Intervention levels (the moderator follows the script, verbatim)

- **L0:** none.
- **L1:** neutral prompt, at most twice per attempt: "Please keep going" or "What are you trying to do now?". Still
  UCC.
- **L2:** directional hint ("Look at the layer's controls"). Not UCC.
- **L3:** the moderator demonstrates or performs a step. Not UCC.
- **L4:** developer repair. Terminal or console use by the moderator, editing a file or setting, killing a process,
  reinstalling, or anything outside the app's ordinary UI. Not UCC, and H1a applies to Hooke. In a Hooke attempt,
  the participant's own use of a terminal, a developer tool or a file edit outside the app also counts as L4,
  because Hooke's ordinary path needs none. The participant's own ordinary actions, such as quitting and reopening
  the app, are not interventions. They count toward time. In a napari attempt, a terminal is ordinary where napari's
  own documentation uses one (T0's install).

A participant may ask to give up. That ends the attempt as not UCC, timed at the cap.

### 7.2 Recorded per attempt

Start and end times (from the screen recording's first frame after "begin" to the participant's "done"); outcome
score from the oracle script on the saved or app state; intervention log with level and time; coded errors (E1 slip,
E2 wrong function, E3 wrong scientific state left in place); recovery (T13: whether the failure was noticed, and
whether the work stayed intact); Single Ease Question; app log excerpt; data and artifact hashes. An attempt lost
because of hardware, power, fire alarm, or the participant leaving for reasons unrelated to the app is recorded as
**lost**: it is excluded from analysis and replaced (§2.2). An attempt lost because either app hung, crashed or
needed repair is a **failure** of that app, never lost. Post-session measures: SUS per app and raw NASA-TLX per block.

### 7.3 Scoring

Oracle scripts score saved or exported outputs (or the app's state where the task asks for a value), against the
references bound in the manifest. Scoring is automated and runs before anyone looks at the summary results. Two
trained coders code errors from video. 20% of attempts are double-coded, with Cohen's κ ≥ 0.80 required. Below that,
all attempts are recoded after retraining. Coders know the app (it can't be hidden), but not the analysis results.

### 7.4 Analyses

- **Primary:** §6 per task, on all completers' paired attempts.
- **Sensitivity (required to agree):** a mixed model per task, with app, period and variant as fixed effects and
  participant as a random effect: a linear model for Y and a logistic GLMM for UCC. The one-sided 95% bounds are
  checked against the same margins.
- **When the GLMM has no finite estimate** (complete separation, for example an app with no failures, or no
  successes, on a task), the H3 sensitivity analysis is instead the exact conditional test on the discordant pairs
  (Amendment 2).
  - With m discordant pairs, c of them favouring napari, and n pairs in all, the one-sided 95% lower bound of the
    risk difference is (m / n) × (1 − 2 × u). Here u is the one-sided 95% Clopper–Pearson upper bound of c / m.
  - With m = 0 the bound is 0.
  - Agreement with H3 is judged against −0.15, as for the GLMM.
- **Reported, no decision:** period and carry-over effects, variant effects, results by stratum (napari naive or
  regular), by site, and for assistive-technology users; error counts and their rates; SUS; NASA-TLX; SEQ; the
  click-latency log.
- **No interim looks.** Nobody computes a comparison until all 250 completers are scored and the data are locked
  (hashed).

### 7.5 Analysis code

`services/bench/h14_analysis.py` is to be written before the pilot. It is tested on simulated data, including the
power simulation of §2.3, run on the pilot data (§8), and then hashed into the binding manifest. Its output is one
JSON with every task's bounds, rules and verdicts, plus the evidence binding ([q-procedures.md](q-procedures.md) §6).

## 8. Pilot (design only, never acceptance)

8 participants, 2 per sequence, run on the bound configuration with the complete protocol. Pilot data never enter the
final analysis and support no pass or fail statement. The pilot may only:
1. clarify the wording of task instructions without changing a goal or an oracle;
2. **raise** a time cap to at least 3× the slower app's pilot median for that task, never lower one;
3. replace a dataset variant whose median time differs by 10% or more from its partner;
4. re-estimate the discordance rate and the SD of D, and **raise** N if 250 then gives under 80% all-task power;
5. fix the moderator script, logging and scoring pipeline.

Nothing else may change because of the pilot: no margin, α, rule, oracle, tolerance, task or participant
criterion. Each change goes into §11 as a dated amendment before the binding freeze. Pilot results are reported
separately and labelled as pilot.

## 9. Q.usable-workflow procedure and the accessibility walkthrough

The other Q procedures (clean preparation, operation proof, catalog admission, failure isolation) and the evidence
record are in their own frozen file, [q-procedures.md](q-procedures.md). Every study run produces that file's
evidence record.

1. This frozen design and its binding manifest (step 1).
2. The study, run by independent moderators (step 2).
3. The records of §7.2 (step 3).
4. **Accessibility and UI walkthrough** (H14, bullet 5), by the independent reviewer on the installed artifact, with no
   console and no AI help:
   - keyboard only, T1, T2, T6, T7 and T11: every control is reachable, the focus is visible, shortcuts are
     documented in Help, and there are no shortcut conflicts;
   - VoiceOver: every control in those tasks has an accessible name and role, and state changes (loading, error,
     saved) are announced;
   - 200% text and window zoom, the minimum window size and a resize during a drag: no clipped control, and
     screen-to-data mapping exact (T6 points within 3 px);
   - reduced motion and increased contrast: no information carried by motion or colour alone;
   - the selection, dirty, loading, error and recovery states are each visible during T7, T8 and T13;
   - for every manual child in those tasks, where the researcher finds it, what changes while it runs, where the
     output is kept, and how an error is recovered (acceptance.md §4).

   Each item passes or fails with a screenshot or recording. Any failure fails Usable for the affected groups.

Evidence: the frozen design and manifest hashes, session recordings, scored outputs, the locked dataset, the analysis
JSON, the walkthrough checklist with its evidence, and the evidence record.

## 10. External gates (needs Will)

- **needs Will: fund the study he set at N = 250** (Amendment 3): 250 completers plus 8 pilot participants at about
  USD 360 each, about USD 93,000 (§2.4), or make the §11 lower-N funding amendment with its stated power.
- **needs Will: name the independent qualification reviewer** (not a Hooke implementer), and add their SSH public key
  to `contract/qualification-signers` by PR.
- **needs Will: recruit 3 sites and moderators.** Moderators are trained on the script and not on the implementation
  team.
- **needs Will: provide 3 identical reference Macs** (§3.2), one per site, and an ethics review or exemption.
- **needs Will: choose the H10 held-out extension** after the SDK freeze (it defines T14).

## 11. Amendments

Allowed before the binding freeze only, each dated, with its reason and the new SHA-256:
- the pilot's five changes, exactly as §8 lists them (wording, raising caps, replacing an unmatched variant, raising N,
  fixing the moderator script, logging and scoring);
- tightening a margin, or adding a task or a rule, for any reason;
- Will's single funding amendment that lowers N, stating the power it accepts (§2.3, route 2);
- moving a U task to §4.1 if the coverage reviewer finds a napari equivalent (§4.3).

Never allowed: widen a margin, raise α, drop a task or a rule, change an oracle's tolerance after a pilot result
suggests it, pool data across bound identities, or make any change after any final-study data exist.

**Amendment 1, 2026-10-20** (the coordinator's review of PR #376, before any pilot or final data; the earlier hash
was `a69e62d5a2ad94ad711b34aa452c44a208a52533a2408fe33fb14341f1b025fa`):
- the Q procedures other than Q.usable-workflow moved, word for word, to [q-procedures.md](q-procedures.md), frozen
  on its own;
- §2.3, §8 and §11 now give the same two routes for changing N and the same list of pilot changes;
- §2.3 now states what the power figures leave out.

No margin, α, rule, task, oracle, tolerance, count or cap changed.

**Amendment 2, 2026-10-21** (proposed in PR #389, adopted in PR #399, before any pilot or final data; the hash before
it was `36570385246713b7575413d4e49336ed3b96a7bf35b1cd1e4423b9a4fc35bf2f`):
- §7.4 now says what happens when the logistic GLMM has no finite estimate: the H3 sensitivity analysis is the exact
  conditional test on the discordant pairs, judged against the same −0.15 margin.

It adds a rule and widens nothing. It is within §11's allowed list ("adding a rule").

**Amendment 3, 2026-10-21: a founder decision.** Will decided on 2026-10-21, relayed by the coordinator, that
the final study has **N = 250 completers, exactly**. This is an explicit founder amendment:
- It overrides §2.3's routes for changing N, under which only the pilot could raise N.
- It is **not** claimed to fit §11's list of allowed amendments.

Its reason is the finding in PR #389 (`proposals/README.md`, A3): with H2 on the composite time, 120 completers
gave only 0.09–0.28 probability of passing all 15 comparison tasks when the apps are truly equal. 250 gives
0.82–0.96.

Changed with it:
- §2.2's counts and blocks;
- §2.3's table and the Hooke-only threshold (211 of 250);
- §2.4's budget;
- §4.3's and §6's counts;
- §7.4's "all 250";
- §8's pilot rule;
- §10's gate.

No margin, α, rule, task, oracle, tolerance or cap changed. The pilot may still raise N, and Will may still make one
funding amendment that lowers it, stating the power it accepts.
