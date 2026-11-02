# 0028 — A Project can be mirrored, published and started from, never synchronised

**Accepted, 2026-11-02, by the founder (D11 in the unified Hooke plan).**

It amends ADR 0027. That ADR's line "No bulk migration, automatic deletion, Git requirement, remote sync or
accepted-record filesystem editing" becomes: no bulk migration, automatic deletion, Git requirement, **automatic**
remote sync or accepted-record filesystem editing. Owner-started remote mirrors of sealed snapshots are allowed.

## Context

- **What researchers need.** Hooke should be where research starts: a Project you can publish, that anyone can
  start from, re-run and build on. Edison Scientific and similar services rent a closed AI scientist whose results stay
  inside their platform; agent-research commons share ideas and papers, not working projects.
- **What exists.** ADR 0027 made a Project a portable folder whose identity is its origin UUID, with sealed snapshots
  (`project_folder_copy.py`) that list every file with its SHA-256, and owner-aware provenance bundles
  (`scientific_exchange.py`) that import a document only when absent or when the local history is a prefix.
- **What it must not become.** A second copy of the research that drifts, a sync engine with merges, a requirement to
  use Git or GitHub, or a path by which unpublished history, secrets or someone else's instructions get in or out.

## Decision

1. **A remote mirrors sealed snapshots.** A push seals a snapshot under the host's writer lease, verifies it, and
   sends it. One host writes. There is no automatic sync and no merge. A remote that moved on elsewhere is refused
   ("This project changed in two places") and the owner may start an independent copy instead.
2. **Two layers.** The *record layer* (manifest, README, journals, revisions, manifests, receipts, small previews)
   and the *bulk layer* (content-addressed `files/*.bin` and `arrays/*.bin`, and any other file over 1 MiB) travel
   separately. `hooke-bulk.json` in the record layer names every bulk object by SHA-256 and size and says where it
   can be fetched. The format is open: `docs/architecture/hooke-project-format-v2.md`.
3. **Backends are options.** A folder (drive or NAS) needs no network or account and runs every test. GitHub holds
   the record layer. Object storage holds the bulk layer: the owner's own S3-compatible bucket, or Hooke Cloud. Every
   Hooke Cloud capability can be replaced by an open backend (ADR 0005 tiers). Local Hooke stays complete with none.
4. **Push order.** Bulk objects first, each verified; then one record commit carrying the snapshot manifest's
   SHA-256; then the remote's pointer moves by compare-and-swap. A commit never names an object that is missing, and
   retrying is safe.
5. **Publishing never exposes unpublished history.** A publication is a new, parentless commit of one reviewed
   snapshot in a separate public destination. It never changes the visibility of a backup. The owner reviews the
   exact files (conversations and transcripts excluded unless chosen), a secret scan and metadata scrub must pass, and
   the owner signs off on rights, licence (CC BY 4.0 preselected) and the absence of identifiable human data. The
   review digest binds what is sent.
6. **A publication is a filtered copy; starting from it makes a working copy.** A publication keeps the Project's
   origin and every accepted document byte for byte, but its journal is cut to the origin line alone (the same
   origin-only journal the owner-aware import already writes for restored owners), so conversations never leave.
   Research transcripts are left out unless chosen. Starting from a publication restores a working copy of it,
   records its ancestry (publication origin, snapshot manifest SHA-256, where it came from) as a journal event, and
   inherits no grants. Update brings newer published revisions in through the owner-aware import's prefix rule.
   *Staged next:* a fully independent Project (its own origin, the publication's documents listed as inherited owners)
   needs the inventory to list inherited owners; until then a started-from copy keeps the publication's identity and
   is refused, by the registry, beside another location of the same origin on one host.
7. **Everything from a remote is untrusted.** It is validated as a bundle (no `..`, absolute paths, links, special
   files; size, count and depth caps), every hash is checked and `verify_records` passes before anything is visible.
   Text reaches a model only through `untrusted.py` (ADR 0002). Code arrives inactive (ADR 0021). Claims of authorship
   or ancestry are shown with how they are known (ADR 0012).
8. **Reproduction is stated at three levels.** Inputs are byte-identical; deterministic CPU steps reproduce their
   hashes; GPU model results match within a stated tolerance, reported by comparison. Receipts record device, model
   version and seed.

## Costs

- A Project now has states outside the Mac (backed up, published, started from) that must be described truthfully,
  including "partial" when bulk data is not all present.
- Publication is permanent in practice: forks of a public repository survive its deletion. The sign-off says so.
- The GitHub path needs a registered GitHub App and a sign-in dialog. Hooke Cloud storage needs the hosted service,
  a cap and published-data terms.
- Single-writer stays strict: two Macs editing one Project produce a refusal and an independent copy, never a merge.
