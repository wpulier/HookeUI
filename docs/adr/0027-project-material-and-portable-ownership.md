# 0027 — A Project owns research; its location does not define its identity

Accepted direction, 2026-10-29. Implementation is staged; this decision does not claim completed portability.

## Decision

The Project is the durable research repository shared by UI, researchers and connected agents. A Dataset is a collection within that research, a Study owns a question/protocol, and an Artifact is retained evidence or output. Existing Record datasets retain their cross-Project scope and capture semantics.

One server-owned inventory extends the existing resource index. Classification comes from declared document kinds, capability outputs and explicit membership, never inferred scientific meaning from a filename. Origin and subsequent input use are separate. Unknown files stay accessible. Workflow receipts are history attached to a run, not additional scientific results.

Portable Projects default to `~/Hooke Projects/<name>—<short identity>/` with a versioned `project.json`, README, inputs, artifacts, history, workspace and exports. Existing journals, immutable revisions and workflow receipts remain authoritative. A host registry locates roots; the durable Project-origin UUID owns identity. Credentials, grants, billing and installed runtimes stay host-local.

Imports copy by default (copy-on-write where supported), never hardlink mutable originals. Explicit links report hash and availability. Collection includes every accepted revision and dependency, useful research traces, conversations and workspace, with disk estimates and omissions. Cross-Project recordings require explicit inclusion review. An export is an immutable hashed snapshot; ongoing work is recorded, never replayed by opening it.

One host coordinates writes. Concurrent hosts must open read-only under an active writer lease. Per-document revision conflicts still apply. Duplicate origins require an explicit read-only choice, inactive-location switch, or an independent Project preserving ancestry but inheriting no grants. Historical labels never authenticate local actors.

Migration is explicit: inventory, space estimate, settle work, copy, verify, reopen, then registry switch. Preserve originals for recovery. No bulk migration, automatic deletion, Git requirement, remote sync or accepted-record filesystem editing.

## Delivery and proof

Folder-copy verification checks retained researcher metadata as well as scientific
documents: archived metadata must match its content-addressed name, every record
must retain its Project/researcher/date identity, and saved finding revisions must
match their original payload digests. Incomplete publication refuses sealing.
This neither qualifies provider execution nor establishes closure of external
research dependencies; original files and failed copies remain available for recovery.

1. Shared inventory and Inputs/Data/Activity repair over current storage.
2. Root resolver, manifest, registry, writer lease and new-Project storage.
3. Complete collection/opening, relocation, independent copies and transactional conversion.
4. Actual UI and two named connector clients resume the same research.

The finish line includes mixed microscopy/matrix restoration with exact historical hashes, conversations, Studies, results and layout; interrupted copies, changed/missing links, insufficient disk, unavailable runtimes and conflicting writers must be recoverable. Missing software may block reruns, never reading retained evidence. Partial scientific bundles remain explicitly partial.

ADR 0026 grants apply unchanged to saved-input work. New door actions are deliberate default-deny declarations. Importing or moving a Project never widens authority. Shared contract schemas apply locally and hosted; unsupported hosted storage operations refuse explicitly.

## Ordinary server qualification seam

The server factory accepts an explicitly supplied ProjectLocations dependency,
matching App.build. An operator may now select this staged seam using
`--project-folders /absolute/parent` or `HOOKE_PROJECTS_DIR` through the normal
launcher. This enables isolated real-browser/connector qualification on the same
server path, rather than a private replacement server. No request or saved document
selects it. With neither setting, existing installations remain unchanged; a profile
that already has a location registry refuses to silently fall back to legacy storage.
An isolated full-server probe uses the ordinary lifespan and
Studio Project creation, grants a synthetic connection through signed owner
confirmation, imports copied evidence and creates a Study. After process shutdown,
folder relocation and restart preserve exact saved documents and inventory.
This is server/storage evidence, not actual browser/client, complete dependency
collection or permission to migrate existing Projects. Default-on new-Project
creation remains gated on the remaining qualification. Operator opt-in creates new
folders only; legacy research remains in its existing location.

