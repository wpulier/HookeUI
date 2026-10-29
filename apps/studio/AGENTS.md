A Project with retained Studies uses its empty stage as a return point to those Studies:
question, recorded experiment/input/report counts and Open Study. Counts are history,
not running-status or scientific acceptance. Existing Data and Inputs remain the ways
to inspect material or choose a camera. Opening starts nothing. Project switches clear
old summaries immediately; late reads and stale buttons cannot act on another Project.
The Assistant retains its same connected-agent entry and current-Project setup prompt.
The Inputs drawer's empty message describes its picture/video list only; it directs
scientific-file and saved-result users to Data without claiming their Project is empty.

The Assistant offers a compact connected-agent entry inside each Project, including the local-only example.
It names confirmed, non-revoked Hooke connections without claiming current activity or Project access;
Settings owns connection and Project grants. Copy setup prompt uses the channel's returned playbook,
adds the current Project locator at click time and sends nothing. Clipboard failure shows selectable text;
unreadable setup never guesses a channel. Example Assistant remains off and opens the Project chooser,
rather than offering an implicit new Project. This is client-neutral; named providers are not compatibility proof.

# The studio

A Study's home is laid out as a researcher's Tab (workspace-feel step 7, the mock's `studyHome()`; `study-view.ts`
`renderStudy`, root `.study-home`): on the left, Experiments by phase (Needs you, In progress, Done, each a row with its
lit cell, its name, who runs it and its state; a row brings its step into view and lets it glow), then the inputs
frozen at their versions, the researchers that can run it and Study setup (a disclosure where `workspace-study.ts`
mounts Copy large inputs to Google Cloud, the tools it may use and its GPU jobs; before the first experiment they are
part of its composer card instead). Below 1240 px of page the left column folds into a 28 px toggle ("Experiments and
inputs") before the Study Tab and opens as a 240 px layer (Escape, a click outside or a row closes it, and focus goes
back to the toggle); below 860 px the write-up is the conversation header's second Tab. In the middle its conversation,
one Tab "Study": your question as your words, Judged by under it, then one step row per experiment in the order they
were made ("Started / Planned / Starting / Asked to start <its name>", its state and money as the second line, the lit
cell of its phase or a tick once accepted, never coloured dots or words; its problems, recovery and actions such as
Check for results and Review beneath), then Your move (each decision in words with its one action), then New
experiment and the composer card (researcher tiles, brief, what it works from with the copy and tool steps, both
amounts, Start naming its hold). A Study takes no messages of its own, so its composer (the shared one, no + or model
chip, "Ask the Assistant about this study") opens the Stage bar's Assistant over the write-up and sends the words
through the Assistant's composer (`actions.ask(text)`). On the right the write-up on a
paper page (the question as its title, Judged by as its byline, What we know with each finding's experiments, and Add a
finding), under a status pill with the Study's state. The allowance is said once, in its Tab's
Stage bar (`studyAllowanceWords`: "$0.32 · $0.25 held of $1.00", spent, held and left in its tooltip); spent and held
beyond it is said in the conversation. Pause and Stop sit in the Study's bar. An open Study is a Tab of the row until
its × closes it. The columns scroll on their own; the Stage never scrolls while it is open.

Studies retain local drafts. Managed execution controls depend on configured
providers; the standalone core offers no hosted sign-in or managed implementation.
See docs/CORE-BOUNDARY.md for the optional operator extension.

A tool's run page says where results go (Results go to: This Tab | New Tab; This Tab's tooltip names the Tab,
`destinationWords`), and Run names only its model; New Tab preserves captured input through ordinary admission. On a
researcher's Tab, which keeps its own work, This Tab is disabled, so New Tab must be chosen explicitly. Global tool-control openness
and placement restore per Project/Tab without moving native owner controls or restarting work.

Workspace layouts retain formats 2–4 and use schema 5 only for new Tab/presentation fields.
Duplicate and recovery remap comparison lanes with their placement IDs; moves and closed
windows prune absent lanes after retaining pending result placements. Fewer than two lanes
ends synchronization without deleting the retained results or changing their revisions.
Transfer Undo restores its prior lanes only when no newer comparison edit superseded them.

Specimen-menu refresh updates choice buttons in place by captured Project opening and source identity,
so routine polling preserves keyboard focus. A new Project opening replaces those buttons instead
of changing a focused action's owner; labels and list positions never establish identity.

Tab keyboard commands share the native menu and existing Project navigation. Focused
scientific controls keep handled shortcuts; Command-T reaches their transpose action before
the workspace New Tab fallback. The native New Tab menu intentionally has no key equivalent,
and native Tab dispatch applies only while the main Studio window is key. Transfer dialogs,
dragged Data revisions and queued Reveal retain their captured Project opening.

Project `session-cleared` retires native presentation windows and pending deliveries after
server lease reset. Saved arrangements reacquire exact sources on return; same-Project
hidden Tabs retain valid presentations. Socket/manual/report sequences and scientific
sessions remain with their existing lifecycle owners.

Tool setup names its selected tool, uses structured queue state and measured download bytes,
and keeps raw latest messages, locations and licence details under Setup details. Automatic requirements from a refreshed plan are not repeated as future work during a running attempt;
setup may reuse prepared software, so it promises no unnecessary reinstall. Install
progress remains indeterminate because the queue exposes no structured subphase or full log.
Failures open Details by default; Retry and exact-attempt Stop remain explicit. Close retains
the setup reference for Models → Continue setup without restarting work, and reopening keeps
the original Project/recipe guards. Readiness, local result review and Enable remain separate.

The selected Tab, work or researcher alike, takes the one selected fill of the Tab anatomy (step 2 below), including
after the generic quiet-button styles. Control placement and width remain available in a page header's ⋯; closing
that menu never stops a tool.
The Tab row (44 px) and the Stage bar under it (32 px) own grid rows of their own above the Stage
(`workspace-views.css`), on every Tab. Restoring a transferred document reuses its exact Project/Tab placement
and retained resource revision when its renderer key predates placement assignment;
explicit copied placements remain separate windows.

Library metadata saves serialize by asset and field across preview replacement; reopening adopts the pending
draft and closing preserves its typing-pause save. Only unsaved/in-flight state is retained. Saved applies
only to an acknowledged current field value; typing marks it unsaved, and Retry reads the
current draft. Open in Tab belongs to the Project opening that created its chooser: a changed
Project/generation or pending navigation refuses, and a new Tab must be the exact created target.

Interactive update candidates appear as **Try update** choices in a saved scientific document,
separate from ordinary tools and comparison arms. Models details expose exact candidate result
review and **Enable this update for Apply**; neither changes the active version. Apply sends the
captured generation and receipt digest. A changed candidate refuses instead of substituting
another version. Opening, controls, Save tool and reopening remain explicit ordinary actions.

Models Try uses the workspace run's retained completion placement; it does not open
a second automatic Measurements window. Ordinary Measurements Open reveals an existing
table in the same Project and active Tab at the exact table revision (legacy absent
revision means zero), retaining its placement, geometry and content. Explicit restored
or copied placements remain distinct, as do different results, revisions and Tabs.

Local interactive-tool review uses the existing Models result sheet and explicit Enable
action. A member's Review results opens its owning package's exact-generation review;
ordinary member execution still keeps its distinct member identity. Its evidence is labeled Captured input, Saved state and Reopened result, with
completed local controls/Save/reopen wording; it never claims a reference comparison,
scientific accuracy or export fidelity. A workflow setup step directs the person to a
saved Project trial instead of offering a repeated automatic install/proof loop.

## Current workspace (2026-10-16)

Record opens a scope sheet before any browser permission request. Camera source is
selected initially and uses the bridge's existing studio owner and dataset lifecycle,
without screen capture or microphone. It requires the live camera source to be selected.
Workspace keeps the existing screen+available-camera behavior but explains the system
chooser first; app audio and microphone are separate opt-in choices. Annotated view captures
only the existing input canvas (picture and drawn Hooke overlays), without panels, other windows,
a screen chooser or audio. It can show the live source or saved media already on the input stage;
it does not capture separate scientific result windows. It uses the same recording upload and dataset
lifecycle but requests no extra studio camera owner. Missing canvas capture or a hidden/absent input
refuses; Project/Tab/input or visibility changes stop its owned recording and explain why.
Data recording cards resolve their exact Project and recording ID into the existing Library preview; Open in Tab retains that movie as input without replacing the live Tab. Missing, still-recording or foreign movies refuse.
The start sidecar retains the bounded workspace/annotated scope and Project Data names it; older requests default to workspace, while older files keep Screen recording. Dataset collection may still retain camera video through its separate dataset owner.
Synthetic track/HTTP checks are not encoded-pixel or packaged media acceptance. Camera-only completion
requires new frames from the exact started path and no camera/sidecar failure; Data
remains the authority for final dataset durability. Failed startup releases only an
actually requested studio owner and ends the attempted dataset. Repeated Stop finalizes
once. Dataset commands require acknowledged collection state. Unconfirmed dataset Stop
or bridge studio-owner release retains Retry Stop and blocks a new recording; retries
never force-stop another owner. Unconfirmed Begin stays visible through completion. These source paths require exact packaged recording/media acceptance.

October 22 owner acceptance changes input navigation: Inputs always offers Live camera.
Saved image/recording cards preview without changing the source; Open in Tab creates or
reuses an input Tab. Ordinary imports, examples and the camera menu share that navigation.
Tabs persist source references and recording positions in layout format 4; legacy Tabs
inherit the formerly shared source on migration. Restore uses the existing source command,
paused for saved media, with Project, save, busy-operation, recording and voice guards.
A refused transition leaves its saved Data and prior Tab intact; unavailable input cannot
admit analysis. Previewing never starts analysis. Scientific retained-resource operations
still use their explicitly selected accepted revision, independent of the live source.


Saved scientific headers preserve canonical user titles. Only an exact generated Population title, matched
against its recorded source and supported retention operation, displays the shorter “Image and labels”; the
canonical title and full identity remain in Document identity. Controls keep the exact saved input, revision
and explicitly recorded selected tool visible above the inspector sections. Missing or foreign choice metadata
never supplies a guessed model name. Newly retained reader workspaces name their captured source and prepared
reader when sourceReader agrees with the selected runtime, Project and retained source metadata. Exact integration
identities remain under Reader identity; old records acquire no invented name and custom titles remain unchanged. This context is not a claim that private edits have been saved.
Explicit next-tool and saved-output actions can expand a newly opened foreground window using the existing
Expand/Restore shell. Existing placements, explicit geometry and background restoration never opt in.
Saved document item order follows the visible bottom-to-top stack; background restoration keeps that order
below any newer explicit selection. Reopening an output cannot bury it beneath its restored source windows.
This saves presentation only and neither opens a private session nor accepts scientific changes.
Background restoration leaves scientific Controls closed; explicit Controls/selector actions raise only their visible owning document without expanding it or running work.
Earlier windows, drafts, sessions and errors remain mounted; no close, Stop, Save or execution is implied.
Left/Float/Right placement and its remembered preferences remain independent. Tool choice names its next
Open action and continues to capture the exact accepted input and generation; execution is separate.
Retained image/label landing pages show Continue and saved-data actions before historical provenance;
unsupported provenance and changed-input warnings stay visible. Focused DOM checks are not a rendered
workspace or installed-app qualification; the frozen Beta walkthrough verifies those presentation claims.
Population provenance accepts legacy format 1 and explicit-association format 2. Both retain original
producer/revision, correction descriptions, source checksums and runtime limitations as inert text.
Format 2 additionally names its recorded image→Labels components, explicitly distinguishing attribution
from missing producing actions/inference timing. Basic missing/mismatched association or current-input
warnings remain outside the disclosure. This display does not admit lineage; the shared method-review
service verifies exact accepted source/component provenance. Legacy records acquire no inferred association.

Scientific View's marker/shape styling and feature text use the shared current-frame owner and native setters.
During an admitted annotation request, its mode selector remains available for a queued choice, just as Labels
settings do during their own settings request. Other edits/actions remain blocked. The queued choice waits for
the returned frame's draw receipt; failure, Stop or changed session/layer discards it. An unrelated request does
not gain this availability. Actual keyboard selection remains part of the native browser walkthrough.
Equal native colormap limits remain displayable and editable. Remember this view retains its supported presentation
settings; its guidance explicitly excludes marker styles, shape styles, text content and shape z-order until those
preferences have their own persistence implementation and evidence.

Saved scientific windows expose Measure in the same inspector as editing and saving.
Measurement and native export capture the accepted revision, never unsaved viewer pixels.
Spatial instance-label measurements retain exact decimal object IDs, counts and pixel
centroids, with source provenance and a CSV. Before Measure, the saved-array listing shows
the shared metadata-only plan: grouping by label (including disconnected parts), background,
object limit, output units and column methods. The labels' own supported axis-aligned calibration
adds area or volume; absent or unsupported calibration explains why only counts are available.
Time/channel stacks and class labels show their selection/conversion refusal before execution.
Missing or inconsistent plan details keep native export available but do not offer Measure.
No intensity relationships or calibration are inferred. Typed tables show their retained column
units and methods, including calibrated area/volume alongside pixel centroids. Changed saved labels flag retained
measurements as earlier work. Publication is explicit, duplicate requests are guarded,
and opening the new measurement revision never retargets a running private session.
Native ZIP export includes exact NPY values and scientific metadata; movie export stays distinct.

Saved scientific Data and retained population-label windows expose **Compare saved methods**.
The chooser reads the session resource index using its `day`, `entries` and `unreadable` contract;
it validates document owners/revisions, reports unreadable data, and preserves endpoint error guidance.
Scientific array/comparison replies separately require their explicit success flag.
The current saved document supplies an exact native input; each of two/three arms selects a saved document,
an explicit revision and a label component, or declares a failed/cancelled outcome. Review displays shared
metadata readiness and missing evidence before Retain is enabled. Editing any choice invalidates Review;
an interrupted retention preserves the same request for the same choices. Stop cancels owned comparison work.
The retained comparison window shows area/counts, differences against A, disagreement, producing histories,
recorded timing scope and limitations. Failed arms have unavailable measurements. A rationale is required to
choose a completed arm; reopening or exporting never reruns a producer. Native evidence exports through the
normal Project bundle. Declaration-only failures are explicitly unverified; the UI does not invent a terminal history.
In this bounded slice, source selection starts from the open saved document, arms can select older accepted revisions,
and missing coordinate identity directs the researcher to retain the explicit input grid before comparing.

Inputs and Project Data expose Import native bundle for Hooke provenance ZIPs. The
shared sheet retains the selected file across dismissal and sends its original bytes
through `/api/scientific-bundles`. Indeterminate progress never claims acceptance early.
Opening an accepted result holds the same pending guard as upload: file selection and
another import wait until that exact Project opening finishes. Failure preserves its
validated target for Open retry without reuploading; no late chooser event replaces it.
A lost/refused response keeps the file for retry. Native receipt validation is separate
from Project ownership: only the backend's matched/restored owner, naming the exact
target session and an origin digest, enables Data refresh and Open. Missing, malformed,
unavailable or mismatched ownership leaves Open unavailable even when native data was
imported. Matching a local date/counter ID does not infer ownership. Legacy resource-only
imports cannot claim restored Projects. A restored identity explicitly excludes the full
conversation/workspace. Opening remains an explicit action; no producer is executed.
The backend owner protocol and actual browser roundtrip must be verified before claiming
fresh-profile recovery; the Studio control alone cannot grant that qualification.

Scientific plugin controls distinguish interaction delivery from analysis completion.
An already painted plugin action can wait behind an in-flight background result check
without losing its pointer input. The shared document queue preserves the captured
panel paint and refuses changed Project/Tab/placement or stopped sessions; duplicate
input while queued does not execute again. Foreground actions, editing and pending
saves still block plugin interaction. This synthetic regression is separate from
actual native-panel and installed-app qualification.
A successful refresh does not erase the previous callback/transport problem; the
researcher can inspect and dismiss it without replaying the action.
Live-result observation explains when edits, saves, code, playback or hidden windows
pause updates. These messages come from the same admission checks that preserve
drafts and ownership; checking for results never reruns the original analysis.
Refresh preview remains available while a background result check finishes; its
render waits through the same operation admission as View and Python. Foreground
work, editing and a missing session still disable it.

