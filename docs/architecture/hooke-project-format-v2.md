# Hooke Project format v2: remotes, publications and starting from a Project

Status: **open specification, version 2** (ADR 0028, accepted 2026-11-02). Version 2 adds remote layout to the portable Project
folder of ADR 0027 (`project.json` format 1, `storage: hooke-project-v1`), which is unchanged. Anyone may implement
it; nothing here needs Hooke Cloud or GitHub.

## 1. The sealed snapshot is the unit

Every remote operation starts from a sealed snapshot made by `project_folder_copy` (`copy_snapshot` /
`copy_owned_snapshot`). Its `snapshot.json` (`hooke-project-folder-snapshot-v1`) lists every directory and every file
of `project.json`, `README.md` and the five sections with `path`, `bytes` and `sha256`. A remote never carries a file
that the snapshot does not list, and never omits one it lists (bulk objects may be *absent locally*, see 6).

## 2. Two layers

| Layer | Members | Travels to |
|---|---|---|
| Bulk | `artifacts/records/p<32 hex>/(files|arrays)/<64 hex>.bin`, and any other member larger than 1 MiB | object storage |
| Record | every other member | the record remote (a folder, a Git host) |

Record members larger than 5 MiB are refused by Git-host backends (they belong in bulk; the 1 MiB rule already sends
them there). Classification is by path and size only, never by guessed meaning.

## 3. Remote tree

A record remote stores *commits*. Each commit is a tree of paths to blobs:

```
README.md                    a short notice, or the publication README (8)
LICENSE, CITATION.cff        publications only
ro-crate-metadata.json       publications only
.hooke/snapshot.json         the exact bytes of the sealed snapshot manifest
.hooke/bulk.json             hooke-bulk-v1 (4)
project/<snapshot path>      every record-layer member, at its snapshot path
```

The commit message ends with trailers:

```
Hooke-Snapshot: <sha256 of .hooke/snapshot.json bytes>
Hooke-Origin: <project origin>
Hooke-Format: 2
```

A commit whose trailer is missing, or whose `.hooke/snapshot.json` does not hash to its trailer, or whose record
blobs do not match the snapshot's hashes, is reported as **changed outside Hooke** and is never imported.

## 4. `hooke-bulk.json`

```json
{"format": "hooke-bulk-v2",
 "origin": "<32 hex>",
 "snapshotSha256": "<64 hex>",
 "entries": [{"path": "artifacts/records/p…/files/<sha>.bin", "bytes": 1048576, "sha256": "<sha>"}],
 "packs": [{"sha256": "<64 hex>", "bytes": 16777216,
            "contents": [["<content sha256>", 0, 1048576], ["<content sha256>", 0, 524288]]}],
 "stores": [{"kind": "folder|s3|hooke-cloud|zenodo", "base": "<location>", "visibility": "public|private"}]}
```

`entries` lists exactly the bulk-layer members of the snapshot, sorted by path. `stores` is advisory: a reader may
fetch an object from any store.

The bulk members travel in **packs**, not one object each, because a store charges for every object read: serving
1 TB a thousand times costs about $355 a month on R2 as 1 MiB objects and about $33 as 16 MiB ones. Each distinct
content (by SHA-256; one content serves every path that holds it) goes into the packs once, in path order, laid end
to end in packs of 16 MiB, and one content may run on from one pack into the next. A pack is an object named by the
SHA-256 of its bytes. Its `contents` are its pieces in order, `[content sha256, offset in that content, bytes]`, and
they fill the pack exactly. Every non-empty content is covered once, with no gap or overlap, and a pack holds nothing
that is not in `entries`. A writer may make packs of up to 64 MiB, and a reader refuses a larger one.

A new version reuses each pack of its parent that the store still holds, when every content in that pack is still in
the version and every piece of those contents is in packs reused with it. Only the other contents go into new packs,
so a second push sends only new data, and a version's packs never hold a file it does not list.

A `hooke-bulk-v1` manifest has no `packs`: each content is its own object, named by its SHA-256. Readers still accept
it, and writers make v2.

## 5. Object stores

A folder or S3-compatible store keeps objects at `objects/<sha[0:2]>/<sha256>`. Hooke Cloud keeps them at
`projects/<owner>/<origin>/objects/<sha256>` behind signed links. Objects are write-once; an existing object with the
same name is success, never an overwrite.