## Foundation implementation checkpoint

`project_roots.py` supplies versioned layout validation, origin/location pointers and
an advisory writer lease for later storage adapters. It is not wired into live
Project creation and does not certify dependency completeness or migrate existing
Projects. The registry is host-local and never carries grants. Metadata reads
refuse links/nonregular files; unknown formats and conflicting locations preserve
existing files. Filesystem locking is required; remote/sync filesystems remain
unqualified.

New research transcripts are retained under `research/transcripts/`; legacy
`logs/transcripts/` is excluded from performance pruning. Both locations must be
considered during explicit collection. These are bounded traces, potentially
incomplete after write failure, not complete provider transcripts.

## Retained-store adapter checkpoint

`project_repository.py` creates a new journal-owned folder and adapts the existing
SessionStore and ProjectStore, without routing live App creation or migrating any
existing Project. Runtime ownership reads stream ordinary history independently
of partial-transfer size limits. Shared publication locks remain authoritative and
can be acquired from read-only media. New folders initialize those locks and sync
the copied journal and directory chain before reporting creation success.

Writable scopes require the registry-selected location, the folder writer lease
and a host runtime root for retention registration. Host preferences and installed
runtimes remain external. Saved reads require neither a running worker nor runtime
installation. Isolated close/move/reopen tests preserve conversation events and
exact document revision hashes; these do not certify complete collection, legacy
migration, cross-machine reassociation or the live connector.

## Journal routing checkpoint

The optional host `ProjectLocations` resolver lets the existing SessionStore and
Catalogue use legacy journals alongside separate Project roots. Background filing
captures the original owner; changing the workspace does not change its folder.
Writer leases belong to the host lifetime. A missing registered folder refuses
instead of recreating an empty legacy Project. Host preferences remain external.

Registry format 2 adds the compatible session handle to each origin/location
pointer, allowing unavailable folders to reserve their handles. Readable format-1
locators upgrade on registration; an unavailable old locator requires explicit
recovery using the matching origin. Colliding legacy origins refuse. Foreign
same-handle reassociation and independent-copy creation remain unfinished.

The live App is not enabled on this seam until all scientific/legacy result stores
are routed. The isolated tests exercise real journals, writer exclusion, move and
resume; they do not qualify complete Project collection or live UI portability.

## Scientific routing checkpoint

`ProjectStores` binds scientific storage to the captured Project before a service
uses paths or admits work. Imports, corrections, output publication and workflow
receipts use that binding; the shared workflow library stays host-owned and each
run retains its frozen definition. Unbound filesystem access refuses instead of
falling back to the global store. Legacy stores keep their existing layout.

A bound writer requires the same registered origin/location and host lease.
Another host may read saved results without registering installed runtimes; it
cannot publish while the writer holds the lease. Closed or relocated bindings
refuse late writes. Scientific exports resolve each dependency's owner separately
and still constitute partial scientific bundles, not complete Project exports.

This slice remains opt-in below App wiring. It does not migrate existing results,
route historical Library/capture stores, or qualify full collection. Real isolated
bytes and synthetic native edits verify captured ownership, old revisions, workflow
review, move/reopen and writer exclusion; live UI portability remains a later gate.

## Folder-copy checkpoint

`project_folder_copy` reserves a new destination, estimates disk demand and copies
an inactive routed folder under its exclusive writer lease. It preserves journal
and revision bytes, checks each copied hash and checks the source again before
sealing. Interrupted destinations retain an explicit incomplete marker and cannot
open as Projects. Existing destinations and source files are never replaced.
Sealed snapshots are read-only through the host writer API, require no installed
runtime to verify, and carry no registry entries or host runtime bindings.

