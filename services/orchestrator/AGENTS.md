Owner project-open-folder accepts strict boolean useLocation for a known origin only. It reuses inactive registry replacement with full history verification, both writer locks and the short publication guard. Local grants and accepted bytes are unchanged; unknown origins and conflicting legacy owners refuse.

Owner `project-open-folder` registers a selected working folder through project_launch.register_owner_folder off the event loop. It does not navigate, import access or replay work. Studio uses its ordinary unsaved-work navigation afterward; late completion never changes the workspace. This path-bearing owner command is excluded from the agent door.

# The orchestrator

Owner commands `project-storage` and `project-reveal` capture the current Project
and generation around off-loop location verification. Reveal uses only that verified
folder (or the particular legacy journal folder), never a caller-supplied path.
The read response shares `project-storage` contract semantics with the agent door;
agents continue to use their read-authorized `project.storage` action. Owner Finder
controls do not widen door authority or certify complete collection.

The normal server supports operator-only staged Project routing through
`--project-folders /absolute/parent` or `HOOKE_PROJECTS_DIR`. `project_launch`
constructs the existing resolver before App creation, refuses malformed locations
and refuses to forget an existing registry if the setting is removed. This is an
explicit qualification mode, not default activation or legacy migration. Launch it
through scripts/studio.sh in an isolated profile; requests and saved records cannot
choose this configuration. Full collection and owner folder controls remain separate.
`--reopen-project` / `HOOKE_REOPEN_PROJECT` is an explicit startup recovery for a
registered origin after moving its inactive folder. It verifies both ownership and
the compatible handle under the writer/registry guards. Unknown origins and active
originals/copies refuse. This repairs only a host locator, not grants or evidence;
ordinary new-folder imports and independent copies remain separate.

Dataset-table and analysis/graph snapshots use the captured Project's artifact
folder when `day.locations` is enabled. Legacy paths remain unchanged. Shared
`ArtifactFolder` routing pins the origin/location and folder inode, acquires the
host's writer lease before publication, and refuses closed/replaced storage.
Numeric index reads resolve the recorded owner without switching the workspace.
Every accepted table/derivative revision keeps its original relative path and hash.
This is an opt-in storage adapter, not complete Project collection or live App
activation; missing evaluators do not prevent reading retained numeric results.

`scientific_text.page` gives the connector and ordinary file preview the same bounded
UTF-8 byte-page meaning. UI text reads validate the accepted source manifest and hash
the displayed chunks only; they never claim fresh integrity of unread content. Exact
Project/document/revision, source hash and byte range accompany the response. Downloads
keep their whole-source verification. Text is untrusted data, never executed markup or
proof of a ready scientific reader.

Scientific tool selection distinguishes the active local tool from an explicit current-update
trial. The latter records the exact prepared generation and captured saved input without
moving selection. Candidate workflow acceptance lives in the existing maintenance decisions
ledger; active/rollback own-use acceptance remains intact. The selected generation recognizes
only its own exact accepted receipt. Public distribution qualification is unchanged.

Part of the working context the root `AGENTS.md` maps. "Above" and "below" may mean parts it lists in
other files.

Retained capability tables keep a compact versioned table/digest routing hint outside
the 1,500-character journal preview. Data and action completion share `Results.table_candidate`;
historical cut previews consult only their owned full record (at most 65 MiB, regular file,
no symlink) without expanding the index's values. A 128-entry routing-only cache keys
legacy classifications by owner and file identity/size/timestamps; discovery checks changed
files again and full table reads never trust that cache. Synchronous Index lookups default
to metadata-only hints/previews/cached routing, without loading legacy table payloads.
The resource listing/detail HTTP routes explicitly opt into discovery on their existing
worker thread, preserving historical Data opening and Measurements navigation.
Explicit dataset-table/analysis/graph reads additionally verify and expose their accepted numeric snapshot.
Listing stays metadata-only; historical reads use the original journal pin and owner-relative immutable file,
without rebinding the live Project. Hash/identity failure reports unavailable values. Reads never evaluate settings.
Action completion captures live evidence
and result ownership before awaiting table discovery/value verification off the event loop.
Missing or malformed legacy metadata
does not invent a table. This hint routes to the existing Measurements/CSV surface, whose
full-value reader still checks the producer digest and Project/revision before displaying
or exporting. CSV exports retain round-trip numeric precision; compact display rounding
does not alter exported values. No adapter-specific rendering or new table store is involved.

## Workspace input references

Scientific view admission uses the shared supported stage-layout schemas, including schemas 5 and 6.
A new Tab kind never bypasses captured Project, exact revision, placement or current-frame checks.
Layouts 2–4 keep their original fields; journal and completion paths preserve schema-5 metadata.
Schema 6 (ticket 66) adds the reader Tab, `{kind: "reader", items: [{resource: {day, id, version}, title}]}` with the
specimen hidden: one entry of the index read full width, a reference only (`workspace_views._checked_reader`: exactly
one item of this Project, its placement identity optional, no notes, input, provenance or lanes). Schema 5 layouts load
and save unchanged; a client that asks for schema 5 of a schema-6 layout is told to reload. A run's completion never
lands in a reader Tab (it reads one thing): it stays available, as one that does not fit. Comparison tables read the
newest schema (`DataTables.from_comparison`).


`workspace_views.py` layout format 4 optionally stores each Tab's live/asset source
reference and playback position. It stores no pixels and neither admits nor executes
analysis. Existing source commands resolve library membership and availability at
selection; missing assets refuse there. Format 4 readers preserve older arrangements;
older readers/writers refuse a persisted format 4 layout. Completion reconciliation,
removed-Tab recovery and job-origin ownership preserve these references unchanged.
Scientific-window admission accepts formats 2–4 and still matches the exact Project,
Tab, placement and retained resource revision. A format4 input/playback reference does
not grant camera access, change the saved scientific input or retarget an open worker.

## Options, not replacements

The built-in Fiji Analyze Particles binding declares its owned JVM process while retaining
analysis-lane scheduling. Readiness still requires the exact active-generation proof;
it does not require an unrelated GPU worker to be running.

RGB/RGBA native outputs use `native-rgb-output-v1`, with an explicit trailing
color component and red/green/blue[/alpha] identities. Spatial transforms exclude
that component. Exact bytes and dtype use the existing bounded atomic publication
path; reopening explicitly sets RGB interpretation, and scalar three-wide arrays
remain scalar. Legacy output metadata remains readable. This does not lift the
64 MiB limit on new RGB outputs or qualify large-data capture.

Output coordinate relationships are explicit. The host advertises
`Context.output_binding_version == 2`; absent capability means version 1.
`bind_view_output(version=2, ..., meaning=..., coordinates_from=bound_input_layer)`
declares the same scalar input grid for a new scalar/RGB output, without granting
input edits or inheriting input meaning/dtype. `scientific_output_coordinates.py`
registers the actual bound identity and source geometry without claiming completed
work or taking a snapshot during asynchronous output insertion. First describe/save
requires the unchanged quiescence guard and compares frozen layer values with the
immutable admitted source, read through bounded native grants. Only that accepted
source proof is cached across outputs; current values are checked on every capture.
Dirty values before declaration cannot become a new baseline. Inputs are bounded to
64 MiB; lazy/structured inputs are unsupported.
The parent independently checks the exact admitted native reference, accepted source
values, ordered axes, shape, affine and units before staging. Dirty inputs must be
accepted and the operation rerun. All related sources are checked after freezing and
before transfer. Existing quiescence, workspace and atomic publication guards hold.
Only calibration is inherited; output provenance records the explicit source reference
and value proof beside the existing originating revision and runtime. Export follows
that exact base. Reopened metadata has no live declaration; identical coordinates do
not recreate a source relationship. Version 1 stays unchanged. Actual package/native
qualification is separate from these synthetic worker and real storage regressions.

