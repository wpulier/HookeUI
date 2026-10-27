# HookeUI Beta — scientific workspace candidate

**Draft for review.** Version, build, app source, artifact hashes and update offer are not yet assigned. Finalize these notes only after the frozen candidate passes its required checks and exact packaged walkthrough. No publication, installed-app acceptance or Part 1 completion is claimed here.

This candidate brings the scientific workflow into one Project: import an image, choose and prepare tools, segment, correct and compare results, retain a choice, measure, animate and export, then save and reopen the work. Measurements can continue into editable tables, analyses and graphs. Saved Studies bring the research question, captured inputs, provider readiness and evidence review into the same Mac workspace. Clearer input/model context, left-side Controls and explicit saved/draft/failure states help keep the next action visible.

## Walkthrough to verify on the packaged candidate

1. Install through the Beta updater when this candidate is offered. Choose when to restart. Beta keeps its own local data and backend, separate from Stable.
2. In Models, import the supplied OME-Zarr reader, Cellpose and animation companion ZIPs. Review their requirements and prepare them. Local workflow review and explicit Enable remain separate from preparation; nothing runs merely because a package was imported.
3. Create a Project. Unzip the supplied `human_mitosis_crop.ome.zarr.zip`, then import its folder through ordinary input controls. Check the selected image, axes and scale. This is the same pinned 128 × 128 uint8 CC0 David Root human-mitosis crop used by the retained scientific evidence; it is not biological ground truth.
4. Choose Cellpose for the saved input, inspect settings and run. Inspect the produced labels, correct known objects, try Undo/Redo and explicitly save accepted edits. Compare alternative results on that same captured input, then retain the chosen result.
5. Continue from the accepted correction into measurement. Inspect definitions and units, then open the saved measurements as an editable table. Add a condition column, assign groups and explicitly exclude any rows you choose; Save the table. Use Analyze or Graph on that saved revision, select the calculation or chart, run it explicitly and save its result. Export CSV and graph SVG/PNG; use native export for scientific arrays. Keep the original segmentation and alternatives available.
6. Export the current scientific view or a selected region as PNG when you need a presentation image. This captures the displayed preview; use native export for full scientific pixel data. Previous exports retains those exact files. Choose animation for the saved scene. Create and edit keyframes, set timing, preview and export a movie. Save the tool document explicitly.
7. Close and reopen the Project, then quit and reopen Beta. Confirm that saved input, corrected labels, chosen result, table edits, analyses, graphs and animation state return without rerunning the scientific operations. Inspect exported data and movie independently.

For typed questions about a supported saved image, use **Use with Assistant** in its document,
then confirm the attachment shown above the composer. The attachment keeps that saved revision
when the question receives focus. Remove it explicitly before returning to Talk on the Stage.
Verify this on the packaged candidate; source-level checks alone do not establish the live-provider path.

## Studies in the Mac workspace

1. Open Researchers → New study. Name the question, describe success, set an allowance and select saved inputs. **Save study draft** retains their exact revisions and starts no research. Reopen it from Researchers.
2. Choose Claude managed research or the separate Gemini adapter. Read the preparation status before Start. Scientific inputs require private staging, reviewed tool permissions and the applicable provider connection. A configured connection is not proof of live access. Unavailable preparation keeps Start disabled.
3. Once the live route is qualified and ready, review the instruction, session budget and reserved allowance, then explicitly Start research. Pause prevents new starts; active work continues until Stop is confirmed. A reserved allowance is not an invoice or a guaranteed provider spending ceiling.
4. Inspect retained transcripts and named outputs. Review the cost attached to that evidence; Gemini currently records its allowance as an estimate. Write your review and explicitly choose Accept result, Record failure or Record cancellation. Reopening saved work must not start or resume a researcher.

**What has been checked:** ordinary browser use over retained local scientific data completed measurement → editable table → analysis/graph → CSV/SVG/PNG export and restart/reopen. Study creation, provider selection, disabled unavailable actions and unchanged draft reopening also passed. Saved work now restores when the camera bridge is unavailable. These are local application observations, not verification of the final Mac package or a live cloud investigation.

**Still to qualify:** actual Claude-managed research, Gemini tool use, private GCP staging/GPU execution, interruption/recovery and accepted results through the packaged Mac app. The shared implementation is present; those live journeys remain unverified. OpenAI access is an unavailable follow-up and does not block this candidate. No hosted web app or sharing service is included.

## Limits to retain in the final notes

- These companion packages support bounded declared routes. Local functional readiness is not independent scientific qualification. Unsupported inputs must explain recovery; never silently reinterpret axes, calibration or labels.
- Saved-input typed Ask and the shared cell-tool input bridge currently support uint8 Y/X intensity images up to one megapixel. Other data needs an explicit supported conversion. Native Ask does not yet support Talk or researcher handoff; it must explain that limit rather than switch to the camera.
- The reader supports the declared uncompressed OME-Zarr subset. Compressed chunks and unsupported label/pyramid layouts require a supported exported copy, keeping the original.
- The native folder bridge's 64 GiB storage ceiling is an admission limit, not verified workflow capacity. Large-data qualification and exact installed-package behavior need their own evidence.
- Animation preserves the supported camera/dimension keyframe state; do not imply arbitrary layer-appearance or generalized automation support.
- Table groups and exclusions are user annotations, not biological validation. Native table row highlighting remains unavailable. XLSX export’s Project-ownership repair still needs its actual packaged walkthrough.
- Unsaved tool changes remain drafts. Use Save tool and confirm saved state before closing or restarting. A saved layout alone does not save every plugin's transient state.
- Compare completed methods from their original saved input, rather than an equal-pixel image copied into a result document. Data → Show masks can open a duplicate window, and restored windows may cover comparison text; Expand the comparison or close the redundant visible window. Closing a window preserves its saved result.
- Local interactive review works through the package-level Review action. The member-level Review results link is repaired with focused checks; its actual packaged click and some summary wording remain to verify. The exact review distinguishes local functional readiness from scientific qualification. Native text entry and older saved-tool labels also need attention in the packaged walkthrough; do not treat the retained readiness check as complete usability acceptance.
- The setup sheet names the tool and current setup state. Setup details exposes the latest diagnostic status and software location; it is not a complete streaming log. Close hides the sheet while work continues; Models → Continue setup returns to it.
- Explicit saved-label measurement requests through typed Ask use the same operation as the UI. A prior bounded researcher invocation reached measurement but stopped before its report; generic researcher document reads currently expose table schema/provenance rather than numeric rows.
- Interactive package updates require an explicit candidate trial, exact result review and Apply. A post-Apply restore/render check does not rerun the scientific operation. Other maintenance policies retain their documented boundaries.
- Ticket 64's remaining continuous-use, reopen/export and owner acceptance are separate. Earlier physical camera/voice evidence does not certify this new packaged build. Final release notes must state the exact accepted scope and any remaining limitations.

The full continuation plan remains intact. Part 1 delivers the extensible Mac app and qualified GCP-backed researchers through accepted Beta and explicit Stable promotion. Part 2 resumes advanced local batch/automation/training, broader built-in integrations and hosted/cloud features. Part 3 retains Windows/Linux, independent qualification and contributors, the 250-person study, adoption and public launch. This candidate is not a full-program completion or public-launch declaration.