This is the byte-copy stage only. Its manifest explicitly says external dependency
closure is not verified. It is not exposed as Collect Project or complete export;
legacy assets, shared captures and external dependencies still require their
collector and inclusion review. No accepted research is rewritten or replayed.

Copy intent is synced before retained-looking files are created. Final directory
syncs finish before completion is published; a failed sync or marker removal leaves
the destination incomplete across restart. No fallible storage read or sync follows
marker removal. A crash may conservatively restore the incomplete marker.

## Numeric snapshot routing checkpoint

Dataset tables and derived analyses/graphs retain their existing versioned snapshot
format under the owning Project's artifacts folder when location routing is enabled.
Reads resolve the captured Project and verify accepted hashes; publication requires
its host writer lease and unchanged location. A moved Project can read historical
numeric results without the evaluator that produced them. Legacy storage remains
compatible. This adapter does not enable new-folder creation in the live App or
claim that shared recordings and all other research dependencies are collected.

## Microscopy evidence routing checkpoint

With location routing enabled, each population save retains an immutable folder,
origin-qualified relative reference and manifest digest in the existing journal.
Earlier saves survive corrections, links and relocation. Readers require the same
origin and accepted manifest; publication requires the host writer lease and
unchanged destination. Legacy absolute evidence is not silently converted.

This adapter does not collect original Library assets, tracks or shared Record
captures. It remains below live App activation and does not certify complete
microscopy Project portability. Isolated synthetic-image tests qualify retention
and failure behavior; actual mixed-domain collection remains a separate gate.

## Motion evidence routing checkpoint

Tracks now share the staged immutable evidence publication and resolution path.
Every accepted save pins its original Project, relative location and manifest hash;
reads do not recompute motion. This closes the tracks adapter gap but not Library,
Record, researcher, snapshot/bookmark or full dependency-collection coverage.
Live App activation remains off. Synthetic points/images through real journals and
workspace recovery establish storage behavior, not a real microscopy experiment.

### Staged bookmark adapter

Bookmarks join the opt-in Project-owned artifact stores. Each accepted bookmark keeps its original owner, date, identity, note and picture digest; it can be read after relocation without a camera. Background decoding cannot retarget a later Project selection. Library assets, recordings and remaining research dependencies still block live activation and complete collection.

### Staged snapshot adapter

Snapshots now publish annotated and clean source images under their captured Project,
with hashes and an incomplete marker removed only after publication. Index reads
resolve that original owner after relocation without a camera or installed runtime.
Legacy snapshots keep their existing format and location. Library identity mapping,
shared captures and remaining dependencies still block live activation and complete
collection; this adapter alone is not the full portability journey.

Snapshot publication durably retains its pending marker before writing output.
All required syncing finishes before the final acceptance action removes the marker.
Failures preserve uncertainty across host restart without relying on process-local
state or a second recovery write. A crash may conservatively restore a pending
marker; explicit reconciliation is still required, never automatic replay.

### Staged volume and registration storage

Volume copies, inspections, job outputs and previous metadata states route through the captured owner. Existing registration state and output-acceptance semantics remain authoritative. Equal local job IDs are scoped by Project for cancellation and publication visibility. Host installations remain external dependencies. This adapter alone does not qualify collection, imported execution recovery or live portable activation.

### Staged identity history

Identity links and observations use their captured Project’s append-only event file. Original IDs and merge history replay unchanged after relocation; failed writes are not applied to in-memory accepted state. File/root replacement refuses. A crash-torn tail is preserved and isolated from later complete events. This does not route cross-Project taught/seen memory or enable complete collection.

Portable identity publication keeps a durable pending marker before append. Failed
publication leaves the journal uncertain: retries and fresh replay refuse, retaining
all bytes for explicit reconciliation. Removing that marker is the final acceptance
action; a crash may conservatively restore uncertainty. Background identity lifecycle
binding and reconciliation UI remain open gates: the staged adapter explicitly
refuses event-loop use, rather than blocking live frames or silently claiming a save.