## 6. Pushing

1. Seal a snapshot under the writer lease; `verify_records` passes.
2. Classify; scan the record layer for secrets (a finding stops the push and names the file).
3. Pack the bulk layer (4), reusing the parent's packs the store still holds; upload each new pack the store lacks,
   and verify each upload.
4. Write one commit (parent = the last commit this host pushed there, or none for a new remote). On a Git host, a
   signed-in push sends the new objects in one Git pack over smart HTTP to a branch of their own
   (`hooke-pending-<12 hex of the commit>`); the host then re-reads that branch, the commit and its whole file
   listing, and refuses unless they are exactly what was built. When a pack cannot be sent, the commit is written
   through the host's REST API instead.
5. Move the remote's pointer by compare-and-swap from the expected old commit. If the remote moved on elsewhere,
   refuse: "This project changed in two places." Never force. Pending branches are removed afterwards, published or
   not.

## 7. Fetching

A reader validates the commit before writing anything: every path is relative, under `project/` or one of the root
names above, has no `..`, NUL or control characters, and is not a link or special file; counts, sizes and JSON depth
are bounded. Into a new folder marked incomplete, it writes the record members, then fetches each pack (verifying its
SHA-256 and size) and writes its pieces at their offsets. It verifies every bulk member's SHA-256 and size, writes
`snapshot.json`, verifies the whole folder (`verify_snapshot`, which re-runs `verify_records`), then clears the
marker. The result is a sealed snapshot, which `restore_snapshot` turns into a working copy.

## 8. Publications

A publication is a filtered sealed snapshot of the Project:

- `project.json`, the Project's README, every accepted document under `artifacts/records/`, and the chosen artifact
  sections (by default all except `artifacts/research`, which holds research transcripts).
- Each owner's session journal is replaced by its origin-only form (`ownership.journal_bytes`), which keeps the
  identity verifiable, followed by the lines that list its results: `evidence-saved` and `tracks-saved` (only with a
  portable `project:` path), `datatable`, `table-derivative` and `annotation`, each once, in order, without its
  `thread` (`project_inherited.published`). It carries no conversation. Connector operations and restoration receipts
  are left out.
- A person's notes (`annotation` text and title) are published text: the review lists them as `publishedText`, and the
  secret scan reads them with every other text member.
- It keeps the Project's origin and every revision digest.
- An imported JPEG, PNG or TIFF (including OME-TIFF, ImageJ and Aperio SVS) whose metadata names a place, a person, a
  home folder, a date or a device goes **cleaned** unless the owner turns cleaning off (`project_publish_clean`,
  `project_metadata_scrub`). Every edit is at the byte level, so image data is copied verbatim; Hooke also decodes the
  original and the copy and compares their pixels where this Mac can, and a copy that differs is not used. A slide
  scanner's label and macro photographs are dropped. The file's manifest stays byte for byte, so no document changes;
  the original's chunks are left out, and a second manifest in the same `files/` folder, named by its own SHA-256,
  stands in for it:

  ```json
  {"format": "project-source-file-clean-v1", "of": "<the original manifest's sha256>",
   "originalSha256": "<the original file's sha256>", "removed": ["location", "person"],
   "tool": "hooke-metadata-clean-1", "name": "<the original's name>", "size": 812345,
   "sha256": "<the cleaned file's sha256>", "chunkBytes": 1048576, "chunks": ["<sha256>", "..."]}
  ```

  A reader uses the cleaned copy only where the original's chunks are absent; a file with two cleaned copies, or one
  that names another original, is refused. The publisher's claim that the pixels are unchanged cannot be checked
  without the original, and is shown as the publisher's (ADR 0012). The Project keeps its original.

A connected agent may only ask for a publication: Hooke prepares the review as here and records
`project-publish-requested` with its `reviewDigest`; the owner reviews that review and signs off, and the
`project-published` event then names the agent as `requestedBy`.

Before sending, the owner reviews the exact members and their sizes, and which files were cleaned of what. A secret
finding blocks; a home-directory path, a metadata finding or a file in a format Hooke cannot check (HEIC, CZI, ND2,
DICOM, video) is shown for the owner to resolve. The owner signs off on rights, the licence (CC BY 4.0
preselected) and the absence of identifiable human data; the review digest (SHA-256 over the canonical review) binds
what is sent. The first publication is a parentless commit in its own destination; later publications of the same
Project chain onto it. Publishing never changes the visibility of a backup.