Saved scientific tools expose versioned recipes in Save. Starting a run is explicit;
each step prepares a working copy through the same tool path, then requires inspection
and acceptance of an exact saved result. Manual corrections are review checkpoints.
Reopening never executes a recipe. Unsupported versions refuse without changing work.
A run may start on another exact saved input, chosen in the recipe page. A researcher can
record a step attempt as failed; the run then says it is partial ("Partial: k of n steps
accepted · step k+1 failed") and lists each failed attempt with its kept working copy.
Scientific sessions offers browser-local earlier-window recovery in pages. Select the
original Project and work Tab before reopening; entries are identities, not proof of a
running worker. Recovery capabilities remain browser-private. Unsent specialist-panel
text defers panel refresh, but never blocks completed image results from arriving.

The default `/` and `/index.html` entry points now load `src/workspace-study.ts`, as does
the compatible `/workspace.html` link. The module mounts the shared HTML shell and initializes
`main.ts` once; do not initialize both entry modules from HTML. The older descriptions below
document the shared controls and their owners, rather than the current navigation placement.

Projects are across the top; the Tabs (yours and your researchers') and the Stage bar above the Stage; the Working
strip, with a pill for each researcher and run at work, below it.
A Project owns work and Data; Tabs are saved arrangements; Windows are placements of resources.
Library is the global asset library. Datasets package assets with Project metadata. The Agent,
Data, Tools and Researchers retain their existing service owners. A new work Tab shows the
shared specimen feed; changing Tabs never changes analysis input or acquisition.

Projects opens Recent projects from the header at every supported width; each row's line counts in words
(`sessions.ts` `projectRowWords`: "1 input · 1 conversation"). The Hooke menu's Scientific sessions opens the sheet of
that title (the running tool sessions and earlier-window recovery). The Tabs row owns
the single new-Tab control; closing a Tab offers Reopen and keeps it under Closed in ⌄ All Tabs
without deleting its arrangement. Controls dock beside the left rail, leaving navigation
reachable. Rail counts come from current Project results, active work and actual readiness;
they never substitute for execution evidence. Data lists by what each thing is, with one Made by filter; every card
action remains available in its row's ⋯ (`menuActions`), read when the ⋯ is pressed, so refreshed results never
change a menu that is open.

In `data-roles.ts` every listed entry belongs to exactly one section (Other catches the rest), and the
rail's Data count counts Data's rows. Data's Open in Tab… opens a result in a chosen or new Tab
only once that Tab is in view. Camera, Record (with its Data light) and Assistant are on the Stage bar's right (the top
row is Projects only, the Tab row Tabs only); the camera menu opens under its control, and the Assistant control is
pressed while the Assistant is open and closes it. An opened tool says what is on the Stage in
the header's words, a fact, never what the tool reads. A Library field says Saving…, Saved, or Not
saved with the reason and Try again. The Assistant's starters are its three jobs (look at the
specimen, run a model, start a researcher), shown until the conversation itself begins (Activity's
own lines do not end them); its specimen is one chip that shows it (or opens Inputs to add one); in
the example Project, where it is off, one card says so instead of starters; the walkthrough card
stays on the Stage. A conversation is renamed in place, never through a blocking prompt. Activity's Problems only hides, never removes, entries and gives way when an
answer's ⓘ asks for its request. Its empty message says only that no failures are recorded, never
that running work succeeded. Inline conversation rename captures its Project; switching Projects
cancels the field, and the server refuses a delayed rename against another Project. Enter/Escape
restore picker focus; blur keeps the next control usable.

Close/Reopen is browser-local; Remove/Restore arrangement is shared and recoverable. Layout
schema 2 has stable placement IDs and stage-relative geometry; old schema writers are refused.
The default workspace negotiates `restoration: project-layout` in its accepted hello. Project arrangements alone reopen retained windows; legacy native snapshots remain for older clients. Saved view controls restore sequentially against accepted revisions, not four simultaneous manual writes.
Pinned measurement links in the current Project, including the example guide,
open retained document windows. The population layer’s Table action carries the displayed
Project, resource and saved revision into that same window path. Changed ownership
or revision before the controls refresh refuses the old action; missing saved identity
disables Table. Original/Current use the same captured identity to open retained mask windows
at revision zero or the displayed accepted revision. They refuse changed ownership before
controls refresh and never update only the hidden legacy preview in the current workspace.
A different current Project requires opening the result’s Project first. Guided progress acknowledges exact rendered rows
only while their window is visible in the current Project/Tab; background restore,
failed reads and dismissed or hidden windows cannot advance it.
Reopening a retained document reveals its artifact container before the workspace
captures geometry. A hidden container must never turn a saved window into a zero
rectangle. Rows clipped outside their window or viewport do not acknowledge progress.
Project Data Reveal raises the exact retained placement through its document shell or the native window
action owner. Delayed restoration cannot raise a window after a newer Reveal or Project/Tab change.
Notes and layout recovery retain separate writer/draft identities. Completion associations are
durable and placed against the latest destination arrangement, independently of result saving.
A stale arrangement save may include proven server completion placements only: the existing journal
must show a contiguous completion-only chain within its last 64 events, with exact admitted origin,
placement identity and otherwise unchanged layouts. User edits, removed destinations, conflicting
placements, schema/capacity changes or unavailable history require explicit recovery. The Studio
applies the returned additions without replacing edits made during the save, restores exact new
windows and preserves pending placements during capture. Restoration does not block user edits;
failed restores keep recovery and never report Saved. An exact lost merged reply can replay only
the latest accepted journal receipt; it cannot overwrite a subsequent user save.
Scientific Python accepts UTF-8 `.py` drafts up to 256 KiB. Opening a script never
executes it or overwrites an existing draft without an explicit choice. Run keeps
the same accepted-resource/rendered-frame and cell identities; larger source uses
the bounded script route. Saved history displays source inertly; transcript and
total history bounds are unchanged. After 128 confirmed cells in its current session, the console disables Run
and explains how to keep the transcript, accepted Data and draft before a new session. Earlier-session cells
remain visible without consuming the new session’s count; server admission remains authoritative.
Stop and Interrupt retain distinct semantics.
See `../../docs/research/workspace-study/2026-10-16-workspace-recovery-implementation.md` for
contracts, browser evidence, limitations and the backend integration surface.

Part of the working context the root `AGENTS.md` maps. "Above" and "below" may mean parts it lists in
other files.

The studio is the workspace and the library (`docs/design/2026-10-12-workspace-and-assets.md`; Will's design of
2026-10-12, late: the session is the timeline, assets are the footage):
- **The workspace** is where you always are: the stage and the chat with one session loaded. The header: the session's
  title, top left, is its switcher (`session-menu.ts`: search, + New session, every session with a picture of what it last
  looked at, rename in place, and All sessions ›: the full gallery page, `#sessions`, `sessions-page.ts`); then the camera, one control (its state; a click opens its checklist, the phone's controls,
  Reconnect and the camera page: `phonepanel.ts`); Record with its Data light inside; Datasets; Models (its picker under
  it); Sessions (every session as a gallery, `#sessions`); All assets; the setup chip only when something needs you; and the gear (`gear.ts`: Example walkthrough, Behaviour,
  the Toolbox, the blind test, sharing the tab, and test footage, a developer setting; no Models row: Models is on the bar). Datasets and Models are on the
  bar, not in the gear (`docs/design/2026-10-13-researcher-ready.md`: nothing core hidden).
- **The side bar is the session** (`sidebar.ts`): this session's Assets, Made, AI researchers and Activity, under its name
  (the background helpers are "AI researchers": the people using the app are researchers).
  The tray that once held Made, Researchers and Activity is gone.