### Retained researcher files

The staged research storage adapter routes saved researcher records, previous
interpretations, output files, notes and bounded transcripts by their captured
Project. Historical file serving resolves the saved owner rather than the active
workspace. Ambiguous researcher identities refuse rather than silently choosing
another Project. An interrupted imported effort is displayed as interrupted and
never replayed; a read-only host does not rewrite its saved accounting or status.

This is retained-work routing, not provider execution qualification. New provider
runs, follow-ups and presentation generation in portable Projects explicitly
refuse until host-local spending, worker lifetime, source acquisitions and full
collection are qualified. Existing legacy Projects keep their working researcher
paths. Global descriptions/shared acquisitions, dependency closure and live App
activation remain open; no complete portable Project claim follows from this slice.
### Selected Library media retention

The staged `ProjectMedia` adapter copies an admitted image or recording through the
existing immutable file intake. Its exact reference includes the Project origin,
document revision and digest; a coincident `a1` in another host's Library cannot
resolve it. Known Library metadata is retained at that revision. Changed originals
require a new admission, and deleting the original does not remove accepted bytes.
The shared inventory uses the declared media kind and preserves generated origin
when a snapshot is selected as input. Reading retained chunks needs no media decoder.

This adapter does not remap live Library aliases, collect Library edit history,
materialize playback files or collect Record datasets. Record parts refuse until
whole-dataset inclusion is reviewed. Live portable activation and complete mixed
Project collection remain off pending those integrations.

### Selected media metadata history

Collection of a Library source preserves its available metadata revisions, including
calibration and user corrections, alongside the exact copied bytes. Only records
for that source and applicable shared example classifications enter the Project;
the rest of the Library stays outside. A bounded, complete journal read and a
publication-time Library guard prevent mixed metadata/byte captures. Truncated,
conflicting or oversized history refuses rather than claiming complete collection.
This extends selected-media retention; shared Record capture collection, playback
identity mapping and full Project migration remain separate acceptance gates.

### Whole Record folder inclusion

The internal Record collector reviews the complete selected folder, including its
cross-Project contents, hashes and required space. An admitted collection binds the
exact review digest; that digest is not authorization. The caller must separately
establish the owner's inclusion decision. Files enter the existing immutable
source-directory store, never mutable hard links to an original recording.

The review preserves capture warnings and explicitly reports external references
as uncollected. It verifies declared camera parts and retains every folder file;
it does not certify capture quality, decode media, or recursively collect paths
mentioned in conversations. Unfinished checks, missing parts, links, changed files,
oversized metadata and cancelled copies refuse. No public collection action or
live App activation is added by this storage slice. Complete Project dependency
closure, inclusion UI and mixed-domain reopen acceptance remain required.

### Application integration qualification

App.build can explicitly receive the host location resolver. The real application
then shares it between its journal and scientific record services; no request or
saved document can select that dependency. Build failure releases admitted leases.
App shutdown fences later storage admission and retains its leases until actual
process termination. Cancelling asyncio tasks does not terminate their writing
threads. A second host stays read-only until the original process exits. Ordinary server startup remains legacy.

The isolated application/connector test creates a Project folder, copies an input,
creates a Study, stops, relocates, resumes the selected Project and reads the same
revisions. Its connection confirmation is synthetic. This is a step beyond separate
store tests, but not ordinary UI session-switch qualification, full dependency
collection, portable media playback or provider researcher execution.

## Storage-status contract

Authorized readers can inspect durable origin, known folder location and observed
owner availability through the shared `project-storage` contract. Legacy storage is
explicitly distinct from a portable folder; a missing registered folder does not
fall back to legacy creation. Inspection acquires no writer lease and transfers no
authority. Collection status remains `not-assessed` until the complete dependency
collector is qualified. A host without local storage support reports unavailability
under the same contract. This slice adds no collection/open/migration mutation.

## Owning-host snapshot checkpoint