Large data (H08). A retained grey image over 64 MiB, and every retained multiscale image
(a scene row with `levels`, finest first), opens as a lazy layer:
- `env_worker/lazy_arrays.py`: a LazyNative (napari's LayerDataProtocol) reads only the
  chunks a slice touches into a shared 256 MiB chunk cache. A whole-array read, a slice over
  256 MiB, and fancy indexing are refused in words. A lazy layer is never bound as editable.
  Retained lazy grey images initialize contrast from napari's displayed slice after
  all native layers are added; they never scan the volume for display limits.
  Remembered image appearance is applied afterward and overrides this initial fallback.
- Everything else (labels to edit, small images, geometry) loads whole through bounded view
  reads, up to the worker's 512 MiB editable allowance together. Labels above512MiB instead use
  `env_worker/lazy_labels.py`: the same regional read/cache grants plus immutable private changed
  chunks, sharing a64MiB dirty allowance across restored Labels. Each assignment touches at most16MiB
  of chunks; coordinate vectors are bounded before grouping. Native napari painting and undo atoms remain
  authoritative. Existing gesture/history limits, captured geometry and frame ownership still apply.
  A gesture snapshot retains immutable changed chunks, not a whole-volume copy; digest checks detect
  edits outside the manual history. Rollback restores the exact prior private map and native history.
  Explicit Save freezes changed chunk identities and uses the existing accepted-revision edit transaction.
  Untouched chunks send `keep-native-base-chunk`; the parent resolves and checks its own full accepted
  digest, never trusting a truncated digest. No unread chunk is scanned by the worker during Save.
  Reopening uses retained native values without replaying edits or the creating tool. Whole-volume
  materialization, oversized edits/fills and automatic whole-volume maximum/new-label discovery refuse.
  Smaller Labels retain their eager behavior. Actual native/UI and full working-set qualification remain open.
  The64MiB dirty counter is not a resident-memory measurement: a serialized gesture can retain another
  starting map of at most64MiB, while one assignment prepares at most16MiB of replacements plus a1MiB
  chunk buffer. Save freezes at most64MiB across its layers. Native undo and its rollback copy each remain
  bounded by the existing64MiB history allowance; cache, displayed slices, temporary indexing and native
  rendering consume their separate allowances. The unchanged process guard remains authoritative for
  aggregate footprint. Save still conservatively reserves logical-source-sized free disk; optimizing that
  reservation and admitting first-time outputs above1GiB are separate work. Native publication checks
  its owner's cancellation between metadata and individual chunk verification operations; Stop preserves
  the accepted head and leaves the candidate available for an explicit retry.
- View reads carry `view: true` and are admitted per rolling 10 s window (512 MiB, 8192
  requests) at both ends. Operation reads keep the 64 MiB lifetime cap.
- `ngff_sources.py` opens an accepted immutable selected folder as OME-Zarr 0.4. SelectedStore uses
  the existing ProjectStore source-file manifest and complete directory index. Regional opening verifies
  accepted ownership and metadata; each consumed retained chunk is digest-checked. The default
  `file_source` open still verifies all content. Metadata-only opening never asserts fresh whole-file integrity.
  Reader binding reuses accepted file chunks through `scientific_files.retain_into` and the existing
  FileIntake staging/publication transaction. Each reader document owns separate directory entries to
  immutable chunks, so removing its original source does not remove its bytes. Binding checks the exact
  accepted revision, manifest, read-only regular chunk identities/sizes and retained runtime; it neither
  copies payloads nor claims a new whole-file integrity scan. Every later read hashes its consumed chunks.
  Hooke's `_put` never overwrites accepted objects; hard links share physical storage and do not isolate
  external chmod/write corruption, which reads detect. Missing/unsafe chunks or unsupported hard links
  refuse before publication. Disk admission reserves bounded metadata/link space; uploads keep their
  full byte reservation and verification. Existing source/reader identities and retry receipts persist.
  Raw local/remote readers cannot establish an accepted pyramid revision. Transport reads are bounded,
  reject symlinks/redirects and keep broker credentials in the parent. zlib/gzip decoding stops at the
  declared chunk length plus one overflow byte; no unbounded flush is allowed.
- `bench/h08_large_data.py` requires an exact accepted source Project/revision. It checks opened
  shape/dtype/levels, labels footprint observations as sampled and refuses a missing measurement.
  Entry-byte counts exclude physical chunk-verification I/O. No report closes full H08 qualification.
- NGFF dataset scale/translation chains precede root chains; native descriptors and benchmark metadata carry the
  composed calibrated origin and basis. Unknown, nonfinite or numerically unrepresentable transforms refuse.
  The current multiscale renderer derives level coordinates from shape ratios: differing level origins or scales
  it cannot represent refuse with guidance to open separate calibrated levels through the ordinary reader.
- Large-label editing has bounded actual native/UI evidence; the ≥4×budget workflow remains unqualified.
  Multiscale writes, opening an OME-Zarr folder or URL in place, and immutable remote lazy intake remain incomplete.
- Absolute slice navigation can replace one owned in-flight target. The parent retains one latest slot,
  at most256 sequence identities and the original request deadline. Axis/display changes, edits, Save and
  plugin actions keep ordinary busy admission. Every replacement matches the original connection, Project,
  Tab, placement, source revision and draw receipt. Superseded display reads abort obsolete chunk loops
  without terminating the private worker or resetting unsaved edits; only the final rendered target is
  offered for browser paint acknowledgement. Only exact cancellation or recognized psygnal wrappers are
  recoverable; unrelated cleanup failures propagate. Synthetic broker/ownership checks are separate from
  pending native/UI navigation qualification.

Regional reader integrations may call `Context.decode_image(input=..., shape=..., dtype=..., axes=..., decode=...)`.
The named input must be an admitted regional file/folder grant. The synchronous decoder accepts basic region slices
and returns an exact numpy array with the declared native dtype and shape, at most1MiB. Integrations own deterministic
format interpretation and bounded codec allocations over their immutable input and fixed settings. The host detaches
returned buffers, shares a256MiB image cache across that Context, and uses ordinary grey-image Save to retain decoded
pixels through atomic publication. Reopening those results never calls the decoder. Unknown lazy objects, lazy Labels,
RGB decoder-backed output capture remains unsupported. Aligned grey pyramids can Save their exact ordered native
levels through shared atomic publication. Each level keeps dtype/shape and calibrated shape-ratio geometry.
Immutable regional levels stream; mutable NumPy levels freeze together within64MiB. Ordinary publication stays
at eight native inputs/1GiB. Larger new publications require only immutable regional grey images, with
`regionalSources` matched by the parent against the current native or explicitly regional file grants.
The worker independently re-describes actual supported immutable sources before and during capture.
This exact owner may admit up to64GiB aggregate logical output and65,536 chunks; memory/cache,
1MiB messages, rolling read rate, source identity and disk-space gates remain unchanged. Capture paces
only its regional view reads, checking cancellation at most50ms apart while waiting; ordinary navigation
still refuses over-budget reads. Transfer receives a finite size-derived deadline (60s through1GiB,
then60s plus twice the minimum rolling-window transfer time); the overall capture has a3,100s ceiling
including metadata headroom. Parent final verification has a separate finite work deadline and checks
Stop between chunks and before publication. Only the internal private publication receives the exact
admitted new-byte allowance; normal ProjectStore writes retain1GiB. No working memory allowance
is derived from total streamed bytes. These are bounded admission contracts, not measured large-data
throughput, full working-set or ordinary-UI qualification. Tool-state Save still refuses multiscale pending
its separate identity contract; native output Save is not a claim of package/UI pyramid qualification.
Grey-image publication visits consecutive rows and leading-axis slabs in at most1MiB chunks, so a decoded
stack does not reread every stored source plane for each small spatial tile. This is a storage traversal choice,
not a larger read/cache allowance or a guarantee for every source layout.
The OME-Zarr companion opts into regional grants and uses actual upstream synchronous slice decoding above32MiB.
It validates all declared shapes/calibration before adding layers. Uncompressed stored chunks are at most8MiB; its
per-grant four-entry source cache avoids repeatedly fetching the same chunk (at most32MiB resident). Labels remain
bounded/eager. `Context.streamed_image_publication` returns a fresh `{'version':1,'maximum_bytes':64<<30}`
feature description. A reader must check this before admitting a larger source on an older host; the
feature is a bounded supported Save contract, not scientific/large-data qualification. Missing or
unsupported versions do not grant larger publication. OME-Zarr companion v6 negotiates this feature before
opening larger images; older hosts retain its1GiB refusal. Labels remain32MiB in that reader. Shared streamed publication does not
automatically expand an adapter declaration. Larger reader acceptance and ordinary-UI qualification remain open.

Native scientific workers initialize Qt through napari's `get_qapp` before VisPy claims the application and the
CGL backend is installed. This retains upstream application defaults, including tabular numerals; VisPy still
owns the native app for the worker lifetime. Existing applications, offscreen enforcement and cleanup remain
intact. The default napari 0.9.2 runtime still needs actual verification; retained-source inspection and the
current native comparison use the separately approved 0.7.1.dev0 reference.

Native scientific panels retain Qt's offscreen raster style. The pinned Qt6.12.0
stylesheet layout clips NoButtons numeric fields to one pixel; a private-application
compatibility rule aligns only those absent button subcontrols left, preserving
the full numeric value. Native macOS styling is unsuitable: it leaves controls
unpainted offscreen. Exact pixel/state admission, callback error capture and resource
bounds remain unchanged. Confined Qt checks require visible choice/action text,
unclipped numeric values, stable paints and delayed scroll-panel actions. They do
not independently qualify an integration; other OS qualification remains open.
The bridge activates accessibility in its private offscreen Qt application so
list/table model changes invalidate native accessible-item caches. It never skips
invalid items or repeats an action to obtain a new snapshot.
Panel acknowledgements mean interaction delivery, not completed asynchronous analysis.
Recoverable callback causes reach the private workspace as bounded messages without
locals or source reads; previous panel failures remain inspectable after refresh.
Once a scientific session declares its native panel, its existing owner delivers
idle Qt callbacks through the same serialized `observe` request, at most once per
1.5 seconds between user requests. Browser visibility and unsent drafts cannot stop
worker progress. The parent continues servicing scoped regional reads and applying
the existing process deadline and cancellation. This does not paint, run an analysis
again, publish data or grant a new input. Recoverable background failures pause idle
delivery and are retained for the next explicit observation; fatal failures use the
ordinary owned-worker cleanup. Rendering still requires current presentation rights.

When a job can be done several valid ways (vision models, pointing strategies, outline
models, brain modes, dissect methods), add the new way as an option in `services/orchestrator/options.py`
next to the others, with its measured numbers; do not remove working options. Its label is its one
display name (no commas, where it runs apart; names.py), a label it replaces is kept in `names.OLD_LABELS` for
one release, and code it cannot run without declares `installed`. A group's choices that change while the app runs
(your models turned on, contributed entries proven or withdrawn) change only through `options.change_choices`, which
builds a new dict and swaps it into `GROUPS` under one writers' lock: worker threads register while the loop iterates
the groups (`catalogue`), so a published group is never edited in place. Read a group through `GROUPS[group]` at use,
not a name imported at load. The runner's `operations.OPERATIONS` and `operations.ADAPTERS` change the same way, for
the same reason (kinds.register on the loop and in a capability run's thread, mine.register in sync_mine's): each is
a `Registry` whose every write copies the dict and swaps it in under the writers' lock, so its readers iterate freely and
a name imported at load stays current. A change built on what an operation is now (its implementations) goes through
`operations.change_operation`, never a read here and a write there; read an operation as it is now through
`OPERATIONS[id]`, not its constant (`OUTLINE`, `LOOKS`...). Defaults
come from measurements. Interactions are logged with the options used
(`services/logs/interactions.jsonl`) so they can be A/B compared, and a turn also logs what it chose and
what really ran (`used`: its `use` choices and the implementations that ran for it) and every outline it
made (`outlined`; its look's alone `lookOutlined`); every action carries its
arguments, the real error and the models that worked on it (`models`, as Activity says them), hands and pointing log every decision (also when nothing was
done, and why), the voice logs how fast it starts speaking and how long hand-offs take, where a hand-off's
time goes (waiting for words, for hands, the brain), when the vision call starts, and the
server's word times against our clock (`make voice-report`), and opened detail cards are logged (do descriptions get read?).
What the voice held is logged too: every piece of silent context GPT-Live is sent is a `voice-note` record with its whole
text, its `source` (view, recap, hands, narrator, recovery, in-progress), the session id and, for a view note, why it went
(session start, view changed, chat notes) and how old the running look and its frame were (`voice/route.py look_facts`),
and `voice-session` keeps its instructions' hash and its recap whole. A studio command that did not work keeps its reason
(`error`, `code`, `next`: `commands.refusal`) in its `command` record and in its Activity line's details. Blind A/B/C votes
(`services/orchestrator/arena.py`, `services/logs/votes.jsonl`, `make arena-report`) are
the other half of choosing defaults; never reveal an arm's model before its vote.

What a model was shown is kept with its log line, so logs can be joined back to the pictures:
- **What is kept:** naming a click, each running look and each brain turn save a small copy
  of the picture in `services/logs/crops/<day>/` (git-ignored, kept like recordings). Their
  log records carry the frame id, object ids, confidences and that image's path.
- **Corrections:** a suggested correction keeps the object's crop. `make export-corrections`
  turns accepted and dismissed corrections into labelled pictures, appended by id to
  `logs/kept/corrections.jsonl` (kept for good beside the votes; a row is never rebuilt or removed, so a
  deleted asset never takes one away; the latest line for a kind, day and id is how it stands (readers skip a line that does not parse)), which is ground truth on our own specimens. It also exports the user's corrections of segmented
  cells (M3.4, `segmentation_rows`): one row per revision, with the view, the masks file (revision 0
  and every mask a correction added), the execution it corrects (with its producer, the view's source and region and the scale), the user's own words as said (`userWords`) apart from the model's paraphrase (`modelWhy`), and for each member
  what it was, what the revision made it and what it is now; an undone revision says which undid it.

Every option says which models, weights and services it uses (`Option.uses`). Each of those
has a licence tier in `services/orchestrator/licences.py`: open, terms or noncommercial
(ADR 0005).
- **Edition:** `research` is the default and permits the separately gated noncommercial components, including direct
  app, GPU and sim launches (the user's 2026-10-08 decision, ADR 0005). Explicit
  `MICROMIND_EDITION=commercial` offers only open and terms components; an invalid nonempty
  override also uses that gate.
- **Gated code:** code for a noncommercial component goes in `services/noncommercial/`,
  reached only through `licences.adapter()`.
- **Refusals:** an option the edition may not use is refused with the reason, and a tool that
  falls back says so.
- **Adding a component:** add it to `COMPONENTS` and run `make licences`.

## Saves: three homes

- Every save is one kind (`orchestrator/paths.py` `KINDS`, `LIFETIMES`; `docs/saves.md`); a test fails
  if a save has none.
  - **Datasets** are kept until the user deletes one.
  - **Performance logs** (interactions, the chat export, crops, saved logs, the servers' logs) are
    temporary. Votes, corrections and verdicts in `logs/kept/` are kept for good.
  - **Assets** (day journals, researchers' work, recordings, snapshots, evidence, taught, seen, bookmarks, fine-tuned
    models) are
    kept until the user deletes them. Evidence (`orchestrator/evidence.py`, `motiontracks.py`; `docs/saves.md`
    "Evidence") is what an operation a request made found: saved atomically, manifest last, filed in
    the day's journal and read back at start; never deleted automatically; past its disk budget new
    analyses are refused. Evidence and motion tracks remain `saving` until journal filing finishes;
    a filing error reports `failed` and retains their unsaved pixels, points and holds for retry.
    A queued newer save keeps ownership of the writer slot and determines the final status.
- **Moving them:** `make saves-move APPLY=1` (`orchestrator/saves.py`) hard-links the checkout's
  `services/` and `recordings/` into `~/MicroscopeMind/{datasets,logs,assets}`. It checks every file
  and only then writes `~/MicroscopeMind/.saves.json`, which switches `Paths.from_env` to
  `Paths.saves`.
  - The originals stay; it never overwrites.
  - `make start` then links in anything older code wrote in the old places since the move (`saves
    sync`).
  - Precedence: `MICROMIND_HOME` (tests, the sim) > `MICROMIND_SAVES` > the marker > `services/`.
- **Scripts never assume `services/`:** they ask `python -m orchestrator.paths logs|research|...`
  (studio.sh's logs and recordings, save-logs, the demo, clear-session). With neither `MICROMIND_HOME` nor
  `MICROMIND_SAVES` set, the CLI answers from the launch settings' `home` (`paths.cli_environ`: what `make start`
  exports), so scripts find the live app's logs; the app, the benches, the sim and a second stack read their own
  environment and keep their own homes.
- **Pruning:** `make logs-prune` lists the performance logs' dated folders older than `DAYS` (14) and
  removes them only with `APPLY=1`. It never touches kept judgments, assets or datasets.
- **The dataset trash:** the Datasets tab's Move to trash (`dataset-trash`, after a confirm) moves a
  dataset to `datasets/.trash/`, never one being collected.

Scientific array publication freezes the owning session's exact saved history at capture admission
(`scientific_provenance.py`). The accepted revision and conflict copy retain the original document
revision/digest, before/after array references and producer history revision/digest atomically.
Manual labels and contributed control callbacks persist admission and outcome through
`ScientificHistory`; ordinary viewing is not an edit. A failed history acknowledgement blocks
publication until repaired. Storage retry restores the edit receipt without executing it again.
This association is bounded snapshot provenance, not general callback replay or full H02 acceptance.
`scientific_provenance.resolve` (`GET /api/scientific-projects/{id}/provenance`) explains a revision only from
retained, digest-checked records: its base, input, output, the producing history's own cells and actions, the
revision that accepted it, the chunks whose bytes differ, and any conflict-copy lineage. A record it cannot read or
match is listed as a problem; no other history sharing the input is substituted. A Studio document save cannot add,
change or remove `hookeEdit`, `hookeOutput` or `hookeCopy`; only owned publication writes them. A publication keeps
`hookeEdit` only while the document still holds the values it describes, and a copy remaps it to the copied values.
Owned callers can restrict resolution to an exact held resource. Its edit keeps its validated base; an unchanged
original output is checked at its introducing revision. This permits following a corrected output's edit chain
without relabelling its historical output as current. Default whole-document diagnostics remain unchanged.
An unchanged input can retain both its older producing record and a later transaction-verified tool selection.
Method lineage follows that exact selection back to its captured revision before traversing an older producer or edit;
an edit accepted after selection remains the first edge. Selection never substitutes equal pixels, another component or a newer revision.
The record also keeps the base's runtimes, coordinate space and chunk change record, and says what was not recorded.
`scientific_exchange.py` (`GET /api/scientific-projects/{id}/bundle`, `POST /api/scientific-bundles`) exports a
revision with everything its provenance names as exact bytes, and reimports into another profile only after
verifying every byte: absent documents or longer histories are added, nothing is overwritten or executed.
Owner-aware v2 exports include the exact owner closure from genuine journal origins; a missing/legacy origin keeps
the backward-compatible v1 data export without claiming restored ownership. `scientific_import_transaction.py`
coordinates v2 document heads and owner journals under a bounded durable intent and shared visibility locks.
Pending transactions roll back on startup; a durable commit survives cleanup failure and lost responses. Failed
rollback retains its staging for recovery. Public reads/listing report retryable unavailability during publication,
including retention scans, rather than waiting in an inverted lock order or reporting an empty Project.
`POST /api/scientific-bundles` keeps its exact `target` and adds `owner` with `session` and `state`:
`restored`/`matched` require a verified `originDigest`. Public sessions-aware import refuses v1 before publication:
its unverified SID could otherwise expose work under an unrelated existing or later Project through Data. Read-only
legacy export and explicitly offline `reimport(..., sessions=None)` remain available; no owner is invented. Only
verified ownership permits the ordinary UI to offer Open. Import does not switch the active Project or restore unrelated history.
These filesystem/API guarantees do not establish an actual fresh-profile browser journey or platform qualification.

`method_evidence.py` retains two or three declared methods as a Project document, separate from the existing
`method_comparison` aggregate API. It admits exact native source/mask revisions through `admission.native_source`,
requires matching ordered axes, shape and calibration, and follows recorded edit/output associations back to the
frozen input. Histories, runtime generations and corrections stay at their accepted references. Manual methods
need no invented model declaration. Missing producing context is explicitly incomplete; a known different input
or damaged provenance refuses. Failed/cancelled arms retain outcomes without an invented mask or zero area.
Metadata is bounded before atomic publication; choosing a completed arm records one immutable rationale by CAS.
Ordinary document saves cannot rewrite `methodEvidence`; normal provenance bundles carry its dependencies on reopen.
New unaligned output coordinate spaces are refused; no alignment is inferred from equal pixels or shape.
The retention service remains independent of execution. `method_workflow` declares `compare-retained-methods-v1`
for Studio and Registry: metadata-only Review, Retain, and Choose share explicit accepted revisions, request identity,
caller/ticket/session checks, cancellation and memory admission. Atomic Project creation accepts a final `check`,
just as revision Save does. Retry preserves the accepted destination and original attribution; a different selection
cannot reuse the request. Choice uses CAS and only an immediately succeeding identical choice can be recovered.

The native summary reads spatial integer masks in bounded row fragments, at most 64 MiB per arm and three arms,
through the existing native source/chunk bounds. It requires explicit matching coordinate space/axes/calibration;
equal shape alone never proves alignment. Nonzero labels are foreground; negative labels require conversion.
`method_comparison.from_counts` supplies the same count/area/difference arithmetic as the existing aggregate API.
Missing physical calibration keeps counts with its reason; failed/cancelled arms have unavailable quantities,
and failed A never changes the baseline. Histories listed alongside summaries are retained producing records,
not an inferred count of corrections or model-inference timing. Agreement never ranks scientific accuracy.

Protected `methodSummary` and `methodAdmission` accompany `methodEvidence`; ordinary Save cannot forge them.
The evidence meaning describes the capture step; comparison calculations are separate. Reopening preserves the
exact historical v1 meaning string when present, while still revalidating every other evidence field.
Reopen revalidates evidence and recomputes the bounded counts against exact native masks before displaying the summary.
The current choice comes from that revision's evidence. The normal provenance bundle carries alternatives,
producing histories and native data. Missing histories remain explicitly unverified; a real ordinary UI and independent
scientific/runtime journey still require their own acceptance. No producer or model is rerun by these operations.

Saved-array measurement discovery and execution share `scientific_resource_actions.measurement_plan`.
The plan reads metadata only and supplies eligibility/refusal, label-grouping method and the exact output columns.
Supported axis-aligned calibration uses `calibrated_area` for physical area/volume; unsupported or numerically
unrepresentable scales/products retain explicit pixel/voxel counts and a calibration limitation. Centroids remain
array indices. Actual value checks (nonnegative labels, the 10,000-object bound and positive finite calibrated
object sizes) still run during measurement. An object-size overflow refuses before publication with calibration/
region recovery guidance. The accepted revision/digest, CAS publication,
retry identity and retained source are unchanged; discovery neither reads label chunks nor reruns segmentation.

`scientific_measurement` is the declared built-in `scientific-object-measurements-v2` operation.
Studio's saved-data controls and Registry's `measure_saved_objects` share its descriptor, metadata readiness,
exact accepted input, memory admission, cancellation and atomic typed-table publication. Native metadata resolves
through `admission.native_source`; no latest-revision fallback exists. The original producer records the server
invocation and caller (and request ticket when present); retries report their new invocation separately. Only the
immediately succeeding matching server transaction can be reused. Concurrent publication conflicts preserve the
winner; intervening work requires explicit reselection. Project/session/ticket validity is checked through the
owned thread and immediately before publication. Stop waits for owned work and never removes accepted content.
Model-oriented `operations.Runner` stores model Results from captures/implementation choices; this built-in instead
uses the existing Project CAS and typed families, without a model environment or alternative result store.

`population_native` declares the built-in `retain-population-labels-v1` operation. Studio's population
correction controls and Registry's `retain_population_labels` share metadata readiness, captured session/id/revision,
request identity, memory/cancellation/ticket admission and native Project publication. It accepts a saved original
single-plane uint8 grayscale image in pixel units and exclusive, unscaled masks with unique positive uint16 instance
IDs. Unsupported physical calibration, overlap and missing IDs refuse before conversion; nothing silently relabels.
The original file, image array and corrected labels share one accepted coordinate grid. Producer, source checksums,
member mapping, correction history and original caller receipt are frozen in protected `populationSource` state.
Native measurement uses mean array indices; the legacy exchange explicitly uses index + 0.5 pixel centroids.

New acceptance uses `populationSource` format 2: a protected image→Labels association repeats the exact
source population identity/revision/execution/files and producer beside both native component references.
`scientific_provenance.resolve` checks its introducing revision, ownership, original-file manifest digest,
component roles and coordinate identity before exposing that association. This metadata check does not freshly
read every pixel; native consumers retain their usual per-read digest checks. Format 1 stays readable but is
never backfilled into a producing association. Explicit malformed associations refuse rather than becoming
unknown provenance. The captured producer is attribution, not a verified native action history or inference timing.
Attribution larger than the callback-context limit is exposed as an exact accepted-document/path reference
with canonical JSON byte count and SHA-256; the complete producer remains in that immutable document and
its exported closure. Small attribution stays inline for compatibility. Callback admission limits do not change.

MT6 can use the exact original image at the introducing revision as input, original Labels as A and an accepted
manual correction as B. An ordinary explicit tool selection may advance the same document before correction;
unchanged protected component references allow that edit lineage to reach the original association. A later
chosen input revision is not silently rebased, and unrelated components, copies or other algorithm outputs gain
no relationship from equal shape/pixels. Original/corrected arms retain the normalized association and any actual
manual history. Missing original actions/timing remain limitations; this does not claim complete MT6 evidence.

Pixel I/O and private ArrayDraft staging occur off the loop and outside Evidence's lock. `exact_revision` serializes
correction mutation with the final source check and atomic FileIntake publication. A changed/cancelled request leaves
no visible empty Project. Concurrent or lost-ack retries recover only their matching first accepted revision; the
original admission is unchanged. This data-only `population-labels` document opens saved arrays and the existing
measurement/export service without launching a Python integration. It reuses FileIntake, ProjectStore, typed arrays,
execution receipts and the Registry rather than adapting model Runner's capture/Results store to native publication.

For a catalog Fiji producer, acceptance leases its actual installed numbered generation, checks the recorded receipt
hash and retains a `fiji` runtime reference. The existing Space/removal boundary honors that reference; Space currently
groups Fiji generations, so the hold conservatively blocks that group. This is not fresh verification of every runtime
file. Non-Fiji producer identity without a runtime descriptor is recorded without claiming its model environment is retained;
a declared runtime without a supported retention owner refuses. Original population
history stays in its evidence store; the native Project keeps its frozen attribution and accepted image/label values.

A local Jupyter connection (`scientific_jupyter.py`, `jupyter_kernel.py`; docs/jupyter.md) adds no way to run code:
its kernel forwards cells, completion, help and interrupts with a per-session token to `/api/jupyter/*`, which become
the same `python-resources`, `python-assist` and `python-interrupt` requests the console sends. The token lives only in
a mode-600 file in Hooke's home; requests from a browser are refused; grants end with the session, revocation, a
restart or 12 hours.

Scientific Python may run against its exact authorized saved resource without a rendered frame
(`python-resources`). Connection, Project, placement and revision checks still apply; pointer edits
still require rendered evidence. Snapshot storage checks the worker volume and existing byte/file/
entry limits independently of retained storage. Before transport, unavailable temporary storage
falls back to the original bounded RAM snapshot; partial exports are never recaptured or replayed.

GPU shutdown queues its stop message through the writer; it never waits for a full pipe's
send lock on the stopping thread. Queued unsent work is withdrawn and bounded process
termination must be reaped. Simulation teardown is part of the gate, not a warning.

Scientific array publication uses ResourceEdits with the exact accepted input/revision,
shared chunk writer, CAS, retry identity and conflict-copy path. It issues no rendered
receipt; provenance records accepted-resource authority with null frame/view. ViewEdits
still requires a real rendered frame for pointer gestures. Studio save-resource binds the
originating document revision and declared layer/input; the worker rechecks its actual
editable binding, scientific geometry and stable snapshot before exporting. Save-status
reads the owned attempt without rendering. Only a typed refusal before any chunk arrives
preserves the worker and releases an uncaptured attempt for correction; partial exports
and malformed replies retain fatal cleanup. An authoritative null status clears a lost
admission in the UI; a missing outcome never does.

Scientific layer inventory is a bounded, resource-authorized read using the frame's
same layer validator. It supplies no pixels, frame receipt or pointer authority.
Start without preview loads inventory and runs resource Python/save without a first
render. Remembered presentation is deferred until the first explicit preview; a
compatibility refusal before applying any view changes returns a visible notice and
keeps private work. Other renderer/restore failures retain existing owned cleanup.

Multi-layer native saves use `scientific_group_save.LayersSave` and the same
`scientific_publication.ArrayPublication` as single edits. The worker freezes all
selected declared arrays before the first transport callback; the parent accepts
only the ordered granted chunks. One Project CAS publishes every replacement and
its provenance. Manifest/acknowledgement failures retain captured edits for retry;
conflicts can copy the complete set without rerunning Python. Single-save record
formats remain readable. Multi-change provenance and copy lineage are versioned;
new/unbound layers are not implicitly included or claimed saved.

New native layer publication uses `scientific_outputs.OutputSave`: the parent
owns a separate first revision, write grant, frozen capture and idempotent save.
`env_worker.scientific_output_wire` validates semantic declarations, native metadata
and agreement between retained layer geometry and scientific calibration. Reopen
uses accepted values through `env_worker.scientific_outputs.restore`, never the
producing callback. Completed output release keeps the private session; staging
cleanup failures stay owned for retry. A single-resolution grey `LazyNative` backed by an exact
`NativeArray` input can stream its immutable accepted pixels into this same output grant, one region at a time.
It does not freeze a whole lazy array in RAM/disk or rerun its creating capability. Layer identity/geometry and
single-threaded capture are checked before and throughout transfer; a revoked input or changed layer aborts
private staging. Mutable arrays retain their freeze-before-transfer path and 64 MiB new-output bound. Regional
grey-image metadata permits the existing 1 GiB aggregate new-publication ceiling; per-read, rolling navigation,
60-second request and disk limits still apply. This is not proof that a 1 GiB transfer fits those limits. Arbitrary
lazy decoder objects, multiscale publication, lazy Labels and larger publication remain unsupported here.
These bounded Image/Labels paths do not establish structured-layer or full large-data completion.

Scientific Python interruption targets an exact running cell through its owning
review, worker and opaque execution token. `scientific_interrupt.Control` handles
the owned pipe/signal on the worker's GUI thread, deferring interruption until a
native resource exchange has consumed its reply. A readiness receipt prevents
signalling before that handler is active; late tokens cannot interrupt another
cell. Interrupt preserves variables and partial private mutations. Stop retains
its separate owned-process termination and cleanup semantics. UI acknowledgements
cannot replace a completed cell result or the state of a newer kernel.

Scientific scripts have a separate 256 KiB UTF-8 source allowance. The Studio
`/script` route retains the same origin, Project, resource/frame, cell replay and
history owner as `/request`; only its bounded source envelope is larger. Worker
source travels in a length- and digest-checked body admitted before allocation.
Control headers remain 128 KiB, transcript fields 16 KiB, and cumulative history
keeps its existing bound. Source size never grants resource or actor authority.

Compound scientific outputs use the same owned OutputSave boundary. Static Surface
components and ragged Shapes vertices/offsets retain independent native types in one
atomic document. Shapes also retain explicit geometry types, typed features and
native color state; parent admission validates finite vertices, complete boundaries,
geometry counts and bounded appearance before publication. Failed transfers reap
only the owned worker and remove private staging; failed publication retries its
captured values. New-output reopen reads accepted components without executing the
creator. Existing 64 MiB aggregate and typed-state bounds remain; regional geometry
remains separate work. Empty structured layers preserve their native shape and
typed defaults; empty Shapes use a zero-row vertex matrix and offset `[0]`, without
inventing an annotation. `save-outputs` uses the same OutputSave owner
for an explicitly selected mixed scene. Every selected layer freezes before any
transport callback; one private directory rename publishes all components and
producing history. Per-layer meanings/calibrations are retained without inferring
alignment. Aggregate bytes, input count and exact staging/reopening message sizes
are checked before admission; invalid selections keep private work available.
Lost acknowledgements retry the same captured scene. Group selection does not
include edits to already bound inputs; those keep their existing save path.

Tables, embeddings and artifacts that Python or a plugin makes in a review are drafts until saved (H01 a3).
Python declares them through `families` in the console (`table`, `features` for a layer's napari features,
`embedding`, `artifact`; `env_worker/scientific_family_drafts.py`). Declaring copies the values once, read-only,
with each part's size and SHA-256. `family-drafts` lists them; `save-families` (`scientific_family_saves.py`) receives
every part in order against its declared size and digest. It rebuilds each declaration from the listing and resolves an
object space, region or coordinates only to an input this session's layers came from (`scientific_rebase.Track`). The
worker refuses before any transfer when a layer bound to such an input has unsaved changes. Everything is then published
by `scientific_families.publish` onto the newest revision in one CAS, which checks every value, key and relation again.
A newer revision accepted meanwhile saves nothing, and saving again joins it. The listing is untrusted; the parent only
lets it shape what it asks for.

Surface presentation uses the same current-frame change and remembered-view
boundary as dimensions and image controls. Shading, contrast/gamma, wireframe and
face/vertex normals run native setters in the owning confined worker. Nested line
model events invalidate its rendered frame. Schema-9 view preferences preserve
these controls independently of mesh components; older schemas retain current
native defaults. Surface controls do not edit geometry, measured values or features.

Points presentation uses the same frame-bound owner and native setters for
shading, projection_mode, antialiasing and canvas_size_limits. Validation precedes
mutation; deprecated out_of_slice_display cannot replace the four-mode contract.
Schema-10 preferences retain these display values independently of point geometry,
features and individual marker styles. Earlier schemas remain readable and preserve
the current point settings when none were recorded. Native continuous limits may
exceed 2^53; only finite, nonnegative, ordered limits are accepted.

Points/Shapes marker colors preserve native constant-feature contrast limits `(v, v)` in frames and edits; reversed
or nonfinite limits still refuse. Text formats validate every feature row before mutation in batches of 64, capped
at 10,000 rows, 1,000 characters per label and 1,048,576 total characters. Format width/precision must be fixed and at most
1,000. Each batch is bounded before napari formats it: only direct string, numeric, boolean or empty scalar
features support substitution; unknown objects and attribute/index traversal refuse explicitly. Larger or
incompatible formats refuse explicitly, keeping previous text. These controls use shared frame
ownership and native setters. Marker styles, shape styles, text content and shape z-order are not yet retained by
Remember this view; Studio names that limit. Full native/browser and persistence qualification remains separate.

Selected scientific source files use `scientific_files`: immutable bounded chunks,
content hashes and names (never source paths), accepted only through a Project
revision. These bytes survive software replacement independently of the reader.
`source_files.SourceFileInputs` grants only the named accepted file ranges and
revokes delivery before Stop waits on an in-flight read. The scientific host exposes
seekable read-only handles as `context.files` and console `files`; it gives no new
filesystem or network permissions. A retained file is not proof that any reader or
runtime supports it. Format-3 readers may explicitly declare `access="regional"` and a source size up to
64 GiB (the retained-file ceiling). Only the declared input receives this grant. `SourceFile.read(..., view=True)`
and `SourceDirectory.open(..., view=True)` use the native navigation budget: 512 MiB/8192 reads per rolling
10 seconds, enforced independently in parent and worker. Ordinary reads retain their lifetime 64 MiB/4096-read
budget, including on regional inputs; read ranges remain at most 1 MiB. Stop revokes both modes. No reader is
silently upgraded, and a declaration does not provide a decoder, lazy writer or large-data qualification.
Source-file intake/UI/reader declaration gates remain separate.

Opening a scientific review is bounded at 60 s per attempt (`scientific_reviews.OPEN_SECONDS`). The worker names
each startup step as it begins (`env_worker.scientific_host.STARTUP_STEPS`: the integration's code, the viewer, saved
layers, the panel), in order and only while opening. The parent refuses any other report and passes the steps to the
owner through `scientific_host.reporting`. While a review opens, `GET /api/scientific-reviews` gives its `opening`
(step, words, seconds so far, attempt), and the studio shows that in place of a bare "Opening…". An attempt that
reaches the bound is tried once more on its own, keeping the failed attempt's step and time. Two such attempts are
reported in plain words, with saved work unchanged. Any other failure is reported at once and never retried.

An integration may refuse an unsupported input during its factory's opening with
`context.refuse_input(reason, next_step)`. Both fields are explicit integration-authored
user guidance: nonempty printable single lines, at most 1000 characters each, without
paths, credentials or exception text. The worker emits the exact
`SCIENTIFIC_INPUT_REFUSED` response; the parent admits it only during opening with no
payload and revalidates both fields. HTTP returns 422 with the reason and recovery.
The failed attempt closes its worker and grants without publishing a result or changing
accepted Project data. Malformed refusal messages retain the contained generic failure, and
arbitrary `ValueError` text never becomes user guidance. A tool that fails while opening for
any other reason is said in its own words, apart from the guidance (`scientific_host.ToolNotOpened`,
HTTP 422 `SCIENTIFIC_TOOL_FAILED`: "<tool> did not open: “…”"): only its
first line, with every path, key and token removed and no control character, at most 300
characters (`shown_words`, `report.py`'s rule). The worker's stderr goes with its temporary
folder, so its tail is kept first, keys removed, in `logs/tool-failures/<day>/` (pruned with
the dated logs; `report.local_details` reads it apart). After opening, worker errors keep the
contained generic failure.

Private source readers declare their contract on the existing scientific-host bundle
member. Format 3 (`bundle_contract._reader_v3`) is napari-shaped: the kind of source
(file, folder or ordered stack), lowercase name patterns, optional required folder
entries and entry patterns, `maximum_bytes`, and `keeps`, the native facts it keeps
exact (values, dtype, byte order, axes, calibration). `scientific_readers` discovers
declarations without executing code and chooses by declared capability: one reader
that fits and keeps every fact is chosen, several are the researcher's choice, and
none is a refusal that names each reader's reason and the next step. Each row says
whether it is blocked as incompatible (no setup will help), by this Mac's devices, or
by setup (trust or preparation), so missing setup is never shown as incompatibility. A reader that
does not keep every fact, or a format-1/2 declaration (which states none), is refused
for scientific sources. Binding pins an exact original source revision to a separately
prepared generation and records `keeps`. `scientific_reader_api` requires Studio
provenance for that selection; Start still uses the existing confined scientific-review
owner. Preparation and pattern matching are never operation proof or green readiness.
The original source revision remains available independently of the reader workspace.
New reader workspaces use the source and prepared member's recorded names in their title and context;
capability, generation and original digest remain in sourceReader. Generic document saves cannot change
that reader provenance. Retry identity is unchanged: an exact legacy first revision is recovered without
renaming or migrating it; arbitrary custom titles remain intact. These names do not claim scientific qualification.

A web address becomes a source through `scientific_url_intake` (`POST
/api/scientific-urls`): https only, an ASCII host, no credentials, every resolved
and connected address public (NAT64, 6to4 and IPv4-mapped addresses are checked as the
IPv4 they reach) on each of at most three redirects, a bounded name lookup, no cookies, environment proxies
or `.netrc` (`trust_env=False`), identity encoding only, 64 MiB before and during the
transfer, a five-minute deadline and the caller's stop between chunks. The bytes are
spooled, checked against an optional expected SHA-256, and only then retained by
`FileIntake`. `state.sourceRemote` records the address, where the bytes came from,
when, the server's media type (untrusted) and the exact size and digest; a Studio save
cannot add or change it. A retry of the same request returns its receipt without a
second download. Web content is data: nothing is parsed or run at intake.

Sample data (napari's sample_data) is declared on a format-3 file reader as pinned
https files with their SHA-256, size and licence, never a command. `scientific_samples`
lists declarations without running anything (`GET /api/scientific-samples`) and opens
one, from a catalog whose code is allowed, as an ordinary download that must be exactly
the declared size and digest (`POST`); the receipt names the declaring reader and sample. A sample qualifies nothing.

`scientific_tools` records explicit next-tool selection as a CAS revision over the
same accepted native resources. Selection admits a prepared scientific-host
member without executing it, copies no array bytes, and preserves earlier
revisions and runtimes. Exact-runtime selection preserves its codec; another
runtime receives retained layers without the earlier tool's codec. Version-1
`initializeTool` runs only after explicit Start and attaches the new factory to
restored layers, checking their scientific identity before and after attachment.
Ordinary retained-output reopen still skips its producing factory. Unknown
initialization versions refuse. This is bounded scene continuation, not general
multi-document composition or a qualified integration.
Retained Population native inputs (including their accepted measurement revision)
may choose the same declared tool before having a viewer runtime. Shared native
grant resolution validates exact named resources; explicit choice creates a scene
revision with that runtime and preserves original pixels, calibration, correction
provenance and tables. Reopening the original data still cannot start a worker.
No producing tool is rerun and no runtime is inferred from provenance.

Saved-output provenance may also report a display-only `savedTool` name from the exact
originating revision's verified selection transaction, or, for an output saved from a reader
workspace, from that workspace's guarded `sourceReader` while it names the workspace's own
runtime. Its runtime must match the retained output view and both runtime inventories. Missing or mismatched attribution supplies no name;
no current catalog declaration or execution is substituted. This adds no lineage acceptance.

`scientificToolChoice` is protected from ordinary document writes. The shared provenance
resolver validates its introducing CAS transaction, exact previous owned revision,
capability/generation and unchanged admitted inputs/runtime. It describes input selection,
never a producing action. MT6 may connect an explicitly bound `hookeOutput.coordinatesFrom`
through that verified selection to the exact frozen original input; it records the selection
separately from producing histories. Unknown/missing, altered or differently owned links
refuse without rebasing the input. Copies/forks and recursive algorithm chains remain outside
this bounded edge. Export/reimport keeps its exact dependencies; missing action history and
inference timing remain missing, even when native alignment and lineage are known.
Explicit output-coordinate associations also recheck their accepted input value fingerprint,
including after import. This uses the shared at-most-64-MiB scalar fingerprint with bounded
native reads and one cached digest per exact source in an output record; it neither starts
a tool nor replays producing code. Other metadata-only method/Population paths remain so.


Slab margins and Image projection run native Dims/Image setters in the existing
confined scientific host. Margins are finite nonnegative world-coordinate distances
for each axis; projection supports only the pinned Image modes. Validation occurs
before mutation, and native events invalidate prior rendered frames. Schema-11
view preferences preserve this presentation independently of scientific arrays and
calibration. Older preferences keep current settings; restoring unsupported new
fields refuses explicitly rather than silently dropping them.

Scientific axis-order actions expose only native roll/transpose through the
current-frame change boundary. Native rollable flags and singleton dimensions
remain authoritative; the browser does not duplicate their algorithm. Axis order
is presentation and persists through the existing remembered-view contract.

Dimension navigation (H04) adds three operations at the same boundary, validated in
`env_worker/scientific_slices.py` before anything native runs: `viewer-action` runs
napari's own action function (`increment_dims_left/right`, `focus_axes_up/down`,
`roll_axes`, `transpose_axes`, `toggle_ndisplay` from `_viewer_key_bindings`),
`dims-point` is `Dims.set_point` (a world coordinate; napari's slider then keeps a
changed slice on its step), and `dims-focus` sets `Dims.last_used`, the keyboard axis.
Frames carry `lastUsed`, `rollable` and `units` beside the dimensions. The Studio binds
napari's default shortcuts on the focused preview and View panel (Left/Right and
Option/Alt+Up/Down; Command/Control+E/T/Y where the browser leaves them to the page),
never inside a text field or slider, and in 3D leaves the arrow keys to the camera.
Playback defaults are napari's (10 per second, loop), accepts 0.1–10 per second (each step relays a drawn frame; higher rates wait for a live-delay measurement), starts at once in loop and back and forth as napari's AnimationThread does, waits
for every drawn slice and says how many were shown and the rate reached.

Scientific grid settings are validated by `env_worker/scientific_grid.py` before
native events run. Detached native grouping computes the requested shape and
checks it against the current viewport before allocation. Schema 12 retains
grid intent alongside the camera; legacy preferences keep current grid defaults.
Grid placement never changes native layer arrays or scientific transforms.

Scientific native inspection uses the existing frame/draw/window/resource admission. `inspect` retains the current frame and does not create completion or edit history. The bounded worker reply carries calibrated coordinates and exact value strings. Loading and empty native slices cannot masquerade as measured zero values; parent and browser validate the reply independently.

Shared native picking converts canvas-world values from the layer-list's rendering units into the target layer's
declared units before its inverse affine. Mixed micrometer/nanometer axes therefore pick the displayed native
pixel. The renderer's incompatible-unit fallback, axis order, orientation and current-frame/grid guards remain;
this conversion does not rewrite calibration or change native annotation mouse-callback semantics.

Readiness unlock counters resolve only current option choices using the ordinary
readiness resolver. They skip menu metadata, not admission rules. Fixed-role
answers may be reused inside one synchronous role evaluation only; changes in
keys, installations, proofs and worker health must be reflected on the next call.

Movie export uses `scientific_movie`: an explicit saved revision opens a separate
confined host, never borrows the live session, and executes its version-1 sequence
callbacks on the GUI thread. Frames stream to Hooke's packaged raw-ARGB MOV encoder (legacy qtrle remains supported);
a bounded decode must exactly reproduce RGBA bytes/count before atomic retained
file publication. The derived display movie records input revision, runtime,
sequence, settings and encoder hash. Cancellation/deadlines cover verification,
worker/encoder cleanup and publication. Retry keys recover accepted results without
rerunning the integration. Current bounded MOV mode is not large-data, platform or
complete animation qualification. `scientific_movie_api` owns Studio admission and
parent memory; packages cannot choose executables, filesystem destinations or
publication. No saved movie or tool executes merely because its window reopens.

Scientific calibration identity uses `env_worker.scientific_calibration` at
OutputSave admission and retained-resource reopening. Only canonical aliases in
bounded unit products/ratios with simple integer exponents are compared through
Pint's exact serialized names/exponents. Numeric values, transforms, axes and
coordinate-space identities are never converted. The original accepted calibration
is retained verbatim; unknown equal unit strings preserve exact-match behavior,
while unsupported unequal expressions refuse. The shared module is included in
the confined worker bootstrap; parent Pint is pinned in the first-launch lock.

Selected folders use `scientific_directory_intake` over the same immutable file
chunks and atomic publication as individual sources. Versioned indexes partition
bytes into relative keys; `source_directory` validates paths/ranges and supplies
read-only mappings/independent streams under shared source grants. Registry reader
format 3 declares folder input explicitly (format 2 still parses but is refused for
scientific sources, like format 1, because it states no kept facts). This 64-MiB
increment is not lazy remote or larger-than-memory intake. Open files as a stack
(`POST /api/scientific-stacks`, `StackTransfer`) keeps flat file names in the order
the researcher gave and `state.sourceStack` (the new axis they named and the count):
two or more files of one type, refused before staging otherwise. Stack readers declare
kind `stack`, and their patterns match the stacked files' names; binding records the stack beside `keeps`, and a save cannot change it.

Scientific-host preparation admits pinned model artifacts through `prepared_artifacts`.
Use the immutable prepared owner manifest, never the current catalog's declaration.
`MICROMIND_ENTRY_FILES` names its published revision; confinement grants only declared
regular files after bounded streaming content verification. No developer-cache or
network fallback is available. Live `files_from` aliases are refused: scientific
packages declare their pins directly. The artifact lease lasts until the worker is
reaped, including failed-start cleanup. This does not increase worker limits or
qualify an actual model, integration, or platform.

Scientific members may declare `resources={format=1,memory_mb=N}` with integer
1024–8192MiB. The default remains1024MiB. Admission reserves the exact retained
member's allowance plus the existing frame reserve before launch; the same
allowance reaches the process supervisor. Work storage, child count, deadlines
and confinement remain fixed. Setup displays this declaration without treating
it as observed footprint or readiness. Unknown resource versions/fields refuse;
changing a declaration creates a distinct runtime generation. Artifact ownership
also holds the existing download lock in shared mode, excluding other-process
writers; replacement publication observes the generation withdrawal boundary.

## Stop and repeated runs (L7b)

The Actions panel's Stop answers with `user_stop.Stop.report`: what stopped, what was kept, what was discarded, and
work that carries on because another request still needs it (the runner records each on the Stop's holder; a begun GPU
call is said as finished by its worker with its result dropped). One run of the same action, settings and input runs
at a time in a Project (`user_stop.once`, in `commands._action`): another window's Run is refused before it starts.
`jobs.run` holds the same rule for jobs (`jobs.Duplicate`).

## Scientific resource admission recovery

Population revision transactions validate the captured object in its owning
Project's evidence registry, including retained work outside the open Project.
The owner lock still covers identity, revision and mutation together; replaced
or stale objects refuse. Native Labels intake additionally requires the current
Project and its captured session generation throughout publication.

`jobs.JobMemory` raises a typed `MemoryRefused` (still a `jobs.Failed`) before
acquiring a denied reservation. Scientific-review HTTP exposes `SCIENTIFIC_MEMORY`
with recovery through the existing owned-session inventory. This does not change
the memory margin or reservation lifetime, stop unrelated sessions, or automatically
retry an uncertain action. Stop still discards private changes; accepted Data stays.


Visible, idle scientific windows observe queued native-plugin callbacks through the
same serialized GUI-thread owner. The observation returns only current image identity;
unchanged images are not repainted. Active gestures, unsent controls, Python, saves,
playback, hidden windows and preview-disabled sessions suspend automatic observation.
Changed images refresh layer/save inventory without rerunning analysis or accepting
results. Observation failure stops the loop and exposes Resume live results; late
results cannot repaint a different Project, Tab or placement.

Scientific sessions reserve the existing 128 MiB parent save workspace before
starting their worker, in addition to the unchanged worker and frame allowances.
New outputs, accepted layer edits and grouped saves explicitly retain that owning
workspace for serial capture/publication/copy retry. A save does not reserve the
same peak again after model loading. Current free memory must still cover 128 MiB
plus the unchanged live-view margin. Released workspaces require fresh admission;
process cleanup failures retain their reservations. No worker limit is increased.

The ordinary Project review retains its separate 128 MiB input/manifest workspace;
the save workspace is additional parent headroom, not a second worker allowance.

Scientific sessions opened with a browser-private recovery capability retain their
bounded worker on connection loss. The server stores only its digest. Open from a
newly admitted exact Project/revision/digest/Tab/placement may reclaim only a
disconnected owner with that capability; a connected owner cannot be stolen.
Old image/panel receipts are revoked. Admitted work finishes once; lost replies
retain their result/history for inspection, never replay. Legacy clients without
recovery still clean up on disconnect. Stop and application shutdown reap either
kind; private worker state is not a durable save across backend restart. The
four-session cap and runtime/resource reservations include detached owners.

Retained scientific comparisons (`scientific_comparisons.py`) capture one exact
source revision and fork separate working scenes through scientific copies and
tool admission. Preparation never executes the integration. Accepted outputs must
name the arm's exact source; configuration-incomplete outputs remain explicitly
identified. Retaining a result never changes defaults. The workspace opens updated
comparison revisions explicitly; no analysis starts on reopen.
Format v2 keeps each arm's attempts, append-only: a kept result's model, the
integration's declared device and recipe settings, its outputs and the times Hooke
observed (Save tool and the producing history's panel actions; the last action may
follow the Run, so its gap to Save is not run time; compute time stays null because
the tool reports none). Settings are the panel's at Save tool, labelled as such. A preparation failure or stop is recorded as
that arm's failed or cancelled attempt in a new revision; the researcher may record
a failure or cancellation in their own words. Successful arms are never replaced.
One integration may fill several arms. `choose` records a preferred kept result in
the comparison only. v1 documents read unchanged.


Retained recipes (`scientific_recipes.py`, `scientific_recipe_api.py`) use the
same exact-source fork and tool admission as comparisons. Definitions pin saved
contributed recipe settings and runtime generations. Runs retain explicit review
checkpoints and previous accepted outputs; only an output naming the current
working revision can advance. Prepare configures a new scene without executing
analysis. Reopen never runs or repeats a step. Current beta replay uses ordinary
interactive controls, not unattended scheduling. Run format v2 names the run's own
exact input (it may differ from the recipe's original) and keeps failed attempts: `fail`
records the researcher's judgment with the step's working copy, and the next prepare makes
a separate copy. A v1 run reads as v2 and is written as v2 only on its next save.

## Headless generation constraints

`capability_runs.run_on_resource(expected_generation=…)` carries an optional exact entry constraint through the same
runner as local callers. Python environment and Fiji adapters compare the selected installed entry using
`capability_identity.generation_of`, also used by hosted discovery, before executing it. They retain the observed
`producer.entryGeneration`; the headless result refuses a missing or different identity before keeping it.
The optional `expected_environment` also binds the selected prepared Python or Fiji generation and its receipt hash
(including the Python lock). Adapters check it before input execution and retain `producer.environment`; the shared
headless caller validates that evidence before keeping output. These constraints use the same runner-support
predicate as hosted qualification/discovery. Unconstrained local calls do no additional hashing. Other adapters
fail closed for constrained calls until they implement these checks. Hosted worker/provider identity remains the
hosted adapter's responsibility.


Retained RGB inputs in typed groups preflight their fixed PNG headers and recorded scientific metadata through
`retained_inputs.layout`. Every port and the aggregate size must pass before pixels are read. The shared JobMemory
reservation covers retained decoding, native array reads and admission hashing, including all-RGB groups; the runner
then takes its separate execution hold. Full original digests, revisions and preflight dimensions are checked again
before decoding. Header inspection alone never establishes a usable scientific input.

Typed file inputs use `typed_files.load` for both headless admission and bundle installation proof. It reads bounded
NPY 1/2 headers without payload prefetch, validates explicit ordered axes and stored layout, and checks the entire
group's decoded size before allocating arrays. One transient JobMemory reservation covers encoded buffers, decoded
arrays and admission hashing. Open/path identities and authorized digests are checked around loading; every refusal
releases handles and reservations. The runner retains its separate execution reservation. Headless NPY callers
supply `{id, path, sha256, descriptor}` for each port; NPY shape never supplies missing scientific axes. Retained
input records include the validated descriptor so differing scientific metadata cannot share the same capture key.
Typed-file refusals carry an explicit next step: choose a supported capability,
correct the input contract, or select and authorize a changed input again. Memory
refusal retains its wait-for-other-work guidance; no refused input is dispatched.

Manual Labels transactions retain independent copies of native Undo/Redo atoms,
including their mutable index/value arrays. Callback failure restores and verifies
both pixels and complete history; a failed verification invalidates editing and
requires stopping the private session and reopening accepted Data. This does not
accept or publish edits. Existing Points/Shapes transactions likewise refuse
history whose current endpoint changed, and move a history entry only after
complete native restoration. Their rollback also verifies the editable snapshot.

Native regional input grants preserve optional source chunk boundaries. Retained
editable layers align eager reads to those boundaries, grouping at most 64 small
chunks or subdividing large chunks to the unchanged 1 MiB wire limit. Iteration
does not pool logical-dimension ranges. This avoids small requests decoding
many stored planes; the 8 MiB retained-source decoder bound, per-invocation/view
budgets and 512 MiB editable allowance remain unchanged. Sources without chunk
metadata retain balanced regional reads. This is not lazy Labels editing or a
large-data performance qualification.

`reader_proof.py` owns the parent-issued directory-reader data check. It reuses
prepared admission, immutable DirectoryIntake, SourceFileInputs and scientific_host
cleanup/resource limits. `ReviewWorker.reader_observation()` is private to this
producer; the browser request allowlist does not expose it. The confined
`scientific_reader_probe` preflights all layers/levels before reading, uses bounded
exact hashes and the existing Python quiescence guard, and refuses changed layer
ownership/geometry/type. Image-only observations retain their version-1 wire;
version-2 observations explicitly type every layer when any Labels are present.
Single-level Labels require integer nonnegative IDs under the same aggregate and
regional-read bounds; the parent compares semantic type as well as values and calibration.
It does not render, save outputs or claim scientific/workspace
qualification. Reference/actual artifacts use the existing bounded bundle-review
store; the issuer seal key is outside all confined read paths. Rechecking requires
fresh exact preparation/source/input identity. A caller's success JSON is not proof.

Retained population Masks overlays accept revision 0 from the immutable original membership and
measurement snapshot, or the exact current head. Original member geometry remains retained when
corrections delete/merge members; this read never changes member status, the live layer or accepted
revision. The overlay captures Project generation, population object and current head under the
existing evidence lock, verifies retained files off-loop, then rechecks that authority. Intermediate
revisions lack a complete measurement snapshot and are explicitly unavailable; no latest substitution
or reconstructed measurements are advertised. Missing/corrupt original snapshots or files refuse.

Tool discovery states comparison availability separately from direct tool selection.
Runtime-free native data can choose a tool, but comparison creation refuses before
publication until an explicitly selected scene exists. The Studio keeps direct choice
available and explains that prerequisite; no unusable comparison is retained.


Browser recording start receipts keep a bounded `scope` (`workspace` or `annotated`) in the existing
channel event sidecar. Legacy requests default to workspace; other scopes refuse before allocating a file.
Annotated means the input canvas and its drawn overlays, not a device grant or whole workspace.
Scope is intent; channel observations and actual finalized media establish what was captured.

`scientific_chunk_intake` provides bounded individual-file and selected-folder transport over the existing
`DirectoryTransfer`/`FileIntake` transaction. File and directory routes share one registry; source-kind
checks prevent one route from consuming or stopping the other route’s admitted transfer. At most four opaque, origin-gated upload
leases reserve fixed buffers/metadata and outstanding disk space. One request transfers
at most 1 MiB at its exact offset; retrying the last acknowledged bytes is idempotent.
Complete source bytes alone may publish, with the original captured Project/request.
Stop, idle expiry and App.stop join active work and clean private staging. An active
finish uses its existing bounded verification deadline, not the idle-upload timer.
Stopping attempts stay discoverable until their finish joins, so overlapping recovery
requests cannot report absence before publication settles. The recovery operation
resolves the original deterministic transaction, never a new import. The existing
64 GiB retained-file ceiling and 4,096-entry index remain separate from reader,
output, memory and qualification bounds. Ordinary single-request imports stay 64 MiB.

Supported local reader bundles (every member declares the scientific reader bridge and exact reader checks)
use ordinary setup, reference review and maintenance without claiming independent qualification.
`bundle_admission` separates scientific classification, local eligibility and distribution refusal; mixed or
unchecked scientific packages remain private. Trust, rights, current sealed proof and explicit acceptance
remain required. Reader review identity includes its host/preparation identities; a changed proof needs review
again. Ordinary reader setup records the review requirement before preparation/proof; existing explicitly
activated CLI paths keep their scoped legacy contract, and preparation alone never enables a reader.
New reader intake resolves the selected proven/accepted generation and its declarations, while retained
scientific documents keep their exact original runtime. Public scientific distribution gates are unchanged.

`local_workflow.py` bridges supported nonreader scientific-scene tools into local readiness.
Every declared member needs its own parent-observed completed ordinary control history,
confirmed Save tool (native resources and data-only tool codec), and a new worker that
restores that exact saved revision and renders before any new control or Python call.
The existing proof-attempt/proof files and bounded review artifacts hold sealed local
receipts bound to preparation, declarations, host, input/result/history revisions and
tool state. Preparation, a lone render, Python-only execution, failed controls, changed
source, cancellation or one covered sibling never supplies a complete receipt.
Receipt collection runs on the scientific owner thread; failure never undoes a successful
scientific Save. It does not execute new work or press Enable. Models offers explicit
generation/digest-bound acceptance through the existing own-trial marker; ordinary setup
cannot loop an RGB recheck for an interactive tool. Earlier selected generations remain
selected. Saved documents keep their original runtime independently of new-tool choices.
These are local functional observations, not scientific accuracy, movie/export fidelity,
independent qualification or permission to distribute. Private Project identities and
artifacts remain in the local review store; public scientific package sharing stays gated.

Saved native image → cells uses the existing `run_capability` input/admission,
`population.segment`, Runner, settings and Stop boundary. `native_picture_inputs`
admits only exact Project/revision/component intensity uint8 Y/X planes up to one
megapixel. Lossless PNG is transport to original-picture adapters; no preview,
resizing, flattening or dtype conversion is substituted. Its captured native
identity and scientific descriptor survive in host-recorded population provenance.
Population measurements remain pixel-grid measurements. Explicit Retain labels
checks native source values again, preserves its declared calibration (including
nonunit transforms, or absence), and publishes via existing atomic PopulationIntake.
The shared Population association resolves to the captured accepted input, not an
equal-pixel document; native export includes that source revision. No action history,
biological accuracy or inference timing is inferred. Tools discovery is bounded to
64 saved documents; exact earlier references remain accepted through the shared tool.

`kinds._app_owned` exposes built-in catalog-2 contracts backed by an existing exact
operation/id adapter, beside backend-prepared capabilities. It never changes
ADAPTERS, never borrows a contributed identity, and uses the full readiness resolver
(including install/proof gates). Existing backend rows win deduplication. This lets
the existing reviewed Fiji particle binding use the same native-input action as the
bundled Bright objects algorithm without adding a second execution route.

**Exact view PNG export** (`scientific_view_export.py`, scientific review API). Studio's Export tab captures the
server-issued native frame with the existing Project/Tab/placement/draw admission, saved revision and complete view
preferences. An immutable native frame is encoded as RGB PNG at preview resolution (or an explicit preview-pixel
rectangle), reopened and checked through `view_export`; this is display evidence, never native scientific values or
scientific accuracy. Python, plugin, geometry and Labels mutation conservatively require saving and reopening accepted
work before export; ordinary view navigation/style does not. Background panel invalidation also revokes this eligibility.
The exact generation needs its current proof and explicit review acceptance. Files enter the existing Project file
store; per-document export receipts and failed checks remain beside the original document. Discovery is bounded to
100 receipts and says when limited. Declared affected versions show fix-pending truthfully. Re-export requires an
explicitly opened reviewed fixed renderer, identical retained input bindings and captured view/viewport/region; it
uses that renderer's newly displayed native frame, creates another file and preserves the original. Missing or
incompatible replay stays pending, never copies an old PNG and calls it corrected. No algorithm is run by export.

Measurement summaries (`tables._stats`, `stats.describe`) use one finite numeric population for counts,
quantiles, sample SD/SEM and the Student-t interval. Missing/non-finite values and booleans do not count;
columns without finite observations omit their summary. Ordinary finite tables retain their earlier summary keys.

`analysis_math.analyze` consumes a saved table snapshot and an explicitly selected test. `table_selection.selected_rows`
is shared with graphs: exact row identities, finite numeric observations and stated exclusion reasons. Paired t
requires a unique explicit pair-key column and reports unmatched rows. Tests are two-sided; code reports assumption
facts and never chooses a test or labels significance. Nonparametric/omnibus effect intervals use a recorded fixed-seed
percentile bootstrap, bounded to 5000 observations; larger or degenerate intervals remain explicitly unavailable.
All calculation and SciPy imports belong off the live event loop. Saved results, not recomputation, feed exports.

The table graph evaluator reads the same explicit row selection as table analyses.
It computes histogram edges/counts, quartiles/whiskers, Scott-kernel density and
mean uncertainty off the loop. Accepted row identities, exclusions, units and
unavailable uncertainty remain in the result. Pair annotations require an accepted
analysis of the same exact table, selection and included rows; they display recorded
probabilities, never an inferred significance verdict. Graph reopening and export
render accepted values without invoking the evaluator.

`table_routes.py` exposes explicit table-derived creation, exact-version reading and
CAS Freeze/Follow/Update through the Studio's local-origin header boundary. Every
request captures its Project owner and rechecks it after off-loop evaluation/read/
encoding. Workbook export includes each analysis's newest accepted version matching
the selected exact historical table hash; neither export nor reopen evaluates a tool.
Malformed or changed selections refuse with recovery, preserving prior accepted work.

A new workspace researcher's finished work is kept, never placed in a Tab by the app: it stays in Data and in
Researchers, and a person opens it in a Tab (Add to Tab…, New Tab). The server completion hook, which works
without an open Studio, records one `available` receipt per accepted result version with the reason "Kept in Data
and in Researchers." (`workspace_views.KEPT_REASON`), no `outputTab` and no `notice`; it changes no arrangement, so
it takes no new layout revision and an open Studio's draft stays current. Reconnect is idempotent. Output Tabs made
before (0.3.0) stay readable as saved, with what they hold; an earlier refusal row whose notice promised a later
placement is replaced once by the kept receipt. Local arrangement saves merge only proven server additions (a kept
receipt adds nothing; an earlier output Tab's completion still proves itself). Schema-5 table/analysis/graph
placements require the matching tb/an/gr identity, positive saved revision and no picture/output selector. These
layout references grant no execution or scientific access.

Saved A/B/C Compare Tabs can become dataset tables through `DataTables.from_comparison`: exact population
revisions, declared/actual method match and common captured-input identity are checked before the shared
measurement reader and snapshot publisher. The Method rule and explicit same-objects/non-independence warning
are retained with comparison provenance; object pairing is never inferred. Tables start frozen. Explicit updates
retain actual source revisions and recheck input/method identity. Native scene-only lanes need retained supported
measurements; this path neither converts pixels privately nor runs scientific code.

Derived table snapshots also retain the exact source snapshot's `comparison` metadata, when present. Their wire
replies expose that accepted warning and source-revision record; rendering an analysis/graph must not lose the
same-object/non-independent-sample qualification. Reopening reads this stored metadata without recomputing it.

Named agent Tab actions use the original request’s accepted Studio observation. The
`workspace-tabs` structural surface pins its real root lifetime, source generation and
connection identity, separate from the backend Project epoch. A reopened workspace
cannot inherit a queued action; ordinary switching between its Tabs preserves the pin.
The Studio validates the pin before mutation and after its initial save, and echoes it
only after the arrangement is saved. The broker checks the same original owner again;
no other Studio, newer observation or unsaved acknowledgement substitutes for it.

Native object measurements continue through `tables.read_table` into the existing editable
DataTables, analysis and graph services. The source records the exact native document digest,
measurement schema/units/definitions and separate producing-label revision. Object IDs remain
lossless text row identities; rows never become synthetic populations. Native sources are frozen;
edits and reopen read accepted snapshots and never rerun measurement or segmentation.

Editable-table exports and historical numeric reads bind ownership to the exact
Project-relative snapshot path, ID/version and hash; newer records also retain an
explicit `session`. Journal `day` is the calendar save date, never Project identity.
This preserves suffixed/reopened Projects while foreign paths, sessions and derivative
source owners still refuse. Export/reopen never refresh or recompute accepted values.

Scientific workflows use `workflow_service.py` for Studio, local researchers and connected agents. Definitions are personal immutable versions; runs and separate attempts belong to their captured Project. Named file inputs are exact revisions, not paths. `matrix_capability_runs` executes catalog operations without owning another task ledger. Reads do not dispatch; explicit recovery never replays uncertain attempts. See `docs/architecture/scientific-workflows.md`.

Built-in workflow algorithms publish through `BuiltinOutputSave`, sharing native chunk grants,
coordinate checks and atomic publication with worker outputs but retaining their own versioned
producer instead of borrowing an input viewer runtime. External worker `OutputSave` still
requires its retained runtime. Native inputs/layers without a runtime may be copied for
measurement; this does not authorize execution or replay.

Matrix workflow steps wait up to one hour for the shared machine compute reservation,
retaining the original running attempt and a waiting progress note. Stop or service
shutdown prevents a waiting step from acquiring compute; no other owner’s lock is
reclaimed. The admission rechecks disk and the pinned generation after acquiring the slot;
the scientific worker retains its memory guard.

Scientific matrix worker failure notes put the final exception before their bounded traceback tail, so the
workflow's compact retained note preserves an actionable cause. Full worker logs are not claimed as retained.

Retained comparison input identity comes from `retained_input.comparison_input_key`, shared by
resource discovery and comparison-table validation. Imported images use their exact source descriptor,
including file/pixel hashes and scientific metadata; a temporary negative capture number is not identity.
Camera results require capture, sequence, boot and process run. Unknown source identity refuses comparison.
Existing table snapshots remain readable; new publication/update revalidates inputs without rewriting evidence.

Portable scientific routing is described by ADR 0027. `ProjectStores` resolves a
retained owner before paths are used; admission helpers capture `for_session`
with write intent. Never derive a scientific destination from the visible Stage.
The shared workflow library remains host-owned; runs retain frozen definitions.
Live App activation and complete legacy collection remain separate qualification.

`project_folder_copy` is a verified byte-copy stage, not proof of dependency
closure. Both scientific/journal publication guards remain held through copy
acceptance, excluding concurrent bundle import and refusing its pending barriers.
Inactive copies acquire a writer lease; an owning host can copy under
its retained lease without releasing it. Source membership and captured file
identities/hashes are checked again before publication; observed changes, shutdown
or location changes leave an incomplete destination for explicit recovery.
Snapshots cannot acquire a writer. External/shared dependencies and eventual
explicit working-copy import remain separate responsibilities.

Selected Library intake can retain its metadata history alongside copied bytes.
`project_media_history.py` reads bounded complete journal lines on the intake
worker and selects only the source's identity, edits and applicable shared example
classification. Final publication holds the Library lock and verifies the captured
journal identity; changed metadata refuses without accepting a mixed revision.
History hashes cover selected records only, so unrelated later edits do not change
an accepted import's request identity. Author labels remain historical evidence,
not grants. This does not collect a whole Record dataset or activate playback aliases.

Portable snapshots accept only after all output and directory syncing finishes. The durable pending marker is removed as the last fallible action; failures remain uncertain across host restart without a recovery write. Explicit reconciliation remains required before using uncertain output.

App.build accepts an internal, explicitly supplied ProjectLocations dependency for
isolated host qualification. It checks the legacy host identity and shares that
resolver between the real SessionStore and scientific ProjectStores. Failed build
releases its leases. App shutdown fences further admission but retains its writer
leases until process termination: cancelled tasks may still have writing threads.
`server.create_app` accepts the same keyword-only dependency for isolated full-server
qualification; ordinary command-line startup still supplies no resolver. This is not a
request-selected storage path or a live migration/default-policy change. Portable
UI session switching, media aliasing and provider execution remain unqualified.

`project_storage_status` projects location/owner facts for an already authorized
Project. It checks retained ownership even for an existing lease, reports missing
registered folders without fallback, and leaves collection completeness unassessed.
This read does not enable live portable creation or acquire write authority.

Retained media previews use exact Project/document/revision/content hashes and verified file chunks. A small byte-signature allowlist supplies image/video transport types; SVG/HTML and unknown encodings remain downloads. Single HTTP byte ranges support playback seeking; invalid or multiple ranges refuse. Manifest/ownership checks precede regional reads; only the header and requested chunks are freshly verified, never the entire recording on each seek. This route does not select a Library source or infer scientific meaning.

`project_folder_copy.restore_snapshot` explicitly creates a new working copy of a
verified snapshot. It retains the source seal under history/restorations, preserves
exact accepted bytes and checks disk, source membership and changes before final
publication. It does not register the location, transfer grants, select the Project
or replay ongoing operations. Source snapshots remain immutable and external
dependency completeness remains unverified. Ordinary portable activation stays off.

Matrix inspection retains strict raw-count admission by default. Its explicit
`require_raw_counts=false` mode describes finite numeric matrices, recording row
totals and unverified value semantics instead of implying cell library depths.
Count preparation, prediction and export keep their existing strict checks.

Project-folder estimates also include a bounded declared-record dependency review.
It checks every accepted revision's exact artifact links, including Study inputs,
claims/questions and workflow inputs/output lineage. External Projects are reported
for inclusion review and never opened. Missing evidence, digest conflicts and
unpinned execution locators are distinct; no prose/path inference, mutation or
replay occurs. This is a partial preflight, not full dependency-closure certification.

Project snapshot sealing and verification read every accepted scientific revision
and verify its owned file/array bytes using the existing import/publication readers.
Corruption cannot acquire legitimacy from a fresh outer checksum. Verification is
bounded and cancellable; it neither repairs evidence nor certifies dependency closure.

`project_dependency_access.inspect` is the shared read-only, captured-Project
inspection over declared scientific record dependencies. It retains an exact head
basis and checks for changes before returning. It runs off the event loop and
supports cancellation/authorization checks. It never opens another Project on the
strength of a saved reference or claims journal/media collection is complete.

Project folder estimates include bounded historical source-event preflight. Removed
Library sources remain dependencies; live camera and prose do not become file links.
Exact retained choices are scoped by origin, with missing inclusion explicit. This
reads no host Library and does not certify all media/journal dependencies collected.

Owner Project-folder export commands use the same snapshot service and original
Operations records as connected contributors. Preparation is read-only and scoped
to the captured Project; starting captures the requested owner before background
work. Navigation does not retarget admitted copying. Status/Stop read/control only
that Project's export operations. Completed copies remain explicitly unverified for
external dependencies; failure/cancellation preserve incomplete destinations.

Folder snapshot verification reads every journal-accepted dataset-table, analysis and graph revision through table_reading.read_accepted using only the captured Project artifact roots. Missing, changed, foreign or unsupported numeric history refuses a new seal; verification does not run evaluators or rewrite history. Complete external-source collection remains separate.