- **Actions** (`actions-panel.ts`; `docs/design/2026-10-13-actions-panel.md`) is the side bar's lower half, as wide as
  the whole side bar (Premiere's lower panel), a divider between it and the section above: everything the agent can run
  on the view, run by hand. Its rows are `contract/capabilities.json`'s agent capabilities with a tool in
  `contract/actions.json`'s families (less its agent-only ones); whether each can run, and its step, are the
  `capabilities` message's; its forms are the tools' own schemas (`GET /api/actions`). Run is the studio command
  `action {capability, args, use?}` through `run_tool` (the tool the agent would call; `use`, "Run with…", through the
  agent's own `use` first), said in Activity as the user's. It acts on the selected outline, else the view; a cloud
  model asks once a session. The dock's view tools (Whole frame, spots, the map, the cells' layer) moved in.
  `test_actions_panel` runs every row it offers through `action`; sim scenario `actions`. Plain words: each argument's
  label, choices and default are `contract/actions.json` `fields`; Common (`common`) is first, the Measurements table
  its first row; an action that made cells or motion says what it measured (`tables.made_words`) with its Table.
- **Tools** (`actions-panel.ts`, `run-spec.ts`, `tools-catalog.ts`, `model-comparison.ts`; the workspace-feel plan's
  "Running a model", the mock's `toolsDrawer()`; Will, 2026-10-20: "a researcher will reach to run a model") is the
  Actions panel as the workspace shows it, and one list with Models. By task is one row a task: a fixed 20 px icon
  column, the task's name, the model it will use beside its lit cell and "+n more" (no chip walls); tasks that can't
  run here and catalog kinds not set up yet wait under **Can be set up** at the bottom with their one step; a catalog
  entry that implements another task's model choice (Analyze Particles, InstanSeg: their kind's operation is that
  task's role) is no second row: it is a model of that task, its own settings showing when it is chosen there. An
  entry contract/actions.json `ownTasks` lists is a task row of its own by that name: Bright objects is **Find bright
  regions** in Common ("Bright objects · built in"), never a cell finder's row (a threshold finds regions, not cells),
  though it stays a choice in Outline every cell, where its declared output words follow it. By model is the wire's `models` in Models' sections and counts (Needs you, Ready, Not checked, Can be
  set up, then Not on this Mac folded, then New), five a section until **Show all N**, each row its lit cell, one quiet
  line and its next step; a ready model's row opens its first task with it chosen, another opens its card in Models.
  A task that compares (cells, outline) has **Compare models…** in its page's ⋯: up to three ready models ticked in
  place, each given its letter and colour (`COMPARE_COLOURS`, used nowhere else, a test keeps it so); a cells
  comparison opens on the Stage as one picture with each model's retained masks in its colour, each hideable
  (`openStudyComparison`), one line of facts on why the counts differ (each model's catalog `what`, never a verdict),
  and "Ask the Assistant which to trust".
- **The Measurements page** (`#measurements/<id>`, `measurements.ts`; `orchestrator/tables.py`,
  `GET /api/resources/<id>/table[.csv]`): a population's cells or a motion item's tracks as rows, sizes in µm only by
  the capture's own scale, sortable, medians and middle halves with mean ± SD under them, Download CSV. Every summary
  number (n, median, quartiles, min, max, mean, SD, SEM, the mean's 95% CI) is the server's, from
  `orchestrator/stats.py` (pure, deterministic code; never a model, never recomputed in the studio). From Made (Table),
  the cells' and tracks' controls (Table), Actions and the agent (`show_measurements`).
- **The empty stage** (`session-home.ts`) is one box under the session's name, "What will you look at?": side by side,
  Open a picture or video (dropped anywhere on the stage, or chosen: it joins your assets and this session) and the live
  camera (its state, Use live camera for a new session, Connect a camera and Reconnect when it has no picture); under
  them a quiet "New here?" strip with Try an example (a real microscope image and a short walkthrough, no account or API
  key: `onboarding.ts`); the newest of your assets with All assets; and a quiet Troubleshoot. A new session opens on it (the app looks at nothing until a choice), and the
  live camera with no picture shows it. That is the default state, not an error.
- **All assets** (`#assets`, `library-page.ts`) is the library, a gallery sorted by what is in its pictures: yours or the
  examples (the switch top right), categories first, a click previews (never the session's), Add to this session, Move
  to trash with Undo. Once the user turns it on (setting Library looks, off by default), one fast look files each new
  asset (`library_looks.py`); with it off, nothing is sent and assets are filed by what the user names them.

The studio's performance meter (`apps/studio/src/perf.ts`, a dot and a word at the right end of the
Working strip under the Stage, with Spending; under a Stage narrower than 760 px it keeps its dot while all is well) is green, amber or red by how far the live view and the computer are from their
ideal (contract/overlay.json "performance": frame rate, camera to screen, frame analysis, click time,
load per core, memory, swapping (pages swapped back in a second over the contract's `windowS`, 30 s, its level left
only once past the line by the contract's `hysteresis`, so it no longer flips every 5 s as on 2026-10-11; never how
full swap is: macOS keeps it near full long after memory ran short), disk); hovering, focusing or tapping it shows the
view now (delay, slide, outlines: `#readout`, no longer on the line itself), each part now against its ideal and the
programs using the most CPU (`orchestrator/machine.py`, sampled every 5 s, the program list only
while it is open). Our own GPU workers (and micro-sam's Python) are sampled on every tick with their
role and the model loading there (`machine.ours`), so when the cause is ours the meter says so ("micro-sam
is loading: the live view is slowed"; live 2026-10-11 it blamed WindowServer), and a `machine` record is
logged whenever a reading crosses one of the contract's lines. Timing results (fps, click p95, the sim's
timing checks) mean little while it is red for the computer's sake.

Its panel also says which models really ran in the last ten minutes ("Models", `/api/health` `models`), so a
slower run is never taken for a regression of a model that did not run (live 2026-10-11):
- **Defaults:** "The default models ran", or each exception with how often and why (`orchestrator/fallbacks.py`).
  Each is noted where it is decided: the operation runner, when an implementation's fallback produced the result,
  and the fast pass, when the full look answered instead.
- **Providers:** each one whose calls did not all return, by what happened: cut off by the app's deadline, rate
  limited or out of quota (429), key or billing refused (401/403), provider errors, connection. This is read from
  the error itself in the one wrapper every provider call goes through (`api_usage.attempt`), which also keeps it
  in the usage ledger.

## Capability setup sheet

`modelspage.ts` keeps Turn on in one sheet. Before admission it says what it takes as one list of lines, first
(its size or that it isn't known here, the room for it, where pictures go, its licence, what it costs: `sheetFacts`,
unknowns said as unknown), then the remaining steps, which are automatic, which need the user, and the server's
download/install destinations. The primary button names downloads or installation. Live progress comes from that
setup's existing queues: bytes only when known, file verification separate from transfer, indeterminate installation.
Its last button says what leaving does: Cancel before anything starts, Close while admitted work carries on (Stop
cancels only its attempt), Done once it is ready; × in its header closes it the same way. Readiness, steps and
percentages are never persisted in journey context. A proof-only recheck downloads no dependencies. Its licence line
is the licence's short name (model-status.ts `licenceName`), the whole text under Setup details; no paragraph stands
ahead of the actions (the primary's label says what starts). Ready leads with the ready lit cell and "<name> is ready"
(its 240 ms settle once; "Ready to use" its accessible words), one quiet line of what holds ("413 MB on this Mac ·
pictures stay on this Mac · licence MIT · free") and what is now true in plain words (`doneWords`: its files are on
this Mac, checked on this Mac; reached from a link, "Found from your link · <site> · <name>" first); downloaded, or
checked but waiting for a review, is a quiet line with its tick, never coloured words. Reached from Add model or tool
(its candidate) the sheet keeps that title through to Ready. Completion retains explicit selection and the existing
Try/input/comparison paths; when the entry serves a task in the workspace its primary becomes Use it here ("Try it on
<specimen>"), which closes the sheet and opens that task's run page with it chosen (opened by a run page's step, the
task of that page, which `doStep` hands over through `openModels(…, { task })` and `ModelsPage.openedFrom` just before
it navigates, when the model serves it; otherwise its first task), unless the job's current choice can't run, when
Use <name> (choose it for the job) stays the primary (step 8 below). Escape closes the sheet alone (its default
prevented, so the Models dialog's cancel never follows) and focus goes back to the press that opened it.

Actions and card Try share workspace admission and owned execution. Their origins bind the initiating Project,
Tab and exact source, retained resource revision or volume. A retained resource never inherits the specimen feed.
Capability tables restore as tables. Background volume admission is queued; its terminal job record supplies the
completion placement after reconnect. Closing a window does not cancel work or change the shared input.

Scientific view Layer stacking uses exact worker layer identities and a rendered-frame-bound
permutation. Position 1 is frontmost. Drafts use the shared navigation admission, stale-view
recovery and explicit Apply; underlying arrays are unchanged. Remember this view retains
the accepted order only for integration-declared persistent layer identities. Temporary
Python layers remain visibly unrememberable until an integration supplies those identities.

Image presentation exposes contrast/rendering, blending and native 3D slicing planes.
Plane position/normal use the currently displayed data axes; they never alter calibration
or voxel values. Invalid settings preserve the draft. Commands retain exact frame authority,
including after nested plane events from Python/plugins. Clipping lists include disabled
planes and refuse excessive shader/response costs before mutation. Remembered-view schema 7
also retains intensity color maps by value (including missing/high/low colors), alongside native 2D/3D interpolation and a bounded custom 2D kernel; schemas 1–6
remain readable without inventing omitted historical settings. Custom 3D interpolation
is refused, matching native napari. These are display settings, never resampling operations.
Unsent, applying and applied navigation states are distinct; invalid drafts remain editable.
Palette names come from the owning native registry; RGB layers have no intensity-palette control.
Restore preflight resolves name collisions and budgets the complete restored presentation before
mutation. Failed restoration keeps the default preview and original saved settings recoverable.

Scientific windows keep the native preview beside an independently scrolling inspector
when wide, and stack them in narrow windows. Inspect, Edit, View, Tool, Save and
Python sections retain the same live nodes and draft owners. Section selection
uses keyboard-accessible tabs and never dispatches scientific work or changes
input. Pending View/Tool changes have visible return controls across sections;

View's Apply/recovery controls stay available while scrolling its settings.
Inspect and Edit own canvas sampling and label gestures only while selected.
An unfinished stroke or uncertain label edit keeps its section open until explicit
finish/cancel/check; switching sections cannot discard it. Native plugin text
drafts also keep a visible Tool return notice until acknowledged or cleared.
Edit's Labels tools set napari's own Labels state at once (the `labels-tool` change,
env_worker/scientific_labels_tool.py): mode, selected label, brush, preservation, connected fill
and edit dimensions, shown back from the frame row `labelsTool` (IDs as exact strings).
An admitted Labels settings request keeps its own settings fields available so committing
one field does not discard focus on the next. Each control receives its final disabled
state once per paint, because transient disable/re-enable also destroys browser focus;
subsequent settings queue on the same
session/layer. Each admitted Labels settings request waits for its exact canvas draw
receipt before releasing ownership to the next queued setting; cancellation or a changed
binding refuses that continuation. Other view changes keep their existing semantics.
The document still locks gestures, history actions, Python and saves.
Failure or Stop clears the queued request; other document operations do not receive
this availability exception. napari's
default Labels keys on the focused preview run napari's own key-binding actions, Cmd/Ctrl+Z and
Cmd/Ctrl+Shift+Z undo and redo label edits, and a polygon is painted from napari's own
polygon-tool vertices (pixel-centred, ending at the pointer). Edit's annotation panel
(`scientific-annotate.ts`) edits Points and Shapes layers through napari itself: a drag is sent on
release, Polyline and Polygon send each click (the second click of a double click is napari's
double-click event, counted from the mouse event before the captured pointer is released;
PointerEvent.detail does not carry that count). A fast second click while the first is still in flight
queues napari's existing Finish action after the exact response is drawn, without overlapping requests;
failure, mode/layer/session replacement, Escape, pointer cancellation, blur or close discards the unsent Finish. Unpressed moves while a shape is being drawn go with the next click,
Shift travels with the gesture, and the worker dispatches it exactly as napari's canvas does
(the `annotate` change, env_worker/scientific_annotate.py). Its buttons and napari's default
The focused preview claims Labels shortcuts and unsent-draft Escape before workspace dismissal; Escape
without a Labels draft remains available to the workspace. It also claims handled annotation keys before workspace dismissal; unowned keys keep their normal routing.
Points and Shapes keys run napari's actions; Undo and Redo annotation edit step through Hooke's
per-gesture transactions, since napari keeps none. Keys go to the editor of napari's active
layer: an annotation mode or layer takes them, otherwise the Labels tools do.
Queued tool changes stay bound to their session and layer. Failed hover requests
cannot continue into annotation actions; stop, replacement and close invalidate
late responses and queued modes. Reopening the same Labels IDs rebuilds the selector.
Annotation Undo/Redo requires the current data, features, styles and selection to
match its expected endpoint. Changing an annotation tool may clear native selection, but does not add a
content Undo step or discard Redo. Only still-current adjacent history endpoints follow that tool's selection;
foreign data/features/style/selection changes cannot be authorized by switching tools. A tool change that
finishes or otherwise changes geometry remains an edit. A failed native restoration keeps both history stacks;
failed rollback stops the private session rather than claiming an applied edit.
Scrolling never changes rendered authority. Session
options retain resource-only start and recovery. History failures remain above the
workbench; native frame/viewport checks still refuse stale interaction after resize.
The scientific preview uses the available window height and width, with a reserved
resolution caption. Camera settings and gesture feedback live in View so their
appearance cannot resize rendered pixels. The canvas and edit overlay share the
same viewport; render requests still use its measured dimensions and pixel budget.

Document headers offer Expand/Restore through the shared floating-window owner.
Expansion follows the visible stage, preserving prior geometry intent and live
content; an explicit move/resize supersedes it. Retained layout remains ordinary
geometry, not a scientific save or a new presentation identity. Long titles stay
single-line with their full text available as the title/accessible name. Wide
scientific windows use one row of section tabs to reserve height for controls.
Native plugin-panel loading keeps its initiating button keyboard-reachable with
aria-disabled admission; repeated activation cannot start duplicate loads. Session
replacement or closure suppresses late success/error presentation and keeps text
drafts. These component guarantees do not qualify native keyboard usability.
Escape leaves native plugin controls for their refresh button without propagating
to the workspace's close-window action. Panel failures and explicit refresh recovery
stay beside those controls; an old duplicate error must not remain above the image.
Image-preview failures still remain in the document and never replay a plugin action.
When live results pause, the session status expands to keep Resume outside the
scrolling explanation. Long messages wrap within bounded text areas; routine
status keeps its reserved height. This changes presentation only: resuming remains
explicit, and pause, saved-data and scientific ownership checks are unchanged.
Expand/Restore and Close share the title row, with arrangement controls below.
Typed scientific memory refusals offer
Review running sessions beside the failure; that inventory never stops or retries
anything automatically. A corrected retry keeps the initiating saved input.


Startup notices distinguish initial connection, connected-but-waiting status and observed
interruption. A missing first health message does not establish that Hooke is stopped.
Automatic reconnect keeps drafts; uncertain work must be checked in Activity before retry.
A request refused before socket send names the workspace connection, says the action was not sent,
and asks the person to retry after reconnection. It is never queued for automatic replay.

Accepted scientific array revisions expose **How these changes were made**: the exact original
revision and producing scientific history, opened as saved Data without executing code. History
includes Python cells, manual edits and contributed control callbacks; it does not promise replay
of arbitrary code. Unknown legacy provenance stays unknown. Ordinary camera/slice navigation does
not consume the scientific edit-history allowance. Retrying history storage never replays a gesture.

Scientific Python Run uses the exact saved resource revision even when its preview is unavailable.
The console retains code and outcomes through rendering failure; a render failure does not invite
re-execution. Camera/pointer controls still require current rendered evidence. This does not yet
make scientific host creation or array publication independent of rendering.

Workspace observation samples current physical pointer/contact hits and keyboard focus before
applying its existing 40-item/16-target bounds. Those referents, selected targets, retained content
focus and pending manual targets take priority over idle windows; excess mandatory targets still
fail explicitly. Report omission never removes a window or grants rendered evidence.

New native Shapes layers have the ordinary Save new layer path: declare shape-set
meaning and coordinate axes, save, then open the retained output without replaying
creator code. Line, polygon, rectangle, ellipse and path boundaries, typed features,
calibration and native styling stay together. A saved output does not claim that
other private layers were saved. Native geometry/typed-state limits remain explicit;
manual Shapes editing and large regional publication have separate open gates.

Scientific Surface windows expose shading, contrast/gamma and foldable wireframe
and face/vertex-normal controls. Apply uses the current rendered frame; polling
preserves unsent fields and stale drafts cannot dispatch. Remember this view stores
schema-9 presentation with the window, independently of retained mesh data. New
native geometry and full scientific edits continue through their separate save path.

Points display controls expose native flat/spherical shading, all four slice
projection modes, antialiasing and canvas-size limits. Apply preserves drafts and
requires the current rendered frame. These controls leave coordinates, per-point
sizes, colors and typed features unchanged. Remember this view uses schema 10;
schemas 1–9 retain their existing meaning and keep current native point settings
where those settings were not recorded. Continuous display values are finite
numbers, not scientific IDs subject to the JavaScript safe-integer bound.

Mixed new scientific outputs use each layer's existing meaning/axis form and
explicit Include controls, then Save selected new layers together. Keep drafts
and selection through layer refresh; removed identities cannot inherit selection.
This calls the existing owned scientific-review transport with the exact initiating
resource, not a rendered-frame token. One returned document contains the entire
accepted set. Open/Continue retains other private work; no successful single-layer
response may be presented as a successful mixed save.

Scientific Dimensions exposes before/after slab margins in world coordinates for
sliced axes. Image layers expose native none/sum/mean/max/min slice projection.
Both use current-frame admission, preserve unsent fields and change only private
presentation. Scientific projection remains an explicit processing operation.
Remember this view uses schema 11 for margins and Image projection; schemas 1–10
remain readable and retain current native values for fields they never recorded.

Dimension sliders and exact numeric indices share the same unsent draft and
current-frame owner. Native range keyboard actions update that draft; applying
waits for the accepted rendered result. Roll axes and Swap displayed axes call
the native Dims methods, preserving singleton/rollable-axis behavior. These
shortcuts are disabled while any unsent draft exists, so they cannot replace it.

Layer grid uses the same frame-bound navigation draft. Rows/columns, signed
stride and spacing are native presentation settings, not array transforms.
Schema 12 remembers grid intent; older views preserve the current native grid.
Camera feedback stays unchanged during a pointer drag so it cannot move its own
canvas. Completion/cancellation updates feedback after releasing the gesture; real
layout changes and stale frames still cancel it.
Single-view label gestures and camera dragging stay unavailable in a grid;
turning it off restores their existing admission rules. Rendering refuses grid
panels below the native 20-pixel minimum rather than allocating huge viewboxes.

Native 2D image/Labels inspection (`scientific-inspection.ts`) is an explicit preview read, not an edit. It keeps exact scalar/RGB values as typed strings, binds to the current draw and session, names projection/multiscale sampling, and drops stale readouts. Its active click mode excludes label painting; Project data and selection stay unchanged.
Visible Points use that same read path with native hit ordering, exact coordinate
strings and bounded typed feature rows. Feature columns are read separately so
mixed numeric rows cannot round uint64 identifiers. A row number is not presented
as an object identity. Missing values remain explicit; arbitrary object/custom
dtype formatters are refused with recovery guidance. Hidden/no-hit points return
no sample, and new frames/sessions clear old feature tables. This does not supply
3D picking or manual Points editing.
The Points and Shapes inspectors offer explicit Select/Add/Remove/Clear through the
existing frame-bound `change` path. Inspection returns an exact `selected`
boolean; a missing/invalid boolean is never inferred from a highlight. Selection
preserves the active native layer (activation callbacks may change row meaning),
so nonactive-layer highlights may be suppressed while the readout remains
truthful. Changing layer/frame/session revokes detached controls; uncertain
responses require refresh/reinspection, never automatic toggle replay. Selection
is private session state, not a retained scientific edit or object identifier.

Restored windows receive ephemeral background intent, including native pictures
through their exact admitted request. Explicit Reveal queued during tab opening
is scoped to its Project/generation and latest Reveal; it runs when restoration
settles. Restoration never reopens a locally dismissed window. Explicit accepted
native restore raises its shell; stale callbacks cannot move a newer Project.
The specimen shell has its own stack group because it is a sibling of the fixed
artifacts stacking context; its order must never be reindexed with child windows.

Unrun Python drafts use browser-local recovery, scoped to the Project, exact
scientific resource revision and placement, with separate writer identities.
Reload offers explicit Restore/Download; it never executes or starts a kernel.
Restoring preserves different current text before replacement. Storage failures
keep the editor and earlier copies, expose retry, and never masquerade as an
empty recovery list. Source size remains 256 KiB; confirmed execution history
and scientific data keep their existing server-owned persistence.

Visible Shapes share the same current-frame inspection path. Native picking
chooses the geometry row; the inspector keeps ordered vertices, explicit shape
type and typed features together. Ellipse vertices describe its control box,
not a polygon approximation. Inspection itself does not select or edit geometry;
its explicit selection controls use the shared native geometry-selection path.
Shapes rows must belong to the current native visible slice, and grid/3D views
refuse these 2D controls. Selection preserves vertices, features, styles and
the active layer; it is not a saved annotation edit.
Replies are bounded to 256 vertices and 64 KiB; an oversized read keeps the
private session and saved data intact. Manual Shapes editing remains separate.

Inspection controls and expanded feature/geometry tables live in the scrolling
scientific inspector, beside the preview. Scrolling retains the same controls
and canvas; it does not remount readers, dispatch work or grant a new frame.

Native layer selection is an explicit current-frame command, separate from
point/shape selection, scientific edits and remembered presentation. The optional
`layerSelection` frame state is authoritative: one selected layer is active,
multiple/none have no active layer. Old frames without it omit these controls.
Native membership events invalidate frames even when active remains null; listener
teardown and stale control guards apply. A retained scientific window reconciles
its title only after exact resource/revision validation, updating owned local
metadata/accessibility without writing a shared layout or replacing placement IDs.

Points/Shapes feature-text display uses native TextManager settings through the
same current-frame change path: visibility, positive size, zoom scaling, anchor,
blending, rotation and scalar/per-axis offsets in data coordinates. It does not
rewrite templates, text/color encodings, features or geometry. Nested text events
revoke old frames and disconnect with the layer/session. Schema-13 remembered
views include this presentation; older saved views retain current native text
settings. Unsupported encodings are not silently replaced by generic labels.

Scientific candidate files in a legacy cache show **Prepare cached files** in Models.
It uses the existing download/adoption command and licence/key controls. Guided setup
must not show Downloaded until verified publication; dependency preparation and
scientific qualification remain separate. No adapter or model code runs from this action.

An imported scientific candidate's guided setup and Details can offer **Prepare for review** after
its code permission is granted. This calls `model-prepare`, shows the shared setup
queue's progress and Stop control, and preserves retry after cancellation/failure.
Prepared dependencies do not change unavailable workflow status or choose a model.
Actual review/execution and independent qualification remain separate actions.
Missing declared files expose the shared Download files control beside their
setup fact, including progress, Cancel and retry. Downloads use the fact's file
owner, which may differ from the selected tool. Candidate setup describes
preparation for review without presenting it as activation or qualification.

Scientific windows expose Continue with another tool for their exact saved
revision. Discovery uses existing integration setup; selection creates a new
revision without starting code or replacing private sessions. The new revision
shows its selected tool and a link to the original input revision. Original
windows remain pinned. A stale head refuses rather than rebasing input; errors
keep the same captured request available for retry. This does not qualify a
package, copy its values, or change the shared acquisition input.

Scientific Export sections offer explicit movie export from the window's exact saved
revision. This creates a separate owned execution and never captures unsaved live
changes. Cancel or closing that window aborts its request; retry retains the same
request identity to recover a lost acknowledgement. Rendered MOV files appear in
Project Data with versioned provenance and a download, clearly distinct from native
scientific values. Reopening them never runs their original package.

Setup-sheet preparation facts place progress and recovery controls below their
status text, so long package names cannot squeeze status labels to zero width.
The import Continue action occupies its own footer outside the scrolling fields,
so focused controls are never covered; file selection and preparation
ownership remain in the existing capability entry and setup modules.

Scientific source intake keeps primary actions together and reader choices ahead
of download/content identity. Reader runtime identity remains available in a
disclosure. Opening a saved scientific document reports true only after the exact
revision renders; failed windows reuse their loader on retry and closed loads
report false. After a successful import-to-source open, the import sheet closes
before the initiating Inputs drawer dismisses and focus returns to its rail.
A changed Project or layout invalidates that dismissal; opening never runs a tool.

Saved output context shows its tool name only after the provenance response validates the retained
input's original tool selection and its runtime matches this saved view. The recorded declaration
name is independent of today's catalog; unavailable or mismatched attribution stays unavailable.
This lookup neither opens a native session nor reruns a producer. Comparison warnings about
unrecorded actions are scoped to the frozen-input-to-arm interval; earlier source provenance,
immutable evidence text and missing timing remain unchanged.

Journey polish (ticket 62's table, 2026-10-23). A document window placed without saved geometry takes the first
place where it covers no other window's title row (and Close), and no window in front of it covers its own title row. It keeps that place, so windows never hop around each other. If its opener sizes it afterwards,
it is placed again for that size; a person's move or resize keeps theirs (`panels.ts` `cascade`). A scientific
preview that a resize (Expand, Controls, a moved panel) or lost focus made stale is drawn again once, as Refresh
preview would, keeping the status line. An unsent draft, a pending save, playback or a hidden window leaves Refresh
preview as the explicit way back, and a failed redraw is said once and never retried. The tool line says it is
checking the saved records, then shows the verified name, or that none is recorded and where the history is. An
output saved from a reader workspace is named by that workspace's guarded `sourceReader`. The row's layout status reads
"Layout: …" (Tabs and window places only) and shows, with Retry, only while the layout needs the person
(`layoutStatusNeedsYou`: Not saved, Reload required, Opening failed, Camera unavailable, Tab removed); Saved and Saving…
take no room, and ⌄ All Tabs always says it. The chip, Retry or Review recovery coming or going refits the Tab row at
once (`say`, and the observer on the row's tools), so a Tab is never clipped behind them. A scientific window says private changes once, as not saved yet. Labels
state the worker's own limits (512 MiB whole; larger region by region, 16 MiB per edit, 64 MiB before Save). Movie
export says keyframes are edited in napari-animation and how to open it. In a window, Measurements' table is the
scroll area, with its header row and row names held.

Scientific workspaces keep session actions and a saved-revision badge above the
viewer/inspector. Export is a direct inspector section; document inputs, history
and provenance live under Save in a labelled disclosure. Pending notices follow
the active panel so they do not shift focused fields. Short windows retain an
outer scrolling fallback. Switching sections preserves existing control nodes,
private drafts and session ownership; opening or exporting never captures unsaved
changes implicitly.

Movie export defaults to uncompressed ARGB MOV for native playback with exact
display-pixel preservation. Larger output stays under the existing export bound.
Legacy qtrle movie documents remain readable; format/codec mismatches refuse.

Project Data uses compact fixed-size previews beside title/kind/revision; missing
pictures cannot create a large empty card. Exact resource IDs remain in Resource
identity and Provenance. Movie export puts saved-revision context, action and
retained download ahead of optional format/help details; retries retain their
original request identity and never borrow the active scientific session.

Scientific source intake offers File, Folder, Stack of files, Web address and
Sample data explicitly. Sample data lists what installed readers declare, with the
declaring reader, size, licence and any reason the reader is refused. Dropping onto
the open sheet fills the same choice the controls would and never uploads by itself:
one file is File, several are Stack of files, a link is Web address. A dropped folder
is refused with the Folder step, because the browser omits hidden metadata. The sheet
stops drag events so the stage's live-view drop handlers never see them. A stack is ordered by natural name (2 before 10), shows that order before
saving and takes a named new axis (z, t or c). A web
address takes an optional expected SHA-256; the server downloads, and Stop aborts the
request, which stops the download. The saved source says where its bytes came from.
Find readers shows the declared choice first, or the refusal with its next step, and
labels each blocked reader Can’t open this source, Needs setup or Not available on
this Mac; an incompatible reader offers no setup link. A folder is a bounded
index plus original browser File blobs, not an eager concatenated ArrayBuffer.
The same Project-captured sheet owns cancellation/retry. Folder receipts open a
source-directory document and never download its internal concatenation as an
original file. Individual entries are downloadable from their exact revision;
reader discovery remains explicit and never executes on reopen.

The Mac launcher supplies `hookeFolder` for complete selected folders, including
hidden scientific metadata that WebKit's folder enumeration omits. Studio reads
bounded chunks into a ≤65MiB framed Blob and releases the native lease before
upload; the existing source import owns Project capture, retry and acceptance.
Closing the sheet retains its draft. Successful acceptance drops selected bytes.
The native chooser can be cancelled without replacing the prior selection.

Scientific reader and next-tool setup links encode package owner IDs. The shared
Toolbox router decodes those IDs once, validates the existing identifier alphabet,
and preserves legacy unencoded links. Malformed routes cannot throw or change
Project/input ownership; preparation still does not imply activation or qualification.

Candidate details put declared code permission and preparation ahead of optional
capability descriptions. Failed preparation retains a concise recovery message,
full diagnostics in a disclosure, and the existing retry/Stop owner. These controls
do not activate or qualify the package or change the selected scientific input.

New-layer Save forms reuse declared geometry axes while keeping scientific meaning
explicit. User declarations survive layer refresh; bound inputs retain their
recorded meaning. Invalid selected layers open their declaration and focus the
missing meaning or invalid axes before any capture request. No values or alignment
are inferred by this presentation assistance.

When an integration declares complete tool-state persistence, Save presents
“Save tool and all layers” before narrower array saves and layer declarations.
The primary action still validates every layer and reveals missing scientific
meaning; it never substitutes partial array preservation for complete tool state.

Tool places the declared specialist panel before generated settings, initially
expanded but never automatically loaded. The researcher explicitly opens/refreshes
its controls. Compact panel actions retain readable status and keyboard/saving
help without moving the owning scientific preview. Tab changes keep existing
controls, drafts, frame receipts and private session ownership.

Confirmed scientific save receipts offer Continue with saved output using the exact
accepted resource and revision. The original window's saved input stays separately
labelled; neither choice silently retargets the private session. Staged, failed and
conflicting saves cannot offer an output as a next-tool input. Discovery, choosing,
opening and execution keep their existing separate ownership and admission.


Visible, idle scientific windows observe queued native-plugin callbacks through the
same serialized GUI-thread owner. The observation returns only current image identity;
unchanged images are not repainted. Active gestures, unsent controls, Python, saves,
playback, hidden windows and preview-disabled sessions suspend automatic observation.
Changed images refresh layer/save inventory without rerunning analysis or accepting
results. Observation failure stops the loop and exposes Resume live results; late
results cannot repaint a different Project, Tab or placement.

Scientific Open session records a recovery capability in this browser profile's
private local storage before admitting a worker. It is scoped to the exact saved
resource and placement, sent only through the scientific transport, and excluded
from Project documents, screen reports and public session inventories. Reopening
the original window can reclaim disconnected private work while the backend lives;
Stop removes the local key after confirmed cleanup. Storage failure blocks startup
before analysis. Save is still required before quitting/restarting Hooke.

Document windows expose Move/Also show under the title's Tabs disclosure. Opening
or closing it keeps the same scientific/editor nodes and never changes input;
Escape returns focus to its trigger instead of closing the window. Arrangement
recovery lists named windows and saved revisions, with a persistent title/Close
header. Recovery captures its Project and selection before admission, serializes
repeated clicks, and keeps its local copy when completion is uncertain. A Project
change cannot redirect the save or reload the newly selected workspace.


Capability control placement (`capability-controls.ts`) is browser-local to the
profile, Project, work Tab and control identity. Actions forms, model comparison,
Bright objects and scientific inspectors share Left / Float / Right, a switcher between
several open pages on one side, width and optional drag-to-edge. Two kinds share it (workspace-feel step 4): a tool
page (an `action:*` form or Bright objects, no owner) belongs to Tools, and its header is "← Tools", its name, ⋯ and ×
in every placement; a scientific inspector (an owner) shows its window's title and × only. There are no surface
switches: no "Navigation | Controls" over a docked page, no copy of it on the drawer and no "Show <side> underneath";
the header keeps its plain title, and several open pages on one side are listed in its ⋯ as "Open pages" (menu radio
items, the one showing checked; choosing one shows it), never as a native select. Left, Float and Right (menu radio items that
say which is current) and the width (Compact, Comfortable, Wide, for Float and Right) are in the header's ⋯. Docked
headers have no grip and do not drag; a floating page's header drags, with labelled edge targets matching the actual
drop zones, an outline of the final footprint, and Escape cancelling. A left page takes the Tools drawer's width
(`leftWidth`, the workspace's `toolsWidth`), so the Stage never shifts between the Tools list and a page, and its
resize edge sets that width; Float and Right keep their own. Switching the rail parks what is docked (`park`): a parked
control is hidden but open, per Project/Tab for tool pages, with its fields, scroll and running work untouched. The Tools
rail item shows a dot while a page is parked and the lit cell while a run works, only while Tools is not the place
showing (never on the current item, so not on the Tools list either); it opens the parked page, or the list when the
page is showing (the page parks); "← Tools" parks the page and shows the list. These decisions are `left-side.ts`
(`toolsPress`, `railCurrent`, `toolsMark`, `assistantHeader`, `afterShown`), tested there; workspace-study.ts applies them.
Only a control's own × closes it: a docked page closes with its side (the drawer closes; on the right the Assistant
shows), and focus returns to where it came from or to the side's rail item or the Assistant button (`focusBase`). A
page on the right is reached from the Assistant's header by its "<tool> controls" chip (`parkedRight`); the Stage bar's
Assistant button shows the Assistant over it. When a tool page comes into view on the left (`shown`) the drawer is on
Tools under it, so the rail says Tools; an inspector docked left closes the drawer and the Data window and no rail item
is current. A Tab or Project switch brings back a left page only while the drawer is on Tools (`toolsShowing`);
otherwise it waits, parked: both the pages that keep their openness per Tab (`rememberOpenPerScope`, the Actions forms)
and those that keep the Tab they were opened in (Bright objects; `refresh` parks them on the scope change). Showing a page unparks the others open on its side, so the switcher lists them all. Docked controls
replace the underlying sidebar footprint without changing its saved width or content; the drawer and Data window are
hidden from display and keyboard focus beneath a left page, keeping their mounted content. Compact docks reserve their
actual footprint whenever at least 320px remains for the canvas, including 1100px windows. Crowded layouts still fall
back to overlay so the canvas cannot collapse (the Stage still starts after the rail). `controlStageInsets` is the one
decision: its `overLeft`/`overRight` say a side was not reserved, and that side (a docked page, and on the left the
drawer or Data window, through `data-drawer-dock`, which `occupy` sets) then floats under the Tab row and the Stage
bar, above the strip, so nothing covers the Tabs, a page docked right included (R-16). `refresh` asks for the insets
before it places anything; the workspace's `applyLayout` asks for a refresh (`refreshSoon`) after every layout change.
`occupy` takes the rail's and the side columns' widths from the workspace's `sides` hook (its state, so nothing is measured
while the layout is written; measured only without the hook), writes body's custom properties only when they change,
and the deck's observer refreshes once a frame at most (a microtask while the page is hidden). Measurement sections scroll
horizontally within their owner instead of clipping native table columns. Floating controls retain their own frame. One
tool occupies each side; narrow viewports show the most recently used side. Closing controls hides them without
stopping work, deleting results or changing the authoritative input. Only placement is stored, never form contents or
secrets. Setup modals suspend controls and restore the same nodes afterward.
Scientific inspectors remain descendants of their owning document via manual
popovers, preserving rendered-frame authority (a page is never moved into the drawer's DOM, whose sections are
replaced on every switch). Docking removes the old inspector
grid track. Geometry changes refresh an already opened native panel's paint,
never run a callback or automatically open a plugin. Active native gestures block
movement. Restoring windows never steals focus; explicit opening returns focus
on dismissal when its launcher is visible. Shared layout/CAS and execution paths
remain independent of personal control placement. Floating controls retain activation
order above restored sides; top-layer reordering preserves focused fields and
waits for active pointer/native gestures to finish. Unchanged refreshes do not
hide/reopen popovers.

An active dependency retry presents its current preparation state and Stop control.
The previous readiness/failure fact remains under “Before this attempt” rather
than appearing as the current failure; preparation still does not imply readiness.

Scientific scene comparisons use `scientific-comparison.ts`: fixed input revision,
independent tool scenes, explicit saved-result inspection/retention and preparation
links that leave the comparison intact. Results without saved tool state visibly
lack producing settings; agreement is not accuracy. Each arm lists its attempts,
newest first, with outcome, settings, declared device, outputs and timing said for
what it measures; failures and cancellations stay beside successes. Choosing a
preferred result needs a confirmation and changes nothing else. An action's outcome is
said beside the button pressed, with its Open updated comparison there. Save tool keeps
the tool's whole layer set, and a saved scene holds at most eight layers: beyond that it
says the count and what to do instead (`toolSaveLimit`; F-L8-7). The Hooke menu's build
line names the real update path: Check for Updates in the signed app, the source
checkout otherwise (`updatePathWords`; F-L8-5). The chooser lets one
tool fill several arms. Changing inspector sections
resets only the owning dock's scroll position, preserving mounted drafts/sessions.

Packaged Mac workspaces show a quiet Update reminder above Settings from Sparkle's
authenticated native state. Browser workspaces do not advertise installation.
Settings opens the same native review. Once an update is downloaded and waits for its
restart (Later on the Mac app's sheet; `downloadedVersion` in the native state), the
reminder says Restart to update and Settings says "Hooke <version> is downloaded. Restart
Hooke to finish updating."; either asks about the restart again.

Before restarting into an update or quitting, the Mac app reads `window.hookeUpdateReadiness()`
(`app-updates.ts`): `{blockers, running}` in the workspace's own words (`restart-readiness.ts`).
Blockers are WorkspaceViews save readiness (unsaved/conflicted arrangements or notes, including
earlier open Projects), a document's draft, a recording still being saved, and a lost connection.
Running is read from each kind of work's source, never the Working strip's pills (× hides a pill,
not the work; short work is held back there): hand-started runs (kept until they end), Record (the
dataset carries on when Hooke opens again; a screen recording does not), the Assistant's answer
still being written and its steps, voice, unsent words in any composer, researchers working in any
Project (`GET /api/research`; each comes back interrupted), the open Project's Studies with work
at a provider, and tool sessions (`GET /api/scientific-reviews`). A source that can't be read
rejects the whole read, so the app says it can't check rather than "nothing is running". No
scientific draft is implicitly accepted.

Labels windows offer a label display panel in View (H06a): the selected label (exact decimal text, the whole
integer range) with napari's color box for it, Show only the selected label, contour width, automatic colors
(original, or new colors from a seed as napari's new_colormap) or a direct color table edited as one
"label: r g b a" line per label ("other" for every label not listed), the background label, and 3D rendering
with isosurface shading. Changing them never writes label values, except that the background label is
scientific as in napari: Erase writes it, so erasing after a change writes the new background into the data
(test_erase_writes_the_changed_background_label_as_napari). Remembered views (schema 14) keep it.
A direct table naming a label above 2**16 is refused before the view changes when the integration's napari has
no numba (pinned napari cannot draw it). Image windows add napari's histogram (bins, displayed slice or whole
array, logarithmic counts, sample limit, Reset) and a contrast range row: the range, napari's popup actions (Full
range, offered for integer images only, and Autoscale range to data, its reset: autoscale the limits, then narrow
the range to them) and Auto-contrast once, also key C on the focused preview for the active image layer. Inspect on a
Labels layer shows that label's native feature row, found by the features' index column as napari does.

## Usable-core repairs (ticket 64, 2026-10-21)

Activity uses one bound navigation owner: rail, chat, reply details and Help open
the current Project's retained history even with the Assistant closed. Opening it
does not run work. The composer gives context (its chip line), typing (the field) and its actions (+, the model chip,
Talk, Send) separate rows, so a long Project/input label cannot collapse the input.

Talk resumes both audio contexts within the user gesture and connects the capture
worklet through a silent output. Listening requires a captured microphone chunk
and provider acknowledgement; the model-name announcement is not readiness.
Pending permission, connection timeout, capture stalls and blocked playback have
actionable states. End releases the session without cancelling delegated work.
Generation guards discard callbacks from earlier sessions. Execution details offer
Copy diagnostics through the same redacted export as Download.

A researcher's lit cell breathes while work is active and never shows an invented percent. Cards show actual engine,
elapsed time and age of the last reported step; old records say that age is unknown.
These source repairs still require the exact installed Mac acceptance walkthrough.

Talk names its action: End Talk while active, Cancel while connecting (accessible name Cancel Talk; in the composer
the level dot takes the microphone's place in both, so the Assistant's model stays named in full beside it). The
adjacent status reports listening/processing/speaking; microphone-muted status
remains visible while incoming response audio plays. Ending Talk keeps delegated
work under its own Stop control.

Voice captions with `journaled` arrive through the normal conversation stream, using
one server line ID for live growth and reopen. `speechSession` and the first speech
timestamp place a late caption among that session's captions; old records remain readable.
WebRTC waits for ICE candidates and actual media connectivity, retains the trusted
server sideband, and reports connection loss without replaying a paid request. Playback
status measures output audio; continuous silent packets are not labelled Speaking.

The workspace labels the conversational surface Assistant, as the concept vocabulary
requires. Its composer keeps input/destination, typing, + / model / Talk / Send and Talk's session status
in stable separate rows. The conversations and Activity are in the header's ⋯, keyboard reachable at narrow saved
panel widths. Dismissing the native update sheet keeps the available-update reminder.

Silent relay packets cannot acknowledge a response or hold Speaking indefinitely.
Only audible output clears the no-response watchdog, and speaking tracks audible
buffers separately from continuous silence. Researcher cards link to their owning
Project's Activity and explicitly say No recent update after 30 seconds without a
reported step; that is an observation, not a claim that work stopped.

When the selected camera has no picture, the live canvas retires its previous frame.
Retained result windows remain visible and unchanged. The empty-camera visibility
rule covers the canvas inside the specimen window, not only the legacy direct child.
Saved-media camera summaries say checks are paused, never that zero checks passed.
The source chooser also hides the nested acquisition canvas while its view is held,
including the explicit No camera path.
A partial researcher result says Partial work kept; partial status alone does not
establish a timeout (a scoped user Stop can also retain partial work).

Scientific playback counts a slice only after `FrameCanvas.whenDrawn` returns its
matching canvas receipt. Stop waits for a requested slice's receipt; hiding,
replacement or teardown rejects pending paint without counting it. The bounded
`scientific-frame-drawn` event exposes frame/draw identifiers for observation only;
interaction still uses the canvas owner's current receipt. Dimension shortcuts and
C auto-contrast share the focused preview, retaining 3D camera-arrow ownership.

Saved-data measurement controls (`scientific-resource-actions.ts`) require the shared built-in operation declaration
and the selected array's metadata readiness. They send its explicit operation ID with the accepted Project/session,
revision and input. Missing/incompatible declarations keep native export available and refuse measurement.
The same operation is discoverable as Measure saved objects through the existing Tools catalogue; its registered
form and authorized agent caller use the same native admission/publication service. Receipt recovery retains the
original producer rather than attributing an existing table to the retrying caller.

Reopened retained Masks windows expose the same **Retain labels for tools** action as live correction controls.
The checked displayed Population/Project/revision supplies its input, including historical revisions; shared
readiness may refuse unavailable original data rather than selecting newer labels. A hidden, closed or replaced
window, Project or Tab cancels its pending presentation and cannot open a late result elsewhere. Already accepted
server work remains in Project Data. Opening retained labels is explicit and never reruns the producer.

Population correction controls offer **Retain labels for tools** after the selected revision is saved.
`population-native.ts` binds shared metadata readiness and acceptance to that exact population/Project/revision;
Stop and stale selection cancel presentation, and retry keeps the request identity. **Open retained labels** opens a
data-only scientific document with the existing saved-array measurement/export controls; it does not launch a model.
Its bounded provenance explanation preserves original instance IDs, correction/source attribution and the explicit
pixel-centroid convention. A saved receipt is not an assertion that another window has rendered.

Retained native Population documents offer the shared **Find interactive tools**
control, including after measurement. Discovery and explicit choice use the saved
Project/revision; only **Open next tool revision**, then **Open session**, starts
the selected integration. Original data and its measurement/export controls remain
available in their original revision. This does not establish animation acceptance.
Comparison controls use discovery's explicit availability: native-only data explains
that an interactive tool must first be chosen, while keeping direct selection enabled.
Missing availability is unverified, never permission to offer comparison creation.

A retained method comparison's **Continue with chosen result** validates its completed
arm's exact Project, accepted revision, native component and revision digest through
shared saved-array discovery before opening. It never looks up the current head or
runs a producer. The new document rechecks the digest and original owner before
exposing actions. Its transient chosen-array selection opens Measure with only that
component's shared measurement/native-export actions; other arrays remain retained
in the same document and ordinary next-tool discovery stays available. The selection
does not rewrite scientific values, saved tool state or comparison provenance.
Project/Tab changes or cancellation during the handoff refuse late opening. Once
opened, the result owns its own lifetime; closing its comparison does not close it.

Native folder selection v2 keeps its immutable snapshot on the Mac's private disk.
`scientific-folder-native` and individual files over 64 MiB use `scientific-chunk-upload`,
reading one bounded chunk only after the previous upload is acknowledged through the shared source intake; it never accumulates the
whole native snapshot in browser arrays. The import sheet shows transferred bytes,
then verification, and Stop releases its native lease and server staging. An uncertain
finish resolves the original Project/request acceptance before permitting another
selection, including drag/drop; failed recovery exposes Check import outcome and
keeps that identity. A browser File uses one 1 MiB slice at a time, up to the 64 GiB
retained-file ceiling subject to disk space. Older native v1 selections, browser folders,
stacks, web imports and small-file requests retain their 64 MiB path. The storage ceiling is not evidence
that a reader supports that size or that large-data runtime qualification passed.

Reader reference review displays exact input identities and expected/actual retained JSON without inventing
a CPU/device claim. Enable remains an explicit generation-and-evidence-bound action. Functional reader
checks never claim independent scientific qualification; ordinary operation reviews retain their device facts.

The ordinary Tools cells action offers saved uint8 Y/X intensity planes in its
existing input picker, beside the current still image. The selected native Project,
revision and component travel with workspace admission and settings. Running does
not replace the live view; the saved Population opens from Data/Measurements and
Retain labels for tools publishes its native Labels for existing comparison.
The picker states its one-megapixel/type and 64-document discovery bounds.


Saved scientific documents offer an explicit component → Use with Assistant attachment for typed Ask.
Scientific views place that same control beside the selected input/tool context, available before opening a worker; it is not hidden under Save history.
The chip names the saved revision and survives question focus, but clears on Project/Tab change. Talk refuses until the person removes it explicitly.
Send clones the exact native reference/digest and excludes the live frame/outline/trial. This changes no saved
work. Supported input is a uint8 Y/X intensity plane up to one megapixel; other selections refuse before a model.
Talk retains its Stage scope; the chip says so. Native researcher handoff is not established by this path.

The scientific Export tab also has **Picture of this view**: Current view or an explicit rectangle in preview pixels.
`scientific-view-export.ts` uses the same scientific window's transport and busy/ownership guard, displays verified
Project-file downloads only after an exact-source receipt, and keeps native-data/movie export distinct. Previous view
exports show affected/fix-pending states. Restore export view changes presentation only; re-export is a separate
explicit action, refused until the reviewed fixed renderer and exact saved inputs/view/preview size match. Backend
mutation admission requires reopening accepted work after scientific edits; no hidden console or recomputation is used.


Data-card drag captures its exact retained revision and the initiating Project opening;
a changed card or away-and-back Project switch requires a fresh drag. Data's In-Tab
links reveal that exact revision in that exact Tab, including after restoration, without
falling back to another placement. Send dialogs and their Go to/Undo actions retain the
original Project owner. Project loads clear transfer notices; late actions cannot target
the new Project. Undo reports not saved yet until its own arrangement save is accepted.


Specimen-menu and Inputs choices capture the Project opening that rendered them.
Opening a saved source or the live input checks that owner before navigation and again
through the existing input-Tab admission. Their shared file chooser carries the same
owner across selection, upload and Library discovery; leaving and returning to the
same Project does not renew an old choice. A stale import stops before further uploads
or Project attachment. Already uploaded Library files are preserved and said explicitly;
no stale completion is presented as an accepted input in the new Project.

`graph-render.ts` draws accepted graph values as portable SVG with units, counts,
individual observations, definitions and exclusions. It positions values but never
bins or calculates statistics. Titles and labels are escaped; unsupported or
nonfinite saved data refuses instead of drawing a plausible substitute. Optional
pair annotations quote stored analysis probabilities and correction methods.

Editable tables use `datatable-presentation.ts` in the shared artifact window shell: Measurements → Make editable table
captures the owning Project opening, source revision and Tab before POST. A late accepted response stays in that
Project's Data; it never opens in a newly selected Tab. Table Save updates only its original placement to the exact
accepted version. Drafts and pending saves block destructive window/Tab/Project closure and updater restart; ordinary
Tab switches preserve mounted drafts. Retained population rows and mask contours link by Project, population revision
and member; generic result rows without a retained mask mapping do not claim cell linking. Analyze/Graph use the same
shell via `datatable-derived-presentation.ts`, calculate only on explicit submission, and reopen exact saved output
versions without recalculation. Unsaved setup is not represented as a saved result or arrangement item. CSV/XLSX,
analysis annotations and graph exports retain their exact table/source versions; opening another revision is explicit.

`workspace-compare.ts` turns 2–3 retained populations from one exact capture into an ordinary Compare Tab through
WorkspaceViews hooks. Its selector preserves Project opening, revision and declared producer. The active Compare
Tab binds pan/zoom to those existing mask SVG placements only; inspecting it starts no segmentation. Sending it to
a table names the accepted layout revision; a late response never opens in another Tab or Project. The server
revalidates each lane and captured input. The comparison's non-independence warning stays visible in the table,
analysis setup and accepted analysis/graph result. Inspection pan/zoom is transient; saved lane identities and the
Sync option are arrangement state.


Study scientific admission exposes **Connect Claude tools** only after a retained tool
grant and locally approved setup policy. The explicit action uses one retained request
identity; uncertain provider writes cannot be repeated, and a confirmed vault checkpoint
continues with its original request. Reopening only reads local connection state.
Configured credentials are distinct from verified live tool access and from Start.
Pause prevents new researcher starts; active sessions and their allowed tools continue
until Stop is confirmed.


A saved Study claims its mounted page before closing the Researchers drawer. The
synchronous drawer-to-workspace route preserves that page through its first render;
only legacy address-owned researcher details close when their route is left.

Saved Live camera Tabs can restore their retained windows and notes when the server
explicitly reports an offline camera bridge. Camera readiness remains unavailable;
Retry camera reselects that input without reloading or discarding the arrangement.
Permission, ownership, active-operation and arbitrary selection errors still refuse
restoration. Saved scientific data does not require opening a physical camera.

Measure offers Open editable table for accepted native object measurements. It captures the
owning Project, Tab and measured revision, then uses the existing Data Table Analyze/Graph
controls. Native source tables stay frozen; measured values and object IDs are immutable while
added columns remain editable. Row selection does not pretend to highlight native labels.

Successful arrangement saves retain the unavailable-camera badge and Retry camera;
Saving, unsaved changes and save errors keep their own status and recovery action.

Study preparation is shown before explicit Start. The per-Study readiness GET checks exact captured
inputs locally; both provider configuration and input readiness must pass. Preparation refreshes retain
an edited instruction and never launch work; failed readiness invalidates cached permission to Start.
Scientific copy, tool allowance and Claude connection remain separate explicit actions. The Claude
session budget and larger reserved allowance remain visible and editable, with overrun/estimated-cost
semantics explained. A held allowance is not a measured charge or a guaranteed provider ceiling.
Experiment rows and counts share lifecycle words. Only a confirmed Claude session offers collection;
Finish is available only when that session is observed idle. Evidence displays named transcript events,
retained output names and list cost from the exact displayed snapshot (Gemini settlement remains an
explicit reservation estimate). Accept as completed, Record as failed and Record as cancelled are separate
choices requiring a review note and exact evidence hash; no outcome is preselected. Backend admission,
termination, archival and cost checks remain authoritative. Input checkboxes precede their labels and
New study keeps its Save/Cancel footer outside the scrolling form body.

The Tab row holds only Tabs (Will, 2026-10-23). Every Tab has one shape: a researcher's or Study's Tab is
the same height, corner and selected fill as a work Tab, its state its lit cell and its tooltip, its × inside.
The row's controls are "+", a menu: New Tab (⌘T, through the Tab command, so an open researcher Tab is
left first), New note (disabled when no work Tab is active), New researcher, New study…; and ⌄, All Tabs (step 2 below). New researcher opens a researcher Tab with an
empty one-column workspace; the Stage bar above it says New researcher, with the way back. Its Start sends "Start a researcher to …" to the Assistant as the person's own request
(the server starts researchers only on the user's words), so it also shows in the conversation. The Tab
follows that request's real outcome through `hooke-ask-sent`/`hooke-ask-settled` (main.ts): a refused or
failed request is said at once with the words kept, and so is an accepted request whose answer's start_researcher
was refused (`refused` on `hooke-ask-settled`: a missing key, the cap; its own words); an accepted one waits for the
new researcher, which takes the Tab over, and says plainly if none has started after 90 seconds. It never claims a researcher
started. With no Project open, the launcher shows the most recent Project first, up to seven more recent
ones and New project; thumbnails are 192 px wide, so pictures stay near that size.

The whole-app usability audit (2026-10-23/24, ledger `docs/design/2026-10-23-part1-uiux-pass/audit.md`) settled these:
- **Tabs.** `WorkspaceResearchTabs` owns every Tab that is a view rather than a saved Tab (`study:<id>`, the
  `draft:new-researcher` draft) in one map, so a starting draft and an open Study coexist; pills are drawn in place,
  never rebuilt; a draft's pill is handed to the researcher it started (`adopt`) so nothing moves. Every × means Close
  this Tab and stops nothing (the draft's × is held while its start is in flight); closed virtual Tabs are listed under
  Closed in All Tabs and reopen by ⌘⇧T in turn. Nothing folds into a "Researchers (n)" Tab any more (step 2). The
  research stage keeps its pages mounted and hidden; leaving a Study with unsaved form words asks in a sheet with a
  continuation (never `window.confirm`, which froze the live view).
- **Research state.** `research-state.ts` is the one `isWorking`; `research-stop.ts` makes every Stop say "Stop
  requested" until the server lists the researcher as stopped; a keep-going question offers Keep going (a note) and Not
  now; `researchers-board.ts` is the Researchers drawer's one list at every count (step 4 below). The rail's
  Researchers badge counts those waiting on you.
- **Settings** (the rail's foot) is the header's gear menu moved into the sheet: one row per control, each a title, one
  quiet line (`data-sub`) and its switch or chevron at the right (Behaviour's choices open under it); the header's compact
  looks of those controls (Share as an icon, Behaviour's select chevron and narrow icon, the blind switch's hidden
  words) are scoped to the bar with `:where(.bar)` in studio-shell.css, so they never reach the sheet.
- **The sheet.** Models, Library, All Projects, Settings, Help and Shortcuts open as one centred sheet over a plain
  55% scrim with no blur (the live view runs beneath), in one 150 ms settle; Escape closes the innermost layer first
  (setup sheet, Details, a Library preview); a model's Details is a right pane beside the grid; one action per card;
  "Not on this Mac" is the one phrase. Data and Activity switch one panel. A setup sheet opened from Tools closes
  back to the Project (`hooke-setup-sheet-closed`).
- **Data.** By what each thing is (ticket 66 D2, the mock's `data` and `data-by` frames; `data-roles.ts` reads the
  index's `role`, `maker`, `run`, `forms` and `values`, `data-list.ts` draws them): Pictures and recordings (this
  Project's inputs from the sources model, in its order, each joined with its index entry, then the index's other
  acquired entries; the live camera is not an item), Analyses, Write-ups, Sources, then Other folded last (bookmarks,
  calibrations, taught labels; its heading opens it, a search opens it, and it carries the New dot while folded), each
  with its count of rows. Help and every message that points into Data name these headings from `ROLE_SECTIONS`
  (`sectionWords`: a researcher's write-ups are "under Write-ups", never "under Research", which Data has no heading for); Search Data as before (any part of a row, its maker, a write-up's forms, a source's site);
  "Made by everyone ▾" lists You, the Assistant and each researcher by its number, each only when a record here says it
  made something (an older record names no maker, so offering You with nothing under it would say you made nothing), and
  a chosen maker is a chip with × (and Open Researcher N). One row per run: entries sharing the server's `run` (and a
  section) are one row that opens its first part, outlines before a table, a table before an answer ("Bright regions 271
  regions", holding Outlines · Table, 271 rows · Answer in its tooltip); the other parts' open and ⋯ stay in its ⋯ under
  their names. Its title is the server title's first part (without "for Researcher N" when the row names it), or a
  researcher's own words whole (a report's result title, its table's, a source's), said as theirs in the tooltip
  (`data-untrusted`); its count from the server's own words ("271 outlined", "4 rows"); its quiet line "On this Tab"
  first when it is on the Tab you are on (an item of the Tab's saved layout, a window open on it saved or not, the
  outlines or motion drawn on its specimen through `bridge.drawnOnStage`, an input on its Stage), then who made it
  (never "You", never guessed: an older record shows none, nor does a row under its maker's chip), what made it and
  when; a write-up its current form ("Presentation · being written" with the lit cell, "Findings v2"); a source Page or
  Web picture, its authors or credit and its site. A report with nothing written yet is its researcher, in Researchers,
  not a row. A row (an input's too) opens in the Data reader on the right of the Tab you are on (below; ticket 66, D1):
  nothing moves you to another Tab unless you ask, and the row the reader shows is marked (`data-current`). Its ⋯ holds
  Add to Tab… (it adds without leaving: the Tab row's toast says "Added to Tab 2" with Go to Tab 2, once the save was
  accepted; `WorkspaceViews.addItem`), the Tabs it is on, its kind's next look, How it was made (the reader on that
  view), Show what Researcher N made (Data under that maker's chip), Open in new Tab and the rest; an input's ⋯ Open in
  its Tab and Show in Inputs (the separate Inputs section and its "All N inputs" are gone: every input is a row).
  Dragging a row onto a Tab still opens it there. The studio's own `<rid>#<output>` rows are gone: the server lists a
  researcher's real entries. Data refreshes on the server's
  events relayed by main.ts as `hooke-results-changed` and a 10 s poll, both sending back the index's `revision`
  (`since=`): an unchanged index answers in 85 bytes and nothing is redrawn. "On this Tab" is checked again on a Tab
  switch, a window change or the drawn layer changing (`hooke-stage-drawn`, sent by main.ts only when what is drawn
  changed) and the list is drawn only when it changed. What has not been in view since the person last looked is marked
  New (localStorage per Project, by entry and version as before) and counted on the Data badge by rows, never an input
  or a source, which pulses when one lands while Data is closed. A researcher's report is dated by `finished`.
- **The Data reader** (`data-reader.ts`, `data-reader.css`; ticket 66 D1, the mock's open-* frames). One reader on the
  right edge in the Assistant's place, one of the two at a time: opening it hides the Assistant (`readerShown`,
  `readerVisible`), its × brings the Assistant back if it was showing, and the Stage bar's Assistant closes it and
  brings the Assistant back; a page docked on the right parks as it does for the Assistant (`parkControls('right')`).
  The Stage keeps the room of the one shown through the single layout write (`--study-inspector-width`, `sideWidths`,
  `sidesShown`; the reader slides with the Stage in a press, never a synthetic resize). Its width is the mock's 600 px
  beside Data, narrower when the Stage would keep under 400 px, 540 px below 1200 px (`readerWidth`), where it takes the
  Data drawer's place and shows ‹ Data (a left column opening there closes it). Each Tab remembers its open item for the
  session (sessionStorage `<storageKey>:reader`, never the saved layout); switching back shows it again. On a
  researcher's, a Study's or a reader Tab's page it is not shown (it belongs to the work Tab); a researcher's Tab has
  the same reader of its own on its right, where a Data row clicked there opens (The researcher Tab below). Its header: the kind's
  mark (the lit cell while its researcher is writing it), the title (a result's first part; a researcher's report by its
  findings' title once read), the count once its table is read ("271 bright regions"), and kind · maker · time; its two
  views, the thing (Outlines, Table, Page for a write-up, Source…) and How it was made; Show on picture (outlines only:
  `bridge.showLayer`, the present verb through `WorkspaceSession.showRetainedLayer`; "On the picture ✓" once the
  studio shows it, pressed again it hides them; a failure is said under the header), Talk to Researcher N (a
  researcher's work: the one action that goes to its Tab, and its title says so), Edit (a note: today's note window on
  this Tab), Open in new Tab, ⋯ and ×. ⋯ keeps
  every route its row had, by name: Add to Tab… (not for an input: Open in its Tab is its way), Go to <Tab>, Open masks window, Open in Measurements, Open the
  scientific viewer, Open the data table, Open the analysis/graph window, Open captured input, Use … as analysis input,
  Open in its Tab (an input, a recording), Show in Inputs, Edit in a note window, Open in a window (an answer, tracks…
  as Data opened them before), and Move to trash (an input from the Library, where it asks first; for results it is
  said, disabled, that this version has no way yet). The renderers draw into the body they are given with the windows'
  own code: outlines (`drawRetainedMasks`, falling back to the input its record names when the capture is not kept),
  then its table; a table with its rows drawn as they scroll (`drawRows`, `windowRows`; `readMeasurementTable`); a
  write-up from its research session (`/api/research/<rid>`, `researchHero`, `researchFindingPage`: presentation,
  report, lesson, video) on its page in its own theme (the researcher page's `.page` rules); a researcher's table or
  chart (`researchTableNode`, `researchChartNode`); a source from its `values` (title, authors, site ↗, licence and
  credit, the picture, and Cited in opening that write-up in the same reader); a note on its page (`readRetainedText`);
  a recording's player (today's preview modal is gone); a picture (an input by its name, its credit on the quiet line);
  an answer's words.
  How it was made reads the detail's `how` when the server sends it (versions, written or made by with the words you
  asked, made from, used in, cited in, sources, method and settings), otherwise only what the entry records (the input
  its record names, the model by its one name, who when the record says, when, the Tabs it is on), never raw JSON and
  never a guessed maker. Every wire-contract field (`role`, `maker`, `run`, `forms`, `values`, `untrusted`, `how`) is
  optional. Open in new Tab makes a **reader Tab** (`WorkspaceViews.openReaderTab`; layout schema 6, kind `reader`,
  `{items:[{resource:{day,id,version},title}]}`, the specimen hidden; `readerView`, `readerItem`): the same reader full
  width (`tabReader`), its title on the Stage bar's left (`stageSlots().reader`), its mark its item's kind, the
  Assistant as a layer over it (`assistantAside`); a Data row clicked there shows in that Tab's reader (the Tab keeps its
  saved item). The studio asks for layout schema 6 and, refused by a server that
  reads only 5, asks again for 5 (`readLayout`): reader Tabs then stay for the session (`sessionReaders`) and the
  saved layout is sent without them (`layoutForServer`). The screen report lists the reader and its item
  (`readerSurfaces`, docs/architecture/on-screen.md; the roots' ids `data-reader` and `data-reader-tab` are their report
  identity: an empty root id makes the server refuse the whole report), titled by the item's own title only for what
  you and the app made, by kind and maker for a researcher's work, a web source or any untrusted entry
  (`DataReader.reportTitle`). **The Assistant opens things the same way** (ticket 66, D6): `present` open and
  `show_result` reach the studio as a `reader` message (main.ts relays it as `hooke-reader-request` and reports at once;
  `ReaderRequestMessage` in protocol.ts), and workspace-study.ts opens its item with `openInReader`, as a Data row's
  click does, only in the workspace opening it was asked in (`check`, the Tab strip's pin) and the Project open now.
  The item is Data's own entry when it has that exact revision, else the entry the server sent (Data refreshes
  after); a `close` closes the side reader while it shows that item. Done is the screen report showing it
  (orchestrator/data_reader.py).
- **First launch.** A fresh profile opens the server's current Project on its empty Stage (`session-home.ts`); `firstRun`
  (no stored Tabs, no Project with an input or result) shows Welcome to Hooke only while no Project Tab is open. Both
  carry the **small-models offer** under their ways in (`starter-models.ts`; Will, 2026-10-26), and Models carries it
  as one row at the top until answered: "Add a few small models?", rows in the wire's own words and sizes (Bright
  objects Included; EdgeTAM and DINOv2 small ticked; InstanSeg with its own software and Cellpose-SAM, non-commercial,
  unticked; an entry the edition excludes is left out), the models folder with its free space and Show in Finder, where
  they come from, **Download <size>** (following the ticks) and **Not now**. What is ticked follows one rule read from
  the wire: the default of a role that powers a capability whose actor is `auto`, open licence tier, no install, under
  100 MB. Download keeps the answer and uses `model-download` (or `model-turn-on` for an entry with its own software) in
  the one queue, the rows then showing measured progress with the lit cell, then "Downloaded · not checked" until
  Hooke's own check has passed here and Ready after (one meaning of Ready: Models' sections, `run-spec.ts`
  `runnableWords`, which the offer, Tools, the settings picker, a download's pill and a status in words all say; a run of
  yours is not that check); Not now collapses to "No models
  downloaded. Models has them whenever you want them." The answer is the server's (`onboarding-models`,
  `onboarding/models.json` in the data home), so no place asks again. Choosing the folder is a separate ticket: no
  Change button until a real setting exists. The key note (`setup-note.ts`) waits until something that needs the key was asked
  for (`hooke-ask-sent`) and says what the Assistant needs; `askError` says the server's keyless refusal as the step to
  take. Library's preview actions are Open in this Project, Open in new Project (`hooke-open-in-new-project`, handled
  by the workspace) and Move to trash.

The workspace feel (plan of 2026-10-24, target `design/workspace-feel/`) is built step by step on these foundations:
- **Palette.** `workspace-theme.css` holds the mock's values on the `--mm-*` tokens: page `#141416` (the window, the rail),
  bar `#19191c` (panels: every drawer, the Data window, the Assistant, the Models sheet), card `#222226` (raised:
  rows, buttons, fields), raise `#2a2a2f` (menus, popovers), white 7 % and 11 % hairlines, one shadow for floating
  layers, radii by tier (`--mm-r-control` 6, `--mm-r-tab` 8 8 0 0, `--mm-r-card` 10, `--mm-r-sheet` 14, `--mm-r-pill`)
  and motion (`--mm-fast` 120, `--mm-base` 200, `--mm-reveal` 320 ms, one ease). The Stage (and the specimen window in
  it) is true black in every theme. Tertiary text is `#909099` and the light accent as text `#2360d4`, a step from the
  mock's, so every text colour reaches 4.5:1 on every surface (`tokens.test.ts` checks dark, black and light).
  Inside panels, raised things (the selected rail item, rows, buttons, fields, the Assistant's composer) take card and
  their hover takes raise, as the mock's buttons do; a plain icon button's hover is `--mm-hover`; only menus, popovers
  and toasts sit on raise. Focus in the workspace is a 2 px ring at a 2 px offset (`workspace-feel.css`; a panel
  header that takes focus keeps it inside its edge). Nothing warm but the lit cell: the rail's Models and Researchers
  badges are the action fill and Data's count is quiet. In light, the specimen window's bar keeps the dark theme's words (it sits on
  the black Stage), and the empty Stage card's inner cards are card with a hairline, its drop zone dashed.
- **The lit cell.** `lit-cell.ts` is the one status sign of work: `litCell(state, {size, fraction, label, title,
  settle, decorative})` returns the brand cell as an SVG (`data-kind="lit-cell"`, `data-state`, the state's word as its
  name) and `setLitCell` changes it in place. Gold (`--mm-alive`, the chrome's only warm colour) breathes while working
  and is still for Stop requested; setup fills bottom-up only from a measured fraction; needs you is the accent with a
  light centre; green is ready, a green outline partial findings, red outline needs attention, dim outlines idle,
  queued and stopped. Its CSS is `workspace-feel.css`, imported last after `workspace-calm.css`: breathing and the one
  240 ms settle are CSS only and stop under reduced motion; the settle ends only on its own animation, so a working
  cell turned ready in place still plays it (the polygon's cancelled breathing bubbles up and is ignored). `lit-cell.fixture.html` shows every state.
- **Pills update in place.** A Tab-row pill's build is named by `data-kind` (`researcher-tab`, `view-tab`; a work Tab is
  `work-tab`), not found by the mark it draws, so its lit cell changes in place (`setLitCell`) and focus never moves.
- **The Tab row and the Stage bar (step 2).** The row holds only Tabs, then + and ⌄. One anatomy (`workTab` in
  `workspace-views.ts`, `drawResearcher`/`drawVirtual` in `workspace-research-tabs.ts`, marks in `tab-marks.ts`): a kind
  mark or the lit cell, the whole name (a researcher's Tab is its name, "Researcher 3"; what it works on and its state
  are the tooltip), ×. The selected Tab's × keeps its place; an unselected Tab's, a researcher's exactly like every
  other's, floats over its end and fades in on hover and focus, so it never takes room from a name (2026-10-25: "Tab 2"
  read "Ta…" at 88 px while researcher Tabs kept their names). Main is always first: `render` puts the home Tab first, `mount` normalises a
  saved order and orders nothing while the row says Opening…, Alt+arrow never moves Main or puts a Tab before it, and
  only Main is sticky. Arrows and Alt+arrows move among the Tabs in sight (a folded Tab is reached through ⌄, ⌘1–9 or
  next/previous, which unfold it), so one visible Tab always keeps the Tab order. `tab-row-fit.ts` fits the row
  so the person's own Tabs stay: first the work Tabs shrink (CSS, to 96 px, so a short name like "Tab 2" is whole and
  the longest names ellipsize; researcher, Study and draft Tabs and Main keep their size); once a name is cut or the row
  is over, a recording's label shortens (`data-fit="tight"`); the fit and ⌄'s count allow the same 1 px of rounding.
  Then a picture or recording with a long name (more than 12 characters) keeps only its input's thumbnail
  (`inputThumb`), from the right; one with a short name or no thumbnail is never reduced to a mark that could not be
  told from the next, and folds instead. Only then do the least urgent Tabs fold into ⌄: finished researcher, Study and draft Tabs first (rightmost
  first), then the person's own (Will's "yours … and researchers you made, side by side"; a folded finished researcher
  is still its green strip pill, in Researchers and under ⌄); one working, needing you, Stop requested, waiting for a
  free slot (a researcher just started opens as a Tab beside yours and stays in sight) or showing never folds, nor Main.
  ⌄ counts what is out of sight, coloured for the most urgent (a waiting one is counted, never called working), and
  opens All Tabs: the open Tabs in row order (Showing), the closed work, researcher and Study Tabs with Reopen, studies
  not open as a Tab, then the
  layout's status, Retry, Recovery copies and Manage Tabs… (rename, duplicate, remove, arrange and restore stay there).
  The fade is an overlay shown while the Tabs move. Toasts (Closed X with Reopen, flashes, completed results) float in
  a layer under the Stage bar and take no room; on a researcher's or a Study's Tab, where the page covers the Stage, they
  sit at the Stage's bottom centre instead (`placeToasts` measures the Stage into `--stage-h` when one shows). Closing a
  Tab changes the state at once, then a copy of the Tab flies to where its content lives (Researchers for a researcher or Study, Data for a Tab with an input or results, ⌄ otherwise)
  and the target pulses once (`tab-close-motion.ts`; reduced motion keeps only the pulse); the toast says where it went.
  Review recovery shows on the row only for a copy made at or after the loaded revision whose arrangement differs from
  the saved one; older copies are listed under ⌄ and never deleted. A researcher has a Tab while it works or needs you,
  ended and not yet opened, its work ready or stopped on its own as the strip's pill is (`shelf.isNews` through the
  bridge's `researchNews`; `isNew`, ready work only, stays the Researchers count's green), or opened this session
  (sessionStorage); a Tab the person closed stays closed across launches (localStorage, per Project), and opening its
  Tab counts as opened (`researchOpened`). A server-made results Tab is kept but not opened while its researcher's own
  Tab is in the row. The Assistant's switch leaves a researcher's page the way a click does (`leaveResearch`), and its
  Tab report lists researcher and Study Tabs (`research`), which it can show or close. The Stage bar (`stageSlots`) is
  on every Tab: on a work Tab the specimen's name, its credit quieter and its picture (`specimenLabel`), with Float or
  Fill and Hide (the floating specimen keeps its own bar; the full one has none), or "Specimen hidden" with Show as
  backdrop; on a researcher's, Study's or draft's page its bar (back to the work Tab, what it works on with its state
  words, Stop while it can be stopped; step 7 below); on the right the camera, Record and the Assistant. The specimen is
  named once: with a picture or recording on the Stage, the camera control says only the camera ("No camera", its
  device, else Camera; `CameraFacts.named`), with its menu and its title naming the specimen. When the bar runs short
  the left's credit gives way first, then the name ellipsizes, and the camera control is the one on the right that
  shrinks; below about 440 px (a container query) it is its mark alone and the left keeps the thumbnail. The camera
  control shows once its words are settled (connected, the bridge's health, and the camera checklist, which main.ts
  asks for as soon as the bridge's address is known, about 0.9 s after load; 4 s at the latest), then fades in, so it
  never changes words in front of the person. `workspace-research-tabs.fixture.html` shows the row with Main, Tab 2, a recording and researchers working,
  needing you and with partial findings (fixture only).
- **The Working strip (step 3).** A fixed 40 px row of the workspace grid under the Stage (`working-strip.ts`, built by
  the studio shell), always there, so the Stage never resizes and the eyepiece never rescales as work comes and goes
  (no fit beside the eyepiece any more: the shelf's `shelfFits`, `--shelf-room` and four-chip cap are gone). It reaches
  under the Assistant's column to the window's edge while that column sits beside the Stage (re-placed in the next frame
  when a tool's page docks or undocks on the right, which keeps the strip's meters clear; during a side press's motion the
  motion carries the reach and the strip lays out once when it lands), and the Assistant stops above it; below
  1200 px the drawer and the Data window sit beside the Stage (the strip starts after them), or, too narrow for that,
  float above it. Its left is one pill per piece of work, each its lit cell, its name, its elapsed time (tabular, from a real
  start, never a percent) or its state in words, and ×; ordered needs you, working, finished, oldest first; drawn in
  place (a pill keeps its element, its cell changes with `setLitCell` and settles once when it lights or turns ready).
  When they run out of room the passing line gives way first (`data-squeezed`: it steps aside, or one with an Undo
  ellipsizes to it, its words in its tooltip), so live work never goes compact or folds for a hint; then they go
  compact (names alone), then the rest fold into one +N that opens Researchers when only researchers folded, else
  Activity. The
  sources: the shelf (`shelf.ts`: researchers working, needing you, or finished and not yet opened, lit in the frame
  their Tab lights, since `main.ts` runs the workspace's listeners at once on a research message; click opens the
  researcher's Tab, × takes it off the strip for the day through the presenter's `remove`, and it stays in
  Researchers; opening its page anywhere marks it opened, so a green pill leaves once read), the runs started by hand,
  one pill per run (`strip-sources.ts runPill`, kept by `RunPills`, from `workspace-study-action` and
  `workspace-study-settled`: its model's name, short (`pillName`: the part before " · "; whole in its tooltip), its time,
  then Done green until its results are opened in Data, or Failed, Stopped, Stop requested; click opens its run page;
  a new start adds its pill beside the others, so a finished or failed run's pill stays until its own click opens its
  results, its × hides it, or its results are opened from its run page: View retained results, or a run page's Open in
  Data when it is the one run done in that Project; Run again of the same task replaces an earlier run of it that
  failed or stopped (its page now shows the new run); at most 8 are kept, the oldest ended leaving first; a background job
  a newer start left behind is still read from the retained jobs every 10 s),
  agent steps still running after their answer (`StepPills`, from `ChatLog`'s `steps` hook; an Assistant-started run
  gets its pill when its answer arrives) and model loads (`LoadPills`, Activity's `model` lines; a failed load stays).
  Work that ends within a second never shows a pill. With nothing at work the left is the view's passing line (#hint,
  "Click anything in the view to outline it.", hidden on an empty Stage), as wide as its words so its Undo and × follow
  them; among pills it shows only when it has news, and shrinks before any pill does. The right end holds the performance meter and Spending (the button says "$X today",
  the panel's own Today total, once today's tracked spend is above zero, else "Spending"; refreshed when the workspace
  loads, a researcher ends and a run settles), moved off the Stage's corner;
  their panels place themselves above their buttons (`meter-popover.ts`; nothing around them may blur or transform).
- **The left side (step 4).** The rail is 72 px of places in hairline groups: Data, Activity, Researchers, Inputs; Tools,
  Models; Library; then Help, Shortcuts and Settings at its foot (the Hooke menu is the Project row's first control,
  over the rail's column). Its marks (`rail-badges.ts`): Data a quiet grey count of New results; Researchers a blue count
  of those waiting on you, else its lit cell while any researcher works (here or in another Project); Models a blue
  count of entries that need you; Tools the lit cell while a run works and a dot while a run page is parked. Rail and
  content always agree (`renderRailCurrent`): the place the drawer or the Data window shows, Tools under a tool page,
  none under an inspector. The drawer and the Data window are one column beside the rail with one 52 px header each
  (`.mm-dhead`: a 15 px title, at most one action, ×): "+ New" in Researchers (New researcher, New study…), Add model or
  tool in Tools; Data's header holds its two doors Contents | Activity as quiet tabs and a ⋯ with the imports. The
  column is 340 px at 1200 px and wider and 320 px below (`leftColumnWidth`) until the person drags its edge (320 to
  720 px; a dragged width wins, saved as `panelSizing` 6). It sits beside the Stage while the Stage keeps 320 px beside
  it and whatever is docked right (`data-drawer-dock="beside"`; the Tab row and the strip start after it), else below
  1200 px it floats over the Stage under the Tab row and the Stage bar and above the strip (`"over"`), never over the
  Tabs; the deck's stage insets decide it (above). Data's Search Data, Made by and sections stay mounted while
  Contents shows (`data-window-body.ts`), so Search Data keeps focus while you type and while a refresh redraws the
  list. Activity's
  entries there are the mock's act-line: the entry's lit cell (set by `drawActivityCells`; only the workspace draws it),
  what happened on one line, one quiet line (where it came from · when, to the minute), under day separators
  (`activityDays`, drawn from `data-day`); a line's exact time shows with its details, and Problems only is a quiet
  toggle. Every list is the same 56 px row (`.mm-row`: a 36 px picture or kind, a title in up
  to two lines, one quiet line, the New dot, ⋯; menus from `drawer-menu.ts`). A Result's server title ("Bright regions
  · Bright objects · 19:54") splits (`resultRowWords`): its first part is the title, and the quiet line is its kind,
  time (or day), the Tab it is on and who made it, said once (an older result's "… · Bright objects · Basic local
  method · 19:54" splits the same way, its maker said "Bright objects (Basic local method)"); the whole title stays its
  tooltip and accessible name. Researchers (`researchers-board.ts`) lists
  this Project's researchers at every count, grouped Needs you, Working (a queued one says Waiting for a free slot),
  Done, then its Studies, then those still working in other Projects (named by their Project): a cover picture with
  the lit cell on its corner (its newest picture, else the view it started from, else `cover`: the Project input it
  started from by name, else the Project's specimen; with none of those its mark on the raised tone, never an empty
  box), "Researcher N · Working 1:01:14" (its clock ticks once a second, the list redraws only
  when something it shows changed), engine and step age in the tooltip; ⋯ holds Stop (Stop requested until confirmed),
  Activity and Open; a question's answer buttons sit in its row; under a row that made something, what it made
  ("Made a report, a table and outlines ›", "… and 3 sources"; `madeWords`, one thing per Data row, a presentation only
  once it is made, from Data's index: nothing when it has made nothing yet, nor for one working in another Project),
  which opens Data filtered to it (ticket 66, D4); from eight researchers a search and ticks for Stop selected. With
  nothing to list: "No researchers yet. Ask the Assistant, or start one." and "+ New researcher". An
  answer's Not now, an empty place's one action and a card's Show page are the mock's secondary button (a hairline, the
  raised fill, a top highlight), so beside a primary the pair reads as one primary and one secondary. Data's
  rows, the Inputs drawer and Activity's lines use the same anatomy. The rings are gone (`setRing` is
  retired): researcher cards (`researcher-cards.ts`, the legacy side bar) carry the lit cell too.
- **The Stage's motion (Beta 0.4.1; Will, 2026-10-26: the side panels made the main space "jumping and jittery").** A
  press of a rail place, the drawer's or the Data window's ×, the Assistant button or its ×, Alt+A or Escape is one
  `press` (`workspace-study.ts`): the handlers change the state and the layout is written once at its end (it ran 1 to 5
  times, each run firing a synthetic window `resize` that ran every resize listener inside the click). Nothing measures
  inside the press: the deck's `occupy` takes the rail's and the columns' widths from the workspace (`sideWidths`, its
  `sides` hook), the guide card and the camera menu write their place on themselves (never on body, where a changed
  value re-styles the page), focus moving into a panel it opens (the composer, Data's tab) moves in its first frame, and
  Data opening reads no scroll position. When the press opens or closes a side column (the drawer, the Data window or a
  docked page on the left; the Assistant or a docked page on the right), the Stage's edge and that column move together
  (`side-motion.ts`): 200 ms on the house curve, Web Animations sharing one explicit start time with pixel keyframes, on
  `.studio` (margin-left, width), the column (`left`, or `right` with its width held, for the Assistant) and the Working
  strip's reach under the Assistant (margin-right); below 1200 px, where a left press also closes the Assistant and the
  reverse, both edges move in the same motion. A docked page (top layer) slides out from behind the rail clipped at it;
  the deck hides one at once, so it never animates out. The start and end come from state (`left-side.ts`
  `stageInsets`, the CSS's own rule), never measured. Closing animates out: the column is inert at once (focus has gone
  to the rail item) and hidden when it lands (a 260 ms timer if the animations never say they finished). Switching
  between places while one is open plays nothing: the other column shows in place, with no fade (the old
  `workspace-drawer-in` and `study-panel-in` are gone). A press while the sides move starts from where they are. Only a
  press moves: a Project switch, startup, a window resize across 1200 px and a page docking land at once (no transition
  on `.studio`; the docked page's Stage rule in capability-controls.css is `html`-prefixed instead of `!important`, which
  would outrank the animations). The picture follows its box: a new canvas size places it at once (`view.ts` `resize`),
  so it re-fits each frame of the motion and is never past the Stage's edge; the glide stays for the whole frame and
  eyepiece switch. While the sides move (`body[data-side-motion]`), work that would measure each frame waits and runs once
  when they land (`holdUntilLanded`): the Working strip's reach and pill folding, the Stage's corners (`StageLayout`), the
  camera menu, the session menu and the meters' panels; the Tab row is fitted once in the first frame for the narrower
  width (`fitAhead`), then waits. Reduced motion (checked in JS, since Web Animations are not covered by the CSS rule), no
  Web Animations, or a hidden page: everything lands in one frame. At idle the capability deck refreshes once a frame at
  most (its observer was answered with a refresh about 69 times a second, mostly the magnifier rewriting its buttons'
  `hidden`, now written only on change), and no `:has()` has html, body or `.studio` as its subject (each change inside
  one re-styled the whole Stage, 8 to 10 ms, two or three times a second; `studio-shell.test.ts` guards it). Measured
  (Vite dev and the built studio, headless Chrome, 1440x900 and 1100x800, dpr 1 and 2, 64 presses each): the Stage passes
  through 9 to 12 sizes over 200 ms, the picture is never past its edge and never reverses, no frame over 20 ms after
  the press frame, one applyLayout per press, no script-forced style or layout pass in the click. Not measured in
  WKWebView or at 120 Hz.
- **Running a model (step 5).** One run page for every capability (`actions-panel.ts`, rendered only from
  `run-spec.ts`'s spec, so any catalog folder gets it with no app code): the spec is assembled from what the wire
  carries (the capability's fields, roles and catalog key; the entries' `newKind` / `memberKinds`, `usedFor` and
  abilities; each role option's readiness; contract/actions.json `needsTarget`). In the mock's order: what the chosen
  model does for THIS task (its kind's words, else its entry's when the option is the entry, else the option's own
  note: EdgeTAM automatic masks is not "Outlines what you click"); **On**, one row per declared data input (the
  Stage's picture with the specimen's thumbnail, a saved input behind its Change, a volume, each typed port, the
  selected outline a task needs, a click task's clicks); **With**, the chosen model as a card with its lit cell, whole
  name and where it runs (a Not checked model adds "not checked on this Mac yet", and "ran here · its check not run yet"
  once this page ran it without a failure: Hooke's own check, one run on the public example, is a record a run of yours
  does not make, contract/AGENTS.md; its tooltip says so), whose Change opens the models in place (Models' sections, Not on this Mac folded; each row a
  lit cell, the whole name in up to two lines, what it does here, at most one action under the name; a step nobody can
  press here says its reason, "Made from your corrections", and is not picked; a step only the person can take, their
  key or a licence, is the needs cell wherever its section is); a cloud model says where the picture goes ("Sends this
  picture to fal, billed per call") whether or not its key is there yet; **Makes** (the chosen model's declared output
  in its folder's words, `newKind.made`, its output's label: "Bright regions (not cells)", "Nuclei"; else its type in a
  few words, else the role operation's returns); **Settings**, only declared fields through `parameterControl`'s run
  look (a bounded number gets its slider, its unit in the box; a field's `hint` is its line under the field, and its
  tooltip), or "none declared for <model>"; a catalog entry not set up yet shows the settings its folder declares
  (`catalog_view.py` `fields`; an advanced one under More options), those under Settings disabled with its step under
  them ("Set up Fiji to change these"; "Set it up to change these" when no step is named; `run-spec.ts` `entrySpec`),
  or "its settings show once it is set up" when the wire carries none, never invented; **More options** only
  when the chosen model declares advanced fields (a task's field for some models only, contract/actions.json `fields`
  … `for`, shows and is sent only with them: Compare with the ImageJ macro is Analyze Particles'); **Results go to**
  This Tab | New Tab for every run but a click (a query's too: This Tab is disabled on a researcher's Tab, where New
  Tab must be chosen, so nothing there is a dead end); **What it runs on** (each role's model by its job, its methods
  and algorithms, Models ›) from the page's ⋯, under a hairline; and a sticky footer whose one primary spans it: **Run
  <model>** on one line (the short name, the whole one its tooltip), the chosen model's step ("Download 16 MB", "Set
  up Fiji", "Add key", in the server's SI sizes through `size-words.ts`), **Click in the view** for a click-driven task
  (the role's own model needs no arming; another arms the next click as the guided Try does, `tryClicks`, and one not
  checked on this Mac yet, which the server would refuse, has **Check it here** as its primary instead), or the run's
  state. A download starts in place (`download`) and the primary becomes its progress from the server's download row
  ("Downloading · 6 of 16 MB", its label drawn twice, the text colour over the track and white clipped to the fill, so
  it reads in both themes; Cancel), and Run once its model is ready; the strip shows it with a filling cell
  (`DownloadPills`). Key, licence, Fiji, install and check steps open the setup sheet (`#models/<id>/setup`) alone over
  the Project (the Models page under it is not shown: `data-setup-only`, workspace-study-surfaces.ts), which closes
  back to the page with focus on its primary. Why Run did not start, and why a click could not be armed or a download
  start, are said in plain words (the latter in the Failed footer's anatomy), never red body text. A chosen format-2 entry shows its own fields (Bright objects' Smoothing and
  Smallest object) and, once one is changed, runs its own capability with them; left at its defaults it runs as the
  task's model choice (one result). Running: fields and Change wait ("Changes apply to the next run"); the footer is
  its lit cell, "Running <model> on <Tab>", its clock from the studio's start, the run's latest Activity line
  (`activity`: a line of its run, or its own model loading, by the name Activity gives it; never another model's; a
  load said in one line by `loadLine`, "Loading Cellpose-SAM (on this Mac) · usually about 3.5 s", in the same words
  when written in place and when drawn again, `liveWords`), and
  Stop, then "Stop requested" (still cell, the clock going) until the server confirms, then Stopped
  in grey; the origin Tab's specimen gets a faint CSS veil (`data-run-veil`, pointer-events none). Done: when the reply
  shows a kept result (the server's `said`, its table): "<model> found N <its words>" from the server's own outcome
  (`outcomeWords`), how long it took and "saved on <Tab> and in Data" (S5a: a This Tab run on the Stage's still whose
  reply says `placed: "specimen"` keeps its window for the specimen, so it says "saved in Data; drawn on the specimen
  once checked there" until the studio draws its outlines there, `stillDrawn`, re-read on each `layer` through
  `layerChanged`: "drawn on the specimen and saved in Data"; the server's `action-placed` `tab`, its outlines not
  checked there, puts it back to "saved on <Tab> and in Data", final), the rest of its words, its model's note on what
  it makes (`newKind.madeNote`: "Bright regions are not a cell count"), Open in Data (Table when it made one) and Run
  again; an answer or an effect (Count them, Clear outlines) keeps nothing and says only the server's words and how
  long it took, with Run again (ADR 0006). With no table, a quiet Details (Activity: what ran, with which model; 0.3.0's
  Activity ›) closes that one row, which never wraps. Failed: "<model> didn't run" and the server's words, Choose another model
  (Run again when the task has no other) and Details (Activity). A model serving several tasks shows a switch under
  the header (two or three short names segmented; four or more, or names a segment would cut, a content-width "Task:
  <name> ⌄" opening the app's own menu), which shows the chosen task's page and then closes this one. The page's ⋯
  holds Compare models…, the model's card in Models and What it runs on above Left / Float / Right
  (`ControlHandle.setActions`). Use it here (Models, Tools By model; a named task from Models' Details) opens the first
  task the model serves (`modelTasks`, the task where it is the current choice first) with it chosen and the current
  specimen as its input (`useModel`, through the bridge); a model that serves no task here opens Tools By model at its
  row. A new result's outlines ramp in once, 320 ms inside the existing population draw (`view.ts` `rampOf`: one
  multiplication a frame while it ramps; only a population made after the page loaded, by its `at`, the first time it
  is drawn: a reload or an older result opened from Data appears at once; reduced motion skips it; no sim evidence of
  its frame cost yet). A population found on the still the Stage shows is drawn on the specimen through the layer's
  `fit` (`population.ts` `drawable`: the still's original pixels to the bridge's frame by its matrix, then the frame's own
  place on the Stage, at every Stage size and letterbox; only on frames of the size and epoch it was checked on), so its
  ramp plays there too; clicks and hover take the point back through it (`memberAt`; main.ts sends a click on a drawn
  member straight to the server's `member_hit`, which selects it without waiting for the outline model; the member under
  the pointer is drawn firmer). Everything else about it is in `docs/architecture/freshness-and-overlay.md`.
  The outline card (`layer-controls.ts`) lists the layers it showed before under a hairline, newest first, each one
  quiet line and **Show** (`present` show): Run again adds a layer under the card (Will's call, 2026-10-25). Each
  earlier layer stays listed with Show; one is drawn at a time (the server's layer shows one population, so Show swaps
  them; whether Will's call means stacking them instead is his to confirm). The list is kept per Project in this
  browser (`micromind-earlier-layers:<Project>`), so a reload, another Tab or page keep it; a drawn run is also listed
  in Data and under the Tabs menu's Completed work in Data. When the layer is shown but none of its members is drawn
  on the Stage and the server gave its reason (`why`: the Stage shows another picture, the picture is changing, the
  live view moved), the card says so under its count, in up to two lines (`notDrawnWords`: "Kept on the original
  image; open the saved result…" as itself, any other reason as "Not drawn on the Stage: <why>."), never the count
  alone; before its first check (no reason yet) it claims nothing.
  `run-page.fixture.html` shows the page's states over `src/run-spec.fixture.json` (fixture only).
- **The chat (step 6).** The Assistant panel's header is a drawer header (`mm-dhead`): "Assistant", the "<tool> controls"
  chip while a page is docked on the right, a ⋯ holding the conversations (each one, the open one ticked; New
  conversation; Rename this one…, whose field shows in the toolbar row only while it is open) and Activity, then ×. The
  `#thread` select stays the conversations' source (main.ts `renderDay`); the ⋯ reads it when opened. Under it the log
  (`ChatLog`), wrapped (`.chat-log-wrap`) so "New messages ↓" can float 8 px above the composer, then the starters while
  the conversation is empty (one sentence and three cards), then the composer.
  **One slot for the answer:** the old progress row is the assistant's own slot (`chat-slot`, `data-role="thinking"`): a
  12 px lit cell (`cells` hook) and calm words ("Working on it", "Writing the answer" once its steps have run), aligned
  with answers, no card. It is always the last line; a new line is drawn above it, so the answer takes its place as the
  slot goes, with no gap. It shows from `thinking` and clears on the answer (a typed assistant line of this request), an
  unpublished-answer status, a standalone step's receipt, another conversation (a replay of the same one while it is
  answered keeps it), the request's own reply (`settled`, from the `ask` result), or `SLOT_GRACE_MS` (2 s) after
  thinking ends, so it can never stick. A later line of an earlier turn (a late step) does not clear it. Steps still
  running after their answer are the Working strip's (`steps` hook, `StepPills`), not the slot's. The same place keeps
  the receipts that are facts in quieter words: "Photo displayed", "Earlier work has no final result recorded",
  "Connection lost · work status unavailable". The answer's ⓘ / ! / ✓ and its hover model line are unchanged.
  **Ack, card, outcome** (`chat-cards.ts`): when a `research` message lists a researcher a request of this conversation
  started, a compact card appears under that request: its lit cell, what it was asked (two lines), "Researcher 5 ·
  Choosing a model" (its progress words), "Working 0:12" without them (its clock, redrawn each second only while one
  counts), "Needs you", "Findings ready" (`researcherCardView` in `shelf.ts`, so the card says what its Tab and pill
  say), and a chevron; a click opens its Tab through `openStudyResearch`, which marks it opened. Which request: the
  session's `requestId` (S1, the server's request id) matches its start step's `requestId` on the request's steps; the
  answer's `outputs` name it (`research-run`, by researcherId); a typed request still being answered matches by the
  session's `turnId` and the request's Activity line, which arrives as it starts. They reconcile by researcher: one card
  each. While the answer is written the cards sit under the slot; then under the answer ("Sure." then the cards). No
  card for a manual start, another conversation or session, or a quick lookup. "Show it" goes through
  `openStudyResearch` too. A new answer and a card rise in once (`[data-arrived]`, 200 ms; reduced motion none).
  **Scrolling that respects reading** (`chat-scroll.ts` `ChatFollow`): your own send follows (`sent()` from `ask`, and
  your words arriving); while following, the end stays in view as lines are drawn and, through the injected
  ResizeObserver, as they grow. A reader who scrolled up stays put: the log has `overflow-anchor: none` and keeps the
  first drawn line in view in its place by hand (WKWebView has no scroll anchoring; hidden lines, which report top 0,
  are never the anchor), and news raises "New messages ↓", which
  jumps to the start of the newest answer (and from there to the end). An answer taller than the view is shown from
  its start; following stops and the pill shows. A Project's saved reading position comes back through
  `restoreReading`. **The composer** (`composer.ts`, one component; step 7's researcher composer uses it too): radius 14
  on the raised tone; the chip line (the specimen chip, a click shows it in this Tab or opens Inputs, then "answers go
  to <the Tab showing>"; or the saved scientific attachment, its words and Remove attachment); the field (#question, two
  lines to eight; Enter sends, Shift+Enter a new line, never while an input method composes; words put there by code,
  such as Help's Ask the Assistant or a researcher draft, go through `composer.set` so Send follows them; your bubble
  keeps the lines you wrote); then + (Choose from Data…,
  Open a picture or video…: the workspace's own routes, `setComposerAttach`), the Assistant's model chip (Seeing's
  model, "Gemini 3.8 Flash ▾", opening the Models defaults picker, `SettingsPicker.openFrom`, over everything, 6 px
  above the chip with its right edge on the composer's; a chosen model without its key adds "needs a key" in the
  attention colour, from its readiness, so the first question is not how the person finds out), Talk (its microphone and word, every voice state kept; its live status, Mute and Activity at the composer's
  head) and Send (grey with nothing to send or while the request is answered, blue with words). No stop: no command
  cancels an answer being written. The composer stays in the Assistant panel at every width (the old phone layout's
  fixed composer is gone). `chat.fixture.html` shows the states over synthetic messages (fixture only).
- **The researcher Tab (ticket 66 D3, after step 7).** A researcher's page (`research-workspace.ts`, drawn by
  `research.ts` from its record; words and lists in `research-tab.ts`; the mock's research, research-docs,
  research-sources and research-narrow frames in `design/data-research`) is two columns told apart by tone: **its
  conversation** on the left and **the Data reader** on the right, the same component as everywhere (`data-reader.ts`,
  mode `research`, root `#data-reader-research`, its screen report identity `document:reader-research`). The Files,
  Used and Outline column is gone: Files became the reader's switcher, Used went into its page's How it was made, and
  the outline stays inside the page (Sections). **The conversation** (456 px, 392 px below 1240 px of page; the panel
  tone): one inner Tab, Conversation (no file Tabs and no +: tables and charts open in the reader), the brief ("Brief
  from the Assistant"), its steps as compact rows
  (`researchSteps`: a tick, the lit cell on the step at work, the still cell once Stop is asked, the partial cell for an
  early write-up, a speech bubble for a note it read; the step's own words split at their first colon, the second line
  in sentence case; a run is one row named by its tool, "Ran Bright objects", with what the app said it made as the
  second line, the worker's "Asking the app to …" and the app's reply whole on hover, and chips for what it made, named
  as Data names them ("Bright regions"), each opening in the reader), its questions once (`researchQuestion`: Keep going
  / Not now with the server's words while it waits,
  `evidenceActions`; once answered in the past tense with your answer as your words, unless your own message already
  says it, so an answer given through the Assistant or by voice shows too; finished without an answer said so; its
  "Asked the user…" step is not repeated), Continue on partial or interrupted work (asks the Assistant in your words,
  `research.onAsk`; it says whether it can; offered only where it can act), what it kept when stopped or interrupted
  (a chip that shows its notes), your notes and follow-ups (a note says "It reads this at its next step" until its Got a
  note step), and how it ended; the log follows the bottom only while you are there, and each researcher's log keeps its
  own scroll. The shared composer (`composer.ts`) has no Talk (voice goes to the Project's Assistant); its + attaches
  only what this researcher made (a file or a section, sent as the message's exact selection); its model chip names the
  researcher's engine (the catalog's name) and its menu says it is fixed for this run, with Ask a new researcher; its
  placeholder follows the state (`researchPlaceholder`). **The reader** (the rest): its header is the mock's rHead,
  the researcher's lit cell, the **switcher** as its title (the title and ▾: "Made by Researcher 3", everything it made
  as Data lists it, `workOf`: the index entries whose `maker` is it, its report first, then its lessons, videos and
  pictures, then its findings' tables and charts and the outlines its hands made, each "Title · Kind, count" in its
  record's own words, `switchLabel`; then "Sources · N", the sources it cited as cards with site ↗ and Cited in; then All
  its work in Data, which opens Data and sends `hooke-data-made-by` {rid, name} for the maker filter), its kind and the
  researcher's state words with the clock while it works (where the status pill said them), Fit, download only for an
  output with its own file, ⋯; then Page (or the kind) | How it was made. Its own write-ups are **its page**
  (`ReaderPage`, `pageItem`: its report `<rid>`, its lessons, videos and pictures `<rid>:<first output id>`): the
  real output, the findings or presentation drawn by `presentation.ts` on a paper page in the presentation's own theme
  on the desk (true black, light grey in light; paper when none was chosen; the theme's colours are
  `research-workspace.css`'s `--s-*`, never interface tokens), with every head in the page's one heading style, its
  sources as numbered references ("[1] nature.com ↗") and a long table as its first eight rows, its count and Open (the
  table in the reader), numbers set right; a lesson in its sandboxed frame, a video or pictures. Choosing one of them in
  the switcher asks research.ts to show it (`itemValue`), and the reader follows the page (`pageChanged`). Everything
  else draws with the reader's own renderers: a step's link and a record it made or read open in this reader, never
  leaving the Tab. Its ⋯ on its page: its versions when it has more than one (each findings version with its time,
  each output by kind, Latest first), Show beside the live view (the same present / presenter routes as before, which
  open it in a window beside the live view on the work Tab and go there), How it was made, Open in a new window, Ask
  about this page, make, make again or stop its presentation, then Open in new Tab, Add to Tab…, the Tabs it is on and
  Move to trash; on anything else Show on Main ↗ (leaves this Tab and opens it in Main's reader, where its windows and
  tools are), then the same. How it was made on its page: its versions, each a press that shows it, then the reader's
  rows (written by, made from, sources), then **Worked from** (the live view it was asked on opens Main ↗, the records it
  read open in this reader, the tools it ran open their run page ↗), its record's words (basis, what it noted with
  where it read it, where it read, its effort and spending, the live view when it was asked) and today's research
  spending. Each researcher's reader comes back as it was (what it showed besides its page, `ReadingState.doc`); Talk
  to Researcher N from another Tab's reader opens its Tab with the same item on its right (`openOnResearcherTab`); a
  Data row clicked while a researcher's Tab is in front opens in its reader and the Tab stays (`showInResearchReader`).
  The workspace gives the reader Data's entries and its ways out (`provideResearchReader`; without it, as in a test, it
  shows its page and sources and nothing leaves the Tab). The page's
  page bar floats in its own 56 px band and holds
  only Sections (the page's outline), shown while the page has sections; the page is one flowing sheet, so there is no
  page count and no arrows. New sections glow once while it writes; the desk follows new writing only while you are at
  the latest part; before anything is written one line says what it will make (`willMake`). **Widths** are container
  queries on the page, never the window (`.research-workspace .rpage`; the Study home keeps its three columns): the two
  columns from 860 px, sharing the width (the mock's research-narrow at 1100), one below (Conversation | the reader as
  inner Tabs, the composer under both). The divider between the conversation and the reader drags, resets on a
  double-click, and is remembered per researcher (`hooke-research-layout:<rid>` in localStorage, its wide and narrow
  widths only, every read and write tried, reaching localStorage itself included, since a browser that blocks site
  storage throws there). **The Stage
  bar** on its Tab holds ← Main, what it was asked to do (`researchStageBar`: its label or brief, never the findings'
  title its page already shows), its state words and clock (Working 1:01:13, Waiting for you, Stop requested, Stopped,
  Interrupted, Took 0:28) and Stop while it can be stopped (pressed, it goes and the words say Stop requested until the
  server lists it stopped); the page has no Stop of its own. **The Assistant** steps aside on a researcher's or a
  Study's Tab: its Stage bar button (or anything that opens it there, a Study's Ask box too) shows it as a 360 px layer
  from the right over the reader (`assistant-layer.ts`, `data-assistant-over`), × or Escape closes it and focus goes
  back to the button, and a work Tab finds the panel as it was. `workspace-researcher.fixture.html` shows writing, a
  table opened, partial findings (report), done, needs you, stop requested, stopped, interrupted, just started and the
  Study home over synthetic records (fixture only; drawn before ticket 66, it still shows the three columns).
- **0.3.0's results Tabs** (ticket 66 D5; `legacy-output-tab.ts`, the mock's legacy frame). A Tab of kind `output` with
  `provenance.researcher` (no longer made) shows a pointer on the Stage instead of the old report window, whether or not
  the researcher's own Tab is in the row: the researcher's lit cell, "Researcher 3’s results now open in its own Tab",
  its result's title and what of it is in Data from Data's entries, said only as far as they exist ("Its presentation,
  its table and its outlines are also in Data.", `inDataWords`), Open Researcher 3 and Close this Tab; a researcher
  this Project does not list is said so, with Close this Tab only. The Stage bar names it ("Researcher 3 results · Made
  by an earlier version", `data-legacy-tab`). Its saved windows are kept as they were, never restored and never
  dropped by a capture, so nothing is lost; an old "Go to results" notice of one opens the researcher's own Tab.
- **Models and Add model or tool (step 8).** Models (`modelspage.ts`, the mock's `modelsSheet()`) is one grid of one card
  shape a section, and its sections are run-spec.ts's `modelSection`, the ones Tools By model and a task's model list
  use: Needs you, Ready (face on or obtained, the one meaning of Ready), Not checked, Can be set up, then Not on this Mac
  folded; so the summary, the side's counts (`workspace-study-surfaces.ts`: a card's `data-section`, never its words;
  where it runs by `data-runs`) and Tools agree exactly, and a section with nothing in it is not listed on the side. The
  sheet: a 60 px header (Models; **Add model or tool**, which opens the three ways in, through `hooke-models-add`; the
  search with its mark; ×), the 200 px side (All models, the sections with their counts, a hairline, Keys, Storage), the
  where-it-runs chips, the grid (220 px columns, 200 px rows). **The card** (`card`, its look and verb from
  model-status.ts `cardLook`, tested in `model-status.test.ts`): its lit cell and name, what it is and where it runs
  ("Model, on this Mac", "Cloud model, Google", "Built-in method"), what it does in two lines, one facts line ("On this
  Mac · free", "413 MB · free", "Your Google key", "Your choice for find all like this", "Needs Fiji · free", or a setup's
  own words while it works; the face's words are its tooltip), one action at the bottom; a click anywhere on it opens
  its Details beside the grid (`model-card-hit`, 360 px pane). No step lists, terms or status pills on a card: they are
  in its setup sheet and Details. **One verb per state:** Use it here (ready and serving a task here: `useModel` through
  `hooke-use-model`; a ready entry that serves none, and every data entry, says "Used by the Assistant and researchers"
  with no button), Check it here (Not checked: its sheet), Download 413 MB (started in place; the card then fills from
  the server's measured bytes, "Downloading · 42%" with its setup cell and Cancel; a stopped download says where it
  stopped and offers Retry and Details), Add key and Accept licence (the person's step, on the button; the cell is its
  section's: blue only in Needs you, the grey outline in Can be set up, as Tools By model draws the same row), Turn on, Set up
  Fiji, Allow (its sheet), Continue setup while a setup works, Retry when one failed; a step nobody can press here says
  why on its facts line ("Made from your corrections"); an install only the person can carry out is How to install
  (its Details); a contribution waiting for its review keeps Review comparison, Choose a comparison picture or Review
  results, never Use it here. The comparison picture is chosen with the studio's own control (`file-picker.ts`
  `filePicker`: "Choose a picture" and the chosen file's name, the browser's input hidden but focusable), never a raw
  "Choose File / No file chosen", and so is a brain volume (`volumespanel.ts`: "Choose a volume"); Add your own says
  where yours live on this instance (`setup.mineFolder`, the server's `mine_root`), never a fixed ~/MicroscopeMind; each comparison row (`.own-trial`) is a grid, so its thumbnail and Compare on my
  picture never overlap. Outside the workspace (no run page) a ready card keeps its setup step. The amber stripe on
  Needs you cards is gone (the needs cell says it). **Details** (the mock's `modelDetail()`): its name and a 28 px ×
  ("Close details"), what it is and does, then **Tasks**, one row per task a person runs here (by name, once), each with
  Try it on <specimen> where it can run (`served`, `specimen`: actions-panel.ts `served` and `stageName`, wired in
  main.ts), which opens that task's run page with it chosen (`useModel(model, task)`), so a Not checked model is one press
  from use; a Not checked model's Tasks are followed by what that means (`tool-check-note`: Hooke's own check is not
  recorded on this Mac; trying it on your picture does not record it, Check it here in its setup does); then Size, Licence (its short name) and Pictures. A candidate's Prepare for review stays in view; everything
  else (every capability, the Assistant's too, demonstrations, trial and setup controls, alternatives, facts and files,
  versions, updates, Turn off, Remove, Share, Report) is under a closed "More about this model" (How to install opens it).
  Its check line says its time as people read it ("works · Oct 28, 2:46 am · 1700 ms", `readableTime`), never ISO. Its
  versions (`model-versions`) and Updates (`model-maintenance`, maintenance-status.ts) are blocks the pane's width that
  wrap, never `model-card-action`, which the overlay draws as one row that never wraps (the thesis check on 0.5.2: the
  update line was one letter wide and the pane scrolled sideways). Versions leave out an update maintenance prepared and
  has not applied (its Apply is in Updates), and newest and older are by when each was made, since the catalog's pin
  can be behind an applied update: "Go back to 2" before 2 ran, or "Use the newest" for the version it replaced, was wrong.
  The pane stands sticky beside the grid at the body's visible height, so its hairline runs top to bottom; under 1100 px
  it takes the grid's place, opening at its top and giving back the grid's scroll when it closes. Escape closes Details
  alone, as it does a setup sheet. **Storage** rows are `capability-space-item` (their own flat rows, never a card), and
  the band's older Add model or tool box is not shown in the sheet (the header's button is the way in); the where-it-runs
  chips show only where there are cards. **Add model or
  tool** (`capability-entry.ts`, the mock's `addSheet()`): from a header it opens on three plain choices (Find it in the
  catalog, I have a link or file, Have AI build it: a mark, a title, one line, a chevron), with "Advanced contribution
  controls" under them (Models' older Add your own and its friends, opened as the side's Advanced contribution, through
  `hooke-models-category`; that category also shows From the community, whose Where entries come from holds Add channel,
  the route by which a brought-in tool is offered updates: `workspace-study-v2.css` hides every other body section there,
  and the catalog categories hide all sections without cards); once one is chosen the three are a quiet switch above it, and `#models/add-<way>` opens a way
  directly as before (`#models/add`, Tools' Add model or tool, opens the choices, every time it is pressed: the guard that
  lets the workspace's second dispatch of its initial route open it once is reset when Models hides). From Tools
  (`#toolbox/add…`) the sheet stands alone over the Project, like a setup sheet opened from outside Models (`sheetAlone`,
  workspace-study-surfaces.ts), and closing it returns there; its Advanced contribution controls open Models first. Its sheet is titled Add model or tool,
  with no capitalised eyebrow. I have a link or file is its field (32 px, as the mock's search) and a file's drop, then
  Review beside Close on one row (`CapabilityEntry.footer`, placed in the sheet's actions), Review disabled until a link
  or a file is given. The review is the candidate's own page (the method switch steps aside; Change source is the way
  back): its name, what its catalog row says it does (nothing until the row arrives), then its real facts before anything
  runs (where it came from, `sourceWords`: "From bioimage.io · diplomatic-bug"; then `reviewFacts`: its size on this Mac,
  where pictures go, its licence, what it costs; what its row does not say is said as not known, and "Reading what it
  takes…" until its row arrives), then Review setup (its setup sheet: set up, check, Ready, then Try it on <specimen>)
  and Change source side by side. A search with only close guesses says so in a quiet line. Every endpoint and route stays. The find results are
  `capability-entry-result`, never `model-card` (Models' filters count only catalog cards, `.model-card[data-section]`).
  `models.fixture.html` shows the sheet, the add sheet's link step, review and ready states (Ready reached the Add way),
  a download filling and a stopped one, Details, Can be set up, Not on this Mac, Storage, a brought-in tool's versions
  and Updates after Apply (`?state=maintenance`), a shared file's tool (`shared`), Advanced contribution's channels
  (`channels`), and light, over synthetic rows (fixture only); `scripts/audit-shots.mjs`'s `models-fixture*` scenes shoot
  them with only the studio's Vite server running.

Live Tab restoration waits at most three seconds for the first bridge health observation.
A startup `waiting` state with a recorded bridge health error and no live bridge is
observed camera absence even before the server's three-failure offline threshold;
saved documents can open with Camera unavailable. No observation remains an explicit
opening failure with Retry. The same boundary rechecks Project, recording, voice and
active-run guards after waiting, and never converts a genuine source-selection refusal
into camera absence. Retained recording selection does not wait for camera health.

The Workflows page is a lazy React island with a personal library and Project-owned run history. Exact input revisions and pinned settings are frozen on Start. Its catalog capability form uses the same one-step workflow path; missing runtimes remain unavailable. Hidden Project editors retain unsaved drafts. Results open through the existing retained scientific-document surface.
Reviewed file-capability catalog cards link to `#workflows?capability=<id>` and that capability's exact-file form; they do not offer a live-picture action. Current readiness comes from the workflow descriptor. The Study rail uses the same named-input keys, keeps unchanged retry identities and links to retained runs with explicit interrupted/unstarted execution observations.

Local Study setup reuses the retained Study Tab: `study-local-researcher.ts` admits the existing
configured researcher with an explicit allowance and a stable request, and `study-workflows.ts`
selects a frozen workflow and exact named file bindings. Researchers open in their retained
`#research/<id>?day=…` Tabs; workflow rows link to their canonical run. Claims and challenges show
attributed statements and exact evidence links in the Study. Local completion means finished,
not scientifically accepted. Stop failures stay visible; the original runs establish termination.

Study workflow admissions count alongside provider tasks in Researchers; an existing
workflow is never described as “Not started” or “No experiments yet.” The Study's
central conversation exposes each workflow's observed state, named ordered attempts
and exact result revisions; setup stays in the inputs rail. Finished local researcher
reports remain explicitly unreviewed. Tools lists scientific file capabilities with
links to the same frozen single-capability workflow form, without offering live-view
execution. Computed matrix documents show their retained numerical summary, runtime,
parameters and original input revisions, plus a verified file download; opening them
never recomputes a model or treats its output as independent validation.

Workflow selectors do not mark the Study prose as an unsaved draft. Built-in native
workflow results open the retained-array tools without requiring or inventing an
external viewer runtime. Computed file exports are labelled as results, not original
sources. External worker output still requires its declared retained runtime.

Connected agents have a Project access control beside Disconnect. It names the
exact Project and connection, distinguishes read from contribute, preserves the
creator, and reports failed grants without claiming they saved. The source-Studio
owner confirmation route is explicit; a packaged instance without qualified native
grant support shows unavailability rather than offering a nonfunctional grant.

Owner Project access lists the retained canonical Projects, including those created in
the UI. It supplies the launch Studio token when packaged, never an agent bearer token;
source development uses its explicit owner mode and same-origin request context.
Seeing a Project in this owner picker grants nothing. A confirmed grant records the
person as creator and the connection as reader/contributor without changing the stage.

Retained comparison choices use the resource index's canonical comparison input key, not temporary
frame numbers. Independently captured runs of an unchanged imported image can share a Compare Tab;
unknown inputs and unrelated Project/revision/method entries remain excluded. Opening never reruns a model.

Connected-agent settings use compact identity/action rows with a reversible Project-access
expander. Its form retains the owner confirmation and exact connection/Project scope;
copying setup remains a separate action. Connection explanations and permission details
stay available in disclosures, without dominating the list.
Retained matrix summaries use label/value rows with bounded column widths. Large arrays,
objects and text expand into a scrollable exact metadata view on demand; opening a result
never reruns computation. Download and exact input/revision links remain available.

The retained Project overview presents each Study's saved title and status, expandable question/criteria,
separate input/experiment/report/finding counts, and the latest appended attributed researcher report.
Reports are not accepted findings; full statements and limitations remain expandable. Contributor labels
describe prior authorship, not live presence. Stats navigate to existing Project Data or the retained Study
tab, never start work. Project-opening guards and unchanged-refresh focus preservation remain in force.