A host that already owns the Project writer can invoke the same bounded folder
copy stage without releasing its lease or replaying an operation. Its captured
location and continued writer ownership are rechecked during copying. Both stores
retain their existing shared publication guards throughout, so a concurrent bundle
import cannot be sealed halfway through its transaction. Source
changes refuse acceptance and preserve the incomplete marker. The resulting
read-only snapshot retains observed running states as history, not commands.

This internal primitive does not establish dependency closure, guarantee a copy
will finish while research keeps changing, or expose a complete Project export.
The ordinary host still does not enable portable locations by default.

## Portable identity lifecycle

The live Identity view loads portable history on a serialized background path. Each
admission captures the Project and UI generation; a late load cannot overwrite a
newer selection. Existing identity tools, linking and observations persist through
the same append-only history before updating the matching live view. Caller
cancellation does not release the queue while its worker still writes. A changed
Project refuses new admission; already-admitted work keeps its original owner.

Loading and uncertain history are explicit, never an empty accepted identity list.
The follower waits for known history. Synchronous portable mutation still refuses
on the event loop. These tests qualify the identity lifecycle, not full Library
playback, complete dependency collection or ordinary portable-folder activation.

### Retained media decoder boundary

A historical Library ID is scoped by its Project origin. Exact retained references
remain authoritative; multiple accepted sources are choices, never newest-wins.
For decoders requiring a filename, an off-loop adapter creates a bounded, verified,
read-only temporary copy outside accepted Project storage. Its context lasts through
decoder shutdown and removes only disposable bytes. No Library alias, automatic
execution or accepted record is created. Actual playback and workspace restoration
still require qualification before ordinary portable creation is enabled.

### Recorded inspection facts in the shared inventory

An exact retained matrix-inspect result may describe its input as an AnnData matrix with recorded dimensions. Join by Project, document revision/digest and file component/hash/size. Keep the report references and origin; never infer from filenames, revise inputs, or apply an old report to a new head. Conflicting dimensions remain explicit. UI and agents receive the same projection.

### Explicit snapshot restoration

A retained folder snapshot can be copied into a new working location by an explicit
owner action. The source stays immutable; all recorded bytes and revisions retain
their original identity. Verification precedes disk admission and is repeated before
publication. The working copy keeps the source snapshot manifest as restoration
evidence, including its unverified external-dependency status. This is not an
independent Project, migration, or proof of complete collection.

Restoration does not register a location, import grants, select the Project, execute
work or reinterpret recorded running states. Location selection separately checks
the original writer and same-origin collision. The ordinary host still does not
enable portable creation until complete collection and UI journeys qualify.

### Snapshot record integrity

Folder sealing and verification also validate every accepted scientific revision
and its owned array/file components through the existing scientific-store readers.
Fresh filesystem checksums cannot bless an already damaged accepted record. This
is bounded, cancellable integrity checking, not a claim that external dependencies,
legacy media, or every scientific relationship have been collected. Unknown
research document kinds remain readable; no saved content is executed.

### Declared dependency review

Before collection can claim completeness, review the exact artifact relationships
declared by scientific records, Studies, claims and workflows across every retained
revision. A reference to another Project is an inclusion decision, not authority to
read or copy it. Same-Project references must resolve their original revision and
digest; unpinned execution locators stay explicitly distinct from exact evidence.
The review reports missing/conflicting evidence and external dependencies without
repairing, replaying or guessing links from prose or filenames. This review covers
declared scientific-record relationships only; journals, shared captures and
unknown extension semantics still require their collection qualification.

### Authorized dependency inspection

The same declared-record review is available through a read-authorized
`project.dependencies` door action for both legacy and routed Projects. It captures
all accepted document heads, checks their historical references off the event loop,
and refuses if membership or heads change during the review. The receipt includes
that exact basis; references to external owners remain unresolved inclusion requests,
never authority to read them. Cancellation and revocation stop the affected review.
This is an inspection, not collection, migration, grant creation or a completeness
certificate. Hosted implementations without local storage report unavailable using
the same contract. The CLI discovers this action without a private code path.