## 9. Starting from a publication

Starting from fetches a publication (7) and makes a **new Project** on this host, with its own origin and a handle
that none of the publication's owners use. Each published document comes in through the owner-aware import, byte for
byte and under its own owner. The new Project's `history/sessions` then holds that owner as an *inherited owner*: a
session folder whose journal is exactly an origin and its import (`ownership.journal_bytes`), followed only by the
publication's result lines for that owner (`project_inherited.split`; anything else there and it is not inherited).
Its plain result files are copied where absent, and each owner's result lines join its journal once
(`project_inherited.bring_in`), creating the owner when it has results and no documents. Then it appends one journal
event:

```json
{"type": "project-started-from", "origin": "<publication origin>", "snapshotSha256": "<64 hex>",
 "source": {"kind": "folder|github", "location": "<url or path>", "commit": "<commit id>"}, "documents": 2, "results": 3}
```

A Project's store lists and reads its inherited owners' documents under its own handle, saying which owner accepted
them (`ProjectRevision.owner`, the entry's `source.inheritedFrom`). A reference inside one, which names the owner's
handle, reads through the one Project here that holds that owner. Saving over an inherited document is refused; a
copy of it is the way to change it. It inherits no grants or registry entries.

Its results are listed with the new Project's own, read-only and under the inherited owner's handle (each entry's
`day`), which is how the studio opens them. A host finds the Project holding an inherited owner with
`ProjectLocations.holder`, for reading only: artifact folders refuse to write for it. Saved evidence and tracks name the
Project they were saved in (`project:<origin>/...`); for an inherited owner that is its own origin.

**Update** fetches a newer publication of the same origin (the ancestry event's) and brings each document in the same
way: a document is added when absent or extended when the local history is a prefix of the published one; anything
else is refused with nothing changed. New result lines join their owner's journal; ones already there are not added
again.

## 10. Reproduction levels

Receipts record device, model version and seed. A re-run is reported at three levels: inputs byte-identical (a
cleaned image, section 8, has the original's pixels and says the original's SHA-256 instead); deterministic CPU steps
reproduce their hashes; GPU model results match within a stated tolerance.

## 11. Backups

A backup is a push (6) of the **whole** sealed snapshot, journals and conversations included, to a destination only
its owner reads: a folder, or a private Git-host repository (`project_backup`). A Git-host backup's large files are
assets of its private `hooke-data` release, which are read signed in, never from the public download address. A
backup to a public repository is refused before anything is sent. A destination outside the owner's own storage is
scanned for secrets (6.2), and a folder is not.

Each backup records a `project-backed-up` journal event (destination, commit, parent, snapshot SHA-256). The next
backup to the same destination has the latest such commit as its parent, so when another Mac backed up since, the
compare-and-swap (6.5) refuses and nothing is sent: the owner restores the newer backup first.

**Restoring** fetches (7) and restores the snapshot as a working folder with the **same** origin, handle and history.
It is then registered through the owner's folder registration: on a Mac that has never held the Project it becomes
that Mac's Project, with no agent permissions; on a Mac whose registered location is gone, it takes that location's
place. A Mac whose location still exists refuses, so one Project never has two working folders on one Mac. The
restored journal records `project-restored-from-backup` (destination, commit), which is the next backup's parent there.

**Deleting** a backup marks it first: a folder remote gains `deleted.json` (`hooke-remote-deleted-v1`, with `at`), and
a Git-host repository is archived. A marked backup accepts no new version but can still be restored, and the owner
can keep it (the mark is removed). Thirty days after it was marked (`project-backup-deleted` records `purgeAfter`), the
next look at the Project's backups removes it: a folder loses only the entries a folder remote writes (`remote.json`,
`HEAD`, `.lock`, `deleted.json`, `commits/`, `blobs/`, `objects/`), and the folder itself only when nothing else is in
it; a repository is deleted. `project-backup-purged` is recorded, and a later backup there starts with no parent.

**Old data.** After each backup, an object of the bulk store is removed when it arrived more than 30 days ago and no
version of the window names it: the current version, every version that arrived in the last 30 days, and the one that
was current when the window began. A store that cannot say when things arrived is left as it is, and anything
unreadable stops the removal with nothing removed. Record blobs and older version records stay; a version older than
the window may no longer be fetchable whole.
