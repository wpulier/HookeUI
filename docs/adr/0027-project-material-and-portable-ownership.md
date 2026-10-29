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