### Historical source inclusion preflight

Collection estimates inspect all journal source events, including removed sources,
and show exact retained Library-binding choices scoped by the Project origin.
They never resolve a historical Library ID against this host's unrelated Library,
select newest-wins, or infer references from conversation text. A missing binding
requires explicit inclusion review. A binding is not proof of whole-Record inclusion,
complete Library history or verified media bytes. Changes, damaged journals and
resource limits refuse this preflight; active work is never replayed.

The authorized dependency action includes this historical-source review when a local
Project journal is available. Both legacy and portable stores use the same bounded
reader, origin checks and exact choices. This adds no authority to inspect a global
Library or other Project, and no caller can supply a filesystem path. Older or
unavailable hosts may omit sourceReview; omission never means dependencies complete.

### Retained Stage inputs

With portable routing explicitly enabled, Stage selection requires an exact retained
media revision. A historical Library ID is only an alias within that Project's
recorded source binding; it cannot resolve against the current host's Library.
The bridge receives a verified disposable decoder copy with a supported suffix.
An uncertain bridge request retains its possible input until a later acknowledged
replacement. Stopping the host retains these decoder owners until process teardown.
Original-image analysis reads accepted Project chunks independently of that cache.

Source journals retain the reference, and same-ID/different-revision populations
cannot overlay each other. Calibration and Library metadata/trash actions cannot
mutate accepted source facts or an unrelated local asset. A separate reviewed
revision-editing path remains required. This staged integration does not yet qualify
complete source collection, automatic imported-source selection, or the ordinary
portable browser journey; normal portable activation remains off.

## Explicit material intake

The ordinary owner Add material path and already-authorized connector file-open share selected-media retention before Stage selection. A `retained` journal event binds exact bytes to the captured Project without moving the active source. Reopening resolves that binding and never consults a mutable original. The shared `project-media-reference` schema includes the durable Project origin, legacy host handle, document revision and digest. This adds no outside-agent Library access, and whole Record inclusion remains separately reviewed. Ordinary portable activation and complete collection remain gated on their unfinished acceptance journeys.

An intake receipt is replayed without reapplying membership: a later source choice or removal wins. The journal reconstructs that receipt set, including after restart. UI retries preserve their request key across uncertain connection failure. Layout schema 7 carries the selected exact media reference through Tab navigation and reopening; older layouts remain unchanged and older servers cannot silently discard it. The opened Stage reference remains separate from a subsequently retained source binding.

### Operator recovery of a known relocated folder

Staged hosts may explicitly supply `--reopen-project` (or `HOOKE_REOPEN_PROJECT`)
at startup to resolve a moved folder already registered in that host. Verify its
retained origin and unchanged compatible handle, reject a conflicting legacy owner,
and replace the locator only while both old and selected locations are inactive.
This is relocation recovery, not new-Project import, independent-copy creation or
collection. Unknown origins refuse; no grants, accepted history or preference
selection are imported or rewritten. The host's existing last-opened Project resumes
normally after the locator is repaired, and existing execution recovery rules apply.

### Project-owned connector operation records

New operation admissions in routed Projects use `history/operations` as their one
authoritative record location. The existing operation service still owns admission,
retry keys, cancellation and observation of workflow executions; storage routing
does not create another execution system. Capture the Project origin at admission
and refuse a changed or unwritable location rather than falling back to host storage.
Old host-local receipts remain where they are until explicit collection.

Loading a Project's saved operations never launches work or imports connections or
grants. Duplicate operation IDs or request identities across locations are conflicts,
not a choice to overwrite either history. A second host may read saved records but
may reconcile or change them only with the Project's writer lease. Unfinished direct
operations recover as interrupted; workflow references remain owned by WorkflowService.


### Native microscopy evidence in collection verification

