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

1. Shared inventory and Inputs/Data/Activity repair over current storage.
2. Root resolver, manifest, registry, writer lease and new-Project storage.
3. Complete collection/opening, relocation, independent copies and transactional conversion.
4. Actual UI and two named connector clients resume the same research.

The finish line includes mixed microscopy/matrix restoration with exact historical hashes, conversations, Studies, results and layout; interrupted copies, changed/missing links, insufficient disk, unavailable runtimes and conflicting writers must be recoverable. Missing software may block reruns, never reading retained evidence. Partial scientific bundles remain explicitly partial.

ADR 0026 grants apply unchanged to saved-input work. New door actions are deliberate default-deny declarations. Importing or moving a Project never widens authority. Shared contract schemas apply locally and hosted; unsupported hosted storage operations refuse explicitly.

## Ordinary server qualification seam

The server factory accepts an explicitly supplied ProjectLocations dependency,
matching App.build. No request, environment flag or ordinary command-line launch
selects this seam. An isolated full-server probe uses the ordinary lifespan and
Studio Project creation, grants a synthetic connection through signed owner
confirmation, imports copied evidence and creates a Study. After process shutdown,
folder relocation and restart preserve exact saved documents and inventory.
This is server/storage evidence, not actual browser/client, media restoration,
complete dependency collection or permission to migrate existing Projects.

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