Folder snapshots also verify every journal-accepted microscopy save, including
superseded revisions and same-revision enrichments. Resolve the origin-scoped
retained location and manifest pin through the existing evidence reader, verify
its required capture/mask files and every declared optional file, and confirm its
original population, Project and revision. A new outer snapshot hash cannot bless
corrupted accepted evidence. Legacy absolute locations require explicit collection;
no filename guessing or fallback to another host's evidence folder is allowed.
This extends integrity checking, not complete journal dependency coverage.


### Explicit folder snapshot export

Owner controls and contributing connections may request a snapshot of an already
routed Project through the existing operation service. A read-only preparation
reports the byte estimate and unresolved collection scope first. Export writes only
a new host-selected destination under the configured Projects parent’s Exports
folder; callers cannot provide a filesystem destination. No Project membership,
credentials, grants, registry entries or running work are copied into authority.

The snapshot retains its manifest and hashes and is immutable under the existing
Project writer rules. This first export is explicitly a **folder snapshot with
unverified external dependencies**, never a complete collected Project. Callers
acknowledge that scope. Legacy Projects refuse until migration/collection exists.
Retries recover a completed same-operation destination, never overwrite it; an
incomplete destination remains for recovery and requires a new export request.
UI and connector expose the same receipt and completeness meaning. Hosted services
without local folders report explicit unavailability. Full collection stays a
separate gate and is not inferred from a successful export.


### Owner-selected working-folder opening

A staged host may explicitly open an existing working Project folder at launch.
This registers a new origin only after validating its journal and accepted evidence
under the writer lease. A conflicting host handle, legacy owner or existing local
agent-permission record refuses; historical author labels never become authority.
A known origin at a different location still requires the separate inactive-location
choice. An immutable snapshot first needs explicit working-copy restoration.

Opening imports no credentials, grants or runtimes, never invokes retained work,
and does not certify complete dependency collection. This operator launch seam
qualifies restoration through the ordinary host; the native folder picker, read-only
workspace mode and independent-copy UI remain separate product gates.


### Owner Open Project control

The existing Project menu can register an explicitly entered local working-folder
path through the same owner registration service as startup. Registration verifies
accepted history and conflicts before adding a locator; it never moves the Stage,
imports grants, or launches work. The UI then uses ordinary Project navigation,
including its unsaved-work checks. Closing the dialog or changing Projects while
verification runs cannot cause late navigation. A registered folder remains in All
Projects if navigation is declined. This owner command is deliberately unavailable
to connected agents; they still need locally established Project access.

The native shell supplies its origin-checked directory chooser; browser path entry
uses the same owner admission. Neither entrance is snapshot restoration, read-only opening, independent-copy creation or legacy migration.
Portable-mode activation remains an operator choice.

## Historical attribution after relocation

Resource makers use the label captured in an accepted agent import before any
current host label. Reopening a Project without its old connections must preserve
that recorded display name and stable actor ID. Reading a historical label never
registers a connection, updates the host label cache, or grants access. Records
without a retained label keep the existing host-label/unknown fallback.

### Owner-selected location recovery in Studio

Open Project may explicitly request “Use this location” for an origin already
registered on this host. This reuses the registry's inactive-location transaction:
verify retained history, refuse either active writer, and publish only the locator.
The Project origin, accepted bytes and local grants stay unchanged. An unknown
origin cannot use this mode or adopt historical permissions. Default opening still
refuses an implicit relocation. Studio's normal unsaved-work gate owns navigation.


### Accepted numeric history in snapshots

All journal-accepted table, analysis and graph revisions participate in folder verification, including superseded values. Reuse the existing numeric reader with the captured Project artifact root and original accepted identity/hash. Copying or recomputing an outer manifest cannot bless damaged earlier results. No evaluator is installed or run, and original records are never repaired automatically. This is another producer-owned integrity check; source dependency closure and legacy migration remain separately qualified.
